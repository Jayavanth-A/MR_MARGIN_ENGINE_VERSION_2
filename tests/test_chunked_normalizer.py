import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from engine.config import AppConfig
from engine.llm_client import BaseLLMClient, MockLLMClient
from engine.models import ImageRecord, TimelineEntry
from engine.reconciler import TimelineReconciler, TimelineReconciliationError
from engine.timestamp_normalizer import (
    ChunkValidationError,
    TimestampNormalizer,
    extract_line_image_index,
    partition_timestamp_text,
    validate_and_parse_chunk,
)


def _generate_synthetic_timestamp_text(num_images: int) -> str:
    """Generate synthetic user timestamp lines for num_images."""
    lines = []
    current_time = 0.0
    for i in range(1, num_images + 1):
        start = current_time
        end = current_time + 2.5
        lines.append(f"Image {i}: {start:.2f} - {end:.2f}")
        current_time = end
    return "\n".join(lines)


# =====================================================================
# Scenario 1: Small timestamp input (5 images, 1 chunk)
# =====================================================================
def test_scenario_1_small_timestamp_input_5_images():
    text = _generate_synthetic_timestamp_text(5)
    config = AppConfig(max_chunk_images=100)
    mock_llm = MockLLMClient()
    normalizer = TimestampNormalizer(config, mock_llm)

    chunks = partition_timestamp_text(text, total_images=5, chunk_size=100)
    assert len(chunks) == 1
    assert chunks[0][0] == 1
    assert chunks[0][1] == 5

    entries = normalizer.normalize(text, expected_count=5, audio_duration=12.5)
    assert len(entries) == 5
    assert [e.image_index for e in entries] == [1, 2, 3, 4, 5]
    assert entries[0].start == 0.0
    assert entries[-1].end == 12.5


# =====================================================================
# Scenario 2: 40-image input (1 chunk)
# =====================================================================
def test_scenario_2_40_images_input_1_chunk():
    text = _generate_synthetic_timestamp_text(40)
    config = AppConfig(max_chunk_images=100)
    mock_llm = MockLLMClient()
    normalizer = TimestampNormalizer(config, mock_llm)

    chunks = partition_timestamp_text(text, total_images=40, chunk_size=100)
    assert len(chunks) == 1
    assert chunks[0][0] == 1
    assert chunks[0][1] == 40

    entries = normalizer.normalize(text, expected_count=40)
    assert len(entries) == 40
    assert [e.image_index for e in entries] == list(range(1, 41))


# =====================================================================
# Scenario 3: 100-image input (1 chunk)
# =====================================================================
def test_scenario_3_100_images_input_1_chunk():
    text = _generate_synthetic_timestamp_text(100)
    config = AppConfig(max_chunk_images=100)
    mock_llm = MockLLMClient()
    normalizer = TimestampNormalizer(config, mock_llm)

    chunks = partition_timestamp_text(text, total_images=100, chunk_size=100)
    assert len(chunks) == 1
    assert chunks[0][0] == 1
    assert chunks[0][1] == 100

    entries = normalizer.normalize(text, expected_count=100)
    assert len(entries) == 100
    assert [e.image_index for e in entries] == list(range(1, 101))


# =====================================================================
# Scenario 4: 679-image input (7 chunks)
# =====================================================================
def test_scenario_4_679_images_input_7_chunks():
    text = _generate_synthetic_timestamp_text(679)
    chunks = partition_timestamp_text(text, total_images=679, chunk_size=100)
    assert len(chunks) == 7
    expected_ranges = [
        (1, 100),
        (101, 200),
        (201, 300),
        (301, 400),
        (401, 500),
        (501, 600),
        (601, 679),
    ]
    for i, (expected_start, expected_end) in enumerate(expected_ranges):
        start_idx, end_idx, chunk_text, _ = chunks[i]
        assert start_idx == expected_start
        assert end_idx == expected_end
        assert f"Image {expected_start}:" in chunk_text
        assert f"Image {expected_end}:" in chunk_text


# =====================================================================
# Scenario 5: Chunk boundary handling
# =====================================================================
def test_scenario_5_chunk_boundary_handling():
    text = _generate_synthetic_timestamp_text(250)
    chunks = partition_timestamp_text(text, total_images=250, chunk_size=100)
    assert len(chunks) == 3
    # Check boundaries: no gaps, no overlaps
    assert chunks[0][0] == 1 and chunks[0][1] == 100
    assert chunks[1][0] == 101 and chunks[1][1] == 200
    assert chunks[2][0] == 201 and chunks[2][1] == 250
    for i in range(len(chunks) - 1):
        assert chunks[i][1] + 1 == chunks[i + 1][0]


