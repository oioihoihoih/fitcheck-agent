from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import run_web_candidates_match
from app.providers import build_ocr_provider, build_search_provider, get_provider_status


PNG_1X1 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="


def main() -> None:
    provider_status = get_provider_status()
    if not _truthy(os.getenv("FITCHECK_RUN_LIVE_PROVIDER_SMOKE")):
        _print(
            {
                "status": "skipped",
                "reason": "Set FITCHECK_RUN_LIVE_PROVIDER_SMOKE=1 to run selected live provider smoke checks.",
                "providers": provider_status,
            }
        )
        return

    results = [
        _assert_selected_live_search(provider_status),
        _assert_selected_live_ocr(provider_status),
    ]
    failed = [item for item in results if item["status"] == "failed"]
    _print(
        {
            "status": "failed" if failed else "completed",
            "results": results,
            "providers": provider_status,
        }
    )
    if failed:
        raise SystemExit(1)


def _assert_selected_live_search(provider_status: dict[str, Any]) -> dict[str, Any]:
    search_status = provider_status["search"]
    if search_status["mode"] != "live_provider":
        return {
            "check": "live_search",
            "status": "skipped",
            "reason": "FITCHECK_SEARCH_PROVIDER is not set to a live provider.",
            "selected": search_status["selected"],
        }
    if search_status["requires_api_key"] and not search_status["api_key_configured"]:
        return {
            "check": "live_search",
            "status": "failed",
            "reason": _search_key_requirement(search_status),
            "selected": search_status["selected"],
        }
    if search_status.get("requires_base_url") and not search_status.get("base_url_configured"):
        return {
            "check": "live_search",
            "status": "failed",
            "reason": _search_base_url_requirement(search_status),
            "selected": search_status["selected"],
        }

    root = Path(__file__).resolve().parents[1]
    output_dir = root / "harness_outputs"
    provider = build_search_provider(
        web_result_path=output_dir / "web_candidates_match_result.json",
        refresh_web_candidates=run_web_candidates_match.main,
    )
    query = os.getenv("FITCHECK_LIVE_SMOKE_QUERY", "반팔 티셔츠 소재 사이즈표")
    try:
        result = provider.search(query, refresh=False)
    except Exception as exc:
        return {
            "check": "live_search",
            "status": "failed",
            "reason": "live_search_provider_request_failed",
            "provider": search_status["provider"],
            "error": str(exc),
        }

    if result.get("status") != "completed":
        return {
            "check": "live_search",
            "status": "failed",
            "reason": "live_search_provider_did_not_complete",
            "provider": result.get("provider"),
            "result_status": result.get("status"),
            "error": result.get("error"),
        }
    if result.get("external_api_called") is not True:
        return {
            "check": "live_search",
            "status": "failed",
            "reason": "live_search_provider_did_not_mark_external_api_called",
            "provider": result.get("provider"),
        }
    if int(result.get("candidate_count") or 0) < 1:
        return {
            "check": "live_search",
            "status": "failed",
            "reason": "live_search_provider_returned_no_candidates",
            "provider": result.get("provider"),
            "query": query,
        }
    return {
        "check": "live_search",
        "status": "completed",
        "provider": result.get("provider"),
        "mode": result.get("mode"),
        "external_api_called": result.get("external_api_called"),
        "candidate_count": result.get("candidate_count"),
        "first_candidate_type": (result.get("candidates") or [{}])[0].get("candidate_type"),
    }


def _assert_selected_live_ocr(provider_status: dict[str, Any]) -> dict[str, Any]:
    ocr_status = provider_status["ocr"]
    if ocr_status["mode"] != "live_provider":
        return {
            "check": "live_ocr",
            "status": "skipped",
            "reason": "FITCHECK_OCR_PROVIDER is not set to tesseract_cli.",
            "selected": ocr_status["selected"],
        }
    if not ocr_status.get("tesseract_cli_available"):
        return {
            "check": "live_ocr",
            "status": "failed",
            "reason": "FITCHECK_OCR_PROVIDER=tesseract_cli but local tesseract executable is unavailable.",
            "selected": ocr_status["selected"],
        }

    provider = build_ocr_provider()
    with TemporaryDirectory() as temp_dir:
        image_path = Path(temp_dir) / "fitcheck_ocr_smoke.png"
        image_path.write_bytes(base64.b64decode(PNG_1X1.split(",", 1)[1]))
        product = {
            "candidate_key": "live_ocr_smoke",
            "asset_results": [
                {
                    "asset_id": "live_ocr_smoke_asset",
                    "image_type": "size_chart",
                    "ocr_target": True,
                    "asset_path": str(image_path),
                }
            ],
        }
        result = provider.extract_product_evidence(product)

    if result.get("provider") != "tesseract_cli_ocr":
        return {
            "check": "live_ocr",
            "status": "failed",
            "reason": "FITCHECK_OCR_PROVIDER=tesseract_cli did not build Tesseract provider.",
            "provider": result.get("provider"),
        }
    evidence = result.get("evidence") or []
    if not evidence:
        return {
            "check": "live_ocr",
            "status": "failed",
            "reason": "Tesseract provider returned no evidence for OCR target asset.",
        }
    if any(item.get("reason") == "no_local_asset_path_for_live_ocr" for item in evidence):
        return {
            "check": "live_ocr",
            "status": "failed",
            "reason": "Tesseract provider did not receive the local asset path.",
        }
    if any(item.get("status") == "configuration_required" for item in evidence):
        return {
            "check": "live_ocr",
            "status": "failed",
            "reason": "Tesseract executable exists, but OCR language/configuration is not usable.",
            "evidence": evidence,
        }
    if not any(item.get("external_api_called") for item in evidence):
        return {
            "check": "live_ocr",
            "status": "failed",
            "reason": "Tesseract provider did not attempt OCR execution.",
            "evidence": evidence,
        }
    return {
        "check": "live_ocr",
        "status": "completed",
        "provider": result.get("provider"),
        "mode": result.get("mode"),
        "external_api_called": result.get("external_api_called"),
        "evidence_statuses": [item.get("status") for item in evidence],
    }


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _search_key_requirement(search_status: dict[str, Any]) -> str:
    provider = search_status.get("provider")
    if provider == "jina_search":
        return "Selected Jina search provider requires JINA_API_KEY or FITCHECK_SEARCH_API_KEY."
    if provider == "brave_search":
        return "Selected Brave search provider requires BRAVE_SEARCH_API_KEY or FITCHECK_SEARCH_API_KEY."
    return "Selected live search provider requires an API key."


def _search_base_url_requirement(search_status: dict[str, Any]) -> str:
    provider = search_status.get("provider")
    if provider == "searxng_search":
        return "Selected SearXNG search provider requires FITCHECK_SEARXNG_URL or SEARXNG_URL."
    return "Selected live search provider requires a base URL."


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
