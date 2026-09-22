from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, computed_field


class ImageRecord(BaseModel):
    """Represents a validated, ordered image item."""
    index: int
    filename: str
    path: Path
    prompt: str

    model_config = {"frozen": True}


class InputValidationReport(BaseModel):
    """Detailed report for pre-LLM input verification."""
    is_valid: bool
    total_images: int
    total_prompts: int
    expected_count: int
    missing_images: List[int] = Field(default_factory=list)
    missing_prompts: List[int] = Field(default_factory=list)
    duplicate_images: List[int] = Field(default_factory=list)
    duplicate_prompts: List[int] = Field(default_factory=list)
    invalid_filenames: List[str] = Field(default_factory=list)
    sequence_gaps: List[int] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)

    def format_console_report(self) -> str:
        """Format an actionable, clear diagnostic output matching user specification."""
        if self.is_valid:
            return f"✓ Input Validation Passed: {self.total_images} images and prompts verified (1 → {self.total_images})"

        lines = [
            "==================================================",
            "❌ INPUT VALIDATION FAILED",
            "==================================================",
            f"Images detected: {self.total_images}",
            f"Prompts detected: {self.total_prompts}",
            ""
        ]

        if self.invalid_filenames:
            lines.append("Invalid image filenames (missing numeric prefix like '1_name.jpg'):")
            for fn in self.invalid_filenames:
                lines.append(f"  - {fn}")
            lines.append("")

        if self.duplicate_images:
            lines.append("Duplicate image indexes:")
            for idx in self.duplicate_images:
                lines.append(f"  - {idx}")
            lines.append("")

        if self.duplicate_prompts:
            lines.append("Duplicate prompt indexes:")
            for idx in self.duplicate_prompts:
                lines.append(f"  - {idx}")
            lines.append("")

        if self.missing_images:
            lines.append("Missing image indexes (prompt exists, but image does not):")
            for idx in self.missing_images:
                lines.append(f"  - {idx}")
            lines.append("")

        if self.missing_prompts:
            lines.append("Missing prompt indexes (image exists, but prompt does not):")
            for idx in self.missing_prompts:
                lines.append(f"  - {idx}")
            lines.append("")

        if self.sequence_gaps:
            lines.append("Sequence gaps detected (expected consecutive 1..N):")
            for gap in self.sequence_gaps:
                lines.append(f"  - Missing index {gap}")
            lines.append("")

        lines.append("Status: FAILED")
        lines.append("LLM processing will not start. Please resolve input issues.")
        lines.append("==================================================")
        return "\n".join(lines)


class StorySection(BaseModel):
    """A semantic section of the story and its mapped image range."""
    section_index: int
    title: str
    text: str
    image_start_index: int
    image_end_index: int
    allocated_start_time: float
    allocated_end_time: float

    @property
    def allocated_duration(self) -> float:
        return max(0.0, self.allocated_end_time - self.allocated_start_time)

    @property
    def image_count(self) -> int:
        return self.image_end_index - self.image_start_index + 1


class GlobalStoryPlan(BaseModel):
    """Pass 1 Output: Narrative structure mapped to contiguous image slices."""
    audio_duration: float
    total_images: int
    narrative_summary: str
    sections: List[StorySection]


class TimelineEntry(BaseModel):
    """An individual image placement on the timeline."""
    image_index: int
    filename: str
    start: float
    end: float
    narrative_context: Optional[str] = None
    source_reference: Optional[str] = None

    @computed_field
    @property
    def duration(self) -> float:
        return round(self.end - self.start, 4)


class TimelineReconciliationError(Exception):
    """Raised when a timeline contains major gaps or overlaps exceeding safe reconciliation tolerance."""
    pass


class Timeline(BaseModel):
    """Final, reconciled timeline matching user output format."""
    audio_duration: float
    image_count: int
    timeline: List[TimelineEntry]


class TimelineValidationReport(BaseModel):
    """Deterministic validation of the generated timeline."""
    is_valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    leftover_dump_detected: bool = False
    min_duration: float = 0.0
    max_duration: float = 0.0
    median_duration: float = 0.0
    tail_median_duration: float = 0.0
    details: Dict[str, Any] = Field(default_factory=dict)


class VerificationReport(BaseModel):
    """Post-render FFprobe verification report of the final video."""
    video_valid: bool
    video_path: str
    video_duration: float
    audio_duration: float
    duration_difference: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str
    errors: List[str] = Field(default_factory=list)


class CapCutExportReport(BaseModel):
    """Report detailing CapCut project export and validation."""
    success: bool
    project_dir: Path
    project_name: str
    image_count: int
    duration_seconds: float
    duration_us: int
    registered_in_capcut: bool = False
    capcut_draft_path: Optional[Path] = None
    validation_passed: bool = False
    errors: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)
