#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import harness_api

TERMINAL_TOOL_CALL_STATUSES = {"completed", "failed", "cancelled"}
DEFAULT_EVENT_REPLAY_MAX_EVENTS = 500
EVENT_REPLAY_FETCH_BUFFER = 25


def main() -> int:
    parser = argparse.ArgumentParser(description="Map agent trace/result JSON into harness task evidence.")
    parser.add_argument("--trace-json", required=True, help="Path to Agents SDK/Responses trace or result JSON.")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--client-id", default=os.getenv("HARNESS_CLIENT_ID"))
    parser.add_argument("--base-url", default=os.getenv("HARNESS_BASE_URL", harness_api.DEFAULT_BASE_URL))
    parser.add_argument("--api-key", default=os.getenv("HARNESS_API_KEY"))
    parser.add_argument("--output-json", default="", help="Optional path for the full trace-ingest payload.")
    parser.add_argument("--compact-output", action="store_true", help="Print a compact controller summary to stdout.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--readback",
        action="store_true",
        help="After ingesting, fetch task evidence plus SDK-event and client-transcript summaries.",
    )
    parser.add_argument(
        "--readback-only",
        action="store_true",
        help=(
            "Fetch and validate existing task evidence without posting trace evidence. "
            "This implies --readback and derives expected counts from --trace-json."
        ),
    )
    parser.add_argument(
        "--fail-on-readback-invalid",
        action="store_true",
        help="Return exit code 1 when --readback validation is present and invalid.",
    )
    args = parser.parse_args()

    if not args.client_id:
        raise SystemExit("HARNESS_CLIENT_ID or --client-id is required.")

    trace_path = Path(args.trace_json)
    trace = json.loads(trace_path.read_text(encoding="utf-8-sig"))
    actions = build_actions(args.task_id, trace, trace_path)
    summary = summarize_actions(actions)
    expected_result_summary = expected_result_summary_from_actions(actions)
    expected_trace_artifact = expected_trace_artifact_from_actions(actions)
    if args.dry_run:
        payload = {"summary": summary}
        if args.readback_only:
            payload["mode"] = "readback_only"
            payload["expected_result_summary"] = expected_result_summary
            payload["expected_trace_artifact"] = expected_trace_artifact
            payload["readback_actions"] = readback_actions(args.task_id, include_event_replay=True)
        else:
            payload["actions"] = actions
            if args.readback:
                payload["expected_trace_artifact"] = expected_trace_artifact
        if args.readback and not args.readback_only:
            payload["readback_actions"] = readback_actions(args.task_id, include_event_replay=True)
        maybe_write_json(args.output_json, payload)
        stdout_payload = trace_ingest_compact_summary(payload) if args.compact_output else payload
        print(json.dumps(stdout_payload, ensure_ascii=False, indent=2))
        return 0

    client_args = argparse.Namespace(
        base_url=args.base_url,
        client_id=args.client_id,
        api_key=args.api_key,
        dry_run=False,
    )
    if args.readback_only:
        readback = fetch_readback(client_args, args.task_id, include_event_replay=True)
        payload = {
            "summary": summary,
            "mode": "readback_only",
            "expected_result_summary": expected_result_summary,
            "expected_trace_artifact": expected_trace_artifact,
            "readback_actions": readback_actions(args.task_id, include_event_replay=True),
            "readback": {
                **readback,
                "validation": validate_readback(
                    readback,
                    expected_result_summary,
                    expected_task_id=args.task_id,
                    expected_trace_artifact=expected_trace_artifact,
                ),
            },
        }
        exit_code = 0
        validation = payload["readback"]["validation"]
        if args.fail_on_readback_invalid and validation.get("valid") is False:
            exit_code = 1
        maybe_write_json(args.output_json, payload)
        stdout_payload = trace_ingest_compact_summary(payload) if args.compact_output else payload
        print(json.dumps(stdout_payload, ensure_ascii=False, indent=2))
        return exit_code

    results = []
    for action in actions:
        results.append(harness_api.request(client_args, action["method"], action["path"], action.get("body")))
    result_summary = summarize_results(results)
    payload = {
        "summary": summary,
        "result_summary": result_summary,
        "expected_trace_artifact": expected_trace_artifact,
        "results": results,
    }
    if args.readback:
        readback = fetch_readback(client_args, args.task_id, include_event_replay=True)
        payload["readback"] = {
            **readback,
            "validation": validate_readback(
                readback,
                result_summary,
                expected_task_id=args.task_id,
                expected_trace_artifact=expected_trace_artifact,
            ),
        }
    exit_code = 0
    if args.fail_on_readback_invalid:
        validation = (payload.get("readback") or {}).get("validation") if isinstance(payload.get("readback"), dict) else {}
        if isinstance(validation, dict) and validation.get("valid") is False:
            exit_code = 1
    maybe_write_json(args.output_json, payload)
    stdout_payload = trace_ingest_compact_summary(payload) if args.compact_output else payload
    print(json.dumps(stdout_payload, ensure_ascii=False, indent=2))
    return exit_code


def build_actions(task_id: str, trace: dict[str, Any], trace_path: Path) -> list[dict[str, Any]]:
    trace_digest = file_digest(trace_path)
    actions: list[dict[str, Any]] = [
        action(
            "POST",
            f"/tasks/{task_id}/artifacts",
            {
                "kind": "agent_trace",
                "path": str(trace_path),
                "digest": trace_digest,
                "metadata": {
                    "source": "trace_ingest",
                    "trace_id": trace.get("trace_id"),
                    "provider": trace.get("provider"),
                    "api_style": trace.get("api_style"),
                    "sdk": trace.get("sdk"),
                    "sdk_version": trace.get("sdk_version"),
                    "model": trace.get("model"),
                    "status": trace.get("status"),
                    "external_api_called": trace.get("external_api_called"),
                },
            },
        )
    ]
    summary = trace_summary(trace, trace_path)
    if summary:
        actions.append(
            action(
                "POST",
                f"/tasks/{task_id}/observations",
                {
                    "tool_name": "agent_run_summary",
                    "result": summary,
                    "evidence_refs": [f"artifact:agent_trace:{trace_path.name}"],
                    "status": "failed" if is_failure_status(trace.get("status")) else "completed",
                    "metadata": {"source": "trace_ingest", "surface": "run_summary"},
                },
            )
        )
    if trace.get("error_type") or trace.get("error"):
        actions.append(run_error_observation_action(task_id, trace, trace_path))
    for index, phase in enumerate(as_list(trace.get("phases")), start=1):
        if isinstance(phase, dict):
            actions.append(run_phase_observation_action(task_id, phase, index, trace_path))
    for index, response in enumerate(iter_raw_responses(trace), start=1):
        actions.append(model_response_observation(task_id, trace, response, index, trace_path))
    if isinstance(trace.get("session"), dict):
        actions.append(session_observation_action(task_id, trace["session"], trace_path))
    stream_tool_names_by_call_id: dict[str, str] = {}
    for index, stream_event in enumerate(as_list(trace.get("stream_events")), start=1):
        if isinstance(stream_event, dict):
            actions.append(
                stream_event_observation_action(
                    task_id,
                    stream_event,
                    index,
                    trace_path,
                    stream_tool_names_by_call_id,
                )
            )
    items = iter_items(trace)
    for index, question in enumerate(iter_question_records(trace, items), start=1):
        actions.append(question_observation_action(task_id, question, index, trace_path))
    for index, guidance in enumerate(iter_guidance_records(trace, items), start=1):
        actions.append(guidance_observation_action(task_id, guidance, index, trace_path))
    approval_call_ids = approval_required_call_ids(items)
    tool_names_by_call_id: dict[str, str] = {}
    handoff_tool_names_by_call_id: dict[str, str] = {}
    for item in items:
        item_type = type_name(item)
        lower_type = item_kind(item).lower()
        if is_question_item(item, lower_type) or is_guidance_item(item, lower_type):
            continue
        if is_approval_item(item, lower_type):
            actions.append(
                action(
                    "POST",
                    f"/tasks/{task_id}/artifacts",
                    {
                        "kind": "approval_or_interrupt",
                        "path": str(trace_path),
                        "metadata": {"item_type": item_type, "item": item},
                    },
                )
            )
            actions.append(approval_request_observation_action(task_id, item, item_type, trace_path))
        elif is_tool_call_item(item, lower_type):
            call_id = extract_call_id(item)
            approval_required = call_id is not None and str(call_id) in approval_call_ids
            tool_action = tool_call_action(
                task_id,
                item,
                item_type,
                status="queued" if approval_required else "completed",
                metadata_extra={"approval_required": True} if approval_required else None,
            )
            actions.append(tool_action)
            tool_name = tool_action["body"].get("tool_name")
            if call_id and tool_name:
                tool_names_by_call_id[str(call_id)] = str(tool_name)
        elif is_tool_output_item(item, lower_type):
            actions.append(observation_action(task_id, item, item_type, tool_names_by_call_id))
        elif "handoff" in lower_type:
            actions.append(
                action(
                    "POST",
                    f"/tasks/{task_id}/artifacts",
                    {
                        "kind": "handoff",
                        "path": str(trace_path),
                        "metadata": {"item_type": item_type, "item": item},
                    },
                )
            )
            actions.append(
                handoff_observation_action(
                    task_id,
                    item,
                    item_type,
                    trace_path,
                    handoff_tool_names_by_call_id,
                )
            )
    for index, decision in enumerate(as_list(trace.get("approval_decisions")), start=1):
        if isinstance(decision, dict):
            actions.append(approval_decision_observation_action(task_id, decision, index, trace_path))
    for key in [
        "input_guardrail_results",
        "output_guardrail_results",
        "tool_input_guardrail_results",
        "tool_output_guardrail_results",
    ]:
        for index, guardrail in enumerate(as_list(trace.get(key)), start=1):
            actions.append(
                action(
                    "POST",
                    f"/tasks/{task_id}/artifacts",
                    {
                        "kind": "guardrail_result",
                        "path": str(trace_path),
                        "metadata": {"surface": key, "result": guardrail},
                    },
                )
            )
            if isinstance(guardrail, dict):
                actions.append(guardrail_observation_action(task_id, key, guardrail, index, trace_path))
    if "final_output" in trace:
        actions.append(
            action(
                "POST",
                f"/tasks/{task_id}/observations",
                {
                    "tool_name": "agent_final_output",
                    "result": {"final_output": trace.get("final_output")},
                    "evidence_refs": [f"artifact:agent_trace:{trace_path.name}"],
                    "status": "completed",
                },
            )
        )
    return actions


