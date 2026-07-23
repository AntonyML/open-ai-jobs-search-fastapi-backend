#!/bin/bash
# Entrypoint for Docker — runs API or worker based on DOCKER_PROCESS env var.

set -e

if [ "$DOCKER_PROCESS" = "worker" ]; then
    echo "[entrypoint] Starting worker..."
    exec python -m app.worker
else
    echo "[entrypoint] Starting API server..."
    exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
fi