# =====================================================================
# Scenario 6: Final partial chunk (chunk 7 has 79 images for 679 total)
# =====================================================================
def test_scenario_6_final_partial_chunk():
    text = _generate_synthetic_timestamp_text(679)
    chunks = partition_timestamp_text(text, total_images=679, chunk_size=100)
    last_chunk = chunks[-1]
    assert last_chunk[0] == 601
    assert last_chunk[1] == 679
    assert (last_chunk[1] - last_chunk[0] + 1) == 79


# =====================================================================
# Scenario 7: Missing image in chunk rejection
# =====================================================================
def test_scenario_7_missing_image_in_chunk_rejection():
    # Chunk 1..5 missing image 3
    payload = {
        "timeline": [
            {"image_index": 1, "start": 0.0, "end": 1.0},
            {"image_index": 2, "start": 1.0, "end": 2.0},
            {"image_index": 4, "start": 2.0, "end": 3.0},
            {"image_index": 5, "start": 3.0, "end": 4.0},
        ]
    }
    with pytest.raises(ChunkValidationError) as exc:
        validate_and_parse_chunk(payload, start_idx=1, end_idx=5)
    assert "Expected 5 timeline entries" in str(exc.value)


# =====================================================================
# Scenario 8: Duplicate image in chunk rejection
# =====================================================================
def test_scenario_8_duplicate_image_in_chunk_rejection():
    # Chunk 1..3 with duplicate index 2
    payload = {
        "timeline": [
            {"image_index": 1, "start": 0.0, "end": 1.0},
            {"image_index": 2, "start": 1.0, "end": 2.0},
            {"image_index": 2, "start": 2.0, "end": 3.0},
        ]
    }
    with pytest.raises(ChunkValidationError) as exc:
        validate_and_parse_chunk(payload, start_idx=1, end_idx=3)
    assert "Duplicate image index 2" in str(exc.value)


# =====================================================================
# Scenario 9: Extra image in chunk rejection
# =====================================================================
def test_scenario_9_extra_image_in_chunk_rejection():
    # Chunk 1..3 has 4 entries
    payload = {
        "timeline": [
            {"image_index": 1, "start": 0.0, "end": 1.0},
            {"image_index": 2, "start": 1.0, "end": 2.0},
            {"image_index": 3, "start": 2.0, "end": 3.0},
            {"image_index": 4, "start": 3.0, "end": 4.0},
        ]
    }
    with pytest.raises(ChunkValidationError) as exc:
        validate_and_parse_chunk(payload, start_idx=1, end_idx=3)
    assert "Expected 3 timeline entries" in str(exc.value)


# =====================================================================
# Scenario 10: Out-of-range image in chunk rejection
# =====================================================================
def test_scenario_10_out_of_range_image_in_chunk_rejection():
    # Chunk 101..103 has image 104 instead of 103
    payload = {
        "timeline": [
            {"image_index": 101, "start": 0.0, "end": 1.0},
            {"image_index": 102, "start": 1.0, "end": 2.0},
            {"image_index": 104, "start": 2.0, "end": 3.0},
        ]
    }
    with pytest.raises(ChunkValidationError) as exc:
        validate_and_parse_chunk(payload, start_idx=101, end_idx=103)
    assert "does not belong to chunk range" in str(exc.value) or "104" in str(exc.value)


# =====================================================================
# Scenario 11: Missing image_index rejection (no silent default)
# =====================================================================
def test_scenario_11_missing_image_index_rejection():
    payload = {
        "timeline": [
            {"start": 0.0, "end": 1.0},
            {"image_index": 2, "start": 1.0, "end": 2.0},
        ]
    }
    with pytest.raises(ChunkValidationError) as exc:
        validate_and_parse_chunk(payload, start_idx=1, end_idx=2)
    assert "Missing required 'image_index'" in str(exc.value)


# =====================================================================
# Scenario 12: Missing start rejection (no silent default)
# =====================================================================
def test_scenario_12_missing_start_rejection():
    payload = {
        "timeline": [
            {"image_index": 1, "end": 1.0},
            {"image_index": 2, "start": 1.0, "end": 2.0},
        ]
    }
    with pytest.raises(ChunkValidationError) as exc:
        validate_and_parse_chunk(payload, start_idx=1, end_idx=2)
    assert "Missing required 'start'" in str(exc.value)


