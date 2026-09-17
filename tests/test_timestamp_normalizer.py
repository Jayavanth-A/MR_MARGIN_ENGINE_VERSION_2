from pathlib import Path
from unittest.mock import MagicMock
import pytest

from engine.config import AppConfig
from engine.llm_client import MockLLMClient
from engine.timestamp_normalizer import (
    TimestampNormalizer,
    parse_time_str_to_seconds,
)


def test_parse_time_str_to_seconds():
    assert parse_time_str_to_seconds("00:00") == 0.0
    assert parse_time_str_to_seconds("00:04") == 4.0
    assert parse_time_str_to_seconds("01:30.500") == 90.5
    assert parse_time_str_to_seconds("01:02:03.500") == 3723.5
    assert parse_time_str_to_seconds("4.25s") == 4.25
    assert parse_time_str_to_seconds("1503.421") == 1503.421


def test_normalizer_always_dispatches_to_llm():
    """Verify that user text is ALWAYS dispatched directly to LLM without bypassing."""
    config = AppConfig()
    mock_llm = MockLLMClient()
    mock_llm.generate_json = MagicMock(side_effect=mock_llm.generate_json)

    normalizer = TimestampNormalizer(config, mock_llm)

    text = (
        "Image 1: 00:00 - 00:04\n"
        "Image 2: 00:04 - 00:08"
    )

    entries = normalizer.normalize(text, expected_count=2, audio_duration=8.0)

    # Verify generate_json was invoked directly with namespace='timestamp_normalization'
    mock_llm.generate_json.assert_called_once()
    call_kwargs = mock_llm.generate_json.call_args.kwargs
    assert call_kwargs["namespace"] == "timestamp_normalization"
    assert text in call_kwargs["user_prompt"]

    assert len(entries) == 2
    assert entries[0].image_index == 1
    assert entries[0].start == 0.0
    assert entries[0].end == 4.0
    assert entries[0].source_reference is not None


def test_normalizer_captures_source_reference():
    config = AppConfig()
    mock_llm = MockLLMClient()
    normalizer = TimestampNormalizer(config, mock_llm)

    text = (
        "Image 1: 00:00 - 00:02.5\n"
        "Image 2: 00:02.5 - 00:06.0"
    )

    entries = normalizer.normalize(text)
    assert len(entries) == 2
    assert "Image 1" in entries[0].source_reference
    assert "Image 2" in entries[1].source_reference


def test_normalizer_natural_language_timestamps():
    config = AppConfig()
    mock_llm = MockLLMClient()
    normalizer = TimestampNormalizer(config, mock_llm)

    text = (
        "Image 1 should appear from 0 to 4.5 seconds.\n"
        "Image 2 should appear from 4.5 to 9.0 seconds."
    )

    entries = normalizer.normalize(text)
    assert len(entries) == 2
    assert entries[0].image_index == 1
    assert entries[0].start == 0.0
    assert entries[0].end == 4.5
    assert entries[1].image_index == 2
    assert entries[1].start == 4.5
    assert entries[1].end == 9.0


def test_normalizer_arrow_timestamps():
    config = AppConfig()
    mock_llm = MockLLMClient()
    normalizer = TimestampNormalizer(config, mock_llm)

    text = (
        "1 → 00:00 to 00:03.2\n"
        "2 → 00:03.2 to 00:07.5"
    )

    entries = normalizer.normalize(text)
    assert len(entries) == 2
    assert entries[0].image_index == 1
    assert entries[0].start == 0.0
    assert entries[0].end == 3.2
    assert entries[1].image_index == 2
    assert entries[1].start == 3.2
    assert entries[1].end == 7.5
