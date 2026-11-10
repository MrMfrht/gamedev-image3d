# GameDev native image-to-3D worker

Experimental RunPod worker with pinned ComfyUI and CUDA 13 dependencies. Model weights are downloaded only on RunPod; none are included in this repository or image. Runtime requires a mounted /runpod-volume and GAMEDEV_MODEL=trellis2 or pixal3d. Use zero active workers until GPU validation passes.

Input: operation=generate, request_id=UUID hex, image_base64=PNG/JPEG, seed=integer, quality=preview|balanced|detail, settings=bounded overrides. Output artifacts persist on the volume. Higher settings are unbenchmarked.

ComfyUI source and license: https://github.com/Comfy-Org/ComfyUI . Third-party licenses remain applicable. No character assets or credentials are included.
