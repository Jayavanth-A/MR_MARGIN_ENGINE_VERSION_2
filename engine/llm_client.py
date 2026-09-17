import abc
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional
import httpx

from engine.cache import CacheManager
from engine.config import AppConfig

logger = logging.getLogger(__name__)


def extract_json_from_response(text: str) -> Any:
    """Extract and parse JSON object or array from LLM response text,
    stripping markdown code fences or conversational preamble.
    """
    cleaned = text.strip()
    # Try finding markdown json fence
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    
    # Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try finding first [ or { to last ] or }
    first_bracket = min(
        [i for i in [cleaned.find('{'), cleaned.find('[')] if i != -1],
        default=-1
    )
    last_bracket = max(
        [cleaned.rfind('}'), cleaned.rfind(']')],
        default=-1
    )

    if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
        candidate = cleaned[first_bracket : last_bracket + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Failed to extract valid JSON from LLM response:\n{text[:500]}...")


class BaseLLMClient(abc.ABC):
    """Abstract interface for LLM calls."""

    @abc.abstractmethod
    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        namespace: str,
        cache_key_data: Optional[Any] = None
    ) -> Any:
        """Generate structured JSON response."""
        pass


class AICreditsClient(BaseLLMClient):
    """OpenAI-compatible client for AICredits.in API."""

    def __init__(self, config: AppConfig, cache_manager: Optional[CacheManager] = None):
        self.config = config
        self.cache_manager = cache_manager
        self.base_url = config.aicredits_base_url.rstrip("/")
        self.api_key = config.aicredits_api_key
        self.model = config.aicredits_model

        if not self.api_key:
            raise ValueError(
                "AICREDITS_API_KEY is not set. Please provide it in .env or via environment variable."
            )

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        namespace: str,
        cache_key_data: Optional[Any] = None
    ) -> Any:
        # Check cache first
        if self.cache_manager:
            cache_payload = {
                "system": system_prompt,
                "user": user_prompt,
                "model": self.model,
                "extra": cache_key_data
            }
            cached = self.cache_manager.get(namespace, cache_payload)
            if cached is not None:
                logger.info(f"Loaded cached LLM response for namespace: {namespace}")
                return cached

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": self.config.llm_temperature,
            "max_tokens": 8192,
            "response_format": {"type": "json_object"}
        }

        retries = self.config.llm_max_retries
        last_error: Optional[Exception] = None

        for attempt in range(1, retries + 1):
            try:
                with httpx.Client(timeout=self.config.llm_timeout_seconds) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 429:
                        wait_sec = attempt * 3
                        logger.warning(f"Rate limited (429). Retrying in {wait_sec}s...")
                        time.sleep(wait_sec)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    parsed = extract_json_from_response(content)

                    # Store in cache
                    if self.cache_manager:
                        self.cache_manager.set(namespace, cache_payload, parsed)

                    return parsed

            except Exception as e:
                last_error = e
                logger.warning(f"LLM call attempt {attempt}/{retries} failed: {e}")
                time.sleep(attempt * 2)

        raise RuntimeError(f"LLM request to {url} failed after {retries} retries: {last_error}")


