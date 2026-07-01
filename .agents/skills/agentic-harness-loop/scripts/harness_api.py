#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/harness/v1"
PROBE_PROVIDER_DEFAULTS = {
    "openai": {
        "api_style": "responses",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "supports_tool_calling": True,
        "supports_stateful_responses": True,
    },
    "ollama": {
        "api_style": "chat_completions",
        "base_url": "http://localhost:11434/v1",
        "api_key_env": None,
        "supports_tool_calling": True,
        "supports_stateful_responses": False,
    },
    "lm_studio": {
        "api_style": "chat_completions",
        "base_url": "http://localhost:1234/v1",
        "api_key_env": None,
        "supports_tool_calling": True,
        "supports_stateful_responses": False,
    },
    "vllm_or_other_openai_compatible": {
        "api_style": "chat_completions",
        "base_url": "http://localhost:8000/v1",
        "api_key_env": None,
        "supports_tool_calling": True,
        "supports_stateful_responses": False,
    },
}
ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from developer_harness.read_model_summary import summarize_client_transcript, summarize_sdk_events

CAPABILITIES_SUMMARY_COMMAND = "capabilities --summary true"
READ_MODEL_CAPABILITIES_GATE_COMMAND = (
    "capabilities --summary true "
    "--require-sdk-event-type agent_eval_case_verification "
    "--require-sdk-event-type harness_verification_result "
    "--require-transcript-section agent_eval_case_verifications "
    "--require-transcript-section harness_verification_results "
    "--require-summary-field agent_eval_case_verification_count "
    "--require-summary-field harness_verification_result_count"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Call the com2us development harness API.")
    parser.add_argument("--base-url", default=os.getenv("HARNESS_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--client-id", default=os.getenv("HARNESS_CLIENT_ID"))
    parser.add_argument("--api-key", default=os.getenv("HARNESS_API_KEY"))
    parser.add_argument("--dry-run", action="store_true", help="Print the request without sending it.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health")
    sub.add_parser("ready")
    capabilities = sub.add_parser("capabilities")
    capabilities.add_argument("--summary", choices=["true", "false"], default="false")
    capabilities.add_argument("--require-sdk-event-type", action="append", default=[])
    capabilities.add_argument("--require-transcript-section", action="append", default=[])
    capabilities.add_argument("--require-summary-field", action="append", default=[])
    sub.add_parser("sdk-initialize")
    sub.add_parser("sdk-mcp-status")
    sub.add_parser("sdk-context-usage")
    sub.add_parser("providers")
    diagnostics = sub.add_parser("diagnostics")
    diagnostics.add_argument("--limit", type=int, default=50)
    diagnostics.add_argument("--expect-diagnostic-id", type=int)
    diagnostics.add_argument("--expect-status")
    diagnostics.add_argument("--expect-provider")
    diagnostics.add_argument("--expect-model")
    diagnostics.add_argument("--expect-api-style", choices=["responses", "chat_completions"])
    diagnostics.add_argument("--expect-provider-request-attempted", choices=["true", "false"])
    diagnostics.add_argument("--expect-provider-response-received", choices=["true", "false"])
    diagnostics.add_argument("--expect-external-api-called", choices=["true", "false"])
    sub.add_parser("tasks")
    sub.add_parser("sessions")
    sub.add_parser("eval-cases")
    sub.add_parser("loops")

    eval_case_verify = sub.add_parser("eval-case-verify")
    eval_case_verify.add_argument("--eval-case-id", type=int)
    eval_case_verify.add_argument("--name")
    eval_case_verify.add_argument("--failure-label")
    eval_case_verify.add_argument("--limit", type=int, default=50)
    eval_case_verify.add_argument("--cwd", default=str(ROOT))
    eval_case_verify.add_argument("--timeout-seconds", type=int, default=120)
    eval_case_verify.add_argument(
        "--record-result-task-id",
        help=(
            "Optional task id that should receive an eval_case_verification observation "
            "with the selected eval case and verifier result."
        ),
    )
    eval_case_verify.add_argument(
        "--execute",
        action="store_true",
        help="Actually run the selected eval case verifier_command. Omit to inspect only.",
    )

    plan = sub.add_parser("plan")
    plan.add_argument(
        "--goal",
        required=True,
        choices=["provider", "task", "tool-evidence", "eval", "worker", "subagent", "loop", "trace", "failure"],
    )
    plan.add_argument(
        "--status",
        choices=["configuration_required", "failed", "not_connected", "completed", "404", "409", "api_unreachable"],
    )

    probe = sub.add_parser("probe")
    probe.add_argument("--provider", default="ollama")
    probe.add_argument("--api-style", choices=["responses", "chat_completions"])
    probe.add_argument("--base-url", dest="provider_base_url")
    probe.add_argument("--model")
    probe.add_argument("--api-key-env")
    probe.add_argument("--supports-tool-calling", choices=["true", "false"])
    probe.add_argument("--supports-stateful-responses", choices=["true", "false"])
    probe.add_argument("--timeout-seconds", type=int, default=5)

    task = sub.add_parser("task")
    task.add_argument("--prompt", required=True)
    task.add_argument("--cwd", required=True)
    task.add_argument("--sandbox", default="workspace-write", choices=["read-only", "workspace-write", "danger-full-access"])
    task.add_argument("--agent")
    task.add_argument("--skill", action="append", default=[])
    task.add_argument("--verification-command", action="append", default=[])
    task.add_argument("--metadata-json", default="{}")
    task.add_argument("--idempotency-key")
    task.add_argument("--background", default="false", choices=["true", "false"])
    task.add_argument("--start", default="true", choices=["true", "false"])

    loop_create = sub.add_parser("loop-create")
    loop_create.add_argument("--name", required=True)
    loop_create.add_argument("--prompt", required=True)
    loop_create.add_argument("--cwd", required=True)
    loop_create.add_argument("--sandbox", default="read-only", choices=["read-only", "workspace-write", "danger-full-access"])
    loop_create.add_argument("--agent")
    loop_create.add_argument("--skill", action="append", default=[])
    loop_create.add_argument("--verification-command", action="append", default=[])
    loop_create.add_argument("--metadata-json", default="{}")
    loop_create.add_argument("--idempotency-key")
    loop_create.add_argument("--interval-seconds", type=int, default=600)
    loop_create.add_argument("--max-iterations", type=int, default=12)
    loop_create.add_argument("--trace-source", action="append", default=[])
    loop_create.add_argument("--eval-target", action="append", default=[])
    loop_create.add_argument("--termination-condition", action="append", default=[])

    loop_tick = sub.add_parser("loop-tick")
    loop_tick.add_argument("--loop-id", required=True)
    loop_tick.add_argument("--force", default="false", choices=["true", "false"])
    loop_tick.add_argument("--start", default="false", choices=["true", "false"])
    loop_tick.add_argument("--background", default="true", choices=["true", "false"])

    loop_evidence = sub.add_parser("loop-evidence")
    loop_evidence.add_argument("--loop-id", required=True)

    annotate = sub.add_parser("annotate")
    annotate.add_argument("--task-id", required=True)
    annotate.add_argument("--title")
    annotate.add_argument("--tag")
    annotate.add_argument("--summary")
    annotate.add_argument("--expected-state-version", type=int)

    fork = sub.add_parser("fork")
    fork.add_argument("--task-id", required=True)
    fork.add_argument("--prompt")
    fork.add_argument("--idempotency-key")
    fork.add_argument("--expected-state-version", type=int)
    fork.add_argument("--metadata-json", default="{}")

    command = sub.add_parser("command")
    command.add_argument("--task-id", required=True)
    command.add_argument("--command", dest="task_command", required=True, choices=["pause", "resume", "cancel"])
    command.add_argument("--expected-state-version", type=int)

    reply = sub.add_parser("reply")
    reply.add_argument("--task-id", required=True)
    reply.add_argument("--prompt", required=True)
    reply.add_argument("--expected-state-version", type=int)
    reply.add_argument("--background", default="true", choices=["true", "false"])

    subagents = sub.add_parser("subagents")
    subagents.add_argument("--task-id", required=True)

    subagent = sub.add_parser("subagent")
    subagent.add_argument("--task-id", required=True)
    subagent.add_argument("--prompt", required=True)
    subagent.add_argument("--cwd", required=True)
    subagent.add_argument("--sandbox", default="workspace-write", choices=["read-only", "workspace-write", "danger-full-access"])
    subagent.add_argument("--agent")
    subagent.add_argument("--skill", action="append", default=[])
    subagent.add_argument("--verification-command", action="append", default=[])
    subagent.add_argument("--metadata-json", default="{}")
    subagent.add_argument("--idempotency-key")
    subagent.add_argument("--background", default="false", choices=["true", "false"])
    subagent.add_argument("--join-policy", default="manual", choices=["manual", "all", "any"])

    evidence = sub.add_parser("evidence")
    evidence.add_argument("--task-id", required=True)

    events = sub.add_parser("events")
    events.add_argument("--task-id", required=True)
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--max-events", type=int, default=50)
    events.add_argument("--timeout-seconds", type=float, default=5.0)
    events.add_argument("--include-keepalive", default="false", choices=["true", "false"])

    task_info = sub.add_parser("task-info")
    task_info.add_argument("--task-id", required=True)

    session_info = sub.add_parser("session-info")
    session_info.add_argument("--task-id", required=True)

    transcript = sub.add_parser("client-transcript")
    transcript.add_argument("--task-id", required=True)
    transcript.add_argument("--summary", default="false", choices=["true", "false"])

    sdk_events = sub.add_parser("sdk-events")
    sdk_events.add_argument("--task-id", required=True)
    sdk_events.add_argument("--after", type=int, default=0)
    sdk_events.add_argument("--summary", default="false", choices=["true", "false"])

    progress = sub.add_parser("progress")
    progress.add_argument("--task-id", required=True)
    progress.add_argument("--items-json", required=True)
    progress.add_argument("--expected-state-version", type=int)
    progress.add_argument("--metadata-json", default="{}")

    queued_message = sub.add_parser("queued-message")
    queued_message.add_argument("--task-id", required=True)
    queued_message.add_argument("--prompt", required=True)
    queued_message.add_argument("--priority", default="later", choices=["now", "next", "later"])
    queued_message.add_argument("--message-uuid")
    queued_message.add_argument("--expected-state-version", type=int)
    queued_message.add_argument("--metadata-json", default="{}")

    cancel_queued_message = sub.add_parser("cancel-queued-message")
    cancel_queued_message.add_argument("--task-id", required=True)
    cancel_queued_message.add_argument("--message-uuid", required=True)
    cancel_queued_message.add_argument("--expected-state-version", type=int)

    tool_call = sub.add_parser("tool-call")
    tool_call.add_argument("--task-id", required=True)
    tool_call.add_argument("--tool-name", required=True)
    tool_call.add_argument("--call-id")
    tool_call.add_argument("--input-json", default="{}")
    tool_call.add_argument("--status", default="completed")

    observation = sub.add_parser("observation")
    observation.add_argument("--task-id", required=True)
    observation.add_argument("--tool-name", required=True)
    observation.add_argument("--call-id")
    observation.add_argument("--result-json", default="{}")
    observation.add_argument("--evidence-ref", action="append", default=[])
    observation.add_argument("--status", default="completed")

    permission_decision = sub.add_parser("permission-decision")
    permission_decision.add_argument("--task-id", required=True)
    permission_decision.add_argument("--tool-name", required=True)
    permission_decision.add_argument("--behavior", required=True, choices=["allow", "ask", "deny", "passthrough"])
    permission_decision.add_argument("--call-id")
    permission_decision.add_argument("--input-json", default="{}")
    permission_decision.add_argument("--updated-input-json")
    permission_decision.add_argument("--decision-reason-json", default="{}")
    permission_decision.add_argument("--message")
    permission_decision.add_argument("--blocked-path")
    permission_decision.add_argument("--suggestions-json", default="[]")
    permission_decision.add_argument("--status", default="recorded", choices=["recorded", "applied", "superseded"])
    permission_decision.add_argument("--metadata-json", default="{}")

    artifact = sub.add_parser("artifact")
    artifact.add_argument("--task-id", required=True)
    artifact.add_argument("--kind", required=True)
    artifact.add_argument("--path")
    artifact.add_argument("--url")
    artifact.add_argument("--digest")
    artifact.add_argument("--metadata-json", default="{}")

    eval_case = sub.add_parser("eval-case")
    eval_case.add_argument("--name", required=True)
    eval_case.add_argument("--source-task-id")
    eval_case.add_argument("--failure-label")
    eval_case.add_argument("--input-json", default="{}")
    eval_case.add_argument("--expected-json", default="{}")
    eval_case.add_argument("--verifier-command")
    eval_case.add_argument("--evidence-ref", action="append", default=[])

    heartbeat = sub.add_parser("heartbeat")
    heartbeat.add_argument("--task-id", required=True)
    heartbeat.add_argument("--lease-epoch", type=int, required=True)
    heartbeat.add_argument("--lease-seconds", type=int, default=300)

    sub.add_parser("worker-tick")

    args = parser.parse_args(normalize_global_flags(sys.argv[1:]))
    method, path, body, needs_client = build_request(args)
    if needs_client and not args.client_id:
        raise SystemExit("HARNESS_CLIENT_ID or --client-id is required for this command.")
    result = request(args, method, path, body)
    result = summarize_read_model(args, result)
    result, exit_code = apply_command_validation(args, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


def normalize_global_flags(argv: list[str]) -> list[str]:
    """Accept selected global flags before or after the subcommand."""
    if "--dry-run" not in argv:
        return argv
    return ["--dry-run", *[item for item in argv if item != "--dry-run"]]


def build_request(args: argparse.Namespace) -> tuple[str, str, dict[str, Any] | None, bool]:
    if args.command == "health":
        return "GET", "/health", None, False
    if args.command == "ready":
        return "GET", "/ready", None, False
    if args.command == "capabilities":
        return "GET", "/capabilities", None, False
    if args.command == "sdk-initialize":
        return "GET", "/sdk/initialize", None, True
    if args.command == "sdk-mcp-status":
        return "GET", "/sdk/mcp-status", None, True
    if args.command == "sdk-context-usage":
        return "GET", "/sdk/context-usage", None, True
    if args.command == "providers":
        return "GET", "/llm/providers", None, False
    if args.command == "diagnostics":
        if diagnostics_field_expectations(args) and not int(getattr(args, "expect_diagnostic_id", 0) or 0):
            raise SystemExit("--expect-diagnostic-id is required when checking diagnostic fields.")
        limit = max(1, min(int(args.limit or 50), 200))
        return "GET", f"/llm/diagnostics?limit={limit}", None, True
    if args.command == "tasks":
        return "GET", "/tasks", None, True
    if args.command == "sessions":
        return "GET", "/sessions", None, True
    if args.command == "eval-cases":
        return "GET", "/eval-cases", None, True
    if args.command == "eval-case-verify":
        limit = max(1, min(int(args.limit or 50), 200))
        return "GET", f"/eval-cases?limit={limit}", None, True
    if args.command == "loops":
        return "GET", "/loops", None, True
    if args.command == "plan":
        return "PLAN", "", build_plan(args), False
    if args.command == "probe":
        provider = resolved_probe_provider(args)
        return "POST", "/llm/probe", {
            "provider": provider,
            "timeout_seconds": args.timeout_seconds,
        }, True
    if args.command == "task":
        return "POST", f"/tasks?background={args.background}&start={args.start}", {
            "prompt": args.prompt,
            "cwd": args.cwd,
            "sandbox": args.sandbox,
            "agent": args.agent,
            "skills": args.skill,
            "idempotency_key": args.idempotency_key,
            "metadata": parse_json(args.metadata_json),
            "verification": {"commands": args.verification_command, "timeout_seconds": 120},
        }, True
    if args.command == "loop-create":
        return "POST", "/loops", {
            "name": args.name,
            "task": {
                "prompt": args.prompt,
                "cwd": args.cwd,
                "sandbox": args.sandbox,
                "agent": args.agent,
                "skills": args.skill,
                "idempotency_key": args.idempotency_key,
                "metadata": parse_json(args.metadata_json),
                "verification": {"commands": args.verification_command, "timeout_seconds": 120},
            },
            "interval_seconds": args.interval_seconds,
            "max_iterations": args.max_iterations,
            "evidence_contract": {
                "trace_sources": args.trace_source,
                "eval_targets": args.eval_target,
                "verification_commands": args.verification_command,
                "termination_conditions": args.termination_condition,
            },
        }, True
    if args.command == "loop-tick":
        return "POST", f"/loops/{args.loop_id}/tick", {
            "force": args.force == "true",
            "start": args.start == "true",
            "background": args.background == "true",
        }, True
    if args.command == "loop-evidence":
        return "GET", f"/loops/{args.loop_id}/evidence", None, True
    if args.command == "annotate":
        return "POST", f"/tasks/{args.task_id}/annotations", {
            "title": args.title,
            "tag": args.tag,
            "summary": args.summary,
            "expected_state_version": args.expected_state_version,
        }, True
    if args.command == "fork":
        return "POST", f"/tasks/{args.task_id}/fork", {
            "prompt": args.prompt,
            "idempotency_key": args.idempotency_key,
            "expected_state_version": args.expected_state_version,
            "metadata": parse_json(args.metadata_json),
        }, True
    if args.command == "command":
        return "POST", f"/tasks/{args.task_id}/commands", {
            "command": args.task_command,
            "expected_state_version": args.expected_state_version,
        }, True
    if args.command == "reply":
        return "POST", f"/tasks/{args.task_id}/reply?background={args.background}", {
            "prompt": args.prompt,
            "expected_state_version": args.expected_state_version,
        }, True
    if args.command == "subagents":
        return "GET", f"/tasks/{args.task_id}/subagents", None, True
    if args.command == "subagent":
        return "POST", f"/tasks/{args.task_id}/subagents?background={args.background}", {
            "prompt": args.prompt,
            "cwd": args.cwd,
            "sandbox": args.sandbox,
            "agent": args.agent,
            "skills": args.skill,
            "idempotency_key": args.idempotency_key,
            "join_policy": args.join_policy,
            "metadata": parse_json(args.metadata_json),
            "verification": {"commands": args.verification_command, "timeout_seconds": 120},
        }, True
    if args.command == "evidence":
        return "GET", f"/tasks/{args.task_id}/evidence", None, True
    if args.command == "events":
        return "SSE", f"/tasks/{args.task_id}/events?after={args.after}", None, True
    if args.command == "task-info":
        return "GET", f"/tasks/{args.task_id}", None, True
    if args.command == "session-info":
        return "GET", f"/tasks/{args.task_id}/session-info", None, True
    if args.command == "client-transcript":
        suffix = "?summary=true" if getattr(args, "summary", "false") == "true" else ""
        return "GET", f"/tasks/{args.task_id}/client-transcript{suffix}", None, True
    if args.command == "sdk-events":
        suffix = f"?after={args.after}"
        if getattr(args, "summary", "false") == "true":
            suffix += "&summary=true"
        return "GET", f"/tasks/{args.task_id}/sdk-events{suffix}", None, True
    if args.command == "progress":
        return "POST", f"/tasks/{args.task_id}/progress", {
            "items": parse_json_list(args.items_json),
            "expected_state_version": args.expected_state_version,
            "metadata": parse_json(args.metadata_json),
        }, True
    if args.command == "queued-message":
        return "POST", f"/tasks/{args.task_id}/queued-messages", {
            "prompt": args.prompt,
            "priority": args.priority,
            "message_uuid": args.message_uuid,
            "expected_state_version": args.expected_state_version,
            "metadata": parse_json(args.metadata_json),
        }, True
    if args.command == "cancel-queued-message":
        return "POST", f"/tasks/{args.task_id}/queued-messages/cancel", {
            "message_uuid": args.message_uuid,
            "expected_state_version": args.expected_state_version,
        }, True
    if args.command == "tool-call":
        return "POST", f"/tasks/{args.task_id}/tool-calls", {
            "call_id": args.call_id,
            "tool_name": args.tool_name,
            "input": parse_json(args.input_json),
            "status": args.status,
        }, True
    if args.command == "observation":
        return "POST", f"/tasks/{args.task_id}/observations", {
            "call_id": args.call_id,
            "tool_name": args.tool_name,
            "result": parse_json(args.result_json),
            "evidence_refs": args.evidence_ref,
            "status": args.status,
        }, True
    if args.command == "permission-decision":
        return "POST", f"/tasks/{args.task_id}/permission-decisions", {
            "tool_name": args.tool_name,
            "behavior": args.behavior,
            "call_id": args.call_id,
            "input": parse_json(args.input_json),
            "updated_input": parse_json(args.updated_input_json) if args.updated_input_json else None,
            "decision_reason": parse_json(args.decision_reason_json),
            "message": args.message,
            "blocked_path": args.blocked_path,
            "suggestions": parse_json_list(args.suggestions_json),
            "status": args.status,
            "metadata": parse_json(args.metadata_json),
        }, True
    if args.command == "artifact":
        return "POST", f"/tasks/{args.task_id}/artifacts", {
            "kind": args.kind,
            "path": args.path,
            "url": args.url,
            "digest": args.digest,
            "metadata": parse_json(args.metadata_json),
        }, True
    if args.command == "eval-case":
        return "POST", "/eval-cases", {
            "name": args.name,
            "source_task_id": args.source_task_id,
            "failure_label": args.failure_label,
            "input": parse_json(args.input_json),
            "expected": parse_json(args.expected_json),
            "verifier_command": args.verifier_command,
            "evidence_refs": args.evidence_ref,
        }, True
    if args.command == "heartbeat":
        return "POST", "/workers/heartbeat", {
            "task_id": args.task_id,
            "lease_epoch": args.lease_epoch,
            "lease_seconds": args.lease_seconds,
        }, True
    if args.command == "worker-tick":
        return "POST", "/workers/tick", None, True
    raise SystemExit(f"unsupported command: {args.command}")


def resolved_probe_provider(args: argparse.Namespace) -> dict[str, Any]:
    provider = str(args.provider or "ollama")
    defaults = PROBE_PROVIDER_DEFAULTS.get(provider, PROBE_PROVIDER_DEFAULTS["ollama"])
    return {
        "provider": provider,
        "api_style": args.api_style or defaults["api_style"],
        "base_url": args.provider_base_url or defaults["base_url"],
        "model": args.model,
        "api_key_env": args.api_key_env if args.api_key_env is not None else defaults["api_key_env"],
        "supports_tool_calling": bool_choice(
            args.supports_tool_calling,
            default=bool(defaults["supports_tool_calling"]),
        ),
        "supports_stateful_responses": bool_choice(
            args.supports_stateful_responses,
            default=bool(defaults["supports_stateful_responses"]),
        ),
    }


def bool_choice(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value == "true"


def optional_bool_choice(value: str | None) -> bool | None:
    if value is None:
        return None
    return value == "true"


def parse_json(value: str) -> dict[str, Any]:
    parsed = parse_json_value(value)
    if not isinstance(parsed, dict):
        raise SystemExit("JSON value must be an object.")
    return parsed


def parse_json_list(value: str) -> list[Any]:
    parsed = parse_json_value(value)
    if not isinstance(parsed, list):
        raise SystemExit("JSON value must be an array.")
    return parsed


def parse_json_value(value: str) -> Any:
    if value.startswith("@"):
        path = value[1:]
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                value = handle.read()
        except OSError as exc:
            raise SystemExit(f"cannot read JSON file {path}: {exc}") from exc
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON: {exc}") from exc
    return parsed


def request(args: argparse.Namespace, method: str, path: str, body: dict[str, Any] | None) -> Any:
    if method == "PLAN":
        return body
    base_url = str(args.base_url).rstrip("/")
    url = base_url + path
    headers = {"Accept": "text/event-stream" if method == "SSE" else "application/json"}
    data = None
    if args.client_id:
        headers["X-Harness-Client-Id"] = args.client_id
    if args.api_key:
        headers["X-Harness-Api-Key"] = args.api_key
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    if args.dry_run:
        return {
            "method": "GET" if method == "SSE" else method,
            "response_mode": "sse" if method == "SSE" else "json",
            "url": url,
            "headers": sorted(headers),
            "body": body,
        }
    req = Request(url, data=data, headers=headers, method="GET" if method == "SSE" else method)
    if method == "SSE":
        return read_sse_events(req, args)
    try:
        with urlopen(req, timeout=30) as response:
            text = response.read().decode("utf-8")
            return json.loads(text) if text else {}
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {text}") from exc
    except URLError as exc:
        raise SystemExit(f"harness API not reachable: {exc}") from exc


def summarize_read_model(args: argparse.Namespace, result: Any) -> Any:
    if getattr(args, "summary", "false") != "true":
        return result
    if isinstance(result, dict) and result.get("summary_requested") is True:
        return result
    if getattr(args, "dry_run", False):
        if isinstance(result, dict):
            return {**result, "summary_requested": True}
        return result
    if getattr(args, "command", "") == "sdk-events" and isinstance(result, dict):
        return summarize_sdk_events(result, task_id=getattr(args, "task_id", None))
    if getattr(args, "command", "") == "client-transcript" and isinstance(result, dict):
        return summarize_client_transcript(result, task_id=getattr(args, "task_id", None))
    if getattr(args, "command", "") == "capabilities" and isinstance(result, dict):
        return summarize_capabilities(result)
    return result


def summarize_capabilities(result: dict[str, Any]) -> dict[str, Any]:
    read_model_contract = result.get("read_model_contract") if isinstance(result.get("read_model_contract"), dict) else {}
    loop_contract = (
        result.get("loop_engineering_contract")
        if isinstance(result.get("loop_engineering_contract"), dict)
        else {}
    )
    sdk_event_types = _string_items(read_model_contract.get("sdk_event_types"))
    transcript_sections = _string_items(read_model_contract.get("client_transcript_sections"))
    summary_fields = _string_items(read_model_contract.get("summary_fields"))
    return {
        "status": result.get("status"),
        "source": "harness_capabilities_summary",
        "summary_requested": True,
        "api_surface": result.get("api_surface"),
        "runtime": result.get("runtime"),
        "loop_engineering_contract_keys": sorted(loop_contract.keys()),
        "read_model_contract_present": bool(read_model_contract),
        "read_model_contract_source": read_model_contract.get("source"),
        "read_model_endpoints": read_model_contract.get("endpoints") or {},
        "read_model_cursor": read_model_contract.get("cursor") or {},
        "sdk_event_type_count": len(sdk_event_types),
        "sdk_event_types": sdk_event_types,
        "client_transcript_section_count": len(transcript_sections),
        "client_transcript_sections": transcript_sections,
        "summary_field_count": len(summary_fields),
        "summary_fields": summary_fields,
        "read_model_support": {
            "agent_eval_case_verification": "agent_eval_case_verification" in sdk_event_types,
            "harness_verification_result": "harness_verification_result" in sdk_event_types,
            "agent_eval_case_verifications_section": "agent_eval_case_verifications" in transcript_sections,
            "harness_verification_results_section": "harness_verification_results" in transcript_sections,
            "agent_eval_case_verification_count": "agent_eval_case_verification_count" in summary_fields,
            "harness_verification_result_count": "harness_verification_result_count" in summary_fields,
        },
        "agentic_loop_template_ids": [
            str(item.get("id"))
            for item in result.get("agentic_loop_templates") or []
            if isinstance(item, dict) and item.get("id")
        ],
        "skill_names": [
            str(item.get("name"))
            for item in result.get("skills") or []
            if isinstance(item, dict) and item.get("name")
        ],
        "claim_boundary": (
            read_model_contract.get("claim_boundary")
            or "Summary of harness capabilities only; not proof of live provider connectivity."
        ),
    }


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def apply_command_validation(args: argparse.Namespace, result: Any) -> tuple[Any, int]:
    if getattr(args, "command", "") == "eval-case-verify":
        return eval_case_verify_summary(args, result)
    if getattr(args, "command", "") == "capabilities":
        required_sdk_types = _string_items(getattr(args, "require_sdk_event_type", []))
        required_sections = _string_items(getattr(args, "require_transcript_section", []))
        required_fields = _string_items(getattr(args, "require_summary_field", []))
        if not required_sdk_types and not required_sections and not required_fields:
            return result, 0
        if getattr(args, "dry_run", False):
            return result, 0
        validation = capabilities_expectation_summary(
            result,
            required_sdk_event_types=required_sdk_types,
            required_transcript_sections=required_sections,
            required_summary_fields=required_fields,
        )
        if isinstance(result, dict):
            return {**result, "capabilities_validation": validation}, 0 if validation["valid"] else 1
        return {
            "status": "failed",
            "capabilities_validation": validation,
            "result": result,
        }, 1
    if getattr(args, "command", "") != "diagnostics":
        return result, 0
    expected_id = int(getattr(args, "expect_diagnostic_id", 0) or 0)
    if expected_id <= 0 or getattr(args, "dry_run", False):
        return result, 0
    validation = diagnostics_expectation_summary(
        result,
        expected_id,
        expected_status=getattr(args, "expect_status", None),
        expected_provider=getattr(args, "expect_provider", None),
        expected_model=getattr(args, "expect_model", None),
        expected_api_style=getattr(args, "expect_api_style", None),
        expected_provider_request_attempted=optional_bool_choice(
            getattr(args, "expect_provider_request_attempted", None)
        ),
        expected_provider_response_received=optional_bool_choice(
            getattr(args, "expect_provider_response_received", None)
        ),
        expected_external_api_called=optional_bool_choice(getattr(args, "expect_external_api_called", None)),
    )
    if isinstance(result, dict):
        return {**result, "diagnostics_validation": validation}, 0 if validation["valid"] else 1
    return {
        "status": "failed",
        "diagnostics_validation": validation,
        "result": result,
    }, 1


def eval_case_verify_summary(args: argparse.Namespace, result: Any) -> tuple[dict[str, Any], int]:
    if getattr(args, "dry_run", False):
        return {
            "status": "dry_run",
            "source": "harness_eval_case_verify",
            "request": result,
            "selection": eval_case_selection_summary(args),
            "execute": bool(getattr(args, "execute", False)),
            "cwd": str(getattr(args, "cwd", "") or ""),
            "timeout_seconds": int(getattr(args, "timeout_seconds", 0) or 0),
            "record_result_task_id": getattr(args, "record_result_task_id", None),
            "claim_boundary": (
                "Dry run only shows the eval-case readback request and local verifier execution "
                "plan. It does not execute the stored verifier command."
            ),
        }, 0
    eval_cases = result.get("eval_cases") if isinstance(result, dict) else []
    eval_case, selection = select_eval_case(args, eval_cases if isinstance(eval_cases, list) else [])
    if not eval_case:
        return {
            "status": "failed",
            "source": "harness_eval_case_verify",
            "selection": selection,
            "readback_status": result.get("status") if isinstance(result, dict) else None,
            "eval_case_count": len(eval_cases) if isinstance(eval_cases, list) else 0,
            "failed_checks": ["eval_case_selected"],
            "claim_boundary": (
                "The command only reads the client-scoped /eval-cases read model. Check "
                "X-Harness-Client-Id and selection filters."
            ),
        }, 1
    verifier_command = str(eval_case.get("verifier_command") or "").strip()
    base_payload = {
        "source": "harness_eval_case_verify",
        "selected_eval_case": sanitize_eval_case_for_verify(eval_case),
        "selection": selection,
        "verifier_command": verifier_command,
        "cwd": str(getattr(args, "cwd", "") or ""),
        "timeout_seconds": int(getattr(args, "timeout_seconds", 0) or 0),
        "execute": bool(getattr(args, "execute", False)),
        "record_result_task_id": getattr(args, "record_result_task_id", None),
        "claim_boundary": (
            "This command verifies a stored harness eval-case verifier command against local "
            "state. It does not prove live provider connectivity unless the verifier itself "
            "performs and records that provider probe."
        ),
    }
    if not verifier_command:
        payload = {
            **base_payload,
            "status": "failed",
            "failed_checks": ["eval_case_verifier_command_present"],
        }
        return maybe_record_eval_case_verify_result(args, payload, 1)
    if not getattr(args, "execute", False):
        payload = {
            **base_payload,
            "status": "ready",
            "execution_skipped_reason": "execute_flag_required",
            "failed_checks": [],
        }
        return maybe_record_eval_case_verify_result(args, payload, 0)
    cwd = Path(str(getattr(args, "cwd", "") or ".")).resolve()
    if not cwd.exists() or not cwd.is_dir():
        payload = {
            **base_payload,
            "status": "failed",
            "failed_checks": ["eval_case_verify_cwd_exists"],
            "execution": {"cwd_exists": False, "cwd": str(cwd)},
        }
        return maybe_record_eval_case_verify_result(args, payload, 1)
    timeout_seconds = max(1, int(getattr(args, "timeout_seconds", 120) or 120))
    executed_command = portable_verifier_command(verifier_command)
    try:
        completed = subprocess.run(
            executed_command,
            cwd=cwd,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        status = "completed" if completed.returncode == 0 else "failed"
        execution = {
            "status": status,
            "executed_command": executed_command,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        status = "failed"
        execution = {
            "status": status,
            "executed_command": executed_command,
            "returncode": None,
            "stdout": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
            "error": {"code": "EVAL_CASE_VERIFIER_TIMEOUT", "timeout_seconds": timeout_seconds},
        }
    expected = eval_case.get("expected") if isinstance(eval_case.get("expected"), dict) else {}
    expected_status = expected.get("status")
    expected_status_matches = status == expected_status if isinstance(expected_status, str) else None
    failed_checks = []
    if status != "completed":
        failed_checks.append("eval_case_verifier_completed")
    if expected_status_matches is False:
        failed_checks.append("eval_case_expected_status_matches")
    payload = {
        **base_payload,
        "status": status,
        "execution": execution,
        "expected_status": expected_status,
        "expected_status_matches": expected_status_matches,
        "failed_checks": failed_checks,
    }
    return maybe_record_eval_case_verify_result(args, payload, 0 if status == "completed" else 1)


def maybe_record_eval_case_verify_result(
    args: argparse.Namespace,
    payload: dict[str, Any],
    exit_code: int,
) -> tuple[dict[str, Any], int]:
    task_id = str(getattr(args, "record_result_task_id", "") or "").strip()
    if not task_id:
        return payload, exit_code
    body = eval_case_verification_observation_body(payload)
    try:
        recorded = request(args, "POST", f"/tasks/{task_id}/observations", body)
    except BaseException as exc:
        failed_checks = list(payload.get("failed_checks") or [])
        failed_checks.append("eval_case_verification_observation_recorded")
        return {
            **payload,
            "status": "failed",
            "failed_checks": failed_checks,
            "record_result": {
                "status": "failed",
                "task_id": task_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        }, 1
    return {
        **payload,
        "record_result": {
            "status": "completed",
            "task_id": task_id,
            "observation_id": recorded.get("observation_id") if isinstance(recorded, dict) else None,
            "tool_name": recorded.get("tool_name") if isinstance(recorded, dict) else None,
            "observation_status": recorded.get("status") if isinstance(recorded, dict) else None,
            "evidence_refs": recorded.get("evidence_refs") if isinstance(recorded, dict) else None,
        },
    }, exit_code


def eval_case_verification_observation_body(payload: dict[str, Any]) -> dict[str, Any]:
    selected = (
        payload.get("selected_eval_case") if isinstance(payload.get("selected_eval_case"), dict) else {}
    )
    execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
    eval_case_id = selected.get("eval_case_id")
    evidence_refs = [f"eval_case:{eval_case_id}"] if eval_case_id is not None else []
    for ref in selected.get("evidence_refs") or []:
        if isinstance(ref, str) and ref not in evidence_refs:
            evidence_refs.append(ref)
    result = {
        "source": "harness_eval_case_verify",
        "status": payload.get("status"),
        "execute": payload.get("execute"),
        "eval_case_id": eval_case_id,
        "name": selected.get("name"),
        "source_task_id": selected.get("source_task_id"),
        "failure_label": selected.get("failure_label"),
        "verifier_command": payload.get("verifier_command"),
        "cwd": payload.get("cwd"),
        "expected_status": payload.get("expected_status"),
        "expected_status_matches": payload.get("expected_status_matches"),
        "failed_checks": payload.get("failed_checks") or [],
        "execution": {
            "status": execution.get("status"),
            "executed_command": execution.get("executed_command"),
            "returncode": execution.get("returncode"),
            "stdout_tail": execution.get("stdout"),
            "stderr_tail": execution.get("stderr"),
            "error": execution.get("error"),
        }
        if execution
        else {},
        "selected_eval_case": selected,
        "claim_boundary": payload.get("claim_boundary"),
    }
    status = "completed" if payload.get("status") in {"ready", "completed"} else "failed"
    return {
        "call_id": f"eval-case-verify-{eval_case_id}" if eval_case_id is not None else None,
        "tool_name": "eval_case_verification",
        "result": result,
        "evidence_refs": evidence_refs,
        "status": status,
        "metadata": {
            "source": "harness_api.py",
            "recorded_from": "eval-case-verify",
            "eval_case_id": eval_case_id,
        },
    }


def eval_case_selection_summary(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "eval_case_id": getattr(args, "eval_case_id", None),
        "name": getattr(args, "name", None),
        "failure_label": getattr(args, "failure_label", None),
    }


def select_eval_case(
    args: argparse.Namespace,
    eval_cases: list[Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    selection = eval_case_selection_summary(args)
    candidates = [item for item in eval_cases if isinstance(item, dict)]
    matched = candidates
    if selection["eval_case_id"] is not None:
        matched = [
            item
            for item in matched
            if int_value(item.get("eval_case_id")) == int(selection["eval_case_id"])
        ]
    if selection["name"]:
        matched = [item for item in matched if item.get("name") == selection["name"]]
    if selection["failure_label"]:
        matched = [item for item in matched if item.get("failure_label") == selection["failure_label"]]
    selection = {
        **selection,
        "eval_case_count": len(candidates),
        "matched_count": len(matched),
        "matched_eval_case_ids": [item.get("eval_case_id") for item in matched],
    }
    if len(matched) == 1:
        selection["status"] = "matched"
        return matched[0], selection
    selection["status"] = "not_found" if not matched else "ambiguous"
    return None, selection


def sanitize_eval_case_for_verify(eval_case: dict[str, Any]) -> dict[str, Any]:
    metadata = eval_case.get("metadata") if isinstance(eval_case.get("metadata"), dict) else {}
    return {
        "eval_case_id": eval_case.get("eval_case_id"),
        "name": eval_case.get("name"),
        "source_task_id": eval_case.get("source_task_id"),
        "failure_label": eval_case.get("failure_label"),
        "status": eval_case.get("status"),
        "evidence_refs": eval_case.get("evidence_refs") if isinstance(eval_case.get("evidence_refs"), list) else [],
        "expected": eval_case.get("expected") if isinstance(eval_case.get("expected"), dict) else {},
        "metadata": {
            key: metadata.get(key)
            for key in (
                "source",
                "matrix_run_id",
                "recorded_from",
                "failure_signature",
                "failure_signature_version",
                "failure_signature_material_digest",
            )
            if key in metadata
        },
    }


def portable_verifier_command(command: str) -> str:
    stripped = command.strip()
    if stripped == "python" or stripped.startswith("python "):
        if shutil.which("python"):
            return command
        return f"{shell_quote(sys.executable)}{stripped[len('python'):]}"
    return command


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def capabilities_expectation_summary(
    result: Any,
    *,
    required_sdk_event_types: list[str],
    required_transcript_sections: list[str],
    required_summary_fields: list[str],
) -> dict[str, Any]:
    if not isinstance(result, dict):
        sdk_event_types: list[str] = []
        transcript_sections: list[str] = []
        summary_fields: list[str] = []
        source = None
    elif result.get("source") == "harness_capabilities_summary":
        sdk_event_types = _string_items(result.get("sdk_event_types"))
        transcript_sections = _string_items(result.get("client_transcript_sections"))
        summary_fields = _string_items(result.get("summary_fields"))
        source = result.get("source")
    else:
        contract = result.get("read_model_contract") if isinstance(result.get("read_model_contract"), dict) else {}
        sdk_event_types = _string_items(contract.get("sdk_event_types"))
        transcript_sections = _string_items(contract.get("client_transcript_sections"))
        summary_fields = _string_items(contract.get("summary_fields"))
        source = contract.get("source") or result.get("source")
    missing_sdk_event_types = [item for item in required_sdk_event_types if item not in sdk_event_types]
    missing_transcript_sections = [item for item in required_transcript_sections if item not in transcript_sections]
    missing_summary_fields = [item for item in required_summary_fields if item not in summary_fields]
    checks = {
        "capabilities_payload_object": isinstance(result, dict),
        "required_sdk_event_types_present": not missing_sdk_event_types,
        "required_transcript_sections_present": not missing_transcript_sections,
        "required_summary_fields_present": not missing_summary_fields,
    }
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    return {
        "status": "matched" if not failed_checks else "missing",
        "valid": not failed_checks,
        "source": source,
        "required_sdk_event_types": required_sdk_event_types,
        "required_transcript_sections": required_transcript_sections,
        "required_summary_fields": required_summary_fields,
        "missing_sdk_event_types": missing_sdk_event_types,
        "missing_transcript_sections": missing_transcript_sections,
        "missing_summary_fields": missing_summary_fields,
        "sdk_event_type_count": len(sdk_event_types),
        "client_transcript_section_count": len(transcript_sections),
        "summary_field_count": len(summary_fields),
        "failed_checks": failed_checks,
        "checks": checks,
        "claim_boundary": (
            "Client-side capabilities expectation checks the current /capabilities read model. "
            "It does not run a provider probe."
        ),
    }


def diagnostics_expectation_summary(
    result: Any,
    expected_id: int,
    *,
    expected_status: str | None = None,
    expected_provider: str | None = None,
    expected_model: str | None = None,
    expected_api_style: str | None = None,
    expected_provider_request_attempted: bool | None = None,
    expected_provider_response_received: bool | None = None,
    expected_external_api_called: bool | None = None,
) -> dict[str, Any]:
    diagnostics = result.get("diagnostics") if isinstance(result, dict) else []
    diagnostics_list = diagnostics if isinstance(diagnostics, list) else []
    matched = next(
        (
            item
            for item in diagnostics_list
            if isinstance(item, dict) and int_value(item.get("diagnostic_id")) == expected_id
        ),
        {},
    )
    matched_provider = diagnostic_provider_details(matched)
    checks = {
        "diagnostics_payload_object": isinstance(result, dict),
        "diagnostics_list_present": isinstance(diagnostics, list),
        "diagnostic_id_matched": bool(matched),
    }
    if expected_status:
        checks["diagnostic_status_matches"] = matched.get("status") == expected_status if matched else False
    if expected_provider:
        checks["diagnostic_provider_matches"] = (
            matched_provider.get("provider") == expected_provider if matched else False
        )
    if expected_model:
        checks["diagnostic_model_matches"] = matched_provider.get("model") == expected_model if matched else False
    if expected_api_style:
        checks["diagnostic_api_style_matches"] = (
            matched_provider.get("api_style") == expected_api_style if matched else False
        )
    matched_provider_request_attempted = diagnostic_result_field(matched, "provider_request_attempted")
    matched_provider_response_received = diagnostic_result_field(matched, "provider_response_received")
    matched_external_api_called = diagnostic_result_field(matched, "external_api_called")
    if expected_provider_request_attempted is not None:
        checks["diagnostic_provider_request_attempted_matches"] = (
            matched_provider_request_attempted is expected_provider_request_attempted if matched else False
        )
    if expected_provider_response_received is not None:
        checks["diagnostic_provider_response_received_matches"] = (
            matched_provider_response_received is expected_provider_response_received if matched else False
        )
    if expected_external_api_called is not None:
        checks["diagnostic_external_api_called_matches"] = (
            matched_external_api_called is expected_external_api_called if matched else False
        )
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    return {
        "status": "matched" if not failed_checks else "missing",
        "valid": not failed_checks,
        "expected_diagnostic_id": expected_id,
        "expected_status": expected_status,
        "expected_provider": expected_provider,
        "expected_model": expected_model,
        "expected_api_style": expected_api_style,
        "expected_provider_request_attempted": expected_provider_request_attempted,
        "expected_provider_response_received": expected_provider_response_received,
        "expected_external_api_called": expected_external_api_called,
        "matched_diagnostic_id": int_value(matched.get("diagnostic_id")) if matched else 0,
        "matched_status": matched.get("status") if matched else None,
        "matched_provider": matched_provider,
        "matched_provider_request_attempted": matched_provider_request_attempted,
        "matched_provider_response_received": matched_provider_response_received,
        "matched_external_api_called": matched_external_api_called,
        "diagnostic_count": len(diagnostics_list),
        "failed_checks": failed_checks,
        "checks": checks,
        "claim_boundary": (
            "Client-side diagnostics expectation checks the current client-scoped "
            "/llm/diagnostics read-model response. It does not run a new provider probe."
        ),
    }


def diagnostics_field_expectations(args: argparse.Namespace) -> bool:
    return any(
        bool(getattr(args, name, None))
        for name in (
            "expect_status",
            "expect_provider",
            "expect_model",
            "expect_api_style",
            "expect_provider_request_attempted",
            "expect_provider_response_received",
            "expect_external_api_called",
        )
    )


def diagnostic_result_field(diagnostic: Any, field: str) -> Any:
    if not isinstance(diagnostic, dict):
        return None
    result = diagnostic.get("result") if isinstance(diagnostic.get("result"), dict) else {}
    if field in result:
        return result.get(field)
    return diagnostic.get(field)


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


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def read_sse_events(req: Request, args: argparse.Namespace) -> dict[str, Any]:
    started = monotonic()
    max_events = max(1, int(getattr(args, "max_events", 50) or 50))
    timeout_seconds = max(0.25, float(getattr(args, "timeout_seconds", 5.0) or 5.0))
    include_keepalive = getattr(args, "include_keepalive", "false") == "true"
    events: list[dict[str, Any]] = []
    keepalive_count = 0
    current: dict[str, Any] = {}
    data_lines: list[str] = []
    timed_out = False
    try:
        with urlopen(req, timeout=timeout_seconds) as response:
            while len(events) < max_events and monotonic() - started < timeout_seconds:
                raw_line = response.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    if current or data_lines:
                        current["data"] = _parse_sse_data("\n".join(data_lines))
                        events.append(current)
                        current = {}
                        data_lines = []
                    continue
                if line.startswith(":"):
                    keepalive_count += 1
                    if include_keepalive:
                        events.append({"comment": line[1:].strip()})
                    if not data_lines and not current:
                        break
                    continue
                field, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if field == "data":
                    data_lines.append(value)
                elif field == "id":
                    current["id"] = value
                elif field == "event":
                    current["event"] = value
                elif field == "retry":
                    current["retry"] = value
        if current or data_lines:
            current["data"] = _parse_sse_data("\n".join(data_lines))
            events.append(current)
    except TimeoutError:
        timed_out = True
        if current or data_lines:
            current["data"] = _parse_sse_data("\n".join(data_lines))
            events.append(current)
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {text}") from exc
    except URLError as exc:
        raise SystemExit(f"harness API not reachable: {exc}") from exc
    next_after = _latest_sse_event_id(events, fallback=getattr(args, "after", 0))
    event_summaries = summarize_sse_replay_events(events)
    return {
        "status": "completed",
        "source": "harness_event_sse_replay",
        "task_id": getattr(args, "task_id", None),
        "after": getattr(args, "after", 0),
        "events": events,
        "event_count": len(events),
        **event_summaries,
        "keepalive_count": keepalive_count,
        "timed_out": timed_out,
        "next_after": next_after,
        "cursor_policy": {
            "cursor_field": "after",
            "cursor_basis": "raw_harness_event_id",
            "cursor_semantics": "exclusive",
            "next_after": next_after,
        },
    }


def summarize_sse_replay_events(events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    data_items = [item.get("data") for item in events if isinstance(item.get("data"), dict)]
    return {
        "sse_event_name_counts": _count_values(
            item.get("event") if isinstance(item, dict) else None
            for item in events
        ),
        "event_type_counts": _count_field(data_items, "event_type"),
        "event_status_counts": _count_field(data_items, "status"),
        "event_actor_counts": _count_field(data_items, "actor"),
    }


def _count_field(items: list[dict[str, Any]], field: str) -> dict[str, int]:
    return _count_values(item.get(field) for item in items if isinstance(item, dict))


def _count_values(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "").strip() or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _parse_sse_data(value: str) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _latest_sse_event_id(events: list[dict[str, Any]], *, fallback: int) -> int:
    latest = int(fallback or 0)
    for item in events:
        try:
            latest = max(latest, int(item.get("id") or 0))
        except (TypeError, ValueError):
            continue
    return latest


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    commands = {
        "provider": [
            CAPABILITIES_SUMMARY_COMMAND,
            "providers",
            "probe --provider openai --model <model>",
            "probe --provider ollama --base-url http://localhost:11434/v1 --model <model>",
            "diagnostics --limit 20",
            "diagnostics --limit 20 --expect-diagnostic-id <diagnostic_id> --expect-status completed --expect-provider openai --expect-model <model> --expect-api-style responses --expect-provider-request-attempted true --expect-provider-response-received true --expect-external-api-called true",
        ],
        "task": [
            READ_MODEL_CAPABILITIES_GATE_COMMAND,
            "sdk-initialize",
            "sdk-context-usage",
            "tasks",
            "sessions",
            "task --cwd <absolute-cwd> --sandbox read-only --skill agentic-harness-loop --verification-command \"python -B verify.py\" --prompt \"<delegated work>\"",
            "task --start false --cwd <absolute-cwd> --sandbox read-only --idempotency-key <key> --prompt \"<evidence holder>\"",
            "events --task-id <task_id>",
            "evidence --task-id <task_id>",
            "sdk-events --task-id <task_id>",
            "sdk-events --task-id <task_id> --summary true",
            "client-transcript --task-id <task_id>",
            "client-transcript --task-id <task_id> --summary true",
            "annotate --task-id <task_id> --title \"<title>\" --summary \"<summary>\"",
            "fork --task-id <task_id> --prompt \"<fork prompt>\" --idempotency-key <key>",
            "command --task-id <task_id> --command pause",
            "reply --task-id <task_id> --prompt \"<resume prompt>\" --background true",
            "queued-message --task-id <task_id> --prompt \"<follow-up>\" --priority next",
            "cancel-queued-message --task-id <task_id> --message-uuid <uuid>",
        ],
        "tool-evidence": [
            "tool-call --task-id <task_id> --call-id <call_id> --tool-name <tool> --input-json \"{}\" --status running",
            "observation --task-id <task_id> --call-id <call_id> --tool-name <tool> --result-json \"{}\" --evidence-ref <ref>",
            "permission-decision --task-id <task_id> --call-id <call_id> --tool-name <tool> --behavior ask",
            "progress --task-id <task_id> --items-json '[{\"content\":\"Verify harness evidence\",\"active_form\":\"Verifying harness evidence\",\"status\":\"completed\"}]'",
            "artifact --task-id <task_id> --kind <kind> --path <path>",
        ],
        "eval": [
            READ_MODEL_CAPABILITIES_GATE_COMMAND,
            "eval-case --name \"<regression>\" --source-task-id <task_id> --failure-label <label> --input-json \"{}\" --expected-json \"{}\" --verifier-command \"python -B verify.py\"",
            "eval-cases",
            "eval-case-verify --eval-case-id <eval_case_id>",
            "eval-case-verify --eval-case-id <eval_case_id> --execute --cwd <absolute-cwd>",
            "eval-case-verify --eval-case-id <eval_case_id> --execute --cwd <absolute-cwd> --record-result-task-id <task_id>",
        ],
        "worker": [
            "worker-tick",
            "heartbeat --task-id <task_id> --lease-epoch <current_epoch> --lease-seconds 300",
        ],
        "subagent": [
            "subagent --task-id <parent_task_id> --cwd <absolute-cwd> --sandbox read-only --prompt \"<child work>\" --background false",
            "subagents --task-id <parent_task_id>",
            "events --task-id <parent_task_id>",
            "client-transcript --task-id <parent_task_id>",
        ],
        "loop": [
            "loop-create --name \"<loop name>\" --cwd <absolute-cwd> --sandbox read-only --skill agentic-harness-loop --verification-command \"python -B verify.py\" --interval-seconds 600 --max-iterations 3 --prompt \"<recurring loop work>\"",
            "loop-tick --loop-id <loop_id> --force true --start false",
            "loop-tick --loop-id <loop_id> --force true --start true --background true",
            "loop-evidence --loop-id <loop_id>",
            "loops",
        ],
        "trace": [
            READ_MODEL_CAPABILITIES_GATE_COMMAND,
            "python .agents/skills/agentic-harness-loop/scripts/agents_sdk_smoke.py --scenario stream_handoff --output-json <trace.json>",
            "task --start false --cwd <absolute-cwd> --sandbox read-only --idempotency-key <key> --prompt \"<trace evidence holder>\"",
            "python .agents/skills/agentic-harness-loop/scripts/trace_ingest.py --dry-run --client-id <client_id> --task-id <task_id> --trace-json <trace.json>",
            "python .agents/skills/agentic-harness-loop/scripts/trace_ingest.py --client-id <client_id> --task-id <task_id> --trace-json <trace.json> --readback --fail-on-readback-invalid --compact-output --output-json <trace-ingest-full.json>",
            "python .agents/skills/agentic-harness-loop/scripts/trace_ingest.py --client-id <client_id> --task-id <task_id> --trace-json <trace.json> --readback-only --fail-on-readback-invalid --compact-output --output-json <trace-readback-only-full.json>",
            "evidence --task-id <task_id>",
            "sdk-events --task-id <task_id>",
            "sdk-events --task-id <task_id> --summary true",
            "client-transcript --task-id <task_id>",
            "client-transcript --task-id <task_id> --summary true",
            "eval-case --name \"<trace regression>\" --source-task-id <task_id> --failure-label <label> --input-json \"{}\" --expected-json \"{}\" --verifier-command \"python -B developer_harness/verify.py\" --evidence-ref task:<task_id>",
        ],
        "failure": recovery_commands(args.status),
    }
    guardrails = [
        "Use X-Harness-Client-Id for stateful calls.",
        "Probe /models before claiming provider connectivity.",
        "Record tool calls and observations before final claims.",
        "Use readback-only trace digest validation before reusing old trace evidence.",
        "Run capabilities --summary with required projection fields before relying on read-model projections.",
        "Promote repeatable failures to eval cases.",
        "Treat mock, cached, fixture, and deterministic outputs as harness evidence only.",
    ]
    return {"goal": args.goal, "status": args.status, "recommended_commands": commands[args.goal], "guardrails": guardrails}


def recovery_commands(status: str | None) -> list[str]:
    if status == "configuration_required":
        return ["Fix provider profile/env, then run probe again.", "Record an eval-case if the gap should not regress."]
    if status in {"failed", "not_connected", "api_unreachable"}:
        return ["Check HARNESS_BASE_URL and server health.", "Run providers/probe after endpoint is reachable."]
    if status == "404":
        return ["Verify task_id/loop_id and X-Harness-Client-Id; cross-client access should return 404."]
    if status == "409":
        return ["Refresh task state and retry with current state_version or lease_epoch."]
    if status == "completed":
        return ["Inspect evidence and verifier output before reporting final claims."]
    return ["Inspect task evidence, diagnostics, and events; choose provider/task/tool-evidence/eval/worker next."]


if __name__ == "__main__":
    raise SystemExit(main())
