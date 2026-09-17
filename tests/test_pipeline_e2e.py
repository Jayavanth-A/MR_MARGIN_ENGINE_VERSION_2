import json
from pathlib import Path
import subprocess
import pytest

from engine.audio import get_audio_duration
from engine.config import AppConfig
from engine.pipeline import VideoAutomationPipeline


def _create_color_image(path: Path, color: str):
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s=480x270:d=0.1",
        "-frames:v", "1",
        str(path)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def _create_audio(path: Path, duration: float):
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={duration}",
        "-c:a", "pcm_s16le",
        str(path)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def test_pipeline_e2e(tmp_path: Path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()

    # Create 4 test images
    colors = ["red", "blue", "green", "yellow"]
    for i, col in enumerate(colors, start=1):
        _create_color_image(images_dir / f"{i}_scene.jpg", col)

    # Create prompts.json
    prompts_file = tmp_path / "prompts.json"
    prompts_file.write_text(json.dumps({
        "1": "Sunrise over the cityscape",
        "2": "Morning crowds gathering on the street",
        "3": "Shop owners rolling up the shutters",
        "4": "The day begins in full energy"
    }))

    # Create voiceover.txt
    vo_file = tmp_path / "voiceover.txt"
    vo_file.write_text(
        "Every morning the city wakes up with quiet anticipation.\n\n"
        "Soon, people flood the avenues and streets.\n\n"
        "Stores open their doors to welcome customers.\n\n"
        "And another vibrant day begins."
    )

    # Create audio file (4.5 seconds)
    audio_file = tmp_path / "voiceover.wav"
    _create_audio(audio_file, 4.5)

    out_dir = tmp_path / "output"

    config = AppConfig(
        video_width=640,
        video_height=360,
        video_fps=15,
        video_preset="ultrafast",
        output_dir=out_dir
    )

    # Timestamp instructions provided by user
    timestamp_input = (
        "Image 1: 00:00 - 00:01.0\n"
        "Image 2: 00:01.0 - 00:02.2\n"
        "Image 3: 00:02.2 - 00:03.5\n"
        "Image 4: 00:03.5 - 00:04.5"
    )

    pipeline = VideoAutomationPipeline(config=config, use_mock_llm=True)
    final_video = pipeline.run(
        images_dir=images_dir,
        prompts_file=prompts_file,
        voiceover_text_file=vo_file,
        voiceover_audio_file=audio_file,
        timestamp_text=timestamp_input,
        output_dir=out_dir,
        generate_preview=True
    )

    assert final_video.exists()
    assert (out_dir / "source_timestamp_text.txt").exists()
    assert (out_dir / "llm_timeline.json").exists()
    assert (out_dir / "final_timeline.json").exists()
    assert (out_dir / "validation_report.json").exists()
    assert (out_dir / "timing_report.json").exists()
    assert (out_dir / "render_log.txt").exists()
    assert (out_dir / "preview.mp4").exists()

    # Verify exact user text preservation
    saved_text = (out_dir / "source_timestamp_text.txt").read_text(encoding="utf-8")
    assert saved_text.strip() == timestamp_input.strip()

    # Check final_timeline.json content
    tl_data = json.loads((out_dir / "final_timeline.json").read_text(encoding="utf-8"))
    assert tl_data["image_count"] == 4
    assert len(tl_data["timeline"]) == 4
    assert tl_data["timeline"][0]["start"] == 0.0
    assert pytest.approx(tl_data["timeline"][-1]["end"], abs=0.05) == 4.5

    # Check video duration matches audio duration via ffprobe
    from engine.audio import verify_rendered_video
    v_report = verify_rendered_video(final_video, expected_audio_duration=4.5)
    assert v_report.video_valid is True
    assert v_report.video_codec == "h264"
    assert v_report.audio_codec == "aac"
