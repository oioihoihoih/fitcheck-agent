from __future__ import annotations

from pathlib import Path


OCR_TARGET_TYPES = {"label_photo", "size_chart", "material_chart", "detail_text"}


def extract_ocr_for_asset(asset_path: Path, classification: dict) -> dict:
    """OCR tool boundary.

    The current closet item photo is not a text-heavy image, so OCR is skipped.
    Production can replace this function with PaddleOCR, Tesseract, or a cloud
    OCR API.
    """
    image_type = classification.get("image_type")
    if image_type not in OCR_TARGET_TYPES:
        return {
            "asset_path": str(asset_path),
            "status": "skipped",
            "reason": "not_text_heavy_image",
            "provider": "ocr_stub",
            "external_api_called": False,
            "text": None,
            "confidence": None,
        }

    return {
        "asset_path": str(asset_path),
        "status": "not_implemented",
        "reason": "ocr_provider_not_connected_in_harness",
        "provider": "ocr_stub",
        "external_api_called": False,
        "text": None,
        "confidence": 0.0,
    }

