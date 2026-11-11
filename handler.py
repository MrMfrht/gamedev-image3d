"""Start the image-to-3D queue worker from the container's repository root."""
from pathlib import Path
import sys

import runpod

# The cloud modules also run as standalone scripts in the Pod bootstrap.
sys.path.insert(0, str(Path(__file__).resolve().parent / "cloud"))
from worker import handler


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
