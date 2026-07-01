from __future__ import annotations

import base64
import json
import os
import sys
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener

from app.agent_run_store import build_agent_run_store
from app.server_context import AGENT_RUNS_PATH


PNG_1X1_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="


def main() -> None:
    base_url = os.getenv("FITCHECK_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/") + "/"
    if not _truthy(os.getenv("FITCHECK_RUN_HTTP_API_SMOKE")):
        _print(
            {
                "status": "skipped",
                "reason": "Set FITCHECK_RUN_HTTP_API_SMOKE=1 to verify the running API server without calling the LLM route.",
                "base_url": base_url,
                "checks": [
                    "api_status_schema",
                    "cookie_session_persistence",
                    "openapi_route_models",
                    "request_body_validation",
                    "upload_success_and_validation",
                    "agent_run_session_isolation",
                    "agent_configuration_required_sse_terminal",
                    "agent_not_found_json",
                    "sse_not_found_event_stream",
                ],
            }
        )
        return

    timeout = float(os.getenv("FITCHECK_HTTP_API_TIMEOUT", "30"))
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    headers = {"X-FitCheck-Session-Id": os.getenv("FITCHECK_HTTP_API_SESSION_ID", "http_api_smoke")}

    checks = [
        _check_status(opener, base_url, timeout, headers),
        _check_cookie_session_persistence(base_url, timeout),
        _check_openapi(opener, base_url, timeout, headers),
        _check_request_body_validation(opener, base_url, timeout, headers),
        _check_uploads(opener, base_url, timeout, headers),
        _check_agent_session_isolation(opener, base_url, timeout, headers),
        _check_agent_configuration_required_sse_terminal(opener, base_url, timeout, headers),
        _check_agent_resumable_llm_failure_sse_terminal(opener, base_url, timeout, headers),
        _check_agent_not_found(opener, base_url, timeout, headers),
        _check_sse_not_found(opener, base_url, timeout, headers),
    ]
    failures = [check for check in checks if check["status"] != "completed"]
    result = {
        "status": "failed" if failures else "completed",
        "base_url": base_url,
        "checks": checks,
    }
    _print(result)
    if failures:
        raise SystemExit(1)


def _check_status(opener, base_url: str, timeout: float, headers: dict[str, str]) -> dict[str, Any]:
    payload, meta = _request_json(opener, "GET", urljoin(base_url, "api/status"), timeout=timeout, headers=headers)
    required = [
        payload.get("status") == "ready",
        bool(payload.get("session_id")),
        payload.get("providers", {}).get("search", {}).get("provider"),
        payload.get("providers", {}).get("ocr", {}).get("provider"),
        payload.get("providers", {}).get("page_fetch", {}).get("provider"),
        payload.get("production_agent", {}).get("route") == "/api/agent/runs",
        payload.get("orchestration", {}).get("current_runtime") == "fitcheck_backend_owned_tool_loop",
        payload.get("orchestration", {}).get("codex_sdk", {}).get("enabled_for_product_runtime") is False,
    ]
    return {
        "check": "api_status_schema",
        "status": "completed" if all(required) else "failed",
        "http_status": meta["status"],
        "session_id": payload.get("session_id"),
        "llm_status": payload.get("llm", {}).get("status"),
        "search_provider": payload.get("providers", {}).get("search", {}).get("provider"),
        "ocr_provider": payload.get("providers", {}).get("ocr", {}).get("provider"),
        "page_fetch_provider": payload.get("providers", {}).get("page_fetch", {}).get("provider"),
        "page_fetch_fallbacks": payload.get("providers", {}).get("page_fetch", {}).get("fallbacks"),
        "production_agent_route": payload.get("production_agent", {}).get("route"),
        "orchestration_runtime": payload.get("orchestration", {}).get("current_runtime"),
        "orchestration_decision": payload.get("orchestration", {}).get("decision"),
    }


def _check_cookie_session_persistence(base_url: str, timeout: float) -> dict[str, Any]:
    cookie_jar = CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))
    first, first_meta = _request_json(
        opener,
        "GET",
        urljoin(base_url, "api/status"),
        timeout=timeout,
        headers={},
    )
    cookie_session = _cookie_value(cookie_jar, "fitcheck_session_id")
    second, second_meta = _request_json(
        opener,
        "GET",
        urljoin(base_url, "api/status"),
        timeout=timeout,
        headers={},
    )
    ok = (
        bool(cookie_session)
        and first.get("session_id") == cookie_session
        and second.get("session_id") == cookie_session
        and first_meta.get("x_fitcheck_session_id") == cookie_session
        and second_meta.get("x_fitcheck_session_id") == cookie_session
    )
    return {
        "check": "cookie_session_persistence",
        "status": "completed" if ok else "failed",
        "first_session_id": first.get("session_id"),
        "second_session_id": second.get("session_id"),
        "cookie_session_id": cookie_session,
        "first_response_header_session_id": first_meta.get("x_fitcheck_session_id"),
        "second_response_header_session_id": second_meta.get("x_fitcheck_session_id"),
    }


