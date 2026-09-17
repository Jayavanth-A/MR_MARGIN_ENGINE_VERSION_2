# Deployment Guide: MR_MARGIN_ENGINE

This guide covers deploying **MR_MARGIN_ENGINE** across various target environments, including Docker, local production servers, and cloud container environments.

---

## 🐳 Option 1: Docker Deployment (Cross-Platform)

MR_MARGIN_ENGINE includes a production-ready `Dockerfile` and `docker-compose.yml` with FFmpeg and Python 3.11 pre-configured.

### 1. Build & Run with Docker Compose
```bash
# 1. Create .env from template
cp .env.example .env
# Edit .env with your credentials

# 2. Build and start container in detached mode
docker compose up -d --build

# 3. View live server logs
docker compose logs -f
```
The web dashboard will be available at `http://localhost:8000`.

### 2. Manual Docker Build & Run
```bash
docker build -t mr_margin_engine:latest .

docker run -d \
  --name mr_margin_engine \
  -p 8000:8000 \
  -v $(pwd)/output:/app/output \
  -v $(pwd)/cache:/app/cache \
  --env-file .env \
  mr_margin_engine:latest
```

---

## 🐧 Option 2: Linux / Ubuntu Production Host

### 1. System Dependencies
```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv ffmpeg curl git
```

### 2. Setup Application
```bash
git clone <repository_url> MR_MARGIN_ENGINE
cd MR_MARGIN_ENGINE

python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
```

### 3. Systemd Service Configuration
Create `/etc/systemd/system/mr_margin_engine.service`:
```ini
[Unit]
Description=MR_MARGIN_ENGINE Video Automation Server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/MR_MARGIN_ENGINE
ExecStart=/home/ubuntu/MR_MARGIN_ENGINE/venv/bin/python main.py --serve --port 8000
Restart=always
RestartSec=5
EnvironmentFile=/home/ubuntu/MR_MARGIN_ENGINE/.env

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable mr_margin_engine
sudo systemctl start mr_margin_engine
```

---

## ⚙️ Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `AICREDITS_API_KEY` | None (Required for live) | AICredits.in API access token |
| `AICREDITS_BASE_URL` | `https://api.aicredits.in/v1` | OpenAI-compatible endpoint base URL |
| `AICREDITS_MODEL` | `gemini-2.5-flash` | Model used for timestamp normalization |
| `VIDEO_WIDTH` | `1920` | Output video horizontal resolution |
| `VIDEO_HEIGHT` | `1080` | Output video vertical resolution |
| `VIDEO_FPS` | `30` | Output frame rate |
| `VIDEO_CRF` | `20` | H.264 quality factor (lower = higher quality) |
| `VIDEO_PRESET` | `medium` | H.264 encoding preset (`ultrafast` to `veryslow`) |
| `AUDIO_BITRATE` | `192k` | AAC audio bitrate |
| `CACHE_DIR` | `cache` | Directory storing SHA256 request caches |
| `OUTPUT_DIR` | `output` | Directory storing generated video and reports |
