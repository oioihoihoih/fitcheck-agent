from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HARNESS_SCRIPTS = ROOT / ".agents" / "skills" / "agentic-harness-loop" / "scripts"
if str(HARNESS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(HARNESS_SCRIPTS))

import harness_api  # type: ignore  # noqa: E402
import trace_ingest  # type: ignore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Publish verify_live_agent_smoke.py trace/eval exports into the generic "
            "development harness as task evidence and an eval case."
        )
    )
    parser.add_argument("--trace-json")
    parser.add_argument("--eval-case-json")
    parser.add_argument("--task-id")
    parser.add_argument("--client-id")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key", default=os.getenv("HARNESS_API_KEY"))
    parser.add_argument("--cwd", default=str(ROOT))
    parser.add_argument("--idempotency-key")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-eval-case", action="store_true")
    parser.add_argument(
        "--readback-only",
        action="store_true",
        help="Validate an already-published evidence-holder task without posting new evidence.",
    )
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--output-json")
    parser.add_argument(
        "--record-result-task-id",
        help="Record the publisher revalidation outcome as an eval_case_verification observation on this harness task.",
    )
    parser.add_argument(
        "--revalidate-from-summary-json",
        help="Read revalidation_inputs from a previous compact/full publisher JSON and run readback-only.",
    )
    args = parser.parse_args()

    summary_inputs = (
        _revalidation_inputs_from_summary(Path(args.revalidate_from_summary_json))
        if args.revalidate_from_summary_json
        else {}
    )
    trace_json = args.trace_json or summary_inputs.get("trace_json")
    eval_case_json = args.eval_case_json or summary_inputs.get("eval_case_json")
    task_id = args.task_id or summary_inputs.get("task_id")
    client_id = args.client_id or summary_inputs.get("client_id") or os.getenv("HARNESS_CLIENT_ID")
    base_url = args.base_url or summary_inputs.get("base_url") or os.getenv("HARNESS_BASE_URL") or harness_api.DEFAULT_BASE_URL
    skip_eval_case = args.skip_eval_case or (not eval_case_json and bool(summary_inputs.get("skip_eval_case")))
    readback_only = args.readback_only or bool(args.revalidate_from_summary_json)

    if not client_id:
        raise SystemExit("HARNESS_CLIENT_ID or --client-id is required.")
    if not trace_json:
        raise SystemExit("--trace-json or --revalidate-from-summary-json with revalidation_inputs.trace_json is required.")

    exit_code = 0
    result = publish_export(
        trace_json=Path(str(trace_json)),
        eval_case_json=Path(str(eval_case_json)) if eval_case_json else None,
        task_id=str(task_id) if task_id else None,
        client_id=str(client_id),
        base_url=str(base_url),
        api_key=args.api_key,
        cwd=args.cwd,
        idempotency_key=args.idempotency_key,
        dry_run=args.dry_run,
        skip_eval_case=skip_eval_case,
        readback_only=readback_only,
    )
    if args.revalidate_from_summary_json:
        result.update(
            _summary_revalidation_fields(
                summary_path=Path(args.revalidate_from_summary_json),
                client_id=str(client_id),
                base_url=str(base_url),
            )
        )
    if args.record_result_task_id:
        result = attach_record_result_gate(
            result,
            client_args=argparse.Namespace(
                base_url=str(base_url),
                client_id=str(client_id),
                api_key=args.api_key,
                dry_run=args.dry_run,
            ),
            record_task_id=str(args.record_result_task_id),
        )
        if result.get("status") == "failed" and result.get("publish_status"):
            exit_code = 1
    output = _compact_publish_result(result) if args.compact_output else result
    if args.output_json:
        _write_json_file(
            Path(args.output_json),
            _output_json_payload(result, compact_output=args.compact_output),
        )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return exit_code


def publish_export(
    *,
    trace_json: Path,
    eval_case_json: Path | None = None,
    task_id: str | None = None,
    client_id: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    cwd: str | None = None,
    idempotency_key: str | None = None,
    dry_run: bool = False,
    skip_eval_case: bool = False,
    readback_only: bool = False,
) -> dict[str, Any]:
    if not client_id:
        raise SystemExit("HARNESS_CLIENT_ID or --client-id is required.")

    trace_path = trace_json
    trace = _read_json(trace_path)
    eval_case_path = eval_case_json if eval_case_json else _default_eval_case_path(trace_path)
    eval_case = None if skip_eval_case else _read_json(eval_case_path)
    run_id = str(trace.get("trace_id") or trace.get("run_id") or trace_path.stem)

    client_args = argparse.Namespace(
        base_url=base_url or harness_api.DEFAULT_BASE_URL,
        client_id=client_id,
        api_key=api_key,
        dry_run=dry_run,
    )

    publish_task_id = task_id
    planned: list[dict[str, Any]] = []
    results: list[Any] = []
    if readback_only:
        if not publish_task_id:
            raise SystemExit("--readback-only requires --task-id.")
        ingest_actions = trace_ingest.build_actions(str(publish_task_id), trace, trace_path)
        expected_trace_artifact = trace_ingest.expected_trace_artifact_from_actions(ingest_actions)
        eval_case_body = _eval_case_with_task(eval_case, str(publish_task_id), trace_path) if eval_case is not None else None
        if dry_run:
            revalidation = _revalidation_fields(
                task_id=str(publish_task_id),
                trace_path=trace_path,
                eval_case_path=eval_case_path if eval_case is not None else None,
                client_id=client_id,
                base_url=client_args.base_url,
            )
            return {
                "status": "dry_run",
                "mode": "readback_only",
                "task_id": publish_task_id,
                "trace_json": str(trace_path),
                "eval_case_json": str(eval_case_path) if eval_case is not None else None,
                "expected_trace_artifact": expected_trace_artifact,
                "trace_action_count": len(ingest_actions),
                "readback_actions": _publish_readback_actions(str(publish_task_id), include_eval_cases=eval_case_body is not None),
                **revalidation,
            }
        expected_counts = _expected_evidence_counts(ingest_actions)
        expected_projection_counts = _expected_projection_counts(trace, ingest_actions)
        expected_model_response_projection_maps = _expected_model_response_projection_maps(
            trace,
            ingest_actions,
        )
        readback = _readback_published_harness_state(
            client_args,
            task_id=str(publish_task_id),
            eval_case_body=eval_case_body,
            expected_counts=expected_counts,
            expected_projection_counts=expected_projection_counts,
            expected_model_response_projection_maps=expected_model_response_projection_maps,
            expected_trace_artifact=expected_trace_artifact,
        )
        revalidation = _revalidation_fields(
            task_id=str(publish_task_id),
            trace_path=trace_path,
            eval_case_path=eval_case_path if eval_case is not None else None,
            client_id=client_id,
            base_url=client_args.base_url,
        )
        return {
            "status": "completed",
            "mode": "readback_only",
            "task_id": publish_task_id,
            "trace_json": str(trace_path),
            "eval_case_json": str(eval_case_path) if eval_case is not None else None,
            "expected_trace_artifact": expected_trace_artifact,
            "trace_action_count": len(ingest_actions),
            "evidence_counts": readback["evidence_counts"],
            "readback": readback,
            **revalidation,
        }

    if not publish_task_id:
        task_body = _evidence_holder_task_body(
            run_id=run_id,
            cwd=cwd or str(ROOT),
            idempotency_key=idempotency_key or f"fitcheck-live-smoke-export-{run_id}",
            trace_path=trace_path,
        )
        task_action = {"method": "POST", "path": "/tasks?background=false&start=false", "body": task_body}
        planned.append(task_action)
        if dry_run:
            publish_task_id = "<created_task_id>"
        else:
            created = harness_api.request(client_args, task_action["method"], task_action["path"], task_action["body"])
            results.append({"action": "create_task", "result": created})
            publish_task_id = str(created.get("task_id") or "")
            if not publish_task_id:
                raise SystemExit("harness task creation did not return task_id")

    ingest_actions = trace_ingest.build_actions(str(publish_task_id), trace, trace_path)
    expected_trace_artifact = trace_ingest.expected_trace_artifact_from_actions(ingest_actions)
    planned.extend(ingest_actions)
    if dry_run:
        revalidation = _revalidation_fields(
            task_id=str(publish_task_id),
            trace_path=trace_path,
            eval_case_path=eval_case_path if eval_case is not None else None,
            client_id=client_id,
            base_url=client_args.base_url,
        )
        if eval_case is not None:
            planned.append({"method": "POST", "path": "/eval-cases", "body": _eval_case_with_task(eval_case, str(publish_task_id), trace_path)})
        return {
            "status": "dry_run",
            "mode": "publish",
            "task_id": publish_task_id,
            "trace_json": str(trace_path),
            "eval_case_json": str(eval_case_path) if eval_case is not None else None,
            "expected_trace_artifact": expected_trace_artifact,
            "actions": planned,
            **revalidation,
        }

    for action in ingest_actions:
        results.append(
            {
                "action": f"{action['method']} {action['path']}",
                "result": harness_api.request(client_args, action["method"], action["path"], action.get("body")),
            }
        )

    eval_case_result = None
    eval_case_body = None
    if eval_case is not None:
        eval_case_body = _eval_case_with_task(eval_case, str(publish_task_id), trace_path)
        eval_case_result = harness_api.request(client_args, "POST", "/eval-cases", eval_case_body)

    expected_counts = _expected_evidence_counts(ingest_actions)
    expected_projection_counts = _expected_projection_counts(trace, ingest_actions)
    expected_model_response_projection_maps = _expected_model_response_projection_maps(
        trace,
        ingest_actions,
    )
    readback = _readback_published_harness_state(
        client_args,
        task_id=str(publish_task_id),
        eval_case_body=eval_case_body,
        expected_counts=expected_counts,
        expected_projection_counts=expected_projection_counts,
        expected_model_response_projection_maps=expected_model_response_projection_maps,
        expected_trace_artifact=expected_trace_artifact,
    )
    evidence_counts = readback["evidence_counts"]
    revalidation = _revalidation_fields(
        task_id=str(publish_task_id),
        trace_path=trace_path,
        eval_case_path=eval_case_path if eval_case is not None else None,
        client_id=client_id,
        base_url=client_args.base_url,
    )
    return {
        "status": "completed",
        "mode": "publish",
        "task_id": publish_task_id,
        "trace_json": str(trace_path),
        "eval_case_json": str(eval_case_path) if eval_case is not None else None,
        "expected_trace_artifact": expected_trace_artifact,
        "trace_action_count": len(ingest_actions),
        "eval_case_id": (eval_case_result or {}).get("eval_case_id") if isinstance(eval_case_result, dict) else None,
        "evidence_counts": evidence_counts,
        "readback": readback,
        **revalidation,
    }


