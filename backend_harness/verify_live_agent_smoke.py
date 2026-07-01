from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
from http.cookiejar import CookieJar
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener


DEFAULT_GOAL = (
    "유니언즈에서 산 L 사이즈 반팔 티셔츠처럼 핏, 재질, 오래 입는 느낌이 비슷한 옷을 여러 개 찾아줘."
)
DEFAULT_FREE_TEXT = (
    "이거 유니언즈에서 산 반팔 티셔츠인데, 사이즈는 L이고, 내 옷의 핏, 재질이 마음에 들고 "
    "오래 입어도 괜찮아서 비슷한 옷을 추천해줘."
)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    base_url = os.getenv("FITCHECK_AGENT_BASE_URL", "http://127.0.0.1:8000").rstrip("/") + "/"
    _assert_call_id_correlation_helper_contract()
    _assert_agent_loop_trace_summary_contract()
    _assert_product_evidence_summary_contract()
    _assert_final_report_user_text_quality_contract()
    _assert_harness_publish_summary_contract()
    _assert_live_agent_harness_export_contract()
    if not _truthy(os.getenv("FITCHECK_RUN_LIVE_AGENT_SMOKE")):
        _emit(
            {
                "status": "skipped",
                "reason": "Set FITCHECK_RUN_LIVE_AGENT_SMOKE=1 to call the live /api/agent/runs route.",
                "base_url": base_url,
                "checks": [
                    "api_status_llm_connected",
                    "agent_run_created",
                    "external_api_called",
                    "model_selected_tool_call",
                    "backend_tool_observation_persisted",
                    "call_id_correlation_helper_contract",
                    "agent_loop_trace_summary_contract",
                    "product_evidence_summary_contract",
                    "tool_call_output_observation_call_id_correlated",
                    "completed_final_report_has_no_pending_user_question",
                    "run_fetch_by_session",
                    "agentic_boundary_status_reported",
                    "completion_status_reported",
                    "optional_expected_missing_info_field",
                    "optional_expected_backend_recovery_reason",
                    "optional_expected_backend_recovery_tools",
                    "harness_trace_artifact_contract",
                    "harness_eval_case_record_contract",
                    "optional_harness_publish_summary_contract",
                    "optional_harness_publish_contract",
                ],
            },
            args,
        )
        return

    timeout = float(os.getenv("FITCHECK_LIVE_AGENT_TIMEOUT", "180"))
    expected_status = os.getenv("FITCHECK_LIVE_AGENT_EXPECT_STATUS", "completed").strip() or "completed"
    expectations = _live_agent_expectations()
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    headers = {"X-FitCheck-Session-Id": os.getenv("FITCHECK_LIVE_AGENT_SESSION_ID", "live_agent_smoke")}

    status = _request_json(opener, "GET", urljoin(base_url, "api/status"), timeout=timeout, headers=headers)
    if status.get("llm", {}).get("status") != "connected":
        _fail(
            {
                "reason": "llm_not_connected",
                "base_url": base_url,
                "llm": status.get("llm"),
            }
        )
    provider_probe = _probe_harness_provider_if_requested(opener, timeout=timeout, status=status)
    provider_probe_failures = _provider_probe_failures(provider_probe)

    payload = {
        "goal": os.getenv("FITCHECK_LIVE_AGENT_GOAL", DEFAULT_GOAL),
        "free_text": os.getenv("FITCHECK_LIVE_AGENT_FREE_TEXT", DEFAULT_FREE_TEXT),
        "refresh_web": _truthy(os.getenv("FITCHECK_LIVE_AGENT_REFRESH_WEB")),
    }
    allow_cached_search = _truthy(os.getenv("FITCHECK_LIVE_AGENT_ALLOW_CACHED_SEARCH"))
    if allow_cached_search:
        payload["allow_cached_harness_search"] = True
    run = _request_json(
        opener,
        "POST",
        urljoin(base_url, "api/agent/runs"),
        payload=payload,
        timeout=timeout,
        headers=headers,
    )
    summary = _summarize_run(run)

    fetched = None
    sdk_events = None
    sdk_events_terminal_delta = None
    client_transcript = None
    fetch_failures: list[str] = []
    if run.get("run_id"):
        fetched = _request_json(
            opener,
            "GET",
            urljoin(base_url, f"api/agent/runs/{run['run_id']}"),
            timeout=timeout,
            headers=headers,
        )
        if fetched.get("run_id") != run.get("run_id"):
            fetch_failures.append("run_fetch_by_session_failed")
        if not fetched.get("state", {}).get("observations"):
            fetch_failures.append("fetched_run_missing_observations")
        fetch_failures.extend(
            f"fetched_{failure}" for failure in _call_id_correlation_failures(fetched)
        )
        sdk_events = _request_json(
            opener,
            "GET",
            urljoin(base_url, f"api/agent/runs/{run['run_id']}/sdk-events"),
            timeout=timeout,
            headers=headers,
        )
        fetch_failures.extend(
            f"sdk_events_{failure}"
            for failure in _sdk_event_projection_failures(run, sdk_events, expected_status, expectations)
        )
        client_transcript = _request_json(
            opener,
            "GET",
            urljoin(base_url, f"api/agent/runs/{run['run_id']}/client-transcript"),
            timeout=timeout,
            headers=headers,
        )
        fetch_failures.extend(
            f"client_transcript_{failure}"
            for failure in _client_transcript_failures(run, client_transcript, expected_status, expectations)
        )
        next_after = _safe_int(sdk_events.get("next_after"))
        if next_after and next_after > 0:
            sdk_events_terminal_delta = _request_json(
                opener,
                "GET",
                urljoin(base_url, f"api/agent/runs/{run['run_id']}/sdk-events?after={next_after - 1}"),
                timeout=timeout,
                headers=headers,
            )
            fetch_failures.extend(
                f"sdk_events_terminal_delta_{failure}"
                for failure in _sdk_terminal_delta_failures(
                    run,
                    sdk_events_terminal_delta,
                    next_after,
                    expected_status,
                    expectations,
                )
            )

    boundary_failures = provider_probe_failures + _live_agent_boundary_failures(run) + fetch_failures
    completion_failures = _live_agent_completion_failures(run, expected_status, expectations)
    require_completion = _truthy(os.getenv("FITCHECK_LIVE_AGENT_REQUIRE_COMPLETION"))
    failures = list(boundary_failures)
    if require_completion:
        failures.extend(completion_failures)

    result = {
        "status": "failed" if failures else "completed",
        "base_url": base_url,
        **_live_run_provenance_summary(run),
        "allow_cached_harness_search": allow_cached_search,
        "expected_status": expected_status,
        "expected_missing_info_field": expectations.get("missing_field"),
        "expected_recovery_reason": expectations.get("recovery_reason"),
        "expected_backend_recovery_tools": expectations.get("backend_recovery_tools"),
        "expected_min_search_candidates": expectations.get("min_search_candidates"),
        "expected_min_ranked_matches": expectations.get("min_ranked_matches"),
        "expected_min_normalized_candidates": expectations.get("min_normalized_candidates"),
        "expected_min_reported_candidates": expectations.get("min_reported_candidates"),
        "expected_min_size_measurement_rows": expectations.get("min_size_measurement_rows"),
        "expected_min_matched_target_size": expectations.get("min_matched_target_size"),
        "expected_min_ranked_measurement_deltas": expectations.get("min_ranked_measurement_deltas"),
        "expected_min_ranked_size_field_results": expectations.get("min_ranked_size_field_results"),
        "require_completion": require_completion,
        "agentic_boundary_status": "failed" if boundary_failures else "completed",
        "completion_status": "completed" if not completion_failures else "failed",
        "provider_probe_status": provider_probe.get("status") if provider_probe else None,
        "provider_probe_diagnostic_id": provider_probe.get("diagnostic_id") if provider_probe else None,
        "provider_probe_external_api_called": provider_probe.get("external_api_called") if provider_probe else None,
        "provider_probe_failures": provider_probe_failures,
        "provider_probe": provider_probe or {},
        "agent_run": summary,
        **_agent_loop_trace_summary(run),
        **_product_evidence_summary(run),
        "fetched_call_id_correlation_count": (
            _call_id_correlation_count(fetched) if fetched else 0
        ),
        "sdk_event_final_output_count": _sdk_final_output_count(sdk_events),
        "sdk_event_final_output_grounded": _sdk_final_output_grounded(sdk_events),
        "sdk_event_final_output_citation_badge_count": _sdk_final_output_citation_badge_count(sdk_events),
        "sdk_event_final_output_suspicious_claim_counts": _sdk_final_output_suspicious_claim_counts(sdk_events),
        "sdk_event_final_output_user_text_failures": _sdk_final_output_user_text_failures(sdk_events),
        "sdk_event_missing_info_request_count": _sdk_missing_info_request_count(sdk_events),
        "sdk_event_agent_guidance_count": _sdk_agent_guidance_count(sdk_events),
        "sdk_event_expected_guidance_count": _sdk_agent_guidance_count(
            sdk_events,
            reason=str(expectations.get("recovery_reason") or ""),
        ),
        "sdk_event_projected_count": (sdk_events or {}).get("projected_event_count"),
        "sdk_event_cursor_policy": (sdk_events or {}).get("cursor_policy"),
        "sdk_event_terminal_event_included": (sdk_events or {}).get("terminal_event_included"),
        "sdk_event_terminal_delta_raw_count": (sdk_events_terminal_delta or {}).get("raw_event_count"),
        "sdk_event_terminal_delta_final_output_included": (
            sdk_events_terminal_delta or {}
        ).get("final_output_included"),
        "client_transcript_run_status": (client_transcript or {}).get("run_status"),
        "client_transcript_message_count": len((client_transcript or {}).get("messages") or []),
        "client_transcript_question_count": _client_transcript_question_count(client_transcript),
        "client_transcript_agent_guidance_count": _client_transcript_guidance_count(client_transcript),
        "client_transcript_expected_guidance_count": _client_transcript_guidance_count(
            client_transcript,
            reason=str(expectations.get("recovery_reason") or ""),
        ),
        "boundary_failures": boundary_failures,
        "completion_failures": completion_failures,
        "failures": failures,
        "fetched_run_status": fetched.get("status") if fetched else None,
    }
    harness_export = _export_live_agent_harness_artifacts(
        trace_artifact=_build_live_agent_trace_artifact(
            base_url=base_url,
            request_payload=payload,
            run=run,
            fetched_run=fetched,
            sdk_events=sdk_events,
            sdk_events_terminal_delta=sdk_events_terminal_delta,
            client_transcript=client_transcript,
            result=result,
            expected_status=expected_status,
            expectations=expectations,
            allow_cached_search=allow_cached_search,
        ),
        eval_case_record=_build_live_agent_eval_case_record(
            base_url=base_url,
            request_payload=payload,
            result=result,
            expected_status=expected_status,
            expectations=expectations,
            allow_cached_search=allow_cached_search,
        ),
        run_id=str(run.get("run_id") or "no_run"),
    )
    if harness_export:
        result["harness_export"] = harness_export
        result.update(_harness_export_summary(harness_export))
    harness_publish = _publish_live_agent_harness_export_if_requested(
        harness_export,
        run_id=str(run.get("run_id") or "no_run"),
    )
    if harness_publish:
        result["harness_publish"] = harness_publish
        harness_publish_summary = _harness_publish_summary(harness_publish)
        result.update(harness_publish_summary)
        harness_publish_failures = harness_publish_summary.get("harness_publish_failures") or []
        if harness_publish_failures:
            failures.extend(harness_publish_failures)
            result["status"] = "failed"
            result["failures"] = failures
            result["harness_publish_status"] = "failed"
    _emit(result, args)
    if failures:
        raise SystemExit(1)


def _request_json(
    opener,
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {url} failed with HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise SystemExit(f"{method} {url} failed: {exc.reason}") from exc
    return json.loads(body)


def _summarize_run(run: dict[str, Any]) -> dict[str, Any]:
    trace = run.get("trace") or []
    observations = run.get("state", {}).get("observations") or []
    return {
        "status": run.get("status"),
        "run_id": run.get("run_id"),
        "error_code": (run.get("error") or {}).get("code"),
        "llm": {
            "provider": run.get("llm", {}).get("provider"),
            "model": run.get("llm", {}).get("model"),
            "api_style": run.get("llm", {}).get("api_style"),
            "external_api_called": run.get("llm", {}).get("external_api_called"),
        },
        "trace_count": len(trace),
        "tool_calls": [step.get("name") for step in trace if step.get("step_type") == "tool_call"],
        "tool_outputs": [step.get("name") for step in trace if step.get("step_type") == "tool_output"],
        "observation_tools": [item.get("tool") for item in observations],
        "backend_recovery_tools": _backend_recovery_tool_names(run),
        "backend_recovery_reasons": _backend_recovery_reasons(run),
        "agent_guidance_reasons": _agent_guidance_reasons(run),
        "missing_info_fields": _missing_info_fields(run),
        "call_id_correlation_count": _call_id_correlation_count(run),
        "final_report_grounded": (run.get("final_report") or {}).get("grounded"),
        "final_report_user_text_failures": (
            _user_visible_report_text_failures(str((run.get("final_report") or {}).get("summary") or ""))
            if run.get("final_report")
            else []
        ),
        "final_report_summary_preview": str((run.get("final_report") or {}).get("summary") or "")[:240],
    }


def _live_run_provenance_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run.get("run_id"),
        "trace_id": run.get("run_id"),
    }


def _build_live_agent_trace_artifact(
    *,
    base_url: str,
    request_payload: dict[str, Any],
    run: dict[str, Any],
    fetched_run: dict[str, Any] | None,
    sdk_events: dict[str, Any] | None,
    sdk_events_terminal_delta: dict[str, Any] | None,
    client_transcript: dict[str, Any] | None,
    result: dict[str, Any],
    expected_status: str,
    expectations: dict[str, Any],
    allow_cached_search: bool,
) -> dict[str, Any]:
    llm = run.get("llm") if isinstance(run.get("llm"), dict) else {}
    items = _trace_ingest_items_from_run(run)
    final_report = run.get("final_report") if isinstance(run.get("final_report"), dict) else {}
    trace = {
        "schema_version": "fitcheck_live_agent_trace.v1",
        "source": "fitcheck_live_agent_smoke",
        "status": result.get("status"),
        "scenario": "fitcheck_live_agent_route",
        "trace_id": run.get("run_id"),
        "provider": llm.get("provider"),
        "api_style": llm.get("api_style"),
        "model": llm.get("model"),
        "external_api_called": llm.get("external_api_called"),
        "base_url": base_url,
        "request": {
            "goal": request_payload.get("goal"),
            "free_text": request_payload.get("free_text"),
            "refresh_web": request_payload.get("refresh_web"),
            "allow_cached_harness_search": allow_cached_search,
        },
        "expected": {
            "status": expected_status,
            "missing_field": expectations.get("missing_field"),
            "recovery_reason": expectations.get("recovery_reason"),
            "backend_recovery_tools": expectations.get("backend_recovery_tools") or [],
        },
        "summary": result,
        "raw_responses": _raw_responses_from_run(run),
        "items": items,
        "stream_events": _stream_events_from_sdk_events(sdk_events),
        "phases": [
            {
                "name": "live_agent_smoke",
                "status": run.get("status"),
                "raw_response_count": len(_raw_responses_from_run(run)),
                "new_item_count": len(items),
                "final_output": final_report.get("summary") or None,
                "run_id": run.get("run_id"),
            }
        ],
        "missing_info": (run.get("state") or {}).get("missing_info") or [],
        "agent_questions": _agent_questions_from_sdk_events(sdk_events),
        "agent_guidance": _agent_guidance_from_sdk_events(sdk_events),
        "final_output": _final_output_from_run(run),
        "source_endpoints": {
            "run": f"/api/agent/runs/{run.get('run_id')}" if run.get("run_id") else None,
            "evidence": f"/api/agent/runs/{run.get('run_id')}/evidence" if run.get("run_id") else None,
            "client_transcript": f"/api/agent/runs/{run.get('run_id')}/client-transcript" if run.get("run_id") else None,
            "sdk_events": f"/api/agent/runs/{run.get('run_id')}/sdk-events" if run.get("run_id") else None,
        },
        "projections": {
            "fetched_run_status": (fetched_run or {}).get("status"),
            "sdk_events_projected_count": (sdk_events or {}).get("projected_event_count"),
            "sdk_events_terminal_delta_raw_count": (sdk_events_terminal_delta or {}).get("raw_event_count"),
            "client_transcript_message_count": len((client_transcript or {}).get("messages") or []),
        },
        "provider_probe": _provider_probe_artifact_summary(result.get("provider_probe")),
    }
    if result.get("failures"):
        trace["error_type"] = "live_agent_smoke_failure"
        trace["error"] = {"failures": result.get("failures")}
    return _drop_empty(trace)