def summarize_actions(actions: list[dict[str, Any]]) -> dict[str, Any]:
    method_counts: dict[str, int] = {}
    endpoint_counts: dict[str, int] = {}
    artifact_kind_counts: dict[str, int] = {}
    observation_tool_counts: dict[str, int] = {}
    tool_call_name_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    call_ids: list[str] = []

    for item in actions:
        method = str(item.get("method") or "unknown")
        path = str(item.get("path") or "")
        body = item.get("body") if isinstance(item.get("body"), dict) else {}
        method_counts[method] = method_counts.get(method, 0) + 1
        endpoint = path.rsplit("/", 1)[-1] if path else "unknown"
        endpoint_counts[endpoint] = endpoint_counts.get(endpoint, 0) + 1
        status = str(body.get("status") or "").strip()
        if status:
            status_counts[status] = status_counts.get(status, 0) + 1
        call_id = str(body.get("call_id") or "").strip()
        if call_id and call_id not in call_ids:
            call_ids.append(call_id)
        if endpoint == "artifacts":
            kind = str(body.get("kind") or "unknown")
            artifact_kind_counts[kind] = artifact_kind_counts.get(kind, 0) + 1
        elif endpoint == "observations":
            tool_name = str(body.get("tool_name") or "unknown")
            observation_tool_counts[tool_name] = observation_tool_counts.get(tool_name, 0) + 1
        elif endpoint == "tool-calls":
            tool_name = str(body.get("tool_name") or "unknown")
            tool_call_name_counts[tool_name] = tool_call_name_counts.get(tool_name, 0) + 1

    return {
        "status": "completed",
        "source": "trace_ingest_action_summary",
        "action_count": len(actions),
        "method_counts": method_counts,
        "endpoint_counts": endpoint_counts,
        "artifact_count": endpoint_counts.get("artifacts", 0),
        "artifact_kind_counts": artifact_kind_counts,
        "tool_call_count": endpoint_counts.get("tool-calls", 0),
        "tool_call_status_update_expected_count": tool_call_status_update_expected_count_from_records(
            actions,
            tool_call_id_key="body",
            observation_id_key="body",
        ),
        "tool_call_name_counts": tool_call_name_counts,
        "observation_count": endpoint_counts.get("observations", 0),
        "observation_tool_counts": observation_tool_counts,
        "status_counts": status_counts,
        "call_ids": call_ids,
        "claim_boundary": (
            "Summary of planned trace-ingest writes only; use task evidence and SDK read models "
            "after ingestion as the authoritative stored evidence."
        ),
    }


def expected_result_summary_from_actions(actions: list[dict[str, Any]]) -> dict[str, Any]:
    action_summary = summarize_actions(actions)
    return {
        "status": "completed",
        "source": "trace_ingest_expected_result_summary",
        "result_count": action_summary["action_count"],
        "artifact_count": action_summary["artifact_count"],
        "artifact_ids": [],
        "artifact_kind_counts": action_summary["artifact_kind_counts"],
        "tool_call_count": action_summary["tool_call_count"],
        "tool_call_pks": [],
        "tool_call_status_update_expected_count": action_summary[
            "tool_call_status_update_expected_count"
        ],
        "tool_call_name_counts": action_summary["tool_call_name_counts"],
        "observation_count": action_summary["observation_count"],
        "observation_ids": [],
        "observation_tool_counts": action_summary["observation_tool_counts"],
        "status_counts": action_summary["status_counts"],
        "call_ids": action_summary["call_ids"],
        "claim_boundary": (
            "Expected result counts derived from the trace file for readback-only validation; "
            "this summary does not prove that write APIs were called."
        ),
    }