def _publish_readback_actions(task_id: str, *, include_eval_cases: bool) -> list[dict[str, str]]:
    actions = [
        {"method": "GET", "path": f"/tasks/{task_id}"},
        {"method": "GET", "path": f"/tasks/{task_id}/evidence"},
        {"method": "GET", "path": f"/tasks/{task_id}/sdk-events"},
        {"method": "GET", "path": f"/tasks/{task_id}/sdk-events?summary=true"},
        {"method": "GET", "path": f"/tasks/{task_id}/client-transcript"},
        {"method": "GET", "path": f"/tasks/{task_id}/client-transcript?summary=true"},
        {"method": "SSE", "path": f"/tasks/{task_id}/events?after=0"},
    ]
    if include_eval_cases:
        actions.append({"method": "GET", "path": "/eval-cases"})
    return actions


def _revalidation_inputs_from_summary(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    for key in ("revalidation_inputs", "harness_publish_revalidation_inputs"):
        inputs = payload.get(key)
        if isinstance(inputs, dict):
            return inputs
    for key in ("fitcheck_live_smoke_publish_summary", "publish_summary", "readback_summary"):
        nested = payload.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("revalidation_inputs"), dict):
            return nested["revalidation_inputs"]
    nested_publish = payload.get("harness_publish")
    if isinstance(nested_publish, dict) and isinstance(nested_publish.get("revalidation_inputs"), dict):
        return nested_publish["revalidation_inputs"]
    raise SystemExit(f"{path} does not contain revalidation_inputs")


def _summary_revalidation_fields(
    *,
    summary_path: Path,
    client_id: str,
    base_url: str,
) -> dict[str, Any]:
    inputs = {
        "source": "fitcheck_live_smoke_publish_summary_revalidation_inputs",
        "revalidate_from_summary_json": str(summary_path),
        "client_id": client_id,
        "base_url": base_url,
        "compact_output": True,
    }
    required_fields = ["revalidate_from_summary_json", "client_id", "base_url"]
    missing_fields = [
        field
        for field in required_fields
        if _missing_revalidation_value(inputs.get(field))
    ]
    return {
        "summary_revalidation_inputs": inputs,
        "summary_revalidation_ready": not missing_fields,
        "summary_revalidation_required_fields": required_fields,
        "summary_revalidation_missing_fields": missing_fields,
        "summary_revalidation_cli_args": [
            "--revalidate-from-summary-json",
            str(summary_path),
            "--client-id",
            client_id,
            "--base-url",
            base_url,
            "--compact-output",
        ],
    }


def _revalidation_fields(
    *,
    task_id: str,
    trace_path: Path,
    eval_case_path: Path | None,
    client_id: str,
    base_url: str,
) -> dict[str, Any]:
    trace_json = str(trace_path)
    eval_case_json = str(eval_case_path) if eval_case_path is not None else None
    inputs = {
        "source": "fitcheck_live_smoke_publish_revalidation_inputs",
        "task_id": task_id,
        "trace_json": trace_json,
        "eval_case_json": eval_case_json,
        "client_id": client_id,
        "base_url": base_url,
        "readback_only": True,
        "compact_output": True,
        "skip_eval_case": eval_case_path is None,
    }
    required_fields = ["task_id", "trace_json", "client_id", "base_url"]
    if eval_case_path is not None:
        required_fields.append("eval_case_json")
    missing_fields = [
        field
        for field in required_fields
        if _missing_revalidation_value(inputs.get(field))
    ]
    cli_args = [
        "--readback-only",
        "--task-id",
        task_id,
        "--trace-json",
        trace_json,
        "--client-id",
        client_id,
        "--base-url",
        base_url,
        "--compact-output",
    ]
    if eval_case_json:
        cli_args.extend(["--eval-case-json", eval_case_json])
    else:
        cli_args.append("--skip-eval-case")
    return {
        "revalidation_inputs": inputs,
        "revalidation_ready": not missing_fields,
        "revalidation_required_fields": required_fields,
        "revalidation_missing_fields": missing_fields,
        "revalidation_cli_args": cli_args,
    }


