import json
from pathlib import Path
import subprocess


def create_sample(target_dir: Path):
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    images_dir = target_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    samples = [
        (1, "1_exterior-neighborhood-laundromat-sunrise.jpg", "Cinematic exterior of a busy neighborhood laundromat at sunrise with golden light reflecting on windows.", "orange"),
        (2, "2_interior-modern-laundromat-machines.jpg", "Wide interior of a modern laundromat filled with stainless steel commercial washers and dryers.", "blue"),
        (3, "3_close-up-washing-machine-spinning-clothes.jpg", "Close-up macro shot of vibrant clothes spinning rapidly behind sudsy glass door.", "cyan"),
        (4, "4_customer-folding-clean-laundry-counter.jpg", "Warm atmospheric shot of customer folding fresh laundry at clean wooden folding table.", "magenta"),
        (5, "5_worker-closing-down-laundromat-at-dusk.jpg", "Atmospheric wide shot of laundromat attendant locking glass front door as evening streetlights turn on.", "navy"),
    ]

    prompts = {}
    for idx, filename, prompt, color in samples:
        img_path = images_dir / filename
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c={color}:s=1280x720:d=0.1",
            "-frames:v", "1",
            str(img_path)
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        prompts[idx] = prompt

    (target_dir / "prompts.json").write_text(json.dumps(prompts, indent=2), encoding="utf-8")

    voiceover_text = (
        "Every morning, as dawn breaks over the city, the neighborhood laundromat hums to life.\n\n"
        "Rows of heavy-duty industrial washers begin their continuous, rhythmic spin, washing away the week's fatigue.\n\n"
        "Inside, the gentle tumble of clothes and the scent of warm lavender detergent create an unexpected sanctuary of calm.\n\n"
        "Neighbors chat and fold their freshly dried garments together in quiet community.\n\n"
        "As the sun sets and the streetlights flicker on, the attendants turn the keys, closing another day of quiet comfort."
    )
    (target_dir / "voiceover.txt").write_text(voiceover_text, encoding="utf-8")

    # Generate audio (12 seconds)
    audio_path = target_dir / "audio.wav"
    audio_cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "sine=frequency=320:duration=12.0",
        "-c:a", "pcm_s16le",
        str(audio_path)
    ]
    subprocess.run(audio_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    print(f"[SUCCESS] Sample project created successfully in {target_dir}")


if __name__ == "__main__":
    create_sample(Path("sample_data"))
