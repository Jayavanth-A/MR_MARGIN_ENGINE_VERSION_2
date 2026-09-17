import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from engine.config import AppConfig
from engine.llm_client import BaseLLMClient
from engine.models import TimelineEntry

logger = logging.getLogger(__name__)


class ChunkValidationError(ValueError):
    """Raised when an LLM chunk fails strict validation."""
    pass


def parse_time_str_to_seconds(val: str) -> float:
    """Parse time representations into decimal seconds.
    
    Supports:
    - MM:SS (e.g. '01:30' -> 90.0)
    - MM:SS.mmm (e.g. '00:04.500' -> 4.5)
    - HH:MM:SS (e.g. '01:00:00' -> 3600.0)
    - HH:MM:SS.mmm (e.g. '01:02:03.500' -> 3723.5)
    - Raw seconds (e.g. '4', '4.5', '4.5s')
    """
    val = val.strip().lower().rstrip("s").strip()
    if ":" in val:
        parts = val.split(":")
        if len(parts) == 2:
            minutes = float(parts[0])
            seconds = float(parts[1])
            return round(minutes * 60.0 + seconds, 4)
        elif len(parts) == 3:
            hours = float(parts[0])
            minutes = float(parts[1])
            seconds = float(parts[2])
            return round(hours * 3600.0 + minutes * 60.0 + seconds, 4)
    return round(float(val), 4)


def extract_line_image_index(line: str) -> Optional[int]:
    """Extract 1-indexed image number from a timestamp line.
    
    Supports:
    - 'Image 1: 00:00 - 00:02' -> 1
    - 'img 1: ...' -> 1
    - '#1: ...' -> 1
    - '[1] ...' -> 1
    - '1 -> 00:00 to 00:02' -> 1
    - '1 → 00:00 to 00:02' -> 1
    - '1. 00:00 to 00:02' -> 1
    - '1) 00:00 to 00:02' -> 1
    - '1: 00:00' -> 1
    """
    clean = line.strip()
    if not clean:
        return None
    # 1. Explicit keyword: "image 42", "img 42", "#42", "[42]"
    m = re.search(r"\b(?:image|img)\s*#?\s*(\d+)\b", clean, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"^#(\d+)\b", clean)
    if m:
        return int(m.group(1))
    m = re.search(r"^\[(\d+)\]", clean)
    if m:
        return int(m.group(1))
    # 2. Starting number format: "1 ->", "1 →", "1.", "1)", "1 - ", "1: 00:00"
    m = re.match(r"^(\d+)\s*(?:[→\->\.\)]|:\s+[0-9])", clean)
    if m:
        return int(m.group(1))
    return None


