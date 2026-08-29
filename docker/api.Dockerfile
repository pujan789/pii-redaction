FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/src

RUN python -m venv /opt/venv \
    && groupadd --gid 10001 pii \
    && useradd --uid 10001 --gid pii --no-create-home --shell /usr/sbin/nologin pii

WORKDIR /app
COPY requirements/api.txt /tmp/requirements-api.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements-api.txt \
    && rm /tmp/requirements-api.txt

COPY LICENSE ./
COPY src ./src

USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "taxhance_pii.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
