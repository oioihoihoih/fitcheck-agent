from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import run_web_candidates_match
from app.providers import get_provider_status, provider_error_metadata_from_result
from app.tool_registry import ToolContext, build_fitcheck_tool_registry


DEFAULT_LIVE_PRODUCT_URL = "https://www.musinsa.com/products/2414779"


def main() -> None:
    provider_status = get_provider_status()
    if not _truthy(os.getenv("FITCHECK_RUN_LIVE_PAGE_FETCH_SMOKE")):
        _print(
            {
                "status": "skipped",
                "reason": "Set FITCHECK_RUN_LIVE_PAGE_FETCH_SMOKE=1 to run a live page fetch smoke check.",
                "providers": provider_status,
            }
        )
        return

    result = _assert_live_page_fetch(provider_status)
    _print({"status": result["status"], "result": result, "providers": provider_status})
    if result["status"] == "failed":
        raise SystemExit(1)


def _assert_live_page_fetch(provider_status: dict[str, Any]) -> dict[str, Any]:
    source_url = os.getenv("FITCHECK_LIVE_PAGE_FETCH_URL", DEFAULT_LIVE_PRODUCT_URL).strip()
    root = Path(__file__).resolve().parents[1]
    output_dir = root / "harness_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    run = {
        "goal": "live page fetch smoke",
        "input": {"goal": "live page fetch smoke", "candidate_url": source_url},
        "state": {"observations": []},
    }
    context = ToolContext(
        web_result_path=output_dir / "web_candidates_match_result.json",
        keyword_result_path=output_dir / "web_keyword_probe" / "keyword_web_probe_result.json",
        refresh_web_candidates=run_web_candidates_match.main,
        run=run,
    )
    registry = build_fitcheck_tool_registry()
    registered = registry.get("register_user_candidate_url").execute(
        {
            "source_url": source_url,
            "candidate_key": "live_page_fetch_smoke",
            "name": "Live page fetch smoke candidate",
        },
        context,
    )
    run["state"]["observations"].append(
        {
            "tool": "register_user_candidate_url",
            "arguments": {"source_url": source_url, "candidate_key": "live_page_fetch_smoke"},
            "result": registered,
        }
    )
    if registered.get("status") != "completed":
        return {
            "check": "live_page_fetch",
            "status": "failed",
            "reason": "candidate_url_registration_failed",
            "source_url": source_url,
            "registered": registered,
        }

    fetched = registry.get("fetch_product_page").execute({"candidate_key": "live_page_fetch_smoke"}, context)
    run["state"]["observations"].append(
        {
            "tool": "fetch_product_page",
            "arguments": {"candidate_key": "live_page_fetch_smoke"},
            "result": fetched,
        }
    )
    metadata = provider_error_metadata_from_result(fetched)
    status = str(fetched.get("fetch_status") or "")
    has_text_evidence = bool(
        (fetched.get("raw_text_excerpt") or "").strip()
        or (fetched.get("spec_table_excerpt") or "").strip()
        or (fetched.get("name") or "").strip()
    )
    if status != "completed":
        return {
            "check": "live_page_fetch",
            "status": "failed",
            "reason": "live_page_fetch_did_not_complete",
            "source_url": source_url,
            "fetch_status": status,
            "provider": fetched.get("provider"),
            "external_api_called": fetched.get("external_api_called"),
            "fetch_error": fetched.get("fetch_error"),
            "fetch_fallback_provider": fetched.get("fetch_fallback_provider"),
            "fetch_fallback_status": fetched.get("fetch_fallback_status"),
            "fetch_fallback_error": fetched.get("fetch_fallback_error"),
            **metadata,
        }
    if fetched.get("external_api_called") is not True:
        return {
            "check": "live_page_fetch",
            "status": "failed",
            "reason": "live_page_fetch_did_not_mark_external_api_called",
            "source_url": source_url,
            "provider": fetched.get("provider"),
            "fetch_status": status,
        }
    if not has_text_evidence:
        return {
            "check": "live_page_fetch",
            "status": "failed",
            "reason": "live_page_fetch_returned_no_text_evidence",
            "source_url": source_url,
            "provider": fetched.get("provider"),
            "fetch_status": status,
            "asset_count": fetched.get("asset_count"),
            "ocr_target_asset_count": fetched.get("ocr_target_asset_count"),
        }
    return {
        "check": "live_page_fetch",
        "status": "completed",
        "source_url": source_url,
        "provider": fetched.get("provider"),
        "external_api_called": fetched.get("external_api_called"),
        "fetch_status": fetched.get("fetch_status"),
        "fetch_fallback_provider": fetched.get("fetch_fallback_provider"),
        "fetch_fallback_status": fetched.get("fetch_fallback_status"),
        "direct_fetch_status": fetched.get("direct_fetch_status"),
        "timeout_seconds": fetched.get("timeout_seconds"),
        "retry_count": fetched.get("retry_count"),
        "asset_count": fetched.get("asset_count"),
        "ocr_target_asset_count": fetched.get("ocr_target_asset_count"),
        "has_raw_text_excerpt": bool((fetched.get("raw_text_excerpt") or "").strip()),
        "has_spec_table_excerpt": bool((fetched.get("spec_table_excerpt") or "").strip()),
        "name": fetched.get("name"),
    }


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
