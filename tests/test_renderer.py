from pathlib import Path
import subprocess
import pytest

from engine.config import AppConfig
from engine.models import Timeline, TimelineEntry
from engine.renderer import VideoRenderer


def _create_test_image(path: Path, color: str = "red"):
    """Create a 100x100 solid color image using ffmpeg lavfi."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s=320x240:d=0.1",
        "-frames:v", "1",
        str(path)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def _create_test_audio(path: Path, duration: float = 3.0):
    """Create a sine tone test audio using ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=800:duration={duration}",
        "-c:a", "pcm_s16le",
        str(path)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def test_concat_script_generation(tmp_path: Path):
    config = AppConfig()
    renderer = VideoRenderer(config)

    timeline = Timeline(
        audio_duration=6.0,
        image_count=2,
        timeline=[
            TimelineEntry(image_index=1, filename="1.jpg", start=0.0, end=2.5),
            TimelineEntry(image_index=2, filename="2.jpg", start=2.5, end=6.0),
        ]
    )

    script_path = tmp_path / "concat.txt"
    renderer.generate_concat_script(timeline, tmp_path, script_path)

    content = script_path.read_text(encoding="utf-8")
    assert "ffconcat version 1.0" in content
    assert "duration 2.5000" in content
    assert "duration 3.5000" in content
    # Last image repeated at the end
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    assert lines[-1] == f"file '{(tmp_path / '2.jpg').resolve().as_posix()}'"


def test_video_render_execution(tmp_path: Path):
    config = AppConfig(video_width=640, video_height=360, video_fps=15, video_preset="ultrafast")
    renderer = VideoRenderer(config)

    img1 = tmp_path / "1.jpg"
    img2 = tmp_path / "2.jpg"
    _create_test_image(img1, "blue")
    _create_test_image(img2, "green")

    audio_file = tmp_path / "test.wav"
    _create_test_audio(audio_file, duration=2.0)

    timeline = Timeline(
        audio_duration=2.0,
        image_count=2,
        timeline=[
            TimelineEntry(image_index=1, filename="1.jpg", start=0.0, end=1.0),
            TimelineEntry(image_index=2, filename="2.jpg", start=1.0, end=2.0),
        ]
    )

    output_video = tmp_path / "rendered.mp4"
    renderer.render(
        timeline=timeline,
        images_dir=tmp_path,
        audio_path=audio_file,
        output_path=output_video
    )

    assert output_video.exists()
    assert output_video.stat().st_size > 0