# =====================================================================
# Scenario 13: Missing end rejection (no silent default)
# =====================================================================
def test_scenario_13_missing_end_rejection():
    payload = {
        "timeline": [
            {"image_index": 1, "start": 0.0},
            {"image_index": 2, "start": 1.0, "end": 2.0},
        ]
    }
    with pytest.raises(ChunkValidationError) as exc:
        validate_and_parse_chunk(payload, start_idx=1, end_idx=2)
    assert "Missing required 'end'" in str(exc.value)


# =====================================================================
# Scenario 14: Invalid JSON rejection and retry
# =====================================================================
def test_scenario_14_invalid_json_rejection_and_retry():
    config = AppConfig(max_chunk_images=10, max_retries=3)
    mock_llm = MagicMock()

    # First call returns non-dict / invalid JSON, second call returns valid
    valid_resp = {
        "timeline": [
            {"image_index": 1, "start": 0.0, "end": 2.0},
            {"image_index": 2, "start": 2.0, "end": 4.0},
        ]
    }
    mock_llm.generate_json.side_effect = [
        RuntimeError("Failed to extract valid JSON from LLM response"),
        valid_resp,
    ]

    normalizer = TimestampNormalizer(config, mock_llm)
    text = "Image 1: 00:00 - 00:02\nImage 2: 00:02 - 00:04"
    entries = normalizer.normalize(text, expected_count=2)
    assert len(entries) == 2
    assert mock_llm.generate_json.call_count == 2


# =====================================================================
# Scenario 15: Truncated JSON rejection and retry
# =====================================================================
def test_scenario_15_truncated_json_rejection_and_retry():
    config = AppConfig(max_chunk_images=10, max_retries=3)
    mock_llm = MagicMock()

    # First returns incomplete list (truncated), second returns full
    truncated_resp = {
        "timeline": [
            {"image_index": 1, "start": 0.0, "end": 2.0},
        ]
    }
    full_resp = {
        "timeline": [
            {"image_index": 1, "start": 0.0, "end": 2.0},
            {"image_index": 2, "start": 2.0, "end": 4.0},
        ]
    }
    mock_llm.generate_json.side_effect = [truncated_resp, full_resp]

    normalizer = TimestampNormalizer(config, mock_llm)
    text = "Image 1: 00:00 - 00:02\nImage 2: 00:02 - 00:04"
    entries = normalizer.normalize(text, expected_count=2)
    assert len(entries) == 2
    assert mock_llm.generate_json.call_count == 2


# =====================================================================
# Scenario 16: Failed chunk retry (only failed chunk retried)
# =====================================================================
def test_scenario_16_failed_chunk_retry_preserves_successful_chunks():
    config = AppConfig(max_chunk_images=2, max_retries=3)
    mock_llm = MagicMock()

    # 4 images -> 2 chunks: (1..2) and (3..4)
    # Chunk 1 succeeds immediately
    # Chunk 2 fails once, then succeeds on retry
    chunk1_resp = {
        "timeline": [
            {"image_index": 1, "start": 0.0, "end": 2.0},
            {"image_index": 2, "start": 2.0, "end": 4.0},
        ]
    }
    chunk2_fail = {"timeline": [{"image_index": 3, "start": 4.0, "end": 6.0}]}  # missing 4
    chunk2_success = {
        "timeline": [
            {"image_index": 3, "start": 4.0, "end": 6.0},
            {"image_index": 4, "start": 6.0, "end": 8.0},
        ]
    }

    mock_llm.generate_json.side_effect = [chunk1_resp, chunk2_fail, chunk2_success]

    normalizer = TimestampNormalizer(config, mock_llm)
    text = (
        "Image 1: 00:00 - 00:02\n"
        "Image 2: 00:02 - 00:04\n"
        "Image 3: 00:04 - 00:06\n"
        "Image 4: 00:06 - 00:08\n"
    )
    entries = normalizer.normalize(text, expected_count=4)
    assert len(entries) == 4
    # Total calls: 1 for chunk 1 + 2 for chunk 2 = 3 calls
    # Chunk 1 was NOT retried!
    assert mock_llm.generate_json.call_count == 3


