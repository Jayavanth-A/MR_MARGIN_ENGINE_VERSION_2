import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from engine.config import AppConfig
from engine.llm_client import BaseLLMClient
from engine.models import TimelineEntry

logger = logging.getLogger(__name__)


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


class TimestampNormalizer:
    """Normalizes human-provided timestamp instructions into strict JSON timeline via LLM.
    
    The LLM always receives the entire user text and translates it into strict JSON schema.
    No local regex parsing is used to bypass the LLM.
    """

    NORMALIZATION_SYSTEM_PROMPT = (
        "You are a timestamp normalization engine for an automated video rendering pipeline.\n\n"
        "The user will provide timestamp instructions describing which image should appear at which point in a video.\n\n"
        "Your task is to convert the user's timestamp instructions into the required strict JSON schema.\n\n"
        "You are NOT an editorial planner.\n"
        "You must NOT invent, remove, skip, reorder, or renumber images.\n"
        "Preserve every image index explicitly present in the user's input.\n"
        "Interpret timestamps accurately.\n\n"
        "Convert timestamps such as:\n"
        "MM:SS\n"
        "MM:SS.mmm\n"
        "HH:MM:SS\n"
        "HH:MM:SS.mmm\n"
        "seconds\n"
        "natural-language timestamp descriptions\n"
        "into decimal seconds.\n\n"
        "For each image entry, also include a 'source_reference' field containing the exact source phrase or line interpreted.\n\n"
        "Return JSON only.\n"
        "Do not return Markdown.\n"
        "Do not return explanations.\n"
        "Do not return code fences.\n\n"
        "If the input is ambiguous or cannot be safely normalized, return a structured error instead of inventing information.\n\n"
        "Output JSON schema:\n"
        "{\n"
        '  "timeline": [\n'
        '    {"image_index": <int>, "start": <float>, "end": <float>, "source_reference": "<str>"}\n'
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
        audio_duration: Optional[float] = None
    ) -> List[TimelineEntry]:
        """Normalize user timestamp text into structured TimelineEntry items.
        
        MANDATORY RULE: The user timestamp text is ALWAYS sent to the AICredits LLM.
        No local regex-first parsing or bypass is performed.
        """
        cleaned_text = timestamp_text.strip()
        if not cleaned_text:
            raise ValueError("Timestamp text is empty.")

        logger.info("Sending complete user timestamp text to AICredits LLM for normalization...")

        # Cache key based on complete source text, model, and system prompt
        cache_payload = {
            "source_timestamp_text": cleaned_text,
            "model": self.config.aicredits_model,
            "expected_count": expected_count,
            "audio_duration": audio_duration
        }

        user_prompt = (
            f"USER TIMESTAMP INSTRUCTIONS:\n"
            f"----------------------------------------\n"
            f"{cleaned_text}\n"
            f"----------------------------------------\n"
            f"Convert every image timestamp above into strict JSON schema:\n"
            f"{{'timeline': [{{'image_index': <int>, 'start': <float>, 'end': <float>, 'source_reference': '<str>'}}, ...]}}."
        )

        response = self.llm_client.generate_json(
            system_prompt=self.NORMALIZATION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            namespace="timestamp_normalization",
            cache_key_data=cache_payload
        )

        if isinstance(response, dict) and "timeline" in response:
            raw_entries = response["timeline"]
        elif isinstance(response, list):
            raw_entries = response
        else:
            raise ValueError(f"LLM returned invalid timeline structure: {response}")

        # Convert to TimelineEntry models with source traceability
        normalized_entries: List[TimelineEntry] = []
        for i, item in enumerate(raw_entries):
            idx = int(item.get("image_index", i + 1))
            st = float(item.get("start", 0.0))
            en = float(item.get("end", st))
            src_ref = item.get("source_reference") or f"Line {i+1} from user timestamp input"

            normalized_entries.append(
                TimelineEntry(
                    image_index=idx,
                    filename="",  # Resolved from immutable manifest
                    start=round(st, 4),
                    end=round(en, 4),
                    narrative_context=f"Normalized timestamp for image {idx}",
                    source_reference=src_ref
                )
            )

        return normalized_entries
