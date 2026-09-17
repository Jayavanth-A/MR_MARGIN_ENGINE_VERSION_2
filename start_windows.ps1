# MR_MARGIN_ENGINE: Start Web Dashboard
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "  Starting MR_MARGIN_ENGINE Web Dashboard" -ForegroundColor Cyan
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host ""

if (Test-Path ".\venv\Scripts\Activate.ps1") {
    & ".\venv\Scripts\Activate.ps1"
}

Start-Process "http://127.0.0.1:8000"
& python main.py --serve --port 8000
