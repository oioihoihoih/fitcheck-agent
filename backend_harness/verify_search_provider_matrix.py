from __future__ import annotations

import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any

import run_web_candidates_match
from app.providers import build_search_provider, get_provider_status, provider_error_metadata_from_result


DEFAULT_PROVIDER_MATRIX = ["brave_search", "tavily_search", "searxng", "jina_search", "duckduckgo_html", "google_cse"]


def main() -> None:
    provider_aliases = _provider_aliases()
    if not _truthy(os.getenv("FITCHECK_RUN_SEARCH_PROVIDER_MATRIX")):
        _print(
            {
                "status": "skipped",
                "reason": "Set FITCHECK_RUN_SEARCH_PROVIDER_MATRIX=1 to compare configured live search providers.",
                "provider_aliases": provider_aliases,
                "current_provider": get_provider_status()["search"],
            }
        )
        return

    query = os.getenv("FITCHECK_SEARCH_PROVIDER_MATRIX_QUERY", "반팔 티셔츠 소재 사이즈표")
    results = [_evaluate_provider(alias, query) for alias in provider_aliases]
    summary = _summarize_results(results)
    payload = {
        **summary,
        "query": query,
        "provider_aliases": provider_aliases,
        "results": results,
    }
    _print(payload)
    if payload["status"] == "failed":
        raise SystemExit(1)


def _evaluate_provider(provider_alias: str, query: str) -> dict[str, Any]:
    saved_provider = os.environ.get("FITCHECK_SEARCH_PROVIDER")
    os.environ["FITCHECK_SEARCH_PROVIDER"] = provider_alias
    try:
        provider_status = get_provider_status()["search"]
        config_status = _configuration_gap(provider_status)
        if config_status:
            return {
                "provider_alias": provider_alias,
                "provider": provider_status.get("provider"),
                "status": "configuration_required",
                "reason": config_status,
                "call_attempted": False,
                "provider_status": provider_status,
            }

        root = Path(__file__).resolve().parents[1]
        output_dir = root / "harness_outputs"
        provider = build_search_provider(
            web_result_path=output_dir / "web_candidates_match_result.json",
            refresh_web_candidates=run_web_candidates_match.main,
        )
        started = perf_counter()
        try:
            result = provider.search(query, refresh=False)
        except Exception as exc:
            return {
                "provider_alias": provider_alias,
                "provider": provider_status.get("provider"),
                "status": "failed",
                "reason": "provider_exception",
                "call_attempted": True,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "error": str(exc)[:500],
                "provider_status": provider_status,
            }
        duration_ms = round((perf_counter() - started) * 1000, 2)
        return _summarize_provider_result(provider_alias, provider_status, result, duration_ms)
    finally:
        _restore_env("FITCHECK_SEARCH_PROVIDER", saved_provider)


def _configuration_gap(provider_status: dict[str, Any]) -> str | None:
    if provider_status.get("mode") != "live_provider":
        return "not_live_provider"
    if provider_status.get("requires_api_key") and not provider_status.get("api_key_configured"):
        return "api_key_required"
    if provider_status.get("requires_search_engine_id") and not provider_status.get("search_engine_id_configured"):
        return "search_engine_id_required"
    if provider_status.get("requires_base_url") and not provider_status.get("base_url_configured"):
        return "base_url_required"
    return None


def _summarize_provider_result(
    provider_alias: str,
    provider_status: dict[str, Any],
    result: dict[str, Any],
    duration_ms: float,
) -> dict[str, Any]:
    result_status = str(result.get("status") or "unknown")
    candidate_count = int(result.get("candidate_count") or 0)
    matrix_status = "completed" if result_status == "completed" and candidate_count > 0 else result_status
    if result_status == "completed" and candidate_count == 0:
        matrix_status = "completed_no_candidates"
    summary = {
        "provider_alias": provider_alias,
        "provider": result.get("provider") or provider_status.get("provider"),
        "status": matrix_status,
        "result_status": result_status,
        "call_attempted": True,
        "external_api_called": result.get("external_api_called"),
        "duration_ms": duration_ms,
        "timeout_seconds": result.get("timeout_seconds"),
        "retry_count": result.get("retry_count"),
        "retry_errors": result.get("retry_errors", []),
        "candidate_count": candidate_count,
        "candidate_filter_policy": result.get("candidate_filter_policy"),
        "rejected_candidate_count": result.get("rejected_candidate_count"),
        "first_candidate": _first_candidate_summary(result.get("candidates") or []),
        "error": result.get("error"),
        "provider_status": provider_status,
    }
    summary.update(provider_error_metadata_from_result(result))
    return summary


def _first_candidate_summary(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    first = candidates[0]
    return {
        "name": first.get("name"),
        "source_url": first.get("source_url"),
        "candidate_type": first.get("candidate_type"),
        "commerce_score": first.get("commerce_score"),
        "commerce_signals": first.get("commerce_signals", []),
        "content_risk_flags": first.get("content_risk_flags", []),
    }


def _summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in results if item.get("status") == "completed"]
    attempted_failures = [
        item
        for item in results
        if item.get("call_attempted") and item.get("status") not in {"completed", "completed_no_candidates"}
    ]
    configuration_required = [item for item in results if item.get("status") == "configuration_required"]
    no_candidates = [item for item in results if item.get("status") == "completed_no_candidates"]
    require_success = _truthy(os.getenv("FITCHECK_SEARCH_PROVIDER_MATRIX_REQUIRE_SUCCESS"))
    status = "completed"
    if attempted_failures:
        status = "failed"
    elif require_success and not completed:
        status = "failed"
    elif not completed and not no_candidates:
        status = "configuration_required"
    return {
        "status": status,
        "completed_count": len(completed),
        "completed_no_candidates_count": len(no_candidates),
        "configuration_required_count": len(configuration_required),
        "attempted_failure_count": len(attempted_failures),
        "require_success": require_success,
    }


def _provider_aliases() -> list[str]:
    raw = os.getenv("FITCHECK_SEARCH_PROVIDER_MATRIX", "")
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