def _check_openapi(opener, base_url: str, timeout: float, headers: dict[str, str]) -> dict[str, Any]:
    payload, meta = _request_json(opener, "GET", urljoin(base_url, "openapi.json"), timeout=timeout, headers=headers)
    schemas = payload.get("components", {}).get("schemas", {})
    paths = payload.get("paths", {})
    expected_schemas = {
        "AgentReplyAnswer",
        "AgentRunCreateRequest",
        "AgentRunReplyRequest",
        "AgentRunListResponse",
        "AgentReportResponse",
        "AgentRunSessionInfoResponse",
        "AgentSdkEventsResponse",
        "ApiStatusResponse",
        "OrchestrationStatusResponse",
        "Attachment",
        "AgentRunResponse",
        "ImageUploadRequest",
        "ImageUploadResponse",
        "WebCandidateRunRequest",
        "WebCandidateRunResponse",
        "TraceEntryResponse",
        "SearchCandidateResponse",
        "SearchQueryQualityResponse",
        "SearchDomainPolicyResponse",
        "SearchAttemptResponse",
        "ToolResultErrorResponse",
        "ToolResultResponse",
        "ToolSuggestionResponse",
        "RejectedSearchCandidateResponse",
        "ToolObservationResponse",
        "AgentStateEventResponse",
        "GroundingEvidenceSourceResponse",
        "GroundingEvidenceItemResponse",
        "CitationBadgeResponse",
        "RetryBadgeResponse",
        "GroundingValidationResponse",
    }
    missing_schemas = sorted(expected_schemas - set(schemas))
    grounding_properties = schemas.get("GroundingValidationResponse", {}).get("properties", {})
    expected_grounding_properties = {
        "suspicious_numbers",
        "suspicious_urls",
        "suspicious_material_claims",
        "suspicious_score_claims",
        "suspicious_identity_claims",
        "suspicious_style_claims",
        "suspicious_fit_claims",
        "suspicious_evidence_claims",
        "grounding_evidence",
        "citation_badges",
    }
    missing_grounding_properties = sorted(
        expected_grounding_properties - set(grounding_properties)
    )
    observation_properties = schemas.get("ToolObservationResponse", {}).get("properties", {})
    expected_observation_properties = {
        "result",
        "result_status",
        "provider_error_code",
        "provider_error_category",
        "retry_badges",
    }
    missing_observation_properties = sorted(
        expected_observation_properties - set(observation_properties)
    )
    result_properties = schemas.get("ToolResultResponse", {}).get("properties", {})
    expected_result_properties = {
        "status",
        "error",
        "provider_error_code",
        "provider_error_category",
        "retry_badges",
        "next_tool_suggestions",
        "query",
        "query_quality",
        "domain_policy",
        "search_attempt",
        "candidate_filter_policy",
        "candidate_count",
        "candidates",
        "rejected_candidate_count",
        "rejected_candidates",
    }
    missing_tool_result_properties = sorted(
        expected_result_properties - set(result_properties)
    )
    event_properties = schemas.get("AgentStateEventResponse", {}).get("properties", {})
    expected_event_properties = {
        "error",
        "latest_observation",
        "trace_delta",
    }
    missing_event_properties = sorted(expected_event_properties - set(event_properties))
    sdk_event_properties = schemas.get("AgentSdkEventsResponse", {}).get("properties", {})
    expected_sdk_event_properties = {
        "raw_event_count",
        "projected_event_count",
        "latest_source_event_seq",
        "next_after",
        "cursor_policy",
        "terminal_event_seq",
        "terminal_event_included",
        "final_output_included",
    }
    missing_sdk_event_properties = sorted(
        expected_sdk_event_properties - set(sdk_event_properties)
    )
    if event_properties.get("latest_observation", {}).get("anyOf", [{}])[0].get("$ref") != "#/components/schemas/ToolObservationResponse":
        missing_event_properties.append("latest_observation:$ref:ToolObservationResponse")
    if observation_properties.get("result", {}).get("$ref") != "#/components/schemas/ToolResultResponse":
        missing_observation_properties.append("result:$ref:ToolResultResponse")
    if result_properties.get("candidates", {}).get("items", {}).get("$ref") != "#/components/schemas/SearchCandidateResponse":
        missing_tool_result_properties.append("candidates:$ref:SearchCandidateResponse")
    if result_properties.get("rejected_candidates", {}).get("items", {}).get("$ref") != "#/components/schemas/RejectedSearchCandidateResponse":
        missing_tool_result_properties.append("rejected_candidates:$ref:RejectedSearchCandidateResponse")
    if result_properties.get("query_quality", {}).get("anyOf", [{}])[0].get("$ref") != "#/components/schemas/SearchQueryQualityResponse":
        missing_tool_result_properties.append("query_quality:$ref:SearchQueryQualityResponse")
    if result_properties.get("domain_policy", {}).get("anyOf", [{}])[0].get("$ref") != "#/components/schemas/SearchDomainPolicyResponse":
        missing_tool_result_properties.append("domain_policy:$ref:SearchDomainPolicyResponse")
    if result_properties.get("search_attempt", {}).get("anyOf", [{}])[0].get("$ref") != "#/components/schemas/SearchAttemptResponse":
        missing_tool_result_properties.append("search_attempt:$ref:SearchAttemptResponse")
    candidate_properties = schemas.get("SearchCandidateResponse", {}).get("properties", {})
    if candidate_properties.get("domain_policy", {}).get("anyOf", [{}])[0].get("$ref") != "#/components/schemas/SearchDomainPolicyResponse":
        missing_tool_result_properties.append("candidate.domain_policy:$ref:SearchDomainPolicyResponse")
    attempt_properties = schemas.get("SearchAttemptResponse", {}).get("properties", {})
    if attempt_properties.get("current_query_quality", {}).get("anyOf", [{}])[0].get("$ref") != "#/components/schemas/SearchQueryQualityResponse":
        missing_tool_result_properties.append("search_attempt.current_query_quality:$ref:SearchQueryQualityResponse")
    expected_refs = {
        ("/api/status", "get"): "ApiStatusResponse",
        ("/api/uploads/images", "post"): "ImageUploadResponse",
        ("/api/agent/runs", "post"): "AgentRunResponse",
        ("/api/agent/runs", "get"): "AgentRunListResponse",
        ("/api/agent/runs/{run_id}/report", "get"): "AgentReportResponse",
        ("/api/agent/runs/{run_id}/session-info", "get"): "AgentRunSessionInfoResponse",
        ("/api/agent/runs/{run_id}/sdk-events", "get"): "AgentSdkEventsResponse",
        ("/api/results/web-candidates", "get"): "WebCandidateRunResponse",
    }
    missing_refs = []
    for (path, method), schema_name in expected_refs.items():
        schema = (
            paths.get(path, {})
            .get(method, {})
            .get("responses", {})
            .get("200", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        if schema.get("$ref") != f"#/components/schemas/{schema_name}":
            missing_refs.append(f"{method.upper()} {path} -> {schema_name}")
    sse_schema = (
        paths.get("/api/agent/runs/{run_id}/events", {})
        .get("get", {})
        .get("responses", {})
        .get("200", {})
        .get("content", {})
        .get("text/event-stream", {})
        .get("schema", {})
    )
    if sse_schema.get("$ref") != "#/components/schemas/AgentStateEventResponse":
        missing_refs.append("GET /api/agent/runs/{run_id}/events -> AgentStateEventResponse")
    expected_request_refs = {
        ("/api/agent/runs", "post"): "AgentRunCreateRequest",
        ("/api/agent/runs/{run_id}/reply", "post"): "AgentRunReplyRequest",
        ("/api/uploads/images", "post"): "ImageUploadRequest",
        ("/api/run/web-candidates", "post"): "WebCandidateRunRequest",
    }
    missing_request_refs = []
    for (path, method), schema_name in expected_request_refs.items():
        schema = (
            paths.get(path, {})
            .get(method, {})
            .get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        if schema.get("$ref") != f"#/components/schemas/{schema_name}":
            missing_request_refs.append(f"{method.upper()} {path} -> {schema_name}")
    return {
        "check": "openapi_route_models",
        "status": "completed"
        if not missing_schemas
        and not missing_grounding_properties
        and not missing_observation_properties
        and not missing_tool_result_properties
        and not missing_event_properties
        and not missing_refs
        and not missing_request_refs
        else "failed",
        "http_status": meta["status"],
        "missing_schemas": missing_schemas,
        "missing_grounding_properties": missing_grounding_properties,
        "missing_observation_properties": missing_observation_properties,
        "missing_tool_result_properties": missing_tool_result_properties,
        "missing_event_properties": missing_event_properties,
        "missing_sdk_event_properties": missing_sdk_event_properties,
        "missing_response_refs": missing_refs,
        "missing_request_refs": missing_request_refs,
    }


def _check_request_body_validation(opener, base_url: str, timeout: float, headers: dict[str, str]) -> dict[str, Any]:
    upload_error, upload_meta = _request_json(
        opener,
        "POST",
        urljoin(base_url, "api/uploads/images"),
        payload={},
        timeout=timeout,
        headers=headers,
        allowed_statuses={422},
    )
    agent_create_error, agent_create_meta = _request_json(
        opener,
        "POST",
        urljoin(base_url, "api/agent/runs"),
        payload={"attachments": "not-a-list"},
        timeout=timeout,
        headers=headers,
        allowed_statuses={422},
    )
    empty_agent_create_error, empty_agent_create_meta = _request_json(
        opener,
        "POST",
        urljoin(base_url, "api/agent/runs"),
        payload={},
        timeout=timeout,
        headers=headers,
        allowed_statuses={422},
    )
    reply_error, reply_meta = _request_json(
        opener,
        "POST",
        urljoin(base_url, "api/agent/runs/http-smoke-not-found/reply"),
        payload={"answers": "not-a-list"},
        timeout=timeout,
        headers=headers,
        allowed_statuses={422},
    )
    empty_reply_error, empty_reply_meta = _request_json(
        opener,
        "POST",
        urljoin(base_url, "api/agent/runs/http-smoke-not-found/reply"),
        payload={},
        timeout=timeout,
        headers=headers,
        allowed_statuses={422},
    )
    ok = (
        upload_meta["status"] == 422
        and _validation_error_contains(upload_error, "media_type")
        and agent_create_meta["status"] == 422
        and _validation_error_contains(agent_create_error, "attachments")
        and empty_agent_create_meta["status"] == 422
        and _validation_error_message_contains(
            empty_agent_create_error,
            "Agent run requires goal, free_text, candidate_url, or attachments.",
        )
        and reply_meta["status"] == 422
        and _validation_error_contains(reply_error, "answers")
        and empty_reply_meta["status"] == 422
        and _validation_error_message_contains(
            empty_reply_error,
            "Agent reply requires message, answers, or attachments.",
        )
    )
    return {
        "check": "request_body_validation",
        "status": "completed" if ok else "failed",
        "upload_status": upload_meta["status"],
        "upload_error_fields": _validation_error_fields(upload_error),
        "agent_create_status": agent_create_meta["status"],
        "agent_create_error_fields": _validation_error_fields(agent_create_error),
        "empty_agent_create_status": empty_agent_create_meta["status"],
        "reply_status": reply_meta["status"],
        "reply_error_fields": _validation_error_fields(reply_error),
        "empty_reply_status": empty_reply_meta["status"],
    }


def _check_uploads(opener, base_url: str, timeout: float, headers: dict[str, str]) -> dict[str, Any]:
    invalid, _ = _request_json(
        opener,
        "POST",
        urljoin(base_url, "api/uploads/images"),
        payload={
            "filename": "note.txt",
            "media_type": "text/plain",
            "base64": base64.b64encode(b"not an image").decode("ascii"),
        },
        timeout=timeout,
        headers=headers,
    )
    valid, meta = _request_json(
        opener,
        "POST",
        urljoin(base_url, "api/uploads/images"),
        payload={
            "filename": "smoke.png",
            "media_type": "image/png",
            "base64": PNG_1X1_BASE64,
        },
        timeout=timeout,
        headers=headers,
    )
    attachment = valid.get("attachment") or {}
    ok = (
        invalid.get("status") == "failed"
        and invalid.get("error", {}).get("code") == "UNSUPPORTED_IMAGE_TYPE"
        and valid.get("status") == "completed"
        and attachment.get("media_type") == "image/png"
        and str(attachment.get("url", "")).startswith("/outputs/uploads/")
    )
    return {
        "check": "upload_success_and_validation",
        "status": "completed" if ok else "failed",
        "http_status": meta["status"],
        "invalid_error_code": invalid.get("error", {}).get("code"),
        "attachment_id": attachment.get("id"),
        "attachment_url": attachment.get("url"),
    }


def _check_agent_configuration_required_sse_terminal(
    opener,
    base_url: str,
    timeout: float,
    headers: dict[str, str],
) -> dict[str, Any]:
    session_id = headers.get("X-FitCheck-Session-Id", "http_api_smoke")
    run = _seed_configuration_required_agent_run(session_id)
    run_id = run["run_id"]
    event_body, event_meta = _request_text(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}/events"),
        timeout=timeout,
        headers=headers,
    )
    event = _parse_first_sse_event(event_body)
    data = event.get("data", {})
    latest_observation = data.get("latest_observation") or {}
    ok = (
        event_meta["content_type"].startswith("text/event-stream")
        and event.get("event") == "agent_state"
        and event.get("id") == f"{run_id}:trace:3"
        and data.get("status") == "configuration_required"
        and data.get("error", {}).get("code") == "GOOGLE_CSE_CONFIGURATION_REQUIRED"
        and latest_observation.get("tool") == "search_web_candidates"
        and latest_observation.get("provider_error_code") == "GOOGLE_CSE_CONFIGURATION_REQUIRED"
        and (latest_observation.get("retry_badges") or [{}])[0].get("status") == "not_started"
    )
    return {
        "check": "agent_configuration_required_sse_terminal",
        "status": "completed" if ok else "failed",
        "run_id": run_id,
        "sse_id": event.get("id"),
        "sse_status": data.get("status"),
        "sse_error_code": data.get("error", {}).get("code"),
        "latest_observation_tool": latest_observation.get("tool"),
        "latest_observation_provider_error_code": latest_observation.get("provider_error_code"),
        "latest_observation_retry_status": (latest_observation.get("retry_badges") or [{}])[0].get("status"),
    }


def _check_agent_not_found(opener, base_url: str, timeout: float, headers: dict[str, str]) -> dict[str, Any]:
    payload, meta = _request_json(
        opener,
        "GET",
        urljoin(base_url, "api/agent/runs/http-smoke-not-found"),
        timeout=timeout,
        headers=headers,
    )
    return {
        "check": "agent_not_found_json",
        "status": "completed" if payload.get("status") == "not_found" else "failed",
        "http_status": meta["status"],
        "payload_status": payload.get("status"),
    }


def _check_agent_session_isolation(opener, base_url: str, timeout: float, headers: dict[str, str]) -> dict[str, Any]:
    owner_session = headers["X-FitCheck-Session-Id"]
    other_session = f"{owner_session}_other"
    other_headers = {"X-FitCheck-Session-Id": other_session}
    run = _seed_completed_agent_run(owner_session)
    run_id = run["run_id"]

    owner_detail, _ = _request_json(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}"),
        timeout=timeout,
        headers=headers,
    )
    other_detail, _ = _request_json(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}"),
        timeout=timeout,
        headers=other_headers,
    )
    owner_report, _ = _request_json(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}/report"),
        timeout=timeout,
        headers=headers,
    )
    owner_session_info, _ = _request_json(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}/session-info"),
        timeout=timeout,
        headers=headers,
    )
    owner_sdk_events, _ = _request_json(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}/sdk-events"),
        timeout=timeout,
        headers=headers,
    )
    owner_sdk_events_after, _ = _request_json(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}/sdk-events?after=1"),
        timeout=timeout,
        headers=headers,
    )
    owner_sdk_events_terminal, _ = _request_json(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}/sdk-events?after=2"),
        timeout=timeout,
        headers=headers,
    )
    owner_sdk_events_caught_up, _ = _request_json(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}/sdk-events?after=3"),
        timeout=timeout,
        headers=headers,
    )
    other_report, _ = _request_json(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}/report"),
        timeout=timeout,
        headers=other_headers,
    )
    other_session_info, _ = _request_json(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}/session-info"),
        timeout=timeout,
        headers=other_headers,
    )
    other_sdk_events, _ = _request_json(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}/sdk-events"),
        timeout=timeout,
        headers=other_headers,
    )
    other_reply, _ = _request_json(
        opener,
        "POST",
        urljoin(base_url, f"api/agent/runs/{run_id}/reply"),
        payload={"message": "다른 세션에서 보낸 reply는 거부되어야 합니다."},
        timeout=timeout,
        headers=other_headers,
    )
    owner_event_body, owner_event_meta = _request_text(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}/events"),
        timeout=timeout,
        headers=headers,
    )
    other_event_body, other_event_meta = _request_text(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}/events"),
        timeout=timeout,
        headers=other_headers,
    )
    owner_event = _parse_first_sse_event(owner_event_body)
    other_event = _parse_first_sse_event(other_event_body)
    owner_list, _ = _request_json(
        opener,
        "GET",
        urljoin(base_url, "api/agent/runs"),
        timeout=timeout,
        headers=headers,
    )
    other_list, _ = _request_json(
        opener,
        "GET",
        urljoin(base_url, "api/agent/runs"),
        timeout=timeout,
        headers=other_headers,
    )
    owner_ids = {item.get("run_id") for item in owner_list.get("runs", [])}
    other_ids = {item.get("run_id") for item in other_list.get("runs", [])}
    owner_report_grounding = owner_report.get("grounding_validation") or {}
    owner_sse_grounding = (
        owner_event.get("data", {}).get("final_report", {}).get("grounding_validation") or {}
    )
    expected_call_id = "http_smoke_search_call"
    owner_detail_trace = owner_detail.get("trace") or []
    owner_detail_tool_call = _first_trace_step(
        owner_detail_trace,
        step_type="tool_call",
        name="search_web_candidates",
    )
    owner_detail_tool_output = _first_trace_step(
        owner_detail_trace,
        step_type="tool_output",
        name="search_web_candidates",
    )
    owner_detail_observations = owner_detail.get("state", {}).get("observations") or []
    owner_sse_latest_trace = owner_event.get("data", {}).get("latest_trace") or []
    owner_sse_latest_step = owner_sse_latest_trace[0] if owner_sse_latest_trace else {}
    owner_sse_latest_observation = owner_event.get("data", {}).get("latest_observation") or {}
    owner_sdk_event_types = [item.get("type") for item in owner_sdk_events.get("events") or []]
    owner_sdk_final_outputs = [
        item for item in owner_sdk_events.get("events") or [] if item.get("type") == "final_output"
    ]
    owner_sdk_final_output = owner_sdk_final_outputs[0] if owner_sdk_final_outputs else {}
    owner_sdk_final_grounding = owner_sdk_final_output.get("grounding_validation") or {}
    ok = (
        owner_detail.get("run_id") == run_id
        and owner_detail.get("session_id") == owner_session
        and len(owner_detail_trace) == 3
        and owner_detail_tool_call.get("input", {}).get("call_id") == expected_call_id
        and owner_detail_tool_output.get("output", {}).get("call_id") == expected_call_id
        and (owner_detail_observations or [{}])[0].get("call_id") == expected_call_id
        and other_detail.get("status") == "not_found"
        and owner_report.get("run_id") == run_id
        and (owner_report.get("citation_badges") or [{}])[0].get("label") == "[G1]"
        and owner_report_grounding.get("suspicious_identity_claims") == []
        and owner_report_grounding.get("suspicious_score_claims") == []
        and owner_report_grounding.get("suspicious_style_claims") == []
        and owner_report_grounding.get("suspicious_fit_claims") == []
        and owner_report_grounding.get("suspicious_evidence_claims") == []
        and owner_session_info.get("status") == "completed"
        and owner_session_info.get("source") == "fitcheck_agent_session_info"
        and owner_session_info.get("latest_event_seq") == 3
        and owner_session_info.get("stream", {}).get("sdk_events_after") == 3
        and owner_session_info.get("source_endpoints", {}).get("sdk_events")
        == f"/api/agent/runs/{run_id}/sdk-events"
        and owner_sdk_events.get("status") == "completed"
        and owner_sdk_events.get("source") == "fitcheck_agent_sdk_event_projection"
        and owner_sdk_events.get("raw_event_count") == 3
        and owner_sdk_events.get("projected_event_count") == len(owner_sdk_events.get("events") or [])
        and owner_sdk_events.get("next_after") == 3
        and owner_sdk_events.get("cursor_policy", {}).get("cursor_basis") == "raw_source_event_seq"
        and owner_sdk_events.get("cursor_policy", {}).get("cursor_semantics") == "exclusive"
        and owner_sdk_events.get("terminal_event_seq") == 3
        and owner_sdk_events.get("terminal_event_included") is True
        and owner_sdk_events.get("final_output_included") is True
        and "tool_progress" in owner_sdk_event_types
        and any(
            item.get("type") == "tool_progress" and item.get("backend_owned") is True
            for item in owner_sdk_events.get("events") or []
        )
        and any(
            item.get("subtype") == "session_state_changed" and item.get("state") == "idle"
            for item in owner_sdk_events.get("events") or []
        )
        and owner_sdk_final_output.get("subtype") == "grounded_report"
        and owner_sdk_final_output.get("grounded") is True
        and owner_sdk_final_grounding.get("valid") is True
        and owner_sdk_final_grounding.get("reason") == "ok"
        and owner_sdk_final_grounding.get("suspicious_evidence_claims") == []
        and (owner_sdk_final_output.get("citation_badges") or [{}])[0].get("label") == "[G1]"
        and int(owner_sdk_final_output.get("citation_badge_count") or 0) == 1
        and owner_sdk_events_after.get("raw_event_count") == 2
        and owner_sdk_events_after.get("next_after") == 3
        and owner_sdk_events_after.get("terminal_event_included") is True
        and owner_sdk_events_after.get("final_output_included") is True
        and owner_sdk_events_terminal.get("raw_event_count") == 1
        and owner_sdk_events_terminal.get("next_after") == 3
        and owner_sdk_events_terminal.get("terminal_event_included") is True
        and owner_sdk_events_terminal.get("final_output_included") is True
        and any(item.get("type") == "final_output" for item in owner_sdk_events_terminal.get("events") or [])
        and owner_sdk_events_caught_up.get("raw_event_count") == 0
        and owner_sdk_events_caught_up.get("projected_event_count") == 0
        and owner_sdk_events_caught_up.get("next_after") == 3
        and owner_sdk_events_caught_up.get("terminal_event_included") is False
        and owner_sdk_events_caught_up.get("final_output_included") is False
        and other_report.get("status") == "not_found"
        and other_session_info.get("status") == "not_found"
        and other_sdk_events.get("status") == "not_found"
        and other_reply.get("status") == "not_found"
        and owner_event_meta["content_type"].startswith("text/event-stream")
        and other_event_meta["content_type"].startswith("text/event-stream")
        and owner_event_meta["cache_control"] == "no-cache"
        and other_event_meta["x_accel_buffering"] == "no"
        and owner_event.get("event") == "agent_state"
        and owner_event.get("id") == f"{run_id}:trace:3"
        and owner_event.get("retry") == 3000
        and owner_event.get("data", {}).get("event_id") == owner_event.get("id")
        and owner_event.get("data", {}).get("retry_ms") == 3000
        and owner_event.get("data", {}).get("run_id") == run_id
        and owner_event.get("data", {}).get("status") == "completed"
        and owner_event.get("data", {}).get("trace_count") == 3
        and owner_sse_latest_step.get("step_type") == "tool_output"
        and owner_sse_latest_step.get("output", {}).get("call_id") == expected_call_id
        and owner_sse_latest_observation.get("call_id") == expected_call_id
        and owner_sse_latest_observation.get("tool") == "search_web_candidates"
        and owner_sse_latest_observation.get("result", {}).get("provider") == "http_smoke_fake_search"
        and (owner_event.get("data", {}).get("citation_badges") or [{}])[0].get("label") == "[G1]"
        and owner_sse_grounding.get("suspicious_identity_claims") == []
        and owner_sse_grounding.get("suspicious_score_claims") == []
        and owner_sse_grounding.get("suspicious_style_claims") == []
        and owner_sse_grounding.get("suspicious_fit_claims") == []
        and owner_sse_grounding.get("suspicious_evidence_claims") == []
        and other_event.get("event") == "agent_state"
        and other_event.get("id") == f"{run_id}:trace:0"
        and other_event.get("retry") == 3000
        and other_event.get("data", {}).get("status") == "not_found"
        and run_id in owner_ids
        and run_id not in other_ids
    )
    return {
        "check": "agent_run_session_isolation",
        "status": "completed" if ok else "failed",
        "run_id": run_id,
        "owner_session": owner_session,
        "other_session": other_session,
        "owner_detail_status": owner_detail.get("status"),
        "other_detail_status": other_detail.get("status"),
        "owner_report_status": owner_report.get("status"),
        "other_report_status": other_report.get("status"),
        "owner_session_info_status": owner_session_info.get("status"),
        "other_session_info_status": other_session_info.get("status"),
        "owner_session_info_latest_event_seq": owner_session_info.get("latest_event_seq"),
        "owner_sdk_events_status": owner_sdk_events.get("status"),
        "other_sdk_events_status": other_sdk_events.get("status"),
        "owner_sdk_events_raw_count": owner_sdk_events.get("raw_event_count"),
        "owner_sdk_events_projected_count": owner_sdk_events.get("projected_event_count"),
        "owner_sdk_events_after_raw_count": owner_sdk_events_after.get("raw_event_count"),
        "owner_sdk_events_after_final_output_included": owner_sdk_events_after.get("final_output_included"),
        "owner_sdk_events_terminal_raw_count": owner_sdk_events_terminal.get("raw_event_count"),
        "owner_sdk_events_terminal_final_output_included": owner_sdk_events_terminal.get("final_output_included"),
        "owner_sdk_events_caught_up_raw_count": owner_sdk_events_caught_up.get("raw_event_count"),
        "owner_sdk_events_caught_up_projected_count": owner_sdk_events_caught_up.get("projected_event_count"),
        "owner_sdk_events_next_after": owner_sdk_events.get("next_after"),
        "owner_sdk_events_cursor_policy": owner_sdk_events.get("cursor_policy"),
        "owner_sdk_events_terminal_included": owner_sdk_events.get("terminal_event_included"),
        "owner_sdk_events_final_output_included": owner_sdk_events.get("final_output_included"),
        "owner_sdk_event_types": owner_sdk_event_types,
        "owner_sdk_final_output_count": len(owner_sdk_final_outputs),
        "owner_sdk_final_output_grounded": owner_sdk_final_output.get("grounded"),
        "owner_sdk_final_output_grounding_reason": owner_sdk_final_grounding.get("reason"),
        "owner_sdk_final_output_suspicious_evidence_claims": owner_sdk_final_grounding.get(
            "suspicious_evidence_claims"
        ),
        "owner_sdk_final_output_citation_badge_count": owner_sdk_final_output.get(
            "citation_badge_count"
        ),
        "other_reply_status": other_reply.get("status"),
        "owner_sse_status": owner_event.get("data", {}).get("status"),
        "other_sse_status": other_event.get("data", {}).get("status"),
        "owner_sse_id": owner_event.get("id"),
        "other_sse_id": other_event.get("id"),
        "owner_sse_retry": owner_event.get("retry"),
        "sse_cache_control": owner_event_meta.get("cache_control"),
        "sse_accel_buffering": other_event_meta.get("x_accel_buffering"),
        "owner_detail_trace_count": len(owner_detail_trace),
        "owner_detail_tool_call_call_id": owner_detail_tool_call.get("input", {}).get("call_id"),
        "owner_detail_tool_output_call_id": owner_detail_tool_output.get("output", {}).get("call_id"),
        "owner_detail_observation_call_id": (owner_detail_observations or [{}])[0].get("call_id"),
        "owner_sse_trace_count": owner_event.get("data", {}).get("trace_count"),
        "owner_sse_latest_trace_call_id": owner_sse_latest_step.get("output", {}).get("call_id"),
        "owner_sse_latest_observation_tool": owner_sse_latest_observation.get("tool"),
        "owner_sse_latest_observation_call_id": owner_sse_latest_observation.get("call_id"),
        "owner_report_citation_badge_count": len(owner_report.get("citation_badges") or []),
        "owner_sse_citation_badge_count": len(owner_event.get("data", {}).get("citation_badges") or []),
        "owner_report_suspicious_identity_claims": owner_report_grounding.get(
            "suspicious_identity_claims"
        ),
        "owner_report_suspicious_score_claims": owner_report_grounding.get(
            "suspicious_score_claims"
        ),
        "owner_report_suspicious_style_claims": owner_report_grounding.get(
            "suspicious_style_claims"
        ),
        "owner_report_suspicious_fit_claims": owner_report_grounding.get(
            "suspicious_fit_claims"
        ),
        "owner_report_suspicious_evidence_claims": owner_report_grounding.get(
            "suspicious_evidence_claims"
        ),
        "owner_sse_suspicious_identity_claims": owner_sse_grounding.get(
            "suspicious_identity_claims"
        ),
        "owner_sse_suspicious_score_claims": owner_sse_grounding.get(
            "suspicious_score_claims"
        ),
        "owner_sse_suspicious_style_claims": owner_sse_grounding.get(
            "suspicious_style_claims"
        ),
        "owner_sse_suspicious_fit_claims": owner_sse_grounding.get(
            "suspicious_fit_claims"
        ),
        "owner_sse_suspicious_evidence_claims": owner_sse_grounding.get(
            "suspicious_evidence_claims"
        ),
        "owner_list_contains_run": run_id in owner_ids,
        "other_list_contains_run": run_id in other_ids,
    }


