import json
from pathlib import Path
import shutil
import subprocess
import pytest

from engine.capcut_exporter import CapCutExporter, detect_capcut_draft_root
from engine.capcut_validator import CapCutValidationError, CapCutValidator
from engine.config import AppConfig
from engine.models import ImageRecord, Timeline, TimelineEntry
from engine.pipeline import VideoAutomationPipeline


def _create_color_image(path: Path, color: str = "blue", width: int = 1920, height: int = 1080):
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s={width}x{height}:d=0.1",
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


@pytest.fixture
def sample_dataset(tmp_path: Path):
    """Fixture providing 3 images and 1 audio file with authoritative Timeline."""
    img_dir = tmp_path / "images"
    img_dir.mkdir()

    manifest: list[ImageRecord] = []
    colors = ["red", "green", "blue"]
    for i, col in enumerate(colors, start=1):
        p = img_dir / f"{i}_frame_{col}.jpg"
        _create_color_image(p, col)
        manifest.append(ImageRecord(index=i, filename=p.name, path=p, prompt=f"Prompt for {i}"))

    audio_file = tmp_path / "voiceover.wav"
    _create_audio(audio_file, 7.50)

    timeline_entries = [
        TimelineEntry(image_index=1, filename=manifest[0].filename, start=0.0, end=2.50),
        TimelineEntry(image_index=2, filename=manifest[1].filename, start=2.50, end=5.20),
        TimelineEntry(image_index=3, filename=manifest[2].filename, start=5.20, end=7.50),
    ]

    timeline = Timeline(
        audio_duration=7.50,
        image_count=3,
        timeline=timeline_entries
    )

    return {
        "manifest": manifest,
        "audio_file": audio_file,
        "timeline": timeline,
        "images_dir": img_dir,
        "tmp_path": tmp_path
    }


def test_capcut_project_generation_basic(sample_dataset):
    """Verify that CapCutExporter generates complete CapCut project hierarchy."""
    data = sample_dataset
    out_dir = data["tmp_path"] / "capcut_project"
    exporter = CapCutExporter()

    report = exporter.export(
        timeline=data["timeline"],
        images_manifest=data["manifest"],
        audio_path=data["audio_file"],
        output_dir=out_dir,
        project_name="TEST_PROJECT_BASIC",
        copy_assets=True,
        auto_register=False
    )

    assert report.success is True
    assert report.validation_passed is True
    assert report.image_count == 3
    assert report.duration_seconds == 7.50
    assert report.duration_us == 7500000

    # Essential root files
    assert (out_dir / "draft_content.json").exists()
    assert (out_dir / "draft_content.json.bak").exists()
    assert (out_dir / "draft_meta_info.json").exists()
    assert (out_dir / "template-2.tmp").exists()
    assert (out_dir / "draft_cover.jpg").exists()
    assert (out_dir / "draft_agency_config.json").exists()
    assert (out_dir / "attachment_pc_common.json").exists()
    assert (out_dir / "draft_settings").exists()
    assert (out_dir / "draft_virtual_store.json").exists()
    assert (out_dir / "timeline_layout.json").exists()

    # Multi-timeline structure
    project_json = out_dir / "Timelines" / "project.json"
    assert project_json.exists()
    pj_data = json.loads(project_json.read_text(encoding="utf-8"))
    main_tl_id = pj_data["main_timeline_id"]
    assert main_tl_id is not None
    assert (out_dir / "Timelines" / main_tl_id / "draft_content.json").exists()
    assert (out_dir / "Timelines" / main_tl_id / "draft_cover.jpg").exists()
    assert (out_dir / "Timelines" / main_tl_id / "attachment_editing.json").exists()

    # Self-contained assets
    assert (out_dir / "assets" / data["manifest"][0].filename).exists()
    assert (out_dir / "assets" / data["manifest"][1].filename).exists()
    assert (out_dir / "assets" / data["manifest"][2].filename).exists()
    assert (out_dir / "assets" / data["audio_file"].name).exists()


def test_capcut_timing_and_order_preservation(sample_dataset):
    """Verify microsecond timing, continuous boundaries, and 1..N sequential ordering."""
    data = sample_dataset
    out_dir = data["tmp_path"] / "capcut_project"
    exporter = CapCutExporter()

    exporter.export(
        timeline=data["timeline"],
        images_manifest=data["manifest"],
        audio_path=data["audio_file"],
        output_dir=out_dir,
        project_name="TEST_TIMING",
        copy_assets=True,
        auto_register=False
    )

    dc = json.loads((out_dir / "draft_content.json").read_text(encoding="utf-8"))
    v_track = [t for t in dc["tracks"] if t["type"] == "video"][0]
    segments = v_track["segments"]

    assert len(segments) == 3

    # Check 1: starts at 0
    assert segments[0]["target_timerange"]["start"] == 0

    # Check 2: exact durations
    # 0.0 -> 2.50 = 2,500,000 us
    assert segments[0]["target_timerange"]["duration"] == 2500000
    # 2.50 -> 5.20 = 2,700,000 us
    assert segments[1]["target_timerange"]["start"] == 2500000
    assert segments[1]["target_timerange"]["duration"] == 2700000
    # 5.20 -> 7.50 = 2,300,000 us
    assert segments[2]["target_timerange"]["start"] == 5200000
    assert segments[2]["target_timerange"]["duration"] == 2300000

    # Check 3: Final image end == total audio duration
    final_end_us = segments[2]["target_timerange"]["start"] + segments[2]["target_timerange"]["duration"]
    assert final_end_us == 7500000
    assert dc["duration"] == 7500000