def expected_trace_artifact_from_actions(actions: list[dict[str, Any]]) -> dict[str, Any]:
    for item in actions:
        path = str(item.get("path") or "")
        body = item.get("body") if isinstance(item.get("body"), dict) else {}
        if path.rsplit("/", 1)[-1] != "artifacts" or body.get("kind") != "agent_trace":
            continue
        artifact_path = str(body.get("path") or "")
        return {
            "status": "completed",
            "source": "trace_ingest_expected_trace_artifact",
            "kind": "agent_trace",
            "path": artifact_path,
            "name": Path(artifact_path).name if artifact_path else "",
            "digest": str(body.get("digest") or ""),
            "metadata": body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
            "claim_boundary": (
                "Expected trace artifact binding derived from the trace-ingest action plan. "
                "Readback validation should match this digest against stored agent_trace artifacts."
            ),
        }
    return {
        "status": "missing",
        "source": "trace_ingest_expected_trace_artifact",
        "kind": "agent_trace",
        "path": "",
        "name": "",
        "digest": "",
        "metadata": {},
        "claim_boundary": "No planned agent_trace artifact was found in the trace-ingest action plan.",
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    artifact_kind_counts: dict[str, int] = {}
    observation_tool_counts: dict[str, int] = {}
    tool_call_name_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    artifact_ids: list[Any] = []
    observation_ids: list[Any] = []
    tool_call_pks: list[Any] = []
    call_ids: list[str] = []

    for result in results:
        if not isinstance(result, dict):
            continue
        status = str(result.get("status") or "").strip()
        if status:
            status_counts[status] = status_counts.get(status, 0) + 1
        call_id = str(result.get("call_id") or "").strip()
        if call_id and call_id not in call_ids:
            call_ids.append(call_id)
        if result.get("artifact_id") is not None:
            artifact_ids.append(result.get("artifact_id"))
            kind = str(result.get("kind") or "unknown")
            artifact_kind_counts[kind] = artifact_kind_counts.get(kind, 0) + 1
        if result.get("observation_id") is not None:
            observation_ids.append(result.get("observation_id"))
            tool_name = str(result.get("tool_name") or "unknown")
            observation_tool_counts[tool_name] = observation_tool_counts.get(tool_name, 0) + 1
        if result.get("call_pk") is not None:
            tool_call_pks.append(result.get("call_pk"))
            tool_name = str(result.get("tool_name") or "unknown")
            tool_call_name_counts[tool_name] = tool_call_name_counts.get(tool_name, 0) + 1

    return {
        "status": "completed",
        "source": "trace_ingest_result_summary",
        "result_count": len(results),
        "artifact_count": len(artifact_ids),
        "artifact_ids": artifact_ids,
        "artifact_kind_counts": artifact_kind_counts,
        "tool_call_count": len(tool_call_pks),
        "tool_call_pks": tool_call_pks,
        "tool_call_status_update_expected_count": tool_call_status_update_expected_count_from_records(
            results,
            tool_call_id_key=None,
            observation_id_key=None,
        ),
        "tool_call_name_counts": tool_call_name_counts,
        "observation_count": len(observation_ids),
        "observation_ids": observation_ids,
        "observation_tool_counts": observation_tool_counts,
        "status_counts": status_counts,
        "call_ids": call_ids,
        "claim_boundary": (
            "Summary of successful trace-ingest API responses only; fetch task evidence and SDK read models "
            "for authoritative replay."
        ),
    }


def trace_ingest_compact_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    result_summary = payload.get("result_summary") if isinstance(payload.get("result_summary"), dict) else {}
    expected_result_summary = (
        payload.get("expected_result_summary")
        if isinstance(payload.get("expected_result_summary"), dict)
        else {}
    )
    expected_trace_artifact = (
        payload.get("expected_trace_artifact")
        if isinstance(payload.get("expected_trace_artifact"), dict)
        else {}
    )
    status_result_summary = result_summary if result_summary else expected_result_summary
    readback = payload.get("readback") if isinstance(payload.get("readback"), dict) else {}
    validation = readback.get("validation") if isinstance(readback.get("validation"), dict) else {}
    event_replay_validation = (
        validation.get("event_replay_validation")
        if isinstance(validation.get("event_replay_validation"), dict)
        else {}
    )
    event_replay_summary = (
        validation.get("event_replay_summary")
        if isinstance(validation.get("event_replay_summary"), dict)
        else readback.get("event_replay") if isinstance(readback.get("event_replay"), dict) else {}
    )
    sdk_summary = (
        readback.get("sdk_events_summary")
        if isinstance(readback.get("sdk_events_summary"), dict)
        else {}
    )
    transcript_summary = (
        readback.get("client_transcript_summary")
        if isinstance(readback.get("client_transcript_summary"), dict)
        else {}
    )
    evidence_counts = (
        readback.get("evidence_counts")
        if isinstance(readback.get("evidence_counts"), dict)
        else {}
    )
    compact = {
        "status": compact_status(summary, status_result_summary, validation),
        "source": "trace_ingest_compact_summary",
        "mode": payload.get("mode") or ("readback_only" if expected_result_summary and not result_summary else "ingest"),
        "summary_status": summary.get("status"),
        "action_count": _int_value(summary.get("action_count")),
        "artifact_count": _int_value(summary.get("artifact_count")),
        "tool_call_count": _int_value(summary.get("tool_call_count")),
        "tool_call_status_update_expected_count": _int_value(
            summary.get("tool_call_status_update_expected_count")
        ),
        "observation_count": _int_value(summary.get("observation_count")),
        "artifact_kind_counts": _dict_or_empty(summary.get("artifact_kind_counts")),
        "observation_tool_counts": _dict_or_empty(summary.get("observation_tool_counts")),
        "tool_call_name_counts": _dict_or_empty(summary.get("tool_call_name_counts")),
        "call_ids": as_list(summary.get("call_ids")),
        "result_status": result_summary.get("status"),
        "result_count": _int_value(result_summary.get("result_count")),
        "stored_artifact_count": _int_value(result_summary.get("artifact_count")),
        "stored_tool_call_count": _int_value(result_summary.get("tool_call_count")),
        "stored_observation_count": _int_value(result_summary.get("observation_count")),
        "stored_artifact_ids": as_list(result_summary.get("artifact_ids")),
        "stored_tool_call_pks": as_list(result_summary.get("tool_call_pks")),
        "stored_observation_ids": as_list(result_summary.get("observation_ids")),
        "expected_result_summary_status": expected_result_summary.get("status"),
        "expected_result_count": _int_value(expected_result_summary.get("result_count")),
        "expected_artifact_count": _int_value(expected_result_summary.get("artifact_count")),
        "expected_tool_call_count": _int_value(expected_result_summary.get("tool_call_count")),
        "expected_tool_call_status_update_expected_count": _int_value(
            expected_result_summary.get("tool_call_status_update_expected_count")
        ),
        "expected_observation_count": _int_value(expected_result_summary.get("observation_count")),
        "expected_trace_artifact_digest": expected_trace_artifact.get("digest") or "",
        "expected_trace_artifact_path": expected_trace_artifact.get("path") or "",
        "readback_status": readback.get("status"),
        "readback_valid": validation.get("valid") if validation else None,
        "readback_check_count": _int_value(validation.get("check_count")),
        "readback_failed_checks": as_list(validation.get("failed_checks")),
        "readback_task_binding": validation.get("task_binding") if isinstance(validation.get("task_binding"), dict) else {},
        "readback_trace_artifact_binding": (
            validation.get("trace_artifact_binding")
            if isinstance(validation.get("trace_artifact_binding"), dict)
            else {}
        ),
        "readback_trace_artifact_digest_matches": (
            (validation.get("checks") or {}).get("agent_trace_artifact_digest_matches_expected")
            if isinstance(validation.get("checks"), dict)
            else None
        ),
        "readback_expected_projection_counts": (
            validation.get("expected_projection_counts")
            if isinstance(validation.get("expected_projection_counts"), dict)
            else {}
        ),
        "readback_expected_stream_event_counts": (
            validation.get("expected_stream_event_counts")
            if isinstance(validation.get("expected_stream_event_counts"), dict)
            else {}
        ),
        "readback_expected_model_response_projection_maps": (
            validation.get("expected_model_response_projection_maps")
            if isinstance(validation.get("expected_model_response_projection_maps"), dict)
            else {}
        ),
        "evidence_counts": evidence_counts,
        "evidence_agent_stream_event_type_counts": _dict_or_empty(
            evidence_counts.get("agent_stream_event_type_counts")
        ),
        "evidence_agent_stream_event_name_counts": _dict_or_empty(
            evidence_counts.get("agent_stream_event_name_counts")
        ),
        "evidence_agent_stream_raw_event_type_counts": _dict_or_empty(
            evidence_counts.get("agent_stream_raw_event_type_counts")
        ),
        "evidence_agent_stream_item_class_counts": _dict_or_empty(
            evidence_counts.get("agent_stream_item_class_counts")
        ),
        "evidence_agent_stream_tool_name_counts": _dict_or_empty(
            evidence_counts.get("agent_stream_tool_name_counts")
        ),
        "artifact_summaries": as_list(readback.get("artifact_summaries")),
        "agent_session_summaries": as_list(readback.get("agent_session_summaries")),
        "sdk_events_summary_status": sdk_summary.get("status"),
        "sdk_events_agent_model_response_count": _int_value(sdk_summary.get("agent_model_response_count")),
        "sdk_events_agent_model_response_provider_counts": _dict_or_empty(
            sdk_summary.get("agent_model_response_provider_counts")
        ),
        "sdk_events_agent_model_response_api_style_counts": _dict_or_empty(
            sdk_summary.get("agent_model_response_api_style_counts")
        ),
        "sdk_events_agent_stream_event_count": _int_value(sdk_summary.get("agent_stream_event_count")),
        "sdk_events_agent_stream_event_type_counts": _dict_or_empty(
            sdk_summary.get("agent_stream_event_type_counts")
        ),
        "sdk_events_agent_stream_event_name_counts": _dict_or_empty(
            sdk_summary.get("agent_stream_event_name_counts")
        ),
        "sdk_events_agent_stream_raw_event_type_counts": _dict_or_empty(
            sdk_summary.get("agent_stream_raw_event_type_counts")
        ),
        "sdk_events_agent_stream_item_class_counts": _dict_or_empty(
            sdk_summary.get("agent_stream_item_class_counts")
        ),
        "sdk_events_agent_stream_tool_name_counts": _dict_or_empty(
            sdk_summary.get("agent_stream_tool_name_counts")
        ),
        "sdk_events_final_output_count": _int_value(sdk_summary.get("final_output_count")),
        "sdk_events_raw_event_count": _int_value(sdk_summary.get("raw_event_count")),
        "client_transcript_summary_status": transcript_summary.get("status"),
        "client_transcript_item_type_counts": (
            transcript_summary.get("item_type_counts")
            if isinstance(transcript_summary.get("item_type_counts"), dict)
            else {}
        ),
        "client_transcript_agent_model_response_provider_counts": _dict_or_empty(
            transcript_summary.get("agent_model_response_provider_counts")
        ),
        "client_transcript_agent_model_response_api_style_counts": _dict_or_empty(
            transcript_summary.get("agent_model_response_api_style_counts")
        ),
        "client_transcript_agent_stream_event_count": _int_value(
            transcript_summary.get("agent_stream_event_count")
        ),
        "client_transcript_agent_stream_event_type_counts": _dict_or_empty(
            transcript_summary.get("agent_stream_event_type_counts")
        ),
        "client_transcript_agent_stream_event_name_counts": _dict_or_empty(
            transcript_summary.get("agent_stream_event_name_counts")
        ),
        "client_transcript_agent_stream_raw_event_type_counts": _dict_or_empty(
            transcript_summary.get("agent_stream_raw_event_type_counts")
        ),
        "client_transcript_agent_stream_item_class_counts": _dict_or_empty(
            transcript_summary.get("agent_stream_item_class_counts")
        ),
        "client_transcript_agent_stream_tool_name_counts": _dict_or_empty(
            transcript_summary.get("agent_stream_tool_name_counts")
        ),
        "event_replay_valid": event_replay_validation.get("valid") if event_replay_validation else None,
        "event_replay_check_count": _int_value(event_replay_validation.get("check_count")),
        "event_replay_failed_checks": as_list(event_replay_validation.get("failed_checks")),
        "event_replay_status": event_replay_summary.get("status"),
        "event_replay_event_count": _int_value(event_replay_summary.get("event_count")),
        "event_replay_timed_out": event_replay_summary.get("timed_out") is True,
        "event_replay_sse_event_name_counts": _dict_or_empty(
            event_replay_summary.get("sse_event_name_counts")
        ),
        "event_replay_event_type_counts": _dict_or_empty(
            event_replay_summary.get("event_type_counts")
        ),
        "event_replay_event_status_counts": _dict_or_empty(
            event_replay_summary.get("event_status_counts")
        ),
        "event_replay_event_actor_counts": _dict_or_empty(
            event_replay_summary.get("event_actor_counts")
        ),
        "event_replay_fetch_plan": (
            event_replay_validation.get("event_replay_fetch_plan")
            if isinstance(event_replay_validation.get("event_replay_fetch_plan"), dict)
            else event_replay_summary.get("fetch_plan") if isinstance(event_replay_summary.get("fetch_plan"), dict) else {}
        ),
        "event_replay_count_map_totals": (
            event_replay_validation.get("event_replay_count_map_totals")
            if isinstance(event_replay_validation.get("event_replay_count_map_totals"), dict)
            else {}
        ),
        "event_replay_expected_event_counts": (
            event_replay_validation.get("expected_event_counts")
            if isinstance(event_replay_validation.get("expected_event_counts"), dict)
            else {}
        ),
        "event_replay_observed_event_counts": (
            event_replay_validation.get("observed_event_counts")
            if isinstance(event_replay_validation.get("observed_event_counts"), dict)
            else {}
        ),
        "readback_action_count": len(as_list(payload.get("readback_actions"))),
        "readback_actions": as_list(payload.get("readback_actions")),
        "claim_boundary": (
            "Compact trace-ingest stdout summarizes planned writes, stored results, readback "
            "validation, and replay coverage. The full payload, task evidence, and read models "
            "remain authoritative."
        ),
    }
    return compact


def compact_status(
    summary: Mapping[str, Any],
    result_summary: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> str:
    if validation and validation.get("valid") is False:
        return "failed"
    if result_summary:
        return str(result_summary.get("status") or "completed")
    return str(summary.get("status") or "completed")


def maybe_write_json(path: str, payload: Mapping[str, Any]) -> None:
    if not path:
        return
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def tool_call_status_update_expected_count_from_records(
    records: list[dict[str, Any]],
    *,
    tool_call_id_key: str | None,
    observation_id_key: str | None,
) -> int:
    tool_call_status_by_call_id: dict[str, str] = {}
    terminal_observation_call_ids: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        source = record
        if tool_call_id_key is not None:
            source = record.get(tool_call_id_key) if isinstance(record.get(tool_call_id_key), dict) else {}
        endpoint = str(record.get("path") or "").rsplit("/", 1)[-1]
        is_tool_call = bool(source.get("call_pk") is not None) or endpoint == "tool-calls"
        if is_tool_call:
            call_id = str(source.get("call_id") or "").strip()
            if call_id:
                tool_call_status_by_call_id[call_id] = str(source.get("status") or "").strip()

        observation_source = record
        if observation_id_key is not None:
            observation_source = (
                record.get(observation_id_key) if isinstance(record.get(observation_id_key), dict) else {}
            )
        is_observation = bool(observation_source.get("observation_id") is not None) or endpoint == "observations"
        if is_observation:
            call_id = str(observation_source.get("call_id") or "").strip()
            status = str(observation_source.get("status") or "").strip()
            if call_id and status in TERMINAL_TOOL_CALL_STATUSES:
                terminal_observation_call_ids.add(call_id)

    return sum(
        1
        for call_id, status in tool_call_status_by_call_id.items()
        if status not in TERMINAL_TOOL_CALL_STATUSES and call_id in terminal_observation_call_ids
    )


def readback_actions(task_id: str, *, include_event_replay: bool = True) -> list[dict[str, str]]:
    actions = [
        {"method": "GET", "path": f"/tasks/{task_id}/evidence"},
        {"method": "GET", "path": f"/tasks/{task_id}/sdk-events?summary=true"},
        {"method": "GET", "path": f"/tasks/{task_id}/client-transcript?summary=true"},
    ]
    if include_event_replay:
        actions.append({"method": "SSE", "path": f"/tasks/{task_id}/events?after=0"})
    return actions


def fetch_event_replay(
    client_args: argparse.Namespace,
    task_id: str,
    *,
    max_events: int = DEFAULT_EVENT_REPLAY_MAX_EVENTS,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    event_args = argparse.Namespace(
        base_url=client_args.base_url,
        client_id=client_args.client_id,
        api_key=client_args.api_key,
        dry_run=False,
        task_id=task_id,
        after=0,
        max_events=max_events,
        timeout_seconds=timeout_seconds,
        include_keepalive="false",
    )
    return harness_api.request(event_args, "SSE", f"/tasks/{task_id}/events?after=0", None)


def event_replay_fetch_plan(
    evidence_counts: dict[str, Any],
    sdk_events_summary: dict[str, Any],
) -> dict[str, Any]:
    raw_event_count = _int_value(sdk_events_summary.get("raw_event_count"))
    evidence_event_floor = (
        _int_value(evidence_counts.get("artifacts"))
        + _int_value(evidence_counts.get("observations"))
        + _int_value(evidence_counts.get("tool_calls"))
    )
    expected_event_floor = max(raw_event_count, evidence_event_floor)
    max_events = max(DEFAULT_EVENT_REPLAY_MAX_EVENTS, expected_event_floor + EVENT_REPLAY_FETCH_BUFFER)
    basis = "sdk_events_raw_event_count" if raw_event_count > 0 else "evidence_counts"
    return {
        "source": "trace_ingest_event_replay_fetch_plan",
        "basis": basis,
        "max_events": max_events,
        "default_max_events": DEFAULT_EVENT_REPLAY_MAX_EVENTS,
        "buffer": EVENT_REPLAY_FETCH_BUFFER,
        "expected_raw_event_count": raw_event_count,
        "evidence_event_floor": evidence_event_floor,
        "expected_event_floor": expected_event_floor,
        "claim_boundary": (
            "Replay fetch size is derived from stored readback summaries so large trace-ingest "
            "tasks are not truncated by the default SSE event limit."
        ),
    }


def fetch_readback(
    client_args: argparse.Namespace,
    task_id: str,
    *,
    include_event_replay: bool = False,
) -> dict[str, Any]:
    evidence = harness_api.request(client_args, "GET", f"/tasks/{task_id}/evidence", None)
    sdk_events_summary = harness_api.request(client_args, "GET", f"/tasks/{task_id}/sdk-events?summary=true", None)
    client_transcript_summary = harness_api.request(
        client_args,
        "GET",
        f"/tasks/{task_id}/client-transcript?summary=true",
        None,
    )
    evidence_counts = summarize_evidence(evidence)
    payload = {
        "status": "completed",
        "source": "trace_ingest_readback",
        "task_id": task_id,
        "evidence_task_id": evidence.get("task_id"),
        "evidence_counts": evidence_counts,
        "artifact_summaries": summarize_artifacts(evidence),
        "agent_session_summaries": summarize_agent_sessions(evidence),
        "sdk_events_summary": sdk_events_summary,
        "client_transcript_summary": client_transcript_summary,
        "claim_boundary": (
            "Readback is fetched after trace ingestion from stored task evidence and read-model "
            "summary endpoints; it is not proof that a provider is currently connected."
        ),
    }
    if include_event_replay:
        fetch_plan = event_replay_fetch_plan(evidence_counts, sdk_events_summary)
        event_replay = summarize_event_replay(
            fetch_event_replay(client_args, task_id, max_events=int(fetch_plan["max_events"]))
        )
        event_replay["fetch_plan"] = fetch_plan
        payload["event_replay"] = event_replay
    return payload


def summarize_event_replay(event_replay: dict[str, Any] | None) -> dict[str, Any]:
    replay = event_replay if isinstance(event_replay, dict) else {}
    events = replay.get("events") if isinstance(replay.get("events"), list) else []
    sse_event_name_counts = (
        _dict_or_empty(replay.get("sse_event_name_counts"))
        if isinstance(replay.get("sse_event_name_counts"), dict)
        else _counts(
            (
                item.get("event")
                if isinstance(item, dict)
                else None
            )
            for item in events
        )
    )
    event_type_counts = (
        _dict_or_empty(replay.get("event_type_counts"))
        if isinstance(replay.get("event_type_counts"), dict)
        else _counts(
            (
                (item.get("data") or {}).get("event_type")
                if isinstance(item, dict) and isinstance(item.get("data"), dict)
                else None
            )
            for item in events
        )
    )
    event_status_counts = (
        _dict_or_empty(replay.get("event_status_counts"))
        if isinstance(replay.get("event_status_counts"), dict)
        else _counts(
            (
                (item.get("data") or {}).get("status")
                if isinstance(item, dict) and isinstance(item.get("data"), dict)
                else None
            )
            for item in events
        )
    )
    event_actor_counts = (
        _dict_or_empty(replay.get("event_actor_counts"))
        if isinstance(replay.get("event_actor_counts"), dict)
        else _counts(
            (
                (item.get("data") or {}).get("actor")
                if isinstance(item, dict) and isinstance(item.get("data"), dict)
                else None
            )
            for item in events
        )
    )
    fetch_plan = replay.get("fetch_plan") if isinstance(replay.get("fetch_plan"), dict) else {}
    summary = {
        "status": replay.get("status"),
        "source": replay.get("source"),
        "task_id": replay.get("task_id"),
        "event_count": _int_value(replay.get("event_count")),
        "timed_out": replay.get("timed_out") is True,
        "after": _int_value(replay.get("after")),
        "next_after": _int_value(replay.get("next_after")),
        "sse_event_name_counts": sse_event_name_counts,
        "event_type_counts": event_type_counts,
        "event_status_counts": event_status_counts,
        "event_actor_counts": event_actor_counts,
        "claim_boundary": "Summary of raw harness event SSE replay for the requested trace-ingest task.",
    }
    if fetch_plan:
        summary["fetch_plan"] = fetch_plan
    return summary


def validate_event_replay(
    event_replay: dict[str, Any] | None,
    result_summary: dict[str, Any],
    *,
    expected_task_id: str | None = None,
) -> dict[str, Any]:
    event_summary = summarize_event_replay(event_replay)
    event_count = _int_value(event_summary.get("event_count"))
    count_map_totals = {
        "sse_event_name_counts": _count_map_total(event_summary.get("sse_event_name_counts")),
        "event_type_counts": _count_map_total(event_summary.get("event_type_counts")),
        "event_status_counts": _count_map_total(event_summary.get("event_status_counts")),
        "event_actor_counts": _count_map_total(event_summary.get("event_actor_counts")),
    }
    event_type_counts = (
        event_summary.get("event_type_counts")
        if isinstance(event_summary.get("event_type_counts"), dict)
        else {}
    )
    expected_task = str(expected_task_id or "")
    event_summary_task_id = str(event_summary.get("task_id") or "")
    expected_event_counts = {
        "artifact.recorded": _int_value(result_summary.get("artifact_count")),
        "observation.recorded": _int_value(result_summary.get("observation_count")),
        "tool_call.recorded": _int_value(result_summary.get("tool_call_count")),
        "tool_call.status_updated": _int_value(
            result_summary.get("tool_call_status_update_expected_count")
        ),
    }
    observed_event_counts = {
        key: _int_value(event_type_counts.get(key))
        for key in expected_event_counts
    }
    fetch_plan = (
        event_summary.get("fetch_plan")
        if isinstance(event_summary.get("fetch_plan"), dict)
        else {}
    )
    expected_raw_event_count = _int_value(fetch_plan.get("expected_raw_event_count"))
    requested_max_events = _int_value(fetch_plan.get("max_events"))
    checks = {
        "event_replay_expected_task_id_present": bool(expected_task) if expected_task_id is not None else True,
        "event_replay_task_id_matches": (
            event_summary_task_id == expected_task if expected_task else True
        ),
        "event_replay_completed": event_summary.get("status") == "completed",
        "event_replay_not_timed_out": event_summary.get("timed_out") is False,
        "event_replay_cursor_advanced": _int_value(event_summary.get("next_after"))
        > _int_value(event_summary.get("after")),
        "event_replay_sse_event_name_count_total_matches_event_count": (
            count_map_totals["sse_event_name_counts"] == event_count
        ),
        "event_replay_event_type_count_total_matches_event_count": (
            count_map_totals["event_type_counts"] == event_count
        ),
        "event_replay_event_status_count_total_matches_event_count": (
            count_map_totals["event_status_counts"] == event_count
        ),
        "event_replay_event_actor_count_total_matches_event_count": (
            count_map_totals["event_actor_counts"] == event_count
        ),
        "event_replay_covers_artifact_recorded": observed_event_counts["artifact.recorded"]
        >= expected_event_counts["artifact.recorded"],
        "event_replay_covers_observation_recorded": observed_event_counts["observation.recorded"]
        >= expected_event_counts["observation.recorded"],
        "event_replay_covers_tool_call_recorded": observed_event_counts["tool_call.recorded"]
        >= expected_event_counts["tool_call.recorded"],
        "event_replay_covers_tool_call_status_updated": observed_event_counts["tool_call.status_updated"]
        >= expected_event_counts["tool_call.status_updated"],
    }
    if fetch_plan:
        checks.update(
            {
                "event_replay_fetch_plan_covers_expected_raw_event_count": (
                    requested_max_events >= expected_raw_event_count
                ),
                "event_replay_not_truncated_by_fetch_limit": (
                    expected_raw_event_count <= 0
                    or _int_value(event_summary.get("event_count")) >= expected_raw_event_count
                ),
            }
        )
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    return {
        "valid": not failed_checks,
        "source": "trace_ingest_event_replay_validation",
        "check_count": len(checks),
        "failed_checks": failed_checks,
        "checks": checks,
        "expected_event_counts": expected_event_counts,
        "observed_event_counts": observed_event_counts,
        "event_replay_count_map_totals": count_map_totals,
        "event_replay_fetch_plan": fetch_plan,
        "event_replay_summary": event_summary,
        "task_binding": {
            "expected_task_id": expected_task,
            "event_replay_task_id": event_summary_task_id,
        },
        "claim_boundary": (
            "Validation compares trace-ingest result counts with raw harness event SSE replay."
        ),
    }


def summarize_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    tool_calls = as_list(evidence.get("tool_calls"))
    observations = as_list(evidence.get("observations"))
    artifacts = as_list(evidence.get("artifacts"))
    trace_metadata = first_agent_trace_metadata(artifacts)
    model_response_results = [
        item.get("result") if isinstance(item.get("result"), dict) else {}
        for item in observations
        if isinstance(item, dict)
        and item.get("tool_name") == "agent_model_response"
    ]
    stream_results = [
        item.get("result")
        for item in observations
        if isinstance(item, dict)
        and item.get("tool_name") == "agent_stream_event"
        and isinstance(item.get("result"), dict)
    ]
    return {
        "tool_calls": len(tool_calls),
        "observations": len(observations),
        "artifacts": len(artifacts),
        "verification_results": len(as_list(evidence.get("verification_results"))),
        "observation_tool_counts": _counts(
            str(item.get("tool_name") or "unknown")
            for item in observations
            if isinstance(item, dict)
        ),
        "artifact_kind_counts": _counts(
            str(item.get("kind") or "unknown")
            for item in artifacts
            if isinstance(item, dict)
        ),
        "tool_call_name_counts": _counts(
            str(item.get("tool_name") or "unknown")
            for item in tool_calls
            if isinstance(item, dict)
        ),
        "agent_stream_event_type_counts": _stream_result_counts(stream_results, "type"),
        "agent_stream_event_name_counts": _stream_result_counts(stream_results, "name"),
        "agent_stream_raw_event_type_counts": _stream_result_counts(stream_results, "raw_event_type"),
        "agent_stream_item_class_counts": _stream_result_counts(stream_results, "item_class"),
        "agent_stream_tool_name_counts": _stream_result_counts(stream_results, "tool_name"),
        "agent_model_response_provider_counts": _model_response_result_counts(
            model_response_results,
            "provider",
            fallback=trace_metadata.get("provider"),
        ),
        "agent_model_response_api_style_counts": _model_response_result_counts(
            model_response_results,
            "api_style",
            fallback=trace_metadata.get("api_style"),
        ),
    }


def summarize_artifacts(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = as_list(evidence.get("artifacts"))
    summaries: list[dict[str, Any]] = []
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        metadata = artifact_summary_metadata(item.get("metadata"))
        summaries.append(
            {
                "artifact_id": item.get("artifact_id"),
                "kind": item.get("kind"),
                "path": item.get("path") or item.get("url") or "",
                "digest": item.get("digest") or "",
                "status": item.get("status"),
                "metadata": metadata,
            }
        )
    return summaries


def summarize_agent_sessions(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for observation in as_list(evidence.get("observations")):
        if not isinstance(observation, dict):
            continue
        if observation.get("tool_name") != "agent_session":
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        final_item_types = [
            str(item or "unknown")
            for item in as_list(result.get("final_item_types"))
            if item is not None
        ]
        turns = [item for item in as_list(result.get("turns")) if isinstance(item, dict)]
        turn_phases = [
            str(item.get("phase") or "").strip()
            for item in turns
            if str(item.get("phase") or "").strip()
        ]
        summaries.append(
            {
                "agent_session_id": (
                    result.get("session_id")
                    or result.get("agent_session_id")
                    or result.get("sdk_session_id")
                    or ""
                ),
                "backend": result.get("backend") or "",
                "turn_count": _int_value(result.get("turn_count")),
                "turn_count_from_turns": len(turns),
                "final_item_count": _int_value(result.get("final_item_count")),
                "final_item_types": final_item_types,
                "final_item_type_counts": _counts(final_item_types),
                "turn_phases": turn_phases,
                "status": observation.get("status"),
                "evidence_refs": as_list(observation.get("evidence_refs")),
            }
        )
    return summaries


def artifact_summary_metadata(value: Any) -> dict[str, Any]:
    metadata = value if isinstance(value, dict) else {}
    allowed_keys = [
        "source",
        "trace_id",
        "provider",
        "api_style",
        "sdk",
        "sdk_version",
        "model",
        "status",
        "external_api_called",
        "scenario",
        "item_type",
        "surface",
    ]
    return {key: metadata.get(key) for key in allowed_keys if key in metadata}


def validate_readback(
    readback: dict[str, Any],
    result_summary: dict[str, Any],
    *,
    expected_task_id: str | None = None,
    expected_trace_artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence_counts = readback.get("evidence_counts") if isinstance(readback.get("evidence_counts"), dict) else {}
    observation_tool_counts = (
        evidence_counts.get("observation_tool_counts")
        if isinstance(evidence_counts.get("observation_tool_counts"), dict)
        else {}
    )
    artifact_kind_counts = (
        evidence_counts.get("artifact_kind_counts")
        if isinstance(evidence_counts.get("artifact_kind_counts"), dict)
        else {}
    )
    tool_call_name_counts = (
        evidence_counts.get("tool_call_name_counts")
        if isinstance(evidence_counts.get("tool_call_name_counts"), dict)
        else {}
    )
    expected_stream_type_counts = _dict_or_empty(evidence_counts.get("agent_stream_event_type_counts"))
    expected_stream_name_counts = _dict_or_empty(evidence_counts.get("agent_stream_event_name_counts"))
    expected_stream_raw_type_counts = _dict_or_empty(evidence_counts.get("agent_stream_raw_event_type_counts"))
    expected_stream_item_class_counts = _dict_or_empty(evidence_counts.get("agent_stream_item_class_counts"))
    expected_stream_tool_name_counts = _dict_or_empty(evidence_counts.get("agent_stream_tool_name_counts"))
    expected_model_response_provider_counts = _dict_or_empty(
        evidence_counts.get("agent_model_response_provider_counts")
    )
    expected_model_response_api_style_counts = _dict_or_empty(
        evidence_counts.get("agent_model_response_api_style_counts")
    )
    sdk_summary = (
        readback.get("sdk_events_summary")
        if isinstance(readback.get("sdk_events_summary"), dict)
        else {}
    )
    transcript_summary = (
        readback.get("client_transcript_summary")
        if isinstance(readback.get("client_transcript_summary"), dict)
        else {}
    )
    transcript_item_counts = (
        transcript_summary.get("item_type_counts")
        if isinstance(transcript_summary.get("item_type_counts"), dict)
        else {}
    )
    expected_task = str(expected_task_id or "")
    expected_artifact = expected_trace_artifact if isinstance(expected_trace_artifact, dict) else {}
    expected_trace_digest = str(expected_artifact.get("digest") or "").strip()
    expected_trace_path = str(expected_artifact.get("path") or "").strip()
    artifact_summaries = as_list(readback.get("artifact_summaries"))
    agent_trace_artifacts = [
        item
        for item in artifact_summaries
        if isinstance(item, dict) and item.get("kind") == "agent_trace"
    ]
    matching_trace_artifacts = [
        item
        for item in agent_trace_artifacts
        if str(item.get("digest") or "").strip() == expected_trace_digest
    ]
    readback_task_id = str(readback.get("task_id") or "")
    evidence_task_id = str(readback.get("evidence_task_id") or "")
    sdk_summary_task_id = str(sdk_summary.get("task_id") or "")
    transcript_summary_task_id = str(transcript_summary.get("task_id") or "")
    expected_agent_approval_count = int(observation_tool_counts.get("agent_approval_request") or 0) + int(
        observation_tool_counts.get("agent_approval_decision") or 0
    )
    expected_projection_counts = {
        "agent_model_response": int(observation_tool_counts.get("agent_model_response") or 0),
        "agent_stream_event": int(observation_tool_counts.get("agent_stream_event") or 0),
        "agent_phase": int(observation_tool_counts.get("agent_run_phase") or 0),
        "agent_session": int(observation_tool_counts.get("agent_session") or 0),
        "agent_handoff": int(observation_tool_counts.get("agent_handoff") or 0),
        "agent_approval": expected_agent_approval_count,
        "agent_question": int(observation_tool_counts.get("agent_question") or 0),
        "agent_guidance": int(observation_tool_counts.get("agent_guidance") or 0),
        "agent_guardrail": int(observation_tool_counts.get("agent_guardrail_result") or 0),
        "agent_run_error": int(observation_tool_counts.get("agent_run_error") or 0),
        "final_output": int(observation_tool_counts.get("agent_final_output") or 0),
    }
    checks = {
        "artifact_count_covers_result_summary": int(evidence_counts.get("artifacts") or 0)
        >= int(result_summary.get("artifact_count") or 0),
        "observation_count_covers_result_summary": int(evidence_counts.get("observations") or 0)
        >= int(result_summary.get("observation_count") or 0),
        "tool_call_count_covers_result_summary": int(evidence_counts.get("tool_calls") or 0)
        >= int(result_summary.get("tool_call_count") or 0),
        "readback_expected_task_id_present": bool(expected_task) if expected_task_id is not None else True,
        "readback_task_id_matches": readback_task_id == expected_task if expected_task else True,
        "readback_evidence_task_id_matches": (
            evidence_task_id == expected_task if expected_task else True
        ),
        "readback_sdk_summary_task_id_matches": (
            sdk_summary_task_id == expected_task if expected_task else True
        ),
        "readback_transcript_task_id_matches": (
            transcript_summary_task_id == expected_task if expected_task else True
        ),
        "sdk_summary_requested": sdk_summary.get("summary_requested") is True,
        "client_transcript_summary_requested": transcript_summary.get("summary_requested") is True,
        "sdk_agent_model_response_matches_evidence": int(sdk_summary.get("agent_model_response_count") or 0)
        == expected_projection_counts["agent_model_response"],
        "sdk_agent_model_response_provider_counts_match_evidence": _dict_or_empty(
            sdk_summary.get("agent_model_response_provider_counts")
        )
        == expected_model_response_provider_counts,
        "sdk_agent_model_response_provider_counts_known": projection_count_map_known(
            expected_model_response_provider_counts,
            expected_projection_counts["agent_model_response"],
        ),
        "sdk_agent_model_response_api_style_counts_match_evidence": _dict_or_empty(
            sdk_summary.get("agent_model_response_api_style_counts")
        )
        == expected_model_response_api_style_counts,
        "sdk_agent_model_response_api_style_counts_known": projection_count_map_known(
            expected_model_response_api_style_counts,
            expected_projection_counts["agent_model_response"],
        ),
        "sdk_agent_stream_event_matches_evidence": int(sdk_summary.get("agent_stream_event_count") or 0)
        == expected_projection_counts["agent_stream_event"],
        "sdk_agent_stream_event_type_counts_match_evidence": _dict_or_empty(
            sdk_summary.get("agent_stream_event_type_counts")
        )
        == expected_stream_type_counts,
        "sdk_agent_stream_event_name_counts_match_evidence": _dict_or_empty(
            sdk_summary.get("agent_stream_event_name_counts")
        )
        == expected_stream_name_counts,
        "sdk_agent_stream_raw_event_type_counts_match_evidence": _dict_or_empty(
            sdk_summary.get("agent_stream_raw_event_type_counts")
        )
        == expected_stream_raw_type_counts,
        "sdk_agent_stream_item_class_counts_match_evidence": _dict_or_empty(
            sdk_summary.get("agent_stream_item_class_counts")
        )
        == expected_stream_item_class_counts,
        "sdk_agent_stream_tool_name_counts_match_evidence": _dict_or_empty(
            sdk_summary.get("agent_stream_tool_name_counts")
        )
        == expected_stream_tool_name_counts,
        "sdk_agent_phase_matches_evidence": int(sdk_summary.get("agent_phase_count") or 0)
        == expected_projection_counts["agent_phase"],
        "sdk_agent_session_matches_evidence": int(sdk_summary.get("agent_session_count") or 0)
        == expected_projection_counts["agent_session"],
        "sdk_agent_handoff_matches_evidence": int(sdk_summary.get("agent_handoff_count") or 0)
        == expected_projection_counts["agent_handoff"],
        "sdk_agent_approval_matches_evidence": int(sdk_summary.get("agent_approval_count") or 0)
        == expected_projection_counts["agent_approval"],
        "sdk_agent_question_matches_evidence": int(sdk_summary.get("agent_question_count") or 0)
        == expected_projection_counts["agent_question"],
        "sdk_agent_guidance_matches_evidence": int(sdk_summary.get("agent_guidance_count") or 0)
        == expected_projection_counts["agent_guidance"],
        "sdk_agent_guardrail_matches_evidence": int(sdk_summary.get("agent_guardrail_count") or 0)
        == expected_projection_counts["agent_guardrail"],
        "sdk_agent_run_error_matches_evidence": int(sdk_summary.get("agent_run_error_count") or 0)
        == expected_projection_counts["agent_run_error"],
        "sdk_final_output_covers_agent_final_output": int(sdk_summary.get("final_output_count") or 0)
        >= expected_projection_counts["final_output"],
        "transcript_agent_model_response_matches_evidence": int(
            transcript_item_counts.get("agent_model_response") or 0
        )
        == expected_projection_counts["agent_model_response"],
        "transcript_agent_model_response_provider_counts_match_evidence": _dict_or_empty(
            transcript_summary.get("agent_model_response_provider_counts")
        )
        == expected_model_response_provider_counts,
        "transcript_agent_model_response_provider_counts_known": projection_count_map_known(
            expected_model_response_provider_counts,
            expected_projection_counts["agent_model_response"],
        ),
        "transcript_agent_model_response_api_style_counts_match_evidence": _dict_or_empty(
            transcript_summary.get("agent_model_response_api_style_counts")
        )
        == expected_model_response_api_style_counts,
        "transcript_agent_model_response_api_style_counts_known": projection_count_map_known(
            expected_model_response_api_style_counts,
            expected_projection_counts["agent_model_response"],
        ),
        "transcript_agent_stream_event_matches_evidence": int(transcript_item_counts.get("agent_stream_event") or 0)
        == expected_projection_counts["agent_stream_event"],
        "transcript_agent_stream_event_type_counts_match_evidence": _dict_or_empty(
            transcript_summary.get("agent_stream_event_type_counts")
        )
        == expected_stream_type_counts,
        "transcript_agent_stream_event_name_counts_match_evidence": _dict_or_empty(
            transcript_summary.get("agent_stream_event_name_counts")
        )
        == expected_stream_name_counts,
        "transcript_agent_stream_raw_event_type_counts_match_evidence": _dict_or_empty(
            transcript_summary.get("agent_stream_raw_event_type_counts")
        )
        == expected_stream_raw_type_counts,
        "transcript_agent_stream_item_class_counts_match_evidence": _dict_or_empty(
            transcript_summary.get("agent_stream_item_class_counts")
        )
        == expected_stream_item_class_counts,
        "transcript_agent_stream_tool_name_counts_match_evidence": _dict_or_empty(
            transcript_summary.get("agent_stream_tool_name_counts")
        )
        == expected_stream_tool_name_counts,
        "transcript_agent_phase_matches_evidence": int(transcript_item_counts.get("agent_phase") or 0)
        == expected_projection_counts["agent_phase"],
        "transcript_agent_session_matches_evidence": int(transcript_item_counts.get("agent_session") or 0)
        == expected_projection_counts["agent_session"],
        "transcript_agent_handoff_matches_evidence": int(transcript_item_counts.get("agent_handoff") or 0)
        == expected_projection_counts["agent_handoff"],
        "transcript_agent_approval_matches_evidence": int(transcript_item_counts.get("agent_approval") or 0)
        == expected_projection_counts["agent_approval"],
        "transcript_agent_question_matches_evidence": int(transcript_item_counts.get("agent_question") or 0)
        == expected_projection_counts["agent_question"],
        "transcript_agent_guidance_matches_evidence": int(transcript_item_counts.get("agent_guidance") or 0)
        == expected_projection_counts["agent_guidance"],
        "transcript_agent_guardrail_matches_evidence": int(transcript_item_counts.get("agent_guardrail") or 0)
        == expected_projection_counts["agent_guardrail"],
        "transcript_agent_run_error_matches_evidence": int(transcript_item_counts.get("agent_run_error") or 0)
        == expected_projection_counts["agent_run_error"],
    }
    if expected_artifact:
        checks.update(
            {
                "expected_trace_artifact_digest_present": bool(expected_trace_digest),
                "agent_trace_artifact_present": bool(agent_trace_artifacts),
                "agent_trace_artifact_digest_matches_expected": bool(matching_trace_artifacts)
                if expected_trace_digest
                else False,
            }
        )
    event_replay_validation = None
    if isinstance(readback.get("event_replay"), dict):
        event_replay_validation = validate_event_replay(
            readback.get("event_replay"),
            result_summary,
            expected_task_id=expected_task_id,
        )
        event_checks = (
            event_replay_validation.get("checks")
            if isinstance(event_replay_validation.get("checks"), dict)
            else {}
        )
        checks.update({str(name): passed is True for name, passed in event_checks.items()})
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    payload = {
        "valid": not failed_checks,
        "source": "trace_ingest_readback_validation",
        "check_count": len(checks),
        "failed_checks": failed_checks,
        "checks": checks,
        "expected_projection_counts": expected_projection_counts,
        "expected_stream_event_counts": {
            "event_type": expected_stream_type_counts,
            "event_name": expected_stream_name_counts,
            "raw_event_type": expected_stream_raw_type_counts,
            "item_class": expected_stream_item_class_counts,
            "tool_name": expected_stream_tool_name_counts,
        },
        "expected_model_response_projection_maps": {
            "agent_model_response_provider_counts": expected_model_response_provider_counts,
            "agent_model_response_api_style_counts": expected_model_response_api_style_counts,
        },
        "task_binding": {
            "expected_task_id": expected_task,
            "readback_task_id": readback_task_id,
            "evidence_task_id": evidence_task_id,
            "sdk_events_summary_task_id": sdk_summary_task_id,
            "client_transcript_summary_task_id": transcript_summary_task_id,
        },
        "trace_artifact_binding": {
            "expected_path": expected_trace_path,
            "expected_digest": expected_trace_digest,
            "agent_trace_artifact_count": len(agent_trace_artifacts),
            "matched_artifact_count": len(matching_trace_artifacts),
            "matched_artifact_ids": [
                item.get("artifact_id")
                for item in matching_trace_artifacts
                if isinstance(item, dict) and item.get("artifact_id") is not None
            ],
            "matched_artifact_paths": [
                str(item.get("path") or "")
                for item in matching_trace_artifacts
                if isinstance(item, dict)
            ],
        },
        "evidence_observation_tool_counts": observation_tool_counts,
        "evidence_artifact_kind_counts": artifact_kind_counts,
        "evidence_tool_call_name_counts": tool_call_name_counts,
        "claim_boundary": "Validation compares stored harness evidence and read-model summaries after ingestion.",
    }
    if event_replay_validation is not None:
        payload["event_replay_validation"] = event_replay_validation
        payload["event_replay_summary"] = event_replay_validation.get("event_replay_summary")
        payload["event_replay_expected_event_counts"] = event_replay_validation.get("expected_event_counts")
        payload["event_replay_observed_event_counts"] = event_replay_validation.get("observed_event_counts")
        payload["claim_boundary"] = (
            "Validation compares stored harness evidence, read-model summaries, and raw event "
            "SSE replay after ingestion."
        )
    return payload


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "").strip() or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _dict_or_empty(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for key, item in value.items():
        counts[str(key)] = _int_value(item)
    return counts


def _count_map_total(value: Any) -> int:
    return sum(_dict_or_empty(value).values())


def projection_count_map_known(values: dict[str, int], expected_count: int) -> bool:
    if expected_count <= 0:
        return not values
    if not values or "none" in values:
        return False
    return sum(values.values()) >= expected_count


def first_agent_trace_metadata(artifacts: list[Any]) -> dict[str, Any]:
    for item in artifacts:
        if not isinstance(item, dict) or item.get("kind") != "agent_trace":
            continue
        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            return metadata
    return {}


def _model_response_result_counts(results: list[Any], field: str, *, fallback: Any = None) -> dict[str, int]:
    return _counts(
        str((item.get(field) if isinstance(item, dict) else None) or fallback or "none")
        for item in results
        if isinstance(item, dict)
    )


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _stream_result_counts(results: list[Any], field: str) -> dict[str, int]:
    return _counts(
        str(item.get(field) or "none")
        for item in results
        if isinstance(item, dict)
    )


def trace_summary(trace: dict[str, Any], trace_path: Path) -> dict[str, Any]:
    keys = [
        "status",
        "source",
        "scenario",
        "provider",
        "api_style",
        "sdk",
        "sdk_version",
        "external_api_called",
        "model",
        "trace_id",
        "created_at",
        "tracing_disabled",
        "error_type",
        "error",
    ]
    summary = {key: trace.get(key) for key in keys if key in trace}
    nested_summary = trace.get("summary")
    if isinstance(nested_summary, dict):
        summary["summary"] = nested_summary
    summary["trace_file"] = str(trace_path)
    summary["trace_file_name"] = trace_path.name
    summary["raw_response_count"] = len(iter_raw_responses(trace))
    summary["new_item_count"] = len(iter_items(trace))
    return summary


def model_response_observation(
    task_id: str,
    trace: dict[str, Any],
    response: dict[str, Any],
    index: int,
    trace_path: Path,
) -> dict[str, Any]:
    output = response.get("output")
    output_items = output if isinstance(output, list) else []
    output_types = []
    for item in output_items:
        if isinstance(item, dict):
            output_types.append(str(item.get("type") or item.get("class") or "unknown"))
        else:
            output_types.append(type(item).__name__)
    response_model = response.get("model") or trace.get("model")
    return action(
        "POST",
        f"/tasks/{task_id}/observations",
        {
            "tool_name": "agent_model_response",
            "result": {
                "index": index,
                "response_id": response.get("response_id") or response.get("id"),
                "request_id": response.get("request_id"),
                "model": response_model,
                "model_source": "raw_response" if response.get("model") else "trace",
                "provider": trace.get("provider"),
                "api_style": trace.get("api_style"),
                "usage": normalize_usage(response.get("usage")),
                "output_count": len(output_items),
                "output_types": output_types,
            },
            "evidence_refs": [f"artifact:agent_trace:{trace_path.name}", f"raw_response:{index}"],
            "status": "completed",
            "metadata": {"source": "trace_ingest", "surface": "raw_response"},
        },
    )


def run_error_observation_action(task_id: str, trace: dict[str, Any], trace_path: Path) -> dict[str, Any]:
    return action(
        "POST",
        f"/tasks/{task_id}/observations",
        {
            "tool_name": "agent_run_error",
            "result": {
                "status": trace.get("status"),
                "error_type": trace.get("error_type"),
                "error": trace.get("error"),
                "scenario": trace.get("scenario"),
                "trace_id": trace.get("trace_id"),
                "external_api_called": trace.get("external_api_called"),
            },
            "evidence_refs": [f"artifact:agent_trace:{trace_path.name}"],
            "status": "failed" if is_failure_status(trace.get("status")) else "completed",
            "metadata": {"source": "trace_ingest", "surface": "run_error"},
        },
    )


def run_phase_observation_action(
    task_id: str,
    phase: dict[str, Any],
    index: int,
    trace_path: Path,
) -> dict[str, Any]:
    new_items = as_list(phase.get("new_items"))
    result = {
        "index": index,
        "name": phase.get("name") or f"phase_{index}",
        "final_output": phase.get("final_output"),
        "new_item_count": len(new_items),
        "raw_response_count": phase.get("raw_response_count"),
        "interruption_count": phase.get("interruption_count"),
    }
    for key, value in phase.items():
        if key not in result:
            result[key] = value
    result = {key: value for key, value in result.items() if value not in (None, "", [], {})}
    return action(
        "POST",
        f"/tasks/{task_id}/observations",
        {
            "tool_name": "agent_run_phase",
            "result": result,
            "evidence_refs": [f"artifact:agent_trace:{trace_path.name}", f"phase:{index}"],
            "status": "completed",
            "metadata": {"source": "trace_ingest", "surface": "run_phase"},
        },
    )


def session_observation_action(task_id: str, session: dict[str, Any], trace_path: Path) -> dict[str, Any]:
    return action(
        "POST",
        f"/tasks/{task_id}/observations",
        {
            "tool_name": "agent_session",
            "result": dict(session),
            "evidence_refs": [f"artifact:agent_trace:{trace_path.name}", "agent_session"],
            "status": "completed",
            "metadata": {"source": "trace_ingest", "surface": "agent_session"},
        },
    )


def stream_event_observation_action(
    task_id: str,
    stream_event: dict[str, Any],
    index: int,
    trace_path: Path,
    tool_names_by_call_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    item = stream_event.get("item") if isinstance(stream_event.get("item"), Mapping) else {}
    raw_event = stream_event.get("raw_event") if isinstance(stream_event.get("raw_event"), Mapping) else {}
    item_type = str(item.get("class") or item.get("type") or "stream_item")
    call_id = extract_call_id(item) if item else None
    tool_name = extract_tool_name(item, item_type) if item else None
    generic_tool_names = {"ToolCallOutputItem", "MessageOutputItem", "HandoffOutputItem", "stream_item"}
    if tool_name in generic_tool_names:
        tool_name = None
    if call_id and tool_names_by_call_id is not None:
        if tool_name:
            tool_names_by_call_id[str(call_id)] = str(tool_name)
        elif str(call_id) in tool_names_by_call_id:
            tool_name = tool_names_by_call_id[str(call_id)]
    result = {
        "index": stream_event.get("index") or index,
        "type": stream_event.get("type"),
        "class": stream_event.get("class"),
        "name": stream_event.get("name"),
        "raw_event_type": stream_event.get("raw_event_type") or raw_event.get("type"),
        "item_type": item.get("type"),
        "item_class": item.get("class"),
        "tool_name": tool_name,
        "call_id": call_id,
        "agent": first_present(stream_event, [("agent", "name"), ("item", "agent", "name")], default=None),
        "item": item or None,
        "raw_event": raw_event or None,
    }
    result = {key: value for key, value in result.items() if value not in (None, "", [], {})}
    return action(
        "POST",
        f"/tasks/{task_id}/observations",
        {
            "call_id": result.get("call_id"),
            "tool_name": "agent_stream_event",
            "result": result,
            "evidence_refs": [f"artifact:agent_trace:{trace_path.name}", f"stream_event:{index}"],
            "status": "completed",
            "metadata": {"source": "trace_ingest", "surface": "stream_event"},
        },
    )


def iter_items(trace: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in ["new_items", "items", "output", "interruptions"]:
        for item in as_list(trace.get(key)):
            if isinstance(item, dict):
                fingerprint = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                result.append(item)
    return result


def iter_question_records(trace: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(record: dict[str, Any]) -> None:
        questions = normalize_questions(
            record.get("questions")
            if "questions" in record
            else record.get("missing_info")
            if "missing_info" in record
            else record
        )
        if not questions:
            return
        normalized = {
            "question_type": record.get("question_type")
            or record.get("subtype")
            or record.get("type")
            or "missing_info_request",
            "questions": questions,
            "reason": record.get("reason") or trace.get("reason"),
            "evidence_status": record.get("evidence_status") or trace.get("evidence_status"),
            "source_observation": record.get("source_observation"),
            "agent": first_present(record, [("agent", "name"), ("agent",)], default=None),
            "item_type": record.get("item_type") or record.get("type") or record.get("class"),
            "raw_type": first_present(record, [("raw_item", "type"), ("raw_type",)], default=None),
            "status": record.get("status") or trace.get("status"),
            "source": record.get("source"),
        }
        normalized = {key: value for key, value in normalized.items() if value not in (None, "", [], {})}
        fingerprint = json.dumps(normalized, sort_keys=True, ensure_ascii=False, default=str)
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        records.append(normalized)

    for key in ["missing_info", "questions", "agent_questions", "missing_info_requests"]:
        value = trace.get(key)
        if value is None:
            continue
        if isinstance(value, Mapping):
            add({"source": key, **dict(value)})
        else:
            add({"source": key, "questions": value})

    final_output = trace.get("final_output")
    if isinstance(final_output, Mapping):
        for key in ["missing_info", "questions"]:
            if final_output.get(key) is not None:
                add({"source": f"final_output.{key}", **dict(final_output), "questions": final_output.get(key)})

    for item in items:
        lower_type = item_kind(item).lower()
        if is_question_item(item, lower_type):
            add({"source": "trace_item", **item})
    return records


def iter_guidance_records(trace: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(record: dict[str, Any]) -> None:
        result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
        merged = {**dict(result), **{key: value for key, value in record.items() if key != "result"}}
        suggestions = merged.get("next_tool_suggestions")
        if not isinstance(suggestions, list):
            suggestions = []
        if not (
            merged.get("guidance_type")
            or merged.get("reason")
            or suggestions
            or merged.get("required_action")
        ):
            return
        normalized = {
            "guidance_type": merged.get("guidance_type") or merged.get("subtype") or "backend_runtime_guidance",
            "subtype": merged.get("name") or merged.get("subtype") or merged.get("type") or "agent_guidance",
            "reason": merged.get("reason"),
            "status": merged.get("status"),
            "candidate_key": merged.get("candidate_key"),
            "missing_fields": merged.get("missing_fields") or [],
            "required_action": merged.get("required_action"),
            "ask_user_allowed": merged.get("ask_user_allowed"),
            "next_tool_suggestions": suggestions,
            "close_fit_search_budget": merged.get("close_fit_search_budget"),
            "search_recovery_policy": merged.get("search_recovery_policy"),
            "agent": first_present(record, [("agent", "name"), ("agent",)], default=None),
            "item_type": record.get("item_type") or record.get("type") or record.get("class"),
            "source": record.get("source"),
        }
        normalized = {key: value for key, value in normalized.items() if value not in (None, "", [], {})}
        fingerprint_payload = {
            key: value
            for key, value in normalized.items()
            if key not in {"source", "item_type", "guidance_type", "status"}
        }
        fingerprint = json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=False, default=str)
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        records.append(normalized)

    for key in ["agent_guidance", "guidance", "backend_runtime_guidance"]:
        value = trace.get(key)
        if value is None:
            continue
        for item in as_list(value):
            if isinstance(item, Mapping):
                add({"source": key, **dict(item)})

    for item in items:
        lower_type = item_kind(item).lower()
        if is_guidance_item(item, lower_type):
            add({"source": "trace_item", **item})
    return records


def normalize_questions(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        nested = value.get("questions") if "questions" in value else value.get("missing_info")
        if nested is not None:
            return normalize_questions(nested)
        if value.get("question") is not None or value.get("field") is not None:
            return [dict(value)]
        return []
    questions = []
    for item in as_list(value):
        if isinstance(item, Mapping):
            questions.append(dict(item))
        elif item not in (None, ""):
            questions.append({"question": str(item)})
    return questions


def approval_required_call_ids(items: list[dict[str, Any]]) -> set[str]:
    call_ids: set[str] = set()
    for item in items:
        lower_type = item_kind(item).lower()
        if not is_approval_item(item, lower_type):
            continue
        call_id = extract_call_id(item)
        if call_id:
            call_ids.add(str(call_id))
    return call_ids


def iter_raw_responses(trace: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for response in as_list(trace.get("raw_responses")):
        if not isinstance(response, dict):
            continue
        fingerprint = json.dumps(response, sort_keys=True, ensure_ascii=False, default=str)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(response)
    return result


def tool_call_action(
    task_id: str,
    item: dict[str, Any],
    item_type: str,
    *,
    status: str = "completed",
    metadata_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool_name = extract_tool_name(item, item_type)
    call_id = extract_call_id(item)
    input_json = first_present(
        item,
        [
            ("arguments",),
            ("input",),
            ("tool_arguments",),
            ("raw_item", "arguments"),
            ("raw_item", "input"),
            ("raw_item", "params"),
            ("function", "arguments"),
            ("raw_item", "function", "arguments"),
        ],
        default={},
    )
    input_json = normalize_jsonish(input_json, string_key="text")
    return action(
        "POST",
        f"/tasks/{task_id}/tool-calls",
        {
            "call_id": call_id,
            "tool_name": tool_name,
            "input": input_json if isinstance(input_json, dict) else {"value": input_json},
            "status": status,
            "metadata": {"item_type": item_type, **(metadata_extra or {})},
        },
    )


def observation_action(
    task_id: str,
    item: dict[str, Any],
    item_type: str,
    tool_names_by_call_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    tool_name = extract_tool_name(item, item_type)
    call_id = extract_call_id(item)
    if tool_name == item_type and call_id and tool_names_by_call_id:
        tool_name = tool_names_by_call_id.get(str(call_id), tool_name)
    output = first_present(
        item,
        [
            ("output",),
            ("result",),
            ("content",),
            ("raw_item", "output"),
            ("raw_item", "result"),
            ("raw_item", "content"),
        ],
        default=item.get("raw_item") or item,
    )
    output = normalize_jsonish(output, string_key="value")
    return action(
        "POST",
        f"/tasks/{task_id}/observations",
        {
            "call_id": call_id,
            "tool_name": tool_name,
            "result": output,
            "evidence_refs": [f"trace_item:{item_type}"],
            "status": "completed",
            "metadata": {"item_type": item_type},
        },
    )


def handoff_observation_action(
    task_id: str,
    item: dict[str, Any],
    item_type: str,
    trace_path: Path,
    handoff_tool_names_by_call_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    raw_item = item.get("raw_item") if isinstance(item.get("raw_item"), Mapping) else {}
    arguments = first_present(
        item,
        [
            ("arguments",),
            ("raw_item", "arguments"),
            ("raw_item", "input"),
            ("function", "arguments"),
            ("raw_item", "function", "arguments"),
        ],
        default=None,
    )
    output = first_present(
        item,
        [
            ("output",),
            ("result",),
            ("raw_item", "output"),
            ("raw_item", "result"),
        ],
        default=None,
    )
    call_id = extract_call_id(item)
    tool_name = extract_tool_name(item, item_type)
    generic_handoff_names = {"handoff_output_item", "HandoffOutputItem", "handoff_call_item", "HandoffCallItem"}
    if call_id and handoff_tool_names_by_call_id is not None:
        if tool_name and tool_name not in generic_handoff_names:
            handoff_tool_names_by_call_id[str(call_id)] = str(tool_name)
        elif str(call_id) in handoff_tool_names_by_call_id:
            tool_name = handoff_tool_names_by_call_id[str(call_id)]
    result = {
        "item_type": item_type,
        "class": item.get("class"),
        "call_id": call_id,
        "tool_name": tool_name,
        "arguments": normalize_jsonish(arguments, string_key="text") if arguments is not None else None,
        "output": normalize_jsonish(output, string_key="value") if output is not None else None,
        "raw_item_id": raw_item.get("id") if isinstance(raw_item, Mapping) else None,
        "raw_type": raw_item.get("type") if isinstance(raw_item, Mapping) else None,
        "raw_status": raw_item.get("status") if isinstance(raw_item, Mapping) else None,
        "source_agent": first_present(item, [("source_agent", "name")], default=None),
        "target_agent": first_present(item, [("target_agent", "name")], default=None),
        "agent": first_present(item, [("agent", "name")], default=None),
    }
    result = {key: value for key, value in result.items() if value is not None}
    return action(
        "POST",
        f"/tasks/{task_id}/observations",
        {
            "call_id": result.get("call_id"),
            "tool_name": "agent_handoff",
            "result": result,
            "evidence_refs": [f"artifact:agent_trace:{trace_path.name}", f"trace_item:{item_type}"],
            "status": "completed",
            "metadata": {"source": "trace_ingest", "surface": "handoff", "item_type": item_type},
        },
    )


def approval_request_observation_action(
    task_id: str,
    item: dict[str, Any],
    item_type: str,
    trace_path: Path,
) -> dict[str, Any]:
    raw_item = item.get("raw_item") if isinstance(item.get("raw_item"), Mapping) else {}
    arguments = first_present(
        item,
        [
            ("arguments",),
            ("raw_item", "arguments"),
            ("raw_item", "input"),
            ("function", "arguments"),
            ("raw_item", "function", "arguments"),
        ],
        default=None,
    )
    result = {
        "item_type": item_type,
        "class": item.get("class"),
        "call_id": extract_call_id(item),
        "tool_name": extract_tool_name(item, item_type),
        "arguments": normalize_jsonish(arguments, string_key="text") if arguments is not None else {},
        "raw_type": raw_item.get("type") if isinstance(raw_item, Mapping) else None,
        "agent": first_present(item, [("agent", "name")], default=None),
    }
    result = {key: value for key, value in result.items() if value is not None}
    return action(
        "POST",
        f"/tasks/{task_id}/observations",
        {
            "call_id": result.get("call_id"),
            "tool_name": "agent_approval_request",
            "result": result,
            "evidence_refs": [f"artifact:agent_trace:{trace_path.name}", f"trace_item:{item_type}"],
            "status": "completed",
            "metadata": {"source": "trace_ingest", "surface": "approval_request", "item_type": item_type},
        },
    )


def approval_decision_observation_action(
    task_id: str,
    decision: dict[str, Any],
    index: int,
    trace_path: Path,
) -> dict[str, Any]:
    arguments = decision.get("arguments")
    result = {
        "index": index,
        "decision": decision.get("decision"),
        "call_id": decision.get("call_id"),
        "tool_name": decision.get("tool_name"),
        "item_type": decision.get("item_type"),
        "arguments": normalize_jsonish(arguments, string_key="text") if arguments is not None else None,
        "raw_type": decision.get("raw_type"),
        "agent": decision.get("agent"),
    }
    result.update({key: value for key, value in decision.items() if key not in result})
    result = {key: value for key, value in result.items() if value is not None}
    return action(
        "POST",
        f"/tasks/{task_id}/observations",
        {
            "call_id": result.get("call_id"),
            "tool_name": "agent_approval_decision",
            "result": result,
            "evidence_refs": [f"artifact:agent_trace:{trace_path.name}", f"approval_decision:{index}"],
            "status": "completed",
            "metadata": {"source": "trace_ingest", "surface": "approval_decision"},
        },
    )


def question_observation_action(
    task_id: str,
    question: dict[str, Any],
    index: int,
    trace_path: Path,
) -> dict[str, Any]:
    result = {
        "index": index,
        "question_type": question.get("question_type") or "missing_info_request",
        "questions": normalize_questions(question.get("questions")),
        "reason": question.get("reason"),
        "evidence_status": question.get("evidence_status"),
        "source_observation": question.get("source_observation"),
        "agent": question.get("agent"),
        "item_type": question.get("item_type"),
        "raw_type": question.get("raw_type"),
        "source": question.get("source"),
    }
    result = {key: value for key, value in result.items() if value not in (None, "", [], {})}
    return action(
        "POST",
        f"/tasks/{task_id}/observations",
        {
            "tool_name": "agent_question",
            "result": result,
            "evidence_refs": [f"artifact:agent_trace:{trace_path.name}", f"agent_question:{index}"],
            "status": "waiting_for_user"
            if str(question.get("status") or "").lower() == "waiting_for_user"
            else "completed",
            "metadata": {"source": "trace_ingest", "surface": "agent_question"},
        },
    )


def guidance_observation_action(
    task_id: str,
    guidance: dict[str, Any],
    index: int,
    trace_path: Path,
) -> dict[str, Any]:
    result = {
        "index": index,
        "guidance_type": guidance.get("guidance_type") or "backend_runtime_guidance",
        "subtype": guidance.get("subtype") or "agent_guidance",
        "reason": guidance.get("reason"),
        "candidate_key": guidance.get("candidate_key"),
        "missing_fields": guidance.get("missing_fields") or [],
        "required_action": guidance.get("required_action"),
        "ask_user_allowed": guidance.get("ask_user_allowed"),
        "next_tool_suggestions": guidance.get("next_tool_suggestions") or [],
        "close_fit_search_budget": guidance.get("close_fit_search_budget"),
        "search_recovery_policy": guidance.get("search_recovery_policy"),
        "agent": guidance.get("agent"),
        "item_type": guidance.get("item_type"),
        "source": guidance.get("source"),
    }
    result = {key: value for key, value in result.items() if value not in (None, "", [], {})}
    return action(
        "POST",
        f"/tasks/{task_id}/observations",
        {
            "tool_name": "agent_guidance",
            "result": result,
            "evidence_refs": [f"artifact:agent_trace:{trace_path.name}", f"agent_guidance:{index}"],
            "status": "failed" if str(guidance.get("status") or "").lower() == "failed" else "completed",
            "metadata": {"source": "trace_ingest", "surface": "agent_guidance"},
        },
    )


def guardrail_observation_action(
    task_id: str,
    surface: str,
    guardrail: dict[str, Any],
    index: int,
    trace_path: Path,
) -> dict[str, Any]:
    output = guardrail.get("output") if isinstance(guardrail.get("output"), Mapping) else {}
    guardrail_info = guardrail.get("guardrail") if isinstance(guardrail.get("guardrail"), Mapping) else {}
    behavior = output.get("behavior") if isinstance(output.get("behavior"), Mapping) else {}
    output_info = output.get("output_info")
    result = {
        "index": index,
        "surface": surface,
        "guardrail_type": surface.replace("_results", ""),
        "guardrail_name": guardrail_info.get("name") or guardrail.get("name"),
        "guardrail_class": guardrail_info.get("class") or guardrail.get("class"),
        "status": guardrail.get("status"),
        "tripwire_triggered": first_present(
            output,
            [("tripwire_triggered",), ("tripwireTriggered",)],
            default=None,
        ),
        "behavior": behavior.get("type") or output.get("behavior"),
        "output_info": output_info,
        "agent": first_present(guardrail, [("agent", "name")], default=None),
        "agent_output": guardrail.get("agent_output"),
    }
    result = {key: value for key, value in result.items() if value not in (None, "", [], {})}
    return action(
        "POST",
        f"/tasks/{task_id}/observations",
        {
            "tool_name": "agent_guardrail_result",
            "result": result,
            "evidence_refs": [f"artifact:agent_trace:{trace_path.name}", f"guardrail:{surface}:{index}"],
            "status": "failed" if result.get("tripwire_triggered") is True else "completed",
            "metadata": {"source": "trace_ingest", "surface": surface},
        },
    )


def action(method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    return {"method": method, "path": path, "body": body}


def type_name(item: dict[str, Any]) -> str:
    return str(item.get("type") or item.get("item_type") or item.get("class") or "unknown")


def item_kind(item: dict[str, Any]) -> str:
    raw = item.get("raw_item")
    raw_type = raw.get("type") if isinstance(raw, Mapping) else None
    declared = item.get("type") or item.get("item_type")
    if declared and str(declared).lower() != "unknown":
        return str(declared)
    return str(raw_type or item.get("class") or "unknown")


def is_tool_call_item(item: dict[str, Any], lower_type: str) -> bool:
    if lower_type in {"function_call", "mcp_call", "local_shell_call", "computer_call"}:
        return True
    return (
        "tool" in lower_type
        and ("call" in lower_type or item.get("tool_name") or item.get("name"))
        and "output" not in lower_type
    )


def is_approval_item(item: dict[str, Any], lower_type: str) -> bool:
    return "approval" in lower_type or "interrupt" in lower_type


def is_question_item(item: dict[str, Any], lower_type: str) -> bool:
    if any(token in lower_type for token in ["agent_question", "question", "missing_info", "missing-info"]):
        return True
    return bool(item.get("questions") or item.get("missing_info")) and not is_tool_call_item(item, lower_type)


def is_guidance_item(item: dict[str, Any], lower_type: str) -> bool:
    result = item.get("result") if isinstance(item.get("result"), Mapping) else {}
    if "guidance" in lower_type or lower_type == "tool_guidance":
        return True
    if lower_type == "agent_guardrail" and (
        result.get("guidance_type")
        or result.get("next_tool_suggestions")
        or result.get("required_action")
    ):
        return True
    return bool(
        item.get("guidance_type")
        or item.get("backend_runtime_guidance")
        or (
            isinstance(result, Mapping)
            and (
                result.get("guidance_type")
                or result.get("next_tool_suggestions")
                or result.get("required_action")
            )
        )
    )


def is_tool_output_item(item: dict[str, Any], lower_type: str) -> bool:
    if lower_type in {"function_call_output", "local_shell_call_output", "computer_call_output"}:
        return True
    return (
        ("tool" in lower_type and "output" in lower_type)
        or item.get("tool_call_id") is not None
        or (
            item.get("call_id") is not None
            and any(key in item for key in ["output", "result", "content"])
        )
    )


def extract_tool_name(item: dict[str, Any], item_type: str) -> str:
    value = first_present(
        item,
        [
            ("tool_name",),
            ("name",),
            ("function", "name"),
            ("raw_item", "name"),
            ("raw_item", "tool_name"),
            ("raw_item", "function", "name"),
            ("tool_origin", "tool_name"),
            ("custom_data", "tool_name"),
        ],
        default=None,
    )
    return str(value or item_type)


def extract_call_id(item: dict[str, Any]) -> Any:
    return first_present(
        item,
        [
            ("call_id",),
            ("tool_call_id",),
            ("id",),
            ("raw_item", "call_id"),
            ("raw_item", "tool_call_id"),
            ("raw_item", "id"),
        ],
        default=None,
    )


def first_present(item: Mapping[str, Any], paths: list[tuple[str, ...]], *, default: Any) -> Any:
    for path in paths:
        current: Any = item
        missing = False
        for key in path:
            if not isinstance(current, Mapping) or key not in current:
                missing = True
                break
            current = current[key]
        if not missing and current is not None:
            return current
    return default


def normalize_jsonish(value: Any, *, string_key: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {string_key: value}
        if isinstance(parsed, dict):
            return parsed
        return {string_key: parsed}
    return {"value": value}


def normalize_usage(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"text": value}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {"value": value}


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def is_failure_status(status: Any) -> bool:
    return str(status or "").lower() in {"failed", "guardrail_tripwire_triggered"}


if __name__ == "__main__":
    raise SystemExit(main())
