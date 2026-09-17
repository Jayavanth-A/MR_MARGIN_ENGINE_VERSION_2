import json
from pathlib import Path
import pytest

from engine.config import AppConfig
from engine.models import ImageRecord
from engine.pipeline import VideoAutomationPipeline
from engine.validator import (
    InputValidator,
    parse_prompts_file,
    parse_raw_prompt_blocks,
    parse_voiceover_paragraphs,
)
from engine.workspace import WorkspaceManager


def test_parse_raw_prompt_blocks_basic():
    raw = """A cinematic shot of a massive factory at night, dramatic lighting

A close-up of a robotic arm assembling a component

A wide aerial view of the city with thousands of lights

A scientist working inside a futuristic laboratory"""

    prompts = parse_raw_prompt_blocks(raw)
    assert len(prompts) == 4
    assert prompts[0] == "A cinematic shot of a massive factory at night, dramatic lighting"
    assert prompts[1] == "A close-up of a robotic arm assembling a component"
    assert prompts[2] == "A wide aerial view of the city with thousands of lights"
    assert prompts[3] == "A scientist working inside a futuristic laboratory"


def test_parse_raw_prompt_blocks_multiple_blank_lines_and_crlf():
    raw = "Prompt 1 text\r\n\r\n\r\n\r\nPrompt 2 text\r\n\n\nPrompt 3 text\n\n"
    prompts = parse_raw_prompt_blocks(raw)
    assert len(prompts) == 3
    assert prompts == ["Prompt 1 text", "Prompt 2 text", "Prompt 3 text"]


def test_parse_raw_prompt_blocks_empty():
    assert parse_raw_prompt_blocks("") == []
    assert parse_raw_prompt_blocks("   \n\n   \n  ") == []


def test_parse_voiceover_paragraphs_basic(tmp_path: Path):
    text = """Paragraph 1 starts here with some story.
Continuing the first paragraph.

Paragraph 2 explains the next event in chronological sequence.

Paragraph 3 concludes the narration."""

    paragraphs = parse_voiceover_paragraphs(text)
    assert len(paragraphs) == 3
    assert "Paragraph 1 starts here" in paragraphs[0]
    assert "Continuing the first paragraph." in paragraphs[0]
    assert paragraphs[1] == "Paragraph 2 explains the next event in chronological sequence."
    assert paragraphs[2] == "Paragraph 3 concludes the narration."

    # Test reading from Path
    vo_file = tmp_path / "vo.txt"
    vo_file.write_text(text, encoding="utf-8")
    from_file = parse_voiceover_paragraphs(vo_file)
    assert from_file == paragraphs


def test_parse_prompts_file_raw_txt(tmp_path: Path):
    p_file = tmp_path / "prompts.txt"
    p_file.write_text("""First prompt for image 1

Second prompt for image 2

Third prompt for image 3""", encoding="utf-8")

    prompts_map = parse_prompts_file(p_file)
    assert len(prompts_map) == 3
    assert prompts_map[1] == "First prompt for image 1"
    assert prompts_map[2] == "Second prompt for image 2"
    assert prompts_map[3] == "Third prompt for image 3"


def test_strict_1_to_1_count_mismatch_images_gt_prompts(tmp_path: Path):
    # 5 images vs 3 prompts
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    for i in range(1, 6):
        (img_dir / f"{i}_img.jpg").write_text("fake")

    prompts_file = tmp_path / "prompts.txt"
    prompts_file.write_text("P1\n\nP2\n\nP3", encoding="utf-8")

    report, sequence = InputValidator.validate_inputs(img_dir, prompts_file)
    assert report.is_valid is False
    assert sequence is None
    assert any("Prompt count mismatch: 5 images found, but 3 prompts provided" in err for err in report.errors)


def test_strict_1_to_1_count_mismatch_prompts_gt_images(tmp_path: Path):
    # 3 images vs 5 prompts
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    for i in range(1, 4):
        (img_dir / f"{i}_img.jpg").write_text("fake")

    prompts_file = tmp_path / "prompts.txt"
    prompts_file.write_text("P1\n\nP2\n\nP3\n\nP4\n\nP5", encoding="utf-8")

    report, sequence = InputValidator.validate_inputs(img_dir, prompts_file)
    assert report.is_valid is False
    assert sequence is None
    assert any("Prompt count mismatch: 3 images found, but 5 prompts provided" in err for err in report.errors)


