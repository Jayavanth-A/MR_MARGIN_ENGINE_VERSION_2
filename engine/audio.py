import json
import shutil
import subprocess
from pathlib import Path
from typing import Tuple


class AudioError(Exception):
    """Raised when audio inspection or processing fails."""
    pass


def format_timestamp(seconds: float) -> str:
    """Format seconds into HH:MM:SS.mmm or MM:SS.mmm."""
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"
    return f"{minutes:02d}:{secs:06.3f}"


def get_audio_duration(audio_path: Path) -> float:
    """Extract exact audio duration in seconds using ffprobe.

    Raises:
        FileNotFoundError: If audio file does not exist.
        AudioError: If ffprobe fails, finds no audio streams, or duration cannot be read.
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    ffprobe_cmd = shutil.which("ffprobe")
    if not ffprobe_cmd:
        raise AudioError("ffprobe is not installed or not found in PATH.")

    # Try 1: Format duration with JSON output
    cmd = [
        ffprobe_cmd,
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "format=duration:stream=duration",
        "-of", "json",
        str(audio_path.resolve())
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        data = json.loads(result.stdout)
        
        # Check stream duration first
        streams = data.get("streams", [])
        if streams and "duration" in streams[0] and streams[0]["duration"] is not None:
            try:
                dur = float(streams[0]["duration"])
                if dur > 0:
                    return dur
            except (ValueError, TypeError):
                pass

        # Check format duration
        format_info = data.get("format", {})
        if "duration" in format_info and format_info["duration"] is not None:
            try:
                dur = float(format_info["duration"])
                if dur > 0:
                    return dur
            except (ValueError, TypeError):
                pass
                
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        pass

    # Try 2: Plain text fallback
    fallback_cmd = [
        ffprobe_cmd,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path.resolve())
    ]

    try:
        res = subprocess.run(
            fallback_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        val = res.stdout.strip()
        dur = float(val)
        if dur > 0:
            return dur
    except Exception as e:
        raise AudioError(f"Failed to probe audio duration for {audio_path}: {e}")

    raise AudioError(f"Could not determine positive audio duration for {audio_path}")


def verify_rendered_video(
    video_path: Path,
    expected_audio_duration: float,
    tolerance: float = 0.5
) -> "VerificationReport":
    """Verify final rendered video file using ffprobe.
    
    Checks:
    - File exists and is non-empty
    - Video stream presence, codec, resolution, and fps
    - Audio stream presence and codec
    - Video duration matches audio duration within tolerance
    """
    from engine.models import VerificationReport

    video_path = Path(video_path)
    errors = []

    if not video_path.exists() or video_path.stat().st_size == 0:
        return VerificationReport(
            video_valid=False,
            video_path=str(video_path),
            video_duration=0.0,
            audio_duration=expected_audio_duration,
            duration_difference=expected_audio_duration,
            width=0,
            height=0,
            fps=0.0,
            video_codec="none",
            audio_codec="none",
            errors=["Video file does not exist or is empty"]
        )

    ffprobe_cmd = shutil.which("ffprobe")
    if not ffprobe_cmd:
        raise AudioError("ffprobe executable not found in system PATH.")

    cmd = [
        ffprobe_cmd,
        "-v", "error",
        "-show_entries", "stream=index,codec_type,codec_name,width,height,r_frame_rate,duration:format=duration",
        "-of", "json",
        str(video_path.resolve())
    ]

    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(res.stdout)
    except Exception as e:
        return VerificationReport(
            video_valid=False,
            video_path=str(video_path),
            video_duration=0.0,
            audio_duration=expected_audio_duration,
            duration_difference=expected_audio_duration,
            width=0,
            height=0,
            fps=0.0,
            video_codec="unknown",
            audio_codec="unknown",
            errors=[f"FFprobe execution failed: {e}"]
        )

    video_stream = None
    audio_stream = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not video_stream:
            video_stream = s
        elif s.get("codec_type") == "audio" and not audio_stream:
            audio_stream = s

    if not video_stream:
        errors.append("No video stream found in rendered file.")
    if not audio_stream:
        errors.append("No audio stream found in rendered file.")

    format_dur = float(data.get("format", {}).get("duration", 0.0))
    video_dur = float(video_stream.get("duration", format_dur)) if video_stream else format_dur
    if video_dur == 0.0:
        video_dur = format_dur

    width = int(video_stream.get("width", 0)) if video_stream else 0
    height = int(video_stream.get("height", 0)) if video_stream else 0
    video_codec = video_stream.get("codec_name", "unknown") if video_stream else "none"
    audio_codec = audio_stream.get("codec_name", "unknown") if audio_stream else "none"

    fps = 0.0
    if video_stream and "r_frame_rate" in video_stream:
        try:
            num, den = video_stream["r_frame_rate"].split("/")
            fps = float(num) / float(den) if float(den) > 0 else 0.0
        except Exception:
            fps = 0.0

    diff = abs(video_dur - expected_audio_duration)
    if diff > tolerance:
        errors.append(
            f"Video duration ({video_dur:.3f}s) differs from audio duration "
            f"({expected_audio_duration:.3f}s) by {diff:.3f}s (tolerance: {tolerance:.3f}s)."
        )

    is_valid = len(errors) == 0

    return VerificationReport(
        video_valid=is_valid,
        video_path=str(video_path),
        video_duration=round(video_dur, 3),
        audio_duration=round(expected_audio_duration, 3),
        duration_difference=round(diff, 3),
        width=width,
        height=height,
        fps=round(fps, 2),
        video_codec=video_codec,
        audio_codec=audio_codec,
        errors=errors
    )