def _first_trace_step(trace: list[dict[str, Any]], *, step_type: str, name: str) -> dict[str, Any]:
    for step in trace:
        if step.get("step_type") == step_type and step.get("name") == name:
            return step
    return {}


def _check_sse_not_found(opener, base_url: str, timeout: float, headers: dict[str, str]) -> dict[str, Any]:
    body, meta = _request_text(
        opener,
        "GET",
        urljoin(base_url, "api/agent/runs/http-smoke-not-found/events"),
        timeout=timeout,
        headers=headers,
    )
    parsed = _parse_first_sse_event(body)
    ok = (
        meta["content_type"].startswith("text/event-stream")
        and meta["cache_control"] == "no-cache"
        and meta["x_accel_buffering"] == "no"
        and parsed.get("event") == "agent_state"
        and parsed.get("id") == "http-smoke-not-found:trace:0"
        and parsed.get("retry") == 3000
        and parsed.get("data", {}).get("event_id") == parsed.get("id")
        and parsed.get("data", {}).get("retry_ms") == 3000
        and parsed.get("data", {}).get("status") == "not_found"
    )
    return {
        "check": "sse_not_found_event_stream",
        "status": "completed" if ok else "failed",
        "http_status": meta["status"],
        "content_type": meta["content_type"],
        "cache_control": meta.get("cache_control"),
        "x_accel_buffering": meta.get("x_accel_buffering"),
        "event": parsed.get("event"),
        "event_id": parsed.get("id"),
        "retry": parsed.get("retry"),
        "payload_status": parsed.get("data", {}).get("status"),
    }


