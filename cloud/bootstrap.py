"""Run only in RunPod's Linux container; never installs anything on the client."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    if sys.platform != "linux" or not os.environ.get("RUNPOD_POD_ID"):
        raise RuntimeError("Bootstrap is restricted to a RunPod Linux worker")
    persistent = os.environ.get("GAMEDEV_STORAGE_MODE", "persistent") == "persistent"
    volume = Path("/runpod-volume" if persistent else "/tmp/gamedev-data")
    if persistent and not volume.is_mount():
        raise RuntimeError("Persistent RunPod network volume is required")
    volume.mkdir(parents=True, exist_ok=True)
    # Package caches contain many tiny files; keep them off the NFS volume.
    os.environ["PIP_CACHE_DIR"] = "/tmp/gamedev-pip-cache"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    # This lightweight SDK starts first. The expensive setup happens within a
    # job so RunPod's execution timeout also covers model/dependency downloads.
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                    "runpod==2.2.0"], check=True, timeout=180)
    os.execv(sys.executable, [sys.executable, "-u", str(Path(__file__).with_name("worker.py"))])


if __name__ == "__main__":
    main()
