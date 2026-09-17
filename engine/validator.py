import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union
import statistics

from engine.models import (
    ImageRecord,
    InputValidationReport,
    Timeline,
    TimelineEntry,
    TimelineValidationReport,
)


def extract_numeric_index(filename: str) -> Optional[int]:
    """Extract numeric prefix from filename (e.g., '1_exterior.jpg' -> 1, '042.png' -> 42).
    
    Returns None if filename does not start with digits.
    """
    stem = Path(filename).stem
    match = re.match(r"^(\d+)", stem)
    if match:
        return int(match.group(1))
    return None


def parse_raw_prompt_blocks(content: str) -> List[str]:
    """Parse raw prompts separated by blank-line divisions.
    
    A blank-line division means one or more empty lines between prompt blocks.
    Preserves exact prompt text (stripping only outer whitespace of each block).
    Returns list of prompts in immutable order.
    """
    if not content:
        return []
    normalized = content.replace("\r\n", "\n")
    blocks = re.split(r"\n\s*\n+", normalized)
    prompts = []
    for b in blocks:
        clean = b.strip()
        if clean:
            prompts.append(clean)
    return prompts


def parse_voiceover_paragraphs(content: Union[Path, str]) -> List[str]:
    """Parse voiceover text into paragraphs by blank-line divisions.
    
    Preserves exact paragraph order and text (stripping only outer whitespace).
    """
    if isinstance(content, Path) or (isinstance(content, str) and len(content) < 500 and "\n" not in content and Path(content).exists()):
        text = Path(content).read_text(encoding="utf-8")
    else:
        text = str(content)

    if not text:
        return []
    normalized = text.replace("\r\n", "\n")
    blocks = re.split(r"\n\s*\n+", normalized)
    paragraphs = []
    for b in blocks:
        clean = b.strip()
        if clean:
            paragraphs.append(clean)
    return paragraphs


def parse_prompts_file(prompts_path: Path) -> Dict[int, str]:
    """Parse prompts file supporting:
    - Raw plain text (.txt): prompts separated by blank lines (Image 1 -> Prompt 1, etc.)
    - JSON list of objects: [{"index": 1, "prompt": "..."}, ...]
    - JSON dict mapping index: {"1": "prompt...", "2": "prompt..."}
    - JSONL: each line a JSON object
    """
    prompts_path = Path(prompts_path)
    if not prompts_path.exists():
        raise FileNotFoundError(f"Prompts file not found: {prompts_path}")

    content = prompts_path.read_text(encoding="utf-8")
    if not content.strip():
        return {}

    prompts_map: Dict[int, str] = {}

    # If file is explicitly .txt, parse directly as raw prompt blocks
    if prompts_path.suffix.lower() == ".txt":
        blocks = parse_raw_prompt_blocks(content)
        for i, block in enumerate(blocks, start=1):
            prompts_map[i] = block
        return prompts_map

    # Try JSON first for .json / other extensions
    try:
        data = json.loads(content.strip())
        if isinstance(data, dict):
            for k, v in data.items():
                try:
                    idx = int(k)
                    if isinstance(v, str):
                        prompts_map[idx] = v
                    elif isinstance(v, dict) and "prompt" in v:
                        prompts_map[idx] = str(v["prompt"])
                except ValueError:
                    continue
            if prompts_map:
                return prompts_map
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    idx = item.get("index")
                    prompt_text = item.get("prompt") or item.get("text") or item.get("description", "")
                    if idx is not None:
                        prompts_map[int(idx)] = str(prompt_text)
                    elif "filename" in item:
                        file_idx = extract_numeric_index(item["filename"])
                        if file_idx is not None:
                            prompts_map[file_idx] = str(prompt_text)
            if prompts_map:
                return prompts_map
    except json.JSONDecodeError:
        pass

    # Try JSON Lines
    lines = content.strip().splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                idx = item.get("index")
                prompt_text = item.get("prompt") or item.get("text", "")
                if idx is not None:
                    prompts_map[int(idx)] = str(prompt_text)
                elif "filename" in item:
                    file_idx = extract_numeric_index(item["filename"])
                    if file_idx is not None:
                        prompts_map[file_idx] = str(prompt_text)
        except json.JSONDecodeError:
            continue

    if prompts_map:
        return prompts_map

    # Fallback to raw blank-line divisions
    blocks = parse_raw_prompt_blocks(content)
    for i, block in enumerate(blocks, start=1):
        prompts_map[i] = block
    return prompts_map


