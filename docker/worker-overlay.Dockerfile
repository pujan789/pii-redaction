ARG WORKER_BASE
FROM public.ecr.aws/docker/library/python@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49 AS overlay-dependencies

COPY requirements/worker-overlay.txt /tmp/requirements-worker-overlay.txt
# pip only creates the target directory when it installs something; an empty
# additive-wheels file must still leave a directory for the COPY below.
RUN mkdir -p /overlay \
    && pip install --no-cache-dir --no-deps --require-hashes \
        --target /overlay -r /tmp/requirements-worker-overlay.txt

FROM ${WORKER_BASE}

ARG WORKER_BASE
LABEL org.taxhance.pii-redaction.overlay-base="${WORKER_BASE}"

# Release overlays are allowed only when dependency locks and the full worker
# Dockerfile are unchanged. A fresh source path prevents deleted old modules
# from surviving underneath the overlay.
ENV PYTHONPATH=/app/release-src
COPY --from=overlay-dependencies /overlay/ /usr/local/lib/python3.12/dist-packages/
COPY LICENSE /app/release-LICENSE
COPY src /app/release-src

RUN python3 -c "import vllm; import taxhance_pii.worker.main"

USER 10001:10001
