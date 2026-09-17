from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from server import app

client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def ensure_sample_data():
    """Ensure sample audio and images exist before server tests run."""
    from scripts.create_sample_project import create_sample
    audio_path = Path("sample_data/audio.wav")
    if not audio_path.exists():
        create_sample(Path("sample_data"))


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


def test_api_status():
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["engine"] == "online"
    assert "aicredits_configured" in data
    assert "version" in data


def test_api_project_lifecycle_and_upload():
    # 1. Create project
    create_resp = client.post("/api/project/create")
    assert create_resp.status_code == 200
    pdata = create_resp.json()
    assert pdata["success"] is True
    pid = pdata["project_id"]

    # 2. Upload images
    img1 = ("1.jpg", b"fake_jpg_1", "image/jpeg")
    img2 = ("2.jpg", b"fake_jpg_2", "image/jpeg")
    up_resp = client.post(f"/api/project/{pid}/upload/images", files=[
        ("files", img1),
        ("files", img2)
    ])
    assert up_resp.status_code == 200
    up_data = up_resp.json()
    assert up_data["uploaded"] == 2
    assert up_data["metadata"]["count"] == 2

    # 3. Upload prompts
    prompts_bytes = b'{"1": "P1", "2": "P2"}'
    pr_resp = client.post(f"/api/project/{pid}/upload/prompts", files={
        "file": ("prompts.json", prompts_bytes, "application/json")
    })
    assert pr_resp.status_code == 200
    assert pr_resp.json()["metadata"]["count"] == 2

    # 4. Upload voiceover
    vo_bytes = b"Hello world narration text"
    vo_resp = client.post(f"/api/project/{pid}/upload/voiceover", files={
        "file": ("voiceover.txt", vo_bytes, "text/plain")
    })
    assert vo_resp.status_code == 200
    assert vo_resp.json()["metadata"]["word_count"] == 4

    # 5. Check readiness (audio still missing)
    ready_resp = client.get(f"/api/project/{pid}/readiness")
    assert ready_resp.status_code == 200
    rdata = ready_resp.json()
    assert rdata["ready"] is False
    assert "audio" in rdata["checklist"]
    assert rdata["checklist"]["images"]["status"] == "ok"

