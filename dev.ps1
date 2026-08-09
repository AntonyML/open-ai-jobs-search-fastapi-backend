$ErrorActionPreference = "Stop"
$VENV_PYTHON = Join-Path $PSScriptRoot ".venv" "Scripts" "python.exe"

Write-Host "=== Iniciando API (el worker de ranking vive en el microservicio rankjobs :8002) ===" -ForegroundColor Cyan
Write-Host "[API]    Iniciando uvicorn en http://localhost:8000 ..." -ForegroundColor Green
try {
    & $VENV_PYTHON -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
} finally {
    Write-Host "=== API detenida ===" -ForegroundColor Cyan
}
