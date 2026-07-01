from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..server_context import SUBMISSION_DIR, WEBAPP_DIR, active_webapp_dir


router = APIRouter()


@router.get("/")
def index() -> FileResponse:
    # Prefer the modern React/Vite build when it has been produced.
    return FileResponse(active_webapp_dir() / "index.html")


@router.get("/legacy")
def legacy_index() -> FileResponse:
    # The previous vanilla-JS UI remains available for comparison.
    return FileResponse(WEBAPP_DIR / "index.html")


@router.get("/presentation")
def presentation() -> FileResponse:
    return FileResponse(SUBMISSION_DIR / "fitcheck_agent_presentation.html")

