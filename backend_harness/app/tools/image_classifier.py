from __future__ import annotations

from pathlib import Path


def classify_closet_asset(asset_path: Path, asset_type_hint: str | None = None) -> dict:
    """Classify a user-uploaded closet image.

    MVP behavior is rule-based. Production can replace this with a VLM or a
    lightweight image classifier.
    """
    suffix = asset_path.suffix.lower()
    filename = asset_path.name.lower()

    if asset_type_hint:
        image_type = asset_type_hint
        confidence = 0.95
    elif "label" in filename or "라벨" in filename:
        image_type = "label_photo"
        confidence = 0.82
    elif suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        image_type = "item_photo"
        confidence = 0.72
    else:
        image_type = "unknown"
        confidence = 0.35

    return {
        "asset_path": str(asset_path),
        "image_type": image_type,
        "confidence": confidence,
        "method": "rule_based_harness",
    }

