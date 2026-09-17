@echo off
setlocal enabledelayedexpansion

echo ====================================================================
echo   MR_MARGIN_ENGINE: Windows Automated Setup
echo ====================================================================
echo.

REM 1. Check Python
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not found in system PATH.
    echo Please install Python 3.11+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version') do echo [OK] Found %%i

REM 2. Check FFmpeg
ffmpeg -version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [WARNING] FFmpeg is not found in system PATH.
    echo FFmpeg is required for video rendering and duration probing.
    echo You can install it via winget: winget install Gyan.FFmpeg
    echo Or download from https://www.gyan.dev/ffmpeg/builds/ and add to PATH.
    echo.
) else (
    echo [OK] Found FFmpeg
)

REM 3. Create Virtual Environment
if not exist "venv" (
    echo [INFO] Creating Python virtual environment (venv)...
    python -m venv venv
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [OK] Virtual environment already exists.
)

REM 4. Activate Virtual Environment and Install Dependencies
echo [INFO] Activating virtual environment...
call venv\Scripts\activate.bat

echo [INFO] Installing required dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)
echo [OK] Dependencies successfully installed.

REM 5. Initialize .env if missing
if not exist ".env" (
    echo [INFO] Creating .env from .env.example...
    copy .env.example .env >nul
    echo [NOTICE] Please edit .env to add your AICREDITS_API_KEY if using live models.
) else (
    echo [OK] Existing .env file found.
)

REM 6. Generate Sample Dataset
echo [INFO] Initializing sample test assets...
python scripts\create_sample_project.py >nul 2>&1

echo.
echo ====================================================================
echo [SUCCESS] Setup complete!
echo To launch the web dashboard, run: start_windows.bat
echo Or run: python main.py --serve
echo ====================================================================
echo.
pause
