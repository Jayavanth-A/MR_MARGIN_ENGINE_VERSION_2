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
from engine.validator import InputValidator, TimelineValidator

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

    def _notify(
        self,
        stage: str,
        message: str,
        callback: Optional[Callable[[str, str], None]] = None
    ):
        """Helper to emit logs and notify dashboard UI."""
        self.console.print(f"[bold cyan]{stage}[/bold cyan] {message}")
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
        self._notify("[1/13]", "Scanning images...", status_callback)
        report, locked_sequence = InputValidator.validate_inputs(images_dir, prompts_file)

        # Save validation report
        val_report_path = out_dir / "validation_report.json"
        val_report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

        if not report.is_valid or locked_sequence is None:
            self.console.print(f"[bold red]{report.format_console_report()}[/bold red]")
            raise ValueError(f"Input validation failed:\n{report.format_console_report()}")

        self.console.print(f"[bold green]✓ {report.total_images} images detected[/bold green]")
        log_event(f"Images verified: {report.total_images}")

        # -------------------------------------------------------------
        # [2] Extract numeric indexes
        # -------------------------------------------------------------
        self._notify("[2/13]", "Extracting image indexes...", status_callback)
        self.console.print(f"[bold green]✓ Sequence detected: 1 → {report.total_images}[/bold green]")
        log_event(f"Sequence locked: 1 -> {report.total_images}")

        # -------------------------------------------------------------
        # [3] Validate prompts
        # -------------------------------------------------------------
        self._notify("[3/13]", "Validating prompts...", status_callback)
        self.console.print(f"[bold green]✓ {report.total_prompts}/{report.total_images} prompts matched[/bold green]")
        log_event(f"Prompts matched: {report.total_prompts}/{report.total_images}")

        # -------------------------------------------------------------
        # [4] Load voiceover text
        # -------------------------------------------------------------
        self._notify("[4/13]", "Loading voiceover text...", status_callback)
        vo_path = Path(voiceover_text_file)
        if not vo_path.exists():
            raise FileNotFoundError(f"Voiceover text file not found: {vo_path}")
        voiceover_text = vo_path.read_text(encoding="utf-8").strip()
        self.console.print("[bold green]✓ Text loaded[/bold green]")
        log_event(f"Voiceover text loaded ({len(voiceover_text)} chars)")

        # -------------------------------------------------------------
        # [5] Probe audio duration
        # -------------------------------------------------------------
        self._notify("[5/13]", "Probing audio duration...", status_callback)
        audio_duration = get_audio_duration(Path(voiceover_audio_file))
        formatted_dur = format_timestamp(audio_duration)
        self.console.print(f"[bold green]✓ Duration: {formatted_dur} ({audio_duration:.3f}s)[/bold green]")
        log_event(f"Audio duration probed: {audio_duration:.3f}s ({formatted_dur})")

        # -------------------------------------------------------------
        # [6] Receive & preserve exact timestamp text
        # -------------------------------------------------------------
        self._notify("[6/13]", "Receiving timestamp instructions...", status_callback)
        raw_text = ""
        if timestamp_text and timestamp_text.strip():
            raw_text = timestamp_text.strip()
        elif timestamp_file and Path(timestamp_file).exists():
            raw_text = Path(timestamp_file).read_text(encoding="utf-8").strip()
        else:
            raise ValueError("No timestamp text provided. Please provide timestamp text or file.")

        # Crucial Requirement: Preserve exact raw user input
        source_text_path = out_dir / "source_timestamp_text.txt"
        source_text_path.write_text(raw_text, encoding="utf-8")
        self.console.print(f"[bold green]✓ Saved exact user timestamp text ({len(raw_text.splitlines())} lines)[/bold green]")
        log_event(f"Source timestamp text saved to {source_text_path.name}")

        # -------------------------------------------------------------
        # [7] Send timestamp text to AICredits
        # -------------------------------------------------------------
        self._notify("[7/13]", "Sending timestamp text to AICredits...", status_callback)
        log_event("Sending timestamp text for normalization...")

        # -------------------------------------------------------------
        # [8] Normalize LLM response
        # -------------------------------------------------------------
        self._notify("[8/13]", "Normalizing timeline...", status_callback)
        normalized_entries = self.normalizer.normalize(
            timestamp_text=raw_text,
            expected_count=len(locked_sequence),
            audio_duration=audio_duration
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
        self.console.print(f"[bold green]✓ Normalized {len(normalized_entries)} timeline entries[/bold green]")
        log_event(f"Saved llm_timeline.json with {len(normalized_entries)} entries")

        # -------------------------------------------------------------
        # [9] Validate normalized timeline against manifest & rules
        # -------------------------------------------------------------
        self._notify("[9/13]", "Validating normalized timeline...", status_callback)
        entry_indices = [e.image_index for e in normalized_entries]
        expected_indices = list(range(1, len(locked_sequence) + 1))

        missing_in_timeline = sorted(list(set(expected_indices) - set(entry_indices)))
        duplicate_in_timeline = sorted(list(set([x for x in entry_indices if entry_indices.count(x) > 1])))

        if missing_in_timeline:
            msg = f"❌ TIMELINE VALIDATION FAILED\nMissing image indexes: {', '.join(map(str, missing_in_timeline))}"
            self.console.print(f"[bold red]{msg}[/bold red]")
            raise ValueError(msg)

        if duplicate_in_timeline:
            msg = f"❌ TIMELINE VALIDATION FAILED\nDuplicate image indexes in timeline: {', '.join(map(str, duplicate_in_timeline))}"
            self.console.print(f"[bold red]{msg}[/bold red]")
            raise ValueError(msg)

        self.console.print("[bold green]✓ All image indexes matched[/bold green]")

        # -------------------------------------------------------------
        # [10] Reconcile timeline (zero gaps, zero overlaps, exact duration)
        # -------------------------------------------------------------
        self._notify("[10/13]", "Reconciling timeline boundaries...", status_callback)
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

        self.console.print("[bold green]✓ Timeline reconciled[/bold green]")
        log_event(f"Reconciled timeline saved to {final_timeline_path.name}")

        # -------------------------------------------------------------
        # [11] Final timeline validation
        # -------------------------------------------------------------
        self._notify("[11/13]", "Performing final timeline validation...", status_callback)
        tl_validation = TimelineValidator.validate_timeline(
            timeline=reconciled_timeline,
            expected_image_count=len(locked_sequence),
            expected_audio_duration=audio_duration
        )
        if not tl_validation.is_valid:
            err_str = "\n".join(tl_validation.errors)
            self.console.print(f"[bold red]❌ TIMELINE ERROR:\n{err_str}[/bold red]")
            raise ValueError(f"Final timeline validation failed:\n{err_str}")

        self.console.print(f"[bold green]✓ {len(locked_sequence)}/{len(locked_sequence)} images strictly verified[/bold green]")
        self.console.print("[bold green]✓ Continuous timing, no gaps, no overlaps[/bold green]")
        self.console.print(f"[bold green]✓ Full audio coverage (0.000 → {audio_duration:.3f}s)[/bold green]")
        log_event("Final timeline validation passed.")

        # -------------------------------------------------------------
        # [12] Render with FFmpeg
        # -------------------------------------------------------------
        self._notify("[12/13]", "Rendering video with FFmpeg...", status_callback)
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

        self.console.print("[bold green]✓ FFmpeg render complete[/bold green]")
        log_event(f"Video rendered to {final_video_path.name}")

        # -------------------------------------------------------------
        # [13] Verify final MP4 with FFprobe
        # -------------------------------------------------------------
        self._notify("[13/13]", "Verifying final MP4 with FFprobe...", status_callback)
        verification = verify_rendered_video(final_video_path, expected_audio_duration=audio_duration)
        if not verification.video_valid:
            err_msg = "\n".join(verification.errors)
            self.console.print(f"[bold red]❌ VIDEO VERIFICATION FAILED:\n{err_msg}[/bold red]")
            raise ValueError(f"Post-render video verification failed:\n{err_msg}")

        self.console.print(
            f"[bold green]✓ Verification Passed: {verification.width}x{verification.height} "
            f"@{verification.fps}fps | Video: {verification.video_duration:.3f}s vs Audio: {verification.audio_duration:.3f}s[/bold green]"
        )
        log_event(f"Video verification passed: {verification.model_dump_json()}")

        # Write render log
        (out_dir / "render_log.txt").write_text("\n".join(render_log_lines), encoding="utf-8")

        self.console.print(f"\n[bold green]FINAL VIDEO:\n{final_video_path.as_posix()}[/bold green]\n")

        return final_video_path
