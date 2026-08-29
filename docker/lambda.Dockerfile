FROM public.ecr.aws/lambda/python:3.12@sha256:ded1ec786a439375177b034b39c576edcb1b5f9ac6b025bd2deced9334ba6dd3

COPY requirements/api.txt /tmp/requirements-api.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements-api.txt --target ${LAMBDA_TASK_ROOT} \
    && rm /tmp/requirements-api.txt

COPY LICENSE ${LAMBDA_TASK_ROOT}/
COPY src/taxhance_pii ${LAMBDA_TASK_ROOT}/taxhance_pii

CMD ["taxhance_pii.api.main.handler"]
