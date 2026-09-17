import argparse
import sys
from pathlib import Path
from dotenv import load_dotenv
from rich.console import Console

from engine.config import AppConfig
from engine.pipeline import VideoAutomationPipeline

# Reconfigure stdout/stderr to UTF-8 on Windows to prevent charmap UnicodeEncodeErrors
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console()


def main():
    # Load .env file
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="AI-Driven Sequential Image-to-Voiceover Video Automation Pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--images-dir", "-i",
        type=Path,
        default=Path("images"),
        help="Path to folder containing ordered images (e.g. 1_exterior.jpg, 2_interior.jpg)"
    )
    parser.add_argument(
        "--prompts-file", "-p",
        type=Path,
        default=Path("prompts.json"),
        help="Path to JSON file with image prompts"
    )
    parser.add_argument(
        "--voiceover-file", "-v",
        type=Path,
        default=Path("voiceover.txt"),
        help="Path to plain text voiceover file (with paragraphs)"
    )
    parser.add_argument(
        "--audio-file", "-a",
        type=Path,
        default=Path("audio.mp3"),
        help="Path to voiceover audio file (.mp3, .wav, .m4a, etc.)"
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=Path("output"),
        help="Output directory for final video and metadata reports"
    )
    parser.add_argument(
        "--timestamp-file", "-t",
        type=Path,
        default=None,
        help="Path to text file containing user timestamp instructions"
    )
    parser.add_argument(
        "--timestamp-text",
        type=str,
        default=None,
        help="Direct inline string containing user timestamp instructions"
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Launch the interactive Web Dashboard server"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the interactive Web Dashboard server (default: 8000)"
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Also generate a low-resolution fast preview video (output/preview.mp4)"
    )
    parser.add_argument(
        "--mock-llm",
        action="store_true",
        help="Use built-in mock LLM for local offline verification without API credits"
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Override AICredits.in API key (overrides AICREDITS_API_KEY in .env)"
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override AICredits model name (overrides AICREDITS_MODEL in .env)"
    )

    args = parser.parse_args()

    # Build config
    config = AppConfig()
    if args.api_key:
        config.aicredits_api_key = args.api_key
    if args.model:
        config.aicredits_model = args.model
    if args.output_dir:
        config.output_dir = args.output_dir

    console.print("[bold yellow]====================================================================[/bold yellow]")
    console.print("[bold yellow]  MR_MARGIN_ENGINE: Sequential Image-to-Voiceover Video Automation  [/bold yellow]")
    console.print("[bold yellow]====================================================================[/bold yellow]")

    if args.serve:
        console.print(f"[bold green]Starting Web Dashboard at http://127.0.0.1:{args.port}...[/bold green]")
        import uvicorn
        from server import app
        uvicorn.run(app, host="127.0.0.1", port=args.port)
        return

    # Check existence of essential inputs before launching
    missing_inputs = []
    if not args.images_dir.exists():
        missing_inputs.append(f"Images folder not found: {args.images_dir}")
    if not args.prompts_file.exists():
        missing_inputs.append(f"Prompts file not found: {args.prompts_file}")
    if not args.voiceover_file.exists():
        missing_inputs.append(f"Voiceover text file not found: {args.voiceover_file}")
    if not args.audio_file.exists():
        missing_inputs.append(f"Audio file not found: {args.audio_file}")

    if missing_inputs:
        console.print("[bold red]Missing Required Inputs:[/bold red]")
        for m in missing_inputs:
            console.print(f"  ❌ {m}")
        console.print("\nUsage example:")
        console.print("  python main.py --images-dir sample_data/images --prompts-file sample_data/prompts.json --voiceover-file sample_data/voiceover.txt --audio-file sample_data/audio.wav --timestamp-file sample_data/timestamps.txt\n")
        console.print("Or launch the interactive web dashboard:")
        console.print("  python main.py --serve\n")
        sys.exit(1)

    try:
        pipeline = VideoAutomationPipeline(
            config=config,
            use_mock_llm=args.mock_llm,
            console=console
        )

        pipeline.run(
            images_dir=args.images_dir,
            prompts_file=args.prompts_file,
            voiceover_text_file=args.voiceover_file,
            voiceover_audio_file=args.audio_file,
            timestamp_text=args.timestamp_text,
            timestamp_file=args.timestamp_file,
            output_dir=args.output_dir,
            generate_preview=args.preview
        )

    except Exception as e:
        console.print(f"\n[bold red]Pipeline halted with error:[/bold red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