def partition_timestamp_text(
    timestamp_text: str,
    total_images: int,
    chunk_size: int
) -> List[Tuple[int, int, str, Dict[int, str]]]:
    """Deterministically partition timestamp text into image ranges and their source text.
    
    Returns a list of tuples:
    [(start_image_index, end_image_index, chunk_text, image_to_source_line_map), ...]
    """
    if total_images <= 0:
        return []

    lines = timestamp_text.splitlines()
    non_empty_lines = [l for l in lines if l.strip()]

    # First attempt: map lines by explicit image index
    lines_by_image: Dict[int, List[str]] = {i: [] for i in range(1, total_images + 1)}
    explicit_count = 0
    current_idx: Optional[int] = None

    for line in lines:
        clean = line.strip()
        if not clean:
            continue
        line_idx = extract_line_image_index(clean)
        if line_idx is not None and 1 <= line_idx <= total_images:
            explicit_count += 1
            current_idx = line_idx
            lines_by_image[line_idx].append(clean)
        elif current_idx is not None:
            # Continuation line for the current image
            lines_by_image[current_idx].append(clean)

    # If explicit indices were found for a significant portion, use lines_by_image
    use_explicit = (explicit_count > 0 and explicit_count >= min(total_images // 2, 5)) or (explicit_count == total_images)

    if not use_explicit:
        # Fallback: sequential mapping (Line 1 = Image 1, Line 2 = Image 2, etc.)
        lines_by_image = {i: [] for i in range(1, total_images + 1)}
        for i, line in enumerate(non_empty_lines):
            img_idx = i + 1
            if img_idx <= total_images:
                lines_by_image[img_idx].append(line.strip())

    # Build chunk slices
    chunk_specs: List[Tuple[int, int, str, Dict[int, str]]] = []
    chunk_size = max(1, chunk_size)
    for start_idx in range(1, total_images + 1, chunk_size):
        end_idx = min(start_idx + chunk_size - 1, total_images)
        chunk_lines = []
        chunk_src_map: Dict[int, str] = {}
        for idx in range(start_idx, end_idx + 1):
            src_lines = lines_by_image.get(idx, [])
            if src_lines:
                text_block = "\n".join(src_lines)
                chunk_lines.append(text_block)
                chunk_src_map[idx] = text_block
            else:
                placeholder = f"Image {idx}: [timestamp specification required]"
                chunk_lines.append(placeholder)
                chunk_src_map[idx] = placeholder

        chunk_text = "\n".join(chunk_lines)
        chunk_specs.append((start_idx, end_idx, chunk_text, chunk_src_map))

    return chunk_specs


def validate_and_parse_chunk(
    response: Any,
    start_idx: int,
    end_idx: int,
    source_map: Optional[Dict[int, str]] = None
) -> List[TimelineEntry]:
    """Strictly validate LLM chunk response without any silent defaults or repairs.
    
    Requirements:
    - JSON must be valid dict with 'timeline' list (or list of dicts).
    - Every entry must contain 'image_index', 'start', 'end'.
    - Values must be numeric.
    - end must be strictly greater than start.
    - Exactly the indexes belonging to that chunk (start_idx..end_idx).
    - No duplicate indexes.
    - No missing indexes.
    - No extra indexes.
    - Indexes must be in consecutive sorted order.
    """
    if isinstance(response, dict):
        if "timeline" not in response:
            raise ChunkValidationError("Chunk response missing 'timeline' key.")
        raw_entries = response["timeline"]
    elif isinstance(response, list):
        raw_entries = response
    else:
        raise ChunkValidationError(
            f"Invalid chunk response format: expected dict or list, got {type(response).__name__}"
        )

    if not isinstance(raw_entries, list):
        raise ChunkValidationError(f"'timeline' must be a list, got {type(raw_entries).__name__}")

    expected_count = end_idx - start_idx + 1
    expected_indices = list(range(start_idx, end_idx + 1))

    if len(raw_entries) != expected_count:
        raise ChunkValidationError(
            f"Expected {expected_count} timeline entries for images {start_idx}..{end_idx}, got {len(raw_entries)}."
        )

    entries: List[TimelineEntry] = []
    seen_indices = set()

    for pos, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            raise ChunkValidationError(f"Entry at position {pos} is not an object: {item}")

        # Strict check for required keys (NO SILENT DEFAULTS)
        if "image_index" not in item:
            raise ChunkValidationError(f"Missing required 'image_index' in entry at position {pos}.")
        if "start" not in item:
            raise ChunkValidationError(f"Missing required 'start' in entry for position {pos}.")
        if "end" not in item:
            raise ChunkValidationError(f"Missing required 'end' in entry for position {pos}.")

        # Numeric validation
        try:
            idx = int(item["image_index"])
        except (ValueError, TypeError):
            raise ChunkValidationError(f"Non-numeric 'image_index' at position {pos}: {item['image_index']}")

        try:
            st = float(item["start"])
        except (ValueError, TypeError):
            raise ChunkValidationError(f"Non-numeric 'start' timestamp for image {idx}: {item['start']}")

        try:
            en = float(item["end"])
        except (ValueError, TypeError):
            raise ChunkValidationError(f"Non-numeric 'end' timestamp for image {idx}: {item['end']}")

        # Duration validation
        if en <= st:
            raise ChunkValidationError(
                f"Invalid duration for image {idx}: end ({en}) must be greater than start ({st})."
            )

        # In-chunk range validation
        if idx < start_idx or idx > end_idx:
            raise ChunkValidationError(
                f"Image index {idx} does not belong to chunk range {start_idx}..{end_idx}."
            )

        # Duplicate check
        if idx in seen_indices:
            raise ChunkValidationError(f"Duplicate image index {idx} in chunk {start_idx}..{end_idx}.")
        seen_indices.add(idx)

        # Order validation: entries must be in expected consecutive order
        expected_at_pos = start_idx + pos
        if idx != expected_at_pos:
            raise ChunkValidationError(
                f"Chunk entries not in consecutive order: expected image {expected_at_pos} at position {pos}, got {idx}."
            )

        src_ref = ""
        if source_map and idx in source_map:
            src_ref = source_map[idx]
        elif "source_reference" in item and item["source_reference"]:
            src_ref = str(item["source_reference"])
        else:
            src_ref = f"Image {idx} timestamp specification"

        entries.append(
            TimelineEntry(
                image_index=idx,
                filename="",
                start=round(st, 4),
                end=round(en, 4),
                narrative_context=f"Normalized timestamp for image {idx}",
                source_reference=src_ref
            )
        )

    # Missing index check
    missing = set(expected_indices) - seen_indices
    if missing:
        raise ChunkValidationError(f"Missing image indexes in chunk: {sorted(missing)}.")

    return entries


class TimestampNormalizer:
    """Normalizes human-provided timestamp instructions into strict JSON timeline via chunked LLM calls."""

    NORMALIZATION_SYSTEM_PROMPT = (
        "You are a timestamp normalization engine for an automated video rendering pipeline.\n\n"
        "The user will provide timestamp instructions for a specific sequence of images.\n"
        "Your task is to convert the timestamp instructions into strict JSON schema.\n\n"
        "You are NOT an editorial planner.\n"
        "You must NOT invent, remove, skip, reorder, or renumber images.\n"
        "You must output exactly the requested image indexes in consecutive order.\n"
        "Convert all timestamps (MM:SS, MM:SS.mmm, HH:MM:SS, seconds, or natural language) into decimal seconds.\n\n"
        "Return JSON only.\n"
        "Do not return Markdown or code fences.\n\n"
        "Output JSON schema:\n"
        "{\n"
        '  "timeline": [\n'
        '    {"image_index": <int>, "start": <float>, "end": <float>}\n'
        "  ]\n"
        "}"
    )

    def __init__(self, config: AppConfig, llm_client: BaseLLMClient):
        self.config = config
        self.llm_client = llm_client

    def normalize(
        self,
        timestamp_text: str,
        expected_count: Optional[int] = None,
        audio_duration: Optional[float] = None,
        status_callback: Optional[Callable[[str, str], None]] = None
    ) -> List[TimelineEntry]:
        """Normalize user timestamp text into structured TimelineEntry items via chunked LLM calls."""
        if not timestamp_text or not timestamp_text.strip():
            raise ValueError("Timestamp text is empty.")

        # Determine total images
        total_images = expected_count
        if total_images is None:
            # Infer from explicit indices or line count
            lines = [l.strip() for l in timestamp_text.splitlines() if l.strip()]
            indices = [extract_line_image_index(l) for l in lines]
            valid_indices = [idx for idx in indices if idx is not None]
            if valid_indices:
                total_images = max(valid_indices)
            else:
                total_images = len(lines)

        chunk_size = getattr(self.config, "max_chunk_images", 100) or 100
        chunks = partition_timestamp_text(timestamp_text, total_images, chunk_size)
        total_chunks = len(chunks)

        logger.info("Timestamp normalization started")
        logger.info(f"Total images: {total_images}")
        logger.info(f"Chunk size: {chunk_size}")
        logger.info(f"Total chunks: {total_chunks}")

        all_entries: List[TimelineEntry] = []
        retries = getattr(self.config, "llm_max_retries", 3)

        for chunk_num, (start_idx, end_idx, chunk_text, src_map) in enumerate(chunks, start=1):
            logger.info(f"Chunk {chunk_num}/{total_chunks}: images {start_idx}-{end_idx}")
            if status_callback:
                status_callback(
                    f"[{chunk_num}/{total_chunks}]",
                    f"Normalizing images {start_idx}-{end_idx} via LLM..."
                )

            cache_payload = {
                "chunk_index": chunk_num,
                "total_chunks": total_chunks,
                "start_image_index": start_idx,
                "end_image_index": end_idx,
                "chunk_text": chunk_text,
                "model": self.config.aicredits_model
            }

            user_prompt = (
                f"USER TIMESTAMP INSTRUCTIONS (Images {start_idx} to {end_idx}):\n"
                f"----------------------------------------\n"
                f"{chunk_text}\n"
                f"----------------------------------------\n"
                f"Convert every image timestamp above into strict JSON schema:\n"
                f'{{"timeline": [{{"image_index": <int>, "start": <float>, "end": <float>}}, ...]}}\n'
                f"Expected image indexes: {start_idx} to {end_idx} (exactly {end_idx - start_idx + 1} images in sequence)."
            )

            chunk_success = False
            last_error: Optional[Exception] = None

            for attempt in range(1, retries + 1):
                logger.info(f"Chunk {chunk_num}/{total_chunks}: LLM request (attempt {attempt}/{retries})")
                try:
                    response = self.llm_client.generate_json(
                        system_prompt=self.NORMALIZATION_SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                        namespace="timestamp_normalization",
                        cache_key_data=cache_payload
                    )

                    chunk_entries = validate_and_parse_chunk(
                        response=response,
                        start_idx=start_idx,
                        end_idx=end_idx,
                        source_map=src_map
                    )

                    logger.info(f"Chunk {chunk_num}/{total_chunks}: validation passed")
                    all_entries.extend(chunk_entries)
                    chunk_success = True
                    break

                except Exception as e:
                    last_error = e
                    logger.warning(f"Chunk {chunk_num}/{total_chunks} failed validation: {e}")
                    if attempt < retries:
                        logger.info(f"Retrying chunk {chunk_num}/{total_chunks}")

            if not chunk_success:
                raise RuntimeError(
                    f"Chunk {chunk_num}/{total_chunks} (images {start_idx}-{end_idx}) failed validation after {retries} attempts: {last_error}"
                )

        logger.info("All chunks normalized successfully")

        # Deterministic combination: order strictly by image_index
        all_entries.sort(key=lambda e: e.image_index)
        logger.info(f"Combined timeline contains {len(all_entries)} images")

        return all_entries

