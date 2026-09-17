import json
import logging
from pathlib import Path
from typing import Callable, List, Optional

from rich.console import Console

from engine.audio import format_timestamp, get_audio_duration, verify_rendered_video
from engine.cache import CacheManager
from engine.config import AppConfig
from engine.llm_client import AICreditsClient, BaseLLMClient, MockLLMClient
from engine.models import InputValidationReport, Timeline, TimelineEntry, VerificationReport
from engine.reconciler import TimelineReconciler
from engine.renderer import VideoRenderer
from engine.timestamp_normalizer import TimestampNormalizer
from engine.validator import InputValidator, TimelineValidator, parse_voiceover_paragraphs

logger = logging.getLogger(__name__)


class VideoAutomationPipeline:
    """Master orchestrator for Timestamp-Normalization Video Pipeline."""

    def __init__(
        self,
        config: AppConfig,
        llm_client: Optional[BaseLLMClient] = None,
        use_mock_llm: bool = False,
        console: Optional[Console] = None
    ):
        self.config = config
        self.console = console or Console()
        self.cache_manager = CacheManager(config.cache_dir)

        if llm_client:
            self.llm_client = llm_client
        elif use_mock_llm:
            self.llm_client = MockLLMClient(cache_manager=self.cache_manager)
        else:
            self.llm_client = AICreditsClient(config, cache_manager=self.cache_manager)

        self.normalizer = TimestampNormalizer(config, self.llm_client)
        self.renderer = VideoRenderer(config)

    def _safe_print(self, text: str):
        """Safely print Rich formatted messages preventing Windows charmap crashes."""
        try:
            self.console.print(text)
        except Exception:
            try:
                clean = text.encode("ascii", errors="replace").decode("ascii")
                self.console.print(clean)
            except Exception:
                pass

    def _notify(
        self,
        stage: str,
        message: str,
        callback: Optional[Callable[[str, str], None]] = None
    ):
        """Helper to emit logs and notify dashboard UI."""
        self._safe_print(f"[bold cyan]{stage}[/bold cyan] {message}")
        if callback:
            try:
                callback(stage, message)
            except Exception:
                pass

    def run(
        self,
        images_dir: Path,
        prompts_file: Path,
        voiceover_text_file: Path,
        voiceover_audio_file: Path,
        timestamp_text: Optional[str] = None,
        timestamp_file: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        generate_preview: bool = False,
        status_callback: Optional[Callable[[str, str], None]] = None
    ) -> Path:
        out_dir = Path(output_dir or self.config.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        render_log_lines = []

        def log_event(msg: str):
            render_log_lines.append(msg)
            logger.info(msg)

        log_event("Starting Timestamp-Normalization Video Automation Pipeline...")

        # -------------------------------------------------------------
        # [1] Scan images
        # -------------------------------------------------------------
        # -------------------------------------------------------------
        # [1/12] Scan images & build locked numeric manifest
        # -------------------------------------------------------------
        self._notify("[1/12]", "Scanning images and building locked numeric manifest...", status_callback)
        report, locked_sequence = InputValidator.validate_inputs(images_dir, prompts_file)

        # Save validation report
        val_report_path = out_dir / "validation_report.json"
        val_report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

        if not report.is_valid or locked_sequence is None:
            self._safe_print(f"[bold red]{report.format_console_report()}[/bold red]")
            raise ValueError(f"Input validation failed:\n{report.format_console_report()}")

        self._safe_print(f"[bold green]✓ {report.total_images} images detected[/bold green]")
        log_event(f"Images verified: {report.total_images}")

        # -------------------------------------------------------------
        # [2/12] Validate numeric sequence (1..N)
        # -------------------------------------------------------------
        self._notify("[2/12]", "Validating numeric sequence (1..N)...", status_callback)
        self._safe_print(f"[bold green]✓ Sequence detected: 1 → {report.total_images}[/bold green]")
        log_event(f"Sequence locked: 1 -> {report.total_images}")

        # -------------------------------------------------------------
        # [3/12] Validate prompts against images
        # -------------------------------------------------------------
        self._notify("[3/12]", "Validating prompts against images...", status_callback)
        self._safe_print(f"[bold green]✓ {report.total_prompts}/{report.total_images} prompts matched[/bold green]")
        log_event(f"Prompts matched: {report.total_prompts}/{report.total_images}")

        # Generate and save prompt_manifest.json (audit manifest)
        prompt_manifest = {
            str(img.index): {
                "image": img.filename,
                "prompt": img.prompt,
            }
            for img in locked_sequence
        }
        manifest_path = out_dir / "prompt_manifest.json"
        manifest_path.write_text(json.dumps(prompt_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        log_event(f"Saved prompt manifest ({len(prompt_manifest)} entries) to {manifest_path.name}")

        # -------------------------------------------------------------
        # [4/12] Validate voiceover text
        # -------------------------------------------------------------
        self._notify("[4/12]", "Validating voiceover text...", status_callback)
        vo_path = Path(voiceover_text_file)
        if not vo_path.exists():
            raise FileNotFoundError(f"Voiceover text file not found: {vo_path}")
        voiceover_text = vo_path.read_text(encoding="utf-8")
        if not voiceover_text.strip():
            raise ValueError("Voiceover text file is empty.")

        # Parse and save voiceover_paragraphs.json (audit manifest)
        paragraphs = parse_voiceover_paragraphs(voiceover_text)
        paragraphs_path = out_dir / "voiceover_paragraphs.json"
        paragraphs_path.write_text(json.dumps(paragraphs, indent=2, ensure_ascii=False), encoding="utf-8")
        self._safe_print(f"[bold green]✓ Text loaded and validated ({len(paragraphs)} paragraphs)[/bold green]")
        log_event(f"Voiceover text loaded ({len(paragraphs)} paragraphs, {len(voiceover_text)} chars)")

        # -------------------------------------------------------------
        # [5/12] Probe voiceover audio duration with FFprobe
        # -------------------------------------------------------------
        self._notify("[5/12]", "Probing voiceover audio duration with FFprobe...", status_callback)
        audio_duration = get_audio_duration(Path(voiceover_audio_file))
        formatted_dur = format_timestamp(audio_duration)
        self._safe_print(f"[bold green]✓ Duration: {formatted_dur} ({audio_duration:.3f}s)[/bold green]")
        log_event(f"Audio duration probed: {audio_duration:.3f}s ({formatted_dur})")

        # -------------------------------------------------------------
        # [6/12] Receive & preserve exact user timestamp text
        # -------------------------------------------------------------
        self._notify("[6/12]", "Receiving timestamp instructions...", status_callback)
        raw_text = ""
        if timestamp_text is not None and len(timestamp_text) > 0:
            # Must NEVER call .strip() - preserve user text untouched
            raw_text = timestamp_text
        elif timestamp_file and Path(timestamp_file).exists():
            raw_text = Path(timestamp_file).read_text(encoding="utf-8")
        else:
            raise ValueError("No timestamp text provided. Please provide timestamp text or file.")

        # Crucial Requirement: Preserve exact raw user input untouched
        source_text_path = out_dir / "source_timestamp_text.txt"
        source_text_path.write_text(raw_text, encoding="utf-8")
        self._safe_print(f"[bold green]✓ Saved exact user timestamp text ({len(raw_text.splitlines())} lines)[/bold green]")
        log_event(f"Source timestamp text saved to {source_text_path.name}")

        # -------------------------------------------------------------
        # [7/12] Dispatch timestamp text to LLM
        # -------------------------------------------------------------
        self._notify("[7/12]", "Dispatching timestamp text to LLM...", status_callback)
        log_event("Sending timestamp text for normalization...")

        # -------------------------------------------------------------
        # [8/12] Normalize LLM response into structured timeline
        # -------------------------------------------------------------
        self._notify("[8/12]", "Normalizing timeline entries...", status_callback)
        normalized_entries = self.normalizer.normalize(
            timestamp_text=raw_text,
            expected_count=len(locked_sequence),
            audio_duration=audio_duration,
            status_callback=status_callback
        )

        # Match filenames from locked sequence
        manifest_map = {img.index: img for img in locked_sequence}
        for entry in normalized_entries:
            if entry.image_index in manifest_map:
                entry.filename = manifest_map[entry.image_index].filename

        llm_timeline = Timeline(
            audio_duration=audio_duration,
            image_count=len(normalized_entries),
            timeline=normalized_entries
        )

        # Save llm_timeline.json
        llm_timeline_path = out_dir / "llm_timeline.json"
        llm_timeline_path.write_text(llm_timeline.model_dump_json(indent=2), encoding="utf-8")
        self._safe_print(f"[bold green]✓ Normalized {len(normalized_entries)} timeline entries[/bold green]")
        log_event(f"Saved llm_timeline.json with {len(normalized_entries)} entries")

        # -------------------------------------------------------------
        # [9/12] Validate normalized timeline against manifest & rules
        # -------------------------------------------------------------
        self._notify("[9/12]", "Validating normalized timeline against manifest & rules...", status_callback)
        entry_indices = [e.image_index for e in normalized_entries]
        expected_indices = list(range(1, len(locked_sequence) + 1))

        # Check out-of-range / extra indices
        out_of_range = sorted(list(set([x for x in entry_indices if x < 1 or x > len(locked_sequence)])))
        if out_of_range:
            msg = f"❌ TIMELINE VALIDATION FAILED\nOut-of-range image indexes in timeline: {out_of_range} (valid range is 1 to {len(locked_sequence)})"
            self._safe_print(f"[bold red]{msg}[/bold red]")
            raise ValueError(msg)

        missing_in_timeline = sorted(list(set(expected_indices) - set(entry_indices)))
        if missing_in_timeline:
            msg = f"❌ TIMELINE VALIDATION FAILED\nMissing image indexes: {', '.join(map(str, missing_in_timeline))}"
            self._safe_print(f"[bold red]{msg}[/bold red]")
            raise ValueError(msg)

        duplicate_in_timeline = sorted(list(set([x for x in entry_indices if entry_indices.count(x) > 1])))
        if duplicate_in_timeline:
            msg = f"❌ TIMELINE VALIDATION FAILED\nDuplicate image indexes in timeline: {', '.join(map(str, duplicate_in_timeline))}"
            self._safe_print(f"[bold red]{msg}[/bold red]")
            raise ValueError(msg)

        # Check zero or negative durations
        invalid_durations = [e for e in normalized_entries if (e.end - e.start) <= 0]
        if invalid_durations:
            msg = f"❌ TIMELINE VALIDATION FAILED\nZero or negative durations detected: {[f'Image {e.image_index} ({e.start}->{e.end})' for e in invalid_durations]}"
            self._safe_print(f"[bold red]{msg}[/bold red]")
            raise ValueError(msg)

        self._safe_print("[bold green]✓ All image indexes matched[/bold green]")

        # -------------------------------------------------------------
        # [10/12] Reconcile timeline boundaries & safe micro-snapping
        # -------------------------------------------------------------
        self._notify("[10/12]", "Reconciling timeline boundaries & safe micro-snapping...", status_callback)
        reconciled_timeline = TimelineReconciler.reconcile(
            raw_entries=normalized_entries,
            locked_images=locked_sequence,
            target_audio_duration=audio_duration
        )

        # Save final_timeline.json (and legacy timeline.json for compatibility)
        final_timeline_path = out_dir / "final_timeline.json"
        final_timeline_path.write_text(reconciled_timeline.model_dump_json(indent=2), encoding="utf-8")
        (out_dir / "timeline.json").write_text(reconciled_timeline.model_dump_json(indent=2), encoding="utf-8")

        # Save timing report
        timing_report_path = out_dir / "timing_report.json"
        timing_report = {
            "audio_duration": audio_duration,
            "formatted_duration": formatted_dur,
            "total_images": len(locked_sequence),
            "reconciled_entries": len(reconciled_timeline.timeline),
            "first_start": reconciled_timeline.timeline[0].start if reconciled_timeline.timeline else 0.0,
            "final_end": reconciled_timeline.timeline[-1].end if reconciled_timeline.timeline else 0.0,
        }
        timing_report_path.write_text(json.dumps(timing_report, indent=2), encoding="utf-8")
        (out_dir / "planning_report.json").write_text(json.dumps(timing_report, indent=2), encoding="utf-8")

        tl_validation = TimelineValidator.validate_timeline(
            timeline=reconciled_timeline,
            expected_image_count=len(locked_sequence),
            expected_audio_duration=audio_duration
        )
        if not tl_validation.is_valid:
            err_str = "\n".join(tl_validation.errors)
            self._safe_print(f"[bold red]❌ TIMELINE ERROR:\n{err_str}[/bold red]")
            raise ValueError(f"Final timeline validation failed:\n{err_str}")

        self._safe_print(f"[bold green]✓ {len(locked_sequence)}/{len(locked_sequence)} images strictly verified[/bold green]")
        self._safe_print("[bold green]✓ Continuous timing, no gaps, no overlaps[/bold green]")
        self._safe_print(f"[bold green]✓ Full audio coverage (0.000 → {audio_duration:.3f}s)[/bold green]")
        log_event("Final timeline validation passed.")

        # -------------------------------------------------------------
        # [11/12] Render with FFmpeg
        # -------------------------------------------------------------
        self._notify("[11/12]", "Rendering video with FFmpeg...", status_callback)
        final_video_path = out_dir / "final_video.mp4"
        self.renderer.render(
            timeline=reconciled_timeline,
            images_dir=images_dir,
            audio_path=Path(voiceover_audio_file),
            output_path=final_video_path
        )

        if generate_preview:
            preview_video_path = out_dir / "preview.mp4"
            self.renderer.render(
                timeline=reconciled_timeline,
                images_dir=images_dir,
                audio_path=Path(voiceover_audio_file),
                output_path=preview_video_path,
                is_preview=True
            )

        self._safe_print("[bold green]✓ FFmpeg render complete[/bold green]")
        log_event(f"Video rendered to {final_video_path.name}")

        # -------------------------------------------------------------
        # [12/12] Post-render FFprobe verification
        # -------------------------------------------------------------
        self._notify("[12/12]", "Verifying final MP4 with FFprobe...", status_callback)
        verification = verify_rendered_video(
            final_video_path,
            expected_audio_duration=audio_duration,
            tolerance=0.25,
            expected_width=self.config.video_width,
            expected_height=self.config.video_height,
            expected_fps=self.config.video_fps,
            expected_video_codec="libx264",
            expected_audio_codec="aac"
        )
        if not verification.video_valid:
            err_msg = "\n".join(verification.errors)
            self._safe_print(f"[bold red]❌ VIDEO VERIFICATION FAILED:\n{err_msg}[/bold red]")
            raise ValueError(f"Post-render video verification failed:\n{err_msg}")

        self._safe_print(
            f"[bold green]✓ Verification Passed: {verification.width}x{verification.height} "
            f"@{verification.fps}fps | Video: {verification.video_duration:.3f}s vs Audio: {verification.audio_duration:.3f}s[/bold green]"
        )
        # Write render log
        (out_dir / "render_log.txt").write_text("\n".join(render_log_lines), encoding="utf-8")

        self.console.print(f"\n[bold green]FINAL VIDEO:\n{final_video_path.as_posix()}[/bold green]\n")

        return final_video_path
