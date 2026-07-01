#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import harness_api  # type: ignore  # noqa: E402
import trace_ingest  # type: ignore  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
SMOKE_SCRIPT = SCRIPT_DIR / "agents_sdk_smoke.py"
EVENT_REPLAY_COUNT_TOTAL_CHECK_KEYS = (
    "event_replay_sse_event_name_count_total_matches_event_count",
    "event_replay_event_type_count_total_matches_event_count",
    "event_replay_event_status_count_total_matches_event_count",
    "event_replay_event_actor_count_total_matches_event_count",
)
DEFAULT_SCENARIOS = [
    "tool",
    "handoff",
    "approval",
    "guardrail",
    "guardrail_tripwire",
    "session",
    "stream",
    "stream_handoff",
]
PROJECTION_COUNT_KEYS = [
    "agent_model_response_count",
    "agent_stream_event_count",
    "agent_phase_count",
    "agent_session_count",
    "agent_handoff_count",
    "agent_approval_count",
    "agent_question_count",
    "agent_guidance_count",
    "agent_guardrail_count",
    "agent_run_error_count",
    "tool_progress_count",
    "final_output_count",
]
PROJECTION_MAP_KEYS = [
    "agent_model_response_provider_counts",
    "agent_model_response_api_style_counts",
]
MATRIX_ARTIFACT_CHECK_KEYS = [
    "matrix_artifact_recorded",
    "matrix_artifact_path_recorded",
    "matrix_artifact_digest_recorded",
    "matrix_artifact_path_matches_payload",
    "matrix_artifact_digest_matches_recorded_path",
    "matrix_artifact_digest_matches_expected_path",
]
EVENT_REPLAY_FETCH_PLAN_CHECK_KEYS = [
    "event_replay_fetch_plan_covers_expected_raw_event_count",
    "event_replay_not_truncated_by_fetch_limit",
]
PROVIDER_PROBE_SUMMARY_KEYS = [
    "provider_probe_valid",
    "provider_probe_failed_checks",
    "provider_probe_diagnostic_id",
    "provider_probe_check_count",
    "provider_probe_checks",
    "provider_probe_status",
    "provider_probe_http_status",
    "provider_probe_endpoint",
    "provider_probe_models_preview_count",
    "provider_probe_profile",
    "provider_probe_agent_sdk_fit",
]
EVAL_CASE_FAILURE_SIGNATURE_VERSION = "agents_sdk_matrix_failure_v2"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a live OpenAI Agents SDK smoke matrix and optionally publish trace evidence."
    )
    parser.add_argument("--model", default=os.getenv("FITCHECK_LLM_MODEL") or "gpt-4.1-mini")
    parser.add_argument("--scenario", action="append", choices=[*DEFAULT_SCENARIOS, "all"], default=[])
    parser.add_argument("--output-dir", default=str(ROOT / "harness_outputs" / "sdk-live-smoke-matrix"))
    parser.add_argument("--output-json", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--marker-prefix", default="AGENTS_SDK_MATRIX")
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--max-tokens", type=int, default=120)
    parser.add_argument("--tracing-disabled", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--probe-provider",
        action="store_true",
        help="Probe the configured OpenAI provider through the harness before running smoke scenarios.",
    )
    parser.add_argument(
        "--publish-readback",
        action="store_true",
        help="Create one harness evidence-holder task per scenario, ingest the trace, and validate readback.",
    )
    parser.add_argument(
        "--publish-matrix-readback",
        action="store_true",
        help=(
            "Create an additional harness evidence-holder task for the aggregate matrix JSON and validate "
            "its stored evidence readback."
        ),
    )
    parser.add_argument(
        "--validate-matrix-readback-task-id",
        default="",
        help="Validate an existing aggregate matrix evidence-holder task without running live smoke scenarios.",
    )
    parser.add_argument(
        "--validate-matrix-json",
        default="",
        help="Matrix JSON payload to compare against --validate-matrix-readback-task-id.",
    )
    parser.add_argument(
        "--validate-published-scenarios",
        action="store_true",
        help=(
            "During existing matrix readback validation, also refetch each published scenario "
            "evidence-holder task from the matrix JSON and rerun generic, projection, and event "
            "replay readback validation without live SDK/provider calls."
        ),
    )
    parser.add_argument(
        "--revalidate-from-summary-json",
        default="",
        help=(
            "Read revalidation_inputs from a previous compact or full summary JSON and run "
            "stored-evidence matrix readback validation without live SDK/provider calls."
        ),
    )
    parser.add_argument(
        "--compact-output",
        action="store_true",
        help=(
            "Print a compact controller summary to stdout while preserving the full payload "
            "in --output-json."
        ),
    )
    parser.add_argument(
        "--record-eval-case-on-failure",
        action="store_true",
        help=(
            "When stored-evidence matrix revalidation fails, record a harness eval case "
            "that can rerun the same validation command."
        ),
    )
    parser.add_argument(
        "--eval-case-verifier-command",
        default="",
        help=(
            "Optional verifier command to store on failure eval cases. Defaults to a "
            "non-secret agents_sdk_smoke_matrix.py revalidation command."
        ),
    )
    parser.add_argument("--base-url", default=os.getenv("HARNESS_BASE_URL", harness_api.DEFAULT_BASE_URL))
    parser.add_argument("--client-id", default=os.getenv("HARNESS_CLIENT_ID"))
    parser.add_argument("--api-key", default=os.getenv("HARNESS_API_KEY"))
    parser.add_argument("--cwd", default=str(ROOT / "developer_harness"))
    parser.add_argument("--idempotency-prefix", default="agents-sdk-smoke-matrix")
    args = parser.parse_args()

    scenarios = normalize_scenarios(args.scenario)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir)
    matrix_output = Path(args.output_json) if args.output_json else output_dir / f"agents-sdk-smoke-matrix-{run_id}.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    matrix_output.parent.mkdir(parents=True, exist_ok=True)

    if args.revalidate_from_summary_json:
        payload = validate_revalidation_summary_json(args, Path(args.revalidate_from_summary_json))
        maybe_record_failure_eval_case(args, payload)
        if args.output_json:
            payload["summary_output_json"] = str(matrix_output)
            payload["readback_validation_summary"] = existing_matrix_readback_compact_summary(payload)
        if args.output_json:
            write_json(matrix_output, payload)
        stdout_payload = (
            payload["readback_validation_summary"]
            if args.compact_output
            else payload
        )
        print(json.dumps(stdout_payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("status") == "completed" else 1

    validation_mode = bool(args.validate_matrix_readback_task_id or args.validate_matrix_json)
    if validation_mode:
        if not args.validate_matrix_readback_task_id or not args.validate_matrix_json:
            raise SystemExit("--validate-matrix-readback-task-id and --validate-matrix-json are both required.")
        if not args.client_id:
            raise SystemExit("HARNESS_CLIENT_ID or --client-id is required for matrix readback validation.")
        client_args = argparse.Namespace(
            base_url=args.base_url,
            client_id=args.client_id,
            api_key=args.api_key,
            dry_run=False,
        )
        payload = validate_existing_matrix_readback(
            client_args,
            args.validate_matrix_readback_task_id,
            Path(args.validate_matrix_json),
            validate_published_scenarios=args.validate_published_scenarios,
        )
        maybe_record_failure_eval_case(args, payload)
        if args.output_json:
            payload["summary_output_json"] = str(matrix_output)
            payload["readback_validation_summary"] = existing_matrix_readback_compact_summary(payload)
        if args.output_json:
            write_json(matrix_output, payload)
        stdout_payload = (
            payload["readback_validation_summary"]
            if args.compact_output
            else payload
        )
        print(json.dumps(stdout_payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("status") == "completed" else 1

    if (args.probe_provider or args.publish_readback or args.publish_matrix_readback) and not args.client_id:
        raise SystemExit(
            "HARNESS_CLIENT_ID or --client-id is required for --probe-provider, --publish-readback, "
            "or --publish-matrix-readback."
        )

    plan = build_plan(args, scenarios, run_id, output_dir, matrix_output)
    if args.dry_run:
        payload = {
            "status": "dry_run",
            "source": "agents_sdk_smoke_matrix",
            "run_id": run_id,
            "scenario_count": len(scenarios),
            "plan": plan,
        }
        payload["matrix_run_summary"] = matrix_run_compact_summary(payload)
        write_json(matrix_output, payload)
        print(json.dumps(payload["matrix_run_summary"] if args.compact_output else payload, ensure_ascii=False, indent=2))
        return 0

    client_args = argparse.Namespace(
        base_url=args.base_url,
        client_id=args.client_id,
        api_key=args.api_key,
        dry_run=False,
    )
    provider_probe = run_provider_probe(client_args, args) if args.probe_provider else None
    provider_probe_validation = validate_provider_probe(provider_probe) if args.probe_provider else None
    scenario_results: list[dict[str, Any]] = []
    for scenario in scenarios:
        result = run_smoke_scenario(args, scenario, run_id, output_dir)
        if args.publish_readback and result.get("passed"):
            result["publish_readback"] = publish_trace_readback(client_args, args, result, run_id)
        scenario_results.append(result)
        if args.fail_fast and not scenario_passed(result, require_publish=args.publish_readback):
            break

    summary = summarize_matrix(
        scenario_results,
        publish_readback=args.publish_readback,
        provider_probe_validation=provider_probe_validation,
    )
    payload = {
        "status": summary["status"],
        "source": "agents_sdk_smoke_matrix",
        "run_id": run_id,
        "model": args.model,
        "provider": "openai",
        "api_style": "responses",
        "base_url": args.base_url,
        "client_id": args.client_id,
        "scenario_count": len(scenario_results),
        "requested_scenarios": scenarios,
        "provider_probe": provider_probe,
        "provider_probe_validation": provider_probe_validation,
        "summary": summary,
        "results": scenario_results,
        "matrix_output_json": str(matrix_output),
        "claim_boundary": (
            "Matrix result summarizes live smoke output and optional stored harness readback evidence. "
            "Published readbacks are stored harness evidence, not proof of future provider connectivity."
        ),
    }
    if args.publish_matrix_readback:
        matrix_evidence_path = aggregate_evidence_source_path(matrix_output)
        payload["matrix_evidence_json"] = str(matrix_evidence_path)
        write_json(matrix_evidence_path, payload)
        matrix_readback = publish_matrix_readback(client_args, args, payload, matrix_evidence_path, run_id)
        payload["matrix_readback"] = matrix_readback
        attach_matrix_gate_readback_summary(
            summary,
            matrix_readback,
            prefix="matrix_readback",
            failure_reason="matrix_readback_failed",
        )
        payload_readback = validate_published_matrix_payload_readback(
            client_args,
            str(matrix_readback.get("task_id") or ""),
            payload,
        )
        payload["matrix_payload_readback_validation"] = payload_readback
        attach_matrix_gate_readback_summary(
            summary,
            payload_readback,
            prefix="matrix_payload_readback",
            failure_reason="matrix_payload_readback_failed",
        )
        if matrix_readback.get("status") != "completed":
            summary["status"] = "failed"
            payload["status"] = "failed"
        if payload_readback.get("status") != "completed":
            summary["status"] = "failed"
            payload["status"] = "failed"
    payload["matrix_run_summary"] = matrix_run_compact_summary(payload)
    write_json(matrix_output, payload)
    stdout_payload = payload["matrix_run_summary"] if args.compact_output else payload
    print(json.dumps(stdout_payload, ensure_ascii=False, indent=2))
    if summary["status"] == "completed":
        return 0
    if summary["status"] == "configuration_required":
        return 2
    return 1


def attach_matrix_gate_readback_summary(
    summary: dict[str, Any],
    readback: dict[str, Any],
    *,
    prefix: str,
    failure_reason: str,
) -> None:
    validation = readback.get("validation") if isinstance(readback.get("validation"), dict) else {}
    provider_diagnostic = (
        readback.get("provider_diagnostic_readback")
        if isinstance(readback.get("provider_diagnostic_readback"), dict)
        else {}
    )
    failed_checks = list_value(validation.get("failed_checks"))
    if provider_diagnostic.get("valid") is False:
        failed_checks.extend(
            f"provider_diagnostic:{name}"
            for name in list_value(provider_diagnostic.get("failed_checks"))
        )
    status = readback.get("status")
    summary[f"{prefix}_status"] = status
    summary[f"{prefix}_valid"] = (
        status == "completed"
        and validation.get("valid") is True
        and provider_diagnostic.get("valid") is not False
    )
    summary[f"{prefix}_failed_checks"] = failed_checks
    summary[f"{prefix}_task_binding"] = (
        validation.get("task_binding") if isinstance(validation.get("task_binding"), dict) else {}
    )
    if readback.get("error"):
        summary[f"{prefix}_error"] = readback.get("error")
    if status != "completed":
        add_aggregate_failed_reason(summary, failure_reason)


def add_aggregate_failed_reason(summary: dict[str, Any], reason: str) -> None:
    reasons = summary.get("aggregate_failed_reasons")
    if not isinstance(reasons, list):
        reasons = []
        summary["aggregate_failed_reasons"] = reasons
    if reason not in reasons:
        reasons.append(reason)


REVALIDATION_INPUT_REQUIRED_FIELDS = [
    "validate_matrix_readback_task_id",
    "validate_matrix_json",
    "client_id",
    "base_url",
]


def revalidation_input_readiness(inputs: dict[str, Any]) -> dict[str, Any]:
    missing_fields = [
        field for field in REVALIDATION_INPUT_REQUIRED_FIELDS if not inputs.get(field)
    ]
    return {
        "ready": not missing_fields,
        "missing_fields": missing_fields,
        "required_fields": list(REVALIDATION_INPUT_REQUIRED_FIELDS),
        "claim_boundary": (
            "Readiness only checks required non-secret CLI inputs for stored-evidence "
            "revalidation. It does not prove that the referenced harness task still validates."
        ),
    }


def revalidation_cli_args(inputs: dict[str, Any]) -> list[str]:
    args: list[str] = []
    if inputs.get("validate_matrix_readback_task_id"):
        args.extend(
            [
                "--validate-matrix-readback-task-id",
                str(inputs["validate_matrix_readback_task_id"]),
            ]
        )
    if inputs.get("validate_matrix_json"):
        args.extend(["--validate-matrix-json", str(inputs["validate_matrix_json"])])
    if inputs.get("validate_published_scenarios"):
        args.append("--validate-published-scenarios")
    if inputs.get("compact_output"):
        args.append("--compact-output")
    if inputs.get("client_id"):
        args.extend(["--client-id", str(inputs["client_id"])])
    if inputs.get("base_url"):
        args.extend(["--base-url", str(inputs["base_url"])])
    return args


def summary_revalidation_details(
    summary_path: Any,
    revalidation_readiness: dict[str, Any],
) -> dict[str, Any]:
    path = str(summary_path or "")
    missing_fields = list_value(revalidation_readiness.get("missing_fields"))
    summary_missing_fields = list(missing_fields)
    if not path:
        summary_missing_fields.insert(0, "revalidate_from_summary_json")
    cli_args: list[str] = []
    if path:
        cli_args.extend(["--revalidate-from-summary-json", path])
    cli_args.append("--compact-output")
    return {
        "inputs": {
            "revalidate_from_summary_json": path or None,
            "compact_output": True,
        },
        "ready": not summary_missing_fields,
        "missing_fields": summary_missing_fields,
        "cli_args": cli_args,
    }


def artifact_summaries_external_api_called(artifacts: Any) -> bool | None:
    saw_value = False
    for artifact in list_value(artifacts):
        if not isinstance(artifact, dict):
            continue
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        if "external_api_called" not in metadata:
            continue
        saw_value = True
        if metadata.get("external_api_called") is True:
            return True
    return False if saw_value else None


def unique_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in list_value(values):
        text = str(value or "")
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def short_text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def compact_guardrail_result(surface: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    guardrail = value.get("guardrail") if isinstance(value.get("guardrail"), dict) else {}
    output = value.get("output") if isinstance(value.get("output"), dict) else {}
    output_info = output.get("output_info") if isinstance(output.get("output_info"), dict) else {}
    agent = value.get("agent") if isinstance(value.get("agent"), dict) else {}
    return {
        "surface": surface,
        "result_class": value.get("class") or "",
        "guardrail_class": guardrail.get("class") or "",
        "guardrail_name": guardrail.get("name") or "",
        "tripwire_triggered": output.get("tripwire_triggered") is True,
        "output_status": output_info.get("status") or "",
        "reason": output_info.get("reason") or "",
        "agent_name": agent.get("name") or "",
        "agent_output_present": value.get("agent_output") is not None,
    }


def compact_guardrail_projection_summary(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    sdk_counts = projection_count_map(source.get("sdk_events_summary"))
    transcript_counts = projection_count_map(source.get("client_transcript_summary"))
    keys = ("agent_guardrail_count", "agent_run_error_count", "final_output_count")
    sdk_projection = {key: int_value(sdk_counts.get(key)) for key in keys if key in sdk_counts}
    transcript_projection = {
        key: int_value(transcript_counts.get(key)) for key in keys if key in transcript_counts
    }
    guardrail_or_error_count = (
        int_value(sdk_projection.get("agent_guardrail_count"))
        + int_value(sdk_projection.get("agent_run_error_count"))
        + int_value(transcript_projection.get("agent_guardrail_count"))
        + int_value(transcript_projection.get("agent_run_error_count"))
    )
    if guardrail_or_error_count <= 0:
        return {}
    return {
        "sdk_events": sdk_projection,
        "client_transcript": transcript_projection,
        "guardrail_projected": (
            int_value(sdk_projection.get("agent_guardrail_count")) > 0
            or int_value(transcript_projection.get("agent_guardrail_count")) > 0
        ),
        "run_error_projected": (
            int_value(sdk_projection.get("agent_run_error_count")) > 0
            or int_value(transcript_projection.get("agent_run_error_count")) > 0
        ),
        "final_output_projected": (
            int_value(sdk_projection.get("final_output_count")) > 0
            or int_value(transcript_projection.get("final_output_count")) > 0
        ),
    }


def parse_arguments_map(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def compact_tool_item(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    raw_item = value.get("raw_item") if isinstance(value.get("raw_item"), dict) else {}
    arguments = parse_arguments_map(value.get("arguments") or raw_item.get("arguments"))
    output = parse_arguments_map(value.get("output") or raw_item.get("output"))
    compact = {
        "call_id": value.get("call_id") or raw_item.get("call_id") or "",
        "tool_name": value.get("tool_name") or value.get("name") or raw_item.get("name") or "",
        "item_type": value.get("type") or "",
        "item_class": value.get("class") or "",
        "raw_type": raw_item.get("type") or "",
        "argument_keys": sorted(str(key) for key in arguments),
        "output_keys": sorted(str(key) for key in output),
        "marker_argument_present": "marker" in arguments,
        "marker_output_present": "marker" in output,
        "status_output_key_present": "status" in output,
    }
    return {key: item for key, item in compact.items() if item not in ("", [], {})}


def compact_tool_phase(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    new_items = [item for item in list_value(value.get("new_items")) if isinstance(item, dict)]
    item_types = [
        str(item.get("type") or "unknown")
        for item in new_items
        if item.get("type") is not None
    ]
    return {
        "name": value.get("name") or "",
        "new_item_count": len(new_items),
        "new_item_type_counts": count_values(item_types),
        "tool_call_count": sum(1 for item in new_items if str(item.get("type") or "") == "tool_call_item"),
        "tool_output_count": sum(
            1 for item in new_items if str(item.get("type") or "") == "tool_call_output_item"
        ),
        "message_output_count": sum(
            1 for item in new_items if str(item.get("type") or "") == "message_output_item"
        ),
        "raw_response_count": int_value(value.get("raw_response_count")),
        "interruption_count": int_value(value.get("interruption_count")),
        "final_output_present": value.get("final_output") is not None,
    }


def tool_pair_summaries(
    calls: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_call_id: dict[str, dict[str, Any]] = {}
    for item in calls:
        call_id = str(item.get("call_id") or "")
        by_call_id.setdefault(call_id, {})["call"] = item
    for item in outputs:
        call_id = str(item.get("call_id") or "")
        by_call_id.setdefault(call_id, {})["output"] = item
    pairs: list[dict[str, Any]] = []
    for call_id in sorted(key for key in by_call_id if key):
        pair = by_call_id[call_id]
        call = pair.get("call") if isinstance(pair.get("call"), dict) else {}
        output = pair.get("output") if isinstance(pair.get("output"), dict) else {}
        pairs.append(
            {
                "call_id": call_id,
                "tool_name": call.get("tool_name") or output.get("tool_name") or "",
                "call_recorded": bool(call),
                "output_recorded": bool(output),
                "argument_keys": call.get("argument_keys") or [],
                "output_keys": output.get("output_keys") or [],
                "marker_argument_present": call.get("marker_argument_present") is True,
                "marker_output_present": output.get("marker_output_present") is True,
            }
        )
    return pairs


def compact_live_tool_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    new_items = [item for item in list_value(payload.get("new_items")) if isinstance(item, dict)]
    tool_calls = [
        compact_tool_item(item)
        for item in new_items
        if str(item.get("type") or "") == "tool_call_item"
    ]
    tool_calls = [item for item in tool_calls if item]
    tool_outputs = [
        compact_tool_item(item)
        for item in new_items
        if str(item.get("type") or "") == "tool_call_output_item"
    ]
    tool_outputs = [item for item in tool_outputs if item]
    tool_call_count = int_value(summary.get("tool_call_count")) or len(tool_calls)
    tool_output_count = int_value(summary.get("tool_output_count")) or len(tool_outputs)
    if tool_call_count <= 0 and tool_output_count <= 0:
        return {}
    phases = [compact_tool_phase(item) for item in list_value(payload.get("phases"))]
    phases = [item for item in phases if item]
    phase_names = [
        str(value)
        for value in list_value(summary.get("phase_names"))
        if str(value or "").strip()
    ] or [str(item.get("name") or "") for item in phases if str(item.get("name") or "").strip()]
    pairs = tool_pair_summaries(tool_calls, tool_outputs)
    return {
        "status": payload.get("status") or "",
        "tool_call_count": tool_call_count,
        "tool_output_count": tool_output_count,
        "tool_pair_count": len(pairs),
        "tool_completed_observed": tool_call_count > 0 and tool_output_count > 0,
        "all_tool_calls_paired": bool(pairs) and all(
            item.get("call_recorded") is True and item.get("output_recorded") is True
            for item in pairs
        ),
        "tool_names": unique_strings([item.get("tool_name") for item in tool_calls]),
        "expected_tool_observed": any(
            item.get("tool_name") == "record_signal" for item in tool_calls
        ),
        "phase_count": int_value(summary.get("phase_count")) or len(phases),
        "phase_names": phase_names,
        "phase_new_item_counts": normalized_event_count_map(summary.get("phase_new_item_counts")),
        "phase_raw_response_counts": normalized_event_count_map(
            summary.get("phase_raw_response_counts")
        ),
        "phase_interruption_counts": normalized_event_count_map(
            summary.get("phase_interruption_counts")
        ),
        "new_item_type_counts": normalized_event_count_map(summary.get("new_item_type_counts")),
        "final_ok": summary.get("final_ok") is True,
        "marker_observed": summary.get("marker_observed") is True,
        "call_summaries": tool_calls,
        "output_summaries": tool_outputs,
        "tool_pairs": pairs,
        "phase_summaries": phases,
        "claim_boundary": (
            "Sanitized live tool-call state from the SDK smoke result. Raw prompts, final text, "
            "tool argument values, and tool output values are not copied into this compact summary."
        ),
    }


def compact_tool_read_model_summary(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    counts = projection_count_map(summary)
    item_type_counts = normalized_event_count_map(summary.get("item_type_counts"))
    tool_progress_count = int_value(counts.get("tool_progress_count"))
    if tool_progress_count <= 0 and int_value(item_type_counts.get("tool_progress")) <= 0:
        return {}
    compact = {
        "summary_requested": summary.get("summary_requested"),
        "task_id": summary.get("task_id") or "",
        "status": summary.get("status") or "",
        "tool_progress_count": tool_progress_count,
        "tool_progress_item_count": int_value(item_type_counts.get("tool_progress")),
        "agent_phase_count": int_value(counts.get("agent_phase_count")),
        "agent_model_response_count": int_value(counts.get("agent_model_response_count")),
        "final_output_count": int_value(counts.get("final_output_count")),
    }
    return {key: value for key, value in compact.items() if value not in ("", None)}


def compact_tool_projection_summary(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    sdk_events = compact_tool_read_model_summary(source.get("sdk_events_summary"))
    client_transcript = compact_tool_read_model_summary(
        source.get("client_transcript_summary")
    )
    if not sdk_events and not client_transcript:
        return {}
    sdk_tool_count = int_value(sdk_events.get("tool_progress_count"))
    transcript_tool_count = int_value(client_transcript.get("tool_progress_count"))
    return {
        "sdk_events": sdk_events,
        "client_transcript": client_transcript,
        "tool_progress_projected": sdk_tool_count > 0 or transcript_tool_count > 0,
        "tool_progress_counts_match": (
            sdk_tool_count == transcript_tool_count
            if sdk_events and client_transcript
            else None
        ),
        "phase_counts_match": (
            int_value(sdk_events.get("agent_phase_count"))
            == int_value(client_transcript.get("agent_phase_count"))
            if sdk_events and client_transcript
            else None
        ),
        "model_response_counts_match": (
            int_value(sdk_events.get("agent_model_response_count"))
            == int_value(client_transcript.get("agent_model_response_count"))
            if sdk_events and client_transcript
            else None
        ),
        "tool_progress_delta": sdk_tool_count - transcript_tool_count,
        "final_output_projected": (
            int_value(sdk_events.get("final_output_count")) > 0
            or int_value(client_transcript.get("final_output_count")) > 0
        ),
    }


def compact_tool_evidence_summary(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    result_summary = (
        source.get("result_summary")
        if isinstance(source.get("result_summary"), dict)
        else {}
    )
    validation = (
        source.get("validation")
        if isinstance(source.get("validation"), dict)
        else {}
    )
    checks = validation.get("checks") if isinstance(validation.get("checks"), dict) else {}
    result_tool_call_name_counts = normalized_event_count_map(
        result_summary.get("tool_call_name_counts")
    )
    result_observation_tool_counts = normalized_event_count_map(
        result_summary.get("observation_tool_counts")
    )
    evidence_tool_call_name_counts = normalized_event_count_map(
        validation.get("evidence_tool_call_name_counts")
    )
    evidence_observation_tool_counts = normalized_event_count_map(
        validation.get("evidence_observation_tool_counts")
    )
    result_tool_call_count = int_value(result_summary.get("tool_call_count"))
    result_observation_count = int_value(result_summary.get("observation_count"))
    if (
        result_tool_call_count <= 0
        and int_value(result_tool_call_name_counts.get("record_signal")) <= 0
        and int_value(evidence_tool_call_name_counts.get("record_signal")) <= 0
    ):
        return {}
    return {
        "task_id": source.get("task_id") or "",
        "status": source.get("status") or "",
        "readback_valid": validation.get("valid"),
        "result_status": result_summary.get("status") or "",
        "result_tool_call_count": result_tool_call_count,
        "result_observation_count": result_observation_count,
        "result_artifact_count": int_value(result_summary.get("artifact_count")),
        "result_tool_call_status_update_expected_count": int_value(
            result_summary.get("tool_call_status_update_expected_count")
        ),
        "result_tool_call_name_counts": result_tool_call_name_counts,
        "result_observation_tool_counts": result_observation_tool_counts,
        "result_status_counts": normalized_event_count_map(result_summary.get("status_counts")),
        "result_call_id_count": len(list_value(result_summary.get("call_ids"))),
        "result_tool_call_pk_count": len(list_value(result_summary.get("tool_call_pks"))),
        "result_observation_id_count": len(list_value(result_summary.get("observation_ids"))),
        "evidence_tool_call_name_counts": evidence_tool_call_name_counts,
        "evidence_observation_tool_counts": evidence_observation_tool_counts,
        "expected_tool_call_recorded": int_value(result_tool_call_name_counts.get("record_signal")) > 0,
        "expected_tool_output_observation_recorded": (
            int_value(result_observation_tool_counts.get("record_signal")) > 0
        ),
        "evidence_expected_tool_call_recorded": (
            int_value(evidence_tool_call_name_counts.get("record_signal")) > 0
        ),
        "evidence_expected_tool_output_observation_recorded": (
            int_value(evidence_observation_tool_counts.get("record_signal")) > 0
        ),
        "artifact_count_covers_result_summary": checks.get("artifact_count_covers_result_summary"),
        "observation_count_covers_result_summary": checks.get("observation_count_covers_result_summary"),
        "tool_call_count_covers_result_summary": checks.get("tool_call_count_covers_result_summary"),
        "claim_boundary": (
            "Sanitized stored tool evidence summary from trace-ingest API results and harness "
            "readback validation. Raw tool arguments, raw tool outputs, observation payloads, "
            "and database primary-key values are not copied into this compact summary."
        ),
    }


def compact_named_evidence_summary(
    source: Any,
    *,
    expected_tool_names: tuple[str, ...] = (),
    expected_observation_tools: tuple[str, ...] = (),
    claim_boundary: str,
) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    result_summary = (
        source.get("result_summary")
        if isinstance(source.get("result_summary"), dict)
        else {}
    )
    validation = (
        source.get("validation")
        if isinstance(source.get("validation"), dict)
        else {}
    )
    checks = validation.get("checks") if isinstance(validation.get("checks"), dict) else {}
    result_tool_call_name_counts = normalized_event_count_map(
        result_summary.get("tool_call_name_counts")
    )
    result_observation_tool_counts = normalized_event_count_map(
        result_summary.get("observation_tool_counts")
    )
    evidence_tool_call_name_counts = normalized_event_count_map(
        validation.get("evidence_tool_call_name_counts")
    )
    evidence_observation_tool_counts = normalized_event_count_map(
        validation.get("evidence_observation_tool_counts")
    )
    result_tool_call_count = int_value(result_summary.get("tool_call_count"))
    result_observation_count = int_value(result_summary.get("observation_count"))
    expected_tool_call_recorded_by_name = {
        name: int_value(result_tool_call_name_counts.get(name)) > 0
        for name in expected_tool_names
    }
    expected_observation_recorded_by_tool = {
        name: int_value(result_observation_tool_counts.get(name)) > 0
        for name in expected_observation_tools
    }
    evidence_expected_tool_call_recorded_by_name = {
        name: int_value(evidence_tool_call_name_counts.get(name)) > 0
        for name in expected_tool_names
    }
    evidence_expected_observation_recorded_by_tool = {
        name: int_value(evidence_observation_tool_counts.get(name)) > 0
        for name in expected_observation_tools
    }
    has_expected_surface = (
        any(expected_tool_call_recorded_by_name.values())
        or any(expected_observation_recorded_by_tool.values())
        or any(evidence_expected_tool_call_recorded_by_name.values())
        or any(evidence_expected_observation_recorded_by_tool.values())
    )
    if result_tool_call_count <= 0 and result_observation_count <= 0 and not has_expected_surface:
        return {}
    return {
        "task_id": source.get("task_id") or "",
        "status": source.get("status") or "",
        "readback_valid": validation.get("valid"),
        "result_status": result_summary.get("status") or "",
        "result_tool_call_count": result_tool_call_count,
        "result_observation_count": result_observation_count,
        "result_artifact_count": int_value(result_summary.get("artifact_count")),
        "result_tool_call_status_update_expected_count": int_value(
            result_summary.get("tool_call_status_update_expected_count")
        ),
        "result_tool_call_name_counts": result_tool_call_name_counts,
        "result_observation_tool_counts": result_observation_tool_counts,
        "result_status_counts": normalized_event_count_map(
            result_summary.get("status_counts")
        ),
        "result_call_id_count": len(list_value(result_summary.get("call_ids"))),
        "result_tool_call_pk_count": len(list_value(result_summary.get("tool_call_pks"))),
        "result_observation_id_count": len(list_value(result_summary.get("observation_ids"))),
        "evidence_tool_call_name_counts": evidence_tool_call_name_counts,
        "evidence_observation_tool_counts": evidence_observation_tool_counts,
        "expected_tool_call_recorded_by_name": expected_tool_call_recorded_by_name,
        "expected_observation_recorded_by_tool": expected_observation_recorded_by_tool,
        "evidence_expected_tool_call_recorded_by_name": (
            evidence_expected_tool_call_recorded_by_name
        ),
        "evidence_expected_observation_recorded_by_tool": (
            evidence_expected_observation_recorded_by_tool
        ),
        "all_expected_tool_calls_recorded": (
            all(expected_tool_call_recorded_by_name.values())
            if expected_tool_call_recorded_by_name
            else None
        ),
        "all_expected_observations_recorded": (
            all(expected_observation_recorded_by_tool.values())
            if expected_observation_recorded_by_tool
            else None
        ),
        "evidence_all_expected_tool_calls_recorded": (
            all(evidence_expected_tool_call_recorded_by_name.values())
            if evidence_expected_tool_call_recorded_by_name
            else None
        ),
        "evidence_all_expected_observations_recorded": (
            all(evidence_expected_observation_recorded_by_tool.values())
            if evidence_expected_observation_recorded_by_tool
            else None
        ),
        "artifact_count_covers_result_summary": checks.get(
            "artifact_count_covers_result_summary"
        ),
        "observation_count_covers_result_summary": checks.get(
            "observation_count_covers_result_summary"
        ),
        "tool_call_count_covers_result_summary": checks.get(
            "tool_call_count_covers_result_summary"
        ),
        "claim_boundary": claim_boundary,
    }


def compact_approval_evidence_summary(source: Any) -> dict[str, Any]:
    summary = compact_named_evidence_summary(
        source,
        expected_tool_names=("approval_required_signal",),
        expected_observation_tools=(
            "agent_approval_request",
            "approval_required_signal",
            "agent_approval_decision",
        ),
        claim_boundary=(
            "Sanitized stored approval evidence summary from trace-ingest API results and "
            "harness readback validation. Raw approval items, tool arguments, tool outputs, "
            "observation payloads, call id lists, observation id lists, and database primary-key "
            "values are not copied into this compact summary."
        ),
    )
    if not summary:
        return {}
    observation_flags = summary.get("expected_observation_recorded_by_tool") or {}
    evidence_observation_flags = (
        summary.get("evidence_expected_observation_recorded_by_tool") or {}
    )
    tool_flags = summary.get("expected_tool_call_recorded_by_name") or {}
    evidence_tool_flags = summary.get("evidence_expected_tool_call_recorded_by_name") or {}
    summary.update(
        {
            "expected_approval_tool_call_recorded": (
                tool_flags.get("approval_required_signal") is True
            ),
            "expected_approval_request_observation_recorded": (
                observation_flags.get("agent_approval_request") is True
            ),
            "expected_approval_tool_output_observation_recorded": (
                observation_flags.get("approval_required_signal") is True
            ),
            "expected_approval_decision_observation_recorded": (
                observation_flags.get("agent_approval_decision") is True
            ),
            "evidence_expected_approval_tool_call_recorded": (
                evidence_tool_flags.get("approval_required_signal") is True
            ),
            "evidence_expected_approval_request_observation_recorded": (
                evidence_observation_flags.get("agent_approval_request") is True
            ),
            "evidence_expected_approval_tool_output_observation_recorded": (
                evidence_observation_flags.get("approval_required_signal") is True
            ),
            "evidence_expected_approval_decision_observation_recorded": (
                evidence_observation_flags.get("agent_approval_decision") is True
            ),
        }
    )
    return summary


def compact_handoff_evidence_summary(source: Any) -> dict[str, Any]:
    summary = compact_named_evidence_summary(
        source,
        expected_observation_tools=("agent_handoff",),
        claim_boundary=(
            "Sanitized stored handoff evidence summary from trace-ingest API results and "
            "harness readback validation. Raw handoff items, handoff output payloads, "
            "observation payloads, call id lists, observation id lists, and database primary-key "
            "values are not copied into this compact summary."
        ),
    )
    if not summary:
        return {}
    observation_flags = summary.get("expected_observation_recorded_by_tool") or {}
    evidence_observation_flags = (
        summary.get("evidence_expected_observation_recorded_by_tool") or {}
    )
    summary.update(
        {
            "expected_handoff_observation_recorded": (
                observation_flags.get("agent_handoff") is True
            ),
            "evidence_expected_handoff_observation_recorded": (
                evidence_observation_flags.get("agent_handoff") is True
            ),
        }
    )
    return summary


def compact_guardrail_evidence_summary(
    source: Any,
    *,
    tripwire_expected: bool = False,
) -> dict[str, Any]:
    expected_observation_tools = (
        ("agent_guardrail_result", "agent_run_error")
        if tripwire_expected
        else ("agent_guardrail_result",)
    )
    summary = compact_named_evidence_summary(
        source,
        expected_observation_tools=expected_observation_tools,
        claim_boundary=(
            "Sanitized stored guardrail evidence summary from trace-ingest API results and "
            "harness readback validation. Raw guardrail results, model output text, "
            "run-error payloads, observation payloads, call id lists, observation id lists, "
            "and database primary-key values are not copied into this compact summary."
        ),
    )
    if not summary:
        return {}
    observation_flags = summary.get("expected_observation_recorded_by_tool") or {}
    evidence_observation_flags = (
        summary.get("evidence_expected_observation_recorded_by_tool") or {}
    )
    status_counts = normalized_event_count_map(summary.get("result_status_counts"))
    summary.update(
        {
            "tripwire_expected": tripwire_expected,
            "expected_guardrail_result_observation_recorded": (
                observation_flags.get("agent_guardrail_result") is True
            ),
            "evidence_expected_guardrail_result_observation_recorded": (
                evidence_observation_flags.get("agent_guardrail_result") is True
            ),
            "expected_tripwire_run_error_observation_recorded": (
                observation_flags.get("agent_run_error") is True
                if tripwire_expected
                else None
            ),
            "evidence_expected_tripwire_run_error_observation_recorded": (
                evidence_observation_flags.get("agent_run_error") is True
                if tripwire_expected
                else None
            ),
            "failed_status_count": int_value(status_counts.get("failed")),
            "tripwire_failed_status_recorded": (
                int_value(status_counts.get("failed")) > 0 if tripwire_expected else None
            ),
        }
    )
    return summary


def compact_session_evidence_summary(source: Any) -> dict[str, Any]:
    summary = compact_named_evidence_summary(
        source,
        expected_observation_tools=("agent_session",),
        claim_boundary=(
            "Sanitized stored session evidence summary from trace-ingest API results and "
            "harness readback validation. Raw session item previews, local session database "
            "paths, observation payloads, call id lists, observation id lists, and database "
            "primary-key values are not copied into this compact summary."
        ),
    )
    if not summary:
        return {}
    observation_flags = summary.get("expected_observation_recorded_by_tool") or {}
    evidence_observation_flags = (
        summary.get("evidence_expected_observation_recorded_by_tool") or {}
    )
    sessions = compact_agent_session_summaries(source.get("agent_session_summaries"))
    turn_counts = [int_value(item.get("turn_count")) for item in sessions]
    final_item_counts = [int_value(item.get("final_item_count")) for item in sessions]
    turn_phases = sorted(
        {
            str(phase)
            for session in sessions
            for phase in list_value(session.get("turn_phases"))
            if str(phase or "").strip()
        }
    )
    summary.update(
        {
            "expected_agent_session_observation_recorded": (
                observation_flags.get("agent_session") is True
            ),
            "evidence_expected_agent_session_observation_recorded": (
                evidence_observation_flags.get("agent_session") is True
            ),
            "readback_agent_session_summary_count": len(sessions),
            "readback_agent_session_backends": unique_strings(
                [item.get("backend") for item in sessions]
            ),
            "readback_agent_session_turn_count_max": max(turn_counts, default=0),
            "readback_agent_session_final_item_count_max": max(
                final_item_counts,
                default=0,
            ),
            "readback_agent_session_turn_phases": turn_phases,
            "readback_agent_session_summary_present": bool(sessions),
            "readback_stateful_session_recorded": (
                max(turn_counts, default=0) >= 2
                and max(final_item_counts, default=0) >= 2
            ),
        }
    )
    return summary


def compact_stream_evidence_summary(
    source: Any,
    *,
    expected_tool_name: str = "",
    handoff_expected: bool = False,
) -> dict[str, Any]:
    summary = compact_named_evidence_summary(
        source,
        expected_observation_tools=("agent_stream_event",),
        claim_boundary=(
            "Sanitized stored stream evidence summary from trace-ingest API results and "
            "harness readback validation. Raw stream items, raw response events, model output "
            "text, observation payloads, call id lists, observation id lists, and database "
            "primary-key values are not copied into this compact summary."
        ),
    )
    if not summary:
        return {}
    validation = (
        source.get("validation")
        if isinstance(source.get("validation"), dict)
        else {}
    )
    checks = validation.get("checks") if isinstance(validation.get("checks"), dict) else {}
    expected_stream_counts = (
        validation.get("expected_stream_event_counts")
        if isinstance(validation.get("expected_stream_event_counts"), dict)
        else {}
    )
    stream_count_maps = {
        "event_type": normalized_event_count_map(expected_stream_counts.get("event_type")),
        "event_name": normalized_event_count_map(expected_stream_counts.get("event_name")),
        "raw_event_type": normalized_event_count_map(
            expected_stream_counts.get("raw_event_type")
        ),
        "item_class": normalized_event_count_map(expected_stream_counts.get("item_class")),
        "tool_name": normalized_event_count_map(expected_stream_counts.get("tool_name")),
    }
    stream_count_map_totals = {
        key: sum(value.values()) for key, value in stream_count_maps.items() if value
    }
    observation_flags = summary.get("expected_observation_recorded_by_tool") or {}
    evidence_observation_flags = (
        summary.get("evidence_expected_observation_recorded_by_tool") or {}
    )
    result_observation_counts = summary.get("result_observation_tool_counts") or {}
    evidence_observation_counts = summary.get("evidence_observation_tool_counts") or {}
    result_stream_event_count = int_value(result_observation_counts.get("agent_stream_event"))
    evidence_stream_event_count = int_value(evidence_observation_counts.get("agent_stream_event"))
    sdk_stream_summary = compact_stream_read_model_summary(source.get("sdk_events_summary"))
    transcript_stream_summary = compact_stream_read_model_summary(
        source.get("client_transcript_summary")
    )
    event_name_counts = stream_count_maps["event_name"]
    tool_name_counts = stream_count_maps["tool_name"]
    handoff_completed_count = (
        int_value(event_name_counts.get("handoff_occured"))
        + int_value(event_name_counts.get("handoff_occurred"))
    )
    expected_tool_name_count = (
        int_value(tool_name_counts.get(expected_tool_name)) if expected_tool_name else None
    )
    summary.update(
        {
            "expected_stream_event_observation_recorded": (
                observation_flags.get("agent_stream_event") is True
            ),
            "evidence_expected_stream_event_observation_recorded": (
                evidence_observation_flags.get("agent_stream_event") is True
            ),
            "result_stream_event_count": result_stream_event_count,
            "evidence_stream_event_count": evidence_stream_event_count,
            "expected_stream_event_count_maps": stream_count_maps,
            "expected_stream_event_count_map_totals": stream_count_map_totals,
            "expected_stream_event_count_maps_match_result_count": (
                all(total == result_stream_event_count for total in stream_count_map_totals.values())
                if stream_count_map_totals
                else None
            ),
            "expected_tool_name": expected_tool_name,
            "expected_tool_name_recorded": (
                expected_tool_name_count is not None and expected_tool_name_count > 0
            )
            if expected_tool_name
            else None,
            "expected_tool_name_count": expected_tool_name_count,
            "handoff_expected": handoff_expected,
            "expected_handoff_requested_event_recorded": (
                int_value(event_name_counts.get("handoff_requested")) > 0
                if handoff_expected
                else None
            ),
            "expected_handoff_completed_event_recorded": (
                handoff_completed_count > 0 if handoff_expected else None
            ),
            "sdk_stream_event_count": int_value(
                sdk_stream_summary.get("agent_stream_event_count")
            ),
            "transcript_stream_event_count": int_value(
                transcript_stream_summary.get("agent_stream_event_count")
            ),
            "sdk_stream_event_matches_evidence": checks.get(
                "sdk_agent_stream_event_matches_evidence"
            ),
            "transcript_stream_event_matches_evidence": checks.get(
                "transcript_agent_stream_event_matches_evidence"
            ),
            "sdk_stream_count_maps_match_evidence": all(
                checks.get(name) is True
                for name in (
                    "sdk_agent_stream_event_type_counts_match_evidence",
                    "sdk_agent_stream_event_name_counts_match_evidence",
                    "sdk_agent_stream_raw_event_type_counts_match_evidence",
                    "sdk_agent_stream_item_class_counts_match_evidence",
                    "sdk_agent_stream_tool_name_counts_match_evidence",
                )
            ),
            "transcript_stream_count_maps_match_evidence": all(
                checks.get(name) is True
                for name in (
                    "transcript_agent_stream_event_type_counts_match_evidence",
                    "transcript_agent_stream_event_name_counts_match_evidence",
                    "transcript_agent_stream_raw_event_type_counts_match_evidence",
                    "transcript_agent_stream_item_class_counts_match_evidence",
                    "transcript_agent_stream_tool_name_counts_match_evidence",
                )
            ),
        }
    )
    return summary


def scenario_tool_evidence_summary_maps(
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict) or str(item.get("scenario") or "") != "tool":
            continue
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        tool_summary = compact_tool_evidence_summary(publish_readback)
        if tool_summary:
            summaries["tool"] = tool_summary
    return summaries


def scenario_approval_evidence_summary_maps(
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        scenario = str(item.get("scenario") or "unknown")
        if scenario != "approval":
            continue
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        approval_summary = compact_approval_evidence_summary(publish_readback)
        if approval_summary:
            summaries[scenario] = approval_summary
    return summaries


def scenario_handoff_evidence_summary_maps(
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        scenario = str(item.get("scenario") or "unknown")
        if scenario not in {"handoff", "stream_handoff"}:
            continue
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        handoff_summary = compact_handoff_evidence_summary(publish_readback)
        if handoff_summary:
            summaries[scenario] = handoff_summary
    return summaries


def scenario_guardrail_evidence_summary_maps(
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        scenario = str(item.get("scenario") or "unknown")
        if scenario not in {"guardrail", "guardrail_tripwire"}:
            continue
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        guardrail_summary = compact_guardrail_evidence_summary(
            publish_readback,
            tripwire_expected=scenario == "guardrail_tripwire",
        )
        if guardrail_summary:
            summaries[scenario] = guardrail_summary
    return summaries


def scenario_session_evidence_summary_maps(
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        scenario = str(item.get("scenario") or "unknown")
        if scenario != "session":
            continue
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        session_summary = compact_session_evidence_summary(publish_readback)
        if session_summary:
            summaries[scenario] = session_summary
    return summaries


def scenario_stream_evidence_summary_maps(
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        scenario = str(item.get("scenario") or "unknown")
        if scenario not in {"stream", "stream_handoff"}:
            continue
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        stream_summary = compact_stream_evidence_summary(
            publish_readback,
            expected_tool_name=(
                "transfer_to_harness_specialist"
                if scenario == "stream_handoff"
                else "record_signal"
            ),
            handoff_expected=scenario == "stream_handoff",
        )
        if stream_summary:
            summaries[scenario] = stream_summary
    return summaries


def scenario_tool_state_summary_maps(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict) or str(item.get("scenario") or "") != "tool":
            continue
        summary = (
            item.get("tool_state_summary")
            if isinstance(item.get("tool_state_summary"), dict)
            else {}
        )
        if summary:
            summaries["tool"] = summary
    return summaries


def scenario_tool_projection_summary_maps(
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict) or str(item.get("scenario") or "") != "tool":
            continue
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        tool_summary = compact_tool_projection_summary(publish_readback)
        if tool_summary:
            summaries["tool"] = tool_summary
    return summaries


def compact_approval_item(value: Any, *, include_decision: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    raw_item = value.get("raw_item") if isinstance(value.get("raw_item"), dict) else {}
    agent = value.get("agent")
    if isinstance(agent, dict):
        agent_name = agent.get("name") or ""
    else:
        agent_name = str(agent or "")
    arguments = parse_arguments_map(value.get("arguments") or raw_item.get("arguments"))
    compact = {
        "call_id": value.get("call_id") or raw_item.get("call_id") or "",
        "tool_name": value.get("tool_name") or value.get("name") or raw_item.get("name") or "",
        "item_type": value.get("item_type") or value.get("type") or "",
        "item_class": value.get("class") or "",
        "raw_type": value.get("raw_type") or raw_item.get("type") or "",
        "agent_name": agent_name,
        "argument_keys": sorted(str(key) for key in arguments),
        "marker_argument_present": "marker" in arguments,
    }
    if include_decision:
        compact["decision"] = value.get("decision") or ""
    return {key: item for key, item in compact.items() if item not in ("", [], {})}


def compact_approval_phase(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    new_items = [item for item in list_value(value.get("new_items")) if isinstance(item, dict)]
    item_types = [
        str(item.get("type") or "unknown")
        for item in new_items
        if item.get("type") is not None
    ]
    return {
        "name": value.get("name") or "",
        "new_item_count": len(new_items),
        "new_item_type_counts": count_values(item_types),
        "tool_call_count": sum(1 for item in new_items if str(item.get("type") or "") == "tool_call_item"),
        "approval_request_item_count": sum(
            1 for item in new_items if str(item.get("type") or "") == "tool_approval_item"
        ),
        "tool_output_count": sum(
            1 for item in new_items if str(item.get("type") or "") == "tool_call_output_item"
        ),
        "raw_response_count": int_value(value.get("raw_response_count")),
        "interruption_count": int_value(value.get("interruption_count")),
        "final_output_present": value.get("final_output") is not None,
    }


def compact_live_approval_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    phases = [compact_approval_phase(item) for item in list_value(payload.get("phases"))]
    phases = [item for item in phases if item]
    requests = [
        compact_approval_item(item)
        for item in list_value(payload.get("interruptions"))
    ]
    requests = [item for item in requests if item]
    decisions = [
        compact_approval_item(item, include_decision=True)
        for item in list_value(payload.get("approval_decisions"))
    ]
    decisions = [item for item in decisions if item]
    phase_names = [
        str(value)
        for value in list_value(summary.get("phase_names"))
        if str(value or "").strip()
    ] or [str(item.get("name") or "") for item in phases if str(item.get("name") or "").strip()]
    phase_interruption_counts = normalized_event_count_map(summary.get("phase_interruption_counts"))
    if not phase_interruption_counts:
        phase_interruption_counts = {
            str(item.get("name") or ""): int_value(item.get("interruption_count"))
            for item in phases
            if str(item.get("name") or "").strip()
        }
    approval_request_count = int_value(summary.get("approval_request_count")) or len(requests)
    approval_decision_count = int_value(summary.get("approval_decision_count")) or len(decisions)
    approval_decision_counts = normalized_event_count_map(summary.get("approval_decision_counts"))
    if not approval_decision_counts and decisions:
        approval_decision_counts = count_values(
            [str(item.get("decision") or "unknown") for item in decisions]
        )
    has_approval_surface = (
        approval_request_count > 0
        or approval_decision_count > 0
        or "after_approval" in phase_names
        or any(int_value(item.get("approval_request_item_count")) > 0 for item in phases)
    )
    if not has_approval_surface:
        return {}
    initial_interruption_count = int_value(phase_interruption_counts.get("initial"))
    resume_interruption_count = int_value(phase_interruption_counts.get("after_approval"))
    approved_count = int_value(approval_decision_counts.get("approved"))
    return {
        "status": payload.get("status") or "",
        "approval_request_count": approval_request_count,
        "approval_request_type_counts": normalized_event_count_map(
            summary.get("approval_request_type_counts")
        ),
        "approval_decision_count": approval_decision_count,
        "approval_decision_counts": approval_decision_counts,
        "approved_count": approved_count,
        "phase_count": int_value(summary.get("phase_count")) or len(phases),
        "phase_names": phase_names,
        "phase_new_item_counts": normalized_event_count_map(summary.get("phase_new_item_counts")),
        "phase_raw_response_counts": normalized_event_count_map(
            summary.get("phase_raw_response_counts")
        ),
        "phase_interruption_counts": phase_interruption_counts,
        "initial_phase_recorded": "initial" in phase_names,
        "resume_phase_recorded": "after_approval" in phase_names,
        "initial_interruption_count": initial_interruption_count,
        "resume_interruption_count": resume_interruption_count,
        "approval_interruption_observed": initial_interruption_count > 0,
        "approved_resume_observed": approved_count > 0 and "after_approval" in phase_names,
        "tool_call_count": int_value(summary.get("tool_call_count")),
        "tool_output_count": int_value(summary.get("tool_output_count")),
        "final_ok": summary.get("final_ok") is True,
        "marker_observed": summary.get("marker_observed") is True,
        "request_summaries": requests,
        "decision_summaries": decisions,
        "phase_summaries": phases,
        "claim_boundary": (
            "Sanitized live approval/interruption state from the SDK smoke result. Raw approval "
            "items, prompts, and tool outputs are not copied into this compact summary."
        ),
    }


def compact_approval_read_model_summary(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    counts = projection_count_map(summary)
    item_type_counts = normalized_event_count_map(summary.get("item_type_counts"))
    approval_count = int_value(counts.get("agent_approval_count"))
    approval_item_count = int_value(item_type_counts.get("agent_approval"))
    phase_count = int_value(counts.get("agent_phase_count"))
    if approval_count <= 0 and approval_item_count <= 0:
        return {}
    compact = {
        "summary_requested": summary.get("summary_requested"),
        "task_id": summary.get("task_id") or "",
        "status": summary.get("status") or "",
        "agent_approval_count": approval_count,
        "agent_phase_count": phase_count,
        "tool_progress_count": int_value(counts.get("tool_progress_count")),
        "final_output_count": int_value(counts.get("final_output_count")),
        "agent_approval_item_count": approval_item_count,
        "agent_phase_item_count": int_value(item_type_counts.get("agent_phase")),
    }
    return {key: value for key, value in compact.items() if value not in ("", None)}


def compact_approval_projection_summary(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    sdk_events = compact_approval_read_model_summary(source.get("sdk_events_summary"))
    client_transcript = compact_approval_read_model_summary(
        source.get("client_transcript_summary")
    )
    if not sdk_events and not client_transcript:
        return {}
    sdk_approval_count = int_value(sdk_events.get("agent_approval_count"))
    transcript_approval_count = int_value(client_transcript.get("agent_approval_count"))
    sdk_phase_count = int_value(sdk_events.get("agent_phase_count"))
    transcript_phase_count = int_value(client_transcript.get("agent_phase_count"))
    return {
        "sdk_events": sdk_events,
        "client_transcript": client_transcript,
        "approval_projected": sdk_approval_count > 0 or transcript_approval_count > 0,
        "approval_phase_projected": sdk_phase_count > 0 or transcript_phase_count > 0,
        "approval_counts_match": (
            sdk_approval_count == transcript_approval_count
            if sdk_events and client_transcript
            else None
        ),
        "phase_counts_match": (
            sdk_phase_count == transcript_phase_count
            if sdk_events and client_transcript
            else None
        ),
        "tool_progress_delta": int_value(sdk_events.get("tool_progress_count"))
        - int_value(client_transcript.get("tool_progress_count")),
        "final_output_projected": (
            int_value(sdk_events.get("final_output_count")) > 0
            or int_value(client_transcript.get("final_output_count")) > 0
        ),
    }


def scenario_approval_state_summary_maps(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        summary = (
            item.get("approval_state_summary")
            if isinstance(item.get("approval_state_summary"), dict)
            else {}
        )
        if summary:
            summaries[str(item.get("scenario") or "unknown")] = summary
    return summaries


def scenario_approval_projection_summary_maps(
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in results:
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        approval_summary = compact_approval_projection_summary(publish_readback)
        if approval_summary:
            summaries[str(item.get("scenario") or "unknown")] = approval_summary
    return summaries


def agent_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return str(value or "")


def compact_handoff_item(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    raw_item = value.get("raw_item") if isinstance(value.get("raw_item"), dict) else {}
    arguments = parse_arguments_map(value.get("arguments") or raw_item.get("arguments"))
    output = parse_arguments_map(value.get("output") or raw_item.get("output"))
    compact = {
        "call_id": value.get("call_id") or raw_item.get("call_id") or "",
        "tool_name": value.get("tool_name") or raw_item.get("name") or "",
        "item_type": value.get("type") or "",
        "item_class": value.get("class") or "",
        "raw_type": raw_item.get("type") or "",
        "agent_name": agent_name(value.get("agent")),
        "source_agent": agent_name(value.get("source_agent")),
        "target_agent": agent_name(value.get("target_agent")),
        "argument_keys": sorted(str(key) for key in arguments),
        "output_keys": sorted(str(key) for key in output),
        "assistant_output_present": "assistant" in output,
    }
    return {key: item for key, item in compact.items() if item not in ("", [], {})}


def compact_handoff_phase(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    new_items = [item for item in list_value(value.get("new_items")) if isinstance(item, dict)]
    item_types = [
        str(item.get("type") or "unknown")
        for item in new_items
        if item.get("type") is not None
    ]
    return {
        "name": value.get("name") or "",
        "new_item_count": len(new_items),
        "new_item_type_counts": count_values(item_types),
        "handoff_call_count": sum(
            1 for item in new_items if str(item.get("type") or "") == "handoff_call_item"
        ),
        "handoff_output_count": sum(
            1 for item in new_items if str(item.get("type") or "") == "handoff_output_item"
        ),
        "message_output_count": sum(
            1 for item in new_items if str(item.get("type") or "") == "message_output_item"
        ),
        "raw_response_count": int_value(value.get("raw_response_count")),
        "interruption_count": int_value(value.get("interruption_count")),
        "final_output_present": value.get("final_output") is not None,
    }


def handoff_pair_summaries(
    calls: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_call_id: dict[str, dict[str, Any]] = {}
    for item in calls:
        call_id = str(item.get("call_id") or "")
        by_call_id.setdefault(call_id, {})["call"] = item
    for item in outputs:
        call_id = str(item.get("call_id") or "")
        by_call_id.setdefault(call_id, {})["output"] = item
    pairs: list[dict[str, Any]] = []
    for call_id in sorted(key for key in by_call_id if key):
        pair = by_call_id[call_id]
        call = pair.get("call") if isinstance(pair.get("call"), dict) else {}
        output = pair.get("output") if isinstance(pair.get("output"), dict) else {}
        pairs.append(
            {
                "call_id": call_id,
                "tool_name": call.get("tool_name") or output.get("tool_name") or "",
                "source_agent": output.get("source_agent") or call.get("agent_name") or "",
                "target_agent": output.get("target_agent") or "",
                "call_recorded": bool(call),
                "output_recorded": bool(output),
                "assistant_output_present": output.get("assistant_output_present") is True,
            }
        )
    return pairs


def compact_live_handoff_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    new_items = [item for item in list_value(payload.get("new_items")) if isinstance(item, dict)]
    handoff_calls = [
        compact_handoff_item(item)
        for item in new_items
        if str(item.get("type") or "") == "handoff_call_item"
    ]
    handoff_calls = [item for item in handoff_calls if item]
    handoff_outputs = [
        compact_handoff_item(item)
        for item in new_items
        if str(item.get("type") or "") == "handoff_output_item"
    ]
    handoff_outputs = [item for item in handoff_outputs if item]
    handoff_call_count = int_value(summary.get("handoff_call_count")) or len(handoff_calls)
    handoff_output_count = int_value(summary.get("handoff_output_count")) or len(handoff_outputs)
    if handoff_call_count <= 0 and handoff_output_count <= 0:
        return {}
    phases = [compact_handoff_phase(item) for item in list_value(payload.get("phases"))]
    phases = [item for item in phases if item]
    phase_names = [
        str(value)
        for value in list_value(summary.get("phase_names"))
        if str(value or "").strip()
    ] or [str(item.get("name") or "") for item in phases if str(item.get("name") or "").strip()]
    source_agents = unique_strings(
        [item.get("source_agent") for item in handoff_outputs]
        + [item.get("agent_name") for item in handoff_calls]
    )
    target_agents = unique_strings([item.get("target_agent") for item in handoff_outputs])
    tool_names = unique_strings(
        [item.get("tool_name") for item in handoff_calls]
        + [item.get("tool_name") for item in handoff_outputs]
    )
    pairs = handoff_pair_summaries(handoff_calls, handoff_outputs)
    return {
        "status": payload.get("status") or "",
        "handoff_call_count": handoff_call_count,
        "handoff_output_count": handoff_output_count,
        "handoff_pair_count": len(pairs),
        "handoff_completed_observed": handoff_call_count > 0 and handoff_output_count > 0,
        "source_agents": source_agents,
        "target_agents": target_agents,
        "tool_names": tool_names,
        "expected_tool_observed": "transfer_to_harness_specialist" in tool_names,
        "expected_source_observed": "Harness Router Agent" in source_agents,
        "expected_target_observed": "Harness Specialist Agent" in target_agents,
        "phase_count": int_value(summary.get("phase_count")) or len(phases),
        "phase_names": phase_names,
        "phase_new_item_counts": normalized_event_count_map(summary.get("phase_new_item_counts")),
        "phase_raw_response_counts": normalized_event_count_map(
            summary.get("phase_raw_response_counts")
        ),
        "phase_interruption_counts": normalized_event_count_map(
            summary.get("phase_interruption_counts")
        ),
        "new_item_type_counts": normalized_event_count_map(summary.get("new_item_type_counts")),
        "final_ok": summary.get("final_ok") is True,
        "marker_observed": summary.get("marker_observed") is True,
        "call_summaries": handoff_calls,
        "output_summaries": handoff_outputs,
        "handoff_pairs": pairs,
        "phase_summaries": phases,
        "claim_boundary": (
            "Sanitized live handoff state from the SDK smoke result. Raw prompts, final text, "
            "and handoff output payload values are not copied into this compact summary."
        ),
    }


def compact_handoff_read_model_summary(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    counts = projection_count_map(summary)
    handoff_count = int_value(counts.get("agent_handoff_count"))
    item_type_counts = normalized_event_count_map(summary.get("item_type_counts"))
    handoff_item_count = int_value(item_type_counts.get("agent_handoff"))
    if handoff_count <= 0 and handoff_item_count <= 0:
        return {}
    compact = {
        "summary_requested": summary.get("summary_requested"),
        "task_id": summary.get("task_id") or "",
        "status": summary.get("status") or "",
        "agent_handoff_count": handoff_count,
        "agent_phase_count": int_value(counts.get("agent_phase_count")),
        "final_output_count": int_value(counts.get("final_output_count")),
        "agent_handoff_item_count": handoff_item_count,
        "agent_phase_item_count": int_value(item_type_counts.get("agent_phase")),
    }
    return {key: value for key, value in compact.items() if value not in ("", None)}


def compact_handoff_projection_summary(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    sdk_events = compact_handoff_read_model_summary(source.get("sdk_events_summary"))
    client_transcript = compact_handoff_read_model_summary(
        source.get("client_transcript_summary")
    )
    if not sdk_events and not client_transcript:
        return {}
    sdk_handoff_count = int_value(sdk_events.get("agent_handoff_count"))
    transcript_handoff_count = int_value(client_transcript.get("agent_handoff_count"))
    return {
        "sdk_events": sdk_events,
        "client_transcript": client_transcript,
        "handoff_projected": sdk_handoff_count > 0 or transcript_handoff_count > 0,
        "handoff_counts_match": (
            sdk_handoff_count == transcript_handoff_count
            if sdk_events and client_transcript
            else None
        ),
        "phase_counts_match": (
            int_value(sdk_events.get("agent_phase_count"))
            == int_value(client_transcript.get("agent_phase_count"))
            if sdk_events and client_transcript
            else None
        ),
        "final_output_projected": (
            int_value(sdk_events.get("final_output_count")) > 0
            or int_value(client_transcript.get("final_output_count")) > 0
        ),
    }


def scenario_handoff_state_summary_maps(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        summary = (
            item.get("handoff_state_summary")
            if isinstance(item.get("handoff_state_summary"), dict)
            else {}
        )
        if summary:
            summaries[str(item.get("scenario") or "unknown")] = summary
    return summaries


def scenario_handoff_projection_summary_maps(
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in results:
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        handoff_summary = compact_handoff_projection_summary(publish_readback)
        if handoff_summary:
            summaries[str(item.get("scenario") or "unknown")] = handoff_summary
    return summaries


def scenario_guardrail_tripwire_summary_maps(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    guardrail_surfaces = (
        "input_guardrail_results",
        "output_guardrail_results",
        "tool_input_guardrail_results",
        "tool_output_guardrail_results",
    )
    for item in results:
        if not isinstance(item, dict):
            continue
        scenario = str(item.get("scenario") or "unknown")
        smoke_summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        guardrail_results: list[dict[str, Any]] = []
        guardrail_names: list[str] = []
        tripwire_names: list[str] = []
        tripwire_reasons: list[str] = []
        for surface in guardrail_surfaces:
            for raw_result in list_value(item.get(surface)):
                compact = compact_guardrail_result(surface, raw_result)
                if not compact:
                    continue
                guardrail_results.append(compact)
                guardrail_names.append(str(compact.get("guardrail_name") or ""))
                if compact.get("tripwire_triggered") is True:
                    tripwire_names.append(str(compact.get("guardrail_name") or ""))
                    tripwire_reasons.append(str(compact.get("reason") or ""))
        validation = item.get("validation") if isinstance(item.get("validation"), dict) else {}
        checks = validation.get("checks") if isinstance(validation.get("checks"), dict) else {}
        guardrail_checks = {
            key: checks.get(key) is True
            for key in sorted(checks)
            if str(key).startswith("guardrail_")
        }
        has_guardrail_summary = any(
            int_value(smoke_summary.get(key)) > 0
            for key in (
                "input_guardrail_count",
                "output_guardrail_count",
                "tool_input_guardrail_count",
                "tool_output_guardrail_count",
                "guardrail_tripwire_count",
            )
        )
        has_error = bool(item.get("error_type") or item.get("error"))
        if not guardrail_results and not has_guardrail_summary and not guardrail_checks and not has_error:
            continue
        summaries[scenario] = {
            "status": item.get("status") or "",
            "error_type": item.get("error_type") or "",
            "error_preview": short_text(item.get("error")),
            "final_ok": smoke_summary.get("final_ok") is True,
            "marker_observed": smoke_summary.get("marker_observed") is True,
            "input_guardrail_count": int_value(smoke_summary.get("input_guardrail_count")),
            "output_guardrail_count": int_value(smoke_summary.get("output_guardrail_count")),
            "tool_input_guardrail_count": int_value(
                smoke_summary.get("tool_input_guardrail_count")
            ),
            "tool_output_guardrail_count": int_value(
                smoke_summary.get("tool_output_guardrail_count")
            ),
            "guardrail_tripwire_count": int_value(
                smoke_summary.get("guardrail_tripwire_count")
            ),
            "guardrail_tripwire_triggered": (
                smoke_summary.get("guardrail_tripwire_triggered") is True
                or int_value(smoke_summary.get("guardrail_tripwire_count")) > 0
            ),
            "guardrail_names": unique_strings(guardrail_names),
            "tripwire_guardrail_names": unique_strings(tripwire_names),
            "tripwire_reasons": unique_strings(tripwire_reasons),
            "guardrail_results": guardrail_results,
            "validation_checks": guardrail_checks,
        }
    return summaries


def scenario_session_state_summary_maps(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        session_summary = (
            item.get("session_state_summary")
            if isinstance(item.get("session_state_summary"), dict)
            else {}
        )
        if session_summary:
            summaries[str(item.get("scenario") or "unknown")] = session_summary
    return summaries


def scenario_publish_readback_compact_counts(results: list[Any]) -> dict[str, Any]:
    status_by_scenario: dict[str, Any] = {}
    generic_check_counts: dict[str, int] = {}
    projection_check_counts: dict[str, int] = {}
    event_replay_check_counts: dict[str, int] = {}
    total_check_counts: dict[str, int] = {}
    external_api_called_by_scenario: dict[str, bool] = {}
    event_replay_fetch_plans: dict[str, dict[str, Any]] = {}
    event_replay_fetch_plan_checks: dict[str, dict[str, Any]] = {}
    event_replay_sse_event_name_counts: dict[str, dict[str, int]] = {}
    event_replay_event_type_counts: dict[str, dict[str, int]] = {}
    event_replay_event_status_counts: dict[str, dict[str, int]] = {}
    event_replay_event_actor_counts: dict[str, dict[str, int]] = {}
    event_replay_event_counts: dict[str, int] = {}
    event_replay_expected_event_counts: dict[str, dict[str, int]] = {}
    event_replay_observed_event_counts: dict[str, dict[str, int]] = {}
    event_replay_count_map_totals: dict[str, dict[str, int]] = {}
    event_replay_count_total_checks: dict[str, dict[str, bool]] = {}
    generic_failed_checks: dict[str, list[Any]] = {}
    projection_failed_checks: dict[str, list[Any]] = {}
    event_replay_failed_checks: dict[str, list[Any]] = {}
    failed_checks: dict[str, list[Any]] = {}
    valid_by_scenario: dict[str, bool] = {}
    task_bindings: dict[str, dict[str, Any]] = {}
    trace_artifact_digest_matches: dict[str, bool] = {}
    trace_artifact_bindings: dict[str, dict[str, Any]] = {}
    artifact_summaries: dict[str, list[Any]] = {}
    session_summaries: dict[str, list[Any]] = {}
    tool_evidence_summaries: dict[str, dict[str, Any]] = {}
    approval_evidence_summaries: dict[str, dict[str, Any]] = {}
    handoff_evidence_summaries: dict[str, dict[str, Any]] = {}
    guardrail_evidence_summaries: dict[str, dict[str, Any]] = {}
    session_evidence_summaries: dict[str, dict[str, Any]] = {}
    stream_evidence_summaries: dict[str, dict[str, Any]] = {}
    tool_projection_summaries: dict[str, dict[str, Any]] = {}
    stream_projection_summaries: dict[str, dict[str, Any]] = {}
    handoff_projection_summaries: dict[str, dict[str, Any]] = {}
    guardrail_projection_summaries: dict[str, dict[str, Any]] = {}
    approval_projection_summaries: dict[str, dict[str, Any]] = {}
    sdk_events_projection_maps: dict[str, dict[str, dict[str, int]]] = {}
    client_transcript_projection_maps: dict[str, dict[str, dict[str, int]]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        scenario = str(item.get("scenario") or "unknown")
        publish_readback = item.get("publish_readback")
        if not isinstance(publish_readback, dict):
            continue
        status_by_scenario[scenario] = publish_readback.get("status")
        validation = (
            publish_readback.get("validation")
            if isinstance(publish_readback.get("validation"), dict)
            else {}
        )
        projection = (
            publish_readback.get("scenario_projection_validation")
            if isinstance(publish_readback.get("scenario_projection_validation"), dict)
            else {}
        )
        event_replay = (
            publish_readback.get("scenario_event_replay_validation")
            if isinstance(publish_readback.get("scenario_event_replay_validation"), dict)
            else {}
        )
        valid_by_scenario[scenario] = (
            publish_readback.get("status") == "completed"
            and compact_validation_passed(validation)
            and compact_validation_passed(projection)
            and compact_validation_passed(event_replay)
        )
        generic_check_counts[scenario] = int_value(validation.get("check_count"))
        projection_check_counts[scenario] = int_value(projection.get("check_count"))
        event_replay_check_counts[scenario] = int_value(event_replay.get("check_count"))
        total_check_counts[scenario] = (
            generic_check_counts[scenario]
            + projection_check_counts[scenario]
            + event_replay_check_counts[scenario]
        )
        event_replay_fetch_plan = event_replay_fetch_plan_compact_validation(event_replay)
        if event_replay_fetch_plan["fetch_plan"]:
            event_replay_fetch_plans[scenario] = event_replay_fetch_plan["fetch_plan"]
        if event_replay_fetch_plan["checks"]:
            event_replay_fetch_plan_checks[scenario] = event_replay_fetch_plan["checks"]
        event_replay_summary = (
            event_replay.get("event_replay_summary")
            if isinstance(event_replay.get("event_replay_summary"), dict)
            else {}
        )
        event_replay_event_counts[scenario] = int_value(event_replay_summary.get("event_count"))
        expected_event_counts = normalized_event_count_map(event_replay.get("expected_event_counts"))
        observed_event_counts = normalized_event_count_map(event_replay.get("observed_event_counts"))
        if expected_event_counts:
            event_replay_expected_event_counts[scenario] = expected_event_counts
        if observed_event_counts:
            event_replay_observed_event_counts[scenario] = observed_event_counts
        count_map_totals = normalized_event_count_map(event_replay.get("event_replay_count_map_totals"))
        if count_map_totals:
            event_replay_count_map_totals[scenario] = count_map_totals
        count_total_checks = event_replay_count_total_check_map(event_replay)
        if count_total_checks:
            event_replay_count_total_checks[scenario] = count_total_checks
        sse_event_counts = normalized_event_count_map(
            event_replay_summary.get("sse_event_name_counts")
        )
        event_type_counts = normalized_event_count_map(
            event_replay_summary.get("event_type_counts")
        )
        event_status_counts = normalized_event_count_map(
            event_replay_summary.get("event_status_counts")
        )
        event_actor_counts = normalized_event_count_map(
            event_replay_summary.get("event_actor_counts")
        )
        if sse_event_counts:
            event_replay_sse_event_name_counts[scenario] = sse_event_counts
        if event_type_counts:
            event_replay_event_type_counts[scenario] = event_type_counts
        if event_status_counts:
            event_replay_event_status_counts[scenario] = event_status_counts
        if event_actor_counts:
            event_replay_event_actor_counts[scenario] = event_actor_counts
        generic_failures = list_value(validation.get("failed_checks"))
        projection_failures = list_value(projection.get("failed_checks"))
        event_replay_failures = list_value(event_replay.get("failed_checks"))
        if generic_failures:
            generic_failed_checks[scenario] = generic_failures
        if projection_failures:
            projection_failed_checks[scenario] = projection_failures
        if event_replay_failures:
            event_replay_failed_checks[scenario] = event_replay_failures
        combined_failures = []
        combined_failures.extend(f"generic:{item}" for item in generic_failures)
        combined_failures.extend(f"projection:{item}" for item in projection_failures)
        combined_failures.extend(f"event_replay:{item}" for item in event_replay_failures)
        if combined_failures:
            failed_checks[scenario] = combined_failures
        task_binding = validation.get("task_binding")
        if isinstance(task_binding, dict):
            task_bindings[scenario] = task_binding
        trace_artifact_binding = validation.get("trace_artifact_binding")
        if isinstance(trace_artifact_binding, dict):
            trace_artifact_bindings[scenario] = trace_artifact_binding
        checks = validation.get("checks")
        if isinstance(checks, dict) and "agent_trace_artifact_digest_matches_expected" in checks:
            trace_artifact_digest_matches[scenario] = (
                checks.get("agent_trace_artifact_digest_matches_expected") is True
            )
        artifacts = list_value(publish_readback.get("artifact_summaries"))
        if artifacts:
            artifact_summaries[scenario] = artifacts
        external_api_called = artifact_summaries_external_api_called(artifacts)
        if external_api_called is not None:
            external_api_called_by_scenario[scenario] = external_api_called
        sessions = compact_agent_session_summaries(publish_readback.get("agent_session_summaries"))
        if sessions:
            session_summaries[scenario] = sessions
        if scenario == "tool":
            tool_evidence_summary = compact_tool_evidence_summary(publish_readback)
            if tool_evidence_summary:
                tool_evidence_summaries[scenario] = tool_evidence_summary
            tool_summary = compact_tool_projection_summary(publish_readback)
            if tool_summary:
                tool_projection_summaries[scenario] = tool_summary
        if scenario == "approval":
            approval_evidence_summary = compact_approval_evidence_summary(publish_readback)
            if approval_evidence_summary:
                approval_evidence_summaries[scenario] = approval_evidence_summary
        if scenario in {"handoff", "stream_handoff"}:
            handoff_evidence_summary = compact_handoff_evidence_summary(publish_readback)
            if handoff_evidence_summary:
                handoff_evidence_summaries[scenario] = handoff_evidence_summary
        if scenario in {"guardrail", "guardrail_tripwire"}:
            guardrail_evidence_summary = compact_guardrail_evidence_summary(
                publish_readback,
                tripwire_expected=scenario == "guardrail_tripwire",
            )
            if guardrail_evidence_summary:
                guardrail_evidence_summaries[scenario] = guardrail_evidence_summary
        if scenario == "session":
            session_evidence_summary = compact_session_evidence_summary(publish_readback)
            if session_evidence_summary:
                session_evidence_summaries[scenario] = session_evidence_summary
        if scenario in {"stream", "stream_handoff"}:
            stream_evidence_summary = compact_stream_evidence_summary(
                publish_readback,
                expected_tool_name=(
                    "transfer_to_harness_specialist"
                    if scenario == "stream_handoff"
                    else "record_signal"
                ),
                handoff_expected=scenario == "stream_handoff",
            )
            if stream_evidence_summary:
                stream_evidence_summaries[scenario] = stream_evidence_summary
        stream_summary = compact_stream_projection_summary(publish_readback)
        if stream_summary:
            stream_projection_summaries[scenario] = stream_summary
        handoff_projection_summary = compact_handoff_projection_summary(publish_readback)
        if handoff_projection_summary:
            handoff_projection_summaries[scenario] = handoff_projection_summary
        guardrail_projection_summary = compact_guardrail_projection_summary(publish_readback)
        if guardrail_projection_summary:
            guardrail_projection_summaries[scenario] = guardrail_projection_summary
        approval_projection_summary = compact_approval_projection_summary(publish_readback)
        if approval_projection_summary:
            approval_projection_summaries[scenario] = approval_projection_summary
        sdk_projection_map = projection_map_summary(publish_readback.get("sdk_events_summary"))
        if sdk_projection_map:
            sdk_events_projection_maps[scenario] = sdk_projection_map
        transcript_projection_map = projection_map_summary(
            publish_readback.get("client_transcript_summary")
        )
        if transcript_projection_map:
            client_transcript_projection_maps[scenario] = transcript_projection_map
    return {
        "published_scenario_count": len(status_by_scenario),
        "valid": all(valid_by_scenario.values()) if status_by_scenario else None,
        "valid_by_scenario": valid_by_scenario,
        "failed_scenarios": [
            scenario for scenario, valid in sorted(valid_by_scenario.items()) if valid is not True
        ],
        "status_by_scenario": status_by_scenario,
        "generic_check_counts": generic_check_counts,
        "projection_check_counts": projection_check_counts,
        "event_replay_check_counts": event_replay_check_counts,
        "total_check_counts": total_check_counts,
        "external_api_called_by_scenario": external_api_called_by_scenario,
        "event_replay_fetch_plans": event_replay_fetch_plans,
        "event_replay_fetch_plan_checks": event_replay_fetch_plan_checks,
        "event_replay_sse_event_name_counts": event_replay_sse_event_name_counts,
        "event_replay_event_type_counts": event_replay_event_type_counts,
        "event_replay_event_status_counts": event_replay_event_status_counts,
        "event_replay_event_actor_counts": event_replay_event_actor_counts,
        "event_replay_event_counts": event_replay_event_counts,
        "event_replay_expected_event_counts": event_replay_expected_event_counts,
        "event_replay_observed_event_counts": event_replay_observed_event_counts,
        "event_replay_expected_event_totals": event_count_totals(
            event_replay_expected_event_counts
        ),
        "event_replay_observed_event_totals": event_count_totals(
            event_replay_observed_event_counts
        ),
        "event_replay_count_map_totals": event_replay_count_map_totals,
        "event_replay_count_map_total_totals": event_count_totals(
            event_replay_count_map_totals
        ),
        "event_replay_count_total_checks": event_replay_count_total_checks,
        "event_replay_sse_event_name_totals": event_count_totals(
            event_replay_sse_event_name_counts
        ),
        "event_replay_event_type_totals": event_count_totals(event_replay_event_type_counts),
        "event_replay_event_status_totals": event_count_totals(event_replay_event_status_counts),
        "event_replay_event_actor_totals": event_count_totals(event_replay_event_actor_counts),
        "generic_failed_checks": generic_failed_checks,
        "projection_failed_checks": projection_failed_checks,
        "event_replay_failed_checks": event_replay_failed_checks,
        "failed_checks": failed_checks,
        "task_bindings": task_bindings,
        "trace_artifact_digest_matches": trace_artifact_digest_matches,
        "trace_artifact_bindings": trace_artifact_bindings,
        "artifact_summaries": artifact_summaries,
        "session_summaries": session_summaries,
        "tool_evidence_summaries": tool_evidence_summaries,
        "approval_evidence_summaries": approval_evidence_summaries,
        "handoff_evidence_summaries": handoff_evidence_summaries,
        "guardrail_evidence_summaries": guardrail_evidence_summaries,
        "session_evidence_summaries": session_evidence_summaries,
        "stream_evidence_summaries": stream_evidence_summaries,
        "tool_projection_summaries": tool_projection_summaries,
        "stream_projection_summaries": stream_projection_summaries,
        "handoff_projection_summaries": handoff_projection_summaries,
        "guardrail_projection_summaries": guardrail_projection_summaries,
        "approval_projection_summaries": approval_projection_summaries,
        "sdk_events_projection_maps": sdk_events_projection_maps,
        "client_transcript_projection_maps": client_transcript_projection_maps,
        "sdk_events_projection_map_totals": projection_map_totals(
            sdk_events_projection_maps.values()
        ),
        "client_transcript_projection_map_totals": projection_map_totals(
            client_transcript_projection_maps.values()
        ),
    }


def compact_validation_passed(validation: Any) -> bool:
    if not isinstance(validation, dict) or not validation:
        return False
    if validation.get("valid") is False:
        return False
    if list_value(validation.get("failed_checks")):
        return False
    if "valid" in validation:
        return validation.get("valid") is True
    return int_value(validation.get("check_count")) > 0


def compact_agent_session_summaries(value: Any) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for item in list_value(value):
        if not isinstance(item, dict):
            continue
        summaries.append(
            {
                "agent_session_id": item.get("agent_session_id") or "",
                "backend": item.get("backend") or "",
                "turn_count": int_value(item.get("turn_count")),
                "turn_count_from_turns": int_value(item.get("turn_count_from_turns")),
                "final_item_count": int_value(item.get("final_item_count")),
                "final_item_types": [
                    str(value or "unknown")
                    for value in list_value(item.get("final_item_types"))
                    if value is not None
                ],
                "final_item_type_counts": normalized_event_count_map(
                    item.get("final_item_type_counts")
                ),
                "turn_phases": [
                    str(value)
                    for value in list_value(item.get("turn_phases"))
                    if str(value or "").strip()
                ],
                "status": item.get("status"),
                "evidence_refs": [
                    str(value)
                    for value in list_value(item.get("evidence_refs"))
                    if str(value or "").strip()
                ],
            }
        )
    return summaries


def compact_live_session_state(session: Any, summary: Any) -> dict[str, Any]:
    if not isinstance(session, dict):
        return {}
    summary = summary if isinstance(summary, dict) else {}
    turns = [item for item in list_value(session.get("turns")) if isinstance(item, dict)]
    turn_phases = [
        str(item.get("phase") or "")
        for item in turns
        if str(item.get("phase") or "").strip()
    ]
    item_count_before_by_phase: dict[str, int] = {}
    item_count_after_by_phase: dict[str, int] = {}
    new_item_count_by_phase: dict[str, int] = {}
    raw_response_count_by_phase: dict[str, int] = {}
    for index, turn in enumerate(turns, start=1):
        phase = str(turn.get("phase") or f"turn_{index}")
        item_count_before_by_phase[phase] = int_value(turn.get("item_count_before"))
        item_count_after_by_phase[phase] = int_value(turn.get("item_count_after"))
        new_item_count_by_phase[phase] = int_value(turn.get("new_item_count"))
        raw_response_count_by_phase[phase] = int_value(turn.get("raw_response_count"))
    final_item_types = [
        str(value or "unknown")
        for value in list_value(session.get("final_item_types"))
        if value is not None
    ]
    final_item_type_counts = count_values(final_item_types)
    summary_item_type_counts = normalized_event_count_map(summary.get("session_item_type_counts"))
    turn_count = int_value(session.get("turn_count")) or len(turns)
    final_item_count = int_value(session.get("final_item_count"))
    summary_turn_count = int_value(summary.get("session_turn_count"))
    summary_item_count = int_value(summary.get("session_item_count"))
    observed_second_turn_before = (
        max((value for key, value in item_count_before_by_phase.items() if key != "session_initial"), default=0)
    )
    first_before = int_value(next(iter(item_count_before_by_phase.values()), 0))
    last_after = int_value(next(reversed(item_count_after_by_phase.values()), 0)) if item_count_after_by_phase else 0
    return {
        "agent_session_id": session.get("session_id") or "",
        "backend": session.get("backend") or "",
        "turn_count": turn_count,
        "summary_turn_count": summary_turn_count,
        "turn_count_matches_summary": (
            summary_turn_count == 0 or turn_count == summary_turn_count
        ),
        "final_item_count": final_item_count,
        "summary_item_count": summary_item_count,
        "final_item_count_matches_summary": (
            summary_item_count == 0 or final_item_count == summary_item_count
        ),
        "final_item_types": final_item_types,
        "final_item_type_counts": final_item_type_counts,
        "summary_item_type_counts": summary_item_type_counts,
        "final_item_type_counts_match_summary": (
            not summary_item_type_counts or final_item_type_counts == summary_item_type_counts
        ),
        "turn_phases": turn_phases,
        "summary_phase_names": [
            str(value)
            for value in list_value(summary.get("phase_names"))
            if str(value or "").strip()
        ],
        "item_count_before_by_phase": item_count_before_by_phase,
        "item_count_after_by_phase": item_count_after_by_phase,
        "new_item_count_by_phase": new_item_count_by_phase,
        "raw_response_count_by_phase": raw_response_count_by_phase,
        "session_growth_observed": last_after > first_before,
        "stateful_second_turn_observed": observed_second_turn_before > 0,
        "db_path_recorded": bool(session.get("db_path")),
        "final_items_preview_count": len(list_value(session.get("final_items_preview"))),
    }


def compact_stream_projection_summary(publish_readback: Any) -> dict[str, Any]:
    if not isinstance(publish_readback, dict):
        return {}
    sdk_events = compact_stream_read_model_summary(publish_readback.get("sdk_events_summary"))
    client_transcript = compact_stream_read_model_summary(
        publish_readback.get("client_transcript_summary")
    )
    if not sdk_events and not client_transcript:
        return {}
    return {
        "sdk_events": sdk_events,
        "client_transcript": client_transcript,
    }


def compact_stream_read_model_summary(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    maps = {
        "agent_stream_event_type_counts": normalized_event_count_map(
            summary.get("agent_stream_event_type_counts")
        ),
        "agent_stream_event_name_counts": normalized_event_count_map(
            summary.get("agent_stream_event_name_counts")
        ),
        "agent_stream_raw_event_type_counts": normalized_event_count_map(
            summary.get("agent_stream_raw_event_type_counts")
        ),
        "agent_stream_item_class_counts": normalized_event_count_map(
            summary.get("agent_stream_item_class_counts")
        ),
        "agent_stream_tool_name_counts": normalized_event_count_map(
            summary.get("agent_stream_tool_name_counts")
        ),
    }
    stream_count = int_value(summary.get("agent_stream_event_count"))
    handoff_count = int_value(summary.get("agent_handoff_count"))
    if not stream_count and not handoff_count and not any(maps.values()):
        return {}
    return {
        "summary_requested": summary.get("summary_requested"),
        "task_id": summary.get("task_id") or "",
        "agent_stream_event_count": stream_count,
        "agent_handoff_count": handoff_count,
        **maps,
    }


def find_revalidation_summary(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    for key in ("readback_validation_summary", "matrix_run_summary"):
        value = payload.get(key)
        if isinstance(value, dict) and isinstance(value.get("revalidation_inputs"), dict):
            return key, value
    if isinstance(payload.get("revalidation_inputs"), dict):
        return "root", payload
    raise SystemExit(
        "Summary JSON does not contain revalidation_inputs at root, "
        "readback_validation_summary, or matrix_run_summary."
    )


def validate_revalidation_summary_json(args: argparse.Namespace, summary_json_path: Path) -> dict[str, Any]:
    payload = read_json_if_present(summary_json_path)
    if not payload:
        raise SystemExit(f"Unable to read summary JSON: {summary_json_path}")
    summary_key, summary = find_revalidation_summary(payload)
    inputs = summary.get("revalidation_inputs") if isinstance(summary.get("revalidation_inputs"), dict) else {}
    task_id = str(args.validate_matrix_readback_task_id or inputs.get("validate_matrix_readback_task_id") or "")
    matrix_json = str(args.validate_matrix_json or inputs.get("validate_matrix_json") or "")
    client_id = str(inputs.get("client_id") or args.client_id or "")
    base_url = str(inputs.get("base_url") or args.base_url or "")
    missing_fields = [
        field
        for field, value in {
            "validate_matrix_readback_task_id": task_id,
            "validate_matrix_json": matrix_json,
            "client_id": client_id,
            "base_url": base_url,
        }.items()
        if not value
    ]
    if missing_fields:
        raise SystemExit(
            "Summary JSON revalidation inputs are incomplete: "
            + ", ".join(missing_fields)
        )
    client_args = argparse.Namespace(
        base_url=base_url,
        client_id=client_id,
        api_key=args.api_key,
        dry_run=False,
    )
    result = validate_existing_matrix_readback(
        client_args,
        task_id,
        Path(matrix_json),
        validate_published_scenarios=(
            args.validate_published_scenarios
            or bool(inputs.get("validate_published_scenarios"))
        ),
    )
    result["revalidation_source_summary_json"] = str(summary_json_path)
    result["revalidation_source_summary_key"] = summary_key
    result["revalidation_source_ready"] = summary.get("revalidation_ready")
    result["readback_validation_summary"] = existing_matrix_readback_compact_summary(result)
    return result


def maybe_record_failure_eval_case(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any]:
    if not getattr(args, "record_eval_case_on_failure", False):
        return payload
    payload["eval_case_recording"] = record_failure_eval_case(args, payload)
    if isinstance(payload.get("readback_validation_summary"), dict):
        payload["readback_validation_summary"] = existing_matrix_readback_compact_summary(payload)
    return payload


def record_failure_eval_case(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("status") == "completed":
        return {
            "status": "not_recorded",
            "reason": "matrix_revalidation_completed",
            "post_attempted": False,
            "post_skipped_reason": "matrix_revalidation_completed",
            "claim_boundary": "Eval cases are recorded only for failed stored-evidence matrix validation.",
        }
    client_id = str(payload.get("client_id") or getattr(args, "client_id", "") or "")
    base_url = str(payload.get("base_url") or getattr(args, "base_url", "") or harness_api.DEFAULT_BASE_URL)
    if not client_id:
        return {
            "status": "failed",
            "reason": "client_id_missing",
            "post_attempted": False,
            "post_skipped_reason": "client_id_missing",
            "claim_boundary": "Recording a harness eval case requires a client id.",
        }
    client_args = argparse.Namespace(
        base_url=base_url,
        client_id=client_id,
        api_key=getattr(args, "api_key", None),
        dry_run=False,
    )
    body = failure_eval_case_body(
        payload,
        verifier_command=str(getattr(args, "eval_case_verifier_command", "") or ""),
    )
    existing_recording = find_existing_eval_case_recording(client_args, body)
    if existing_recording.get("status") == "already_recorded":
        return existing_recording
    try:
        result = harness_api.request(client_args, "POST", "/eval-cases", body)
    except BaseException as exc:  # pragma: no cover - defensive controller boundary
        return {
            "status": "failed",
            "reason": "eval_case_recording_failed",
            "post_attempted": True,
            "post_failed_reason": "eval_case_recording_failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "request": body,
            "claim_boundary": (
                "The matrix failure was detected, but recording the regression eval case failed."
            ),
        }
    recording = {
        "status": "recorded",
        "source": "agents_sdk_smoke_matrix_failure_eval_case",
        "eval_case_id": result.get("eval_case_id"),
        "name": result.get("name") or body.get("name"),
        "failure_label": result.get("failure_label") or body.get("failure_label"),
        "source_task_id": result.get("source_task_id") or body.get("source_task_id"),
        "verifier_command": result.get("verifier_command") or body.get("verifier_command"),
        "evidence_refs": result.get("evidence_refs") or body.get("evidence_refs"),
        "failure_signature": eval_case_failure_signature(body),
        "failure_signature_version": EVAL_CASE_FAILURE_SIGNATURE_VERSION,
        "failure_signature_material": eval_case_failure_signature_material(body),
        "duplicate_prevented": False,
        "post_attempted": True,
        "post_required_reason": (
            "dedupe_lookup_failed"
            if existing_recording.get("status") == "lookup_failed"
            else "dedupe_lookup_not_found"
        ),
        "dedupe_lookup_status": existing_recording.get("status") or "unknown",
        "dedupe_lookup_readback_status": existing_recording.get("readback_status"),
        "dedupe_lookup_valid": existing_recording.get("readback_valid"),
        "dedupe_lookup_matching_eval_case_count": existing_recording.get(
            "readback_matching_eval_case_count"
        ),
        "dedupe_lookup_matching_eval_case_ids": existing_recording.get(
            "readback_matching_eval_case_ids"
        ),
        "claim_boundary": (
            "Eval case was recorded from failed stored matrix revalidation; it reuses stored "
            "harness evidence and does not run a new live SDK/provider call."
        ),
    }
    if existing_recording.get("status") == "lookup_failed":
        recording.update(
            {
                "dedupe_lookup_error_type": existing_recording.get("error_type"),
                "dedupe_lookup_error": existing_recording.get("error"),
                "dedupe_lookup_claim_boundary": existing_recording.get("claim_boundary"),
            }
        )
    recording.update(fetch_eval_case_recording_readback(client_args, result, body))
    return recording


def find_existing_eval_case_recording(
    client_args: argparse.Namespace,
    requested: dict[str, Any],
) -> dict[str, Any]:
    try:
        readback = harness_api.request(client_args, "GET", "/eval-cases", None)
    except BaseException as exc:
        return {
            "status": "lookup_failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "claim_boundary": (
                "The pre-recording eval-case duplicate lookup failed. The script may still try "
                "to record the eval case, but duplicate prevention was not verified first."
            ),
        }
    validation = validate_eval_case_recording_readback(readback, requested, requested)
    if validation.get("readback_status") != "matched" or not validation.get("readback_valid"):
        return {"status": "not_found", **validation}
    return {
        "status": "already_recorded",
        "dedupe_lookup_status": "matched",
        "source": "agents_sdk_smoke_matrix_failure_eval_case",
        "eval_case_id": validation.get("readback_matched_eval_case_id"),
        "name": requested.get("name"),
        "failure_label": requested.get("failure_label"),
        "source_task_id": requested.get("source_task_id"),
        "verifier_command": requested.get("verifier_command"),
        "evidence_refs": requested.get("evidence_refs"),
        "failure_signature": validation.get("failure_signature"),
        "failure_signature_version": validation.get("failure_signature_version"),
        "failure_signature_material": validation.get("failure_signature_material"),
        "duplicate_prevented": True,
        "post_attempted": False,
        "post_skipped_reason": "matching_eval_case_found",
        "dedupe_lookup_readback_status": validation.get("readback_status"),
        "dedupe_lookup_valid": validation.get("readback_valid"),
        "dedupe_lookup_matching_eval_case_count": validation.get(
            "readback_matching_eval_case_count"
        ),
        "dedupe_lookup_matching_eval_case_ids": validation.get(
            "readback_matching_eval_case_ids"
        ),
        "claim_boundary": (
            "A matching client-scoped eval case already existed, so the matrix script reused "
            "the stored regression seed instead of posting a duplicate. This still reuses stored "
            "harness evidence and does not run a new live SDK/provider call."
        ),
        **validation,
    }


def fetch_eval_case_recording_readback(
    client_args: argparse.Namespace,
    recorded: dict[str, Any],
    requested: dict[str, Any],
) -> dict[str, Any]:
    try:
        readback = harness_api.request(client_args, "GET", "/eval-cases", None)
    except BaseException as exc:  # pragma: no cover - defensive controller boundary
        return {
            "readback_status": "failed",
            "readback_valid": False,
            "readback_check_count": 1,
            "readback_failed_checks": ["eval_case_readback_request_completed"],
            "readback_error_type": type(exc).__name__,
            "readback_error": str(exc),
            "claim_boundary": (
                "Eval case POST succeeded, but the script could not verify it through "
                "GET /eval-cases readback."
            ),
        }
    return validate_eval_case_recording_readback(readback, recorded, requested)


def validate_eval_case_recording_readback(
    readback: dict[str, Any],
    recorded: dict[str, Any],
    requested: dict[str, Any],
) -> dict[str, Any]:
    eval_cases = list_value(readback.get("eval_cases"))
    target_id = recorded.get("eval_case_id")
    expected_name = recorded.get("name") or requested.get("name")
    expected_failure_label = recorded.get("failure_label") or requested.get("failure_label")
    expected_source_task_id = recorded.get("source_task_id") or requested.get("source_task_id")
    expected_verifier_command = recorded.get("verifier_command") or requested.get("verifier_command")
    expected_canonical_verifier_command = canonical_eval_case_verifier_command(
        expected_verifier_command
    )
    expected_evidence_refs = list_value(recorded.get("evidence_refs") or requested.get("evidence_refs"))
    expected_payload = requested.get("expected") if isinstance(requested.get("expected"), dict) else {}
    expected_signature = eval_case_failure_signature(requested)
    expected_signature_material = eval_case_failure_signature_material(requested)
    expected_signature_material_digest = eval_case_failure_signature_material_digest(
        expected_signature_material
    )
    matching_eval_case_ids: list[Any] = []
    missing_stored_signature_ids: list[Any] = []
    mismatched_stored_signature_ids: list[Any] = []
    for item in eval_cases:
        if not isinstance(item, dict):
            continue
        if eval_case_record_signature(item) == expected_signature:
            eval_case_id = item.get("eval_case_id")
            matching_eval_case_ids.append(eval_case_id)
            stored_signature = stored_eval_case_failure_signature(item)
            if not stored_signature:
                missing_stored_signature_ids.append(eval_case_id)
            elif stored_signature != expected_signature:
                mismatched_stored_signature_ids.append(eval_case_id)
    matched = None
    match_strategy = "not_found"
    if target_id is not None:
        for item in eval_cases:
            if isinstance(item, dict) and str(item.get("eval_case_id")) == str(target_id):
                matched = item
                match_strategy = "eval_case_id"
                break
    if matched is None:
        for item in eval_cases:
            if not isinstance(item, dict):
                continue
            if (
                item.get("eval_case_id") in matching_eval_case_ids
                or (
                    item.get("name") == expected_name
                    and item.get("source_task_id") == expected_source_task_id
                    and item.get("failure_label") == expected_failure_label
                )
            ):
                matched = item
                match_strategy = "name_source_failure_label"
                break

    checks: dict[str, bool] = {
        "eval_case_readback_status_completed": readback.get("status") == "completed",
        "eval_case_readback_list_present": isinstance(readback.get("eval_cases"), list),
        "eval_case_readback_record_found": matched is not None,
        "eval_case_readback_signature_matches_material": (
            expected_signature_material_digest == expected_signature
        ),
        "eval_case_readback_signature_version_current": (
            expected_signature_material.get("signature_version")
            == EVAL_CASE_FAILURE_SIGNATURE_VERSION
        ),
    }
    if isinstance(matched, dict):
        matched_expected = matched.get("expected") if isinstance(matched.get("expected"), dict) else {}
        matched_canonical_verifier_command = canonical_eval_case_verifier_command(
            matched.get("verifier_command")
        )
        checks.update(
            {
                "eval_case_readback_id_matches": (
                    target_id is None or str(matched.get("eval_case_id")) == str(target_id)
                ),
                "eval_case_readback_name_matches": matched.get("name") == expected_name,
                "eval_case_readback_failure_label_matches": (
                    matched.get("failure_label") == expected_failure_label
                ),
                "eval_case_readback_source_task_matches": (
                    matched.get("source_task_id") == expected_source_task_id
                ),
                "eval_case_readback_verifier_command_matches": (
                    matched_canonical_verifier_command == expected_canonical_verifier_command
                ),
                "eval_case_readback_verifier_command_exact_matches": (
                    matched.get("verifier_command") == expected_verifier_command
                ),
                "eval_case_readback_evidence_refs_match": (
                    list_value(matched.get("evidence_refs")) == expected_evidence_refs
                ),
                "eval_case_readback_expected_status_matches": (
                    matched_expected.get("status") == expected_payload.get("status")
                ),
            }
        )
    non_blocking_checks = {"eval_case_readback_verifier_command_exact_matches"}
    failed_checks = [
        name for name, valid in checks.items() if not valid and name not in non_blocking_checks
    ]
    non_blocking_failed_checks = [
        name for name, valid in checks.items() if not valid and name in non_blocking_checks
    ]
    if mismatched_stored_signature_ids:
        signature_metadata_status = "mismatch"
    elif missing_stored_signature_ids:
        signature_metadata_status = "missing"
    elif matching_eval_case_ids:
        signature_metadata_status = "current"
    else:
        signature_metadata_status = "not_found"
    return {
        "readback_status": "matched" if isinstance(matched, dict) else "not_found",
        "readback_valid": not failed_checks,
        "readback_check_count": len(checks),
        "readback_failed_checks": failed_checks,
        "readback_non_blocking_failed_checks": non_blocking_failed_checks,
        "readback_checks": checks,
        "readback_eval_case_count": len(eval_cases),
        "failure_signature": expected_signature,
        "failure_signature_version": EVAL_CASE_FAILURE_SIGNATURE_VERSION,
        "failure_signature_material": expected_signature_material,
        "failure_signature_material_digest": expected_signature_material_digest,
        "readback_signature_metadata_status": signature_metadata_status,
        "readback_matching_eval_case_count": len(matching_eval_case_ids),
        "readback_matching_eval_case_ids": matching_eval_case_ids,
        "readback_matching_eval_case_stored_signature_missing_count": len(
            missing_stored_signature_ids
        ),
        "readback_matching_eval_case_stored_signature_missing_ids": missing_stored_signature_ids,
        "readback_matching_eval_case_stored_signature_mismatch_count": len(
            mismatched_stored_signature_ids
        ),
        "readback_matching_eval_case_stored_signature_mismatch_ids": mismatched_stored_signature_ids,
        "readback_matched_eval_case_id": matched.get("eval_case_id") if isinstance(matched, dict) else None,
        "readback_match_strategy": match_strategy,
    }


def failure_eval_case_body(payload: dict[str, Any], *, verifier_command: str = "") -> dict[str, Any]:
    summary = (
        payload.get("readback_validation_summary")
        if isinstance(payload.get("readback_validation_summary"), dict)
        else existing_matrix_readback_compact_summary(payload)
    )
    task_id = str(payload.get("task_id") or "")
    matrix_json = str(payload.get("matrix_json") or "")
    failure_label = matrix_failure_label(summary, payload)
    evidence_refs = matrix_failure_evidence_refs(summary, task_id, matrix_json)
    matrix_run_id = str(payload.get("matrix_run_id") or summary.get("matrix_run_id") or "unknown")
    body = {
        "name": f"Agents SDK matrix readback failure: {matrix_run_id}",
        "source_task_id": task_id or None,
        "failure_label": failure_label,
        "input": {
            "mode": "agents_sdk_smoke_matrix_readback",
            "task_id": task_id,
            "matrix_json": matrix_json,
            "validate_published_scenarios": bool(payload.get("validate_published_scenarios")),
            "revalidation_cli_args": summary.get("revalidation_cli_args") or [],
            "summary_revalidation_cli_args": summary.get("summary_revalidation_cli_args") or [],
            "aggregate_failed_checks": summary.get("aggregate_failed_checks") or [],
            "published_scenario_stage_failed_checks": (
                summary.get("published_scenario_stage_failed_checks") or {}
            ),
        },
        "expected": {
            "status": "completed",
            "aggregate_failed_checks": [],
            "published_scenario_stage_failed_checks": {},
        },
        "verifier_command": verifier_command or matrix_failure_verifier_command(summary),
        "evidence_refs": evidence_refs,
        "metadata": {
            "source": "agents_sdk_smoke_matrix",
            "matrix_run_id": matrix_run_id,
            "matrix_json": matrix_json,
            "recorded_from": "stored_matrix_revalidation_failure",
            "claim_boundary": (
                "Regression seed for stored harness evidence validation, not proof of live "
                "provider connectivity."
            ),
        },
    }
    body["metadata"]["failure_signature_version"] = EVAL_CASE_FAILURE_SIGNATURE_VERSION
    body["metadata"]["failure_signature_material"] = eval_case_failure_signature_material(body)
    body["metadata"]["failure_signature_material_digest"] = eval_case_failure_signature_material_digest(
        body["metadata"]["failure_signature_material"]
    )
    body["metadata"]["failure_signature"] = eval_case_failure_signature(body)
    return body


def eval_case_record_signature(record: dict[str, Any]) -> str:
    # Recompute so older rows with pre-canonical metadata still group with the current rule.
    return eval_case_failure_signature(record)


def stored_eval_case_failure_signature(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    signature = metadata.get("failure_signature")
    if isinstance(signature, str) and signature.startswith("sha256:"):
        return signature
    return ""


def eval_case_failure_signature(record: dict[str, Any]) -> str:
    material = eval_case_failure_signature_material(record)
    return eval_case_failure_signature_material_digest(material)


def eval_case_failure_signature_material_digest(material: dict[str, Any]) -> str:
    encoded = json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def eval_case_failure_signature_material(record: dict[str, Any]) -> dict[str, Any]:
    input_payload = record.get("input") if isinstance(record.get("input"), dict) else {}
    expected_payload = record.get("expected") if isinstance(record.get("expected"), dict) else {}
    return {
        "signature_version": EVAL_CASE_FAILURE_SIGNATURE_VERSION,
        "name": record.get("name"),
        "source_task_id": record.get("source_task_id"),
        "failure_label": record.get("failure_label"),
        "verifier_command": canonical_eval_case_verifier_command(record.get("verifier_command")),
        "evidence_refs": list_value(record.get("evidence_refs")),
        "expected": expected_payload,
        "input": {
            "mode": input_payload.get("mode"),
            "task_id": input_payload.get("task_id"),
            "matrix_json": input_payload.get("matrix_json"),
            "validate_published_scenarios": input_payload.get("validate_published_scenarios"),
        },
    }


def canonical_eval_case_verifier_command(value: Any) -> list[str]:
    text = str(value or "")
    if not text:
        return []
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    stripped: list[str] = []
    skip_next = False
    ignored_options = {"--base-url", "--client-id", "--compact-output"}
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in {"--base-url", "--client-id"}:
            skip_next = True
            continue
        if token in ignored_options:
            continue
        if token.startswith("--base-url=") or token.startswith("--client-id="):
            continue
        stripped.append(token)
    return stripped


def matrix_failure_label(summary: dict[str, Any], payload: dict[str, Any]) -> str:
    aggregate_failed = list_value(summary.get("aggregate_failed_checks"))
    if aggregate_failed:
        return safe_failure_label(f"aggregate:{aggregate_failed[0]}")
    stage_failed = summary.get("published_scenario_stage_failed_checks")
    if isinstance(stage_failed, dict):
        for scenario, failures in sorted(stage_failed.items()):
            failure_items = list_value(failures)
            if failure_items:
                return safe_failure_label(f"scenario:{scenario}:{failure_items[0]}")
    scenario_failed = summary.get("published_scenario_event_replay_failed_checks")
    if isinstance(scenario_failed, dict):
        for scenario, failures in sorted(scenario_failed.items()):
            failure_items = list_value(failures)
            if failure_items:
                return safe_failure_label(f"scenario_event_replay:{scenario}:{failure_items[0]}")
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    failed_checks = list_value(validation.get("failed_checks"))
    if failed_checks:
        return safe_failure_label(f"validation:{failed_checks[0]}")
    return "agents_sdk_matrix_readback_failed"


def safe_failure_label(value: Any) -> str:
    text = str(value or "agents_sdk_matrix_readback_failed").strip()
    safe = "".join(ch if ch.isalnum() or ch in {":", "_", "-", "."} else "_" for ch in text)
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe.strip("_")[:180] or "agents_sdk_matrix_readback_failed"


def matrix_failure_evidence_refs(summary: dict[str, Any], task_id: str, matrix_json: str) -> list[str]:
    refs: list[str] = []
    if task_id:
        refs.append(f"task:{task_id}")
    if matrix_json:
        refs.append(f"matrix:{matrix_json}")
    task_ids = summary.get("published_scenario_task_bindings")
    if isinstance(task_ids, dict):
        for scenario, binding in sorted(task_ids.items()):
            if not isinstance(binding, dict):
                continue
            scenario_task_id = binding.get("expected_task_id") or binding.get("readback_task_id")
            if scenario_task_id:
                refs.append(f"scenario_task:{scenario}:{scenario_task_id}")
    return refs


def matrix_failure_verifier_command(summary: dict[str, Any]) -> str:
    cli_args = list_value(summary.get("revalidation_cli_args"))
    command = [
        "python",
        ".agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke_matrix.py",
        *[str(item) for item in cli_args],
    ]
    return " ".join(shlex.quote(item) for item in command)


def matrix_run_compact_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    results = list_value(payload.get("results"))
    matrix_readback = (
        payload.get("matrix_readback") if isinstance(payload.get("matrix_readback"), dict) else {}
    )
    matrix_readback_validation = (
        matrix_readback.get("validation")
        if isinstance(matrix_readback.get("validation"), dict)
        else {}
    )
    matrix_payload_readback = (
        payload.get("matrix_payload_readback_validation")
        if isinstance(payload.get("matrix_payload_readback_validation"), dict)
        else {}
    )
    matrix_payload_readback_validation = (
        matrix_payload_readback.get("validation")
        if isinstance(matrix_payload_readback.get("validation"), dict)
        else {}
    )
    matrix_payload_provider_diagnostic = (
        matrix_payload_readback.get("provider_diagnostic_readback")
        if isinstance(matrix_payload_readback.get("provider_diagnostic_readback"), dict)
        else {}
    )
    matrix_readback_artifact = matrix_artifact_compact_validation(matrix_readback_validation)
    matrix_payload_readback_artifact = matrix_artifact_compact_validation(
        matrix_payload_readback_validation
    )
    matrix_readback_fetch_plan = event_replay_fetch_plan_compact_validation(matrix_readback_validation)
    matrix_payload_readback_fetch_plan = event_replay_fetch_plan_compact_validation(
        matrix_payload_readback_validation
    )
    matrix_readback_replay_counts = event_replay_count_compact_validation(
        matrix_readback_validation
    )
    matrix_payload_readback_replay_counts = event_replay_count_compact_validation(
        matrix_payload_readback_validation
    )
    matrix_readback_replay_coverage = event_replay_coverage_compact_validation(
        matrix_readback_validation
    )
    matrix_payload_readback_replay_coverage = event_replay_coverage_compact_validation(
        matrix_payload_readback_validation
    )
    provider_probe_validation = (
        payload.get("provider_probe_validation")
        if isinstance(payload.get("provider_probe_validation"), dict)
        else {}
    )
    provider_probe_summary = provider_probe_summary_fields(summary, provider_probe_validation)
    planned_commands = list_value(plan.get("commands"))
    matrix_output_json = payload.get("matrix_output_json") or plan.get("matrix_output_json")
    matrix_evidence_json = payload.get("matrix_evidence_json") or plan.get("matrix_evidence_json")
    matrix_readback_task_id = matrix_readback.get("task_id")
    revalidation_inputs = {
        "validate_matrix_readback_task_id": matrix_readback_task_id,
        "validate_matrix_json": matrix_output_json,
        "validate_published_scenarios": bool(summary.get("published_task_ids") or {}),
        "client_id": payload.get("client_id") or plan.get("client_id"),
        "base_url": payload.get("base_url") or plan.get("base_url"),
        "compact_output": True,
    }
    revalidation_readiness = revalidation_input_readiness(revalidation_inputs)
    summary_revalidation = summary_revalidation_details(
        matrix_output_json,
        revalidation_readiness,
    )
    scenario_readback_counts = scenario_publish_readback_compact_counts(results)
    valid = matrix_run_compact_valid(
        payload,
        summary,
        provider_probe_validation,
        matrix_readback,
        matrix_readback_validation,
        matrix_payload_readback,
        matrix_payload_readback_validation,
        matrix_payload_provider_diagnostic,
    )
    return {
        "status": payload.get("status"),
        "valid": valid,
        "source": "agents_sdk_smoke_matrix_compact_summary",
        "run_id": payload.get("run_id"),
        "model": payload.get("model") or plan.get("model"),
        "provider": payload.get("provider"),
        "api_style": payload.get("api_style"),
        "base_url": payload.get("base_url") or plan.get("base_url"),
        "client_id": payload.get("client_id") or plan.get("client_id"),
        "scenario_count": int_value(payload.get("scenario_count")),
        "requested_scenarios": payload.get("requested_scenarios")
        or [item.get("scenario") for item in planned_commands if isinstance(item, dict)],
        "passed_count": int_value(summary.get("passed_count")),
        "failed_count": int_value(summary.get("failed_count")),
        "scenario_status_counts": summary.get("scenario_status_counts") or {},
        "validation_status_counts": summary.get("validation_status_counts") or {},
        "publish_status_counts": summary.get("publish_status_counts") or {},
        "generic_readback_status_counts": summary.get("generic_readback_status_counts") or {},
        "projection_validation_status_counts": (
            summary.get("projection_validation_status_counts") or {}
        ),
        "scenario_event_replay_status_counts": (
            summary.get("scenario_event_replay_status_counts") or {}
        ),
        "failed_scenarios": summary.get("failed_scenarios") or [],
        "scenario_failed_checks": summary.get("scenario_failed_checks") or {},
        "generic_readback_failed_scenarios": summary.get("generic_readback_failed_scenarios") or [],
        "projection_failed_scenarios": summary.get("projection_failed_scenarios") or [],
        "scenario_event_replay_failed_scenarios": (
            summary.get("scenario_event_replay_failed_scenarios") or []
        ),
        "generic_readback_failed_checks": summary.get("generic_readback_failed_checks") or {},
        "projection_failed_checks": summary.get("projection_failed_checks") or {},
        "scenario_event_replay_failed_checks": (
            summary.get("scenario_event_replay_failed_checks") or {}
        ),
        "aggregate_failed_reasons": summary.get("aggregate_failed_reasons") or [],
        "provider_probe_valid": provider_probe_summary["provider_probe_valid"],
        "provider_probe_diagnostic_id": provider_probe_summary["provider_probe_diagnostic_id"],
        "provider_probe_failed_checks": provider_probe_summary["provider_probe_failed_checks"],
        "provider_probe_check_count": provider_probe_summary["provider_probe_check_count"],
        "provider_probe_checks": provider_probe_summary["provider_probe_checks"],
        "provider_probe_status": provider_probe_summary["provider_probe_status"],
        "provider_probe_http_status": provider_probe_summary["provider_probe_http_status"],
        "provider_probe_endpoint": provider_probe_summary["provider_probe_endpoint"],
        "provider_probe_models_preview_count": provider_probe_summary[
            "provider_probe_models_preview_count"
        ],
        "provider_probe_profile": provider_probe_summary["provider_probe_profile"],
        "provider_probe_agent_sdk_fit": provider_probe_summary[
            "provider_probe_agent_sdk_fit"
        ],
        "external_api_called": summary.get("external_api_called") is True,
        "scenario_external_api_called": (
            summary.get("scenario_external_api_called") or {}
        ),
        "scenario_readback_external_api_called": (
            scenario_readback_counts["external_api_called_by_scenario"]
        ),
        "scenario_api_usage_totals": summary.get("scenario_api_usage_totals") or {},
        "api_usage_totals": summary.get("api_usage_totals") or {},
        "scenario_sdk_events_projection_counts": (
            summary.get("scenario_sdk_events_projection_counts") or {}
        ),
        "scenario_client_transcript_projection_counts": (
            summary.get("scenario_client_transcript_projection_counts") or {}
        ),
        "scenario_sdk_events_projection_maps": (
            summary.get("scenario_sdk_events_projection_maps")
            or scenario_readback_counts["sdk_events_projection_maps"]
            or {}
        ),
        "scenario_client_transcript_projection_maps": (
            summary.get("scenario_client_transcript_projection_maps")
            or scenario_readback_counts["client_transcript_projection_maps"]
            or {}
        ),
        "scenario_tool_state_summaries": (
            summary.get("scenario_tool_state_summaries")
            or scenario_tool_state_summary_maps(results)
            or {}
        ),
        "scenario_tool_evidence_summaries": (
            summary.get("scenario_tool_evidence_summaries")
            or scenario_readback_counts["tool_evidence_summaries"]
            or {}
        ),
        "scenario_approval_evidence_summaries": (
            summary.get("scenario_approval_evidence_summaries")
            or scenario_readback_counts["approval_evidence_summaries"]
            or {}
        ),
        "scenario_handoff_evidence_summaries": (
            summary.get("scenario_handoff_evidence_summaries")
            or scenario_readback_counts["handoff_evidence_summaries"]
            or {}
        ),
        "scenario_guardrail_evidence_summaries": (
            summary.get("scenario_guardrail_evidence_summaries")
            or scenario_readback_counts["guardrail_evidence_summaries"]
            or {}
        ),
        "scenario_session_evidence_summaries": (
            summary.get("scenario_session_evidence_summaries")
            or scenario_readback_counts["session_evidence_summaries"]
            or {}
        ),
        "scenario_stream_evidence_summaries": (
            summary.get("scenario_stream_evidence_summaries")
            or scenario_readback_counts["stream_evidence_summaries"]
            or {}
        ),
        "scenario_tool_projection_summaries": (
            summary.get("scenario_tool_projection_summaries")
            or scenario_readback_counts["tool_projection_summaries"]
            or {}
        ),
        "scenario_stream_projection_summaries": (
            summary.get("scenario_stream_projection_summaries") or {}
        ),
        "scenario_handoff_state_summaries": (
            summary.get("scenario_handoff_state_summaries")
            or scenario_handoff_state_summary_maps(results)
            or {}
        ),
        "scenario_handoff_projection_summaries": (
            summary.get("scenario_handoff_projection_summaries")
            or scenario_readback_counts["handoff_projection_summaries"]
            or {}
        ),
        "scenario_approval_state_summaries": (
            summary.get("scenario_approval_state_summaries")
            or scenario_approval_state_summary_maps(results)
            or {}
        ),
        "scenario_approval_projection_summaries": (
            summary.get("scenario_approval_projection_summaries")
            or scenario_readback_counts["approval_projection_summaries"]
            or {}
        ),
        "scenario_session_state_summaries": (
            summary.get("scenario_session_state_summaries")
            or scenario_session_state_summary_maps(results)
            or {}
        ),
        "scenario_guardrail_tripwire_summaries": (
            summary.get("scenario_guardrail_tripwire_summaries")
            or scenario_guardrail_tripwire_summary_maps(results)
            or {}
        ),
        "scenario_guardrail_projection_summaries": (
            summary.get("scenario_guardrail_projection_summaries")
            or scenario_readback_counts["guardrail_projection_summaries"]
            or {}
        ),
        "sdk_events_projection_totals": summary.get("sdk_events_projection_totals") or {},
        "client_transcript_projection_totals": (
            summary.get("client_transcript_projection_totals") or {}
        ),
        "sdk_events_projection_map_totals": (
            summary.get("sdk_events_projection_map_totals")
            or scenario_readback_counts["sdk_events_projection_map_totals"]
            or {}
        ),
        "client_transcript_projection_map_totals": (
            summary.get("client_transcript_projection_map_totals")
            or scenario_readback_counts["client_transcript_projection_map_totals"]
            or {}
        ),
        "projection_total_deltas": summary.get("projection_total_deltas") or {},
        "projection_total_delta_status": summary.get("projection_total_delta_status"),
        "projection_total_delta_assessment": (
            summary.get("projection_total_delta_assessment") or {}
        ),
        "projection_total_delta_allowed_keys": (
            summary.get("projection_total_delta_allowed_keys") or []
        ),
        "projection_total_delta_unexpected_keys": (
            summary.get("projection_total_delta_unexpected_keys") or []
        ),
        "projection_map_deltas": summary.get("projection_map_deltas") or {},
        "projection_map_delta_status": summary.get("projection_map_delta_status"),
        "projection_map_delta_assessment": (
            summary.get("projection_map_delta_assessment") or {}
        ),
        "projection_map_delta_unexpected_keys": (
            summary.get("projection_map_delta_unexpected_keys") or []
        ),
        "scenario_event_replay_expected_event_counts": (
            summary.get("scenario_event_replay_expected_event_counts") or {}
        ),
        "scenario_event_replay_observed_event_counts": (
            summary.get("scenario_event_replay_observed_event_counts") or {}
        ),
        "scenario_event_replay_event_counts": (
            summary.get("scenario_event_replay_event_counts")
            or scenario_readback_counts["event_replay_event_counts"]
            or {}
        ),
        "scenario_event_replay_count_map_totals": (
            summary.get("scenario_event_replay_count_map_totals")
            or scenario_readback_counts["event_replay_count_map_totals"]
            or {}
        ),
        "scenario_event_replay_count_map_total_totals": (
            summary.get("scenario_event_replay_count_map_total_totals")
            or scenario_readback_counts["event_replay_count_map_total_totals"]
            or {}
        ),
        "scenario_event_replay_count_total_checks": (
            summary.get("scenario_event_replay_count_total_checks")
            or scenario_readback_counts["event_replay_count_total_checks"]
            or {}
        ),
        "scenario_event_replay_sse_event_name_counts": (
            summary.get("scenario_event_replay_sse_event_name_counts")
            or scenario_readback_counts["event_replay_sse_event_name_counts"]
            or {}
        ),
        "scenario_event_replay_event_type_counts": (
            summary.get("scenario_event_replay_event_type_counts")
            or scenario_readback_counts["event_replay_event_type_counts"]
            or {}
        ),
        "scenario_event_replay_event_status_counts": (
            summary.get("scenario_event_replay_event_status_counts")
            or scenario_readback_counts["event_replay_event_status_counts"]
            or {}
        ),
        "scenario_event_replay_event_actor_counts": (
            summary.get("scenario_event_replay_event_actor_counts")
            or scenario_readback_counts["event_replay_event_actor_counts"]
            or {}
        ),
        "scenario_event_replay_sse_event_name_totals": (
            summary.get("scenario_event_replay_sse_event_name_totals")
            or scenario_readback_counts["event_replay_sse_event_name_totals"]
            or {}
        ),
        "scenario_event_replay_event_type_totals": (
            summary.get("scenario_event_replay_event_type_totals")
            or scenario_readback_counts["event_replay_event_type_totals"]
            or {}
        ),
        "scenario_event_replay_event_status_totals": (
            summary.get("scenario_event_replay_event_status_totals")
            or scenario_readback_counts["event_replay_event_status_totals"]
            or {}
        ),
        "scenario_event_replay_event_actor_totals": (
            summary.get("scenario_event_replay_event_actor_totals")
            or scenario_readback_counts["event_replay_event_actor_totals"]
            or {}
        ),
        "scenario_event_replay_expected_event_totals": (
            summary.get("scenario_event_replay_expected_event_totals") or {}
        ),
        "scenario_event_replay_observed_event_totals": (
            summary.get("scenario_event_replay_observed_event_totals") or {}
        ),
        "tool_call_status_update_expected_counts": (
            summary.get("tool_call_status_update_expected_counts") or {}
        ),
        "tool_call_status_updated_event_counts": (
            summary.get("tool_call_status_updated_event_counts") or {}
        ),
        "tool_call_status_update_expected_total": int_value(
            summary.get("tool_call_status_update_expected_total")
        ),
        "tool_call_status_updated_event_total": int_value(
            summary.get("tool_call_status_updated_event_total")
        ),
        "published_task_ids": summary.get("published_task_ids") or {},
        "matrix_readback_status": summary.get("matrix_readback_status"),
        "matrix_readback_valid": summary.get("matrix_readback_valid"),
        "matrix_readback_task_id": matrix_readback_task_id,
        "matrix_readback_check_count": int_value(matrix_readback_validation.get("check_count")),
        "matrix_readback_failed_checks": summary.get("matrix_readback_failed_checks") or [],
        "matrix_readback_task_binding": summary.get("matrix_readback_task_binding") or {},
        "matrix_readback_event_replay_fetch_plan": matrix_readback_fetch_plan["fetch_plan"],
        "matrix_readback_event_replay_fetch_plan_checks": matrix_readback_fetch_plan["checks"],
        "matrix_readback_event_replay_event_count": matrix_readback_replay_counts["event_count"],
        "matrix_readback_event_replay_count_map_totals": (
            matrix_readback_replay_counts["count_map_totals"]
        ),
        "matrix_readback_event_replay_count_total_checks": (
            matrix_readback_replay_counts["checks"]
        ),
        "matrix_readback_event_replay_expected_event_counts": (
            matrix_readback_replay_coverage["expected_event_counts"]
        ),
        "matrix_readback_event_replay_observed_event_counts": (
            matrix_readback_replay_coverage["observed_event_counts"]
        ),
        "matrix_readback_event_replay_sse_event_name_counts": (
            matrix_readback_replay_counts["sse_event_name_counts"]
        ),
        "matrix_readback_event_replay_event_type_counts": (
            matrix_readback_replay_counts["event_type_counts"]
        ),
        "matrix_readback_event_replay_event_status_counts": (
            matrix_readback_replay_counts["event_status_counts"]
        ),
        "matrix_readback_event_replay_event_actor_counts": (
            matrix_readback_replay_counts["event_actor_counts"]
        ),
        "matrix_readback_artifact": matrix_readback_artifact["artifact"],
        "matrix_readback_artifact_checks": matrix_readback_artifact["checks"],
        "matrix_payload_readback_status": summary.get("matrix_payload_readback_status"),
        "matrix_payload_readback_valid": summary.get("matrix_payload_readback_valid"),
        "matrix_payload_readback_check_count": int_value(
            matrix_payload_readback_validation.get("check_count")
        ),
        "matrix_payload_readback_failed_checks": (
            summary.get("matrix_payload_readback_failed_checks") or []
        ),
        "matrix_payload_readback_task_binding": (
            summary.get("matrix_payload_readback_task_binding") or {}
        ),
        "matrix_payload_readback_event_replay_fetch_plan": (
            matrix_payload_readback_fetch_plan["fetch_plan"]
        ),
        "matrix_payload_readback_event_replay_fetch_plan_checks": (
            matrix_payload_readback_fetch_plan["checks"]
        ),
        "matrix_payload_readback_event_replay_event_count": (
            matrix_payload_readback_replay_counts["event_count"]
        ),
        "matrix_payload_readback_event_replay_count_map_totals": (
            matrix_payload_readback_replay_counts["count_map_totals"]
        ),
        "matrix_payload_readback_event_replay_count_total_checks": (
            matrix_payload_readback_replay_counts["checks"]
        ),
        "matrix_payload_readback_event_replay_expected_event_counts": (
            matrix_payload_readback_replay_coverage["expected_event_counts"]
        ),
        "matrix_payload_readback_event_replay_observed_event_counts": (
            matrix_payload_readback_replay_coverage["observed_event_counts"]
        ),
        "matrix_payload_readback_event_replay_sse_event_name_counts": (
            matrix_payload_readback_replay_counts["sse_event_name_counts"]
        ),
        "matrix_payload_readback_event_replay_event_type_counts": (
            matrix_payload_readback_replay_counts["event_type_counts"]
        ),
        "matrix_payload_readback_event_replay_event_status_counts": (
            matrix_payload_readback_replay_counts["event_status_counts"]
        ),
        "matrix_payload_readback_event_replay_event_actor_counts": (
            matrix_payload_readback_replay_counts["event_actor_counts"]
        ),
        "matrix_payload_readback_artifact": matrix_payload_readback_artifact["artifact"],
        "matrix_payload_readback_artifact_checks": matrix_payload_readback_artifact["checks"],
        "provider_diagnostic_readback": matrix_payload_provider_diagnostic,
        "provider_diagnostic_readback_status": matrix_payload_provider_diagnostic.get("status"),
        "provider_diagnostic_readback_valid": matrix_payload_provider_diagnostic.get("valid"),
        "provider_diagnostic_readback_failed_checks": (
            matrix_payload_provider_diagnostic.get("failed_checks") or []
        ),
        "provider_diagnostic_readback_check_count": int_value(
            matrix_payload_provider_diagnostic.get("check_count")
        ),
        "provider_diagnostic_readback_checks": (
            matrix_payload_provider_diagnostic.get("checks")
            if isinstance(matrix_payload_provider_diagnostic.get("checks"), dict)
            else {}
        ),
        "provider_diagnostic_readback_expected_id": int_value(
            matrix_payload_provider_diagnostic.get("expected_diagnostic_id")
        ),
        "provider_diagnostic_readback_matched_id": int_value(
            matrix_payload_provider_diagnostic.get("matched_diagnostic_id")
        ),
        "provider_diagnostic_readback_matched": matrix_payload_provider_diagnostic.get("matched"),
        "provider_diagnostic_readback_diagnostic_count": int_value(
            matrix_payload_provider_diagnostic.get("diagnostic_count")
        ),
        "provider_diagnostic_readback_matched_status": (
            matrix_payload_provider_diagnostic.get("matched_status")
        ),
        "provider_diagnostic_readback_matched_provider_request_attempted": (
            matrix_payload_provider_diagnostic.get("matched_provider_request_attempted")
        ),
        "provider_diagnostic_readback_matched_provider_response_received": (
            matrix_payload_provider_diagnostic.get("matched_provider_response_received")
        ),
        "provider_diagnostic_readback_matched_external_api_called": (
            matrix_payload_provider_diagnostic.get("matched_external_api_called")
        ),
        "provider_diagnostic_readback_matched_provider": (
            matrix_payload_provider_diagnostic.get("matched_provider")
            if isinstance(matrix_payload_provider_diagnostic.get("matched_provider"), dict)
            else {}
        ),
        "matrix_output_json": matrix_output_json,
        "matrix_evidence_json": matrix_evidence_json,
        "revalidation_inputs": revalidation_inputs,
        "revalidation_ready": revalidation_readiness["ready"],
        "revalidation_missing_fields": revalidation_readiness["missing_fields"],
        "revalidation_required_fields": revalidation_readiness["required_fields"],
        "revalidation_cli_args": revalidation_cli_args(revalidation_inputs),
        "summary_revalidation_inputs": summary_revalidation["inputs"],
        "summary_revalidation_ready": summary_revalidation["ready"],
        "summary_revalidation_missing_fields": summary_revalidation["missing_fields"],
        "summary_revalidation_cli_args": summary_revalidation["cli_args"],
        "scenario_publish_readback_count": scenario_readback_counts["published_scenario_count"],
        "scenario_publish_readback_valid": scenario_readback_counts["valid"],
        "scenario_publish_readback_valid_by_scenario": (
            scenario_readback_counts["valid_by_scenario"]
        ),
        "scenario_publish_readback_failed_scenarios": (
            scenario_readback_counts["failed_scenarios"]
        ),
        "scenario_publish_readback_status_by_scenario": scenario_readback_counts["status_by_scenario"],
        "scenario_generic_readback_check_counts": scenario_readback_counts["generic_check_counts"],
        "scenario_projection_check_counts": scenario_readback_counts["projection_check_counts"],
        "scenario_event_replay_check_counts": scenario_readback_counts["event_replay_check_counts"],
        "scenario_total_readback_check_counts": scenario_readback_counts["total_check_counts"],
        "scenario_event_replay_fetch_plans": scenario_readback_counts["event_replay_fetch_plans"],
        "scenario_event_replay_fetch_plan_checks": (
            scenario_readback_counts["event_replay_fetch_plan_checks"]
        ),
        "scenario_readback_event_replay_event_counts": (
            scenario_readback_counts["event_replay_event_counts"]
        ),
        "scenario_readback_event_replay_expected_event_counts": (
            scenario_readback_counts["event_replay_expected_event_counts"]
        ),
        "scenario_readback_event_replay_observed_event_counts": (
            scenario_readback_counts["event_replay_observed_event_counts"]
        ),
        "scenario_readback_event_replay_expected_event_totals": (
            scenario_readback_counts["event_replay_expected_event_totals"]
        ),
        "scenario_readback_event_replay_observed_event_totals": (
            scenario_readback_counts["event_replay_observed_event_totals"]
        ),
        "scenario_readback_event_replay_count_map_totals": (
            scenario_readback_counts["event_replay_count_map_totals"]
        ),
        "scenario_readback_event_replay_count_map_total_totals": (
            scenario_readback_counts["event_replay_count_map_total_totals"]
        ),
        "scenario_readback_event_replay_count_total_checks": (
            scenario_readback_counts["event_replay_count_total_checks"]
        ),
        "scenario_readback_event_replay_sse_event_name_counts": (
            scenario_readback_counts["event_replay_sse_event_name_counts"]
        ),
        "scenario_readback_event_replay_event_type_counts": (
            scenario_readback_counts["event_replay_event_type_counts"]
        ),
        "scenario_readback_event_replay_event_status_counts": (
            scenario_readback_counts["event_replay_event_status_counts"]
        ),
        "scenario_readback_event_replay_event_actor_counts": (
            scenario_readback_counts["event_replay_event_actor_counts"]
        ),
        "scenario_readback_event_replay_sse_event_name_totals": (
            scenario_readback_counts["event_replay_sse_event_name_totals"]
        ),
        "scenario_readback_event_replay_event_type_totals": (
            scenario_readback_counts["event_replay_event_type_totals"]
        ),
        "scenario_readback_event_replay_event_status_totals": (
            scenario_readback_counts["event_replay_event_status_totals"]
        ),
        "scenario_readback_event_replay_event_actor_totals": (
            scenario_readback_counts["event_replay_event_actor_totals"]
        ),
        "scenario_generic_readback_failed_checks": (
            scenario_readback_counts["generic_failed_checks"]
            or summary.get("generic_readback_failed_checks")
            or {}
        ),
        "scenario_projection_failed_checks": (
            scenario_readback_counts["projection_failed_checks"]
            or summary.get("projection_failed_checks")
            or {}
        ),
        "scenario_event_replay_failed_checks": (
            scenario_readback_counts["event_replay_failed_checks"]
            or summary.get("scenario_event_replay_failed_checks")
            or {}
        ),
        "scenario_readback_failed_checks": scenario_readback_counts["failed_checks"],
        "scenario_readback_task_bindings": scenario_readback_counts["task_bindings"],
        "scenario_trace_artifact_digest_matches": (
            scenario_readback_counts["trace_artifact_digest_matches"]
        ),
        "scenario_trace_artifact_bindings": (
            scenario_readback_counts["trace_artifact_bindings"]
        ),
        "scenario_artifact_summaries": scenario_readback_counts["artifact_summaries"],
        "scenario_session_summaries": scenario_readback_counts["session_summaries"],
        "scenario_readback_tool_evidence_summaries": (
            scenario_readback_counts["tool_evidence_summaries"]
        ),
        "scenario_readback_approval_evidence_summaries": (
            scenario_readback_counts["approval_evidence_summaries"]
        ),
        "scenario_readback_handoff_evidence_summaries": (
            scenario_readback_counts["handoff_evidence_summaries"]
        ),
        "scenario_readback_guardrail_evidence_summaries": (
            scenario_readback_counts["guardrail_evidence_summaries"]
        ),
        "scenario_readback_session_evidence_summaries": (
            scenario_readback_counts["session_evidence_summaries"]
        ),
        "scenario_readback_stream_evidence_summaries": (
            scenario_readback_counts["stream_evidence_summaries"]
        ),
        "scenario_readback_tool_projection_summaries": (
            scenario_readback_counts["tool_projection_summaries"]
        ),
        "scenario_stream_projection_summaries": (
            scenario_readback_counts["stream_projection_summaries"]
            or summary.get("scenario_stream_projection_summaries")
            or {}
        ),
        "scenario_readback_handoff_projection_summaries": (
            scenario_readback_counts["handoff_projection_summaries"]
        ),
        "scenario_readback_approval_projection_summaries": (
            scenario_readback_counts["approval_projection_summaries"]
        ),
        "scenario_readback_guardrail_projection_summaries": (
            scenario_readback_counts["guardrail_projection_summaries"]
        ),
        "result_count": len(results),
        "claim_boundary": (
            "Compact controller summary for an Agents SDK smoke matrix run. Full scenario traces, "
            "provider probe details, readback payloads, and per-scenario evidence remain in the "
            "full matrix JSON."
        ),
    }


def matrix_run_compact_valid(
    payload: dict[str, Any],
    summary: dict[str, Any],
    provider_probe_validation: dict[str, Any],
    matrix_readback: dict[str, Any],
    matrix_readback_validation: dict[str, Any],
    matrix_payload_readback: dict[str, Any],
    matrix_payload_readback_validation: dict[str, Any],
    matrix_payload_provider_diagnostic: dict[str, Any],
) -> bool:
    if payload.get("status") != "completed":
        return False
    if summary.get("status") not in {None, "completed"}:
        return False
    if int_value(summary.get("failed_count")) > 0:
        return False
    if list_value(summary.get("aggregate_failed_reasons")):
        return False
    if matrix_summary_has_failure_signals(summary):
        return False
    if provider_probe_validation and provider_probe_validation.get("valid") is False:
        return False
    if not matrix_compact_gate_valid(
        summary,
        matrix_readback,
        matrix_readback_validation,
        prefix="matrix_readback",
    ):
        return False
    if not matrix_compact_gate_valid(
        summary,
        matrix_payload_readback,
        matrix_payload_readback_validation,
        prefix="matrix_payload_readback",
    ):
        return False
    provider_diagnostic_valid = (
        summary.get("provider_diagnostic_readback_valid")
        if isinstance(summary.get("provider_diagnostic_readback_valid"), bool)
        else matrix_payload_provider_diagnostic.get("valid")
    )
    if provider_diagnostic_valid is False:
        return False
    return True


def matrix_summary_has_failure_signals(summary: dict[str, Any]) -> bool:
    list_fields = (
        "failed_scenarios",
        "generic_readback_failed_scenarios",
        "projection_failed_scenarios",
        "scenario_event_replay_failed_scenarios",
        "provider_probe_failed_checks",
        "provider_diagnostic_readback_failed_checks",
    )
    map_fields = (
        "scenario_failed_checks",
        "generic_readback_failed_checks",
        "projection_failed_checks",
        "scenario_event_replay_failed_checks",
    )
    if any(list_value(summary.get(field)) for field in list_fields):
        return True
    return any(isinstance(summary.get(field), dict) and bool(summary.get(field)) for field in map_fields)


def matrix_compact_gate_valid(
    summary: dict[str, Any],
    readback: dict[str, Any],
    validation: dict[str, Any],
    *,
    prefix: str,
) -> bool:
    status = summary.get(f"{prefix}_status") or readback.get("status")
    valid = (
        summary.get(f"{prefix}_valid")
        if isinstance(summary.get(f"{prefix}_valid"), bool)
        else validation.get("valid")
    )
    failed_checks = list_value(summary.get(f"{prefix}_failed_checks")) or list_value(
        validation.get("failed_checks")
    )
    if not readback and status is None and valid is None and not failed_checks:
        return True
    return status == "completed" and valid is True and not failed_checks


def matrix_artifact_compact_validation(validation: dict[str, Any]) -> dict[str, Any]:
    checks = validation.get("checks") if isinstance(validation.get("checks"), dict) else {}
    artifact = (
        validation.get("matrix_artifact")
        if isinstance(validation.get("matrix_artifact"), dict)
        else {}
    )
    return {
        "checks": {
            key: checks.get(key)
            for key in MATRIX_ARTIFACT_CHECK_KEYS
            if key in checks
        },
        "artifact": {
            "path": artifact.get("path") or "",
            "expected_path": artifact.get("expected_path") or "",
            "digest": artifact.get("digest") or "",
            "digest_from_recorded_path": artifact.get("digest_from_recorded_path"),
            "digest_from_expected_path": artifact.get("digest_from_expected_path"),
            "recorded_path_available": artifact.get("recorded_path_available"),
            "expected_path_available": artifact.get("expected_path_available"),
        },
    }


def event_replay_fetch_plan_compact_validation(validation: Any) -> dict[str, Any]:
    if not isinstance(validation, dict):
        return {"fetch_plan": {}, "checks": {}}
    checks = validation.get("checks") if isinstance(validation.get("checks"), dict) else {}
    fetch_plan = (
        validation.get("event_replay_fetch_plan")
        if isinstance(validation.get("event_replay_fetch_plan"), dict)
        else {}
    )
    compact_plan = {
        "source": fetch_plan.get("source") or "",
        "basis": fetch_plan.get("basis") or "",
        "max_events": int_value(fetch_plan.get("max_events")),
        "default_max_events": int_value(fetch_plan.get("default_max_events")),
        "buffer": int_value(fetch_plan.get("buffer")),
        "expected_raw_event_count": int_value(fetch_plan.get("expected_raw_event_count")),
        "evidence_event_floor": int_value(fetch_plan.get("evidence_event_floor")),
        "expected_event_floor": int_value(fetch_plan.get("expected_event_floor")),
    } if fetch_plan else {}
    return {
        "fetch_plan": compact_plan,
        "checks": {
            key: checks.get(key)
            for key in EVENT_REPLAY_FETCH_PLAN_CHECK_KEYS
            if key in checks
        },
    }


def event_replay_count_compact_validation(validation: Any) -> dict[str, Any]:
    if not isinstance(validation, dict):
        return {
            "event_count": 0,
            "count_map_totals": {},
            "checks": {},
            "sse_event_name_counts": {},
            "event_type_counts": {},
            "event_status_counts": {},
            "event_actor_counts": {},
        }
    event_summary = (
        validation.get("event_replay_summary")
        if isinstance(validation.get("event_replay_summary"), dict)
        else {}
    )
    count_map_totals = normalized_event_count_map(
        validation.get("event_replay_count_map_totals")
    )
    if not count_map_totals and event_summary:
        computed_totals = {
            "sse_event_name_counts": event_count_map_total(
                event_summary.get("sse_event_name_counts")
            ),
            "event_type_counts": event_count_map_total(
                event_summary.get("event_type_counts")
            ),
            "event_status_counts": event_count_map_total(
                event_summary.get("event_status_counts")
            ),
            "event_actor_counts": event_count_map_total(
                event_summary.get("event_actor_counts")
            ),
        }
        if any(computed_totals.values()):
            count_map_totals = computed_totals
    return {
        "event_count": int_value(event_summary.get("event_count")),
        "count_map_totals": count_map_totals,
        "checks": event_replay_count_total_check_map(validation),
        "sse_event_name_counts": normalized_event_count_map(
            event_summary.get("sse_event_name_counts")
        ),
        "event_type_counts": normalized_event_count_map(
            event_summary.get("event_type_counts")
        ),
        "event_status_counts": normalized_event_count_map(
            event_summary.get("event_status_counts")
        ),
        "event_actor_counts": normalized_event_count_map(
            event_summary.get("event_actor_counts")
        ),
    }


def event_replay_coverage_compact_validation(validation: Any) -> dict[str, Any]:
    if not isinstance(validation, dict):
        return {"expected_event_counts": {}, "observed_event_counts": {}}
    return {
        "expected_event_counts": normalized_event_count_map(
            validation.get("expected_event_counts")
        ),
        "observed_event_counts": normalized_event_count_map(
            validation.get("observed_event_counts")
        ),
    }


def provider_probe_summary_fields(
    summary: dict[str, Any],
    fallback_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields = provider_probe_validation_summary_fields(fallback_validation)
    for key in PROVIDER_PROBE_SUMMARY_KEYS:
        if key in summary:
            fields[key] = summary.get(key)
    return fields


def provider_probe_validation_summary_fields(validation: dict[str, Any] | None) -> dict[str, Any]:
    payload = validation if isinstance(validation, dict) else {}
    return {
        "provider_probe_valid": payload.get("valid") if "valid" in payload else None,
        "provider_probe_failed_checks": list_value(payload.get("failed_checks")),
        "provider_probe_diagnostic_id": payload.get("diagnostic_id"),
        "provider_probe_check_count": int_value(payload.get("check_count")),
        "provider_probe_checks": payload.get("checks") if isinstance(payload.get("checks"), dict) else {},
        "provider_probe_status": payload.get("status"),
        "provider_probe_http_status": payload.get("http_status"),
        "provider_probe_endpoint": payload.get("endpoint") or "",
        "provider_probe_models_preview_count": int_value(payload.get("models_preview_count")),
        "provider_probe_profile": payload.get("provider") if isinstance(payload.get("provider"), dict) else {},
        "provider_probe_agent_sdk_fit": (
            payload.get("agent_sdk_fit") if isinstance(payload.get("agent_sdk_fit"), dict) else {}
        ),
    }


def matrix_provider_diagnostic_id(matrix_payload: dict[str, Any]) -> int:
    provider_validation = (
        matrix_payload.get("provider_probe_validation")
        if isinstance(matrix_payload.get("provider_probe_validation"), dict)
        else {}
    )
    matrix_summary = (
        matrix_payload.get("summary") if isinstance(matrix_payload.get("summary"), dict) else {}
    )
    for candidate in (
        provider_validation.get("diagnostic_id"),
        matrix_summary.get("provider_probe_diagnostic_id"),
    ):
        diagnostic_id = int_value(candidate)
        if diagnostic_id > 0:
            return diagnostic_id
    return 0


def provider_diagnostic_readback(
    client_args: argparse.Namespace,
    matrix_payload: dict[str, Any],
) -> dict[str, Any]:
    expected_id = matrix_provider_diagnostic_id(matrix_payload)
    provider_validation = (
        matrix_payload.get("provider_probe_validation")
        if isinstance(matrix_payload.get("provider_probe_validation"), dict)
        else {}
    )
    if expected_id <= 0:
        return {
            "status": "not_required",
            "valid": True,
            "source": "agents_sdk_smoke_matrix_provider_diagnostic_readback",
            "expected_diagnostic_id": 0,
            "matched": False,
            "matched_diagnostic_id": 0,
            "diagnostic_count": 0,
            "check_count": 0,
            "failed_checks": [],
            "checks": {},
            "claim_boundary": (
                "No provider diagnostic id was present in the matrix payload, so stored "
                "provider diagnostic readback was not required."
            ),
        }

    try:
        diagnostics_payload = harness_api.request(
            client_args,
            "GET",
            "/llm/diagnostics?limit=200",
            None,
        )
    except SystemExit as exc:
        return provider_diagnostic_readback_error(expected_id, exc)
    except Exception as exc:  # pragma: no cover - defensive controller boundary
        return provider_diagnostic_readback_error(expected_id, exc)
    return summarize_provider_diagnostic_readback(
        diagnostics_payload,
        expected_id,
        provider_validation,
    )


def provider_diagnostic_readback_error(expected_id: int, exc: BaseException) -> dict[str, Any]:
    checks = {
        "provider_diagnostics_readback_completed": False,
        "provider_diagnostic_readback_matched_expected_id": False,
    }
    return {
        "status": "failed",
        "valid": False,
        "source": "agents_sdk_smoke_matrix_provider_diagnostic_readback",
        "expected_diagnostic_id": expected_id,
        "matched": False,
        "matched_diagnostic_id": 0,
        "diagnostic_count": 0,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "check_count": len(checks),
        "failed_checks": [name for name, passed in checks.items() if passed is not True],
        "checks": checks,
        "claim_boundary": (
            "Provider diagnostic readback failed while checking the client-scoped "
            "/llm/diagnostics read model. This does not run a new provider probe."
        ),
    }


def summarize_provider_diagnostic_readback(
    diagnostics_payload: dict[str, Any],
    expected_id: int,
    provider_validation: dict[str, Any],
) -> dict[str, Any]:
    diagnostics = (
        diagnostics_payload.get("diagnostics")
        if isinstance(diagnostics_payload.get("diagnostics"), list)
        else []
    )
    matched = next(
        (
            item
            for item in diagnostics
            if isinstance(item, dict) and int_value(item.get("diagnostic_id")) == expected_id
        ),
        {},
    )
    expected_provider = (
        provider_validation.get("provider")
        if isinstance(provider_validation.get("provider"), dict)
        else {}
    )
    matched_provider = diagnostic_provider_details(matched)
    matched_provider_request_attempted = diagnostic_result_field(matched, "provider_request_attempted")
    matched_provider_response_received = diagnostic_result_field(matched, "provider_response_received")
    matched_external_api_called = diagnostic_result_field(matched, "external_api_called")
    checks = {
        "provider_diagnostics_readback_completed": diagnostics_payload.get("status") == "completed",
        "provider_diagnostics_readback_has_diagnostics_list": isinstance(
            diagnostics_payload.get("diagnostics"),
            list,
        ),
        "provider_diagnostic_readback_matched_expected_id": bool(matched),
        "provider_diagnostic_readback_status_matches": (
            matched.get("status") == provider_validation.get("status")
            if matched and provider_validation.get("status")
            else bool(matched)
        ),
        "provider_diagnostic_readback_model_matches": (
            matched_provider.get("model") == expected_provider.get("model")
            if matched and expected_provider.get("model")
            else bool(matched)
        ),
        "provider_diagnostic_readback_api_style_matches": (
            matched_provider.get("api_style") == expected_provider.get("api_style")
            if matched and expected_provider.get("api_style")
            else bool(matched)
        ),
        "provider_diagnostic_readback_request_attempted_matches": (
            matched_provider_request_attempted is provider_validation.get("provider_request_attempted")
            if matched and isinstance(provider_validation.get("provider_request_attempted"), bool)
            else bool(matched)
        ),
        "provider_diagnostic_readback_response_received_matches": (
            matched_provider_response_received is provider_validation.get("provider_response_received")
            if matched and isinstance(provider_validation.get("provider_response_received"), bool)
            else bool(matched)
        ),
        "provider_diagnostic_readback_external_api_called_matches": (
            matched_external_api_called is provider_validation.get("external_api_called")
            if matched and isinstance(provider_validation.get("external_api_called"), bool)
            else bool(matched)
        ),
    }
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    return {
        "status": "completed" if not failed_checks else "failed",
        "valid": not failed_checks,
        "source": "agents_sdk_smoke_matrix_provider_diagnostic_readback",
        "expected_diagnostic_id": expected_id,
        "matched": bool(matched),
        "matched_diagnostic_id": int_value(matched.get("diagnostic_id")) if matched else 0,
        "matched_status": matched.get("status") if matched else None,
        "matched_provider": matched_provider,
        "matched_provider_request_attempted": matched_provider_request_attempted,
        "matched_provider_response_received": matched_provider_response_received,
        "matched_external_api_called": matched_external_api_called,
        "diagnostic_count": len(diagnostics),
        "check_count": len(checks),
        "failed_checks": failed_checks,
        "checks": checks,
        "claim_boundary": (
            "Stored-evidence matrix revalidation refetches client-scoped /llm/diagnostics "
            "and checks that the matrix provider diagnostic id is still visible. This does "
            "not run a new provider probe."
        ),
    }


def diagnostic_provider_details(diagnostic: Any) -> dict[str, Any]:
    if not isinstance(diagnostic, dict):
        return {}
    result = diagnostic.get("result") if isinstance(diagnostic.get("result"), dict) else {}
    if isinstance(diagnostic.get("provider"), dict):
        nested_provider = diagnostic.get("provider")
    elif isinstance(result.get("provider"), dict):
        nested_provider = result.get("provider")
    else:
        nested_provider = {}
    details = dict(nested_provider)
    if not details.get("provider") and isinstance(diagnostic.get("provider"), str):
        details["provider"] = diagnostic.get("provider")
    if not details.get("api_style") and isinstance(diagnostic.get("api_style"), str):
        details["api_style"] = diagnostic.get("api_style")
    if not details.get("base_url") and isinstance(diagnostic.get("base_url"), str):
        details["base_url"] = diagnostic.get("base_url")
    if not details.get("model") and isinstance(diagnostic.get("model"), str):
        details["model"] = diagnostic.get("model")
    return details


def diagnostic_result_field(diagnostic: Any, field: str) -> Any:
    if not isinstance(diagnostic, dict):
        return None
    result = diagnostic.get("result") if isinstance(diagnostic.get("result"), dict) else {}
    if field in result:
        return result.get(field)
    return diagnostic.get(field)


def normalize_scenarios(values: list[str]) -> list[str]:
    if not values or "all" in values:
        return list(DEFAULT_SCENARIOS)
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def build_plan(
    args: argparse.Namespace,
    scenarios: list[str],
    run_id: str,
    output_dir: Path,
    matrix_output: Path,
) -> dict[str, Any]:
    commands = []
    for scenario in scenarios:
        output_path = scenario_output_path(output_dir, run_id, scenario)
        commands.append(
            {
                "scenario": scenario,
                "output_json": str(output_path),
                "command": smoke_command(args, scenario, output_path),
                "publish_readback": bool(args.publish_readback),
            }
        )
    return {
        "model": args.model,
        "run_id": run_id,
        "output_dir": str(output_dir),
        "matrix_output_json": str(matrix_output),
        "matrix_evidence_json": str(aggregate_evidence_source_path(matrix_output)),
        "base_url": args.base_url,
        "client_id": args.client_id,
        "probe_provider": bool(args.probe_provider),
        "publish_readback": bool(args.publish_readback),
        "publish_matrix_readback": bool(args.publish_matrix_readback),
        "commands": commands,
    }


def run_provider_probe(client_args: argparse.Namespace, args: argparse.Namespace) -> dict[str, Any]:
    return harness_api.request(
        client_args,
        "POST",
        "/llm/probe",
        {
            "provider": {
                "provider": "openai",
                "api_style": "responses",
                "base_url": "https://api.openai.com/v1",
                "model": args.model,
                "api_key_env": "OPENAI_API_KEY",
                "supports_tool_calling": True,
                "supports_stateful_responses": True,
            },
            "timeout_seconds": 10,
        },
    )


def validate_provider_probe(provider_probe: dict[str, Any] | None) -> dict[str, Any]:
    probe = provider_probe if isinstance(provider_probe, dict) else {}
    provider = probe.get("provider") if isinstance(probe.get("provider"), dict) else {}
    agent_sdk_fit = probe.get("agent_sdk_fit") if isinstance(probe.get("agent_sdk_fit"), dict) else {}
    models_preview = list_value(probe.get("models_preview"))
    checks = {
        "probe_response_present": bool(probe),
        "probe_completed": probe.get("status") == "completed",
        "probe_http_200": int_value(probe.get("http_status")) == 200,
        "probe_provider_openai": provider.get("provider") == "openai",
        "probe_api_style_responses": provider.get("api_style") == "responses",
        "probe_provider_request_attempted": probe.get("provider_request_attempted") is True,
        "probe_provider_response_received": probe.get("provider_response_received") is True,
        "probe_external_api_called": probe.get("external_api_called") is True,
    }
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    return {
        "valid": not failed_checks,
        "source": "agents_sdk_smoke_matrix_provider_probe_validation",
        "check_count": len(checks),
        "failed_checks": failed_checks,
        "checks": checks,
        "diagnostic_id": probe.get("diagnostic_id"),
        "status": probe.get("status"),
        "http_status": probe.get("http_status"),
        "endpoint": probe.get("endpoint") or "",
        "models_preview_count": len(models_preview),
        "provider_request_attempted": probe.get("provider_request_attempted") is True,
        "provider_response_received": probe.get("provider_response_received") is True,
        "external_api_called": probe.get("external_api_called") is True,
        "provider": {
            "provider": provider.get("provider"),
            "api_style": provider.get("api_style"),
            "base_url": provider.get("base_url"),
            "model": provider.get("model"),
            "api_key_env": provider.get("api_key_env"),
            "supports_tool_calling": provider.get("supports_tool_calling"),
            "supports_stateful_responses": provider.get("supports_stateful_responses"),
        },
        "agent_sdk_fit": agent_sdk_fit,
        "claim_boundary": "Provider probe validation proves only the recorded /models probe for this matrix run.",
    }


def run_smoke_scenario(args: argparse.Namespace, scenario: str, run_id: str, output_dir: Path) -> dict[str, Any]:
    output_path = scenario_output_path(output_dir, run_id, scenario)
    command = smoke_command(args, scenario, output_path)
    completed = subprocess.run(command, text=True, capture_output=True)
    payload = read_json_if_present(output_path)
    validation = validate_smoke_result(scenario, completed.returncode, payload)
    return {
        "scenario": scenario,
        "status": payload.get("status") if isinstance(payload, dict) else "missing_output",
        "passed": validation["passed"],
        "validation": validation,
        "returncode": completed.returncode,
        "output_json": str(output_path),
        "command": command,
        "stdout_tail": tail_text(completed.stdout),
        "stderr_tail": tail_text(completed.stderr),
        "trace_id": payload.get("trace_id") if isinstance(payload, dict) else None,
        "external_api_called": payload.get("external_api_called") is True if isinstance(payload, dict) else False,
        "summary": payload.get("summary") if isinstance(payload, dict) else None,
        "tool_state_summary": (
            compact_live_tool_state(payload)
            if scenario == "tool"
            else {}
        ),
        "handoff_state_summary": compact_live_handoff_state(payload),
        "approval_state_summary": compact_live_approval_state(payload),
        "session_state_summary": compact_live_session_state(
            payload.get("session") if isinstance(payload, dict) else None,
            payload.get("summary") if isinstance(payload, dict) else None,
        ),
        "error_type": payload.get("error_type") if isinstance(payload, dict) else None,
        "error": payload.get("error") if isinstance(payload, dict) else None,
        "input_guardrail_results": (
            payload.get("input_guardrail_results") if isinstance(payload, dict) else None
        ),
        "output_guardrail_results": (
            payload.get("output_guardrail_results") if isinstance(payload, dict) else None
        ),
        "tool_input_guardrail_results": (
            payload.get("tool_input_guardrail_results") if isinstance(payload, dict) else None
        ),
        "tool_output_guardrail_results": (
            payload.get("tool_output_guardrail_results") if isinstance(payload, dict) else None
        ),
    }


def smoke_command(args: argparse.Namespace, scenario: str, output_path: Path) -> list[str]:
    command = [
        sys.executable,
        str(SMOKE_SCRIPT),
        "--scenario",
        scenario,
        "--model",
        args.model,
        "--output-json",
        str(output_path),
        "--max-turns",
        str(args.max_turns),
        "--max-tokens",
        str(args.max_tokens),
        "--marker",
        f"{args.marker_prefix}_{scenario.upper()}",
    ]
    if args.tracing_disabled:
        command.append("--tracing-disabled")
    return command


def validate_smoke_result(scenario: str, returncode: int, payload: dict[str, Any]) -> dict[str, Any]:
    expected_status = "guardrail_tripwire_triggered" if scenario == "guardrail_tripwire" else "completed"
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    checks = {
        "process_exit_ok": returncode == 0,
        "output_json_object": bool(payload),
        "status_matches_expected": payload.get("status") == expected_status,
        "external_api_called": payload.get("external_api_called") is True,
        "summary_present": bool(summary),
        "final_ok": summary.get("final_ok") is True,
        "raw_response_recorded": int_value(summary.get("raw_response_count")) >= 1,
    }
    checks.update(scenario_required_checks(scenario, payload, summary))
    if payload.get("status") == "configuration_required":
        status = "configuration_required"
    elif all(checks.values()):
        status = "completed"
    else:
        status = "failed"
    return {
        "status": status,
        "passed": status == "completed",
        "expected_status": expected_status,
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if passed is not True],
    }


def scenario_required_checks(scenario: str, payload: dict[str, Any], summary: dict[str, Any]) -> dict[str, bool]:
    if scenario == "tool":
        return {
            "tool_call_recorded": int_value(summary.get("tool_call_count")) >= 1,
            "tool_output_recorded": int_value(summary.get("tool_output_count")) >= 1,
        }
    if scenario == "handoff":
        return {
            "handoff_call_recorded": int_value(summary.get("handoff_call_count")) >= 1,
            "handoff_output_recorded": int_value(summary.get("handoff_output_count")) >= 1,
        }
    if scenario == "approval":
        return {
            "approval_request_recorded": int_value(summary.get("approval_request_count")) >= 1,
            "approval_decision_recorded": int_value(summary.get("approval_decision_count")) >= 1,
            "approval_decision_approved": int_value(dict_value(summary, "approval_decision_counts", "approved")) >= 1,
            "approval_initial_phase_recorded": "initial" in list_value(summary.get("phase_names")),
            "approval_resume_phase_recorded": "after_approval" in list_value(summary.get("phase_names")),
            "approval_interruption_recorded": int_value(
                dict_value(summary, "phase_interruption_counts", "initial")
            )
            >= 1,
            "approval_tool_call_recorded": int_value(summary.get("tool_call_count")) >= 1,
            "approval_tool_output_recorded": int_value(summary.get("tool_output_count")) >= 1,
        }
    if scenario == "guardrail":
        return {
            "guardrail_result_recorded": int_value(summary.get("output_guardrail_count")) >= 1,
            "guardrail_not_tripped": int_value(summary.get("guardrail_tripwire_count")) == 0,
        }
    if scenario == "guardrail_tripwire":
        return {
            "guardrail_tripwire_error_recorded": str(payload.get("error_type") or "")
            == "OutputGuardrailTripwireTriggered",
            "guardrail_tripwire_result_recorded": int_value(summary.get("output_guardrail_count")) >= 1,
            "guardrail_tripwire_triggered": int_value(summary.get("guardrail_tripwire_count")) >= 1,
        }
    if scenario == "session":
        return {
            "session_turns_recorded": int_value(summary.get("session_turn_count")) >= 2,
            "session_items_recorded": int_value(summary.get("session_item_count")) >= 2,
            "session_phases_recorded": int_value(summary.get("phase_count")) >= 2,
            "session_item_types_recorded": bool(summary.get("session_item_type_counts")),
        }
    if scenario == "stream":
        return {
            "stream_events_recorded": int_value(summary.get("stream_event_count")) > 0,
            "stream_raw_events_recorded": int_value(dict_value(summary, "stream_event_type_counts", "raw_response_event"))
            > 0,
            "stream_run_item_events_recorded": int_value(
                dict_value(summary, "stream_event_type_counts", "run_item_stream_event")
            )
            > 0,
            "stream_tool_name_recorded": int_value(dict_value(summary, "stream_tool_name_counts", "record_signal"))
            >= 1,
        }
    if scenario == "stream_handoff":
        return {
            "stream_handoff_events_recorded": int_value(summary.get("stream_event_count")) > 0,
            "stream_handoff_call_recorded": int_value(summary.get("handoff_call_count")) >= 1,
            "stream_handoff_output_recorded": int_value(summary.get("handoff_output_count")) >= 1,
            "stream_handoff_requested_event": int_value(
                dict_value(summary, "stream_event_name_counts", "handoff_requested")
            )
            >= 1,
            "stream_handoff_completed_event": int_value(
                dict_value(summary, "stream_event_name_counts", "handoff_occured")
            )
            >= 1,
            "stream_handoff_tool_name_recorded": int_value(
                dict_value(summary, "stream_tool_name_counts", "transfer_to_harness_specialist")
            )
            >= 1,
        }
    return {}


def publish_trace_readback(
    client_args: argparse.Namespace,
    args: argparse.Namespace,
    scenario_result: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    scenario = str(scenario_result["scenario"])
    trace_path = Path(str(scenario_result["output_json"]))
    trace = read_json_if_present(trace_path)
    task_body = {
        "prompt": (
            f"Evidence holder for Agents SDK smoke matrix scenario {scenario} in run {run_id}. "
            "Preserve trace/readback evidence for generic harness regression work; this is not "
            "FitCheck product runtime evidence."
        ),
        "cwd": args.cwd,
        "sandbox": "read-only",
        "idempotency_key": f"{args.idempotency_prefix}-{run_id}-{scenario}",
        "metadata": {
            "agentic_loop_template": "agents_sdk_trace_eval_codex",
            "source": "agents_sdk_smoke_matrix",
            "run_id": run_id,
            "scenario": scenario,
            "trace_json": str(trace_path),
        },
        "verification": {"commands": [], "timeout_seconds": 120},
    }
    created = harness_api.request(client_args, "POST", "/tasks?background=false&start=false", task_body)
    task_id = str(created.get("task_id") or "")
    if not task_id:
        return {"status": "failed", "error": "harness task creation did not return task_id", "created": created}
    actions = trace_ingest.build_actions(task_id, trace, trace_path)
    results = [
        harness_api.request(client_args, action["method"], action["path"], action.get("body"))
        for action in actions
    ]
    result_summary = trace_ingest.summarize_results(results)
    expected_trace_artifact = trace_ingest.expected_trace_artifact_from_actions(actions)
    readback = trace_ingest.fetch_readback(client_args, task_id)
    validation = trace_ingest.validate_readback(
        readback,
        result_summary,
        expected_task_id=task_id,
        expected_trace_artifact=expected_trace_artifact,
    )
    scenario_projection_validation = validate_readback_projection(scenario, readback)
    event_replay = fetch_task_event_replay(client_args, task_id, readback=readback)
    scenario_event_replay_validation = validate_trace_task_event_replay(
        event_replay,
        result_summary,
        expected_task_id=task_id,
    )
    return {
        "status": "completed"
        if validation.get("valid") is True and scenario_projection_validation.get("valid") is True
        and scenario_event_replay_validation.get("valid") is True
        else "failed",
        "task_id": task_id,
        "trace_action_count": len(actions),
        "result_summary": result_summary,
        "expected_trace_artifact": expected_trace_artifact,
        "validation": validation,
        "scenario_projection_validation": scenario_projection_validation,
        "scenario_event_replay_validation": scenario_event_replay_validation,
        "event_replay": matrix_event_replay_summary(event_replay),
        "artifact_summaries": readback.get("artifact_summaries") or [],
        "agent_session_summaries": compact_agent_session_summaries(
            readback.get("agent_session_summaries")
        ),
        "sdk_events_summary": readback.get("sdk_events_summary"),
        "client_transcript_summary": readback.get("client_transcript_summary"),
    }


def publish_matrix_readback(
    client_args: argparse.Namespace,
    args: argparse.Namespace,
    matrix_payload: dict[str, Any],
    matrix_path: Path,
    run_id: str,
) -> dict[str, Any]:
    task_body = {
        "prompt": (
            f"Evidence holder for Agents SDK smoke matrix aggregate run {run_id}. "
            "Preserve aggregate gate, provider-probe validation, scenario rollup, and readback evidence "
            "for generic harness regression work; this is not FitCheck product runtime evidence."
        ),
        "cwd": args.cwd,
        "sandbox": "read-only",
        "idempotency_key": f"{args.idempotency_prefix}-{run_id}-matrix",
        "metadata": {
            "agentic_loop_template": "agents_sdk_trace_eval_codex",
            "source": "agents_sdk_smoke_matrix",
            "run_id": run_id,
            "matrix_json": str(matrix_path),
            "matrix_status": matrix_payload.get("status"),
            "scenario_count": matrix_payload.get("scenario_count"),
            "evidence_kind": "agents_sdk_smoke_matrix_aggregate_holder",
        },
        "verification": {"commands": [], "timeout_seconds": 120},
    }
    created = harness_api.request(client_args, "POST", "/tasks?background=false&start=false", task_body)
    task_id = str(created.get("task_id") or "")
    if not task_id:
        return {"status": "failed", "error": "harness task creation did not return task_id", "created": created}
    actions = build_matrix_readback_actions(task_id, matrix_payload, matrix_path)
    results = [
        harness_api.request(client_args, action["method"], action["path"], action.get("body"))
        for action in actions
    ]
    result_summary = trace_ingest.summarize_results(results)
    evidence = harness_api.request(client_args, "GET", f"/tasks/{task_id}/evidence", None)
    readback = trace_ingest.fetch_readback(client_args, task_id)
    event_replay = fetch_task_event_replay(client_args, task_id, readback=readback)
    validation = validate_matrix_readback(
        evidence,
        readback,
        result_summary,
        matrix_payload,
        event_replay,
        expected_task_id=task_id,
    )
    return {
        "status": "completed" if validation.get("valid") is True else "failed",
        "task_id": task_id,
        "matrix_json": str(matrix_path),
        "action_count": len(actions),
        "result_summary": result_summary,
        "event_replay": matrix_event_replay_summary(event_replay),
        "validation": validation,
        "evidence_counts": readback.get("evidence_counts"),
        "sdk_events_summary": readback.get("sdk_events_summary"),
        "client_transcript_summary": readback.get("client_transcript_summary"),
    }


def validate_existing_matrix_readback(
    client_args: argparse.Namespace,
    task_id: str,
    matrix_json_path: Path,
    *,
    validate_published_scenarios: bool = False,
) -> dict[str, Any]:
    matrix_payload = read_json_if_present(matrix_json_path)
    if not matrix_payload:
        return {
            "status": "failed",
            "source": "agents_sdk_smoke_matrix_existing_readback_validation",
            "task_id": task_id,
            "matrix_json": str(matrix_json_path),
            "error": "matrix JSON could not be read as an object",
        }
    evidence = harness_api.request(client_args, "GET", f"/tasks/{task_id}/evidence", None)
    readback = trace_ingest.fetch_readback(client_args, task_id)
    result_summary = matrix_readback_result_summary(readback)
    event_replay = fetch_task_event_replay(client_args, task_id, readback=readback)
    validation = validate_matrix_readback(
        evidence,
        readback,
        result_summary,
        matrix_payload,
        event_replay,
        expected_task_id=task_id,
    )
    published_scenario_validation = (
        validate_existing_published_scenario_readbacks(client_args, matrix_payload)
        if validate_published_scenarios
        else None
    )
    provider_diagnostic_validation = provider_diagnostic_readback(client_args, matrix_payload)
    scenarios_valid = (
        published_scenario_validation.get("valid") is True
        if isinstance(published_scenario_validation, dict)
        else True
    )
    provider_diagnostic_valid = provider_diagnostic_validation.get("valid") is True
    payload = {
        "status": (
            "completed"
            if validation.get("valid") is True and scenarios_valid and provider_diagnostic_valid
            else "failed"
        ),
        "source": "agents_sdk_smoke_matrix_existing_readback_validation",
        "task_id": task_id,
        "matrix_json": str(matrix_json_path),
        "matrix_run_id": matrix_payload.get("run_id"),
        "base_url": getattr(client_args, "base_url", None),
        "client_id": getattr(client_args, "client_id", None),
        "validate_published_scenarios": validate_published_scenarios,
        "matrix_summary": matrix_payload.get("summary")
        if isinstance(matrix_payload.get("summary"), dict)
        else {},
        "validation": validation,
        "provider_diagnostic_readback": provider_diagnostic_validation,
        "published_scenario_readback_validation": published_scenario_validation,
        "published_scenario_readback_summary": published_scenario_readback_compact_summary(
            published_scenario_validation
        ),
        "result_summary": result_summary,
        "event_replay": matrix_event_replay_summary(event_replay),
        "evidence_counts": readback.get("evidence_counts"),
        "sdk_events_summary": readback.get("sdk_events_summary"),
        "client_transcript_summary": readback.get("client_transcript_summary"),
        "claim_boundary": (
            "Existing readback validation compares stored harness evidence, event replay, and "
            "read-model summaries with a supplied matrix JSON. Optional published scenario "
            "validation refetches stored per-scenario evidence-holder tasks. It does not run new "
            "live SDK or provider calls."
        ),
    }
    payload["readback_validation_summary"] = existing_matrix_readback_compact_summary(payload)
    return payload


def existing_matrix_readback_compact_summary(payload: dict[str, Any]) -> dict[str, Any]:
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    aggregate_artifact = matrix_artifact_compact_validation(validation)
    aggregate_fetch_plan = event_replay_fetch_plan_compact_validation(validation)
    aggregate_replay_counts = event_replay_count_compact_validation(validation)
    aggregate_replay_coverage = event_replay_coverage_compact_validation(validation)
    provider_probe_summary = provider_probe_summary_fields(
        payload.get("matrix_summary") if isinstance(payload.get("matrix_summary"), dict) else {},
        validation.get("provider_probe_validation")
        if isinstance(validation.get("provider_probe_validation"), dict)
        else None,
    )
    provider_diagnostic_summary = (
        payload.get("provider_diagnostic_readback")
        if isinstance(payload.get("provider_diagnostic_readback"), dict)
        else {}
    )
    matrix_summary = (
        payload.get("matrix_summary") if isinstance(payload.get("matrix_summary"), dict) else {}
    )
    scenario_summary = (
        payload.get("published_scenario_readback_summary")
        if isinstance(payload.get("published_scenario_readback_summary"), dict)
        else None
    )
    revalidation_inputs = {
        "validate_matrix_readback_task_id": payload.get("task_id"),
        "validate_matrix_json": payload.get("matrix_json"),
        "validate_published_scenarios": bool(payload.get("validate_published_scenarios")),
        "client_id": payload.get("client_id"),
        "base_url": payload.get("base_url"),
        "compact_output": True,
    }
    revalidation_readiness = revalidation_input_readiness(revalidation_inputs)
    summary_revalidation = summary_revalidation_details(
        payload.get("summary_output_json")
        or payload.get("output_json")
        or payload.get("revalidation_source_summary_json"),
        revalidation_readiness,
    )
    return {
        "status": payload.get("status"),
        "valid": payload.get("status") == "completed",
        "source": "agents_sdk_smoke_matrix_existing_readback_compact_summary",
        "task_id": payload.get("task_id"),
        "matrix_json": payload.get("matrix_json"),
        "matrix_run_id": payload.get("matrix_run_id"),
        "base_url": payload.get("base_url"),
        "client_id": payload.get("client_id"),
        "aggregate_valid": validation.get("valid"),
        "aggregate_check_count": int_value(validation.get("check_count")),
        "aggregate_failed_checks": validation.get("failed_checks") or [],
        "aggregate_task_binding": validation.get("task_binding") or {},
        "aggregate_event_replay_fetch_plan": aggregate_fetch_plan["fetch_plan"],
        "aggregate_event_replay_fetch_plan_checks": aggregate_fetch_plan["checks"],
        "aggregate_event_replay_event_count": aggregate_replay_counts["event_count"],
        "aggregate_event_replay_count_map_totals": aggregate_replay_counts["count_map_totals"],
        "aggregate_event_replay_count_total_checks": aggregate_replay_counts["checks"],
        "aggregate_event_replay_expected_event_counts": (
            aggregate_replay_coverage["expected_event_counts"]
        ),
        "aggregate_event_replay_observed_event_counts": (
            aggregate_replay_coverage["observed_event_counts"]
        ),
        "aggregate_event_replay_sse_event_name_counts": (
            aggregate_replay_counts["sse_event_name_counts"]
        ),
        "aggregate_event_replay_event_type_counts": aggregate_replay_counts["event_type_counts"],
        "aggregate_event_replay_event_status_counts": (
            aggregate_replay_counts["event_status_counts"]
        ),
        "aggregate_event_replay_event_actor_counts": (
            aggregate_replay_counts["event_actor_counts"]
        ),
        "aggregate_artifact": aggregate_artifact["artifact"],
        "aggregate_artifact_checks": aggregate_artifact["checks"],
        "provider_probe_valid": provider_probe_summary["provider_probe_valid"],
        "provider_probe_diagnostic_id": provider_probe_summary["provider_probe_diagnostic_id"],
        "provider_probe_failed_checks": provider_probe_summary["provider_probe_failed_checks"],
        "provider_probe_check_count": provider_probe_summary["provider_probe_check_count"],
        "provider_probe_checks": provider_probe_summary["provider_probe_checks"],
        "provider_probe_status": provider_probe_summary["provider_probe_status"],
        "provider_probe_http_status": provider_probe_summary["provider_probe_http_status"],
        "provider_probe_endpoint": provider_probe_summary["provider_probe_endpoint"],
        "provider_probe_models_preview_count": provider_probe_summary[
            "provider_probe_models_preview_count"
        ],
        "provider_probe_profile": provider_probe_summary["provider_probe_profile"],
        "provider_probe_agent_sdk_fit": provider_probe_summary["provider_probe_agent_sdk_fit"],
        "provider_diagnostic_readback": provider_diagnostic_summary,
        "provider_diagnostic_readback_status": provider_diagnostic_summary.get("status"),
        "provider_diagnostic_readback_valid": provider_diagnostic_summary.get("valid"),
        "provider_diagnostic_readback_failed_checks": (
            provider_diagnostic_summary.get("failed_checks") or []
        ),
        "provider_diagnostic_readback_check_count": int_value(
            provider_diagnostic_summary.get("check_count")
        ),
        "provider_diagnostic_readback_checks": (
            provider_diagnostic_summary.get("checks")
            if isinstance(provider_diagnostic_summary.get("checks"), dict)
            else {}
        ),
        "provider_diagnostic_readback_expected_id": int_value(
            provider_diagnostic_summary.get("expected_diagnostic_id")
        ),
        "provider_diagnostic_readback_matched_id": int_value(
            provider_diagnostic_summary.get("matched_diagnostic_id")
        ),
        "provider_diagnostic_readback_matched": provider_diagnostic_summary.get("matched"),
        "provider_diagnostic_readback_diagnostic_count": int_value(
            provider_diagnostic_summary.get("diagnostic_count")
        ),
        "provider_diagnostic_readback_matched_status": (
            provider_diagnostic_summary.get("matched_status")
        ),
        "provider_diagnostic_readback_matched_provider_request_attempted": (
            provider_diagnostic_summary.get("matched_provider_request_attempted")
        ),
        "provider_diagnostic_readback_matched_provider_response_received": (
            provider_diagnostic_summary.get("matched_provider_response_received")
        ),
        "provider_diagnostic_readback_matched_external_api_called": (
            provider_diagnostic_summary.get("matched_external_api_called")
        ),
        "provider_diagnostic_readback_matched_provider": (
            provider_diagnostic_summary.get("matched_provider")
            if isinstance(provider_diagnostic_summary.get("matched_provider"), dict)
            else {}
        ),
        "external_api_called": matrix_summary.get("external_api_called") is True,
        "scenario_external_api_called": matrix_summary.get("scenario_external_api_called") or {},
        "scenario_api_usage_totals": matrix_summary.get("scenario_api_usage_totals") or {},
        "api_usage_totals": matrix_summary.get("api_usage_totals") or {},
        "scenario_sdk_events_projection_counts": (
            matrix_summary.get("scenario_sdk_events_projection_counts") or {}
        ),
        "scenario_client_transcript_projection_counts": (
            matrix_summary.get("scenario_client_transcript_projection_counts") or {}
        ),
        "scenario_sdk_events_projection_maps": (
            matrix_summary.get("scenario_sdk_events_projection_maps") or {}
        ),
        "scenario_client_transcript_projection_maps": (
            matrix_summary.get("scenario_client_transcript_projection_maps") or {}
        ),
        "scenario_tool_state_summaries": (
            matrix_summary.get("scenario_tool_state_summaries") or {}
        ),
        "scenario_tool_evidence_summaries": (
            matrix_summary.get("scenario_tool_evidence_summaries") or {}
        ),
        "scenario_approval_evidence_summaries": (
            matrix_summary.get("scenario_approval_evidence_summaries") or {}
        ),
        "scenario_handoff_evidence_summaries": (
            matrix_summary.get("scenario_handoff_evidence_summaries") or {}
        ),
        "scenario_guardrail_evidence_summaries": (
            matrix_summary.get("scenario_guardrail_evidence_summaries") or {}
        ),
        "scenario_session_evidence_summaries": (
            matrix_summary.get("scenario_session_evidence_summaries") or {}
        ),
        "scenario_stream_evidence_summaries": (
            matrix_summary.get("scenario_stream_evidence_summaries") or {}
        ),
        "scenario_tool_projection_summaries": (
            matrix_summary.get("scenario_tool_projection_summaries") or {}
        ),
        "scenario_handoff_state_summaries": (
            matrix_summary.get("scenario_handoff_state_summaries") or {}
        ),
        "scenario_handoff_projection_summaries": (
            matrix_summary.get("scenario_handoff_projection_summaries") or {}
        ),
        "scenario_approval_state_summaries": (
            matrix_summary.get("scenario_approval_state_summaries") or {}
        ),
        "scenario_approval_projection_summaries": (
            matrix_summary.get("scenario_approval_projection_summaries") or {}
        ),
        "scenario_session_state_summaries": (
            matrix_summary.get("scenario_session_state_summaries") or {}
        ),
        "scenario_guardrail_tripwire_summaries": (
            matrix_summary.get("scenario_guardrail_tripwire_summaries") or {}
        ),
        "scenario_guardrail_projection_summaries": (
            matrix_summary.get("scenario_guardrail_projection_summaries") or {}
        ),
        "scenario_stream_projection_summaries": (
            matrix_summary.get("scenario_stream_projection_summaries") or {}
        ),
        "sdk_events_projection_totals": matrix_summary.get("sdk_events_projection_totals") or {},
        "client_transcript_projection_totals": (
            matrix_summary.get("client_transcript_projection_totals") or {}
        ),
        "sdk_events_projection_map_totals": (
            matrix_summary.get("sdk_events_projection_map_totals") or {}
        ),
        "client_transcript_projection_map_totals": (
            matrix_summary.get("client_transcript_projection_map_totals") or {}
        ),
        "projection_total_deltas": matrix_summary.get("projection_total_deltas") or {},
        "projection_total_delta_status": matrix_summary.get("projection_total_delta_status"),
        "projection_total_delta_assessment": (
            matrix_summary.get("projection_total_delta_assessment") or {}
        ),
        "projection_total_delta_allowed_keys": (
            matrix_summary.get("projection_total_delta_allowed_keys") or []
        ),
        "projection_total_delta_unexpected_keys": (
            matrix_summary.get("projection_total_delta_unexpected_keys") or []
        ),
        "projection_map_deltas": matrix_summary.get("projection_map_deltas") or {},
        "projection_map_delta_status": matrix_summary.get("projection_map_delta_status"),
        "projection_map_delta_assessment": (
            matrix_summary.get("projection_map_delta_assessment") or {}
        ),
        "projection_map_delta_unexpected_keys": (
            matrix_summary.get("projection_map_delta_unexpected_keys") or []
        ),
        "scenario_event_replay_expected_event_counts": (
            matrix_summary.get("scenario_event_replay_expected_event_counts") or {}
        ),
        "scenario_event_replay_observed_event_counts": (
            matrix_summary.get("scenario_event_replay_observed_event_counts") or {}
        ),
        "scenario_event_replay_event_counts": (
            matrix_summary.get("scenario_event_replay_event_counts") or {}
        ),
        "scenario_event_replay_count_map_totals": (
            matrix_summary.get("scenario_event_replay_count_map_totals") or {}
        ),
        "scenario_event_replay_count_map_total_totals": (
            matrix_summary.get("scenario_event_replay_count_map_total_totals") or {}
        ),
        "scenario_event_replay_count_total_checks": (
            matrix_summary.get("scenario_event_replay_count_total_checks") or {}
        ),
        "scenario_event_replay_sse_event_name_counts": (
            matrix_summary.get("scenario_event_replay_sse_event_name_counts") or {}
        ),
        "scenario_event_replay_event_type_counts": (
            matrix_summary.get("scenario_event_replay_event_type_counts") or {}
        ),
        "scenario_event_replay_event_status_counts": (
            matrix_summary.get("scenario_event_replay_event_status_counts") or {}
        ),
        "scenario_event_replay_event_actor_counts": (
            matrix_summary.get("scenario_event_replay_event_actor_counts") or {}
        ),
        "scenario_event_replay_sse_event_name_totals": (
            matrix_summary.get("scenario_event_replay_sse_event_name_totals") or {}
        ),
        "scenario_event_replay_event_type_totals": (
            matrix_summary.get("scenario_event_replay_event_type_totals") or {}
        ),
        "scenario_event_replay_event_status_totals": (
            matrix_summary.get("scenario_event_replay_event_status_totals") or {}
        ),
        "scenario_event_replay_event_actor_totals": (
            matrix_summary.get("scenario_event_replay_event_actor_totals") or {}
        ),
        "scenario_event_replay_expected_event_totals": (
            matrix_summary.get("scenario_event_replay_expected_event_totals") or {}
        ),
        "scenario_event_replay_observed_event_totals": (
            matrix_summary.get("scenario_event_replay_observed_event_totals") or {}
        ),
        "tool_call_status_update_expected_counts": (
            matrix_summary.get("tool_call_status_update_expected_counts") or {}
        ),
        "tool_call_status_updated_event_counts": (
            matrix_summary.get("tool_call_status_updated_event_counts") or {}
        ),
        "tool_call_status_update_expected_total": int_value(
            matrix_summary.get("tool_call_status_update_expected_total")
        ),
        "tool_call_status_updated_event_total": int_value(
            matrix_summary.get("tool_call_status_updated_event_total")
        ),
        "published_scenario_readback": scenario_summary,
        "published_scenario_readback_status": (
            scenario_summary.get("status") if isinstance(scenario_summary, dict) else None
        ),
        "published_scenario_readback_valid": (
            scenario_summary.get("valid") if isinstance(scenario_summary, dict) else None
        ),
        "published_scenario_readback_check_count": (
            int_value(scenario_summary.get("check_count")) if isinstance(scenario_summary, dict) else 0
        ),
        "published_scenario_count": (
            int_value(scenario_summary.get("published_scenario_count"))
            if isinstance(scenario_summary, dict)
            else 0
        ),
        "published_scenario_readback_status_counts": (
            scenario_summary.get("status_counts") if isinstance(scenario_summary, dict) else {}
        ),
        "published_scenario_readback_failed_scenarios": (
            scenario_summary.get("failed_scenarios") if isinstance(scenario_summary, dict) else []
        ),
        "published_scenario_readback_failed_checks": (
            scenario_summary.get("failed_checks") if isinstance(scenario_summary, dict) else {}
        ),
        "published_scenario_error_scenarios": (
            scenario_summary.get("error_scenarios") if isinstance(scenario_summary, dict) else {}
        ),
        "published_scenario_status_by_scenario": (
            scenario_summary.get("status_by_scenario") if isinstance(scenario_summary, dict) else {}
        ),
        "published_scenario_generic_check_counts": (
            scenario_summary.get("generic_check_counts") if isinstance(scenario_summary, dict) else {}
        ),
        "published_scenario_projection_check_counts": (
            scenario_summary.get("projection_check_counts") if isinstance(scenario_summary, dict) else {}
        ),
        "published_scenario_event_replay_check_counts": (
            scenario_summary.get("event_replay_check_counts") if isinstance(scenario_summary, dict) else {}
        ),
        "published_scenario_total_check_counts": (
            scenario_summary.get("total_check_counts") if isinstance(scenario_summary, dict) else {}
        ),
        "published_scenario_external_api_called": (
            scenario_summary.get("external_api_called_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_fetch_plans": (
            scenario_summary.get("event_replay_fetch_plans")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_fetch_plan_checks": (
            scenario_summary.get("event_replay_fetch_plan_checks")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_event_counts": (
            scenario_summary.get("event_replay_event_counts")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_expected_event_counts": (
            scenario_summary.get("event_replay_expected_event_counts")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_observed_event_counts": (
            scenario_summary.get("event_replay_observed_event_counts")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_expected_event_totals": (
            scenario_summary.get("event_replay_expected_event_totals")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_observed_event_totals": (
            scenario_summary.get("event_replay_observed_event_totals")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_count_map_totals": (
            scenario_summary.get("event_replay_count_map_totals")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_count_map_total_totals": (
            scenario_summary.get("event_replay_count_map_total_totals")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_count_total_checks": (
            scenario_summary.get("event_replay_count_total_checks")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_sse_event_name_counts": (
            scenario_summary.get("event_replay_sse_event_name_counts")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_event_type_counts": (
            scenario_summary.get("event_replay_event_type_counts")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_event_status_counts": (
            scenario_summary.get("event_replay_event_status_counts")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_event_actor_counts": (
            scenario_summary.get("event_replay_event_actor_counts")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_sse_event_name_totals": (
            scenario_summary.get("event_replay_sse_event_name_totals")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_event_type_totals": (
            scenario_summary.get("event_replay_event_type_totals")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_event_status_totals": (
            scenario_summary.get("event_replay_event_status_totals")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_event_replay_event_actor_totals": (
            scenario_summary.get("event_replay_event_actor_totals")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_generic_failed_checks": (
            scenario_summary.get("generic_failed_checks") if isinstance(scenario_summary, dict) else {}
        ),
        "published_scenario_projection_failed_checks": (
            scenario_summary.get("projection_failed_checks") if isinstance(scenario_summary, dict) else {}
        ),
        "published_scenario_event_replay_failed_checks": (
            scenario_summary.get("event_replay_failed_checks") if isinstance(scenario_summary, dict) else {}
        ),
        "published_scenario_stage_failed_checks": (
            scenario_summary.get("stage_failed_checks") if isinstance(scenario_summary, dict) else {}
        ),
        "published_scenario_task_bindings": (
            scenario_summary.get("task_bindings") if isinstance(scenario_summary, dict) else {}
        ),
        "published_scenario_trace_artifact_digest_matches": (
            scenario_summary.get("trace_artifact_digest_matches")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_trace_artifact_bindings": (
            scenario_summary.get("trace_artifact_bindings")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_artifact_summaries": (
            scenario_summary.get("artifact_summaries_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_session_summaries": (
            scenario_summary.get("session_summaries_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_tool_evidence_summaries": (
            scenario_summary.get("tool_evidence_summaries_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_approval_evidence_summaries": (
            scenario_summary.get("approval_evidence_summaries_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_handoff_evidence_summaries": (
            scenario_summary.get("handoff_evidence_summaries_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_guardrail_evidence_summaries": (
            scenario_summary.get("guardrail_evidence_summaries_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_session_evidence_summaries": (
            scenario_summary.get("session_evidence_summaries_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_stream_evidence_summaries": (
            scenario_summary.get("stream_evidence_summaries_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_tool_projection_summaries": (
            scenario_summary.get("tool_projection_summaries_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_stream_projection_summaries": (
            scenario_summary.get("stream_projection_summaries_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_handoff_projection_summaries": (
            scenario_summary.get("handoff_projection_summaries_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_approval_projection_summaries": (
            scenario_summary.get("approval_projection_summaries_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_guardrail_projection_summaries": (
            scenario_summary.get("guardrail_projection_summaries_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_sdk_events_projection_maps": (
            scenario_summary.get("sdk_events_projection_maps_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_client_transcript_projection_maps": (
            scenario_summary.get("client_transcript_projection_maps_by_scenario")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_sdk_events_projection_map_totals": (
            scenario_summary.get("sdk_events_projection_map_totals")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "published_scenario_client_transcript_projection_map_totals": (
            scenario_summary.get("client_transcript_projection_map_totals")
            if isinstance(scenario_summary, dict)
            else {}
        ),
        "revalidation_inputs": revalidation_inputs,
        "revalidation_ready": revalidation_readiness["ready"],
        "revalidation_missing_fields": revalidation_readiness["missing_fields"],
        "revalidation_required_fields": revalidation_readiness["required_fields"],
        "revalidation_cli_args": revalidation_cli_args(revalidation_inputs),
        "summary_revalidation_inputs": summary_revalidation["inputs"],
        "summary_revalidation_ready": summary_revalidation["ready"],
        "summary_revalidation_missing_fields": summary_revalidation["missing_fields"],
        "summary_revalidation_cli_args": summary_revalidation["cli_args"],
        "revalidation_source_summary_json": payload.get("revalidation_source_summary_json"),
        "revalidation_source_summary_key": payload.get("revalidation_source_summary_key"),
        "revalidation_source_ready": payload.get("revalidation_source_ready"),
        "eval_case_recording": (
            payload.get("eval_case_recording")
            if isinstance(payload.get("eval_case_recording"), dict)
            else {}
        ),
        "claim_boundary": (
            "Compact controller summary for existing matrix readback validation. Full stored "
            "evidence, read-model summaries, and per-scenario details remain in the full payload "
            "or --output-json file."
        ),
    }


def validate_existing_published_scenario_readbacks(
    client_args: argparse.Namespace,
    matrix_payload: dict[str, Any],
) -> dict[str, Any]:
    scenario_results: list[dict[str, Any]] = []
    for item in list_value(matrix_payload.get("results")):
        if not isinstance(item, dict):
            continue
        scenario = str(item.get("scenario") or "unknown")
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        task_id = str(publish_readback.get("task_id") or "")
        result_summary = (
            publish_readback.get("result_summary")
            if isinstance(publish_readback.get("result_summary"), dict)
            else {}
        )
        expected_trace_artifact = expected_trace_artifact_from_published_scenario(
            item,
            publish_readback,
        )
        if not task_id:
            scenario_results.append(
                {
                    "scenario": scenario,
                    "status": "not_published",
                    "valid": False,
                    "task_id": "",
                    "failed_checks": ["published_scenario_task_id_present"],
                    "claim_boundary": (
                        "Scenario readback revalidation requires publish_readback.task_id in the "
                        "matrix JSON."
                    ),
                }
            )
            continue

        try:
            readback = trace_ingest.fetch_readback(client_args, task_id)
            validation = trace_ingest.validate_readback(
                readback,
                result_summary,
                expected_task_id=task_id,
                expected_trace_artifact=expected_trace_artifact or None,
            )
            scenario_projection_validation = validate_readback_projection(scenario, readback)
            event_replay = fetch_task_event_replay(client_args, task_id, readback=readback)
            scenario_event_replay_validation = validate_trace_task_event_replay(
                event_replay,
                result_summary,
                expected_task_id=task_id,
            )
        except SystemExit as exc:
            scenario_results.append(
                published_scenario_readback_error_result(
                    scenario,
                    task_id,
                    result_summary,
                    exc,
                )
            )
            continue
        except Exception as exc:  # pragma: no cover - defensive controller boundary
            scenario_results.append(
                published_scenario_readback_error_result(
                    scenario,
                    task_id,
                    result_summary,
                    exc,
                )
            )
            continue
        failed_checks = published_scenario_revalidation_failed_checks(
            validation,
            scenario_projection_validation,
            scenario_event_replay_validation,
        )
        scenario_results.append(
            {
                "scenario": scenario,
                "status": "completed" if not failed_checks else "failed",
                "valid": not failed_checks,
                "task_id": task_id,
                "failed_checks": failed_checks,
                "result_summary": result_summary,
                "expected_trace_artifact": expected_trace_artifact,
                "validation": validation,
                "scenario_projection_validation": scenario_projection_validation,
                "scenario_event_replay_validation": scenario_event_replay_validation,
                "event_replay": matrix_event_replay_summary(event_replay),
                "artifact_summaries": readback.get("artifact_summaries") or [],
                "agent_session_summaries": compact_agent_session_summaries(
                    readback.get("agent_session_summaries")
                ),
                "evidence_counts": readback.get("evidence_counts"),
                "sdk_events_summary": readback.get("sdk_events_summary"),
                "client_transcript_summary": readback.get("client_transcript_summary"),
            }
        )

    published_count = sum(1 for item in scenario_results if item.get("task_id"))
    failed_scenarios = [
        str(item.get("scenario") or "unknown")
        for item in scenario_results
        if item.get("valid") is not True
    ]
    failed_checks_by_scenario = {
        str(item.get("scenario") or "unknown"): list_value(item.get("failed_checks"))
        for item in scenario_results
        if list_value(item.get("failed_checks"))
    }
    check_count = sum(
        int_value(
            (
                (item.get("validation") or {})
                if isinstance(item.get("validation"), dict)
                else {}
            ).get("check_count")
        )
        + int_value(
            (
                (item.get("scenario_projection_validation") or {})
                if isinstance(item.get("scenario_projection_validation"), dict)
                else {}
            ).get("check_count")
        )
        + int_value(
            (
                (item.get("scenario_event_replay_validation") or {})
                if isinstance(item.get("scenario_event_replay_validation"), dict)
                else {}
            ).get("check_count")
        )
        + (1 if item.get("status") == "not_published" else 0)
        for item in scenario_results
    )
    valid = bool(scenario_results) and published_count == len(scenario_results) and not failed_scenarios
    payload = {
        "status": "completed" if valid else "failed",
        "valid": valid,
        "source": "agents_sdk_smoke_matrix_published_scenario_readback_revalidation",
        "scenario_count": len(scenario_results),
        "published_scenario_count": published_count,
        "status_counts": count_values(str(item.get("status") or "unknown") for item in scenario_results),
        "check_count": check_count,
        "failed_scenarios": failed_scenarios,
        "failed_checks": failed_checks_by_scenario,
        "task_ids": {
            str(item.get("scenario") or "unknown"): item.get("task_id")
            for item in scenario_results
            if item.get("task_id")
        },
        "results": scenario_results,
        "claim_boundary": (
            "Published scenario revalidation refetches stored per-scenario evidence-holder tasks, "
            "SDK-event summaries, client-transcript summaries, and raw event replay. It does not "
            "run new live SDK or provider calls."
        ),
    }
    payload["summary"] = published_scenario_readback_compact_summary(payload)
    return payload


def expected_trace_artifact_from_published_scenario(
    scenario_result: dict[str, Any],
    publish_readback: dict[str, Any],
) -> dict[str, Any]:
    recorded = (
        publish_readback.get("expected_trace_artifact")
        if isinstance(publish_readback.get("expected_trace_artifact"), dict)
        else {}
    )
    if str(recorded.get("digest") or "").startswith("sha256:"):
        return recorded

    trace_path_text = str(scenario_result.get("output_json") or "").strip()
    if not trace_path_text:
        return {}
    trace_path = Path(trace_path_text)
    if not trace_path.exists():
        return {}
    trace = read_json_if_present(trace_path)
    task_id = str(publish_readback.get("task_id") or "task_trace_revalidation")
    actions = trace_ingest.build_actions(task_id, trace, trace_path)
    return trace_ingest.expected_trace_artifact_from_actions(actions)


def published_scenario_readback_error_result(
    scenario: str,
    task_id: str,
    result_summary: dict[str, Any],
    exc: BaseException,
) -> dict[str, Any]:
    error_type = type(exc).__name__
    return {
        "scenario": scenario,
        "status": "failed",
        "valid": False,
        "task_id": task_id,
        "failed_checks": [f"readback_error:{error_type}"],
        "result_summary": result_summary,
        "error_type": error_type,
        "error": str(exc),
        "claim_boundary": (
            "Scenario readback revalidation failed while fetching or validating stored harness "
            "evidence. The failure is recorded as scenario evidence instead of aborting the whole "
            "matrix validation command."
        ),
    }


def published_scenario_readback_compact_summary(validation: Any) -> dict[str, Any] | None:
    if not isinstance(validation, dict):
        return None
    results = list_value(validation.get("results"))
    generic_check_counts: dict[str, int] = {}
    projection_check_counts: dict[str, int] = {}
    event_replay_check_counts: dict[str, int] = {}
    total_check_counts: dict[str, int] = {}
    external_api_called_by_scenario: dict[str, bool] = {}
    event_replay_fetch_plans: dict[str, dict[str, Any]] = {}
    event_replay_fetch_plan_checks: dict[str, dict[str, Any]] = {}
    event_replay_sse_event_name_counts: dict[str, dict[str, int]] = {}
    event_replay_event_type_counts: dict[str, dict[str, int]] = {}
    event_replay_event_status_counts: dict[str, dict[str, int]] = {}
    event_replay_event_actor_counts: dict[str, dict[str, int]] = {}
    event_replay_event_counts: dict[str, int] = {}
    event_replay_expected_event_counts: dict[str, dict[str, int]] = {}
    event_replay_observed_event_counts: dict[str, dict[str, int]] = {}
    event_replay_count_map_totals: dict[str, dict[str, int]] = {}
    event_replay_count_total_checks: dict[str, dict[str, bool]] = {}
    generic_failed_checks: dict[str, list[Any]] = {}
    projection_failed_checks: dict[str, list[Any]] = {}
    event_replay_failed_checks: dict[str, list[Any]] = {}
    stage_failed_checks: dict[str, list[Any]] = {}
    task_bindings: dict[str, dict[str, Any]] = {}
    trace_artifact_digest_matches: dict[str, bool] = {}
    trace_artifact_bindings: dict[str, dict[str, Any]] = {}
    status_by_scenario: dict[str, Any] = {}
    artifact_summaries: dict[str, list[Any]] = {}
    session_summaries: dict[str, list[Any]] = {}
    tool_evidence_summaries: dict[str, dict[str, Any]] = {}
    approval_evidence_summaries: dict[str, dict[str, Any]] = {}
    handoff_evidence_summaries: dict[str, dict[str, Any]] = {}
    guardrail_evidence_summaries: dict[str, dict[str, Any]] = {}
    session_evidence_summaries: dict[str, dict[str, Any]] = {}
    stream_evidence_summaries: dict[str, dict[str, Any]] = {}
    tool_projection_summaries: dict[str, dict[str, Any]] = {}
    stream_projection_summaries: dict[str, dict[str, Any]] = {}
    handoff_projection_summaries: dict[str, dict[str, Any]] = {}
    guardrail_projection_summaries: dict[str, dict[str, Any]] = {}
    approval_projection_summaries: dict[str, dict[str, Any]] = {}
    sdk_events_projection_maps: dict[str, dict[str, dict[str, int]]] = {}
    client_transcript_projection_maps: dict[str, dict[str, dict[str, int]]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        scenario = str(item.get("scenario") or "unknown")
        status_by_scenario[scenario] = item.get("status")
        generic_validation = item.get("validation") if isinstance(item.get("validation"), dict) else {}
        projection_validation = (
            item.get("scenario_projection_validation")
            if isinstance(item.get("scenario_projection_validation"), dict)
            else {}
        )
        event_replay_validation = (
            item.get("scenario_event_replay_validation")
            if isinstance(item.get("scenario_event_replay_validation"), dict)
            else {}
        )
        generic_check_counts[scenario] = int_value(generic_validation.get("check_count"))
        projection_check_counts[scenario] = int_value(projection_validation.get("check_count"))
        event_replay_check_counts[scenario] = int_value(event_replay_validation.get("check_count"))
        total_check_counts[scenario] = (
            generic_check_counts[scenario]
            + projection_check_counts[scenario]
            + event_replay_check_counts[scenario]
        )
        event_replay_fetch_plan = event_replay_fetch_plan_compact_validation(event_replay_validation)
        if event_replay_fetch_plan["fetch_plan"]:
            event_replay_fetch_plans[scenario] = event_replay_fetch_plan["fetch_plan"]
        if event_replay_fetch_plan["checks"]:
            event_replay_fetch_plan_checks[scenario] = event_replay_fetch_plan["checks"]
        event_replay_summary = (
            event_replay_validation.get("event_replay_summary")
            if isinstance(event_replay_validation.get("event_replay_summary"), dict)
            else {}
        )
        event_replay_event_counts[scenario] = int_value(event_replay_summary.get("event_count"))
        expected_event_counts = normalized_event_count_map(
            event_replay_validation.get("expected_event_counts")
        )
        observed_event_counts = normalized_event_count_map(
            event_replay_validation.get("observed_event_counts")
        )
        if expected_event_counts:
            event_replay_expected_event_counts[scenario] = expected_event_counts
        if observed_event_counts:
            event_replay_observed_event_counts[scenario] = observed_event_counts
        count_map_totals = normalized_event_count_map(
            event_replay_validation.get("event_replay_count_map_totals")
        )
        if count_map_totals:
            event_replay_count_map_totals[scenario] = count_map_totals
        count_total_checks = event_replay_count_total_check_map(event_replay_validation)
        if count_total_checks:
            event_replay_count_total_checks[scenario] = count_total_checks
        sse_event_counts = normalized_event_count_map(
            event_replay_summary.get("sse_event_name_counts")
        )
        event_type_counts = normalized_event_count_map(
            event_replay_summary.get("event_type_counts")
        )
        event_status_counts = normalized_event_count_map(
            event_replay_summary.get("event_status_counts")
        )
        event_actor_counts = normalized_event_count_map(
            event_replay_summary.get("event_actor_counts")
        )
        if sse_event_counts:
            event_replay_sse_event_name_counts[scenario] = sse_event_counts
        if event_type_counts:
            event_replay_event_type_counts[scenario] = event_type_counts
        if event_status_counts:
            event_replay_event_status_counts[scenario] = event_status_counts
        if event_actor_counts:
            event_replay_event_actor_counts[scenario] = event_actor_counts
        generic_failures = list_value(generic_validation.get("failed_checks"))
        projection_failures = list_value(projection_validation.get("failed_checks"))
        event_replay_failures = list_value(event_replay_validation.get("failed_checks"))
        if generic_failures:
            generic_failed_checks[scenario] = generic_failures
        if projection_failures:
            projection_failed_checks[scenario] = projection_failures
        if event_replay_failures:
            event_replay_failed_checks[scenario] = event_replay_failures
        combined_failures = list_value(item.get("failed_checks"))
        if combined_failures:
            stage_failed_checks[scenario] = combined_failures
        task_binding = generic_validation.get("task_binding")
        if isinstance(task_binding, dict):
            task_bindings[scenario] = task_binding
        trace_artifact_binding = generic_validation.get("trace_artifact_binding")
        if isinstance(trace_artifact_binding, dict):
            trace_artifact_bindings[scenario] = trace_artifact_binding
        checks = generic_validation.get("checks")
        if isinstance(checks, dict) and "agent_trace_artifact_digest_matches_expected" in checks:
            trace_artifact_digest_matches[scenario] = (
                checks.get("agent_trace_artifact_digest_matches_expected") is True
            )
        artifacts = list_value(item.get("artifact_summaries"))
        if artifacts:
            artifact_summaries[scenario] = artifacts
        external_api_called = artifact_summaries_external_api_called(artifacts)
        if external_api_called is not None:
            external_api_called_by_scenario[scenario] = external_api_called
        sessions = compact_agent_session_summaries(item.get("agent_session_summaries"))
        if sessions:
            session_summaries[scenario] = sessions
        if scenario == "tool":
            tool_evidence_summary = compact_tool_evidence_summary(item)
            if tool_evidence_summary:
                tool_evidence_summaries[scenario] = tool_evidence_summary
            tool_summary = compact_tool_projection_summary(item)
            if tool_summary:
                tool_projection_summaries[scenario] = tool_summary
        if scenario == "approval":
            approval_evidence_summary = compact_approval_evidence_summary(item)
            if approval_evidence_summary:
                approval_evidence_summaries[scenario] = approval_evidence_summary
        if scenario in {"handoff", "stream_handoff"}:
            handoff_evidence_summary = compact_handoff_evidence_summary(item)
            if handoff_evidence_summary:
                handoff_evidence_summaries[scenario] = handoff_evidence_summary
        if scenario in {"guardrail", "guardrail_tripwire"}:
            guardrail_evidence_summary = compact_guardrail_evidence_summary(
                item,
                tripwire_expected=scenario == "guardrail_tripwire",
            )
            if guardrail_evidence_summary:
                guardrail_evidence_summaries[scenario] = guardrail_evidence_summary
        if scenario == "session":
            session_evidence_summary = compact_session_evidence_summary(item)
            if session_evidence_summary:
                session_evidence_summaries[scenario] = session_evidence_summary
        if scenario in {"stream", "stream_handoff"}:
            stream_evidence_summary = compact_stream_evidence_summary(
                item,
                expected_tool_name=(
                    "transfer_to_harness_specialist"
                    if scenario == "stream_handoff"
                    else "record_signal"
                ),
                handoff_expected=scenario == "stream_handoff",
            )
            if stream_evidence_summary:
                stream_evidence_summaries[scenario] = stream_evidence_summary
        stream_summary = compact_stream_projection_summary(item)
        if stream_summary:
            stream_projection_summaries[scenario] = stream_summary
        handoff_projection_summary = compact_handoff_projection_summary(item)
        if handoff_projection_summary:
            handoff_projection_summaries[scenario] = handoff_projection_summary
        guardrail_projection_summary = compact_guardrail_projection_summary(item)
        if guardrail_projection_summary:
            guardrail_projection_summaries[scenario] = guardrail_projection_summary
        approval_projection_summary = compact_approval_projection_summary(item)
        if approval_projection_summary:
            approval_projection_summaries[scenario] = approval_projection_summary
        sdk_projection_map = projection_map_summary(item.get("sdk_events_summary"))
        if sdk_projection_map:
            sdk_events_projection_maps[scenario] = sdk_projection_map
        transcript_projection_map = projection_map_summary(item.get("client_transcript_summary"))
        if transcript_projection_map:
            client_transcript_projection_maps[scenario] = transcript_projection_map
    error_scenarios = {
        str(item.get("scenario") or "unknown"): {
            "task_id": item.get("task_id"),
            "error_type": item.get("error_type"),
            "error": item.get("error"),
        }
        for item in results
        if isinstance(item, dict) and item.get("error")
    }
    return {
        "status": validation.get("status"),
        "valid": validation.get("valid"),
        "scenario_count": int_value(validation.get("scenario_count")),
        "published_scenario_count": int_value(validation.get("published_scenario_count")),
        "status_counts": validation.get("status_counts") or {},
        "check_count": int_value(validation.get("check_count")),
        "failed_scenarios": validation.get("failed_scenarios") or [],
        "failed_checks": validation.get("failed_checks") or {},
        "task_ids": validation.get("task_ids") or {},
        "status_by_scenario": status_by_scenario,
        "generic_check_counts": generic_check_counts,
        "projection_check_counts": projection_check_counts,
        "event_replay_check_counts": event_replay_check_counts,
        "total_check_counts": total_check_counts,
        "external_api_called_by_scenario": external_api_called_by_scenario,
        "event_replay_fetch_plans": event_replay_fetch_plans,
        "event_replay_fetch_plan_checks": event_replay_fetch_plan_checks,
        "event_replay_sse_event_name_counts": event_replay_sse_event_name_counts,
        "event_replay_event_type_counts": event_replay_event_type_counts,
        "event_replay_event_status_counts": event_replay_event_status_counts,
        "event_replay_event_actor_counts": event_replay_event_actor_counts,
        "event_replay_event_counts": event_replay_event_counts,
        "event_replay_expected_event_counts": event_replay_expected_event_counts,
        "event_replay_observed_event_counts": event_replay_observed_event_counts,
        "event_replay_expected_event_totals": event_count_totals(
            event_replay_expected_event_counts
        ),
        "event_replay_observed_event_totals": event_count_totals(
            event_replay_observed_event_counts
        ),
        "event_replay_count_map_totals": event_replay_count_map_totals,
        "event_replay_count_map_total_totals": event_count_totals(
            event_replay_count_map_totals
        ),
        "event_replay_count_total_checks": event_replay_count_total_checks,
        "event_replay_sse_event_name_totals": event_count_totals(
            event_replay_sse_event_name_counts
        ),
        "event_replay_event_type_totals": event_count_totals(event_replay_event_type_counts),
        "event_replay_event_status_totals": event_count_totals(event_replay_event_status_counts),
        "event_replay_event_actor_totals": event_count_totals(event_replay_event_actor_counts),
        "generic_failed_checks": generic_failed_checks,
        "projection_failed_checks": projection_failed_checks,
        "event_replay_failed_checks": event_replay_failed_checks,
        "stage_failed_checks": stage_failed_checks,
        "task_bindings": task_bindings,
        "trace_artifact_digest_matches": trace_artifact_digest_matches,
        "trace_artifact_bindings": trace_artifact_bindings,
        "artifact_summaries_by_scenario": artifact_summaries,
        "session_summaries_by_scenario": session_summaries,
        "tool_evidence_summaries_by_scenario": tool_evidence_summaries,
        "approval_evidence_summaries_by_scenario": approval_evidence_summaries,
        "handoff_evidence_summaries_by_scenario": handoff_evidence_summaries,
        "guardrail_evidence_summaries_by_scenario": guardrail_evidence_summaries,
        "session_evidence_summaries_by_scenario": session_evidence_summaries,
        "stream_evidence_summaries_by_scenario": stream_evidence_summaries,
        "tool_projection_summaries_by_scenario": tool_projection_summaries,
        "stream_projection_summaries_by_scenario": stream_projection_summaries,
        "handoff_projection_summaries_by_scenario": handoff_projection_summaries,
        "approval_projection_summaries_by_scenario": approval_projection_summaries,
        "guardrail_projection_summaries_by_scenario": guardrail_projection_summaries,
        "sdk_events_projection_maps_by_scenario": sdk_events_projection_maps,
        "client_transcript_projection_maps_by_scenario": client_transcript_projection_maps,
        "sdk_events_projection_map_totals": projection_map_totals(
            sdk_events_projection_maps.values()
        ),
        "client_transcript_projection_map_totals": projection_map_totals(
            client_transcript_projection_maps.values()
        ),
        "error_scenarios": error_scenarios,
        "claim_boundary": (
            "Compact controller summary for published scenario readback revalidation; detailed "
            "per-scenario evidence remains under published_scenario_readback_validation.results."
        ),
    }


def published_scenario_revalidation_failed_checks(
    validation: dict[str, Any],
    projection_validation: dict[str, Any],
    event_replay_validation: dict[str, Any],
) -> list[str]:
    failed_checks: list[str] = []
    if validation.get("valid") is not True:
        failed_checks.extend(f"generic:{name}" for name in list_value(validation.get("failed_checks")))
    if projection_validation.get("valid") is not True:
        failed_checks.extend(f"projection:{name}" for name in list_value(projection_validation.get("failed_checks")))
    if event_replay_validation.get("valid") is not True:
        failed_checks.extend(
            f"event_replay:{name}" for name in list_value(event_replay_validation.get("failed_checks"))
        )
    return failed_checks


def validate_published_matrix_payload_readback(
    client_args: argparse.Namespace,
    task_id: str,
    matrix_payload: dict[str, Any],
) -> dict[str, Any]:
    if not task_id:
        return {
            "status": "failed",
            "source": "agents_sdk_smoke_matrix_published_payload_readback_validation",
            "task_id": task_id,
            "error": "matrix readback task_id is missing",
        }
    evidence = harness_api.request(client_args, "GET", f"/tasks/{task_id}/evidence", None)
    readback = trace_ingest.fetch_readback(client_args, task_id)
    result_summary = matrix_readback_result_summary(readback)
    event_replay = fetch_task_event_replay(client_args, task_id, readback=readback)
    validation = validate_matrix_readback(
        evidence,
        readback,
        result_summary,
        matrix_payload,
        event_replay,
        expected_task_id=task_id,
    )
    provider_diagnostic_validation = provider_diagnostic_readback(client_args, matrix_payload)
    valid = validation.get("valid") is True and provider_diagnostic_validation.get("valid") is True
    return {
        "status": "completed" if valid else "failed",
        "source": "agents_sdk_smoke_matrix_published_payload_readback_validation",
        "task_id": task_id,
        "matrix_run_id": matrix_payload.get("run_id"),
        "validation": validation,
        "provider_diagnostic_readback": provider_diagnostic_validation,
        "result_summary": result_summary,
        "event_replay": matrix_event_replay_summary(event_replay),
        "evidence_counts": readback.get("evidence_counts"),
        "sdk_events_summary": readback.get("sdk_events_summary"),
        "client_transcript_summary": readback.get("client_transcript_summary"),
        "claim_boundary": (
            "Publish-time final payload validation confirms the finalized matrix JSON binds the "
            "aggregate readback task id, stored evidence, event replay, and read-model summaries "
            "and provider diagnostic read model without requiring a separate revalidation command."
        ),
    }


def matrix_readback_result_summary(readback: dict[str, Any]) -> dict[str, Any]:
    evidence_counts = readback.get("evidence_counts") if isinstance(readback.get("evidence_counts"), dict) else {}
    return {
        "status": "completed",
        "source": "agents_sdk_smoke_matrix_existing_readback_result_summary",
        "artifact_count": int_value(evidence_counts.get("artifacts")),
        "observation_count": int_value(evidence_counts.get("observations")),
        "tool_call_count": int_value(evidence_counts.get("tool_calls")),
        "artifact_kind_counts": evidence_counts.get("artifact_kind_counts") or {},
        "observation_tool_counts": evidence_counts.get("observation_tool_counts") or {},
        "tool_call_name_counts": evidence_counts.get("tool_call_name_counts") or {},
        "claim_boundary": (
            "Derived from current task evidence counts for validating an already-published matrix "
            "aggregate task."
        ),
    }


def matrix_event_replay_fetch_plan(readback: dict[str, Any] | None) -> dict[str, Any]:
    readback_payload = readback if isinstance(readback, dict) else {}
    evidence_counts = (
        readback_payload.get("evidence_counts")
        if isinstance(readback_payload.get("evidence_counts"), dict)
        else {}
    )
    sdk_events_summary = (
        readback_payload.get("sdk_events_summary")
        if isinstance(readback_payload.get("sdk_events_summary"), dict)
        else {}
    )
    plan = trace_ingest.event_replay_fetch_plan(evidence_counts, sdk_events_summary)
    return {
        **plan,
        "source": "agents_sdk_smoke_matrix_event_replay_fetch_plan",
        "claim_boundary": (
            "Replay fetch size is derived from matrix readback summaries so aggregate and scenario "
            "event replay validation is not truncated by a fixed SSE event limit."
        ),
    }


def fetch_task_event_replay(
    client_args: argparse.Namespace,
    task_id: str,
    *,
    readback: dict[str, Any] | None = None,
    max_events: int | None = None,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    fetch_plan = matrix_event_replay_fetch_plan(readback) if readback is not None else {}
    resolved_max_events = (
        int(max_events)
        if max_events is not None
        else int(fetch_plan.get("max_events") or trace_ingest.DEFAULT_EVENT_REPLAY_MAX_EVENTS)
    )
    event_args = argparse.Namespace(
        base_url=client_args.base_url,
        client_id=client_args.client_id,
        api_key=client_args.api_key,
        dry_run=False,
        task_id=task_id,
        after=0,
        max_events=resolved_max_events,
        timeout_seconds=timeout_seconds,
        include_keepalive="false",
    )
    replay = harness_api.request(event_args, "SSE", f"/tasks/{task_id}/events?after=0", None)
    if fetch_plan and isinstance(replay, dict):
        replay["fetch_plan"] = fetch_plan
    return replay


def matrix_event_replay_summary(event_replay: dict[str, Any] | None) -> dict[str, Any]:
    replay = event_replay if isinstance(event_replay, dict) else {}
    events = replay.get("events") if isinstance(replay.get("events"), list) else []
    sse_event_name_counts = count_values(
        (item.get("event") if isinstance(item, dict) else None)
        for item in events
    ) if not isinstance(replay.get("sse_event_name_counts"), dict) else normalized_event_count_map(replay.get("sse_event_name_counts"))
    event_type_counts = count_values(
        ((item.get("data") or {}).get("event_type") if isinstance(item, dict) and isinstance(item.get("data"), dict) else None)
        for item in events
    ) if not isinstance(replay.get("event_type_counts"), dict) else normalized_event_count_map(replay.get("event_type_counts"))
    event_status_counts = count_values(
        ((item.get("data") or {}).get("status") if isinstance(item, dict) and isinstance(item.get("data"), dict) else None)
        for item in events
    ) if not isinstance(replay.get("event_status_counts"), dict) else normalized_event_count_map(replay.get("event_status_counts"))
    event_actor_counts = count_values(
        ((item.get("data") or {}).get("actor") if isinstance(item, dict) and isinstance(item.get("data"), dict) else None)
        for item in events
    ) if not isinstance(replay.get("event_actor_counts"), dict) else normalized_event_count_map(replay.get("event_actor_counts"))
    fetch_plan = replay.get("fetch_plan") if isinstance(replay.get("fetch_plan"), dict) else {}
    summary = {
        "status": replay.get("status"),
        "source": replay.get("source"),
        "task_id": replay.get("task_id"),
        "event_count": int_value(replay.get("event_count")),
        "timed_out": replay.get("timed_out") is True,
        "after": int_value(replay.get("after")),
        "next_after": int_value(replay.get("next_after")),
        "sse_event_name_counts": sse_event_name_counts,
        "event_type_counts": event_type_counts,
        "event_status_counts": event_status_counts,
        "event_actor_counts": event_actor_counts,
        "claim_boundary": "Summary of raw harness event SSE replay for the requested harness task.",
    }
    if fetch_plan:
        summary["fetch_plan"] = fetch_plan
    return summary


def validate_trace_task_event_replay(
    event_replay: dict[str, Any] | None,
    result_summary: dict[str, Any],
    *,
    expected_task_id: str | None = None,
) -> dict[str, Any]:
    event_summary = matrix_event_replay_summary(event_replay)
    event_count = int_value(event_summary.get("event_count"))
    count_map_totals = {
        "sse_event_name_counts": event_count_map_total(event_summary.get("sse_event_name_counts")),
        "event_type_counts": event_count_map_total(event_summary.get("event_type_counts")),
        "event_status_counts": event_count_map_total(event_summary.get("event_status_counts")),
        "event_actor_counts": event_count_map_total(event_summary.get("event_actor_counts")),
    }
    event_type_counts = (
        event_summary.get("event_type_counts") if isinstance(event_summary.get("event_type_counts"), dict) else {}
    )
    expected_task = str(expected_task_id or "")
    event_summary_task_id = str(event_summary.get("task_id") or "")
    expected_event_counts = {
        "artifact.recorded": int_value(result_summary.get("artifact_count")),
        "observation.recorded": int_value(result_summary.get("observation_count")),
        "tool_call.recorded": int_value(result_summary.get("tool_call_count")),
        "tool_call.status_updated": int_value(
            result_summary.get("tool_call_status_update_expected_count")
        ),
    }
    observed_event_counts = {
        key: int_value(event_type_counts.get(key))
        for key in expected_event_counts
    }
    fetch_plan = (
        event_summary.get("fetch_plan")
        if isinstance(event_summary.get("fetch_plan"), dict)
        else {}
    )
    expected_raw_event_count = int_value(fetch_plan.get("expected_raw_event_count"))
    requested_max_events = int_value(fetch_plan.get("max_events"))
    checks = {
        "event_replay_expected_task_id_present": bool(expected_task) if expected_task_id is not None else True,
        "event_replay_task_id_matches": (
            event_summary_task_id == expected_task if expected_task else True
        ),
        "event_replay_completed": event_summary.get("status") == "completed",
        "event_replay_not_timed_out": event_summary.get("timed_out") is False,
        "event_replay_cursor_advanced": int_value(event_summary.get("next_after"))
        > int_value(event_summary.get("after")),
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
                    or int_value(event_summary.get("event_count")) >= expected_raw_event_count
                ),
            }
        )
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    return {
        "valid": not failed_checks,
        "source": "agents_sdk_trace_task_event_replay_validation",
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
            "Validation compares scenario trace-ingest result counts with raw harness event SSE replay."
        ),
    }


def build_matrix_readback_actions(
    task_id: str,
    matrix_payload: dict[str, Any],
    matrix_path: Path,
) -> list[dict[str, Any]]:
    artifact_ref = f"artifact:agents_sdk_smoke_matrix:{matrix_path.name}"
    summary = matrix_payload.get("summary") if isinstance(matrix_payload.get("summary"), dict) else {}
    provider_probe_validation = (
        matrix_payload.get("provider_probe_validation")
        if isinstance(matrix_payload.get("provider_probe_validation"), dict)
        else None
    )
    actions = [
        trace_ingest.action(
            "POST",
            f"/tasks/{task_id}/artifacts",
            {
                "kind": "agents_sdk_smoke_matrix",
                "path": str(matrix_path),
                "digest": trace_ingest.file_digest(matrix_path),
                "metadata": {
                    "source": "agents_sdk_smoke_matrix",
                    "run_id": matrix_payload.get("run_id"),
                    "provider": matrix_payload.get("provider"),
                    "api_style": matrix_payload.get("api_style"),
                    "model": matrix_payload.get("model"),
                    "status": matrix_payload.get("status"),
                    "scenario_count": matrix_payload.get("scenario_count"),
                    "requested_scenarios": matrix_payload.get("requested_scenarios") or [],
                },
            },
        ),
        trace_ingest.action(
            "POST",
            f"/tasks/{task_id}/observations",
            {
                "tool_name": "agents_sdk_smoke_matrix_summary",
                "result": summary,
                "evidence_refs": [artifact_ref],
                "status": "completed" if summary.get("status") == "completed" else "failed",
                "metadata": {"source": "agents_sdk_smoke_matrix", "surface": "aggregate_summary"},
            },
        ),
        trace_ingest.action(
            "POST",
            f"/tasks/{task_id}/observations",
            {
                "tool_name": "agents_sdk_smoke_matrix_scenario_rollup",
                "result": matrix_scenario_rollup(matrix_payload),
                "evidence_refs": [artifact_ref],
                "status": "completed" if summary.get("failed_count") in {0, "0"} else "failed",
                "metadata": {"source": "agents_sdk_smoke_matrix", "surface": "scenario_rollup"},
            },
        ),
    ]
    if provider_probe_validation is not None:
        actions.append(
            trace_ingest.action(
                "POST",
                f"/tasks/{task_id}/observations",
                {
                    "tool_name": "agents_sdk_smoke_matrix_provider_probe_validation",
                    "result": provider_probe_validation,
                    "evidence_refs": [artifact_ref],
                    "status": "completed" if provider_probe_validation.get("valid") is True else "failed",
                    "metadata": {
                        "source": "agents_sdk_smoke_matrix",
                        "surface": "provider_probe_validation",
                    },
                },
            )
        )
    return actions


def matrix_scenario_rollup(matrix_payload: dict[str, Any]) -> dict[str, Any]:
    summary = matrix_payload.get("summary") if isinstance(matrix_payload.get("summary"), dict) else {}
    scenarios = []
    for item in list_value(matrix_payload.get("results")):
        if not isinstance(item, dict):
            continue
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        validation = item.get("validation") if isinstance(item.get("validation"), dict) else {}
        projection_validation = (
            publish_readback.get("scenario_projection_validation")
            if isinstance(publish_readback.get("scenario_projection_validation"), dict)
            else {}
        )
        generic_validation = (
            publish_readback.get("validation")
            if isinstance(publish_readback.get("validation"), dict)
            else {}
        )
        scenario_event_replay_validation = (
            publish_readback.get("scenario_event_replay_validation")
            if isinstance(publish_readback.get("scenario_event_replay_validation"), dict)
            else {}
        )
        result_summary = (
            publish_readback.get("result_summary")
            if isinstance(publish_readback.get("result_summary"), dict)
            else {}
        )
        event_replay_summary = (
            scenario_event_replay_validation.get("event_replay_summary")
            if isinstance(scenario_event_replay_validation.get("event_replay_summary"), dict)
            else {}
        )
        event_type_counts = (
            event_replay_summary.get("event_type_counts")
            if isinstance(event_replay_summary.get("event_type_counts"), dict)
            else {}
        )
        scenarios.append(
            {
                "scenario": item.get("scenario"),
                "status": item.get("status"),
                "passed": item.get("passed"),
                "external_api_called": item.get("external_api_called") is True
                if isinstance(item.get("external_api_called"), bool)
                else bool((validation.get("checks") or {}).get("external_api_called"))
                if isinstance(validation.get("checks"), dict)
                else False,
                "validation_status": validation.get("status"),
                "validation_failed_checks": validation.get("failed_checks") or [],
                "trace_id": item.get("trace_id"),
                "output_json": item.get("output_json"),
                "api_usage_totals": normalized_numeric_map(
                    (item.get("summary") or {}).get("raw_response_usage_totals")
                    if isinstance(item.get("summary"), dict)
                    else None
                ),
                "tool_state_summary": (
                    item.get("tool_state_summary")
                    if isinstance(item.get("tool_state_summary"), dict)
                    else {}
                ),
                "session_state_summary": (
                    item.get("session_state_summary")
                    if isinstance(item.get("session_state_summary"), dict)
                    else {}
                ),
                "published_task_id": publish_readback.get("task_id"),
                "generic_readback_valid": generic_validation.get("valid"),
                "generic_readback_failed_checks": generic_validation.get("failed_checks") or [],
                "generic_readback_task_binding": generic_validation.get("task_binding") or {},
                "projection_readback_valid": projection_validation.get("valid"),
                "projection_failed_checks": projection_validation.get("failed_checks") or [],
                "sdk_events_projection_counts": projection_count_map(
                    publish_readback.get("sdk_events_summary")
                ),
                "client_transcript_projection_counts": projection_count_map(
                    publish_readback.get("client_transcript_summary")
                ),
                "sdk_events_projection_maps": projection_map_summary(
                    publish_readback.get("sdk_events_summary")
                ),
                "client_transcript_projection_maps": projection_map_summary(
                    publish_readback.get("client_transcript_summary")
                ),
                "tool_evidence_summary": compact_tool_evidence_summary(publish_readback)
                if item.get("scenario") == "tool"
                else {},
                "approval_evidence_summary": compact_approval_evidence_summary(
                    publish_readback
                )
                if item.get("scenario") == "approval"
                else {},
                "handoff_evidence_summary": compact_handoff_evidence_summary(
                    publish_readback
                )
                if item.get("scenario") in {"handoff", "stream_handoff"}
                else {},
                "guardrail_evidence_summary": compact_guardrail_evidence_summary(
                    publish_readback,
                    tripwire_expected=item.get("scenario") == "guardrail_tripwire",
                )
                if item.get("scenario") in {"guardrail", "guardrail_tripwire"}
                else {},
                "session_evidence_summary": compact_session_evidence_summary(
                    publish_readback
                )
                if item.get("scenario") == "session"
                else {},
                "stream_evidence_summary": compact_stream_evidence_summary(
                    publish_readback,
                    expected_tool_name=(
                        "transfer_to_harness_specialist"
                        if item.get("scenario") == "stream_handoff"
                        else "record_signal"
                    ),
                    handoff_expected=item.get("scenario") == "stream_handoff",
                )
                if item.get("scenario") in {"stream", "stream_handoff"}
                else {},
                "tool_projection_summary": compact_tool_projection_summary(publish_readback)
                if item.get("scenario") == "tool"
                else {},
                "stream_projection_summary": compact_stream_projection_summary(publish_readback),
                "guardrail_projection_summary": compact_guardrail_projection_summary(
                    publish_readback
                ),
                "scenario_event_replay_valid": scenario_event_replay_validation.get("valid"),
                "scenario_event_replay_failed_checks": (
                    scenario_event_replay_validation.get("failed_checks") or []
                ),
                "scenario_event_replay_task_binding": (
                    scenario_event_replay_validation.get("task_binding") or {}
                ),
                "scenario_event_replay_expected_event_counts": normalized_event_count_map(
                    scenario_event_replay_validation.get("expected_event_counts")
                ),
                "scenario_event_replay_observed_event_counts": normalized_event_count_map(
                    scenario_event_replay_validation.get("observed_event_counts")
                ),
                "scenario_event_replay_event_count": int_value(
                    event_replay_summary.get("event_count")
                ),
                "scenario_event_replay_count_map_totals": normalized_event_count_map(
                    scenario_event_replay_validation.get("event_replay_count_map_totals")
                ),
                "scenario_event_replay_count_total_checks": event_replay_count_total_check_map(
                    scenario_event_replay_validation
                ),
                "scenario_event_replay_sse_event_name_counts": normalized_event_count_map(
                    event_replay_summary.get("sse_event_name_counts")
                ),
                "scenario_event_replay_event_type_counts": normalized_event_count_map(
                    event_replay_summary.get("event_type_counts")
                ),
                "scenario_event_replay_event_status_counts": normalized_event_count_map(
                    event_replay_summary.get("event_status_counts")
                ),
                "scenario_event_replay_event_actor_counts": normalized_event_count_map(
                    event_replay_summary.get("event_actor_counts")
                ),
                "tool_call_status_update_expected_count": int_value(
                    result_summary.get("tool_call_status_update_expected_count")
                ),
                "tool_call_status_updated_event_count": int_value(
                    event_type_counts.get("tool_call.status_updated")
                ),
            }
        )
    return {
        "source": "agents_sdk_smoke_matrix_scenario_rollup",
        "run_id": matrix_payload.get("run_id"),
        "status": summary.get("status"),
        "scenario_count": len(scenarios),
        "published_task_ids": summary.get("published_task_ids") or {},
        "failed_scenarios": summary.get("failed_scenarios") or [],
        "generic_readback_failed_scenarios": summary.get("generic_readback_failed_scenarios") or [],
        "projection_failed_scenarios": summary.get("projection_failed_scenarios") or [],
        "scenario_event_replay_failed_scenarios": (
            summary.get("scenario_event_replay_failed_scenarios") or []
        ),
        "scenario_failed_checks": summary.get("scenario_failed_checks") or {},
        "generic_readback_failed_checks": summary.get("generic_readback_failed_checks") or {},
        "projection_failed_checks": summary.get("projection_failed_checks") or {},
        "external_api_called": summary.get("external_api_called") is True,
        "scenario_external_api_called": summary.get("scenario_external_api_called") or {},
        "scenario_api_usage_totals": summary.get("scenario_api_usage_totals") or {},
        "api_usage_totals": summary.get("api_usage_totals") or {},
        "scenario_sdk_events_projection_counts": (
            summary.get("scenario_sdk_events_projection_counts") or {}
        ),
        "scenario_client_transcript_projection_counts": (
            summary.get("scenario_client_transcript_projection_counts") or {}
        ),
        "scenario_sdk_events_projection_maps": (
            summary.get("scenario_sdk_events_projection_maps") or {}
        ),
        "scenario_client_transcript_projection_maps": (
            summary.get("scenario_client_transcript_projection_maps") or {}
        ),
        "scenario_tool_state_summaries": (
            summary.get("scenario_tool_state_summaries") or {}
        ),
        "scenario_tool_evidence_summaries": (
            summary.get("scenario_tool_evidence_summaries") or {}
        ),
        "scenario_approval_evidence_summaries": (
            summary.get("scenario_approval_evidence_summaries") or {}
        ),
        "scenario_handoff_evidence_summaries": (
            summary.get("scenario_handoff_evidence_summaries") or {}
        ),
        "scenario_guardrail_evidence_summaries": (
            summary.get("scenario_guardrail_evidence_summaries") or {}
        ),
        "scenario_session_evidence_summaries": (
            summary.get("scenario_session_evidence_summaries") or {}
        ),
        "scenario_stream_evidence_summaries": (
            summary.get("scenario_stream_evidence_summaries") or {}
        ),
        "scenario_tool_projection_summaries": (
            summary.get("scenario_tool_projection_summaries") or {}
        ),
        "scenario_handoff_state_summaries": (
            summary.get("scenario_handoff_state_summaries") or {}
        ),
        "scenario_handoff_projection_summaries": (
            summary.get("scenario_handoff_projection_summaries") or {}
        ),
        "scenario_approval_state_summaries": (
            summary.get("scenario_approval_state_summaries") or {}
        ),
        "scenario_approval_projection_summaries": (
            summary.get("scenario_approval_projection_summaries") or {}
        ),
        "scenario_session_state_summaries": (
            summary.get("scenario_session_state_summaries") or {}
        ),
        "scenario_guardrail_tripwire_summaries": (
            summary.get("scenario_guardrail_tripwire_summaries") or {}
        ),
        "scenario_guardrail_projection_summaries": (
            summary.get("scenario_guardrail_projection_summaries") or {}
        ),
        "scenario_stream_projection_summaries": (
            summary.get("scenario_stream_projection_summaries") or {}
        ),
        "scenario_event_replay_failed_checks": (
            summary.get("scenario_event_replay_failed_checks") or {}
        ),
        "scenario_event_replay_expected_event_counts": (
            summary.get("scenario_event_replay_expected_event_counts") or {}
        ),
        "scenario_event_replay_observed_event_counts": (
            summary.get("scenario_event_replay_observed_event_counts") or {}
        ),
        "scenario_event_replay_event_counts": (
            summary.get("scenario_event_replay_event_counts") or {}
        ),
        "scenario_event_replay_count_map_totals": (
            summary.get("scenario_event_replay_count_map_totals") or {}
        ),
        "scenario_event_replay_count_map_total_totals": (
            summary.get("scenario_event_replay_count_map_total_totals") or {}
        ),
        "scenario_event_replay_count_total_checks": (
            summary.get("scenario_event_replay_count_total_checks") or {}
        ),
        "scenario_event_replay_sse_event_name_counts": (
            summary.get("scenario_event_replay_sse_event_name_counts") or {}
        ),
        "scenario_event_replay_event_type_counts": (
            summary.get("scenario_event_replay_event_type_counts") or {}
        ),
        "scenario_event_replay_event_status_counts": (
            summary.get("scenario_event_replay_event_status_counts") or {}
        ),
        "scenario_event_replay_event_actor_counts": (
            summary.get("scenario_event_replay_event_actor_counts") or {}
        ),
        "scenario_event_replay_sse_event_name_totals": (
            summary.get("scenario_event_replay_sse_event_name_totals") or {}
        ),
        "scenario_event_replay_event_type_totals": (
            summary.get("scenario_event_replay_event_type_totals") or {}
        ),
        "scenario_event_replay_event_status_totals": (
            summary.get("scenario_event_replay_event_status_totals") or {}
        ),
        "scenario_event_replay_event_actor_totals": (
            summary.get("scenario_event_replay_event_actor_totals") or {}
        ),
        "scenario_event_replay_expected_event_totals": (
            summary.get("scenario_event_replay_expected_event_totals") or {}
        ),
        "scenario_event_replay_observed_event_totals": (
            summary.get("scenario_event_replay_observed_event_totals") or {}
        ),
        "tool_call_status_update_expected_counts": (
            summary.get("tool_call_status_update_expected_counts") or {}
        ),
        "tool_call_status_updated_event_counts": (
            summary.get("tool_call_status_updated_event_counts") or {}
        ),
        "tool_call_status_update_expected_total": int_value(
            summary.get("tool_call_status_update_expected_total")
        ),
        "tool_call_status_updated_event_total": int_value(
            summary.get("tool_call_status_updated_event_total")
        ),
        "scenarios": scenarios,
        "claim_boundary": "Scenario rollup is copied from the matrix JSON and stored as harness evidence.",
    }


def validate_matrix_readback(
    evidence: dict[str, Any],
    readback: dict[str, Any],
    result_summary: dict[str, Any],
    matrix_payload: dict[str, Any],
    event_replay: dict[str, Any] | None = None,
    *,
    expected_task_id: str | None = None,
) -> dict[str, Any]:
    evidence_counts = readback.get("evidence_counts") if isinstance(readback.get("evidence_counts"), dict) else {}
    artifact_kind_counts = (
        evidence_counts.get("artifact_kind_counts")
        if isinstance(evidence_counts.get("artifact_kind_counts"), dict)
        else {}
    )
    observation_tool_counts = (
        evidence_counts.get("observation_tool_counts")
        if isinstance(evidence_counts.get("observation_tool_counts"), dict)
        else {}
    )
    summary = matrix_payload.get("summary") if isinstance(matrix_payload.get("summary"), dict) else {}
    matrix_readback_payload = (
        matrix_payload.get("matrix_readback")
        if isinstance(matrix_payload.get("matrix_readback"), dict)
        else {}
    )
    expected_task = str(expected_task_id or "")
    matrix_payload_task_id = str(matrix_readback_payload.get("task_id") or "")
    evidence_task_id = str(evidence.get("task_id") or "")
    provider_probe_validation = (
        matrix_payload.get("provider_probe_validation")
        if isinstance(matrix_payload.get("provider_probe_validation"), dict)
        else None
    )
    summary_result = latest_observation_result(evidence, "agents_sdk_smoke_matrix_summary")
    comparable_summary_result = comparable_matrix_summary(summary_result)
    comparable_summary = comparable_matrix_summary(summary)
    matrix_artifact = latest_artifact(evidence, "agents_sdk_smoke_matrix")
    matrix_artifact_path = str(matrix_artifact.get("path") or "")
    matrix_artifact_digest = str(matrix_artifact.get("digest") or "")
    expected_artifact_path = expected_matrix_artifact_path(matrix_payload)
    matrix_artifact_file_digest = file_digest_if_available(matrix_artifact_path)
    expected_artifact_file_digest = file_digest_if_available(expected_artifact_path)
    rollup_result = latest_observation_result(evidence, "agents_sdk_smoke_matrix_scenario_rollup")
    expected_rollup_result = matrix_scenario_rollup(matrix_payload)
    provider_result = latest_observation_result(evidence, "agents_sdk_smoke_matrix_provider_probe_validation")
    sdk_summary = readback.get("sdk_events_summary") if isinstance(readback.get("sdk_events_summary"), dict) else {}
    transcript_summary = (
        readback.get("client_transcript_summary")
        if isinstance(readback.get("client_transcript_summary"), dict)
        else {}
    )
    sdk_summary_task_id = str(sdk_summary.get("task_id") or "")
    transcript_summary_task_id = str(transcript_summary.get("task_id") or "")
    event_summary = matrix_event_replay_summary(event_replay)
    event_summary_task_id = str(event_summary.get("task_id") or "")
    event_count = int_value(event_summary.get("event_count"))
    event_count_map_totals = {
        "sse_event_name_counts": event_count_map_total(
            event_summary.get("sse_event_name_counts")
        ),
        "event_type_counts": event_count_map_total(event_summary.get("event_type_counts")),
        "event_status_counts": event_count_map_total(
            event_summary.get("event_status_counts")
        ),
        "event_actor_counts": event_count_map_total(event_summary.get("event_actor_counts")),
    }
    event_type_counts = (
        event_summary.get("event_type_counts") if isinstance(event_summary.get("event_type_counts"), dict) else {}
    )
    expected_event_counts = {
        "artifact.recorded": int_value(result_summary.get("artifact_count")),
        "observation.recorded": int_value(result_summary.get("observation_count")),
        "tool_call.recorded": int_value(result_summary.get("tool_call_count")),
        "tool_call.status_updated": int_value(
            result_summary.get("tool_call_status_update_expected_count")
        ),
    }
    observed_event_counts = {
        key: int_value(event_type_counts.get(key))
        for key in expected_event_counts
    }
    event_fetch_plan = (
        event_summary.get("fetch_plan") if isinstance(event_summary.get("fetch_plan"), dict) else {}
    )
    event_expected_raw_event_count = int_value(event_fetch_plan.get("expected_raw_event_count"))
    event_requested_max_events = int_value(event_fetch_plan.get("max_events"))
    provider_expected = provider_probe_validation is not None
    checks = {
        "artifact_count_covers_result_summary": int_value(evidence_counts.get("artifacts"))
        >= int_value(result_summary.get("artifact_count")),
        "observation_count_covers_result_summary": int_value(evidence_counts.get("observations"))
        >= int_value(result_summary.get("observation_count")),
        "matrix_readback_expected_task_id_present": bool(expected_task) if expected_task_id is not None else True,
        "matrix_readback_evidence_task_id_matches": (
            evidence_task_id == expected_task if expected_task else True
        ),
        "matrix_readback_sdk_summary_task_id_matches": (
            sdk_summary_task_id == expected_task if expected_task else True
        ),
        "matrix_readback_transcript_task_id_matches": (
            transcript_summary_task_id == expected_task if expected_task else True
        ),
        "matrix_readback_event_replay_task_id_matches": (
            event_summary_task_id == expected_task if expected_task else True
        ),
        "matrix_readback_payload_task_id_matches": (
            matrix_payload_task_id == expected_task if expected_task and matrix_payload_task_id else True
        ),
        "matrix_artifact_recorded": int_value(artifact_kind_counts.get("agents_sdk_smoke_matrix")) >= 1,
        "matrix_artifact_path_recorded": bool(matrix_artifact_path),
        "matrix_artifact_digest_recorded": matrix_artifact_digest.startswith("sha256:"),
        "matrix_artifact_path_matches_payload": (
            normalize_path_string(matrix_artifact_path) == normalize_path_string(expected_artifact_path)
            if expected_artifact_path
            else True
        ),
        "matrix_artifact_digest_matches_recorded_path": (
            matrix_artifact_digest == matrix_artifact_file_digest if matrix_artifact_file_digest else True
        ),
        "matrix_artifact_digest_matches_expected_path": (
            matrix_artifact_digest == expected_artifact_file_digest if expected_artifact_file_digest else True
        ),
        "matrix_summary_observation_recorded": int_value(
            observation_tool_counts.get("agents_sdk_smoke_matrix_summary")
        )
        >= 1,
        "matrix_scenario_rollup_observation_recorded": int_value(
            observation_tool_counts.get("agents_sdk_smoke_matrix_scenario_rollup")
        )
        >= 1,
        "matrix_summary_status_matches": summary_result.get("status") == summary.get("status"),
        "matrix_summary_scenario_count_matches": int_value(summary_result.get("scenario_count"))
        == int_value(summary.get("scenario_count")),
        "matrix_summary_payload_matches": comparable_summary_result == comparable_summary,
        "matrix_summary_scenario_api_usage_totals_match": (
            summary_result.get("scenario_api_usage_totals") or {}
        )
        == (summary.get("scenario_api_usage_totals") or {}),
        "matrix_summary_api_usage_totals_match": (
            summary_result.get("api_usage_totals") or {}
        )
        == (summary.get("api_usage_totals") or {}),
        "matrix_summary_sdk_projection_totals_match": (
            summary_result.get("sdk_events_projection_totals") or {}
        )
        == (summary.get("sdk_events_projection_totals") or {}),
        "matrix_summary_client_projection_totals_match": (
            summary_result.get("client_transcript_projection_totals") or {}
        )
        == (summary.get("client_transcript_projection_totals") or {}),
        "matrix_summary_scenario_sdk_events_projection_counts_match": (
            summary_result.get("scenario_sdk_events_projection_counts") or {}
        )
        == (summary.get("scenario_sdk_events_projection_counts") or {}),
        "matrix_summary_scenario_client_transcript_projection_counts_match": (
            summary_result.get("scenario_client_transcript_projection_counts") or {}
        )
        == (summary.get("scenario_client_transcript_projection_counts") or {}),
        "matrix_summary_scenario_sdk_events_projection_maps_match": (
            summary_result.get("scenario_sdk_events_projection_maps") or {}
        )
        == (summary.get("scenario_sdk_events_projection_maps") or {}),
        "matrix_summary_scenario_client_transcript_projection_maps_match": (
            summary_result.get("scenario_client_transcript_projection_maps") or {}
        )
        == (summary.get("scenario_client_transcript_projection_maps") or {}),
        "matrix_summary_scenario_tool_state_summaries_match": (
            summary_result.get("scenario_tool_state_summaries") or {}
        )
        == (summary.get("scenario_tool_state_summaries") or {}),
        "matrix_summary_scenario_tool_evidence_summaries_match": (
            summary_result.get("scenario_tool_evidence_summaries") or {}
        )
        == (summary.get("scenario_tool_evidence_summaries") or {}),
        "matrix_summary_scenario_approval_evidence_summaries_match": (
            summary_result.get("scenario_approval_evidence_summaries") or {}
        )
        == (summary.get("scenario_approval_evidence_summaries") or {}),
        "matrix_summary_scenario_handoff_evidence_summaries_match": (
            summary_result.get("scenario_handoff_evidence_summaries") or {}
        )
        == (summary.get("scenario_handoff_evidence_summaries") or {}),
        "matrix_summary_scenario_guardrail_evidence_summaries_match": (
            summary_result.get("scenario_guardrail_evidence_summaries") or {}
        )
        == (summary.get("scenario_guardrail_evidence_summaries") or {}),
        "matrix_summary_scenario_session_evidence_summaries_match": (
            summary_result.get("scenario_session_evidence_summaries") or {}
        )
        == (summary.get("scenario_session_evidence_summaries") or {}),
        "matrix_summary_scenario_stream_evidence_summaries_match": (
            summary_result.get("scenario_stream_evidence_summaries") or {}
        )
        == (summary.get("scenario_stream_evidence_summaries") or {}),
        "matrix_summary_scenario_tool_projection_summaries_match": (
            summary_result.get("scenario_tool_projection_summaries") or {}
        )
        == (summary.get("scenario_tool_projection_summaries") or {}),
        "matrix_summary_scenario_stream_projection_summaries_match": (
            summary_result.get("scenario_stream_projection_summaries") or {}
        )
        == (summary.get("scenario_stream_projection_summaries") or {}),
        "matrix_summary_scenario_handoff_state_summaries_match": (
            summary_result.get("scenario_handoff_state_summaries") or {}
        )
        == (summary.get("scenario_handoff_state_summaries") or {}),
        "matrix_summary_scenario_handoff_projection_summaries_match": (
            summary_result.get("scenario_handoff_projection_summaries") or {}
        )
        == (summary.get("scenario_handoff_projection_summaries") or {}),
        "matrix_summary_projection_deltas_match": (summary_result.get("projection_total_deltas") or {})
        == (summary.get("projection_total_deltas") or {}),
        "matrix_summary_projection_delta_status_match": summary_result.get("projection_total_delta_status")
        == summary.get("projection_total_delta_status"),
        "matrix_summary_projection_delta_unexpected_keys_match": (
            summary_result.get("projection_total_delta_unexpected_keys") or []
        )
        == (summary.get("projection_total_delta_unexpected_keys") or []),
        "matrix_summary_projection_delta_assessment_match": (
            summary_result.get("projection_total_delta_assessment") or {}
        )
        == (summary.get("projection_total_delta_assessment") or {}),
        "matrix_summary_sdk_projection_map_totals_match": (
            summary_result.get("sdk_events_projection_map_totals") or {}
        )
        == (summary.get("sdk_events_projection_map_totals") or {}),
        "matrix_summary_client_projection_map_totals_match": (
            summary_result.get("client_transcript_projection_map_totals") or {}
        )
        == (summary.get("client_transcript_projection_map_totals") or {}),
        "matrix_summary_projection_map_deltas_match": (summary_result.get("projection_map_deltas") or {})
        == (summary.get("projection_map_deltas") or {}),
        "matrix_summary_projection_map_delta_status_match": summary_result.get("projection_map_delta_status")
        == summary.get("projection_map_delta_status"),
        "matrix_summary_projection_map_delta_unexpected_keys_match": (
            summary_result.get("projection_map_delta_unexpected_keys") or []
        )
        == (summary.get("projection_map_delta_unexpected_keys") or []),
        "matrix_summary_projection_map_delta_assessment_match": (
            summary_result.get("projection_map_delta_assessment") or {}
        )
        == (summary.get("projection_map_delta_assessment") or {}),
        "matrix_summary_external_api_called_match": (
            summary_result.get("external_api_called") is True
        )
        == (summary.get("external_api_called") is True),
        "matrix_summary_scenario_external_api_called_match": (
            summary_result.get("scenario_external_api_called") or {}
        )
        == (summary.get("scenario_external_api_called") or {}),
        "matrix_summary_scenario_event_replay_expected_event_counts_match": (
            summary_result.get("scenario_event_replay_expected_event_counts") or {}
        )
        == (summary.get("scenario_event_replay_expected_event_counts") or {}),
        "matrix_summary_scenario_event_replay_observed_event_counts_match": (
            summary_result.get("scenario_event_replay_observed_event_counts") or {}
        )
        == (summary.get("scenario_event_replay_observed_event_counts") or {}),
        "matrix_summary_scenario_event_replay_event_counts_match": (
            summary_result.get("scenario_event_replay_event_counts") or {}
        )
        == (summary.get("scenario_event_replay_event_counts") or {}),
        "matrix_summary_scenario_event_replay_count_map_totals_match": (
            summary_result.get("scenario_event_replay_count_map_totals") or {}
        )
        == (summary.get("scenario_event_replay_count_map_totals") or {}),
        "matrix_summary_scenario_event_replay_count_map_total_totals_match": (
            summary_result.get("scenario_event_replay_count_map_total_totals") or {}
        )
        == (summary.get("scenario_event_replay_count_map_total_totals") or {}),
        "matrix_summary_scenario_event_replay_count_total_checks_match": (
            summary_result.get("scenario_event_replay_count_total_checks") or {}
        )
        == (summary.get("scenario_event_replay_count_total_checks") or {}),
        "matrix_summary_scenario_event_replay_sse_event_name_counts_match": (
            summary_result.get("scenario_event_replay_sse_event_name_counts") or {}
        )
        == (summary.get("scenario_event_replay_sse_event_name_counts") or {}),
        "matrix_summary_scenario_event_replay_event_type_counts_match": (
            summary_result.get("scenario_event_replay_event_type_counts") or {}
        )
        == (summary.get("scenario_event_replay_event_type_counts") or {}),
        "matrix_summary_scenario_event_replay_event_status_counts_match": (
            summary_result.get("scenario_event_replay_event_status_counts") or {}
        )
        == (summary.get("scenario_event_replay_event_status_counts") or {}),
        "matrix_summary_scenario_event_replay_event_actor_counts_match": (
            summary_result.get("scenario_event_replay_event_actor_counts") or {}
        )
        == (summary.get("scenario_event_replay_event_actor_counts") or {}),
        "matrix_summary_scenario_event_replay_sse_event_name_totals_match": (
            summary_result.get("scenario_event_replay_sse_event_name_totals") or {}
        )
        == (summary.get("scenario_event_replay_sse_event_name_totals") or {}),
        "matrix_summary_scenario_event_replay_event_type_totals_match": (
            summary_result.get("scenario_event_replay_event_type_totals") or {}
        )
        == (summary.get("scenario_event_replay_event_type_totals") or {}),
        "matrix_summary_scenario_event_replay_event_status_totals_match": (
            summary_result.get("scenario_event_replay_event_status_totals") or {}
        )
        == (summary.get("scenario_event_replay_event_status_totals") or {}),
        "matrix_summary_scenario_event_replay_event_actor_totals_match": (
            summary_result.get("scenario_event_replay_event_actor_totals") or {}
        )
        == (summary.get("scenario_event_replay_event_actor_totals") or {}),
        "matrix_summary_scenario_event_replay_expected_event_totals_match": (
            summary_result.get("scenario_event_replay_expected_event_totals") or {}
        )
        == (summary.get("scenario_event_replay_expected_event_totals") or {}),
        "matrix_summary_scenario_event_replay_observed_event_totals_match": (
            summary_result.get("scenario_event_replay_observed_event_totals") or {}
        )
        == (summary.get("scenario_event_replay_observed_event_totals") or {}),
        "matrix_summary_tool_call_status_update_expected_counts_match": (
            summary_result.get("tool_call_status_update_expected_counts") or {}
        )
        == (summary.get("tool_call_status_update_expected_counts") or {}),
        "matrix_summary_tool_call_status_updated_event_counts_match": (
            summary_result.get("tool_call_status_updated_event_counts") or {}
        )
        == (summary.get("tool_call_status_updated_event_counts") or {}),
        "matrix_summary_tool_call_status_update_expected_total_match": int_value(
            summary_result.get("tool_call_status_update_expected_total")
        )
        == int_value(summary.get("tool_call_status_update_expected_total")),
        "matrix_summary_tool_call_status_updated_event_total_match": int_value(
            summary_result.get("tool_call_status_updated_event_total")
        )
        == int_value(summary.get("tool_call_status_updated_event_total")),
        "matrix_rollup_scenario_count_matches": int_value(rollup_result.get("scenario_count"))
        == len(list_value(matrix_payload.get("results"))),
        "matrix_rollup_published_task_ids_match": (rollup_result.get("published_task_ids") or {})
        == (summary.get("published_task_ids") or {}),
        "matrix_rollup_scenario_api_usage_totals_match": (
            rollup_result.get("scenario_api_usage_totals") or {}
        )
        == (expected_rollup_result.get("scenario_api_usage_totals") or {}),
        "matrix_rollup_api_usage_totals_match": (
            rollup_result.get("api_usage_totals") or {}
        )
        == (expected_rollup_result.get("api_usage_totals") or {}),
        "matrix_rollup_scenario_sdk_events_projection_counts_match": (
            rollup_result.get("scenario_sdk_events_projection_counts") or {}
        )
        == (expected_rollup_result.get("scenario_sdk_events_projection_counts") or {}),
        "matrix_rollup_scenario_client_transcript_projection_counts_match": (
            rollup_result.get("scenario_client_transcript_projection_counts") or {}
        )
        == (expected_rollup_result.get("scenario_client_transcript_projection_counts") or {}),
        "matrix_rollup_scenario_sdk_events_projection_maps_match": (
            rollup_result.get("scenario_sdk_events_projection_maps") or {}
        )
        == (expected_rollup_result.get("scenario_sdk_events_projection_maps") or {}),
        "matrix_rollup_scenario_client_transcript_projection_maps_match": (
            rollup_result.get("scenario_client_transcript_projection_maps") or {}
        )
        == (expected_rollup_result.get("scenario_client_transcript_projection_maps") or {}),
        "matrix_rollup_scenario_tool_state_summaries_match": (
            rollup_result.get("scenario_tool_state_summaries") or {}
        )
        == (expected_rollup_result.get("scenario_tool_state_summaries") or {}),
        "matrix_rollup_scenario_tool_evidence_summaries_match": (
            rollup_result.get("scenario_tool_evidence_summaries") or {}
        )
        == (expected_rollup_result.get("scenario_tool_evidence_summaries") or {}),
        "matrix_rollup_scenario_approval_evidence_summaries_match": (
            rollup_result.get("scenario_approval_evidence_summaries") or {}
        )
        == (expected_rollup_result.get("scenario_approval_evidence_summaries") or {}),
        "matrix_rollup_scenario_handoff_evidence_summaries_match": (
            rollup_result.get("scenario_handoff_evidence_summaries") or {}
        )
        == (expected_rollup_result.get("scenario_handoff_evidence_summaries") or {}),
        "matrix_rollup_scenario_guardrail_evidence_summaries_match": (
            rollup_result.get("scenario_guardrail_evidence_summaries") or {}
        )
        == (expected_rollup_result.get("scenario_guardrail_evidence_summaries") or {}),
        "matrix_rollup_scenario_session_evidence_summaries_match": (
            rollup_result.get("scenario_session_evidence_summaries") or {}
        )
        == (expected_rollup_result.get("scenario_session_evidence_summaries") or {}),
        "matrix_rollup_scenario_stream_evidence_summaries_match": (
            rollup_result.get("scenario_stream_evidence_summaries") or {}
        )
        == (expected_rollup_result.get("scenario_stream_evidence_summaries") or {}),
        "matrix_rollup_scenario_tool_projection_summaries_match": (
            rollup_result.get("scenario_tool_projection_summaries") or {}
        )
        == (expected_rollup_result.get("scenario_tool_projection_summaries") or {}),
        "matrix_rollup_scenario_stream_projection_summaries_match": (
            rollup_result.get("scenario_stream_projection_summaries") or {}
        )
        == (expected_rollup_result.get("scenario_stream_projection_summaries") or {}),
        "matrix_rollup_scenario_handoff_state_summaries_match": (
            rollup_result.get("scenario_handoff_state_summaries") or {}
        )
        == (expected_rollup_result.get("scenario_handoff_state_summaries") or {}),
        "matrix_rollup_scenario_handoff_projection_summaries_match": (
            rollup_result.get("scenario_handoff_projection_summaries") or {}
        )
        == (expected_rollup_result.get("scenario_handoff_projection_summaries") or {}),
        "matrix_rollup_external_api_called_match": (
            rollup_result.get("external_api_called") is True
        )
        == (expected_rollup_result.get("external_api_called") is True),
        "matrix_rollup_scenario_external_api_called_match": (
            rollup_result.get("scenario_external_api_called") or {}
        )
        == (expected_rollup_result.get("scenario_external_api_called") or {}),
        "matrix_rollup_scenario_event_replay_expected_event_counts_match": (
            rollup_result.get("scenario_event_replay_expected_event_counts") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_expected_event_counts") or {}),
        "matrix_rollup_scenario_event_replay_observed_event_counts_match": (
            rollup_result.get("scenario_event_replay_observed_event_counts") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_observed_event_counts") or {}),
        "matrix_rollup_scenario_event_replay_event_counts_match": (
            rollup_result.get("scenario_event_replay_event_counts") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_event_counts") or {}),
        "matrix_rollup_scenario_event_replay_count_map_totals_match": (
            rollup_result.get("scenario_event_replay_count_map_totals") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_count_map_totals") or {}),
        "matrix_rollup_scenario_event_replay_count_map_total_totals_match": (
            rollup_result.get("scenario_event_replay_count_map_total_totals") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_count_map_total_totals") or {}),
        "matrix_rollup_scenario_event_replay_count_total_checks_match": (
            rollup_result.get("scenario_event_replay_count_total_checks") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_count_total_checks") or {}),
        "matrix_rollup_scenario_event_replay_sse_event_name_counts_match": (
            rollup_result.get("scenario_event_replay_sse_event_name_counts") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_sse_event_name_counts") or {}),
        "matrix_rollup_scenario_event_replay_event_type_counts_match": (
            rollup_result.get("scenario_event_replay_event_type_counts") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_event_type_counts") or {}),
        "matrix_rollup_scenario_event_replay_event_status_counts_match": (
            rollup_result.get("scenario_event_replay_event_status_counts") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_event_status_counts") or {}),
        "matrix_rollup_scenario_event_replay_event_actor_counts_match": (
            rollup_result.get("scenario_event_replay_event_actor_counts") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_event_actor_counts") or {}),
        "matrix_rollup_scenario_event_replay_sse_event_name_totals_match": (
            rollup_result.get("scenario_event_replay_sse_event_name_totals") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_sse_event_name_totals") or {}),
        "matrix_rollup_scenario_event_replay_event_type_totals_match": (
            rollup_result.get("scenario_event_replay_event_type_totals") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_event_type_totals") or {}),
        "matrix_rollup_scenario_event_replay_event_status_totals_match": (
            rollup_result.get("scenario_event_replay_event_status_totals") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_event_status_totals") or {}),
        "matrix_rollup_scenario_event_replay_event_actor_totals_match": (
            rollup_result.get("scenario_event_replay_event_actor_totals") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_event_actor_totals") or {}),
        "matrix_rollup_scenario_event_replay_expected_event_totals_match": (
            rollup_result.get("scenario_event_replay_expected_event_totals") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_expected_event_totals") or {}),
        "matrix_rollup_scenario_event_replay_observed_event_totals_match": (
            rollup_result.get("scenario_event_replay_observed_event_totals") or {}
        )
        == (expected_rollup_result.get("scenario_event_replay_observed_event_totals") or {}),
        "matrix_rollup_tool_call_status_update_expected_counts_match": (
            rollup_result.get("tool_call_status_update_expected_counts") or {}
        )
        == (expected_rollup_result.get("tool_call_status_update_expected_counts") or {}),
        "matrix_rollup_tool_call_status_updated_event_counts_match": (
            rollup_result.get("tool_call_status_updated_event_counts") or {}
        )
        == (expected_rollup_result.get("tool_call_status_updated_event_counts") or {}),
        "matrix_rollup_tool_call_status_update_expected_total_match": int_value(
            rollup_result.get("tool_call_status_update_expected_total")
        )
        == int_value(expected_rollup_result.get("tool_call_status_update_expected_total")),
        "matrix_rollup_tool_call_status_updated_event_total_match": int_value(
            rollup_result.get("tool_call_status_updated_event_total")
        )
        == int_value(expected_rollup_result.get("tool_call_status_updated_event_total")),
        "matrix_rollup_payload_matches": rollup_result == expected_rollup_result,
        "provider_validation_observation_state": (
            int_value(observation_tool_counts.get("agents_sdk_smoke_matrix_provider_probe_validation")) >= 1
            if provider_expected
            else provider_result == {}
        ),
        "provider_validation_matches": (
            provider_result.get("valid") == provider_probe_validation.get("valid")
            and provider_result.get("diagnostic_id") == provider_probe_validation.get("diagnostic_id")
            if provider_expected
            else True
        ),
        "provider_validation_payload_matches": (
            provider_result == provider_probe_validation if provider_expected else True
        ),
        "sdk_summary_requested": sdk_summary.get("summary_requested") is True,
        "client_transcript_summary_requested": transcript_summary.get("summary_requested") is True,
    }
    if event_replay is not None:
        checks.update(
            {
                "event_replay_completed": event_summary.get("status") == "completed",
                "event_replay_not_timed_out": event_summary.get("timed_out") is False,
                "event_replay_cursor_advanced": int_value(event_summary.get("next_after"))
                > int_value(event_summary.get("after")),
                "event_replay_sse_event_name_count_total_matches_event_count": (
                    event_count_map_totals["sse_event_name_counts"] == event_count
                ),
                "event_replay_event_type_count_total_matches_event_count": (
                    event_count_map_totals["event_type_counts"] == event_count
                ),
                "event_replay_event_status_count_total_matches_event_count": (
                    event_count_map_totals["event_status_counts"] == event_count
                ),
                "event_replay_event_actor_count_total_matches_event_count": (
                    event_count_map_totals["event_actor_counts"] == event_count
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
        )
        if event_fetch_plan:
            checks.update(
                {
                    "event_replay_fetch_plan_covers_expected_raw_event_count": (
                        event_requested_max_events >= event_expected_raw_event_count
                    ),
                    "event_replay_not_truncated_by_fetch_limit": (
                        event_expected_raw_event_count <= 0
                        or int_value(event_summary.get("event_count")) >= event_expected_raw_event_count
                    ),
                }
            )
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    return {
        "valid": not failed_checks,
        "source": "agents_sdk_smoke_matrix_readback_validation",
        "check_count": len(checks),
        "failed_checks": failed_checks,
        "checks": checks,
        "evidence_observation_tool_counts": observation_tool_counts,
        "evidence_artifact_kind_counts": artifact_kind_counts,
        "task_binding": {
            "expected_task_id": expected_task,
            "evidence_task_id": evidence_task_id,
            "sdk_events_summary_task_id": sdk_summary_task_id,
            "client_transcript_summary_task_id": transcript_summary_task_id,
            "event_replay_task_id": event_summary_task_id,
            "matrix_payload_task_id": matrix_payload_task_id,
        },
        "matrix_artifact": {
            "path": matrix_artifact_path,
            "digest": matrix_artifact_digest,
            "expected_path": expected_artifact_path,
            "digest_from_recorded_path": matrix_artifact_file_digest,
            "digest_from_expected_path": expected_artifact_file_digest,
            "recorded_path_available": matrix_artifact_file_digest is not None,
            "expected_path_available": expected_artifact_file_digest is not None,
        },
        "provider_probe_validation": provider_result if provider_result else provider_probe_validation,
        "event_replay_summary": event_summary if event_replay is not None else None,
        "event_replay_count_map_totals": (
            event_count_map_totals if event_replay is not None else {}
        ),
        "event_replay_count_total_checks": {
            key: checks.get(key) is True
            for key in EVENT_REPLAY_COUNT_TOTAL_CHECK_KEYS
            if key in checks
        },
        "expected_event_counts": expected_event_counts if event_replay is not None else {},
        "observed_event_counts": observed_event_counts if event_replay is not None else {},
        "event_replay_fetch_plan": event_fetch_plan,
        "claim_boundary": (
            "Validation compares stored aggregate matrix artifact, custom observations, event replay, "
            "and read-model summaries against the matrix payload and harness readback."
        ),
    }


def latest_observation_result(evidence: dict[str, Any], tool_name: str) -> dict[str, Any]:
    observations = evidence.get("observations") if isinstance(evidence.get("observations"), list) else []
    for item in reversed(observations):
        if not isinstance(item, dict) or item.get("tool_name") != tool_name:
            continue
        result = item.get("result")
        return result if isinstance(result, dict) else {}
    return {}


def latest_artifact(evidence: dict[str, Any], kind: str) -> dict[str, Any]:
    artifacts = evidence.get("artifacts") if isinstance(evidence.get("artifacts"), list) else []
    for item in reversed(artifacts):
        if isinstance(item, dict) and item.get("kind") == kind:
            return item
    return {}


def comparable_matrix_summary(summary: dict[str, Any]) -> dict[str, Any]:
    self_referential_fields = {
        "matrix_readback_status",
        "matrix_readback_valid",
        "matrix_readback_failed_checks",
        "matrix_readback_task_binding",
        "matrix_readback_error",
        "matrix_payload_readback_status",
        "matrix_payload_readback_valid",
        "matrix_payload_readback_failed_checks",
        "matrix_payload_readback_task_binding",
        "matrix_payload_readback_error",
    }
    return {
        str(key): value
        for key, value in summary.items()
        if str(key) not in self_referential_fields
    }


def expected_matrix_artifact_path(matrix_payload: dict[str, Any]) -> str:
    if matrix_payload.get("matrix_evidence_json"):
        return str(matrix_payload.get("matrix_evidence_json") or "")
    readback = matrix_payload.get("matrix_readback")
    if isinstance(readback, dict) and readback.get("matrix_json"):
        return str(readback.get("matrix_json") or "")
    return ""


def file_digest_if_available(path_value: str) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_file():
        return None
    try:
        return trace_ingest.file_digest(path)
    except OSError:
        return None


def normalize_path_string(path_value: str) -> str:
    normalized = str(path_value or "").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip("/")


def validate_readback_projection(scenario: str, readback: dict[str, Any]) -> dict[str, Any]:
    sdk_summary = readback.get("sdk_events_summary") if isinstance(readback.get("sdk_events_summary"), dict) else {}
    transcript_summary = (
        readback.get("client_transcript_summary")
        if isinstance(readback.get("client_transcript_summary"), dict)
        else {}
    )
    checks = {
        "sdk_summary_requested": sdk_summary.get("summary_requested") is True,
        "transcript_summary_requested": transcript_summary.get("summary_requested") is True,
        "sdk_model_response_projected": int_value(sdk_summary.get("agent_model_response_count")) >= 1,
        "transcript_model_response_projected": int_value(transcript_summary.get("agent_model_response_count")) >= 1,
        "sdk_model_response_provider_counted": projection_map_counts_known(
            sdk_summary,
            "agent_model_response_provider_counts",
            "agent_model_response_count",
        ),
        "transcript_model_response_provider_counted": projection_map_counts_known(
            transcript_summary,
            "agent_model_response_provider_counts",
            "agent_model_response_count",
        ),
        "sdk_transcript_model_response_provider_counts_match": normalized_event_count_map(
            sdk_summary.get("agent_model_response_provider_counts")
        )
        == normalized_event_count_map(transcript_summary.get("agent_model_response_provider_counts")),
        "sdk_model_response_api_style_counted": projection_map_counts_known(
            sdk_summary,
            "agent_model_response_api_style_counts",
            "agent_model_response_count",
        ),
        "transcript_model_response_api_style_counted": projection_map_counts_known(
            transcript_summary,
            "agent_model_response_api_style_counts",
            "agent_model_response_count",
        ),
        "sdk_transcript_model_response_api_style_counts_match": normalized_event_count_map(
            sdk_summary.get("agent_model_response_api_style_counts")
        )
        == normalized_event_count_map(transcript_summary.get("agent_model_response_api_style_counts")),
        "sdk_final_output_projected": int_value(sdk_summary.get("final_output_count")) >= 1,
    }
    checks.update(scenario_readback_required_checks(scenario, sdk_summary, transcript_summary))
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    return {
        "valid": not failed_checks,
        "source": "agents_sdk_smoke_matrix_projection_validation",
        "scenario": scenario,
        "check_count": len(checks),
        "failed_checks": failed_checks,
        "checks": checks,
        "claim_boundary": (
            "Validation compares scenario-specific requirements against stored SDK-event and "
            "client-transcript readback summaries."
        ),
    }


def scenario_readback_required_checks(
    scenario: str,
    sdk_summary: dict[str, Any],
    transcript_summary: dict[str, Any],
) -> dict[str, bool]:
    if scenario == "tool":
        return {
            "sdk_tool_progress_projected": int_value(sdk_summary.get("tool_progress_count")) >= 1,
            "transcript_tool_progress_projected": int_value(transcript_summary.get("tool_progress_count")) >= 1,
        }
    if scenario == "handoff":
        return {
            "sdk_handoffs_projected": int_value(sdk_summary.get("agent_handoff_count")) >= 2,
            "transcript_handoffs_projected": int_value(transcript_summary.get("agent_handoff_count")) >= 2,
        }
    if scenario == "approval":
        return {
            "sdk_approvals_projected": int_value(sdk_summary.get("agent_approval_count")) >= 2,
            "transcript_approvals_projected": int_value(transcript_summary.get("agent_approval_count")) >= 2,
            "sdk_approval_phases_projected": int_value(sdk_summary.get("agent_phase_count")) >= 2,
            "transcript_approval_phases_projected": int_value(transcript_summary.get("agent_phase_count")) >= 2,
            "sdk_approval_tool_progress_projected": int_value(sdk_summary.get("tool_progress_count")) >= 1,
            "transcript_approval_tool_progress_projected": int_value(transcript_summary.get("tool_progress_count"))
            >= 1,
        }
    if scenario == "guardrail":
        return {
            "sdk_guardrail_projected": int_value(sdk_summary.get("agent_guardrail_count")) >= 1,
            "transcript_guardrail_projected": int_value(transcript_summary.get("agent_guardrail_count")) >= 1,
            "sdk_no_run_error_projected": int_value(sdk_summary.get("agent_run_error_count")) == 0,
            "transcript_no_run_error_projected": int_value(transcript_summary.get("agent_run_error_count")) == 0,
        }
    if scenario == "guardrail_tripwire":
        return {
            "sdk_tripwire_guardrail_projected": int_value(sdk_summary.get("agent_guardrail_count")) >= 1,
            "transcript_tripwire_guardrail_projected": int_value(transcript_summary.get("agent_guardrail_count")) >= 1,
            "sdk_tripwire_run_error_projected": int_value(sdk_summary.get("agent_run_error_count")) >= 1,
            "transcript_tripwire_run_error_projected": int_value(transcript_summary.get("agent_run_error_count")) >= 1,
        }
    if scenario == "session":
        return {
            "sdk_session_projected": int_value(sdk_summary.get("agent_session_count")) >= 1,
            "transcript_session_projected": int_value(transcript_summary.get("agent_session_count")) >= 1,
            "sdk_session_phases_projected": int_value(sdk_summary.get("agent_phase_count")) >= 2,
            "transcript_session_phases_projected": int_value(transcript_summary.get("agent_phase_count")) >= 2,
        }
    if scenario == "stream":
        return {
            "sdk_stream_events_projected": int_value(sdk_summary.get("agent_stream_event_count")) > 0,
            "transcript_stream_events_projected": int_value(transcript_summary.get("agent_stream_event_count")) > 0,
            "sdk_stream_raw_events_projected": summary_map_int(
                sdk_summary, "agent_stream_event_type_counts", "raw_response_event"
            )
            > 0,
            "sdk_stream_run_item_events_projected": summary_map_int(
                sdk_summary, "agent_stream_event_type_counts", "run_item_stream_event"
            )
            > 0,
            "sdk_stream_tool_name_projected": summary_map_int(
                sdk_summary, "agent_stream_tool_name_counts", "record_signal"
            )
            >= 1,
            "transcript_stream_tool_name_projected": summary_map_int(
                transcript_summary, "agent_stream_tool_name_counts", "record_signal"
            )
            >= 1,
        }
    if scenario == "stream_handoff":
        return {
            "sdk_stream_handoff_events_projected": int_value(sdk_summary.get("agent_stream_event_count")) > 0,
            "transcript_stream_handoff_events_projected": int_value(
                transcript_summary.get("agent_stream_event_count")
            )
            > 0,
            "sdk_stream_handoffs_projected": int_value(sdk_summary.get("agent_handoff_count")) >= 2,
            "transcript_stream_handoffs_projected": int_value(transcript_summary.get("agent_handoff_count")) >= 2,
            "sdk_handoff_requested_projected": summary_map_int(
                sdk_summary, "agent_stream_event_name_counts", "handoff_requested"
            )
            >= 1,
            "sdk_handoff_completed_projected": summary_map_int(
                sdk_summary, "agent_stream_event_name_counts", "handoff_occured"
            )
            >= 1,
            "sdk_handoff_tool_name_projected": summary_map_int(
                sdk_summary, "agent_stream_tool_name_counts", "transfer_to_harness_specialist"
            )
            >= 1,
            "transcript_handoff_tool_name_projected": summary_map_int(
                transcript_summary, "agent_stream_tool_name_counts", "transfer_to_harness_specialist"
            )
            >= 1,
        }
    return {}


def summarize_matrix(
    results: list[dict[str, Any]],
    *,
    publish_readback: bool,
    provider_probe_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scenario_status_counts = count_values(str(item.get("status") or "unknown") for item in results)
    validation_status_counts = count_values(
        str((item.get("validation") or {}).get("status") or "unknown") for item in results
    )
    publish_status_counts = count_values(
        str((item.get("publish_readback") or {}).get("status") or "not_requested")
        for item in results
    )
    generic_readback_status_counts = count_values(
        "valid"
        if ((item.get("publish_readback") or {}).get("validation") or {}).get("valid") is True
        else "invalid"
        if item.get("publish_readback")
        else "not_requested"
        for item in results
    )
    projection_validation_status_counts = count_values(
        "valid"
        if ((item.get("publish_readback") or {}).get("scenario_projection_validation") or {}).get("valid") is True
        else "invalid"
        if item.get("publish_readback")
        else "not_requested"
        for item in results
    )
    scenario_event_replay_status_counts = count_values(
        "valid"
        if ((item.get("publish_readback") or {}).get("scenario_event_replay_validation") or {}).get("valid") is True
        else "invalid"
        if item.get("publish_readback")
        else "not_requested"
        for item in results
    )
    published_task_ids = {
        str(item.get("scenario")): (item.get("publish_readback") or {}).get("task_id")
        for item in results
        if (item.get("publish_readback") or {}).get("task_id")
    }
    scenario_sdk_projection_counts = scenario_projection_count_maps(results, "sdk_events_summary")
    scenario_client_projection_counts = scenario_projection_count_maps(results, "client_transcript_summary")
    scenario_sdk_projection_maps = scenario_projection_map_summaries(results, "sdk_events_summary")
    scenario_client_projection_maps = scenario_projection_map_summaries(results, "client_transcript_summary")
    scenario_tool_state_summaries = scenario_tool_state_summary_maps(results)
    scenario_tool_evidence_summaries = scenario_tool_evidence_summary_maps(results)
    scenario_approval_evidence_summaries = scenario_approval_evidence_summary_maps(results)
    scenario_handoff_evidence_summaries = scenario_handoff_evidence_summary_maps(results)
    scenario_guardrail_evidence_summaries = scenario_guardrail_evidence_summary_maps(results)
    scenario_session_evidence_summaries = scenario_session_evidence_summary_maps(results)
    scenario_stream_evidence_summaries = scenario_stream_evidence_summary_maps(results)
    scenario_tool_projection_summaries = scenario_tool_projection_summary_maps(results)
    scenario_stream_projection_summaries = scenario_stream_projection_summary_maps(results)
    scenario_handoff_state_summaries = scenario_handoff_state_summary_maps(results)
    scenario_handoff_projection_summaries = scenario_handoff_projection_summary_maps(results)
    scenario_approval_state_summaries = scenario_approval_state_summary_maps(results)
    scenario_approval_projection_summaries = scenario_approval_projection_summary_maps(results)
    scenario_session_state_summaries = scenario_session_state_summary_maps(results)
    scenario_guardrail_tripwire_summaries = scenario_guardrail_tripwire_summary_maps(results)
    scenario_guardrail_projection_summaries = scenario_guardrail_projection_summary_maps(results)
    replay_expected_event_counts = scenario_replay_expected_event_count_maps(results)
    replay_observed_event_counts = scenario_replay_observed_event_count_maps(results)
    replay_event_counts = scenario_replay_raw_event_counts(results)
    replay_count_map_totals = scenario_replay_count_map_total_maps(results)
    replay_count_total_checks = scenario_replay_count_total_check_maps(results)
    replay_sse_event_name_counts = scenario_replay_summary_count_maps(
        results, "sse_event_name_counts"
    )
    replay_event_type_counts = scenario_replay_summary_count_maps(results, "event_type_counts")
    replay_event_status_counts = scenario_replay_summary_count_maps(
        results, "event_status_counts"
    )
    replay_event_actor_counts = scenario_replay_summary_count_maps(results, "event_actor_counts")
    tool_call_status_update_expected_counts = scenario_tool_call_status_update_expected_counts(results)
    tool_call_status_updated_event_counts = scenario_tool_call_status_updated_event_counts(results)
    sdk_events_projection_totals = projection_count_totals(
        (item.get("publish_readback") or {}).get("sdk_events_summary") for item in results
    )
    client_transcript_projection_totals = projection_count_totals(
        (item.get("publish_readback") or {}).get("client_transcript_summary") for item in results
    )
    sdk_events_projection_map_totals = projection_map_totals(
        (item.get("publish_readback") or {}).get("sdk_events_summary") for item in results
    )
    client_transcript_projection_map_totals = projection_map_totals(
        (item.get("publish_readback") or {}).get("client_transcript_summary") for item in results
    )
    projection_total_deltas = projection_count_deltas(
        sdk_events_projection_totals,
        client_transcript_projection_totals,
    )
    projection_total_delta_assessment = assess_projection_total_deltas(projection_total_deltas)
    projection_map_deltas = projection_map_count_deltas(
        sdk_events_projection_map_totals,
        client_transcript_projection_map_totals,
    )
    projection_map_delta_assessment = assess_projection_map_deltas(projection_map_deltas)
    provider_probe_valid = (
        provider_probe_validation.get("valid") is True
        if isinstance(provider_probe_validation, dict)
        else None
    )
    provider_probe_failed_checks = (
        list_value(provider_probe_validation.get("failed_checks"))
        if isinstance(provider_probe_validation, dict)
        else []
    )
    provider_probe_diagnostic_id = (
        provider_probe_validation.get("diagnostic_id")
        if isinstance(provider_probe_validation, dict)
        else None
    )
    provider_probe_summary = provider_probe_validation_summary_fields(provider_probe_validation)
    scenario_api_usage_totals = scenario_api_usage_total_maps(results)
    scenario_external_api_called = scenario_external_api_called_maps(results)
    api_usage_totals: dict[str, int | float] = {}
    for usage in scenario_api_usage_totals.values():
        for key, value in usage.items():
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            api_usage_totals[str(key)] = api_usage_totals.get(str(key), 0) + value
    completed = [item for item in results if scenario_passed(item, require_publish=publish_readback)]
    provider_allows_completion = provider_probe_valid is not False
    aggregate_failed_reasons = []
    if projection_total_delta_assessment.get("unexpected_delta_count"):
        aggregate_failed_reasons.append("unexpected_projection_total_deltas")
    if projection_map_delta_assessment.get("unexpected_delta_count"):
        aggregate_failed_reasons.append("unexpected_projection_map_deltas")
    if results and len(completed) == len(results) and provider_allows_completion and not aggregate_failed_reasons:
        status = "completed"
    elif provider_probe_valid is False:
        status = "failed"
    elif any((item.get("validation") or {}).get("status") == "configuration_required" for item in results):
        status = "configuration_required"
    else:
        status = "failed"
    return {
        "status": status,
        "scenario_count": len(results),
        "passed_count": len(completed),
        "failed_count": len(results) - len(completed),
        "scenario_status_counts": scenario_status_counts,
        "validation_status_counts": validation_status_counts,
        "publish_status_counts": publish_status_counts,
        "generic_readback_status_counts": generic_readback_status_counts,
        "projection_validation_status_counts": projection_validation_status_counts,
        "scenario_event_replay_status_counts": scenario_event_replay_status_counts,
        "provider_probe_valid": provider_probe_valid,
        "provider_probe_failed_checks": provider_probe_failed_checks,
        "provider_probe_diagnostic_id": provider_probe_diagnostic_id,
        "provider_probe_check_count": provider_probe_summary["provider_probe_check_count"],
        "provider_probe_checks": provider_probe_summary["provider_probe_checks"],
        "provider_probe_status": provider_probe_summary["provider_probe_status"],
        "provider_probe_http_status": provider_probe_summary["provider_probe_http_status"],
        "provider_probe_endpoint": provider_probe_summary["provider_probe_endpoint"],
        "provider_probe_models_preview_count": provider_probe_summary[
            "provider_probe_models_preview_count"
        ],
        "provider_probe_profile": provider_probe_summary["provider_probe_profile"],
        "provider_probe_agent_sdk_fit": provider_probe_summary["provider_probe_agent_sdk_fit"],
        "external_api_called": any(scenario_external_api_called.values()),
        "scenario_external_api_called": scenario_external_api_called,
        "api_usage_totals": api_usage_totals,
        "scenario_api_usage_totals": scenario_api_usage_totals,
        "published_task_ids": published_task_ids,
        "scenario_sdk_events_projection_counts": scenario_sdk_projection_counts,
        "scenario_client_transcript_projection_counts": scenario_client_projection_counts,
        "scenario_sdk_events_projection_maps": scenario_sdk_projection_maps,
        "scenario_client_transcript_projection_maps": scenario_client_projection_maps,
        "scenario_tool_state_summaries": scenario_tool_state_summaries,
        "scenario_tool_evidence_summaries": scenario_tool_evidence_summaries,
        "scenario_approval_evidence_summaries": scenario_approval_evidence_summaries,
        "scenario_handoff_evidence_summaries": scenario_handoff_evidence_summaries,
        "scenario_guardrail_evidence_summaries": scenario_guardrail_evidence_summaries,
        "scenario_session_evidence_summaries": scenario_session_evidence_summaries,
        "scenario_stream_evidence_summaries": scenario_stream_evidence_summaries,
        "scenario_tool_projection_summaries": scenario_tool_projection_summaries,
        "scenario_stream_projection_summaries": scenario_stream_projection_summaries,
        "scenario_handoff_state_summaries": scenario_handoff_state_summaries,
        "scenario_handoff_projection_summaries": scenario_handoff_projection_summaries,
        "scenario_approval_state_summaries": scenario_approval_state_summaries,
        "scenario_approval_projection_summaries": scenario_approval_projection_summaries,
        "scenario_session_state_summaries": scenario_session_state_summaries,
        "scenario_guardrail_tripwire_summaries": scenario_guardrail_tripwire_summaries,
        "scenario_guardrail_projection_summaries": scenario_guardrail_projection_summaries,
        "scenario_event_replay_expected_event_counts": replay_expected_event_counts,
        "scenario_event_replay_observed_event_counts": replay_observed_event_counts,
        "scenario_event_replay_event_counts": replay_event_counts,
        "scenario_event_replay_count_map_totals": replay_count_map_totals,
        "scenario_event_replay_count_map_total_totals": event_count_totals(
            replay_count_map_totals
        ),
        "scenario_event_replay_count_total_checks": replay_count_total_checks,
        "scenario_event_replay_sse_event_name_counts": replay_sse_event_name_counts,
        "scenario_event_replay_event_type_counts": replay_event_type_counts,
        "scenario_event_replay_event_status_counts": replay_event_status_counts,
        "scenario_event_replay_event_actor_counts": replay_event_actor_counts,
        "scenario_event_replay_sse_event_name_totals": event_count_totals(
            replay_sse_event_name_counts
        ),
        "scenario_event_replay_event_type_totals": event_count_totals(replay_event_type_counts),
        "scenario_event_replay_event_status_totals": event_count_totals(
            replay_event_status_counts
        ),
        "scenario_event_replay_event_actor_totals": event_count_totals(replay_event_actor_counts),
        "scenario_event_replay_expected_event_totals": event_count_totals(replay_expected_event_counts),
        "scenario_event_replay_observed_event_totals": event_count_totals(replay_observed_event_counts),
        "tool_call_status_update_expected_counts": tool_call_status_update_expected_counts,
        "tool_call_status_updated_event_counts": tool_call_status_updated_event_counts,
        "tool_call_status_update_expected_total": sum(tool_call_status_update_expected_counts.values()),
        "tool_call_status_updated_event_total": sum(tool_call_status_updated_event_counts.values()),
        "sdk_events_projection_totals": sdk_events_projection_totals,
        "client_transcript_projection_totals": client_transcript_projection_totals,
        "sdk_events_projection_map_totals": sdk_events_projection_map_totals,
        "client_transcript_projection_map_totals": client_transcript_projection_map_totals,
        "projection_total_deltas": projection_total_deltas,
        "projection_total_delta_status": projection_total_delta_assessment.get("status"),
        "projection_total_delta_unexpected_keys": projection_total_delta_assessment.get("unexpected_keys") or [],
        "projection_total_delta_allowed_keys": projection_total_delta_assessment.get("allowed_keys") or [],
        "projection_total_delta_assessment": projection_total_delta_assessment,
        "projection_map_deltas": projection_map_deltas,
        "projection_map_delta_status": projection_map_delta_assessment.get("status"),
        "projection_map_delta_unexpected_keys": projection_map_delta_assessment.get("unexpected_keys") or [],
        "projection_map_delta_assessment": projection_map_delta_assessment,
        "aggregate_failed_reasons": aggregate_failed_reasons,
        "failed_scenarios": [
            item.get("scenario")
            for item in results
            if not scenario_passed(item, require_publish=publish_readback)
        ],
        "scenario_failed_checks": scenario_failure_map(results, ("validation",)),
        "generic_readback_failed_checks": scenario_failure_map(results, ("publish_readback", "validation")),
        "projection_failed_checks": scenario_failure_map(
            results,
            ("publish_readback", "scenario_projection_validation"),
        ),
        "scenario_event_replay_failed_checks": scenario_failure_map(
            results,
            ("publish_readback", "scenario_event_replay_validation"),
        ),
        "generic_readback_failed_scenarios": [
            item.get("scenario")
            for item in results
            if item.get("publish_readback")
            and ((item.get("publish_readback") or {}).get("validation") or {}).get("valid") is not True
        ],
        "projection_failed_scenarios": [
            item.get("scenario")
            for item in results
            if item.get("publish_readback")
            and ((item.get("publish_readback") or {}).get("scenario_projection_validation") or {}).get("valid")
            is not True
        ],
        "scenario_event_replay_failed_scenarios": [
            item.get("scenario")
            for item in results
            if item.get("publish_readback")
            and ((item.get("publish_readback") or {}).get("scenario_event_replay_validation") or {}).get("valid")
            is not True
        ],
    }


def scenario_api_usage_total_maps(results: list[dict[str, Any]]) -> dict[str, dict[str, int | float]]:
    usage_by_scenario: dict[str, dict[str, int | float]] = {}
    for item in results:
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        usage_by_scenario[str(item.get("scenario") or "unknown")] = normalized_numeric_map(
            summary.get("raw_response_usage_totals")
        )
    return usage_by_scenario


def scenario_external_api_called_maps(results: list[dict[str, Any]]) -> dict[str, bool]:
    called_by_scenario: dict[str, bool] = {}
    for item in results:
        scenario = str(item.get("scenario") or "unknown")
        if isinstance(item.get("external_api_called"), bool):
            called_by_scenario[scenario] = item.get("external_api_called") is True
            continue
        validation = item.get("validation") if isinstance(item.get("validation"), dict) else {}
        checks = validation.get("checks") if isinstance(validation.get("checks"), dict) else {}
        if "external_api_called" in checks:
            called_by_scenario[scenario] = checks.get("external_api_called") is True
    return called_by_scenario


def normalized_numeric_map(value: Any) -> dict[str, int | float]:
    if not isinstance(value, dict):
        return {}
    numeric: dict[str, int | float] = {}
    for key, item in sorted(value.items()):
        if isinstance(item, bool) or not isinstance(item, int | float):
            continue
        numeric[str(key)] = item
    return numeric


def scenario_replay_expected_event_count_maps(results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return scenario_replay_event_count_maps(results, "expected_event_counts")


def scenario_replay_observed_event_count_maps(results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return scenario_replay_event_count_maps(results, "observed_event_counts")


def scenario_replay_raw_event_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        validation = (
            publish_readback.get("scenario_event_replay_validation")
            if isinstance(publish_readback.get("scenario_event_replay_validation"), dict)
            else {}
        )
        if not validation:
            continue
        event_summary = (
            validation.get("event_replay_summary")
            if isinstance(validation.get("event_replay_summary"), dict)
            else {}
        )
        counts[str(item.get("scenario") or "unknown")] = int_value(event_summary.get("event_count"))
    return counts


def scenario_replay_count_map_total_maps(results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for item in results:
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        validation = (
            publish_readback.get("scenario_event_replay_validation")
            if isinstance(publish_readback.get("scenario_event_replay_validation"), dict)
            else {}
        )
        if not validation:
            continue
        counts[str(item.get("scenario") or "unknown")] = normalized_event_count_map(
            validation.get("event_replay_count_map_totals")
        )
    return counts


def scenario_replay_count_total_check_maps(results: list[dict[str, Any]]) -> dict[str, dict[str, bool]]:
    checks_by_scenario: dict[str, dict[str, bool]] = {}
    for item in results:
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        validation = (
            publish_readback.get("scenario_event_replay_validation")
            if isinstance(publish_readback.get("scenario_event_replay_validation"), dict)
            else {}
        )
        if not validation:
            continue
        checks_by_scenario[str(item.get("scenario") or "unknown")] = event_replay_count_total_check_map(
            validation
        )
    return checks_by_scenario


def scenario_replay_event_count_maps(results: list[dict[str, Any]], field: str) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for item in results:
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        validation = (
            publish_readback.get("scenario_event_replay_validation")
            if isinstance(publish_readback.get("scenario_event_replay_validation"), dict)
            else {}
        )
        counts[str(item.get("scenario") or "unknown")] = normalized_event_count_map(validation.get(field))
    return counts


def scenario_replay_summary_count_maps(results: list[dict[str, Any]], field: str) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for item in results:
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        validation = (
            publish_readback.get("scenario_event_replay_validation")
            if isinstance(publish_readback.get("scenario_event_replay_validation"), dict)
            else {}
        )
        event_summary = (
            validation.get("event_replay_summary")
            if isinstance(validation.get("event_replay_summary"), dict)
            else {}
        )
        counts[str(item.get("scenario") or "unknown")] = normalized_event_count_map(
            event_summary.get(field)
        )
    return counts


def normalized_event_count_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int_value(count) for key, count in sorted(value.items())}


def event_replay_count_total_check_map(validation: Any) -> dict[str, bool]:
    if not isinstance(validation, dict):
        return {}
    checks = validation.get("checks") if isinstance(validation.get("checks"), dict) else {}
    return {
        key: checks.get(key) is True
        for key in EVENT_REPLAY_COUNT_TOTAL_CHECK_KEYS
        if key in checks
    }


def event_count_map_total(value: Any) -> int:
    return sum(normalized_event_count_map(value).values())


def projection_map_counts_known(summary: dict[str, Any], map_key: str, count_key: str) -> bool:
    count = int_value(summary.get(count_key))
    values = normalized_event_count_map(summary.get(map_key))
    if count <= 0:
        return not values
    if not values or "none" in values:
        return False
    return sum(values.values()) >= count


def event_count_totals(counts_by_scenario: dict[str, dict[str, int]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for counts in counts_by_scenario.values():
        if not isinstance(counts, dict):
            continue
        for key, value in counts.items():
            totals[str(key)] = totals.get(str(key), 0) + int_value(value)
    return {key: totals[key] for key in sorted(totals)}


def scenario_projection_count_maps(results: list[dict[str, Any]], summary_field: str) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for item in results:
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        counts[str(item.get("scenario") or "unknown")] = projection_count_map(
            publish_readback.get(summary_field)
        )
    return counts


def scenario_projection_map_summaries(
    results: list[dict[str, Any]],
    summary_field: str,
) -> dict[str, dict[str, dict[str, int]]]:
    maps: dict[str, dict[str, dict[str, int]]] = {}
    for item in results:
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        projected = projection_map_summary(publish_readback.get(summary_field))
        if projected:
            maps[str(item.get("scenario") or "unknown")] = projected
    return maps


def scenario_stream_projection_summary_maps(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in results:
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        stream_summary = compact_stream_projection_summary(publish_readback)
        if stream_summary:
            summaries[str(item.get("scenario") or "unknown")] = stream_summary
    return summaries


def scenario_guardrail_projection_summary_maps(
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for item in results:
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        guardrail_summary = compact_guardrail_projection_summary(publish_readback)
        if guardrail_summary:
            summaries[str(item.get("scenario") or "unknown")] = guardrail_summary
    return summaries


def scenario_tool_call_status_update_expected_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        result_summary = (
            publish_readback.get("result_summary")
            if isinstance(publish_readback.get("result_summary"), dict)
            else {}
        )
        counts[str(item.get("scenario") or "unknown")] = int_value(
            result_summary.get("tool_call_status_update_expected_count")
        )
    return counts


def scenario_tool_call_status_updated_event_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        publish_readback = item.get("publish_readback") if isinstance(item.get("publish_readback"), dict) else {}
        if not publish_readback:
            continue
        validation = (
            publish_readback.get("scenario_event_replay_validation")
            if isinstance(publish_readback.get("scenario_event_replay_validation"), dict)
            else {}
        )
        event_summary = (
            validation.get("event_replay_summary")
            if isinstance(validation.get("event_replay_summary"), dict)
            else {}
        )
        event_type_counts = (
            event_summary.get("event_type_counts")
            if isinstance(event_summary.get("event_type_counts"), dict)
            else {}
        )
        counts[str(item.get("scenario") or "unknown")] = int_value(
            event_type_counts.get("tool_call.status_updated")
        )
    return counts


def scenario_failure_map(results: list[dict[str, Any]], path: tuple[str, ...]) -> dict[str, list[Any]]:
    failures: dict[str, list[Any]] = {}
    for item in results:
        scenario = str(item.get("scenario") or "unknown")
        current: Any = item
        for key in path:
            current = current.get(key) if isinstance(current, dict) else None
        failed_checks = list_value(current.get("failed_checks")) if isinstance(current, dict) else []
        if failed_checks:
            failures[scenario] = failed_checks
    return failures


def scenario_passed(result: dict[str, Any], *, require_publish: bool) -> bool:
    if result.get("passed") is not True:
        return False
    if not require_publish:
        return True
    return (result.get("publish_readback") or {}).get("status") == "completed"


def projection_count_totals(summaries: Any) -> dict[str, int]:
    totals = {key: 0 for key in PROJECTION_COUNT_KEYS}
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        for key in PROJECTION_COUNT_KEYS:
            totals[key] += int_value(summary.get(key))
    return {key: value for key, value in totals.items() if value}


def projection_count_map(summary: Any) -> dict[str, int]:
    if not isinstance(summary, dict):
        return {}
    counts = {key: int_value(summary.get(key)) for key in PROJECTION_COUNT_KEYS}
    return {key: value for key, value in counts.items() if value}


def projection_map_summary(summary: Any) -> dict[str, dict[str, int]]:
    if not isinstance(summary, dict):
        return {}
    maps: dict[str, dict[str, int]] = {}
    for key in PROJECTION_MAP_KEYS:
        values = normalized_event_count_map(summary.get(key))
        if values:
            maps[key] = values
    return maps


def projection_map_totals(summaries: Any) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = {}
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        for map_key, values in projection_map_summary(summary).items():
            bucket = totals.setdefault(map_key, {})
            for key, value in values.items():
                bucket[key] = bucket.get(key, 0) + int_value(value)
    return {
        map_key: {key: values[key] for key in sorted(values)}
        for map_key, values in sorted(totals.items())
        if values
    }


def projection_count_deltas(
    sdk_events_totals: dict[str, int],
    client_transcript_totals: dict[str, int],
) -> dict[str, dict[str, int]]:
    deltas: dict[str, dict[str, int]] = {}
    for key in sorted(set(sdk_events_totals) | set(client_transcript_totals)):
        sdk_value = int_value(sdk_events_totals.get(key))
        transcript_value = int_value(client_transcript_totals.get(key))
        if sdk_value == transcript_value:
            continue
        deltas[key] = {
            "sdk_events": sdk_value,
            "client_transcript": transcript_value,
            "delta": sdk_value - transcript_value,
        }
    return deltas


def projection_map_count_deltas(
    sdk_events_totals: dict[str, dict[str, int]],
    client_transcript_totals: dict[str, dict[str, int]],
) -> dict[str, dict[str, dict[str, int]]]:
    deltas: dict[str, dict[str, dict[str, int]]] = {}
    for map_key in sorted(set(sdk_events_totals) | set(client_transcript_totals)):
        sdk_values = sdk_events_totals.get(map_key) if isinstance(sdk_events_totals.get(map_key), dict) else {}
        transcript_values = (
            client_transcript_totals.get(map_key)
            if isinstance(client_transcript_totals.get(map_key), dict)
            else {}
        )
        nested: dict[str, dict[str, int]] = {}
        for key in sorted(set(sdk_values) | set(transcript_values)):
            sdk_value = int_value(sdk_values.get(key))
            transcript_value = int_value(transcript_values.get(key))
            if sdk_value == transcript_value:
                continue
            nested[key] = {
                "sdk_events": sdk_value,
                "client_transcript": transcript_value,
                "delta": sdk_value - transcript_value,
            }
        if nested:
            deltas[map_key] = nested
    return deltas


def assess_projection_total_deltas(projection_total_deltas: dict[str, dict[str, int]]) -> dict[str, Any]:
    items: dict[str, dict[str, Any]] = {}
    allowed_keys: list[str] = []
    unexpected_keys: list[str] = []
    for key, delta in sorted(projection_total_deltas.items()):
        sdk_value = int_value(delta.get("sdk_events"))
        transcript_value = int_value(delta.get("client_transcript"))
        signed_delta = int_value(delta.get("delta"))
        allowed, reason = projection_delta_allowance(key, signed_delta)
        status = "allowed" if allowed else "unexpected"
        if allowed:
            allowed_keys.append(key)
        else:
            unexpected_keys.append(key)
        items[key] = {
            "sdk_events": sdk_value,
            "client_transcript": transcript_value,
            "delta": signed_delta,
            "status": status,
            "reason": reason,
        }
    if unexpected_keys:
        status = "unexpected_deltas"
    elif allowed_keys:
        status = "allowed_deltas"
    else:
        status = "no_deltas"
    return {
        "status": status,
        "allowed_keys": allowed_keys,
        "unexpected_keys": unexpected_keys,
        "allowed_delta_count": len(allowed_keys),
        "unexpected_delta_count": len(unexpected_keys),
        "items": items,
        "claim_boundary": (
            "Assessment classifies known projection-count differences between SDK-event and "
            "client-transcript read models; unexpected deltas make the aggregate matrix gate fail."
        ),
    }


def assess_projection_map_deltas(
    projection_map_deltas: dict[str, dict[str, dict[str, int]]],
) -> dict[str, Any]:
    unexpected_keys = [
        f"{map_key}.{key}"
        for map_key, values in sorted(projection_map_deltas.items())
        for key in sorted(values)
    ]
    return {
        "status": "unexpected_deltas" if unexpected_keys else "no_deltas",
        "unexpected_keys": unexpected_keys,
        "unexpected_delta_count": len(unexpected_keys),
        "items": projection_map_deltas,
        "claim_boundary": (
            "Assessment compares nested SDK-event and client-transcript projection maps such as "
            "model-response provider and API-style counts; any delta is unexpected."
        ),
    }


def projection_delta_allowance(key: str, signed_delta: int) -> tuple[bool, str]:
    if key == "tool_progress_count" and signed_delta >= 0:
        return (
            True,
            "SDK-event projection is lifecycle-oriented and may retain more tool progress records "
            "than the compact client-transcript projection.",
        )
    return (
        False,
        "No allowance is registered for this projection-count delta; inspect read-model drift before "
        "accepting the aggregate gate.",
    )


def scenario_output_path(output_dir: Path, run_id: str, scenario: str) -> Path:
    return output_dir / f"agents-sdk-{scenario}-{run_id}.json"


def aggregate_evidence_source_path(matrix_output: Path) -> Path:
    return matrix_output.with_name(f"{matrix_output.stem}.evidence-source{matrix_output.suffix}")


def read_json_if_present(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def dict_value(mapping: dict[str, Any], key: str, nested_key: str) -> Any:
    value = mapping.get(key)
    if not isinstance(value, dict):
        return None
    return value.get(nested_key)


def summary_map_int(summary: dict[str, Any], key: str, nested_key: str) -> int:
    return int_value(dict_value(summary, key, nested_key))


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def count_values(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def tail_text(value: str, *, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


if __name__ == "__main__":
    raise SystemExit(main())
