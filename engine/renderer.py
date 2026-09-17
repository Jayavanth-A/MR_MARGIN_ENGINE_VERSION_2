import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Optional

from engine.config import AppConfig
from engine.models import Timeline

logger = logging.getLogger(__name__)


class RenderError(Exception):
    """Raised when video rendering fails."""
    pass


class VideoRenderer:
    """FFmpeg-based video renderer using dynamic concat demuxer."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.ffmpeg_cmd = shutil.which("ffmpeg")
        if not self.ffmpeg_cmd:
            raise RenderError("ffmpeg executable not found in system PATH.")

    def generate_concat_script(
        self,
        timeline: Timeline,
        images_dir: Path,
        script_path: Path
    ) -> Path:
        """Generate an FFmpeg concat demuxer script file with dynamic image durations."""
        script_path = Path(script_path)
        script_path.parent.mkdir(parents=True, exist_ok=True)

        lines = ["ffconcat version 1.0"]

        entries = timeline.timeline
        for i, entry in enumerate(entries):
            img_path = Path(images_dir) / entry.filename
            # Convert Windows backslashes to forward slashes for FFmpeg concat syntax
            posix_path = str(img_path.resolve()).replace("\\", "/")
            lines.append(f"file '{posix_path}'")
            lines.append(f"duration {entry.duration:.4f}")

        # FFmpeg concat quirk: repeat the final file to ensure last frame displays for its full duration
        if entries:
            last_path = Path(images_dir) / entries[-1].filename
            posix_last = str(last_path.resolve()).replace("\\", "/")
            lines.append(f"file '{posix_last}'")

        script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return script_path

    def render(
        self,
        timeline: Timeline,
        images_dir: Path,
        audio_path: Path,
        output_path: Path,
        is_preview: bool = False,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> Path:
        """Render synchronized MP4 video using FFmpeg concat demuxer."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Generate temporary concat script
        concat_script = output_path.parent / "concat_script.txt"
        self.generate_concat_script(timeline, images_dir, concat_script)

        # Configure video filters for scaling and aspect ratio handling
        width = 1280 if is_preview else self.config.video_width
        height = 720 if is_preview else self.config.video_height
        fps = self.config.video_fps
        preset = "ultrafast" if is_preview else self.config.video_preset
        crf = 24 if is_preview else self.config.video_crf

        if self.config.video_scale_mode == "crop":
            # Scale to fill and crop excess
            vf = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},"
                f"format=yuv420p"
            )
        else:
            # Scale with letterbox / pillarbox padding (default)
            vf = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"format=yuv420p"
            )

        cmd = [
            self.ffmpeg_cmd,
            "-y",  # Overwrite output
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_script.resolve()),
            "-i", str(Path(audio_path).resolve()),
            "-vf", vf,
            "-r", str(fps),
            "-c:v", "libx264",
            "-preset", preset,
            "-crf", str(crf),
            "-c:a", "aac",
            "-b:a", self.config.audio_bitrate,
            "-pix_fmt", "yuv420p",
            "-shortest",
            "-t", f"{timeline.audio_duration:.3f}",
            "-movflags", "+faststart",
            str(output_path.resolve())
        ]

        logger.info(f"Rendering video to {output_path} ({width}x{height} @ {fps}fps)...")
        if progress_callback:
            progress_callback(f"Executing FFmpeg render -> {output_path.name}")

        try:
            process = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr[-1000:] if e.stderr else str(e)
            raise RenderError(f"FFmpeg rendering failed with exit code {e.returncode}:\n{err_msg}")

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RenderError(f"FFmpeg exited without generating output file: {output_path}")

        return output_path
