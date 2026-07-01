from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import StreamingResponse

from ..schemas import (
    AgentClientTranscriptResponse,
    AgentReportResponse,
    AgentRunCreateRequest,
    AgentRunEvidenceResponse,
    AgentRunListResponse,
    AgentRunReplyRequest,
    AgentRunResponse,
    AgentRunSessionInfoResponse,
    AgentSdkEventsResponse,
    AgentStateEventResponse,
)
from ..server_context import agent_run_store, production_agent_runtime
from ..session import get_or_create_session_id


router = APIRouter(prefix="/api/agent")
SSE_RETRY_MS = 3000
TERMINAL_AGENT_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "configuration_required",
    "invalid_request",
    "not_found",
    "waiting_for_user",
}


@router.post("/runs", response_model=AgentRunResponse)
def create_agent_run(
    payload: AgentRunCreateRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    background: bool = False,
) -> dict:
    session_id = get_or_create_session_id(request, response)
    runtime = production_agent_runtime()
    runtime_payload = payload.to_runtime_payload(session_id)
    if not background:
        return runtime.start_run(runtime_payload)
    run = runtime.prepare_run(runtime_payload)
    if run.get("status") == "running" and run.get("run_id"):
        background_tasks.add_task(runtime.continue_run, run["run_id"])
    return run


@router.get("/runs", response_model=AgentRunListResponse)
def list_agent_runs(request: Request, response: Response) -> dict:
    session_id = get_or_create_session_id(request, response)
    store = agent_run_store()
    try:
        runs = store.list_runs(session_id=session_id)
    except TypeError:
        runs = [
            run
            for run in store.list_runs()
            if not run.get("session_id") or run.get("session_id") == session_id
        ]
    return {
        "status": "completed",
        "session_id": session_id,
        "runs": runs,
    }


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
def get_agent_run(run_id: str, request: Request, response: Response) -> dict:
    session_id = get_or_create_session_id(request, response)
    run = _get_session_run(run_id, session_id)
    if not run:
        return {"status": "not_found", "run_id": run_id}
    return run


@router.post("/runs/{run_id}/continue", response_model=AgentRunResponse)
def continue_agent_run(run_id: str, request: Request, response: Response) -> dict:
    session_id = get_or_create_session_id(request, response)
    run = _get_session_run(run_id, session_id)
    if not run:
        return {"status": "not_found", "run_id": run_id}
    runtime = production_agent_runtime()
    return runtime.continue_run(run_id)


@router.post("/runs/{run_id}/cancel", response_model=AgentRunResponse)
def cancel_agent_run(run_id: str, request: Request, response: Response) -> dict:
    session_id = get_or_create_session_id(request, response)
    run = _get_session_run(run_id, session_id)
    if not run:
        return {"status": "not_found", "run_id": run_id}
    runtime = production_agent_runtime()
    return runtime.cancel_run(run_id)


@router.post("/runs/{run_id}/reply", response_model=AgentRunResponse)
def reply_agent_run(
    run_id: str,
    payload: AgentRunReplyRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    background: bool = False,
) -> dict:
    session_id = get_or_create_session_id(request, response)
    run = _get_session_run(run_id, session_id)
    if not run:
        return {"status": "not_found", "run_id": run_id}
    runtime = production_agent_runtime()
    runtime_payload = payload.to_runtime_payload(session_id)
    if not background:
        return runtime.reply(run_id, runtime_payload)
    prepared = runtime.prepare_reply(run_id, runtime_payload)
    if prepared.get("status") == "running":
        background_tasks.add_task(runtime.continue_run, run_id)
    return prepared


@router.get("/runs/{run_id}/report", response_model=AgentReportResponse)
def get_agent_report(run_id: str, request: Request, response: Response) -> dict:
    session_id = get_or_create_session_id(request, response)
    run = _get_session_run(run_id, session_id)
    if not run:
        return {"status": "not_found", "run_id": run_id, "trace_count": 0}
    return {
        "status": run.get("status"),
        "run_id": run_id,
        "final_report": run.get("final_report"),
        "trace_count": len(run.get("trace", [])),
        "grounding_validation": (run.get("final_report") or {}).get("grounding_validation"),
        "citation_badges": _citation_badges_for_run(run),
    }


@router.get("/runs/{run_id}/evidence", response_model=AgentRunEvidenceResponse)
def get_agent_run_evidence(run_id: str, request: Request, response: Response) -> dict:
    session_id = get_or_create_session_id(request, response)
    run = _get_session_run(run_id, session_id)
    if not run:
        return {"status": "not_found", "run_id": run_id}
    return _agent_run_evidence_for_run(run)


@router.get("/runs/{run_id}/client-transcript", response_model=AgentClientTranscriptResponse)
def get_agent_client_transcript(run_id: str, request: Request, response: Response) -> dict:
    session_id = get_or_create_session_id(request, response)
    run = _get_session_run(run_id, session_id)
    if not run:
        return {"status": "not_found", "run_id": run_id}
    return _agent_client_transcript_for_run(run)


@router.get("/runs/{run_id}/session-info", response_model=AgentRunSessionInfoResponse)
def get_agent_session_info(run_id: str, request: Request, response: Response) -> dict:
    session_id = get_or_create_session_id(request, response)
    run = _get_session_run(run_id, session_id)
    if not run:
        return {"status": "not_found", "run_id": run_id}
    return _agent_session_info_for_run(run)


@router.get("/runs/{run_id}/sdk-events", response_model=AgentSdkEventsResponse)
def get_agent_sdk_events(run_id: str, request: Request, response: Response, after: int = 0) -> dict:
    session_id = get_or_create_session_id(request, response)
    run = _get_session_run(run_id, session_id)
    if not run:
        return {
            "status": "not_found",
            "run_id": run_id,
            "source": "fitcheck_agent_sdk_event_projection",
            "after": _non_negative_int(str(after)),
            "events": [],
            "raw_event_count": 0,
            "next_after": _non_negative_int(str(after)),
        }
    return _agent_sdk_events_for_run(run, after=after)


