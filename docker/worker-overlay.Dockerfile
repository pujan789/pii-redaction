ARG WORKER_BASE
FROM ghcr.io/ggml-org/llama.cpp@sha256:9810cc5f409cbebb6412ee351190698b381aa0261fa96ea150738094bde1269d AS llama_cpp
FROM public.ecr.aws/docker/library/python@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49 AS overlay-dependencies

COPY requirements/worker-overlay.txt /tmp/requirements-worker-overlay.txt
RUN pip install --no-cache-dir --no-deps --require-hashes \
    --target /overlay -r /tmp/requirements-worker-overlay.txt

FROM ${WORKER_BASE}

ARG WORKER_BASE
LABEL org.taxhance.pii-redaction.overlay-base="${WORKER_BASE}"

# Release overlays are allowed only when dependency locks and the full worker
# Dockerfile are unchanged. A fresh source path prevents deleted old modules
# from surviving underneath the overlay.
ENV PYTHONPATH=/app/release-src \
    LD_LIBRARY_PATH=/opt/llama.cpp
COPY --from=llama_cpp /app /opt/llama.cpp
COPY --from=overlay-dependencies /overlay/ /opt/venv/lib/python3.12/site-packages/
COPY LICENSE /app/release-LICENSE
COPY src /app/release-src

RUN python -c "import torch, torchvision; assert torch.__version__.startswith('2.13.'); assert torchvision.__version__.startswith('0.28.')"

USER 10001:10001
CMD ["python", "-m", "taxhance_pii.worker.main"]