class InputValidator:
    """Performs rigorous pre-LLM validation on image folders and prompts."""

    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}

    @classmethod
    def validate_inputs(
        cls,
        images_dir: Path,
        prompts_path: Path
    ) -> Tuple[InputValidationReport, Optional[List[ImageRecord]]]:
        """Validate images and prompts. If valid, return (report, locked_sequence).
        If invalid, return (report, None).
        """
        images_dir = Path(images_dir)
        prompts_path = Path(prompts_path)

        if not images_dir.exists() or not images_dir.is_dir():
            report = InputValidationReport(
                is_valid=False,
                total_images=0,
                total_prompts=0,
                expected_count=0,
                errors=[f"Images directory does not exist or is not a directory: {images_dir}"]
            )
            return report, None

        if not prompts_path.exists() or not prompts_path.is_file():
            report = InputValidationReport(
                is_valid=False,
                total_images=0,
                total_prompts=0,
                expected_count=0,
                errors=[f"Prompts file does not exist: {prompts_path}"]
            )
            return report, None

        # 1. Scan image files
        image_files = [
            f for f in images_dir.iterdir()
            if f.is_file() and f.suffix.lower() in cls.IMAGE_EXTENSIONS
        ]

        invalid_filenames: List[str] = []
        image_index_to_file: Dict[int, Path] = {}
        duplicate_image_indexes: Set[int] = set()

        for img_file in image_files:
            idx = extract_numeric_index(img_file.name)
            if idx is None:
                invalid_filenames.append(img_file.name)
            else:
                if idx in image_index_to_file:
                    duplicate_image_indexes.add(idx)
                else:
                    image_index_to_file[idx] = img_file

        # 2. Parse prompts
        try:
            prompts_map = parse_prompts_file(prompts_path)
        except Exception as e:
            report = InputValidationReport(
                is_valid=False,
                total_images=len(image_files),
                total_prompts=0,
                expected_count=0,
                errors=[f"Failed to parse prompts file: {e}"]
            )
            return report, None

        total_images = len(image_files)
        total_prompts = len(prompts_map)
        image_indexes = set(image_index_to_file.keys())
        prompt_indexes = set(prompts_map.keys())

        # Determine expected count based on max index or union
        all_indexes = image_indexes.union(prompt_indexes)
        max_idx = max(all_indexes) if all_indexes else 0
        min_idx = min(all_indexes) if all_indexes else 1

        # Check missing images and prompts
        missing_images = sorted(list(prompt_indexes - image_indexes))
        missing_prompts = sorted(list(image_indexes - prompt_indexes))

        # Check sequence gaps: expected 1 .. max_idx
        sequence_gaps: List[int] = []
        if max_idx > 0:
            for expected_i in range(1, max_idx + 1):
                if expected_i not in image_indexes:
                    sequence_gaps.append(expected_i)

        errors: List[str] = []
        if total_images != total_prompts:
            errors.append(
                f"Prompt count mismatch: {total_images} images found, but {total_prompts} prompts provided in {prompts_path.name}. Exactly 1 prompt per image is required."
            )
        if invalid_filenames:
            errors.append(f"{len(invalid_filenames)} image filenames lack a numeric prefix.")
        if duplicate_image_indexes:
            errors.append(f"{len(duplicate_image_indexes)} duplicate image indexes detected.")
        if missing_images:
            errors.append(f"{len(missing_images)} images missing for corresponding prompts.")
        if missing_prompts:
            errors.append(f"{len(missing_prompts)} prompts missing for corresponding images.")
        if sequence_gaps:
            errors.append(f"{len(sequence_gaps)} sequence gaps detected in 1..{max_idx}.")
        if min_idx != 1 and max_idx > 0:
            errors.append(f"Image sequence must start at index 1, but starts at {min_idx}.")

        is_valid = (
            len(errors) == 0 and
            total_images == total_prompts and
            len(invalid_filenames) == 0 and
            len(duplicate_image_indexes) == 0 and
            len(missing_images) == 0 and
            len(missing_prompts) == 0 and
            len(sequence_gaps) == 0 and
            total_images > 0
        )

        report = InputValidationReport(
            is_valid=is_valid,
            total_images=total_images,
            total_prompts=total_prompts,
            expected_count=max_idx,
            missing_images=missing_images,
            missing_prompts=missing_prompts,
            duplicate_images=sorted(list(duplicate_image_indexes)),
            duplicate_prompts=[],
            invalid_filenames=sorted(invalid_filenames),
            sequence_gaps=sequence_gaps,
            errors=errors
        )

        if not is_valid:
            return report, None

        # Build Sequence Lock (1..N sorted numerically)
        locked_sequence: List[ImageRecord] = []
        for i in range(1, max_idx + 1):
            file_path = image_index_to_file[i]
            prompt = prompts_map[i]
            locked_sequence.append(
                ImageRecord(
                    index=i,
                    filename=file_path.name,
                    path=file_path.resolve(),
                    prompt=prompt
                )
            )

        return report, locked_sequence


