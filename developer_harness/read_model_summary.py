from __future__ import annotations

from typing import Any


def summarize_sdk_events(result: dict[str, Any], *, task_id: str | None = None) -> dict[str, Any]:
    events = [item for item in result.get("events") or [] if isinstance(item, dict)]
    approval_events = [item for item in events if item.get("type") == "agent_approval"]
    eval_case_verifications = [item for item in events if item.get("type") == "agent_eval_case_verification"]
    guidance_events = [item for item in events if item.get("type") == "agent_guidance"]
    guardrail_events = [item for item in events if item.get("type") == "agent_guardrail"]
    handoff_events = [item for item in events if item.get("type") == "agent_handoff"]
    phase_events = [item for item in events if item.get("type") == "agent_phase"]
    question_events = [item for item in events if item.get("type") == "agent_question"]
    run_error_events = [item for item in events if item.get("type") == "agent_run_error"]
    session_events = [item for item in events if item.get("type") == "agent_session"]
    stream_events = [item for item in events if item.get("type") == "agent_stream_event"]
    model_responses = [item for item in events if item.get("type") == "agent_model_response"]
    verification_results = [item for item in events if item.get("type") == "harness_verification_result"]
    final_outputs = [item for item in events if item.get("type") == "final_output"]
    return {
        "status": result.get("status"),
        "source": "harness_sdk_events_summary",
        "summary_requested": True,
        "task_id": result.get("task_id") or task_id,
        "projected_event_count": result.get("projected_event_count"),
        "raw_event_count": result.get("raw_event_count"),
        "event_type_counts": _type_counts(events),
        "tool_progress_count": _count_type(events, "tool_progress"),
        "agent_model_response_count": len(model_responses),
        "agent_model_response_provider_counts": _field_counts(model_responses, "provider"),
        "agent_model_response_api_style_counts": _field_counts(model_responses, "api_style"),
        "agent_stream_event_count": len(stream_events),
        "agent_stream_event_type_counts": _field_counts(stream_events, "stream_event_type"),
        "agent_stream_event_name_counts": _field_counts(stream_events, "stream_event_name"),
        "agent_stream_raw_event_type_counts": _field_counts(stream_events, "raw_event_type"),
        "agent_stream_item_class_counts": _field_counts(stream_events, "item_class"),
        "agent_stream_tool_name_counts": _field_counts(stream_events, "tool_name"),
        "agent_phase_count": len(phase_events),
        "agent_session_count": len(session_events),
        "agent_handoff_count": len(handoff_events),
        "agent_approval_count": len(approval_events),
        "agent_question_count": len(question_events),
        "missing_info_question_count": len(
            [item for item in question_events if item.get("subtype") == "missing_info_request"]
        ),
        "agent_guidance_count": len(guidance_events),
        "agent_guidance_reasons": _unique_strings(item.get("reason") for item in guidance_events),
        "agent_guidance_reason_counts": _string_counts(item.get("reason") for item in guidance_events),
        "agent_guidance_suggested_tool_count": sum(
            len(item.get("next_tool_suggestions") or []) for item in guidance_events
        ),
        "agent_eval_case_verification_count": len(eval_case_verifications),
        "agent_eval_case_verification_failed_count": _eval_case_failed_count(eval_case_verifications),
        "agent_eval_case_failure_label_counts": _field_counts(eval_case_verifications, "failure_label"),
        "agent_guardrail_count": len(guardrail_events),
        "agent_run_error_count": len(run_error_events),
        "harness_verification_result_count": len(verification_results),
        "harness_verification_failed_count": _failed_status_count(verification_results),
        "harness_verification_status_counts": _field_counts(verification_results, "status"),
        "final_output_count": len(final_outputs),
        "final_output_included": result.get("final_output_included"),
        "terminal_event_included": result.get("terminal_event_included"),
        "next_after": result.get("next_after"),
        "cursor_policy": result.get("cursor_policy"),
        "claim_boundary": "Summary of stored SDK-event projection only; not proof of live provider connectivity.",
    }


