from __future__ import annotations

from fastapi import APIRouter

from ..schemas import ImageUploadRequest, ImageUploadResponse
from ..server_context import upload_image_payload


router = APIRouter(prefix="/api/uploads")


@router.post("/images", response_model=ImageUploadResponse)
def upload_image(payload: ImageUploadRequest) -> dict:
    return upload_image_payload(payload)