def _check_agent_resumable_llm_failure_sse_terminal(opener, base_url: str, timeout: float, headers: dict[str, str]) -> dict[str, Any]:
    session_id = headers["X-FitCheck-Session-Id"]
    run = _seed_resumable_llm_failure_agent_run(session_id)
    run_id = run["run_id"]
    detail, detail_meta = _request_json(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}"),
        timeout=timeout,
        headers=headers,
    )
    body, event_meta = _request_text(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{run_id}/events"),
        timeout=timeout,
        headers=headers,
    )
    event = _parse_first_sse_event(body)
    event_data = event.get("data", {})
    detail_error = detail.get("error") or {}
    event_error = event_data.get("error") or {}
    ok = (
        detail_meta["status"] == 200
        and detail.get("status") == "failed"
        and detail_error.get("provider_error_code") == "HTTP_429_RATE_LIMITED"
        and detail_error.get("provider_error_category") == "rate_limit"
        and detail_error.get("retryable") is True
        and detail_error.get("resume_available") is True
        and len(detail.get("state", {}).get("pending_tool_outputs") or []) == 1
        and event_meta["content_type"].startswith("text/event-stream")
        and event.get("event") == "agent_state"
        and event.get("id") == f"{run_id}:trace:3"
        and event_data.get("status") == "failed"
        and event_error.get("provider_error_code") == "HTTP_429_RATE_LIMITED"
        and event_error.get("resume_available") is True
    )
    return {
        "check": "agent_resumable_llm_failure_sse_terminal",
        "status": "completed" if ok else "failed",
        "run_id": run_id,
        "detail_status": detail.get("status"),
        "detail_error_code": detail_error.get("code"),
        "detail_provider_error_code": detail_error.get("provider_error_code"),
        "detail_provider_error_category": detail_error.get("provider_error_category"),
        "detail_retryable": detail_error.get("retryable"),
        "detail_resume_available": detail_error.get("resume_available"),
        "detail_pending_tool_output_count": len(detail.get("state", {}).get("pending_tool_outputs") or []),
        "sse_status": event_data.get("status"),
        "sse_error_code": event_error.get("code"),
        "sse_provider_error_code": event_error.get("provider_error_code"),
        "sse_resume_available": event_error.get("resume_available"),
        "sse_event_id": event.get("id"),
    }