def summarize_client_transcript(result: dict[str, Any], *, task_id: str | None = None) -> dict[str, Any]:
    messages = [item for item in result.get("messages") or [] if isinstance(item, dict)]
    tool_progress = [item for item in result.get("tool_progress") or [] if isinstance(item, dict)]
    model_responses = [item for item in result.get("agent_model_responses") or [] if isinstance(item, dict)]
    stream_events = [item for item in result.get("agent_stream_events") or [] if isinstance(item, dict)]
    phase_items = _transcript_items(result, "agent_phases", "agent_phase")
    session_items = _transcript_items(result, "agent_sessions", "agent_session")
    handoff_items = _transcript_items(result, "agent_handoffs", "agent_handoff")
    approval_items = _transcript_items(result, "agent_approvals", "agent_approval")
    guidance_items = _transcript_items(result, "agent_guidance", "agent_guidance")
    eval_case_verifications = _transcript_items(
        result,
        "agent_eval_case_verifications",
        "agent_eval_case_verification",
    )
    guardrail_items = _transcript_items(result, "agent_guardrails", "agent_guardrail")
    question_items = _transcript_items(result, "agent_questions", "agent_question")
    run_error_items = _transcript_items(result, "agent_run_errors", "agent_run_error")
    verification_results = _transcript_items(
        result,
        "harness_verification_results",
        "harness_verification_result",
    )
    final_outputs = _transcript_items(result, "agent_final_outputs", "final_output")
    return {
        "status": result.get("status"),
        "source": "harness_client_transcript_summary",
        "summary_requested": True,
        "task_id": result.get("task_id") or task_id,
        "session_id": result.get("session_id"),
        "task_status": (result.get("status_line") or {}).get("task_status"),
        "message_count": len(messages),
        "message_type_counts": _type_counts(messages),
        "item_type_counts": {
            **_type_counts(messages),
            "tool_progress": len(tool_progress),
            "agent_model_response": len(model_responses),
            "agent_stream_event": len(stream_events),
            "agent_phase": len(phase_items),
            "agent_session": len(session_items),
            "agent_handoff": len(handoff_items),
            "agent_approval": len(approval_items),
            "agent_question": len(question_items),
            "agent_guidance": len(guidance_items),
            "agent_eval_case_verification": len(eval_case_verifications),
            "agent_guardrail": len(guardrail_items),
            "agent_run_error": len(run_error_items),
            "harness_verification_result": len(verification_results),
            "final_output": len(final_outputs),
        },
        "tool_progress_count": len(tool_progress),
        "agent_model_response_count": len(model_responses),
        "agent_model_response_provider_counts": _field_counts(model_responses, "provider"),
        "agent_model_response_api_style_counts": _field_counts(model_responses, "api_style"),
        "agent_stream_event_count": len(stream_events),
        "agent_stream_event_type_counts": _field_counts(stream_events, "stream_event_type"),
        "agent_stream_event_name_counts": _field_counts(stream_events, "stream_event_name"),
        "agent_stream_raw_event_type_counts": _field_counts(stream_events, "raw_event_type"),
        "agent_stream_item_class_counts": _field_counts(stream_events, "item_class"),
        "agent_stream_tool_name_counts": _field_counts(stream_events, "tool_name"),
        "agent_phase_count": len(phase_items),
        "agent_session_count": len(session_items),
        "agent_handoff_count": len(handoff_items),
        "agent_approval_count": len(approval_items),
        "agent_question_count": len(question_items),
        "missing_info_question_count": len(
            [item for item in question_items if item.get("subtype") == "missing_info_request"]
        ),
        "agent_guidance_count": len(guidance_items),
        "agent_guidance_reasons": _unique_strings(item.get("reason") for item in guidance_items),
        "agent_guidance_reason_counts": _string_counts(item.get("reason") for item in guidance_items),
        "agent_guidance_suggested_tool_count": sum(
            len(item.get("next_tool_suggestions") or []) for item in guidance_items
        ),
        "agent_eval_case_verification_count": len(eval_case_verifications),
        "agent_eval_case_verification_failed_count": _eval_case_failed_count(eval_case_verifications),
        "agent_eval_case_failure_label_counts": _field_counts(eval_case_verifications, "failure_label"),
        "agent_guardrail_count": len(guardrail_items),
        "agent_run_error_count": len(run_error_items),
        "harness_verification_result_count": len(verification_results),
        "harness_verification_failed_count": _failed_status_count(verification_results),
        "harness_verification_status_counts": _field_counts(verification_results, "status"),
        "final_output_count": len(final_outputs),
        "source_endpoints_present": sorted((result.get("source_endpoints") or {}).keys()),
        "claim_boundary": "Summary of stored client transcript projection only; not proof of live provider connectivity.",
    }


def _transcript_items(result: dict[str, Any], top_key: str, item_type: str) -> list[dict[str, Any]]:
    top_items = [item for item in result.get(top_key) or [] if isinstance(item, dict)]
    if top_items:
        return top_items
    return [
        item
        for item in result.get("messages") or []
        if isinstance(item, dict) and item.get("type") == item_type
    ]


def _count_type(items: list[dict[str, Any]], item_type: str) -> int:
    return sum(1 for item in items if item.get("type") == item_type)


def _type_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        item_type = str(item.get("type") or "unknown")
        counts[item_type] = counts.get(item_type, 0) + 1
    return counts


def _field_counts(items: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        text = str(item.get(field) or "none").strip() or "none"
        counts[text] = counts.get(text, 0) + 1
    return counts


def _eval_case_failed_count(items: list[dict[str, Any]]) -> int:
    return sum(
        1
        for item in items
        if item.get("status") == "failed"
        or item.get("execution_status") == "failed"
        or item.get("expected_status_matches") is False
    )


def _failed_status_count(items: list[dict[str, Any]]) -> int:
    return sum(1 for item in items if item.get("status") == "failed")


def _string_counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        counts[text] = counts.get(text, 0) + 1
    return counts


def _unique_strings(values: Any) -> list[str]:
    unique: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in unique:
            unique.append(text)
    return unique
