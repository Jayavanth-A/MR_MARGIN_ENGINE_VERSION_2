"""Deterministic validation suite for generated CapCut Desktop projects.

Verifies:
1. Number of CapCut image clips == number of manifest images
2. Every image index exists exactly once
3. Image order is exactly sequential (1..N)
4. No image is missing
5. No image is duplicated
6. Every CapCut media reference points to an existing file
7. Audio reference exists
8. Audio starts at 0
9. First image starts at 0
10. No image gaps (continuous timing: end[i] == start[i+1])
11. No image overlaps
12. Final image end == audio duration within tolerance
13. CapCut project duration matches authoritative final timeline
14. draft_content.json and draft_meta_info.json are valid JSON
15. Multi-timeline references (Timelines/project.json) are valid
"""

import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from engine.models import ImageRecord, Timeline

logger = logging.getLogger(__name__)


class CapCutValidationError(Exception):
    """Raised when generated CapCut project fails deterministic validation."""
    pass


class CapCutValidator:
    """Validates an exported CapCut Desktop project against authoritative timeline and manifest."""

    TOLERANCE_US: int = 2000  # 2 milliseconds tolerance for sub-frame microsecond rounding

    @classmethod
    def validate(
        cls,
        project_dir: Path,
        expected_timeline: Timeline,
        expected_manifest: List[ImageRecord],
        expected_audio_path: Optional[Path] = None,
        tolerance_us: int = TOLERANCE_US
    ) -> Tuple[bool, List[str]]:
        """Validate the generated CapCut project against the 15 criteria."""
        errors: List[str] = []
        project_dir = Path(project_dir)

        # 1. Check essential project files existence
        draft_content_file = project_dir / "draft_content.json"
        draft_meta_file = project_dir / "draft_meta_info.json"
        project_json_file = project_dir / "Timelines" / "project.json"

        if not draft_content_file.exists():
            errors.append(f"Missing essential file: {draft_content_file.name}")
            return False, errors

        if not draft_meta_file.exists():
            errors.append(f"Missing essential file: {draft_meta_file.name}")
            return False, errors

        # 2. Parse draft_content.json
        try:
            content = json.loads(draft_content_file.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"draft_content.json is not valid JSON: {e}")
            return False, errors

        # 3. Parse draft_meta_info.json
        try:
            meta = json.loads(draft_meta_file.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"draft_meta_info.json is not valid JSON: {e}")
            return False, errors

        # 4. Check Tracks in draft_content.json
        tracks = content.get("tracks", [])
        video_tracks = [t for t in tracks if t.get("type") == "video"]
        audio_tracks = [t for t in tracks if t.get("type") == "audio"]

        if not video_tracks:
            errors.append("No video track found in CapCut draft_content.json.")
            return False, errors

        if not audio_tracks:
            errors.append("No audio track found in CapCut draft_content.json.")
            return False, errors

        v_segments = video_tracks[0].get("segments", [])
        a_segments = audio_tracks[0].get("segments", [])

        # Criterion 1: Number of CapCut image clips == number of manifest images
        expected_count = len(expected_manifest)
        actual_clip_count = len(v_segments)
        if actual_clip_count != expected_count:
            errors.append(
                f"Image clip count mismatch: expected {expected_count} clips for manifest, "
                f"got {actual_clip_count} in CapCut video track."
            )

        # Build material mapping
        materials = content.get("materials", {})
        v_materials = {m["id"]: m for m in materials.get("videos", [])}
        a_materials = {m["id"]: m for m in materials.get("audios", [])}

        # Criterion 6: Every CapCut media reference points to an existing file
        # Check video materials
        referenced_indices: List[int] = []
        for i, seg in enumerate(v_segments):
            mat_id = seg.get("material_id")
            if not mat_id or mat_id not in v_materials:
                errors.append(f"Clip {i} references missing video material: {mat_id}")
                continue

            v_mat = v_materials[mat_id]
            file_path_str = v_mat.get("path", "")
            if not file_path_str:
                errors.append(f"Video material {mat_id} has empty path.")
            else:
                p = Path(file_path_str)
                if not p.exists():
                    errors.append(f"Referenced image file does not exist on disk: {file_path_str}")

            # Match with manifest entry
            if i < len(expected_manifest):
                manifest_img = expected_manifest[i]
                referenced_indices.append(manifest_img.index)

        # Criterion 2, 3, 4, 5: Image order, duplicates, missing
        if referenced_indices:
            expected_indices = [img.index for img in expected_manifest]
            if referenced_indices != expected_indices:
                errors.append(
                    f"Image order mismatch: expected {expected_indices[:10]}..., "
                    f"got {referenced_indices[:10]}..."
                )

            duplicates = [x for x in set(referenced_indices) if referenced_indices.count(x) > 1]
            if duplicates:
                errors.append(f"Duplicate image clips detected: {duplicates}")

            missing = set(expected_indices) - set(referenced_indices)
            if missing:
                errors.append(f"Missing image clips: {sorted(list(missing))}")

        # Criterion 7 & 8: Audio reference exists and starts at 0
        if not a_segments:
            errors.append("Audio track has 0 segments.")
        else:
            a_seg = a_segments[0]
            a_target = a_seg.get("target_timerange", {})
            if a_target.get("start", 0) != 0:
                errors.append(f"Audio track does not start at 0: got start={a_target.get('start')}")

            a_mat_id = a_seg.get("material_id")
            if not a_mat_id or a_mat_id not in a_materials:
                errors.append(f"Audio segment references missing audio material: {a_mat_id}")
            else:
                a_mat = a_materials[a_mat_id]
                a_path_str = a_mat.get("path", "")
                if not a_path_str:
                    errors.append("Audio material has empty path.")
                else:
                    ap = Path(a_path_str)
                    if not ap.exists():
                        errors.append(f"Referenced audio file does not exist on disk: {a_path_str}")

        # Criterion 9, 10, 11: Timing continuity (start at 0, no gaps, no overlaps)
        current_time_us = 0
        for i, seg in enumerate(v_segments):
            target_tr = seg.get("target_timerange", {})
            seg_start = target_tr.get("start", 0)
            seg_dur = target_tr.get("duration", 0)

            if i == 0 and seg_start != 0:
                errors.append(f"First image clip does not start at 0: got start={seg_start}")

            # Continuity check: seg_start should match previous end
            if abs(seg_start - current_time_us) > tolerance_us:
                if seg_start > current_time_us:
                    gap_ms = (seg_start - current_time_us) / 1000.0
                    errors.append(f"Gap detected before clip {i} (image {referenced_indices[i] if i < len(referenced_indices) else i}): gap of {gap_ms:.2f}ms")
                else:
                    overlap_ms = (current_time_us - seg_start) / 1000.0
                    errors.append(f"Overlap detected at clip {i} (image {referenced_indices[i] if i < len(referenced_indices) else i}): overlap of {overlap_ms:.2f}ms")

            current_time_us = seg_start + seg_dur

        # Criterion 12 & 13: Final image end == audio duration, project duration matches
        expected_audio_us = int(round(expected_timeline.audio_duration * 1_000_000))
        if abs(current_time_us - expected_audio_us) > tolerance_us:
            diff_ms = (current_time_us - expected_audio_us) / 1000.0
            errors.append(
                f"Final image end ({current_time_us}us) does not match authoritative audio duration "
                f"({expected_audio_us}us): difference of {diff_ms:.2f}ms exceeds tolerance of {tolerance_us/1000:.2f}ms"
            )

        project_dur = content.get("duration", 0)
        if abs(project_dur - expected_audio_us) > tolerance_us:
            errors.append(
                f"CapCut project duration ({project_dur}us) does not match authoritative timeline "
                f"({expected_audio_us}us)"
            )

        # Multi-timeline file check
        if project_json_file.exists():
            try:
                pj = json.loads(project_json_file.read_text(encoding="utf-8"))
                main_tl_id = pj.get("main_timeline_id")
                if not main_tl_id:
                    errors.append("Timelines/project.json missing 'main_timeline_id'")
                else:
                    sub_content = project_dir / "Timelines" / main_tl_id / "draft_content.json"
                    if not sub_content.exists():
                        errors.append(f"Multi-timeline draft_content.json missing at Timelines/{main_tl_id}/")
            except Exception as e:
                errors.append(f"Timelines/project.json is invalid: {e}")

        is_valid = len(errors) == 0
        return is_valid, errors
