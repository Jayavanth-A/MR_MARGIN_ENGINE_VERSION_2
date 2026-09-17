import json
from pathlib import Path
import pytest

from engine.models import Timeline, TimelineEntry
from engine.validator import (
    InputValidator,
    TimelineValidator,
    extract_numeric_index,
    parse_prompts_file,
)


def test_extract_numeric_index():
    assert extract_numeric_index("1_exterior-of-laundromat.jpg") == 1
    assert extract_numeric_index("02_interior.png") == 2
    assert extract_numeric_index("653_closing.webp") == 653
    assert extract_numeric_index("42.jpg") == 42
    assert extract_numeric_index("no_prefix.jpg") is None
    assert extract_numeric_index("exterior_1.jpg") is None


def test_parse_prompts_file_formats(tmp_path: Path):
    # Test JSON list format
    list_file = tmp_path / "prompts_list.json"
    list_file.write_text(json.dumps([
        {"index": 1, "prompt": "Prompt 1"},
        {"index": 2, "prompt": "Prompt 2"}
    ]))
    res1 = parse_prompts_file(list_file)
    assert res1 == {1: "Prompt 1", 2: "Prompt 2"}

    # Test JSON dict format
    dict_file = tmp_path / "prompts_dict.json"
    dict_file.write_text(json.dumps({
        "1": "Prompt 1",
        "2": "Prompt 2"
    }))
    res2 = parse_prompts_file(dict_file)
    assert res2 == {1: "Prompt 1", 2: "Prompt 2"}


def test_input_validator_success(tmp_path: Path):
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    (img_dir / "1_intro.jpg").write_text("dummy")
    (img_dir / "2_middle.jpg").write_text("dummy")
    (img_dir / "3_end.jpg").write_text("dummy")

    prompts_file = tmp_path / "prompts.json"
    prompts_file.write_text(json.dumps({
        "1": "Exterior view",
        "2": "Inside machines",
        "3": "Worker closing"
    }))

    report, sequence = InputValidator.validate_inputs(img_dir, prompts_file)
    assert report.is_valid is True
    assert sequence is not None
    assert len(sequence) == 3
    assert [img.index for img in sequence] == [1, 2, 3]


def test_input_validator_missing_image(tmp_path: Path):
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    (img_dir / "1_intro.jpg").write_text("dummy")
    # Image 2 is missing!
    (img_dir / "3_end.jpg").write_text("dummy")

    prompts_file = tmp_path / "prompts.json"
    prompts_file.write_text(json.dumps({
        "1": "Exterior view",
        "2": "Inside machines",
        "3": "Worker closing"
    }))

    report, sequence = InputValidator.validate_inputs(img_dir, prompts_file)
    assert report.is_valid is False
    assert sequence is None
    assert 2 in report.missing_images
    assert 2 in report.sequence_gaps

    report_str = report.format_console_report()
    assert "INPUT VALIDATION FAILED" in report_str
    assert "Missing image indexes" in report_str
    assert "2" in report_str


def test_input_validator_missing_prompt(tmp_path: Path):
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    (img_dir / "1_intro.jpg").write_text("dummy")
    (img_dir / "2_middle.jpg").write_text("dummy")

    prompts_file = tmp_path / "prompts.json"
    # Prompt 2 is missing!
    prompts_file.write_text(json.dumps({
        "1": "Exterior view"
    }))

    report, sequence = InputValidator.validate_inputs(img_dir, prompts_file)
    assert report.is_valid is False
    assert sequence is None
    assert 2 in report.missing_prompts

    report_str = report.format_console_report()
    assert "Missing prompt indexes" in report_str
    assert "2" in report_str


def test_input_validator_invalid_filename(tmp_path: Path):
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    (img_dir / "1_intro.jpg").write_text("dummy")
    (img_dir / "invalid_name.jpg").write_text("dummy")

    prompts_file = tmp_path / "prompts.json"
    prompts_file.write_text(json.dumps({
        "1": "Exterior view"
    }))

    report, sequence = InputValidator.validate_inputs(img_dir, prompts_file)
    assert report.is_valid is False
    assert "invalid_name.jpg" in report.invalid_filenames


def test_timeline_validator():
    timeline = Timeline(
        audio_duration=10.0,
        image_count=3,
        timeline=[
            TimelineEntry(image_index=1, filename="1.jpg", start=0.0, end=3.5),
            TimelineEntry(image_index=2, filename="2.jpg", start=3.5, end=7.0),
            TimelineEntry(image_index=3, filename="3.jpg", start=7.0, end=10.0),
        ]
    )
    report = TimelineValidator.validate_timeline(timeline, expected_image_count=3, expected_audio_duration=10.0)
    assert report.is_valid is True
    assert len(report.errors) == 0


def test_timeline_validator_gap_detected():
    timeline = Timeline(
        audio_duration=10.0,
        image_count=2,
        timeline=[
            TimelineEntry(image_index=1, filename="1.jpg", start=0.0, end=4.0),
            # Gap of 1.0 second between 4.0 and 5.0
            TimelineEntry(image_index=2, filename="2.jpg", start=5.0, end=10.0),
        ]
    )
    report = TimelineValidator.validate_timeline(timeline, expected_image_count=2, expected_audio_duration=10.0)
    assert report.is_valid is False
    assert any("Gap detected" in err for err in report.errors)


def test_timeline_validator_leftover_dump_warning():
    # 10 images where tail images are squeezed down to 0.1s while earlier are 3.0s
    entries = []
    t = 0.0
    for i in range(1, 9):
        entries.append(TimelineEntry(image_index=i, filename=f"{i}.jpg", start=t, end=t + 3.0))
        t += 3.0
    for i in range(9, 11):
        entries.append(TimelineEntry(image_index=i, filename=f"{i}.jpg", start=t, end=t + 0.1))
        t += 0.1

    timeline = Timeline(audio_duration=t, image_count=10, timeline=entries)
    report = TimelineValidator.validate_timeline(timeline, expected_image_count=10, expected_audio_duration=t)
    assert report.leftover_dump_detected is True
    assert any("Possible leftover dumping detected" in w for w in report.warnings)