def _seed_completed_agent_run(session_id: str) -> dict[str, Any]:
    store = build_agent_run_store(AGENT_RUNS_PATH)
    run = store.create_run(
        {
            "goal": "http api smoke seeded run",
            "free_text": "HTTP route session isolation smoke",
            "session_id": session_id,
        },
        {
            "provider": "http_smoke_fake_llm",
            "model": "http-smoke",
            "api_style": "responses",
            "tool_execution": "backend_owned_functions",
            "hosted_tools_supported": False,
            "local_function_tools_supported": True,
            "status": "connected",
        },
    )
    run["status"] = "completed"
    tool_call_id = "http_smoke_search_call"
    run["trace"] = [
        {
            "step_index": 1,
            "step_type": "run",
            "name": "http_api_smoke_seed",
            "status": "completed",
            "input": {},
            "output": {"session_id": session_id},
            "created_at": run["created_at"],
        },
        {
            "step_index": 2,
            "step_type": "tool_call",
            "name": "search_web_candidates",
            "status": "started",
            "input": {
                "call_id": tool_call_id,
                "arguments": {"query": "http smoke 반팔티"},
                "risk": "external_search_io",
            },
            "output": {},
            "created_at": run["created_at"],
        },
        {
            "step_index": 3,
            "step_type": "tool_output",
            "name": "search_web_candidates",
            "status": "completed",
            "input": {},
            "output": {
                "call_id": tool_call_id,
                "status": "completed",
                "provider": "http_smoke_fake_search",
                "candidate_count": 0,
            },
            "created_at": run["created_at"],
        },
    ]
    run["state"]["observations"] = [
        {
            "tool": "search_web_candidates",
            "call_id": tool_call_id,
            "arguments": {"query": "http smoke 반팔티"},
            "result": {
                "status": "completed",
                "provider": "http_smoke_fake_search",
                "candidate_count": 0,
            },
        }
    ]
    run["final_report"] = {
        "summary": "HTTP smoke seeded run.",
        "grounded": True,
        "grounding": "seeded_http_smoke",
        "grounding_validation": {
            "valid": True,
            "reason": "ok",
            "suspicious_numbers": [],
            "suspicious_urls": [],
            "suspicious_material_claims": [],
            "suspicious_score_claims": [],
            "suspicious_identity_claims": [],
            "suspicious_style_claims": [],
            "suspicious_fit_claims": [],
            "suspicious_evidence_claims": [],
            "grounding_evidence": [],
            "citation_badges": [
                {
                    "id": "G1",
                    "label": "[G1]",
                    "claim_type": "number",
                    "claim": "1",
                    "source_path": "observations[0].result.status",
                    "source_value_preview": "completed",
                    "observation_index": 0,
                    "source_kind": "tool_result",
                    "source_count": 1,
                }
            ],
            "has_match_observation": True,
            "has_report_observation": False,
        },
    }
    return store.update_run(run)


