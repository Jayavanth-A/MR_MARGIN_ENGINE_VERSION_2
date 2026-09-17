import asyncio
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from engine.config import AppConfig
from engine.pipeline import VideoAutomationPipeline
from engine.workspace import WorkspaceManager, WorkspaceError


app = FastAPI(title="MR_MARGIN_ENGINE Video Automation Dashboard", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_PATH = Path(__file__).parent / "frontend" / "index.html"
workspace_manager = WorkspaceManager(base_dir="workspace")


# ---------------------------------------------------------------------------
# Job Management
# ---------------------------------------------------------------------------

class JobState:
    def __init__(self, job_id: str, project_id: Optional[str], output_dir: Path):
        self.job_id = job_id
        self.project_id = project_id
        self.output_dir = output_dir
        self.status = "queued"  # queued, running, completed, failed
        self.current_stage = ""
        self.progress_pct = 0
        self.stages: List[Dict[str, Any]] = []
        self.logs: List[str] = []
        self.error: Optional[str] = None
        self.video_path: Optional[str] = None
        self.created_at = time.time()
        self.completed_at: Optional[float] = None
        self.event_queue: asyncio.Queue = asyncio.Queue()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def push_event(self, event_type: str, data: Dict[str, Any]):
        msg = {"type": event_type, "data": data, "timestamp": time.time()}
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.event_queue.put_nowait, msg)


jobs_db: Dict[str, JobState] = {}


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    project_id: Optional[str] = None
    images_dir: Optional[str] = "sample_data/images"
    prompts_file: Optional[str] = "sample_data/prompts.json"
    voiceover_file: Optional[str] = "sample_data/voiceover.txt"
    audio_file: Optional[str] = "sample_data/audio.wav"
    output_dir: Optional[str] = "output"
    timestamp_text: str
    mock_llm: bool = False
    async_job: bool = False


# ---------------------------------------------------------------------------
# Core Endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def read_root():
    if not FRONTEND_PATH.exists():
        raise HTTPException(status_code=404, detail="Dashboard frontend not found")
    return HTMLResponse(content=FRONTEND_PATH.read_text(encoding="utf-8"))


@app.get("/api/status")
def get_engine_status():
    """Report engine readiness and LLM provider configuration status."""
    api_key = os.getenv("AICREDITS_API_KEY", "")
    has_key = bool(api_key.strip() and api_key != "your_aicredits_api_key_here")
    return {
        "engine": "online",
        "version": "2.0.0",
        "aicredits_configured": has_key,
        "aicredits_model": os.getenv("AICREDITS_MODEL", "deepseek-v3"),
        "active_jobs": len([j for j in jobs_db.values() if j.status == "running"])
    }


# ---------------------------------------------------------------------------
# Workspace Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/project/create")
def create_project(project_id: Optional[str] = None):
    try:
        pid = workspace_manager.create_project(project_id)
        return {"success": True, "project_id": pid}
    except WorkspaceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/project/{project_id}/upload/images")
async def upload_images(project_id: str, files: List[UploadFile] = File(...)):
    try:
        saved_count = 0
        for f in files:
            content = await f.read()
            workspace_manager.save_image(project_id, f.filename, content)
            saved_count += 1
        meta = workspace_manager.get_images_metadata(project_id)
        return {"success": True, "uploaded": saved_count, "metadata": meta}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Image upload failed: {str(e)}")


@app.post("/api/project/{project_id}/upload/prompts")
async def upload_prompts(project_id: str, file: UploadFile = File(...)):
    try:
        content = await file.read()
        workspace_manager.save_prompts(project_id, file.filename, content)
        meta = workspace_manager.get_prompts_metadata(project_id)
        return {"success": True, "metadata": meta}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Prompts upload failed: {str(e)}")


@app.post("/api/project/{project_id}/upload/voiceover")
async def upload_voiceover(project_id: str, file: UploadFile = File(...)):
    try:
        content = await file.read()
        workspace_manager.save_voiceover(project_id, file.filename, content)
        meta = workspace_manager.get_voiceover_metadata(project_id)
        return {"success": True, "metadata": meta}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Voiceover upload failed: {str(e)}")