def _missing_revalidation_value(value: Any) -> bool:
    text = str(value or "").strip()
    return not text or text.startswith("<") or text.endswith(">")


def _compact_publish_result(result: dict[str, Any]) -> dict[str, Any]:
    readback = result.get("readback") if isinstance(result.get("readback"), dict) else {}
    validation = readback.get("validation") if isinstance(readback.get("validation"), dict) else {}
    event_replay = readback.get("event_replay") if isinstance(readback.get("event_replay"), dict) else {}
    event_replay_validation = (
        event_replay.get("validation") if isinstance(event_replay.get("validation"), dict) else {}
    )
    event_replay_summary = event_replay.get("summary") if isinstance(event_replay.get("summary"), dict) else {}
    sdk_events = readback.get("sdk_events") if isinstance(readback.get("sdk_events"), dict) else {}
    client_transcript = (
        readback.get("client_transcript") if isinstance(readback.get("client_transcript"), dict) else {}
    )
    record_result_readback = (
        result.get("record_result_readback") if isinstance(result.get("record_result_readback"), dict) else {}
    )
    record_result_validation = (
        record_result_readback.get("validation")
        if isinstance(record_result_readback.get("validation"), dict)
        else {}
    )
    record_result_gate = (
        result.get("record_result_gate") if isinstance(result.get("record_result_gate"), dict) else {}
    )
    return {
        "source": "fitcheck_live_smoke_publish_compact_summary",
        "status": result.get("status"),
        "publish_status": result.get("publish_status"),
        "mode": result.get("mode"),
        "task_id": result.get("task_id"),
        "trace_json": result.get("trace_json"),
        "eval_case_json": result.get("eval_case_json"),
        "expected_trace_artifact": result.get("expected_trace_artifact"),
        "trace_action_count": result.get("trace_action_count"),
        "readback_action_count": len(result.get("readback_actions") or []),
        "readback_actions": result.get("readback_actions") or [],
        "revalidation_inputs": result.get("revalidation_inputs") or {},
        "revalidation_ready": result.get("revalidation_ready"),
        "revalidation_required_fields": result.get("revalidation_required_fields") or [],
        "revalidation_missing_fields": result.get("revalidation_missing_fields") or [],
        "revalidation_cli_args": result.get("revalidation_cli_args") or [],
        "summary_revalidation_inputs": result.get("summary_revalidation_inputs") or {},
        "summary_revalidation_ready": result.get("summary_revalidation_ready"),
        "summary_revalidation_required_fields": result.get("summary_revalidation_required_fields") or [],
        "summary_revalidation_missing_fields": result.get("summary_revalidation_missing_fields") or [],
        "summary_revalidation_cli_args": result.get("summary_revalidation_cli_args") or [],
        "eval_case_id": result.get("eval_case_id"),
        "record_result": result.get("record_result") or {},
        "record_result_readback": record_result_readback,
        "record_result_readback_valid": record_result_validation.get("valid"),
        "record_result_readback_failed_checks": record_result_validation.get("failed_checks") or [],
        "record_result_readback_sdk_event_match_count": record_result_readback.get("sdk_event_match_count"),
        "record_result_readback_transcript_match_count": record_result_readback.get("transcript_match_count"),
        "record_result_gate": record_result_gate,
        "record_result_gate_valid": record_result_gate.get("valid"),
        "record_result_gate_failed_checks": record_result_gate.get("failed_checks") or [],
        "evidence_counts": result.get("evidence_counts") or readback.get("evidence_counts"),
        "readback_valid": validation.get("valid"),
        "readback_check_count": validation.get("check_count"),
        "readback_failed_checks": validation.get("failed_checks") or [],
        "trace_artifact_binding": validation.get("trace_artifact_binding") or {},
        "sdk_events_projected_event_count": sdk_events.get("projected_event_count"),
        "sdk_events_raw_event_count": sdk_events.get("raw_event_count"),
        "client_transcript_tool_progress_count": client_transcript.get("tool_progress_count"),
        "client_transcript_agent_model_response_count": client_transcript.get("agent_model_response_count"),
        "event_replay_valid": event_replay_validation.get("valid"),
        "event_replay_check_count": event_replay_validation.get("check_count"),
        "event_replay_failed_checks": event_replay_validation.get("failed_checks") or [],
        "event_replay_event_count": event_replay_summary.get("event_count"),
        "event_replay_expected_event_counts": event_replay.get("expected_event_counts") or {},
        "event_replay_observed_event_counts": event_replay.get("observed_event_counts") or {},
        "event_replay_fetch_plan": event_replay.get("event_replay_fetch_plan") or {},
        "claim_boundary": (
            "This compact summary validates stored generic-harness evidence for a FitCheck "
            "live-smoke export; it is not a new live product or provider call."
        ),
    }


def _output_json_payload(result: dict[str, Any], *, compact_output: bool) -> dict[str, Any]:
    if not compact_output:
        return result
    compact = _compact_publish_result(result)
    payload = dict(result)
    payload.update(compact)
    payload["publish_summary"] = compact
    payload["compact_summary"] = compact
    return payload


