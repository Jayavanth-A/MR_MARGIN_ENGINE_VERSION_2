import logging
import statistics
from typing import Any, Dict, List, Optional, Tuple

from engine.config import AppConfig
from engine.llm_client import BaseLLMClient
from engine.models import (
    GlobalStoryPlan,
    ImageRecord,
    StorySection,
    Timeline,
    TimelineEntry,
)

logger = logging.getLogger(__name__)


class EditorialPlanner:
    """Two-Pass Hierarchical AI Video Editorial Planner.
    
    Pass 1: Maps global story sections to sequential image ranges and time windows.
    Pass 2: Plans granular dynamic durations for each sequential image chunk.
    """

    def __init__(self, config: AppConfig, llm_client: BaseLLMClient):
        self.config = config
        self.llm_client = llm_client

    def plan_story_sections(
        self,
        voiceover_text: str,
        audio_duration: float,
        images: List[ImageRecord]
    ) -> GlobalStoryPlan:
        """PASS 1: Analyze story progression and divide into coherent narrative sections
        mapped to sequential image index ranges.
        """
        total_images = len(images)
        paragraphs = [p.strip() for p in voiceover_text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [p.strip() for p in voiceover_text.splitlines() if p.strip()]
        if not paragraphs:
            paragraphs = [voiceover_text]

        logger.info(f"Pass 1: Analyzing {len(paragraphs)} voiceover paragraphs for {total_images} images over {audio_duration:.2f}s...")

        # If very small number of images (e.g. <= 25), single section is optimal
        if total_images <= self.config.max_chunk_images:
            return GlobalStoryPlan(
                audio_duration=audio_duration,
                total_images=total_images,
                narrative_summary="Single section narrative flow",
                sections=[
                    StorySection(
                        section_index=1,
                        title="Complete Story",
                        text=voiceover_text,
                        image_start_index=1,
                        image_end_index=total_images,
                        allocated_start_time=0.0,
                        allocated_end_time=audio_duration
                    )
                ]
            )

        # Prepare Pass 1 Prompt
        system_prompt = (
            "You are a master film director and AI video editor. "
            "Your task is to analyze the voiceover narration and an immutable ordered sequence of images, "
            "then partition the narrative into chronological story sections.\n\n"
            "RULES:\n"
            "1. Sequence is strictly 1 to N. Every image must belong to exactly one section.\n"
            "2. Section image ranges MUST be contiguous: section 1 starts at index 1; section K+1 starts at previous section end + 1.\n"
            "3. Time ranges MUST be contiguous: section 1 starts at 0.0; final section ends at exactly audio_duration.\n"
            "4. Distribute image ranges proportionally to narrative weight and visual story beats. Never dump excess images in the final section.\n"
            "5. Return pure JSON with structure: {'narrative_summary': '...', 'sections': [{'section_index': 1, 'title': '...', 'image_start_index': 1, 'image_end_index': X, 'allocated_start_time': 0.0, 'allocated_end_time': Y}, ...]}"
        )

        # Build image progression overview (sample or full depending on count)
        step = max(1, total_images // 30)
        sample_overview = [
            f"Image {img.index}: {img.prompt[:80]}"
            for i, img in enumerate(images)
            if i % step == 0 or i == 0 or i == total_images - 1
        ]

        user_prompt = (
            f"Total Audio Duration: {audio_duration:.3f} seconds\n"
            f"Total Images: {total_images} (indices 1 to {total_images})\n\n"
            f"VOICEOVER NARRATION:\n{voiceover_text[:4000]}\n\n"
            f"IMAGE PROGRESSION BEATS (Sampled):\n" + "\n".join(sample_overview) + "\n\n"
            f"Divide this into {max(2, min(len(paragraphs), total_images // 25))} to {max(3, total_images // 20)} chronological sections. "
            f"Ensure section 1 starts at image 1 and time 0.0, and the last section ends at image {total_images} and time {audio_duration:.3f}."
        )

        cache_key = {
            "total_images": total_images,
            "audio_duration": round(audio_duration, 3),
            "text_hash": hash(voiceover_text)
        }

        resp = self.llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            namespace="story_analysis",
            cache_key_data=cache_key
        )

        # Parse and rigorously reconcile sections
        raw_sections = resp.get("sections", [])
        sections: List[StorySection] = []

        if not raw_sections:
            # Fallback partitioning if LLM returned empty sections
            num_sec = max(2, total_images // self.config.max_chunk_images)
            imgs_per_sec = total_images // num_sec
            time_per_sec = audio_duration / num_sec
            for s in range(num_sec):
                s_start_img = s * imgs_per_sec + 1
                s_end_img = (s + 1) * imgs_per_sec if s < num_sec - 1 else total_images
                sections.append(
                    StorySection(
                        section_index=s + 1,
                        title=f"Section {s + 1}",
                        text="",
                        image_start_index=s_start_img,
                        image_end_index=s_end_img,
                        allocated_start_time=round(s * time_per_sec, 3),
                        allocated_end_time=round((s + 1) * time_per_sec if s < num_sec - 1 else audio_duration, 3)
                    )
                )
        else:
            # Validate and fix any contiguous range issues in LLM output
            curr_img_idx = 1
            curr_time = 0.0
            num_sec = len(raw_sections)

            for i, raw_sec in enumerate(raw_sections):
                is_last = (i == num_sec - 1)
                s_title = raw_sec.get("title", f"Section {i+1}")
                s_end_img = total_images if is_last else int(raw_sec.get("image_end_index", curr_img_idx + 10))
                s_end_img = max(curr_img_idx, min(total_images, s_end_img))
                if is_last:
                    s_end_img = total_images

                s_end_time = audio_duration if is_last else float(raw_sec.get("allocated_end_time", curr_time + 10.0))
                s_end_time = max(curr_time + 0.1, min(audio_duration, s_end_time))
                if is_last:
                    s_end_time = audio_duration

                sections.append(
                    StorySection(
                        section_index=i + 1,
                        title=s_title,
                        text=raw_sec.get("text_summary", ""),
                        image_start_index=curr_img_idx,
                        image_end_index=s_end_img,
                        allocated_start_time=round(curr_time, 3),
                        allocated_end_time=round(s_end_time, 3)
                    )
                )
                curr_img_idx = s_end_img + 1
                curr_time = s_end_time

                if curr_img_idx > total_images:
                    break

            # If sections did not reach total_images, extend last section
            if sections and sections[-1].image_end_index < total_images:
                sections[-1].image_end_index = total_images
                sections[-1].allocated_end_time = audio_duration

        return GlobalStoryPlan(
            audio_duration=audio_duration,
            total_images=total_images,
            narrative_summary=resp.get("narrative_summary", "Global story progression"),
            sections=sections
        )

    def plan_chunk_timelines(
        self,
        section: StorySection,
        images: List[ImageRecord],
        full_voiceover: str
    ) -> List[TimelineEntry]:
        """PASS 2: Plan dynamic image durations for a specific sequential chunk."""
        chunk_images = [img for img in images if section.image_start_index <= img.index <= section.image_end_index]
        n_images = len(chunk_images)
        if n_images == 0:
            return []

        logger.info(
            f"Pass 2: Planning chunk {section.section_index} ({section.title}) - "
            f"Images {section.image_start_index}..{section.image_end_index} ({n_images} images) "
            f"across {section.allocated_start_time:.2f}s..{section.allocated_end_time:.2f}s"
        )

        system_prompt = (
            "You are an expert AI video editor. "
            "You are assigning dynamic, narrative-justified display durations to a strictly ordered sequence of images.\n\n"
            "NON-NEGOTIABLE CORE CONSTRAINTS:\n"
            "1. IMMUTABLE SEQUENCE: Images must appear strictly in order: 1, 2, 3... Never reorder.\n"
            "2. EVERY IMAGE MANDATORY: Every provided image must appear. No images skipped, invented, or removed.\n"
            "3. DYNAMIC TIMING: Durations must be variable and driven by narrative pace and visual complexity. NO fixed 2s/3s.\n"
            "4. MEANINGFUL COVERAGE (NO LEFTOVER DUMPING): The final images must NOT be squeezed into tiny fractions of a second. "
            "Distribute visual transitions naturally across the entire narrative window.\n"
            "5. CONTINUOUS TIMING: timeline must start at allocated_start_time and end at allocated_end_time.\n\n"
            "Return structured JSON format:\n"
            "{\n"
            '  "timeline": [\n'
            '    {"image_index": <int>, "filename": "<str>", "start": <float>, "end": <float>, "narrative_context": "<str>"}\n'
            "  ]\n"
            "}"
        )

        image_items = [
            {"index": img.index, "filename": img.filename, "prompt": img.prompt}
            for img in chunk_images
        ]

        user_prompt = (
            f"Section: {section.title}\n"
            f"Allocated Window: {section.allocated_start_time:.3f}s to {section.allocated_end_time:.3f}s "
            f"(Total Duration: {section.allocated_duration:.3f}s)\n"
            f"Image Range: {section.image_start_index} to {section.image_end_index} (Count: {n_images})\n\n"
            f"NARRATION CONTEXT:\n{section.text or full_voiceover[:1500]}\n\n"
            f"ORDERED IMAGES AND PROMPTS:\n"
        )

        for img in image_items:
            user_prompt += f"[{img['index']}] {img['filename']} | Prompt: {img['prompt']}\n"

        cache_key = {
            "section_index": section.section_index,
            "image_start": section.image_start_index,
            "image_end": section.image_end_index,
            "start_time": section.allocated_start_time,
            "end_time": section.allocated_end_time,
            "images": image_items
        }

        # Attempt planning with anti-dumping heuristic check
        max_attempts = 2
        for attempt in range(max_attempts):
            resp = self.llm_client.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt if attempt == 0 else (
                    user_prompt + "\n\nCRITICAL FIX: The previous attempt compressed the final images into arbitrary filler. "
                    "You MUST allocate meaningful, smooth narrative durations to the final images. Do not dump them at the end!"
                ),
                namespace="timeline_chunks",
                cache_key_data=cache_key
            )

            raw_entries = resp.get("timeline", [])
            if len(raw_entries) != n_images:
                logger.warning(f"Chunk returned {len(raw_entries)} entries, expected {n_images}.")

            # Build entry objects
            entries: List[TimelineEntry] = []
            for item in raw_entries:
                idx = int(item.get("image_index", 0))
                fn = item.get("filename", "")
                st = float(item.get("start", 0.0))
                en = float(item.get("end", st + 1.0))
                ctx = item.get("narrative_context", "")
                entries.append(TimelineEntry(
                    image_index=idx,
                    filename=fn,
                    start=st,
                    end=en,
                    narrative_context=ctx
                ))

            # Anti-dumping check on this chunk
            if len(entries) >= 8:
                durations = [e.end - e.start for e in entries]
                tail_size = max(2, int(len(durations) * 0.2))
                med = statistics.median(durations)
                tail_med = statistics.median(durations[-tail_size:])
                if med > 0 and (tail_med / med) < self.config.tail_dumping_ratio_threshold and attempt < max_attempts - 1:
                    logger.warning(
                        f"Leftover dumping detected in chunk attempt {attempt+1}: "
                        f"tail median {tail_med:.2f}s vs median {med:.2f}s. Requesting revision..."
                    )
                    continue

            return entries

        return entries

    def generate_full_timeline(
        self,
        voiceover_text: str,
        audio_duration: float,
        images: List[ImageRecord]
    ) -> List[TimelineEntry]:
        """Execute full two-pass planning across all images and story sections."""
        # Pass 1: Global story sectioning
        story_plan = self.plan_story_sections(voiceover_text, audio_duration, images)

        # Pass 2: Local chunk timeline generation
        all_entries: List[TimelineEntry] = []
        for section in story_plan.sections:
            chunk_entries = self.plan_chunk_timelines(section, images, voiceover_text)
            all_entries.extend(chunk_entries)

        return all_entries
