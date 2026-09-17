import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from engine.audio import format_timestamp, get_audio_duration
from engine.validator import extract_numeric_index, parse_prompts_file


ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_PROMPT_EXTENSIONS = {".json", ".jsonl"}
ALLOWED_VOICEOVER_EXTENSIONS = {".txt"}
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}


class WorkspaceError(Exception):
    """Raised when workspace operations encounter security or validation errors."""
    pass


class WorkspaceManager:
    """Manages project workspaces, secure file uploads, and asset metadata."""

    def __init__(self, base_dir: str = "workspace"):
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """Sanitize filename to prevent directory traversal and invalid characters.
        
        Preserves only alphanumerics, underscores, hyphens, and dots.
        """
        base = Path(filename).name
        # Remove any path traversal components
        clean = re.sub(r"[^a-zA-Z0-9_.-]", "_", base)
        # Collapse repeated underscores
        clean = re.sub(r"_+", "_", clean)
        # Reject filenames that are solely dots or empty
        stripped = clean.strip("._")
        if not stripped:
            clean = f"file_{uuid.uuid4().hex[:8]}"
        return clean

    def _validate_project_id(self, project_id: str) -> str:
        if not project_id or not re.match(r"^[a-zA-Z0-9_-]+$", project_id):
            raise WorkspaceError(f"Invalid project_id: {project_id}")
        return project_id

    def create_project(self, project_id: Optional[str] = None) -> str:
        """Create a new isolated project workspace."""
        if not project_id:
            project_id = f"proj_{uuid.uuid4().hex[:8]}"
        self._validate_project_id(project_id)

        proj_dir = (self.base_dir / project_id).resolve()
        if not str(proj_dir).startswith(str(self.base_dir)):
            raise WorkspaceError("Project directory path traversal detected.")

        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "images").mkdir(parents=True, exist_ok=True)
        (proj_dir / "output").mkdir(parents=True, exist_ok=True)
        return project_id

    def get_project_dir(self, project_id: str) -> Path:
        """Get absolute path to a project directory."""
        self._validate_project_id(project_id)
        proj_dir = (self.base_dir / project_id).resolve()
        if not str(proj_dir).startswith(str(self.base_dir)):
            raise WorkspaceError("Invalid project path.")
        if not proj_dir.exists():
            raise FileNotFoundError(f"Project workspace '{project_id}' does not exist.")
        return proj_dir

    def save_file(
        self,
        project_id: str,
        filename: str,
        content: bytes,
        subfolder: str = "",
        allowed_extensions: Optional[Set[str]] = None,
        target_name: Optional[str] = None,
    ) -> Path:
        """Securely save an uploaded file inside the project workspace."""
        proj_dir = self.get_project_dir(project_id)
        sanitized = self.sanitize_filename(filename)
        ext = Path(sanitized).suffix.lower()

        if allowed_extensions and ext not in allowed_extensions:
            raise WorkspaceError(
                f"File extension '{ext}' not allowed. Allowed: {', '.join(sorted(allowed_extensions))}"
            )

        if subfolder:
            dest_dir = (proj_dir / subfolder).resolve()
            if not str(dest_dir).startswith(str(proj_dir)):
                raise WorkspaceError("Subfolder path traversal detected.")
            dest_dir.mkdir(parents=True, exist_ok=True)
        else:
            dest_dir = proj_dir

        final_filename = target_name if target_name else sanitized
        target_path = (dest_dir / final_filename).resolve()
        if not str(target_path).startswith(str(proj_dir)):
            raise WorkspaceError("Target file path traversal detected.")

        target_path.write_bytes(content)
        return target_path

    def save_image(self, project_id: str, filename: str, content: bytes) -> Path:
        """Save an image file into project's images directory."""
        return self.save_file(
            project_id=project_id,
            filename=filename,
            content=content,
            subfolder="images",
            allowed_extensions=ALLOWED_IMAGE_EXTENSIONS,
        )

    def save_prompts(self, project_id: str, filename: str, content: bytes) -> Path:
        """Save prompts file into project root."""
        ext = Path(filename).suffix.lower()
        target_name = f"prompts{ext}" if ext in ALLOWED_PROMPT_EXTENSIONS else "prompts.json"
        return self.save_file(
            project_id=project_id,
            filename=filename,
            content=content,
            allowed_extensions=ALLOWED_PROMPT_EXTENSIONS,
            target_name=target_name,
        )

    def save_voiceover(self, project_id: str, filename: str, content: bytes) -> Path:
        """Save voiceover text file into project root."""
        return self.save_file(
            project_id=project_id,
            filename=filename,
            content=content,
            allowed_extensions=ALLOWED_VOICEOVER_EXTENSIONS,
            target_name="voiceover.txt",
        )

    def save_audio(self, project_id: str, filename: str, content: bytes) -> Path:
        """Save voiceover audio file into project root."""
        ext = Path(filename).suffix.lower()
        target_name = f"audio{ext}" if ext in ALLOWED_AUDIO_EXTENSIONS else "audio.wav"
        return self.save_file(
            project_id=project_id,
            filename=filename,
            content=content,
            allowed_extensions=ALLOWED_AUDIO_EXTENSIONS,
            target_name=target_name,
        )

    def get_thumbnail_path(self, project_id: str, filename: str) -> Path:
        """Return path to an image file for thumbnail preview with traversal check."""
        if "/" in filename or "\\" in filename or ".." in filename:
            raise WorkspaceError("Path traversal not allowed in thumbnail request.")
        proj_dir = self.get_project_dir(project_id)
        sanitized = self.sanitize_filename(filename)
        img_path = (proj_dir / "images" / sanitized).resolve()
        if not str(img_path).startswith(str((proj_dir / "images").resolve())):
            raise WorkspaceError("Path traversal in thumbnail request.")
        if not img_path.exists() or not img_path.is_file():
            raise FileNotFoundError(f"Image not found: {filename}")
        return img_path

    def get_images_metadata(self, project_id: str) -> Dict[str, Any]:
        """Inspect images in project workspace and return numeric sequence details."""
        proj_dir = self.get_project_dir(project_id)
        images_dir = proj_dir / "images"
        if not images_dir.exists():
            return {
                "count": 0,
                "is_valid": False,
                "min_index": None,
                "max_index": None,
                "missing_indices": [],
                "duplicate_indices": [],
                "invalid_files": [],
                "images": [],
            }

        files = [
            f for f in images_dir.iterdir()
            if f.is_file() and f.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS
        ]

        indexed_files = []
        invalid_files = []
        index_counts: Dict[int, int] = {}

        for f in files:
            idx = extract_numeric_index(f.name)
            if idx is not None:
                indexed_files.append((idx, f))
                index_counts[idx] = index_counts.get(idx, 0) + 1
            else:
                invalid_files.append(f.name)

        indexed_files.sort(key=lambda x: (x[0], x[1].name))

        count = len(indexed_files)
        duplicates = sorted([idx for idx, cnt in index_counts.items() if cnt > 1])
        min_idx = indexed_files[0][0] if indexed_files else None
        max_idx = indexed_files[-1][0] if indexed_files else None

        missing = []
        if count > 0 and min_idx == 1 and max_idx is not None:
            expected = set(range(1, count + 1))
            actual = set(index_counts.keys())
            missing = sorted(list(expected - actual))

        is_valid = (
            count > 0
            and len(invalid_files) == 0
            and len(duplicates) == 0
            and len(missing) == 0
            and min_idx == 1
            and max_idx == count
        )

        image_list = [
            {
                "filename": f.name,
                "index": idx,
                "size_bytes": f.stat().st_size,
                "thumbnail_url": f"/api/project/{project_id}/thumbnail/{f.name}",
            }
            for idx, f in indexed_files
        ]

        return {
            "count": count,
            "is_valid": is_valid,
            "min_index": min_idx,
            "max_index": max_idx,
            "missing_indices": missing,
            "duplicate_indices": duplicates,
            "invalid_files": invalid_files,
            "images": image_list,
        }

    def get_prompts_metadata(self, project_id: str) -> Dict[str, Any]:
        """Inspect prompts in project workspace."""
        proj_dir = self.get_project_dir(project_id)
        candidates = list(proj_dir.glob("prompts.*"))
        if not candidates:
            # Check any json/jsonl in root
            candidates = [
                f for f in proj_dir.iterdir()
                if f.is_file() and f.suffix.lower() in ALLOWED_PROMPT_EXTENSIONS
            ]

        if not candidates:
            return {"exists": False, "count": 0, "filename": None, "error": "No prompts file found."}

        p_file = candidates[0]
        try:
            prompts_map = parse_prompts_file(p_file)
            return {
                "exists": True,
                "filename": p_file.name,
                "count": len(prompts_map),
                "indices": sorted(list(prompts_map.keys())),
                "is_valid": len(prompts_map) > 0,
            }
        except Exception as e:
            return {
                "exists": True,
                "filename": p_file.name,
                "count": 0,
                "error": str(e),
                "is_valid": False,
            }

    def get_voiceover_metadata(self, project_id: str) -> Dict[str, Any]:
        """Inspect voiceover text in project workspace."""
        proj_dir = self.get_project_dir(project_id)
        vo_path = proj_dir / "voiceover.txt"
        if not vo_path.exists():
            candidates = [
                f for f in proj_dir.iterdir()
                if f.is_file()
                and f.suffix.lower() == ".txt"
                and f.name not in ("timestamps.txt", "source_timestamp_text.txt")
            ]
            if candidates:
                vo_path = candidates[0]
            else:
                return {"exists": False, "error": "No voiceover text file found."}

        try:
            text = vo_path.read_text(encoding="utf-8")
            clean_text = text.strip()
            return {
                "exists": True,
                "filename": vo_path.name,
                "char_count": len(clean_text),
                "word_count": len(clean_text.split()),
                "line_count": len(text.splitlines()),
                "is_empty": len(clean_text) == 0,
                "preview": clean_text[:200] + ("..." if len(clean_text) > 200 else ""),
            }
        except Exception as e:
            return {"exists": True, "filename": vo_path.name, "error": str(e), "is_empty": True}

    def get_audio_metadata(self, project_id: str) -> Dict[str, Any]:
        """Inspect audio file in project workspace using FFprobe."""
        proj_dir = self.get_project_dir(project_id)
        audio_candidates = [
            f for f in proj_dir.iterdir()
            if f.is_file() and f.suffix.lower() in ALLOWED_AUDIO_EXTENSIONS
        ]
        if not audio_candidates:
            return {"exists": False, "error": "No audio file found."}

        audio_file = audio_candidates[0]
        try:
            dur = get_audio_duration(audio_file)
            return {
                "exists": True,
                "filename": audio_file.name,
                "duration": round(dur, 3),
                "formatted_duration": format_timestamp(dur),
                "size_bytes": audio_file.stat().st_size,
            }
        except Exception as e:
            return {
                "exists": True,
                "filename": audio_file.name,
                "error": str(e),
                "duration": 0.0,
            }

    def get_project_readiness(self, project_id: str) -> Dict[str, Any]:
        """Compile comprehensive pre-flight checklist for the project."""
        img_meta = self.get_images_metadata(project_id)
        prompt_meta = self.get_prompts_metadata(project_id)
        vo_meta = self.get_voiceover_metadata(project_id)
        audio_meta = self.get_audio_metadata(project_id)

        errors: List[str] = []
        warnings: List[str] = []

        # 1. Images
        images_ok = False
        if img_meta["count"] == 0:
            errors.append("No images uploaded.")
        elif not img_meta["is_valid"]:
            if img_meta["duplicate_indices"]:
                errors.append(f"Duplicate image indices: {img_meta['duplicate_indices']}")
            if img_meta["missing_indices"]:
                errors.append(f"Missing image indices: {img_meta['missing_indices']}")
            if img_meta["invalid_files"]:
                errors.append(f"Non-numeric image filenames: {img_meta['invalid_files']}")
        else:
            images_ok = True

        # 2. Prompts
        prompts_ok = False
        if not prompt_meta["exists"]:
            errors.append("Prompts file missing.")
        elif not prompt_meta.get("is_valid", False):
            errors.append(f"Invalid prompts file: {prompt_meta.get('error', 'empty or unparseable')}")
        elif img_meta["count"] > 0 and prompt_meta["count"] != img_meta["count"]:
            warnings.append(
                f"Prompt count ({prompt_meta['count']}) differs from image count ({img_meta['count']})."
            )
            prompts_ok = True
        else:
            prompts_ok = True

        # 3. Voiceover
        vo_ok = False
        if not vo_meta["exists"]:
            errors.append("Voiceover text missing.")
        elif vo_meta.get("is_empty", True):
            errors.append("Voiceover text is empty.")
        else:
            vo_ok = True

        # 4. Audio
        audio_ok = False
        if not audio_meta["exists"]:
            errors.append("Voiceover audio file missing.")
        elif "error" in audio_meta:
            errors.append(f"Audio inspection failed: {audio_meta['error']}")
        elif audio_meta.get("duration", 0.0) <= 0:
            errors.append("Audio duration is non-positive.")
        else:
            audio_ok = True

        ready = images_ok and prompts_ok and vo_ok and audio_ok and len(errors) == 0

        return {
            "project_id": project_id,
            "ready": ready,
            "checklist": {
                "images": {
                    "status": "ok" if images_ok else "error",
                    "count": img_meta["count"],
                    "sequence": f"1 → {img_meta['count']}" if images_ok else "invalid",
                    "details": img_meta,
                },
                "prompts": {
                    "status": "ok" if prompts_ok else "error",
                    "count": prompt_meta.get("count", 0),
                    "filename": prompt_meta.get("filename"),
                    "details": prompt_meta,
                },
                "voiceover": {
                    "status": "ok" if vo_ok else "error",
                    "words": vo_meta.get("word_count", 0),
                    "chars": vo_meta.get("char_count", 0),
                    "details": vo_meta,
                },
                "audio": {
                    "status": "ok" if audio_ok else "error",
                    "duration": audio_meta.get("duration", 0.0),
                    "formatted": audio_meta.get("formatted_duration", "00:00.000"),
                    "details": audio_meta,
                },
            },
            "errors": errors,
            "warnings": warnings,
        }
