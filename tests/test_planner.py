from pathlib import Path
import pytest

from engine.config import AppConfig
from engine.llm_client import MockLLMClient
from engine.models import ImageRecord
from engine.planner import EditorialPlanner


def test_planner_story_sections_and_chunk_timelines():
    config = AppConfig(max_chunk_images=5)
    mock_llm = MockLLMClient()
    planner = EditorialPlanner(config, mock_llm)

    images = [
        ImageRecord(index=i, filename=f"{i}.jpg", path=Path(f"{i}.jpg"), prompt=f"Prompt for visual beat {i}")
        for i in range(1, 13)
    ]

    voiceover = (
        "Paragraph 1: In the beginning there was silence.\n\n"
        "Paragraph 2: Machines began humming across the floor.\n\n"
        "Paragraph 3: As twilight set in, the doors closed quietly."
    )

    audio_duration = 36.0

    # Test Pass 1
    story_plan = planner.plan_story_sections(voiceover, audio_duration, images)
    assert story_plan.total_images == 12
    assert len(story_plan.sections) >= 2
    assert story_plan.sections[0].image_start_index == 1
    assert story_plan.sections[-1].image_end_index == 12

    # Test full timeline generation
    entries = planner.generate_full_timeline(voiceover, audio_duration, images)
    assert len(entries) == 12
    assert [e.image_index for e in entries] == list(range(1, 13))