def _build_live_agent_eval_case_record(
    *,
    base_url: str,
    request_payload: dict[str, Any],
    result: dict[str, Any],
    expected_status: str,
    expectations: dict[str, Any],
    allow_cached_search: bool,
) -> dict[str, Any]:
    run_summary = result.get("agent_run") if isinstance(result.get("agent_run"), dict) else {}
    failures = list(result.get("failures") or [])
    name_status = "failure" if failures else "regression"
    verifier_command = _live_agent_verifier_command(expected_status, expectations)
    return {
        "name": f"fitcheck live agent SDK smoke {name_status}: {expected_status}",
        "failure_label": _live_agent_eval_failure_label(result),
        "input": {
            "goal": request_payload.get("goal"),
            "free_text": request_payload.get("free_text"),
            "refresh_web": request_payload.get("refresh_web"),
            "allow_cached_harness_search": allow_cached_search,
            "base_url": base_url,
        },
        "expected": {
            "run_status": expected_status,
            "agentic_boundary_status": "completed",
            "external_api_called": True,
            "model_selected_tool_call": True,
            "backend_tool_observation_persisted": True,
            "sdk_events_projected": True,
            "client_transcript_projected": True,
            "agent_guidance_projected": bool(expectations.get("recovery_reason")),
            "expected_guidance_reason": expectations.get("recovery_reason") or None,
            "missing_field": expectations.get("missing_field") or None,
            "recovery_reason": expectations.get("recovery_reason") or None,
            "backend_recovery_tools": expectations.get("backend_recovery_tools") or [],
            "min_search_candidates": expectations.get("min_search_candidates"),
            "min_ranked_matches": expectations.get("min_ranked_matches"),
            "min_normalized_candidates": expectations.get("min_normalized_candidates"),
            "min_reported_candidates": expectations.get("min_reported_candidates"),
            "min_size_measurement_rows": expectations.get("min_size_measurement_rows"),
            "min_matched_target_size": expectations.get("min_matched_target_size"),
            "min_ranked_measurement_deltas": expectations.get("min_ranked_measurement_deltas"),
            "min_ranked_size_field_results": expectations.get("min_ranked_size_field_results"),
            "provider_probe_valid": bool(result.get("provider_probe")),
        },
        "verifier_command": verifier_command,
        "evidence_refs": [
            f"fitcheck_run:{run_summary.get('run_id')}",
            "artifact:fitcheck_live_agent_trace",
            "endpoint:/api/agent/runs/{run_id}/sdk-events",
            "endpoint:/api/agent/runs/{run_id}/client-transcript",
        ],
        "status": "active",
        "metadata": {
            "kind": "fitcheck-live-agent-sdk-regression",
            "source": "verify_live_agent_smoke.py",
            "result_status": result.get("status"),
            "agent_run_status": run_summary.get("status"),
            "agentic_boundary_status": result.get("agentic_boundary_status"),
            "completion_status": result.get("completion_status"),
            "failure_count": len(failures),
            "failures": failures,
            "llm_raw_response_count": result.get("llm_raw_response_count"),
            "trace_tool_call_count": result.get("trace_tool_call_count"),
            "trace_model_requested_tool_call_count": result.get("trace_model_requested_tool_call_count"),
            "trace_backend_recovery_tool_call_count": result.get("trace_backend_recovery_tool_call_count"),
            "trace_tool_output_count": result.get("trace_tool_output_count"),
            "observation_count": result.get("observation_count"),
            "trace_model_requested_tool_names": result.get("trace_model_requested_tool_names") or [],
            "trace_backend_recovery_tool_names": result.get("trace_backend_recovery_tool_names") or [],
            "latest_search_candidate_count": result.get("latest_search_candidate_count"),
            "latest_search_product_like_candidate_count": result.get("latest_search_product_like_candidate_count"),
            "latest_search_rejected_candidate_count": result.get("latest_search_rejected_candidate_count"),
            "search_provider_names": result.get("search_provider_names") or [],
            "search_external_api_called": result.get("search_external_api_called"),
            "fetch_completed_candidate_count": result.get("fetch_completed_candidate_count"),
            "normalized_candidate_count": result.get("normalized_candidate_count"),
            "normalized_with_measurements_count": result.get("normalized_with_measurements_count"),
            "normalized_with_material_count": result.get("normalized_with_material_count"),
            "normalized_with_price_count": result.get("normalized_with_price_count"),
            "normalized_with_size_label_count": result.get("normalized_with_size_label_count"),
            "normalized_size_measurement_row_count": result.get("normalized_size_measurement_row_count"),
            "normalized_measurement_selection_status_counts": result.get("normalized_measurement_selection_status_counts") or {},
            "normalized_target_size_not_found_count": result.get("normalized_target_size_not_found_count"),
            "expected_min_size_measurement_rows": expectations.get("min_size_measurement_rows"),
            "expected_min_matched_target_size": expectations.get("min_matched_target_size"),
            "score_candidate_count": result.get("score_candidate_count"),
            "ranked_match_count": result.get("ranked_match_count"),
            "ranked_size_basis_method_counts": result.get("ranked_size_basis_method_counts") or {},
            "ranked_measurement_selection_status_counts": result.get("ranked_measurement_selection_status_counts") or {},
            "ranked_with_measurement_deltas_count": result.get("ranked_with_measurement_deltas_count"),
            "ranked_with_size_field_results_count": result.get("ranked_with_size_field_results_count"),
            "ranked_size_field_result_count": result.get("ranked_size_field_result_count"),
            "expected_min_ranked_measurement_deltas": expectations.get("min_ranked_measurement_deltas"),
            "expected_min_ranked_size_field_results": expectations.get("min_ranked_size_field_results"),
            "ranked_measurement_comparison_not_applicable_count": result.get("ranked_measurement_comparison_not_applicable_count"),
            "ranked_uncomparable_size_score_over_neutral_count": result.get("ranked_uncomparable_size_score_over_neutral_count"),
            "top_ranked_overall_score": result.get("top_ranked_overall_score"),
            "top_ranked_size_score": result.get("top_ranked_size_score"),
            "top_ranked_size_basis_method": result.get("top_ranked_size_basis_method"),
            "top_ranked_size_observed_measurement_field_count": result.get("top_ranked_size_observed_measurement_field_count"),
            "top_ranked_size_field_result_count": result.get("top_ranked_size_field_result_count"),
            "top_ranked_measurement_selection_status": result.get("top_ranked_measurement_selection_status"),
            "top_ranked_size_measurement_row_count": result.get("top_ranked_size_measurement_row_count"),
            "top_ranked_baseline_precision_status": result.get("top_ranked_baseline_precision_status"),
            "top_ranked_baseline_missing_field_count": result.get("top_ranked_baseline_missing_field_count"),
            "top_ranked_confidence_score": result.get("top_ranked_confidence_score"),
            "report_candidate_audit_reported_count": result.get("report_candidate_audit_reported_count"),
            "report_candidate_audit_excluded_count": result.get("report_candidate_audit_excluded_count"),
            "close_fit_search_attempt_count": result.get("close_fit_search_attempt_count"),
            "close_fit_search_attempt_limit": result.get("close_fit_search_attempt_limit"),
            "close_fit_search_remaining_attempts": result.get("close_fit_search_remaining_attempts"),
            "sdk_event_projected_count": result.get("sdk_event_projected_count"),
            "sdk_event_agent_guidance_count": result.get("sdk_event_agent_guidance_count"),
            "sdk_event_expected_guidance_count": result.get("sdk_event_expected_guidance_count"),
            "client_transcript_message_count": result.get("client_transcript_message_count"),
            "client_transcript_agent_guidance_count": result.get("client_transcript_agent_guidance_count"),
            "client_transcript_expected_guidance_count": result.get("client_transcript_expected_guidance_count"),
            "provider_probe_status": result.get("provider_probe_status"),
            "provider_probe_diagnostic_id": result.get("provider_probe_diagnostic_id"),
            "provider_probe_external_api_called": result.get("provider_probe_external_api_called"),
            "provider_probe_failures": result.get("provider_probe_failures") or [],
            "claim_boundary": (
                "This eval case records FitCheck product-route evidence for the generic development "
                "harness. It does not prove live provider connectivity unless the smoke result "
                "shows external_api_called=true and api/status reported an LLM connection."
            ),
        },
    }


