# MR_MARGIN_ENGINE: Timestamp-Normalization Video Automation Pipeline

A production-ready pipeline that converts user-provided timestamp instructions, an ordered folder of images ($1 \to N$), image prompts, voiceover text, and voiceover audio into a synchronized final MP4 video.

---

## 🌟 Overview & Key Concepts

Rather than asking the AI to invent editorial timing from scratch, the system acts as a **deterministic timestamp normalization and video rendering engine**:

```text
USER PASTES TIMESTAMP TEXT
        ↓
USER PRESSES SEND ARROW ➤
        ↓
ENTIRE TEXT IS SENT TO AICREDITS LLM
        ↓
LLM NORMALIZES / PARSES THE TEXT
        ↓
STRICT STRUCTURED TIMELINE JSON
        ↓
PYTHON DETERMINISTIC VALIDATION
        ↓
VALIDATE AGAINST IMAGES, PROMPTS, AUDIO
        ↓
TIMELINE RECONCILIATION
        ↓
FFmpeg RENDERING
        ↓
FINAL MP4 POST-RENDER VERIFICATION
```

### Non-Negotiable Core Rules:
1. **Rule 1 — Immutable Image Sequence**: The final video strictly preserves the numeric index sequence: `1 → 2 → 3 → ... → N`. Images are never reordered.
2. **Rule 2 — 100% Mandatory Image Coverage**: Every valid image in the folder appears exactly once in the video.
3. **Rule 3 — Exact User Text Preservation**: The raw user timestamp text is saved untouched to `output/source_timestamp_text.txt`.
4. **Rule 4 — Continuous Sequence**: No gaps, no skips, no overlaps (`start[0] = 0.000` and `end[N-1] = audio_duration`).
5. **Rule 5 — Dynamic Timing**: Adheres to the user's intended timing instructions while normalizing any format to decimal seconds.
6. **Rule 6 — FFprobe Post-Render Verification**: Verifies audio and video streams, codecs, resolution, fps, and that `abs(video_duration - audio_duration) <= tolerance`.

---

## 🖥️ Interactive Web Dashboard

Launch the web dashboard with a single command:

```bash
python main.py --serve
# Or: python server.py
```

Open your browser at **`http://127.0.0.1:8000`**.

### Dashboard Highlights:
- **Timestamp Text Area**: Large multi-line text field accepting arbitrary human timestamp formats.
- **Corner Send Arrow (`➤`)**: Prominent action button in the bottom-right corner of the text area.
- **Live Pipeline Tracker**: Real-time visual progress steps through all stages.
- **Video Preview Player**: Embedded MP4 player automatically playing the rendered video.
- **Direct File Downloads**: Instant inspection of `source_timestamp_text.txt`, `llm_timeline.json`, and `final_timeline.json`.
- **Preloaded Presets**: Quick buttons to load standard `MM:SS`, natural language, or arrow formats.

---

## 💻 CLI Usage

You can also run the pipeline headlessly via CLI:

```bash
python main.py \
  --images-dir sample_data/images \
  --prompts-file sample_data/prompts.json \
  --voiceover-file sample_data/voiceover.txt \
  --audio-file sample_data/audio.wav \
  --timestamp-file sample_data/timestamps.txt \
  --output-dir output
```

### Offline / Free Test Mode (Mock LLM)
```bash
python main.py \
  --images-dir sample_data/images \
  --prompts-file sample_data/prompts.json \
  --voiceover-file sample_data/voiceover.txt \
  --audio-file sample_data/audio.wav \
  --timestamp-file sample_data/timestamps.txt \
  --mock-llm \
  --preview
```

---

## 📂 Three-Tier Timeline Outputs

The pipeline preserves three distinct states of the timeline for total auditability:

```text
output/
├── source_timestamp_text.txt   # [State 1] Exactly what the user pasted
├── llm_timeline.json           # [State 2] Structured LLM interpretation of timestamps
├── final_timeline.json         # [State 3] Reconciled executable timeline passed to FFmpeg
├── validation_report.json      # Pre-flight input verification diagnostics
├── timing_report.json          # Boundary and pacing metadata
├── render_log.txt              # FFmpeg execution and verification log
├── final_video.mp4             # 1080p H.264 / AAC master video
└── preview.mp4                 # (Optional) 720p fast-render preview
```

---

## ⏱️ Supported Timestamp Formats

The normalization layer supports diverse formats:

- **Standard `MM:SS`**:
  ```text
  Image 1: 00:00 - 00:02.5
  Image 2: 00:02.5 - 00:05.0
  ```
- **Arrow Format**:
  ```text
  1 → 00:00 to 00:04
  2 → 00:04 to 00:07
  ```
- **Natural Language**:
  ```text
  Image 1 should appear from 0 to 4 seconds.
  Image 2 should appear from 4 to 7 seconds.
  ```
- **Hours & Milliseconds**:
  ```text
  Image 1: 01:00:00.500 - 01:00:04.200
  ```

---

## 🧪 Running Automated Tests

Run the full pytest suite (28 automated tests):

```bash
python -m pytest tests/ -v
```

Test coverage includes:
- Timestamp normalizer (natural language, MM:SS, HH:MM:SS, decimal seconds).

## Want to run locally?

python main.py --serve --port 8000   (MAKE SURE NO OTHER RUNNING ON THIS PORT)
- User input preservation (`source_timestamp_text.txt`).
- Input and sequence validation (missing images, missing prompts, sequence gaps, duplicates).
- Timeline reconciliation (boundary snapping, zero gaps/overlaps, exact audio duration).
- Concat script generation & FFmpeg rendering.
- Post-render verification using FFprobe.
- FastAPI server and web dashboard endpoints.
