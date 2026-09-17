import os
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from engine.config import AppConfig
from engine.pipeline import VideoAutomationPipeline

app = FastAPI(title="MR_MARGIN_ENGINE Video Automation Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_PATH = Path(__file__).parent / "frontend" / "index.html"


class RunRequest(BaseModel):
    images_dir: str = "sample_data/images"
    prompts_file: str = "sample_data/prompts.json"
    voiceover_file: str = "sample_data/voiceover.txt"
    audio_file: str = "sample_data/audio.wav"
    output_dir: str = "output"
    timestamp_text: str
    mock_llm: bool = False


@app.get("/", response_class=HTMLResponse)
def read_root():
    if not FRONTEND_PATH.exists():
        raise HTTPException(status_code=404, detail="Dashboard frontend not found")
    return HTMLResponse(content=FRONTEND_PATH.read_text(encoding="utf-8"))


@app.post("/api/run")
def run_pipeline(req: RunRequest):
    config = AppConfig(output_dir=Path(req.output_dir))
    pipeline = VideoAutomationPipeline(config=config, use_mock_llm=req.mock_llm)

    try:
        final_video = pipeline.run(
            images_dir=Path(req.images_dir),
            prompts_file=Path(req.prompts_file),
            voiceover_text_file=Path(req.voiceover_file),
            voiceover_audio_file=Path(req.audio_file),
            timestamp_text=req.timestamp_text,
            output_dir=Path(req.output_dir),
            generate_preview=False
        )
        return {
            "success": True,
            "message": "Video successfully generated and verified!",
            "video_path": str(final_video)
        }
    except Exception as e:
        err_msg = str(e)
        # Categorize errors per prompt requirements
        prefix = "❌ ERROR: "
        if "Missing image" in err_msg or "INPUT VALIDATION" in err_msg:
            prefix = "❌ IMAGE VALIDATION ERROR:\n"
        elif "LLM" in err_msg or "AI" in err_msg or "normalization" in err_msg:
            prefix = "❌ AI TIMELINE ERROR:\n"
        elif "Overlap" in err_msg or "Gap" in err_msg or "TIMELINE" in err_msg:
            prefix = "❌ TIMELINE ERROR:\n"
        elif "audio" in err_msg.lower() or "duration" in err_msg.lower():
            prefix = "❌ AUDIO ALIGNMENT ERROR:\n"

        return {
            "success": False,
            "error": f"{prefix}{err_msg}"
        }


@app.get("/api/video")
def get_video(output_dir: str = "output"):
    video_path = Path(output_dir) / "final_video.mp4"
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(
        path=str(video_path.resolve()),
        media_type="video/mp4",
        filename="final_video.mp4"
    )


@app.get("/api/file/{filename}")
def get_output_file(filename: str, output_dir: str = "output"):
    safe_name = os.path.basename(filename)
    file_path = Path(output_dir) / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File {safe_name} not found")
    
    # Determine media type
    media_type = "text/plain"
    if safe_name.endswith(".json"):
        media_type = "application/json"
    elif safe_name.endswith(".mp4"):
        media_type = "video/mp4"

    return FileResponse(
        path=str(file_path.resolve()),
        media_type=media_type,
        filename=safe_name
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
