# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Unprivileged runtime user; uid/gid must match runAsUser/runAsGroup in k8s/deployment.yaml
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# Code stays root-owned (read-only for the app user); see .dockerignore for what is excluded
COPY . .

USER app

EXPOSE 5000

# --worker-tmp-dir /dev/shm: gunicorn heartbeat files need a writable tmpfs when the
# root filesystem is read-only (readOnlyRootFilesystem in k8s/deployment.yaml).
# --no-control-socket: gunicorn >= 25 otherwise tries to create a socket under $HOME.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--worker-tmp-dir", "/dev/shm", "--no-control-socket", "--timeout", "60", "run:app"]
