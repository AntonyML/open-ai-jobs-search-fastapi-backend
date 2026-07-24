param(
    [switch]$NoWorker
)

$ErrorActionPreference = "Stop"
$VENV_PYTHON = Join-Path $PSScriptRoot ".venv" "Scripts" "python.exe"

Write-Host "=== setup_latex.py ===" -ForegroundColor Cyan
& $VENV_PYTHON scripts/setup_latex.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] setup_latex.py fallo, continuando de todas formas" -ForegroundColor Yellow
}

Write-Host "=== Iniciando API + Worker ===" -ForegroundColor Cyan

if (-not $NoWorker) {
    Write-Host "[Worker] Iniciando en segundo plano..." -ForegroundColor Green
    $null = Start-Job -Name "worker" -ScriptBlock {
        param($py, $dir)
        Set-Location $dir
        & $py -m app.worker
    } -ArgumentList $VENV_PYTHON, $PSScriptRoot
    Start-Sleep -Seconds 2
    $j = Get-Job -Name "worker"
    if ($j.HasMoreData) {
        $msg = Receive-Job $j
        if ($msg -match "error|Error") { Write-Host "[Worker] $msg" -ForegroundColor Red }
    }
    Write-Host "[Worker] Activo" -ForegroundColor Green
}

Write-Host "[API]    Iniciando uvicorn en http://localhost:8000 ..." -ForegroundColor Green
try {
    & $VENV_PYTHON -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
} finally {
    if (-not $NoWorker) {
        Write-Host "[Worker] Deteniendo..." -ForegroundColor Yellow
        $j = Get-Job -Name "worker" -ErrorAction SilentlyContinue
        if ($j) {
            Stop-Job $j -ErrorAction SilentlyContinue
            Remove-Job $j -ErrorAction SilentlyContinue
        }
        Write-Host "[Worker] Detenido" -ForegroundColor Green
    }
    Write-Host "=== Todo detenido ===" -ForegroundColor Cyan
}