def _readback_published_harness_state(
    client_args: argparse.Namespace,
    *,
    task_id: str,
    eval_case_body: dict[str, Any] | None,
    expected_counts: dict[str, int],
    expected_projection_counts: dict[str, int | bool],
    expected_model_response_projection_maps: dict[str, dict[str, int]],
    expected_trace_artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task = harness_api.request(client_args, "GET", f"/tasks/{task_id}", None)
    evidence = harness_api.request(client_args, "GET", f"/tasks/{task_id}/evidence", None)
    sdk_events = harness_api.request(client_args, "GET", f"/tasks/{task_id}/sdk-events", None)
    sdk_event_summary = harness_api.request(client_args, "GET", f"/tasks/{task_id}/sdk-events?summary=true", None)
    transcript = harness_api.request(client_args, "GET", f"/tasks/{task_id}/client-transcript", None)
    transcript_summary = harness_api.request(client_args, "GET", f"/tasks/{task_id}/client-transcript?summary=true", None)
    eval_cases = harness_api.request(client_args, "GET", "/eval-cases", None) if eval_case_body is not None else {}

    evidence_counts = {
        "tool_calls": len(evidence.get("tool_calls") or []),
        "observations": len(evidence.get("observations") or []),
        "artifacts": len(evidence.get("artifacts") or []),
        "verification_results": len(evidence.get("verification_results") or []),
    }
    event_replay_fetch_plan = trace_ingest.event_replay_fetch_plan(evidence_counts, sdk_event_summary)
    event_replay = trace_ingest.summarize_event_replay(
        trace_ingest.fetch_event_replay(
            client_args,
            task_id,
            max_events=int(event_replay_fetch_plan["max_events"]),
        )
    )
    event_replay["fetch_plan"] = event_replay_fetch_plan
    event_replay_result_summary = {
        "artifact_count": evidence_counts["artifacts"],
        "observation_count": evidence_counts["observations"],
        "tool_call_count": evidence_counts["tool_calls"],
        "tool_call_status_update_expected_count": trace_ingest.tool_call_status_update_expected_count_from_records(
            list(evidence.get("tool_calls") or []) + list(evidence.get("observations") or []),
            tool_call_id_key=None,
            observation_id_key=None,
        ),
    }
    event_replay_validation = trace_ingest.validate_event_replay(
        event_replay,
        event_replay_result_summary,
        expected_task_id=task_id,
    )
    artifact_summaries = trace_ingest.summarize_artifacts(evidence)
    expected_artifact = expected_trace_artifact if isinstance(expected_trace_artifact, dict) else {}
    expected_trace_digest = str(expected_artifact.get("digest") or "").strip()
    expected_trace_path = str(expected_artifact.get("path") or "").strip()
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
    sdk_event_type_counts = _type_counts(sdk_events.get("events") or [])
    sdk_stream_event_counts = _stream_event_counts(sdk_events.get("events") or [])
    transcript_stream_event_counts = _stream_event_counts(transcript.get("agent_stream_events") or [])
    transcript_counts = {
        "messages": len(transcript.get("messages") or []),
        "tool_progress": len(transcript.get("tool_progress") or []),
        "agent_model_response": len(transcript.get("agent_model_responses") or []),
        "agent_phase": len(transcript.get("agent_phases") or []),
        "agent_session": len(transcript.get("agent_sessions") or []),
        "agent_handoff": len(transcript.get("agent_handoffs") or []),
        "agent_approval": len(transcript.get("agent_approvals") or []),
        "agent_question": len(transcript.get("agent_questions") or []),
        "agent_guidance": len(transcript.get("agent_guidance") or []),
        "agent_guardrail": len(transcript.get("agent_guardrails") or []),
        "agent_run_error": len(transcript.get("agent_run_errors") or []),
        "agent_stream_event": len(transcript.get("agent_stream_events") or []),
    }
    matching_eval_case = _matching_eval_case(eval_cases, eval_case_body, task_id) if eval_case_body is not None else None
    source_endpoints = transcript.get("source_endpoints") if isinstance(transcript.get("source_endpoints"), dict) else {}
    checks = {
        "task_readable": task.get("task_id") == task_id,
        "artifact_recorded": evidence_counts["artifacts"] >= expected_counts.get("artifacts", 0),
        "tool_calls_recorded": evidence_counts["tool_calls"] >= expected_counts.get("tool_calls", 0),
        "observations_recorded": evidence_counts["observations"] >= expected_counts.get("observations", 0),
        "sdk_events_projected": sdk_events.get("status") == "completed" and int(sdk_events.get("projected_event_count") or 0) > 0,
        "client_transcript_projected": transcript.get("status") == "completed" and "evidence" in source_endpoints and "sdk_events" in source_endpoints,
        "eval_case_readable": eval_case_body is None or matching_eval_case is not None,
        "sdk_tool_progress_projected": _at_least(sdk_event_type_counts.get("tool_progress"), expected_projection_counts.get("tool_progress")),
        "transcript_tool_progress_projected": _at_least(transcript_counts.get("tool_progress"), expected_projection_counts.get("tool_progress")),
        "sdk_model_responses_projected": _at_least(sdk_event_type_counts.get("agent_model_response"), expected_projection_counts.get("agent_model_response")),
        "transcript_model_responses_projected": _at_least(transcript_counts.get("agent_model_response"), expected_projection_counts.get("agent_model_response")),
        "sdk_phases_projected": _at_least(sdk_event_type_counts.get("agent_phase"), expected_projection_counts.get("agent_phase")),
        "transcript_phases_projected": _at_least(transcript_counts.get("agent_phase"), expected_projection_counts.get("agent_phase")),
        "sdk_sessions_projected": _at_least(sdk_event_type_counts.get("agent_session"), expected_projection_counts.get("agent_session")),
        "transcript_sessions_projected": _at_least(transcript_counts.get("agent_session"), expected_projection_counts.get("agent_session")),
        "sdk_handoffs_projected": _at_least(sdk_event_type_counts.get("agent_handoff"), expected_projection_counts.get("agent_handoff")),
        "transcript_handoffs_projected": _at_least(transcript_counts.get("agent_handoff"), expected_projection_counts.get("agent_handoff")),
        "sdk_approvals_projected": _at_least(sdk_event_type_counts.get("agent_approval"), expected_projection_counts.get("agent_approval")),
        "transcript_approvals_projected": _at_least(transcript_counts.get("agent_approval"), expected_projection_counts.get("agent_approval")),
        "sdk_questions_projected": _at_least(sdk_event_type_counts.get("agent_question"), expected_projection_counts.get("agent_question")),
        "transcript_questions_projected": _at_least(transcript_counts.get("agent_question"), expected_projection_counts.get("agent_question")),
        "sdk_guidance_projected": _at_least(sdk_event_type_counts.get("agent_guidance"), expected_projection_counts.get("agent_guidance")),
        "transcript_guidance_projected": _at_least(transcript_counts.get("agent_guidance"), expected_projection_counts.get("agent_guidance")),
        "sdk_guardrails_projected": _at_least(sdk_event_type_counts.get("agent_guardrail"), expected_projection_counts.get("agent_guardrail")),
        "transcript_guardrails_projected": _at_least(transcript_counts.get("agent_guardrail"), expected_projection_counts.get("agent_guardrail")),
        "sdk_run_errors_projected": _at_least(sdk_event_type_counts.get("agent_run_error"), expected_projection_counts.get("agent_run_error")),
        "transcript_run_errors_projected": _at_least(transcript_counts.get("agent_run_error"), expected_projection_counts.get("agent_run_error")),
        "sdk_stream_events_projected": _at_least(sdk_event_type_counts.get("agent_stream_event"), expected_projection_counts.get("agent_stream_event")),
        "transcript_stream_events_projected": _at_least(transcript_counts.get("agent_stream_event"), expected_projection_counts.get("agent_stream_event")),
        "sdk_final_output_projected": (
            _at_least(sdk_event_type_counts.get("final_output"), expected_projection_counts.get("final_output"))
            and (
                not bool(expected_projection_counts.get("final_output_required"))
                or sdk_events.get("final_output_included") is True
            )
        ),
        "sdk_summary_requested": sdk_event_summary.get("summary_requested") is True,
        "sdk_summary_source_server": sdk_event_summary.get("source") == "harness_sdk_events_summary",
        "sdk_summary_projected_count_matches": sdk_event_summary.get("projected_event_count") == sdk_events.get("projected_event_count"),
        "sdk_summary_model_response_count_matches": _summary_count_matches(sdk_event_summary, sdk_event_type_counts, "agent_model_response"),
        "sdk_summary_model_response_provider_counts_match": _dict_or_empty(
            sdk_event_summary.get("agent_model_response_provider_counts")
        )
        == expected_model_response_projection_maps["agent_model_response_provider_counts"],
        "sdk_summary_model_response_provider_counts_known": _projection_count_map_known(
            expected_model_response_projection_maps["agent_model_response_provider_counts"],
            expected_projection_counts.get("agent_model_response"),
        ),
        "sdk_summary_model_response_api_style_counts_match": _dict_or_empty(
            sdk_event_summary.get("agent_model_response_api_style_counts")
        )
        == expected_model_response_projection_maps["agent_model_response_api_style_counts"],
        "sdk_summary_model_response_api_style_counts_known": _projection_count_map_known(
            expected_model_response_projection_maps["agent_model_response_api_style_counts"],
            expected_projection_counts.get("agent_model_response"),
        ),
        "sdk_summary_phase_count_matches": _summary_count_matches(sdk_event_summary, sdk_event_type_counts, "agent_phase"),
        "sdk_summary_session_count_matches": _summary_count_matches(sdk_event_summary, sdk_event_type_counts, "agent_session"),
        "sdk_summary_handoff_count_matches": _summary_count_matches(sdk_event_summary, sdk_event_type_counts, "agent_handoff"),
        "sdk_summary_approval_count_matches": _summary_count_matches(sdk_event_summary, sdk_event_type_counts, "agent_approval"),
        "sdk_summary_question_count_matches": _summary_count_matches(sdk_event_summary, sdk_event_type_counts, "agent_question"),
        "sdk_summary_guidance_count_matches": _summary_count_matches(sdk_event_summary, sdk_event_type_counts, "agent_guidance"),
        "sdk_summary_guardrail_count_matches": _summary_count_matches(sdk_event_summary, sdk_event_type_counts, "agent_guardrail"),
        "sdk_summary_run_error_count_matches": _summary_count_matches(sdk_event_summary, sdk_event_type_counts, "agent_run_error"),
        "sdk_summary_stream_event_count_matches": _summary_count_matches(sdk_event_summary, sdk_event_type_counts, "agent_stream_event"),
        "sdk_summary_stream_event_type_counts_match": _summary_map_matches(
            sdk_event_summary,
            sdk_stream_event_counts,
            "agent_stream_event_type_counts",
            "event_type",
        ),
        "sdk_summary_stream_event_name_counts_match": _summary_map_matches(
            sdk_event_summary,
            sdk_stream_event_counts,
            "agent_stream_event_name_counts",
            "event_name",
        ),
        "sdk_summary_stream_raw_event_type_counts_match": _summary_map_matches(
            sdk_event_summary,
            sdk_stream_event_counts,
            "agent_stream_raw_event_type_counts",
            "raw_event_type",
        ),
        "sdk_summary_stream_item_class_counts_match": _summary_map_matches(
            sdk_event_summary,
            sdk_stream_event_counts,
            "agent_stream_item_class_counts",
            "item_class",
        ),
        "sdk_summary_stream_tool_name_counts_match": _summary_map_matches(
            sdk_event_summary,
            sdk_stream_event_counts,
            "agent_stream_tool_name_counts",
            "tool_name",
        ),
        "sdk_summary_final_output_count_matches": _summary_count_matches(sdk_event_summary, sdk_event_type_counts, "final_output", "final_output_count"),
        "transcript_summary_requested": transcript_summary.get("summary_requested") is True,
        "transcript_summary_source_server": transcript_summary.get("source") == "harness_client_transcript_summary",
        "transcript_summary_model_response_count_matches": _summary_count_matches(transcript_summary, transcript_counts, "agent_model_response"),
        "transcript_summary_model_response_provider_counts_match": _dict_or_empty(
            transcript_summary.get("agent_model_response_provider_counts")
        )
        == expected_model_response_projection_maps["agent_model_response_provider_counts"],
        "transcript_summary_model_response_provider_counts_known": _projection_count_map_known(
            expected_model_response_projection_maps["agent_model_response_provider_counts"],
            expected_projection_counts.get("agent_model_response"),
        ),
        "transcript_summary_model_response_api_style_counts_match": _dict_or_empty(
            transcript_summary.get("agent_model_response_api_style_counts")
        )
        == expected_model_response_projection_maps["agent_model_response_api_style_counts"],
        "transcript_summary_model_response_api_style_counts_known": _projection_count_map_known(
            expected_model_response_projection_maps["agent_model_response_api_style_counts"],
            expected_projection_counts.get("agent_model_response"),
        ),
        "transcript_summary_phase_count_matches": _summary_count_matches(transcript_summary, transcript_counts, "agent_phase"),
        "transcript_summary_session_count_matches": _summary_count_matches(transcript_summary, transcript_counts, "agent_session"),
        "transcript_summary_handoff_count_matches": _summary_count_matches(transcript_summary, transcript_counts, "agent_handoff"),
        "transcript_summary_approval_count_matches": _summary_count_matches(transcript_summary, transcript_counts, "agent_approval"),
        "transcript_summary_question_count_matches": _summary_count_matches(transcript_summary, transcript_counts, "agent_question"),
        "transcript_summary_guidance_count_matches": _summary_count_matches(transcript_summary, transcript_counts, "agent_guidance"),
        "transcript_summary_guardrail_count_matches": _summary_count_matches(transcript_summary, transcript_counts, "agent_guardrail"),
        "transcript_summary_run_error_count_matches": _summary_count_matches(transcript_summary, transcript_counts, "agent_run_error"),
        "transcript_summary_stream_event_count_matches": _summary_count_matches(transcript_summary, transcript_counts, "agent_stream_event"),
        "transcript_summary_stream_event_type_counts_match": _summary_map_matches(
            transcript_summary,
            transcript_stream_event_counts,
            "agent_stream_event_type_counts",
            "event_type",
        ),
        "transcript_summary_stream_event_name_counts_match": _summary_map_matches(
            transcript_summary,
            transcript_stream_event_counts,
            "agent_stream_event_name_counts",
            "event_name",
        ),
        "transcript_summary_stream_raw_event_type_counts_match": _summary_map_matches(
            transcript_summary,
            transcript_stream_event_counts,
            "agent_stream_raw_event_type_counts",
            "raw_event_type",
        ),
        "transcript_summary_stream_item_class_counts_match": _summary_map_matches(
            transcript_summary,
            transcript_stream_event_counts,
            "agent_stream_item_class_counts",
            "item_class",
        ),
        "transcript_summary_stream_tool_name_counts_match": _summary_map_matches(
            transcript_summary,
            transcript_stream_event_counts,
            "agent_stream_tool_name_counts",
            "tool_name",
        ),
        "transcript_summary_item_type_counts_present": isinstance(transcript_summary.get("item_type_counts"), dict),
        "event_replay_valid": event_replay_validation.get("valid") is True,
    }
    event_replay_checks = (
        event_replay_validation.get("checks")
        if isinstance(event_replay_validation.get("checks"), dict)
        else {}
    )
    checks.update(event_replay_checks)
    if expected_artifact:
        checks.update(
            {
                "expected_trace_artifact_digest_present": bool(expected_trace_digest),
                "agent_trace_artifact_present": bool(agent_trace_artifacts),
                "agent_trace_artifact_digest_matches_expected": (
                    bool(matching_trace_artifacts) if expected_trace_digest else False
                ),
            }
        )
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    readback = {
        "task": {
            "task_id": task.get("task_id"),
            "status": task.get("status"),
            "state_version": task.get("state_version"),
            "client_id": task.get("client_id"),
        },
        "evidence_counts": evidence_counts,
        "expected_min_evidence_counts": expected_counts,
        "expected_min_projection_counts": expected_projection_counts,
        "expected_model_response_projection_maps": expected_model_response_projection_maps,
        "expected_trace_artifact": expected_artifact,
        "artifact_summaries": artifact_summaries,
        "sdk_events": {
            "projected_event_count": sdk_events.get("projected_event_count"),
            "raw_event_count": sdk_events.get("raw_event_count"),
            "cursor_policy": sdk_events.get("cursor_policy"),
            "final_output_included": sdk_events.get("final_output_included"),
            "event_type_counts": sdk_event_type_counts,
            "stream_event_counts": sdk_stream_event_counts,
            "summary": _read_model_summary_slice(sdk_event_summary),
        },
        "client_transcript": {
            "message_count": transcript_counts["messages"],
            "tool_progress_count": transcript_counts["tool_progress"],
            "agent_model_response_count": transcript_counts["agent_model_response"],
            "agent_phase_count": transcript_counts["agent_phase"],
            "agent_session_count": transcript_counts["agent_session"],
            "agent_handoff_count": transcript_counts["agent_handoff"],
            "agent_approval_count": transcript_counts["agent_approval"],
            "agent_question_count": transcript_counts["agent_question"],
            "agent_guidance_count": transcript_counts["agent_guidance"],
            "agent_guardrail_count": transcript_counts["agent_guardrail"],
            "agent_run_error_count": transcript_counts["agent_run_error"],
            "agent_stream_event_count": transcript_counts["agent_stream_event"],
            "stream_event_counts": transcript_stream_event_counts,
            "item_type_counts": transcript_summary.get("item_type_counts"),
            "summary": _read_model_summary_slice(transcript_summary),
            "source_endpoints_present": sorted(source_endpoints),
        },
        "event_replay": {
            "validation": event_replay_validation,
            "summary": event_replay_validation.get("event_replay_summary"),
            "expected_event_counts": event_replay_validation.get("expected_event_counts"),
            "observed_event_counts": event_replay_validation.get("observed_event_counts"),
            "event_replay_count_map_totals": event_replay_validation.get("event_replay_count_map_totals"),
            "event_replay_fetch_plan": event_replay_validation.get("event_replay_fetch_plan"),
            "result_summary": event_replay_result_summary,
        },
        "eval_case": {
            "required": eval_case_body is not None,
            "found": matching_eval_case is not None,
            "eval_case_id": (matching_eval_case or {}).get("eval_case_id"),
            "name": (matching_eval_case or {}).get("name"),
            "source_task_id": (matching_eval_case or {}).get("source_task_id"),
            "status": (matching_eval_case or {}).get("status"),
        },
        "validation": {
            "valid": not failed_checks,
            "check_count": len(checks),
            "failed_checks": failed_checks,
            "checks": checks,
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
        },
    }
    if not readback["validation"]["valid"]:
        raise SystemExit(f"harness publish readback validation failed: {json.dumps(readback, ensure_ascii=False, sort_keys=True)}")
    return readback


def _record_publish_revalidation_result(
    client_args: argparse.Namespace,
    *,
    record_task_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    body = _publish_revalidation_observation_body(result)
    if client_args.dry_run:
        return {
            "status": "dry_run",
            "task_id": record_task_id,
            "body": body,
            "claim_boundary": "Dry run only; no harness observation was recorded.",
        }
    try:
        recorded = harness_api.request(client_args, "POST", f"/tasks/{record_task_id}/observations", body)
    except BaseException as exc:
        return {
            "status": "failed",
            "task_id": record_task_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    return {
        "status": "completed",
        "task_id": record_task_id,
        "observation_id": recorded.get("observation_id") if isinstance(recorded, dict) else None,
        "tool_name": recorded.get("tool_name") if isinstance(recorded, dict) else None,
        "observation_status": recorded.get("status") if isinstance(recorded, dict) else None,
        "evidence_refs": recorded.get("evidence_refs") if isinstance(recorded, dict) else None,
    }


def _readback_recorded_revalidation_result(
    client_args: argparse.Namespace,
    *,
    record_task_id: str,
    record_result: dict[str, Any],
) -> dict[str, Any]:
    if client_args.dry_run:
        return {
            "status": "dry_run",
            "task_id": record_task_id,
            "claim_boundary": "Dry run only; no recorded observation readback was fetched.",
        }
    if record_result.get("status") != "completed":
        return {
            "status": "skipped",
            "task_id": record_task_id,
            "reason": "record_result_not_completed",
            "record_result_status": record_result.get("status"),
        }

    observation_id = record_result.get("observation_id")
    tool_name = record_result.get("tool_name") or "eval_case_verification"
    evidence = harness_api.request(client_args, "GET", f"/tasks/{record_task_id}/evidence", None)
    sdk_events = harness_api.request(client_args, "GET", f"/tasks/{record_task_id}/sdk-events", None)
    sdk_summary = harness_api.request(client_args, "GET", f"/tasks/{record_task_id}/sdk-events?summary=true", None)
    transcript = harness_api.request(client_args, "GET", f"/tasks/{record_task_id}/client-transcript", None)
    transcript_summary = harness_api.request(
        client_args,
        "GET",
        f"/tasks/{record_task_id}/client-transcript?summary=true",
        None,
    )

    evidence_matches = _records_matching_observation_id(
        evidence.get("observations") or [],
        observation_id=observation_id,
        tool_name=tool_name,
    )
    sdk_event_matches = _records_matching_observation_id(
        sdk_events.get("events") or [],
        observation_id=observation_id,
        tool_name="agent_eval_case_verification",
        type_field="type",
    )
    transcript_matches = _records_matching_observation_id(
        transcript.get("agent_eval_case_verifications") or [],
        observation_id=observation_id,
        tool_name="agent_eval_case_verification",
        type_field="type",
    )
    failure_label = "fitcheck_live_smoke_publish_revalidation"
    sdk_failure_labels = _dict_or_empty(sdk_summary.get("agent_eval_case_failure_label_counts"))
    transcript_failure_labels = _dict_or_empty(
        transcript_summary.get("agent_eval_case_failure_label_counts")
    )
    checks = {
        "record_result_completed": record_result.get("status") == "completed",
        "record_result_observation_id_present": observation_id is not None,
        "record_result_tool_name_matches": tool_name == "eval_case_verification",
        "evidence_observation_present": bool(evidence_matches),
        "sdk_event_projection_present": bool(sdk_event_matches),
        "client_transcript_projection_present": bool(transcript_matches),
        "sdk_summary_eval_case_verification_count_present": _at_least(
            sdk_summary.get("agent_eval_case_verification_count"),
            max(1, len(sdk_event_matches)),
        ),
        "transcript_summary_eval_case_verification_count_present": _at_least(
            transcript_summary.get("agent_eval_case_verification_count"),
            max(1, len(transcript_matches)),
        ),
        "sdk_summary_failure_label_count_present": _at_least(
            sdk_failure_labels.get(failure_label),
            1,
        ),
        "transcript_summary_failure_label_count_present": _at_least(
            transcript_failure_labels.get(failure_label),
            1,
        ),
    }
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    return {
        "status": "completed" if not failed_checks else "failed",
        "task_id": record_task_id,
        "observation_id": observation_id,
        "tool_name": tool_name,
        "evidence_observation_match_count": len(evidence_matches),
        "sdk_event_match_count": len(sdk_event_matches),
        "transcript_match_count": len(transcript_matches),
        "sdk_summary": _read_model_summary_slice(sdk_summary),
        "client_transcript_summary": _read_model_summary_slice(transcript_summary),
        "validation": {
            "valid": not failed_checks,
            "check_count": len(checks),
            "failed_checks": failed_checks,
            "checks": checks,
        },
        "claim_boundary": (
            "Readback of the just-recorded eval_case_verification observation through stored evidence, "
            "SDK-event projection, and client transcript projection only."
        ),
    }


def attach_record_result_gate(
    result: dict[str, Any],
    *,
    client_args: argparse.Namespace,
    record_task_id: str,
) -> dict[str, Any]:
    updated = dict(result)
    updated["record_result"] = _record_publish_revalidation_result(
        client_args,
        record_task_id=record_task_id,
        result=updated,
    )
    updated["record_result_readback"] = _readback_recorded_revalidation_result(
        client_args,
        record_task_id=record_task_id,
        record_result=updated["record_result"],
    )
    updated["record_result_gate"] = _record_result_gate(updated)
    if updated["record_result_gate"].get("valid") is not True:
        updated["publish_status"] = updated.get("status")
        updated["status"] = "failed"
    return updated


def _record_result_gate(result: dict[str, Any]) -> dict[str, Any]:
    record_result = result.get("record_result") if isinstance(result.get("record_result"), dict) else {}
    readback = (
        result.get("record_result_readback")
        if isinstance(result.get("record_result_readback"), dict)
        else {}
    )
    if record_result.get("status") == "dry_run" or readback.get("status") == "dry_run":
        return {
            "status": "dry_run",
            "valid": True,
            "failed_checks": [],
            "claim_boundary": "Dry run only; no record-result gate was enforced.",
        }
    readback_validation = (
        readback.get("validation") if isinstance(readback.get("validation"), dict) else {}
    )
    checks = {
        "record_result_completed": record_result.get("status") == "completed",
        "recorded_observation_completed": record_result.get("observation_status") == "completed",
        "record_result_observation_id_present": record_result.get("observation_id") is not None,
        "record_result_readback_completed": readback.get("status") == "completed",
        "record_result_readback_valid": readback_validation.get("valid") is True,
    }
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    readback_failed_checks = readback_validation.get("failed_checks") or []
    failed_checks.extend(f"readback:{item}" for item in readback_failed_checks)
    return {
        "status": "completed" if not failed_checks else "failed",
        "valid": not failed_checks,
        "failed_checks": failed_checks,
        "checks": checks,
        "claim_boundary": (
            "Overall CLI gate for recording and readback-projecting the publisher revalidation result."
        ),
    }


def _publish_revalidation_observation_body(result: dict[str, Any]) -> dict[str, Any]:
    readback = result.get("readback") if isinstance(result.get("readback"), dict) else {}
    validation = readback.get("validation") if isinstance(readback.get("validation"), dict) else {}
    event_replay = readback.get("event_replay") if isinstance(readback.get("event_replay"), dict) else {}
    event_validation = event_replay.get("validation") if isinstance(event_replay.get("validation"), dict) else {}
    eval_case = readback.get("eval_case") if isinstance(readback.get("eval_case"), dict) else {}
    failed_checks = list(validation.get("failed_checks") or [])
    failed_checks.extend(f"event_replay:{item}" for item in event_validation.get("failed_checks") or [])
    expected_status_matches = result.get("status") == "completed" and validation.get("valid") is True
    command = " ".join(str(item) for item in result.get("revalidation_cli_args") or [])
    evidence_refs = [f"task:{result.get('task_id')}"]
    trace_artifact = result.get("expected_trace_artifact") if isinstance(result.get("expected_trace_artifact"), dict) else {}
    trace_name = trace_artifact.get("name")
    if trace_name:
        evidence_refs.append(f"artifact:agent_trace:{trace_name}")
    if eval_case.get("eval_case_id") is not None:
        evidence_refs.append(f"eval_case:{eval_case.get('eval_case_id')}")
    payload = {
        "source": "fitcheck_live_smoke_publish_revalidation",
        "status": "completed" if expected_status_matches else "failed",
        "execute": True,
        "eval_case_id": eval_case.get("eval_case_id"),
        "name": eval_case.get("name"),
        "source_task_id": result.get("task_id"),
        "failure_label": "fitcheck_live_smoke_publish_revalidation",
        "verifier_command": command,
        "cwd": str(ROOT),
        "expected_status": "completed",
        "expected_status_matches": expected_status_matches,
        "failed_checks": failed_checks,
        "execution": {
            "status": "completed" if expected_status_matches else "failed",
            "executed_command": command,
            "returncode": 0 if expected_status_matches else 1,
            "stdout_tail": "",
            "stderr_tail": "",
        },
        "selected_eval_case": eval_case,
        "readback_valid": validation.get("valid"),
        "event_replay_valid": event_validation.get("valid"),
        "event_replay_expected_event_counts": event_replay.get("expected_event_counts") or {},
        "event_replay_observed_event_counts": event_replay.get("observed_event_counts") or {},
        "claim_boundary": (
            "Stored FitCheck live-smoke publish revalidation evidence only; this observation does not "
            "prove a new live product, provider, or search call."
        ),
    }
    return {
        "call_id": f"fitcheck-publish-revalidation-{result.get('task_id')}",
        "tool_name": "eval_case_verification",
        "result": payload,
        "evidence_refs": evidence_refs,
        "status": "completed" if expected_status_matches else "failed",
        "metadata": {
            "source": "publish_live_agent_smoke_export.py",
            "recorded_from": "fitcheck-live-smoke-publish-revalidation",
            "source_task_id": result.get("task_id"),
            "eval_case_id": eval_case.get("eval_case_id"),
        },
    }


def _read_model_summary_slice(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: summary.get(key)
        for key in [
            "source",
            "summary_requested",
            "event_type_counts",
            "item_type_counts",
            "agent_guidance_count",
            "agent_guidance_reasons",
            "agent_guidance_reason_counts",
            "agent_guidance_suggested_tool_count",
            "agent_model_response_count",
            "agent_model_response_provider_counts",
            "agent_model_response_api_style_counts",
            "agent_stream_event_count",
            "agent_stream_event_type_counts",
            "agent_stream_event_name_counts",
            "agent_stream_raw_event_type_counts",
            "agent_stream_item_class_counts",
            "agent_stream_tool_name_counts",
            "agent_phase_count",
            "agent_session_count",
            "agent_handoff_count",
            "agent_approval_count",
            "agent_question_count",
            "agent_guardrail_count",
            "agent_run_error_count",
            "agent_eval_case_verification_count",
            "agent_eval_case_verification_failed_count",
            "agent_eval_case_failure_label_counts",
            "missing_info_question_count",
            "final_output_count",
            "final_output_included",
            "claim_boundary",
        ]
        if key in summary
    }


def _records_matching_observation_id(
    records: list[Any],
    *,
    observation_id: Any,
    tool_name: str,
    type_field: str = "tool_name",
) -> list[dict[str, Any]]:
    matches = []
    for item in records:
        if not isinstance(item, dict):
            continue
        if item.get(type_field) != tool_name:
            continue
        if observation_id is None or str(item.get("observation_id")) == str(observation_id):
            matches.append(item)
    return matches


def _expected_evidence_counts(actions: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "artifacts": sum(1 for action in actions if str(action.get("path") or "").endswith("/artifacts")),
        "tool_calls": sum(1 for action in actions if str(action.get("path") or "").endswith("/tool-calls")),
        "observations": sum(1 for action in actions if str(action.get("path") or "").endswith("/observations")),
    }


def _expected_projection_counts(trace: dict[str, Any], ingest_actions: list[dict[str, Any]]) -> dict[str, int | bool]:
    agent_approval_count = _action_observation_count(ingest_actions, "agent_approval_request") + _action_observation_count(
        ingest_actions,
        "agent_approval_decision",
    )
    final_output_count = _action_observation_count(ingest_actions, "agent_final_output")
    return {
        "tool_progress": _action_tool_call_count(ingest_actions),
        "agent_model_response": _action_observation_count(ingest_actions, "agent_model_response"),
        "agent_phase": _action_observation_count(ingest_actions, "agent_run_phase"),
        "agent_session": _action_observation_count(ingest_actions, "agent_session"),
        "agent_handoff": _action_observation_count(ingest_actions, "agent_handoff"),
        "agent_approval": agent_approval_count,
        "agent_question": _action_observation_count(ingest_actions, "agent_question"),
        "agent_guidance": _action_observation_count(ingest_actions, "agent_guidance"),
        "agent_guardrail": _action_observation_count(ingest_actions, "agent_guardrail_result"),
        "agent_run_error": _action_observation_count(ingest_actions, "agent_run_error"),
        "agent_stream_event": _action_observation_count(ingest_actions, "agent_stream_event"),
        "final_output": final_output_count,
        "final_output_required": final_output_count > 0 or bool(str(trace.get("final_output") or "").strip()),
    }


def _expected_model_response_projection_maps(
    trace: dict[str, Any],
    ingest_actions: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    model_response_results = []
    for action in ingest_actions:
        if not str(action.get("path") or "").endswith("/observations"):
            continue
        body = action.get("body") if isinstance(action.get("body"), dict) else {}
        if body.get("tool_name") != "agent_model_response":
            continue
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        model_response_results.append(result)
    return {
        "agent_model_response_provider_counts": _model_response_result_counts(
            model_response_results,
            "provider",
            fallback=trace.get("provider"),
        ),
        "agent_model_response_api_style_counts": _model_response_result_counts(
            model_response_results,
            "api_style",
            fallback=trace.get("api_style"),
        ),
    }


def _model_response_result_counts(results: list[dict[str, Any]], field: str, *, fallback: Any = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        key = str(item.get(field) or fallback or "none").strip() or "none"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _action_tool_call_count(actions: list[dict[str, Any]]) -> int:
    return sum(1 for action in actions if str(action.get("path") or "").endswith("/tool-calls"))


def _action_observation_count(actions: list[dict[str, Any]], tool_name: str) -> int:
    count = 0
    for action in actions:
        if not str(action.get("path") or "").endswith("/observations"):
            continue
        body = action.get("body") if isinstance(action.get("body"), dict) else {}
        if body.get("tool_name") == tool_name:
            count += 1
    return count


def _type_counts(items: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if not item_type:
            continue
        counts[item_type] = counts.get(item_type, 0) + 1
    return counts


def _stream_event_counts(items: list[Any]) -> dict[str, dict[str, int]]:
    events = [item for item in items if isinstance(item, dict) and item.get("type") == "agent_stream_event"]
    return {
        "event_type": _field_counts(events, "stream_event_type"),
        "event_name": _field_counts(events, "stream_event_name"),
        "raw_event_type": _field_counts(events, "raw_event_type"),
        "item_class": _field_counts(events, "item_class"),
        "tool_name": _field_counts(events, "tool_name"),
    }


def _field_counts(items: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(item.get(field) or "none").strip() or "none"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _summary_count_matches(
    summary: dict[str, Any],
    observed_counts: dict[str, int],
    item_type: str,
    summary_field: str | None = None,
) -> bool:
    field = summary_field or f"{item_type}_count"
    try:
        summary_count = int(summary.get(field) or 0)
    except (TypeError, ValueError):
        summary_count = 0
    try:
        observed_count = int(observed_counts.get(item_type) or 0)
    except (TypeError, ValueError):
        observed_count = 0
    return summary_count == observed_count


def _summary_map_matches(
    summary: dict[str, Any],
    observed_counts: dict[str, dict[str, int]],
    summary_field: str,
    observed_key: str,
) -> bool:
    return _dict_or_empty(summary.get(summary_field)) == _dict_or_empty(observed_counts.get(observed_key))


def _projection_count_map_known(values: dict[str, int], expected: Any) -> bool:
    try:
        expected_int = int(expected or 0)
    except (TypeError, ValueError):
        expected_int = 0
    if expected_int <= 0:
        return not values
    if not values or "none" in values:
        return False
    return sum(values.values()) >= expected_int


def _dict_or_empty(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for key, item in value.items():
        try:
            counts[str(key)] = int(item or 0)
        except (TypeError, ValueError):
            counts[str(key)] = 0
    return counts


def _at_least(actual: Any, expected: Any) -> bool:
    try:
        expected_int = int(expected or 0)
    except (TypeError, ValueError):
        expected_int = 0
    if expected_int <= 0:
        return True
    try:
        actual_int = int(actual or 0)
    except (TypeError, ValueError):
        actual_int = 0
    return actual_int >= expected_int


def _matching_eval_case(
    eval_cases: dict[str, Any],
    eval_case_body: dict[str, Any] | None,
    task_id: str,
) -> dict[str, Any] | None:
    if eval_case_body is None:
        return None
    expected_name = str(eval_case_body.get("name") or "")
    for item in eval_cases.get("eval_cases") or []:
        if not isinstance(item, dict):
            continue
        if item.get("source_task_id") == task_id and str(item.get("name") or "") == expected_name:
            return item
    return None


def _evidence_holder_task_body(
    *,
    run_id: str,
    cwd: str,
    idempotency_key: str,
    trace_path: Path,
) -> dict[str, Any]:
    return {
        "prompt": (
            f"Evidence holder for FitCheck live agent SDK smoke export {run_id}. "
            "Preserve trace/eval artifacts for harness regression work; this task is "
            "generic harness evidence, not the FitCheck product runtime."
        ),
        "cwd": cwd,
        "sandbox": "read-only",
        "idempotency_key": idempotency_key,
        "metadata": {
            "agentic_loop_template": "backend_owned_tool_loop_hardening",
            "source": "fitcheck_live_agent_smoke",
            "fitcheck_run_id": run_id,
            "trace_json": str(trace_path),
        },
        "verification": {"commands": [], "timeout_seconds": 120},
    }


def _eval_case_with_task(eval_case: dict[str, Any], task_id: str, trace_path: Path) -> dict[str, Any]:
    output = dict(eval_case)
    output["source_task_id"] = task_id
    refs = [str(item) for item in output.get("evidence_refs") or []]
    for ref in [f"task:{task_id}", f"artifact:agent_trace:{trace_path.name}"]:
        if ref not in refs:
            refs.append(ref)
    output["evidence_refs"] = refs
    metadata = dict(output.get("metadata") or {})
    metadata["published_by"] = "publish_live_agent_smoke_export.py"
    metadata["source_task_id"] = task_id
    output["metadata"] = metadata
    return output


def _default_eval_case_path(trace_path: Path) -> Path:
    name = trace_path.name.replace("-fitcheck-live-agent-trace.json", "-fitcheck-live-agent-eval-case.json")
    if name == trace_path.name:
        name = f"{trace_path.stem}-eval-case.json"
    return trace_path.with_name(name)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise SystemExit(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return payload


def _write_json_file(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(path)}


if __name__ == "__main__":
    raise SystemExit(main())