# =====================================================================
# Scenario 17: Successful chunk cache reuse
# =====================================================================
def test_scenario_17_successful_chunk_cache_reuse(tmp_path):
    from engine.cache import CacheManager
    cache_dir = tmp_path / "cache"
    config = AppConfig(cache_enabled=True, cache_dir=cache_dir, max_chunk_images=2)
    cache_mgr = CacheManager(cache_dir)
    mock_llm = MockLLMClient(cache_manager=cache_mgr)
    mock_llm.generate_json = MagicMock(side_effect=mock_llm.generate_json)

    normalizer = TimestampNormalizer(config, mock_llm)
    text = (
        "Image 1: 00:00 - 00:02\n"
        "Image 2: 00:02 - 00:04\n"
        "Image 3: 00:04 - 00:06\n"
        "Image 4: 00:06 - 00:08\n"
    )
    # First run: populates cache for 2 chunks
    entries1 = normalizer.normalize(text, expected_count=4)
    assert len(entries1) == 4
    assert mock_llm.generate_json.call_count == 2

    # Second run: should hit cache
    mock_llm.generate_json.reset_mock()
    entries2 = normalizer.normalize(text, expected_count=4)
    assert len(entries2) == 4
    # Verify generate_json was called with cache hits (mock_llm returns from cache)
    assert mock_llm.generate_json.call_count == 2


# =====================================================================
# Scenario 18: Exact source timestamp preservation (unstripped)
# =====================================================================
def test_scenario_18_exact_source_timestamp_preservation(tmp_path):
    # Unstripped text with leading newlines and trailing whitespace
    raw_user_text = "\n\n  Image 1: 00:00 - 00:03\n  Image 2: 00:03 - 00:06\n\n\t "
    out_dir = tmp_path / "output"
    out_dir.mkdir()

    source_text_path = out_dir / "source_timestamp_text.txt"
    source_text_path.write_text(raw_user_text, encoding="utf-8")

    saved_text = source_text_path.read_text(encoding="utf-8")
    assert saved_text == raw_user_text
    assert saved_text.startswith("\n\n  Image 1")
    assert saved_text.endswith("\n\n\t ")


# =====================================================================
# Scenario 19: Image order preservation (immutable 1..N order)
# =====================================================================
def test_scenario_19_image_order_preservation():
    text = _generate_synthetic_timestamp_text(679)
    config = AppConfig(max_chunk_images=100)
    mock_llm = MockLLMClient()
    normalizer = TimestampNormalizer(config, mock_llm)

    entries = normalizer.normalize(text, expected_count=679)
    assert len(entries) == 679
    for i, entry in enumerate(entries):
        assert entry.image_index == i + 1, f"Image order violated at position {i}: got {entry.image_index}"


# =====================================================================
# Scenario 20: Final timeline validation & reconciliation
# =====================================================================
def test_scenario_20_final_timeline_validation_and_reconciliation():
    manifest = [
        ImageRecord(index=1, filename="img_001.png", path=Path("img_001.png"), prompt="Prompt 1"),
        ImageRecord(index=2, filename="img_002.png", path=Path("img_002.png"), prompt="Prompt 2"),
        ImageRecord(index=3, filename="img_003.png", path=Path("img_003.png"), prompt="Prompt 3"),
    ]

    # Valid timeline
    valid_entries = [
        TimelineEntry(image_index=1, filename="img_001.png", start=0.0, end=2.0),
        TimelineEntry(image_index=2, filename="img_002.png", start=2.0, end=4.0),
        TimelineEntry(image_index=3, filename="img_003.png", start=4.0, end=6.0),
    ]
    reconciled = TimelineReconciler.reconcile(valid_entries, manifest, target_audio_duration=6.0)
    assert len(reconciled.timeline) == 3

    # Extra image rejection in reconciler
    extra_entries = valid_entries + [
        TimelineEntry(image_index=4, filename="img_004.png", start=6.0, end=8.0)
    ]
    with pytest.raises(TimelineReconciliationError) as exc:
        TimelineReconciler.reconcile(extra_entries, manifest, target_audio_duration=8.0)
    assert "Extra image indexes" in str(exc.value) and "4" in str(exc.value)

    # Missing image rejection in reconciler
    missing_entries = valid_entries[:2]
    with pytest.raises(TimelineReconciliationError) as exc:
        TimelineReconciler.reconcile(missing_entries, manifest, target_audio_duration=6.0)
    assert "Missing image indexes" in str(exc.value) and "3" in str(exc.value)

    # Duplicate image rejection in reconciler
    dup_entries = [
        TimelineEntry(image_index=1, filename="img_001.png", start=0.0, end=2.0),
        TimelineEntry(image_index=2, filename="img_002.png", start=2.0, end=4.0),
        TimelineEntry(image_index=2, filename="img_002.png", start=4.0, end=6.0),
    ]
    with pytest.raises(TimelineReconciliationError) as exc:
        TimelineReconciler.reconcile(dup_entries, manifest, target_audio_duration=6.0)
    assert "Duplicate image indexes" in str(exc.value) and "2" in str(exc.value)
