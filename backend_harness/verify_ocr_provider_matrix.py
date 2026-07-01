from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

from app.providers import build_ocr_provider, get_provider_status, provider_error_metadata_from_result


DEFAULT_PROVIDER_MATRIX = ["fixture_boundary", "tesseract_cli"]


def main() -> None:
    provider_aliases = _provider_aliases()
    if not _truthy(os.getenv("FITCHECK_RUN_OCR_PROVIDER_MATRIX")):
        _print(
            {
                "status": "skipped",
                "reason": "Set FITCHECK_RUN_OCR_PROVIDER_MATRIX=1 to compare OCR providers.",
                "provider_aliases": provider_aliases,
                "current_provider": get_provider_status()["ocr"],
            }
        )
        return

    results = [_evaluate_provider(alias) for alias in provider_aliases]
    payload = {
        **_summarize_results(results),
        "provider_aliases": provider_aliases,
        "results": results,
    }
    _print(payload)
    if payload["status"] == "failed":
        raise SystemExit(1)


def _evaluate_provider(provider_alias: str) -> dict[str, Any]:
    saved_provider = os.environ.get("FITCHECK_OCR_PROVIDER")
    os.environ["FITCHECK_OCR_PROVIDER"] = provider_alias
    try:
        provider_status = get_provider_status()["ocr"]
        provider = build_ocr_provider()
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = _matrix_image_path(temp_path)
            upload_dir = temp_path / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)
            upload_path = upload_dir / image_path.name
            upload_path.write_bytes(image_path.read_bytes())

            product = {
                "candidate_key": "ocr_matrix_candidate",
                "asset_results": [
                    {
                        "asset_id": "ocr_matrix_asset",
                        "image_type": "size_chart",
                        "ocr_target": True,
                        "asset_path": str(image_path),
                    }
                ],
            }
            attachment = {
                "id": "ocr_matrix_upload",
                "filename": upload_path.name,
                "media_type": "image/png",
                "url": f"/outputs/uploads/{upload_path.name}",
                "role": "current_clothing_photo",
            }
            started = perf_counter()
            try:
                product_result = provider.extract_product_evidence(product)
                upload_result = provider.extract_upload_evidence([attachment], upload_dir=upload_dir)
            except Exception as exc:
                return {
                    "provider_alias": provider_alias,
                    "provider": provider_status.get("provider"),
                    "status": "failed",
                    "reason": "ocr_provider_exception",
                    "call_attempted": True,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                    "error": str(exc)[:500],
                    "provider_status": provider_status,
                }
            duration_ms = round((perf_counter() - started) * 1000, 2)
        return _summarize_provider_result(
            provider_alias,
            provider_status,
            product_result,
            upload_result,
            duration_ms,
        )
    finally:
        _restore_env("FITCHECK_OCR_PROVIDER", saved_provider)


def _matrix_image_path(temp_path: Path) -> Path:
    configured = os.getenv("FITCHECK_OCR_PROVIDER_MATRIX_IMAGE_PATH")
    if configured:
        path = Path(configured)
        if path.exists():
            return path
    image_path = temp_path / "fitcheck_ocr_matrix.png"
    _write_ocr_matrix_image(image_path)
    return image_path


def _write_ocr_matrix_image(image_path: Path) -> None:
    try:
        from PIL import Image, ImageDraw
    except Exception:
        image_path.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
            )
        )
        return
    image = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.text((32, 48), "FitCheck OCR matrix", fill="black")
    draw.text((32, 92), "Cotton 100% price 19900 size L", fill="black")
    draw.text((32, 136), "Length 70 Shoulder 48 Chest 55 Sleeve 22", fill="black")
    image.save(image_path)


