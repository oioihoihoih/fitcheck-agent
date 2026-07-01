#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
import traceback
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend_harness.app.config_env import load_project_env


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a live OpenAI Agents SDK smoke test.")
    parser.add_argument("--model", default=os.getenv("FITCHECK_LLM_MODEL") or "gpt-4.1-mini")
    parser.add_argument(
        "--scenario",
        choices=[
            "tool",
            "handoff",
            "approval",
            "guardrail",
            "guardrail_tripwire",
            "session",
            "stream",
            "stream_handoff",
        ],
        default="tool",
    )
    parser.add_argument("--marker", default="AGENTS_SDK_LIVE_TOOL_OK")
    parser.add_argument("--output-json", default="")
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--max-tokens", type=int, default=120)
    parser.add_argument("--tracing-disabled", action=argparse.BooleanOptionalAction, default=False)
    args = parser.parse_args()

    load_project_env(ROOT)
    output_path = Path(args.output_json) if args.output_json else default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not os.getenv("OPENAI_API_KEY"):
        payload = {
            "status": "configuration_required",
            "provider": "openai",
            "api_style": "responses",
            "model": args.model,
            "error": "OPENAI_API_KEY is not configured.",
            "created_at": now_iso(),
        }
        write_json(output_path, payload)
        print(json.dumps({"status": "configuration_required", "output_json": str(output_path)}, indent=2))
        return 2

    try:
        import agents
        from agents import (
            Agent,
            GuardrailFunctionOutput,
            InputGuardrailTripwireTriggered,
            ModelSettings,
            OutputGuardrailTripwireTriggered,
            RunConfig,
            Runner,
            SQLiteSession,
            function_tool,
            handoff,
            output_guardrail,
        )
    except Exception as exc:
        payload = {
            "status": "dependency_missing",
            "dependency": "openai-agents",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "created_at": now_iso(),
        }
        write_json(output_path, payload)
        print(json.dumps({"status": "dependency_missing", "output_json": str(output_path)}, indent=2))
        return 2

    @function_tool
    def record_signal(marker: str) -> str:
        """Record the exact smoke-test marker.

        Args:
            marker: The marker string to record.
        """

        return json.dumps({"status": "recorded", "marker": marker}, ensure_ascii=False)

    @function_tool(needs_approval=True)
    def approval_required_signal(marker: str) -> str:
        """Record the smoke-test marker only after explicit approval.

        Args:
            marker: The marker string to record after approval.
        """

        return json.dumps({"status": "approved_and_recorded", "marker": marker}, ensure_ascii=False)

    @output_guardrail(name="harness_output_guardrail")
    def harness_output_guardrail(ctx: Any, agent: Any, agent_output: Any) -> Any:
        return GuardrailFunctionOutput(
            output_info={
                "status": "checked",
                "marker": args.marker,
                "agent_name": safe_attr(agent, "name"),
                "agent_output": str(agent_output),
            },
            tripwire_triggered=False,
        )

    @output_guardrail(name="harness_tripwire_guardrail")
    def harness_tripwire_guardrail(ctx: Any, agent: Any, agent_output: Any) -> Any:
        return GuardrailFunctionOutput(
            output_info={
                "status": "blocked",
                "marker": args.marker,
                "agent_name": safe_attr(agent, "name"),
                "agent_output": str(agent_output),
                "reason": "intentional_live_smoke_tripwire",
            },
            tripwire_triggered=True,
        )

    scenario = build_scenario(
        scenario=args.scenario,
        marker=args.marker,
        model=args.model,
        max_tokens=args.max_tokens,
        record_signal=record_signal,
        approval_required_signal=approval_required_signal,
        harness_output_guardrail=harness_output_guardrail,
        harness_tripwire_guardrail=harness_tripwire_guardrail,
        agent_cls=Agent,
        model_settings_cls=ModelSettings,
        handoff_fn=handoff,
    )
    trace_id = build_trace_id(args.scenario)
    session = None
    session_db_path = None
    if args.scenario == "session":
        session_db_path = output_path.with_suffix(".session.sqlite3")
        if session_db_path.exists():
            session_db_path.unlink()
        session = SQLiteSession(f"{trace_id}_session", db_path=session_db_path)
    run_config = RunConfig(
        model=args.model,
        workflow_name=f"com2us harness agents sdk {args.scenario} smoke",
        trace_id=trace_id,
        tracing_disabled=args.tracing_disabled,
        trace_include_sensitive_data=False,
    )

    try:
        run_payload = run_scenario(
            runner=Runner,
            scenario=scenario,
            max_turns=args.max_turns,
            run_config=run_config,
            session=session,
            session_db_path=session_db_path,
        )
    except (InputGuardrailTripwireTriggered, OutputGuardrailTripwireTriggered) as exc:
        payload = tripwire_payload(
            exc=exc,
            args=args,
            agents_module=agents,
            scenario=scenario,
            trace_id=trace_id,
        )
        write_json(output_path, payload)
        print(
            json.dumps(
                {
                    "status": payload["status"],
                    "output_json": str(output_path),
                    "model": args.model,
                    "scenario": args.scenario,
                    "error_type": payload.get("error_type"),
                    "final_output": payload.get("final_output"),
                    "marker_observed": payload.get("summary", {}).get("marker_observed"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if args.scenario == "guardrail_tripwire" else 1
    except Exception as exc:
        payload = {
            "status": "failed",
            "provider": "openai",
            "api_style": "responses",
            "sdk": "openai-agents",
            "sdk_version": getattr(sys.modules.get("agents"), "__version__", None),
            "model": args.model,
            "trace_id": trace_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback_tail": traceback.format_exc(limit=5),
            "created_at": now_iso(),
        }
        write_json(output_path, payload)
        print(json.dumps({"status": "failed", "output_json": str(output_path)}, indent=2))
        return 1

    result = run_payload["result"]
    items = run_payload["items"]
    raw_responses = run_payload["raw_responses"]
    interruptions = run_payload["interruptions"]
    stream_events = run_payload.get("stream_events") or []
    input_guardrail_results = [serialize_guardrail_result(item) for item in result.input_guardrail_results]
    output_guardrail_results = [serialize_guardrail_result(item) for item in result.output_guardrail_results]
    tool_input_guardrail_results = [
        serialize_guardrail_result(item) for item in result.tool_input_guardrail_results
    ]
    tool_output_guardrail_results = [
        serialize_guardrail_result(item) for item in result.tool_output_guardrail_results
    ]
    marker_search_surface = {
        "new_items": items,
        "stream_events": stream_events,
        "input_guardrail_results": input_guardrail_results,
        "output_guardrail_results": output_guardrail_results,
        "tool_input_guardrail_results": tool_input_guardrail_results,
        "tool_output_guardrail_results": tool_output_guardrail_results,
    }
    payload = {
        "status": "completed",
        "source": "agents_sdk_smoke",
        "scenario": args.scenario,
        "provider": "openai",
        "api_style": "responses",
        "sdk": "openai-agents",
        "sdk_version": getattr(agents, "__version__", None),
        "external_api_called": True,
        "model": args.model,
        "trace_id": trace_id,
        "tracing_disabled": args.tracing_disabled,
        "created_at": now_iso(),
        "input": scenario["prompt"],
        "final_output": dump_value(result.final_output),
        "new_items": items,
        "raw_responses": raw_responses,
        "interruptions": interruptions,
        "approval_decisions": run_payload["approval_decisions"],
        "phases": run_payload["phases"],
        "session": run_payload.get("session"),
        "stream_events": stream_events,
        "input_guardrail_results": input_guardrail_results,
        "output_guardrail_results": output_guardrail_results,
        "tool_input_guardrail_results": tool_input_guardrail_results,
        "tool_output_guardrail_results": tool_output_guardrail_results,
        "summary": build_smoke_summary(
            items=items,
            raw_responses=raw_responses,
            interruptions=interruptions,
            approval_decisions=run_payload["approval_decisions"],
            phases=run_payload["phases"],
            stream_events=stream_events,
            input_guardrail_results=input_guardrail_results,
            output_guardrail_results=output_guardrail_results,
            tool_input_guardrail_results=tool_input_guardrail_results,
            tool_output_guardrail_results=tool_output_guardrail_results,
            session=run_payload.get("session"),
            marker=args.marker,
            marker_search_surface=marker_search_surface,
            final_output=result.final_output,
            expected_final_output=scenario["expected_final_output"],
        ),
    }
    write_json(output_path, payload)
    print(
        json.dumps(
            {
                "status": "completed",
                "output_json": str(output_path),
                "model": args.model,
                "scenario": args.scenario,
                "new_item_count": len(items),
                "stream_event_count": len(stream_events),
                "final_output": str(result.final_output),
                "marker_observed": payload["summary"]["marker_observed"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_scenario(
    *,
    scenario: str,
    marker: str,
    model: str,
    max_tokens: int,
    record_signal: Any,
    approval_required_signal: Any,
    harness_output_guardrail: Any,
    harness_tripwire_guardrail: Any,
    agent_cls: Any,
    model_settings_cls: Any,
    handoff_fn: Any,
) -> dict[str, Any]:
    if scenario == "tool":
        prompt = (
            f"Call record_signal with marker {marker!r}. "
            "After the tool result, answer exactly AGENTS_SDK_LIVE_FINAL_OK."
        )
        agent = agent_cls(
            name="Harness SDK Smoke Agent",
            instructions=(
                "You are a harness smoke-test agent. Use the provided function tool when asked. "
                "Do not add extra prose to the final answer."
            ),
            tools=[record_signal],
            model=model,
            model_settings=model_settings_cls(max_tokens=max_tokens),
        )
        return {
            "agent": agent,
            "prompt": prompt,
            "expected_final_output": "AGENTS_SDK_LIVE_FINAL_OK",
        }
    if scenario in {"handoff", "stream_handoff"}:
        final_marker = (
            "AGENTS_SDK_STREAM_HANDOFF_FINAL_OK"
            if scenario == "stream_handoff"
            else "AGENTS_SDK_HANDOFF_FINAL_OK"
        )
        specialist = agent_cls(
            name="Harness Specialist Agent",
            handoff_description="Handles handoff smoke-test requests and returns the exact marker.",
            instructions=(
                "You are the specialist for a handoff smoke test. "
                f"Answer exactly {final_marker} and nothing else."
            ),
            model=model,
            model_settings=model_settings_cls(max_tokens=max_tokens),
        )
        router = agent_cls(
            name="Harness Router Agent",
            instructions=(
                "You are only a router. For every user request, call the "
                "transfer_to_harness_specialist handoff tool. Do not answer directly."
            ),
            handoffs=[
                handoff_fn(
                    specialist,
                    tool_name_override="transfer_to_harness_specialist",
                    tool_description_override="Transfer to the harness specialist for the smoke-test final answer.",
                )
            ],
            model=model,
            model_settings=model_settings_cls(max_tokens=max_tokens),
        )
        return {
            "agent": router,
            "prompt": (
                "Use the handoff tool to transfer this request to the specialist. "
                f"The final answer must be exactly {final_marker}."
            ),
            "expected_final_output": final_marker,
            "use_streaming": scenario == "stream_handoff",
        }
    if scenario == "approval":
        prompt = (
            f"Call approval_required_signal with marker {marker!r}. "
            "After the tool has been approved and run, answer exactly AGENTS_SDK_APPROVAL_FINAL_OK."
        )
        agent = agent_cls(
            name="Harness Approval Agent",
            instructions=(
                "You are a harness approval smoke-test agent. You must call the approval-required "
                "tool when asked. After the approved tool result is available, answer exactly "
                "AGENTS_SDK_APPROVAL_FINAL_OK and nothing else."
            ),
            tools=[approval_required_signal],
            model=model,
            model_settings=model_settings_cls(max_tokens=max_tokens),
        )
        return {
            "agent": agent,
            "prompt": prompt,
            "expected_final_output": "AGENTS_SDK_APPROVAL_FINAL_OK",
        }
    if scenario == "guardrail":
        prompt = "Answer exactly AGENTS_SDK_GUARDRAIL_FINAL_OK and nothing else."
        agent = agent_cls(
            name="Harness Guardrail Agent",
            instructions=(
                "You are a harness guardrail smoke-test agent. Return the exact requested "
                "answer and do not add extra prose."
            ),
            output_guardrails=[harness_output_guardrail],
            model=model,
            model_settings=model_settings_cls(max_tokens=max_tokens),
        )
        return {
            "agent": agent,
            "prompt": prompt,
            "expected_final_output": "AGENTS_SDK_GUARDRAIL_FINAL_OK",
        }
    if scenario == "guardrail_tripwire":
        prompt = "Answer exactly AGENTS_SDK_GUARDRAIL_TRIPWIRE_FINAL_OK and nothing else."
        agent = agent_cls(
            name="Harness Tripwire Agent",
            instructions=(
                "You are a harness guardrail tripwire smoke-test agent. Return the exact "
                "requested answer and do not add extra prose."
            ),
            output_guardrails=[harness_tripwire_guardrail],
            model=model,
            model_settings=model_settings_cls(max_tokens=max_tokens),
        )
        return {
            "agent": agent,
            "prompt": prompt,
            "expected_final_output": "AGENTS_SDK_GUARDRAIL_TRIPWIRE_FINAL_OK",
        }
    if scenario == "session":
        prompts = [
            "Remember this session code exactly: BLUE-HARNESS-42. Answer exactly SESSION_TURN_1_OK.",
            (
                "Using only the prior session context, what was the session code? "
                "If it was BLUE-HARNESS-42, answer exactly AGENTS_SDK_SESSION_FINAL_OK."
            ),
        ]
        agent = agent_cls(
            name="Harness Session Agent",
            instructions=(
                "You are a harness session smoke-test agent. Preserve short facts from prior "
                "session turns. Return only the exact requested marker."
            ),
            model=model,
            model_settings=model_settings_cls(max_tokens=max_tokens),
        )
        return {
            "agent": agent,
            "prompt": prompts[0],
            "prompts": prompts,
            "expected_final_output": "AGENTS_SDK_SESSION_FINAL_OK",
        }
    if scenario == "stream":
        prompt = (
            f"Call record_signal with marker {marker!r}. "
            "After the tool result, answer exactly AGENTS_SDK_STREAM_FINAL_OK."
        )
        agent = agent_cls(
            name="Harness Stream Agent",
            instructions=(
                "You are a harness streaming smoke-test agent. Use the provided function tool when asked. "
                "Do not add extra prose to the final answer."
            ),
            tools=[record_signal],
            model=model,
            model_settings=model_settings_cls(max_tokens=max_tokens),
        )
        return {
            "agent": agent,
            "prompt": prompt,
            "expected_final_output": "AGENTS_SDK_STREAM_FINAL_OK",
            "use_streaming": True,
        }
    raise ValueError(f"unsupported scenario: {scenario}")


def build_smoke_summary(
    *,
    items: list[dict[str, Any]],
    raw_responses: list[dict[str, Any]],
    interruptions: list[dict[str, Any]],
    approval_decisions: list[dict[str, Any]],
    phases: list[dict[str, Any]],
    stream_events: list[dict[str, Any]],
    input_guardrail_results: list[dict[str, Any]],
    output_guardrail_results: list[dict[str, Any]],
    tool_input_guardrail_results: list[dict[str, Any]],
    tool_output_guardrail_results: list[dict[str, Any]],
    session: dict[str, Any] | None,
    marker: str,
    marker_search_surface: dict[str, Any],
    final_output: Any,
    expected_final_output: str,
) -> dict[str, Any]:
    stream_counts = stream_event_summary_counts(stream_events)
    summary = {
        "new_item_count": len(items),
        "new_item_type_counts": count_path(items, ("type",), default="unknown"),
        "new_item_class_counts": count_path(items, ("class",), default="unknown"),
        "tool_call_count": sum(1 for item in items if str(item.get("type", "")) == "tool_call_item"),
        "tool_output_count": sum(1 for item in items if str(item.get("type", "")) == "tool_call_output_item"),
        "handoff_call_count": sum(1 for item in items if str(item.get("type", "")) == "handoff_call_item"),
        "handoff_output_count": sum(1 for item in items if str(item.get("type", "")) == "handoff_output_item"),
        "approval_request_count": len(interruptions),
        "approval_request_type_counts": count_path(interruptions, ("type",), default="unknown"),
        "approval_decision_count": len(approval_decisions),
        "approval_decision_counts": count_path(approval_decisions, ("decision",), default="unknown"),
        "input_guardrail_count": len(input_guardrail_results),
        "output_guardrail_count": len(output_guardrail_results),
        "tool_input_guardrail_count": len(tool_input_guardrail_results),
        "tool_output_guardrail_count": len(tool_output_guardrail_results),
        "guardrail_tripwire_count": guardrail_tripwire_count(
            input_guardrail_results
            + output_guardrail_results
            + tool_input_guardrail_results
            + tool_output_guardrail_results
        ),
        "raw_response_count": len(raw_responses),
        "raw_response_output_type_counts": raw_response_output_type_counts(raw_responses),
        "raw_response_usage_totals": raw_response_usage_totals(raw_responses),
        "phase_count": len(phases),
        "phase_names": [str(item.get("name") or f"phase_{index}") for index, item in enumerate(phases, start=1)],
        "phase_new_item_counts": phase_count_map(phases, "new_items", mode="len"),
        "phase_raw_response_counts": phase_count_map(phases, "raw_response_count"),
        "phase_interruption_counts": phase_count_map(phases, "interruption_count"),
        "stream_event_count": len(stream_events),
        "stream_event_types": sorted(key for key in stream_counts["event_type"] if key != "none"),
        "stream_event_type_counts": stream_counts["event_type"],
        "stream_event_name_counts": stream_counts["event_name"],
        "stream_raw_event_type_counts": stream_counts["raw_event_type"],
        "stream_item_class_counts": stream_counts["item_class"],
        "stream_tool_name_counts": stream_counts["tool_name"],
        "marker_observed": marker in json.dumps(marker_search_surface, ensure_ascii=False),
        "final_ok": str(final_output).strip() == expected_final_output,
        "expected_final_output": expected_final_output,
    }
    if session:
        final_item_types = [
            str(item or "unknown")
            for item in session.get("final_item_types") or []
        ]
        summary["session_turn_count"] = len(session.get("turns") or [])
        summary["session_item_count"] = session.get("final_item_count")
        summary["session_item_type_counts"] = count_values(final_item_types, default="unknown")
    return summary


def count_values(values: Any, *, default: str = "unknown") -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or default).strip() or default
        counts[key] = counts.get(key, 0) + 1
    return counts


def count_path(items: list[dict[str, Any]], path: tuple[str, ...], *, default: str) -> dict[str, int]:
    values = []
    for item in items:
        value = first_present(item, [path], default=default)
        values.append(value)
    return count_values(values, default=default)


def raw_response_output_type_counts(raw_responses: list[dict[str, Any]]) -> dict[str, int]:
    values: list[str] = []
    for response in raw_responses:
        for output_item in as_list(response.get("output")):
            if isinstance(output_item, Mapping):
                values.append(str(output_item.get("type") or output_item.get("class") or "unknown"))
            else:
                values.append(type(output_item).__name__)
    return count_values(values, default="unknown")


def raw_response_usage_totals(raw_responses: list[dict[str, Any]]) -> dict[str, int | float]:
    totals: dict[str, int | float] = {}
    for response in raw_responses:
        usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
        for key, value in usage.items():
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            totals[str(key)] = totals.get(str(key), 0) + value
    return totals


def phase_count_map(phases: list[dict[str, Any]], field: str, *, mode: str = "value") -> dict[str, int]:
    result: dict[str, int] = {}
    for index, phase in enumerate(phases, start=1):
        name = str(phase.get("name") or f"phase_{index}")
        value = phase.get(field)
        if mode == "len":
            result[name] = len(as_list(value))
            continue
        try:
            result[name] = int(value or 0)
        except (TypeError, ValueError):
            result[name] = 0
    return result


def guardrail_tripwire_count(guardrail_results: list[dict[str, Any]]) -> int:
    count = 0
    for guardrail in guardrail_results:
        output = guardrail.get("output") if isinstance(guardrail.get("output"), Mapping) else {}
        if first_present(output, [("tripwire_triggered",), ("tripwireTriggered",)], default=False) is True:
            count += 1
    return count


def stream_event_summary_counts(stream_events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    tool_names_by_call_id: dict[str, str] = {}
    event_types: list[str] = []
    event_names: list[str] = []
    raw_event_types: list[str] = []
    item_classes: list[str] = []
    tool_names: list[str] = []
    for event in stream_events:
        if not isinstance(event, Mapping):
            continue
        event_types.append(str(event.get("type") or "none"))
        event_names.append(str(event.get("name") or "none"))
        raw_event_types.append(str(event.get("raw_event_type") or "none"))
        item = event.get("item") if isinstance(event.get("item"), Mapping) else {}
        item_classes.append(str(item.get("class") or "none"))
        tool_names.append(stream_event_tool_name(item, tool_names_by_call_id))
    return {
        "event_type": count_values(event_types, default="none"),
        "event_name": count_values(event_names, default="none"),
        "raw_event_type": count_values(raw_event_types, default="none"),
        "item_class": count_values(item_classes, default="none"),
        "tool_name": count_values(tool_names, default="none"),
    }


def stream_event_tool_name(item: Mapping[str, Any], tool_names_by_call_id: dict[str, str]) -> str:
    if not item:
        return "none"
    call_id = first_present(
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
    tool_name = first_present(
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
    generic_tool_names = {"ToolCallOutputItem", "MessageOutputItem", "HandoffOutputItem", "stream_item"}
    if tool_name in generic_tool_names:
        tool_name = None
    if call_id:
        key = str(call_id)
        if tool_name:
            tool_names_by_call_id[key] = str(tool_name)
        elif key in tool_names_by_call_id:
            tool_name = tool_names_by_call_id[key]
    return str(tool_name or "none")


def build_trace_id(scenario: str) -> str:
    scenario_slug = "".join(char if char.isalnum() else "_" for char in scenario.lower()).strip("_")
    return f"trace_sdk_{scenario_slug[:18]}_{uuid.uuid4().hex[:22]}"


def run_scenario(
    *,
    runner: Any,
    scenario: dict[str, Any],
    max_turns: int,
    run_config: Any,
    session: Any = None,
    session_db_path: Path | None = None,
) -> dict[str, Any]:
    if scenario.get("use_streaming"):
        return run_stream_scenario(
            runner=runner,
            scenario=scenario,
            max_turns=max_turns,
            run_config=run_config,
        )
    if session is not None:
        return run_session_scenario(
            runner=runner,
            scenario=scenario,
            max_turns=max_turns,
            run_config=run_config,
            session=session,
            session_db_path=session_db_path,
        )
    first_result = runner.run_sync(
        scenario["agent"],
        scenario["prompt"],
        max_turns=max_turns,
        run_config=run_config,
    )
    first_items = [serialize_run_item(item) for item in first_result.new_items]
    first_responses = [serialize_model_response(response) for response in first_result.raw_responses]
    first_interruptions = [serialize_run_item(item) for item in getattr(first_result, "interruptions", [])]
    phases: list[dict[str, Any]] = [
        {
            "name": "initial",
            "final_output": dump_value(first_result.final_output),
            "new_items": first_items,
            "raw_response_count": len(first_responses),
            "interruption_count": len(first_interruptions),
        }
    ]
    if not first_interruptions:
        return {
            "result": first_result,
            "items": first_items,
            "raw_responses": first_responses,
            "interruptions": [],
            "approval_decisions": [],
            "phases": phases,
        }

    state = first_result.to_state()
    approval_item = first_result.interruptions[0]
    state.approve(approval_item)
    resumed_result = runner.run_sync(scenario["agent"], state, max_turns=max_turns, run_config=run_config)
    resumed_items = [serialize_run_item(item) for item in resumed_result.new_items]
    resumed_responses = [serialize_model_response(response) for response in resumed_result.raw_responses]
    phases.append(
        {
            "name": "after_approval",
            "final_output": dump_value(resumed_result.final_output),
            "new_items": resumed_items,
            "raw_response_count": len(resumed_responses),
            "interruption_count": len(getattr(resumed_result, "interruptions", [])),
        }
    )
    serialized_approval_item = serialize_run_item(approval_item)
    raw_approval_item = (
        serialized_approval_item.get("raw_item")
        if isinstance(serialized_approval_item.get("raw_item"), dict)
        else {}
    )
    approval_agent = safe_attr(approval_item, "agent")
    decision = {
        "decision": "approved",
        "call_id": safe_attr(approval_item, "call_id"),
        "tool_name": safe_attr(approval_item, "tool_name") or safe_attr(approval_item, "name"),
        "item_type": safe_attr(approval_item, "type"),
        "arguments": serialized_approval_item.get("arguments") or raw_approval_item.get("arguments"),
        "raw_type": raw_approval_item.get("type"),
        "agent": safe_attr(approval_agent, "name") if approval_agent is not None else None,
    }
    decision = {key: value for key, value in decision.items() if value is not None}
    return {
        "result": resumed_result,
        "items": dedupe_serialized(first_items + resumed_items),
        "raw_responses": dedupe_serialized(first_responses + resumed_responses),
        "interruptions": first_interruptions,
        "approval_decisions": [decision],
        "phases": phases,
    }


def run_stream_scenario(
    *,
    runner: Any,
    scenario: dict[str, Any],
    max_turns: int,
    run_config: Any,
) -> dict[str, Any]:
    async def collect() -> tuple[Any, list[dict[str, Any]]]:
        result = runner.run_streamed(
            scenario["agent"],
            scenario["prompt"],
            max_turns=max_turns,
            run_config=run_config,
        )
        events: list[dict[str, Any]] = []
        async for event in result.stream_events():
            events.append(serialize_stream_event(event, len(events) + 1))
        return result, events

    stream_result, stream_events = asyncio.run(collect())
    items = [serialize_run_item(item) for item in stream_result.new_items]
    raw_responses = [serialize_model_response(response) for response in stream_result.raw_responses]
    interruptions = [serialize_run_item(item) for item in getattr(stream_result, "interruptions", [])]
    return {
        "result": stream_result,
        "items": dedupe_serialized(items),
        "raw_responses": dedupe_serialized(raw_responses),
        "interruptions": interruptions,
        "approval_decisions": [],
        "stream_events": stream_events,
        "phases": [
            {
                "name": "stream",
                "final_output": dump_value(stream_result.final_output),
                "new_items": dedupe_serialized(items),
                "raw_response_count": len(dedupe_serialized(raw_responses)),
                "interruption_count": len(interruptions),
                "stream_event_count": len(stream_events),
            }
        ],
    }


def run_session_scenario(
    *,
    runner: Any,
    scenario: dict[str, Any],
    max_turns: int,
    run_config: Any,
    session: Any,
    session_db_path: Path | None,
) -> dict[str, Any]:
    prompts = scenario.get("prompts") or [scenario["prompt"]]
    phases: list[dict[str, Any]] = []
    all_items: list[dict[str, Any]] = []
    all_responses: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    final_result = None
    for index, prompt in enumerate(prompts, start=1):
        before_items = session_items_sync(session)
        result = runner.run_sync(
            scenario["agent"],
            prompt,
            max_turns=max_turns,
            run_config=run_config,
            session=session,
        )
        after_items = session_items_sync(session)
        serialized_items = [serialize_run_item(item) for item in result.new_items]
        serialized_responses = [serialize_model_response(response) for response in result.raw_responses]
        all_items.extend(serialized_items)
        all_responses.extend(serialized_responses)
        phase_name = "session_initial" if index == 1 else f"session_turn_{index}"
        phases.append(
            {
                "name": phase_name,
                "final_output": dump_value(result.final_output),
                "new_items": serialized_items,
                "raw_response_count": len(serialized_responses),
                "interruption_count": len(getattr(result, "interruptions", [])),
                "session_item_count_before": len(before_items),
                "session_item_count_after": len(after_items),
            }
        )
        turns.append(
            {
                "index": index,
                "phase": phase_name,
                "prompt": prompt,
                "final_output": dump_value(result.final_output),
                "item_count_before": len(before_items),
                "item_count_after": len(after_items),
                "new_item_count": len(serialized_items),
                "raw_response_count": len(serialized_responses),
            }
        )
        final_result = result
    final_items = session_items_sync(session)
    return {
        "result": final_result,
        "items": dedupe_serialized(all_items),
        "raw_responses": dedupe_serialized(all_responses),
        "interruptions": [],
        "approval_decisions": [],
        "phases": phases,
        "session": {
            "session_id": getattr(session, "session_id", None),
            "backend": session.__class__.__name__,
            "db_path": str(session_db_path) if session_db_path else None,
            "turns": turns,
            "turn_count": len(turns),
            "final_item_count": len(final_items),
            "final_item_types": [
                str(item.get("type") or item.get("role") or item.get("class") or "unknown")
                if isinstance(item, dict)
                else type(item).__name__
                for item in final_items
            ],
            "final_items_preview": [
                compact_session_item(item, index)
                for index, item in enumerate(final_items, start=1)
            ],
        },
    }


def session_items_sync(session: Any) -> list[Any]:
    value = session.get_items()
    if hasattr(value, "__await__"):
        return asyncio.run(value)
    return value


def compact_session_item(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"index": index, "class": type(item).__name__, "value": _truncate_text(str(item), 240)}
    text = session_item_text(item)
    payload = {
        "index": index,
        "role": item.get("role"),
        "type": item.get("type"),
        "status": item.get("status"),
        "phase": item.get("phase"),
        "text": _truncate_text(text, 240) if text else None,
    }
    if isinstance(item.get("content"), list):
        payload["content_types"] = [
            str(part.get("type") or "unknown")
            for part in item["content"]
            if isinstance(part, dict)
        ][:8]
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def session_item_text(item: dict[str, Any]) -> str | None:
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            str(part.get("text"))
            for part in content
            if isinstance(part, dict) and part.get("text") is not None
        ]
        if texts:
            return " ".join(texts)
    return None


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "..."


def serialize_stream_event(event: Any, index: int) -> dict[str, Any]:
    event_type = safe_attr(event, "type")
    payload: dict[str, Any] = {
        "index": index,
        "type": event_type,
        "class": event.__class__.__name__,
    }
    name = safe_attr(event, "name")
    if name is not None:
        payload["name"] = dump_value(name)
    item = safe_attr(event, "item")
    if item is not None:
        payload["item"] = serialize_run_item(item)
    new_agent = safe_attr(event, "new_agent")
    if new_agent is not None:
        payload["agent"] = {"name": safe_attr(new_agent, "name")}
    data = safe_attr(event, "data")
    if data is not None:
        raw_event = dump_value(data)
        payload["raw_event_type"] = _raw_event_type(raw_event)
        payload["raw_event"] = compact_stream_raw_event(raw_event)
    return {key: value for key, value in payload.items() if value not in (None, {}, [])}


def _raw_event_type(raw_event: Any) -> str | None:
    if isinstance(raw_event, dict):
        return str(raw_event.get("type") or raw_event.get("event") or "") or None
    return None


def compact_stream_raw_event(raw_event: Any) -> Any:
    if not isinstance(raw_event, dict):
        return raw_event
    keep_keys = [
        "type",
        "sequence_number",
        "response_id",
        "item_id",
        "output_index",
        "content_index",
        "delta",
        "text",
    ]
    compact = {key: raw_event.get(key) for key in keep_keys if key in raw_event}
    for key in ["item", "part", "response"]:
        value = raw_event.get(key)
        if isinstance(value, dict):
            compact[key] = {
                nested_key: value.get(nested_key)
                for nested_key in ["id", "type", "status", "role", "name", "call_id"]
                if nested_key in value
            }
    return {key: value for key, value in compact.items() if value not in (None, {}, [])}


def serialize_run_item(item: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "type": safe_attr(item, "type"),
        "class": item.__class__.__name__,
    }
    for attr in ["tool_name", "call_id", "name", "arguments"]:
        value = safe_attr(item, attr)
        if value is not None:
            data[attr] = dump_value(value)
    for attr in ["raw_item", "output", "custom_data", "tool_origin"]:
        value = safe_attr(item, attr)
        if value is not None:
            data[attr] = dump_value(value)
    agent = safe_attr(item, "agent")
    if agent is not None:
        data["agent"] = {"name": safe_attr(agent, "name")}
    source_agent = safe_attr(item, "source_agent")
    if source_agent is not None:
        data["source_agent"] = {"name": safe_attr(source_agent, "name")}
    target_agent = safe_attr(item, "target_agent")
    if target_agent is not None:
        data["target_agent"] = {"name": safe_attr(target_agent, "name")}
    return data


def serialize_model_response(response: Any) -> dict[str, Any]:
    return {
        "class": response.__class__.__name__,
        "response_id": safe_attr(response, "response_id"),
        "request_id": safe_attr(response, "request_id"),
        "output": dump_value(safe_attr(response, "output")),
        "usage": dump_value(safe_attr(response, "usage")),
    }


def tripwire_payload(
    *,
    exc: Exception,
    args: Any,
    agents_module: Any,
    scenario: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    run_data = safe_attr(exc, "run_data")
    items = [serialize_run_item(item) for item in as_list(safe_attr(run_data, "new_items"))]
    raw_responses = [serialize_model_response(response) for response in as_list(safe_attr(run_data, "raw_responses"))]
    input_guardrail_results = [
        serialize_guardrail_result(item) for item in as_list(safe_attr(run_data, "input_guardrail_results"))
    ]
    output_guardrail_results = [
        serialize_guardrail_result(item) for item in as_list(safe_attr(run_data, "output_guardrail_results"))
    ]
    triggered_guardrail = serialize_guardrail_result(safe_attr(exc, "guardrail_result"))
    if triggered_guardrail.get("class") == "OutputGuardrailResult" and not contains_serialized(
        output_guardrail_results, triggered_guardrail
    ):
        output_guardrail_results.append(triggered_guardrail)
    if triggered_guardrail.get("class") == "InputGuardrailResult" and not contains_serialized(
        input_guardrail_results, triggered_guardrail
    ):
        input_guardrail_results.append(triggered_guardrail)
    final_output = triggered_guardrail.get("agent_output")
    marker_search_surface = {
        "new_items": items,
        "input_guardrail_results": input_guardrail_results,
        "output_guardrail_results": output_guardrail_results,
    }
    status = "guardrail_tripwire_triggered"
    phases = [
        {
            "name": "guardrail_tripwire",
            "final_output": final_output,
            "new_items": dedupe_serialized(items),
            "raw_response_count": len(dedupe_serialized(raw_responses)),
            "interruption_count": 0,
        }
    ]
    summary = build_smoke_summary(
        items=dedupe_serialized(items),
        raw_responses=dedupe_serialized(raw_responses),
        interruptions=[],
        approval_decisions=[],
        phases=phases,
        stream_events=[],
        input_guardrail_results=input_guardrail_results,
        output_guardrail_results=output_guardrail_results,
        tool_input_guardrail_results=[],
        tool_output_guardrail_results=[],
        session=None,
        marker=args.marker,
        marker_search_surface=marker_search_surface,
        final_output=final_output,
        expected_final_output=scenario["expected_final_output"],
    )
    summary["guardrail_tripwire_triggered"] = True
    return {
        "status": status,
        "source": "agents_sdk_smoke",
        "scenario": args.scenario,
        "provider": "openai",
        "api_style": "responses",
        "sdk": "openai-agents",
        "sdk_version": getattr(agents_module, "__version__", None),
        "external_api_called": bool(raw_responses),
        "model": args.model,
        "trace_id": trace_id,
        "tracing_disabled": args.tracing_disabled,
        "created_at": now_iso(),
        "input": scenario["prompt"],
        "final_output": final_output,
        "new_items": dedupe_serialized(items),
        "raw_responses": dedupe_serialized(raw_responses),
        "interruptions": [],
        "approval_decisions": [],
        "phases": phases,
        "input_guardrail_results": input_guardrail_results,
        "output_guardrail_results": output_guardrail_results,
        "tool_input_guardrail_results": [],
        "tool_output_guardrail_results": [],
        "error_type": exc.__class__.__name__,
        "error": str(exc),
        "summary": summary,
    }


def serialize_guardrail_result(item: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "class": item.__class__.__name__,
    }
    guardrail = safe_attr(item, "guardrail")
    if guardrail is not None:
        name = None
        get_name = safe_attr(guardrail, "get_name")
        if callable(get_name):
            try:
                name = get_name()
            except Exception:
                name = None
        data["guardrail"] = {
            "class": guardrail.__class__.__name__,
            "name": name or safe_attr(guardrail, "name"),
        }
    output = safe_attr(item, "output")
    if output is not None:
        data["output"] = dump_value(output)
    agent = safe_attr(item, "agent")
    if agent is not None:
        data["agent"] = {"name": safe_attr(agent, "name")}
    agent_output = safe_attr(item, "agent_output")
    if agent_output is not None:
        data["agent_output"] = dump_value(agent_output)
    return data


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def contains_serialized(items: list[dict[str, Any]], target: dict[str, Any]) -> bool:
    fingerprint = json.dumps(target, sort_keys=True, ensure_ascii=False, default=str)
    return any(json.dumps(item, sort_keys=True, ensure_ascii=False, default=str) == fingerprint for item in items)


def safe_attr(value: Any, attr: str) -> Any:
    try:
        return getattr(value, attr)
    except Exception:
        return None


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


def dump_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dump_value(dataclasses.asdict(value))
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return value.model_dump(mode="json", exclude_none=True)
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(key): dump_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [dump_value(item) for item in value]
    return str(value)


def dedupe_serialized(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        fingerprint = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(item)
    return result


def default_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / "harness_outputs" / f"agents_sdk_smoke_{stamp}.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
