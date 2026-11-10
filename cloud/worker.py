"""Fixed-model RunPod handler. Accepts images/settings, never arbitrary graphs."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import zipfile

from common import read_json, sha256_file, write_json
from runtime import CONFIG, HERE, VOLUME, PERSISTENT, file_lock, prepare_models, prepare_runtime
from quality import resolve_quality, apply_quality

MAX_IMAGE_BYTES = 6 * 1024 * 1024
MODEL_NAMES = ("pixal3d", "trellis2")


def validate_request(data):
    if not isinstance(data, dict):
        raise ValueError("input must be an object")
    operation = data.get("operation", "generate")
    if operation not in ("probe", "prepare", "generate"):
        raise ValueError("Unsupported operation")
    if operation != "generate":
        if set(data) != {"operation"}:
            raise ValueError("probe/prepare accept only operation")
        return operation, None
    if set(data) - {"operation", "request_id", "image_base64", "seed", "quality", "settings"}:
        raise ValueError("Unsupported field; endpoint model and workflow are fixed")
    if not re.fullmatch(r"[a-f0-9]{32}", str(data.get("request_id", ""))):
        raise ValueError("request_id must be a UUID hex string")
    if type(data.get("seed")) is not int or not 0 <= data["seed"] <= 2**32 - 4:
        raise ValueError("seed must be an integer between 0 and 4294967292")
    if data.get("quality", "preview") not in ("preview", "reference", "balanced", "detail"):
        raise ValueError("Invalid quality preset")
    if 'settings' in data or data.get('quality') in ('balanced','detail'):
        resolve_quality(data.get('quality','preview'), data.get('settings'))
    encoded = data.get("image_base64")
    if not isinstance(encoded, str) or len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
        raise ValueError("PNG/JPEG input must not exceed 6 MiB")
    raw = base64.b64decode(encoded, validate=True)
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Invalid image size")
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as image:
        if image.format not in ("PNG", "JPEG") or image.width * image.height > 16_777_216:
            raise ValueError("Use PNG/JPEG with at most 16 megapixels")
        image.verify()
    return operation, raw


def build_prompt(model, seed, quality, filename, settings=None):
    prompt = read_json(HERE / "workflows" / (model + ".json"))
    prompt["122"]["inputs"]["image"] = filename
    for offset, node_id in enumerate(("3", "18", "23", "12")):
        prompt[node_id]["inputs"]["seed"] = seed + offset
    if quality == "preview":
        # Preview tests plumbing and rough likeness; it is not the final art test.
        prompt["94"]["inputs"]["target_resolution"] = 1024
        prompt["241"]["inputs"]["resolution"] = 384
        prompt["186"]["inputs"]["target_face_count"] = 150000
        prompt["288"]["inputs"]["value"] = 1024
        prompt["224"]["inputs"]["resolution"] = 1024
        prompt["233"]["inputs"]["resolution"] = 512
        prompt["233"]["inputs"]["samples"] = 16
    if quality in ('balanced','detail') or settings is not None:
        apply_quality(prompt, resolve_quality(quality,settings))
    return prompt


def comfy_request(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request("http://127.0.0.1:8188" + path, body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


class ComfyProcess:
    def __init__(self, runtime, models, job_dir):
        self.runtime, self.models, self.job_dir = runtime, models, job_dir
        self.process = None
        self.log = None

    def __enter__(self):
        directories = ("diffusion_models", "vae", "clip_vision", "background_removal", "geometry_estimation")
        config = {"gamedev": {"base_path": str(self.models), **{name: name for name in directories}}}
        paths_file = self.job_dir / "model-paths.json"
        write_json(paths_file, config)  # JSON is valid YAML for extra-model-paths.
        for name in ("input", "output", "temp"):
            (self.job_dir / name).mkdir(exist_ok=True)
        self.log = (self.job_dir / "comfy.log").open("w", encoding="utf-8")
        args = [str(self.runtime / "venv/bin/python"), "-u", str(self.runtime / "ComfyUI/main.py"),
                "--listen", "127.0.0.1", "--port", "8188", "--disable-auto-launch",
                "--disable-all-custom-nodes", "--disable-api-nodes", "--use-pytorch-cross-attention",
                "--extra-model-paths-config", str(paths_file)]
        for name in ("input", "output", "temp"):
            args += ["--" + name + "-directory", str(self.job_dir / name)]
        self.process = subprocess.Popen(args, cwd=self.runtime / "ComfyUI", stdout=self.log,
                                        stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError("ComfyUI failed to start; inspect persisted comfy.log")
                try:
                    comfy_request("/system_stats")
                    return self
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(2)
            raise TimeoutError("ComfyUI startup timed out")
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.process is not None and self.process.poll() is None:
            import signal
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=10)
        if self.log:
            self.log.close()


def execute_prompt(prompt, job_dir, timeout):
    available = comfy_request("/object_info")
    missing = sorted({n["class_type"] for n in prompt.values()} - set(available))
    if missing:
        raise RuntimeError("Required ComfyUI nodes did not load: " + ", ".join(missing))
    try:
        queued = comfy_request("/prompt", {"prompt": prompt})
    except urllib.error.HTTPError as error:
        # Save useful validation errors locally on the volume, without echoing
        # a request image or signed URL into an API response.
        (job_dir / "validation-error.json").write_bytes(error.read())
        raise RuntimeError("ComfyUI rejected workflow; see validation-error.json") from None
    if queued.get("node_errors") or "prompt_id" not in queued:
        write_json(job_dir / "validation-error.json", queued)
        raise RuntimeError("ComfyUI workflow validation failed")
    prompt_id = queued["prompt_id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        history = comfy_request("/history/" + prompt_id).get(prompt_id)
        if history:
            write_json(job_dir / "history.json", history)
            if history["status"]["status_str"] != "success":
                raise RuntimeError("ComfyUI execution failed; see history.json and comfy.log")
            return
        time.sleep(3)
    comfy_request("/interrupt", {})
    raise TimeoutError("Generation reached its time limit")


def package_result(manifest, job_dir):
    if PERSISTENT:
        return manifest
    # Small pilot artifacts return directly through the authenticated job API.
    # Larger production jobs need persistent/object storage instead.
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in sorted(job_dir.rglob("*")):
            relative = path.relative_to(job_dir)
            if path.is_file() and relative.parts[0] not in ("temp", "input"):
                output.write(path, relative.as_posix())
    raw = archive.getvalue()
    if len(raw) > 6 * 1024 * 1024:
        raise RuntimeError("Generated artifact exceeds the direct-return trial limit; persistent storage required")
    return {**manifest, "artifact_zip_base64": base64.b64encode(raw).decode(),
            "artifact_zip_sha256": hashlib.sha256(raw).hexdigest()}


def handler(job):
    model = os.environ.get("GAMEDEV_MODEL")
    if model not in MODEL_NAMES:
        raise RuntimeError("Deployment must select exactly one supported model")
    data = job.get("input")
    operation, image = validate_request(data)
    if operation == "probe":
        return {"model": model, "bundle": os.environ["GAMEDEV_BUNDLE_SHA256"],
                "storage_mode": "persistent" if PERSISTENT else "ephemeral",
                "volume_mounted": VOLUME.is_mount(), "generation_verified": False}
    # Even independent endpoints share this trial lock. Accidental parallel
    # requests fail quickly rather than queueing paid GPUs behind one another.
    # Different requests can run on separate workers. Duplicate request IDs are
    # serialized, while checkpoint downloads retain their own per-file locks.
    lock_id = data.get('request_id', 'prepare-' + model)
    with file_lock(VOLUME / 'locks' / (model + '-' + lock_id + '.lock'), timeout=1):
        started = time.monotonic()
        if operation == "prepare":
            runtime = prepare_runtime()
            prepare_models(model)
            return {"model": model, "cache_ready": True, "generation_verified": False,
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                    "runtime_location": "worker-local cloud disk"}
        request_id = data["request_id"]
        job_dir = VOLUME / "artifacts" / model / request_id
        fingerprint = hashlib.sha256(image + json.dumps({"seed": data["seed"],
                     "quality": data.get("quality", "preview"), "settings": data.get('settings'), "bundle": os.environ["GAMEDEV_BUNDLE_SHA256"]},
                     sort_keys=True).encode()).hexdigest()
        manifest_path = job_dir / "manifest.json"
        if manifest_path.exists():
            old = read_json(manifest_path)
            if old["fingerprint"] != fingerprint:
                raise ValueError("request_id already belongs to different input/settings")
            if old["status"] == "complete":
                return package_result(old, job_dir)
            raise RuntimeError("Previous attempt incomplete; use a new request_id after reviewing its logs")
        job_dir.mkdir(parents=True)
        (job_dir / "input").mkdir()
        filename = "input.png" if image.startswith(b"\x89PNG") else "input.jpg"
        (job_dir / "input" / filename).write_bytes(image)
        manifest = {"status": "running", "model": model, "request_id": request_id,
                    "fingerprint": fingerprint, "seed": data["seed"], "quality": data.get("quality", "preview"),
                    "input_sha256": hashlib.sha256(image).hexdigest(), "comfy_commit": CONFIG["comfy_commit"],
                    "bundle": os.environ["GAMEDEV_BUNDLE_SHA256"],
                    "artifact_prefix": str(job_dir.relative_to(VOLUME))}
        write_json(manifest_path, manifest)
        try:
            runtime = prepare_runtime()
            models = prepare_models(model)
            shutil.copy2(runtime / "resolved-requirements.txt", job_dir / "resolved-requirements.txt")
            shutil.copy2(HERE / "models.lock.json", job_dir / "models.lock.json")
            prompt = build_prompt(model, data["seed"], data.get("quality", "preview"), filename, data.get('settings'))
            write_json(job_dir / "workflow-api.json", prompt)
            remaining = min(CONFIG["generation_timeout_seconds"],
                            CONFIG["execution_timeout_seconds"] - (time.monotonic() - started) - 180)
            if remaining < 60:
                raise TimeoutError("Setup used this job's time allowance; cache is preserved for the next job")
            with ComfyProcess(runtime, models, job_dir):
                execute_prompt(prompt, job_dir, remaining)
            meshes = list((job_dir / "output").rglob("*.glb"))
            if not meshes:
                raise RuntimeError("Workflow reported success but produced no GLB")
            manifest.update(status="complete", elapsed_seconds=round(time.monotonic() - started, 2),
                            outputs=[{"key": str(p.relative_to(VOLUME)), "bytes": p.stat().st_size,
                                      "sha256": sha256_file(p)} for p in meshes])
            write_json(manifest_path, manifest)
            return package_result(manifest, job_dir)
        except Exception as error:
            manifest.update(status="failed", error_type=type(error).__name__,
                            elapsed_seconds=round(time.monotonic() - started, 2))
            write_json(manifest_path, manifest)
            raise


if __name__ == "__main__":
    import runpod
    runpod.serverless.start({"handler": handler})