def _summarize_provider_result(
    provider_alias: str,
    provider_status: dict[str, Any],
    product_result: dict[str, Any],
    upload_result: dict[str, Any],
    duration_ms: float,
) -> dict[str, Any]:
    product_evidence = product_result.get("evidence") or []
    upload_evidence = upload_result.get("evidence") or []
    combined_result = {
        **product_result,
        "evidence": [*product_evidence, *upload_evidence],
    }
    metadata = provider_error_metadata_from_result(combined_result)
    completed_text_count = _completed_text_count(combined_result.get("evidence") or [])
    completed_empty_count = _completed_empty_count(combined_result.get("evidence") or [])
    evidence_failure_count = _evidence_failure_count(combined_result.get("evidence") or [])
    status = _matrix_status(
        metadata=metadata,
        completed_text_count=completed_text_count,
        completed_empty_count=completed_empty_count,
        evidence_failure_count=evidence_failure_count,
    )
    return {
        "provider_alias": provider_alias,
        "provider": product_result.get("provider") or provider_status.get("provider"),
        "mode": product_result.get("mode") or provider_status.get("mode"),
        "status": status,
        "call_attempted": True,
        "external_api_called": bool(product_result.get("external_api_called") or upload_result.get("external_api_called")),
        "duration_ms": duration_ms,
        "timeout_seconds": product_result.get("timeout_seconds") or upload_result.get("timeout_seconds"),
        "retry_count": max(int(product_result.get("retry_count") or 0), int(upload_result.get("retry_count") or 0)),
        "completed_text_count": completed_text_count,
        "completed_empty_count": completed_empty_count,
        "evidence_failure_count": evidence_failure_count,
        "product_evidence_statuses": [item.get("status") for item in product_evidence],
        "product_evidence_image_types": [item.get("image_type") for item in product_evidence],
        "upload_evidence_statuses": [item.get("status") for item in upload_evidence],
        "product_evidence_reasons": [item.get("reason") for item in product_evidence if item.get("reason")],
        "upload_evidence_reasons": [item.get("reason") for item in upload_evidence if item.get("reason")],
        "provider_status": provider_status,
        **metadata,
    }


def _matrix_status(
    *,
    metadata: dict[str, Any],
    completed_text_count: int,
    completed_empty_count: int,
    evidence_failure_count: int,
) -> str:
    category = metadata.get("provider_error_category")
    if category == "configuration":
        return "configuration_required"
    if category == "provider_not_connected":
        return "not_connected"
    if category:
        return "failed"
    if completed_text_count > 0:
        return "completed"
    if completed_empty_count > 0:
        return "completed_empty"
    if evidence_failure_count > 0:
        return "failed"
    return "completed_no_evidence"


def _completed_text_count(evidence: list[dict[str, Any]]) -> int:
    return len([item for item in evidence if item.get("status") == "completed" and item.get("text")])


def _completed_empty_count(evidence: list[dict[str, Any]]) -> int:
    return len([item for item in evidence if item.get("status") == "completed_empty"])


def _evidence_failure_count(evidence: list[dict[str, Any]]) -> int:
    return len([item for item in evidence if item.get("status") in {"failed", "configuration_required", "not_connected"}])


def _summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in results if item.get("status") == "completed"]
    completed_empty = [item for item in results if item.get("status") == "completed_empty"]
    configuration_required = [item for item in results if item.get("status") == "configuration_required"]
    not_connected = [item for item in results if item.get("status") == "not_connected"]
    failed = [item for item in results if item.get("status") == "failed"]
    require_success = _truthy(os.getenv("FITCHECK_OCR_PROVIDER_MATRIX_REQUIRE_SUCCESS"))
    require_text = _truthy(os.getenv("FITCHECK_OCR_PROVIDER_MATRIX_REQUIRE_TEXT"))
    status = "completed"
    if failed:
        status = "failed"
    elif require_text and not completed:
        status = "failed"
    elif require_success and not completed and not completed_empty:
        status = "failed"
    elif not completed and not completed_empty:
        status = "configuration_required" if configuration_required else "not_connected" if not_connected else "failed"
    return {
        "status": status,
        "completed_count": len(completed),
        "completed_empty_count": len(completed_empty),
        "configuration_required_count": len(configuration_required),
        "not_connected_count": len(not_connected),
        "failed_count": len(failed),
        "require_success": require_success,
        "require_text": require_text,
    }


def _provider_aliases() -> list[str]:
    raw = os.getenv("FITCHECK_OCR_PROVIDER_MATRIX", "")
    aliases = [item.strip() for item in raw.split(",") if item.strip()] if raw else DEFAULT_PROVIDER_MATRIX
    return aliases or DEFAULT_PROVIDER_MATRIX


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
