@echo off
setlocal

echo ====================================================================
echo   Starting MR_MARGIN_ENGINE Web Dashboard
echo ====================================================================
echo.

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

echo [INFO] Launching server on http://127.0.0.1:8000 ...
start http://127.0.0.1:8000
python main.py --serve --port 8000

pause