def test_capcut_audio_placement(sample_dataset):
    """Verify audio track starts at 0 and covers total duration."""
    data = sample_dataset
    out_dir = data["tmp_path"] / "capcut_project"
    exporter = CapCutExporter()

    exporter.export(
        timeline=data["timeline"],
        images_manifest=data["manifest"],
        audio_path=data["audio_file"],
        output_dir=out_dir,
        auto_register=False
    )

    dc = json.loads((out_dir / "draft_content.json").read_text(encoding="utf-8"))
    a_track = [t for t in dc["tracks"] if t["type"] == "audio"][0]
    a_seg = a_track["segments"][0]

    assert a_seg["target_timerange"]["start"] == 0
    assert a_seg["target_timerange"]["duration"] == 7500000

    # Audio material check
    mat_id = a_seg["material_id"]
    a_mats = [m for m in dc["materials"]["audios"] if m["id"] == mat_id]
    assert len(a_mats) == 1
    assert a_mats[0]["duration"] == 7500000
    assert Path(a_mats[0]["path"]).exists()


def test_capcut_validator_detects_gap(sample_dataset):
    """Verify validator fails if an image gap is injected."""
    data = sample_dataset
    out_dir = data["tmp_path"] / "capcut_project"
    exporter = CapCutExporter()

    exporter.export(
        timeline=data["timeline"],
        images_manifest=data["manifest"],
        audio_path=data["audio_file"],
        output_dir=out_dir,
        auto_register=False
    )

    # Corrupt draft_content.json by injecting a 100ms gap
    content_file = out_dir / "draft_content.json"
    dc = json.loads(content_file.read_text(encoding="utf-8"))
    v_track = [t for t in dc["tracks"] if t["type"] == "video"][0]
    # Introduce gap between segment 0 and 1
    v_track["segments"][1]["target_timerange"]["start"] += 100000
    content_file.write_text(json.dumps(dc), encoding="utf-8")

    is_valid, errors = CapCutValidator.validate(
        project_dir=out_dir,
        expected_timeline=data["timeline"],
        expected_manifest=data["manifest"]
    )
    assert is_valid is False
    assert any("Gap detected" in e for e in errors)


def test_capcut_validator_detects_overlap(sample_dataset):
    """Verify validator fails if an image overlap is injected."""
    data = sample_dataset
    out_dir = data["tmp_path"] / "capcut_project"
    exporter = CapCutExporter()

    exporter.export(
        timeline=data["timeline"],
        images_manifest=data["manifest"],
        audio_path=data["audio_file"],
        output_dir=out_dir,
        auto_register=False
    )

    content_file = out_dir / "draft_content.json"
    dc = json.loads(content_file.read_text(encoding="utf-8"))
    v_track = [t for t in dc["tracks"] if t["type"] == "video"][0]
    # Introduce overlap
    v_track["segments"][1]["target_timerange"]["start"] -= 50000
    content_file.write_text(json.dumps(dc), encoding="utf-8")

    is_valid, errors = CapCutValidator.validate(
        project_dir=out_dir,
        expected_timeline=data["timeline"],
        expected_manifest=data["manifest"]
    )
    assert is_valid is False
    assert any("Overlap detected" in e for e in errors)


def test_capcut_validator_detects_missing_image(sample_dataset):
    """Verify validator fails if an image clip is missing."""
    data = sample_dataset
    out_dir = data["tmp_path"] / "capcut_project"
    exporter = CapCutExporter()

    exporter.export(
        timeline=data["timeline"],
        images_manifest=data["manifest"],
        audio_path=data["audio_file"],
        output_dir=out_dir,
        auto_register=False
    )

    content_file = out_dir / "draft_content.json"
    dc = json.loads(content_file.read_text(encoding="utf-8"))
    v_track = [t for t in dc["tracks"] if t["type"] == "video"][0]
    v_track["segments"].pop()  # Remove last image clip
    content_file.write_text(json.dumps(dc), encoding="utf-8")

    is_valid, errors = CapCutValidator.validate(
        project_dir=out_dir,
        expected_timeline=data["timeline"],
        expected_manifest=data["manifest"]
    )
    assert is_valid is False
    assert any("clip count mismatch" in e.lower() for e in errors)


def test_capcut_validator_detects_missing_media_file(sample_dataset):
    """Verify validator fails if a referenced media file is deleted from assets."""
    data = sample_dataset
    out_dir = data["tmp_path"] / "capcut_project"
    exporter = CapCutExporter()

    exporter.export(
        timeline=data["timeline"],
        images_manifest=data["manifest"],
        audio_path=data["audio_file"],
        output_dir=out_dir,
        auto_register=False
    )

    # Delete image 1 from assets
    target_asset = out_dir / "assets" / data["manifest"][0].filename
    target_asset.unlink()

    is_valid, errors = CapCutValidator.validate(
        project_dir=out_dir,
        expected_timeline=data["timeline"],
        expected_manifest=data["manifest"]
    )
    assert is_valid is False
    assert any("does not exist on disk" in e for e in errors)


