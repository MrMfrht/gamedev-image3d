"""Remote-only ComfyUI installation and verified, restartable model cache."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

from common import inside, read_json, sha256_file, write_json

HERE = Path(__file__).resolve().parent
CONFIG = read_json(HERE / "config.json" if (HERE / "config.json").exists() else HERE.parent / "config.json")
PERSISTENT = CONFIG.get("storage_mode", "persistent") == "persistent"
VOLUME = Path("/runpod-volume" if PERSISTENT else "/tmp/gamedev-data")
RUNTIMES = Path("/tmp/gamedev-runtimes")


@contextlib.contextmanager
def file_lock(path, timeout=1200):
    import fcntl  # Linux only; locks release automatically after worker death.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for the shared cache lock")
                time.sleep(2)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def download_verified(item, destination):
    """A partial file never becomes a checkpoint until its hash is correct."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(destination.with_suffix(".lock")):
        if destination.exists() and destination.stat().st_size == item["size"]:
            if sha256_file(destination) == item["sha256"]:
                return
        temporary = destination.with_suffix(".partial")
        try:
            print("Downloading checkpoint: " + destination.name, flush=True)
            digest = hashlib.sha256()
            size = 0
            with urllib.request.urlopen(item["url"], timeout=90) as response, temporary.open("wb") as output:
                for chunk in iter(lambda: response.read(8 * 1024 * 1024), b""):
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            if size != item["size"] or digest.hexdigest() != item["sha256"]:
                raise ValueError("Checkpoint checksum mismatch: " + destination.name)
            temporary.replace(destination)
            print("Verified checkpoint: " + destination.name, flush=True)
        finally:
            temporary.unlink(missing_ok=True)


def prepare_runtime():
    if sys.platform != "linux" or not os.environ.get("RUNPOD_POD_ID") or (PERSISTENT and not VOLUME.is_mount()):
        raise RuntimeError("Model installation is restricted to a RunPod worker with its configured storage")
    prebuilt = os.environ.get('GAMEDEV_PREBUILT_RUNTIME')
    if prebuilt:
        root = Path(prebuilt)
        if not (root / 'resolved-requirements.txt').is_file():
            raise RuntimeError('Prebuilt runtime manifest missing')
        return root
    # Source trees/venvs contain many small files. NFS setup timed out in the
    # first live test; only large checkpoints and durable artifacts belong there.
    runtime = RUNTIMES / os.environ["GAMEDEV_BUNDLE_SHA256"]
    with file_lock(runtime.with_suffix(".lock")):
        marker = runtime / "ready.json"
        if marker.exists():
            return runtime
        if runtime.exists():
            shutil.rmtree(inside(RUNTIMES, runtime.name))
        runtime.mkdir(parents=True)
        archive = runtime / "comfy.tar.gz"
        url = f"https://codeload.github.com/Comfy-Org/ComfyUI/tar.gz/{CONFIG['comfy_commit']}"
        with urllib.request.urlopen(url, timeout=90) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        with tarfile.open(archive) as source:
            source.extractall(runtime, filter="data")
        archive.unlink()
        print("ComfyUI source prepared on worker-local disk", flush=True)
        (runtime / ("ComfyUI-" + CONFIG["comfy_commit"])).rename(runtime / "ComfyUI")
        subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(runtime / "venv")],
                       check=True, timeout=120)
        print("Installing ComfyUI dependencies on worker-local disk", flush=True)
        python = str(runtime / "venv/bin/python")
        # Keep the CUDA-compatible Torch supplied by the pinned base image.
        import importlib.metadata
        constraints = runtime / "constraints.txt"
        constraints.write_text("\n".join(name + "==" + importlib.metadata.version(name)
                                         for name in ("torch", "torchvision")) + "\n")
        subprocess.run([python, "-m", "pip", "install", "--disable-pip-version-check", "-c", str(constraints),
                        "-r", str(runtime / "ComfyUI/requirements.txt")], check=True, timeout=900)
        installed = subprocess.check_output([python, "-m", "pip", "freeze"], text=True, timeout=30)
        (runtime / "resolved-requirements.txt").write_text(installed, encoding="utf-8")
        subprocess.run([python, "-m", "pip", "check"], check=True, timeout=30)
        write_json(marker, {"comfy_commit": CONFIG["comfy_commit"], "base_image": CONFIG["base_image"]})
        print("ComfyUI dependencies verified", flush=True)
    return runtime


def prepare_models(model):
    lock = read_json(HERE / "models.lock.json")
    models = VOLUME / "models"
    for item in lock["files"]:
        if model in item["models"]:
            download_verified(item, inside(models, item["path"]))
    return models