class MockLLMClient(BaseLLMClient):
    """Mock LLM client for deterministic unit testing and offline pipeline verification."""

    def __init__(self, cache_manager: Optional[CacheManager] = None):
        self.cache_manager = cache_manager

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        namespace: str,
        cache_key_data: Optional[Any] = None
    ) -> Any:
        cache_payload = {
            "system": system_prompt,
            "user": user_prompt,
            "extra": cache_key_data
        }
        if self.cache_manager:
            cached = self.cache_manager.get(namespace, cache_payload)
            if cached is not None:
                logger.info(f"Loaded cached mock LLM response for namespace: {namespace}")
                return cached

        # If cache key provides chunk metadata, construct an intelligent dynamic timeline
        if namespace == "story_analysis":
            # Pass 1 mock: extract number of images and duration from prompt or cache_key
            extra = cache_key_data or {}
            total_images = extra.get("total_images", 10)
            duration = extra.get("audio_duration", 30.0)
            
            # Split into 3 sections
            sec1_end_img = max(1, total_images // 3)
            sec2_end_img = max(sec1_end_img + 1, (2 * total_images) // 3)
            t1 = duration * 0.33
            t2 = duration * 0.67

            res = {
                "narrative_summary": "Story progression covering introduction, core development, and conclusion.",
                "sections": [
                    {
                        "section_index": 1,
                        "title": "Introduction",
                        "image_start_index": 1,
                        "image_end_index": sec1_end_img,
                        "allocated_start_time": 0.0,
                        "allocated_end_time": round(t1, 3)
                    },
                    {
                        "section_index": 2,
                        "title": "Core Action",
                        "image_start_index": sec1_end_img + 1,
                        "image_end_index": sec2_end_img,
                        "allocated_start_time": round(t1, 3),
                        "allocated_end_time": round(t2, 3)
                    },
                    {
                        "section_index": 3,
                        "title": "Conclusion & Reflection",
                        "image_start_index": sec2_end_img + 1,
                        "image_end_index": total_images,
                        "allocated_start_time": round(t2, 3),
                        "allocated_end_time": round(duration, 3)
                    }
                ]
            }
            if self.cache_manager:
                self.cache_manager.set(namespace, cache_payload, res)
            return res

        elif namespace == "timeline_chunks":
            extra = cache_key_data or {}
            images = extra.get("images", [])
            start_t = extra.get("start_time", 0.0)
            end_t = extra.get("end_time", 10.0)
            total_dur = max(0.1, end_t - start_t)
            n = len(images)

            if n == 0:
                res = {"timeline": []}
                if self.cache_manager:
                    self.cache_manager.set(namespace, cache_payload, res)
                return res

            # Generate dynamic (non-uniform) weights based on prompt length or variation
            weights = []
            for i, img in enumerate(images):
                p_len = len(img.get("prompt", ""))
                w = 1.0 + (p_len % 7) * 0.15 + (i % 3) * 0.2
                weights.append(w)
            
            sum_w = sum(weights)
            current_t = start_t
            entries = []

            for i, img in enumerate(images):
                idx = img.get("index", i + 1)
                fn = img.get("filename", f"{idx}.jpg")
                dur = total_dur * (weights[i] / sum_w)
                next_t = round(current_t + dur, 4)
                if i == n - 1:
                    next_t = round(end_t, 4)
                
                entries.append({
                    "image_index": idx,
                    "filename": fn,
                    "start": round(current_t, 4),
                    "end": round(next_t, 4),
                    "narrative_context": f"Visual context for image {idx}"
                })
                current_t = next_t

            res = {"timeline": entries}
            if self.cache_manager:
                self.cache_manager.set(namespace, cache_payload, res)
            return res

        elif namespace == "timestamp_normalization":
            extra = cache_key_data or {}
            text = extra.get("chunk_text") or extra.get("source_timestamp_text") or extra.get("text") or ""
            if not text:
                text = user_prompt

            start_img = extra.get("start_image_index")
            if start_img is None:
                m_img = re.search(r"\(Images (\d+) to (\d+)\)", user_prompt)
                start_img = int(m_img.group(1)) if m_img else 1

            lines = [line.strip() for line in text.splitlines() if line.strip()]
            entries = []
            cur_idx = start_img

            from engine.timestamp_normalizer import parse_time_str_to_seconds

            for line in lines:
                # Skip prompt structural wrappers
                if line.startswith("---") or line.startswith("USER TIMESTAMP") or line.startswith("Convert every") or line.startswith("Expected image"):
                    continue
                match = re.search(
                    r"(?:image\s+|img\s+|#)?(\d+)?[\s:→\-]*?(?:(?:should\s+appear\s+)?from\s+)?([0-9:.]+)\s*(?:to|-|–|—)\s*([0-9:.]+)",
                    line,
                    re.IGNORECASE
                )
                if match:
                    idx_str = match.group(1)
                    idx = int(idx_str) if idx_str is not None else cur_idx
                    try:
                        st = parse_time_str_to_seconds(match.group(2))
                        en = parse_time_str_to_seconds(match.group(3))
                        entries.append({
                            "image_index": idx,
                            "start": round(st, 4),
                            "end": round(en, 4)
                        })
                        cur_idx = idx + 1
                    except Exception:
                        continue

            res = {"timeline": entries}
            if self.cache_manager:
                self.cache_manager.set(namespace, cache_payload, res)
            return res

        return {"status": "ok"}
