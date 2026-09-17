from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from server import app

client = TestClient(app)


def test_dashboard_page():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "MR_MARGIN_ENGINE" in resp.text
    assert "id=\"timestampText\"" in resp.text
    assert "id=\"sendBtn\"" in resp.text
    assert "➤" in resp.text


def test_api_run_success():
    payload = {
        "images_dir": "sample_data/images",
        "prompts_file": "sample_data/prompts.json",
        "voiceover_file": "sample_data/voiceover.txt",
        "audio_file": "sample_data/audio.wav",
        "output_dir": "output",
        "timestamp_text": (
            "Image 1: 00:00 - 00:02.59\n"
            "Image 2: 00:02.59 - 00:05.04\n"
            "Image 3: 00:05.04 - 00:07.56\n"
            "Image 4: 00:07.56 - 00:09.34\n"
            "Image 5: 00:09.34 - 00:12.00"
        ),
        "mock_llm": True
    }
    resp = client.post("/api/run", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "video_path" in data


def test_api_run_missing_image_error():
    payload = {
        "images_dir": "sample_data/images",
        "prompts_file": "sample_data/prompts.json",
        "voiceover_file": "sample_data/voiceover.txt",
        "audio_file": "sample_data/audio.wav",
        "output_dir": "output",
        # Timestamp text only provides 4 images out of 5!
        "timestamp_text": (
            "Image 1: 00:00 - 00:03\n"
            "Image 2: 00:03 - 00:06\n"
            "Image 3: 00:06 - 00:09\n"
            "Image 4: 00:09 - 00:12"
        ),
        "mock_llm": True
    }
    resp = client.post("/api/run", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "TIMELINE" in data["error"]
    assert "Missing image indexes" in data["error"] or "5" in data["error"]


def test_api_video_stream():
    resp = client.get("/api/video")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"


def test_api_file_download():
    resp = client.get("/api/file/final_timeline.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