def _seed_configuration_required_agent_run(session_id: str) -> dict[str, Any]:
    store = build_agent_run_store(AGENT_RUNS_PATH)
    run = store.create_run(
        {
            "goal": "http smoke configuration required",
            "session_id": session_id,
        },
        {
            "provider": "fake_responses_api",
            "model": "fake-tool-calling-model",
            "status": "connected",
        },
    )
    run["status"] = "configuration_required"
    run["error"] = {
        "code": "GOOGLE_CSE_CONFIGURATION_REQUIRED",
        "message": "Set Google Programmable Search API key and search engine id.",
        "provider_error_code": "GOOGLE_CSE_CONFIGURATION_REQUIRED",
        "provider_error_category": "configuration",
    }
    tool_call_id = "http_smoke_config_search_call"
    run["trace"] = [
        {
            "step_index": 1,
            "step_type": "run",
            "name": "http_api_smoke_config_seed",
            "status": "completed",
            "input": {},
            "output": {"session_id": session_id},
            "created_at": run["created_at"],
        },
        {
            "step_index": 2,
            "step_type": "tool_call",
            "name": "search_web_candidates",
            "status": "started",
            "input": {
                "call_id": tool_call_id,
                "arguments": {"query": "http smoke google cse"},
                "risk": "external_search_io",
            },
            "output": {},
            "created_at": run["created_at"],
        },
        {
            "step_index": 3,
            "step_type": "tool_output",
            "name": "search_web_candidates",
            "status": "configuration_required",
            "input": {},
            "output": {
                "call_id": tool_call_id,
                "status": "configuration_required",
                "provider": "google_cse_search",
                "provider_error_code": "GOOGLE_CSE_CONFIGURATION_REQUIRED",
            },
            "created_at": run["created_at"],
        },
    ]
    run["state"]["observations"] = [
        {
            "tool": "search_web_candidates",
            "call_id": tool_call_id,
            "arguments": {"query": "http smoke google cse"},
            "result": {
                "status": "configuration_required",
                "provider": "google_cse_search",
                "mode": "live_provider",
                "external_api_called": False,
                "timeout_seconds": 12.0,
                "retry_count": 0,
                "candidate_count": 0,
                "candidates": [],
                "error": {
                    "code": "GOOGLE_CSE_CONFIGURATION_REQUIRED",
                    "message": "Set Google Programmable Search API key and search engine id.",
                },
                "provider_error_code": "GOOGLE_CSE_CONFIGURATION_REQUIRED",
                "provider_error_category": "configuration",
            },
            "provider": "google_cse_search",
            "provider_mode": "live_provider",
            "provider_error_code": "GOOGLE_CSE_CONFIGURATION_REQUIRED",
            "provider_error_category": "configuration",
            "retry_badges": [
                {
                    "id": "R0",
                    "label": "not configured",
                    "status": "not_started",
                    "provider": "google_cse_search",
                    "provider_mode": "live_provider",
                    "retry_count": 0,
                    "retry_error_count": 0,
                    "provider_error_code": "GOOGLE_CSE_CONFIGURATION_REQUIRED",
                    "provider_error_category": "configuration",
                    "timeout_seconds": 12.0,
                    "external_api_called": False,
                    "source_kind": "provider_runtime",
                }
            ],
        }
    ]
    return store.update_run(run)