def test_capcut_large_dataset_20_images(tmp_path: Path):
    """Verify export and validation for 20 images."""
    img_dir = tmp_path / "images_20"
    img_dir.mkdir()

    manifest: list[ImageRecord] = []
    timeline_entries: list[TimelineEntry] = []
    cur_time = 0.0

    for i in range(1, 21):
        p = img_dir / f"{i}_shot.jpg"
        _create_color_image(p, "red", width=640, height=360)
        manifest.append(ImageRecord(index=i, filename=p.name, path=p, prompt=f"P {i}"))
        end_time = round(cur_time + 1.25, 2)
        timeline_entries.append(TimelineEntry(image_index=i, filename=p.name, start=cur_time, end=end_time))
        cur_time = end_time

    audio_file = tmp_path / "audio_20.wav"
    _create_audio(audio_file, cur_time)

    timeline = Timeline(audio_duration=cur_time, image_count=20, timeline=timeline_entries)

    out_dir = tmp_path / "capcut_20"
    exporter = CapCutExporter()
    report = exporter.export(
        timeline=timeline,
        images_manifest=manifest,
        audio_path=audio_file,
        output_dir=out_dir,
        project_name="TEST_20_IMAGES",
        auto_register=False
    )

    assert report.success is True
    assert report.validation_passed is True
    assert report.image_count == 20
    assert report.duration_seconds == cur_time


def test_capcut_deterministic_regeneration(sample_dataset):
    """Verify identical logical output across multiple runs with same timeline."""
    data = sample_dataset
    out1 = data["tmp_path"] / "run1"
    out2 = data["tmp_path"] / "run2"
    exporter = CapCutExporter()

    r1 = exporter.export(data["timeline"], data["manifest"], data["audio_file"], out1, auto_register=False)
    r2 = exporter.export(data["timeline"], data["manifest"], data["audio_file"], out2, auto_register=False)

    assert r1.duration_us == r2.duration_us
    assert r1.image_count == r2.image_count

    c1 = json.loads((out1 / "draft_content.json").read_text(encoding="utf-8"))
    c2 = json.loads((out2 / "draft_content.json").read_text(encoding="utf-8"))

    # Compare segment durations and relative positions
    segs1 = c1["tracks"][0]["segments"]
    segs2 = c2["tracks"][0]["segments"]
    for s1, s2 in zip(segs1, segs2):
        assert s1["target_timerange"] == s2["target_timerange"]


def test_capcut_pipeline_e2e_integration(tmp_path: Path):
    """Full pipeline end-to-end verifying both final_video.mp4 and capcut_project/ are generated."""
    images_dir = tmp_path / "images"
    images_dir.mkdir()

    colors = ["red", "blue", "green"]
    for i, col in enumerate(colors, start=1):
        _create_color_image(images_dir / f"{i}_scene.jpg", col)

    prompts_file = tmp_path / "prompts.json"
    prompts_file.write_text(json.dumps({
        "1": "First scene",
        "2": "Second scene",
        "3": "Third scene"
    }))

    vo_file = tmp_path / "voiceover.txt"
    vo_file.write_text("Scene one text.\n\nScene two text.\n\nScene three text.")

    audio_file = tmp_path / "audio.wav"
    _create_audio(audio_file, 6.0)

    out_dir = tmp_path / "output"
    config = AppConfig(
        video_width=640,
        video_height=360,
        video_fps=15,
        video_preset="ultrafast",
        output_dir=out_dir,
        export_capcut_project=True,
        capcut_auto_register=False
    )

    timestamp_input = (
        "Image 1: 00:00 - 00:02.0\n"
        "Image 2: 00:02.0 - 00:04.0\n"
        "Image 3: 00:04.0 - 00:06.0"
    )

    pipeline = VideoAutomationPipeline(config=config, use_mock_llm=True)
    final_video = pipeline.run(
        images_dir=images_dir,
        prompts_file=prompts_file,
        voiceover_text_file=vo_file,
        voiceover_audio_file=audio_file,
        timestamp_text=timestamp_input,
        output_dir=out_dir
    )

    # 1. Video generated and verified
    assert final_video.exists()
    assert (out_dir / "final_video.mp4").exists()

    # 2. CapCut project generated and verified
    capcut_dir = out_dir / "capcut_project"
    assert capcut_dir.exists()
    assert (capcut_dir / "draft_content.json").exists()
    assert (capcut_dir / "draft_meta_info.json").exists()
    assert (capcut_dir / "assets").exists()
    assert (out_dir / "capcut_export_report.json").exists()

    report_data = json.loads((out_dir / "capcut_export_report.json").read_text(encoding="utf-8"))
    assert report_data["success"] is True
    assert report_data["validation_passed"] is True
    assert report_data["image_count"] == 3
