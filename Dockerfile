FROM runpod/pytorch@sha256:cb154fcca15d1d6ce858cfa672b76505e30861ef981d28ec94bd44168767d853
WORKDIR /opt/gamedev
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN python -m venv /opt/gamedev/venv
COPY container-requirements.txt /opt/gamedev/container-requirements.txt
RUN /opt/gamedev/venv/bin/pip install --no-compile --extra-index-url https://download.pytorch.org/whl/cu130 -r container-requirements.txt
ARG COMFY_COMMIT=bbd810e647c64da8212b0d4ab94f1354c9d523f6
RUN git clone https://github.com/Comfy-Org/ComfyUI.git ComfyUI && cd ComfyUI && git checkout ${COMFY_COMMIT}
COPY cloud/ /opt/gamedev/cloud/
COPY config.json /opt/gamedev/cloud/config.json
RUN /opt/gamedev/venv/bin/pip check && /opt/gamedev/venv/bin/pip freeze > /opt/gamedev/resolved-requirements.txt
ENV GAMEDEV_PREBUILT_RUNTIME=/opt/gamedev GAMEDEV_MODEL=trellis2
ARG BUILD_REVISION=development
ENV GAMEDEV_BUNDLE_SHA256=${BUILD_REVISION}
CMD ["/opt/gamedev/venv/bin/python", "-u", "/opt/gamedev/cloud/worker.py"]
