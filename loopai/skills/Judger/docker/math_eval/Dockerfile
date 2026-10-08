FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=
ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG NO_PROXY=

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app/eval

# vLLM is started by Judger on the host. This image contains only the evaluator
# and its grading dependencies, and calls the host service over host networking.
COPY requirements.txt /app/eval/requirements.txt
RUN PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST}" \
    HTTP_PROXY="${HTTP_PROXY}" \
    HTTPS_PROXY="${HTTPS_PROXY}" \
    NO_PROXY="${NO_PROXY}" \
    python3 -m pip install --no-cache-dir -r /app/eval/requirements.txt

COPY evaluate_math.py /app/eval/evaluate_math.py
COPY docker-entrypoint.sh /app/eval/docker-entrypoint.sh
RUN chmod +x /app/eval/docker-entrypoint.sh

ENTRYPOINT ["/app/eval/docker-entrypoint.sh"]
