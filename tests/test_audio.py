from pathlib import Path
import subprocess
import pytest

from engine.audio import format_timestamp, get_audio_duration


def test_format_timestamp():
    assert format_timestamp(0.0) == "00:00.000"
    assert format_timestamp(3.82) == "00:03.820"
    assert format_timestamp(65.432) == "01:05.432"
    assert format_timestamp(1503.421) == "25:03.421"
    assert format_timestamp(3665.12) == "01:01:05.120"


def test_get_audio_duration(tmp_path: Path):
    audio_path = tmp_path / "test_tone.wav"
    # Generate a 2.5 second audio tone using ffmpeg
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "sine=frequency=1000:duration=2.5",
        "-c:a", "pcm_s16le",
        str(audio_path)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    dur = get_audio_duration(audio_path)
    assert pytest.approx(dur, rel=1e-2) == 2.5


def test_verify_rendered_video(tmp_path: Path):
    from engine.audio import verify_rendered_video

    # Generate a simple 2.0s video with audio
    video_path = tmp_path / "test_verify.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "color=c=blue:s=320x240:d=2.0",
        "-f", "lavfi",
        "-i", "sine=frequency=1000:duration=2.0",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-shortest",
        str(video_path)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    report = verify_rendered_video(video_path, expected_audio_duration=2.0, tolerance=0.5)
    assert report.video_valid is True
    assert report.width == 320
    assert report.height == 240
    assert report.video_codec == "h264"
    assert report.audio_codec == "aac"
    assert pytest.approx(report.video_duration, abs=0.2) == 2.0

    # Test non-existent video
    missing_report = verify_rendered_video(tmp_path / "non_existent.mp4", 2.0)
    assert missing_report.video_valid is False
    assert len(missing_report.errors) > 0