@router.get(
    "/runs/{run_id}/events",
    responses={
        200: {
            "description": "Server-Sent Events stream of agent run state.",
            "model": AgentStateEventResponse,
            "content": {
                "text/event-stream": {
                    "schema": {"$ref": "#/components/schemas/AgentStateEventResponse"}
                }
            },
        }
    },
)
async def stream_agent_events(run_id: str, request: Request, response: Response) -> StreamingResponse:
    session_id = get_or_create_session_id(request, response)

    async def events():
        last_signature = None
        last_trace_count = _initial_trace_count_from_request(request, run_id)
        max_polls = _positive_int_env("FITCHECK_SSE_MAX_POLLS", 600, minimum=30)
        for _ in range(max_polls):
            if await request.is_disconnected():
                break
            run = _get_session_run(run_id, session_id)
            payload: dict[str, Any]
            if not run:
                payload = {"status": "not_found", "run_id": run_id, "trace_count": 0}
            else:
                trace = run.get("trace") or []
                payload = {
                    "status": run.get("status"),
                    "run_id": run_id,
                    "trace_count": len(trace),
                    "event_sequence": len(trace),
                    "latest_trace": trace[-1:] or [],
                    "trace_delta": trace[last_trace_count:],
                    "latest_observation": _latest_observation_for_run(run),
                    "final_report": run.get("final_report"),
                    "error": run.get("error"),
                    "citation_badges": _citation_badges_for_run(run),
                    "missing_info": run.get("state", {}).get("missing_info", []),
                    "execution_plan": run.get("state", {}).get("execution_plan", {}),
                    "context_plan": run.get("state", {}).get("context_plan", {}),
                }
            event_id = _sse_event_id(run_id, payload)
            payload["event_id"] = event_id
            payload["retry_ms"] = SSE_RETRY_MS
            payload = AgentStateEventResponse.model_validate(payload).model_dump(
                mode="json",
                exclude_none=True,
            )
            trace_delta = payload.get("trace_delta") or []
            stable_payload = {**payload, "trace_delta": []}
            signature = json.dumps(stable_payload, ensure_ascii=False, sort_keys=True)
            if trace_delta or signature != last_signature:
                yield _format_sse_event(
                    event="agent_state",
                    data=payload,
                    event_id=event_id,
                    retry_ms=SSE_RETRY_MS,
                )
                last_signature = signature
                last_trace_count = int(payload.get("trace_count") or last_trace_count)
            else:
                yield ": keepalive\n\n"
            if payload.get("status") in TERMINAL_AGENT_STATUSES:
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse_event_id(run_id: str, payload: dict[str, Any]) -> str:
    event_sequence = int(payload.get("event_sequence") or payload.get("trace_count") or 0)
    return f"{run_id}:trace:{event_sequence}"


def _initial_trace_count_from_request(request: Request, run_id: str) -> int:
    event_id = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    return _trace_count_from_event_id(event_id, run_id)


def _trace_count_from_event_id(event_id: str | None, run_id: str) -> int:
    if not event_id:
        return 0
    parts = str(event_id).split(":")
    if len(parts) >= 3 and parts[0] == run_id and parts[1] == "trace":
        return _non_negative_int(parts[2])
    if len(parts) >= 3 and parts[0] == run_id:
        return _non_negative_int(parts[-1])
    return 0


def _non_negative_int(value: str) -> int:
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def _positive_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return max(minimum, default)