@app.post("/api/project/{project_id}/upload/audio")
async def upload_audio(project_id: str, file: UploadFile = File(...)):
    try:
        content = await file.read()
        workspace_manager.save_audio(project_id, file.filename, content)
        meta = workspace_manager.get_audio_metadata(project_id)
        return {"success": True, "metadata": meta}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Audio upload failed: {str(e)}")


@app.get("/api/project/{project_id}/readiness")
def get_readiness(project_id: str):
    try:
        readiness = workspace_manager.get_project_readiness(project_id)
        return readiness
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/project/{project_id}/thumbnail/{filename}")
def get_thumbnail(project_id: str, filename: str):
    try:
        path = workspace_manager.get_thumbnail_path(project_id, filename)
        mime, _ = mimetypes.guess_type(str(path))
        return FileResponse(path=str(path), media_type=mime or "image/jpeg")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    except WorkspaceError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Pipeline Execution & Jobs
# ---------------------------------------------------------------------------

def _categorize_error(err_msg: str) -> str:
    prefix = "❌ ERROR: "
    if "Missing image" in err_msg or "INPUT VALIDATION" in err_msg:
        prefix = "❌ IMAGE VALIDATION ERROR:\n"
    elif "LLM" in err_msg or "AI" in err_msg or "normalization" in err_msg:
        prefix = "❌ AI TIMELINE ERROR:\n"
    elif "Overlap" in err_msg or "Gap" in err_msg or "TIMELINE" in err_msg or "Out-of-range" in err_msg:
        prefix = "❌ TIMELINE ERROR:\n"
    elif "audio" in err_msg.lower() or "duration" in err_msg.lower():
        prefix = "❌ AUDIO ALIGNMENT ERROR:\n"
    return f"{prefix}{err_msg}"


def _execute_pipeline_job(job: JobState, req: RunRequest, loop: asyncio.AbstractEventLoop):
    job.loop = loop
    job.status = "running"
    job.push_event("status", {"status": "running", "message": "Pipeline initiated"})

    # Determine paths
    if req.project_id:
        proj_dir = workspace_manager.get_project_dir(req.project_id)
        images_dir = proj_dir / "images"
        output_dir = proj_dir / "output"

        # find prompts
        prompts_candidates = list(proj_dir.glob("prompts.*"))
        prompts_file = prompts_candidates[0] if prompts_candidates else proj_dir / "prompts.json"

        # find voiceover
        vo_file = proj_dir / "voiceover.txt"

        # find audio
        audio_candidates = [
            f for f in proj_dir.iterdir()
            if f.is_file() and f.suffix.lower() in {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}
        ]
        audio_file = audio_candidates[0] if audio_candidates else proj_dir / "audio.wav"
    else:
        images_dir = Path(req.images_dir)
        prompts_file = Path(req.prompts_file)
        vo_file = Path(req.voiceover_file)
        audio_file = Path(req.audio_file)
        output_dir = Path(req.output_dir or "output")

    output_dir.mkdir(parents=True, exist_ok=True)
    job.output_dir = output_dir

    def stage_callback(stage_str: str, message: str):
        job.current_stage = stage_str
        # Calculate progress percent based on stage [X/12]
        m = re.match(r"\[(\d+)/(\d+)\]", stage_str)
        if m:
            cur, tot = int(m.group(1)), int(m.group(2))
            job.progress_pct = int((cur / tot) * 100)

        entry = {"stage": stage_str, "message": message, "pct": job.progress_pct}
        job.stages.append(entry)
        job.logs.append(f"{stage_str} {message}")
        job.push_event("stage", entry)

    try:
        config = AppConfig(output_dir=output_dir)
        pipeline = VideoAutomationPipeline(config=config, use_mock_llm=req.mock_llm)

        final_video = pipeline.run(
            images_dir=images_dir,
            prompts_file=prompts_file,
            voiceover_text_file=vo_file,
            voiceover_audio_file=audio_file,
            timestamp_text=req.timestamp_text,
            output_dir=output_dir,
            generate_preview=False,
            status_callback=stage_callback,
        )

        job.status = "completed"
        job.progress_pct = 100
        job.completed_at = time.time()
        job.video_path = str(final_video)
        job.push_event("completed", {
            "status": "completed",
            "video_path": str(final_video),
            "output_dir": str(output_dir)
        })

    except Exception as e:
        job.status = "failed"
        job.completed_at = time.time()
        cat_error = _categorize_error(str(e))
        job.error = cat_error
        job.logs.append(f"ERROR: {cat_error}")
        job.push_event("error", {"status": "failed", "error": cat_error})


