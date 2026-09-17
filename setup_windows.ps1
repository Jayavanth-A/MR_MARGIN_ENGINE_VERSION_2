# MR_MARGIN_ENGINE: Windows PowerShell Setup Script
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "  MR_MARGIN_ENGINE: Windows PowerShell Setup" -ForegroundColor Cyan
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Check Python
try {
    $pythonVersion = & python --version 2>&1
    Write-Host "[OK] Found $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python is not installed or not in PATH." -ForegroundColor Red
    Write-Host "Please install Python 3.11+ from https://www.python.org/downloads/"
    exit 1
}

# 2. Check FFmpeg
try {
    $ffmpegVersion = & ffmpeg -version 2>&1
    Write-Host "[OK] Found FFmpeg" -ForegroundColor Green
} catch {
    Write-Host "[WARNING] FFmpeg is not found in PATH." -ForegroundColor Yellow
    Write-Host "Install via winget: winget install Gyan.FFmpeg" -ForegroundColor Yellow
}

# 3. Create Virtual Environment
if (-not (Test-Path "venv")) {
    Write-Host "[INFO] Creating virtual environment (venv)..." -ForegroundColor Cyan
    & python -m venv venv
} else {
    Write-Host "[OK] Virtual environment already exists." -ForegroundColor Green
}

# 4. Activate and Install Dependencies
Write-Host "[INFO] Activating virtual environment & installing dependencies..." -ForegroundColor Cyan
$activateScript = ".\venv\Scripts\Activate.ps1"
if (Test-Path $activateScript) {
    & $activateScript
}

& python -m pip install --upgrade pip
& pip install -r requirements.txt

# 5. Initialize .env
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[NOTICE] Created .env from .env.example. Add your AICREDITS_API_KEY for live models." -ForegroundColor Yellow
} else {
    Write-Host "[OK] Existing .env file found." -ForegroundColor Green
}

# 6. Generate Sample Dataset
Write-Host "[INFO] Initializing sample test assets..." -ForegroundColor Cyan
& python scripts\create_sample_project.py

Write-Host ""
Write-Host "====================================================================" -ForegroundColor Green
Write-Host "[SUCCESS] Setup complete! Run .\start_windows.ps1 to launch dashboard." -ForegroundColor Green
Write-Host "====================================================================" -ForegroundColor Green
