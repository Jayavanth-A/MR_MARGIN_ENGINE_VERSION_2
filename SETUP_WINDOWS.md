# Windows Setup Guide: MR_MARGIN_ENGINE

This guide walks you through setting up and running **MR_MARGIN_ENGINE** on any Windows 10/11 computer.

---

## 📋 Prerequisites

### 1. Python 3.11+
- Download and install Python 3.11 or newer from [python.org](https://www.python.org/downloads/).
- **CRITICAL**: Check the checkbox **"Add Python to PATH"** during installation.

### 2. FFmpeg & FFprobe
FFmpeg is required for video rendering and audio duration probing.
You can install FFmpeg using any of the following methods:

**Method A: Using Windows Package Manager (winget) [Recommended]**
Open PowerShell as Administrator and run:
```powershell
winget install Gyan.FFmpeg
```

**Method B: Manual Download**
1. Download `ffmpeg-release-essentials.zip` from [gyan.dev/ffmpeg/builds/](https://www.gyan.dev/ffmpeg/builds/).
2. Extract the archive (e.g., to `C:\ffmpeg`).
3. Add the `bin` directory (`C:\ffmpeg\bin`) to your Windows User or System Environment `PATH`.
4. Open a new PowerShell terminal and verify:
   ```powershell
   ffmpeg -version
   ffprobe -version
   ```

---

## ⚡ 1-Click Automated Setup

If you downloaded the repository as a ZIP archive:

1. Extract the ZIP to your desired location.
2. Double-click **`setup_windows.bat`** (or run `.\setup_windows.ps1` in PowerShell).
3. The setup script will:
   - Verify Python and FFmpeg installations.
   - Create a clean virtual environment (`venv`).
   - Install all required dependencies from `requirements.txt`.
   - Copy `.env.example` to `.env`.
   - Generate sample test assets.

---

## 🔑 Configure API Key

Open `.env` in any text editor (Notepad, VS Code, etc.):

```env
AICREDITS_API_KEY=your_actual_api_key_here
AICREDITS_BASE_URL=https://api.aicredits.in/v1
AICREDITS_MODEL=gemini-2.5-flash
```

> **Note**: You can also use the built-in **Mock LLM mode** in the dashboard or via `--mock-llm` to test the pipeline completely free without an API key!

---

## 🚀 Launching the Application

### Option 1: Web Dashboard (Recommended)
Double-click **`start_windows.bat`** (or run `.\start_windows.ps1`).
Your browser will automatically open to **`http://127.0.0.1:8000`**.

### Option 2: Command Line (Headless)
Activate the virtual environment:
```cmd
venv\Scripts\activate.bat
```
Run the automated pipeline with sample data:
```cmd
python main.py --images-dir sample_data/images --prompts-file sample_data/prompts.json --voiceover-file sample_data/voiceover.txt --audio-file sample_data/audio.wav --timestamp-file sample_data/timestamps.txt --output-dir output --mock-llm --preview
```

---

## 🧪 Verifying Installation (Tests)

Run the full test suite to confirm everything is working properly:
```cmd
python -m pytest tests/ -v
```
All 30 automated tests should pass.
