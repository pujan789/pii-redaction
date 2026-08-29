# GPU worker: vLLM serving google/gemma-4-E2B-it + the text-anchored pipeline.
# The vllm-openai image carries CUDA, torch, and the vllm CLI; we add Tesseract
# for the OCR path and install the project on top.
FROM vllm/vllm-openai:v0.28.0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models/huggingface \
    # Tesseract's OpenMP threads spin-wait and collapse under concurrent page
    # OCR on small hosts; one thread per process keeps parallel OCR linear.
    OMP_THREAD_LIMIT=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 pii \
    && useradd --uid 10001 --gid pii --create-home --shell /usr/sbin/nologin pii \
    && mkdir -p /models/huggingface \
    && chown -R pii:pii /models /home/pii

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[worker]"

COPY docker/worker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER 10001:10001
ENTRYPOINT ["/entrypoint.sh"]