def _seed_resumable_llm_failure_agent_run(session_id: str) -> dict[str, Any]:
    store = build_agent_run_store(AGENT_RUNS_PATH)
    run = store.create_run(
        {
            "goal": "http smoke resumable llm failure",
            "free_text": "rate limited after first tool output",
            "session_id": session_id,
        },
        {
            "provider": "fake_responses_api",
            "model": "fake-tool-calling-model",
            "api_style": "responses",
            "tool_execution": "backend_owned_functions",
            "hosted_tools_supported": False,
            "local_function_tools_supported": True,
            "status": "connected",
        },
    )
    run["status"] = "failed"
    run["error"] = {
        "code": "LLM_RESPONSE_FAILED",
        "message": "429 Client Error: Too Many Requests for url: https://api.openai.com/v1/responses",
        "provider_error_code": "HTTP_429_RATE_LIMITED",
        "provider_error_category": "rate_limit",
        "retryable": True,
        "resume_available": True,
        "pending_tool_output_count": 1,
    }
    run["state"]["previous_response_id"] = "resp_http_smoke_rate_limited"
    run["state"]["pending_tool_outputs"] = [
        {
            "type": "function_call_output",
            "call_id": "http_smoke_load_memory_call",
            "output": json.dumps({"status": "completed", "closet_item": {"id": "current_liked_item"}}, ensure_ascii=False),
        }
    ]
    run["trace"] = [
        {
            "step_index": 1,
            "step_type": "tool_call",
            "name": "load_closet_memory",
            "status": "started",
            "input": {"call_id": "http_smoke_load_memory_call", "arguments": {}},
            "output": {},
            "created_at": run["created_at"],
        },
        {
            "step_index": 2,
            "step_type": "tool_output",
            "name": "load_closet_memory",
            "status": "completed",
            "input": {},
            "output": {"call_id": "http_smoke_load_memory_call", "status": "completed"},
            "created_at": run["created_at"],
        },
        {
            "step_index": 3,
            "step_type": "guardrail",
            "name": "stop_llm_response_failed",
            "status": "failed",
            "input": {},
            "output": run["error"],
            "created_at": run["created_at"],
        },
    ]
    run["state"]["observations"] = [
        {
            "tool": "load_closet_memory",
            "call_id": "http_smoke_load_memory_call",
            "arguments": {},
            "result": {"status": "completed", "closet_item": {"id": "current_liked_item"}},
        }
    ]
    return store.update_run(run)


