from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from uuid import uuid4

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_web_candidates_match

from .agent_run_store import build_agent_run_store
from .agent_runtime import FitCheckAgentRuntime
from .schemas import ImageUploadRequest


ROOT = Path(__file__).resolve().parents[2]
HARNESS_DIR = ROOT / "backend_harness"
WEBAPP_DIR = ROOT / "webapp"
# Modern React/Vite UI build output. Preferred when present; falls back to the
# legacy vanilla webapp otherwise. See webapp-next/.
WEBAPP_NEXT_DIST = ROOT / "webapp-next" / "dist"


def active_webapp_dir() -> Path:
    """Serve the modern build if it has been produced, else the legacy webapp."""
    if (WEBAPP_NEXT_DIST / "index.html").exists():
        return WEBAPP_NEXT_DIST
    return WEBAPP_DIR


SUBMISSION_DIR = ROOT / "submission"
OUTPUT_DIR = ROOT / "harness_outputs"
UPLOAD_DIR = OUTPUT_DIR / "uploads"
WEB_RESULT_PATH = OUTPUT_DIR / "web_candidates_match_result.json"
KEYWORD_RESULT_PATH = OUTPUT_DIR / "web_keyword_probe" / "keyword_web_probe_result.json"
AGENT_RUNS_PATH = OUTPUT_DIR / "agent_runs.sqlite3"
MAX_IMAGE_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def production_agent_runtime() -> FitCheckAgentRuntime:
    return FitCheckAgentRuntime(
        run_store_path=AGENT_RUNS_PATH,
        web_result_path=WEB_RESULT_PATH,
        keyword_result_path=KEYWORD_RESULT_PATH,
        refresh_web_candidates=run_web_candidates_match.main,
        max_turns=_int_env("FITCHECK_AGENT_MAX_TURNS", 18),
    )


def agent_run_store():
    return build_agent_run_store(AGENT_RUNS_PATH)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def upload_image_payload(payload: ImageUploadRequest) -> dict:
    media_type = payload.media_type.lower()
    if media_type not in ALLOWED_IMAGE_TYPES:
        return {
            "status": "failed",
            "error": {
                "code": "UNSUPPORTED_IMAGE_TYPE",
                "message": "Only jpeg, png, webp, and gif images are accepted.",
            },
        }

    raw_data = payload.data_url or payload.base64 or ""
    if "," in raw_data:
        raw_data = raw_data.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(raw_data, validate=True)
    except Exception:
        return {
            "status": "failed",
            "error": {
                "code": "INVALID_IMAGE_DATA",
                "message": "Image payload must be base64 encoded.",
            },
        }

    if not image_bytes:
        return {"status": "failed", "error": {"code": "EMPTY_IMAGE", "message": "Image payload is empty."}}
    if len(image_bytes) > MAX_IMAGE_UPLOAD_BYTES:
        return {
            "status": "failed",
            "error": {
                "code": "IMAGE_TOO_LARGE",
                "message": f"Image must be smaller than {MAX_IMAGE_UPLOAD_BYTES} bytes.",
            },
        }

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    original_name = Path(payload.filename or "upload").name
    attachment_id = f"img_{uuid4().hex[:12]}"
    stored_name = f"{attachment_id}{ALLOWED_IMAGE_TYPES[media_type]}"
    (UPLOAD_DIR / stored_name).write_bytes(image_bytes)

    return {
        "status": "completed",
        "attachment": {
            "id": attachment_id,
            "filename": original_name,
            "stored_name": stored_name,
            "media_type": media_type,
            "size_bytes": len(image_bytes),
            "url": f"/outputs/uploads/{stored_name}",
            "role": payload.role,
            "source": "user_upload",
        },
    }
