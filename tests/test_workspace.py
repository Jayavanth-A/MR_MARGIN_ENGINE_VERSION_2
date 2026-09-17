import json
import pytest
from pathlib import Path

from engine.workspace import WorkspaceManager, WorkspaceError


def test_workspace_sanitization():
    assert WorkspaceManager.sanitize_filename("image.png") == "image.png"
    assert WorkspaceManager.sanitize_filename("../../secret.txt") == "secret.txt"
    assert WorkspaceManager.sanitize_filename("C:\\Windows\\System32\\calc.exe") == "calc.exe"
    assert WorkspaceManager.sanitize_filename("1_exterior (1).png") == "1_exterior_1_.png"
    # Empty or dots fallback
    clean = WorkspaceManager.sanitize_filename("....")
    assert clean.startswith("file_")


def test_workspace_creation(tmp_path):
    wm = WorkspaceManager(base_dir=tmp_path)
    proj_id = wm.create_project("test_proj_1")
    assert proj_id == "test_proj_1"

    proj_dir = wm.get_project_dir("test_proj_1")
    assert proj_dir.exists()
    assert (proj_dir / "images").exists()
    assert (proj_dir / "output").exists()

    with pytest.raises(WorkspaceError):
        wm.create_project("../malicious")


def test_workspace_uploads_and_metadata(tmp_path):
    wm = WorkspaceManager(base_dir=tmp_path)
    proj_id = wm.create_project("test_proj_2")

    # Upload images
    wm.save_image(proj_id, "1.png", b"fake_png_data_1")
    wm.save_image(proj_id, "2.png", b"fake_png_data_2")
    wm.save_image(proj_id, "3.png", b"fake_png_data_3")

    img_meta = wm.get_images_metadata(proj_id)
    assert img_meta["count"] == 3
    assert img_meta["is_valid"] is True
    assert img_meta["min_index"] == 1
    assert img_meta["max_index"] == 3
    assert len(img_meta["missing_indices"]) == 0

    # Upload prompts
    prompts_content = json.dumps({"1": "Prompt 1", "2": "Prompt 2", "3": "Prompt 3"}).encode("utf-8")
    wm.save_prompts(proj_id, "prompts.json", prompts_content)
    p_meta = wm.get_prompts_metadata(proj_id)
    assert p_meta["exists"] is True
    assert p_meta["count"] == 3

    # Upload voiceover
    vo_content = "This is a test voiceover narration with eight words.".encode("utf-8")
    wm.save_voiceover(proj_id, "voiceover.txt", vo_content)
    vo_meta = wm.get_voiceover_metadata(proj_id)
    assert vo_meta["exists"] is True
    assert vo_meta["word_count"] == 9

    # Path traversal check on thumbnail
    with pytest.raises(WorkspaceError):
        wm.get_thumbnail_path(proj_id, "../secret.txt")