def _export_live_agent_harness_artifacts(
    *,
    trace_artifact: dict[str, Any],
    eval_case_record: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    export_dir = os.getenv("FITCHECK_LIVE_AGENT_EXPORT_DIR", "").strip()
    trace_path_text = os.getenv("FITCHECK_LIVE_AGENT_TRACE_EXPORT_PATH", "").strip()
    eval_case_path_text = os.getenv("FITCHECK_LIVE_AGENT_EVAL_CASE_EXPORT_PATH", "").strip()
    if (
        _truthy(os.getenv("FITCHECK_LIVE_AGENT_PUBLISH_HARNESS"))
        and not export_dir
        and not trace_path_text
        and not eval_case_path_text
    ):
        export_dir = str(Path(__file__).resolve().parents[1] / "harness_outputs" / "live_agent_smoke_export")
    if export_dir:
        safe_run_id = _safe_file_stem(run_id)
        trace_path_text = trace_path_text or str(Path(export_dir) / f"{safe_run_id}-fitcheck-live-agent-trace.json")
        eval_case_path_text = eval_case_path_text or str(Path(export_dir) / f"{safe_run_id}-fitcheck-live-agent-eval-case.json")
    written: dict[str, Any] = {}
    if trace_path_text:
        written["trace_artifact"] = _write_json_file(Path(trace_path_text), trace_artifact)
    if eval_case_path_text:
        written["eval_case_record"] = _write_json_file(Path(eval_case_path_text), eval_case_record)
    return written


def _harness_export_summary(harness_export: dict[str, Any]) -> dict[str, Any]:
    trace_info = (
        harness_export.get("trace_artifact")
        if isinstance(harness_export.get("trace_artifact"), dict)
        else {}
    )
    eval_info = (
        harness_export.get("eval_case_record")
        if isinstance(harness_export.get("eval_case_record"), dict)
        else {}
    )
    return {
        "harness_trace_json": trace_info.get("path"),
        "harness_trace_bytes": trace_info.get("bytes"),
        "harness_trace_digest": trace_info.get("digest"),
        "harness_eval_case_json": eval_info.get("path"),
        "harness_eval_case_bytes": eval_info.get("bytes"),
        "harness_eval_case_digest": eval_info.get("digest"),
        "harness_export_ready": bool(trace_info.get("path") and eval_info.get("path")),
    }


def _publish_live_agent_harness_export_if_requested(
    harness_export: dict[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    if not _truthy(os.getenv("FITCHECK_LIVE_AGENT_PUBLISH_HARNESS")):
        return {}
    trace_info = harness_export.get("trace_artifact") if isinstance(harness_export.get("trace_artifact"), dict) else {}
    eval_info = harness_export.get("eval_case_record") if isinstance(harness_export.get("eval_case_record"), dict) else {}
    trace_path = str(trace_info.get("path") or "").strip()
    eval_case_path = str(eval_info.get("path") or "").strip()
    if not trace_path or not eval_case_path:
        raise SystemExit(
            "FITCHECK_LIVE_AGENT_PUBLISH_HARNESS requires a trace and eval export path. "
            "Set FITCHECK_LIVE_AGENT_EXPORT_DIR or explicit export paths."
        )
    client_id = (
        os.getenv("FITCHECK_LIVE_AGENT_HARNESS_CLIENT_ID")
        or os.getenv("HARNESS_CLIENT_ID")
        or ""
    ).strip()
    if not client_id:
        raise SystemExit("FITCHECK_LIVE_AGENT_PUBLISH_HARNESS requires HARNESS_CLIENT_ID or FITCHECK_LIVE_AGENT_HARNESS_CLIENT_ID.")
    from publish_live_agent_smoke_export import attach_record_result_gate, publish_export

    harness_base_url = (
        os.getenv("FITCHECK_LIVE_AGENT_HARNESS_BASE_URL")
        or os.getenv("HARNESS_BASE_URL")
        or "http://127.0.0.1:8000/api/harness/v1"
    )
    published = publish_export(
        trace_json=Path(trace_path),
        eval_case_json=Path(eval_case_path),
        task_id=os.getenv("FITCHECK_LIVE_AGENT_HARNESS_TASK_ID") or None,
        client_id=client_id,
        base_url=harness_base_url,
        api_key=os.getenv("FITCHECK_LIVE_AGENT_HARNESS_API_KEY") or os.getenv("HARNESS_API_KEY") or None,
        cwd=str(Path(__file__).resolve().parents[1]),
        idempotency_key=(
            os.getenv("FITCHECK_LIVE_AGENT_HARNESS_IDEMPOTENCY_KEY")
            or f"fitcheck-live-smoke-export-{run_id}-from-live-smoke"
        ),
        dry_run=_truthy(os.getenv("FITCHECK_LIVE_AGENT_HARNESS_DRY_RUN")),
        skip_eval_case=False,
    )
    if _truthy(os.getenv("FITCHECK_LIVE_AGENT_HARNESS_RECORD_RESULT")):
        record_task_id = (
            os.getenv("FITCHECK_LIVE_AGENT_HARNESS_RECORD_RESULT_TASK_ID")
            or published.get("task_id")
            or ""
        )
        if not str(record_task_id).strip():
            raise SystemExit("FITCHECK_LIVE_AGENT_HARNESS_RECORD_RESULT requires a publish task id.")
        published = attach_record_result_gate(
            published,
            client_args=argparse.Namespace(
                base_url=harness_base_url,
                client_id=client_id,
                api_key=os.getenv("FITCHECK_LIVE_AGENT_HARNESS_API_KEY") or os.getenv("HARNESS_API_KEY") or None,
                dry_run=_truthy(os.getenv("FITCHECK_LIVE_AGENT_HARNESS_DRY_RUN")),
            ),
            record_task_id=str(record_task_id),
        )
    return published


def _harness_publish_failures(harness_publish: dict[str, Any]) -> list[str]:
    failures = []
    status = harness_publish.get("status")
    if status not in {"completed", "dry_run"}:
        failures.append(f"harness_publish_status:{status or 'missing'}")
    record_gate = (
        harness_publish.get("record_result_gate")
        if isinstance(harness_publish.get("record_result_gate"), dict)
        else {}
    )
    if record_gate and record_gate.get("valid") is not True:
        failures.append("harness_publish_record_result_gate_failed")
        failures.extend(
            f"harness_publish_record_result:{item}"
            for item in record_gate.get("failed_checks") or []
        )
    return failures


def _harness_publish_summary(harness_publish: dict[str, Any]) -> dict[str, Any]:
    failures = _harness_publish_failures(harness_publish)
    readback = (
        harness_publish.get("readback")
        if isinstance(harness_publish.get("readback"), dict)
        else {}
    )
    readback_validation = (
        readback.get("validation")
        if isinstance(readback.get("validation"), dict)
        else {}
    )
    record_result = (
        harness_publish.get("record_result")
        if isinstance(harness_publish.get("record_result"), dict)
        else {}
    )
    record_result_readback = (
        harness_publish.get("record_result_readback")
        if isinstance(harness_publish.get("record_result_readback"), dict)
        else {}
    )
    record_result_validation = (
        record_result_readback.get("validation")
        if isinstance(record_result_readback.get("validation"), dict)
        else {}
    )
    record_result_gate = (
        harness_publish.get("record_result_gate")
        if isinstance(harness_publish.get("record_result_gate"), dict)
        else {}
    )
    return {
        "harness_publish_status": "failed" if failures else "completed",
        "harness_publish_raw_status": harness_publish.get("status"),
        "harness_publish_mode": harness_publish.get("mode"),
        "harness_publish_task_id": harness_publish.get("task_id"),
        "harness_publish_eval_case_id": harness_publish.get("eval_case_id"),
        "harness_publish_trace_json": harness_publish.get("trace_json"),
        "harness_publish_eval_case_json": harness_publish.get("eval_case_json"),
        "harness_publish_failures": failures,
        "harness_publish_readback_valid": readback_validation.get("valid"),
        "harness_publish_readback_failed_checks": readback_validation.get("failed_checks") or [],
        "harness_publish_revalidation_inputs": harness_publish.get("revalidation_inputs") or {},
        "harness_publish_revalidation_cli_args": harness_publish.get("revalidation_cli_args") or [],
        "harness_publish_revalidation_ready": harness_publish.get("revalidation_ready"),
        "harness_publish_revalidation_missing_fields": (
            harness_publish.get("revalidation_missing_fields") or []
        ),
        "harness_publish_record_result_status": record_result.get("status"),
        "harness_publish_record_result_observation_id": record_result.get("observation_id"),
        "harness_publish_record_result_readback_valid": record_result_validation.get("valid"),
        "harness_publish_record_result_readback_failed_checks": (
            record_result_validation.get("failed_checks") or []
        ),
        "harness_publish_record_result_gate_valid": record_result_gate.get("valid"),
        "harness_publish_record_result_gate_failed_checks": (
            record_result_gate.get("failed_checks") or []
        ),
    }


def _probe_harness_provider_if_requested(opener, *, timeout: float, status: dict[str, Any]) -> dict[str, Any]:
    if not _truthy(os.getenv("FITCHECK_LIVE_AGENT_PROBE_PROVIDER")):
        return {}
    client_id = (
        os.getenv("FITCHECK_LIVE_AGENT_HARNESS_CLIENT_ID")
        or os.getenv("HARNESS_CLIENT_ID")
        or ""
    ).strip()
    if not client_id:
        raise SystemExit("FITCHECK_LIVE_AGENT_PROBE_PROVIDER requires HARNESS_CLIENT_ID or FITCHECK_LIVE_AGENT_HARNESS_CLIENT_ID.")
    harness_base_url = (
        os.getenv("FITCHECK_LIVE_AGENT_HARNESS_BASE_URL")
        or os.getenv("HARNESS_BASE_URL")
        or "http://127.0.0.1:8000/api/harness/v1"
    ).rstrip("/") + "/"
    llm_status = status.get("llm") if isinstance(status.get("llm"), dict) else {}
    provider_name = os.getenv("FITCHECK_LIVE_AGENT_PROVIDER_PROBE_PROVIDER", "openai").strip() or "openai"
    api_style = (
        os.getenv("FITCHECK_LIVE_AGENT_PROVIDER_PROBE_API_STYLE")
        or llm_status.get("api_style")
        or ("responses" if provider_name == "openai" else "chat_completions")
    )
    model = (
        os.getenv("FITCHECK_LIVE_AGENT_PROVIDER_PROBE_MODEL")
        or os.getenv("FITCHECK_LLM_MODEL")
        or llm_status.get("model")
    )
    provider = {
        "provider": provider_name,
        "api_style": api_style,
        "base_url": _provider_probe_base_url(provider_name),
        "model": model,
        "api_key_env": _provider_probe_api_key_env(provider_name),
        "supports_tool_calling": _truthy(
            os.getenv("FITCHECK_LIVE_AGENT_PROVIDER_PROBE_SUPPORTS_TOOL_CALLING", "1")
        ),
        "supports_stateful_responses": _truthy(
            os.getenv(
                "FITCHECK_LIVE_AGENT_PROVIDER_PROBE_SUPPORTS_STATEFUL_RESPONSES",
                "1" if api_style == "responses" else "0",
            )
        ),
    }
    probe = _request_json(
        opener,
        "POST",
        urljoin(harness_base_url, "llm/probe"),
        payload={
            "provider": provider,
            "timeout_seconds": int(os.getenv("FITCHECK_LIVE_AGENT_PROVIDER_PROBE_TIMEOUT", "10")),
        },
        timeout=timeout,
        headers={"X-Harness-Client-Id": client_id},
    )
    diagnostic_id = probe.get("diagnostic_id")
    diagnostics = {}
    matched = None
    if diagnostic_id is not None:
        diagnostics = _request_json(
            opener,
            "GET",
            urljoin(harness_base_url, "llm/diagnostics?limit=20"),
            timeout=timeout,
            headers={"X-Harness-Client-Id": client_id},
        )
        for item in diagnostics.get("diagnostics") or []:
            if isinstance(item, dict) and str(item.get("diagnostic_id")) == str(diagnostic_id):
                matched = item
                break
    validation_checks = {
        "probe_completed": probe.get("status") == "completed",
        "provider_request_attempted": probe.get("provider_request_attempted") is True,
        "provider_response_received": probe.get("provider_response_received") is True,
        "external_api_called": probe.get("external_api_called") is True,
        "diagnostic_id_present": diagnostic_id is not None,
        "diagnostic_readback_matched": matched is not None,
    }
    failed_checks = [name for name, passed in validation_checks.items() if passed is not True]
    return {
        **probe,
        "diagnostics_readback": {
            "status": diagnostics.get("status") if diagnostics else None,
            "diagnostic_count": len(diagnostics.get("diagnostics") or []) if diagnostics else 0,
            "matched_diagnostic_id": matched.get("diagnostic_id") if matched else None,
        },
        "validation": {
            "valid": not failed_checks,
            "failed_checks": failed_checks,
            "checks": validation_checks,
        },
        "claim_boundary": (
            "Harness provider probe readback for this live smoke; it proves /models connectivity "
            "for the configured provider profile, not product-route reasoning by itself."
        ),
    }


def _provider_probe_base_url(provider_name: str) -> str | None:
    explicit = os.getenv("FITCHECK_LIVE_AGENT_PROVIDER_PROBE_BASE_URL")
    if explicit:
        return explicit
    if provider_name == "openai":
        return "https://api.openai.com/v1"
    return os.getenv("FITCHECK_LLM_BASE_URL")


def _provider_probe_api_key_env(provider_name: str) -> str | None:
    explicit = os.getenv("FITCHECK_LIVE_AGENT_PROVIDER_PROBE_API_KEY_ENV")
    if explicit:
        return explicit
    if provider_name == "openai":
        return "OPENAI_API_KEY"
    return os.getenv("FITCHECK_LLM_API_KEY_ENV")


def _provider_probe_failures(provider_probe: dict[str, Any]) -> list[str]:
    if not provider_probe:
        return []
    validation = provider_probe.get("validation") if isinstance(provider_probe.get("validation"), dict) else {}
    failed_checks = validation.get("failed_checks") or []
    return [f"provider_probe:{item}" for item in failed_checks]


def _agent_loop_trace_summary(run: dict[str, Any]) -> dict[str, Any]:
    trace = run.get("trace") or []
    observations = (run.get("state") or {}).get("observations") or []
    tool_calls = [step for step in trace if step.get("step_type") == "tool_call"]
    model_requested_tool_calls = [
        step for step in tool_calls if not (step.get("input") or {}).get("backend_recovery")
    ]
    backend_recovery_tool_calls = [
        step for step in tool_calls if (step.get("input") or {}).get("backend_recovery")
    ]
    tool_outputs = [step for step in trace if step.get("step_type") == "tool_output"]
    raw_responses = _raw_responses_from_run(run)
    return {
        "llm_raw_response_count": len(raw_responses),
        "trace_tool_call_count": len(tool_calls),
        "trace_model_requested_tool_call_count": len(model_requested_tool_calls),
        "trace_backend_recovery_tool_call_count": len(backend_recovery_tool_calls),
        "trace_tool_output_count": len(tool_outputs),
        "observation_count": len(observations),
        "trace_tool_names": _unique_trace_step_names(tool_calls),
        "trace_model_requested_tool_names": _unique_trace_step_names(model_requested_tool_calls),
        "trace_backend_recovery_tool_names": _unique_trace_step_names(backend_recovery_tool_calls),
        "trace_tool_output_names": _unique_trace_step_names(tool_outputs),
    }


def _product_evidence_summary(run: dict[str, Any]) -> dict[str, Any]:
    observations = (run.get("state") or {}).get("observations") or []
    search_observations = _completed_observations_for_tool(observations, "search_web_candidates")
    fetch_observations = _observations_for_tool(observations, "fetch_product_page")
    completed_fetch_observations = [
        item for item in fetch_observations if _observation_status(item) == "completed"
    ]
    normalize_observations = _completed_observations_for_tool(observations, "normalize_product_spec")
    score_observations = _completed_observations_for_tool(observations, "score_candidate_match")
    report_observations = _completed_observations_for_tool(observations, "write_purchase_report")

    latest_search_result = _latest_observation_result(search_observations)
    latest_score_result = _latest_observation_result(score_observations)
    latest_report_result = _latest_observation_result(report_observations)
    ranked_matches = latest_score_result.get("ranked_matches") or []
    individual_score_results = _individual_score_results(score_observations)
    candidate_audit = (
        latest_report_result.get("candidate_audit")
        if isinstance(latest_report_result.get("candidate_audit"), dict)
        else {}
    )
    audit_scored_count = _safe_int(candidate_audit.get("scored_candidate_count"))
    ranked_match_count = len(ranked_matches)
    if ranked_match_count == 0:
        ranked_match_count = audit_scored_count or len(individual_score_results)
    close_fit_budget = (
        candidate_audit.get("close_fit_search_budget")
        if isinstance(candidate_audit.get("close_fit_search_budget"), dict)
        else latest_score_result.get("close_fit_search_budget")
        if isinstance(latest_score_result.get("close_fit_search_budget"), dict)
        else {}
    )
    normalized_specs = [
        (item.get("result") or {}).get("normalized_spec") or {}
        for item in normalize_observations
    ]
    ranked_source_matches = [
        item for item in (ranked_matches or individual_score_results) if isinstance(item, dict)
    ]
    top_match = ranked_matches[0] if ranked_matches and isinstance(ranked_matches[0], dict) else {}
    if not top_match and individual_score_results:
        top_match = max(
            individual_score_results,
            key=lambda item: _safe_int((item.get("score") or {}).get("overall")) or -1,
        )
    top_score = top_match.get("score") if isinstance(top_match.get("score"), dict) else {}
    top_size_basis = _size_score_basis(top_match)
    top_size_field_results = _size_field_results(top_match)
    top_measurement_selection = _measurement_selection(top_match)
    top_coverage = top_match.get("evidence_coverage") if isinstance(top_match.get("evidence_coverage"), dict) else {}
    normalized_selection_status_counts = _normalized_measurement_selection_status_counts(normalized_specs)
    ranked_size_basis_method_counts = _ranked_size_basis_method_counts(ranked_source_matches)

    return {
        "search_attempt_count": len(search_observations),
        "search_provider_names": _unique_result_values(search_observations, "provider"),
        "search_external_api_called": any(
            (item.get("result") or {}).get("external_api_called") is True for item in search_observations
        ),
        "latest_search_candidate_count": _safe_int(latest_search_result.get("candidate_count")) or 0,
        "latest_search_product_like_candidate_count": (
            _safe_int(latest_search_result.get("product_like_candidate_count")) or 0
        ),
        "latest_search_rejected_candidate_count": (
            _safe_int(latest_search_result.get("rejected_candidate_count")) or 0
        ),
        "search_candidate_keys": _candidate_keys_from_search_result(latest_search_result),
        "fetch_attempt_count": len(fetch_observations),
        "fetch_completed_candidate_count": len(_unique_candidate_keys_from_observations(completed_fetch_observations)),
        "fetch_completed_candidate_keys": _unique_candidate_keys_from_observations(completed_fetch_observations)[:10],
        "normalized_candidate_count": len(_unique_candidate_keys_from_observations(normalize_observations)),
        "normalized_candidate_keys": _unique_candidate_keys_from_observations(normalize_observations)[:10],
        "normalized_with_measurements_count": len(
            [spec for spec in normalized_specs if isinstance(spec.get("measurements"), dict) and spec.get("measurements")]
        ),
        "normalized_with_material_count": len(
            [spec for spec in normalized_specs if isinstance(spec.get("material"), dict) and spec.get("material")]
        ),
        "normalized_with_price_count": len([spec for spec in normalized_specs if spec.get("price") is not None]),
        "normalized_with_size_label_count": len([spec for spec in normalized_specs if spec.get("size_label")]),
        "normalized_size_measurement_row_count": sum(_size_measurement_row_count(spec) for spec in normalized_specs),
        "normalized_measurement_selection_status_counts": normalized_selection_status_counts,
        "normalized_target_size_not_found_count": normalized_selection_status_counts.get("target_size_not_found", 0),
        "score_candidate_count": _safe_int(latest_score_result.get("candidate_count")) or audit_scored_count or len(individual_score_results),
        "score_active_candidate_count": _safe_int(latest_score_result.get("active_candidate_count")) or len(individual_score_results),
        "score_deferred_candidate_count": _safe_int(latest_score_result.get("deferred_candidate_count")) or 0,
        "ranked_match_count": ranked_match_count,
        "ranked_size_basis_method_counts": ranked_size_basis_method_counts,
        "ranked_measurement_selection_status_counts": _ranked_measurement_selection_status_counts(ranked_source_matches),
        "ranked_with_measurement_deltas_count": len(
            [
                item
                for item in ranked_source_matches
                if isinstance(item.get("measurement_deltas"), dict) and item.get("measurement_deltas")
            ]
        ),
        "ranked_with_size_field_results_count": len([item for item in ranked_source_matches if _size_field_results(item)]),
        "ranked_size_field_result_count": sum(len(_size_field_results(item)) for item in ranked_source_matches),
        "ranked_measurement_comparison_not_applicable_count": ranked_size_basis_method_counts.get(
            "measurement_comparison_not_applicable",
            0,
        ),
        "ranked_uncomparable_size_score_over_neutral_count": len(
            [
                item
                for item in ranked_source_matches
                if _size_score_basis(item).get("method") == "measurement_comparison_not_applicable"
                and (_safe_int(((item.get("score") or {}) if isinstance(item.get("score"), dict) else {}).get("size")) or 0) > 55
            ]
        ),
        "top_ranked_candidate_key": top_match.get("candidate_key"),
        "top_ranked_overall_score": top_score.get("overall"),
        "top_ranked_size_score": top_score.get("size"),
        "top_ranked_material_score": top_score.get("material"),
        "top_ranked_confidence_score": top_score.get("confidence"),
        "top_ranked_size_basis_method": top_size_basis.get("method"),
        "top_ranked_size_observed_measurement_field_count": len(top_size_basis.get("observed_measurement_fields") or []),
        "top_ranked_size_field_result_count": len(top_size_field_results),
        "top_ranked_measurement_selection_status": top_measurement_selection.get("status"),
        "top_ranked_size_measurement_row_count": _safe_int(top_coverage.get("size_measurement_row_count")),
        "top_ranked_baseline_precision_status": top_coverage.get("baseline_precision_status"),
        "top_ranked_baseline_missing_field_count": len(top_coverage.get("baseline_missing_fields") or []),
        "report_type": latest_report_result.get("report_type"),
        "report_candidate_audit_searched_count": _safe_int(candidate_audit.get("searched_candidate_count")),
        "report_candidate_audit_scored_count": _safe_int(candidate_audit.get("scored_candidate_count")),
        "report_candidate_audit_reported_count": _safe_int(candidate_audit.get("reported_candidate_count")),
        "report_candidate_audit_excluded_count": _safe_int(candidate_audit.get("excluded_candidate_count")),
        "close_fit_search_attempt_count": _safe_int(close_fit_budget.get("attempt_count")),
        "close_fit_search_attempt_limit": _safe_int(close_fit_budget.get("attempt_limit")),
        "close_fit_search_remaining_attempts": _safe_int(close_fit_budget.get("remaining_attempts")),
    }


def _individual_score_results(score_observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_candidate_key: dict[str, dict[str, Any]] = {}
    for observation in score_observations:
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        candidate_key = str(
            result.get("candidate_key")
            or (observation.get("arguments") or {}).get("candidate_key")
            or ""
        ).strip()
        if not candidate_key or candidate_key.lower() in {"all", "*", "__all__"}:
            continue
        if result.get("status") != "completed" or result.get("score_deferred"):
            continue
        if not isinstance(result.get("score"), dict):
            continue
        by_candidate_key[candidate_key] = {**result, "candidate_key": candidate_key}
    return list(by_candidate_key.values())


def _size_score_basis(match: dict[str, Any]) -> dict[str, Any]:
    score_basis = match.get("score_basis") if isinstance(match.get("score_basis"), dict) else {}
    basis = score_basis.get("size") if isinstance(score_basis.get("size"), dict) else {}
    return basis


def _size_field_results(match: dict[str, Any]) -> list[dict[str, Any]]:
    score_basis = match.get("score_basis") if isinstance(match.get("score_basis"), dict) else {}
    results = score_basis.get("size_field_results")
    return [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []


def _measurement_selection(item: dict[str, Any]) -> dict[str, Any]:
    selection = item.get("measurement_selection") if isinstance(item.get("measurement_selection"), dict) else {}
    if selection:
        return selection
    coverage = item.get("evidence_coverage") if isinstance(item.get("evidence_coverage"), dict) else {}
    return coverage.get("measurement_selection") if isinstance(coverage.get("measurement_selection"), dict) else {}


def _size_measurement_row_count(spec: dict[str, Any]) -> int:
    rows = spec.get("size_measurement_rows")
    if isinstance(rows, list):
        return len(rows)
    row_count = _safe_int(spec.get("size_measurement_row_count"))
    return row_count or 0


def _normalized_measurement_selection_status_counts(specs: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for spec in specs:
        selection = spec.get("measurement_selection") if isinstance(spec.get("measurement_selection"), dict) else {}
        status = str(selection.get("status") or "missing")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _ranked_measurement_selection_status_counts(matches: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for match in matches:
        status = str(_measurement_selection(match).get("status") or "missing")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _ranked_size_basis_method_counts(matches: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for match in matches:
        method = str(_size_score_basis(match).get("method") or "missing")
        counts[method] = counts.get(method, 0) + 1
    return counts


def _observations_for_tool(observations: list[dict[str, Any]], tool_name: str) -> list[dict[str, Any]]:
    return [item for item in observations if item.get("tool") == tool_name]


def _completed_observations_for_tool(observations: list[dict[str, Any]], tool_name: str) -> list[dict[str, Any]]:
    return [
        item
        for item in _observations_for_tool(observations, tool_name)
        if _observation_status(item) == "completed"
    ]


def _observation_status(observation: dict[str, Any]) -> str:
    result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
    return str(observation.get("result_status") or result.get("status") or "").strip()


def _latest_observation_result(observations: list[dict[str, Any]]) -> dict[str, Any]:
    if not observations:
        return {}
    result = observations[-1].get("result")
    return result if isinstance(result, dict) else {}


def _candidate_keys_from_search_result(result: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for candidate in result.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        key = str(candidate.get("candidate_key") or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys[:10]


def _unique_candidate_keys_from_observations(observations: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    for item in observations:
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        key = str(result.get("candidate_key") or (item.get("arguments") or {}).get("candidate_key") or "").strip()
        if key and key.lower() not in {"all", "*", "__all__"} and key not in keys:
            keys.append(key)
    return keys


def _unique_result_values(observations: list[dict[str, Any]], field: str) -> list[str]:
    values: list[str] = []
    for item in observations:
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        value = str(result.get(field) or item.get(field) or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _unique_trace_step_names(steps: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for step in steps:
        name = str(step.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _provider_probe_artifact_summary(provider_probe: Any) -> dict[str, Any]:
    if not isinstance(provider_probe, dict) or not provider_probe:
        return {}
    provider = provider_probe.get("provider") if isinstance(provider_probe.get("provider"), dict) else {}
    validation = provider_probe.get("validation") if isinstance(provider_probe.get("validation"), dict) else {}
    return {
        "status": provider_probe.get("status"),
        "diagnostic_id": provider_probe.get("diagnostic_id"),
        "provider": provider.get("provider"),
        "api_style": provider.get("api_style"),
        "model": provider.get("model"),
        "api_key_env": provider.get("api_key_env"),
        "endpoint": provider_probe.get("endpoint"),
        "http_status": provider_probe.get("http_status"),
        "external_api_called": provider_probe.get("external_api_called"),
        "validation": validation,
    }


def _trace_ingest_items_from_run(run: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for step in run.get("trace") or []:
        step_type = step.get("step_type")
        name = str(step.get("name") or "").strip()
        call_id = _trace_step_call_id(step)
        if step_type == "tool_call":
            items.append(
                _drop_empty(
                    {
                        "type": "function_call",
                        "name": name,
                        "call_id": call_id,
                        "arguments": step.get("input") or {},
                        "source_trace_status": step.get("status"),
                    }
                )
            )
        elif step_type == "tool_output":
            items.append(
                _drop_empty(
                    {
                        "type": "function_call_output",
                        "name": name,
                        "call_id": call_id,
                        "output": step.get("output") or {},
                        "source_trace_status": step.get("status"),
                    }
                )
            )
        elif step_type == "tool_guidance":
            output = step.get("output") if isinstance(step.get("output"), dict) else {}
            items.append(
                _drop_empty(
                    {
                        "type": "agent_guardrail",
                        "name": name or "backend_runtime_guidance",
                        "reason": output.get("reason"),
                        "status": step.get("status"),
                        "result": output,
                    }
                )
            )
        elif step_type == "guardrail":
            items.append(
                _drop_empty(
                    {
                        "type": "agent_guardrail",
                        "name": name,
                        "status": step.get("status"),
                        "result": step.get("output") or {},
                    }
                )
            )
    return items


def _raw_responses_from_run(run: dict[str, Any]) -> list[dict[str, Any]]:
    responses: list[dict[str, Any]] = []
    llm = run.get("llm") if isinstance(run.get("llm"), dict) else {}
    for index, step in enumerate(run.get("trace") or [], start=1):
        if step.get("step_type") != "llm_response":
            continue
        output = step.get("output") if isinstance(step.get("output"), dict) else {}
        responses.append(
            _drop_empty(
                {
                    "id": output.get("response_id") or output.get("id") or f"llm_response_{index}",
                    "response_id": output.get("response_id") or output.get("id"),
                    "request_id": output.get("provider_request_id") or output.get("request_id"),
                    "model": output.get("model") or llm.get("model"),
                    "usage": output.get("usage"),
                    "output": output.get("output") or output.get("items") or [],
                    "status": step.get("status"),
                }
            )
        )
    return responses


def _stream_events_from_sdk_events(sdk_events: dict[str, Any] | None) -> list[dict[str, Any]]:
    stream_events: list[dict[str, Any]] = []
    for index, event in enumerate((sdk_events or {}).get("events") or [], start=1):
        if not isinstance(event, dict):
            continue
        stream_events.append(
            {
                "event": event.get("type") or f"sdk_event_{index}",
                "item": event,
                "raw_event": event,
            }
        )
    return stream_events


def _agent_questions_from_sdk_events(sdk_events: dict[str, Any] | None) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for event in (sdk_events or {}).get("events") or []:
        if not isinstance(event, dict):
            continue
        if event.get("type") == "agent_question":
            questions.append(event)
    return questions


def _agent_guidance_from_sdk_events(sdk_events: dict[str, Any] | None) -> list[dict[str, Any]]:
    guidance: list[dict[str, Any]] = []
    for event in (sdk_events or {}).get("events") or []:
        if not isinstance(event, dict):
            continue
        if event.get("type") == "agent_guidance":
            guidance.append(event)
    return guidance


def _final_output_from_run(run: dict[str, Any]) -> dict[str, Any] | None:
    final_report = run.get("final_report") if isinstance(run.get("final_report"), dict) else {}
    if not final_report:
        return None
    return {
        "summary": final_report.get("summary"),
        "grounded": final_report.get("grounded"),
        "grounding": final_report.get("grounding"),
        "grounding_validation": final_report.get("grounding_validation"),
        "report_type": final_report.get("report_type"),
    }


def _trace_step_call_id(step: dict[str, Any]) -> str | None:
    for key in ["input", "output"]:
        value = step.get(key)
        if isinstance(value, dict) and value.get("call_id"):
            return str(value.get("call_id"))
    if step.get("call_id"):
        return str(step.get("call_id"))
    return None


def _live_agent_eval_failure_label(result: dict[str, Any]) -> str:
    failures = list(result.get("failures") or [])
    if failures:
        return str(failures[0])
    status = str((result.get("agent_run") or {}).get("status") or result.get("expected_status") or "unknown")
    return f"regression_seed_{status}"


def _live_agent_verifier_command(expected_status: str, expectations: dict[str, Any]) -> str:
    env = {
        "FITCHECK_RUN_LIVE_AGENT_SMOKE": "1",
        "FITCHECK_LIVE_AGENT_EXPECT_STATUS": expected_status,
        "FITCHECK_LIVE_AGENT_EXPECT_MISSING_FIELD": expectations.get("missing_field") or "",
        "FITCHECK_LIVE_AGENT_EXPECT_RECOVERY_REASON": expectations.get("recovery_reason") or "",
        "FITCHECK_LIVE_AGENT_EXPECT_BACKEND_RECOVERY_TOOLS": ",".join(expectations.get("backend_recovery_tools") or []),
        "FITCHECK_LIVE_AGENT_EXPECT_MIN_SIZE_MEASUREMENT_ROWS": str(
            expectations.get("min_size_measurement_rows") or ""
        ),
        "FITCHECK_LIVE_AGENT_EXPECT_MIN_MATCHED_TARGET_SIZE": str(
            expectations.get("min_matched_target_size") or ""
        ),
        "FITCHECK_LIVE_AGENT_EXPECT_MIN_RANKED_MEASUREMENT_DELTAS": str(
            expectations.get("min_ranked_measurement_deltas") or ""
        ),
        "FITCHECK_LIVE_AGENT_EXPECT_MIN_RANKED_SIZE_FIELD_RESULTS": str(
            expectations.get("min_ranked_size_field_results") or ""
        ),
    }
    env_parts = [f"{key}={shlex.quote(value)}" for key, value in env.items() if value]
    return f"cd backend_harness && {' '.join(env_parts)} ../.venv/bin/python -B verify_live_agent_smoke.py"


def _write_json_file(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    content = (encoded + "\n").encode("utf-8")
    path.write_bytes(content)
    return {
        "path": str(path),
        "bytes": len(content),
        "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
    }


def _safe_file_stem(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    return cleaned.strip("_") or "fitcheck_live_agent"


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


def _live_agent_expectations() -> dict[str, Any]:
    return {
        "missing_field": os.getenv("FITCHECK_LIVE_AGENT_EXPECT_MISSING_FIELD", "").strip(),
        "recovery_reason": os.getenv("FITCHECK_LIVE_AGENT_EXPECT_RECOVERY_REASON", "").strip(),
        "backend_recovery_tools": _csv_env("FITCHECK_LIVE_AGENT_EXPECT_BACKEND_RECOVERY_TOOLS"),
        "min_search_candidates": _int_env("FITCHECK_LIVE_AGENT_EXPECT_MIN_SEARCH_CANDIDATES", 2),
        "min_ranked_matches": _int_env("FITCHECK_LIVE_AGENT_EXPECT_MIN_RANKED_MATCHES", 2),
        "min_normalized_candidates": _int_env("FITCHECK_LIVE_AGENT_EXPECT_MIN_NORMALIZED_CANDIDATES", 1),
        "min_reported_candidates": _int_env("FITCHECK_LIVE_AGENT_EXPECT_MIN_REPORTED_CANDIDATES", 1),
        "min_size_measurement_rows": _int_env("FITCHECK_LIVE_AGENT_EXPECT_MIN_SIZE_MEASUREMENT_ROWS", 0),
        "min_matched_target_size": _int_env("FITCHECK_LIVE_AGENT_EXPECT_MIN_MATCHED_TARGET_SIZE", 0),
        "min_ranked_measurement_deltas": _int_env("FITCHECK_LIVE_AGENT_EXPECT_MIN_RANKED_MEASUREMENT_DELTAS", 0),
        "min_ranked_size_field_results": _int_env("FITCHECK_LIVE_AGENT_EXPECT_MIN_RANKED_SIZE_FIELD_RESULTS", 0),
    }


def _sdk_event_projection_failures(
    run: dict[str, Any],
    sdk_events: dict[str, Any],
    expected_status: str = "completed",
    expectations: dict[str, Any] | None = None,
) -> list[str]:
    expectations = expectations or {}
    failures: list[str] = []
    if sdk_events.get("status") != "completed":
        failures.append(f"status:{sdk_events.get('status')}")
        return failures
    if sdk_events.get("run_id") != run.get("run_id"):
        failures.append("run_id_mismatch")
    if not any(item.get("type") == "tool_progress" for item in sdk_events.get("events") or []):
        failures.append("tool_progress_missing")
    if sdk_events.get("projected_event_count") != len(sdk_events.get("events") or []):
        failures.append("projected_event_count_mismatch")
    if sdk_events.get("cursor_policy", {}).get("cursor_basis") != "raw_source_event_seq":
        failures.append("cursor_policy_basis_invalid")
    if sdk_events.get("cursor_policy", {}).get("cursor_semantics") != "exclusive":
        failures.append("cursor_policy_semantics_invalid")
    if run.get("status") != expected_status:
        failures.append(f"run_status:{run.get('status')}")
        return failures
    if run.get("status") != "completed":
        if expected_status == "waiting_for_user":
            question_events = [
                item
                for item in sdk_events.get("events") or []
                if item.get("type") == "agent_question" and item.get("subtype") == "missing_info_request"
            ]
            if not question_events:
                failures.append("missing_info_request_missing")
            elif _safe_int(question_events[-1].get("question_count")) <= 0:
                failures.append("missing_info_question_count_empty")
            expected_field = str(expectations.get("missing_field") or "")
            if expected_field and not _question_events_include_field(question_events, expected_field):
                failures.append(f"missing_info_field_missing:{expected_field}")
            expected_reason = str(expectations.get("recovery_reason") or "")
            if expected_reason and not any(
                item.get("type") == "agent_guidance" and item.get("reason") == expected_reason
                for item in sdk_events.get("events") or []
            ):
                failures.append(f"agent_guidance_reason_missing:{expected_reason}")
            if sdk_events.get("final_output_included") is True:
                failures.append("final_output_unexpected_for_waiting")
            if not (run.get("state") or {}).get("missing_info"):
                failures.append("run_missing_info_empty")
        return failures
    if _safe_int(sdk_events.get("terminal_event_seq")) != len(run.get("trace") or []):
        failures.append("terminal_event_seq_mismatch")
    if sdk_events.get("terminal_event_included") is not True:
        failures.append("terminal_event_not_included")
    if sdk_events.get("final_output_included") is not True:
        failures.append("final_output_flag_not_included")
    final_outputs = [item for item in sdk_events.get("events") or [] if item.get("type") == "final_output"]
    if not final_outputs:
        failures.append("final_output_missing")
        return failures
    final_output = final_outputs[-1]
    grounding = final_output.get("grounding_validation") or {}
    if final_output.get("grounded") is not True:
        failures.append("final_output_not_grounded")
    if grounding.get("valid") is not True:
        failures.append(f"final_output_grounding_invalid:{grounding.get('reason')}")
    for field in [
        "suspicious_numbers",
        "suspicious_urls",
        "suspicious_material_claims",
        "suspicious_score_claims",
        "suspicious_identity_claims",
        "suspicious_style_claims",
        "suspicious_fit_claims",
        "suspicious_evidence_claims",
    ]:
        if field not in grounding:
            failures.append(f"final_output_{field}_missing")
        elif grounding.get(field):
            failures.append(f"final_output_{field}_not_empty")
    citation_count = _safe_int(final_output.get("citation_badge_count"))
    if citation_count is None:
        failures.append("final_output_citation_count_missing")
    elif citation_count <= 0:
        failures.append("final_output_citation_count_empty")
    if final_output.get("source_endpoints", {}).get("report") is None:
        failures.append("final_output_report_endpoint_missing")
    if final_output.get("source_endpoints", {}).get("evidence") is None:
        failures.append("final_output_evidence_endpoint_missing")
    summary = str(final_output.get("summary") or final_output.get("final_output") or "")
    for failure in _user_visible_report_text_failures(summary):
        failures.append(f"final_output_user_text_quality:{failure}")
    return failures


def _sdk_terminal_delta_failures(
    run: dict[str, Any],
    sdk_events: dict[str, Any],
    expected_next_after: int,
    expected_status: str = "completed",
    expectations: dict[str, Any] | None = None,
) -> list[str]:
    expectations = expectations or {}
    failures: list[str] = []
    if sdk_events.get("status") != "completed":
        return [f"status:{sdk_events.get('status')}"]
    if sdk_events.get("run_id") != run.get("run_id"):
        failures.append("run_id_mismatch")
    if _safe_int(sdk_events.get("raw_event_count")) != 1:
        failures.append(f"raw_event_count:{sdk_events.get('raw_event_count')}")
    if _safe_int(sdk_events.get("next_after")) != expected_next_after:
        failures.append(f"next_after:{sdk_events.get('next_after')}")
    if sdk_events.get("terminal_event_included") is not True:
        failures.append("terminal_event_not_included")
    if run.get("status") != expected_status:
        failures.append(f"run_status:{run.get('status')}")
    if run.get("status") == "completed":
        if sdk_events.get("final_output_included") is not True:
            failures.append("final_output_flag_not_included")
        if not any(item.get("type") == "final_output" for item in sdk_events.get("events") or []):
            failures.append("final_output_missing")
    elif expected_status == "waiting_for_user":
        question_events = [
            item
            for item in sdk_events.get("events") or []
            if item.get("type") == "agent_question" and item.get("subtype") == "missing_info_request"
        ]
        if not question_events:
            failures.append("missing_info_request_missing")
        expected_field = str(expectations.get("missing_field") or "")
        if expected_field and not _question_events_include_field(question_events, expected_field):
            failures.append(f"missing_info_field_missing:{expected_field}")
        if sdk_events.get("final_output_included") is True:
            failures.append("final_output_unexpected_for_waiting")
    return failures


def _client_transcript_failures(
    run: dict[str, Any],
    transcript: dict[str, Any],
    expected_status: str = "completed",
    expectations: dict[str, Any] | None = None,
) -> list[str]:
    expectations = expectations or {}
    failures: list[str] = []
    if transcript.get("status") != "completed":
        return [f"status:{transcript.get('status')}"]
    if transcript.get("run_id") != run.get("run_id"):
        failures.append("run_id_mismatch")
    if transcript.get("run_status") != run.get("status"):
        failures.append(f"run_status_mismatch:{transcript.get('run_status')}:{run.get('status')}")
    if transcript.get("run_status") != expected_status:
        failures.append(f"run_status:{transcript.get('run_status')}")
    if not (transcript.get("messages") or []):
        failures.append("messages_missing")
    source_endpoints = transcript.get("source_endpoints") if isinstance(transcript.get("source_endpoints"), dict) else {}
    if not source_endpoints.get("sdk_events"):
        failures.append("sdk_events_endpoint_missing")
    if not source_endpoints.get("client_transcript"):
        failures.append("client_transcript_endpoint_missing")
    if expected_status == "waiting_for_user":
        question_messages = _client_transcript_question_messages(transcript)
        if not question_messages:
            failures.append("missing_info_request_missing")
        expected_field = str(expectations.get("missing_field") or "")
        if expected_field and not _question_events_include_field(question_messages, expected_field):
            failures.append(f"missing_info_field_missing:{expected_field}")
        expected_reason = str(expectations.get("recovery_reason") or "")
        if expected_reason and not any(
            item.get("type") == "agent_guidance" and item.get("reason") == expected_reason
            for item in transcript.get("messages") or []
        ):
            failures.append(f"agent_guidance_reason_missing:{expected_reason}")
        if any(item.get("type") == "streamlined_text" and item.get("role") == "assistant" for item in transcript.get("messages") or []):
            failures.append("final_text_unexpected_for_waiting")
    return failures


def _client_transcript_question_messages(transcript: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [
        item
        for item in (transcript or {}).get("messages") or []
        if item.get("type") == "agent_question" and item.get("subtype") == "missing_info_request"
    ]


def _client_transcript_question_count(transcript: dict[str, Any] | None) -> int:
    return len(_client_transcript_question_messages(transcript))


def _client_transcript_guidance_count(transcript: dict[str, Any] | None, *, reason: str = "") -> int:
    return len(
        [
            item
            for item in (transcript or {}).get("messages") or []
            if item.get("type") == "agent_guidance"
            and (not reason or item.get("reason") == reason)
        ]
    )


def _sdk_final_output_count(sdk_events: dict[str, Any] | None) -> int:
    return len([item for item in (sdk_events or {}).get("events") or [] if item.get("type") == "final_output"])


def _sdk_final_output_grounded(sdk_events: dict[str, Any] | None) -> bool | None:
    final_outputs = [item for item in (sdk_events or {}).get("events") or [] if item.get("type") == "final_output"]
    if not final_outputs:
        return None
    return final_outputs[-1].get("grounded")


def _sdk_final_output_citation_badge_count(sdk_events: dict[str, Any] | None) -> int | None:
    final_outputs = [item for item in (sdk_events or {}).get("events") or [] if item.get("type") == "final_output"]
    if not final_outputs:
        return None
    return _safe_int(final_outputs[-1].get("citation_badge_count"))


def _sdk_final_output_suspicious_claim_counts(sdk_events: dict[str, Any] | None) -> dict[str, int] | None:
    final_outputs = [item for item in (sdk_events or {}).get("events") or [] if item.get("type") == "final_output"]
    if not final_outputs:
        return None
    grounding = final_outputs[-1].get("grounding_validation") or {}
    if not isinstance(grounding, dict):
        return None
    return {
        field: len(grounding.get(field) or [])
        for field in [
            "suspicious_numbers",
            "suspicious_urls",
            "suspicious_material_claims",
            "suspicious_score_claims",
            "suspicious_identity_claims",
            "suspicious_style_claims",
            "suspicious_fit_claims",
            "suspicious_evidence_claims",
        ]
    }


def _sdk_final_output_user_text_failures(sdk_events: dict[str, Any] | None) -> list[str] | None:
    final_outputs = [item for item in (sdk_events or {}).get("events") or [] if item.get("type") == "final_output"]
    if not final_outputs:
        return None
    summary = str(final_outputs[-1].get("summary") or final_outputs[-1].get("final_output") or "")
    return _user_visible_report_text_failures(summary)


def _sdk_missing_info_request_count(sdk_events: dict[str, Any] | None) -> int:
    return len(
        [
            item
            for item in (sdk_events or {}).get("events") or []
            if item.get("type") == "agent_question" and item.get("subtype") == "missing_info_request"
        ]
    )


def _sdk_agent_guidance_count(sdk_events: dict[str, Any] | None, *, reason: str = "") -> int:
    return len(
        [
            item
            for item in (sdk_events or {}).get("events") or []
            if item.get("type") == "agent_guidance"
            and (not reason or item.get("reason") == reason)
        ]
    )


def _question_events_include_field(question_events: list[dict[str, Any]], field: str) -> bool:
    for event in question_events:
        for question in event.get("questions") or []:
            if question.get("field") == field:
                return True
    return False


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _live_agent_boundary_failures(run: dict[str, Any]) -> list[str]:
    failures = []
    if not run.get("run_id"):
        failures.append("agent_run_not_created")
    if run.get("status") == "configuration_required":
        failures.append("agent_route_returned_configuration_required")
    if run.get("llm", {}).get("external_api_called") is not True:
        failures.append("external_api_called_not_recorded")
    trace_summary = _agent_loop_trace_summary(run)
    if (_safe_int(trace_summary.get("llm_raw_response_count")) or 0) <= 0:
        failures.append("llm_response_trace_missing")
    if (_safe_int(trace_summary.get("trace_model_requested_tool_call_count")) or 0) <= 0:
        failures.append("model_selected_tool_call_missing")
    if (_safe_int(trace_summary.get("trace_tool_output_count")) or 0) <= 0:
        failures.append("backend_tool_output_trace_missing")
    if (_safe_int(trace_summary.get("observation_count")) or 0) <= 0:
        failures.append("backend_tool_observation_missing")
    failures.extend(_call_id_correlation_failures(run))
    return failures


def _call_id_correlation_count(run: dict[str, Any]) -> int:
    return sum(1 for item in _call_id_correlations(run) if item["correlated"])


def _call_id_correlation_failures(run: dict[str, Any]) -> list[str]:
    correlations = _call_id_correlations(run)
    if not correlations:
        return ["tool_call_id_correlation_missing"]
    failures: list[str] = []
    for item in correlations:
        if not item["correlated"]:
            failures.append(
                "tool_call_id_correlation_failed:"
                f"{item['tool']}:{item['call_id'] or 'missing_call_id'}"
            )
    return failures


def _call_id_correlations(run: dict[str, Any]) -> list[dict[str, Any]]:
    trace = run.get("trace") or []
    observations = run.get("state", {}).get("observations") or []
    tool_call_ids = {
        (step.get("name"), (step.get("input") or {}).get("call_id"))
        for step in trace
        if step.get("step_type") == "tool_call"
    }
    tool_output_ids = {
        (step.get("name"), (step.get("output") or {}).get("call_id"))
        for step in trace
        if step.get("step_type") == "tool_output"
    }
    correlations: list[dict[str, Any]] = []
    for observation in observations:
        call_id = observation.get("call_id")
        has_call_id = isinstance(call_id, str) and bool(call_id.strip())
        key = (observation.get("tool"), call_id)
        correlations.append(
            {
                "tool": observation.get("tool"),
                "call_id": call_id,
                "has_call_id": has_call_id,
                "has_tool_call_trace": key in tool_call_ids,
                "has_tool_output_trace": key in tool_output_ids,
                "correlated": has_call_id and key in tool_call_ids and key in tool_output_ids,
            }
        )
    return correlations


def _assert_call_id_correlation_helper_contract() -> None:
    valid_run = {
        "trace": [
            {
                "step_type": "tool_call",
                "name": "search_web_candidates",
                "input": {"call_id": "call_valid"},
            },
            {
                "step_type": "tool_output",
                "name": "search_web_candidates",
                "output": {"call_id": "call_valid"},
            },
        ],
        "state": {
            "observations": [
                {"tool": "search_web_candidates", "call_id": "call_valid"}
            ]
        },
    }
    missing_id_run = {
        "trace": [
            {
                "step_type": "tool_call",
                "name": "search_web_candidates",
                "input": {},
            },
            {
                "step_type": "tool_output",
                "name": "search_web_candidates",
                "output": {},
            },
        ],
        "state": {
            "observations": [
                {"tool": "search_web_candidates", "call_id": None}
            ]
        },
    }
    mismatched_run = {
        "trace": [
            {
                "step_type": "tool_call",
                "name": "search_web_candidates",
                "input": {"call_id": "call_a"},
            },
            {
                "step_type": "tool_output",
                "name": "search_web_candidates",
                "output": {"call_id": "call_b"},
            },
        ],
        "state": {
            "observations": [
                {"tool": "search_web_candidates", "call_id": "call_a"}
            ]
        },
    }
    if _call_id_correlation_failures(valid_run):
        raise SystemExit("call_id correlation helper rejected a valid correlated run")
    if not _call_id_correlation_failures(missing_id_run):
        raise SystemExit("call_id correlation helper accepted a missing call_id")
    if not _call_id_correlation_failures(mismatched_run):
        raise SystemExit("call_id correlation helper accepted a mismatched call_id")

    recovery_expectations = {
        "missing_field": "baseline_evidence",
        "recovery_reason": "baseline_evidence_recovery_required_before_report",
        "backend_recovery_tools": ["enrich_closet_item_from_web", "score_candidate_match"],
    }
    waiting_after_recovery_run = {
        "run_id": "run_waiting_after_recovery",
        "status": "waiting_for_user",
        "trace": [
            {
                "step_type": "tool_guidance",
                "name": "continue_candidate_enrichment",
                "output": {"reason": "baseline_evidence_recovery_required_before_report"},
            },
            {
                "step_type": "backend_recovery",
                "name": "execute_guidance_suggestions_after_model_no_tool_call",
                "input": {"reason": "baseline_evidence_recovery_required_before_report"},
            },
            {
                "step_type": "tool_call",
                "name": "enrich_closet_item_from_web",
                "input": {
                    "backend_recovery": True,
                    "reason": "baseline_evidence_recovery_required_before_report",
                },
            },
            {
                "step_type": "tool_call",
                "name": "score_candidate_match",
                "input": {
                    "backend_recovery": True,
                    "reason": "rescore_after_backend_enrichment_recovery",
                },
            },
        ],
        "state": {"missing_info": [{"field": "baseline_evidence"}]},
    }
    waiting_failures = _live_agent_completion_failures(
        waiting_after_recovery_run,
        "waiting_for_user",
        recovery_expectations,
    )
    if waiting_failures:
        raise SystemExit(f"waiting recovery helper rejected a valid recovery path: {waiting_failures}")
    waiting_without_recovery_run = {
        "run_id": "run_waiting_without_recovery",
        "status": "waiting_for_user",
        "trace": [],
        "state": {"missing_info": [{"field": "baseline_evidence"}]},
    }
    if not _live_agent_completion_failures(
        waiting_without_recovery_run,
        "waiting_for_user",
        recovery_expectations,
    ):
        raise SystemExit("waiting recovery helper accepted missing backend recovery evidence")

    sdk_waiting_events = {
        "status": "completed",
        "run_id": "run_waiting_after_recovery",
        "projected_event_count": 3,
        "cursor_policy": {"cursor_basis": "raw_source_event_seq", "cursor_semantics": "exclusive"},
        "final_output_included": False,
        "events": [
            {"type": "tool_progress"},
            {
                "type": "agent_guidance",
                "reason": "baseline_evidence_recovery_required_before_report",
            },
            {
                "type": "agent_question",
                "subtype": "missing_info_request",
                "question_count": 1,
                "questions": [{"field": "baseline_evidence"}],
            },
        ],
    }
    sdk_failures = _sdk_event_projection_failures(
        waiting_after_recovery_run,
        sdk_waiting_events,
        "waiting_for_user",
        recovery_expectations,
    )
    if sdk_failures:
        raise SystemExit(f"sdk waiting recovery helper rejected a valid projection: {sdk_failures}")
    sdk_waiting_events_missing_guidance = {
        **sdk_waiting_events,
        "projected_event_count": 2,
        "events": [sdk_waiting_events["events"][0], sdk_waiting_events["events"][2]],
    }
    if not _sdk_event_projection_failures(
        waiting_after_recovery_run,
        sdk_waiting_events_missing_guidance,
        "waiting_for_user",
        recovery_expectations,
    ):
        raise SystemExit("sdk waiting recovery helper accepted missing agent guidance")

    transcript_waiting = {
        "status": "completed",
        "run_id": "run_waiting_after_recovery",
        "run_status": "waiting_for_user",
        "source_endpoints": {
            "client_transcript": "/api/agent/runs/run_waiting_after_recovery/client-transcript",
            "sdk_events": "/api/agent/runs/run_waiting_after_recovery/sdk-events",
        },
        "messages": [
            {
                "type": "agent_guidance",
                "role": "system",
                "reason": "baseline_evidence_recovery_required_before_report",
            },
            {
                "type": "agent_question",
                "role": "assistant",
                "subtype": "missing_info_request",
                "questions": [{"field": "baseline_evidence"}],
            },
        ],
    }
    transcript_failures = _client_transcript_failures(
        waiting_after_recovery_run,
        transcript_waiting,
        "waiting_for_user",
        recovery_expectations,
    )
    if transcript_failures:
        raise SystemExit(f"client transcript helper rejected a valid waiting transcript: {transcript_failures}")
    transcript_missing_question = {
        **transcript_waiting,
        "messages": [transcript_waiting["messages"][0]],
    }
    if not _client_transcript_failures(
        waiting_after_recovery_run,
        transcript_missing_question,
        "waiting_for_user",
        recovery_expectations,
    ):
        raise SystemExit("client transcript helper accepted a missing waiting question")


def _assert_agent_loop_trace_summary_contract() -> None:
    valid_run = {
        "run_id": "run_agent_loop_summary",
        "status": "completed",
        "llm": {"external_api_called": True, "model": "gpt-test"},
        "trace": [
            {
                "step_type": "llm_response",
                "status": "completed",
                "output": {
                    "response_id": "resp_agent_loop",
                    "output": [{"type": "function_call", "name": "search_web_candidates"}],
                },
            },
            {
                "step_type": "tool_call",
                "name": "search_web_candidates",
                "input": {"call_id": "call_model_search"},
            },
            {
                "step_type": "tool_output",
                "name": "search_web_candidates",
                "output": {"call_id": "call_model_search"},
            },
            {
                "step_type": "tool_call",
                "name": "score_candidate_match",
                "input": {"call_id": "call_backend_score", "backend_recovery": True},
            },
            {
                "step_type": "tool_output",
                "name": "score_candidate_match",
                "output": {"call_id": "call_backend_score"},
            },
        ],
        "state": {
            "observations": [
                {"tool": "search_web_candidates", "call_id": "call_model_search"},
                {"tool": "score_candidate_match", "call_id": "call_backend_score"},
            ]
        },
    }
    summary = _agent_loop_trace_summary(valid_run)
    if summary.get("llm_raw_response_count") != 1:
        raise SystemExit("agent loop summary did not count LLM responses")
    if summary.get("trace_model_requested_tool_call_count") != 1:
        raise SystemExit("agent loop summary did not count model-requested tool calls")
    if summary.get("trace_backend_recovery_tool_call_count") != 1:
        raise SystemExit("agent loop summary did not count backend recovery tool calls")
    if summary.get("trace_tool_output_count") != 2:
        raise SystemExit("agent loop summary did not count backend tool outputs")
    if summary.get("observation_count") != 2:
        raise SystemExit("agent loop summary did not count stored observations")
    if summary.get("trace_model_requested_tool_names") != ["search_web_candidates"]:
        raise SystemExit("agent loop summary did not expose model-requested tool names")
    if _live_agent_boundary_failures(valid_run):
        raise SystemExit("agent loop boundary rejected valid model-selected tool execution")

    backend_only_run = {
        **valid_run,
        "run_id": "run_backend_only",
        "trace": [
            valid_run["trace"][0],
            {
                "step_type": "tool_call",
                "name": "search_web_candidates",
                "input": {"call_id": "call_backend_only", "backend_recovery": True},
            },
            {
                "step_type": "tool_output",
                "name": "search_web_candidates",
                "output": {"call_id": "call_backend_only"},
            },
        ],
        "state": {
            "observations": [
                {"tool": "search_web_candidates", "call_id": "call_backend_only"}
            ]
        },
    }
    backend_only_summary = _agent_loop_trace_summary(backend_only_run)
    if backend_only_summary.get("trace_model_requested_tool_call_count") != 0:
        raise SystemExit("agent loop summary counted backend recovery as model-requested")
    backend_only_failures = _live_agent_boundary_failures(backend_only_run)
    if "model_selected_tool_call_missing" not in backend_only_failures:
        raise SystemExit("agent loop boundary accepted backend-recovery-only tool execution")


def _assert_product_evidence_summary_contract() -> None:
    expectations = {
        "min_search_candidates": 3,
        "min_ranked_matches": 2,
        "min_normalized_candidates": 2,
        "min_reported_candidates": 2,
    }
    valid_run = {
        "status": "completed",
        "state": {
            "observations": [
                {
                    "tool": "search_web_candidates",
                    "result_status": "completed",
                    "result": {
                        "status": "completed",
                        "provider": "brave_search",
                        "external_api_called": True,
                        "candidate_count": 3,
                        "product_like_candidate_count": 4,
                        "rejected_candidate_count": 1,
                        "candidates": [
                            {"candidate_key": "candidate_a"},
                            {"candidate_key": "candidate_b"},
                            {"candidate_key": "candidate_c"},
                        ],
                    },
                },
                {
                    "tool": "fetch_product_page",
                    "result_status": "completed",
                    "result": {"status": "completed", "candidate_key": "candidate_a"},
                },
                {
                    "tool": "fetch_product_page",
                    "result_status": "completed",
                    "result": {"status": "completed", "candidate_key": "candidate_b"},
                },
                {
                    "tool": "normalize_product_spec",
                    "result_status": "completed",
                    "result": {
                        "status": "completed",
                        "candidate_key": "candidate_a",
                        "normalized_spec": {
                            "candidate_key": "candidate_a",
                            "size_label": "L",
                            "price": 39000,
                            "material": {"cotton": 100},
                            "measurements": {"chest": 57, "length": 69},
                            "size_measurement_rows": [
                                {"size_label": "L", "measurements": {"chest": 57, "length": 69}}
                            ],
                            "measurement_selection": {
                                "status": "matched_target_size",
                                "requested_size_label": "L",
                                "selected_size_label": "L",
                                "row_count": 1,
                            },
                        },
                    },
                },
                {
                    "tool": "normalize_product_spec",
                    "result_status": "completed",
                    "result": {
                        "status": "completed",
                        "candidate_key": "candidate_b",
                        "normalized_spec": {
                            "candidate_key": "candidate_b",
                            "size_label": "L",
                            "price": 42000,
                            "material": {},
                            "measurements": {},
                        },
                    },
                },
                {
                    "tool": "score_candidate_match",
                    "result_status": "completed",
                    "result": {
                        "status": "completed",
                        "candidate_key": "all",
                        "candidate_count": 3,
                        "active_candidate_count": 2,
                        "deferred_candidate_count": 1,
                        "ranked_matches": [
                            {
                                "candidate_key": "candidate_a",
                                "score": {"overall": 82, "size": 78, "material": 90, "confidence": 66},
                                "score_basis": {
                                    "size": {"method": "per_measurement_delta_thresholds"},
                                    "size_field_results": [
                                        {"field": "chest", "status": "caution", "delta_cm": 1.5},
                                        {"field": "length", "status": "close", "delta_cm": -0.5},
                                    ],
                                },
                                "measurement_deltas": {"chest": 1.5, "length": -0.5},
                                "measurement_selection": {
                                    "status": "matched_target_size",
                                    "requested_size_label": "L",
                                    "selected_size_label": "L",
                                    "row_count": 1,
                                },
                                "evidence_coverage": {
                                    "size_measurement_row_count": 1,
                                    "measurement_selection": {
                                        "status": "matched_target_size",
                                        "requested_size_label": "L",
                                        "selected_size_label": "L",
                                        "row_count": 1,
                                    },
                                },
                            },
                            {
                                "candidate_key": "candidate_b",
                                "score": {"overall": 61, "size": 55, "material": 45, "confidence": 40},
                                "score_basis": {
                                    "size": {
                                        "method": "measurement_comparison_not_applicable",
                                        "observed_measurement_fields": [],
                                        "score_cap": 55,
                                    },
                                    "size_field_results": [],
                                },
                            },
                        ],
                    },
                },
                {
                    "tool": "write_purchase_report",
                    "result_status": "completed",
                    "result": {
                        "status": "completed",
                        "candidate_key": "all",
                        "grounded": True,
                        "report_type": "shortlist",
                        "candidate_audit": {
                            "searched_candidate_count": 3,
                            "scored_candidate_count": 2,
                            "reported_candidate_count": 2,
                            "excluded_candidate_count": 1,
                            "close_fit_search_budget": {
                                "attempt_count": 1,
                                "attempt_limit": 24,
                                "remaining_attempts": 23,
                            },
                        },
                    },
                },
            ]
        },
        "final_report": {"grounded": True, "summary": "기준 아이템과 비교한 후보 shortlist입니다."},
    }
    summary = _product_evidence_summary(valid_run)
    if summary.get("latest_search_candidate_count") != 3:
        raise SystemExit("product evidence summary did not expose search candidate count")
    if summary.get("fetch_completed_candidate_count") != 2:
        raise SystemExit("product evidence summary did not expose completed fetch count")
    if summary.get("normalized_candidate_count") != 2:
        raise SystemExit("product evidence summary did not expose normalized candidate count")
    if summary.get("normalized_with_measurements_count") != 1:
        raise SystemExit("product evidence summary did not expose measurement coverage count")
    if summary.get("normalized_size_measurement_row_count") != 1:
        raise SystemExit("product evidence summary did not expose size measurement row count")
    if summary.get("normalized_measurement_selection_status_counts", {}).get("matched_target_size") != 1:
        raise SystemExit("product evidence summary did not expose normalized measurement selection statuses")
    if summary.get("ranked_match_count") != 2:
        raise SystemExit("product evidence summary did not expose ranked match count")
    if summary.get("ranked_size_basis_method_counts", {}).get("per_measurement_delta_thresholds") != 1:
        raise SystemExit("product evidence summary did not expose ranked size basis methods")
    if summary.get("ranked_size_field_result_count") != 2:
        raise SystemExit("product evidence summary did not expose ranked size field result count")
    if summary.get("ranked_uncomparable_size_score_over_neutral_count") != 0:
        raise SystemExit("product evidence summary incorrectly flagged neutral uncomparable size score")
    if summary.get("top_ranked_measurement_selection_status") != "matched_target_size":
        raise SystemExit("product evidence summary did not expose top measurement selection status")
    if summary.get("top_ranked_overall_score") != 82:
        raise SystemExit("product evidence summary did not expose top ranked score")
    if summary.get("report_candidate_audit_reported_count") != 2:
        raise SystemExit("product evidence summary did not expose report audit count")
    if summary.get("close_fit_search_remaining_attempts") != 23:
        raise SystemExit("product evidence summary did not expose close-fit budget")
    if _product_evidence_failures(valid_run, expectations):
        raise SystemExit("product evidence helper rejected valid multi-candidate evidence")
    size_table_expectations = {
        **expectations,
        "min_size_measurement_rows": 1,
        "min_matched_target_size": 1,
        "min_ranked_measurement_deltas": 1,
        "min_ranked_size_field_results": 2,
    }
    if _product_evidence_failures(valid_run, size_table_expectations):
        raise SystemExit("product evidence helper rejected valid size-table evidence")
    missing_size_table_expectations = {
        **expectations,
        "min_size_measurement_rows": 2,
        "min_matched_target_size": 2,
        "min_ranked_measurement_deltas": 2,
        "min_ranked_size_field_results": 3,
    }
    missing_size_table_failures = _product_evidence_failures(valid_run, missing_size_table_expectations)
    for expected_failure in [
        "product_evidence_size_measurement_row_count_below_min:1:2",
        "product_evidence_matched_target_size_count_below_min:1:2",
        "product_evidence_ranked_measurement_delta_count_below_min:1:2",
        "product_evidence_ranked_size_field_result_count_below_min:2:3",
    ]:
        if expected_failure not in missing_size_table_failures:
            raise SystemExit(f"product evidence helper missed size-table failure: {expected_failure}")

    individual_score_shortlist_run = {
        **valid_run,
        "state": {
            "observations": [
                item
                for item in valid_run["state"]["observations"]
                if item.get("tool") != "score_candidate_match"
            ]
            + [
                {
                    "tool": "score_candidate_match",
                    "result_status": "completed",
                    "arguments": {"candidate_key": "candidate_a"},
                    "result": {
                        "status": "completed",
                        "candidate_key": "candidate_a",
                        "score": {"overall": 82, "size": 78, "material": 90, "confidence": 66},
                    },
                },
                {
                    "tool": "score_candidate_match",
                    "result_status": "completed",
                    "arguments": {"candidate_key": "candidate_b"},
                    "result": {
                        "status": "completed",
                        "candidate_key": "candidate_b",
                        "score": {"overall": 74, "size": 70, "material": 45, "confidence": 40},
                    },
                },
            ]
        },
    }
    individual_summary = _product_evidence_summary(individual_score_shortlist_run)
    if individual_summary.get("ranked_match_count") != 2:
        raise SystemExit("product evidence summary did not count individual score observations")
    if individual_summary.get("score_candidate_count") != 2:
        raise SystemExit("product evidence summary did not expose individual score candidate count")
    if individual_summary.get("top_ranked_candidate_key") != "candidate_a":
        raise SystemExit("product evidence summary did not derive top candidate from individual scores")
    if _product_evidence_failures(individual_score_shortlist_run, expectations):
        raise SystemExit("product evidence helper rejected individual-score shortlist evidence")

    uncomparable_size_run = {
        **valid_run,
        "state": {
            "observations": [
                item
                for item in valid_run["state"]["observations"]
                if item.get("tool") != "score_candidate_match"
            ]
            + [
                {
                    "tool": "score_candidate_match",
                    "result_status": "completed",
                    "result": {
                        "status": "completed",
                        "candidate_key": "all",
                        "candidate_count": 3,
                        "ranked_matches": [
                            {
                                "candidate_key": "candidate_a",
                                "score": {"overall": 74, "size": 70, "material": 60, "confidence": 45},
                                "score_basis": {
                                    "size": {
                                        "method": "measurement_comparison_not_applicable",
                                        "observed_measurement_fields": ["chest", "length"],
                                    },
                                    "size_field_results": [],
                                },
                            }
                        ],
                    },
                },
            ]
        },
    }
    uncomparable_failures = _product_evidence_failures(
        uncomparable_size_run,
        {**expectations, "min_ranked_matches": 1},
    )
    if "product_evidence_uncomparable_size_score_over_neutral:1" not in uncomparable_failures:
        raise SystemExit("product evidence helper accepted a high uncomparable size score")

    weak_run = {
        **valid_run,
        "state": {
            "observations": [
                {
                    "tool": "search_web_candidates",
                    "result_status": "completed",
                    "result": {
                        "status": "completed",
                        "candidate_count": 1,
                        "candidates": [{"candidate_key": "candidate_only"}],
                    },
                },
                {
                    "tool": "score_candidate_match",
                    "result_status": "completed",
                    "result": {
                        "status": "completed",
                        "candidate_key": "all",
                        "candidate_count": 1,
                        "ranked_matches": [{"candidate_key": "candidate_only", "score": {"overall": 61}}],
                    },
                },
                {
                    "tool": "write_purchase_report",
                    "result_status": "completed",
                    "result": {
                        "status": "completed",
                        "candidate_key": "all",
                        "grounded": True,
                        "candidate_audit": {"reported_candidate_count": 1},
                    },
                },
            ]
        },
    }
    weak_failures = _product_evidence_failures(weak_run, expectations)
    for expected_failure in [
        "product_evidence_search_candidate_count_below_min:1:3",
        "product_evidence_ranked_match_count_below_min:1:2",
        "product_evidence_normalized_candidate_count_below_min:0:2",
        "product_evidence_reported_candidate_count_below_min:1:2",
    ]:
        if expected_failure not in weak_failures:
            raise SystemExit(f"product evidence helper missed weak evidence failure: {expected_failure}")


def _assert_final_report_user_text_quality_contract() -> None:
    good_summary = (
        "정확한 기준 상품은 웹검색으로 확인하지 못했습니다. 따라서 제한된 신뢰도로 비교합니다. "
        "기준 아이템의 소재 근거가 부족해 소재 유사도 판단이 제한됩니다."
    )
    bad_summary = (
        f"{good_summary} 추가 확인이 필요합니다: 기준 아이템의 소재 근거가 부족합니다. "
        "라벨/사이즈표 이미지나 실측/소재 텍스트를 보내주면 다시 비교하겠습니다."
    )
    completed_good_run = {
        "run_id": "run_completed_quality",
        "status": "completed",
        "trace": [],
        "state": {},
        "final_report": {"grounded": True, "summary": good_summary},
    }
    completed_bad_run = {
        **completed_good_run,
        "final_report": {"grounded": True, "summary": bad_summary},
    }

    if _user_visible_report_text_failures(good_summary):
        raise SystemExit("final report text quality helper rejected valid limited-confidence wording")
    if not any(
        failure.startswith("pending_user_question:")
        for failure in _user_visible_report_text_failures(bad_summary)
    ):
        raise SystemExit("final report text quality helper accepted pending user-question wording")
    if _live_agent_completion_failures(completed_good_run, "completed", {}):
        raise SystemExit("completion helper rejected valid completed final report wording")
    bad_completion_failures = _live_agent_completion_failures(completed_bad_run, "completed", {})
    if not any(
        failure.startswith("final_report_user_text_quality:pending_user_question:")
        for failure in bad_completion_failures
    ):
        raise SystemExit("completion helper accepted completed report with pending user-question wording")

    summarized_bad_run = _summarize_run(completed_bad_run)
    if not any(
        failure.startswith("pending_user_question:")
        for failure in summarized_bad_run.get("final_report_user_text_failures") or []
    ):
        raise SystemExit("run summary did not expose final report user-text quality failures")

    base_sdk_events = {
        "status": "completed",
        "run_id": "run_completed_quality",
        "projected_event_count": 2,
        "terminal_event_seq": 0,
        "terminal_event_included": True,
        "final_output_included": True,
        "cursor_policy": {"cursor_basis": "raw_source_event_seq", "cursor_semantics": "exclusive"},
        "events": [
            {"type": "tool_progress", "name": "write_purchase_report"},
            {
                "type": "final_output",
                "summary": good_summary,
                "grounded": True,
                "citation_badge_count": 1,
                "source_endpoints": {"report": "/report", "evidence": "/evidence"},
                "grounding_validation": {
                    "valid": True,
                    "suspicious_numbers": [],
                    "suspicious_urls": [],
                    "suspicious_material_claims": [],
                    "suspicious_score_claims": [],
                    "suspicious_identity_claims": [],
                    "suspicious_style_claims": [],
                    "suspicious_fit_claims": [],
                    "suspicious_evidence_claims": [],
                },
            },
        ],
    }
    if _sdk_event_projection_failures(completed_good_run, base_sdk_events, "completed", {}):
        raise SystemExit("SDK projection helper rejected valid completed final output wording")
    bad_sdk_events = {
        **base_sdk_events,
        "events": [
            base_sdk_events["events"][0],
            {**base_sdk_events["events"][1], "summary": bad_summary},
        ],
    }
    bad_sdk_failures = _sdk_event_projection_failures(
        completed_good_run,
        bad_sdk_events,
        "completed",
        {},
    )
    if not any(
        failure.startswith("final_output_user_text_quality:pending_user_question:")
        for failure in bad_sdk_failures
    ):
        raise SystemExit("SDK projection helper accepted final output with pending user-question wording")
    sdk_text_failures = _sdk_final_output_user_text_failures(bad_sdk_events) or []
    if not any(failure.startswith("pending_user_question:") for failure in sdk_text_failures):
        raise SystemExit("SDK final-output summary did not expose pending user-question wording")


def _assert_harness_publish_summary_contract() -> None:
    completed_publish = {
        "status": "completed",
        "mode": "publish",
        "task_id": "task_publish_summary",
        "eval_case_id": 77,
        "trace_json": "harness_outputs/live/run_trace.json",
        "eval_case_json": "harness_outputs/live/run_eval.json",
        "readback": {
            "validation": {
                "valid": True,
                "failed_checks": [],
            }
        },
        "revalidation_inputs": {
            "task_id": "task_publish_summary",
            "trace_json": "harness_outputs/live/run_trace.json",
            "eval_case_json": "harness_outputs/live/run_eval.json",
            "client_id": "verify-client",
            "base_url": "http://127.0.0.1:8000/api/harness/v1",
        },
        "revalidation_cli_args": [
            "--readback-only",
            "--task-id",
            "task_publish_summary",
            "--trace-json",
            "harness_outputs/live/run_trace.json",
        ],
        "revalidation_ready": True,
        "revalidation_missing_fields": [],
        "record_result": {
            "status": "completed",
            "observation_id": 177,
            "observation_status": "completed",
        },
        "record_result_readback": {
            "status": "completed",
            "sdk_event_match_count": 1,
            "transcript_match_count": 1,
            "validation": {
                "valid": True,
                "failed_checks": [],
            },
        },
        "record_result_gate": {
            "status": "completed",
            "valid": True,
            "failed_checks": [],
        },
    }
    summary = _harness_publish_summary(completed_publish)
    if summary.get("harness_publish_status") != "completed":
        raise SystemExit("harness publish summary rejected valid completed publish output")
    if summary.get("harness_publish_raw_status") != "completed":
        raise SystemExit("harness publish summary did not preserve raw publish status")
    if summary.get("harness_publish_task_id") != "task_publish_summary":
        raise SystemExit("harness publish summary did not expose task id")
    if summary.get("harness_publish_eval_case_id") != 77:
        raise SystemExit("harness publish summary did not expose eval case id")
    if summary.get("harness_publish_trace_json") != "harness_outputs/live/run_trace.json":
        raise SystemExit("harness publish summary did not expose trace JSON path")
    if summary.get("harness_publish_eval_case_json") != "harness_outputs/live/run_eval.json":
        raise SystemExit("harness publish summary did not expose eval-case JSON path")
    if summary.get("harness_publish_revalidation_inputs", {}).get("task_id") != "task_publish_summary":
        raise SystemExit("harness publish summary did not expose revalidation inputs")
    if "--readback-only" not in (summary.get("harness_publish_revalidation_cli_args") or []):
        raise SystemExit("harness publish summary did not expose revalidation CLI args")
    if summary.get("harness_publish_record_result_observation_id") != 177:
        raise SystemExit("harness publish summary did not expose record-result observation id")
    if summary.get("harness_publish_readback_valid") is not True:
        raise SystemExit("harness publish summary did not expose publish readback validity")
    if summary.get("harness_publish_record_result_readback_valid") is not True:
        raise SystemExit("harness publish summary did not expose record-result readback validity")
    if summary.get("harness_publish_record_result_gate_valid") is not True:
        raise SystemExit("harness publish summary did not expose record-result gate validity")

    failed_publish = {
        **completed_publish,
        "record_result_gate": {
            "status": "failed",
            "valid": False,
            "failed_checks": ["readback:sdk_event_projection_present"],
        },
    }
    failed_summary = _harness_publish_summary(failed_publish)
    if failed_summary.get("harness_publish_status") != "failed":
        raise SystemExit("harness publish summary accepted failed record-result gate")
    if "harness_publish_record_result:readback:sdk_event_projection_present" not in (
        failed_summary.get("harness_publish_failures") or []
    ):
        raise SystemExit("harness publish summary did not expose record-result gate failure")
    if failed_summary.get("harness_publish_record_result_gate_valid") is not False:
        raise SystemExit("harness publish summary did not expose failed record-result gate validity")

    dry_run_publish = {
        "status": "dry_run",
        "mode": "publish",
        "task_id": "<created_task_id>",
        "record_result_gate": {
            "status": "dry_run",
            "valid": True,
            "failed_checks": [],
        },
    }
    dry_run_summary = _harness_publish_summary(dry_run_publish)
    if dry_run_summary.get("harness_publish_status") != "completed":
        raise SystemExit("harness publish summary should treat dry-run output as a successful gate")
    if dry_run_summary.get("harness_publish_raw_status") != "dry_run":
        raise SystemExit("harness publish summary did not preserve raw dry-run status")


def _assert_live_agent_harness_export_contract() -> None:
    sample_run = {
        "run_id": "run_export_contract",
        "status": "waiting_for_user",
        "llm": {
            "provider": "openai",
            "model": "gpt-test",
            "api_style": "responses",
            "external_api_called": True,
        },
        "trace": [
            {
                "step_type": "llm_response",
                "status": "completed",
                "output": {
                    "response_id": "resp_export",
                    "provider_request_id": "req_export",
                    "model": "gpt-test",
                    "output": [{"type": "function_call", "name": "search_web_candidates"}],
                },
            },
            {
                "step_type": "tool_call",
                "name": "search_web_candidates",
                "status": "completed",
                "input": {
                    "call_id": "call_export_search",
                    "query": "tee length 68 shoulder 52 chest 55",
                    "backend_recovery": True,
                    "reason": "continue_search_for_closer_measurements",
                },
            },
            {
                "step_type": "tool_output",
                "name": "search_web_candidates",
                "status": "completed",
                "output": {
                    "call_id": "call_export_search",
                    "status": "completed",
                    "candidate_count": 1,
                },
            },
            {
                "step_type": "tool_guidance",
                "name": "continue_candidate_enrichment",
                "status": "completed",
                "output": {
                    "reason": "continue_search_for_closer_measurements",
                    "next_tool_suggestions": [{"tool": "search_web_candidates"}],
                    "close_fit_search_budget": {"remaining_attempts": 2, "attempt_limit": 24},
                },
            },
        ],
        "state": {
            "missing_info": [{"field": "baseline_evidence", "question": "기준 옷 실측을 알려주세요."}],
            "observations": [{"tool": "search_web_candidates", "call_id": "call_export_search"}],
        },
    }
    expectations = {
        "missing_field": "baseline_evidence",
        "recovery_reason": "continue_search_for_closer_measurements",
        "backend_recovery_tools": ["search_web_candidates"],
    }
    provenance = _live_run_provenance_summary(sample_run)
    if provenance.get("run_id") != "run_export_contract":
        raise SystemExit("live agent result provenance did not expose run id")
    if provenance.get("trace_id") != "run_export_contract":
        raise SystemExit("live agent result provenance did not expose trace id")

    result = {
        "status": "completed",
        "expected_status": "waiting_for_user",
        "agentic_boundary_status": "completed",
        "completion_status": "completed",
        "agent_run": _summarize_run(sample_run),
        "failures": [],
        "sdk_event_projected_count": 3,
        "sdk_event_agent_guidance_count": 1,
        "sdk_event_expected_guidance_count": 1,
        "client_transcript_message_count": 2,
        "client_transcript_agent_guidance_count": 1,
        "client_transcript_expected_guidance_count": 1,
        "provider_probe_status": "completed",
        "provider_probe_diagnostic_id": 91,
        "provider_probe_external_api_called": True,
        "provider_probe_failures": [],
        "provider_probe": {
            "status": "completed",
            "diagnostic_id": 91,
            "provider": {
                "provider": "openai",
                "api_style": "responses",
                "model": "gpt-test",
                "api_key_env": "OPENAI_API_KEY",
            },
            "endpoint": "https://api.openai.com/v1/models",
            "http_status": 200,
            "external_api_called": True,
            "validation": {"valid": True, "failed_checks": []},
        },
    }
    sdk_events = {
        "events": [
            {"type": "tool_progress", "name": "search_web_candidates"},
            {
                "type": "agent_guidance",
                "subtype": "continue_candidate_enrichment",
                "guidance_type": "backend_enrichment_required",
                "reason": "continue_search_for_closer_measurements",
                "next_tool_suggestions": [{"tool": "search_web_candidates"}],
                "close_fit_search_budget": {"remaining_attempts": 2, "attempt_limit": 24},
            },
            {
                "type": "agent_question",
                "subtype": "missing_info_request",
                "questions": [{"field": "baseline_evidence"}],
            },
        ],
        "projected_event_count": 3,
    }
    trace = _build_live_agent_trace_artifact(
        base_url="http://127.0.0.1:8000/",
        request_payload={"goal": "find close fit", "free_text": "대충 찾지 말고 계속 찾아줘", "refresh_web": False},
        run=sample_run,
        fetched_run=sample_run,
        sdk_events=sdk_events,
        sdk_events_terminal_delta={"raw_event_count": 1},
        client_transcript={"messages": [{"type": "agent_question"}]},
        result=result,
        expected_status="waiting_for_user",
        expectations=expectations,
        allow_cached_search=False,
    )
    if trace.get("schema_version") != "fitcheck_live_agent_trace.v1":
        raise SystemExit("live agent trace export schema version missing")
    if trace.get("source") != "fitcheck_live_agent_smoke":
        raise SystemExit("live agent trace export source missing")
    item_types = [item.get("type") for item in trace.get("items") or []]
    if "function_call" not in item_types or "function_call_output" not in item_types:
        raise SystemExit("live agent trace export did not preserve tool call/output items")
    if not trace.get("raw_responses"):
        raise SystemExit("live agent trace export did not preserve model response evidence")
    if not trace.get("agent_questions"):
        raise SystemExit("live agent trace export did not preserve SDK question evidence")
    if not trace.get("agent_guidance"):
        raise SystemExit("live agent trace export did not preserve SDK guidance evidence")
    if trace["agent_guidance"][0].get("reason") != "continue_search_for_closer_measurements":
        raise SystemExit("live agent trace export did not preserve guidance reason")
    if trace.get("provider_probe", {}).get("diagnostic_id") != 91:
        raise SystemExit("live agent trace export did not preserve provider diagnostic id")
    if trace.get("provider_probe", {}).get("api_key_env") != "OPENAI_API_KEY":
        raise SystemExit("live agent trace export should preserve only provider API-key env name")

    eval_case = _build_live_agent_eval_case_record(
        base_url="http://127.0.0.1:8000/",
        request_payload={"goal": "find close fit", "free_text": "대충 찾지 말고 계속 찾아줘", "refresh_web": False},
        result=result,
        expected_status="waiting_for_user",
        expectations=expectations,
        allow_cached_search=False,
    )
    for key in ["name", "input", "expected", "verifier_command", "evidence_refs", "metadata"]:
        if key not in eval_case:
            raise SystemExit(f"live agent eval-case export missing {key}")
    if eval_case["expected"].get("sdk_events_projected") is not True:
        raise SystemExit("live agent eval-case export should require SDK events projection")
    if eval_case["expected"].get("agent_guidance_projected") is not True:
        raise SystemExit("live agent eval-case export should require guidance projection when recovery is expected")
    if eval_case["expected"].get("expected_guidance_reason") != "continue_search_for_closer_measurements":
        raise SystemExit("live agent eval-case export did not preserve expected guidance reason")
    if eval_case["metadata"].get("sdk_event_agent_guidance_count") != 1:
        raise SystemExit("live agent eval-case export did not preserve SDK guidance count")
    if eval_case["metadata"].get("client_transcript_agent_guidance_count") != 1:
        raise SystemExit("live agent eval-case export did not preserve transcript guidance count")
    if eval_case["expected"].get("provider_probe_valid") is not True:
        raise SystemExit("live agent eval-case export should preserve provider probe expectation")
    if eval_case["metadata"].get("provider_probe_diagnostic_id") != 91:
        raise SystemExit("live agent eval-case export did not preserve provider diagnostic id")
    if "FITCHECK_RUN_LIVE_AGENT_SMOKE=1" not in eval_case["verifier_command"]:
        raise SystemExit("live agent eval-case verifier command should rerun live smoke")

    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        trace_path = temp_path / "run_publish_contract-fitcheck-live-agent-trace.json"
        eval_case_path = temp_path / "run_publish_contract-fitcheck-live-agent-eval-case.json"
        trace_path.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "source": "fitcheck_live_agent_smoke",
                    "trace_id": "run_publish_contract",
                    "items": [
                        {
                            "type": "function_call",
                            "name": "search_web_candidates",
                            "call_id": "call_publish",
                            "arguments": {"query": "close fit tee"},
                        },
                        {
                            "type": "function_call_output",
                            "name": "search_web_candidates",
                            "call_id": "call_publish",
                            "output": {"status": "completed"},
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        eval_case_path.write_text(
            json.dumps(
                {
                    "name": "fitcheck live agent SDK smoke publisher contract",
                    "failure_label": "regression_seed_waiting_for_user",
                    "input": {"goal": "publish contract"},
                    "expected": {"sdk_events_projected": True, "client_transcript_projected": True},
                    "verifier_command": "cd backend_harness && FITCHECK_RUN_LIVE_AGENT_SMOKE=1 ../.venv/bin/python -B verify_live_agent_smoke.py",
                    "evidence_refs": ["fitcheck_run:run_publish_contract"],
                    "metadata": {"kind": "fitcheck-live-agent-sdk-regression"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        old_env = {
            key: os.environ.get(key)
            for key in [
                "FITCHECK_LIVE_AGENT_PUBLISH_HARNESS",
                "FITCHECK_LIVE_AGENT_HARNESS_CLIENT_ID",
                "FITCHECK_LIVE_AGENT_HARNESS_DRY_RUN",
                "FITCHECK_LIVE_AGENT_HARNESS_IDEMPOTENCY_KEY",
                "FITCHECK_LIVE_AGENT_HARNESS_RECORD_RESULT",
                "FITCHECK_LIVE_AGENT_HARNESS_RECORD_RESULT_TASK_ID",
                "FITCHECK_LIVE_AGENT_TRACE_EXPORT_PATH",
                "FITCHECK_LIVE_AGENT_EVAL_CASE_EXPORT_PATH",
            ]
        }
        try:
            os.environ["FITCHECK_LIVE_AGENT_TRACE_EXPORT_PATH"] = str(temp_path / "summary-trace.json")
            os.environ["FITCHECK_LIVE_AGENT_EVAL_CASE_EXPORT_PATH"] = str(temp_path / "summary-eval.json")
            exported = _export_live_agent_harness_artifacts(
                trace_artifact=trace,
                eval_case_record=eval_case,
                run_id="run_export_contract",
            )
            export_summary = _harness_export_summary(exported)
            if export_summary.get("harness_trace_json") != str(temp_path / "summary-trace.json"):
                raise SystemExit("live agent export summary did not expose trace path")
            if not str(export_summary.get("harness_trace_digest") or "").startswith("sha256:"):
                raise SystemExit("live agent export summary did not expose trace digest")
            if export_summary.get("harness_eval_case_json") != str(temp_path / "summary-eval.json"):
                raise SystemExit("live agent export summary did not expose eval-case path")
            if not str(export_summary.get("harness_eval_case_digest") or "").startswith("sha256:"):
                raise SystemExit("live agent export summary did not expose eval-case digest")
            if export_summary.get("harness_export_ready") is not True:
                raise SystemExit("live agent export summary should be ready when both artifacts are written")

            os.environ["FITCHECK_LIVE_AGENT_PUBLISH_HARNESS"] = "1"
            os.environ["FITCHECK_LIVE_AGENT_HARNESS_CLIENT_ID"] = "verify-client"
            os.environ["FITCHECK_LIVE_AGENT_HARNESS_DRY_RUN"] = "1"
            os.environ["FITCHECK_LIVE_AGENT_HARNESS_IDEMPOTENCY_KEY"] = "verify-live-smoke-publish-contract"
            os.environ["FITCHECK_LIVE_AGENT_HARNESS_RECORD_RESULT"] = "1"
            published = _publish_live_agent_harness_export_if_requested(
                {
                    "trace_artifact": {"path": str(trace_path)},
                    "eval_case_record": {"path": str(eval_case_path)},
                },
                run_id="run_publish_contract",
            )
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        if published.get("status") != "dry_run":
            raise SystemExit("live agent publish contract should produce dry_run output")
        if published.get("task_id") != "<created_task_id>":
            raise SystemExit("live agent publish contract should create an evidence-holder task")
        if published.get("record_result", {}).get("status") != "dry_run":
            raise SystemExit("live agent publish contract should dry-run record-result evidence")
        if published.get("record_result_gate", {}).get("valid") is not True:
            raise SystemExit("live agent publish contract should pass dry-run record-result gate")
        paths = [item.get("path") for item in published.get("actions") or []]
        if "/tasks?background=false&start=false" not in paths:
            raise SystemExit("live agent publish contract missing task creation action")
        if "/eval-cases" not in paths:
            raise SystemExit("live agent publish contract missing eval-case action")


def _live_agent_completion_failures(
    run: dict[str, Any],
    expected_status: str = "completed",
    expectations: dict[str, Any] | None = None,
) -> list[str]:
    expectations = expectations or {}
    if run.get("status") != expected_status:
        return [
            f"agent_route_status:{run.get('status')}:{(run.get('error') or {}).get('code') or 'unknown'}:"
            f"expected:{expected_status}"
        ]
    if expected_status == "waiting_for_user":
        failures: list[str] = []
        missing_info = (run.get("state") or {}).get("missing_info") or []
        if not missing_info:
            failures.append("waiting_for_user_missing_info_empty")
        expected_field = str(expectations.get("missing_field") or "")
        if expected_field and expected_field not in _missing_info_fields(run):
            failures.append(f"waiting_for_user_missing_info_field:{expected_field}")
        expected_reason = str(expectations.get("recovery_reason") or "")
        if expected_reason:
            if expected_reason not in _agent_guidance_reasons(run):
                failures.append(f"waiting_for_user_guidance_reason_missing:{expected_reason}")
            if expected_reason not in _backend_recovery_reasons(run):
                failures.append(f"waiting_for_user_backend_recovery_reason_missing:{expected_reason}")
        backend_recovery_tools = _backend_recovery_tool_names(run)
        for tool_name in expectations.get("backend_recovery_tools") or []:
            if tool_name not in backend_recovery_tools:
                failures.append(f"waiting_for_user_backend_recovery_tool_missing:{tool_name}")
        if run.get("final_report"):
            failures.append("waiting_for_user_should_not_include_final_report")
        return failures
    if (run.get("final_report") or {}).get("grounded") is not True:
        return ["final_report_not_grounded"]
    summary = str((run.get("final_report") or {}).get("summary") or "")
    failures = [f"final_report_user_text_quality:{failure}" for failure in _user_visible_report_text_failures(summary)]
    failures.extend(_product_evidence_failures(run, expectations))
    return failures


def _product_evidence_failures(run: dict[str, Any], expectations: dict[str, Any]) -> list[str]:
    summary = _product_evidence_summary(run)
    checks = [
        (
            "search_candidate_count",
            _safe_int(summary.get("latest_search_candidate_count")) or 0,
            _safe_int(expectations.get("min_search_candidates")) or 0,
        ),
        (
            "ranked_match_count",
            _safe_int(summary.get("ranked_match_count")) or 0,
            _safe_int(expectations.get("min_ranked_matches")) or 0,
        ),
        (
            "normalized_candidate_count",
            _safe_int(summary.get("normalized_candidate_count")) or 0,
            _safe_int(expectations.get("min_normalized_candidates")) or 0,
        ),
        (
            "reported_candidate_count",
            _safe_int(summary.get("report_candidate_audit_reported_count")) or 0,
            _safe_int(expectations.get("min_reported_candidates")) or 0,
        ),
        (
            "size_measurement_row_count",
            _safe_int(summary.get("normalized_size_measurement_row_count")) or 0,
            _safe_int(expectations.get("min_size_measurement_rows")) or 0,
        ),
        (
            "matched_target_size_count",
            _safe_int(
                (summary.get("normalized_measurement_selection_status_counts") or {}).get("matched_target_size")
            )
            or 0,
            _safe_int(expectations.get("min_matched_target_size")) or 0,
        ),
        (
            "ranked_measurement_delta_count",
            _safe_int(summary.get("ranked_with_measurement_deltas_count")) or 0,
            _safe_int(expectations.get("min_ranked_measurement_deltas")) or 0,
        ),
        (
            "ranked_size_field_result_count",
            _safe_int(summary.get("ranked_size_field_result_count")) or 0,
            _safe_int(expectations.get("min_ranked_size_field_results")) or 0,
        ),
    ]
    failures: list[str] = []
    for label, actual, expected in checks:
        if expected > 0 and actual < expected:
            failures.append(f"product_evidence_{label}_below_min:{actual}:{expected}")
    ranked_count = _safe_int(summary.get("ranked_match_count")) or 0
    audit_scored_count = _safe_int(summary.get("report_candidate_audit_scored_count"))
    if ranked_count > 0 and audit_scored_count is None:
        failures.append("product_evidence_candidate_audit_scored_missing")
    elif audit_scored_count is not None and audit_scored_count < ranked_count:
        failures.append(f"product_evidence_candidate_audit_scored_below_ranked:{audit_scored_count}:{ranked_count}")
    uncomparable_size_over_neutral = _safe_int(summary.get("ranked_uncomparable_size_score_over_neutral_count")) or 0
    if uncomparable_size_over_neutral:
        failures.append(
            f"product_evidence_uncomparable_size_score_over_neutral:{uncomparable_size_over_neutral}"
        )
    return failures


def _missing_info_fields(run: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for question in (run.get("state") or {}).get("missing_info") or []:
        field = str(question.get("field") or "").strip()
        if field and field not in fields:
            fields.append(field)
    return fields


def _agent_guidance_reasons(run: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for step in run.get("trace") or []:
        if step.get("step_type") != "tool_guidance":
            continue
        reason = str((step.get("output") or {}).get("reason") or "").strip()
        if reason and reason not in reasons:
            reasons.append(reason)
    return reasons


def _backend_recovery_reasons(run: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for step in run.get("trace") or []:
        if step.get("step_type") == "backend_recovery":
            reason = str((step.get("input") or {}).get("reason") or "").strip()
        elif step.get("step_type") == "tool_call" and (step.get("input") or {}).get("backend_recovery"):
            reason = str((step.get("input") or {}).get("reason") or "").strip()
        else:
            continue
        if reason and reason not in reasons:
            reasons.append(reason)
    return reasons


def _backend_recovery_tool_names(run: dict[str, Any]) -> list[str]:
    tools: list[str] = []
    for step in run.get("trace") or []:
        if step.get("step_type") != "tool_call":
            continue
        if not (step.get("input") or {}).get("backend_recovery"):
            continue
        name = str(step.get("name") or "").strip()
        if name and name not in tools:
            tools.append(name)
    return tools


def _csv_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def _int_env(name: str, default: int) -> int:
    parsed = _safe_int(os.getenv(name))
    return default if parsed is None else parsed


def _user_visible_report_text_failures(text: str) -> list[str]:
    if not text.strip():
        return ["missing_summary"]
    forbidden_fragments = [
        "a candidate",
        "is missing evidence",
        "No additional risk",
        "page fetch",
        "Material evidence",
        "Candidate price",
        "measurement evidence is missing",
        "differs from the liked baseline",
    ]
    failures = [f"english_fallback:{fragment}" for fragment in forbidden_fragments if fragment in text]
    pending_question_fragments = [
        "추가 확인이 필요합니다:",
        "보내주면 다시 비교하겠습니다",
        "알려주면 다시 비교하겠습니다",
        "라벨/사이즈표 이미지",
        "원래 상품 URL",
        "정확한 구매 이력 상품명",
    ]
    failures.extend(
        f"pending_user_question:{fragment}"
        for fragment in pending_question_fragments
        if fragment in text
    )
    mojibake_markers = ["�", "Ã", "Â", "횄", "횂", "占", "筌", "疫", "野", "鍮", "留", "湲", "寃"]
    failures.extend(f"mojibake:{marker}" for marker in mojibake_markers if marker in text)
    return failures


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the FitCheck live product-agent route.")
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional path where the full smoke result JSON should be written.",
    )
    return parser.parse_args(argv)


def _emit(payload: dict[str, Any], args: argparse.Namespace) -> None:
    output_json = str(getattr(args, "output_json", "") or "").strip()
    if output_json:
        output_path = Path(output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _print(payload)


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _fail(payload: dict[str, Any]) -> None:
    _print({"status": "failed", **payload})
    raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