def test_strict_1_to_1_count_match(tmp_path: Path):
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    for i in range(1, 4):
        (img_dir / f"{i}_photo.png").write_text("fake")

    prompts_file = tmp_path / "prompts.txt"
    prompts_file.write_text("Prompt one\n\nPrompt two\n\nPrompt three", encoding="utf-8")

    report, sequence = InputValidator.validate_inputs(img_dir, prompts_file)
    assert report.is_valid is True
    assert sequence is not None
    assert len(sequence) == 3
    assert sequence[0].index == 1
    assert sequence[0].prompt == "Prompt one"
    assert sequence[1].index == 2
    assert sequence[1].prompt == "Prompt two"
    assert sequence[2].index == 3
    assert sequence[2].prompt == "Prompt three"


def test_raw_files_unmodified_on_disk(tmp_path: Path):
    p_file = tmp_path / "prompts.txt"
    original_prompts = "  Prompt A  \n\n  Prompt B  \n"
    p_file.write_text(original_prompts, encoding="utf-8")

    vo_file = tmp_path / "voiceover.txt"
    original_vo = "  Paragraph A  \n\n  Paragraph B  \n"
    vo_file.write_text(original_vo, encoding="utf-8")

    # Parse multiple times
    _ = parse_prompts_file(p_file)
    _ = parse_voiceover_paragraphs(vo_file)

    # Verify bytes and content on disk remain unaltered
    assert p_file.read_text(encoding="utf-8") == original_prompts
    assert vo_file.read_text(encoding="utf-8") == original_vo


def test_workspace_supports_raw_txt_prompts_and_paragraphs(tmp_path: Path):
    ws = WorkspaceManager(base_dir=str(tmp_path / "ws"))
    pid = ws.create_project("test_raw")

    # Upload 3 images
    for i in range(1, 4):
        ws.save_image(pid, f"{i}_test.jpg", b"fake_bytes")

    # Upload prompts.txt
    prompts_txt = b"Prompt A\n\nPrompt B\n\nPrompt C"
    ws.save_prompts(pid, "raw_prompts.txt", prompts_txt)

    # Upload voiceover.txt
    vo_txt = b"Paragraph one.\n\nParagraph two.\n\nParagraph three."
    ws.save_voiceover(pid, "my_vo.txt", vo_txt)

    p_meta = ws.get_prompts_metadata(pid)
    assert p_meta["exists"] is True
    assert p_meta["count"] == 3
    assert len(p_meta["preview_items"]) == 3
    assert p_meta["mapping_preview"][0] == "Image 1 -> Prompt 1" or p_meta["mapping_preview"][0] == "Image 1 \u2192 Prompt 1"

    vo_meta = ws.get_voiceover_metadata(pid)
    assert vo_meta["exists"] is True
    assert vo_meta["paragraph_count"] == 3
    assert len(vo_meta["paragraphs_preview"]) == 3

    readiness = ws.get_project_readiness(pid)
    assert readiness["checklist"]["prompts"]["count"] == 3
    assert readiness["checklist"]["voiceover"]["paragraphs"] == 3
    assert "3" in readiness["checklist"]["prompts"]["mapping_status"]


def test_workspace_readiness_rejects_count_mismatch(tmp_path: Path):
    ws = WorkspaceManager(base_dir=str(tmp_path / "ws"))
    pid = ws.create_project("test_mismatch")

    # 2 images
    for i in range(1, 3):
        ws.save_image(pid, f"{i}_test.jpg", b"fake_bytes")

    # 3 prompts
    ws.save_prompts(pid, "prompts.txt", b"P1\n\nP2\n\nP3")

    readiness = ws.get_project_readiness(pid)
    assert readiness["ready"] is False
    assert any("Prompt count mismatch" in err for err in readiness["errors"])
