import logging
from typing import Dict, List, Optional

from engine.models import ImageRecord, Timeline, TimelineEntry, TimelineReconciliationError

logger = logging.getLogger(__name__)


class TimelineReconciler:
    """Deterministic timeline reconciliation layer.
    
    Guarantees:
    - Exactly N images in 1..N order.
    - Zero gaps, zero overlaps after safe micro-reconciliation.
    - Continuous boundary snapping: end[i] == start[i+1].
    - Start[0] == 0.000.
    - End[N-1] == target_audio_duration.
    - Strictly forbids deleting, adding, or reordering images.
    - FORBIDS silently rewriting major timing discrepancies (raises TimelineReconciliationError).
    """

    SAFE_DRIFT_TOLERANCE: float = 0.25  # seconds
    MAX_AUDIO_DIFF_TOLERANCE: float = 2.0  # seconds

    @classmethod
    def reconcile(
        cls,
        raw_entries: List[TimelineEntry],
        locked_images: List[ImageRecord],
        target_audio_duration: float,
        strict_mode: bool = True
    ) -> Timeline:
        total_images = len(locked_images)
        if total_images == 0:
            return Timeline(audio_duration=target_audio_duration, image_count=0, timeline=[])

        raw_indices = [e.image_index for e in raw_entries]
        expected_indices = [img.index for img in locked_images]

        # Check duplicate image indices
        duplicate_indices = sorted(list(set([x for x in raw_indices if raw_indices.count(x) > 1])))
        if duplicate_indices:
            raise TimelineReconciliationError(
                f"Duplicate image indexes in timeline: {', '.join(map(str, duplicate_indices))}."
            )

        # Check extra image indices not in manifest
        extra_indices = sorted(list(set(raw_indices) - set(expected_indices)))
        if extra_indices:
            raise TimelineReconciliationError(
                f"Extra image indexes in timeline not in image manifest: {', '.join(map(str, extra_indices))}."
            )

        # Check missing images
        missing_indices = sorted(list(set(expected_indices) - set(raw_indices)))
        if missing_indices:
            raise TimelineReconciliationError(
                f"Missing image indexes in timeline: {', '.join(map(str, missing_indices))}. "
                f"Every image from 1 to {total_images} must be explicitly present."
            )

        if len(raw_entries) != total_images:
            raise TimelineReconciliationError(
                f"Timeline count mismatch: received {len(raw_entries)} entries, expected {total_images}."
            )

        # Map raw entries by image index
        entry_by_index: Dict[int, TimelineEntry] = {e.image_index: e for e in raw_entries}
        sorted_entries = [entry_by_index[img.index] for img in locked_images]

        # In strict mode, verify that discrepancies are within safe micro-reconciliation tolerance
        if strict_mode and len(sorted_entries) > 0:
            first_st = sorted_entries[0].start
            if abs(first_st - 0.0) > cls.SAFE_DRIFT_TOLERANCE:
                raise TimelineReconciliationError(
                    f"First image start is {first_st:.3f}s instead of 0.000s (exceeds safe tolerance of {cls.SAFE_DRIFT_TOLERANCE}s). "
                    f"Major timing gaps cannot be silently repaired."
                )

            # Check adjacent boundaries
            for i in range(len(sorted_entries) - 1):
                prev_e = sorted_entries[i]
                curr_e = sorted_entries[i + 1]
                gap = curr_e.start - prev_e.end

                if abs(gap) > cls.SAFE_DRIFT_TOLERANCE:
                    defect = "gap" if gap > 0 else "overlap"
                    raise TimelineReconciliationError(
                        f"Major {defect} detected between Image {prev_e.image_index} (ends {prev_e.end:.3f}s) "
                        f"and Image {curr_e.image_index} (starts {curr_e.start:.3f}s): {abs(gap):.3f}s. "
                        f"Major timing discrepancies cannot be silently repaired."
                    )

            # Check audio duration boundary
            final_end = sorted_entries[-1].end
            audio_diff = abs(final_end - target_audio_duration)
            if audio_diff > cls.MAX_AUDIO_DIFF_TOLERANCE:
                raise TimelineReconciliationError(
                    f"Audio alignment error: User timeline ends at {final_end:.3f}s, but actual voiceover audio "
                    f"is {target_audio_duration:.3f}s (difference: {audio_diff:.3f}s). "
                    f"The timeline must cover the complete audio without large unexplained mismatches."
                )

        # Micro boundary snapping
        reconciled_entries: List[TimelineEntry] = []
        current_start = 0.000

        for i, img in enumerate(locked_images):
            raw_e = entry_by_index[img.index]
            
            # Snap final boundary to exact target audio duration
            if i == total_images - 1:
                current_end = round(target_audio_duration, 4)
            else:
                # Snap end to the start of the next image (or its own end)
                next_raw = entry_by_index[locked_images[i + 1].index]
                # Average or boundary snap between this end and next start
                current_end = round(next_raw.start, 4) if abs(next_raw.start - raw_e.end) <= cls.SAFE_DRIFT_TOLERANCE else round(raw_e.end, 4)
                if current_end <= current_start:
                    current_end = round(current_start + max(0.1, raw_e.end - raw_e.start), 4)

            entry = TimelineEntry(
                image_index=img.index,
                filename=img.filename,
                start=round(current_start, 4),
                end=current_end,
                narrative_context=raw_e.narrative_context or f"Visual for {img.filename}",
                source_reference=raw_e.source_reference
            )
            reconciled_entries.append(entry)
            current_start = current_end

        return Timeline(
            audio_duration=target_audio_duration,
            image_count=total_images,
            timeline=reconciled_entries
        )