@app.post("/api/run")
async def run_pipeline(req: RunRequest):
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    out_dir = Path(req.output_dir or "output")
    if req.project_id:
        out_dir = workspace_manager.get_project_dir(req.project_id) / "output"

    job = JobState(job_id=job_id, project_id=req.project_id, output_dir=out_dir)
    jobs_db[job_id] = job

    loop = asyncio.get_running_loop()

    if req.async_job:
        # Launch asynchronously in background thread
        thread = threading.Thread(
            target=_execute_pipeline_job,
            args=(job, req, loop),
            daemon=True
        )
        thread.start()
        return {
            "success": True,
            "job_id": job_id,
            "status": "running",
            "message": "Pipeline processing started"
        }
    else:
        # Synchronous execution (for CLI/tests backward compatibility)
        _execute_pipeline_job(job, req, loop)
        if job.status == "completed":
            return {
                "success": True,
                "job_id": job_id,
                "message": "Video successfully generated and verified!",
                "video_path": job.video_path
            }
        else:
            return {
                "success": False,
                "job_id": job_id,
                "error": job.error
            }


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    job = jobs_db.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "job_id": job.job_id,
        "project_id": job.project_id,
        "status": job.status,
        "current_stage": job.current_stage,
        "progress_pct": job.progress_pct,
        "stages": job.stages,
        "logs": job.logs,
        "error": job.error,
        "video_path": job.video_path,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
    }


@app.get("/api/jobs/{job_id}/events")
async def stream_job_events(job_id: str):
    job = jobs_db.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        # First yield current state
        initial_data = json.dumps({
            "type": "init",
            "status": job.status,
            "current_stage": job.current_stage,
            "progress_pct": job.progress_pct,
            "stages": job.stages,
            "logs": job.logs,
            "error": job.error,
            "video_path": job.video_path
        })
        yield f"data: {initial_data}\n\n"

        while True:
            if job.status in ("completed", "failed") and job.event_queue.empty():
                final_msg = json.dumps({
                    "type": "final",
                    "status": job.status,
                    "error": job.error,
                    "video_path": job.video_path
                })
                yield f"data: {final_msg}\n\n"
                break

            try:
                msg = await asyncio.wait_for(job.event_queue.get(), timeout=1.0)
                yield f"data: {json.dumps(msg)}\n\n"
            except asyncio.TimeoutError:
                # Keep-alive ping
                yield ": keepalive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.get("/api/jobs/{job_id}/video")
def get_job_video(job_id: str):
    job = jobs_db.get(job_id)
    if not job or not job.video_path:
        raise HTTPException(status_code=404, detail="Video not found for job")

    v_path = Path(job.video_path)
    if not v_path.exists():
        raise HTTPException(status_code=404, detail="Video file missing from disk")

    return FileResponse(
        path=str(v_path.resolve()),
        media_type="video/mp4",
        filename="final_video.mp4"
    )


@app.get("/api/jobs/{job_id}/file/{filename}")
def get_job_file(job_id: str, filename: str):
    job = jobs_db.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    safe_name = os.path.basename(filename)
    file_path = job.output_dir / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File {safe_name} not found")

    mime, _ = mimetypes.guess_type(str(file_path))
    return FileResponse(
        path=str(file_path.resolve()),
        media_type=mime or "application/octet-stream",
        filename=safe_name
    )


# Legacy endpoints for test and CLI backward-compatibility
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

    mime = "text/plain"
    if safe_name.endswith(".json"):
        mime = "application/json"
    elif safe_name.endswith(".mp4"):
        mime = "video/mp4"

    return FileResponse(
        path=str(file_path.resolve()),
        media_type=mime,
        filename=safe_name
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
