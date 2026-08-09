#!/bin/bash
# Entrypoint for Docker — starts the API server.
# (The ranking worker moved to its own microservice: rankjobs :8002.)

set -e

echo "[entrypoint] Starting API server..."
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
