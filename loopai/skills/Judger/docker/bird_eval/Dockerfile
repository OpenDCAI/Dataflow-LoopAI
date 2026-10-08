ARG PYTHON_BASE_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# The image only grades generated SQL. The databases and model outputs are
# mounted when the container runs; neither is copied into the image.
COPY bird_eval.py /app/bird_eval.py

ENTRYPOINT ["python", "/app/bird_eval.py"]
