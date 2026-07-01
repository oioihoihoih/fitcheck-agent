from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..server_context import SUBMISSION_DIR, WEBAPP_DIR


router = APIRouter()


@router.get("/")
def index() -> FileResponse:
    return FileResponse(WEBAPP_DIR / "index.html")


@router.get("/presentation")
def presentation() -> FileResponse:
    return FileResponse(SUBMISSION_DIR / "fitcheck_agent_presentation.html")