def _format_sse_event(*, event: str, data: dict[str, Any], event_id: str, retry_ms: int) -> str:
    return (
        f"id: {event_id}\n"
        f"event: {event}\n"
        f"retry: {retry_ms}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )


def _citation_badges_for_run(run: dict[str, Any]) -> list[dict[str, Any]]:
    grounding = (run.get("final_report") or {}).get("grounding_validation") or {}
    return list(grounding.get("citation_badges") or [])


def _latest_observation_for_run(run: dict[str, Any]) -> dict[str, Any] | None:
    observations = run.get("state", {}).get("observations") or []
    return observations[-1] if observations else None


def _agent_run_evidence_for_run(run: dict[str, Any]) -> dict[str, Any]:
    trace = list(run.get("trace") or [])
    observations = list((run.get("state") or {}).get("observations") or [])
    hook_lifecycle = list((run.get("state") or {}).get("hook_lifecycle") or [])
    context_budget = list((run.get("state") or {}).get("context_budget") or [])
    context_compactions = list((run.get("state") or {}).get("context_compactions") or [])
    usage_accounting = dict((run.get("state") or {}).get("usage_accounting") or {})
    normalized_events = _normalized_run_events_for_run(run)
    sdk_events = _sdk_event_projection_for_agent_run(run, normalized_events)
    step_type_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for step in trace:
        step_type = str(step.get("step_type") or "unknown")
        status = str(step.get("status") or "unknown")
        step_type_counts[step_type] = step_type_counts.get(step_type, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1

    tool_call_ids = [
        str(step.get("input", {}).get("call_id"))
        for step in trace
        if step.get("step_type") == "tool_call" and step.get("input", {}).get("call_id")
    ]
    tool_result_ids = [
        str(step.get("output", {}).get("call_id"))
        for step in trace
        if step.get("step_type") == "tool_output" and step.get("output", {}).get("call_id")
    ]
    grounding_validation = (run.get("final_report") or {}).get("grounding_validation")
    return {
        "status": "completed",
        "run_id": run.get("run_id"),
        "session_id": run.get("session_id") or (run.get("input") or {}).get("session_id"),
        "run_status": run.get("status"),
        "llm": run.get("llm") or {},
        "counts": {
            "trace": len(trace),
            "observations": len(observations),
            "hook_lifecycle": len(hook_lifecycle),
            "context_budget": len(context_budget),
            "context_compactions": len(context_compactions),
            "normalized_events": len(normalized_events),
            "llm_responses": step_type_counts.get("llm_response", 0),
            "tool_calls": step_type_counts.get("tool_call", 0),
            "tool_outputs": step_type_counts.get("tool_output", 0),
            "guardrails": step_type_counts.get("guardrail", 0),
            "completed_steps": status_counts.get("completed", 0),
            "failed_steps": status_counts.get("failed", 0),
        },
        "sdk_like": {
            "shape": "responses_run_evidence",
            "raw_response_ids": _raw_response_ids_from_trace(trace),
            "provider_request_ids": _provider_request_ids_from_trace(trace),
            "new_items": _sdk_like_new_items(trace),
            "tool_result_pairing": _tool_result_pairing(tool_call_ids, tool_result_ids),
            "context_budget": context_budget,
            "context_compactions": context_compactions,
            "usage_accounting": usage_accounting,
            "normalized_events": normalized_events,
            "sdk_events": sdk_events,
            "sdk_events_after_cursor_supported": True,
            "final_output": run.get("final_report"),
            "hook_lifecycle": hook_lifecycle,
            "guardrail_results": [
                step
                for step in trace
                if step.get("step_type") == "guardrail" or str(step.get("name") or "").endswith("guardrail")
            ],
        },
        "tool_use_summary": _tool_use_summary_for_client(trace),
        "compact_tool_use_messages": _compact_tool_use_messages(trace, observations),
        "trace": trace,
        "observations": observations,
        "hook_lifecycle": hook_lifecycle,
        "context_budget": context_budget,
        "context_compactions": context_compactions,
        "usage_accounting": usage_accounting,
        "normalized_events": normalized_events,
        "latest_observation": observations[-1] if observations else None,
        "final_report": run.get("final_report"),
        "grounding_validation": grounding_validation,
        "citation_badges": _citation_badges_for_run(run),
    }


def _normalized_run_events_for_run(run: dict[str, Any], *, after: int = 0) -> list[dict[str, Any]]:
    run_id = str(run.get("run_id") or "")
    if not run_id:
        return []
    store = agent_run_store()
    run_events = getattr(store, "run_events", None)
    if not callable(run_events):
        return _normalized_trace_events_from_run(run, after=after)
    try:
        events = run_events(run_id, after=_non_negative_int(str(after)))
    except Exception:
        events = []
    if events:
        return events
    return _normalized_trace_events_from_run(run, after=after)


def _normalized_trace_events_from_run(run: dict[str, Any], *, after: int = 0) -> list[dict[str, Any]]:
    after = _non_negative_int(str(after))
    events: list[dict[str, Any]] = []
    for step in run.get("trace", []) or []:
        if not isinstance(step, dict):
            continue
        try:
            seq = int(step.get("step_index") or 0)
        except (TypeError, ValueError):
            continue
        if seq <= after:
            continue
        events.append(
            {
                "event_id": seq,
                "run_id": run.get("run_id"),
                "seq": seq,
                "event_type": step.get("step_type") or "trace",
                "created_at": step.get("created_at"),
                "payload": step,
            }
        )
    return events


def _agent_sdk_events_for_run(run: dict[str, Any], *, after: int = 0) -> dict[str, Any]:
    after = _non_negative_int(str(after))
    raw_events = _normalized_run_events_for_run(run, after=after)
    projected = _sdk_event_projection_for_agent_run(run, raw_events)
    latest_seq = max([int(item.get("seq") or 0) for item in raw_events] or [after])
    run_id = str(run.get("run_id") or "")
    terminal_event_seq = _latest_trace_sequence(run) if run.get("status") in TERMINAL_AGENT_STATUSES else None
    final_output_included = any(item.get("type") == "final_output" for item in projected)
    terminal_event_included = any(
        item.get("subtype") in {"run_notification", "session_state_changed"}
        and int(item.get("source_event_seq") or 0) == int(terminal_event_seq or 0)
        for item in projected
    )
    return {
        "status": "completed",
        "run_id": run_id,
        "session_id": run.get("session_id") or (run.get("input") or {}).get("session_id"),
        "source": "fitcheck_agent_sdk_event_projection",
        "after": after,
        "events": projected,
        "raw_event_count": len(raw_events),
        "projected_event_count": len(projected),
        "latest_source_event_seq": latest_seq if raw_events else None,
        "next_after": latest_seq,
        "cursor_policy": {
            "cursor_field": "after",
            "cursor_basis": "raw_source_event_seq",
            "cursor_semantics": "exclusive",
            "next_after": latest_seq,
        },
        "terminal_event_seq": terminal_event_seq,
        "terminal_event_included": bool(terminal_event_included),
        "final_output_included": bool(final_output_included),
        "source_endpoints": {
            "run": f"/api/agent/runs/{run_id}",
            "evidence": f"/api/agent/runs/{run_id}/evidence",
            "events": f"/api/agent/runs/{run_id}/events",
            "client_transcript": f"/api/agent/runs/{run_id}/client-transcript",
            "session_info": f"/api/agent/runs/{run_id}/session-info",
            "sdk_events": f"/api/agent/runs/{run_id}/sdk-events",
        },
    }


def _agent_session_info_for_run(run: dict[str, Any]) -> dict[str, Any]:
    run_id = str(run.get("run_id") or "")
    session_id = run.get("session_id") or (run.get("input") or {}).get("session_id")
    evidence = _agent_run_evidence_for_run(run)
    counts = evidence.get("counts") or {}
    latest_seq = _latest_trace_sequence(run)
    final_summary = ((run.get("final_report") or {}).get("summary") or "").strip()
    error = run.get("error") or None
    run_status = str(run.get("status") or "")
    resume_available = bool((error or {}).get("resume_available"))
    can_continue_without_user = run_status == "running" or resume_available
    can_reply = run_status in {"waiting_for_user", "completed", "failed"}
    title = _run_title(run)
    return {
        "status": "completed",
        "run_id": run_id,
        "session_id": session_id,
        "source": "fitcheck_agent_session_info",
        "run_status": run.get("status"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "completed_at": run.get("completed_at"),
        "goal": run.get("goal"),
        "title": title,
        "summary": _truncate_for_client(final_summary or str(run.get("goal") or title), 500),
        "llm": run.get("llm") or {},
        "counts": {
            "trace": int(counts.get("trace") or 0),
            "observations": int(counts.get("observations") or 0),
            "normalized_events": int(counts.get("normalized_events") or 0),
            "tool_calls": int(counts.get("tool_calls") or 0),
            "tool_outputs": int(counts.get("tool_outputs") or 0),
            "llm_responses": int(counts.get("llm_responses") or 0),
            "guardrails": int(counts.get("guardrails") or 0),
            "context_budget": int(counts.get("context_budget") or 0),
            "context_compactions": int(counts.get("context_compactions") or 0),
        },
        "latest_event_seq": latest_seq,
        "latest_event_id": _sse_event_id(run_id, {"event_sequence": latest_seq}) if run_id else None,
        "stream": {
            "event_id": _sse_event_id(run_id, {"event_sequence": latest_seq}) if run_id else None,
            "event_sequence": latest_seq,
            "events_route": f"/api/agent/runs/{run_id}/events",
            "sdk_events_route": f"/api/agent/runs/{run_id}/sdk-events",
            "sdk_events_after": latest_seq,
        },
        "resumability": {
            "can_continue_without_user": can_continue_without_user,
            "continue_route": f"/api/agent/runs/{run_id}/continue" if can_continue_without_user else None,
            "can_reply": can_reply,
            "reply_route": f"/api/agent/runs/{run_id}/reply" if can_reply else None,
            "waiting_for_user": run_status == "waiting_for_user",
            "resume_available": resume_available,
            "terminal": run_status in TERMINAL_AGENT_STATUSES and run_status not in {"waiting_for_user"},
        },
        "error": error,
        "source_endpoints": {
            "run": f"/api/agent/runs/{run_id}",
            "report": f"/api/agent/runs/{run_id}/report",
            "evidence": f"/api/agent/runs/{run_id}/evidence",
            "events": f"/api/agent/runs/{run_id}/events",
            "client_transcript": f"/api/agent/runs/{run_id}/client-transcript",
            "session_info": f"/api/agent/runs/{run_id}/session-info",
            "sdk_events": f"/api/agent/runs/{run_id}/sdk-events",
        },
    }


def _sdk_event_projection_for_agent_run(run: dict[str, Any], raw_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    run_id = str(run.get("run_id") or "")
    session_id = run.get("session_id") or (run.get("input") or {}).get("session_id")
    observations_by_call_id = {
        str(observation.get("call_id")): observation
        for observation in (run.get("state") or {}).get("observations", []) or []
        if observation.get("call_id")
    }
    latest_seq = _latest_trace_sequence(run)
    terminal_status = run.get("status") if run.get("status") in TERMINAL_AGENT_STATUSES else None

    for event in raw_events:
        step = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        event_type = str(event.get("event_type") or step.get("step_type") or "trace")
        seq = int(event.get("seq") or step.get("step_index") or 0)
        name = step.get("name") or event.get("name")
        status = step.get("status") or event.get("status")
        base = {
            "uuid": f"{run_id}:sdk:{seq}",
            "session_id": session_id,
            "run_id": run_id,
            "source_event_id": event.get("event_id") or seq,
            "source_event_seq": seq,
            "timestamp": event.get("created_at") or step.get("created_at"),
        }

        if event_type == "run" and name == "create_agent_run":
            projected.append(
                {
                    **base,
                    "type": "system",
                    "subtype": "run_started",
                    "status": status,
                    "description": _truncate_for_client(str(run.get("goal") or ""), 200) or "Agent run started",
                }
            )
            projected.append(
                {
                    **base,
                    "uuid": f"{run_id}:sdk:{seq}:state-running",
                    "type": "system",
                    "subtype": "session_state_changed",
                    "state": "running",
                }
            )
            continue

        if event_type == "user_reply":
            payload = step.get("input") if isinstance(step.get("input"), dict) else {}
            projected.append(
                {
                    **base,
                    "type": "user_message",
                    "subtype": "reply",
                    "status": status,
                    "summary": _truncate_for_client(_user_reply_summary(payload), 200),
                }
            )
            continue

        if event_type == "llm_response":
            output = step.get("output") if isinstance(step.get("output"), dict) else {}
            projected.append(
                {
                    **base,
                    "type": "model_response",
                    "status": status,
                    "response_id": output.get("response_id"),
                    "raw_response_id": output.get("raw_response_id"),
                    "provider_request_id": output.get("provider_request_id"),
                    "api_style": output.get("api_style"),
                    "transport": output.get("transport"),
                    "tool_call_count": output.get("tool_call_count"),
                    "duration_ms": output.get("duration_ms"),
                }
            )
            continue

        if event_type in {"tool_call", "tool_output", "tool_observation", "tool_execution"}:
            input_payload = step.get("input") if isinstance(step.get("input"), dict) else {}
            output_payload = step.get("output") if isinstance(step.get("output"), dict) else {}
            call_id = output_payload.get("call_id") or input_payload.get("call_id")
            observation = observations_by_call_id.get(str(call_id)) if call_id else None
            projected.append(
                {
                    **base,
                    "type": "tool_progress",
                    "subtype": "tool_output" if event_type == "tool_output" else "tool_call",
                    "tool_use_id": call_id,
                    "tool_name": name,
                    "status": status,
                    "summary": _tool_step_summary(step, observation),
                    "result_status": (observation or {}).get("result_status")
                    or ((observation or {}).get("result") or {}).get("status"),
                    "duration_ms": output_payload.get("duration_ms") or input_payload.get("duration_ms"),
                    "backend_owned": bool(
                        observation
                        or output_payload.get("backend_enforced")
                        or output_payload.get("backend_recovery")
                        or output_payload.get("provider_boundary")
                    ),
                }
            )
            continue

        if event_type == "tool_guidance":
            output = step.get("output") if isinstance(step.get("output"), dict) else {}
            suggestions = output.get("next_tool_suggestions") if isinstance(output.get("next_tool_suggestions"), list) else []
            projected.append(
                {
                    **base,
                    "type": "agent_guidance",
                    "subtype": name or output.get("guidance_type") or "backend_runtime_guidance",
                    "status": status,
                    "summary": _guidance_event_summary(name, output),
                    "guidance_type": output.get("guidance_type"),
                    "reason": output.get("reason"),
                    "required_action": output.get("required_action"),
                    "ask_user_allowed": output.get("ask_user_allowed"),
                    "candidate_key": output.get("candidate_key"),
                    "missing_fields": output.get("missing_fields") or [],
                    "suggested_tool_count": len(suggestions),
                    "next_tool_suggestions": suggestions[:5],
                    "close_fit_search_budget": output.get("close_fit_search_budget"),
                    "search_recovery_policy": output.get("search_recovery_policy"),
                }
            )
            continue

        if event_type == "guardrail":
            output = step.get("output") if isinstance(step.get("output"), dict) else {}
            projected.append(
                {
                    **base,
                    "type": "agent_guardrail",
                    "subtype": name,
                    "status": status,
                    "summary": _guardrail_event_summary(name, output),
                    "result": output,
                }
            )
            continue

        if event_type == "context":
            output = step.get("output") if isinstance(step.get("output"), dict) else {}
            projected.append(
                {
                    **base,
                    "type": "system",
                    "subtype": "context_progress",
                    "status": status,
                    "summary": _context_event_summary(name, output),
                    "context": output,
                }
            )
            continue

        projected.append(
            {
                **base,
                "type": "system",
                "subtype": "run_progress",
                "status": status,
                "summary": _truncate_for_client(str(name or event_type), 200),
            }
        )

    if terminal_status and raw_events:
        latest_event = max(raw_events, key=lambda item: int(item.get("seq") or 0))
        latest_in_delta = int(latest_event.get("seq") or 0) == latest_seq
        if latest_in_delta:
            base = {
                "uuid": f"{run_id}:sdk:{latest_seq}",
                "session_id": session_id,
                "run_id": run_id,
                "source_event_id": latest_event.get("event_id") or latest_seq,
                "source_event_seq": latest_seq,
                "timestamp": latest_event.get("created_at"),
            }
            projected.append(
                {
                    **base,
                    "uuid": f"{run_id}:sdk:{latest_seq}:terminal",
                    "type": "system",
                    "subtype": "run_notification",
                    "status": terminal_status,
                    "summary": _terminal_run_summary(run),
                }
            )
            missing_info_event = _missing_info_sdk_event_for_run(run, base)
            if missing_info_event:
                projected.append(missing_info_event)
            final_output_event = _final_output_sdk_event_for_run(run, base)
            if final_output_event:
                projected.append(final_output_event)
            projected.append(
                {
                    **base,
                    "uuid": f"{run_id}:sdk:{latest_seq}:state-idle",
                    "type": "system",
                    "subtype": "session_state_changed",
                    "state": "idle",
                    "status": terminal_status,
                }
            )
    return projected


def _missing_info_sdk_event_for_run(run: dict[str, Any], base: dict[str, Any]) -> dict[str, Any] | None:
    if run.get("status") != "waiting_for_user":
        return None
    state = run.get("state") if isinstance(run.get("state"), dict) else {}
    questions = list(state.get("missing_info") or [])
    if not questions:
        return None
    latest_reason = ""
    latest_evidence_status = ""
    latest_source_observation = ""
    for observation in reversed(state.get("observations") or []):
        result = observation.get("result") if isinstance(observation, dict) else {}
        if not isinstance(result, dict):
            continue
        if result.get("status") != "waiting_for_user" and not result.get("questions"):
            continue
        latest_reason = str(result.get("reason") or "")
        latest_evidence_status = str(result.get("evidence_status") or "")
        latest_source_observation = str(result.get("source_observation") or observation.get("tool") or "")
        break
    first_question = questions[0] if questions else {}
    if isinstance(first_question, dict):
        summary_text = str(first_question.get("question") or "Missing information required")
    else:
        summary_text = str(first_question or "Missing information required")
    summary = _truncate_for_client(summary_text, 240)
    run_id = run.get("run_id")
    return {
        **base,
        "uuid": f"{base.get('uuid')}:missing-info",
        "type": "agent_question",
        "subtype": "missing_info_request",
        "status": "waiting_for_user",
        "summary": summary,
        "questions": questions,
        "question_count": len(questions),
        "reason": latest_reason or None,
        "evidence_status": latest_evidence_status or None,
        "source_observation": latest_source_observation or None,
        "source_endpoints": {
            "reply": f"/api/agent/runs/{run_id}/reply",
            "session_info": f"/api/agent/runs/{run_id}/session-info",
        },
    }


def _final_output_sdk_event_for_run(run: dict[str, Any], base: dict[str, Any]) -> dict[str, Any] | None:
    final_report = run.get("final_report") if isinstance(run.get("final_report"), dict) else {}
    summary = str((final_report or {}).get("summary") or "").strip()
    if not final_report and not summary:
        return None
    grounding = (final_report or {}).get("grounding_validation")
    citation_badges = list((grounding or {}).get("citation_badges") or [])
    return {
        **base,
        "uuid": f"{base.get('uuid')}:final-output",
        "type": "final_output",
        "subtype": "grounded_report" if (final_report or {}).get("grounded") is True else "report",
        "status": run.get("status"),
        "summary": _truncate_for_client(summary, 240) if summary else "Final report available",
        "grounded": (final_report or {}).get("grounded"),
        "grounding": (final_report or {}).get("grounding"),
        "report_type": (final_report or {}).get("report_type"),
        "grounding_validation": _sdk_grounding_summary(grounding),
        "citation_badges": citation_badges,
        "citation_badge_count": len(citation_badges),
        "source_endpoints": {
            "report": f"/api/agent/runs/{run.get('run_id')}/report",
            "evidence": f"/api/agent/runs/{run.get('run_id')}/evidence",
        },
    }


def _sdk_grounding_summary(grounding: Any) -> dict[str, Any] | None:
    if not isinstance(grounding, dict):
        return None
    evidence = grounding.get("grounding_evidence") or []
    return {
        "valid": grounding.get("valid"),
        "reason": grounding.get("reason"),
        "suspicious_numbers": list(grounding.get("suspicious_numbers") or []),
        "suspicious_urls": list(grounding.get("suspicious_urls") or []),
        "suspicious_material_claims": list(grounding.get("suspicious_material_claims") or []),
        "suspicious_score_claims": list(grounding.get("suspicious_score_claims") or []),
        "suspicious_identity_claims": list(grounding.get("suspicious_identity_claims") or []),
        "suspicious_style_claims": list(grounding.get("suspicious_style_claims") or []),
        "suspicious_fit_claims": list(grounding.get("suspicious_fit_claims") or []),
        "suspicious_evidence_claims": list(grounding.get("suspicious_evidence_claims") or []),
        "grounding_evidence_count": len(evidence) if isinstance(evidence, list) else 0,
        "citation_badge_count": len(grounding.get("citation_badges") or []),
    }


def _latest_trace_sequence(run: dict[str, Any]) -> int:
    return max([int(step.get("step_index") or 0) for step in run.get("trace", []) or []] or [0])


def _user_reply_summary(payload: dict[str, Any]) -> str:
    message = str(payload.get("message") or "").strip()
    if message:
        return message
    answers = payload.get("answers") or []
    if answers:
        fields = [str(item.get("field") or "").strip() for item in answers if isinstance(item, dict)]
        fields = [field for field in fields if field]
        if fields:
            return f"answers={', '.join(fields[:4])}"
    attachments = payload.get("attachments") or []
    if attachments:
        return f"attachments={len(attachments)}"
    return "user reply"


def _guardrail_event_summary(name: Any, output: dict[str, Any]) -> str:
    code = output.get("code") or (output.get("error") or {}).get("code")
    if code:
        return f"{name}: {code}"
    reason = output.get("reason")
    if reason:
        return f"{name}: {reason}"
    return str(name or "guardrail")


def _guidance_event_summary(name: Any, output: dict[str, Any]) -> str:
    parts = [str(name or output.get("guidance_type") or "backend guidance")]
    reason = output.get("reason")
    if reason:
        parts.append(f"reason={reason}")
    suggestions = output.get("next_tool_suggestions") if isinstance(output.get("next_tool_suggestions"), list) else []
    tool_names = []
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            continue
        tool = str(suggestion.get("tool") or "")
        if tool and tool not in tool_names:
            tool_names.append(tool)
    if tool_names:
        parts.append(f"next={','.join(tool_names[:3])}")
    budget = output.get("close_fit_search_budget") if isinstance(output.get("close_fit_search_budget"), dict) else {}
    if budget:
        remaining = budget.get("remaining_attempts")
        limit = budget.get("attempt_limit")
        if remaining is not None or limit is not None:
            parts.append(f"close_fit_remaining={remaining}/{limit}")
    return _truncate_for_client(" · ".join(parts), 260)


def _context_event_summary(name: Any, output: dict[str, Any]) -> str:
    reason = output.get("reason") or output.get("response_context_reset_reason")
    if reason:
        return f"{name}: {reason}"
    return str(name or "context")


def _terminal_run_summary(run: dict[str, Any]) -> str:
    if run.get("status") == "completed":
        final_summary = ((run.get("final_report") or {}).get("summary") or "").strip()
        return _truncate_for_client(final_summary, 220) if final_summary else "Agent run completed"
    error = run.get("error") or {}
    message = error.get("message") or error.get("code") or run.get("status")
    return _truncate_for_client(str(message or "Agent run stopped"), 220)


def _run_title(run: dict[str, Any]) -> str:
    input_payload = run.get("input") if isinstance(run.get("input"), dict) else {}
    for key in ("free_text", "goal", "candidate_url"):
        value = str(input_payload.get(key) or "").strip()
        if value:
            return _truncate_for_client(value, 80)
    return _truncate_for_client(str(run.get("goal") or run.get("run_id") or "FitCheck run"), 80)


def _agent_client_transcript_for_run(run: dict[str, Any]) -> dict[str, Any]:
    evidence = _agent_run_evidence_for_run(run)
    trace = evidence.get("trace") or []
    observations = evidence.get("observations") or []
    context_budget = evidence.get("context_budget") or []
    context_compactions = evidence.get("context_compactions") or []
    hook_lifecycle = evidence.get("hook_lifecycle") or []
    usage_accounting = evidence.get("usage_accounting") or {}
    messages: list[dict[str, Any]] = []
    user_text = _latest_user_text_for_run(run)
    if user_text:
        messages.append(
            {
                "type": "streamlined_text",
                "role": "user",
                "text": user_text,
                "summary": _truncate_for_client(user_text, 120),
            }
        )
    for item in _compact_tool_use_messages(trace, observations):
        messages.append(item)
    for item in _recent_guidance_messages(run, messages):
        messages.append(item)
    if context_compactions:
        latest_compaction = context_compactions[-1]
        messages.append(
            {
                "type": "compact_boundary",
                "role": "system",
                "evidence_kind": "context_compaction",
                "summary": _compact_compaction_summary(latest_compaction),
                "status": latest_compaction.get("status"),
            }
        )
    if hook_lifecycle:
        latest_hook = hook_lifecycle[-1]
        messages.append(
            {
                "type": "hook_progress",
                "role": "system",
                "evidence_kind": "hook_lifecycle",
                "summary": _hook_lifecycle_summary(latest_hook),
                "status": latest_hook.get("status"),
                "tool_name": latest_hook.get("tool") or latest_hook.get("tool_name"),
                "call_id": latest_hook.get("call_id") or latest_hook.get("tool_call_id"),
            }
        )
    if run.get("status") == "waiting_for_user":
        questions = (run.get("state") or {}).get("missing_info") or []
        if questions:
            question_context = _latest_missing_info_context(run)
            messages.append(
                {
                    "type": "agent_question",
                    "role": "assistant",
                    "subtype": "missing_info_request",
                    "status": "waiting_for_user",
                    "summary": _truncate_for_client(_missing_info_summary(questions), 160),
                    "question_count": len(questions),
                    "questions": questions,
                    "reason": question_context.get("reason"),
                    "evidence_status": question_context.get("evidence_status"),
                }
            )
    final_summary = ((run.get("final_report") or {}).get("summary") or "").strip()
    if final_summary:
        messages.append(
            {
                "type": "streamlined_text",
                "role": "assistant",
                "text": final_summary,
                "summary": _truncate_for_client(final_summary, 160),
            }
        )
    elif run.get("error"):
        error = run.get("error") or {}
        messages.append(
            {
                "type": "system_error",
                "role": "system",
                "status": run.get("status"),
                "summary": _truncate_for_client(error.get("message") or error.get("code") or "run failed", 160),
            }
        )
    counts = evidence.get("counts") or {}
    session_id = evidence.get("session_id")
    messages = _with_client_message_identity(messages, run_id=str(run.get("run_id") or ""), session_id=session_id)
    return {
        "status": "completed",
        "run_id": run.get("run_id"),
        "session_id": evidence.get("session_id"),
        "run_status": run.get("status"),
        "status_line": {
            "model": (run.get("llm") or {}).get("model"),
            "api_style": (run.get("llm") or {}).get("api_style"),
            "tool_execution": (run.get("llm") or {}).get("tool_execution"),
            "trace_count": len(trace),
            "observation_count": len(observations),
            "context_estimated_tokens": (context_budget[-1] or {}).get("estimated_total_tokens") if context_budget else None,
            "provider_total_tokens": usage_accounting.get("provider_total_tokens"),
            "provider_input_tokens": usage_accounting.get("provider_input_tokens"),
            "provider_output_tokens": usage_accounting.get("provider_output_tokens"),
            "cost_usd": usage_accounting.get("cost_usd"),
            "cost_status": usage_accounting.get("cost_status"),
            "latest_response_id": (_raw_response_ids_from_trace(trace) or [None])[-1],
            "latest_provider_request_id": (_provider_request_ids_from_trace(trace) or [None])[-1],
        },
        "stream": {
            "event_id": _sse_event_id(str(run.get("run_id") or ""), {"event_sequence": len(trace)}),
            "event_sequence": len(trace),
            "events_route": f"/api/agent/runs/{run.get('run_id')}/events",
        },
        "evidence_summary": {
            "trace": counts.get("trace", len(trace)),
            "observations": counts.get("observations", len(observations)),
            "hook_lifecycle": counts.get("hook_lifecycle", len(hook_lifecycle)),
            "context_budget": counts.get("context_budget", len(context_budget)),
            "context_compactions": counts.get("context_compactions", len(context_compactions)),
            "normalized_events": counts.get("normalized_events", 0),
            "tool_result_pairing": (evidence.get("sdk_like") or {}).get("tool_result_pairing") or {},
            "raw_response_ids": (evidence.get("sdk_like") or {}).get("raw_response_ids") or [],
            "provider_request_ids": (evidence.get("sdk_like") or {}).get("provider_request_ids") or [],
            "usage_accounting": usage_accounting,
        },
        "tool_use_summary": _tool_use_summary_for_client(trace),
        "messages": messages,
        "source_endpoints": {
            "run": f"/api/agent/runs/{run.get('run_id')}",
            "evidence": f"/api/agent/runs/{run.get('run_id')}/evidence",
            "events": f"/api/agent/runs/{run.get('run_id')}/events",
            "client_transcript": f"/api/agent/runs/{run.get('run_id')}/client-transcript",
            "session_info": f"/api/agent/runs/{run.get('run_id')}/session-info",
            "sdk_events": f"/api/agent/runs/{run.get('run_id')}/sdk-events",
        },
    }


def _recent_guidance_messages(run: dict[str, Any], messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_reasons = {
        str(message.get("reason") or "")
        for message in messages
        if message.get("type") == "agent_guidance" and message.get("reason")
    }
    guidance_items = [
        item
        for item in (run.get("state") or {}).get("enrichment_guidance") or []
        if isinstance(item, dict) and item.get("reason")
    ]
    output: list[dict[str, Any]] = []
    for guidance in reversed(guidance_items):
        reason = str(guidance.get("reason") or "")
        if reason in existing_reasons:
            continue
        suggestions = guidance.get("next_tool_suggestions") if isinstance(guidance.get("next_tool_suggestions"), list) else []
        output.append(
            {
                "type": "agent_guidance",
                "role": "system",
                "evidence_kind": "backend_runtime_guidance",
                "status": "completed",
                "summary": _guidance_event_summary(None, guidance),
                "guidance_type": guidance.get("guidance_type"),
                "reason": reason,
                "required_action": guidance.get("required_action"),
                "ask_user_allowed": guidance.get("ask_user_allowed"),
                "suggested_tool_count": len(suggestions),
                "next_tool_suggestions": suggestions[:5],
                "close_fit_search_budget": guidance.get("close_fit_search_budget"),
                "search_recovery_policy": guidance.get("search_recovery_policy"),
            }
        )
        existing_reasons.add(reason)
        if len(output) >= 3:
            break
    return list(reversed(output))


def _latest_missing_info_context(run: dict[str, Any]) -> dict[str, Any]:
    for observation in reversed((run.get("state") or {}).get("observations") or []):
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        questions = result.get("questions") or result.get("next_questions") or []
        if result.get("status") == "waiting_for_user" or questions:
            return {
                "reason": result.get("reason"),
                "evidence_status": result.get("evidence_status"),
            }
    for step in reversed(run.get("trace") or []):
        if step.get("step_type") != "guardrail" or step.get("status") != "waiting_for_user":
            continue
        output = step.get("output") if isinstance(step.get("output"), dict) else {}
        return {
            "reason": output.get("reason"),
            "evidence_status": output.get("evidence_status"),
        }
    return {}


def _missing_info_summary(questions: list[dict[str, Any]]) -> str:
    fields = [
        str(question.get("field") or "").strip()
        for question in questions
        if isinstance(question, dict) and str(question.get("field") or "").strip()
    ]
    if not fields:
        return "추가 정보가 필요합니다."
    return "추가 정보가 필요합니다: " + ", ".join(fields)


def _with_client_message_identity(
    messages: list[dict[str, Any]],
    *,
    run_id: str,
    session_id: str | None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, message in enumerate(messages, start=1):
        copied = dict(message)
        copied.setdefault("session_id", session_id)
        copied.setdefault("uuid", f"{run_id}:client:{index}")
        output.append(copied)
    return output


def _latest_user_text_for_run(run: dict[str, Any]) -> str:
    replies = ((run.get("input") or {}).get("user_replies") or [])
    for reply in reversed(replies):
        message = str(reply.get("message") or "").strip()
        if message:
            return message
    input_payload = run.get("input") or {}
    return str(input_payload.get("free_text") or input_payload.get("goal") or "").strip()


def _compact_tool_use_messages(trace: list[dict[str, Any]], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    observations_by_call_id = {
        str(observation.get("call_id")): observation
        for observation in observations
        if observation.get("call_id")
    }
    messages: list[dict[str, Any]] = []
    for step in trace:
        if step.get("step_type") == "tool_guidance":
            output = step.get("output") if isinstance(step.get("output"), dict) else {}
            messages.append(
                {
                    "type": "agent_guidance",
                    "role": "system",
                    "evidence_kind": "backend_runtime_guidance",
                    "step_index": step.get("step_index"),
                    "status": step.get("status"),
                    "summary": _guidance_event_summary(step.get("name"), output),
                    "reason": output.get("reason"),
                    "required_action": output.get("required_action"),
                    "ask_user_allowed": output.get("ask_user_allowed"),
                    "suggested_tool_count": len(output.get("next_tool_suggestions") or []),
                    "close_fit_search_budget": output.get("close_fit_search_budget"),
                }
            )
            continue
        if step.get("step_type") not in {"tool_call", "tool_output", "tool_observation", "runtime"}:
            continue
        output = step.get("output") or {}
        input_payload = step.get("input") or {}
        call_id = output.get("call_id") or input_payload.get("call_id")
        observation = observations_by_call_id.get(str(call_id)) if call_id else None
        messages.append(
            {
                "type": "tool_use_summary",
                "role": "assistant",
                "step_index": step.get("step_index"),
                "tool_name": step.get("name"),
                "call_id": call_id,
                "status": step.get("status"),
                "summary": _tool_step_summary(step, observation),
            }
        )
    return messages[-12:]


def _tool_step_summary(step: dict[str, Any], observation: dict[str, Any] | None = None) -> str:
    name = str(step.get("name") or "tool")
    status = str(step.get("status") or "unknown")
    output = step.get("output") or {}
    input_payload = step.get("input") or {}
    parts = [name, status]
    for key in ("tool", "parallel_group", "candidate_count", "call_count", "observation_count"):
        value = output.get(key) if key in output else input_payload.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    if observation:
        result_status = observation.get("result_status") or (observation.get("result") or {}).get("status")
        if result_status:
            parts.append(f"result={result_status}")
    return " · ".join(parts)


def _tool_use_summary_for_client(trace: list[dict[str, Any]]) -> dict[str, Any]:
    tool_steps = [
        step
        for step in trace
        if step.get("step_type") in {"tool_call", "tool_output", "tool_observation", "tool_execution", "runtime"}
    ]
    names = [str(step.get("name") or "") for step in tool_steps if step.get("name")]
    return {
        "type": "streamlined_tool_use_summary",
        "tool_uses": len(tool_steps),
        "last_tool_name": names[-1] if names else None,
        "summary": _summarize_tool_names(names),
        "tool_summary": _summarize_tool_names(names),
        "preceding_tool_use_ids": [
            str((step.get("output") or step.get("input") or {}).get("call_id"))
            for step in tool_steps
            if (step.get("output") or step.get("input") or {}).get("call_id")
        ],
    }


def _summarize_tool_names(names: list[str]) -> str:
    if not names:
        return "No tool use"
    unique = []
    for name in names:
        if name not in unique:
            unique.append(name)
    if len(unique) <= 3:
        return ", ".join(unique)
    return f"{', '.join(unique[:3])} +{len(unique) - 3} more"


def _compact_compaction_summary(item: dict[str, Any]) -> str:
    preserved = item.get("preserved") or {}
    pending = preserved.get("pending_tool_output_count", item.get("pending_tool_output_count", 0))
    observations = preserved.get("recent_observations") or item.get("latest_observations") or []
    return (
        f"Conversation compacted · reason={item.get('reason') or item.get('source') or 'threshold'}"
        f" · observations={len(observations)} · pending_tool_outputs={pending}"
    )


def _hook_lifecycle_summary(item: dict[str, Any]) -> str:
    event = item.get("event") or item.get("hook_event") or "hook"
    tool = item.get("tool") or item.get("tool_name") or "-"
    mode = item.get("mode") or "record_only"
    status = item.get("status") or "recorded"
    return f"{event}: {tool} · {status} · mode={mode}"


def _truncate_for_client(value: str, max_length: int) -> str:
    text = str(value or "")
    return text if len(text) <= max_length else f"{text[: max_length - 1]}…"


def _raw_response_ids_from_trace(trace: list[dict[str, Any]]) -> list[str]:
    response_ids: list[str] = []
    for step in trace:
        if step.get("step_type") != "llm_response":
            continue
        response_id = step.get("output", {}).get("response_id") or step.get("output", {}).get("id")
        if response_id:
            response_ids.append(str(response_id))
    return response_ids


def _provider_request_ids_from_trace(trace: list[dict[str, Any]]) -> list[str]:
    request_ids: list[str] = []
    for step in trace:
        if step.get("step_type") != "llm_response":
            continue
        output = step.get("output") or {}
        request_id = output.get("provider_request_id") or output.get("request_id")
        if request_id:
            request_ids.append(str(request_id))
    return request_ids


def _sdk_like_new_items(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for step in trace:
        step_type = step.get("step_type")
        if step_type == "llm_response":
            items.append(
                {
                    "type": "llm_response",
                    "step_index": step.get("step_index"),
                    "status": step.get("status"),
                    "name": step.get("name"),
                    "output": step.get("output") or {},
                }
            )
        elif step_type == "tool_call":
            items.append(
                {
                    "type": "tool_call",
                    "step_index": step.get("step_index"),
                    "status": step.get("status"),
                    "name": step.get("name"),
                    "call_id": (step.get("input") or {}).get("call_id"),
                    "arguments": (step.get("input") or {}).get("arguments") or {},
                }
            )
        elif step_type == "tool_output":
            items.append(
                {
                    "type": "tool_result",
                    "step_index": step.get("step_index"),
                    "status": step.get("status"),
                    "name": step.get("name"),
                    "call_id": (step.get("output") or {}).get("call_id"),
                    "output": step.get("output") or {},
                }
            )
        elif step_type == "guardrail":
            items.append(
                {
                    "type": "guardrail_result",
                    "step_index": step.get("step_index"),
                    "status": step.get("status"),
                    "name": step.get("name"),
                    "output": step.get("output") or {},
                }
            )
    return items


def _tool_result_pairing(tool_call_ids: list[str], tool_result_ids: list[str]) -> dict[str, Any]:
    missing_results = [call_id for call_id in tool_call_ids if call_id not in tool_result_ids]
    orphan_results = [result_id for result_id in tool_result_ids if result_id not in tool_call_ids]
    return {
        "tool_call_count": len(tool_call_ids),
        "tool_result_count": len(tool_result_ids),
        "paired_count": len(tool_result_ids) - len(orphan_results),
        "missing_results": missing_results,
        "orphan_results": orphan_results,
        "status": "paired" if not missing_results and not orphan_results else "mismatch",
    }


def _get_session_run(run_id: str, session_id: str) -> dict[str, Any] | None:
    run = agent_run_store().get_run(run_id)
    if not run:
        return None
    run_session_id = run.get("session_id") or run.get("input", {}).get("session_id")
    if run_session_id and run_session_id != session_id:
        return None
    run = _reap_abandoned_run(run)
    return run


def _reap_abandoned_run(run: dict[str, Any]) -> dict[str, Any]:
    """Finalize a 'running' run whose loop died without releasing (docs/24 #1).

    A run stays 'running' only while a live loop holds a fresh lease. If the
    lease has gone stale, no loop is executing, so the row is abandoned: reap it
    to 'cancelled' (if the user asked to cancel) or 'failed'. This also gives the
    system an automatic reaper for orphaned runs instead of permanent zombies.
    """
    from ..agent_run_store import _lease_is_fresh

    if run.get("status") != "running":
        return run
    state = run.get("state") or {}
    if _lease_is_fresh(state.get("active_lease")):
        return run  # a live loop still owns it
    runtime = production_agent_runtime()
    cancelled = bool(state.get("cancel_requested"))
    reaped = runtime.reap_abandoned_run(run["run_id"], cancelled=cancelled)
    return reaped or run