def _request_json(
    opener,
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float,
    headers: dict[str, str] | None = None,
    allowed_statuses: set[int] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    body, meta = _request_text(
        opener,
        method,
        url,
        payload=payload,
        timeout=timeout,
        headers=headers,
        allowed_statuses=allowed_statuses,
    )
    return json.loads(body), meta


def _request_text(
    opener,
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float,
    headers: dict[str, str] | None = None,
    allowed_statuses: set[int] | None = None,
) -> tuple[str, dict[str, Any]]:
    data = None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            meta = {
                "status": response.status,
                "content_type": response.headers.get("Content-Type", ""),
                "x_fitcheck_session_id": response.headers.get("X-FitCheck-Session-Id"),
                "cache_control": response.headers.get("Cache-Control"),
                "x_accel_buffering": response.headers.get("X-Accel-Buffering"),
            }
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if allowed_statuses and exc.code in allowed_statuses:
            return body, {
                "status": exc.code,
                "content_type": exc.headers.get("Content-Type", ""),
                "x_fitcheck_session_id": exc.headers.get("X-FitCheck-Session-Id"),
                "cache_control": exc.headers.get("Cache-Control"),
                "x_accel_buffering": exc.headers.get("X-Accel-Buffering"),
            }
        raise SystemExit(f"{method} {url} failed with HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise SystemExit(f"{method} {url} failed: {exc.reason}") from exc
    return body, meta


def _validation_error_fields(payload: dict[str, Any]) -> list[str]:
    fields = []
    for item in payload.get("detail", []):
        loc = item.get("loc") or []
        if loc:
            fields.append(str(loc[-1]))
    return fields


def _validation_error_contains(payload: dict[str, Any], field: str) -> bool:
    return field in _validation_error_fields(payload)


def _validation_error_message_contains(payload: dict[str, Any], expected: str) -> bool:
    return expected in json.dumps(payload, ensure_ascii=False)


def _cookie_value(cookie_jar: CookieJar, name: str) -> str | None:
    for cookie in cookie_jar:
        if cookie.name == name:
            return cookie.value
    return None


def _parse_first_sse_event(body: str) -> dict[str, Any]:
    event: dict[str, Any] = {}
    data_lines = []
    for line in body.splitlines():
        if not line.strip():
            if event or data_lines:
                break
            continue
        if line.startswith("event:"):
            event["event"] = line.split(":", 1)[1].strip()
        elif line.startswith("id:"):
            event["id"] = line.split(":", 1)[1].strip()
        elif line.startswith("retry:"):
            value = line.split(":", 1)[1].strip()
            event["retry"] = int(value) if value.isdigit() else value
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
    if data_lines:
        event["data"] = json.loads("\n".join(data_lines))
    return event


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