class TimelineValidator:
    """Deterministic validator for final video timeline."""

    @classmethod
    def validate_timeline(
        cls,
        timeline: Timeline,
        expected_image_count: int,
        expected_audio_duration: float,
        epsilon: float = 0.005,
        tail_dump_threshold: float = 0.35
    ) -> TimelineValidationReport:
        """Validate timeline against non-negotiable rules:
        - timeline count == expected count
        - indices 1..N continuous
        - first start == 0.0
        - final end == audio_duration
        - no gaps or overlaps
        - positive duration for every image
        - anti-leftover dump detection
        """
        errors: List[str] = []
        warnings: List[str] = []
        entries = timeline.timeline

        if len(entries) != expected_image_count:
            errors.append(
                f"Timeline count mismatch: expected {expected_image_count}, got {len(entries)}"
            )

        if not entries:
            return TimelineValidationReport(
                is_valid=False,
                errors=["Timeline is empty"],
                warnings=[],
                leftover_dump_detected=False
            )

        # Check index sequence
        indices = [e.image_index for e in entries]
        expected_indices = list(range(1, expected_image_count + 1))
        if indices != expected_indices:
            errors.append(f"Image sequence violation. Expected 1..{expected_image_count}, got: {indices[:10]}...")

        # Check first start
        if abs(entries[0].start - 0.0) > epsilon:
            errors.append(f"First image start must be 0.000, got {entries[0].start:.4f}")

        # Check final end
        if abs(entries[-1].end - expected_audio_duration) > epsilon:
            errors.append(
                f"Final image end ({entries[-1].end:.4f}) does not match audio duration ({expected_audio_duration:.4f})"
            )

        # Continuity and duration checks
        durations: List[float] = []
        for i, entry in enumerate(entries):
            dur = entry.end - entry.start
            durations.append(dur)

            if dur <= 0:
                errors.append(
                    f"Invalid duration at image {entry.image_index}: start={entry.start:.4f}, end={entry.end:.4f}"
                )

            if i > 0:
                prev_end = entries[i - 1].end
                curr_start = entry.start
                diff = curr_start - prev_end
                if abs(diff) > epsilon:
                    if diff > 0:
                        errors.append(
                            f"Gap detected between image {entries[i-1].image_index} and {entry.image_index}: {diff:.4f}s"
                        )
                    else:
                        errors.append(
                            f"Overlap detected between image {entries[i-1].image_index} and {entry.image_index}: {-diff:.4f}s"
                        )

        # Leftover dumping check
        leftover_dump = False
        min_dur = min(durations) if durations else 0.0
        max_dur = max(durations) if durations else 0.0
        median_dur = statistics.median(durations) if durations else 0.0
        tail_median = median_dur

        if len(durations) >= 10:
            tail_size = max(3, int(len(durations) * 0.08))
            tail_durations = durations[-tail_size:]
            tail_median = statistics.median(tail_durations)

            # If tail median is significantly compressed compared to sequence median
            if median_dur > 0 and (tail_median / median_dur) < tail_dump_threshold:
                leftover_dump = True
                warnings.append(
                    f"Possible leftover dumping detected: final {tail_size} images have median duration "
                    f"{tail_median:.2f}s vs sequence median {median_dur:.2f}s (ratio {tail_median/median_dur:.2f})"
                )

        is_valid = len(errors) == 0

        return TimelineValidationReport(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            leftover_dump_detected=leftover_dump,
            min_duration=round(min_dur, 4),
            max_duration=round(max_dur, 4),
            median_duration=round(median_dur, 4),
            tail_median_duration=round(tail_median, 4),
            details={"durations_count": len(durations)}
        )
