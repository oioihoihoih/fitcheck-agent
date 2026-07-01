from __future__ import annotations

import argparse
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend_harness.web_server import app
import developer_harness.routes as harness_routes
import developer_harness.runtime as runtime_module
from developer_harness.runtime import DevelopmentHarness, _resolve_codex_executable
from developer_harness.store import HarnessStore


class FakeExecutor:
    def execute(self, **kwargs: Any) -> dict[str, Any]:
        self.calls = getattr(self, "calls", [])
        self.calls.append(kwargs)
        kwargs["on_event"]({"type": "thread.started", "thread_id": "thr_test"})
        kwargs["on_event"]({"type": "item.completed", "item": {"text": "verified"}})
        return {
            "returncode": 0,
            "thread_id": "thr_test",
            "last_message": "verified",
            "stderr": "",
            "cwd": str(kwargs["cwd"]),
        }

    def cancel(self, task_id: str) -> bool:
        self.cancelled = getattr(self, "cancelled", [])
        self.cancelled.append(task_id)
        return True


class FailingExecutor:
    def execute(self, **kwargs: Any) -> dict[str, Any]:
        self.calls = getattr(self, "calls", [])
        self.calls.append(kwargs)
        kwargs["on_event"]({"type": "thread.started", "thread_id": "thr_failed"})
        raise RuntimeError("synthetic codex executor failure")


class BlockingExecutor:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.cancelled: list[str] = []

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        self.calls = getattr(self, "calls", [])
        self.calls.append(kwargs)
        kwargs["on_event"]({"type": "thread.started", "thread_id": "thr_blocked"})
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("blocking executor was not released")
        return {
            "returncode": 0,
            "thread_id": "thr_blocked",
            "last_message": "",
            "stderr": "",
            "cwd": str(kwargs["cwd"]),
        }

    def cancel(self, task_id: str) -> bool:
        self.cancelled.append(task_id)
        self.release.set()
        return True


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    root = ROOT
    backend_agents = root / "backend_harness" / "AGENTS.md"
    assert backend_agents.exists()
    backend_agents_text = backend_agents.read_text(encoding="utf-8")
    assert "X-Harness-Client-Id" in backend_agents_text
    assert "workspace_mode=client_isolated" in backend_agents_text
    assert "/api/harness/v1" in backend_agents_text
    with TemporaryDirectory() as temp:
        temp_bin = Path(temp) / "bin"
        temp_bin.mkdir()
        fake_codex = temp_bin / "codex"
        fake_codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_codex.chmod(0o755)
        original_codex_bin = os.environ.pop("CODEX_BIN", None)
        original_path = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = str(temp_bin)
            assert _resolve_codex_executable() == str(fake_codex)
        finally:
            if original_codex_bin is not None:
                os.environ["CODEX_BIN"] = original_codex_bin
            else:
                os.environ.pop("CODEX_BIN", None)
            os.environ["PATH"] = original_path
        saved_path_for_python_alias = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = ""
            portable_python = runtime_module._portable_verification_command("python -c \"print('ok')\"")
        finally:
            os.environ["PATH"] = saved_path_for_python_alias
        assert sys.executable in portable_python
        runtime = DevelopmentHarness(root, HarnessStore(Path(temp) / "harness.sqlite3"), FakeExecutor())
        capabilities = runtime.capabilities()
        assert {item["name"] for item in capabilities["skills"]} >= {
            "harness-task",
            "harness-loop",
            "agentic-harness-loop",
            "local-llm-provider-diagnostics",
        }
        harness_api = root / ".agents" / "skills" / "agentic-harness-loop" / "scripts" / "harness_api.py"
        assert harness_api.exists()
        skill_scripts = [
            harness_api,
            root / ".agents" / "skills" / "agentic-harness-loop" / "scripts" / "trace_ingest.py",
            root / ".agents" / "skills" / "agentic-harness-loop" / "scripts" / "agents_sdk_smoke.py",
            root / ".agents" / "skills" / "agentic-harness-loop" / "scripts" / "agents_sdk_smoke_matrix.py",
            root / ".agents" / "skills" / "agentic-harness-loop" / "scripts" / "scaffold_agent_harness.py",
        ]
        for script in skill_scripts:
            assert script.exists()
            assert script.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n")
            if os.name != "nt":
                assert os.access(script, os.X_OK)
        direct_dry_run = subprocess.run(
            [
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "capabilities",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        direct_dry_run_payload = json.loads(direct_dry_run.stdout)
        assert direct_dry_run_payload["method"] == "GET"
        assert direct_dry_run_payload["url"].endswith("/capabilities")
        capabilities_summary_dry_run = subprocess.run(
            [
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "capabilities",
                "--summary",
                "true",
                "--require-sdk-event-type",
                "agent_eval_case_verification",
                "--require-transcript-section",
                "agent_eval_case_verifications",
                "--require-summary-field",
                "agent_eval_case_verification_count",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        capabilities_summary_dry_run_payload = json.loads(capabilities_summary_dry_run.stdout)
        assert capabilities_summary_dry_run_payload["method"] == "GET"
        assert capabilities_summary_dry_run_payload["url"].endswith("/capabilities")
        assert capabilities_summary_dry_run_payload["summary_requested"] is True
        diagnostics_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "diagnostics",
                "--limit",
                "7",
                "--expect-diagnostic-id",
                "99",
                "--expect-status",
                "completed",
                "--expect-provider",
                "openai",
                "--expect-model",
                "gpt-test",
                "--expect-api-style",
                "responses",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        diagnostics_dry_run_payload = json.loads(diagnostics_dry_run.stdout)
        assert diagnostics_dry_run_payload["method"] == "GET"
        assert diagnostics_dry_run_payload["url"].endswith("/llm/diagnostics?limit=7")
        dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "probe",
                "--provider",
                "ollama",
                "--base-url",
                "http://localhost:11434/v1",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        dry_run_payload = json.loads(dry_run.stdout)
        assert dry_run_payload["method"] == "POST"
        assert dry_run_payload["url"].endswith("/llm/probe")
        assert dry_run_payload["body"]["provider"]["api_style"] == "chat_completions"
        openai_probe_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "probe",
                "--provider",
                "openai",
                "--model",
                "gpt-test",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        openai_probe_payload = json.loads(openai_probe_dry_run.stdout)
        openai_provider = openai_probe_payload["body"]["provider"]
        assert openai_provider["provider"] == "openai"
        assert openai_provider["api_style"] == "responses"
        assert openai_provider["base_url"] == "https://api.openai.com/v1"
        assert openai_provider["api_key_env"] == "OPENAI_API_KEY"
        assert openai_provider["supports_stateful_responses"] is True
        for helper_script in skill_scripts[1:]:
            help_result = subprocess.run(
                [str(helper_script), "--help"],
                text=True,
                capture_output=True,
                check=True,
            )
            assert "usage:" in help_result.stdout.lower()
        evidence_holder_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "task",
                "--prompt",
                "evidence holder",
                "--cwd",
                str(root),
                "--sandbox",
                "read-only",
                "--idempotency-key",
                "verify-evidence-holder",
                "--start",
                "false",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        evidence_holder_request = json.loads(evidence_holder_dry_run.stdout)
        assert evidence_holder_request["url"].endswith("/tasks?background=false&start=false")
        assert evidence_holder_request["body"]["idempotency_key"] == "verify-evidence-holder"
        events_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "events",
                "--task-id",
                "task_verify",
                "--after",
                "9",
                "--max-events",
                "3",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        events_request = json.loads(events_dry_run.stdout)
        assert events_request["method"] == "GET"
        assert events_request["response_mode"] == "sse"
        assert events_request["url"].endswith("/tasks/task_verify/events?after=9")
        assert "Accept" in events_request["headers"]
        harness_api_spec = importlib.util.spec_from_file_location("harness_api_verify", harness_api)
        assert harness_api_spec and harness_api_spec.loader
        harness_api_module = importlib.util.module_from_spec(harness_api_spec)
        harness_api_spec.loader.exec_module(harness_api_module)
        assert harness_api_module.summarize_sdk_events.__module__ == "developer_harness.read_model_summary"
        assert harness_api_module.summarize_client_transcript.__module__ == "developer_harness.read_model_summary"
        capabilities_summary = harness_api_module.summarize_read_model(
            argparse.Namespace(command="capabilities", summary="true", dry_run=False),
            capabilities,
        )
        assert capabilities_summary["source"] == "harness_capabilities_summary"
        assert capabilities_summary["summary_requested"] is True
        assert capabilities_summary["read_model_contract_present"] is True
        assert capabilities_summary["read_model_support"]["agent_eval_case_verification"] is True
        assert capabilities_summary["read_model_support"]["harness_verification_result"] is True
        assert capabilities_summary["read_model_support"]["agent_eval_case_verifications_section"] is True
        assert capabilities_summary["read_model_support"]["harness_verification_results_section"] is True
        assert capabilities_summary["read_model_support"]["agent_eval_case_verification_count"] is True
        assert capabilities_summary["read_model_support"]["harness_verification_result_count"] is True
        assert "agent_eval_case_verification" in capabilities_summary["sdk_event_types"]
        assert "harness_verification_results" in capabilities_summary["client_transcript_sections"]
        assert "not prove current provider connectivity" in capabilities_summary["claim_boundary"]
        capabilities_validated, capabilities_exit_code = harness_api_module.apply_command_validation(
            argparse.Namespace(
                command="capabilities",
                require_sdk_event_type=["agent_eval_case_verification", "harness_verification_result"],
                require_transcript_section=["agent_eval_case_verifications", "harness_verification_results"],
                require_summary_field=["agent_eval_case_verification_count", "harness_verification_result_count"],
                dry_run=False,
            ),
            capabilities_summary,
        )
        assert capabilities_exit_code == 0
        assert capabilities_validated["capabilities_validation"]["valid"] is True
        assert capabilities_validated["capabilities_validation"]["failed_checks"] == []
        capabilities_missing, capabilities_missing_exit_code = harness_api_module.apply_command_validation(
            argparse.Namespace(
                command="capabilities",
                require_sdk_event_type=["missing_projection_type"],
                require_transcript_section=["missing_transcript_section"],
                require_summary_field=["missing_summary_field"],
                dry_run=False,
            ),
            capabilities_summary,
        )
        assert capabilities_missing_exit_code == 1
        assert capabilities_missing["capabilities_validation"]["valid"] is False
        assert capabilities_missing["capabilities_validation"]["missing_sdk_event_types"] == [
            "missing_projection_type"
        ]
        assert capabilities_missing["capabilities_validation"]["missing_transcript_sections"] == [
            "missing_transcript_section"
        ]
        assert capabilities_missing["capabilities_validation"]["missing_summary_fields"] == ["missing_summary_field"]
        assert capabilities_missing["capabilities_validation"]["failed_checks"] == [
            "required_sdk_event_types_present",
            "required_transcript_sections_present",
            "required_summary_fields_present",
        ]

        class SampleSseResponse:
            def __init__(self):
                self._lines = iter(
                    [
                        b"id: 10\n",
                        b"event: harness_event\n",
                        b'data: {"event_type":"tool_call.recorded","status":"completed","actor":"api"}\n',
                        b"\n",
                        b"id: 11\n",
                        b"event: harness_event\n",
                        b'data: {"event_type":"observation.recorded","status":"failed","actor":"api"}\n',
                        b"\n",
                        b"",
                    ]
                )

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def readline(self):
                return next(self._lines)

        class SampleSseArgs:
            task_id = "task_verify"
            after = 9
            max_events = 5
            timeout_seconds = 0.25
            include_keepalive = "false"

        original_urlopen = harness_api_module.urlopen
        harness_api_module.urlopen = lambda req, timeout: SampleSseResponse()
        try:
            replay_events = harness_api_module.read_sse_events(object(), SampleSseArgs())
        finally:
            harness_api_module.urlopen = original_urlopen
        assert replay_events["status"] == "completed"
        assert replay_events["event_count"] == 2
        assert replay_events["next_after"] == 11
        assert replay_events["sse_event_name_counts"] == {"harness_event": 2}
        assert replay_events["event_type_counts"] == {
            "observation.recorded": 1,
            "tool_call.recorded": 1,
        }
        assert replay_events["event_status_counts"] == {"completed": 1, "failed": 1}
        assert replay_events["event_actor_counts"] == {"api": 2}
        diagnostics_payload = {
            "status": "completed",
            "diagnostics": [
                {
                    "diagnostic_id": 99,
                    "status": "completed",
                    "provider": {
                        "provider": "openai",
                        "model": "gpt-test",
                        "api_style": "responses",
                    },
                    "result": {
                        "provider_request_attempted": True,
                        "provider_response_received": True,
                        "external_api_called": True,
                    },
                },
                {"diagnostic_id": 98, "status": "not_connected"},
                {
                    "diagnostic_id": 97,
                    "status": "completed",
                    "result": {
                        "provider": {
                            "provider": "openai",
                            "model": "gpt-result-provider",
                            "api_style": "responses",
                        },
                        "provider_request_attempted": True,
                        "provider_response_received": True,
                        "external_api_called": True,
                    },
                },
                {
                    "diagnostic_id": 96,
                    "status": "completed",
                    "provider": "openai",
                    "model": "gpt-scalar-provider",
                    "api_style": "responses",
                    "provider_request_attempted": True,
                    "provider_response_received": True,
                    "external_api_called": True,
                },
            ],
        }
        diagnostics_validated, diagnostics_exit_code = harness_api_module.apply_command_validation(
            argparse.Namespace(
                command="diagnostics",
                expect_diagnostic_id=99,
                expect_status="completed",
                expect_provider="openai",
                expect_model="gpt-test",
                expect_api_style="responses",
                expect_provider_request_attempted="true",
                expect_provider_response_received="true",
                expect_external_api_called="true",
                dry_run=False,
            ),
            diagnostics_payload,
        )
        assert diagnostics_exit_code == 0
        assert diagnostics_validated["diagnostics_validation"]["valid"] is True
        assert diagnostics_validated["diagnostics_validation"]["matched_diagnostic_id"] == 99
        assert diagnostics_validated["diagnostics_validation"]["matched_provider"]["model"] == "gpt-test"
        assert diagnostics_validated["diagnostics_validation"]["matched_provider_request_attempted"] is True
        assert diagnostics_validated["diagnostics_validation"]["matched_provider_response_received"] is True
        assert diagnostics_validated["diagnostics_validation"]["matched_external_api_called"] is True
        diagnostics_nested_provider, diagnostics_nested_provider_exit_code = (
            harness_api_module.apply_command_validation(
                argparse.Namespace(
                    command="diagnostics",
                    expect_diagnostic_id=97,
                    expect_status="completed",
                    expect_provider="openai",
                    expect_model="gpt-result-provider",
                    expect_api_style="responses",
                    expect_provider_request_attempted="true",
                    expect_provider_response_received="true",
                    expect_external_api_called="true",
                    dry_run=False,
                ),
                diagnostics_payload,
            )
        )
        assert diagnostics_nested_provider_exit_code == 0
        assert diagnostics_nested_provider["diagnostics_validation"]["valid"] is True
        assert diagnostics_nested_provider["diagnostics_validation"]["matched_provider"]["model"] == (
            "gpt-result-provider"
        )
        assert diagnostics_nested_provider["diagnostics_validation"]["matched_external_api_called"] is True
        diagnostics_scalar_provider, diagnostics_scalar_provider_exit_code = (
            harness_api_module.apply_command_validation(
                argparse.Namespace(
                    command="diagnostics",
                    expect_diagnostic_id=96,
                    expect_status="completed",
                    expect_provider="openai",
                    expect_model="gpt-scalar-provider",
                    expect_api_style="responses",
                    expect_provider_request_attempted="true",
                    expect_provider_response_received="true",
                    expect_external_api_called="true",
                    dry_run=False,
                ),
                diagnostics_payload,
            )
        )
        assert diagnostics_scalar_provider_exit_code == 0
        assert diagnostics_scalar_provider["diagnostics_validation"]["valid"] is True
        assert diagnostics_scalar_provider["diagnostics_validation"]["matched_provider"]["model"] == (
            "gpt-scalar-provider"
        )
        assert diagnostics_scalar_provider["diagnostics_validation"]["matched_external_api_called"] is True
        diagnostics_wrong_model, diagnostics_wrong_model_exit_code = harness_api_module.apply_command_validation(
            argparse.Namespace(
                command="diagnostics",
                expect_diagnostic_id=99,
                expect_status="completed",
                expect_provider="openai",
                expect_model="wrong-model",
                expect_api_style="responses",
                expect_provider_request_attempted=None,
                expect_provider_response_received=None,
                expect_external_api_called=None,
                dry_run=False,
            ),
            diagnostics_payload,
        )
        assert diagnostics_wrong_model_exit_code == 1
        assert diagnostics_wrong_model["diagnostics_validation"]["valid"] is False
        assert diagnostics_wrong_model["diagnostics_validation"]["failed_checks"] == ["diagnostic_model_matches"]
        diagnostics_wrong_external_api, diagnostics_wrong_external_api_exit_code = (
            harness_api_module.apply_command_validation(
                argparse.Namespace(
                    command="diagnostics",
                    expect_diagnostic_id=99,
                    expect_status="completed",
                    expect_provider="openai",
                    expect_model="gpt-test",
                    expect_api_style="responses",
                    expect_provider_request_attempted="true",
                    expect_provider_response_received="true",
                    expect_external_api_called="false",
                    dry_run=False,
                ),
                diagnostics_payload,
            )
        )
        assert diagnostics_wrong_external_api_exit_code == 1
        assert diagnostics_wrong_external_api["diagnostics_validation"]["valid"] is False
        assert diagnostics_wrong_external_api["diagnostics_validation"]["failed_checks"] == [
            "diagnostic_external_api_called_matches"
        ]
        diagnostics_missing, diagnostics_missing_exit_code = harness_api_module.apply_command_validation(
            argparse.Namespace(
                command="diagnostics",
                expect_diagnostic_id=100,
                expect_status=None,
                expect_provider=None,
                expect_model=None,
                expect_api_style=None,
                expect_provider_request_attempted=None,
                expect_provider_response_received=None,
                expect_external_api_called=None,
                dry_run=False,
            ),
            diagnostics_payload,
        )
        assert diagnostics_missing_exit_code == 1
        assert diagnostics_missing["diagnostics_validation"]["valid"] is False
        assert diagnostics_missing["diagnostics_validation"]["failed_checks"] == ["diagnostic_id_matched"]
        dry_run_diagnostics_result, dry_run_diagnostics_exit_code = harness_api_module.apply_command_validation(
            argparse.Namespace(
                command="diagnostics",
                expect_diagnostic_id=100,
                expect_status="completed",
                expect_provider="openai",
                expect_model="gpt-test",
                expect_api_style="responses",
                expect_provider_request_attempted="true",
                expect_provider_response_received="true",
                expect_external_api_called="true",
                dry_run=True,
            ),
            {"method": "GET"},
        )
        assert dry_run_diagnostics_exit_code == 0
        assert dry_run_diagnostics_result == {"method": "GET"}

        class TimeoutResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def readline(self):
                raise TimeoutError("synthetic sse timeout")

        class SseArgs:
            task_id = "task_verify"
            after = 0
            max_events = 3
            timeout_seconds = 0.25
            include_keepalive = "false"

        original_urlopen = harness_api_module.urlopen
        harness_api_module.urlopen = lambda req, timeout: TimeoutResponse()
        try:
            timed_out_events = harness_api_module.read_sse_events(object(), SseArgs())
        finally:
            harness_api_module.urlopen = original_urlopen
        assert timed_out_events["status"] == "completed"
        assert timed_out_events["timed_out"] is True
        assert timed_out_events["event_count"] == 0
        tool_call_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "tool-call",
                "--task-id",
                "task_verify",
                "--call-id",
                "call_verify",
                "--tool-name",
                "provider_probe",
                "--status",
                "running",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        tool_call_request = json.loads(tool_call_dry_run.stdout)
        assert tool_call_request["url"].endswith("/tasks/task_verify/tool-calls")
        assert tool_call_request["body"]["call_id"] == "call_verify"
        assert tool_call_request["body"]["status"] == "running"
        sdk_events_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "sdk-events",
                "--task-id",
                "task_verify",
                "--after",
                "12",
                "--summary",
                "true",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        sdk_events_request = json.loads(sdk_events_dry_run.stdout)
        assert sdk_events_request["method"] == "GET"
        assert sdk_events_request["url"].endswith("/tasks/task_verify/sdk-events?after=12&summary=true")
        assert sdk_events_request["summary_requested"] is True
        already_summarized = harness_api_module.summarize_read_model(
            argparse.Namespace(command="sdk-events", summary="true", dry_run=False, task_id="task_summary"),
            {
                "status": "completed",
                "source": "harness_sdk_events_summary",
                "summary_requested": True,
                "task_id": "task_summary",
                "agent_guidance_count": 7,
            },
        )
        assert already_summarized["agent_guidance_count"] == 7
        sdk_summary = harness_api_module.summarize_sdk_events(
            {
                "status": "completed",
                "task_id": "task_summary",
                "projected_event_count": 16,
                "raw_event_count": 16,
                "final_output_included": False,
                "terminal_event_included": False,
                "next_after": 44,
                "cursor_policy": {"cursor_basis": "raw_harness_event_id"},
                "events": [
                    {"type": "tool_progress", "tool_name": "search_web_candidates"},
                    {"type": "agent_phase", "phase_name": "after_approval"},
                    {"type": "agent_session", "agent_session_id": "trace_verify_session"},
                    {"type": "agent_handoff", "source_agent": "Router", "target_agent": "Specialist"},
                    {"type": "agent_approval", "subtype": "approval_request"},
                    {"type": "agent_approval", "subtype": "approval_decision", "decision": "approved"},
                    {
                        "type": "agent_guidance",
                        "reason": "continue_search_for_closer_measurements",
                        "next_tool_suggestions": [{"tool": "search_web_candidates"}],
                    },
                    {
                        "type": "agent_question",
                        "subtype": "missing_info_request",
                        "questions": [{"field": "baseline_evidence"}],
                    },
                    {
                        "type": "agent_eval_case_verification",
                        "status": "failed",
                        "execution_status": "failed",
                        "expected_status_matches": False,
                        "failure_label": "aggregate:matrix_summary_payload_matches",
                    },
                    {
                        "type": "agent_model_response",
                        "model": "gpt-5-mini",
                        "provider": "openai",
                        "api_style": "responses",
                    },
                    {
                        "type": "agent_stream_event",
                        "stream_index": 1,
                        "stream_event_type": "run_item_stream_event",
                        "stream_event_name": "tool_called",
                        "raw_event_type": "none",
                        "item_class": "ToolCallItem",
                        "tool_name": "record_signal",
                    },
                    {"type": "agent_guardrail", "guardrail_name": "verify_guardrail"},
                    {"type": "agent_run_error", "error_type": "VerifyError"},
                    {"type": "harness_verification_result", "status": "completed"},
                    {"type": "harness_verification_result", "status": "failed"},
                    {"type": "final_output"},
                ],
            },
            task_id="task_summary",
        )
        assert sdk_summary["source"] == "harness_sdk_events_summary"
        assert sdk_summary["summary_requested"] is True
        assert sdk_summary["agent_guidance_count"] == 1
        assert sdk_summary["agent_guidance_reasons"] == ["continue_search_for_closer_measurements"]
        assert sdk_summary["agent_guidance_reason_counts"] == {"continue_search_for_closer_measurements": 1}
        assert sdk_summary["agent_guidance_suggested_tool_count"] == 1
        assert sdk_summary["agent_eval_case_verification_count"] == 1
        assert sdk_summary["agent_eval_case_verification_failed_count"] == 1
        assert sdk_summary["agent_eval_case_failure_label_counts"] == {
            "aggregate:matrix_summary_payload_matches": 1
        }
        assert sdk_summary["missing_info_question_count"] == 1
        assert sdk_summary["tool_progress_count"] == 1
        assert sdk_summary["agent_model_response_count"] == 1
        assert sdk_summary["agent_model_response_provider_counts"] == {"openai": 1}
        assert sdk_summary["agent_model_response_api_style_counts"] == {"responses": 1}
        assert sdk_summary["agent_phase_count"] == 1
        assert sdk_summary["agent_session_count"] == 1
        assert sdk_summary["agent_handoff_count"] == 1
        assert sdk_summary["agent_approval_count"] == 2
        assert sdk_summary["agent_guardrail_count"] == 1
        assert sdk_summary["agent_run_error_count"] == 1
        assert sdk_summary["harness_verification_result_count"] == 2
        assert sdk_summary["harness_verification_failed_count"] == 1
        assert sdk_summary["harness_verification_status_counts"] == {"completed": 1, "failed": 1}
        assert sdk_summary["agent_stream_event_type_counts"] == {"run_item_stream_event": 1}
        assert sdk_summary["agent_stream_event_name_counts"] == {"tool_called": 1}
        assert sdk_summary["agent_stream_item_class_counts"] == {"ToolCallItem": 1}
        assert sdk_summary["agent_stream_tool_name_counts"] == {"record_signal": 1}
        assert sdk_summary["final_output_count"] == 1
        transcript_summary = harness_api_module.summarize_client_transcript(
            {
                "status": "completed",
                "task_id": "task_summary",
                "session_id": "thread_summary",
                "status_line": {"task_status": "paused"},
                "messages": [{"type": "user_message"}, {"type": "streamlined_text"}, {"type": "final_output"}],
                "tool_progress": [{"tool_name": "search_web_candidates"}],
                "agent_model_responses": [
                    {
                        "model": "gpt-5-mini",
                        "provider": "openai",
                        "api_style": "responses",
                    }
                ],
                "agent_phases": [{"phase_name": "after_approval"}],
                "agent_sessions": [{"agent_session_id": "trace_verify_session"}],
                "agent_handoffs": [{"source_agent": "Router", "target_agent": "Specialist"}],
                "agent_approvals": [
                    {"type": "agent_approval", "subtype": "approval_request"},
                    {"type": "agent_approval", "subtype": "approval_decision", "decision": "approved"},
                ],
                "agent_questions": [
                    {"type": "agent_question", "subtype": "missing_info_request", "questions": [{"field": "baseline_evidence"}]}
                ],
                "agent_guidance": [
                    {
                        "type": "agent_guidance",
                        "reason": "continue_search_for_closer_measurements",
                        "next_tool_suggestions": [{"tool": "search_web_candidates"}],
                    }
                ],
                "agent_eval_case_verifications": [
                    {
                        "type": "agent_eval_case_verification",
                        "status": "failed",
                        "execution_status": "failed",
                        "expected_status_matches": False,
                        "failure_label": "aggregate:matrix_summary_payload_matches",
                    }
                ],
                "agent_guardrails": [{"guardrail_name": "verify_guardrail"}],
                "agent_run_errors": [{"error_type": "VerifyError"}],
                "harness_verification_results": [
                    {"type": "harness_verification_result", "status": "completed"},
                    {"type": "harness_verification_result", "status": "failed"},
                ],
                "agent_stream_events": [
                    {
                        "stream_index": 1,
                        "type": "agent_stream_event",
                        "stream_event_type": "run_item_stream_event",
                        "stream_event_name": "tool_called",
                        "raw_event_type": "none",
                        "item_class": "ToolCallItem",
                        "tool_name": "record_signal",
                    }
                ],
                "source_endpoints": {"sdk_events": "/sdk-events", "client_transcript": "/client-transcript"},
            },
            task_id="task_summary",
        )
        assert transcript_summary["source"] == "harness_client_transcript_summary"
        assert transcript_summary["summary_requested"] is True
        assert transcript_summary["agent_guidance_count"] == 1
        assert transcript_summary["agent_guidance_reasons"] == ["continue_search_for_closer_measurements"]
        assert transcript_summary["agent_guidance_reason_counts"] == {"continue_search_for_closer_measurements": 1}
        assert transcript_summary["item_type_counts"]["agent_guidance"] == 1
        assert transcript_summary["agent_eval_case_verification_count"] == 1
        assert transcript_summary["agent_eval_case_verification_failed_count"] == 1
        assert transcript_summary["agent_eval_case_failure_label_counts"] == {
            "aggregate:matrix_summary_payload_matches": 1
        }
        assert transcript_summary["item_type_counts"]["agent_eval_case_verification"] == 1
        assert transcript_summary["item_type_counts"]["tool_progress"] == 1
        assert transcript_summary["item_type_counts"]["agent_model_response"] == 1
        assert transcript_summary["item_type_counts"]["agent_phase"] == 1
        assert transcript_summary["item_type_counts"]["agent_session"] == 1
        assert transcript_summary["item_type_counts"]["agent_handoff"] == 1
        assert transcript_summary["item_type_counts"]["agent_approval"] == 2
        assert transcript_summary["item_type_counts"]["agent_guardrail"] == 1
        assert transcript_summary["item_type_counts"]["agent_run_error"] == 1
        assert transcript_summary["item_type_counts"]["harness_verification_result"] == 2
        assert transcript_summary["item_type_counts"]["final_output"] == 1
        assert transcript_summary["final_output_count"] == 1
        assert transcript_summary["missing_info_question_count"] == 1
        assert transcript_summary["agent_model_response_count"] == 1
        assert transcript_summary["agent_model_response_provider_counts"] == {"openai": 1}
        assert transcript_summary["agent_model_response_api_style_counts"] == {"responses": 1}
        assert transcript_summary["agent_stream_event_count"] == 1
        assert transcript_summary["agent_phase_count"] == 1
        assert transcript_summary["agent_session_count"] == 1
        assert transcript_summary["agent_handoff_count"] == 1
        assert transcript_summary["agent_approval_count"] == 2
        assert transcript_summary["agent_guardrail_count"] == 1
        assert transcript_summary["agent_run_error_count"] == 1
        assert transcript_summary["harness_verification_result_count"] == 2
        assert transcript_summary["harness_verification_failed_count"] == 1
        assert transcript_summary["harness_verification_status_counts"] == {"completed": 1, "failed": 1}
        assert transcript_summary["agent_stream_event_type_counts"] == {"run_item_stream_event": 1}
        assert transcript_summary["agent_stream_event_name_counts"] == {"tool_called": 1}
        assert transcript_summary["agent_stream_item_class_counts"] == {"ToolCallItem": 1}
        assert transcript_summary["agent_stream_tool_name_counts"] == {"record_signal": 1}
        progress_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "progress",
                "--task-id",
                "task_verify",
                "--items-json",
                '[{"item_id":"verify","content":"Verify harness CLI progress","active_form":"Verifying harness CLI progress","status":"completed"}]',
                "--metadata-json",
                '{"source":"verify"}',
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        progress_request = json.loads(progress_dry_run.stdout)
        assert progress_request["method"] == "POST"
        assert progress_request["url"].endswith("/tasks/task_verify/progress")
        assert progress_request["body"]["items"][0]["item_id"] == "verify"
        queued_message_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "queued-message",
                "--task-id",
                "task_verify",
                "--prompt",
                "queued follow-up",
                "--priority",
                "next",
                "--message-uuid",
                "msg_verify",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        queued_message_request = json.loads(queued_message_dry_run.stdout)
        assert queued_message_request["url"].endswith("/tasks/task_verify/queued-messages")
        assert queued_message_request["body"]["priority"] == "next"
        assert queued_message_request["body"]["message_uuid"] == "msg_verify"
        cancel_queued_message_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "cancel-queued-message",
                "--task-id",
                "task_verify",
                "--message-uuid",
                "msg_verify",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        cancel_queued_message_request = json.loads(cancel_queued_message_dry_run.stdout)
        assert cancel_queued_message_request["url"].endswith("/tasks/task_verify/queued-messages/cancel")
        permission_decision_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "permission-decision",
                "--task-id",
                "task_verify",
                "--call-id",
                "call_verify",
                "--tool-name",
                "adapter_probe",
                "--behavior",
                "ask",
                "--decision-reason-json",
                '{"type":"mode","mode":"default"}',
                "--suggestions-json",
                '[{"type":"addRules","destination":"session","rules":[{"toolName":"adapter_probe"}],"behavior":"allow"}]',
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        permission_decision_request = json.loads(permission_decision_dry_run.stdout)
        assert permission_decision_request["url"].endswith("/tasks/task_verify/permission-decisions")
        assert permission_decision_request["body"]["behavior"] == "ask"
        assert permission_decision_request["body"]["suggestions"][0]["behavior"] == "allow"
        annotate_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "annotate",
                "--task-id",
                "task_verify",
                "--title",
                "CLI session title",
                "--tag",
                "sdk-cli",
                "--summary",
                "CLI annotation summary",
                "--expected-state-version",
                "4",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        annotate_request = json.loads(annotate_dry_run.stdout)
        assert annotate_request["url"].endswith("/tasks/task_verify/annotations")
        assert annotate_request["body"]["title"] == "CLI session title"
        assert annotate_request["body"]["expected_state_version"] == 4
        fork_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "fork",
                "--task-id",
                "task_verify",
                "--prompt",
                "forked cli prompt",
                "--idempotency-key",
                "fork-cli-verify",
                "--metadata-json",
                '{"source":"verify"}',
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        fork_request = json.loads(fork_dry_run.stdout)
        assert fork_request["url"].endswith("/tasks/task_verify/fork")
        assert fork_request["body"]["idempotency_key"] == "fork-cli-verify"
        command_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "command",
                "--task-id",
                "task_verify",
                "--command",
                "pause",
                "--expected-state-version",
                "5",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        command_request = json.loads(command_dry_run.stdout)
        assert command_request["url"].endswith("/tasks/task_verify/commands")
        assert command_request["body"]["command"] == "pause"
        assert command_request["body"]["expected_state_version"] == 5
        reply_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "reply",
                "--task-id",
                "task_verify",
                "--prompt",
                "resume cli prompt",
                "--expected-state-version",
                "6",
                "--background",
                "false",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        reply_request = json.loads(reply_dry_run.stdout)
        assert reply_request["method"] == "POST"
        assert reply_request["url"].endswith("/tasks/task_verify/reply?background=false")
        assert reply_request["body"]["prompt"] == "resume cli prompt"
        assert reply_request["body"]["expected_state_version"] == 6
        subagents_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "subagents",
                "--task-id",
                "task_verify",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        subagents_request = json.loads(subagents_dry_run.stdout)
        assert subagents_request["method"] == "GET"
        assert subagents_request["url"].endswith("/tasks/task_verify/subagents")
        subagent_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "subagent",
                "--task-id",
                "task_verify",
                "--prompt",
                "child cli work",
                "--cwd",
                str(root),
                "--sandbox",
                "read-only",
                "--skill",
                "agentic-harness-loop",
                "--idempotency-key",
                "child-cli-verify",
                "--background",
                "false",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        subagent_request = json.loads(subagent_dry_run.stdout)
        assert subagent_request["url"].endswith("/tasks/task_verify/subagents?background=false")
        assert "parent_task_id" not in subagent_request["body"]
        assert subagent_request["body"]["idempotency_key"] == "child-cli-verify"
        trailing_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--client-id",
                "verify-client",
                "subagent",
                "--task-id",
                "task_verify",
                "--prompt",
                "child cli work",
                "--cwd",
                str(root),
                "--sandbox",
                "read-only",
                "--skill",
                "agentic-harness-loop",
                "--idempotency-key",
                "child-cli-verify-trailing",
                "--background",
                "false",
                "--dry-run",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        trailing_dry_run_request = json.loads(trailing_dry_run.stdout)
        assert trailing_dry_run_request["url"].endswith("/tasks/task_verify/subagents?background=false")
        assert trailing_dry_run_request["body"]["idempotency_key"] == "child-cli-verify-trailing"
        transcript_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "client-transcript",
                "--task-id",
                "task_verify",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        transcript_request = json.loads(transcript_dry_run.stdout)
        assert transcript_request["url"].endswith("/tasks/task_verify/client-transcript")
        transcript_summary_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "client-transcript",
                "--task-id",
                "task_verify",
                "--summary",
                "true",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        transcript_summary_request = json.loads(transcript_summary_dry_run.stdout)
        assert transcript_summary_request["url"].endswith("/tasks/task_verify/client-transcript?summary=true")
        assert transcript_summary_request["summary_requested"] is True
        session_info_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "session-info",
                "--task-id",
                "task_verify",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        session_info_request = json.loads(session_info_dry_run.stdout)
        assert session_info_request["url"].endswith("/tasks/task_verify/session-info")
        sdk_initialize_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "sdk-initialize",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        sdk_initialize_request = json.loads(sdk_initialize_dry_run.stdout)
        assert sdk_initialize_request["method"] == "GET"
        assert sdk_initialize_request["url"].endswith("/sdk/initialize")
        sdk_mcp_status_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "sdk-mcp-status",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        sdk_mcp_status_request = json.loads(sdk_mcp_status_dry_run.stdout)
        assert sdk_mcp_status_request["url"].endswith("/sdk/mcp-status")
        sdk_context_usage_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "sdk-context-usage",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        sdk_context_usage_request = json.loads(sdk_context_usage_dry_run.stdout)
        assert sdk_context_usage_request["url"].endswith("/sdk/context-usage")
        sessions_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "sessions",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        sessions_request = json.loads(sessions_dry_run.stdout)
        assert sessions_request["url"].endswith("/sessions")
        tasks_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "tasks",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        tasks_request = json.loads(tasks_dry_run.stdout)
        assert tasks_request["url"].endswith("/tasks")
        eval_cases_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "eval-cases",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        eval_cases_request = json.loads(eval_cases_dry_run.stdout)
        assert eval_cases_request["url"].endswith("/eval-cases")
        eval_case_verify_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "eval-case-verify",
                "--eval-case-id",
                "7",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        eval_case_verify_request = json.loads(eval_case_verify_dry_run.stdout)
        assert eval_case_verify_request["status"] == "dry_run"
        assert eval_case_verify_request["request"]["url"].endswith("/eval-cases?limit=50")
        assert eval_case_verify_request["selection"]["eval_case_id"] == 7
        harness_api_module = _load_module("harness_api_verify", harness_api)
        eval_case_readback = {
            "status": "completed",
            "eval_cases": [
                {
                    "eval_case_id": 7,
                    "name": "verify eval-case command",
                    "source_task_id": "task_eval_verify",
                    "failure_label": "synthetic",
                    "expected": {"status": "completed"},
                    "verifier_command": f"{sys.executable} -c \"print('eval verifier ok')\"",
                    "evidence_refs": ["task:task_eval_verify"],
                    "metadata": {"source": "verify"},
                    "status": "active",
                }
            ],
        }
        eval_case_verify_args = argparse.Namespace(
            command="eval-case-verify",
            dry_run=False,
            eval_case_id=7,
            name=None,
            failure_label=None,
            cwd=str(root),
            timeout_seconds=30,
            execute=False,
            record_result_task_id=None,
        )
        eval_case_verify_ready, eval_case_verify_ready_code = (
            harness_api_module.apply_command_validation(eval_case_verify_args, eval_case_readback)
        )
        assert eval_case_verify_ready_code == 0
        assert eval_case_verify_ready["status"] == "ready"
        assert eval_case_verify_ready["execution_skipped_reason"] == "execute_flag_required"
        eval_case_verify_args.execute = True
        eval_case_verify_executed, eval_case_verify_executed_code = (
            harness_api_module.apply_command_validation(eval_case_verify_args, eval_case_readback)
        )
        assert eval_case_verify_executed_code == 0
        assert eval_case_verify_executed["status"] == "completed"
        assert eval_case_verify_executed["execution"]["returncode"] == 0
        assert eval_case_verify_executed["execution"]["executed_command"]
        assert eval_case_verify_executed["expected_status_matches"] is True
        failing_eval_case_readback = {
            "status": "completed",
            "eval_cases": [
                {
                    **eval_case_readback["eval_cases"][0],
                    "eval_case_id": 8,
                    "verifier_command": f"{sys.executable} -c \"import sys; sys.exit(3)\"",
                }
            ],
        }
        eval_case_verify_args.eval_case_id = 8
        eval_case_verify_failed, eval_case_verify_failed_code = (
            harness_api_module.apply_command_validation(eval_case_verify_args, failing_eval_case_readback)
        )
        assert eval_case_verify_failed_code == 1
        assert eval_case_verify_failed["status"] == "failed"
        assert "eval_case_verifier_completed" in eval_case_verify_failed["failed_checks"]
        recorded_eval_case_verify_observations: list[dict[str, Any]] = []
        original_harness_api_request = harness_api_module.request

        def fake_eval_case_verify_record_request(
            _args: argparse.Namespace, method: str, path: str, body: Any = None
        ) -> dict[str, Any]:
            del _args
            assert method == "POST"
            assert path == "/tasks/task_eval_verify_result/observations"
            assert isinstance(body, dict)
            recorded_eval_case_verify_observations.append(body)
            return {
                **body,
                "task_id": "task_eval_verify_result",
                "observation_id": 909,
            }

        try:
            harness_api_module.request = fake_eval_case_verify_record_request
            eval_case_verify_args.eval_case_id = 7
            eval_case_verify_args.execute = True
            eval_case_verify_args.record_result_task_id = "task_eval_verify_result"
            eval_case_verify_recorded, eval_case_verify_recorded_code = (
                harness_api_module.apply_command_validation(eval_case_verify_args, eval_case_readback)
            )
        finally:
            harness_api_module.request = original_harness_api_request
        assert eval_case_verify_recorded_code == 0
        assert eval_case_verify_recorded["record_result"]["status"] == "completed"
        assert eval_case_verify_recorded["record_result"]["observation_id"] == 909
        assert len(recorded_eval_case_verify_observations) == 1
        recorded_eval_observation = recorded_eval_case_verify_observations[0]
        assert recorded_eval_observation["tool_name"] == "eval_case_verification"
        assert recorded_eval_observation["status"] == "completed"
        assert recorded_eval_observation["evidence_refs"] == [
            "eval_case:7",
            "task:task_eval_verify",
        ]
        assert recorded_eval_observation["result"]["eval_case_id"] == 7
        assert recorded_eval_observation["result"]["expected_status_matches"] is True
        assert recorded_eval_observation["result"]["execution"]["returncode"] == 0
        loops_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "loops",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        loops_request = json.loads(loops_dry_run.stdout)
        assert loops_request["url"].endswith("/loops")
        loop_create_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "loop-create",
                "--name",
                "CLI loop verify",
                "--prompt",
                "run loop verification",
                "--cwd",
                str(root),
                "--sandbox",
                "read-only",
                "--skill",
                "agentic-harness-loop",
                "--verification-command",
                "python -B verify.py",
                "--idempotency-key",
                "loop-cli-verify",
                "--interval-seconds",
                "60",
                "--max-iterations",
                "2",
                "--trace-source",
                "harness_events",
                "--eval-target",
                "loop_cli_gap",
                "--termination-condition",
                "max_iterations",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        loop_create_request = json.loads(loop_create_dry_run.stdout)
        assert loop_create_request["method"] == "POST"
        assert loop_create_request["url"].endswith("/loops")
        assert loop_create_request["body"]["name"] == "CLI loop verify"
        assert loop_create_request["body"]["task"]["idempotency_key"] == "loop-cli-verify"
        assert loop_create_request["body"]["interval_seconds"] == 60
        assert loop_create_request["body"]["max_iterations"] == 2
        assert loop_create_request["body"]["evidence_contract"]["trace_sources"] == ["harness_events"]
        loop_tick_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "loop-tick",
                "--loop-id",
                "loop_verify",
                "--force",
                "true",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        loop_tick_request = json.loads(loop_tick_dry_run.stdout)
        assert loop_tick_request["url"].endswith("/loops/loop_verify/tick")
        assert loop_tick_request["body"]["force"] is True
        assert loop_tick_request["body"]["start"] is False
        assert loop_tick_request["body"]["background"] is True
        loop_evidence_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "loop-evidence",
                "--loop-id",
                "loop_verify",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        loop_evidence_request = json.loads(loop_evidence_dry_run.stdout)
        assert loop_evidence_request["method"] == "GET"
        assert loop_evidence_request["url"].endswith("/loops/loop_verify/evidence")
        worker_tick_dry_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "--client-id",
                "verify-client",
                "worker-tick",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        worker_tick_request = json.loads(worker_tick_dry_run.stdout)
        assert worker_tick_request["method"] == "POST"
        assert worker_tick_request["url"].endswith("/workers/tick")
        assert worker_tick_request["body"] is None
        ready_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "--dry-run",
                "ready",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        assert json.loads(ready_run.stdout)["url"].endswith("/ready")
        plan_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "plan",
                "--goal",
                "failure",
                "--status",
                "409",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        plan_payload = json.loads(plan_run.stdout)
        assert plan_payload["goal"] == "failure"
        assert any("lease_epoch" in item or "state_version" in item for item in plan_payload["recommended_commands"])
        provider_plan_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "plan",
                "--goal",
                "provider",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        provider_plan_payload = json.loads(provider_plan_run.stdout)
        assert any("capabilities --summary true" in item for item in provider_plan_payload["recommended_commands"])
        assert any(
            "--expect-external-api-called true" in item
            and "--expect-provider-response-received true" in item
            for item in provider_plan_payload["recommended_commands"]
        )
        task_plan_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "plan",
                "--goal",
                "task",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        task_plan_payload = json.loads(task_plan_run.stdout)
        assert any(
            "capabilities --summary true" in item and "--require-sdk-event-type agent_eval_case_verification" in item
            for item in task_plan_payload["recommended_commands"]
        )
        assert any("--require-summary-field harness_verification_result_count" in item for item in task_plan_payload["recommended_commands"])
        assert any("--start false" in item for item in task_plan_payload["recommended_commands"])
        assert any("sdk-initialize" in item for item in task_plan_payload["recommended_commands"])
        assert any("sdk-context-usage" in item for item in task_plan_payload["recommended_commands"])
        assert any(item == "tasks" for item in task_plan_payload["recommended_commands"])
        assert any("sessions" in item for item in task_plan_payload["recommended_commands"])
        assert any("events --task-id" in item for item in task_plan_payload["recommended_commands"])
        assert any("sdk-events" in item for item in task_plan_payload["recommended_commands"])
        assert any("sdk-events --task-id" in item and "--summary true" in item for item in task_plan_payload["recommended_commands"])
        assert any("client-transcript" in item for item in task_plan_payload["recommended_commands"])
        assert any("client-transcript --task-id" in item and "--summary true" in item for item in task_plan_payload["recommended_commands"])
        assert any("annotate --task-id" in item for item in task_plan_payload["recommended_commands"])
        assert any("fork --task-id" in item for item in task_plan_payload["recommended_commands"])
        assert any("command --task-id" in item for item in task_plan_payload["recommended_commands"])
        assert any("reply --task-id" in item for item in task_plan_payload["recommended_commands"])
        assert any("queued-message --task-id" in item for item in task_plan_payload["recommended_commands"])
        assert any("cancel-queued-message --task-id" in item for item in task_plan_payload["recommended_commands"])
        subagent_plan_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "plan",
                "--goal",
                "subagent",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        subagent_plan_payload = json.loads(subagent_plan_run.stdout)
        assert any("subagent --task-id" in item for item in subagent_plan_payload["recommended_commands"])
        assert any("subagents --task-id" in item for item in subagent_plan_payload["recommended_commands"])
        loop_plan_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "plan",
                "--goal",
                "loop",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        loop_plan_payload = json.loads(loop_plan_run.stdout)
        assert any("loop-create" in item for item in loop_plan_payload["recommended_commands"])
        assert any("loop-tick" in item for item in loop_plan_payload["recommended_commands"])
        assert any("loop-evidence" in item for item in loop_plan_payload["recommended_commands"])
        trace_plan_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "plan",
                "--goal",
                "trace",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        trace_plan_payload = json.loads(trace_plan_run.stdout)
        assert trace_plan_payload["goal"] == "trace"
        assert any("agents_sdk_smoke.py" in item for item in trace_plan_payload["recommended_commands"])
        assert any("trace_ingest.py" in item for item in trace_plan_payload["recommended_commands"])
        assert any("--dry-run" in item for item in trace_plan_payload["recommended_commands"])
        assert any("--readback " in f"{item} " for item in trace_plan_payload["recommended_commands"])
        assert any("--readback-only" in item for item in trace_plan_payload["recommended_commands"])
        assert any("--fail-on-readback-invalid" in item for item in trace_plan_payload["recommended_commands"])
        assert any("sdk-events --task-id" in item for item in trace_plan_payload["recommended_commands"])
        assert any("sdk-events --task-id" in item and "--summary true" in item for item in trace_plan_payload["recommended_commands"])
        assert any("client-transcript --task-id" in item for item in trace_plan_payload["recommended_commands"])
        assert any("client-transcript --task-id" in item and "--summary true" in item for item in trace_plan_payload["recommended_commands"])
        assert any(
            "capabilities --summary true" in item and "--require-transcript-section agent_eval_case_verifications" in item
            for item in trace_plan_payload["recommended_commands"]
        )
        assert any("--require-summary-field agent_eval_case_verification_count" in item for item in trace_plan_payload["recommended_commands"])
        assert any("eval-case" in item for item in trace_plan_payload["recommended_commands"])
        assert any("capabilities --summary" in item for item in trace_plan_payload["guardrails"])
        assert any("digest validation" in item for item in trace_plan_payload["guardrails"])
        tool_plan_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "plan",
                "--goal",
                "tool-evidence",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        tool_plan_payload = json.loads(tool_plan_run.stdout)
        assert any("--call-id" in item for item in tool_plan_payload["recommended_commands"])
        assert any("permission-decision --task-id" in item for item in tool_plan_payload["recommended_commands"])
        assert any("progress --task-id" in item for item in tool_plan_payload["recommended_commands"])
        worker_plan_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "plan",
                "--goal",
                "worker",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        worker_plan_payload = json.loads(worker_plan_run.stdout)
        assert any(item == "worker-tick" for item in worker_plan_payload["recommended_commands"])
        assert any("heartbeat --task-id" in item for item in worker_plan_payload["recommended_commands"])
        eval_plan_run = subprocess.run(
            [
                sys.executable,
                str(harness_api),
                "plan",
                "--goal",
                "eval",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        eval_plan_payload = json.loads(eval_plan_run.stdout)
        assert any(item == "eval-cases" for item in eval_plan_payload["recommended_commands"])
        assert any("--require-sdk-event-type harness_verification_result" in item for item in eval_plan_payload["recommended_commands"])
        trace_ingest = root / ".agents" / "skills" / "agentic-harness-loop" / "scripts" / "trace_ingest.py"
        assert trace_ingest.exists()
        live_smoke = _load_module(
            "fitcheck_live_agent_smoke_verify",
            root / "backend_harness" / "verify_live_agent_smoke.py",
        )
        live_smoke._assert_live_agent_harness_export_contract()
        trace_fixture = Path(temp) / "agent-result.json"
        trace_fixture.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "source": "agents_sdk_smoke",
                    "provider": "openai",
                    "api_style": "responses",
                    "sdk": "openai-agents",
                    "sdk_version": "0.17.7",
                    "external_api_called": True,
                    "model": "gpt-5.4-mini",
                    "trace_id": "trace_verify",
                    "summary": {
                        "new_item_count": 4,
                        "tool_call_count": 2,
                        "tool_output_count": 2,
                        "marker_observed": True,
                        "final_ok": True,
                    },
                    "missing_info": {
                        "status": "waiting_for_user",
                        "questions": [
                            {
                                "field": "baseline_evidence",
                                "question": "기준 옷의 실측/소재 근거가 필요합니다.",
                            }
                        ],
                        "reason": "baseline_evidence_required_before_precise_similarity_report",
                        "evidence_status": "baseline_evidence_incomplete",
                        "source_observation": "write_purchase_report",
                        "agent": "Harness Question Agent",
                    },
                    "agent_guidance": [
                        {
                            "guidance_type": "backend_enrichment_required",
                            "subtype": "continue_candidate_enrichment",
                            "reason": "continue_search_for_closer_measurements",
                            "status": "completed",
                            "required_action": "Call the suggested backend tools before reporting.",
                            "ask_user_allowed": False,
                            "next_tool_suggestions": [
                                {
                                    "tool": "search_web_candidates",
                                    "arguments": {
                                        "query": "tee length 68 shoulder 52 chest 55 sleeve 20",
                                        "refresh_web": False,
                                    },
                                    "reason": "continue_search_for_closer_measurement_match",
                                }
                            ],
                            "close_fit_search_budget": {
                                "attempt_count": 1,
                                "attempt_limit": 24,
                                "remaining_attempts": 23,
                                "attempted_queries": ["tee length 68 shoulder 52 chest 55 sleeve 20"],
                            },
                        }
                    ],
                    "raw_responses": [
                        {
                            "response_id": "resp_verify_1",
                            "request_id": "req_verify_1",
                            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                            "output": [{"type": "function_call", "name": "record_signal"}],
                        },
                        {
                            "id": "resp_verify_2",
                            "model": "gpt-5.4-mini",
                            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                            "output": [],
                        }
                    ],
                    "new_items": [
                        {"type": "ToolCallItem", "call_id": "call_1", "tool_name": "search", "arguments": {"q": "coat"}},
                        {"type": "ToolCallOutputItem", "call_id": "call_1", "tool_name": "search", "output": {"matches": []}},
                        {
                            "type": "tool_call_item",
                            "class": "ToolCallItem",
                            "raw_item": {
                                "type": "function_call",
                                "call_id": "call_nested",
                                "name": "record_signal",
                                "arguments": "{\"marker\":\"AGENTS_SDK_LIVE_TOOL_OK\"}",
                            },
                        },
                        {
                            "type": "tool_call_output_item",
                            "class": "ToolCallOutputItem",
                            "raw_item": {
                                "type": "function_call_output",
                                "call_id": "call_nested",
                                "output": "{\"status\":\"recorded\"}",
                            },
                            "output": "{\"status\":\"recorded\"}",
                        },
                        {
                            "type": "handoff_call_item",
                            "class": "HandoffCallItem",
                            "agent": {"name": "Harness Router Agent"},
                            "raw_item": {
                                "type": "function_call",
                                "call_id": "call_handoff",
                                "name": "transfer_to_harness_specialist",
                                "arguments": "{}",
                            },
                        },
                        {
                            "type": "handoff_output_item",
                            "class": "HandoffOutputItem",
                            "agent": {"name": "Harness Router Agent"},
                            "source_agent": {"name": "Harness Router Agent"},
                            "target_agent": {"name": "Harness Specialist Agent"},
                            "raw_item": {
                                "type": "function_call_output",
                                "call_id": "call_handoff",
                                "output": "{\"assistant\":\"Harness Specialist Agent\"}",
                            },
                        },
                        {
                            "type": "tool_call_item",
                            "class": "ToolCallItem",
                            "call_id": "call_approval",
                            "tool_name": "approval_required_signal",
                            "raw_item": {
                                "type": "function_call",
                                "call_id": "call_approval",
                                "name": "approval_required_signal",
                                "arguments": "{\"marker\":\"AGENTS_SDK_APPROVAL_TOOL_OK\"}",
                            },
                            "agent": {"name": "Harness Approval Agent"},
                        },
                        {
                            "type": "tool_approval_item",
                            "class": "ToolApprovalItem",
                            "call_id": "call_approval",
                            "tool_name": "approval_required_signal",
                            "arguments": "{\"marker\":\"AGENTS_SDK_APPROVAL_TOOL_OK\"}",
                            "agent": {"name": "Harness Approval Agent"},
                            "raw_item": {
                                "type": "function_call",
                                "call_id": "call_approval",
                                "name": "approval_required_signal",
                                "arguments": "{\"marker\":\"AGENTS_SDK_APPROVAL_TOOL_OK\"}",
                            },
                        },
                        {
                            "type": "tool_call_output_item",
                            "class": "ToolCallOutputItem",
                            "call_id": "call_approval",
                            "raw_item": {
                                "type": "function_call_output",
                                "call_id": "call_approval",
                                "output": (
                                    "{\"status\":\"approved_and_recorded\","
                                    "\"marker\":\"AGENTS_SDK_APPROVAL_TOOL_OK\"}"
                                ),
                            },
                            "output": (
                                "{\"status\":\"approved_and_recorded\","
                                "\"marker\":\"AGENTS_SDK_APPROVAL_TOOL_OK\"}"
                            ),
                            "agent": {"name": "Harness Approval Agent"},
                        },
                        {
                            "type": "agent_guardrail",
                            "name": "continue_candidate_enrichment",
                            "status": "completed",
                            "reason": "continue_search_for_closer_measurements",
                            "result": {
                                "guidance_type": "backend_enrichment_required",
                                "reason": "continue_search_for_closer_measurements",
                                "required_action": "Call the suggested backend tools before reporting.",
                                "ask_user_allowed": False,
                                "next_tool_suggestions": [
                                    {
                                        "tool": "search_web_candidates",
                                        "arguments": {
                                            "query": "tee length 68 shoulder 52 chest 55 sleeve 20",
                                            "refresh_web": False,
                                        },
                                        "reason": "continue_search_for_closer_measurement_match",
                                    }
                                ],
                                "close_fit_search_budget": {
                                    "attempt_count": 1,
                                    "attempt_limit": 24,
                                    "remaining_attempts": 23,
                                    "attempted_queries": ["tee length 68 shoulder 52 chest 55 sleeve 20"],
                                },
                            },
                        },
                    ],
                    "interruptions": [
                        {
                            "type": "tool_approval_item",
                            "class": "ToolApprovalItem",
                            "call_id": "call_approval",
                            "tool_name": "approval_required_signal",
                            "arguments": "{\"marker\":\"AGENTS_SDK_APPROVAL_TOOL_OK\"}",
                            "agent": {"name": "Harness Approval Agent"},
                            "raw_item": {
                                "type": "function_call",
                                "call_id": "call_approval",
                                "name": "approval_required_signal",
                                "arguments": "{\"marker\":\"AGENTS_SDK_APPROVAL_TOOL_OK\"}",
                            },
                        }
                    ],
                    "approval_decisions": [
                        {
                            "decision": "approved",
                            "call_id": "call_approval",
                            "tool_name": "approval_required_signal",
                            "item_type": "tool_approval_item",
                            "arguments": "{\"marker\":\"AGENTS_SDK_APPROVAL_TOOL_OK\"}",
                            "raw_type": "function_call",
                            "agent": "Harness Approval Agent",
                        }
                    ],
                    "phases": [
                        {
                            "name": "initial",
                            "final_output": None,
                            "new_items": [
                                {"type": "tool_call_item", "call_id": "call_approval"},
                                {"type": "tool_approval_item", "call_id": "call_approval"},
                            ],
                            "raw_response_count": 1,
                            "interruption_count": 1,
                        },
                        {
                            "name": "after_approval",
                            "final_output": "done",
                            "new_items": [
                                {"type": "tool_call_output_item", "call_id": "call_approval"}
                            ],
                            "raw_response_count": 2,
                            "interruption_count": 0,
                        },
                    ],
                    "session": {
                        "session_id": "trace_verify_session",
                        "backend": "SQLiteSession",
                        "db_path": "harness_outputs/sdk-live-smoke/verify.session.sqlite3",
                        "turn_count": 2,
                        "final_item_count": 4,
                        "final_item_types": ["user", "message", "user", "message"],
                        "final_items_preview": [
                            {
                                "index": 1,
                                "role": "user",
                                "text": "Remember this session code exactly: BLUE-HARNESS-42.",
                            },
                            {
                                "index": 2,
                                "role": "assistant",
                                "type": "message",
                                "text": "SESSION_TURN_1_OK",
                            },
                            {
                                "index": 3,
                                "role": "user",
                                "text": "Using only the prior session context, what was the session code?",
                            },
                            {
                                "index": 4,
                                "role": "assistant",
                                "type": "message",
                                "text": "AGENTS_SDK_SESSION_FINAL_OK",
                            },
                        ],
                        "turns": [
                            {
                                "index": 1,
                                "phase": "session_initial",
                                "final_output": "SESSION_TURN_1_OK",
                                "item_count_before": 0,
                                "item_count_after": 2,
                            },
                            {
                                "index": 2,
                                "phase": "session_turn_2",
                                "final_output": "AGENTS_SDK_SESSION_FINAL_OK",
                                "item_count_before": 2,
                                "item_count_after": 4,
                            },
                        ],
                    },
                    "stream_events": [
                        {
                            "index": 1,
                            "type": "raw_response_event",
                            "class": "RawResponsesStreamEvent",
                            "raw_event_type": "response.output_text.delta",
                            "raw_event": {
                                "type": "response.output_text.delta",
                                "delta": "A",
                                "output_index": 0,
                            },
                        },
                        {
                            "index": 2,
                            "type": "run_item_stream_event",
                            "class": "RunItemStreamEvent",
                            "name": "tool_called",
                            "item": {
                                "type": "tool_call_item",
                                "class": "ToolCallItem",
                                "raw_item": {
                                    "type": "function_call",
                                    "call_id": "call_stream",
                                    "name": "record_signal",
                                    "arguments": "{\"marker\":\"AGENTS_SDK_STREAM_TOOL_OK\"}",
                                },
                            },
                        },
                        {
                            "index": 3,
                            "type": "run_item_stream_event",
                            "class": "RunItemStreamEvent",
                            "name": "handoff_called",
                            "item": {
                                "type": "handoff_call_item",
                                "class": "HandoffCallItem",
                                "raw_item": {
                                    "type": "function_call",
                                    "call_id": "call_stream_handoff",
                                    "name": "transfer_to_harness_specialist",
                                    "arguments": "{}",
                                },
                                "agent": {"name": "Harness Router Agent"},
                            },
                        },
                        {
                            "index": 4,
                            "type": "run_item_stream_event",
                            "class": "RunItemStreamEvent",
                            "name": "handoff_output",
                            "item": {
                                "type": "handoff_output_item",
                                "class": "HandoffOutputItem",
                                "raw_item": {
                                    "type": "function_call_output",
                                    "call_id": "call_stream_handoff",
                                    "output": "{\"assistant\":\"Harness Specialist Agent\"}",
                                },
                                "source_agent": {"name": "Harness Router Agent"},
                                "target_agent": {"name": "Harness Specialist Agent"},
                            },
                        },
                        {
                            "index": 5,
                            "type": "agent_updated_stream_event",
                            "class": "AgentUpdatedStreamEvent",
                            "agent": {"name": "Harness Stream Agent"},
                        },
                    ],
                    "input_guardrail_results": [
                        {
                            "class": "InputGuardrailResult",
                            "guardrail": {"class": "InputGuardrail", "name": "scope"},
                            "output": {
                                "output_info": {"status": "checked", "marker": "GUARDRAIL_INPUT_OK"},
                                "tripwire_triggered": False,
                            },
                        }
                    ],
                    "output_guardrail_results": [
                        {
                            "class": "OutputGuardrailResult",
                            "guardrail": {"class": "OutputGuardrail", "name": "harness_output_guardrail"},
                            "agent": {"name": "Harness Guardrail Agent"},
                            "agent_output": "AGENTS_SDK_GUARDRAIL_FINAL_OK",
                            "output": {
                                "output_info": {
                                    "status": "checked",
                                    "marker": "AGENTS_SDK_GUARDRAIL_TOOL_OK",
                                },
                                "tripwire_triggered": False,
                            },
                        }
                    ],
                    "final_output": "done",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        live_publisher = root / "backend_harness" / "publish_live_agent_smoke_export.py"
        assert live_publisher.exists()
        eval_case_fixture = Path(temp) / "agent-result-eval-case.json"
        eval_case_fixture.write_text(
            json.dumps(
                {
                    "name": "fitcheck live agent SDK smoke regression: waiting_for_user",
                    "failure_label": "regression_seed_waiting_for_user",
                    "input": {"goal": "verify live smoke export publisher"},
                    "expected": {
                        "run_status": "waiting_for_user",
                        "sdk_events_projected": True,
                        "client_transcript_projected": True,
                    },
                    "verifier_command": "cd backend_harness && FITCHECK_RUN_LIVE_AGENT_SMOKE=1 ../.venv/bin/python -B verify_live_agent_smoke.py",
                    "evidence_refs": ["fitcheck_run:trace_verify"],
                    "metadata": {"kind": "fitcheck-live-agent-sdk-regression"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        publish_dry_run = subprocess.run(
            [
                sys.executable,
                str(live_publisher),
                "--dry-run",
                "--client-id",
                "verify-client",
                "--trace-json",
                str(trace_fixture),
                "--eval-case-json",
                str(eval_case_fixture),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        publish_payload = json.loads(publish_dry_run.stdout)
        assert publish_payload["status"] == "dry_run"
        assert publish_payload["task_id"] == "<created_task_id>"
        assert publish_payload["expected_trace_artifact"]["kind"] == "agent_trace"
        assert publish_payload["expected_trace_artifact"]["path"] == str(trace_fixture)
        assert publish_payload["expected_trace_artifact"]["digest"].startswith("sha256:")
        assert publish_payload["revalidation_ready"] is False
        assert "task_id" in publish_payload["revalidation_missing_fields"]
        assert "--readback-only" in publish_payload["revalidation_cli_args"]
        assert "--compact-output" in publish_payload["revalidation_cli_args"]
        publish_paths = [item["path"] for item in publish_payload["actions"]]
        assert publish_paths[0] == "/tasks?background=false&start=false"
        assert "/tasks/<created_task_id>/artifacts" in publish_paths
        assert "/tasks/<created_task_id>/tool-calls" in publish_paths
        assert "/tasks/<created_task_id>/observations" in publish_paths
        assert publish_paths[-1] == "/eval-cases"
        published_eval = publish_payload["actions"][-1]["body"]
        assert published_eval["source_task_id"] == "<created_task_id>"
        assert "task:<created_task_id>" in published_eval["evidence_refs"]
        assert any(item.startswith("artifact:agent_trace:") for item in published_eval["evidence_refs"])

        live_publisher_module = _load_module("live_publisher_readback_verify", live_publisher)
        live_publisher_trace_digest = live_publisher_module.trace_ingest.file_digest(trace_fixture)
        request_calls: list[tuple[str, str, Any]] = []
        recorded_publish_observations: list[dict[str, Any]] = []
        original_request = live_publisher_module.harness_api.request

        def projected_publish_eval_verifications() -> list[dict[str, Any]]:
            projected = []
            for observation in recorded_publish_observations:
                result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
                execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
                projected.append(
                    {
                        "type": "agent_eval_case_verification",
                        "subtype": result.get("failure_label"),
                        "task_id": "task_readback",
                        "observation_id": observation.get("observation_id"),
                        "eval_case_id": result.get("eval_case_id"),
                        "name": result.get("name"),
                        "source_task_id": result.get("source_task_id"),
                        "failure_label": result.get("failure_label"),
                        "expected_status": result.get("expected_status"),
                        "expected_status_matches": result.get("expected_status_matches"),
                        "execution_status": execution.get("status"),
                        "returncode": execution.get("returncode"),
                        "status": observation.get("status"),
                        "evidence_refs": observation.get("evidence_refs") or [],
                    }
                )
            return projected

        def fake_publish_request(args: Any, method: str, path: str, body: Any = None) -> dict[str, Any]:
            request_calls.append((method, path, body))
            if method == "POST" and path == "/tasks?background=false&start=false":
                return {
                    "task_id": "task_readback",
                    "status": "paused",
                    "state_version": 1,
                    "client_id": args.client_id,
                }
            if method == "POST" and path == "/eval-cases":
                return {"status": "completed", "eval_case_id": 77}
            if method == "POST" and path == "/tasks/task_readback/observations":
                if body.get("tool_name") != "eval_case_verification":
                    return {"status": "completed"}
                assert body["tool_name"] == "eval_case_verification"
                assert body["result"]["source"] == "fitcheck_live_smoke_publish_revalidation"
                assert body["result"]["status"] == "completed"
                assert body["result"]["expected_status_matches"] is True
                assert body["result"]["readback_valid"] is True
                assert body["result"]["event_replay_valid"] is True
                assert body["status"] == "completed"
                recorded = {
                    **body,
                    "observation_id": 8801,
                    "created_at": "2026-06-30T00:00:00+00:00",
                }
                recorded_publish_observations.append(recorded)
                return recorded
            if method == "POST":
                return {"status": "completed"}
            if method == "GET" and path == "/tasks/task_readback":
                return {
                    "task_id": "task_readback",
                    "status": "paused",
                    "state_version": 2,
                    "client_id": args.client_id,
                }
            if method == "GET" and path == "/tasks/task_readback/evidence":
                return {
                    "tool_calls": [{"call_id": f"call_{index}"} for index in range(8)],
                    "observations": [{"observation_id": index} for index in range(30)]
                    + recorded_publish_observations,
                    "artifacts": [
                        {
                            "artifact_id": 1,
                            "kind": "agent_trace",
                            "path": str(trace_fixture),
                            "digest": live_publisher_trace_digest,
                            "status": "completed",
                            "metadata": {"source": "trace_ingest"},
                        },
                    ]
                    + [
                        {
                            "artifact_id": index,
                            "kind": "supporting_trace_artifact",
                            "path": str(trace_fixture),
                            "digest": "",
                            "status": "completed",
                            "metadata": {},
                        }
                        for index in range(2, 9)
                    ],
                    "verification_results": [],
                }
            if method == "GET" and path == "/tasks/task_readback/sdk-events":
                eval_verification_events = projected_publish_eval_verifications()
                return {
                    "status": "completed",
                    "events": (
                        [{"type": "tool_progress"} for _ in range(8)]
                        + [{"type": "agent_model_response"} for _ in range(2)]
                        + [{"type": "agent_phase"} for _ in range(2)]
                        + [{"type": "agent_session"}]
                        + [{"type": "agent_handoff"} for _ in range(2)]
                        + [{"type": "agent_approval"} for _ in range(2)]
                        + [{"type": "agent_question"}]
                        + [
                            {
                                "type": "agent_guidance",
                                "reason": "continue_search_for_closer_measurements",
                                "next_tool_suggestions": [{"tool": "search_web_candidates"}],
                            }
                        ]
                        + [{"type": "agent_guardrail"} for _ in range(2)]
                        + [
                            {
                                "type": "agent_stream_event",
                                "stream_event_type": "raw_response_event",
                                "stream_event_name": None,
                                "raw_event_type": "response.output_text.delta",
                                "item_class": None,
                                "tool_name": None,
                            },
                            {
                                "type": "agent_stream_event",
                                "stream_event_type": "run_item_stream_event",
                                "stream_event_name": "tool_called",
                                "raw_event_type": None,
                                "item_class": "ToolCallItem",
                                "tool_name": "record_signal",
                            },
                            {
                                "type": "agent_stream_event",
                                "stream_event_type": "run_item_stream_event",
                                "stream_event_name": "handoff_requested",
                                "raw_event_type": None,
                                "item_class": "HandoffCallItem",
                                "tool_name": "transfer_to_harness_specialist",
                            },
                            {
                                "type": "agent_stream_event",
                                "stream_event_type": "run_item_stream_event",
                                "stream_event_name": "handoff_occured",
                                "raw_event_type": None,
                                "item_class": "HandoffOutputItem",
                                "tool_name": "transfer_to_harness_specialist",
                            },
                            {
                                "type": "agent_stream_event",
                                "stream_event_type": "agent_updated_stream_event",
                                "stream_event_name": None,
                                "raw_event_type": None,
                                "item_class": None,
                                "tool_name": None,
                            },
                        ]
                        + [{"type": "final_output"}]
                        + eval_verification_events
                    ),
                    "projected_event_count": 27 + len(eval_verification_events),
                    "raw_event_count": 30 + len(eval_verification_events),
                    "cursor_policy": {"cursor_basis": "raw_harness_event_id"},
                    "final_output_included": True,
                }
            if method == "GET" and path == "/tasks/task_readback/sdk-events?summary=true":
                eval_verification_events = projected_publish_eval_verifications()
                return {
                    "status": "completed",
                    "source": "harness_sdk_events_summary",
                    "summary_requested": True,
                    "task_id": "task_readback",
                    "projected_event_count": 27 + len(eval_verification_events),
                    "raw_event_count": 30 + len(eval_verification_events),
                    "event_type_counts": {
                        "tool_progress": 8,
                        "agent_model_response": 2,
                        "agent_phase": 2,
                        "agent_session": 1,
                        "agent_handoff": 2,
                        "agent_approval": 2,
                        "agent_question": 1,
                        "agent_guidance": 1,
                        "agent_guardrail": 2,
                        "agent_stream_event": 5,
                        "final_output": 1,
                        "agent_eval_case_verification": len(eval_verification_events),
                    },
                    "agent_model_response_count": 2,
                    "agent_model_response_provider_counts": {"openai": 2},
                    "agent_model_response_api_style_counts": {"responses": 2},
                    "agent_stream_event_count": 5,
                    "agent_stream_event_type_counts": {
                        "raw_response_event": 1,
                        "run_item_stream_event": 3,
                        "agent_updated_stream_event": 1,
                    },
                    "agent_stream_event_name_counts": {
                        "none": 2,
                        "tool_called": 1,
                        "handoff_requested": 1,
                        "handoff_occured": 1,
                    },
                    "agent_stream_raw_event_type_counts": {
                        "response.output_text.delta": 1,
                        "none": 4,
                    },
                    "agent_stream_item_class_counts": {
                        "none": 2,
                        "ToolCallItem": 1,
                        "HandoffCallItem": 1,
                        "HandoffOutputItem": 1,
                    },
                    "agent_stream_tool_name_counts": {
                        "none": 2,
                        "record_signal": 1,
                        "transfer_to_harness_specialist": 2,
                    },
                    "agent_phase_count": 2,
                    "agent_session_count": 1,
                    "agent_handoff_count": 2,
                    "agent_approval_count": 2,
                    "agent_question_count": 1,
                    "agent_guidance_count": 1,
                    "agent_guidance_reasons": ["continue_search_for_closer_measurements"],
                    "agent_guidance_reason_counts": {"continue_search_for_closer_measurements": 1},
                    "agent_guidance_suggested_tool_count": 1,
                    "agent_guardrail_count": 2,
                    "agent_run_error_count": 0,
                    "agent_eval_case_verification_count": len(eval_verification_events),
                    "agent_eval_case_verification_failed_count": 0,
                    "agent_eval_case_failure_label_counts": {
                        "fitcheck_live_smoke_publish_revalidation": len(eval_verification_events)
                    }
                    if eval_verification_events
                    else {},
                    "missing_info_question_count": 1,
                    "final_output_count": 1,
                    "final_output_included": True,
                    "claim_boundary": "Summary of stored SDK-event projection only; not proof of live provider connectivity.",
                }
            if method == "GET" and path == "/tasks/task_readback/client-transcript":
                eval_verification_events = projected_publish_eval_verifications()
                return {
                    "status": "completed",
                    "messages": [{"type": "task_notification"}],
                    "tool_progress": [{"tool_name": "search_web_candidates"} for _ in range(8)],
                    "agent_model_responses": [{"model": "gpt-test"} for _ in range(2)],
                    "agent_phases": [{"name": "initial"}, {"name": "after_approval"}],
                    "agent_sessions": [{"agent_session_id": "trace_verify_session"}],
                    "agent_handoffs": [{"tool_use_id": "call_handoff"} for _ in range(2)],
                    "agent_approvals": [{"tool_use_id": "call_approval"} for _ in range(2)],
                    "agent_questions": [{"questions": [{"field": "baseline_evidence"}]}],
                    "agent_guidance": [
                        {
                            "reason": "continue_search_for_closer_measurements",
                            "next_tool_suggestions": [{"tool": "search_web_candidates"}],
                        }
                    ],
                    "agent_guardrails": [{"guardrail_name": "verify_guardrail"} for _ in range(2)],
                    "agent_eval_case_verifications": eval_verification_events,
                    "agent_run_errors": [],
                    "agent_stream_events": [
                        {
                            "type": "agent_stream_event",
                            "stream_index": 1,
                            "stream_event_type": "raw_response_event",
                            "stream_event_name": None,
                            "raw_event_type": "response.output_text.delta",
                            "item_class": None,
                            "tool_name": None,
                        },
                        {
                            "type": "agent_stream_event",
                            "stream_index": 2,
                            "stream_event_type": "run_item_stream_event",
                            "stream_event_name": "tool_called",
                            "raw_event_type": None,
                            "item_class": "ToolCallItem",
                            "tool_name": "record_signal",
                        },
                        {
                            "type": "agent_stream_event",
                            "stream_index": 3,
                            "stream_event_type": "run_item_stream_event",
                            "stream_event_name": "handoff_requested",
                            "raw_event_type": None,
                            "item_class": "HandoffCallItem",
                            "tool_name": "transfer_to_harness_specialist",
                        },
                        {
                            "type": "agent_stream_event",
                            "stream_index": 4,
                            "stream_event_type": "run_item_stream_event",
                            "stream_event_name": "handoff_occured",
                            "raw_event_type": None,
                            "item_class": "HandoffOutputItem",
                            "tool_name": "transfer_to_harness_specialist",
                        },
                        {
                            "type": "agent_stream_event",
                            "stream_index": 5,
                            "stream_event_type": "agent_updated_stream_event",
                            "stream_event_name": None,
                            "raw_event_type": None,
                            "item_class": None,
                            "tool_name": None,
                        },
                    ],
                    "source_endpoints": {
                        "evidence": "/api/harness/v1/tasks/task_readback/evidence",
                        "sdk_events": "/api/harness/v1/tasks/task_readback/sdk-events",
                    },
                }
            if method == "GET" and path == "/tasks/task_readback/client-transcript?summary=true":
                eval_verification_events = projected_publish_eval_verifications()
                return {
                    "status": "completed",
                    "source": "harness_client_transcript_summary",
                    "summary_requested": True,
                    "task_id": "task_readback",
                    "message_count": 1,
                    "item_type_counts": {
                        "task_notification": 1,
                        "tool_progress": 8,
                        "agent_model_response": 2,
                        "agent_phase": 2,
                        "agent_session": 1,
                        "agent_handoff": 2,
                        "agent_approval": 2,
                        "agent_question": 1,
                        "agent_guidance": 1,
                        "agent_guardrail": 2,
                        "agent_eval_case_verification": len(eval_verification_events),
                        "agent_run_error": 0,
                        "agent_stream_event": 5,
                    },
                    "tool_progress_count": 8,
                    "agent_model_response_count": 2,
                    "agent_model_response_provider_counts": {"openai": 2},
                    "agent_model_response_api_style_counts": {"responses": 2},
                    "agent_stream_event_count": 5,
                    "agent_stream_event_type_counts": {
                        "raw_response_event": 1,
                        "run_item_stream_event": 3,
                        "agent_updated_stream_event": 1,
                    },
                    "agent_stream_event_name_counts": {
                        "none": 2,
                        "tool_called": 1,
                        "handoff_requested": 1,
                        "handoff_occured": 1,
                    },
                    "agent_stream_raw_event_type_counts": {
                        "response.output_text.delta": 1,
                        "none": 4,
                    },
                    "agent_stream_item_class_counts": {
                        "none": 2,
                        "ToolCallItem": 1,
                        "HandoffCallItem": 1,
                        "HandoffOutputItem": 1,
                    },
                    "agent_stream_tool_name_counts": {
                        "none": 2,
                        "record_signal": 1,
                        "transfer_to_harness_specialist": 2,
                    },
                    "agent_phase_count": 2,
                    "agent_session_count": 1,
                    "agent_handoff_count": 2,
                    "agent_approval_count": 2,
                    "agent_question_count": 1,
                    "missing_info_question_count": 1,
                    "agent_guidance_count": 1,
                    "agent_guidance_reasons": ["continue_search_for_closer_measurements"],
                    "agent_guidance_reason_counts": {"continue_search_for_closer_measurements": 1},
                    "agent_guidance_suggested_tool_count": 1,
                    "agent_guardrail_count": 2,
                    "agent_eval_case_verification_count": len(eval_verification_events),
                    "agent_eval_case_verification_failed_count": 0,
                    "agent_eval_case_failure_label_counts": {
                        "fitcheck_live_smoke_publish_revalidation": len(eval_verification_events)
                    }
                    if eval_verification_events
                    else {},
                    "agent_run_error_count": 0,
                    "source_endpoints_present": ["evidence", "sdk_events"],
                    "claim_boundary": "Summary of stored client transcript projection only; not proof of live provider connectivity.",
                }
            if method == "SSE" and path == "/tasks/task_readback/events?after=0":
                return {
                    "status": "completed",
                    "source": "harness_event_sse_replay",
                    "task_id": "task_readback",
                    "after": 0,
                    "next_after": 46,
                    "event_count": 46,
                    "timed_out": False,
                    "sse_event_name_counts": {"harness.event": 46},
                    "event_type_counts": {
                        "artifact.recorded": 8,
                        "tool_call.recorded": 8,
                        "observation.recorded": 30,
                    },
                    "event_status_counts": {"completed": 46},
                    "event_actor_counts": {"harness": 46},
                }
            if method == "GET" and path == "/eval-cases":
                return {
                    "status": "completed",
                    "eval_cases": [
                        {
                            "eval_case_id": 77,
                            "name": "fitcheck live agent SDK smoke regression: waiting_for_user",
                            "status": "active",
                            "source_task_id": "task_readback",
                        }
                    ],
                }
            raise AssertionError(f"unexpected publisher request: {method} {path}")

        try:
            live_publisher_module.harness_api.request = fake_publish_request
            published_readback = live_publisher_module.publish_export(
                trace_json=trace_fixture,
                eval_case_json=eval_case_fixture,
                client_id="verify-client",
                base_url="http://127.0.0.1:8000/api/harness/v1",
                cwd=str(root),
            )
        finally:
            live_publisher_module.harness_api.request = original_request
        assert published_readback["status"] == "completed"
        assert published_readback["task_id"] == "task_readback"
        assert published_readback["expected_trace_artifact"]["digest"] == live_publisher_trace_digest
        assert published_readback["readback"]["expected_trace_artifact"]["digest"] == live_publisher_trace_digest
        assert published_readback["readback"]["artifact_summaries"][0]["kind"] == "agent_trace"
        assert published_readback["readback"]["artifact_summaries"][0]["digest"] == live_publisher_trace_digest
        assert published_readback["readback"]["validation"]["valid"] is True
        assert published_readback["readback"]["validation"]["failed_checks"] == []
        assert published_readback["readback"]["validation"]["check_count"] == len(
            published_readback["readback"]["validation"]["checks"]
        )
        assert published_readback["readback"]["expected_model_response_projection_maps"] == {
            "agent_model_response_provider_counts": {"openai": 2},
            "agent_model_response_api_style_counts": {"responses": 2},
        }
        assert published_readback["readback"]["validation"]["checks"][
            "sdk_summary_model_response_provider_counts_match"
        ] is True
        assert published_readback["readback"]["validation"]["checks"][
            "transcript_summary_model_response_api_style_counts_match"
        ] is True
        assert published_readback["readback"]["validation"]["checks"][
            "agent_trace_artifact_digest_matches_expected"
        ] is True
        assert published_readback["readback"]["validation"]["trace_artifact_binding"][
            "matched_artifact_count"
        ] == 1
        assert published_readback["readback"]["eval_case"]["found"] is True
        assert published_readback["readback"]["sdk_events"]["projected_event_count"] == 27
        assert published_readback["readback"]["sdk_events"]["event_type_counts"]["agent_stream_event"] == 5
        assert published_readback["readback"]["sdk_events"]["event_type_counts"]["agent_guidance"] == 1
        assert published_readback["readback"]["sdk_events"]["event_type_counts"]["agent_phase"] == 2
        assert published_readback["readback"]["sdk_events"]["event_type_counts"]["agent_handoff"] == 2
        assert published_readback["readback"]["sdk_events"]["event_type_counts"]["agent_approval"] == 2
        assert published_readback["readback"]["sdk_events"]["event_type_counts"]["agent_guardrail"] == 2
        assert published_readback["readback"]["sdk_events"]["stream_event_counts"]["event_type"] == {
            "raw_response_event": 1,
            "run_item_stream_event": 3,
            "agent_updated_stream_event": 1,
        }
        assert published_readback["readback"]["sdk_events"]["stream_event_counts"]["item_class"] == {
            "none": 2,
            "ToolCallItem": 1,
            "HandoffCallItem": 1,
            "HandoffOutputItem": 1,
        }
        assert published_readback["readback"]["sdk_events"]["summary"]["summary_requested"] is True
        assert published_readback["readback"]["sdk_events"]["summary"]["source"] == "harness_sdk_events_summary"
        assert published_readback["readback"]["sdk_events"]["summary"]["agent_approval_count"] == 2
        assert published_readback["readback"]["sdk_events"]["summary"][
            "agent_model_response_provider_counts"
        ] == {"openai": 2}
        assert published_readback["readback"]["sdk_events"]["summary"][
            "agent_model_response_api_style_counts"
        ] == {"responses": 2}
        assert published_readback["readback"]["sdk_events"]["summary"]["agent_phase_count"] == 2
        assert published_readback["readback"]["sdk_events"]["summary"]["agent_guardrail_count"] == 2
        assert published_readback["readback"]["sdk_events"]["summary"]["agent_stream_event_name_counts"] == {
            "none": 2,
            "tool_called": 1,
            "handoff_requested": 1,
            "handoff_occured": 1,
        }
        assert published_readback["readback"]["sdk_events"]["summary"]["agent_guidance_reason_counts"] == {
            "continue_search_for_closer_measurements": 1
        }
        assert published_readback["readback"]["client_transcript"]["tool_progress_count"] == 8
        assert published_readback["readback"]["client_transcript"]["agent_phase_count"] == 2
        assert published_readback["readback"]["client_transcript"]["agent_session_count"] == 1
        assert published_readback["readback"]["client_transcript"]["agent_handoff_count"] == 2
        assert published_readback["readback"]["client_transcript"]["agent_approval_count"] == 2
        assert published_readback["readback"]["client_transcript"]["agent_question_count"] == 1
        assert published_readback["readback"]["client_transcript"]["agent_guidance_count"] == 1
        assert published_readback["readback"]["client_transcript"]["agent_guardrail_count"] == 2
        assert published_readback["readback"]["client_transcript"]["agent_stream_event_count"] == 5
        assert published_readback["readback"]["client_transcript"]["stream_event_counts"]["tool_name"] == {
            "none": 2,
            "record_signal": 1,
            "transfer_to_harness_specialist": 2,
        }
        assert published_readback["readback"]["client_transcript"]["item_type_counts"]["agent_approval"] == 2
        assert published_readback["readback"]["client_transcript"]["item_type_counts"]["agent_guidance"] == 1
        assert published_readback["readback"]["client_transcript"]["summary"]["summary_requested"] is True
        assert published_readback["readback"]["client_transcript"]["summary"]["source"] == "harness_client_transcript_summary"
        assert published_readback["readback"]["client_transcript"]["summary"]["agent_approval_count"] == 2
        assert published_readback["readback"]["client_transcript"]["summary"][
            "agent_model_response_provider_counts"
        ] == {"openai": 2}
        assert published_readback["readback"]["client_transcript"]["summary"][
            "agent_model_response_api_style_counts"
        ] == {"responses": 2}
        assert published_readback["readback"]["client_transcript"]["summary"]["agent_phase_count"] == 2
        assert published_readback["readback"]["client_transcript"]["summary"]["agent_guardrail_count"] == 2
        assert published_readback["readback"]["client_transcript"]["summary"]["agent_stream_item_class_counts"] == {
            "none": 2,
            "ToolCallItem": 1,
            "HandoffCallItem": 1,
            "HandoffOutputItem": 1,
        }
        assert published_readback["readback"]["client_transcript"]["summary"]["agent_guidance_reason_counts"] == {
            "continue_search_for_closer_measurements": 1
        }
        event_replay = published_readback["readback"]["event_replay"]
        assert event_replay["validation"]["valid"] is True
        assert event_replay["validation"]["failed_checks"] == []
        assert event_replay["summary"]["event_count"] == 46
        assert event_replay["expected_event_counts"] == {
            "artifact.recorded": 8,
            "observation.recorded": 30,
            "tool_call.recorded": 8,
            "tool_call.status_updated": 0,
        }
        assert event_replay["observed_event_counts"]["artifact.recorded"] == 8
        assert event_replay["observed_event_counts"]["observation.recorded"] == 30
        assert event_replay["observed_event_counts"]["tool_call.recorded"] == 8
        assert event_replay["event_replay_count_map_totals"] == {
            "sse_event_name_counts": 46,
            "event_type_counts": 46,
            "event_status_counts": 46,
            "event_actor_counts": 46,
        }
        assert event_replay["event_replay_fetch_plan"]["expected_raw_event_count"] == 30
        assert published_readback["readback"]["validation"]["checks"]["event_replay_valid"] is True
        assert published_readback["readback"]["validation"]["checks"]["event_replay_task_id_matches"] is True
        assert published_readback["readback"]["validation"]["checks"][
            "event_replay_covers_observation_recorded"
        ] is True
        readback_paths = [path for _, path, _ in request_calls]
        assert "/tasks/task_readback/evidence" in readback_paths
        assert "/tasks/task_readback/sdk-events" in readback_paths
        assert "/tasks/task_readback/sdk-events?summary=true" in readback_paths
        assert "/tasks/task_readback/client-transcript" in readback_paths
        assert "/tasks/task_readback/client-transcript?summary=true" in readback_paths
        assert "/tasks/task_readback/events?after=0" in readback_paths
        assert "/eval-cases" in readback_paths
        request_calls.clear()
        try:
            live_publisher_module.harness_api.request = fake_publish_request
            readback_only_result = live_publisher_module.publish_export(
                trace_json=trace_fixture,
                eval_case_json=eval_case_fixture,
                task_id="task_readback",
                client_id="verify-client",
                base_url="http://127.0.0.1:8000/api/harness/v1",
                cwd=str(root),
                readback_only=True,
            )
        finally:
            live_publisher_module.harness_api.request = original_request
        assert readback_only_result["status"] == "completed"
        assert readback_only_result["mode"] == "readback_only"
        assert readback_only_result["task_id"] == "task_readback"
        assert readback_only_result["readback"]["validation"]["valid"] is True
        assert readback_only_result["readback"]["event_replay"]["validation"]["valid"] is True
        assert readback_only_result["readback"]["eval_case"]["eval_case_id"] == 77
        assert readback_only_result["revalidation_ready"] is True
        assert readback_only_result["revalidation_missing_fields"] == []
        assert readback_only_result["revalidation_inputs"]["task_id"] == "task_readback"
        assert readback_only_result["revalidation_inputs"]["trace_json"] == str(trace_fixture)
        assert readback_only_result["revalidation_inputs"]["eval_case_json"] == str(eval_case_fixture)
        assert "--readback-only" in readback_only_result["revalidation_cli_args"]
        assert "--task-id" in readback_only_result["revalidation_cli_args"]
        assert "task_readback" in readback_only_result["revalidation_cli_args"]
        assert "--compact-output" in readback_only_result["revalidation_cli_args"]
        assert not any(method == "POST" for method, _, _ in request_calls)
        assert "/tasks/task_readback/events?after=0" in [path for _, path, _ in request_calls]
        compact_readback_only = live_publisher_module._compact_publish_result(readback_only_result)
        assert compact_readback_only["mode"] == "readback_only"
        assert compact_readback_only["readback_valid"] is True
        assert compact_readback_only["event_replay_valid"] is True
        assert compact_readback_only["revalidation_ready"] is True
        assert compact_readback_only["revalidation_missing_fields"] == []
        assert compact_readback_only["revalidation_cli_args"] == readback_only_result["revalidation_cli_args"]
        assert compact_readback_only["event_replay_expected_event_counts"] == {
            "artifact.recorded": 8,
            "observation.recorded": 30,
            "tool_call.recorded": 8,
            "tool_call.status_updated": 0,
        }
        compact_output_payload = live_publisher_module._output_json_payload(
            readback_only_result,
            compact_output=True,
        )
        assert compact_output_payload["readback"]["validation"]["valid"] is True
        assert compact_output_payload["readback_valid"] is True
        assert compact_output_payload["event_replay_valid"] is True
        assert compact_output_payload["compact_summary"]["readback_valid"] is True
        assert compact_output_payload["publish_summary"]["event_replay_valid"] is True
        assert compact_output_payload["publish_summary"]["revalidation_inputs"]["task_id"] == "task_readback"
        assert (
            live_publisher_module._output_json_payload(
                readback_only_result,
                compact_output=False,
            )
            is readback_only_result
        )
        request_calls.clear()
        try:
            live_publisher_module.harness_api.request = fake_publish_request
            recorded_revalidation = live_publisher_module._record_publish_revalidation_result(
                argparse.Namespace(
                    base_url="http://127.0.0.1:8000/api/harness/v1",
                    client_id="verify-client",
                    api_key=None,
                    dry_run=False,
                ),
                record_task_id="task_readback",
                result=readback_only_result,
            )
        finally:
            live_publisher_module.harness_api.request = original_request
        assert recorded_revalidation["status"] == "completed"
        assert recorded_revalidation["task_id"] == "task_readback"
        assert recorded_revalidation["observation_id"] == 8801
        assert recorded_revalidation["tool_name"] == "eval_case_verification"
        assert any(method == "POST" and path == "/tasks/task_readback/observations" for method, path, _ in request_calls)
        request_calls.clear()
        try:
            live_publisher_module.harness_api.request = fake_publish_request
            recorded_revalidation_readback = live_publisher_module._readback_recorded_revalidation_result(
                argparse.Namespace(
                    base_url="http://127.0.0.1:8000/api/harness/v1",
                    client_id="verify-client",
                    api_key=None,
                    dry_run=False,
                ),
                record_task_id="task_readback",
                record_result=recorded_revalidation,
            )
        finally:
            live_publisher_module.harness_api.request = original_request
        assert recorded_revalidation_readback["status"] == "completed"
        assert recorded_revalidation_readback["validation"]["valid"] is True
        assert recorded_revalidation_readback["evidence_observation_match_count"] == 1
        assert recorded_revalidation_readback["sdk_event_match_count"] == 1
        assert recorded_revalidation_readback["transcript_match_count"] == 1
        assert recorded_revalidation_readback["sdk_summary"]["agent_eval_case_verification_count"] == 1
        assert (
            recorded_revalidation_readback["client_transcript_summary"][
                "agent_eval_case_failure_label_counts"
            ]["fitcheck_live_smoke_publish_revalidation"]
            == 1
        )
        record_result_gate = live_publisher_module._record_result_gate(
            {
                **compact_readback_only,
                "record_result": recorded_revalidation,
                "record_result_readback": recorded_revalidation_readback,
            }
        )
        assert record_result_gate["status"] == "completed"
        assert record_result_gate["valid"] is True
        broken_record_result_readback = {
            **recorded_revalidation_readback,
            "status": "failed",
            "validation": {
                "valid": False,
                "failed_checks": ["sdk_event_projection_present"],
                "checks": {"sdk_event_projection_present": False},
            },
        }
        failed_record_result_gate = live_publisher_module._record_result_gate(
            {
                **compact_readback_only,
                "record_result": recorded_revalidation,
                "record_result_readback": broken_record_result_readback,
            }
        )
        assert failed_record_result_gate["status"] == "failed"
        assert failed_record_result_gate["valid"] is False
        assert "readback:sdk_event_projection_present" in failed_record_result_gate["failed_checks"]
        compact_with_record = dict(compact_readback_only)
        compact_with_record["record_result"] = recorded_revalidation
        compact_with_record["record_result_readback"] = recorded_revalidation_readback
        compact_with_record["record_result_gate"] = record_result_gate
        compact_with_record = live_publisher_module._compact_publish_result(compact_with_record)
        assert compact_with_record["record_result"]["status"] == "completed"
        assert compact_with_record["record_result_readback_valid"] is True
        assert compact_with_record["record_result_readback_failed_checks"] == []
        assert compact_with_record["record_result_readback_sdk_event_match_count"] == 1
        assert compact_with_record["record_result_readback_transcript_match_count"] == 1
        assert compact_with_record["record_result_gate_valid"] is True
        failed_cli_payload = {
            **compact_readback_only,
            "record_result": recorded_revalidation,
            "record_result_readback": broken_record_result_readback,
            "record_result_gate": failed_record_result_gate,
        }
        failed_cli_payload["publish_status"] = failed_cli_payload["status"]
        failed_cli_payload["status"] = "failed"
        compact_failed_record = live_publisher_module._compact_publish_result(failed_cli_payload)
        assert compact_failed_record["status"] == "failed"
        assert compact_failed_record["publish_status"] == "completed"
        assert compact_failed_record["record_result_gate_valid"] is False
        assert "readback:sdk_event_projection_present" in compact_failed_record["record_result_gate_failed_checks"]
        publisher_summary_path = Path(temp) / "publisher-readback-summary.json"
        publisher_summary_path.write_text(
            json.dumps(compact_readback_only, ensure_ascii=False),
            encoding="utf-8",
        )
        summary_revalidation_run = subprocess.run(
            [
                sys.executable,
                str(live_publisher),
                "--revalidate-from-summary-json",
                str(publisher_summary_path),
                "--dry-run",
                "--compact-output",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        summary_revalidation = json.loads(summary_revalidation_run.stdout)
        assert summary_revalidation["status"] == "dry_run"
        assert summary_revalidation["mode"] == "readback_only"
        assert summary_revalidation["task_id"] == "task_readback"
        assert summary_revalidation["trace_json"] == str(trace_fixture)
        assert summary_revalidation["eval_case_json"] == str(eval_case_fixture)
        assert summary_revalidation["revalidation_ready"] is True
        assert summary_revalidation["summary_revalidation_ready"] is True
        assert summary_revalidation["summary_revalidation_missing_fields"] == []
        assert summary_revalidation["summary_revalidation_inputs"][
            "revalidate_from_summary_json"
        ] == str(publisher_summary_path)
        assert "--revalidate-from-summary-json" in summary_revalidation["summary_revalidation_cli_args"]
        assert str(publisher_summary_path) in summary_revalidation["summary_revalidation_cli_args"]
        broken_publisher_trace_artifact = dict(published_readback["expected_trace_artifact"])
        broken_publisher_trace_artifact["digest"] = "sha256:wrong-publisher-trace-digest"
        try:
            live_publisher_module.harness_api.request = fake_publish_request
            try:
                live_publisher_module._readback_published_harness_state(
                    argparse.Namespace(
                        base_url="http://127.0.0.1:8000/api/harness/v1",
                        client_id="verify-client",
                        api_key=None,
                        dry_run=False,
                    ),
                    task_id="task_readback",
                    eval_case_body=None,
                    expected_counts=published_readback["readback"]["expected_min_evidence_counts"],
                    expected_projection_counts=published_readback["readback"]["expected_min_projection_counts"],
                    expected_model_response_projection_maps=published_readback["readback"][
                        "expected_model_response_projection_maps"
                    ],
                    expected_trace_artifact=broken_publisher_trace_artifact,
                )
                raise AssertionError("publisher readback accepted a mismatched trace artifact digest")
            except SystemExit as exc:
                assert "agent_trace_artifact_digest_matches_expected" in str(exc)
        finally:
            live_publisher_module.harness_api.request = original_request

        def fake_broken_event_replay_request(args: Any, method: str, path: str, body: Any = None) -> dict[str, Any]:
            if method == "SSE" and path == "/tasks/task_readback/events?after=0":
                return {
                    "status": "completed",
                    "source": "harness_event_sse_replay",
                    "task_id": "task_readback",
                    "after": 0,
                    "next_after": 16,
                    "event_count": 16,
                    "timed_out": False,
                    "sse_event_name_counts": {"harness.event": 16},
                    "event_type_counts": {
                        "artifact.recorded": 8,
                        "tool_call.recorded": 8,
                    },
                    "event_status_counts": {"completed": 16},
                    "event_actor_counts": {"harness": 16},
                }
            return fake_publish_request(args, method, path, body)

        try:
            live_publisher_module.harness_api.request = fake_broken_event_replay_request
            try:
                live_publisher_module._readback_published_harness_state(
                    argparse.Namespace(
                        base_url="http://127.0.0.1:8000/api/harness/v1",
                        client_id="verify-client",
                        api_key=None,
                        dry_run=False,
                    ),
                    task_id="task_readback",
                    eval_case_body=None,
                    expected_counts=published_readback["readback"]["expected_min_evidence_counts"],
                    expected_projection_counts=published_readback["readback"]["expected_min_projection_counts"],
                    expected_model_response_projection_maps=published_readback["readback"][
                        "expected_model_response_projection_maps"
                    ],
                    expected_trace_artifact=published_readback["expected_trace_artifact"],
                )
                raise AssertionError("publisher readback accepted missing observation replay coverage")
            except SystemExit as exc:
                assert "event_replay_covers_observation_recorded" in str(exc)
        finally:
            live_publisher_module.harness_api.request = original_request

        ingest_run = subprocess.run(
            [
                sys.executable,
                str(trace_ingest),
                "--dry-run",
                "--client-id",
                "verify-client",
                "--task-id",
                "task_verify",
                "--trace-json",
                str(trace_fixture),
                "--readback",
                "--fail-on-readback-invalid",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        ingest_payload = json.loads(ingest_run.stdout)
        ingest_summary = ingest_payload["summary"]
        assert ingest_summary["source"] == "trace_ingest_action_summary"
        assert ingest_summary["action_count"] == len(ingest_payload["actions"])
        assert ingest_summary["method_counts"]["POST"] == ingest_summary["action_count"]
        assert ingest_summary["artifact_count"] >= 1
        assert ingest_summary["tool_call_count"] >= 1
        assert ingest_summary["observation_count"] >= 1
        assert ingest_summary["artifact_kind_counts"]["agent_trace"] == 1
        assert ingest_summary["observation_tool_counts"]["agent_model_response"] == 2
        assert ingest_summary["observation_tool_counts"]["agent_guidance"] == 1
        assert ingest_summary["observation_tool_counts"]["agent_question"] == 1
        assert ingest_summary["tool_call_name_counts"]["record_signal"] >= 1
        assert "call_approval" in ingest_summary["call_ids"]
        assert "authoritative stored evidence" in ingest_summary["claim_boundary"]
        assert [item["method"] for item in ingest_payload["readback_actions"]] == ["GET", "GET", "GET", "SSE"]
        assert [item["path"] for item in ingest_payload["readback_actions"]] == [
            "/tasks/task_verify/evidence",
            "/tasks/task_verify/sdk-events?summary=true",
            "/tasks/task_verify/client-transcript?summary=true",
            "/tasks/task_verify/events?after=0",
        ]
        compact_ingest_full_payload_path = Path(temp) / "trace-ingest-compact-full.json"
        compact_ingest_run = subprocess.run(
            [
                sys.executable,
                str(trace_ingest),
                "--dry-run",
                "--compact-output",
                "--output-json",
                str(compact_ingest_full_payload_path),
                "--client-id",
                "verify-client",
                "--task-id",
                "task_verify",
                "--trace-json",
                str(trace_fixture),
                "--readback",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        compact_ingest_payload = json.loads(compact_ingest_run.stdout)
        assert compact_ingest_payload["source"] == "trace_ingest_compact_summary"
        assert compact_ingest_payload["action_count"] == ingest_summary["action_count"]
        assert compact_ingest_payload["observation_tool_counts"]["agent_model_response"] == 2
        assert compact_ingest_payload["readback_action_count"] == 4
        assert "actions" not in compact_ingest_payload
        assert "results" not in compact_ingest_payload
        compact_ingest_full_payload = json.loads(compact_ingest_full_payload_path.read_text(encoding="utf-8"))
        assert compact_ingest_full_payload["summary"]["source"] == "trace_ingest_action_summary"
        assert len(compact_ingest_full_payload["actions"]) == compact_ingest_payload["action_count"]
        trace_ingest_module = _load_module("trace_ingest_verify", trace_ingest)
        expected_result_summary = trace_ingest_module.expected_result_summary_from_actions(
            ingest_payload["actions"]
        )
        assert expected_result_summary["source"] == "trace_ingest_expected_result_summary"
        assert expected_result_summary["result_count"] == ingest_summary["action_count"]
        assert expected_result_summary["artifact_count"] == ingest_summary["artifact_count"]
        assert expected_result_summary["tool_call_count"] == ingest_summary["tool_call_count"]
        assert expected_result_summary["observation_count"] == ingest_summary["observation_count"]
        assert expected_result_summary["tool_call_status_update_expected_count"] == (
            ingest_summary["tool_call_status_update_expected_count"]
        )
        assert expected_result_summary["artifact_ids"] == []
        assert expected_result_summary["observation_ids"] == []
        assert expected_result_summary["tool_call_pks"] == []
        assert "does not prove" in expected_result_summary["claim_boundary"]
        expected_trace_artifact = trace_ingest_module.expected_trace_artifact_from_actions(
            ingest_payload["actions"]
        )
        assert expected_trace_artifact["source"] == "trace_ingest_expected_trace_artifact"
        assert expected_trace_artifact["kind"] == "agent_trace"
        assert expected_trace_artifact["path"] == str(trace_fixture)
        assert expected_trace_artifact["name"] == "agent-result.json"
        assert expected_trace_artifact["digest"].startswith("sha256:")
        assert "Readback validation should match this digest" in expected_trace_artifact["claim_boundary"]
        readback_only_full_payload_path = Path(temp) / "trace-ingest-readback-only-full.json"
        readback_only_dry_run = subprocess.run(
            [
                sys.executable,
                str(trace_ingest),
                "--dry-run",
                "--readback-only",
                "--compact-output",
                "--output-json",
                str(readback_only_full_payload_path),
                "--client-id",
                "verify-client",
                "--task-id",
                "task_verify",
                "--trace-json",
                str(trace_fixture),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        readback_only_compact = json.loads(readback_only_dry_run.stdout)
        assert readback_only_compact["source"] == "trace_ingest_compact_summary"
        assert readback_only_compact["mode"] == "readback_only"
        assert readback_only_compact["action_count"] == ingest_summary["action_count"]
        assert readback_only_compact["expected_result_count"] == expected_result_summary["result_count"]
        assert readback_only_compact["expected_artifact_count"] == expected_result_summary["artifact_count"]
        assert readback_only_compact["expected_tool_call_count"] == expected_result_summary["tool_call_count"]
        assert readback_only_compact["expected_observation_count"] == expected_result_summary["observation_count"]
        assert readback_only_compact["expected_trace_artifact_digest"] == expected_trace_artifact["digest"]
        assert readback_only_compact["expected_trace_artifact_path"] == str(trace_fixture)
        assert readback_only_compact["stored_artifact_count"] == 0
        assert readback_only_compact["stored_tool_call_count"] == 0
        assert readback_only_compact["stored_observation_count"] == 0
        assert readback_only_compact["readback_action_count"] == 4
        assert "actions" not in readback_only_compact
        assert "results" not in readback_only_compact
        readback_only_full_payload = json.loads(readback_only_full_payload_path.read_text(encoding="utf-8"))
        assert readback_only_full_payload["mode"] == "readback_only"
        assert "actions" not in readback_only_full_payload
        assert "results" not in readback_only_full_payload
        assert readback_only_full_payload["expected_result_summary"] == expected_result_summary
        assert readback_only_full_payload["expected_trace_artifact"] == expected_trace_artifact

        def evidence_from_actions(actions: list[dict[str, Any]]) -> dict[str, Any]:
            evidence: dict[str, Any] = {
                "task_id": "task_verify",
                "artifacts": [],
                "observations": [],
                "tool_calls": [],
                "verification_results": [],
            }
            for action_item in actions:
                endpoint = str(action_item.get("path") or "").rsplit("/", 1)[-1]
                body = action_item.get("body") if isinstance(action_item.get("body"), dict) else {}
                if endpoint == "artifacts":
                    evidence["artifacts"].append(
                        {
                            "artifact_id": len(evidence["artifacts"]) + 1,
                            "kind": body.get("kind"),
                            "path": body.get("path") or "",
                            "digest": body.get("digest") or "",
                            "status": body.get("status") or "completed",
                            "metadata": body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
                        }
                    )
                elif endpoint == "observations":
                    evidence["observations"].append(
                        {
                            "observation_id": len(evidence["observations"]) + 1,
                            "tool_name": body.get("tool_name"),
                            "result": body.get("result") if isinstance(body.get("result"), dict) else {},
                            "status": body.get("status") or "completed",
                            "call_id": body.get("call_id") or "",
                            "evidence_refs": body.get("evidence_refs") or [],
                        }
                    )
                elif endpoint == "tool-calls":
                    evidence["tool_calls"].append(
                        {
                            "call_pk": len(evidence["tool_calls"]) + 1,
                            "tool_name": body.get("tool_name"),
                            "call_id": body.get("call_id") or "",
                            "status": body.get("status") or "completed",
                        }
                    )
            return evidence

        def sdk_summary_from_counts(counts: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
            observation_counts = counts["observation_tool_counts"]
            approval_count = int(observation_counts.get("agent_approval_request") or 0) + int(
                observation_counts.get("agent_approval_decision") or 0
            )
            return {
                "status": "completed",
                "source": "harness_sdk_events_summary",
                "summary_requested": True,
                "task_id": "task_verify",
                "raw_event_count": (
                    expected["artifact_count"]
                    + expected["observation_count"]
                    + expected["tool_call_count"]
                    + expected["tool_call_status_update_expected_count"]
                ),
                "agent_model_response_count": int(observation_counts.get("agent_model_response") or 0),
                "agent_model_response_provider_counts": counts["agent_model_response_provider_counts"],
                "agent_model_response_api_style_counts": counts["agent_model_response_api_style_counts"],
                "agent_stream_event_count": int(observation_counts.get("agent_stream_event") or 0),
                "agent_stream_event_type_counts": counts["agent_stream_event_type_counts"],
                "agent_stream_event_name_counts": counts["agent_stream_event_name_counts"],
                "agent_stream_raw_event_type_counts": counts["agent_stream_raw_event_type_counts"],
                "agent_stream_item_class_counts": counts["agent_stream_item_class_counts"],
                "agent_stream_tool_name_counts": counts["agent_stream_tool_name_counts"],
                "agent_phase_count": int(observation_counts.get("agent_run_phase") or 0),
                "agent_session_count": int(observation_counts.get("agent_session") or 0),
                "agent_handoff_count": int(observation_counts.get("agent_handoff") or 0),
                "agent_approval_count": approval_count,
                "agent_question_count": int(observation_counts.get("agent_question") or 0),
                "agent_guidance_count": int(observation_counts.get("agent_guidance") or 0),
                "agent_eval_case_verification_count": int(
                    observation_counts.get("eval_case_verification") or 0
                ),
                "agent_eval_case_verification_failed_count": int(
                    observation_counts.get("eval_case_verification") or 0
                ),
                "agent_eval_case_failure_label_counts": {},
                "agent_guardrail_count": int(observation_counts.get("agent_guardrail_result") or 0),
                "agent_run_error_count": int(observation_counts.get("agent_run_error") or 0),
                "final_output_count": int(observation_counts.get("agent_final_output") or 0),
            }

        def transcript_summary_from_counts(counts: dict[str, Any]) -> dict[str, Any]:
            observation_counts = counts["observation_tool_counts"]
            return {
                "status": "completed",
                "source": "harness_client_transcript_summary",
                "summary_requested": True,
                "task_id": "task_verify",
                "item_type_counts": {
                    "agent_model_response": int(observation_counts.get("agent_model_response") or 0),
                    "agent_stream_event": int(observation_counts.get("agent_stream_event") or 0),
                    "agent_phase": int(observation_counts.get("agent_run_phase") or 0),
                    "agent_session": int(observation_counts.get("agent_session") or 0),
                    "agent_handoff": int(observation_counts.get("agent_handoff") or 0),
                    "agent_approval": int(observation_counts.get("agent_approval_request") or 0)
                    + int(observation_counts.get("agent_approval_decision") or 0),
                    "agent_question": int(observation_counts.get("agent_question") or 0),
                    "agent_guidance": int(observation_counts.get("agent_guidance") or 0),
                    "agent_eval_case_verification": int(
                        observation_counts.get("eval_case_verification") or 0
                    ),
                    "agent_guardrail": int(observation_counts.get("agent_guardrail_result") or 0),
                    "agent_run_error": int(observation_counts.get("agent_run_error") or 0),
                },
                "agent_model_response_provider_counts": counts["agent_model_response_provider_counts"],
                "agent_model_response_api_style_counts": counts["agent_model_response_api_style_counts"],
                "agent_stream_event_count": int(observation_counts.get("agent_stream_event") or 0),
                "agent_stream_event_type_counts": counts["agent_stream_event_type_counts"],
                "agent_stream_event_name_counts": counts["agent_stream_event_name_counts"],
                "agent_stream_raw_event_type_counts": counts["agent_stream_raw_event_type_counts"],
                "agent_stream_item_class_counts": counts["agent_stream_item_class_counts"],
                "agent_stream_tool_name_counts": counts["agent_stream_tool_name_counts"],
                "agent_eval_case_verification_count": int(
                    observation_counts.get("eval_case_verification") or 0
                ),
                "agent_eval_case_verification_failed_count": int(
                    observation_counts.get("eval_case_verification") or 0
                ),
                "agent_eval_case_failure_label_counts": {},
            }

        readback_only_evidence = evidence_from_actions(ingest_payload["actions"])
        readback_only_counts = trace_ingest_module.summarize_evidence(readback_only_evidence)
        readback_only_request_calls: list[tuple[str, str]] = []

        def fake_readback_only_request(
            request_args: argparse.Namespace,
            method: str,
            path: str,
            body: Any = None,
        ) -> dict[str, Any]:
            del request_args, body
            readback_only_request_calls.append((method, path))
            assert method in {"GET", "SSE"}
            if path == "/tasks/task_verify/evidence":
                return readback_only_evidence
            if path == "/tasks/task_verify/sdk-events?summary=true":
                return sdk_summary_from_counts(readback_only_counts, expected_result_summary)
            if path == "/tasks/task_verify/client-transcript?summary=true":
                return transcript_summary_from_counts(readback_only_counts)
            if method == "SSE" and path == "/tasks/task_verify/events?after=0":
                raw_event_count = sdk_summary_from_counts(
                    readback_only_counts,
                    expected_result_summary,
                )["raw_event_count"]
                return {
                    "status": "completed",
                    "source": "harness_event_sse_replay",
                    "task_id": "task_verify",
                    "after": 0,
                    "events": [],
                    "event_count": raw_event_count,
                    "timed_out": False,
                    "next_after": raw_event_count,
                    "sse_event_name_counts": {"harness_event": raw_event_count},
                    "event_type_counts": {
                        "artifact.recorded": expected_result_summary["artifact_count"],
                        "observation.recorded": expected_result_summary["observation_count"],
                        "tool_call.recorded": expected_result_summary["tool_call_count"],
                        "tool_call.status_updated": expected_result_summary[
                            "tool_call_status_update_expected_count"
                        ],
                    },
                    "event_status_counts": {"completed": raw_event_count},
                    "event_actor_counts": {
                        "artifact": expected_result_summary["artifact_count"],
                        "tool": (
                            expected_result_summary["observation_count"]
                            + expected_result_summary["tool_call_count"]
                            + expected_result_summary["tool_call_status_update_expected_count"]
                        ),
                    },
                }
            raise AssertionError(f"unexpected readback-only request: {method} {path}")

        readback_only_execution_path = Path(temp) / "trace-ingest-readback-only-executed.json"
        original_trace_ingest_request_for_readback_only = trace_ingest_module.harness_api.request
        original_argv = sys.argv[:]
        stdout_buffer = io.StringIO()
        try:
            trace_ingest_module.harness_api.request = fake_readback_only_request
            sys.argv = [
                str(trace_ingest),
                "--readback-only",
                "--compact-output",
                "--output-json",
                str(readback_only_execution_path),
                "--fail-on-readback-invalid",
                "--client-id",
                "verify-client",
                "--task-id",
                "task_verify",
                "--trace-json",
                str(trace_fixture),
            ]
            with contextlib.redirect_stdout(stdout_buffer):
                readback_only_exit_code = trace_ingest_module.main()
        finally:
            trace_ingest_module.harness_api.request = original_trace_ingest_request_for_readback_only
            sys.argv = original_argv
        assert readback_only_exit_code == 0
        assert all(method in {"GET", "SSE"} for method, _ in readback_only_request_calls)
        assert [method for method, _ in readback_only_request_calls] == ["GET", "GET", "GET", "SSE"]
        readback_only_execution_compact = json.loads(stdout_buffer.getvalue())
        assert readback_only_execution_compact["mode"] == "readback_only"
        assert readback_only_execution_compact["readback_valid"] is True
        assert readback_only_execution_compact["readback_failed_checks"] == []
        assert readback_only_execution_compact["expected_result_count"] == expected_result_summary["result_count"]
        assert readback_only_execution_compact["expected_trace_artifact_digest"] == (
            expected_trace_artifact["digest"]
        )
        assert readback_only_execution_compact["readback_trace_artifact_digest_matches"] is True
        assert readback_only_execution_compact["readback_trace_artifact_binding"][
            "matched_artifact_count"
        ] == 1
        assert readback_only_execution_compact["stored_artifact_count"] == 0
        readback_only_execution_payload = json.loads(
            readback_only_execution_path.read_text(encoding="utf-8")
        )
        assert readback_only_execution_payload["mode"] == "readback_only"
        assert "results" not in readback_only_execution_payload
        assert "actions" not in readback_only_execution_payload
        assert readback_only_execution_payload["readback"]["validation"]["valid"] is True
        assert readback_only_execution_payload["readback"]["validation"]["checks"][
            "agent_trace_artifact_digest_matches_expected"
        ] is True
        broken_trace_artifact = dict(expected_trace_artifact)
        broken_trace_artifact["digest"] = "sha256:wrong-trace-digest"
        broken_trace_artifact_validation = trace_ingest_module.validate_readback(
            readback_only_execution_payload["readback"],
            expected_result_summary,
            expected_task_id="task_verify",
            expected_trace_artifact=broken_trace_artifact,
        )
        assert broken_trace_artifact_validation["valid"] is False
        assert "agent_trace_artifact_digest_matches_expected" in (
            broken_trace_artifact_validation["failed_checks"]
        )
        assert broken_trace_artifact_validation["trace_artifact_binding"][
            "matched_artifact_count"
        ] == 0
        ingest_result_summary = trace_ingest_module.summarize_results(
            [
                {"artifact_id": 42, "kind": "agent_trace", "status": "completed"},
                {
                    "call_pk": 7,
                    "tool_name": "record_signal",
                    "call_id": "call_nested",
                    "status": "completed",
                },
                {
                    "observation_id": 101,
                    "tool_name": "agent_model_response",
                    "status": "completed",
                },
                {
                    "observation_id": 102,
                    "tool_name": "record_signal",
                    "call_id": "call_nested",
                    "status": "completed",
                },
            ]
        )
        assert ingest_result_summary["source"] == "trace_ingest_result_summary"
        assert ingest_result_summary["result_count"] == 4
        assert ingest_result_summary["artifact_count"] == 1
        assert ingest_result_summary["artifact_ids"] == [42]
        assert ingest_result_summary["artifact_kind_counts"]["agent_trace"] == 1
        assert ingest_result_summary["tool_call_count"] == 1
        assert ingest_result_summary["tool_call_pks"] == [7]
        assert ingest_result_summary["tool_call_status_update_expected_count"] == 0
        assert ingest_result_summary["tool_call_name_counts"]["record_signal"] == 1
        assert ingest_result_summary["observation_count"] == 2
        assert ingest_result_summary["observation_ids"] == [101, 102]
        assert ingest_result_summary["observation_tool_counts"]["agent_model_response"] == 1
        assert ingest_result_summary["observation_tool_counts"]["record_signal"] == 1
        assert ingest_result_summary["status_counts"]["completed"] == 4
        assert ingest_result_summary["call_ids"] == ["call_nested"]
        assert "authoritative replay" in ingest_result_summary["claim_boundary"]
        queued_tool_result_summary = trace_ingest_module.summarize_results(
            [
                {
                    "call_pk": 8,
                    "tool_name": "approval_required_signal",
                    "call_id": "call_needs_approval",
                    "status": "queued",
                },
                {
                    "observation_id": 103,
                    "tool_name": "approval_required_signal",
                    "call_id": "call_needs_approval",
                    "status": "completed",
                },
            ]
        )
        assert queued_tool_result_summary["tool_call_status_update_expected_count"] == 1
        evidence_counts = trace_ingest_module.summarize_evidence(
            {
                "artifacts": [
                    {
                        "kind": "agent_trace",
                        "metadata": {"provider": "openai", "api_style": "responses"},
                    }
                ],
                "tool_calls": [{"tool_name": "record_signal"}],
                "observations": [
                    {"tool_name": "agent_model_response"},
                    {"tool_name": "record_signal"},
                ],
            }
        )
        assert evidence_counts["artifacts"] == 1
        assert evidence_counts["tool_calls"] == 1
        assert evidence_counts["observations"] == 2
        assert evidence_counts["observation_tool_counts"]["agent_model_response"] == 1
        assert evidence_counts["agent_model_response_provider_counts"] == {"openai": 1}
        assert evidence_counts["agent_model_response_api_style_counts"] == {"responses": 1}
        artifact_summaries = trace_ingest_module.summarize_artifacts(
            {
                "artifacts": [
                    {
                        "artifact_id": 42,
                        "kind": "agent_trace",
                        "path": str(trace_fixture),
                        "digest": "sha256:trace",
                        "status": "completed",
                        "metadata": {"trace_id": "trace_verify", "item": {"raw": True}},
                    }
                ]
            }
        )
        assert artifact_summaries == [
            {
                "artifact_id": 42,
                "kind": "agent_trace",
                "path": str(trace_fixture),
                "digest": "sha256:trace",
                "status": "completed",
                "metadata": {"trace_id": "trace_verify"},
            }
        ]
        assert "item" not in artifact_summaries[0]["metadata"]
        agent_session_summaries = trace_ingest_module.summarize_agent_sessions(
            {
                "observations": [
                    {
                        "tool_name": "agent_session",
                        "result": {
                            "session_id": "sdk_session_verify",
                            "backend": "SQLiteSession",
                            "db_path": "harness_outputs/private.session.sqlite3",
                            "turn_count": 2,
                            "turns": [
                                {"phase": "session_initial"},
                                {"phase": "session_turn_2"},
                            ],
                            "final_item_count": 4,
                            "final_item_types": ["user", "message", "user", "message"],
                            "final_items_preview": [{"text": "private prompt preview"}],
                        },
                        "status": "completed",
                        "evidence_refs": ["artifact:agent_trace:verify_trace.json", "agent_session"],
                    }
                ]
            }
        )
        assert agent_session_summaries == [
            {
                "agent_session_id": "sdk_session_verify",
                "backend": "SQLiteSession",
                "turn_count": 2,
                "turn_count_from_turns": 2,
                "final_item_count": 4,
                "final_item_types": ["user", "message", "user", "message"],
                "final_item_type_counts": {"user": 2, "message": 2},
                "turn_phases": ["session_initial", "session_turn_2"],
                "status": "completed",
                "evidence_refs": ["artifact:agent_trace:verify_trace.json", "agent_session"],
            }
        ]
        assert "db_path" not in agent_session_summaries[0]
        assert "final_items_preview" not in agent_session_summaries[0]
        large_replay_fetch_plan = trace_ingest_module.event_replay_fetch_plan(
            {
                "artifacts": 3,
                "observations": 700,
                "tool_calls": 4,
            },
            {"raw_event_count": 800},
        )
        assert large_replay_fetch_plan["source"] == "trace_ingest_event_replay_fetch_plan"
        assert large_replay_fetch_plan["basis"] == "sdk_events_raw_event_count"
        assert large_replay_fetch_plan["max_events"] == 825
        assert large_replay_fetch_plan["expected_raw_event_count"] == 800
        evidence_replay_fetch_plan = trace_ingest_module.event_replay_fetch_plan(
            {
                "artifacts": 2,
                "observations": 550,
                "tool_calls": 3,
            },
            {},
        )
        assert evidence_replay_fetch_plan["basis"] == "evidence_counts"
        assert evidence_replay_fetch_plan["max_events"] == 580
        assert evidence_replay_fetch_plan["evidence_event_floor"] == 555
        original_trace_ingest_request = trace_ingest_module.harness_api.request
        trace_ingest_fetch_calls: list[tuple[str, str, int | None]] = []

        def fake_trace_ingest_request(
            request_args: argparse.Namespace,
            method: str,
            path: str,
            body: Any = None,
        ) -> dict[str, Any]:
            del body
            trace_ingest_fetch_calls.append((method, path, getattr(request_args, "max_events", None)))
            if path == "/tasks/task_large/evidence":
                return {
                    "task_id": "task_large",
                    "artifacts": [{"kind": "agent_trace"}],
                    "tool_calls": [{"tool_name": "record_signal"}],
                    "observations": [
                        {"tool_name": "agent_model_response"}
                        for _ in range(700)
                    ],
                }
            if path == "/tasks/task_large/sdk-events?summary=true":
                return {
                    "summary_requested": True,
                    "task_id": "task_large",
                    "raw_event_count": 820,
                    "agent_model_response_count": 700,
                }
            if path == "/tasks/task_large/client-transcript?summary=true":
                return {
                    "summary_requested": True,
                    "task_id": "task_large",
                    "item_type_counts": {"agent_model_response": 700},
                }
            if method == "SSE" and path == "/tasks/task_large/events?after=0":
                return {
                    "status": "completed",
                    "source": "harness_event_sse_replay",
                    "task_id": "task_large",
                    "after": 0,
                    "events": [],
                    "event_count": 820,
                    "timed_out": False,
                    "next_after": 820,
                    "sse_event_name_counts": {"harness_event": 820},
                    "event_type_counts": {
                        "artifact.recorded": 1,
                        "observation.recorded": 700,
                        "task.updated": 118,
                        "tool_call.recorded": 1,
                    },
                    "event_status_counts": {"completed": 820},
                    "event_actor_counts": {
                        "api": 118,
                        "artifact": 1,
                        "tool": 701,
                    },
                }
            raise AssertionError(f"unexpected trace-ingest request: {method} {path}")

        try:
            trace_ingest_module.harness_api.request = fake_trace_ingest_request
            large_fetch_readback = trace_ingest_module.fetch_readback(
                argparse.Namespace(
                    base_url="http://verify",
                    client_id="verify-client",
                    api_key=None,
                    dry_run=False,
                ),
                "task_large",
                include_event_replay=True,
            )
        finally:
            trace_ingest_module.harness_api.request = original_trace_ingest_request
        assert large_fetch_readback["event_replay"]["fetch_plan"]["max_events"] == 845
        assert large_fetch_readback["event_replay"]["fetch_plan"]["expected_raw_event_count"] == 820
        assert ("SSE", "/tasks/task_large/events?after=0", 845) in trace_ingest_fetch_calls
        trace_ingest_event_replay = {
            "status": "completed",
            "source": "harness_event_sse_replay",
            "task_id": "task_verify",
            "after": 0,
            "events": [
                {
                    "id": "1",
                    "event": "harness_event",
                    "data": {"event_type": "artifact.recorded", "status": "completed", "actor": "artifact"},
                },
                {
                    "id": "2",
                    "event": "harness_event",
                    "data": {"event_type": "tool_call.recorded", "status": "completed", "actor": "tool"},
                },
                {
                    "id": "3",
                    "event": "harness_event",
                    "data": {"event_type": "observation.recorded", "status": "completed", "actor": "tool"},
                },
                {
                    "id": "4",
                    "event": "harness_event",
                    "data": {"event_type": "observation.recorded", "status": "completed", "actor": "tool"},
                },
            ],
            "event_count": 4,
            "keepalive_count": 0,
            "timed_out": False,
            "next_after": 4,
            "fetch_plan": {
                "source": "trace_ingest_event_replay_fetch_plan",
                "basis": "sdk_events_raw_event_count",
                "max_events": 500,
                "expected_raw_event_count": 4,
                "evidence_event_floor": 4,
            },
        }
        readback_validation = trace_ingest_module.validate_readback(
            {
                "task_id": "task_verify",
                "evidence_task_id": "task_verify",
                "evidence_counts": evidence_counts,
                "event_replay": trace_ingest_event_replay,
                "sdk_events_summary": {
                    "summary_requested": True,
                    "task_id": "task_verify",
                    "agent_model_response_count": 1,
                    "agent_model_response_provider_counts": {"openai": 1},
                    "agent_model_response_api_style_counts": {"responses": 1},
                    "agent_stream_event_count": 0,
                    "agent_guidance_count": 0,
                },
                "client_transcript_summary": {
                    "summary_requested": True,
                    "task_id": "task_verify",
                    "item_type_counts": {
                        "agent_model_response": 1,
                        "agent_stream_event": 0,
                        "agent_guidance": 0,
                    },
                    "agent_model_response_provider_counts": {"openai": 1},
                    "agent_model_response_api_style_counts": {"responses": 1},
                },
            },
            ingest_result_summary,
            expected_task_id="task_verify",
        )
        assert readback_validation["valid"] is True
        assert readback_validation["source"] == "trace_ingest_readback_validation"
        assert readback_validation["failed_checks"] == []
        assert readback_validation["check_count"] == len(readback_validation["checks"])
        assert readback_validation["event_replay_validation"]["source"] == "trace_ingest_event_replay_validation"
        assert readback_validation["event_replay_expected_event_counts"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 0,
        }
        assert readback_validation["event_replay_observed_event_counts"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 0,
        }
        assert readback_validation["event_replay_summary"]["sse_event_name_counts"] == {
            "harness_event": 4
        }
        assert readback_validation["event_replay_summary"]["event_status_counts"] == {
            "completed": 4
        }
        assert readback_validation["event_replay_summary"]["event_actor_counts"] == {
            "artifact": 1,
            "tool": 3,
        }
        assert readback_validation["event_replay_validation"]["event_replay_count_map_totals"] == {
            "event_actor_counts": 4,
            "event_status_counts": 4,
            "event_type_counts": 4,
            "sse_event_name_counts": 4,
        }
        assert readback_validation["event_replay_validation"]["checks"][
            "event_replay_sse_event_name_count_total_matches_event_count"
        ] is True
        assert readback_validation["event_replay_validation"]["checks"][
            "event_replay_event_type_count_total_matches_event_count"
        ] is True
        assert readback_validation["event_replay_validation"]["checks"][
            "event_replay_event_status_count_total_matches_event_count"
        ] is True
        assert readback_validation["event_replay_validation"]["checks"][
            "event_replay_event_actor_count_total_matches_event_count"
        ] is True
        assert readback_validation["checks"]["event_replay_covers_observation_recorded"] is True
        assert readback_validation["checks"][
            "event_replay_fetch_plan_covers_expected_raw_event_count"
        ] is True
        assert readback_validation["checks"]["event_replay_not_truncated_by_fetch_limit"] is True
        assert readback_validation["expected_model_response_projection_maps"] == {
            "agent_model_response_provider_counts": {"openai": 1},
            "agent_model_response_api_style_counts": {"responses": 1},
        }
        assert readback_validation["task_binding"]["expected_task_id"] == "task_verify"
        assert readback_validation["checks"]["readback_task_id_matches"] is True
        assert readback_validation["checks"]["readback_evidence_task_id_matches"] is True
        assert readback_validation["checks"]["readback_sdk_summary_task_id_matches"] is True
        assert readback_validation["checks"]["readback_transcript_task_id_matches"] is True
        assert readback_validation["checks"][
            "sdk_agent_model_response_provider_counts_match_evidence"
        ] is True
        assert readback_validation["checks"][
            "transcript_agent_model_response_api_style_counts_match_evidence"
        ] is True
        trace_ingest_compact_readback_summary = trace_ingest_module.trace_ingest_compact_summary(
            {
                "summary": ingest_summary,
                "result_summary": ingest_result_summary,
                "readback": {
                    "status": "completed",
                    "task_id": "task_verify",
                    "evidence_task_id": "task_verify",
                    "evidence_counts": evidence_counts,
                    "artifact_summaries": artifact_summaries,
                    "agent_session_summaries": agent_session_summaries,
                    "sdk_events_summary": {
                        "status": "completed",
                        "summary_requested": True,
                        "task_id": "task_verify",
                        "raw_event_count": 4,
                        "agent_model_response_count": 1,
                        "agent_model_response_provider_counts": {"openai": 1},
                        "agent_model_response_api_style_counts": {"responses": 1},
                        "final_output_count": 1,
                    },
                    "client_transcript_summary": {
                        "status": "completed",
                        "summary_requested": True,
                        "task_id": "task_verify",
                        "item_type_counts": {"agent_model_response": 1},
                        "agent_model_response_provider_counts": {"openai": 1},
                        "agent_model_response_api_style_counts": {"responses": 1},
                    },
                    "event_replay": trace_ingest_event_replay,
                    "validation": readback_validation,
                },
            }
        )
        assert trace_ingest_compact_readback_summary["source"] == "trace_ingest_compact_summary"
        assert trace_ingest_compact_readback_summary["status"] == "completed"
        assert trace_ingest_compact_readback_summary["readback_valid"] is True
        assert trace_ingest_compact_readback_summary["readback_check_count"] == readback_validation["check_count"]
        assert trace_ingest_compact_readback_summary["readback_failed_checks"] == []
        assert trace_ingest_compact_readback_summary["readback_task_binding"]["expected_task_id"] == "task_verify"
        assert trace_ingest_compact_readback_summary["event_replay_valid"] is True
        assert trace_ingest_compact_readback_summary["event_replay_event_count"] == 4
        assert trace_ingest_compact_readback_summary["event_replay_sse_event_name_counts"] == {
            "harness_event": 4
        }
        assert trace_ingest_compact_readback_summary["event_replay_event_type_counts"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
        }
        assert trace_ingest_compact_readback_summary["event_replay_event_status_counts"] == {
            "completed": 4
        }
        assert trace_ingest_compact_readback_summary["event_replay_event_actor_counts"] == {
            "artifact": 1,
            "tool": 3,
        }
        assert trace_ingest_compact_readback_summary["event_replay_count_map_totals"] == {
            "event_actor_counts": 4,
            "event_status_counts": 4,
            "event_type_counts": 4,
            "sse_event_name_counts": 4,
        }
        assert trace_ingest_compact_readback_summary["event_replay_expected_event_counts"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 0,
        }
        assert trace_ingest_compact_readback_summary[
            "sdk_events_agent_model_response_provider_counts"
        ] == {"openai": 1}
        assert trace_ingest_compact_readback_summary[
            "client_transcript_agent_model_response_api_style_counts"
        ] == {"responses": 1}
        assert trace_ingest_compact_readback_summary["artifact_summaries"] == artifact_summaries
        failed_trace_ingest_compact_summary = trace_ingest_module.trace_ingest_compact_summary(
            {
                "summary": ingest_summary,
                "result_summary": ingest_result_summary,
                "readback": {
                    "status": "completed",
                    "validation": {
                        "valid": False,
                        "check_count": 1,
                        "failed_checks": ["readback_task_id_matches"],
                    },
                },
            }
        )
        assert failed_trace_ingest_compact_summary["status"] == "failed"
        assert failed_trace_ingest_compact_summary["readback_failed_checks"] == ["readback_task_id_matches"]
        broken_trace_ingest_event_replay = json.loads(json.dumps(trace_ingest_event_replay))
        broken_trace_ingest_event_replay["events"] = [
            item
            for item in broken_trace_ingest_event_replay["events"]
            if item["data"]["event_type"] != "observation.recorded"
        ][:1]
        broken_trace_ingest_event_replay_validation = trace_ingest_module.validate_readback(
            {
                "task_id": "task_verify",
                "evidence_task_id": "task_verify",
                "evidence_counts": evidence_counts,
                "event_replay": broken_trace_ingest_event_replay,
                "sdk_events_summary": {
                    "summary_requested": True,
                    "task_id": "task_verify",
                    "agent_model_response_count": 1,
                    "agent_model_response_provider_counts": {"openai": 1},
                    "agent_model_response_api_style_counts": {"responses": 1},
                    "agent_stream_event_count": 0,
                    "agent_guidance_count": 0,
                },
                "client_transcript_summary": {
                    "summary_requested": True,
                    "task_id": "task_verify",
                    "item_type_counts": {
                        "agent_model_response": 1,
                        "agent_stream_event": 0,
                        "agent_guidance": 0,
                    },
                    "agent_model_response_provider_counts": {"openai": 1},
                    "agent_model_response_api_style_counts": {"responses": 1},
                },
            },
            ingest_result_summary,
            expected_task_id="task_verify",
        )
        assert broken_trace_ingest_event_replay_validation["valid"] is False
        assert "event_replay_covers_observation_recorded" in (
            broken_trace_ingest_event_replay_validation["failed_checks"]
        )
        truncated_trace_ingest_event_replay = json.loads(json.dumps(trace_ingest_event_replay))
        truncated_trace_ingest_event_replay["event_count"] = 4
        truncated_trace_ingest_event_replay["fetch_plan"]["expected_raw_event_count"] = 10
        truncated_trace_ingest_event_replay["fetch_plan"]["max_events"] = 10
        truncated_trace_ingest_event_replay_validation = trace_ingest_module.validate_event_replay(
            truncated_trace_ingest_event_replay,
            ingest_result_summary,
            expected_task_id="task_verify",
        )
        assert truncated_trace_ingest_event_replay_validation["valid"] is False
        assert "event_replay_not_truncated_by_fetch_limit" in (
            truncated_trace_ingest_event_replay_validation["failed_checks"]
        )
        wrong_task_readback_validation = trace_ingest_module.validate_readback(
            {
                "task_id": "task_verify",
                "evidence_task_id": "task_verify",
                "evidence_counts": evidence_counts,
                "sdk_events_summary": {
                    "summary_requested": True,
                    "task_id": "task_verify",
                    "agent_model_response_count": 1,
                    "agent_model_response_provider_counts": {"openai": 1},
                    "agent_model_response_api_style_counts": {"responses": 1},
                    "agent_stream_event_count": 0,
                    "agent_guidance_count": 0,
                },
                "client_transcript_summary": {
                    "summary_requested": True,
                    "task_id": "task_verify",
                    "item_type_counts": {
                        "agent_model_response": 1,
                        "agent_stream_event": 0,
                        "agent_guidance": 0,
                    },
                    "agent_model_response_provider_counts": {"openai": 1},
                    "agent_model_response_api_style_counts": {"responses": 1},
                },
            },
            ingest_result_summary,
            expected_task_id="task_other",
        )
        assert wrong_task_readback_validation["valid"] is False
        assert "readback_task_id_matches" in wrong_task_readback_validation["failed_checks"]
        assert "readback_evidence_task_id_matches" in wrong_task_readback_validation["failed_checks"]
        assert "readback_sdk_summary_task_id_matches" in wrong_task_readback_validation["failed_checks"]
        assert "readback_transcript_task_id_matches" in wrong_task_readback_validation["failed_checks"]
        missing_task_readback_validation = trace_ingest_module.validate_readback(
            {
                "evidence_counts": evidence_counts,
                "sdk_events_summary": {
                    "summary_requested": True,
                    "agent_model_response_count": 1,
                    "agent_model_response_provider_counts": {"openai": 1},
                    "agent_model_response_api_style_counts": {"responses": 1},
                    "agent_stream_event_count": 0,
                    "agent_guidance_count": 0,
                },
                "client_transcript_summary": {
                    "summary_requested": True,
                    "item_type_counts": {
                        "agent_model_response": 1,
                        "agent_stream_event": 0,
                        "agent_guidance": 0,
                    },
                    "agent_model_response_provider_counts": {"openai": 1},
                    "agent_model_response_api_style_counts": {"responses": 1},
                },
            },
            ingest_result_summary,
            expected_task_id="task_verify",
        )
        assert missing_task_readback_validation["valid"] is False
        assert "readback_task_id_matches" in missing_task_readback_validation["failed_checks"]
        assert "readback_evidence_task_id_matches" in missing_task_readback_validation["failed_checks"]
        assert "readback_sdk_summary_task_id_matches" in missing_task_readback_validation["failed_checks"]
        assert "readback_transcript_task_id_matches" in missing_task_readback_validation["failed_checks"]
        expanded_evidence_counts = trace_ingest_module.summarize_evidence(
            {
                "artifacts": [
                    {
                        "kind": "agent_trace",
                        "metadata": {"provider": "openai", "api_style": "responses"},
                    }
                ],
                "tool_calls": [{"tool_name": "record_signal"}],
                "observations": [
                    {"tool_name": "agent_model_response"},
                    {
                        "tool_name": "agent_stream_event",
                        "result": {
                            "type": "raw_response_event",
                            "name": None,
                            "raw_event_type": "response.output_text.delta",
                            "item_class": None,
                            "tool_name": None,
                        },
                    },
                    {
                        "tool_name": "agent_stream_event",
                        "result": {
                            "type": "run_item_stream_event",
                            "name": "tool_called",
                            "raw_event_type": None,
                            "item_class": "ToolCallItem",
                            "tool_name": "record_signal",
                        },
                    },
                    {"tool_name": "agent_run_phase"},
                    {"tool_name": "agent_session"},
                    {"tool_name": "agent_handoff"},
                    {"tool_name": "agent_approval_request"},
                    {"tool_name": "agent_approval_decision"},
                    {"tool_name": "agent_question"},
                    {"tool_name": "agent_guidance"},
                    {"tool_name": "agent_guardrail_result"},
                    {"tool_name": "agent_run_error"},
                    {"tool_name": "agent_final_output"},
                ],
            }
        )
        expanded_readback_validation = trace_ingest_module.validate_readback(
            {
                "task_id": "task_verify",
                "evidence_task_id": "task_verify",
                "evidence_counts": expanded_evidence_counts,
                "sdk_events_summary": {
                    "summary_requested": True,
                    "task_id": "task_verify",
                    "agent_model_response_count": 1,
                    "agent_model_response_provider_counts": {"openai": 1},
                    "agent_model_response_api_style_counts": {"responses": 1},
                    "agent_stream_event_count": 2,
                    "agent_stream_event_type_counts": {
                        "raw_response_event": 1,
                        "run_item_stream_event": 1,
                    },
                    "agent_stream_event_name_counts": {
                        "none": 1,
                        "tool_called": 1,
                    },
                    "agent_stream_raw_event_type_counts": {
                        "response.output_text.delta": 1,
                        "none": 1,
                    },
                    "agent_stream_item_class_counts": {
                        "none": 1,
                        "ToolCallItem": 1,
                    },
                    "agent_stream_tool_name_counts": {
                        "none": 1,
                        "record_signal": 1,
                    },
                    "agent_phase_count": 1,
                    "agent_session_count": 1,
                    "agent_handoff_count": 1,
                    "agent_approval_count": 2,
                    "agent_question_count": 1,
                    "agent_guidance_count": 1,
                    "agent_guardrail_count": 1,
                    "agent_run_error_count": 1,
                    "final_output_count": 1,
                },
                "client_transcript_summary": {
                    "summary_requested": True,
                    "task_id": "task_verify",
                    "item_type_counts": {
                        "agent_model_response": 1,
                        "agent_stream_event": 2,
                        "agent_phase": 1,
                        "agent_session": 1,
                        "agent_handoff": 1,
                        "agent_approval": 2,
                        "agent_question": 1,
                        "agent_guidance": 1,
                        "agent_guardrail": 1,
                        "agent_run_error": 1,
                    },
                    "agent_stream_event_type_counts": {
                        "raw_response_event": 1,
                        "run_item_stream_event": 1,
                    },
                    "agent_stream_event_name_counts": {
                        "none": 1,
                        "tool_called": 1,
                    },
                    "agent_stream_raw_event_type_counts": {
                        "response.output_text.delta": 1,
                        "none": 1,
                    },
                    "agent_stream_item_class_counts": {
                        "none": 1,
                        "ToolCallItem": 1,
                    },
                    "agent_stream_tool_name_counts": {
                        "none": 1,
                        "record_signal": 1,
                    },
                    "agent_model_response_provider_counts": {"openai": 1},
                    "agent_model_response_api_style_counts": {"responses": 1},
                },
            },
            ingest_result_summary,
            expected_task_id="task_verify",
        )
        assert expanded_readback_validation["valid"] is True
        assert expanded_readback_validation["failed_checks"] == []
        assert expanded_readback_validation["check_count"] == len(expanded_readback_validation["checks"])
        assert expanded_readback_validation["expected_projection_counts"]["agent_approval"] == 2
        assert expanded_readback_validation["expected_stream_event_counts"]["event_type"] == {
            "raw_response_event": 1,
            "run_item_stream_event": 1,
        }
        assert expanded_readback_validation["checks"]["sdk_agent_approval_matches_evidence"] is True
        assert expanded_readback_validation["checks"]["sdk_agent_stream_item_class_counts_match_evidence"] is True
        assert expanded_readback_validation["checks"]["transcript_agent_guardrail_matches_evidence"] is True
        assert expanded_readback_validation["checks"]["transcript_agent_stream_tool_name_counts_match_evidence"] is True
        assert expanded_readback_validation["checks"]["sdk_final_output_covers_agent_final_output"] is True
        expanded_trace_ingest_compact_summary = trace_ingest_module.trace_ingest_compact_summary(
            {
                "summary": ingest_summary,
                "result_summary": ingest_result_summary,
                "readback": {
                    "status": "completed",
                    "task_id": "task_verify",
                    "evidence_task_id": "task_verify",
                    "evidence_counts": expanded_evidence_counts,
                    "sdk_events_summary": {
                        "status": "completed",
                        "summary_requested": True,
                        "task_id": "task_verify",
                        "agent_model_response_count": 1,
                        "agent_model_response_provider_counts": {"openai": 1},
                        "agent_model_response_api_style_counts": {"responses": 1},
                        "agent_stream_event_count": 2,
                        "agent_stream_event_type_counts": {
                            "raw_response_event": 1,
                            "run_item_stream_event": 1,
                        },
                        "agent_stream_event_name_counts": {
                            "none": 1,
                            "tool_called": 1,
                        },
                        "agent_stream_raw_event_type_counts": {
                            "response.output_text.delta": 1,
                            "none": 1,
                        },
                        "agent_stream_item_class_counts": {
                            "none": 1,
                            "ToolCallItem": 1,
                        },
                        "agent_stream_tool_name_counts": {
                            "none": 1,
                            "record_signal": 1,
                        },
                    },
                    "client_transcript_summary": {
                        "status": "completed",
                        "summary_requested": True,
                        "task_id": "task_verify",
                        "item_type_counts": {
                            "agent_model_response": 1,
                            "agent_stream_event": 2,
                        },
                        "agent_model_response_provider_counts": {"openai": 1},
                        "agent_model_response_api_style_counts": {"responses": 1},
                        "agent_stream_event_count": 2,
                        "agent_stream_event_type_counts": {
                            "raw_response_event": 1,
                            "run_item_stream_event": 1,
                        },
                        "agent_stream_event_name_counts": {
                            "none": 1,
                            "tool_called": 1,
                        },
                        "agent_stream_raw_event_type_counts": {
                            "response.output_text.delta": 1,
                            "none": 1,
                        },
                        "agent_stream_item_class_counts": {
                            "none": 1,
                            "ToolCallItem": 1,
                        },
                        "agent_stream_tool_name_counts": {
                            "none": 1,
                            "record_signal": 1,
                        },
                    },
                    "validation": expanded_readback_validation,
                },
            }
        )
        assert expanded_trace_ingest_compact_summary["readback_valid"] is True
        assert expanded_trace_ingest_compact_summary["readback_expected_stream_event_counts"]["tool_name"] == {
            "none": 1,
            "record_signal": 1,
        }
        assert expanded_trace_ingest_compact_summary["evidence_agent_stream_tool_name_counts"] == {
            "none": 1,
            "record_signal": 1,
        }
        assert expanded_trace_ingest_compact_summary["sdk_events_agent_stream_event_count"] == 2
        assert expanded_trace_ingest_compact_summary["sdk_events_agent_stream_tool_name_counts"] == {
            "none": 1,
            "record_signal": 1,
        }
        assert expanded_trace_ingest_compact_summary["client_transcript_agent_stream_event_count"] == 2
        assert expanded_trace_ingest_compact_summary["client_transcript_agent_stream_item_class_counts"] == {
            "none": 1,
            "ToolCallItem": 1,
        }
        missing_provider_map_validation = trace_ingest_module.validate_readback(
            {
                "task_id": "task_verify",
                "evidence_task_id": "task_verify",
                "evidence_counts": evidence_counts,
                "sdk_events_summary": {
                    "summary_requested": True,
                    "task_id": "task_verify",
                    "agent_model_response_count": 1,
                    "agent_model_response_provider_counts": {},
                    "agent_model_response_api_style_counts": {"responses": 1},
                    "agent_stream_event_count": 0,
                    "agent_guidance_count": 0,
                },
                "client_transcript_summary": {
                    "summary_requested": True,
                    "task_id": "task_verify",
                    "item_type_counts": {
                        "agent_model_response": 1,
                        "agent_stream_event": 0,
                        "agent_guidance": 0,
                    },
                    "agent_model_response_provider_counts": {},
                    "agent_model_response_api_style_counts": {"responses": 1},
                },
            },
            ingest_result_summary,
            expected_task_id="task_verify",
        )
        assert missing_provider_map_validation["valid"] is False
        assert "sdk_agent_model_response_provider_counts_match_evidence" in (
            missing_provider_map_validation["failed_checks"]
        )
        assert "transcript_agent_model_response_provider_counts_match_evidence" in (
            missing_provider_map_validation["failed_checks"]
        )
        action_paths = [item["path"] for item in ingest_payload["actions"]]
        assert "/tasks/task_verify/tool-calls" in action_paths
        assert "/tasks/task_verify/observations" in action_paths
        assert "/tasks/task_verify/artifacts" in action_paths
        trace_artifact = ingest_payload["actions"][0]["body"]
        assert trace_artifact["digest"].startswith("sha256:")
        assert trace_artifact["metadata"]["trace_id"] == "trace_verify"
        typed_artifacts = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/artifacts"
            and item["body"]["kind"] in {"handoff", "approval_or_interrupt", "guardrail_result"}
        ]
        assert typed_artifacts
        assert all(item["body"].get("path") == str(trace_fixture) for item in typed_artifacts)
        summary_observations = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/observations"
            and item["body"]["tool_name"] == "agent_run_summary"
        ]
        assert summary_observations
        assert summary_observations[0]["body"]["result"]["external_api_called"] is True
        assert summary_observations[0]["body"]["result"]["summary"]["final_ok"] is True
        question_observations = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/observations"
            and item["body"]["tool_name"] == "agent_question"
        ]
        assert len(question_observations) == 1
        assert question_observations[0]["body"]["status"] == "waiting_for_user"
        assert question_observations[0]["body"]["result"]["question_type"] == "missing_info_request"
        assert question_observations[0]["body"]["result"]["questions"][0]["field"] == "baseline_evidence"
        assert (
            question_observations[0]["body"]["result"]["reason"]
            == "baseline_evidence_required_before_precise_similarity_report"
        )
        assert question_observations[0]["body"]["result"]["evidence_status"] == "baseline_evidence_incomplete"
        assert question_observations[0]["body"]["result"]["source_observation"] == "write_purchase_report"
        assert question_observations[0]["body"]["result"]["agent"] == "Harness Question Agent"
        assert "agent_question:1" in question_observations[0]["body"]["evidence_refs"]
        phase_observations = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/observations"
            and item["body"]["tool_name"] == "agent_run_phase"
        ]
        assert len(phase_observations) == 2
        assert phase_observations[0]["body"]["result"]["name"] == "initial"
        assert phase_observations[0]["body"]["result"]["interruption_count"] == 1
        assert phase_observations[1]["body"]["result"]["name"] == "after_approval"
        assert phase_observations[1]["body"]["result"]["final_output"] == "done"
        assert "phase:2" in phase_observations[1]["body"]["evidence_refs"]
        final_output_observations = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/observations"
            and item["body"]["tool_name"] == "agent_final_output"
        ]
        assert final_output_observations
        assert final_output_observations[0]["body"]["result"]["final_output"] == "done"
        assert "artifact:agent_trace:" in final_output_observations[0]["body"]["evidence_refs"][0]
        model_response_observations = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/observations"
            and item["body"]["tool_name"] == "agent_model_response"
        ]
        assert model_response_observations
        assert model_response_observations[0]["body"]["result"]["response_id"] == "resp_verify_1"
        assert model_response_observations[0]["body"]["result"]["model"] == "gpt-5.4-mini"
        assert model_response_observations[0]["body"]["result"]["model_source"] == "trace"
        assert model_response_observations[0]["body"]["result"]["usage"]["total_tokens"] == 15
        assert model_response_observations[1]["body"]["result"]["response_id"] == "resp_verify_2"
        assert model_response_observations[1]["body"]["result"]["model"] == "gpt-5.4-mini"
        assert model_response_observations[1]["body"]["result"]["model_source"] == "raw_response"
        nested_tool_actions = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/tool-calls"
            and item["body"].get("call_id") == "call_nested"
        ]
        assert nested_tool_actions
        assert nested_tool_actions[0]["body"]["tool_name"] == "record_signal"
        assert nested_tool_actions[0]["body"]["input"]["marker"] == "AGENTS_SDK_LIVE_TOOL_OK"
        nested_observations = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/observations"
            and item["body"].get("call_id") == "call_nested"
        ]
        assert nested_observations
        assert nested_observations[0]["body"]["tool_name"] == "record_signal"
        assert nested_observations[0]["body"]["result"]["status"] == "recorded"
        handoff_observations = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/observations"
            and item["body"]["tool_name"] == "agent_handoff"
        ]
        assert len(handoff_observations) == 2
        handoff_call = next(
            item
            for item in handoff_observations
            if item["body"]["result"]["item_type"] == "handoff_call_item"
        )
        assert handoff_call["body"]["call_id"] == "call_handoff"
        assert handoff_call["body"]["result"]["tool_name"] == "transfer_to_harness_specialist"
        assert handoff_call["body"]["result"]["arguments"] == {}
        assert handoff_call["body"]["result"]["raw_type"] == "function_call"
        assert handoff_call["body"]["result"].get("raw_status") is None
        handoff_output = next(
            item
            for item in handoff_observations
            if item["body"]["result"]["item_type"] == "handoff_output_item"
        )
        assert handoff_output["body"]["result"]["source_agent"] == "Harness Router Agent"
        assert handoff_output["body"]["result"]["target_agent"] == "Harness Specialist Agent"
        assert handoff_output["body"]["result"]["tool_name"] == "transfer_to_harness_specialist"
        assert handoff_output["body"]["result"]["output"]["assistant"] == "Harness Specialist Agent"
        assert handoff_output["body"]["result"]["raw_type"] == "function_call_output"
        session_observations = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/observations"
            and item["body"]["tool_name"] == "agent_session"
        ]
        assert len(session_observations) == 1
        assert session_observations[0]["body"]["result"]["backend"] == "SQLiteSession"
        assert session_observations[0]["body"]["result"]["turn_count"] == 2
        assert session_observations[0]["body"]["result"]["final_item_types"] == [
            "user",
            "message",
            "user",
            "message",
        ]
        assert session_observations[0]["body"]["result"]["final_items_preview"][0]["role"] == "user"
        assert (
            session_observations[0]["body"]["result"]["final_items_preview"][0]["text"]
            == "Remember this session code exactly: BLUE-HARNESS-42."
        )
        assert (
            session_observations[0]["body"]["result"]["final_items_preview"][3]["text"]
            == "AGENTS_SDK_SESSION_FINAL_OK"
        )
        assert session_observations[0]["body"]["evidence_refs"] == [
            "artifact:agent_trace:agent-result.json",
            "agent_session",
        ]
        stream_observations = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/observations"
            and item["body"]["tool_name"] == "agent_stream_event"
        ]
        assert len(stream_observations) == 5
        assert stream_observations[0]["body"]["result"]["raw_event_type"] == "response.output_text.delta"
        assert stream_observations[1]["body"]["call_id"] == "call_stream"
        assert stream_observations[1]["body"]["result"]["name"] == "tool_called"
        assert stream_observations[1]["body"]["result"]["tool_name"] == "record_signal"
        assert stream_observations[2]["body"]["call_id"] == "call_stream_handoff"
        assert stream_observations[2]["body"]["result"]["name"] == "handoff_called"
        assert stream_observations[2]["body"]["result"]["tool_name"] == "transfer_to_harness_specialist"
        assert stream_observations[2]["body"]["result"]["agent"] == "Harness Router Agent"
        assert stream_observations[3]["body"]["call_id"] == "call_stream_handoff"
        assert stream_observations[3]["body"]["result"]["name"] == "handoff_output"
        assert stream_observations[3]["body"]["result"]["tool_name"] == "transfer_to_harness_specialist"
        assert stream_observations[3]["body"]["result"]["item_class"] == "HandoffOutputItem"
        assert stream_observations[4]["body"]["result"]["agent"] == "Harness Stream Agent"
        assert stream_observations[4]["body"]["evidence_refs"] == [
            "artifact:agent_trace:agent-result.json",
            "stream_event:5",
        ]
        guidance_observations = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/observations"
            and item["body"]["tool_name"] == "agent_guidance"
        ]
        assert len(guidance_observations) == 1
        assert guidance_observations[0]["body"]["result"]["reason"] == "continue_search_for_closer_measurements"
        assert guidance_observations[0]["body"]["result"]["next_tool_suggestions"][0]["tool"] == "search_web_candidates"
        assert guidance_observations[0]["body"]["result"]["close_fit_search_budget"]["remaining_attempts"] == 23
        assert guidance_observations[0]["body"]["evidence_refs"] == [
            "artifact:agent_trace:agent-result.json",
            "agent_guidance:1",
        ]
        approval_observations = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/observations"
            and item["body"]["tool_name"] in {"agent_approval_request", "agent_approval_decision"}
        ]
        assert len(approval_observations) == 2
        approval_request = next(
            item
            for item in approval_observations
            if item["body"]["tool_name"] == "agent_approval_request"
        )
        assert approval_request["body"]["call_id"] == "call_approval"
        assert approval_request["body"]["result"]["tool_name"] == "approval_required_signal"
        assert approval_request["body"]["result"]["arguments"]["marker"] == "AGENTS_SDK_APPROVAL_TOOL_OK"
        approval_decision = next(
            item
            for item in approval_observations
            if item["body"]["tool_name"] == "agent_approval_decision"
        )
        assert approval_decision["body"]["result"]["decision"] == "approved"
        assert approval_decision["body"]["result"]["arguments"]["marker"] == "AGENTS_SDK_APPROVAL_TOOL_OK"
        assert approval_decision["body"]["result"]["raw_type"] == "function_call"
        assert approval_decision["body"]["result"]["agent"] == "Harness Approval Agent"
        approval_tool_actions = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/tool-calls"
            and item["body"].get("call_id") == "call_approval"
        ]
        assert len(approval_tool_actions) == 1
        assert approval_tool_actions[0]["body"]["tool_name"] == "approval_required_signal"
        assert approval_tool_actions[0]["body"]["status"] == "queued"
        assert approval_tool_actions[0]["body"]["metadata"]["approval_required"] is True
        assert approval_tool_actions[0]["body"]["input"]["marker"] == "AGENTS_SDK_APPROVAL_TOOL_OK"
        approval_tool_output = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/observations"
            and item["body"].get("call_id") == "call_approval"
            and item["body"]["tool_name"] == "approval_required_signal"
        ]
        assert len(approval_tool_output) == 1
        assert approval_tool_output[0]["body"]["result"]["status"] == "approved_and_recorded"
        assert approval_tool_output[0]["body"]["result"]["marker"] == "AGENTS_SDK_APPROVAL_TOOL_OK"
        guardrail_observations = [
            item
            for item in ingest_payload["actions"]
            if item["path"] == "/tasks/task_verify/observations"
            and item["body"]["tool_name"] == "agent_guardrail_result"
        ]
        assert len(guardrail_observations) == 2
        output_guardrail = next(
            item
            for item in guardrail_observations
            if item["body"]["result"]["surface"] == "output_guardrail_results"
        )
        assert output_guardrail["body"]["result"]["guardrail_name"] == "harness_output_guardrail"
        assert output_guardrail["body"]["result"]["tripwire_triggered"] is False
        assert output_guardrail["body"]["result"]["output_info"]["marker"] == "AGENTS_SDK_GUARDRAIL_TOOL_OK"
        tripwire_fixture = Path(temp) / "agent-tripwire-trace.json"
        tripwire_fixture.write_text(
            json.dumps(
                {
                    "status": "guardrail_tripwire_triggered",
                    "source": "agents_sdk_smoke",
                    "scenario": "guardrail_tripwire",
                    "provider": "openai",
                    "api_style": "responses",
                    "sdk": "openai-agents",
                    "sdk_version": "0.17.7",
                    "external_api_called": True,
                    "model": "gpt-5.4-mini",
                    "trace_id": "trace_tripwire_verify",
                    "error_type": "OutputGuardrailTripwireTriggered",
                    "error": "Guardrail OutputGuardrail triggered tripwire",
                    "raw_responses": [
                        {
                            "class": "ModelResponse",
                            "response_id": "resp_tripwire_1",
                            "request_id": "req_tripwire_1",
                            "usage": {"total_tokens": 21},
                            "output": [{"type": "message"}],
                        }
                    ],
                    "phases": [
                        {
                            "name": "guardrail_tripwire",
                            "final_output": "AGENTS_SDK_GUARDRAIL_TRIPWIRE_FINAL_OK",
                            "new_items": [
                                {
                                    "type": "message_output_item",
                                    "class": "MessageOutputItem",
                                    "agent": {"name": "Harness Tripwire Agent"},
                                }
                            ],
                            "raw_response_count": 1,
                            "interruption_count": 0,
                        }
                    ],
                    "output_guardrail_results": [
                        {
                            "class": "OutputGuardrailResult",
                            "guardrail": {
                                "class": "OutputGuardrail",
                                "name": "harness_tripwire_guardrail",
                            },
                            "agent": {"name": "Harness Tripwire Agent"},
                            "agent_output": "AGENTS_SDK_GUARDRAIL_TRIPWIRE_FINAL_OK",
                            "output": {
                                "output_info": {
                                    "status": "blocked",
                                    "marker": "AGENTS_SDK_GUARDRAIL_TRIPWIRE_TOOL_OK",
                                    "reason": "intentional_live_smoke_tripwire",
                                },
                                "tripwire_triggered": True,
                            },
                        }
                    ],
                    "final_output": "AGENTS_SDK_GUARDRAIL_TRIPWIRE_FINAL_OK",
                    "summary": {
                        "guardrail_tripwire_triggered": True,
                        "final_ok": True,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tripwire_ingest_run = subprocess.run(
            [
                sys.executable,
                str(trace_ingest),
                "--dry-run",
                "--client-id",
                "verify-client",
                "--task-id",
                "task_tripwire_verify",
                "--trace-json",
                str(tripwire_fixture),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        tripwire_payload = json.loads(tripwire_ingest_run.stdout)
        tripwire_errors = [
            item
            for item in tripwire_payload["actions"]
            if item["path"] == "/tasks/task_tripwire_verify/observations"
            and item["body"]["tool_name"] == "agent_run_error"
        ]
        assert tripwire_errors
        assert tripwire_errors[0]["body"]["status"] == "failed"
        assert tripwire_errors[0]["body"]["result"]["error_type"] == "OutputGuardrailTripwireTriggered"
        tripwire_model_responses = [
            item
            for item in tripwire_payload["actions"]
            if item["path"] == "/tasks/task_tripwire_verify/observations"
            and item["body"]["tool_name"] == "agent_model_response"
        ]
        assert tripwire_model_responses
        assert tripwire_model_responses[0]["body"]["result"]["model"] == "gpt-5.4-mini"
        assert tripwire_model_responses[0]["body"]["result"]["model_source"] == "trace"
        tripwire_phases = [
            item
            for item in tripwire_payload["actions"]
            if item["path"] == "/tasks/task_tripwire_verify/observations"
            and item["body"]["tool_name"] == "agent_run_phase"
        ]
        assert tripwire_phases
        assert tripwire_phases[0]["body"]["result"]["name"] == "guardrail_tripwire"
        assert tripwire_phases[0]["body"]["result"]["final_output"] == "AGENTS_SDK_GUARDRAIL_TRIPWIRE_FINAL_OK"
        tripwire_guardrails = [
            item
            for item in tripwire_payload["actions"]
            if item["path"] == "/tasks/task_tripwire_verify/observations"
            and item["body"]["tool_name"] == "agent_guardrail_result"
        ]
        assert tripwire_guardrails
        assert tripwire_guardrails[0]["body"]["status"] == "failed"
        assert tripwire_guardrails[0]["body"]["result"]["guardrail_name"] == "harness_tripwire_guardrail"
        assert tripwire_guardrails[0]["body"]["result"]["tripwire_triggered"] is True
        assert tripwire_guardrails[0]["body"]["result"]["output_info"]["reason"] == "intentional_live_smoke_tripwire"
        tripwire_final_outputs = [
            item
            for item in tripwire_payload["actions"]
            if item["path"] == "/tasks/task_tripwire_verify/observations"
            and item["body"]["tool_name"] == "agent_final_output"
        ]
        assert tripwire_final_outputs
        assert tripwire_final_outputs[0]["body"]["result"]["final_output"] == "AGENTS_SDK_GUARDRAIL_TRIPWIRE_FINAL_OK"
        agents_sdk_smoke = root / ".agents" / "skills" / "agentic-harness-loop" / "scripts" / "agents_sdk_smoke.py"
        assert agents_sdk_smoke.exists()
        subprocess.run([sys.executable, "-m", "py_compile", str(agents_sdk_smoke)], check=True)
        agents_sdk_smoke_module = _load_module("agents_sdk_smoke_verify", agents_sdk_smoke)
        smoke_summary = agents_sdk_smoke_module.build_smoke_summary(
            items=[
                {
                    "type": "tool_call_item",
                    "class": "ToolCallItem",
                    "call_id": "call_summary_tool",
                    "tool_name": "record_signal",
                    "raw_item": {"type": "function_call", "call_id": "call_summary_tool", "name": "record_signal"},
                },
                {
                    "type": "tool_call_output_item",
                    "class": "ToolCallOutputItem",
                    "call_id": "call_summary_tool",
                    "raw_item": {"type": "function_call_output", "call_id": "call_summary_tool"},
                    "output": '{"status":"recorded"}',
                },
            ],
            raw_responses=[
                {
                    "response_id": "resp_summary",
                    "usage": {"requests": 1, "input_tokens": 11, "output_tokens": 5, "total_tokens": 16},
                    "output": [{"type": "function_call", "name": "record_signal"}],
                }
            ],
            interruptions=[{"type": "tool_approval_item", "class": "ToolApprovalItem"}],
            approval_decisions=[{"decision": "approved", "tool_name": "record_signal"}],
            phases=[
                {
                    "name": "initial",
                    "new_items": [{"type": "tool_call_item"}],
                    "raw_response_count": 1,
                    "interruption_count": 1,
                },
                {
                    "name": "after_approval",
                    "new_items": [{"type": "tool_call_output_item"}],
                    "raw_response_count": 1,
                    "interruption_count": 0,
                },
            ],
            stream_events=[
                {
                    "index": 1,
                    "type": "run_item_stream_event",
                    "name": "tool_called",
                    "item": {
                        "class": "ToolCallItem",
                        "call_id": "call_summary_stream",
                        "tool_name": "record_signal",
                    },
                },
                {
                    "index": 2,
                    "type": "run_item_stream_event",
                    "name": "tool_output",
                    "item": {
                        "class": "ToolCallOutputItem",
                        "call_id": "call_summary_stream",
                    },
                },
                {"index": 3, "type": "raw_response_event", "raw_event_type": "response.output_text.delta"},
            ],
            input_guardrail_results=[],
            output_guardrail_results=[
                {
                    "output": {
                        "tripwire_triggered": True,
                        "output_info": {"reason": "verify_tripwire"},
                    }
                }
            ],
            tool_input_guardrail_results=[],
            tool_output_guardrail_results=[],
            session={
                "turns": [{"index": 1}, {"index": 2}],
                "final_item_count": 3,
                "final_item_types": ["message", "reasoning", "message"],
            },
            marker="VERIFY_MARKER",
            marker_search_surface={"items": [{"text": "VERIFY_MARKER"}]},
            final_output="VERIFY_FINAL",
            expected_final_output="VERIFY_FINAL",
        )
        assert smoke_summary["new_item_count"] == 2
        assert smoke_summary["new_item_type_counts"] == {"tool_call_item": 1, "tool_call_output_item": 1}
        assert smoke_summary["raw_response_count"] == 1
        assert smoke_summary["raw_response_output_type_counts"] == {"function_call": 1}
        assert smoke_summary["raw_response_usage_totals"]["requests"] == 1
        assert smoke_summary["raw_response_usage_totals"]["total_tokens"] == 16
        assert smoke_summary["phase_names"] == ["initial", "after_approval"]
        assert smoke_summary["phase_interruption_counts"] == {"initial": 1, "after_approval": 0}
        assert smoke_summary["approval_decision_counts"] == {"approved": 1}
        assert smoke_summary["guardrail_tripwire_count"] == 1
        assert smoke_summary["stream_event_type_counts"] == {"run_item_stream_event": 2, "raw_response_event": 1}
        assert smoke_summary["stream_event_name_counts"] == {"tool_called": 1, "tool_output": 1, "none": 1}
        assert smoke_summary["stream_raw_event_type_counts"] == {"none": 2, "response.output_text.delta": 1}
        assert smoke_summary["stream_item_class_counts"] == {"ToolCallItem": 1, "ToolCallOutputItem": 1, "none": 1}
        assert smoke_summary["stream_tool_name_counts"] == {"record_signal": 2, "none": 1}
        assert smoke_summary["session_turn_count"] == 2
        assert smoke_summary["session_item_type_counts"] == {"message": 2, "reasoning": 1}
        assert smoke_summary["marker_observed"] is True
        assert smoke_summary["final_ok"] is True
        agents_sdk_smoke_matrix = (
            root / ".agents" / "skills" / "agentic-harness-loop" / "scripts" / "agents_sdk_smoke_matrix.py"
        )
        assert agents_sdk_smoke_matrix.exists()
        subprocess.run([sys.executable, "-m", "py_compile", str(agents_sdk_smoke_matrix)], check=True)
        matrix_module = _load_module("agents_sdk_smoke_matrix_verify", agents_sdk_smoke_matrix)
        compact_source_session = matrix_module.compact_live_session_state(
            {
                "session_id": "sdk_session_source_verify",
                "backend": "SQLiteSession",
                "db_path": "harness_outputs/private.session.sqlite3",
                "turns": [
                    {
                        "phase": "session_initial",
                        "prompt": "private prompt one",
                        "item_count_before": 0,
                        "item_count_after": 2,
                        "new_item_count": 1,
                        "raw_response_count": 1,
                    },
                    {
                        "phase": "session_turn_2",
                        "prompt": "private prompt two",
                        "item_count_before": 2,
                        "item_count_after": 4,
                        "new_item_count": 1,
                        "raw_response_count": 1,
                    },
                ],
                "turn_count": 2,
                "final_item_count": 4,
                "final_item_types": ["user", "message", "user", "message"],
                "final_items_preview": [{"text": "private"} for _ in range(4)],
            },
            {
                "session_turn_count": 2,
                "session_item_count": 4,
                "session_item_type_counts": {"user": 2, "message": 2},
                "phase_names": ["session_initial", "session_turn_2"],
            },
        )
        assert compact_source_session["agent_session_id"] == "sdk_session_source_verify"
        assert compact_source_session["turn_count_matches_summary"] is True
        assert compact_source_session["final_item_count_matches_summary"] is True
        assert compact_source_session["final_item_type_counts_match_summary"] is True
        assert compact_source_session["item_count_before_by_phase"] == {
            "session_initial": 0,
            "session_turn_2": 2,
        }
        assert compact_source_session["item_count_after_by_phase"] == {
            "session_initial": 2,
            "session_turn_2": 4,
        }
        assert compact_source_session["stateful_second_turn_observed"] is True
        assert compact_source_session["session_growth_observed"] is True
        assert compact_source_session["db_path_recorded"] is True
        assert "db_path" not in compact_source_session
        assert "final_items_preview" not in compact_source_session
        matrix_validation = matrix_module.validate_smoke_result(
            "approval",
            0,
            {
                "status": "completed",
                "external_api_called": True,
                "summary": {
                    "final_ok": True,
                    "raw_response_count": 2,
                    "raw_response_usage_totals": {"total_tokens": 7},
                    "approval_request_count": 1,
                    "approval_decision_count": 1,
                    "approval_decision_counts": {"approved": 1},
                    "phase_names": ["initial", "after_approval"],
                    "phase_interruption_counts": {"initial": 1, "after_approval": 0},
                    "tool_call_count": 1,
                    "tool_output_count": 1,
                },
            },
        )
        assert matrix_validation["passed"] is True
        provider_probe_validation = matrix_module.validate_provider_probe(
            {
                "status": "completed",
                "http_status": 200,
                "provider": {
                    "provider": "openai",
                    "api_style": "responses",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4.1-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "supports_tool_calling": True,
                    "supports_stateful_responses": True,
                },
                "endpoint": "https://api.openai.com/v1/models",
                "models_preview": ["gpt-4.1-mini"],
                "provider_request_attempted": True,
                "provider_response_received": True,
                "external_api_called": True,
                "agent_sdk_fit": {
                    "can_use_openai_agents_sdk_model_layer": True,
                    "requires_backend_owned_tool_execution": True,
                    "local_mode_requires_openai_compatible_tool_calls": False,
                    "stateful_response_ids_assumed": True,
                },
                "diagnostic_id": 99,
            }
        )
        assert provider_probe_validation["valid"] is True
        assert provider_probe_validation["status"] == "completed"
        assert provider_probe_validation["http_status"] == 200
        assert provider_probe_validation["endpoint"] == "https://api.openai.com/v1/models"
        assert provider_probe_validation["models_preview_count"] == 1
        assert provider_probe_validation["provider_request_attempted"] is True
        assert provider_probe_validation["provider_response_received"] is True
        assert provider_probe_validation["external_api_called"] is True
        assert provider_probe_validation["provider"]["model"] == "gpt-4.1-mini"
        assert provider_probe_validation["provider"]["api_key_env"] == "OPENAI_API_KEY"
        assert provider_probe_validation["agent_sdk_fit"]["stateful_response_ids_assumed"] is True
        failed_provider_probe_validation = matrix_module.validate_provider_probe(
            {
                "status": "not_connected",
                "http_status": 0,
                "provider_request_attempted": True,
                "provider_response_received": False,
                "external_api_called": False,
                "provider": {
                    "provider": "openai",
                    "api_style": "responses",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4.1-mini",
                    "api_key_env": "OPENAI_API_KEY",
                },
                "endpoint": "https://api.openai.com/v1/models",
            }
        )
        assert failed_provider_probe_validation["valid"] is False
        assert failed_provider_probe_validation["status"] == "not_connected"
        assert failed_provider_probe_validation["http_status"] == 0
        assert "probe_completed" in failed_provider_probe_validation["failed_checks"]
        missing_approval_validation = matrix_module.validate_smoke_result(
            "approval",
            0,
            {
                "status": "completed",
                "external_api_called": True,
                "summary": {
                    "final_ok": True,
                    "raw_response_count": 2,
                    "approval_request_count": 1,
                    "approval_decision_count": 0,
                    "phase_names": ["initial"],
                },
            },
        )
        assert missing_approval_validation["passed"] is False
        assert "approval_decision_recorded" in missing_approval_validation["failed_checks"]
        approval_projection_validation = matrix_module.validate_readback_projection(
            "approval",
            {
                "sdk_events_summary": {
                    "summary_requested": True,
                    "agent_model_response_count": 2,
                    "agent_model_response_provider_counts": {"openai": 2},
                    "agent_model_response_api_style_counts": {"responses": 2},
                    "final_output_count": 1,
                    "agent_approval_count": 2,
                    "agent_phase_count": 2,
                    "tool_progress_count": 1,
                },
                "client_transcript_summary": {
                    "summary_requested": True,
                    "agent_model_response_count": 2,
                    "agent_model_response_provider_counts": {"openai": 2},
                    "agent_model_response_api_style_counts": {"responses": 2},
                    "agent_approval_count": 2,
                    "agent_phase_count": 2,
                    "tool_progress_count": 1,
                },
            },
        )
        assert approval_projection_validation["valid"] is True
        missing_projection_validation = matrix_module.validate_readback_projection(
            "stream_handoff",
            {
                "sdk_events_summary": {
                    "summary_requested": True,
                    "agent_model_response_count": 2,
                    "agent_model_response_provider_counts": {"openai": 2},
                    "agent_model_response_api_style_counts": {"responses": 2},
                    "final_output_count": 1,
                    "agent_stream_event_count": 30,
                    "agent_handoff_count": 2,
                    "agent_stream_event_name_counts": {"handoff_requested": 1},
                    "agent_stream_tool_name_counts": {},
                },
                "client_transcript_summary": {
                    "summary_requested": True,
                    "agent_model_response_count": 2,
                    "agent_model_response_provider_counts": {"openai": 2},
                    "agent_model_response_api_style_counts": {"responses": 2},
                    "agent_stream_event_count": 30,
                    "agent_handoff_count": 2,
                    "agent_stream_tool_name_counts": {},
                },
            },
        )
        assert missing_projection_validation["valid"] is False
        assert "sdk_handoff_completed_projected" in missing_projection_validation["failed_checks"]
        assert "sdk_handoff_tool_name_projected" in missing_projection_validation["failed_checks"]
        scenario_event_replay = {
            "status": "completed",
            "source": "harness_event_sse_replay",
            "task_id": "task_matrix_verify",
            "after": 0,
            "events": [
                {
                    "id": "1",
                    "event": "harness_event",
                    "data": {"event_type": "task.queued", "status": "queued", "actor": "api"},
                },
                {
                    "id": "2",
                    "event": "harness_event",
                    "data": {"event_type": "task.paused", "status": "paused", "actor": "api"},
                },
                {
                    "id": "3",
                    "event": "harness_event",
                    "data": {"event_type": "artifact.recorded", "status": "completed", "actor": "artifact"},
                },
                {
                    "id": "4",
                    "event": "harness_event",
                    "data": {"event_type": "observation.recorded", "status": "completed", "actor": "tool"},
                },
                {
                    "id": "5",
                    "event": "harness_event",
                    "data": {"event_type": "observation.recorded", "status": "completed", "actor": "tool"},
                },
                {
                    "id": "6",
                    "event": "harness_event",
                    "data": {"event_type": "tool_call.recorded", "status": "completed", "actor": "tool"},
                },
                {
                    "id": "7",
                    "event": "harness_event",
                    "data": {"event_type": "tool_call.status_updated", "status": "completed", "actor": "tool"},
                },
            ],
            "event_count": 7,
            "keepalive_count": 0,
            "timed_out": False,
            "next_after": 7,
        }
        scenario_event_replay_validation = matrix_module.validate_trace_task_event_replay(
            scenario_event_replay,
            {
                "artifact_count": 1,
                "observation_count": 2,
                "tool_call_count": 1,
                "tool_call_status_update_expected_count": 1,
            },
            expected_task_id="task_matrix_verify",
        )
        assert scenario_event_replay_validation["valid"] is True
        assert scenario_event_replay_validation["task_binding"]["expected_task_id"] == "task_matrix_verify"
        assert scenario_event_replay_validation["checks"]["event_replay_task_id_matches"] is True
        assert scenario_event_replay_validation["expected_event_counts"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 1,
        }
        assert scenario_event_replay_validation["observed_event_counts"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 1,
        }
        assert scenario_event_replay_validation["event_replay_summary"]["sse_event_name_counts"] == {
            "harness_event": 7
        }
        assert scenario_event_replay_validation["event_replay_summary"]["event_status_counts"] == {
            "completed": 5,
            "paused": 1,
            "queued": 1,
        }
        assert scenario_event_replay_validation["event_replay_summary"]["event_actor_counts"] == {
            "api": 2,
            "artifact": 1,
            "tool": 4,
        }
        assert scenario_event_replay_validation["event_replay_count_map_totals"] == {
            "event_actor_counts": 7,
            "event_status_counts": 7,
            "event_type_counts": 7,
            "sse_event_name_counts": 7,
        }
        assert scenario_event_replay_validation["checks"][
            "event_replay_sse_event_name_count_total_matches_event_count"
        ] is True
        assert scenario_event_replay_validation["checks"][
            "event_replay_event_type_count_total_matches_event_count"
        ] is True
        assert scenario_event_replay_validation["checks"][
            "event_replay_event_status_count_total_matches_event_count"
        ] is True
        assert scenario_event_replay_validation["checks"][
            "event_replay_event_actor_count_total_matches_event_count"
        ] is True
        matrix_dynamic_fetch_calls: list[tuple[str, str, int | None]] = []
        original_matrix_event_request = matrix_module.harness_api.request

        def fake_matrix_event_request(
            request_args: argparse.Namespace,
            method: str,
            path: str,
            body: Any = None,
        ) -> dict[str, Any]:
            del body
            matrix_dynamic_fetch_calls.append((method, path, getattr(request_args, "max_events", None)))
            if method == "SSE" and path == "/tasks/task_matrix_large/events?after=0":
                return {
                    "status": "completed",
                    "source": "harness_event_sse_replay",
                    "task_id": "task_matrix_large",
                    "after": 0,
                    "events": [],
                    "event_count": 820,
                    "timed_out": False,
                    "next_after": 820,
                    "sse_event_name_counts": {"harness_event": 820},
                    "event_type_counts": {
                        "artifact.recorded": 1,
                        "observation.recorded": 700,
                        "task.updated": 118,
                        "tool_call.recorded": 1,
                    },
                    "event_status_counts": {"completed": 820},
                    "event_actor_counts": {
                        "api": 118,
                        "artifact": 1,
                        "tool": 701,
                    },
                }
            raise AssertionError(f"unexpected matrix event request: {method} {path}")

        try:
            matrix_module.harness_api.request = fake_matrix_event_request
            matrix_dynamic_event_replay = matrix_module.fetch_task_event_replay(
                argparse.Namespace(
                    base_url="http://verify",
                    client_id="verify-client",
                    api_key=None,
                    dry_run=False,
                ),
                "task_matrix_large",
                readback={
                    "evidence_counts": {
                        "artifacts": 1,
                        "observations": 700,
                        "tool_calls": 1,
                    },
                    "sdk_events_summary": {"raw_event_count": 820},
                },
            )
        finally:
            matrix_module.harness_api.request = original_matrix_event_request
        assert ("SSE", "/tasks/task_matrix_large/events?after=0", 845) in matrix_dynamic_fetch_calls
        assert matrix_dynamic_event_replay["fetch_plan"]["source"] == (
            "agents_sdk_smoke_matrix_event_replay_fetch_plan"
        )
        assert matrix_dynamic_event_replay["fetch_plan"]["max_events"] == 845
        matrix_dynamic_event_replay_validation = matrix_module.validate_trace_task_event_replay(
            matrix_dynamic_event_replay,
            {
                "artifact_count": 1,
                "observation_count": 700,
                "tool_call_count": 1,
                "tool_call_status_update_expected_count": 0,
            },
            expected_task_id="task_matrix_large",
        )
        assert matrix_dynamic_event_replay_validation["valid"] is True
        assert matrix_dynamic_event_replay_validation["check_count"] == 15
        assert matrix_dynamic_event_replay_validation["event_replay_count_map_totals"] == {
            "event_actor_counts": 820,
            "event_status_counts": 820,
            "event_type_counts": 820,
            "sse_event_name_counts": 820,
        }
        assert matrix_dynamic_event_replay_validation["event_replay_fetch_plan"]["max_events"] == 845
        assert matrix_dynamic_event_replay_validation["checks"][
            "event_replay_fetch_plan_covers_expected_raw_event_count"
        ] is True
        assert matrix_dynamic_event_replay_validation["checks"][
            "event_replay_not_truncated_by_fetch_limit"
        ] is True
        matrix_dynamic_fetch_plan_compact = matrix_module.event_replay_fetch_plan_compact_validation(
            matrix_dynamic_event_replay_validation
        )
        assert matrix_dynamic_fetch_plan_compact["fetch_plan"]["basis"] == "sdk_events_raw_event_count"
        assert matrix_dynamic_fetch_plan_compact["fetch_plan"]["max_events"] == 845
        assert matrix_dynamic_fetch_plan_compact["fetch_plan"]["expected_raw_event_count"] == 820
        assert matrix_dynamic_fetch_plan_compact["checks"] == {
            "event_replay_fetch_plan_covers_expected_raw_event_count": True,
            "event_replay_not_truncated_by_fetch_limit": True,
        }
        truncated_matrix_dynamic_event_replay = json.loads(json.dumps(matrix_dynamic_event_replay))
        truncated_matrix_dynamic_event_replay["event_count"] = 100
        truncated_matrix_dynamic_validation = matrix_module.validate_trace_task_event_replay(
            truncated_matrix_dynamic_event_replay,
            {
                "artifact_count": 1,
                "observation_count": 700,
                "tool_call_count": 1,
                "tool_call_status_update_expected_count": 0,
            },
            expected_task_id="task_matrix_large",
        )
        assert truncated_matrix_dynamic_validation["valid"] is False
        assert "event_replay_not_truncated_by_fetch_limit" in (
            truncated_matrix_dynamic_validation["failed_checks"]
        )
        broken_scenario_event_replay = json.loads(json.dumps(scenario_event_replay))
        broken_scenario_event_replay["events"] = [
            item
            for item in broken_scenario_event_replay["events"]
            if item["data"]["event_type"] != "tool_call.recorded"
        ]
        broken_scenario_event_replay_validation = matrix_module.validate_trace_task_event_replay(
            broken_scenario_event_replay,
            {
                "artifact_count": 1,
                "observation_count": 2,
                "tool_call_count": 1,
                "tool_call_status_update_expected_count": 1,
            },
            expected_task_id="task_matrix_verify",
        )
        assert broken_scenario_event_replay_validation["valid"] is False
        assert "event_replay_covers_tool_call_recorded" in broken_scenario_event_replay_validation["failed_checks"]
        broken_tool_status_event_replay = json.loads(json.dumps(scenario_event_replay))
        broken_tool_status_event_replay["events"] = [
            item
            for item in broken_tool_status_event_replay["events"]
            if item["data"]["event_type"] != "tool_call.status_updated"
        ]
        broken_tool_status_event_replay_validation = matrix_module.validate_trace_task_event_replay(
            broken_tool_status_event_replay,
            {
                "artifact_count": 1,
                "observation_count": 2,
                "tool_call_count": 1,
                "tool_call_status_update_expected_count": 1,
            },
            expected_task_id="task_matrix_verify",
        )
        assert broken_tool_status_event_replay_validation["valid"] is False
        assert (
            "event_replay_covers_tool_call_status_updated"
            in broken_tool_status_event_replay_validation["failed_checks"]
        )
        assert broken_tool_status_event_replay_validation["expected_event_counts"][
            "tool_call.status_updated"
        ] == 1
        assert broken_tool_status_event_replay_validation["observed_event_counts"][
            "tool_call.status_updated"
        ] == 0
        wrong_task_scenario_event_replay_validation = matrix_module.validate_trace_task_event_replay(
            scenario_event_replay,
            {
                "artifact_count": 1,
                "observation_count": 2,
                "tool_call_count": 1,
                "tool_call_status_update_expected_count": 1,
            },
            expected_task_id="task_wrong_scenario",
        )
        assert wrong_task_scenario_event_replay_validation["valid"] is False
        assert "event_replay_task_id_matches" in wrong_task_scenario_event_replay_validation["failed_checks"]
        missing_task_scenario_event_replay = json.loads(json.dumps(scenario_event_replay))
        missing_task_scenario_event_replay.pop("task_id", None)
        missing_task_scenario_event_replay_validation = matrix_module.validate_trace_task_event_replay(
            missing_task_scenario_event_replay,
            {
                "artifact_count": 1,
                "observation_count": 2,
                "tool_call_count": 1,
                "tool_call_status_update_expected_count": 1,
            },
            expected_task_id="task_matrix_verify",
        )
        assert missing_task_scenario_event_replay_validation["valid"] is False
        assert "event_replay_task_id_matches" in missing_task_scenario_event_replay_validation["failed_checks"]
        matrix_summary = matrix_module.summarize_matrix(
            [
                {
                    "scenario": "approval",
                    "status": "completed",
                    "passed": True,
                    "validation": matrix_validation,
                    "summary": {"raw_response_usage_totals": {"total_tokens": 7}},
                    "publish_readback": {
                        "status": "completed",
                        "task_id": "task_matrix_verify",
                        "result_summary": {"tool_call_status_update_expected_count": 1},
                        "validation": {"valid": True},
                        "scenario_projection_validation": {"valid": True},
                        "scenario_event_replay_validation": scenario_event_replay_validation,
                        "sdk_events_summary": {
                            "summary_requested": True,
                            "agent_model_response_count": 1,
                            "agent_model_response_provider_counts": {"openai": 1},
                            "agent_model_response_api_style_counts": {"responses": 1},
                            "agent_guardrail_count": 1,
                            "agent_run_error_count": 1,
                            "tool_progress_count": 2,
                            "agent_stream_event_count": 5,
                            "agent_stream_event_name_counts": {
                                "handoff_requested": 1,
                                "handoff_occured": 1,
                            },
                            "agent_stream_tool_name_counts": {
                                "transfer_to_harness_specialist": 2,
                            },
                            "final_output_count": 1,
                        },
                        "client_transcript_summary": {
                            "summary_requested": True,
                            "agent_model_response_count": 1,
                            "agent_model_response_provider_counts": {"openai": 1},
                            "agent_model_response_api_style_counts": {"responses": 1},
                            "agent_guardrail_count": 1,
                            "agent_run_error_count": 1,
                            "tool_progress_count": 1,
                            "agent_stream_event_count": 5,
                            "agent_stream_event_name_counts": {
                                "handoff_requested": 1,
                                "handoff_occured": 1,
                            },
                            "agent_stream_tool_name_counts": {
                                "transfer_to_harness_specialist": 2,
                            },
                            "final_output_count": 1,
                        },
                    },
                }
            ],
            publish_readback=True,
            provider_probe_validation=provider_probe_validation,
        )
        assert matrix_summary["status"] == "completed"
        assert matrix_summary["external_api_called"] is True
        assert matrix_summary["scenario_external_api_called"] == {"approval": True}
        assert matrix_summary["api_usage_totals"]["total_tokens"] == 7
        assert matrix_summary["scenario_api_usage_totals"] == {"approval": {"total_tokens": 7}}
        expected_projection_maps = {
            "approval": {
                "agent_model_response_api_style_counts": {"responses": 1},
                "agent_model_response_provider_counts": {"openai": 1},
            }
        }
        expected_projection_map_totals = {
            "agent_model_response_api_style_counts": {"responses": 1},
            "agent_model_response_provider_counts": {"openai": 1},
        }
        expected_guardrail_projection_summaries = {
            "approval": {
                "sdk_events": {
                    "agent_guardrail_count": 1,
                    "agent_run_error_count": 1,
                    "final_output_count": 1,
                },
                "client_transcript": {
                    "agent_guardrail_count": 1,
                    "agent_run_error_count": 1,
                    "final_output_count": 1,
                },
                "guardrail_projected": True,
                "run_error_projected": True,
                "final_output_projected": True,
            }
        }
        expected_count_total_checks = {
            "event_replay_sse_event_name_count_total_matches_event_count": True,
            "event_replay_event_type_count_total_matches_event_count": True,
            "event_replay_event_status_count_total_matches_event_count": True,
            "event_replay_event_actor_count_total_matches_event_count": True,
        }
        assert matrix_summary["scenario_sdk_events_projection_maps"] == expected_projection_maps
        assert matrix_summary["scenario_client_transcript_projection_maps"] == expected_projection_maps
        assert matrix_summary["scenario_guardrail_projection_summaries"] == (
            expected_guardrail_projection_summaries
        )
        assert matrix_summary["scenario_guardrail_tripwire_summaries"] == {}
        assert matrix_summary["sdk_events_projection_map_totals"] == expected_projection_map_totals
        assert matrix_summary["client_transcript_projection_map_totals"] == expected_projection_map_totals
        assert matrix_summary["projection_map_deltas"] == {}
        assert matrix_summary["projection_map_delta_status"] == "no_deltas"
        matrix_compact_summary = matrix_module.matrix_run_compact_summary(
            {
                "status": "completed",
                "run_id": "verify-usage-compact",
                "model": "gpt-4.1-mini",
                "provider": "openai",
                "api_style": "responses",
                "base_url": "http://verify",
                "client_id": "verify-client",
                "scenario_count": 1,
                "requested_scenarios": ["approval"],
                "summary": matrix_summary,
            }
        )
        assert matrix_compact_summary["valid"] is True
        assert matrix_compact_summary["scenario_api_usage_totals"] == {
            "approval": {"total_tokens": 7}
        }
        assert matrix_compact_summary["api_usage_totals"] == {"total_tokens": 7}
        assert matrix_compact_summary["scenario_sdk_events_projection_maps"] == expected_projection_maps
        assert matrix_compact_summary["scenario_client_transcript_projection_maps"] == expected_projection_maps
        assert matrix_compact_summary["scenario_guardrail_projection_summaries"] == (
            expected_guardrail_projection_summaries
        )
        assert matrix_compact_summary["scenario_readback_guardrail_projection_summaries"] == {}
        assert matrix_compact_summary["scenario_guardrail_tripwire_summaries"] == {}
        assert matrix_compact_summary["sdk_events_projection_map_totals"] == expected_projection_map_totals
        assert matrix_compact_summary["client_transcript_projection_map_totals"] == expected_projection_map_totals
        assert matrix_compact_summary["projection_map_deltas"] == {}
        assert matrix_compact_summary["projection_map_delta_status"] == "no_deltas"
        stale_failure_compact_summary = matrix_module.matrix_run_compact_summary(
            {
                "status": "completed",
                "run_id": "verify-stale-failure-compact",
                "model": "gpt-4.1-mini",
                "provider": "openai",
                "api_style": "responses",
                "base_url": "http://verify",
                "client_id": "verify-client",
                "scenario_count": 1,
                "requested_scenarios": ["approval"],
                "summary": {
                    "status": "completed",
                    "passed_count": 1,
                    "failed_count": 0,
                    "aggregate_failed_reasons": [],
                    "scenario_failed_checks": {"approval": ["stale_failure_marker"]},
                },
            }
        )
        assert stale_failure_compact_summary["status"] == "completed"
        assert stale_failure_compact_summary["valid"] is False
        assert stale_failure_compact_summary["scenario_failed_checks"] == {
            "approval": ["stale_failure_marker"]
        }
        tripwire_compact = matrix_module.scenario_guardrail_tripwire_summary_maps(
            [
                {
                    "scenario": "guardrail_tripwire",
                    "status": "guardrail_tripwire_triggered",
                    "error_type": "OutputGuardrailTripwireTriggered",
                    "error": "Guardrail OutputGuardrail triggered tripwire",
                    "summary": {
                        "final_ok": True,
                        "marker_observed": True,
                        "output_guardrail_count": 1,
                        "guardrail_tripwire_count": 1,
                        "guardrail_tripwire_triggered": True,
                    },
                    "validation": {
                        "checks": {
                            "guardrail_tripwire_error_recorded": True,
                            "guardrail_tripwire_result_recorded": True,
                            "guardrail_tripwire_triggered": True,
                        }
                    },
                    "output_guardrail_results": [
                        {
                            "class": "OutputGuardrailResult",
                            "guardrail": {
                                "class": "OutputGuardrail",
                                "name": "harness_tripwire_guardrail",
                            },
                            "output": {
                                "tripwire_triggered": True,
                                "output_info": {
                                    "status": "blocked",
                                    "reason": "intentional_live_smoke_tripwire",
                                },
                            },
                            "agent": {"name": "Harness Tripwire Agent"},
                            "agent_output": "AGENTS_SDK_GUARDRAIL_TRIPWIRE_FINAL_OK",
                        }
                    ],
                }
            ]
        )
        assert tripwire_compact["guardrail_tripwire"]["error_type"] == (
            "OutputGuardrailTripwireTriggered"
        )
        assert tripwire_compact["guardrail_tripwire"]["guardrail_tripwire_triggered"] is True
        assert tripwire_compact["guardrail_tripwire"]["tripwire_guardrail_names"] == [
            "harness_tripwire_guardrail"
        ]
        assert tripwire_compact["guardrail_tripwire"]["tripwire_reasons"] == [
            "intentional_live_smoke_tripwire"
        ]
        assert tripwire_compact["guardrail_tripwire"]["validation_checks"] == {
            "guardrail_tripwire_error_recorded": True,
            "guardrail_tripwire_result_recorded": True,
            "guardrail_tripwire_triggered": True,
        }
        guardrail_result_fixture = {
            "scenario": "guardrail",
            "status": "completed",
            "passed": True,
            "external_api_called": True,
            "validation": {
                "status": "completed",
                "passed": True,
                "failed_checks": [],
                "checks": {"external_api_called": True},
            },
            "publish_readback": {
                "status": "completed",
                "task_id": "task_guardrail_verify",
                "result_summary": {
                    "status": "completed",
                    "tool_call_count": 0,
                    "observation_count": 5,
                    "artifact_count": 2,
                    "tool_call_status_update_expected_count": 0,
                    "tool_call_name_counts": {},
                    "observation_tool_counts": {
                        "agent_run_summary": 1,
                        "agent_run_phase": 1,
                        "agent_model_response": 1,
                        "agent_guardrail_result": 1,
                        "agent_final_output": 1,
                    },
                    "status_counts": {"completed": 5},
                    "call_ids": [],
                    "tool_call_pks": [],
                    "observation_ids": [801, 802, 803, 804, 805],
                },
                "validation": {
                    "valid": True,
                    "check_count": 52,
                    "checks": {
                        "artifact_count_covers_result_summary": True,
                        "observation_count_covers_result_summary": True,
                        "tool_call_count_covers_result_summary": True,
                    },
                    "evidence_tool_call_name_counts": {},
                    "evidence_observation_tool_counts": {
                        "agent_run_summary": 1,
                        "agent_run_phase": 1,
                        "agent_model_response": 1,
                        "agent_guardrail_result": 1,
                        "agent_final_output": 1,
                    },
                },
                "scenario_projection_validation": {"valid": True, "check_count": 13},
                "scenario_event_replay_validation": {"valid": True, "check_count": 15},
                "sdk_events_summary": {
                    "summary_requested": True,
                    "task_id": "task_guardrail_verify",
                    "status": "completed",
                    "agent_guardrail_count": 1,
                    "agent_run_error_count": 0,
                    "final_output_count": 1,
                },
                "client_transcript_summary": {
                    "summary_requested": True,
                    "task_id": "task_guardrail_verify",
                    "status": "completed",
                    "agent_guardrail_count": 1,
                    "agent_run_error_count": 0,
                    "final_output_count": 1,
                    "item_type_counts": {
                        "agent_guardrail": 1,
                        "final_output": 1,
                    },
                },
            },
        }
        guardrail_tripwire_result_fixture = {
            "scenario": "guardrail_tripwire",
            "status": "guardrail_tripwire_triggered",
            "passed": True,
            "external_api_called": True,
            "validation": {
                "status": "guardrail_tripwire_triggered",
                "passed": True,
                "failed_checks": [],
                "checks": {
                    "external_api_called": True,
                    "guardrail_tripwire_error_recorded": True,
                    "guardrail_tripwire_result_recorded": True,
                    "guardrail_tripwire_triggered": True,
                },
            },
            "publish_readback": {
                "status": "completed",
                "task_id": "task_guardrail_tripwire_verify",
                "result_summary": {
                    "status": "completed",
                    "tool_call_count": 0,
                    "observation_count": 6,
                    "artifact_count": 2,
                    "tool_call_status_update_expected_count": 0,
                    "tool_call_name_counts": {},
                    "observation_tool_counts": {
                        "agent_run_summary": 1,
                        "agent_run_error": 1,
                        "agent_run_phase": 1,
                        "agent_model_response": 1,
                        "agent_guardrail_result": 1,
                        "agent_final_output": 1,
                    },
                    "status_counts": {"failed": 3, "completed": 3},
                    "call_ids": [],
                    "tool_call_pks": [],
                    "observation_ids": [901, 902, 903, 904, 905, 906],
                },
                "validation": {
                    "valid": True,
                    "check_count": 52,
                    "checks": {
                        "artifact_count_covers_result_summary": True,
                        "observation_count_covers_result_summary": True,
                        "tool_call_count_covers_result_summary": True,
                    },
                    "evidence_tool_call_name_counts": {},
                    "evidence_observation_tool_counts": {
                        "agent_run_summary": 1,
                        "agent_run_error": 1,
                        "agent_run_phase": 1,
                        "agent_model_response": 1,
                        "agent_guardrail_result": 1,
                        "agent_final_output": 1,
                    },
                },
                "scenario_projection_validation": {"valid": True, "check_count": 13},
                "scenario_event_replay_validation": {"valid": True, "check_count": 15},
                "sdk_events_summary": {
                    "summary_requested": True,
                    "task_id": "task_guardrail_tripwire_verify",
                    "status": "guardrail_tripwire_triggered",
                    "agent_guardrail_count": 1,
                    "agent_run_error_count": 1,
                    "final_output_count": 1,
                },
                "client_transcript_summary": {
                    "summary_requested": True,
                    "task_id": "task_guardrail_tripwire_verify",
                    "status": "guardrail_tripwire_triggered",
                    "agent_guardrail_count": 1,
                    "agent_run_error_count": 1,
                    "final_output_count": 1,
                    "item_type_counts": {
                        "agent_guardrail": 1,
                        "agent_run_error": 1,
                        "final_output": 1,
                    },
                },
            },
        }
        guardrail_summary_fixture = matrix_module.summarize_matrix(
            [guardrail_result_fixture, guardrail_tripwire_result_fixture],
            publish_readback=True,
        )
        guardrail_compact_summary = matrix_module.matrix_run_compact_summary(
            {
                "status": "completed",
                "run_id": "verify-guardrail-compact",
                "model": "gpt-4.1-mini",
                "provider": "openai",
                "api_style": "responses",
                "base_url": "http://verify/api/harness/v1",
                "client_id": "verify-client",
                "scenario_count": 2,
                "requested_scenarios": ["guardrail", "guardrail_tripwire"],
                "summary": guardrail_summary_fixture,
                "results": [guardrail_result_fixture, guardrail_tripwire_result_fixture],
            }
        )
        guardrail_evidence = guardrail_compact_summary[
            "scenario_guardrail_evidence_summaries"
        ]["guardrail"]
        assert guardrail_evidence["result_observation_count"] == 5
        assert guardrail_evidence["result_observation_tool_counts"][
            "agent_guardrail_result"
        ] == 1
        assert guardrail_evidence["expected_guardrail_result_observation_recorded"] is True
        assert (
            guardrail_evidence[
                "evidence_expected_guardrail_result_observation_recorded"
            ]
            is True
        )
        assert guardrail_evidence["expected_tripwire_run_error_observation_recorded"] is None
        assert guardrail_evidence["result_observation_id_count"] == 5
        assert "observation_ids" not in guardrail_evidence
        tripwire_evidence = guardrail_compact_summary[
            "scenario_guardrail_evidence_summaries"
        ]["guardrail_tripwire"]
        assert tripwire_evidence["tripwire_expected"] is True
        assert tripwire_evidence["result_observation_count"] == 6
        assert tripwire_evidence["result_observation_tool_counts"]["agent_run_error"] == 1
        assert tripwire_evidence["expected_guardrail_result_observation_recorded"] is True
        assert tripwire_evidence["expected_tripwire_run_error_observation_recorded"] is True
        assert tripwire_evidence[
            "evidence_expected_tripwire_run_error_observation_recorded"
        ] is True
        assert tripwire_evidence["tripwire_failed_status_recorded"] is True
        assert tripwire_evidence["failed_status_count"] == 3
        assert tripwire_evidence["result_observation_id_count"] == 6
        assert "observation_ids" not in tripwire_evidence
        assert guardrail_compact_summary["scenario_readback_guardrail_evidence_summaries"][
            "guardrail"
        ] == guardrail_evidence
        assert guardrail_compact_summary["scenario_readback_guardrail_evidence_summaries"][
            "guardrail_tripwire"
        ] == tripwire_evidence
        guardrail_rollup = matrix_module.matrix_scenario_rollup(
            {
                "run_id": "verify-guardrail-compact",
                "summary": guardrail_summary_fixture,
                "results": [guardrail_result_fixture, guardrail_tripwire_result_fixture],
            }
        )
        assert guardrail_rollup["scenario_guardrail_evidence_summaries"][
            "guardrail"
        ] == guardrail_evidence
        assert guardrail_rollup["scenario_guardrail_evidence_summaries"][
            "guardrail_tripwire"
        ] == tripwire_evidence
        assert guardrail_rollup["scenarios"][0]["guardrail_evidence_summary"] == (
            guardrail_evidence
        )
        assert guardrail_rollup["scenarios"][1]["guardrail_evidence_summary"] == (
            tripwire_evidence
        )
        published_guardrail_summary = (
            matrix_module.published_scenario_readback_compact_summary(
                {
                    "results": [
                        guardrail_result_fixture["publish_readback"] | {"scenario": "guardrail"},
                        guardrail_tripwire_result_fixture["publish_readback"]
                        | {"scenario": "guardrail_tripwire"},
                    ]
                }
            )
        )
        assert published_guardrail_summary is not None
        assert published_guardrail_summary[
            "guardrail_evidence_summaries_by_scenario"
        ]["guardrail"] == guardrail_evidence
        assert published_guardrail_summary[
            "guardrail_evidence_summaries_by_scenario"
        ]["guardrail_tripwire"] == tripwire_evidence
        session_result_fixture = {
            "scenario": "session",
            "status": "completed",
            "passed": True,
            "publish_readback": {
                "status": "completed",
                "task_id": "task_session_verify",
                "result_summary": {
                    "status": "completed",
                    "artifact_count": 1,
                    "tool_call_count": 0,
                    "observation_count": 7,
                    "observation_ids": [501, 502, 503, 504, 505, 506, 507],
                    "tool_call_pks": [],
                    "call_ids": [],
                    "tool_call_name_counts": {},
                    "observation_tool_counts": {
                        "agent_run_summary": 1,
                        "agent_run_phase": 2,
                        "agent_model_response": 2,
                        "agent_session": 1,
                        "agent_final_output": 1,
                    },
                    "status_counts": {"completed": 7},
                },
                "validation": {
                    "valid": True,
                    "check_count": 12,
                    "checks": {
                        "artifact_count_covers_result_summary": True,
                        "observation_count_covers_result_summary": True,
                        "tool_call_count_covers_result_summary": True,
                    },
                    "evidence_tool_call_name_counts": {},
                    "evidence_observation_tool_counts": {
                        "agent_run_summary": 1,
                        "agent_run_phase": 2,
                        "agent_model_response": 2,
                        "agent_session": 1,
                        "agent_final_output": 1,
                    },
                },
                "scenario_projection_validation": {"valid": True, "check_count": 8},
                "scenario_event_replay_validation": {"valid": True, "check_count": 14},
                "agent_session_summaries": [
                    {
                        "agent_session_id": "sdk_session_evidence_verify",
                        "backend": "SQLiteSession",
                        "turn_count": 2,
                        "turn_count_from_turns": 2,
                        "final_item_count": 4,
                        "final_item_types": ["user", "message", "user", "message"],
                        "final_item_type_counts": {"message": 2, "user": 2},
                        "turn_phases": ["session_initial", "session_turn_2"],
                        "status": "completed",
                        "evidence_refs": [
                            "artifact:agent_trace:verify_session.json",
                            "agent_session",
                        ],
                        "db_path": "/tmp/raw-session.sqlite3",
                        "final_items_preview": ["raw preview should not be copied"],
                    }
                ],
                "sdk_events_summary": {
                    "summary_requested": True,
                    "task_id": "task_session_verify",
                    "status": "completed",
                    "agent_session_count": 1,
                    "agent_phase_count": 2,
                    "agent_model_response_count": 2,
                    "final_output_count": 1,
                },
                "client_transcript_summary": {
                    "summary_requested": True,
                    "task_id": "task_session_verify",
                    "status": "completed",
                    "agent_session_count": 1,
                    "agent_phase_count": 2,
                    "agent_model_response_count": 2,
                    "final_output_count": 1,
                    "item_type_counts": {
                        "agent_session": 1,
                        "agent_phase": 2,
                        "agent_model_response": 2,
                        "final_output": 1,
                    },
                },
            },
        }
        session_summary_fixture = matrix_module.summarize_matrix(
            [session_result_fixture],
            publish_readback=True,
        )
        session_compact_summary = matrix_module.matrix_run_compact_summary(
            {
                "status": "completed",
                "run_id": "verify-session-compact",
                "model": "gpt-4.1-mini",
                "provider": "openai",
                "api_style": "responses",
                "base_url": "http://verify/api/harness/v1",
                "client_id": "verify-client",
                "scenario_count": 1,
                "requested_scenarios": ["session"],
                "summary": session_summary_fixture,
                "results": [session_result_fixture],
            }
        )
        session_evidence = session_compact_summary[
            "scenario_session_evidence_summaries"
        ]["session"]
        assert session_evidence["result_observation_count"] == 7
        assert session_evidence["result_observation_tool_counts"]["agent_session"] == 1
        assert session_evidence["expected_agent_session_observation_recorded"] is True
        assert (
            session_evidence[
                "evidence_expected_agent_session_observation_recorded"
            ]
            is True
        )
        assert session_evidence["readback_agent_session_summary_count"] == 1
        assert session_evidence["readback_agent_session_backends"] == ["SQLiteSession"]
        assert session_evidence["readback_agent_session_turn_count_max"] == 2
        assert session_evidence["readback_agent_session_final_item_count_max"] == 4
        assert session_evidence["readback_agent_session_turn_phases"] == [
            "session_initial",
            "session_turn_2",
        ]
        assert session_evidence["readback_stateful_session_recorded"] is True
        assert session_evidence["result_observation_id_count"] == 7
        assert "observation_ids" not in session_evidence
        serialized_session_evidence = json.dumps(session_evidence, sort_keys=True)
        assert "raw-session.sqlite3" not in serialized_session_evidence
        assert "raw preview should not be copied" not in serialized_session_evidence
        assert session_compact_summary["scenario_readback_session_evidence_summaries"][
            "session"
        ] == session_evidence
        session_rollup = matrix_module.matrix_scenario_rollup(
            {
                "run_id": "verify-session-compact",
                "summary": session_summary_fixture,
                "results": [session_result_fixture],
            }
        )
        assert session_rollup["scenario_session_evidence_summaries"][
            "session"
        ] == session_evidence
        assert session_rollup["scenarios"][0]["session_evidence_summary"] == (
            session_evidence
        )
        published_session_summary = (
            matrix_module.published_scenario_readback_compact_summary(
                {
                    "results": [
                        session_result_fixture["publish_readback"] | {"scenario": "session"}
                    ]
                }
            )
        )
        assert published_session_summary is not None
        assert published_session_summary[
            "session_evidence_summaries_by_scenario"
        ]["session"] == session_evidence
        stream_validation_checks = {
            "artifact_count_covers_result_summary": True,
            "observation_count_covers_result_summary": True,
            "tool_call_count_covers_result_summary": True,
            "sdk_agent_stream_event_matches_evidence": True,
            "transcript_agent_stream_event_matches_evidence": True,
            "sdk_agent_stream_event_type_counts_match_evidence": True,
            "sdk_agent_stream_event_name_counts_match_evidence": True,
            "sdk_agent_stream_raw_event_type_counts_match_evidence": True,
            "sdk_agent_stream_item_class_counts_match_evidence": True,
            "sdk_agent_stream_tool_name_counts_match_evidence": True,
            "transcript_agent_stream_event_type_counts_match_evidence": True,
            "transcript_agent_stream_event_name_counts_match_evidence": True,
            "transcript_agent_stream_raw_event_type_counts_match_evidence": True,
            "transcript_agent_stream_item_class_counts_match_evidence": True,
            "transcript_agent_stream_tool_name_counts_match_evidence": True,
        }
        stream_result_fixture = {
            "scenario": "stream",
            "status": "completed",
            "passed": True,
            "publish_readback": {
                "status": "completed",
                "task_id": "task_stream_verify",
                "result_summary": {
                    "status": "completed",
                    "artifact_count": 1,
                    "tool_call_count": 0,
                    "observation_count": 8,
                    "observation_ids": [601, 602, 603, 604, 605, 606, 607, 608],
                    "tool_call_pks": [],
                    "call_ids": [],
                    "tool_call_name_counts": {},
                    "observation_tool_counts": {
                        "agent_run_summary": 1,
                        "agent_run_phase": 1,
                        "agent_model_response": 1,
                        "agent_stream_event": 3,
                        "record_signal": 1,
                        "agent_final_output": 1,
                    },
                    "status_counts": {"completed": 8},
                },
                "validation": {
                    "valid": True,
                    "check_count": 15,
                    "checks": stream_validation_checks,
                    "evidence_tool_call_name_counts": {},
                    "evidence_observation_tool_counts": {
                        "agent_run_summary": 1,
                        "agent_run_phase": 1,
                        "agent_model_response": 1,
                        "agent_stream_event": 3,
                        "record_signal": 1,
                        "agent_final_output": 1,
                    },
                    "expected_stream_event_counts": {
                        "event_type": {
                            "raw_response_event": 1,
                            "run_item_stream_event": 2,
                        },
                        "event_name": {
                            "none": 1,
                            "tool_called": 1,
                            "tool_output": 1,
                        },
                        "raw_event_type": {"none": 2, "response.created": 1},
                        "item_class": {
                            "none": 1,
                            "ToolCallItem": 1,
                            "ToolCallOutputItem": 1,
                        },
                        "tool_name": {"none": 1, "record_signal": 2},
                    },
                },
                "scenario_projection_validation": {"valid": True, "check_count": 8},
                "scenario_event_replay_validation": {"valid": True, "check_count": 14},
                "sdk_events_summary": {
                    "summary_requested": True,
                    "task_id": "task_stream_verify",
                    "status": "completed",
                    "agent_stream_event_count": 3,
                    "agent_stream_event_type_counts": {
                        "raw_response_event": 1,
                        "run_item_stream_event": 2,
                    },
                    "agent_stream_event_name_counts": {
                        "none": 1,
                        "tool_called": 1,
                        "tool_output": 1,
                    },
                    "agent_stream_raw_event_type_counts": {
                        "none": 2,
                        "response.created": 1,
                    },
                    "agent_stream_item_class_counts": {
                        "none": 1,
                        "ToolCallItem": 1,
                        "ToolCallOutputItem": 1,
                    },
                    "agent_stream_tool_name_counts": {
                        "none": 1,
                        "record_signal": 2,
                    },
                },
                "client_transcript_summary": {
                    "summary_requested": True,
                    "task_id": "task_stream_verify",
                    "status": "completed",
                    "agent_stream_event_count": 3,
                    "agent_stream_event_type_counts": {
                        "raw_response_event": 1,
                        "run_item_stream_event": 2,
                    },
                    "agent_stream_event_name_counts": {
                        "none": 1,
                        "tool_called": 1,
                        "tool_output": 1,
                    },
                    "agent_stream_raw_event_type_counts": {
                        "none": 2,
                        "response.created": 1,
                    },
                    "agent_stream_item_class_counts": {
                        "none": 1,
                        "ToolCallItem": 1,
                        "ToolCallOutputItem": 1,
                    },
                    "agent_stream_tool_name_counts": {
                        "none": 1,
                        "record_signal": 2,
                    },
                    "item_type_counts": {"agent_stream_event": 3},
                },
            },
        }
        stream_handoff_result_fixture = {
            "scenario": "stream_handoff",
            "status": "completed",
            "passed": True,
            "publish_readback": {
                "status": "completed",
                "task_id": "task_stream_handoff_verify",
                "result_summary": {
                    "status": "completed",
                    "artifact_count": 1,
                    "tool_call_count": 0,
                    "observation_count": 10,
                    "observation_ids": [701, 702, 703, 704, 705, 706, 707, 708, 709, 710],
                    "tool_call_pks": [],
                    "call_ids": [],
                    "tool_call_name_counts": {},
                    "observation_tool_counts": {
                        "agent_run_summary": 1,
                        "agent_run_phase": 1,
                        "agent_model_response": 1,
                        "agent_stream_event": 4,
                        "agent_handoff": 2,
                        "agent_final_output": 1,
                    },
                    "status_counts": {"completed": 10},
                },
                "validation": {
                    "valid": True,
                    "check_count": 15,
                    "checks": stream_validation_checks,
                    "evidence_tool_call_name_counts": {},
                    "evidence_observation_tool_counts": {
                        "agent_run_summary": 1,
                        "agent_run_phase": 1,
                        "agent_model_response": 1,
                        "agent_stream_event": 4,
                        "agent_handoff": 2,
                        "agent_final_output": 1,
                    },
                    "expected_stream_event_counts": {
                        "event_type": {
                            "raw_response_event": 1,
                            "run_item_stream_event": 3,
                        },
                        "event_name": {
                            "none": 1,
                            "tool_called": 1,
                            "handoff_requested": 1,
                            "handoff_occured": 1,
                        },
                        "raw_event_type": {"none": 3, "response.created": 1},
                        "item_class": {
                            "none": 1,
                            "ToolCallItem": 1,
                            "HandoffCallItem": 1,
                            "HandoffOutputItem": 1,
                        },
                        "tool_name": {
                            "none": 1,
                            "transfer_to_harness_specialist": 3,
                        },
                    },
                },
                "scenario_projection_validation": {"valid": True, "check_count": 8},
                "scenario_event_replay_validation": {"valid": True, "check_count": 14},
                "sdk_events_summary": {
                    "summary_requested": True,
                    "task_id": "task_stream_handoff_verify",
                    "status": "completed",
                    "agent_stream_event_count": 4,
                    "agent_handoff_count": 2,
                    "agent_stream_event_type_counts": {
                        "raw_response_event": 1,
                        "run_item_stream_event": 3,
                    },
                    "agent_stream_event_name_counts": {
                        "none": 1,
                        "tool_called": 1,
                        "handoff_requested": 1,
                        "handoff_occured": 1,
                    },
                    "agent_stream_raw_event_type_counts": {
                        "none": 3,
                        "response.created": 1,
                    },
                    "agent_stream_item_class_counts": {
                        "none": 1,
                        "ToolCallItem": 1,
                        "HandoffCallItem": 1,
                        "HandoffOutputItem": 1,
                    },
                    "agent_stream_tool_name_counts": {
                        "none": 1,
                        "transfer_to_harness_specialist": 3,
                    },
                },
                "client_transcript_summary": {
                    "summary_requested": True,
                    "task_id": "task_stream_handoff_verify",
                    "status": "completed",
                    "agent_stream_event_count": 4,
                    "agent_handoff_count": 2,
                    "agent_stream_event_type_counts": {
                        "raw_response_event": 1,
                        "run_item_stream_event": 3,
                    },
                    "agent_stream_event_name_counts": {
                        "none": 1,
                        "tool_called": 1,
                        "handoff_requested": 1,
                        "handoff_occured": 1,
                    },
                    "agent_stream_raw_event_type_counts": {
                        "none": 3,
                        "response.created": 1,
                    },
                    "agent_stream_item_class_counts": {
                        "none": 1,
                        "ToolCallItem": 1,
                        "HandoffCallItem": 1,
                        "HandoffOutputItem": 1,
                    },
                    "agent_stream_tool_name_counts": {
                        "none": 1,
                        "transfer_to_harness_specialist": 3,
                    },
                    "item_type_counts": {
                        "agent_stream_event": 4,
                        "agent_handoff": 2,
                    },
                },
            },
        }
        stream_summary_fixture = matrix_module.summarize_matrix(
            [stream_result_fixture, stream_handoff_result_fixture],
            publish_readback=True,
        )
        stream_compact_summary = matrix_module.matrix_run_compact_summary(
            {
                "status": "completed",
                "run_id": "verify-stream-compact",
                "model": "gpt-4.1-mini",
                "provider": "openai",
                "api_style": "responses",
                "base_url": "http://verify/api/harness/v1",
                "client_id": "verify-client",
                "scenario_count": 2,
                "requested_scenarios": ["stream", "stream_handoff"],
                "summary": stream_summary_fixture,
                "results": [stream_result_fixture, stream_handoff_result_fixture],
            }
        )
        stream_evidence = stream_compact_summary[
            "scenario_stream_evidence_summaries"
        ]["stream"]
        assert stream_evidence["result_stream_event_count"] == 3
        assert stream_evidence["evidence_stream_event_count"] == 3
        assert stream_evidence["expected_stream_event_observation_recorded"] is True
        assert stream_evidence["evidence_expected_stream_event_observation_recorded"] is True
        assert stream_evidence["expected_tool_name"] == "record_signal"
        assert stream_evidence["expected_tool_name_recorded"] is True
        assert stream_evidence["expected_tool_name_count"] == 2
        assert stream_evidence["expected_stream_event_count_maps_match_result_count"] is True
        assert stream_evidence["sdk_stream_count_maps_match_evidence"] is True
        assert stream_evidence["transcript_stream_count_maps_match_evidence"] is True
        assert "observation_ids" not in stream_evidence
        stream_handoff_evidence = stream_compact_summary[
            "scenario_stream_evidence_summaries"
        ]["stream_handoff"]
        assert stream_handoff_evidence["result_stream_event_count"] == 4
        assert stream_handoff_evidence["handoff_expected"] is True
        assert stream_handoff_evidence["expected_handoff_requested_event_recorded"] is True
        assert stream_handoff_evidence["expected_handoff_completed_event_recorded"] is True
        assert stream_handoff_evidence["expected_tool_name"] == "transfer_to_harness_specialist"
        assert stream_handoff_evidence["expected_tool_name_count"] == 3
        assert stream_handoff_evidence["expected_tool_name_recorded"] is True
        serialized_stream_evidence = json.dumps(
            stream_compact_summary["scenario_stream_evidence_summaries"],
            sort_keys=True,
        )
        assert '"raw_event"' not in serialized_stream_evidence
        assert '"item":' not in serialized_stream_evidence
        assert stream_compact_summary["scenario_readback_stream_evidence_summaries"][
            "stream"
        ] == stream_evidence
        stream_rollup = matrix_module.matrix_scenario_rollup(
            {
                "run_id": "verify-stream-compact",
                "summary": stream_summary_fixture,
                "results": [stream_result_fixture, stream_handoff_result_fixture],
            }
        )
        assert stream_rollup["scenario_stream_evidence_summaries"]["stream"] == (
            stream_evidence
        )
        assert stream_rollup["scenarios"][0]["stream_evidence_summary"] == (
            stream_evidence
        )
        assert stream_rollup["scenarios"][1]["stream_evidence_summary"] == (
            stream_handoff_evidence
        )
        published_stream_summary = (
            matrix_module.published_scenario_readback_compact_summary(
                {
                    "results": [
                        stream_result_fixture["publish_readback"] | {"scenario": "stream"},
                        stream_handoff_result_fixture["publish_readback"]
                        | {"scenario": "stream_handoff"},
                    ]
                }
            )
        )
        assert published_stream_summary is not None
        assert published_stream_summary[
            "stream_evidence_summaries_by_scenario"
        ]["stream"] == stream_evidence
        assert published_stream_summary[
            "stream_evidence_summaries_by_scenario"
        ]["stream_handoff"] == stream_handoff_evidence
        assert matrix_compact_summary["scenario_status_counts"] == {"completed": 1}
        assert matrix_compact_summary["validation_status_counts"] == {"completed": 1}
        assert matrix_compact_summary["publish_status_counts"] == {"completed": 1}
        assert matrix_compact_summary["generic_readback_status_counts"] == {"valid": 1}
        assert matrix_compact_summary["projection_validation_status_counts"] == {"valid": 1}
        assert matrix_compact_summary["scenario_event_replay_status_counts"] == {"valid": 1}
        assert matrix_compact_summary["scenario_failed_checks"] == {}
        assert matrix_compact_summary["generic_readback_failed_scenarios"] == []
        assert matrix_compact_summary["projection_failed_scenarios"] == []
        assert matrix_compact_summary["scenario_event_replay_failed_scenarios"] == []
        assert matrix_compact_summary["generic_readback_failed_checks"] == {}
        assert matrix_compact_summary["projection_failed_checks"] == {}
        assert matrix_compact_summary["scenario_event_replay_failed_checks"] == {}
        assert matrix_compact_summary["scenario_event_replay_sse_event_name_counts"] == {
            "approval": {"harness_event": 7}
        }
        assert matrix_compact_summary["scenario_event_replay_event_actor_counts"] == {
            "approval": {
                "api": 2,
                "artifact": 1,
                "tool": 4,
            }
        }
        assert matrix_compact_summary["scenario_event_replay_event_counts"] == {"approval": 7}
        assert matrix_compact_summary["scenario_event_replay_count_map_totals"] == {
            "approval": {
                "event_actor_counts": 7,
                "event_status_counts": 7,
                "event_type_counts": 7,
                "sse_event_name_counts": 7,
            }
        }
        assert matrix_compact_summary["scenario_event_replay_count_map_total_totals"] == {
            "event_actor_counts": 7,
            "event_status_counts": 7,
            "event_type_counts": 7,
            "sse_event_name_counts": 7,
        }
        assert matrix_compact_summary["scenario_event_replay_count_total_checks"] == {
            "approval": expected_count_total_checks
        }
        assert matrix_compact_summary["scenario_event_replay_sse_event_name_totals"] == {
            "harness_event": 7
        }
        assert matrix_compact_summary["scenario_event_replay_event_actor_totals"] == {
            "api": 2,
            "artifact": 1,
            "tool": 4,
        }
        assert matrix_summary["provider_probe_valid"] is True
        assert matrix_summary["provider_probe_diagnostic_id"] == 99
        assert matrix_summary["provider_probe_failed_checks"] == []
        assert matrix_summary["provider_probe_check_count"] == 8
        assert matrix_summary["provider_probe_checks"]["probe_http_200"] is True
        assert matrix_summary["provider_probe_checks"]["probe_external_api_called"] is True
        assert matrix_summary["provider_probe_status"] == "completed"
        assert matrix_summary["provider_probe_http_status"] == 200
        assert matrix_summary["provider_probe_endpoint"] == "https://api.openai.com/v1/models"
        assert matrix_summary["provider_probe_models_preview_count"] == 1
        assert matrix_summary["provider_probe_profile"]["provider"] == "openai"
        assert matrix_summary["provider_probe_profile"]["api_style"] == "responses"
        assert matrix_summary["provider_probe_profile"]["model"] == "gpt-4.1-mini"
        assert matrix_summary["provider_probe_profile"]["api_key_env"] == "OPENAI_API_KEY"
        assert matrix_summary["provider_probe_agent_sdk_fit"][
            "requires_backend_owned_tool_execution"
        ] is True
        assert matrix_summary["generic_readback_status_counts"] == {"valid": 1}
        assert matrix_summary["projection_validation_status_counts"] == {"valid": 1}
        assert matrix_summary["scenario_event_replay_status_counts"] == {"valid": 1}
        assert matrix_summary["published_task_ids"] == {"approval": "task_matrix_verify"}
        assert matrix_summary["scenario_event_replay_expected_event_counts"] == {
            "approval": {
                "artifact.recorded": 1,
                "observation.recorded": 2,
                "tool_call.recorded": 1,
                "tool_call.status_updated": 1,
            }
        }
        assert matrix_summary["scenario_event_replay_observed_event_counts"] == {
            "approval": {
                "artifact.recorded": 1,
                "observation.recorded": 2,
                "tool_call.recorded": 1,
                "tool_call.status_updated": 1,
            }
        }
        assert matrix_summary["scenario_event_replay_event_counts"] == {"approval": 7}
        assert matrix_summary["scenario_event_replay_count_map_totals"] == {
            "approval": {
                "event_actor_counts": 7,
                "event_status_counts": 7,
                "event_type_counts": 7,
                "sse_event_name_counts": 7,
            }
        }
        assert matrix_summary["scenario_event_replay_count_map_total_totals"] == {
            "event_actor_counts": 7,
            "event_status_counts": 7,
            "event_type_counts": 7,
            "sse_event_name_counts": 7,
        }
        assert matrix_summary["scenario_event_replay_count_total_checks"] == {
            "approval": expected_count_total_checks
        }
        assert matrix_summary["scenario_event_replay_sse_event_name_counts"] == {
            "approval": {"harness_event": 7}
        }
        assert matrix_summary["scenario_event_replay_event_type_counts"] == {
            "approval": {
                "artifact.recorded": 1,
                "observation.recorded": 2,
                "task.paused": 1,
                "task.queued": 1,
                "tool_call.recorded": 1,
                "tool_call.status_updated": 1,
            }
        }
        assert matrix_summary["scenario_event_replay_event_status_counts"] == {
            "approval": {
                "completed": 5,
                "paused": 1,
                "queued": 1,
            }
        }
        assert matrix_summary["scenario_event_replay_event_actor_counts"] == {
            "approval": {
                "api": 2,
                "artifact": 1,
                "tool": 4,
            }
        }
        assert matrix_summary["scenario_event_replay_sse_event_name_totals"] == {
            "harness_event": 7
        }
        assert matrix_summary["scenario_event_replay_event_type_totals"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "task.paused": 1,
            "task.queued": 1,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 1,
        }
        assert matrix_summary["scenario_event_replay_event_status_totals"] == {
            "completed": 5,
            "paused": 1,
            "queued": 1,
        }
        assert matrix_summary["scenario_event_replay_event_actor_totals"] == {
            "api": 2,
            "artifact": 1,
            "tool": 4,
        }
        assert matrix_summary["scenario_event_replay_expected_event_totals"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 1,
        }
        assert matrix_summary["scenario_event_replay_observed_event_totals"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 1,
        }
        assert matrix_summary["tool_call_status_update_expected_counts"] == {"approval": 1}
        assert matrix_summary["tool_call_status_updated_event_counts"] == {"approval": 1}
        assert matrix_summary["tool_call_status_update_expected_total"] == 1
        assert matrix_summary["tool_call_status_updated_event_total"] == 1
        assert matrix_summary["scenario_sdk_events_projection_counts"] == {
            "approval": {
                "agent_model_response_count": 1,
                "agent_guardrail_count": 1,
                "agent_run_error_count": 1,
                "agent_stream_event_count": 5,
                "tool_progress_count": 2,
                "final_output_count": 1,
            }
        }
        assert matrix_summary["scenario_client_transcript_projection_counts"] == {
            "approval": {
                "agent_model_response_count": 1,
                "agent_guardrail_count": 1,
                "agent_run_error_count": 1,
                "agent_stream_event_count": 5,
                "tool_progress_count": 1,
                "final_output_count": 1,
            }
        }
        assert matrix_summary["scenario_stream_projection_summaries"]["approval"]["sdk_events"][
            "agent_stream_event_name_counts"
        ] == {"handoff_occured": 1, "handoff_requested": 1}
        assert matrix_summary["scenario_stream_projection_summaries"]["approval"]["client_transcript"][
            "agent_stream_tool_name_counts"
        ] == {"transfer_to_harness_specialist": 2}
        assert matrix_compact_summary["scenario_stream_projection_summaries"] == (
            matrix_summary["scenario_stream_projection_summaries"]
        )
        assert matrix_summary["sdk_events_projection_totals"]["agent_guardrail_count"] == 1
        assert matrix_summary["sdk_events_projection_totals"]["agent_run_error_count"] == 1
        assert matrix_summary["client_transcript_projection_totals"]["agent_guardrail_count"] == 1
        assert matrix_summary["client_transcript_projection_totals"]["agent_run_error_count"] == 1
        assert matrix_summary["projection_total_deltas"] == {
            "tool_progress_count": {"sdk_events": 2, "client_transcript": 1, "delta": 1}
        }
        assert matrix_summary["projection_total_delta_status"] == "allowed_deltas"
        assert matrix_summary["projection_total_delta_allowed_keys"] == ["tool_progress_count"]
        assert matrix_summary["projection_total_delta_unexpected_keys"] == []
        assert matrix_summary["projection_total_delta_assessment"]["items"]["tool_progress_count"][
            "status"
        ] == "allowed"
        assert matrix_summary["aggregate_failed_reasons"] == []
        assert matrix_summary["scenario_failed_checks"] == {}
        assert matrix_summary["generic_readback_failed_checks"] == {}
        assert matrix_summary["projection_failed_checks"] == {}
        assert matrix_summary["scenario_event_replay_failed_checks"] == {}
        failed_scenario_summary = matrix_module.summarize_matrix(
            [
                {
                    "scenario": "stream",
                    "status": "failed",
                    "passed": False,
                    "validation": {
                        "status": "failed",
                        "failed_checks": ["stream_events_recorded"],
                    },
                    "summary": {"raw_response_usage_totals": {"total_tokens": 3}},
                    "publish_readback": {
                        "status": "failed",
                        "task_id": "task_stream_failed",
                        "result_summary": {"tool_call_status_update_expected_count": 1},
                        "validation": {
                            "valid": False,
                            "failed_checks": ["sdk_agent_stream_event_matches_evidence"],
                        },
                        "scenario_projection_validation": {
                            "valid": False,
                            "failed_checks": ["sdk_stream_events_projected"],
                        },
                        "scenario_event_replay_validation": {
                            "valid": False,
                            "failed_checks": ["event_replay_completed"],
                        },
                    },
                }
            ],
            publish_readback=True,
            provider_probe_validation=failed_provider_probe_validation,
        )
        assert failed_scenario_summary["status"] == "failed"
        assert failed_scenario_summary["provider_probe_valid"] is False
        assert failed_scenario_summary["provider_probe_status"] == "not_connected"
        assert failed_scenario_summary["provider_probe_http_status"] == 0
        assert failed_scenario_summary["provider_probe_endpoint"] == "https://api.openai.com/v1/models"
        assert failed_scenario_summary["provider_probe_profile"]["api_key_env"] == "OPENAI_API_KEY"
        assert "probe_completed" in failed_scenario_summary["provider_probe_failed_checks"]
        assert failed_scenario_summary["failed_scenarios"] == ["stream"]
        assert failed_scenario_summary["scenario_failed_checks"] == {
            "stream": ["stream_events_recorded"]
        }
        assert failed_scenario_summary["generic_readback_failed_checks"] == {
            "stream": ["sdk_agent_stream_event_matches_evidence"]
        }
        assert failed_scenario_summary["projection_failed_checks"] == {
            "stream": ["sdk_stream_events_projected"]
        }
        assert failed_scenario_summary["scenario_event_replay_failed_checks"] == {
            "stream": ["event_replay_completed"]
        }
        assert failed_scenario_summary["scenario_event_replay_expected_event_counts"] == {"stream": {}}
        assert failed_scenario_summary["scenario_event_replay_observed_event_counts"] == {"stream": {}}
        assert failed_scenario_summary["scenario_event_replay_expected_event_totals"] == {}
        assert failed_scenario_summary["scenario_event_replay_observed_event_totals"] == {}
        assert failed_scenario_summary["api_usage_totals"] == {"total_tokens": 3}
        assert failed_scenario_summary["scenario_api_usage_totals"] == {"stream": {"total_tokens": 3}}
        failed_compact_summary = matrix_module.matrix_run_compact_summary(
            {
                "status": "failed",
                "run_id": "verify-failed-compact",
                "model": "gpt-4.1-mini",
                "provider": "openai",
                "api_style": "responses",
                "base_url": "http://verify",
                "client_id": "verify-client",
                "scenario_count": 1,
                "requested_scenarios": ["stream"],
                "summary": failed_scenario_summary,
            }
        )
        assert failed_compact_summary["valid"] is False
        assert failed_compact_summary["scenario_failed_checks"] == {
            "stream": ["stream_events_recorded"]
        }
        assert failed_compact_summary["generic_readback_failed_scenarios"] == ["stream"]
        assert failed_compact_summary["projection_failed_scenarios"] == ["stream"]
        assert failed_compact_summary["scenario_event_replay_failed_scenarios"] == ["stream"]
        assert failed_compact_summary["generic_readback_failed_checks"] == {
            "stream": ["sdk_agent_stream_event_matches_evidence"]
        }
        assert failed_compact_summary["projection_failed_checks"] == {
            "stream": ["sdk_stream_events_projected"]
        }
        assert failed_compact_summary["scenario_event_replay_failed_checks"] == {
            "stream": ["event_replay_completed"]
        }
        assert failed_scenario_summary["tool_call_status_update_expected_counts"] == {"stream": 1}
        assert failed_scenario_summary["tool_call_status_updated_event_counts"] == {"stream": 0}
        assert failed_scenario_summary["scenario_sdk_events_projection_counts"] == {"stream": {}}
        assert failed_scenario_summary["scenario_client_transcript_projection_counts"] == {"stream": {}}
        unexpected_delta_summary = matrix_module.summarize_matrix(
            [
                {
                    "scenario": "approval",
                    "status": "completed",
                    "passed": True,
                    "validation": matrix_validation,
                    "summary": {"raw_response_usage_totals": {"total_tokens": 7}},
                    "publish_readback": {
                        "status": "completed",
                        "task_id": "task_matrix_verify",
                        "result_summary": {"tool_call_status_update_expected_count": 1},
                        "validation": {"valid": True},
                        "scenario_projection_validation": {"valid": True},
                        "scenario_event_replay_validation": scenario_event_replay_validation,
                        "sdk_events_summary": {
                            "summary_requested": True,
                            "agent_model_response_count": 1,
                            "final_output_count": 1,
                        },
                        "client_transcript_summary": {
                            "summary_requested": True,
                            "agent_model_response_count": 1,
                        },
                    },
                }
            ],
            publish_readback=True,
            provider_probe_validation=provider_probe_validation,
        )
        assert unexpected_delta_summary["status"] == "failed"
        assert unexpected_delta_summary["projection_total_delta_status"] == "unexpected_deltas"
        assert unexpected_delta_summary["projection_total_delta_unexpected_keys"] == ["final_output_count"]
        assert unexpected_delta_summary["aggregate_failed_reasons"] == ["unexpected_projection_total_deltas"]
        matrix_payload = {
            "status": "completed",
            "source": "agents_sdk_smoke_matrix",
            "run_id": "verify-matrix",
            "model": "gpt-5.4-mini",
            "provider": "openai",
            "api_style": "responses",
            "scenario_count": 1,
            "requested_scenarios": ["approval"],
            "provider_probe_validation": provider_probe_validation,
            "summary": matrix_summary,
            "results": [
                {
                    "scenario": "approval",
                    "status": "completed",
                    "passed": True,
                    "validation": matrix_validation,
                    "summary": {"raw_response_usage_totals": {"total_tokens": 7}},
                    "trace_id": "trace_verify_approval",
                    "output_json": "agents-sdk-approval-verify.json",
                    "publish_readback": {
                        "status": "completed",
                        "task_id": "task_matrix_verify",
                        "result_summary": {"tool_call_status_update_expected_count": 1},
                        "expected_trace_artifact": {
                            "status": "completed",
                            "source": "trace_ingest_expected_trace_artifact",
                            "kind": "agent_trace",
                            "path": "agents-sdk-approval-verify.json",
                            "name": "agents-sdk-approval-verify.json",
                            "digest": "sha256:scenario-trace",
                            "metadata": {
                                "provider": "openai",
                                "api_style": "responses",
                                "external_api_called": True,
                            },
                        },
                        "validation": {
                            "valid": True,
                            "failed_checks": [],
                            "task_binding": {
                                "expected_task_id": "task_matrix_verify",
                                "evidence_task_id": "task_matrix_verify",
                            },
                        },
                        "scenario_projection_validation": {"valid": True, "failed_checks": []},
                        "scenario_event_replay_validation": scenario_event_replay_validation,
                        "sdk_events_summary": {
                            "summary_requested": True,
                            "agent_model_response_count": 1,
                            "agent_model_response_provider_counts": {"openai": 1},
                            "agent_model_response_api_style_counts": {"responses": 1},
                            "agent_guardrail_count": 1,
                            "agent_run_error_count": 1,
                            "agent_stream_event_count": 5,
                            "agent_stream_event_name_counts": {
                                "handoff_requested": 1,
                                "handoff_occured": 1,
                            },
                            "agent_stream_tool_name_counts": {
                                "transfer_to_harness_specialist": 2,
                            },
                            "tool_progress_count": 2,
                            "final_output_count": 1,
                        },
                        "client_transcript_summary": {
                            "summary_requested": True,
                            "agent_model_response_count": 1,
                            "agent_model_response_provider_counts": {"openai": 1},
                            "agent_model_response_api_style_counts": {"responses": 1},
                            "agent_guardrail_count": 1,
                            "agent_run_error_count": 1,
                            "agent_stream_event_count": 5,
                            "agent_stream_event_name_counts": {
                                "handoff_requested": 1,
                                "handoff_occured": 1,
                            },
                            "agent_stream_tool_name_counts": {
                                "transfer_to_harness_specialist": 2,
                            },
                            "tool_progress_count": 1,
                            "final_output_count": 1,
                        },
                    },
                }
            ],
        }
        matrix_evidence_path = Path(temp) / "matrix-evidence-source.json"
        matrix_evidence_path.write_text(json.dumps(matrix_payload), encoding="utf-8")
        published_matrix_payload = json.loads(json.dumps(matrix_payload))
        published_matrix_payload["matrix_evidence_json"] = str(matrix_evidence_path)
        published_matrix_payload["matrix_readback"] = {
            "status": "completed",
            "task_id": "task_matrix_aggregate_verify",
            "validation": {"valid": True},
        }
        published_matrix_payload["summary"]["matrix_readback_status"] = "completed"
        published_matrix_payload["summary"]["matrix_readback_valid"] = True
        published_matrix_payload["summary"]["matrix_payload_readback_status"] = "completed"
        published_matrix_payload["summary"]["matrix_payload_readback_valid"] = True
        published_matrix_payload["summary"]["matrix_payload_readback_failed_checks"] = []
        published_matrix_payload["summary"]["matrix_payload_readback_task_binding"] = {
            "matrix_payload_task_id": "task_matrix_aggregate_verify"
        }
        comparable_published_summary = matrix_module.comparable_matrix_summary(
            published_matrix_payload["summary"]
        )
        assert "matrix_payload_readback_status" not in comparable_published_summary
        assert "matrix_payload_readback_valid" not in comparable_published_summary
        assert "matrix_payload_readback_failed_checks" not in comparable_published_summary
        assert "matrix_payload_readback_task_binding" not in comparable_published_summary
        matrix_output_path = Path(temp) / "matrix-output.json"
        matrix_output_path.write_text(json.dumps(published_matrix_payload), encoding="utf-8")
        matrix_actions = matrix_module.build_matrix_readback_actions(
            "task_matrix_aggregate_verify",
            matrix_payload,
            matrix_evidence_path,
        )
        assert [item["body"].get("kind") for item in matrix_actions if item["path"].endswith("/artifacts")] == [
            "agents_sdk_smoke_matrix"
        ]
        matrix_observation_tools = [
            item["body"]["tool_name"] for item in matrix_actions if item["path"].endswith("/observations")
        ]
        assert matrix_observation_tools == [
            "agents_sdk_smoke_matrix_summary",
            "agents_sdk_smoke_matrix_scenario_rollup",
            "agents_sdk_smoke_matrix_provider_probe_validation",
        ]
        matrix_rollup_result = next(
            item["body"]["result"]
            for item in matrix_actions
            if item["path"].endswith("/observations")
            and item["body"]["tool_name"] == "agents_sdk_smoke_matrix_scenario_rollup"
        )
        assert matrix_rollup_result == matrix_module.matrix_scenario_rollup(matrix_payload)
        assert matrix_rollup_result["scenario_event_replay_failed_scenarios"] == []
        assert matrix_rollup_result["scenario_event_replay_failed_checks"] == {}
        assert matrix_rollup_result["external_api_called"] is True
        assert matrix_rollup_result["scenario_external_api_called"] == {"approval": True}
        assert matrix_rollup_result["scenarios"][0]["external_api_called"] is True
        assert matrix_rollup_result["api_usage_totals"] == {"total_tokens": 7}
        assert matrix_rollup_result["scenario_api_usage_totals"] == {"approval": {"total_tokens": 7}}
        assert matrix_rollup_result["scenario_sdk_events_projection_counts"] == {
            "approval": {
                "agent_model_response_count": 1,
                "agent_guardrail_count": 1,
                "agent_run_error_count": 1,
                "agent_stream_event_count": 5,
                "tool_progress_count": 2,
                "final_output_count": 1,
            }
        }
        assert matrix_rollup_result["scenario_client_transcript_projection_counts"] == {
            "approval": {
                "agent_model_response_count": 1,
                "agent_guardrail_count": 1,
                "agent_run_error_count": 1,
                "agent_stream_event_count": 5,
                "tool_progress_count": 1,
                "final_output_count": 1,
            }
        }
        assert matrix_rollup_result["scenario_guardrail_projection_summaries"] == (
            expected_guardrail_projection_summaries
        )
        assert matrix_rollup_result["scenario_guardrail_tripwire_summaries"] == {}
        assert matrix_rollup_result["scenario_stream_projection_summaries"]["approval"][
            "sdk_events"
        ]["agent_stream_event_name_counts"] == {
            "handoff_occured": 1,
            "handoff_requested": 1,
        }
        assert matrix_rollup_result["scenario_stream_projection_summaries"]["approval"][
            "client_transcript"
        ]["agent_stream_tool_name_counts"] == {"transfer_to_harness_specialist": 2}
        assert matrix_rollup_result["scenario_event_replay_expected_event_counts"] == {
            "approval": {
                "artifact.recorded": 1,
                "observation.recorded": 2,
                "tool_call.recorded": 1,
                "tool_call.status_updated": 1,
            }
        }
        assert matrix_rollup_result["scenario_event_replay_observed_event_counts"] == {
            "approval": {
                "artifact.recorded": 1,
                "observation.recorded": 2,
                "tool_call.recorded": 1,
                "tool_call.status_updated": 1,
            }
        }
        assert matrix_rollup_result["scenario_event_replay_event_counts"] == {"approval": 7}
        assert matrix_rollup_result["scenario_event_replay_count_map_totals"] == {
            "approval": {
                "event_actor_counts": 7,
                "event_status_counts": 7,
                "event_type_counts": 7,
                "sse_event_name_counts": 7,
            }
        }
        assert matrix_rollup_result["scenario_event_replay_count_map_total_totals"] == {
            "event_actor_counts": 7,
            "event_status_counts": 7,
            "event_type_counts": 7,
            "sse_event_name_counts": 7,
        }
        assert matrix_rollup_result["scenario_event_replay_count_total_checks"] == {
            "approval": expected_count_total_checks
        }
        assert matrix_rollup_result["scenario_event_replay_sse_event_name_counts"] == {
            "approval": {"harness_event": 7}
        }
        assert matrix_rollup_result["scenario_event_replay_event_status_counts"] == {
            "approval": {
                "completed": 5,
                "paused": 1,
                "queued": 1,
            }
        }
        assert matrix_rollup_result["scenario_event_replay_event_actor_counts"] == {
            "approval": {
                "api": 2,
                "artifact": 1,
                "tool": 4,
            }
        }
        assert matrix_rollup_result["scenario_event_replay_sse_event_name_totals"] == {
            "harness_event": 7
        }
        assert matrix_rollup_result["scenario_event_replay_event_actor_totals"] == {
            "api": 2,
            "artifact": 1,
            "tool": 4,
        }
        assert matrix_rollup_result["scenario_event_replay_expected_event_totals"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 1,
        }
        assert matrix_rollup_result["scenario_event_replay_observed_event_totals"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 1,
        }
        assert matrix_rollup_result["tool_call_status_update_expected_counts"] == {"approval": 1}
        assert matrix_rollup_result["tool_call_status_updated_event_counts"] == {"approval": 1}
        assert matrix_rollup_result["tool_call_status_update_expected_total"] == 1
        assert matrix_rollup_result["tool_call_status_updated_event_total"] == 1
        assert matrix_rollup_result["scenarios"][0]["generic_readback_failed_checks"] == []
        assert matrix_rollup_result["scenarios"][0]["generic_readback_task_binding"][
            "expected_task_id"
        ] == "task_matrix_verify"
        assert matrix_rollup_result["scenarios"][0]["scenario_event_replay_valid"] is True
        assert matrix_rollup_result["scenarios"][0]["scenario_event_replay_failed_checks"] == []
        assert matrix_rollup_result["scenarios"][0]["scenario_event_replay_task_binding"][
            "event_replay_task_id"
        ] == "task_matrix_verify"
        assert matrix_rollup_result["scenarios"][0]["scenario_event_replay_expected_event_counts"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 1,
        }
        assert matrix_rollup_result["scenarios"][0]["scenario_event_replay_observed_event_counts"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 1,
        }
        assert matrix_rollup_result["scenarios"][0]["scenario_event_replay_event_count"] == 7
        assert matrix_rollup_result["scenarios"][0]["scenario_event_replay_count_map_totals"] == {
            "event_actor_counts": 7,
            "event_status_counts": 7,
            "event_type_counts": 7,
            "sse_event_name_counts": 7,
        }
        assert matrix_rollup_result["scenarios"][0]["scenario_event_replay_count_total_checks"] == (
            expected_count_total_checks
        )
        assert matrix_rollup_result["scenarios"][0]["scenario_event_replay_sse_event_name_counts"] == {
            "harness_event": 7
        }
        assert matrix_rollup_result["scenarios"][0]["scenario_event_replay_event_actor_counts"] == {
            "api": 2,
            "artifact": 1,
            "tool": 4,
        }
        assert matrix_rollup_result["scenarios"][0]["api_usage_totals"] == {"total_tokens": 7}
        assert matrix_rollup_result["scenarios"][0]["sdk_events_projection_counts"] == {
            "agent_model_response_count": 1,
            "agent_guardrail_count": 1,
            "agent_run_error_count": 1,
            "agent_stream_event_count": 5,
            "tool_progress_count": 2,
            "final_output_count": 1,
        }
        assert matrix_rollup_result["scenarios"][0]["client_transcript_projection_counts"] == {
            "agent_model_response_count": 1,
            "agent_guardrail_count": 1,
            "agent_run_error_count": 1,
            "agent_stream_event_count": 5,
            "tool_progress_count": 1,
            "final_output_count": 1,
        }
        assert matrix_rollup_result["scenarios"][0]["guardrail_projection_summary"] == (
            expected_guardrail_projection_summaries["approval"]
        )
        assert matrix_rollup_result["scenarios"][0]["stream_projection_summary"]["sdk_events"][
            "agent_stream_event_name_counts"
        ] == {
            "handoff_occured": 1,
            "handoff_requested": 1,
        }
        assert matrix_rollup_result["scenarios"][0]["stream_projection_summary"][
            "client_transcript"
        ]["agent_stream_tool_name_counts"] == {"transfer_to_harness_specialist": 2}
        assert matrix_rollup_result["scenarios"][0]["tool_call_status_update_expected_count"] == 1
        assert matrix_rollup_result["scenarios"][0]["tool_call_status_updated_event_count"] == 1
        matrix_evidence = {
            "status": "completed",
            "task_id": "task_matrix_aggregate_verify",
            "tool_calls": [],
            "artifacts": [
                {
                    "kind": item["body"]["kind"],
                    "path": item["body"]["path"],
                    "digest": item["body"]["digest"],
                }
                for item in matrix_actions
                if item["path"].endswith("/artifacts")
            ],
            "observations": [
                {
                    "tool_name": item["body"]["tool_name"],
                    "result": item["body"]["result"],
                    "status": item["body"]["status"],
                }
                for item in matrix_actions
                if item["path"].endswith("/observations")
            ],
            "verification_results": [],
        }
        matrix_readback = {
            "evidence_counts": {
                "artifacts": 1,
                "observations": 3,
                "artifact_kind_counts": {"agents_sdk_smoke_matrix": 1},
                "observation_tool_counts": {
                    "agents_sdk_smoke_matrix_summary": 1,
                    "agents_sdk_smoke_matrix_scenario_rollup": 1,
                    "agents_sdk_smoke_matrix_provider_probe_validation": 1,
                },
            },
            "sdk_events_summary": {"summary_requested": True, "task_id": "task_matrix_aggregate_verify"},
            "client_transcript_summary": {"summary_requested": True, "task_id": "task_matrix_aggregate_verify"},
        }
        matrix_event_replay = {
            "status": "completed",
            "source": "harness_event_sse_replay",
            "task_id": "task_matrix_aggregate_verify",
            "after": 0,
            "events": [
                {
                    "id": "1",
                    "event": "harness_event",
                    "data": {"event_type": "task.queued", "status": "queued", "actor": "api"},
                },
                {
                    "id": "2",
                    "event": "harness_event",
                    "data": {"event_type": "task.paused", "status": "paused", "actor": "api"},
                },
                {
                    "id": "3",
                    "event": "harness_event",
                    "data": {"event_type": "artifact.recorded", "status": "completed", "actor": "artifact"},
                },
                {
                    "id": "4",
                    "event": "harness_event",
                    "data": {"event_type": "observation.recorded", "status": "completed", "actor": "tool"},
                },
                {
                    "id": "5",
                    "event": "harness_event",
                    "data": {"event_type": "observation.recorded", "status": "completed", "actor": "tool"},
                },
                {
                    "id": "6",
                    "event": "harness_event",
                    "data": {"event_type": "observation.recorded", "status": "completed", "actor": "tool"},
                },
            ],
            "event_count": 6,
            "keepalive_count": 0,
            "timed_out": False,
            "next_after": 6,
        }
        matrix_event_summary = matrix_module.matrix_event_replay_summary(matrix_event_replay)
        assert matrix_event_summary["sse_event_name_counts"] == {"harness_event": 6}
        assert matrix_event_summary["event_type_counts"]["artifact.recorded"] == 1
        assert matrix_event_summary["event_type_counts"]["observation.recorded"] == 3
        assert matrix_event_summary["event_status_counts"] == {
            "completed": 4,
            "paused": 1,
            "queued": 1,
        }
        assert matrix_event_summary["event_actor_counts"] == {
            "api": 2,
            "artifact": 1,
            "tool": 3,
        }
        matrix_readback_validation = matrix_module.validate_matrix_readback(
            matrix_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            matrix_event_replay,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert matrix_readback_validation["valid"] is True
        assert matrix_readback_validation["task_binding"]["expected_task_id"] == "task_matrix_aggregate_verify"
        assert matrix_readback_validation["checks"]["matrix_readback_payload_task_id_matches"] is True
        assert matrix_readback_validation["checks"]["matrix_readback_evidence_task_id_matches"] is True
        assert matrix_readback_validation["checks"]["matrix_readback_sdk_summary_task_id_matches"] is True
        assert matrix_readback_validation["checks"]["matrix_readback_transcript_task_id_matches"] is True
        assert matrix_readback_validation["checks"]["matrix_readback_event_replay_task_id_matches"] is True
        assert matrix_readback_validation["checks"]["matrix_summary_external_api_called_match"] is True
        assert matrix_readback_validation["checks"]["matrix_summary_scenario_external_api_called_match"] is True
        assert (
            matrix_readback_validation["checks"]["matrix_summary_scenario_api_usage_totals_match"]
            is True
        )
        assert matrix_readback_validation["checks"]["matrix_summary_api_usage_totals_match"] is True
        assert (
            matrix_readback_validation["checks"]["matrix_rollup_scenario_api_usage_totals_match"]
            is True
        )
        assert matrix_readback_validation["checks"]["matrix_rollup_api_usage_totals_match"] is True
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_tool_call_status_update_expected_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_event_replay_expected_event_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_event_replay_observed_event_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_event_replay_event_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_event_replay_count_map_totals_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_event_replay_count_map_total_totals_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_event_replay_count_total_checks_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_event_replay_sse_event_name_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_event_replay_event_type_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_event_replay_event_status_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_event_replay_event_actor_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_event_replay_sse_event_name_totals_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_event_replay_event_type_totals_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_event_replay_event_status_totals_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_event_replay_event_actor_totals_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_sdk_events_projection_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_client_transcript_projection_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_tool_state_summaries_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_tool_evidence_summaries_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_approval_evidence_summaries_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_handoff_evidence_summaries_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_guardrail_evidence_summaries_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_session_evidence_summaries_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_stream_evidence_summaries_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_scenario_tool_projection_summaries_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_summary_tool_call_status_updated_event_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_tool_call_status_update_expected_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_event_replay_expected_event_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_event_replay_observed_event_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_event_replay_event_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_event_replay_count_map_totals_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_event_replay_count_map_total_totals_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_event_replay_count_total_checks_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_event_replay_sse_event_name_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_event_replay_event_type_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_event_replay_event_status_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_event_replay_event_actor_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_event_replay_sse_event_name_totals_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_event_replay_event_type_totals_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_event_replay_event_status_totals_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_event_replay_event_actor_totals_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_sdk_events_projection_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_client_transcript_projection_counts_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_tool_state_summaries_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_tool_evidence_summaries_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_approval_evidence_summaries_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_handoff_evidence_summaries_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_guardrail_evidence_summaries_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_session_evidence_summaries_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_stream_evidence_summaries_match"
            ]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_scenario_tool_projection_summaries_match"
            ]
            is True
        )
        assert matrix_readback_validation["checks"]["matrix_rollup_external_api_called_match"] is True
        assert (
            matrix_readback_validation["checks"]["matrix_rollup_scenario_external_api_called_match"]
            is True
        )
        assert (
            matrix_readback_validation["checks"][
                "matrix_rollup_tool_call_status_updated_event_counts_match"
            ]
            is True
        )
        assert matrix_readback_validation["checks"]["matrix_rollup_payload_matches"] is True
        assert matrix_readback_validation["event_replay_summary"]["next_after"] == 6
        expected_aggregate_count_map_totals = {
            "event_actor_counts": 6,
            "event_status_counts": 6,
            "event_type_counts": 6,
            "sse_event_name_counts": 6,
        }
        expected_aggregate_event_counts = {
            "artifact.recorded": 1,
            "observation.recorded": 3,
            "tool_call.recorded": 0,
            "tool_call.status_updated": 0,
        }
        assert matrix_readback_validation["event_replay_count_map_totals"] == (
            expected_aggregate_count_map_totals
        )
        assert matrix_readback_validation["event_replay_count_total_checks"] == (
            expected_count_total_checks
        )
        assert matrix_readback_validation["expected_event_counts"] == (
            expected_aggregate_event_counts
        )
        assert matrix_readback_validation["observed_event_counts"] == (
            expected_aggregate_event_counts
        )
        assert matrix_readback_validation["checks"][
            "event_replay_sse_event_name_count_total_matches_event_count"
        ] is True
        assert matrix_readback_validation["checks"][
            "event_replay_event_type_count_total_matches_event_count"
        ] is True
        assert matrix_readback_validation["checks"][
            "event_replay_event_status_count_total_matches_event_count"
        ] is True
        assert matrix_readback_validation["checks"][
            "event_replay_event_actor_count_total_matches_event_count"
        ] is True
        assert matrix_readback_validation["checks"]["event_replay_covers_tool_call_recorded"] is True
        assert (
            matrix_readback_validation["checks"]["event_replay_covers_tool_call_status_updated"]
            is True
        )
        matrix_event_replay_with_fetch_plan = json.loads(json.dumps(matrix_event_replay))
        matrix_event_replay_with_fetch_plan["fetch_plan"] = {
            "source": "agents_sdk_smoke_matrix_event_replay_fetch_plan",
            "basis": "sdk_events_raw_event_count",
            "max_events": 500,
            "expected_raw_event_count": 6,
            "evidence_event_floor": 4,
            "default_max_events": 500,
        }
        matrix_fetch_plan_validation = matrix_module.validate_matrix_readback(
            matrix_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            matrix_event_replay_with_fetch_plan,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert matrix_fetch_plan_validation["valid"] is True
        assert matrix_fetch_plan_validation["event_replay_fetch_plan"]["max_events"] == 500
        assert matrix_fetch_plan_validation["checks"][
            "event_replay_fetch_plan_covers_expected_raw_event_count"
        ] is True
        assert matrix_fetch_plan_validation["checks"]["event_replay_not_truncated_by_fetch_limit"] is True
        matrix_fetch_plan_compact = matrix_module.event_replay_fetch_plan_compact_validation(
            matrix_fetch_plan_validation
        )
        assert matrix_fetch_plan_compact["fetch_plan"]["max_events"] == 500
        assert matrix_fetch_plan_compact["fetch_plan"]["expected_raw_event_count"] == 6
        assert matrix_fetch_plan_compact["checks"]["event_replay_not_truncated_by_fetch_limit"] is True
        truncated_matrix_fetch_plan_replay = json.loads(json.dumps(matrix_event_replay_with_fetch_plan))
        truncated_matrix_fetch_plan_replay["event_count"] = 5
        truncated_matrix_fetch_plan_validation = matrix_module.validate_matrix_readback(
            matrix_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            truncated_matrix_fetch_plan_replay,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert truncated_matrix_fetch_plan_validation["valid"] is False
        assert "event_replay_not_truncated_by_fetch_limit" in (
            truncated_matrix_fetch_plan_validation["failed_checks"]
        )
        assert matrix_readback_validation["checks"]["matrix_artifact_path_matches_payload"] is True
        assert matrix_readback_validation["checks"]["matrix_artifact_digest_matches_recorded_path"] is True
        assert matrix_readback_validation["checks"]["matrix_artifact_digest_matches_expected_path"] is True
        broken_matrix_event_replay = json.loads(json.dumps(matrix_event_replay))
        broken_matrix_event_replay["events"] = [
            item
            for item in broken_matrix_event_replay["events"]
            if item["data"]["event_type"] != "observation.recorded"
        ]
        broken_event_replay_validation = matrix_module.validate_matrix_readback(
            matrix_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            broken_matrix_event_replay,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert broken_event_replay_validation["valid"] is False
        assert "event_replay_covers_observation_recorded" in broken_event_replay_validation["failed_checks"]
        stale_count_map_event_replay = json.loads(json.dumps(matrix_event_replay))
        stale_count_map_event_replay["event_type_counts"] = {"artifact.recorded": 1}
        stale_count_map_validation = matrix_module.validate_matrix_readback(
            matrix_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            stale_count_map_event_replay,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert stale_count_map_validation["valid"] is False
        assert "event_replay_event_type_count_total_matches_event_count" in (
            stale_count_map_validation["failed_checks"]
        )
        broken_artifact_digest_evidence = json.loads(json.dumps(matrix_evidence))
        broken_artifact_digest_evidence["artifacts"][0]["digest"] = "sha256:incorrect"
        broken_artifact_digest_validation = matrix_module.validate_matrix_readback(
            broken_artifact_digest_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert broken_artifact_digest_validation["valid"] is False
        assert "matrix_artifact_digest_matches_recorded_path" in broken_artifact_digest_validation["failed_checks"]
        assert "matrix_artifact_digest_matches_expected_path" in broken_artifact_digest_validation["failed_checks"]
        broken_artifact_path_evidence = json.loads(json.dumps(matrix_evidence))
        broken_artifact_path_evidence["artifacts"][0]["path"] = str(matrix_output_path)
        broken_artifact_path_validation = matrix_module.validate_matrix_readback(
            broken_artifact_path_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert broken_artifact_path_validation["valid"] is False
        assert "matrix_artifact_path_matches_payload" in broken_artifact_path_validation["failed_checks"]
        assert "matrix_artifact_digest_matches_recorded_path" in broken_artifact_path_validation["failed_checks"]
        unavailable_artifact_payload = json.loads(json.dumps(published_matrix_payload))
        unavailable_artifact_payload["matrix_evidence_json"] = "missing-workspace/matrix.evidence-source.json"
        unavailable_artifact_evidence = json.loads(json.dumps(matrix_evidence))
        unavailable_artifact_evidence["artifacts"][0]["path"] = "missing-workspace/matrix.evidence-source.json"
        unavailable_artifact_evidence["artifacts"][0]["digest"] = "sha256:unavailable-but-recorded"
        unavailable_artifact_validation = matrix_module.validate_matrix_readback(
            unavailable_artifact_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            unavailable_artifact_payload,
            matrix_event_replay,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert unavailable_artifact_validation["valid"] is True
        assert unavailable_artifact_validation["checks"]["matrix_artifact_path_matches_payload"] is True
        assert unavailable_artifact_validation["checks"]["matrix_artifact_digest_matches_recorded_path"] is True
        assert unavailable_artifact_validation["checks"]["matrix_artifact_digest_matches_expected_path"] is True
        assert unavailable_artifact_validation["matrix_artifact"]["recorded_path_available"] is False
        assert unavailable_artifact_validation["matrix_artifact"]["expected_path_available"] is False
        wrong_task_validation = matrix_module.validate_matrix_readback(
            matrix_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            matrix_event_replay,
            expected_task_id="task_wrong_aggregate",
        )
        assert wrong_task_validation["valid"] is False
        assert "matrix_readback_evidence_task_id_matches" in wrong_task_validation["failed_checks"]
        assert "matrix_readback_sdk_summary_task_id_matches" in wrong_task_validation["failed_checks"]
        assert "matrix_readback_transcript_task_id_matches" in wrong_task_validation["failed_checks"]
        assert "matrix_readback_event_replay_task_id_matches" in wrong_task_validation["failed_checks"]
        assert "matrix_readback_payload_task_id_matches" in wrong_task_validation["failed_checks"]
        missing_task_matrix_evidence = json.loads(json.dumps(matrix_evidence))
        missing_task_matrix_evidence.pop("task_id", None)
        missing_task_matrix_readback = json.loads(json.dumps(matrix_readback))
        missing_task_matrix_readback["sdk_events_summary"].pop("task_id", None)
        missing_task_matrix_readback["client_transcript_summary"].pop("task_id", None)
        missing_task_matrix_event_replay = json.loads(json.dumps(matrix_event_replay))
        missing_task_matrix_event_replay.pop("task_id", None)
        missing_task_matrix_validation = matrix_module.validate_matrix_readback(
            missing_task_matrix_evidence,
            missing_task_matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            missing_task_matrix_event_replay,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert missing_task_matrix_validation["valid"] is False
        assert "matrix_readback_evidence_task_id_matches" in missing_task_matrix_validation["failed_checks"]
        assert "matrix_readback_sdk_summary_task_id_matches" in missing_task_matrix_validation["failed_checks"]
        assert "matrix_readback_transcript_task_id_matches" in missing_task_matrix_validation["failed_checks"]
        assert "matrix_readback_event_replay_task_id_matches" in missing_task_matrix_validation["failed_checks"]
        broken_summary_evidence = json.loads(json.dumps(matrix_evidence))
        for observation in broken_summary_evidence["observations"]:
            if observation["tool_name"] == "agents_sdk_smoke_matrix_summary":
                observation["result"]["projection_total_deltas"] = {}
        broken_summary_readback_validation = matrix_module.validate_matrix_readback(
            broken_summary_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert broken_summary_readback_validation["valid"] is False
        assert "matrix_summary_payload_matches" in broken_summary_readback_validation["failed_checks"]
        assert "matrix_summary_projection_deltas_match" in broken_summary_readback_validation["failed_checks"]
        broken_delta_assessment_evidence = json.loads(json.dumps(matrix_evidence))
        for observation in broken_delta_assessment_evidence["observations"]:
            if observation["tool_name"] == "agents_sdk_smoke_matrix_summary":
                observation["result"]["projection_total_delta_assessment"] = {}
        broken_delta_assessment_validation = matrix_module.validate_matrix_readback(
            broken_delta_assessment_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert broken_delta_assessment_validation["valid"] is False
        assert "matrix_summary_payload_matches" in broken_delta_assessment_validation["failed_checks"]
        assert (
            "matrix_summary_projection_delta_assessment_match"
            in broken_delta_assessment_validation["failed_checks"]
        )
        broken_projection_count_summary_evidence = json.loads(json.dumps(matrix_evidence))
        for observation in broken_projection_count_summary_evidence["observations"]:
            if observation["tool_name"] == "agents_sdk_smoke_matrix_summary":
                observation["result"]["scenario_sdk_events_projection_counts"] = {}
        broken_projection_count_summary_validation = matrix_module.validate_matrix_readback(
            broken_projection_count_summary_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert broken_projection_count_summary_validation["valid"] is False
        assert "matrix_summary_payload_matches" in broken_projection_count_summary_validation["failed_checks"]
        assert (
            "matrix_summary_scenario_sdk_events_projection_counts_match"
            in broken_projection_count_summary_validation["failed_checks"]
        )
        broken_usage_summary_evidence = json.loads(json.dumps(matrix_evidence))
        for observation in broken_usage_summary_evidence["observations"]:
            if observation["tool_name"] == "agents_sdk_smoke_matrix_summary":
                observation["result"]["scenario_api_usage_totals"] = {}
        broken_usage_summary_validation = matrix_module.validate_matrix_readback(
            broken_usage_summary_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert broken_usage_summary_validation["valid"] is False
        assert "matrix_summary_payload_matches" in broken_usage_summary_validation["failed_checks"]
        assert (
            "matrix_summary_scenario_api_usage_totals_match"
            in broken_usage_summary_validation["failed_checks"]
        )
        broken_status_summary_evidence = json.loads(json.dumps(matrix_evidence))
        for observation in broken_status_summary_evidence["observations"]:
            if observation["tool_name"] == "agents_sdk_smoke_matrix_summary":
                observation["result"]["tool_call_status_update_expected_counts"] = {}
                observation["result"]["tool_call_status_update_expected_total"] = 0
        broken_status_summary_validation = matrix_module.validate_matrix_readback(
            broken_status_summary_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert broken_status_summary_validation["valid"] is False
        assert "matrix_summary_payload_matches" in broken_status_summary_validation["failed_checks"]
        assert (
            "matrix_summary_tool_call_status_update_expected_counts_match"
            in broken_status_summary_validation["failed_checks"]
        )
        assert (
            "matrix_summary_tool_call_status_update_expected_total_match"
            in broken_status_summary_validation["failed_checks"]
        )
        broken_event_count_summary_evidence = json.loads(json.dumps(matrix_evidence))
        for observation in broken_event_count_summary_evidence["observations"]:
            if observation["tool_name"] == "agents_sdk_smoke_matrix_summary":
                observation["result"]["scenario_event_replay_expected_event_counts"] = {}
                observation["result"]["scenario_event_replay_expected_event_totals"] = {}
        broken_event_count_summary_validation = matrix_module.validate_matrix_readback(
            broken_event_count_summary_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert broken_event_count_summary_validation["valid"] is False
        assert "matrix_summary_payload_matches" in broken_event_count_summary_validation["failed_checks"]
        assert (
            "matrix_summary_scenario_event_replay_expected_event_counts_match"
            in broken_event_count_summary_validation["failed_checks"]
        )
        assert (
            "matrix_summary_scenario_event_replay_expected_event_totals_match"
            in broken_event_count_summary_validation["failed_checks"]
        )
        broken_matrix_evidence = json.loads(json.dumps(matrix_evidence))
        for observation in broken_matrix_evidence["observations"]:
            if observation["tool_name"] == "agents_sdk_smoke_matrix_scenario_rollup":
                observation["result"]["published_task_ids"] = {}
        broken_matrix_readback_validation = matrix_module.validate_matrix_readback(
            broken_matrix_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert broken_matrix_readback_validation["valid"] is False
        assert "matrix_rollup_published_task_ids_match" in broken_matrix_readback_validation["failed_checks"]
        assert "matrix_rollup_payload_matches" in broken_matrix_readback_validation["failed_checks"]
        broken_matrix_rollup_status_evidence = json.loads(json.dumps(matrix_evidence))
        for observation in broken_matrix_rollup_status_evidence["observations"]:
            if observation["tool_name"] == "agents_sdk_smoke_matrix_scenario_rollup":
                observation["result"]["tool_call_status_updated_event_counts"] = {}
                observation["result"]["tool_call_status_updated_event_total"] = 0
        broken_matrix_rollup_status_validation = matrix_module.validate_matrix_readback(
            broken_matrix_rollup_status_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert broken_matrix_rollup_status_validation["valid"] is False
        assert "matrix_rollup_payload_matches" in broken_matrix_rollup_status_validation["failed_checks"]
        assert (
            "matrix_rollup_tool_call_status_updated_event_counts_match"
            in broken_matrix_rollup_status_validation["failed_checks"]
        )
        assert (
            "matrix_rollup_tool_call_status_updated_event_total_match"
            in broken_matrix_rollup_status_validation["failed_checks"]
        )
        broken_matrix_rollup_projection_count_evidence = json.loads(json.dumps(matrix_evidence))
        for observation in broken_matrix_rollup_projection_count_evidence["observations"]:
            if observation["tool_name"] == "agents_sdk_smoke_matrix_scenario_rollup":
                observation["result"]["scenario_client_transcript_projection_counts"] = {}
        broken_matrix_rollup_projection_count_validation = matrix_module.validate_matrix_readback(
            broken_matrix_rollup_projection_count_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert broken_matrix_rollup_projection_count_validation["valid"] is False
        assert "matrix_rollup_payload_matches" in broken_matrix_rollup_projection_count_validation["failed_checks"]
        assert (
            "matrix_rollup_scenario_client_transcript_projection_counts_match"
            in broken_matrix_rollup_projection_count_validation["failed_checks"]
        )
        broken_matrix_rollup_usage_evidence = json.loads(json.dumps(matrix_evidence))
        for observation in broken_matrix_rollup_usage_evidence["observations"]:
            if observation["tool_name"] == "agents_sdk_smoke_matrix_scenario_rollup":
                observation["result"]["scenario_api_usage_totals"] = {}
        broken_matrix_rollup_usage_validation = matrix_module.validate_matrix_readback(
            broken_matrix_rollup_usage_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert broken_matrix_rollup_usage_validation["valid"] is False
        assert "matrix_rollup_payload_matches" in broken_matrix_rollup_usage_validation["failed_checks"]
        assert (
            "matrix_rollup_scenario_api_usage_totals_match"
            in broken_matrix_rollup_usage_validation["failed_checks"]
        )
        broken_matrix_rollup_event_count_evidence = json.loads(json.dumps(matrix_evidence))
        for observation in broken_matrix_rollup_event_count_evidence["observations"]:
            if observation["tool_name"] == "agents_sdk_smoke_matrix_scenario_rollup":
                observation["result"]["scenario_event_replay_observed_event_counts"] = {}
                observation["result"]["scenario_event_replay_observed_event_totals"] = {}
        broken_matrix_rollup_event_count_validation = matrix_module.validate_matrix_readback(
            broken_matrix_rollup_event_count_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert broken_matrix_rollup_event_count_validation["valid"] is False
        assert "matrix_rollup_payload_matches" in broken_matrix_rollup_event_count_validation["failed_checks"]
        assert (
            "matrix_rollup_scenario_event_replay_observed_event_counts_match"
            in broken_matrix_rollup_event_count_validation["failed_checks"]
        )
        assert (
            "matrix_rollup_scenario_event_replay_observed_event_totals_match"
            in broken_matrix_rollup_event_count_validation["failed_checks"]
        )
        broken_matrix_rollup_detail_evidence = json.loads(json.dumps(matrix_evidence))
        for observation in broken_matrix_rollup_detail_evidence["observations"]:
            if observation["tool_name"] == "agents_sdk_smoke_matrix_scenario_rollup":
                observation["result"]["scenarios"][0]["scenario_event_replay_task_binding"] = {}
        broken_matrix_rollup_detail_validation = matrix_module.validate_matrix_readback(
            broken_matrix_rollup_detail_evidence,
            matrix_readback,
            {"artifact_count": 1, "observation_count": 3},
            published_matrix_payload,
            expected_task_id="task_matrix_aggregate_verify",
        )
        assert broken_matrix_rollup_detail_validation["valid"] is False
        assert "matrix_rollup_payload_matches" in broken_matrix_rollup_detail_validation["failed_checks"]
        scenario_evidence = {
            "status": "completed",
            "task_id": "task_matrix_verify",
            "tool_calls": [
                {"tool_name": "approval_tool", "call_id": "call_approval_1", "status": "queued"}
            ],
            "artifacts": [
                {
                    "artifact_id": 501,
                    "kind": "agent_trace",
                    "path": "agents-sdk-approval-verify.json",
                    "digest": "sha256:scenario-trace",
                    "status": "completed",
                    "metadata": {
                        "trace_id": "trace_verify_approval",
                        "provider": "openai",
                        "api_style": "responses",
                        "external_api_called": True,
                    },
                }
            ],
            "observations": [
                {"tool_name": "agent_model_response", "result": {}, "status": "completed"},
                {"tool_name": "agent_final_output", "result": {}, "status": "completed"},
                {"tool_name": "agent_approval_request", "result": {}, "status": "completed"},
                {"tool_name": "agent_approval_decision", "result": {}, "status": "completed"},
                {"tool_name": "agent_run_phase", "result": {}, "status": "completed"},
                {"tool_name": "agent_run_phase", "result": {}, "status": "completed"},
                {
                    "tool_name": "agent_session",
                    "result": {
                        "session_id": "sdk_session_matrix_verify",
                        "backend": "SQLiteSession",
                        "turn_count": 2,
                        "turns": [
                            {"phase": "session_initial"},
                            {"phase": "session_turn_2"},
                        ],
                        "final_item_count": 4,
                        "final_item_types": ["user", "message", "user", "message"],
                    },
                    "status": "completed",
                    "evidence_refs": ["artifact:agent_trace:verify_trace.json", "agent_session"],
                },
                {
                    "tool_name": "agent_stream_event",
                    "result": {
                        "type": "run_item_stream_event",
                        "name": "handoff_requested",
                        "raw_event_type": "none",
                        "item_class": "HandoffCallItem",
                        "tool_name": "transfer_to_harness_specialist",
                    },
                    "status": "completed",
                },
                {
                    "tool_name": "agent_stream_event",
                    "result": {
                        "type": "run_item_stream_event",
                        "name": "handoff_occured",
                        "raw_event_type": "none",
                        "item_class": "HandoffOutputItem",
                        "tool_name": "transfer_to_harness_specialist",
                    },
                    "status": "completed",
                },
            ],
            "verification_results": [],
        }
        scenario_sdk_summary = {
            "summary_requested": True,
            "task_id": "task_matrix_verify",
            "agent_model_response_count": 1,
            "agent_model_response_provider_counts": {"openai": 1},
            "agent_model_response_api_style_counts": {"responses": 1},
            "agent_phase_count": 2,
            "agent_session_count": 1,
            "agent_stream_event_count": 2,
            "agent_stream_event_type_counts": {"run_item_stream_event": 2},
            "agent_stream_event_name_counts": {
                "handoff_requested": 1,
                "handoff_occured": 1,
            },
            "agent_stream_raw_event_type_counts": {"none": 2},
            "agent_stream_item_class_counts": {
                "HandoffCallItem": 1,
                "HandoffOutputItem": 1,
            },
            "agent_stream_tool_name_counts": {
                "transfer_to_harness_specialist": 2,
            },
            "agent_approval_count": 2,
            "tool_progress_count": 1,
            "final_output_count": 1,
        }
        scenario_transcript_summary = {
            "summary_requested": True,
            "task_id": "task_matrix_verify",
            "agent_model_response_count": 1,
            "agent_model_response_provider_counts": {"openai": 1},
            "agent_model_response_api_style_counts": {"responses": 1},
            "agent_phase_count": 2,
            "agent_session_count": 1,
            "agent_stream_event_count": 2,
            "agent_stream_event_type_counts": {"run_item_stream_event": 2},
            "agent_stream_event_name_counts": {
                "handoff_requested": 1,
                "handoff_occured": 1,
            },
            "agent_stream_raw_event_type_counts": {"none": 2},
            "agent_stream_item_class_counts": {
                "HandoffCallItem": 1,
                "HandoffOutputItem": 1,
            },
            "agent_stream_tool_name_counts": {
                "transfer_to_harness_specialist": 2,
            },
            "agent_approval_count": 2,
            "tool_progress_count": 1,
            "final_output_count": 1,
            "item_type_counts": {
                "agent_model_response": 1,
                "agent_phase": 2,
                "agent_session": 1,
                "agent_stream_event": 2,
                "agent_approval": 2,
            },
        }
        scenario_event_replay = {
            "status": "completed",
            "source": "harness_event_sse_replay",
            "task_id": "task_matrix_verify",
            "after": 0,
            "events": [
                {
                    "id": "1",
                    "event": "harness_event",
                    "data": {"event_type": "task.queued", "status": "queued", "actor": "api"},
                },
                {
                    "id": "2",
                    "event": "harness_event",
                    "data": {"event_type": "tool_call.status_updated", "status": "completed", "actor": "tool"},
                },
            ],
            "event_count": 2,
            "keepalive_count": 0,
            "timed_out": False,
            "next_after": 2,
        }
        original_matrix_request = matrix_module.harness_api.request
        original_trace_ingest_request = matrix_module.trace_ingest.harness_api.request
        matrix_readback_paths: list[str] = []

        recorded_matrix_eval_cases: list[dict[str, Any]] = []

        def fake_matrix_request(_client_args: argparse.Namespace, method: str, path: str, body: Any = None) -> dict:
            del _client_args
            matrix_readback_paths.append(f"{method} {path}")
            if method == "POST" and path == "/eval-cases":
                assert isinstance(body, dict)
                stored = {
                    **body,
                    "eval_case_id": 7001 + len(recorded_matrix_eval_cases) + 1,
                    "client_id": "verify-client",
                }
                recorded_matrix_eval_cases.append(stored)
                return stored
            if method == "GET" and path == "/eval-cases":
                return {
                    "status": "completed",
                    "eval_cases": list(reversed(recorded_matrix_eval_cases)),
                }
            if method == "GET" and path == "/llm/diagnostics?limit=200":
                return {
                    "status": "completed",
                    "diagnostics": [
                        {
                            "client_id": "verify-client",
                            "created_at": "2026-06-29T00:00:00+00:00",
                            "status": provider_probe_validation["status"],
                            "provider": "openai",
                            "api_style": "responses",
                            "base_url": "https://api.openai.com/v1",
                            "model": "gpt-4.1-mini",
                            "result": {
                                "status": provider_probe_validation["status"],
                                "provider": provider_probe_validation["provider"],
                                "endpoint": provider_probe_validation["endpoint"],
                                "http_status": provider_probe_validation["http_status"],
                                "models_preview": ["gpt-4.1-mini"],
                                "agent_sdk_fit": provider_probe_validation["agent_sdk_fit"],
                                "provider_request_attempted": True,
                                "provider_response_received": True,
                                "external_api_called": True,
                            },
                            "diagnostic_id": 99,
                        }
                    ],
                }
            if path == "/tasks/task_matrix_aggregate_verify/evidence":
                return matrix_evidence
            if path == "/tasks/task_matrix_aggregate_verify/sdk-events?summary=true":
                return {"summary_requested": True, "task_id": "task_matrix_aggregate_verify"}
            if path == "/tasks/task_matrix_aggregate_verify/client-transcript?summary=true":
                return {"summary_requested": True, "task_id": "task_matrix_aggregate_verify"}
            if method == "SSE" and path == "/tasks/task_matrix_aggregate_verify/events?after=0":
                return matrix_event_replay
            if path == "/tasks/task_matrix_verify/evidence":
                return scenario_evidence
            if path == "/tasks/task_matrix_verify/sdk-events?summary=true":
                return scenario_sdk_summary
            if path == "/tasks/task_matrix_verify/client-transcript?summary=true":
                return scenario_transcript_summary
            if method == "SSE" and path == "/tasks/task_matrix_verify/events?after=0":
                return scenario_event_replay
            raise AssertionError(f"unexpected matrix readback request: {method} {path}")

        try:
            matrix_module.harness_api.request = fake_matrix_request
            matrix_module.trace_ingest.harness_api.request = fake_matrix_request
            existing_matrix_validation = matrix_module.validate_existing_matrix_readback(
                argparse.Namespace(base_url="http://verify", client_id="verify-client", api_key=None, dry_run=False),
                "task_matrix_aggregate_verify",
                matrix_output_path,
            )
        finally:
            matrix_module.harness_api.request = original_matrix_request
            matrix_module.trace_ingest.harness_api.request = original_trace_ingest_request
        assert existing_matrix_validation["status"] == "completed"
        assert existing_matrix_validation["validation"]["valid"] is True
        assert existing_matrix_validation["result_summary"]["artifact_count"] == 1
        assert "GET /tasks/task_matrix_aggregate_verify/evidence" in matrix_readback_paths
        assert "GET /tasks/task_matrix_aggregate_verify/sdk-events?summary=true" in matrix_readback_paths
        assert "GET /tasks/task_matrix_aggregate_verify/client-transcript?summary=true" in matrix_readback_paths
        assert "SSE /tasks/task_matrix_aggregate_verify/events?after=0" in matrix_readback_paths
        assert "GET /llm/diagnostics?limit=200" in matrix_readback_paths
        assert "GET /tasks/task_matrix_verify/evidence" not in matrix_readback_paths
        matrix_readback_paths.clear()
        try:
            matrix_module.harness_api.request = fake_matrix_request
            matrix_module.trace_ingest.harness_api.request = fake_matrix_request
            existing_matrix_with_scenarios = matrix_module.validate_existing_matrix_readback(
                argparse.Namespace(base_url="http://verify", client_id="verify-client", api_key=None, dry_run=False),
                "task_matrix_aggregate_verify",
                matrix_output_path,
                validate_published_scenarios=True,
            )
        finally:
            matrix_module.harness_api.request = original_matrix_request
            matrix_module.trace_ingest.harness_api.request = original_trace_ingest_request
        assert existing_matrix_with_scenarios["status"] == "completed"
        scenario_readback_validation = existing_matrix_with_scenarios[
            "published_scenario_readback_validation"
        ]
        assert scenario_readback_validation["valid"] is True
        assert scenario_readback_validation["published_scenario_count"] == 1
        assert scenario_readback_validation["failed_scenarios"] == []
        assert scenario_readback_validation["task_ids"] == {"approval": "task_matrix_verify"}
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"]["valid"] is True
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"]["error_scenarios"] == {}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_readback_status"
        ] == "completed"
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_readback_valid"
        ] is True
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_readback_check_count"
        ] == 84
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_count"
        ] == 1
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_readback_status_counts"
        ] == {"completed": 1}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_readback_failed_scenarios"
        ] == []
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_readback_failed_checks"
        ] == {}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_error_scenarios"
        ] == {}
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"]["generic_check_counts"] == {
            "approval": 52
        }
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"]["projection_check_counts"] == {
            "approval": 17
        }
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"]["event_replay_check_counts"] == {
            "approval": 15
        }
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"]["total_check_counts"] == {
            "approval": 84
        }
        assert scenario_readback_validation["results"][0]["validation"]["checks"][
            "agent_trace_artifact_digest_matches_expected"
        ] is True
        assert scenario_readback_validation["results"][0]["validation"]["trace_artifact_binding"][
            "matched_artifact_count"
        ] == 1
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "external_api_called_by_scenario"
        ] == {"approval": True}
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "event_replay_fetch_plans"
        ]["approval"]["basis"] == "evidence_counts"
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "event_replay_fetch_plans"
        ]["approval"]["max_events"] == 500
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "event_replay_fetch_plan_checks"
        ]["approval"] == {
            "event_replay_fetch_plan_covers_expected_raw_event_count": True,
            "event_replay_not_truncated_by_fetch_limit": True,
        }
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"]["generic_failed_checks"] == {}
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"]["projection_failed_checks"] == {}
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"]["event_replay_failed_checks"] == {}
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"]["stage_failed_checks"] == {}
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "trace_artifact_digest_matches"
        ] == {"approval": True}
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "trace_artifact_bindings"
        ]["approval"]["matched_artifact_count"] == 1
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "artifact_summaries_by_scenario"
        ]["approval"][0]["path"] == "agents-sdk-approval-verify.json"
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "artifact_summaries_by_scenario"
        ]["approval"][0]["digest"] == "sha256:scenario-trace"
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "session_summaries_by_scenario"
        ]["approval"][0]["agent_session_id"] == "sdk_session_matrix_verify"
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "session_summaries_by_scenario"
        ]["approval"][0]["backend"] == "SQLiteSession"
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "session_summaries_by_scenario"
        ]["approval"][0]["turn_count"] == 2
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "session_summaries_by_scenario"
        ]["approval"][0]["final_item_type_counts"] == {"message": 2, "user": 2}
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "stream_projection_summaries_by_scenario"
        ]["approval"]["sdk_events"]["agent_stream_event_name_counts"] == {
            "handoff_occured": 1,
            "handoff_requested": 1,
        }
        broken_scenario_digest_payload = json.loads(json.dumps(published_matrix_payload))
        broken_scenario_digest_payload["results"][0]["publish_readback"]["expected_trace_artifact"][
            "digest"
        ] = "sha256:wrong-scenario-trace"
        matrix_readback_paths.clear()
        try:
            matrix_module.harness_api.request = fake_matrix_request
            matrix_module.trace_ingest.harness_api.request = fake_matrix_request
            broken_scenario_digest_validation = matrix_module.validate_existing_published_scenario_readbacks(
                argparse.Namespace(base_url="http://verify", client_id="verify-client", api_key=None, dry_run=False),
                broken_scenario_digest_payload,
            )
        finally:
            matrix_module.harness_api.request = original_matrix_request
            matrix_module.trace_ingest.harness_api.request = original_trace_ingest_request
        assert broken_scenario_digest_validation["valid"] is False
        assert broken_scenario_digest_validation["failed_scenarios"] == ["approval"]
        assert broken_scenario_digest_validation["results"][0]["validation"]["valid"] is False
        assert "agent_trace_artifact_digest_matches_expected" in (
            broken_scenario_digest_validation["results"][0]["validation"]["failed_checks"]
        )
        assert broken_scenario_digest_validation["failed_checks"]["approval"] == [
            "generic:agent_trace_artifact_digest_matches_expected"
        ]
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "stream_projection_summaries_by_scenario"
        ]["approval"]["client_transcript"]["agent_stream_tool_name_counts"] == {
            "transfer_to_harness_specialist": 2
        }
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "guardrail_projection_summaries_by_scenario"
        ] == {}
        expected_published_projection_maps = {
            "approval": {
                "agent_model_response_provider_counts": {"openai": 1},
                "agent_model_response_api_style_counts": {"responses": 1},
            }
        }
        expected_published_projection_map_totals = {
            "agent_model_response_provider_counts": {"openai": 1},
            "agent_model_response_api_style_counts": {"responses": 1},
        }
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "sdk_events_projection_maps_by_scenario"
        ] == expected_published_projection_maps
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "client_transcript_projection_maps_by_scenario"
        ] == expected_published_projection_maps
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "sdk_events_projection_map_totals"
        ] == expected_published_projection_map_totals
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "client_transcript_projection_map_totals"
        ] == expected_published_projection_map_totals
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "event_replay_sse_event_name_counts"
        ] == {"approval": {"harness_event": 2}}
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "event_replay_event_actor_counts"
        ] == {"approval": {"api": 1, "tool": 1}}
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "event_replay_event_counts"
        ] == {"approval": 2}
        expected_published_scenario_event_counts = {
            "approval": {
                "artifact.recorded": 0,
                "observation.recorded": 0,
                "tool_call.recorded": 0,
                "tool_call.status_updated": 1,
            }
        }
        expected_published_scenario_event_totals = {
            "artifact.recorded": 0,
            "observation.recorded": 0,
            "tool_call.recorded": 0,
            "tool_call.status_updated": 1,
        }
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "event_replay_expected_event_counts"
        ] == expected_published_scenario_event_counts
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "event_replay_observed_event_counts"
        ] == expected_published_scenario_event_counts
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "event_replay_expected_event_totals"
        ] == expected_published_scenario_event_totals
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "event_replay_observed_event_totals"
        ] == expected_published_scenario_event_totals
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "event_replay_count_map_totals"
        ] == {
            "approval": {
                "event_actor_counts": 2,
                "event_status_counts": 2,
                "event_type_counts": 2,
                "sse_event_name_counts": 2,
            }
        }
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "event_replay_count_map_total_totals"
        ] == {
            "event_actor_counts": 2,
            "event_status_counts": 2,
            "event_type_counts": 2,
            "sse_event_name_counts": 2,
        }
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "event_replay_count_total_checks"
        ] == {"approval": expected_count_total_checks}
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "event_replay_sse_event_name_totals"
        ] == {"harness_event": 2}
        assert existing_matrix_with_scenarios["published_scenario_readback_summary"][
            "event_replay_event_actor_totals"
        ] == {"api": 1, "tool": 1}
        assert existing_matrix_with_scenarios["readback_validation_summary"]["status"] == "completed"
        assert existing_matrix_with_scenarios["readback_validation_summary"]["aggregate_valid"] is True
        assert existing_matrix_with_scenarios["readback_validation_summary"]["aggregate_check_count"] > 0
        aggregate_artifact = existing_matrix_with_scenarios["readback_validation_summary"][
            "aggregate_artifact"
        ]
        aggregate_artifact_checks = existing_matrix_with_scenarios["readback_validation_summary"][
            "aggregate_artifact_checks"
        ]
        assert aggregate_artifact["path"] == str(matrix_evidence_path)
        assert aggregate_artifact["expected_path"] == str(matrix_evidence_path)
        assert aggregate_artifact["digest"] == matrix_module.trace_ingest.file_digest(
            matrix_evidence_path
        )
        assert aggregate_artifact["digest_from_recorded_path"] == aggregate_artifact["digest"]
        assert aggregate_artifact["digest_from_expected_path"] == aggregate_artifact["digest"]
        assert aggregate_artifact["recorded_path_available"] is True
        assert aggregate_artifact["expected_path_available"] is True
        assert aggregate_artifact_checks == {
            "matrix_artifact_recorded": True,
            "matrix_artifact_path_recorded": True,
            "matrix_artifact_digest_recorded": True,
            "matrix_artifact_path_matches_payload": True,
            "matrix_artifact_digest_matches_recorded_path": True,
            "matrix_artifact_digest_matches_expected_path": True,
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_probe_valid"
        ] is True
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_probe_diagnostic_id"
        ] == 99
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_probe_failed_checks"
        ] == []
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_probe_check_count"
        ] == 8
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_probe_checks"
        ]["probe_http_200"] is True
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_probe_checks"
        ]["probe_external_api_called"] is True
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_probe_status"
        ] == "completed"
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_probe_http_status"
        ] == 200
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_probe_endpoint"
        ] == "https://api.openai.com/v1/models"
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_probe_models_preview_count"
        ] == 1
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_probe_profile"
        ]["model"] == "gpt-4.1-mini"
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_probe_profile"
        ]["api_key_env"] == "OPENAI_API_KEY"
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_probe_agent_sdk_fit"
        ]["stateful_response_ids_assumed"] is True
        provider_diagnostic_readback = existing_matrix_with_scenarios[
            "provider_diagnostic_readback"
        ]
        assert provider_diagnostic_readback["status"] == "completed"
        assert provider_diagnostic_readback["valid"] is True
        assert provider_diagnostic_readback["expected_diagnostic_id"] == 99
        assert provider_diagnostic_readback["matched_diagnostic_id"] == 99
        assert provider_diagnostic_readback["diagnostic_count"] == 1
        assert provider_diagnostic_readback["failed_checks"] == []
        assert provider_diagnostic_readback["checks"][
            "provider_diagnostic_readback_matched_expected_id"
        ] is True
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_diagnostic_readback_status"
        ] == "completed"
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_diagnostic_readback_valid"
        ] is True
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_diagnostic_readback_expected_id"
        ] == 99
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_diagnostic_readback_matched_id"
        ] == 99
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_diagnostic_readback_matched"
        ] is True
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_diagnostic_readback_diagnostic_count"
        ] == 1
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_diagnostic_readback_matched_provider"
        ]["model"] == "gpt-4.1-mini"
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_diagnostic_readback_matched_provider_response_received"
        ] is True
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_diagnostic_readback_matched_external_api_called"
        ] is True
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "provider_diagnostic_readback_failed_checks"
        ] == []
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "scenario_event_replay_expected_event_counts"
        ] == {
            "approval": {
                "artifact.recorded": 1,
                "observation.recorded": 2,
                "tool_call.recorded": 1,
                "tool_call.status_updated": 1,
            }
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "scenario_event_replay_observed_event_counts"
        ] == {
            "approval": {
                "artifact.recorded": 1,
                "observation.recorded": 2,
                "tool_call.recorded": 1,
                "tool_call.status_updated": 1,
            }
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "scenario_event_replay_sse_event_name_counts"
        ] == {"approval": {"harness_event": 7}}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "scenario_event_replay_event_type_counts"
        ] == {
            "approval": {
                "artifact.recorded": 1,
                "observation.recorded": 2,
                "task.paused": 1,
                "task.queued": 1,
                "tool_call.recorded": 1,
                "tool_call.status_updated": 1,
            }
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "scenario_event_replay_event_status_counts"
        ] == {"approval": {"completed": 5, "paused": 1, "queued": 1}}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "scenario_event_replay_event_actor_counts"
        ] == {"approval": {"api": 2, "artifact": 1, "tool": 4}}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "scenario_event_replay_sse_event_name_totals"
        ] == {"harness_event": 7}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "scenario_event_replay_event_actor_totals"
        ] == {"api": 2, "artifact": 1, "tool": 4}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "scenario_event_replay_expected_event_totals"
        ] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 1,
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "scenario_event_replay_observed_event_totals"
        ] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 1,
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "tool_call_status_update_expected_counts"
        ] == {"approval": 1}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "tool_call_status_updated_event_counts"
        ] == {"approval": 1}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "tool_call_status_update_expected_total"
        ] == 1
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "tool_call_status_updated_event_total"
        ] == 1
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "scenario_api_usage_totals"
        ] == {"approval": {"total_tokens": 7}}
        assert existing_matrix_with_scenarios["readback_validation_summary"]["api_usage_totals"] == {
            "total_tokens": 7
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "scenario_sdk_events_projection_counts"
        ] == {
            "approval": {
                "agent_model_response_count": 1,
                "agent_guardrail_count": 1,
                "agent_run_error_count": 1,
                "agent_stream_event_count": 5,
                "tool_progress_count": 2,
                "final_output_count": 1,
            }
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "scenario_client_transcript_projection_counts"
        ] == {
            "approval": {
                "agent_model_response_count": 1,
                "agent_guardrail_count": 1,
                "agent_run_error_count": 1,
                "agent_stream_event_count": 5,
                "tool_progress_count": 1,
                "final_output_count": 1,
            }
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "sdk_events_projection_totals"
        ]["tool_progress_count"] == 2
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "client_transcript_projection_totals"
        ]["tool_progress_count"] == 1
        assert existing_matrix_with_scenarios["readback_validation_summary"]["projection_total_deltas"] == {
            "tool_progress_count": {"sdk_events": 2, "client_transcript": 1, "delta": 1}
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "projection_total_delta_status"
        ] == "allowed_deltas"
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "projection_total_delta_assessment"
        ]["items"]["tool_progress_count"]["status"] == "allowed"
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "projection_total_delta_allowed_keys"
        ] == ["tool_progress_count"]
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "projection_total_delta_unexpected_keys"
        ] == []
        assert existing_matrix_with_scenarios["readback_validation_summary"]["published_scenario_readback"][
            "valid"
        ] is True
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_generic_check_counts"
        ] == {"approval": 52}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_projection_check_counts"
        ] == {"approval": 17}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_check_counts"
        ] == {"approval": 15}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_total_check_counts"
        ] == {"approval": 84}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_trace_artifact_digest_matches"
        ] == {"approval": True}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_trace_artifact_bindings"
        ]["approval"]["matched_artifact_count"] == 1
        assert existing_matrix_with_scenarios["readback_validation_summary"]["external_api_called"] is True
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "scenario_external_api_called"
        ] == {"approval": True}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_external_api_called"
        ] == {"approval": True}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "aggregate_event_replay_fetch_plan"
        ]["basis"] == "evidence_counts"
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "aggregate_event_replay_fetch_plan_checks"
        ] == {
            "event_replay_fetch_plan_covers_expected_raw_event_count": True,
            "event_replay_not_truncated_by_fetch_limit": True,
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "aggregate_event_replay_event_count"
        ] == 6
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "aggregate_event_replay_count_map_totals"
        ] == expected_aggregate_count_map_totals
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "aggregate_event_replay_count_total_checks"
        ] == expected_count_total_checks
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "aggregate_event_replay_expected_event_counts"
        ] == expected_aggregate_event_counts
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "aggregate_event_replay_observed_event_counts"
        ] == expected_aggregate_event_counts
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "aggregate_event_replay_event_type_counts"
        ] == {
            "artifact.recorded": 1,
            "observation.recorded": 3,
            "task.paused": 1,
            "task.queued": 1,
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "aggregate_event_replay_event_actor_counts"
        ] == {"api": 2, "artifact": 1, "tool": 3}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_fetch_plans"
        ]["approval"]["basis"] == "evidence_counts"
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_fetch_plan_checks"
        ]["approval"]["event_replay_not_truncated_by_fetch_limit"] is True
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_sse_event_name_counts"
        ] == {"approval": {"harness_event": 2}}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_event_type_counts"
        ] == {"approval": {"task.queued": 1, "tool_call.status_updated": 1}}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_event_status_counts"
        ] == {"approval": {"completed": 1, "queued": 1}}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_event_actor_counts"
        ] == {"approval": {"api": 1, "tool": 1}}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_event_counts"
        ] == {"approval": 2}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_expected_event_counts"
        ] == expected_published_scenario_event_counts
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_observed_event_counts"
        ] == expected_published_scenario_event_counts
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_expected_event_totals"
        ] == expected_published_scenario_event_totals
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_observed_event_totals"
        ] == expected_published_scenario_event_totals
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_count_map_totals"
        ] == {
            "approval": {
                "event_actor_counts": 2,
                "event_status_counts": 2,
                "event_type_counts": 2,
                "sse_event_name_counts": 2,
            }
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_count_map_total_totals"
        ] == {
            "event_actor_counts": 2,
            "event_status_counts": 2,
            "event_type_counts": 2,
            "sse_event_name_counts": 2,
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_count_total_checks"
        ] == {"approval": expected_count_total_checks}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_sse_event_name_totals"
        ] == {"harness_event": 2}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_event_type_totals"
        ] == {"task.queued": 1, "tool_call.status_updated": 1}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_event_status_totals"
        ] == {"completed": 1, "queued": 1}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_event_actor_totals"
        ] == {"api": 1, "tool": 1}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_generic_failed_checks"
        ] == {}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_projection_failed_checks"
        ] == {}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_failed_checks"
        ] == {}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_stage_failed_checks"
        ] == {}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_task_bindings"
        ]["approval"]["expected_task_id"] == "task_matrix_verify"
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_artifact_summaries"
        ]["approval"][0]["metadata"]["trace_id"] == "trace_verify_approval"
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_session_summaries"
        ]["approval"][0]["agent_session_id"] == "sdk_session_matrix_verify"
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_stream_projection_summaries"
        ]["approval"]["sdk_events"]["agent_stream_event_name_counts"] == {
            "handoff_occured": 1,
            "handoff_requested": 1,
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_approval_projection_summaries"
        ]["approval"]["sdk_events"]["agent_approval_count"] == 2
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_approval_projection_summaries"
        ]["approval"]["client_transcript"]["agent_phase_count"] == 2
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_approval_projection_summaries"
        ]["approval"]["approval_counts_match"] is True
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_guardrail_projection_summaries"
        ] == {}
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_sdk_events_projection_maps"
        ] == expected_published_projection_maps
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_client_transcript_projection_maps"
        ] == expected_published_projection_maps
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_sdk_events_projection_map_totals"
        ] == expected_published_projection_map_totals
        assert existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_client_transcript_projection_map_totals"
        ] == expected_published_projection_map_totals
        assert existing_matrix_with_scenarios["readback_validation_summary"]["revalidation_inputs"] == {
            "validate_matrix_readback_task_id": "task_matrix_aggregate_verify",
            "validate_matrix_json": str(matrix_output_path),
            "validate_published_scenarios": True,
            "client_id": "verify-client",
            "base_url": "http://verify",
            "compact_output": True,
        }
        assert existing_matrix_with_scenarios["readback_validation_summary"]["revalidation_ready"] is True
        assert existing_matrix_with_scenarios["readback_validation_summary"]["revalidation_missing_fields"] == []
        assert existing_matrix_with_scenarios["readback_validation_summary"]["revalidation_required_fields"] == [
            "validate_matrix_readback_task_id",
            "validate_matrix_json",
            "client_id",
            "base_url",
        ]
        assert existing_matrix_with_scenarios["readback_validation_summary"]["revalidation_cli_args"] == [
            "--validate-matrix-readback-task-id",
            "task_matrix_aggregate_verify",
            "--validate-matrix-json",
            str(matrix_output_path),
            "--validate-published-scenarios",
            "--compact-output",
            "--client-id",
            "verify-client",
            "--base-url",
            "http://verify",
        ]
        assert existing_matrix_with_scenarios["readback_validation_summary"]["summary_revalidation_ready"] is False
        assert existing_matrix_with_scenarios["readback_validation_summary"]["summary_revalidation_missing_fields"] == [
            "revalidate_from_summary_json"
        ]
        assert scenario_readback_validation["results"][0]["validation"]["valid"] is True
        assert scenario_readback_validation["results"][0]["scenario_projection_validation"]["valid"] is True
        assert scenario_readback_validation["results"][0]["scenario_event_replay_validation"]["valid"] is True
        assert "GET /tasks/task_matrix_verify/evidence" in matrix_readback_paths
        assert "GET /tasks/task_matrix_verify/sdk-events?summary=true" in matrix_readback_paths
        assert "GET /tasks/task_matrix_verify/client-transcript?summary=true" in matrix_readback_paths
        assert "SSE /tasks/task_matrix_verify/events?after=0" in matrix_readback_paths
        summary_revalidation_path = Path(temp) / "matrix-readback-summary.json"
        summary_revalidation_path.write_text(
            json.dumps(existing_matrix_with_scenarios["readback_validation_summary"]),
            encoding="utf-8",
        )
        matrix_readback_paths.clear()
        try:
            matrix_module.harness_api.request = fake_matrix_request
            matrix_module.trace_ingest.harness_api.request = fake_matrix_request
            summary_file_revalidation = matrix_module.validate_revalidation_summary_json(
                argparse.Namespace(
                    base_url="http://ignored",
                    client_id=None,
                    api_key=None,
                    dry_run=False,
                    validate_matrix_readback_task_id="",
                    validate_matrix_json="",
                    validate_published_scenarios=False,
                ),
                summary_revalidation_path,
            )
        finally:
            matrix_module.harness_api.request = original_matrix_request
            matrix_module.trace_ingest.harness_api.request = original_trace_ingest_request
        assert summary_file_revalidation["status"] == "completed"
        assert summary_file_revalidation["revalidation_source_summary_json"] == str(summary_revalidation_path)
        assert summary_file_revalidation["revalidation_source_summary_key"] == "root"
        assert summary_file_revalidation["revalidation_source_ready"] is True
        assert summary_file_revalidation["readback_validation_summary"]["revalidation_source_summary_json"] == (
            str(summary_revalidation_path)
        )
        assert summary_file_revalidation["readback_validation_summary"]["revalidation_cli_args"] == [
            "--validate-matrix-readback-task-id",
            "task_matrix_aggregate_verify",
            "--validate-matrix-json",
            str(matrix_output_path),
            "--validate-published-scenarios",
            "--compact-output",
            "--client-id",
            "verify-client",
            "--base-url",
            "http://verify",
        ]
        assert summary_file_revalidation["readback_validation_summary"]["scenario_api_usage_totals"] == {
            "approval": {"total_tokens": 7}
        }
        assert summary_file_revalidation["readback_validation_summary"][
            "provider_probe_diagnostic_id"
        ] == 99
        assert summary_file_revalidation["readback_validation_summary"][
            "provider_probe_failed_checks"
        ] == []
        assert summary_file_revalidation["readback_validation_summary"][
            "provider_diagnostic_readback_valid"
        ] is True
        assert summary_file_revalidation["readback_validation_summary"][
            "provider_diagnostic_readback_matched_id"
        ] == 99
        assert summary_file_revalidation["readback_validation_summary"][
            "scenario_event_replay_expected_event_counts"
        ]["approval"]["tool_call.status_updated"] == 1
        assert summary_file_revalidation["readback_validation_summary"][
            "scenario_event_replay_observed_event_counts"
        ]["approval"]["tool_call.status_updated"] == 1
        assert summary_file_revalidation["readback_validation_summary"][
            "tool_call_status_update_expected_counts"
        ] == {"approval": 1}
        assert summary_file_revalidation["readback_validation_summary"][
            "tool_call_status_updated_event_counts"
        ] == {"approval": 1}
        assert summary_file_revalidation["readback_validation_summary"][
            "scenario_sdk_events_projection_counts"
        ]["approval"]["tool_progress_count"] == 2
        assert summary_file_revalidation["readback_validation_summary"][
            "scenario_client_transcript_projection_counts"
        ]["approval"]["tool_progress_count"] == 1
        assert summary_file_revalidation["readback_validation_summary"]["projection_total_deltas"] == {
            "tool_progress_count": {"sdk_events": 2, "client_transcript": 1, "delta": 1}
        }
        assert summary_file_revalidation["readback_validation_summary"][
            "projection_total_delta_assessment"
        ]["items"]["tool_progress_count"]["status"] == "allowed"
        assert summary_file_revalidation["readback_validation_summary"]["summary_revalidation_inputs"] == {
            "revalidate_from_summary_json": str(summary_revalidation_path),
            "compact_output": True,
        }
        assert summary_file_revalidation["readback_validation_summary"]["summary_revalidation_ready"] is True
        assert summary_file_revalidation["readback_validation_summary"]["summary_revalidation_missing_fields"] == []
        assert summary_file_revalidation["readback_validation_summary"]["summary_revalidation_cli_args"] == [
            "--revalidate-from-summary-json",
            str(summary_revalidation_path),
            "--compact-output",
        ]
        assert "GET /tasks/task_matrix_aggregate_verify/evidence" in matrix_readback_paths
        assert "GET /tasks/task_matrix_verify/evidence" in matrix_readback_paths
        assert "GET /llm/diagnostics?limit=200" in matrix_readback_paths

        def fake_missing_provider_diagnostic_request(
            _client_args: argparse.Namespace, method: str, path: str, body: Any = None
        ) -> dict:
            if method == "GET" and path == "/llm/diagnostics?limit=200":
                matrix_readback_paths.append(f"{method} {path}")
                return {"status": "completed", "diagnostics": []}
            return fake_matrix_request(_client_args, method, path, body)

        matrix_readback_paths.clear()
        try:
            matrix_module.harness_api.request = fake_missing_provider_diagnostic_request
            matrix_module.trace_ingest.harness_api.request = fake_missing_provider_diagnostic_request
            missing_provider_diagnostic_validation = matrix_module.validate_existing_matrix_readback(
                argparse.Namespace(base_url="http://verify", client_id="verify-client", api_key=None, dry_run=False),
                "task_matrix_aggregate_verify",
                matrix_output_path,
            )
        finally:
            matrix_module.harness_api.request = original_matrix_request
            matrix_module.trace_ingest.harness_api.request = original_trace_ingest_request
        assert missing_provider_diagnostic_validation["status"] == "failed"
        assert missing_provider_diagnostic_validation["validation"]["valid"] is True
        assert missing_provider_diagnostic_validation["provider_diagnostic_readback"]["valid"] is False
        assert missing_provider_diagnostic_validation["provider_diagnostic_readback"]["diagnostic_count"] == 0
        assert (
            "provider_diagnostic_readback_matched_expected_id"
            in missing_provider_diagnostic_validation["provider_diagnostic_readback"]["failed_checks"]
        )
        assert missing_provider_diagnostic_validation["readback_validation_summary"][
            "provider_diagnostic_readback_valid"
        ] is False
        assert missing_provider_diagnostic_validation["readback_validation_summary"][
            "provider_diagnostic_readback_failed_checks"
        ] == [
            "provider_diagnostic_readback_matched_expected_id",
            "provider_diagnostic_readback_status_matches",
            "provider_diagnostic_readback_model_matches",
            "provider_diagnostic_readback_api_style_matches",
            "provider_diagnostic_readback_request_attempted_matches",
            "provider_diagnostic_readback_response_received_matches",
            "provider_diagnostic_readback_external_api_called_matches",
        ]
        assert "GET /llm/diagnostics?limit=200" in matrix_readback_paths
        missing_scenario_payload = json.loads(json.dumps(published_matrix_payload))
        missing_scenario_payload["results"][0]["publish_readback"]["task_id"] = "task_missing_verify"
        matrix_readback_paths.clear()

        def fake_missing_scenario_request(
            _client_args: argparse.Namespace, method: str, path: str, body: Any = None
        ) -> dict:
            del _client_args, method, body
            matrix_readback_paths.append(f"GET {path}")
            if path.startswith("/tasks/task_missing_verify/"):
                raise SystemExit("HTTP 404: scenario task not found")
            raise AssertionError(f"unexpected missing scenario request: {path}")

        try:
            matrix_module.harness_api.request = fake_missing_scenario_request
            matrix_module.trace_ingest.harness_api.request = fake_missing_scenario_request
            missing_scenario_validation = matrix_module.validate_existing_published_scenario_readbacks(
                argparse.Namespace(base_url="http://verify", client_id="verify-client", api_key=None, dry_run=False),
                missing_scenario_payload,
            )
        finally:
            matrix_module.harness_api.request = original_matrix_request
            matrix_module.trace_ingest.harness_api.request = original_trace_ingest_request
        assert missing_scenario_validation["status"] == "failed"
        assert missing_scenario_validation["valid"] is False
        assert missing_scenario_validation["failed_scenarios"] == ["approval"]
        assert missing_scenario_validation["failed_checks"]["approval"] == ["readback_error:SystemExit"]
        assert missing_scenario_validation["results"][0]["error_type"] == "SystemExit"
        assert missing_scenario_validation["summary"]["error_scenarios"]["approval"]["task_id"] == (
            "task_missing_verify"
        )
        broken_scenario_event_replay = json.loads(json.dumps(scenario_event_replay))
        broken_scenario_event_replay["events"] = []
        broken_scenario_event_replay["event_count"] = 0
        broken_scenario_event_replay["next_after"] = 0
        matrix_readback_paths.clear()

        def fake_broken_scenario_request(
            _client_args: argparse.Namespace, method: str, path: str, body: Any = None
        ) -> dict:
            if method == "SSE" and path == "/tasks/task_matrix_verify/events?after=0":
                matrix_readback_paths.append(f"{method} {path}")
                return broken_scenario_event_replay
            return fake_matrix_request(_client_args, method, path, body)

        try:
            matrix_module.harness_api.request = fake_broken_scenario_request
            matrix_module.trace_ingest.harness_api.request = fake_broken_scenario_request
            broken_existing_matrix_with_scenarios = matrix_module.validate_existing_matrix_readback(
                argparse.Namespace(base_url="http://verify", client_id="verify-client", api_key=None, dry_run=False),
                "task_matrix_aggregate_verify",
                matrix_output_path,
                validate_published_scenarios=True,
            )
        finally:
            matrix_module.harness_api.request = original_matrix_request
            matrix_module.trace_ingest.harness_api.request = original_trace_ingest_request
        assert broken_existing_matrix_with_scenarios["status"] == "failed"
        assert broken_existing_matrix_with_scenarios["readback_validation_summary"]["status"] == "failed"
        assert broken_existing_matrix_with_scenarios["readback_validation_summary"]["aggregate_valid"] is True
        assert broken_existing_matrix_with_scenarios["readback_validation_summary"]["revalidation_inputs"][
            "validate_matrix_readback_task_id"
        ] == "task_matrix_aggregate_verify"
        assert broken_existing_matrix_with_scenarios["readback_validation_summary"]["revalidation_ready"] is True
        assert broken_existing_matrix_with_scenarios["readback_validation_summary"]["revalidation_missing_fields"] == []
        assert broken_existing_matrix_with_scenarios["readback_validation_summary"]["revalidation_cli_args"][
            :2
        ] == ["--validate-matrix-readback-task-id", "task_matrix_aggregate_verify"]
        assert broken_existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_event_replay_check_counts"
        ] == {"approval": 15}
        assert broken_existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_total_check_counts"
        ] == {"approval": 84}
        assert broken_existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_readback_status"
        ] == "failed"
        assert broken_existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_readback_valid"
        ] is False
        assert broken_existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_readback_failed_scenarios"
        ] == ["approval"]
        assert broken_existing_matrix_with_scenarios["readback_validation_summary"][
            "published_scenario_readback_status_counts"
        ] == {"failed": 1}
        assert (
            "event_replay_covers_tool_call_status_updated"
            in broken_existing_matrix_with_scenarios["readback_validation_summary"][
                "published_scenario_event_replay_failed_checks"
            ]["approval"]
        )
        assert (
            "event_replay:event_replay_covers_tool_call_status_updated"
            in broken_existing_matrix_with_scenarios["readback_validation_summary"][
                "published_scenario_stage_failed_checks"
            ]["approval"]
        )
        broken_scenario_validation = broken_existing_matrix_with_scenarios[
            "published_scenario_readback_validation"
        ]
        assert broken_scenario_validation["valid"] is False
        assert broken_scenario_validation["failed_scenarios"] == ["approval"]
        assert (
            "event_replay:event_replay_covers_tool_call_status_updated"
            in broken_scenario_validation["failed_checks"]["approval"]
        )
        matrix_readback_paths.clear()
        recorded_matrix_eval_cases.clear()
        try:
            matrix_module.harness_api.request = fake_matrix_request
            eval_recorded_payload = matrix_module.maybe_record_failure_eval_case(
                argparse.Namespace(
                    base_url="http://verify",
                    client_id="verify-client",
                    api_key=None,
                    dry_run=False,
                    record_eval_case_on_failure=True,
                    eval_case_verifier_command="",
                ),
                broken_existing_matrix_with_scenarios,
            )
        finally:
            matrix_module.harness_api.request = original_matrix_request
        assert eval_recorded_payload["eval_case_recording"]["status"] == "recorded"
        assert eval_recorded_payload["eval_case_recording"]["eval_case_id"] == 7002
        assert eval_recorded_payload["eval_case_recording"]["source_task_id"] == "task_matrix_aggregate_verify"
        assert eval_recorded_payload["eval_case_recording"]["failure_label"].startswith(
            "scenario:approval:event_replay:"
        )
        assert "POST /eval-cases" in matrix_readback_paths
        assert "GET /eval-cases" in matrix_readback_paths
        assert eval_recorded_payload["eval_case_recording"]["readback_status"] == "matched"
        assert eval_recorded_payload["eval_case_recording"]["readback_valid"] is True
        assert eval_recorded_payload["eval_case_recording"]["readback_failed_checks"] == []
        assert eval_recorded_payload["eval_case_recording"]["readback_match_strategy"] == "eval_case_id"
        assert eval_recorded_payload["eval_case_recording"]["readback_matched_eval_case_id"] == 7002
        assert eval_recorded_payload["eval_case_recording"]["readback_matching_eval_case_count"] == 1
        assert eval_recorded_payload["eval_case_recording"]["readback_matching_eval_case_ids"] == [7002]
        assert eval_recorded_payload["eval_case_recording"]["readback_signature_metadata_status"] == "current"
        assert eval_recorded_payload["eval_case_recording"]["duplicate_prevented"] is False
        assert eval_recorded_payload["eval_case_recording"]["post_attempted"] is True
        assert eval_recorded_payload["eval_case_recording"]["post_required_reason"] == (
            "dedupe_lookup_not_found"
        )
        assert eval_recorded_payload["eval_case_recording"]["dedupe_lookup_status"] == "not_found"
        assert eval_recorded_payload["eval_case_recording"]["dedupe_lookup_readback_status"] == "not_found"
        assert eval_recorded_payload["eval_case_recording"]["dedupe_lookup_valid"] is False
        assert eval_recorded_payload["eval_case_recording"]["dedupe_lookup_matching_eval_case_count"] == 0
        assert eval_recorded_payload["eval_case_recording"]["dedupe_lookup_matching_eval_case_ids"] == []
        assert eval_recorded_payload["eval_case_recording"][
            "readback_matching_eval_case_stored_signature_missing_ids"
        ] == []
        assert eval_recorded_payload["eval_case_recording"][
            "readback_matching_eval_case_stored_signature_mismatch_ids"
        ] == []
        first_failure_signature = eval_recorded_payload["eval_case_recording"]["failure_signature"]
        assert first_failure_signature.startswith("sha256:")
        assert len(first_failure_signature) == 71
        first_signature_material = eval_recorded_payload["eval_case_recording"][
            "failure_signature_material"
        ]
        first_signature_material_digest = eval_recorded_payload["eval_case_recording"][
            "failure_signature_material_digest"
        ]
        assert eval_recorded_payload["eval_case_recording"]["failure_signature_version"] == (
            "agents_sdk_matrix_failure_v2"
        )
        assert first_signature_material["signature_version"] == "agents_sdk_matrix_failure_v2"
        assert first_signature_material_digest == first_failure_signature
        assert matrix_module.eval_case_failure_signature_material_digest(
            first_signature_material
        ) == first_failure_signature
        assert eval_recorded_payload["eval_case_recording"]["readback_checks"][
            "eval_case_readback_signature_matches_material"
        ] is True
        assert eval_recorded_payload["eval_case_recording"]["readback_checks"][
            "eval_case_readback_signature_version_current"
        ] is True
        assert "--base-url" not in first_signature_material["verifier_command"]
        assert first_signature_material["input"]["task_id"] == "task_matrix_aggregate_verify"
        assert first_signature_material["input"]["validate_published_scenarios"] is True
        variant_body = dict(recorded_matrix_eval_cases[-1])
        variant_body["verifier_command"] = variant_body["verifier_command"].replace(
            "http://verify",
            "http://localhost:8000/api/harness/v1",
        )
        assert matrix_module.eval_case_failure_signature(variant_body) == first_failure_signature
        assert "--base-url" not in matrix_module.canonical_eval_case_verifier_command(
            variant_body["verifier_command"]
        )
        assert eval_recorded_payload["eval_case_recording"]["readback_checks"][
            "eval_case_readback_verifier_command_matches"
        ] is True
        assert recorded_matrix_eval_cases[-1]["metadata"]["failure_signature"] == first_failure_signature
        assert recorded_matrix_eval_cases[-1]["metadata"]["failure_signature_version"] == (
            "agents_sdk_matrix_failure_v2"
        )
        assert recorded_matrix_eval_cases[-1]["metadata"]["failure_signature_material"] == (
            first_signature_material
        )
        assert recorded_matrix_eval_cases[-1]["metadata"]["failure_signature_material_digest"] == (
            first_signature_material_digest
        )
        assert recorded_matrix_eval_cases[-1]["source_task_id"] == "task_matrix_aggregate_verify"
        assert recorded_matrix_eval_cases[-1]["expected"]["status"] == "completed"
        assert recorded_matrix_eval_cases[-1]["input"]["validate_published_scenarios"] is True
        assert "agents_sdk_smoke_matrix.py" in recorded_matrix_eval_cases[-1]["verifier_command"]
        assert "--validate-published-scenarios" in recorded_matrix_eval_cases[-1]["verifier_command"]
        assert f"task:task_matrix_aggregate_verify" in recorded_matrix_eval_cases[-1]["evidence_refs"]
        assert eval_recorded_payload["readback_validation_summary"]["eval_case_recording"][
            "eval_case_id"
        ] == 7002
        assert eval_recorded_payload["readback_validation_summary"]["eval_case_recording"][
            "readback_valid"
        ] is True
        matrix_readback_paths.clear()
        recorded_matrix_eval_cases.append(
            {
                **recorded_matrix_eval_cases[-1],
                "eval_case_id": 7003,
                "verifier_command": recorded_matrix_eval_cases[-1]["verifier_command"].replace(
                    "http://verify",
                    "http://localhost:8000/api/harness/v1",
                ),
                "metadata": {
                    **recorded_matrix_eval_cases[-1]["metadata"],
                    "failure_signature": "sha256:" + "0" * 64,
                },
            }
        )
        recorded_eval_case_count = len(recorded_matrix_eval_cases)
        try:
            matrix_module.harness_api.request = fake_matrix_request
            eval_reused_payload = matrix_module.maybe_record_failure_eval_case(
                argparse.Namespace(
                    base_url="http://verify",
                    client_id="verify-client",
                    api_key=None,
                    dry_run=False,
                    record_eval_case_on_failure=True,
                    eval_case_verifier_command="",
                ),
                broken_existing_matrix_with_scenarios,
            )
        finally:
            matrix_module.harness_api.request = original_matrix_request
        assert eval_reused_payload["eval_case_recording"]["status"] == "already_recorded"
        assert eval_reused_payload["eval_case_recording"]["duplicate_prevented"] is True
        assert eval_reused_payload["eval_case_recording"]["post_attempted"] is False
        assert eval_reused_payload["eval_case_recording"]["post_skipped_reason"] == (
            "matching_eval_case_found"
        )
        assert eval_reused_payload["eval_case_recording"]["dedupe_lookup_status"] == "matched"
        assert eval_reused_payload["eval_case_recording"]["dedupe_lookup_readback_status"] == "matched"
        assert eval_reused_payload["eval_case_recording"]["dedupe_lookup_valid"] is True
        assert eval_reused_payload["eval_case_recording"]["eval_case_id"] == 7003
        assert eval_reused_payload["eval_case_recording"]["readback_status"] == "matched"
        assert eval_reused_payload["eval_case_recording"]["readback_valid"] is True
        assert eval_reused_payload["eval_case_recording"]["readback_failed_checks"] == []
        assert eval_reused_payload["eval_case_recording"]["readback_match_strategy"] == (
            "name_source_failure_label"
        )
        assert eval_reused_payload["eval_case_recording"]["readback_matching_eval_case_count"] == 2
        assert eval_reused_payload["eval_case_recording"]["readback_matching_eval_case_ids"] == [7003, 7002]
        assert eval_reused_payload["eval_case_recording"]["dedupe_lookup_matching_eval_case_count"] == 2
        assert eval_reused_payload["eval_case_recording"]["dedupe_lookup_matching_eval_case_ids"] == [
            7003,
            7002,
        ]
        assert eval_reused_payload["eval_case_recording"]["readback_matched_eval_case_id"] == 7003
        assert eval_reused_payload["eval_case_recording"]["failure_signature"] == first_failure_signature
        assert eval_reused_payload["eval_case_recording"]["failure_signature_version"] == (
            "agents_sdk_matrix_failure_v2"
        )
        assert eval_reused_payload["eval_case_recording"]["failure_signature_material"] == (
            first_signature_material
        )
        assert eval_reused_payload["eval_case_recording"]["failure_signature_material_digest"] == (
            first_signature_material_digest
        )
        assert eval_reused_payload["eval_case_recording"]["readback_checks"][
            "eval_case_readback_signature_matches_material"
        ] is True
        assert eval_reused_payload["eval_case_recording"]["readback_checks"][
            "eval_case_readback_signature_version_current"
        ] is True
        assert eval_reused_payload["eval_case_recording"]["readback_signature_metadata_status"] == "mismatch"
        assert eval_reused_payload["eval_case_recording"][
            "readback_matching_eval_case_stored_signature_mismatch_ids"
        ] == [7003]
        assert eval_reused_payload["eval_case_recording"][
            "readback_matching_eval_case_stored_signature_missing_ids"
        ] == []
        assert eval_reused_payload["eval_case_recording"]["readback_checks"][
            "eval_case_readback_verifier_command_matches"
        ] is True
        assert eval_reused_payload["eval_case_recording"]["readback_checks"][
            "eval_case_readback_verifier_command_exact_matches"
        ] is False
        assert eval_reused_payload["eval_case_recording"]["readback_non_blocking_failed_checks"] == [
            "eval_case_readback_verifier_command_exact_matches"
        ]
        assert eval_reused_payload["eval_case_recording"]["readback_failed_checks"] == []
        assert len(recorded_matrix_eval_cases) == recorded_eval_case_count
        assert "GET /eval-cases" in matrix_readback_paths
        assert "POST /eval-cases" not in matrix_readback_paths
        assert eval_reused_payload["readback_validation_summary"]["eval_case_recording"][
            "status"
        ] == "already_recorded"
        lookup_failed_paths: list[str] = []
        lookup_failed_eval_cases: list[dict[str, Any]] = []
        lookup_failed_get_count = 0

        def fake_lookup_failed_then_record_request(
            _client_args: argparse.Namespace, method: str, path: str, body: Any = None
        ) -> dict:
            nonlocal lookup_failed_get_count
            del _client_args
            lookup_failed_paths.append(f"{method} {path}")
            if method == "GET" and path == "/eval-cases":
                lookup_failed_get_count += 1
                if lookup_failed_get_count == 1:
                    raise SystemExit("HTTP 503: eval-case lookup unavailable")
                return {
                    "status": "completed",
                    "eval_cases": list(reversed(lookup_failed_eval_cases)),
                }
            if method == "POST" and path == "/eval-cases":
                assert isinstance(body, dict)
                stored = {
                    **body,
                    "eval_case_id": 7101 + len(lookup_failed_eval_cases),
                    "client_id": "verify-client",
                }
                lookup_failed_eval_cases.append(stored)
                return stored
            raise AssertionError(f"unexpected lookup-failed eval-case request: {method} {path}")

        try:
            matrix_module.harness_api.request = fake_lookup_failed_then_record_request
            eval_lookup_failed_payload = matrix_module.maybe_record_failure_eval_case(
                argparse.Namespace(
                    base_url="http://verify",
                    client_id="verify-client",
                    api_key=None,
                    dry_run=False,
                    record_eval_case_on_failure=True,
                    eval_case_verifier_command="",
                ),
                broken_existing_matrix_with_scenarios,
            )
        finally:
            matrix_module.harness_api.request = original_matrix_request
        assert eval_lookup_failed_payload["eval_case_recording"]["status"] == "recorded"
        assert eval_lookup_failed_payload["eval_case_recording"]["eval_case_id"] == 7101
        assert eval_lookup_failed_payload["eval_case_recording"]["duplicate_prevented"] is False
        assert eval_lookup_failed_payload["eval_case_recording"]["post_attempted"] is True
        assert eval_lookup_failed_payload["eval_case_recording"]["post_required_reason"] == (
            "dedupe_lookup_failed"
        )
        assert eval_lookup_failed_payload["eval_case_recording"]["dedupe_lookup_status"] == "lookup_failed"
        assert eval_lookup_failed_payload["eval_case_recording"]["dedupe_lookup_error_type"] == "SystemExit"
        assert "eval-case lookup unavailable" in eval_lookup_failed_payload["eval_case_recording"][
            "dedupe_lookup_error"
        ]
        assert eval_lookup_failed_payload["eval_case_recording"]["readback_status"] == "matched"
        assert eval_lookup_failed_payload["eval_case_recording"]["readback_valid"] is True
        assert lookup_failed_paths == ["GET /eval-cases", "POST /eval-cases", "GET /eval-cases"]
        matrix_readback_paths.clear()
        try:
            matrix_module.harness_api.request = fake_matrix_request
            matrix_module.trace_ingest.harness_api.request = fake_matrix_request
            published_payload_validation = matrix_module.validate_published_matrix_payload_readback(
                argparse.Namespace(base_url="http://verify", client_id="verify-client", api_key=None, dry_run=False),
                "task_matrix_aggregate_verify",
                published_matrix_payload,
            )
        finally:
            matrix_module.harness_api.request = original_matrix_request
            matrix_module.trace_ingest.harness_api.request = original_trace_ingest_request
        assert published_payload_validation["status"] == "completed"
        assert published_payload_validation["validation"]["valid"] is True
        assert published_payload_validation["validation"]["task_binding"]["matrix_payload_task_id"] == (
            "task_matrix_aggregate_verify"
        )
        assert published_payload_validation["validation"]["checks"]["matrix_readback_payload_task_id_matches"] is True
        assert published_payload_validation["provider_diagnostic_readback"]["valid"] is True
        assert published_payload_validation["provider_diagnostic_readback"]["matched_diagnostic_id"] == 99
        assert published_payload_validation["provider_diagnostic_readback"]["checks"][
            "provider_diagnostic_readback_model_matches"
        ] is True
        assert "GET /tasks/task_matrix_aggregate_verify/evidence" in matrix_readback_paths
        assert "GET /llm/diagnostics?limit=200" in matrix_readback_paths
        matrix_readback_paths.clear()

        def fake_missing_payload_provider_diagnostic_request(
            _client_args: argparse.Namespace, method: str, path: str, body: Any = None
        ) -> dict:
            if method == "GET" and path == "/llm/diagnostics?limit=200":
                matrix_readback_paths.append(f"{method} {path}")
                return {"status": "completed", "diagnostics": []}
            return fake_matrix_request(_client_args, method, path, body)

        try:
            matrix_module.harness_api.request = fake_missing_payload_provider_diagnostic_request
            matrix_module.trace_ingest.harness_api.request = fake_missing_payload_provider_diagnostic_request
            missing_payload_provider_validation = matrix_module.validate_published_matrix_payload_readback(
                argparse.Namespace(base_url="http://verify", client_id="verify-client", api_key=None, dry_run=False),
                "task_matrix_aggregate_verify",
                published_matrix_payload,
            )
        finally:
            matrix_module.harness_api.request = original_matrix_request
            matrix_module.trace_ingest.harness_api.request = original_trace_ingest_request
        assert missing_payload_provider_validation["status"] == "failed"
        assert missing_payload_provider_validation["validation"]["valid"] is True
        assert missing_payload_provider_validation["provider_diagnostic_readback"]["valid"] is False
        provider_gate_summary: dict[str, Any] = {"aggregate_failed_reasons": []}
        matrix_module.attach_matrix_gate_readback_summary(
            provider_gate_summary,
            missing_payload_provider_validation,
            prefix="matrix_payload_readback",
            failure_reason="matrix_payload_readback_failed",
        )
        assert provider_gate_summary["matrix_payload_readback_valid"] is False
        assert (
            "provider_diagnostic:provider_diagnostic_readback_matched_expected_id"
            in provider_gate_summary["matrix_payload_readback_failed_checks"]
        )
        assert provider_gate_summary["aggregate_failed_reasons"] == ["matrix_payload_readback_failed"]
        missing_payload_task_validation = matrix_module.validate_published_matrix_payload_readback(
            argparse.Namespace(base_url="http://verify", client_id="verify-client", api_key=None, dry_run=False),
            "",
            published_matrix_payload,
        )
        assert missing_payload_task_validation["status"] == "failed"
        assert missing_payload_task_validation["error"] == "matrix readback task_id is missing"
        gate_summary: dict[str, Any] = {"aggregate_failed_reasons": []}
        matrix_module.attach_matrix_gate_readback_summary(
            gate_summary,
            published_payload_validation,
            prefix="matrix_payload_readback",
            failure_reason="matrix_payload_readback_failed",
        )
        assert gate_summary["matrix_payload_readback_status"] == "completed"
        assert gate_summary["matrix_payload_readback_valid"] is True
        assert gate_summary["matrix_payload_readback_failed_checks"] == []
        assert gate_summary["matrix_payload_readback_task_binding"]["matrix_payload_task_id"] == (
            "task_matrix_aggregate_verify"
        )
        assert gate_summary["aggregate_failed_reasons"] == []
        matrix_module.attach_matrix_gate_readback_summary(
            gate_summary,
            missing_payload_task_validation,
            prefix="matrix_payload_readback",
            failure_reason="matrix_payload_readback_failed",
        )
        assert gate_summary["matrix_payload_readback_status"] == "failed"
        assert gate_summary["matrix_payload_readback_valid"] is False
        assert gate_summary["matrix_payload_readback_error"] == "matrix readback task_id is missing"
        assert gate_summary["aggregate_failed_reasons"] == ["matrix_payload_readback_failed"]
        matrix_dry_run_dir = Path(temp) / "matrix-dry-run"
        matrix_dry_run = subprocess.run(
            [
                sys.executable,
                str(agents_sdk_smoke_matrix),
                "--dry-run",
                "--scenario",
                "approval",
                "--scenario",
                "stream_handoff",
                "--run-id",
                "verify-matrix",
                "--model",
                "gpt-5.4-mini",
                "--output-dir",
                str(matrix_dry_run_dir),
                "--output-json",
                str(matrix_dry_run_dir / "matrix.json"),
                "--publish-matrix-readback",
                "--client-id",
                "verify-client",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        matrix_dry_run_payload = json.loads(matrix_dry_run.stdout)
        assert matrix_dry_run_payload["status"] == "dry_run"
        assert matrix_dry_run_payload["scenario_count"] == 2
        assert [item["scenario"] for item in matrix_dry_run_payload["plan"]["commands"]] == [
            "approval",
            "stream_handoff",
        ]
        assert matrix_dry_run_payload["plan"]["publish_matrix_readback"] is True
        assert matrix_dry_run_payload["plan"]["matrix_evidence_json"].endswith(".evidence-source.json")
        assert (matrix_dry_run_dir / "matrix.json").exists()
        compact_dry_run = subprocess.run(
            [
                sys.executable,
                str(agents_sdk_smoke_matrix),
                "--dry-run",
                "--scenario",
                "approval",
                "--run-id",
                "verify-compact-matrix",
                "--model",
                "gpt-5.4-mini",
                "--output-dir",
                str(matrix_dry_run_dir),
                "--output-json",
                str(matrix_dry_run_dir / "compact-matrix.json"),
                "--publish-readback",
                "--compact-output",
                "--client-id",
                "verify-client",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        compact_stdout = json.loads(compact_dry_run.stdout)
        compact_file_payload = json.loads((matrix_dry_run_dir / "compact-matrix.json").read_text(encoding="utf-8"))
        assert compact_stdout["source"] == "agents_sdk_smoke_matrix_compact_summary"
        assert compact_stdout["status"] == "dry_run"
        assert compact_stdout["requested_scenarios"] == ["approval"]
        assert "plan" not in compact_stdout
        assert compact_stdout == compact_file_payload["matrix_run_summary"]
        assert compact_stdout["client_id"] == "verify-client"
        assert compact_stdout["base_url"]
        assert compact_stdout["matrix_readback_task_id"] is None
        assert compact_stdout["matrix_readback_check_count"] == 0
        assert compact_stdout["matrix_readback_event_replay_event_count"] == 0
        assert compact_stdout["matrix_readback_event_replay_count_map_totals"] == {}
        assert compact_stdout["matrix_readback_event_replay_count_total_checks"] == {}
        assert compact_stdout["matrix_readback_event_replay_expected_event_counts"] == {}
        assert compact_stdout["matrix_readback_event_replay_observed_event_counts"] == {}
        assert compact_stdout["matrix_readback_artifact_checks"] == {}
        assert compact_stdout["matrix_readback_artifact"]["path"] == ""
        assert compact_stdout["matrix_payload_readback_event_replay_event_count"] == 0
        assert compact_stdout["matrix_payload_readback_event_replay_count_map_totals"] == {}
        assert compact_stdout["matrix_payload_readback_event_replay_count_total_checks"] == {}
        assert compact_stdout["matrix_payload_readback_event_replay_expected_event_counts"] == {}
        assert compact_stdout["matrix_payload_readback_event_replay_observed_event_counts"] == {}
        assert compact_stdout["matrix_payload_readback_artifact_checks"] == {}
        assert compact_stdout["matrix_payload_readback_artifact"]["path"] == ""
        assert compact_stdout["revalidation_inputs"]["validate_matrix_readback_task_id"] is None
        assert compact_stdout["revalidation_inputs"]["validate_matrix_json"] == str(
            matrix_dry_run_dir / "compact-matrix.json"
        )
        assert compact_stdout["revalidation_inputs"]["client_id"] == "verify-client"
        assert compact_stdout["revalidation_ready"] is False
        assert compact_stdout["revalidation_missing_fields"] == ["validate_matrix_readback_task_id"]
        assert compact_stdout["revalidation_required_fields"] == [
            "validate_matrix_readback_task_id",
            "validate_matrix_json",
            "client_id",
            "base_url",
        ]
        assert compact_stdout["provider_probe_failed_checks"] == []
        assert compact_stdout["provider_probe_check_count"] == 0
        assert compact_stdout["provider_probe_checks"] == {}
        assert compact_stdout["provider_probe_status"] is None
        assert compact_stdout["provider_probe_http_status"] is None
        assert compact_stdout["provider_probe_endpoint"] == ""
        assert compact_stdout["provider_probe_models_preview_count"] == 0
        assert compact_stdout["provider_probe_profile"] == {}
        assert compact_stdout["provider_probe_agent_sdk_fit"] == {}
        assert compact_stdout["external_api_called"] is False
        assert compact_stdout["scenario_external_api_called"] == {}
        assert compact_stdout["scenario_readback_external_api_called"] == {}
        assert compact_stdout["scenario_sdk_events_projection_counts"] == {}
        assert compact_stdout["scenario_client_transcript_projection_counts"] == {}
        assert compact_stdout["sdk_events_projection_totals"] == {}
        assert compact_stdout["client_transcript_projection_totals"] == {}
        assert compact_stdout["projection_total_deltas"] == {}
        assert compact_stdout["projection_total_delta_assessment"] == {}
        assert compact_stdout["scenario_event_replay_expected_event_counts"] == {}
        assert compact_stdout["scenario_event_replay_observed_event_counts"] == {}
        assert compact_stdout["scenario_event_replay_sse_event_name_counts"] == {}
        assert compact_stdout["scenario_event_replay_event_type_counts"] == {}
        assert compact_stdout["scenario_event_replay_event_status_counts"] == {}
        assert compact_stdout["scenario_event_replay_event_actor_counts"] == {}
        assert compact_stdout["scenario_event_replay_sse_event_name_totals"] == {}
        assert compact_stdout["scenario_event_replay_event_type_totals"] == {}
        assert compact_stdout["scenario_event_replay_event_status_totals"] == {}
        assert compact_stdout["scenario_event_replay_event_actor_totals"] == {}
        assert compact_stdout["scenario_event_replay_expected_event_totals"] == {}
        assert compact_stdout["scenario_event_replay_observed_event_totals"] == {}
        assert compact_stdout["tool_call_status_update_expected_counts"] == {}
        assert compact_stdout["tool_call_status_updated_event_counts"] == {}
        assert compact_stdout["tool_call_status_update_expected_total"] == 0
        assert compact_stdout["tool_call_status_updated_event_total"] == 0
        assert "--validate-matrix-readback-task-id" not in compact_stdout["revalidation_cli_args"]
        assert compact_stdout["revalidation_cli_args"] == [
            "--validate-matrix-json",
            str(matrix_dry_run_dir / "compact-matrix.json"),
            "--compact-output",
            "--client-id",
            "verify-client",
            "--base-url",
            compact_stdout["base_url"],
        ]
        assert compact_stdout["summary_revalidation_inputs"] == {
            "revalidate_from_summary_json": str(matrix_dry_run_dir / "compact-matrix.json"),
            "compact_output": True,
        }
        assert compact_stdout["summary_revalidation_ready"] is False
        assert compact_stdout["summary_revalidation_missing_fields"] == ["validate_matrix_readback_task_id"]
        assert compact_stdout["summary_revalidation_cli_args"] == [
            "--revalidate-from-summary-json",
            str(matrix_dry_run_dir / "compact-matrix.json"),
            "--compact-output",
        ]
        assert compact_stdout["scenario_publish_readback_count"] == 0
        assert compact_stdout["scenario_publish_readback_valid"] is None
        assert compact_stdout["scenario_publish_readback_valid_by_scenario"] == {}
        assert compact_stdout["scenario_publish_readback_failed_scenarios"] == []
        assert compact_stdout["scenario_generic_readback_check_counts"] == {}
        assert compact_stdout["scenario_projection_check_counts"] == {}
        assert compact_stdout["scenario_event_replay_check_counts"] == {}
        assert compact_stdout["scenario_total_readback_check_counts"] == {}
        assert compact_stdout["scenario_readback_failed_checks"] == {}
        assert compact_stdout["scenario_event_replay_failed_checks"] == {}
        assert compact_stdout["scenario_artifact_summaries"] == {}
        assert "plan" in compact_file_payload
        compact_artifact = {
            "path": "matrix.evidence-source.json",
            "expected_path": "matrix.evidence-source.json",
            "digest": "sha256:compact-artifact",
            "digest_from_recorded_path": "sha256:compact-artifact",
            "digest_from_expected_path": "sha256:compact-artifact",
            "recorded_path_available": True,
            "expected_path_available": True,
        }
        compact_artifact_checks = {
            "matrix_artifact_recorded": True,
            "matrix_artifact_path_recorded": True,
            "matrix_artifact_digest_recorded": True,
            "matrix_artifact_path_matches_payload": True,
            "matrix_artifact_digest_matches_recorded_path": True,
            "matrix_artifact_digest_matches_expected_path": True,
        }
        aggregate_count_map_totals = {
            "event_actor_counts": 6,
            "event_status_counts": 6,
            "event_type_counts": 6,
            "sse_event_name_counts": 6,
        }
        aggregate_event_replay_summary = {
            "event_count": 6,
            "sse_event_name_counts": {"harness_event": 6},
            "event_type_counts": {
                "artifact.recorded": 1,
                "observation.recorded": 3,
                "task.paused": 1,
                "task.queued": 1,
            },
            "event_status_counts": {
                "completed": 4,
                "paused": 1,
                "queued": 1,
            },
            "event_actor_counts": {
                "api": 2,
                "artifact": 1,
                "tool": 3,
            },
        }
        live_compact_summary = matrix_module.matrix_run_compact_summary(
            {
                "status": "completed",
                "source": "agents_sdk_smoke_matrix",
                "run_id": "verify-live-compact",
                "model": "gpt-5.4-mini",
                "provider": "openai",
                "api_style": "responses",
                "base_url": "http://verify/api/harness/v1",
                "client_id": "verify-client",
                "scenario_count": 1,
                "requested_scenarios": ["approval"],
                "provider_probe_validation": provider_probe_validation,
                "summary": {
                    "status": "completed",
                    "passed_count": 1,
                    "failed_count": 0,
                    "external_api_called": True,
                    "scenario_external_api_called": {"approval": True},
                    "published_task_ids": {"approval": "task_matrix_verify"},
                    "matrix_readback_status": "completed",
                    "matrix_readback_valid": True,
                    "matrix_readback_failed_checks": [],
                    "matrix_readback_task_binding": {
                        "expected_task_id": "task_matrix_aggregate_verify"
                    },
                    "matrix_payload_readback_status": "completed",
                    "matrix_payload_readback_valid": True,
                    "matrix_payload_readback_failed_checks": [],
                    "matrix_payload_readback_task_binding": {
                        "matrix_payload_task_id": "task_matrix_aggregate_verify"
                    },
                    "scenario_sdk_events_projection_counts": {
                        "approval": {
                            "agent_model_response_count": 2,
                            "agent_stream_event_count": 5,
                        }
                    },
                    "scenario_client_transcript_projection_counts": {
                        "approval": {
                            "agent_model_response_count": 2,
                            "agent_stream_event_count": 5,
                        }
                    },
                    "sdk_events_projection_totals": {
                        "agent_model_response_count": 2,
                        "agent_stream_event_count": 5,
                    },
                    "client_transcript_projection_totals": {
                        "agent_model_response_count": 2,
                        "agent_stream_event_count": 5,
                    },
                    "projection_total_deltas": {},
                    "projection_total_delta_assessment": {"status": "no_deltas", "items": {}},
                    "projection_total_delta_allowed_keys": [],
                    "scenario_event_replay_expected_event_counts": {
                        "approval": {
                            "artifact.recorded": 1,
                            "observation.recorded": 2,
                            "tool_call.recorded": 1,
                            "tool_call.status_updated": 1,
                        }
                    },
                    "scenario_event_replay_observed_event_counts": {
                        "approval": {
                            "artifact.recorded": 1,
                            "observation.recorded": 2,
                            "tool_call.recorded": 1,
                            "tool_call.status_updated": 1,
                        }
                    },
                    "scenario_event_replay_event_counts": {"approval": 10},
                    "scenario_event_replay_count_map_totals": {
                        "approval": {
                            "event_actor_counts": 10,
                            "event_status_counts": 10,
                            "event_type_counts": 10,
                            "sse_event_name_counts": 10,
                        }
                    },
                    "scenario_event_replay_count_map_total_totals": {
                        "event_actor_counts": 10,
                        "event_status_counts": 10,
                        "event_type_counts": 10,
                        "sse_event_name_counts": 10,
                    },
                    "scenario_event_replay_count_total_checks": {
                        "approval": expected_count_total_checks
                    },
                    "scenario_event_replay_sse_event_name_counts": {
                        "approval": {"harness_event": 10}
                    },
                    "scenario_event_replay_event_type_counts": {
                        "approval": {
                            "artifact.recorded": 1,
                            "observation.recorded": 2,
                            "task.paused": 1,
                            "task.queued": 1,
                            "task.updated": 3,
                            "tool_call.recorded": 1,
                            "tool_call.status_updated": 1,
                        }
                    },
                    "scenario_event_replay_event_status_counts": {
                        "approval": {
                            "completed": 8,
                            "paused": 1,
                            "queued": 1,
                        }
                    },
                    "scenario_event_replay_event_actor_counts": {
                        "approval": {
                            "api": 2,
                            "artifact": 1,
                            "tool": 7,
                        }
                    },
                    "scenario_event_replay_sse_event_name_totals": {
                        "harness_event": 10
                    },
                    "scenario_event_replay_event_type_totals": {
                        "artifact.recorded": 1,
                        "observation.recorded": 2,
                        "task.paused": 1,
                        "task.queued": 1,
                        "task.updated": 3,
                        "tool_call.recorded": 1,
                        "tool_call.status_updated": 1,
                    },
                    "scenario_event_replay_event_status_totals": {
                        "completed": 8,
                        "paused": 1,
                        "queued": 1,
                    },
                    "scenario_event_replay_event_actor_totals": {
                        "api": 2,
                        "artifact": 1,
                        "tool": 7,
                    },
                    "scenario_event_replay_expected_event_totals": {
                        "artifact.recorded": 1,
                        "observation.recorded": 2,
                        "tool_call.recorded": 1,
                        "tool_call.status_updated": 1,
                    },
                    "scenario_event_replay_observed_event_totals": {
                        "artifact.recorded": 1,
                        "observation.recorded": 2,
                        "tool_call.recorded": 1,
                        "tool_call.status_updated": 1,
                    },
                    "tool_call_status_update_expected_counts": {"approval": 1},
                    "tool_call_status_updated_event_counts": {"approval": 1},
                    "tool_call_status_update_expected_total": 1,
                    "tool_call_status_updated_event_total": 1,
                },
                "results": [
                    {
                        "scenario": "approval",
                        "approval_state_summary": {
                            "status": "completed",
                            "approval_request_count": 1,
                            "approval_request_type_counts": {"tool_approval_item": 1},
                            "approval_decision_count": 1,
                            "approval_decision_counts": {"approved": 1},
                            "approved_count": 1,
                            "phase_count": 2,
                            "phase_names": ["initial", "after_approval"],
                            "phase_interruption_counts": {
                                "initial": 1,
                                "after_approval": 0,
                            },
                            "initial_phase_recorded": True,
                            "resume_phase_recorded": True,
                            "initial_interruption_count": 1,
                            "resume_interruption_count": 0,
                            "approval_interruption_observed": True,
                            "approved_resume_observed": True,
                            "tool_call_count": 1,
                            "tool_output_count": 1,
                            "final_ok": True,
                            "marker_observed": True,
                            "request_summaries": [
                                {
                                    "call_id": "call_approval",
                                    "tool_name": "approval_required_signal",
                                    "item_type": "tool_approval_item",
                                    "item_class": "ToolApprovalItem",
                                    "raw_type": "function_call",
                                    "agent_name": "Harness Approval Agent",
                                    "argument_keys": ["marker"],
                                    "marker_argument_present": True,
                                }
                            ],
                            "decision_summaries": [
                                {
                                    "call_id": "call_approval",
                                    "tool_name": "approval_required_signal",
                                    "item_type": "tool_approval_item",
                                    "raw_type": "function_call",
                                    "agent_name": "Harness Approval Agent",
                                    "argument_keys": ["marker"],
                                    "marker_argument_present": True,
                                    "decision": "approved",
                                }
                            ],
                            "phase_summaries": [
                                {
                                    "name": "initial",
                                    "new_item_count": 2,
                                    "new_item_type_counts": {
                                        "tool_call_item": 1,
                                        "tool_approval_item": 1,
                                    },
                                    "tool_call_count": 1,
                                    "approval_request_item_count": 1,
                                    "tool_output_count": 0,
                                    "raw_response_count": 1,
                                    "interruption_count": 1,
                                    "final_output_present": False,
                                },
                                {
                                    "name": "after_approval",
                                    "new_item_count": 1,
                                    "new_item_type_counts": {"tool_call_output_item": 1},
                                    "tool_call_count": 0,
                                    "approval_request_item_count": 0,
                                    "tool_output_count": 1,
                                    "raw_response_count": 1,
                                    "interruption_count": 0,
                                    "final_output_present": True,
                                },
                            ],
                        },
                        "session_state_summary": {
                            "agent_session_id": "sdk_session_live_compact_verify",
                            "backend": "SQLiteSession",
                            "turn_count": 2,
                            "summary_turn_count": 2,
                            "turn_count_matches_summary": True,
                            "final_item_count": 4,
                            "summary_item_count": 4,
                            "final_item_count_matches_summary": True,
                            "final_item_types": ["user", "message", "user", "message"],
                            "final_item_type_counts": {"user": 2, "message": 2},
                            "summary_item_type_counts": {"user": 2, "message": 2},
                            "final_item_type_counts_match_summary": True,
                            "turn_phases": ["session_initial", "session_turn_2"],
                            "summary_phase_names": ["session_initial", "session_turn_2"],
                            "item_count_before_by_phase": {
                                "session_initial": 0,
                                "session_turn_2": 2,
                            },
                            "item_count_after_by_phase": {
                                "session_initial": 2,
                                "session_turn_2": 4,
                            },
                            "new_item_count_by_phase": {
                                "session_initial": 1,
                                "session_turn_2": 1,
                            },
                            "raw_response_count_by_phase": {
                                "session_initial": 1,
                                "session_turn_2": 1,
                            },
                            "session_growth_observed": True,
                            "stateful_second_turn_observed": True,
                            "db_path_recorded": True,
                            "final_items_preview_count": 4,
                        },
                        "publish_readback": {
                            "status": "completed",
                            "task_id": "task_matrix_verify",
                            "result_summary": {
                                "status": "completed",
                                "tool_call_count": 1,
                                "observation_count": 9,
                                "artifact_count": 2,
                                "tool_call_status_update_expected_count": 1,
                                "tool_call_name_counts": {
                                    "approval_required_signal": 1,
                                },
                                "observation_tool_counts": {
                                    "agent_run_summary": 1,
                                    "agent_run_phase": 2,
                                    "agent_model_response": 2,
                                    "agent_approval_request": 1,
                                    "approval_required_signal": 1,
                                    "agent_approval_decision": 1,
                                    "agent_final_output": 1,
                                },
                                "status_counts": {
                                    "completed": 9,
                                    "queued": 1,
                                },
                                "call_ids": ["call_approval"],
                                "tool_call_pks": [501],
                                "observation_ids": [
                                    601,
                                    602,
                                    603,
                                    604,
                                    605,
                                    606,
                                    607,
                                    608,
                                    609,
                                ],
                            },
                            "validation": {
                                "valid": True,
                                "check_count": 52,
                                "checks": {
                                    "agent_trace_artifact_digest_matches_expected": True,
                                    "artifact_count_covers_result_summary": True,
                                    "observation_count_covers_result_summary": True,
                                    "tool_call_count_covers_result_summary": True,
                                },
                                "evidence_tool_call_name_counts": {
                                    "approval_required_signal": 1,
                                },
                                "evidence_observation_tool_counts": {
                                    "agent_run_summary": 1,
                                    "agent_run_phase": 2,
                                    "agent_model_response": 2,
                                    "agent_approval_request": 1,
                                    "approval_required_signal": 1,
                                    "agent_approval_decision": 1,
                                    "agent_final_output": 1,
                                },
                                "task_binding": {
                                    "expected_task_id": "task_matrix_verify"
                                },
                                "trace_artifact_binding": {
                                    "matched_artifact_count": 1,
                                    "matched_artifact_ids": [501],
                                },
                            },
                            "scenario_projection_validation": {"check_count": 17},
                            "scenario_event_replay_validation": {
                                "check_count": 15,
                                "event_replay_fetch_plan": {
                                    "source": "agents_sdk_smoke_matrix_event_replay_fetch_plan",
                                    "basis": "sdk_events_raw_event_count",
                                    "max_events": 500,
                                    "default_max_events": 500,
                                    "buffer": 25,
                                    "expected_raw_event_count": 10,
                                    "evidence_event_floor": 8,
                                    "expected_event_floor": 10,
                                },
                                "checks": {
                                    "event_replay_fetch_plan_covers_expected_raw_event_count": True,
                                    "event_replay_not_truncated_by_fetch_limit": True,
                                    "event_replay_sse_event_name_count_total_matches_event_count": True,
                                    "event_replay_event_type_count_total_matches_event_count": True,
                                    "event_replay_event_status_count_total_matches_event_count": True,
                                    "event_replay_event_actor_count_total_matches_event_count": True,
                                },
                                "event_replay_count_map_totals": {
                                    "event_actor_counts": 10,
                                    "event_status_counts": 10,
                                    "event_type_counts": 10,
                                    "sse_event_name_counts": 10,
                                },
                                "expected_event_counts": {
                                    "artifact.recorded": 1,
                                    "observation.recorded": 2,
                                    "tool_call.recorded": 1,
                                    "tool_call.status_updated": 1,
                                },
                                "observed_event_counts": {
                                    "artifact.recorded": 1,
                                    "observation.recorded": 2,
                                    "tool_call.recorded": 1,
                                    "tool_call.status_updated": 1,
                                },
                                "event_replay_summary": {
                                    "event_count": 10,
                                    "sse_event_name_counts": {
                                        "harness_event": 10,
                                    },
                                    "event_type_counts": {
                                        "artifact.recorded": 1,
                                        "observation.recorded": 2,
                                        "task.paused": 1,
                                        "task.queued": 1,
                                        "task.updated": 3,
                                        "tool_call.recorded": 1,
                                        "tool_call.status_updated": 1,
                                    },
                                    "event_status_counts": {
                                        "completed": 8,
                                        "paused": 1,
                                        "queued": 1,
                                    },
                                    "event_actor_counts": {
                                        "api": 2,
                                        "artifact": 1,
                                        "tool": 7,
                                    },
                                },
                            },
                            "sdk_events_summary": {
                                "summary_requested": True,
                                "task_id": "task_matrix_verify",
                                "agent_approval_count": 2,
                                "agent_phase_count": 2,
                                "tool_progress_count": 2,
                                "final_output_count": 1,
                                "agent_stream_event_count": 5,
                                "agent_handoff_count": 2,
                                "agent_stream_event_name_counts": {
                                    "handoff_requested": 1,
                                    "handoff_occured": 1,
                                },
                                "agent_stream_tool_name_counts": {
                                    "transfer_to_harness_specialist": 2,
                                },
                            },
                            "client_transcript_summary": {
                                "summary_requested": True,
                                "task_id": "task_matrix_verify",
                                "agent_approval_count": 2,
                                "agent_phase_count": 2,
                                "tool_progress_count": 1,
                                "final_output_count": 1,
                                "item_type_counts": {
                                    "agent_approval": 2,
                                    "agent_phase": 2,
                                    "tool_progress": 1,
                                    "final_output": 1,
                                },
                                "agent_stream_event_count": 5,
                                "agent_handoff_count": 2,
                                "agent_stream_event_name_counts": {
                                    "handoff_requested": 1,
                                    "handoff_occured": 1,
                                },
                                "agent_stream_tool_name_counts": {
                                    "transfer_to_harness_specialist": 2,
                                },
                            },
                            "artifact_summaries": [
                                {
                                    "artifact_id": 501,
                                    "kind": "agent_trace",
                                    "path": "agents-sdk-approval-verify.json",
                                    "digest": "sha256:scenario-trace",
                                    "status": "completed",
                                    "metadata": {
                                        "trace_id": "trace_verify_approval",
                                        "external_api_called": True,
                                    },
                                }
                            ],
                            "agent_session_summaries": [
                                {
                                    "agent_session_id": "sdk_session_live_compact_verify",
                                    "backend": "SQLiteSession",
                                    "turn_count": 2,
                                    "turn_count_from_turns": 2,
                                    "final_item_count": 4,
                                    "final_item_types": ["user", "message", "user", "message"],
                                    "final_item_type_counts": {"user": 2, "message": 2},
                                    "turn_phases": ["session_initial", "session_turn_2"],
                                    "status": "completed",
                                    "evidence_refs": ["artifact:agent_trace:verify_trace.json"],
                                }
                            ],
                        },
                    }
                ],
                "matrix_output_json": "matrix.json",
                "matrix_evidence_json": "matrix.evidence-source.json",
                "matrix_readback": {
                    "task_id": "task_matrix_aggregate_verify",
                    "validation": {
                        "check_count": 79,
                        "checks": {
                            **compact_artifact_checks,
                            **expected_count_total_checks,
                            "event_replay_covers_tool_call_recorded": True,
                            "event_replay_covers_tool_call_status_updated": True,
                            "event_replay_fetch_plan_covers_expected_raw_event_count": True,
                            "event_replay_not_truncated_by_fetch_limit": True,
                        },
                        "event_replay_count_map_totals": aggregate_count_map_totals,
                        "expected_event_counts": expected_aggregate_event_counts,
                        "observed_event_counts": expected_aggregate_event_counts,
                        "event_replay_summary": aggregate_event_replay_summary,
                        "event_replay_fetch_plan": {
                            "source": "agents_sdk_smoke_matrix_event_replay_fetch_plan",
                            "basis": "sdk_events_raw_event_count",
                            "max_events": 500,
                            "default_max_events": 500,
                            "buffer": 25,
                            "expected_raw_event_count": 6,
                            "evidence_event_floor": 4,
                            "expected_event_floor": 6,
                        },
                        "matrix_artifact": compact_artifact,
                    },
                },
                "matrix_payload_readback_validation": {
                    "provider_diagnostic_readback": {
                        "status": "completed",
                        "valid": True,
                        "expected_diagnostic_id": 99,
                        "matched": True,
                        "matched_diagnostic_id": 99,
                        "matched_status": "completed",
                        "matched_provider": {
                            "provider": "openai",
                            "api_style": "responses",
                            "base_url": "https://api.openai.com/v1",
                            "model": "gpt-4.1-mini",
                            "api_key_env": "OPENAI_API_KEY",
                        },
                        "matched_provider_request_attempted": True,
                        "matched_provider_response_received": True,
                        "matched_external_api_called": True,
                        "diagnostic_count": 1,
                        "check_count": 9,
                        "failed_checks": [],
                        "checks": {
                            "provider_diagnostics_readback_completed": True,
                            "provider_diagnostics_readback_has_diagnostics_list": True,
                            "provider_diagnostic_readback_matched_expected_id": True,
                            "provider_diagnostic_readback_status_matches": True,
                            "provider_diagnostic_readback_model_matches": True,
                            "provider_diagnostic_readback_api_style_matches": True,
                            "provider_diagnostic_readback_request_attempted_matches": True,
                            "provider_diagnostic_readback_response_received_matches": True,
                            "provider_diagnostic_readback_external_api_called_matches": True,
                        },
                    },
                    "validation": {
                        "check_count": 79,
                        "checks": {
                            **compact_artifact_checks,
                            **expected_count_total_checks,
                            "event_replay_covers_tool_call_recorded": True,
                            "event_replay_covers_tool_call_status_updated": True,
                            "event_replay_fetch_plan_covers_expected_raw_event_count": True,
                            "event_replay_not_truncated_by_fetch_limit": True,
                        },
                        "event_replay_count_map_totals": aggregate_count_map_totals,
                        "expected_event_counts": expected_aggregate_event_counts,
                        "observed_event_counts": expected_aggregate_event_counts,
                        "event_replay_summary": aggregate_event_replay_summary,
                        "event_replay_fetch_plan": {
                            "source": "agents_sdk_smoke_matrix_event_replay_fetch_plan",
                            "basis": "sdk_events_raw_event_count",
                            "max_events": 500,
                            "default_max_events": 500,
                            "buffer": 25,
                            "expected_raw_event_count": 6,
                            "evidence_event_floor": 4,
                            "expected_event_floor": 6,
                        },
                        "matrix_artifact": compact_artifact,
                    },
                },
            }
        )
        assert live_compact_summary["matrix_readback_task_id"] == "task_matrix_aggregate_verify"
        assert live_compact_summary["matrix_readback_check_count"] == 79
        assert live_compact_summary["matrix_payload_readback_check_count"] == 79
        assert live_compact_summary["matrix_readback_event_replay_fetch_plan"]["basis"] == (
            "sdk_events_raw_event_count"
        )
        assert live_compact_summary["matrix_readback_event_replay_fetch_plan"][
            "expected_raw_event_count"
        ] == 6
        assert live_compact_summary["matrix_readback_event_replay_fetch_plan_checks"] == {
            "event_replay_fetch_plan_covers_expected_raw_event_count": True,
            "event_replay_not_truncated_by_fetch_limit": True,
        }
        assert live_compact_summary["matrix_readback_event_replay_event_count"] == 6
        assert live_compact_summary["matrix_readback_event_replay_count_map_totals"] == (
            aggregate_count_map_totals
        )
        assert live_compact_summary["matrix_readback_event_replay_count_total_checks"] == (
            expected_count_total_checks
        )
        assert live_compact_summary["matrix_readback_event_replay_expected_event_counts"] == (
            expected_aggregate_event_counts
        )
        assert live_compact_summary["matrix_readback_event_replay_observed_event_counts"] == (
            expected_aggregate_event_counts
        )
        assert live_compact_summary["matrix_readback_event_replay_event_type_counts"] == {
            "artifact.recorded": 1,
            "observation.recorded": 3,
            "task.paused": 1,
            "task.queued": 1,
        }
        assert live_compact_summary["matrix_readback_event_replay_event_actor_counts"] == {
            "api": 2,
            "artifact": 1,
            "tool": 3,
        }
        assert live_compact_summary["matrix_payload_readback_event_replay_fetch_plan"][
            "expected_raw_event_count"
        ] == 6
        assert live_compact_summary["matrix_payload_readback_event_replay_fetch_plan_checks"][
            "event_replay_not_truncated_by_fetch_limit"
        ] is True
        assert live_compact_summary["matrix_payload_readback_event_replay_event_count"] == 6
        assert live_compact_summary["matrix_payload_readback_event_replay_count_map_totals"] == (
            aggregate_count_map_totals
        )
        assert live_compact_summary["matrix_payload_readback_event_replay_count_total_checks"] == (
            expected_count_total_checks
        )
        assert live_compact_summary[
            "matrix_payload_readback_event_replay_expected_event_counts"
        ] == expected_aggregate_event_counts
        assert live_compact_summary[
            "matrix_payload_readback_event_replay_observed_event_counts"
        ] == expected_aggregate_event_counts
        assert live_compact_summary["revalidation_inputs"] == {
            "validate_matrix_readback_task_id": "task_matrix_aggregate_verify",
            "validate_matrix_json": "matrix.json",
            "validate_published_scenarios": True,
            "client_id": "verify-client",
            "base_url": "http://verify/api/harness/v1",
            "compact_output": True,
        }
        assert live_compact_summary["revalidation_ready"] is True
        assert live_compact_summary["revalidation_missing_fields"] == []
        assert live_compact_summary["revalidation_cli_args"] == [
            "--validate-matrix-readback-task-id",
            "task_matrix_aggregate_verify",
            "--validate-matrix-json",
            "matrix.json",
            "--validate-published-scenarios",
            "--compact-output",
            "--client-id",
            "verify-client",
            "--base-url",
            "http://verify/api/harness/v1",
        ]
        assert live_compact_summary["summary_revalidation_inputs"] == {
            "revalidate_from_summary_json": "matrix.json",
            "compact_output": True,
        }
        assert live_compact_summary["summary_revalidation_ready"] is True
        assert live_compact_summary["summary_revalidation_missing_fields"] == []
        assert live_compact_summary["summary_revalidation_cli_args"] == [
            "--revalidate-from-summary-json",
            "matrix.json",
            "--compact-output",
        ]
        assert live_compact_summary["valid"] is True
        assert live_compact_summary["provider_probe_failed_checks"] == []
        assert live_compact_summary["provider_probe_check_count"] == 8
        assert live_compact_summary["provider_probe_checks"]["probe_api_style_responses"] is True
        assert live_compact_summary["provider_probe_checks"]["probe_external_api_called"] is True
        assert live_compact_summary["provider_probe_status"] == "completed"
        assert live_compact_summary["provider_probe_http_status"] == 200
        assert live_compact_summary["provider_probe_endpoint"] == "https://api.openai.com/v1/models"
        assert live_compact_summary["provider_probe_models_preview_count"] == 1
        assert live_compact_summary["provider_probe_profile"]["model"] == "gpt-4.1-mini"
        assert live_compact_summary["provider_probe_profile"]["api_key_env"] == "OPENAI_API_KEY"
        assert live_compact_summary["provider_probe_agent_sdk_fit"][
            "can_use_openai_agents_sdk_model_layer"
        ] is True
        assert live_compact_summary["external_api_called"] is True
        assert live_compact_summary["scenario_external_api_called"] == {"approval": True}
        assert live_compact_summary["scenario_readback_external_api_called"] == {"approval": True}
        assert live_compact_summary["scenario_sdk_events_projection_counts"] == {
            "approval": {"agent_model_response_count": 2, "agent_stream_event_count": 5}
        }
        assert live_compact_summary["scenario_client_transcript_projection_counts"] == {
            "approval": {"agent_model_response_count": 2, "agent_stream_event_count": 5}
        }
        assert live_compact_summary["sdk_events_projection_totals"] == {
            "agent_model_response_count": 2,
            "agent_stream_event_count": 5,
        }
        assert live_compact_summary["client_transcript_projection_totals"] == {
            "agent_model_response_count": 2,
            "agent_stream_event_count": 5,
        }
        assert live_compact_summary["projection_total_deltas"] == {}
        assert live_compact_summary["projection_total_delta_assessment"] == {
            "status": "no_deltas",
            "items": {},
        }
        assert live_compact_summary["projection_total_delta_allowed_keys"] == []
        assert live_compact_summary["scenario_event_replay_expected_event_counts"] == {
            "approval": {
                "artifact.recorded": 1,
                "observation.recorded": 2,
                "tool_call.recorded": 1,
                "tool_call.status_updated": 1,
            }
        }
        assert live_compact_summary["scenario_event_replay_observed_event_counts"] == {
            "approval": {
                "artifact.recorded": 1,
                "observation.recorded": 2,
                "tool_call.recorded": 1,
                "tool_call.status_updated": 1,
            }
        }
        assert live_compact_summary["scenario_readback_event_replay_expected_event_counts"] == {
            "approval": {
                "artifact.recorded": 1,
                "observation.recorded": 2,
                "tool_call.recorded": 1,
                "tool_call.status_updated": 1,
            }
        }
        assert live_compact_summary["scenario_readback_event_replay_observed_event_counts"] == {
            "approval": {
                "artifact.recorded": 1,
                "observation.recorded": 2,
                "tool_call.recorded": 1,
                "tool_call.status_updated": 1,
            }
        }
        assert live_compact_summary["scenario_readback_event_replay_expected_event_totals"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 1,
        }
        assert live_compact_summary["scenario_readback_event_replay_observed_event_totals"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 1,
        }
        assert live_compact_summary["scenario_readback_event_replay_sse_event_name_counts"] == {
            "approval": {"harness_event": 10}
        }
        assert live_compact_summary["scenario_readback_event_replay_event_type_counts"] == {
            "approval": {
                "artifact.recorded": 1,
                "observation.recorded": 2,
                "task.paused": 1,
                "task.queued": 1,
                "task.updated": 3,
                "tool_call.recorded": 1,
                "tool_call.status_updated": 1,
            }
        }
        assert live_compact_summary["scenario_readback_event_replay_event_status_counts"] == {
            "approval": {
                "completed": 8,
                "paused": 1,
                "queued": 1,
            }
        }
        assert live_compact_summary["scenario_readback_event_replay_event_actor_counts"] == {
            "approval": {
                "api": 2,
                "artifact": 1,
                "tool": 7,
            }
        }
        assert live_compact_summary["scenario_readback_event_replay_sse_event_name_totals"] == {
            "harness_event": 10
        }
        assert live_compact_summary["scenario_readback_event_replay_event_type_totals"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "task.paused": 1,
            "task.queued": 1,
            "task.updated": 3,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 1,
        }
        assert live_compact_summary["scenario_readback_event_replay_event_status_totals"] == {
            "completed": 8,
            "paused": 1,
            "queued": 1,
        }
        assert live_compact_summary["scenario_readback_event_replay_event_actor_totals"] == {
            "api": 2,
            "artifact": 1,
            "tool": 7,
        }
        assert live_compact_summary["scenario_event_replay_sse_event_name_counts"] == {
            "approval": {"harness_event": 10}
        }
        assert live_compact_summary["scenario_event_replay_event_type_counts"] == {
            "approval": {
                "artifact.recorded": 1,
                "observation.recorded": 2,
                "task.paused": 1,
                "task.queued": 1,
                "task.updated": 3,
                "tool_call.recorded": 1,
                "tool_call.status_updated": 1,
            }
        }
        assert live_compact_summary["scenario_event_replay_event_status_counts"] == {
            "approval": {
                "completed": 8,
                "paused": 1,
                "queued": 1,
            }
        }
        assert live_compact_summary["scenario_event_replay_event_actor_counts"] == {
            "approval": {
                "api": 2,
                "artifact": 1,
                "tool": 7,
            }
        }
        assert live_compact_summary["scenario_event_replay_sse_event_name_totals"] == {
            "harness_event": 10
        }
        assert live_compact_summary["scenario_event_replay_event_type_totals"] == {
            "artifact.recorded": 1,
            "observation.recorded": 2,
            "task.paused": 1,
            "task.queued": 1,
            "task.updated": 3,
            "tool_call.recorded": 1,
            "tool_call.status_updated": 1,
        }
        assert live_compact_summary["scenario_event_replay_event_status_totals"] == {
            "completed": 8,
            "paused": 1,
            "queued": 1,
        }
        assert live_compact_summary["scenario_event_replay_event_actor_totals"] == {
            "api": 2,
            "artifact": 1,
            "tool": 7,
        }
        assert live_compact_summary["scenario_event_replay_event_counts"] == {"approval": 10}
        assert live_compact_summary["scenario_event_replay_count_map_totals"] == {
            "approval": {
                "event_actor_counts": 10,
                "event_status_counts": 10,
                "event_type_counts": 10,
                "sse_event_name_counts": 10,
            }
        }
        assert live_compact_summary["scenario_event_replay_count_map_total_totals"] == {
            "event_actor_counts": 10,
            "event_status_counts": 10,
            "event_type_counts": 10,
            "sse_event_name_counts": 10,
        }
        assert live_compact_summary["scenario_event_replay_count_total_checks"] == {
            "approval": expected_count_total_checks
        }
        assert live_compact_summary["scenario_readback_event_replay_event_counts"] == {
            "approval": 10
        }
        assert live_compact_summary["scenario_readback_event_replay_count_map_totals"] == {
            "approval": {
                "event_actor_counts": 10,
                "event_status_counts": 10,
                "event_type_counts": 10,
                "sse_event_name_counts": 10,
            }
        }
        assert live_compact_summary["scenario_readback_event_replay_count_total_checks"] == {
            "approval": expected_count_total_checks
        }
        assert live_compact_summary["tool_call_status_update_expected_counts"] == {"approval": 1}
        assert live_compact_summary["tool_call_status_updated_event_counts"] == {"approval": 1}
        assert live_compact_summary["tool_call_status_update_expected_total"] == 1
        assert live_compact_summary["tool_call_status_updated_event_total"] == 1
        assert live_compact_summary["scenario_publish_readback_count"] == 1
        assert live_compact_summary["scenario_publish_readback_valid"] is True
        assert live_compact_summary["scenario_publish_readback_valid_by_scenario"] == {
            "approval": True
        }
        assert live_compact_summary["scenario_publish_readback_failed_scenarios"] == []
        assert live_compact_summary["scenario_publish_readback_status_by_scenario"] == {
            "approval": "completed"
        }
        assert live_compact_summary["scenario_generic_readback_check_counts"] == {"approval": 52}
        assert live_compact_summary["scenario_projection_check_counts"] == {"approval": 17}
        assert live_compact_summary["scenario_event_replay_check_counts"] == {"approval": 15}
        assert live_compact_summary["scenario_total_readback_check_counts"] == {"approval": 84}
        assert live_compact_summary["scenario_event_replay_fetch_plans"]["approval"][
            "expected_raw_event_count"
        ] == 10
        assert live_compact_summary["scenario_event_replay_fetch_plan_checks"]["approval"] == {
            "event_replay_fetch_plan_covers_expected_raw_event_count": True,
            "event_replay_not_truncated_by_fetch_limit": True,
        }
        assert live_compact_summary["scenario_readback_failed_checks"] == {}
        assert live_compact_summary["scenario_event_replay_failed_checks"] == {}
        assert live_compact_summary["scenario_readback_task_bindings"] == {
            "approval": {"expected_task_id": "task_matrix_verify"}
        }
        assert live_compact_summary["scenario_trace_artifact_digest_matches"] == {"approval": True}
        assert live_compact_summary["scenario_trace_artifact_bindings"]["approval"][
            "matched_artifact_count"
        ] == 1
        assert live_compact_summary["scenario_artifact_summaries"]["approval"][0]["kind"] == "agent_trace"
        assert live_compact_summary["scenario_artifact_summaries"]["approval"][0]["digest"] == (
            "sha256:scenario-trace"
        )
        approval_state_summary = live_compact_summary["scenario_approval_state_summaries"][
            "approval"
        ]
        assert approval_state_summary["approval_request_count"] == 1
        assert approval_state_summary["approval_decision_counts"] == {"approved": 1}
        assert approval_state_summary["phase_names"] == ["initial", "after_approval"]
        assert approval_state_summary["approval_interruption_observed"] is True
        assert approval_state_summary["approved_resume_observed"] is True
        assert approval_state_summary["request_summaries"][0]["argument_keys"] == ["marker"]
        assert approval_state_summary["decision_summaries"][0]["decision"] == "approved"
        assert "raw_item" not in approval_state_summary["request_summaries"][0]
        approval_projection_summary = live_compact_summary[
            "scenario_approval_projection_summaries"
        ]["approval"]
        assert approval_projection_summary["sdk_events"]["agent_approval_count"] == 2
        assert approval_projection_summary["client_transcript"]["agent_phase_count"] == 2
        assert approval_projection_summary["approval_counts_match"] is True
        assert approval_projection_summary["phase_counts_match"] is True
        assert approval_projection_summary["tool_progress_delta"] == 1
        assert live_compact_summary["scenario_readback_approval_projection_summaries"][
            "approval"
        ] == approval_projection_summary
        approval_evidence_summary = live_compact_summary[
            "scenario_approval_evidence_summaries"
        ]["approval"]
        assert approval_evidence_summary["result_tool_call_count"] == 1
        assert approval_evidence_summary["result_observation_count"] == 9
        assert approval_evidence_summary["result_tool_call_status_update_expected_count"] == 1
        assert approval_evidence_summary["result_tool_call_name_counts"] == {
            "approval_required_signal": 1
        }
        assert approval_evidence_summary["result_observation_tool_counts"][
            "agent_approval_request"
        ] == 1
        assert approval_evidence_summary["result_observation_tool_counts"][
            "agent_approval_decision"
        ] == 1
        assert approval_evidence_summary["expected_approval_tool_call_recorded"] is True
        assert (
            approval_evidence_summary[
                "expected_approval_request_observation_recorded"
            ]
            is True
        )
        assert (
            approval_evidence_summary[
                "expected_approval_decision_observation_recorded"
            ]
            is True
        )
        assert (
            approval_evidence_summary[
                "evidence_expected_approval_tool_output_observation_recorded"
            ]
            is True
        )
        assert approval_evidence_summary["all_expected_tool_calls_recorded"] is True
        assert approval_evidence_summary["all_expected_observations_recorded"] is True
        assert approval_evidence_summary["result_call_id_count"] == 1
        assert approval_evidence_summary["result_tool_call_pk_count"] == 1
        assert approval_evidence_summary["result_observation_id_count"] == 9
        assert "call_ids" not in approval_evidence_summary
        assert "tool_call_pks" not in approval_evidence_summary
        assert "observation_ids" not in approval_evidence_summary
        assert live_compact_summary["scenario_readback_approval_evidence_summaries"][
            "approval"
        ] == approval_evidence_summary
        tool_state_fixture = {
            "status": "completed",
            "tool_call_count": 1,
            "tool_output_count": 1,
            "tool_pair_count": 1,
            "tool_completed_observed": True,
            "all_tool_calls_paired": True,
            "tool_names": ["record_signal"],
            "expected_tool_observed": True,
            "phase_count": 1,
            "phase_names": ["initial"],
            "phase_new_item_counts": {"initial": 3},
            "phase_raw_response_counts": {"initial": 2},
            "phase_interruption_counts": {"initial": 0},
            "new_item_type_counts": {
                "tool_call_item": 1,
                "tool_call_output_item": 1,
                "message_output_item": 1,
            },
            "final_ok": True,
            "marker_observed": True,
            "call_summaries": [
                {
                    "call_id": "call_tool",
                    "tool_name": "record_signal",
                    "item_type": "tool_call_item",
                    "item_class": "ToolCallItem",
                    "raw_type": "function_call",
                    "argument_keys": ["marker"],
                    "marker_argument_present": True,
                }
            ],
            "output_summaries": [
                {
                    "call_id": "call_tool",
                    "item_type": "tool_call_output_item",
                    "item_class": "ToolCallOutputItem",
                    "raw_type": "function_call_output",
                    "output_keys": ["marker", "status"],
                    "marker_output_present": True,
                    "status_output_key_present": True,
                }
            ],
            "tool_pairs": [
                {
                    "call_id": "call_tool",
                    "tool_name": "record_signal",
                    "call_recorded": True,
                    "output_recorded": True,
                    "argument_keys": ["marker"],
                    "output_keys": ["marker", "status"],
                    "marker_argument_present": True,
                    "marker_output_present": True,
                }
            ],
            "phase_summaries": [
                {
                    "name": "initial",
                    "new_item_count": 3,
                    "new_item_type_counts": {
                        "tool_call_item": 1,
                        "tool_call_output_item": 1,
                        "message_output_item": 1,
                    },
                    "tool_call_count": 1,
                    "tool_output_count": 1,
                    "message_output_count": 1,
                    "raw_response_count": 2,
                    "interruption_count": 0,
                    "final_output_present": True,
                }
            ],
        }
        tool_result_fixture = {
            "scenario": "tool",
            "status": "completed",
            "passed": True,
            "external_api_called": True,
            "summary": {
                "raw_response_usage_totals": {
                    "requests": 2,
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "total_tokens": 14,
                }
            },
            "validation": {
                "status": "completed",
                "passed": True,
                "failed_checks": [],
                "checks": {"external_api_called": True},
            },
            "tool_state_summary": tool_state_fixture,
            "publish_readback": {
                "status": "completed",
                "task_id": "task_tool_verify",
                "result_summary": {
                    "status": "completed",
                    "tool_call_count": 1,
                    "observation_count": 6,
                    "artifact_count": 1,
                    "tool_call_status_update_expected_count": 0,
                    "tool_call_name_counts": {"record_signal": 1},
                    "observation_tool_counts": {
                        "agent_run_summary": 1,
                        "agent_run_phase": 1,
                        "agent_model_response": 2,
                        "record_signal": 1,
                        "agent_final_output": 1,
                    },
                    "status_counts": {"completed": 7},
                    "call_ids": ["call_tool"],
                    "tool_call_pks": [42],
                    "observation_ids": [101, 102, 103, 104, 105, 106],
                },
                "validation": {
                    "valid": True,
                    "check_count": 52,
                    "checks": {
                        "artifact_count_covers_result_summary": True,
                        "observation_count_covers_result_summary": True,
                        "tool_call_count_covers_result_summary": True,
                    },
                    "evidence_tool_call_name_counts": {"record_signal": 1},
                    "evidence_observation_tool_counts": {
                        "agent_run_summary": 1,
                        "agent_run_phase": 1,
                        "agent_model_response": 2,
                        "record_signal": 1,
                        "agent_final_output": 1,
                    },
                },
                "scenario_projection_validation": {"valid": True, "check_count": 13},
                "scenario_event_replay_validation": {"valid": True, "check_count": 15},
                "sdk_events_summary": {
                    "summary_requested": True,
                    "task_id": "task_tool_verify",
                    "status": "completed",
                    "tool_progress_count": 1,
                    "agent_phase_count": 1,
                    "agent_model_response_count": 2,
                    "final_output_count": 1,
                },
                "client_transcript_summary": {
                    "summary_requested": True,
                    "task_id": "task_tool_verify",
                    "status": "completed",
                    "tool_progress_count": 1,
                    "agent_phase_count": 1,
                    "agent_model_response_count": 2,
                    "final_output_count": 1,
                    "item_type_counts": {
                        "tool_progress": 1,
                        "agent_phase": 1,
                        "agent_model_response": 2,
                        "final_output": 1,
                    },
                },
            },
        }
        tool_summary_fixture = matrix_module.summarize_matrix(
            [tool_result_fixture],
            publish_readback=True,
        )
        tool_compact_summary = matrix_module.matrix_run_compact_summary(
            {
                "status": "completed",
                "run_id": "verify-tool-compact",
                "model": "gpt-4.1-mini",
                "provider": "openai",
                "api_style": "responses",
                "base_url": "http://verify/api/harness/v1",
                "client_id": "verify-client",
                "scenario_count": 1,
                "requested_scenarios": ["tool"],
                "summary": tool_summary_fixture,
                "results": [tool_result_fixture],
            }
        )
        assert tool_summary_fixture["scenario_tool_state_summaries"]["tool"] == tool_state_fixture
        assert tool_summary_fixture["scenario_tool_evidence_summaries"]["tool"][
            "expected_tool_call_recorded"
        ] is True
        assert tool_summary_fixture["scenario_tool_projection_summaries"]["tool"][
            "tool_progress_projected"
        ] is True
        tool_state_summary = tool_compact_summary["scenario_tool_state_summaries"]["tool"]
        assert tool_state_summary["tool_call_count"] == 1
        assert tool_state_summary["tool_output_count"] == 1
        assert tool_state_summary["tool_names"] == ["record_signal"]
        assert tool_state_summary["all_tool_calls_paired"] is True
        assert tool_state_summary["call_summaries"][0]["argument_keys"] == ["marker"]
        assert tool_state_summary["output_summaries"][0]["output_keys"] == [
            "marker",
            "status",
        ]
        assert tool_state_summary["tool_pairs"][0]["marker_output_present"] is True
        assert "raw_item" not in tool_state_summary["call_summaries"][0]
        assert "arguments" not in tool_state_summary["call_summaries"][0]
        assert "output" not in tool_state_summary["output_summaries"][0]
        tool_evidence_summary = tool_compact_summary["scenario_tool_evidence_summaries"][
            "tool"
        ]
        assert tool_evidence_summary["result_tool_call_count"] == 1
        assert tool_evidence_summary["result_tool_call_name_counts"] == {"record_signal": 1}
        assert tool_evidence_summary["result_observation_tool_counts"]["record_signal"] == 1
        assert tool_evidence_summary["evidence_tool_call_name_counts"] == {"record_signal": 1}
        assert tool_evidence_summary["evidence_observation_tool_counts"]["record_signal"] == 1
        assert tool_evidence_summary["expected_tool_call_recorded"] is True
        assert tool_evidence_summary["expected_tool_output_observation_recorded"] is True
        assert tool_evidence_summary["evidence_expected_tool_call_recorded"] is True
        assert tool_evidence_summary["evidence_expected_tool_output_observation_recorded"] is True
        assert tool_evidence_summary["result_call_id_count"] == 1
        assert tool_evidence_summary["result_tool_call_pk_count"] == 1
        assert "tool_call_pks" not in tool_evidence_summary
        assert "observation_ids" not in tool_evidence_summary
        assert "call_ids" not in tool_evidence_summary
        assert tool_evidence_summary["tool_call_count_covers_result_summary"] is True
        tool_projection_summary = tool_compact_summary["scenario_tool_projection_summaries"][
            "tool"
        ]
        assert tool_projection_summary["sdk_events"]["tool_progress_count"] == 1
        assert tool_projection_summary["client_transcript"]["tool_progress_item_count"] == 1
        assert tool_projection_summary["tool_progress_counts_match"] is True
        assert tool_projection_summary["tool_progress_delta"] == 0
        assert tool_projection_summary["final_output_projected"] is True
        assert tool_compact_summary["scenario_readback_tool_projection_summaries"][
            "tool"
        ] == tool_projection_summary
        assert tool_compact_summary["scenario_readback_tool_evidence_summaries"][
            "tool"
        ] == tool_evidence_summary
        assert tool_compact_summary["scenario_approval_projection_summaries"] == {}
        assert tool_compact_summary["scenario_handoff_projection_summaries"] == {}
        tool_rollup = matrix_module.matrix_scenario_rollup(
            {
                "run_id": "verify-tool-compact",
                "summary": tool_summary_fixture,
                "results": [tool_result_fixture],
            }
        )
        assert tool_rollup["scenario_tool_state_summaries"]["tool"] == tool_state_fixture
        assert tool_rollup["scenario_tool_evidence_summaries"]["tool"] == (
            tool_evidence_summary
        )
        assert tool_rollup["scenario_tool_projection_summaries"]["tool"] == (
            tool_projection_summary
        )
        assert tool_rollup["scenarios"][0]["tool_state_summary"] == tool_state_fixture
        assert tool_rollup["scenarios"][0]["tool_evidence_summary"] == tool_evidence_summary
        assert tool_rollup["scenarios"][0]["tool_projection_summary"] == tool_projection_summary
        published_tool_summary = matrix_module.published_scenario_readback_compact_summary(
            {"results": [tool_result_fixture["publish_readback"] | {"scenario": "tool"}]}
        )
        assert published_tool_summary is not None
        assert published_tool_summary["tool_evidence_summaries_by_scenario"]["tool"] == (
            tool_evidence_summary
        )
        assert published_tool_summary["tool_projection_summaries_by_scenario"]["tool"] == (
            tool_projection_summary
        )
        assert published_tool_summary["approval_projection_summaries_by_scenario"] == {}
        assert published_tool_summary["handoff_projection_summaries_by_scenario"] == {}
        handoff_compact_summary = matrix_module.matrix_run_compact_summary(
            {
                "status": "completed",
                "run_id": "verify-handoff-compact",
                "model": "gpt-4.1-mini",
                "provider": "openai",
                "api_style": "responses",
                "base_url": "http://verify/api/harness/v1",
                "client_id": "verify-client",
                "scenario_count": 1,
                "requested_scenarios": ["handoff"],
                "summary": {
                    "status": "completed",
                    "passed_count": 1,
                    "failed_count": 0,
                    "external_api_called": True,
                    "scenario_external_api_called": {"handoff": True},
                },
                "results": [
                    {
                        "scenario": "handoff",
                        "handoff_state_summary": {
                            "status": "completed",
                            "handoff_call_count": 1,
                            "handoff_output_count": 1,
                            "handoff_pair_count": 1,
                            "handoff_completed_observed": True,
                            "source_agents": ["Harness Router Agent"],
                            "target_agents": ["Harness Specialist Agent"],
                            "tool_names": ["transfer_to_harness_specialist"],
                            "expected_tool_observed": True,
                            "expected_source_observed": True,
                            "expected_target_observed": True,
                            "phase_count": 1,
                            "phase_names": ["initial"],
                            "phase_new_item_counts": {"initial": 3},
                            "phase_raw_response_counts": {"initial": 2},
                            "phase_interruption_counts": {"initial": 0},
                            "new_item_type_counts": {
                                "handoff_call_item": 1,
                                "handoff_output_item": 1,
                                "message_output_item": 1,
                            },
                            "final_ok": True,
                            "marker_observed": False,
                            "call_summaries": [
                                {
                                    "call_id": "call_handoff",
                                    "tool_name": "transfer_to_harness_specialist",
                                    "item_type": "handoff_call_item",
                                    "item_class": "HandoffCallItem",
                                    "raw_type": "function_call",
                                    "agent_name": "Harness Router Agent",
                                }
                            ],
                            "output_summaries": [
                                {
                                    "call_id": "call_handoff",
                                    "item_type": "handoff_output_item",
                                    "item_class": "HandoffOutputItem",
                                    "raw_type": "function_call_output",
                                    "agent_name": "Harness Router Agent",
                                    "source_agent": "Harness Router Agent",
                                    "target_agent": "Harness Specialist Agent",
                                    "output_keys": ["assistant"],
                                    "assistant_output_present": True,
                                }
                            ],
                            "handoff_pairs": [
                                {
                                    "call_id": "call_handoff",
                                    "tool_name": "transfer_to_harness_specialist",
                                    "source_agent": "Harness Router Agent",
                                    "target_agent": "Harness Specialist Agent",
                                    "call_recorded": True,
                                    "output_recorded": True,
                                    "assistant_output_present": True,
                                }
                            ],
                        },
                        "publish_readback": {
                            "status": "completed",
                            "task_id": "task_handoff_verify",
                            "result_summary": {
                                "status": "completed",
                                "tool_call_count": 0,
                                "observation_count": 7,
                                "artifact_count": 3,
                                "tool_call_status_update_expected_count": 0,
                                "tool_call_name_counts": {},
                                "observation_tool_counts": {
                                    "agent_run_summary": 1,
                                    "agent_run_phase": 1,
                                    "agent_model_response": 2,
                                    "agent_handoff": 2,
                                    "agent_final_output": 1,
                                },
                                "status_counts": {"completed": 7},
                                "call_ids": ["call_handoff"],
                                "tool_call_pks": [],
                                "observation_ids": [
                                    701,
                                    702,
                                    703,
                                    704,
                                    705,
                                    706,
                                    707,
                                ],
                            },
                            "validation": {
                                "valid": True,
                                "check_count": 1,
                                "checks": {
                                    "artifact_count_covers_result_summary": True,
                                    "observation_count_covers_result_summary": True,
                                    "tool_call_count_covers_result_summary": True,
                                },
                                "evidence_tool_call_name_counts": {},
                                "evidence_observation_tool_counts": {
                                    "agent_run_summary": 1,
                                    "agent_run_phase": 1,
                                    "agent_model_response": 2,
                                    "agent_handoff": 2,
                                    "agent_final_output": 1,
                                },
                            },
                            "scenario_projection_validation": {"valid": True, "check_count": 1},
                            "scenario_event_replay_validation": {"valid": True, "check_count": 1},
                            "sdk_events_summary": {
                                "summary_requested": True,
                                "task_id": "task_handoff_verify",
                                "status": "completed",
                                "agent_handoff_count": 2,
                                "agent_phase_count": 1,
                                "final_output_count": 1,
                                "agent_phase_item_count": 0,
                            },
                            "client_transcript_summary": {
                                "summary_requested": True,
                                "task_id": "task_handoff_verify",
                                "status": "completed",
                                "agent_handoff_count": 2,
                                "agent_phase_count": 1,
                                "final_output_count": 1,
                                "item_type_counts": {
                                    "agent_handoff": 2,
                                    "agent_phase": 1,
                                    "final_output": 1,
                                },
                            },
                        },
                    }
                ],
            }
        )
        handoff_state_summary = handoff_compact_summary["scenario_handoff_state_summaries"][
            "handoff"
        ]
        assert handoff_state_summary["handoff_completed_observed"] is True
        assert handoff_state_summary["expected_tool_observed"] is True
        assert handoff_state_summary["expected_source_observed"] is True
        assert handoff_state_summary["expected_target_observed"] is True
        assert handoff_state_summary["handoff_pairs"][0]["target_agent"] == (
            "Harness Specialist Agent"
        )
        assert "raw_item" not in handoff_state_summary["call_summaries"][0]
        handoff_projection_summary = handoff_compact_summary[
            "scenario_handoff_projection_summaries"
        ]["handoff"]
        assert handoff_projection_summary["sdk_events"]["agent_handoff_count"] == 2
        assert handoff_projection_summary["client_transcript"]["agent_handoff_item_count"] == 2
        assert handoff_projection_summary["handoff_counts_match"] is True
        assert handoff_compact_summary["scenario_readback_handoff_projection_summaries"][
            "handoff"
        ] == handoff_projection_summary
        handoff_evidence_summary = handoff_compact_summary[
            "scenario_handoff_evidence_summaries"
        ]["handoff"]
        assert handoff_evidence_summary["result_tool_call_count"] == 0
        assert handoff_evidence_summary["result_observation_count"] == 7
        assert handoff_evidence_summary["result_observation_tool_counts"]["agent_handoff"] == 2
        assert handoff_evidence_summary["expected_handoff_observation_recorded"] is True
        assert (
            handoff_evidence_summary[
                "evidence_expected_handoff_observation_recorded"
            ]
            is True
        )
        assert handoff_evidence_summary["all_expected_tool_calls_recorded"] is None
        assert handoff_evidence_summary["all_expected_observations_recorded"] is True
        assert handoff_evidence_summary["result_call_id_count"] == 1
        assert handoff_evidence_summary["result_tool_call_pk_count"] == 0
        assert handoff_evidence_summary["result_observation_id_count"] == 7
        assert "call_ids" not in handoff_evidence_summary
        assert "tool_call_pks" not in handoff_evidence_summary
        assert "observation_ids" not in handoff_evidence_summary
        assert handoff_compact_summary["scenario_readback_handoff_evidence_summaries"][
            "handoff"
        ] == handoff_evidence_summary
        assert handoff_compact_summary["scenario_approval_projection_summaries"] == {}
        assert handoff_compact_summary["scenario_readback_approval_projection_summaries"] == {}
        published_handoff_summary = matrix_module.published_scenario_readback_compact_summary(
            {
                "results": [
                    {
                        "scenario": "handoff",
                        "status": "completed",
                        "task_id": "task_handoff_verify",
                        "result_summary": {
                            "status": "completed",
                            "tool_call_count": 0,
                            "observation_count": 7,
                            "artifact_count": 3,
                            "tool_call_status_update_expected_count": 0,
                            "tool_call_name_counts": {},
                            "observation_tool_counts": {
                                "agent_run_summary": 1,
                                "agent_run_phase": 1,
                                "agent_model_response": 2,
                                "agent_handoff": 2,
                                "agent_final_output": 1,
                            },
                            "status_counts": {"completed": 7},
                            "call_ids": ["call_handoff"],
                            "tool_call_pks": [],
                            "observation_ids": [
                                701,
                                702,
                                703,
                                704,
                                705,
                                706,
                                707,
                            ],
                        },
                        "validation": {
                            "valid": True,
                            "check_count": 1,
                            "checks": {
                                "artifact_count_covers_result_summary": True,
                                "observation_count_covers_result_summary": True,
                                "tool_call_count_covers_result_summary": True,
                            },
                            "evidence_tool_call_name_counts": {},
                            "evidence_observation_tool_counts": {
                                "agent_run_summary": 1,
                                "agent_run_phase": 1,
                                "agent_model_response": 2,
                                "agent_handoff": 2,
                                "agent_final_output": 1,
                            },
                        },
                        "scenario_projection_validation": {"valid": True, "check_count": 1},
                        "scenario_event_replay_validation": {"valid": True, "check_count": 1},
                        "sdk_events_summary": {
                            "summary_requested": True,
                            "task_id": "task_handoff_verify",
                            "status": "completed",
                            "agent_handoff_count": 2,
                            "agent_phase_count": 1,
                            "final_output_count": 1,
                        },
                        "client_transcript_summary": {
                            "summary_requested": True,
                            "task_id": "task_handoff_verify",
                            "status": "completed",
                            "agent_handoff_count": 2,
                            "agent_phase_count": 1,
                            "final_output_count": 1,
                            "item_type_counts": {
                                "agent_handoff": 2,
                                "agent_phase": 1,
                            },
                        },
                    }
                ]
            }
        )
        assert published_handoff_summary is not None
        assert published_handoff_summary["handoff_projection_summaries_by_scenario"][
            "handoff"
        ]["handoff_counts_match"] is True
        assert published_handoff_summary["handoff_evidence_summaries_by_scenario"][
            "handoff"
        ] == handoff_evidence_summary
        assert published_handoff_summary["approval_projection_summaries_by_scenario"] == {}
        assert live_compact_summary["scenario_session_summaries"]["approval"][0][
            "agent_session_id"
        ] == "sdk_session_live_compact_verify"
        assert live_compact_summary["scenario_session_summaries"]["approval"][0][
            "final_item_type_counts"
        ] == {"message": 2, "user": 2}
        assert live_compact_summary["scenario_session_state_summaries"]["approval"][
            "agent_session_id"
        ] == "sdk_session_live_compact_verify"
        assert live_compact_summary["scenario_session_state_summaries"]["approval"][
            "turn_count_matches_summary"
        ] is True
        assert live_compact_summary["scenario_session_state_summaries"]["approval"][
            "final_item_count_matches_summary"
        ] is True
        assert live_compact_summary["scenario_session_state_summaries"]["approval"][
            "final_item_type_counts_match_summary"
        ] is True
        assert live_compact_summary["scenario_session_state_summaries"]["approval"][
            "item_count_before_by_phase"
        ] == {"session_initial": 0, "session_turn_2": 2}
        assert live_compact_summary["scenario_session_state_summaries"]["approval"][
            "item_count_after_by_phase"
        ] == {"session_initial": 2, "session_turn_2": 4}
        assert live_compact_summary["scenario_session_state_summaries"]["approval"][
            "session_growth_observed"
        ] is True
        assert live_compact_summary["scenario_session_state_summaries"]["approval"][
            "stateful_second_turn_observed"
        ] is True
        assert live_compact_summary["scenario_stream_projection_summaries"]["approval"][
            "sdk_events"
        ]["agent_stream_event_name_counts"] == {
            "handoff_occured": 1,
            "handoff_requested": 1,
        }
        assert live_compact_summary["scenario_stream_projection_summaries"]["approval"][
            "client_transcript"
        ]["agent_stream_tool_name_counts"] == {"transfer_to_harness_specialist": 2}
        assert live_compact_summary["matrix_payload_readback_task_binding"][
            "matrix_payload_task_id"
        ] == "task_matrix_aggregate_verify"
        assert live_compact_summary["matrix_readback_artifact"] == compact_artifact
        assert live_compact_summary["matrix_readback_artifact_checks"] == compact_artifact_checks
        assert live_compact_summary["matrix_payload_readback_artifact"] == compact_artifact
        assert live_compact_summary["matrix_payload_readback_artifact_checks"] == compact_artifact_checks
        assert live_compact_summary["provider_diagnostic_readback_status"] == "completed"
        assert live_compact_summary["provider_diagnostic_readback_valid"] is True
        assert live_compact_summary["provider_diagnostic_readback_expected_id"] == 99
        assert live_compact_summary["provider_diagnostic_readback_matched_id"] == 99
        assert live_compact_summary["provider_diagnostic_readback_check_count"] == 9
        assert live_compact_summary["provider_diagnostic_readback_failed_checks"] == []
        assert live_compact_summary["provider_diagnostic_readback_checks"][
            "provider_diagnostic_readback_api_style_matches"
        ] is True
        assert live_compact_summary["provider_diagnostic_readback_checks"][
            "provider_diagnostic_readback_external_api_called_matches"
        ] is True
        assert live_compact_summary[
            "provider_diagnostic_readback_matched_provider_response_received"
        ] is True
        assert live_compact_summary["provider_diagnostic_readback_matched_external_api_called"] is True
        assert live_compact_summary["provider_diagnostic_readback_matched_provider"]["model"] == (
            "gpt-4.1-mini"
        )
        scaffold_script = root / ".agents" / "skills" / "agentic-harness-loop" / "scripts" / "scaffold_agent_harness.py"
        assert scaffold_script.exists()
        scaffold_dir = Path(temp) / "agentic-scaffold"
        subprocess.run(
            [
                sys.executable,
                str(scaffold_script),
                "--output-dir",
                str(scaffold_dir),
                "--name",
                "verify-agentic-loop",
                "--provider",
                "openai",
                "--model",
                "gpt-5",
                "--client-id",
                "verify-client",
                "--cwd",
                str(root / "developer_harness"),
                "--force",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        scaffold_files = {
            "provider_profile.json",
            "harness_task_payload.json",
            "evidence_holder_payload.json",
            "evidence_templates.json",
            "eval_case_seed.json",
            "agent_loop_contract.md",
            "run_probe.ps1",
            "run_task.ps1",
            "run_evidence_holder.ps1",
            "ingest_trace.ps1",
        }
        assert {path.name for path in scaffold_dir.iterdir()} >= scaffold_files
        provider_profile = json.loads((scaffold_dir / "provider_profile.json").read_text(encoding="utf-8"))
        assert provider_profile["api_style"] == "responses"
        assert provider_profile["supports_stateful_responses"] is True
        task_payload = json.loads((scaffold_dir / "harness_task_payload.json").read_text(encoding="utf-8"))
        assert task_payload["idempotency_key"] == "verify-agentic-loop-openai-gpt-5"
        assert task_payload["skills"] == ["agentic-harness-loop", "local-llm-provider-diagnostics"]
        assert task_payload["metadata"]["agentic_loop_template"] == "agents_sdk_trace_eval_codex"
        assert task_payload["verification"]["commands"] == ["python -B verify.py"]
        evidence_holder_payload = json.loads((scaffold_dir / "evidence_holder_payload.json").read_text(encoding="utf-8"))
        assert evidence_holder_payload["idempotency_key"] == "verify-agentic-loop-openai-gpt-5-evidence-holder"
        assert evidence_holder_payload["sandbox"] == "read-only"
        assert evidence_holder_payload["metadata"]["evidence_kind"] == "external_live_llm_smoke_holder"
        assert evidence_holder_payload["verification"]["commands"] == []
        evidence_templates = json.loads((scaffold_dir / "evidence_templates.json").read_text(encoding="utf-8"))
        assert {"tool_call", "observation", "artifact"} <= set(evidence_templates)
        eval_seed = json.loads((scaffold_dir / "eval_case_seed.json").read_text(encoding="utf-8"))
        assert eval_seed["failure_label"] == "configuration_required"
        assert "Invoke-RestMethod" in (scaffold_dir / "run_task.ps1").read_text(encoding="utf-8")
        assert "X-Harness-Client-Id" in (scaffold_dir / "run_task.ps1").read_text(encoding="utf-8")
        assert "tasks?start=false" in (scaffold_dir / "run_evidence_holder.ps1").read_text(encoding="utf-8")
        assert "--supports-stateful-responses true" in (scaffold_dir / "run_probe.ps1").read_text(encoding="utf-8")
        assert "trace_ingest.py" in (scaffold_dir / "ingest_trace.ps1").read_text(encoding="utf-8")
        assert {item["name"] for item in capabilities["agents"]} >= {
            "harness-explorer",
            "harness-worker",
            "harness-verifier",
        }
        assert capabilities["loop_engineering_contract"]["isolation"] == "stateful_operations_require_x_harness_client_id"
        assert capabilities["loop_engineering_contract"]["health_ready"] == "health_is_public_ready_reports_persistence_and_capability_status"
        assert capabilities["loop_engineering_contract"]["idempotency"] == "client_scoped_idempotency_key_body_or_header"
        assert (
            capabilities["loop_engineering_contract"]["sdk_events"]
            == "client_friendly_task_started_progress_handoff_approval_question_guardrail_notification_session_state_projection"
        )
        assert (
            capabilities["loop_engineering_contract"]["read_model_summaries"]
            == "sdk_events_and_client_transcript_summary_views_for_fast_controller_checks"
        )
        assert capabilities["read_model_contract"]["source"] == "developer_harness_read_model_contract"
        assert capabilities["read_model_contract"]["cursor"]["basis"] == "raw_harness_event_id"
        assert "agent_eval_case_verification" in capabilities["read_model_contract"]["sdk_event_types"]
        assert "harness_verification_result" in capabilities["read_model_contract"]["sdk_event_types"]
        assert "agent_eval_case_verifications" in capabilities["read_model_contract"]["client_transcript_sections"]
        assert "harness_verification_results" in capabilities["read_model_contract"]["client_transcript_sections"]
        assert "agent_eval_case_verification_count" in capabilities["read_model_contract"]["summary_fields"]
        assert "harness_verification_result_count" in capabilities["read_model_contract"]["summary_fields"]
        assert "not prove current provider connectivity" in capabilities["read_model_contract"]["claim_boundary"]
        assert (
            capabilities["loop_engineering_contract"]["sdk_initialize"]
            == "client_friendly_initialize_mcp_status_and_context_usage_snapshots"
        )
        assert (
            capabilities["loop_engineering_contract"]["tool_progress"]
            == "sdk_tool_progress_projection_from_recorded_tool_calls"
        )
        assert (
            capabilities["loop_engineering_contract"]["session_metadata"]
            == "sdk_like_session_info_title_tag_summary_and_task_fork"
        )
        assert (
            capabilities["loop_engineering_contract"]["workspace_isolation"]
            == "workspace_mode_client_isolated_creates_separate_cwd_and_agents_md"
        )
        assert capabilities["loop_engineering_contract"]["workers"] == "lease_owner_epoch_expires_at_with_heartbeat"
        assert (
            capabilities["loop_engineering_contract"]["queued_messages"]
            == "durable_now_next_later_input_queue_with_cancel"
        )
        assert (
            capabilities["loop_engineering_contract"]["permission_decisions"]
            == "structured_allow_ask_deny_passthrough_records"
        )
        assert (
            capabilities["loop_engineering_contract"]["task_progress"]
            == "todo_write_style_progress_items_with_verification_nudge"
        )
        assert capabilities["loop_engineering_contract"]["provider_diagnostics"] == "client_scoped_models_probe_records"
        assert capabilities["loop_engineering_contract"]["tool_evidence"] == "typed_tool_calls_observations_and_artifacts"
        assert capabilities["loop_engineering_contract"]["eval_cases"] == "client_scoped_trace_gap_regression_cases"
        assert capabilities["loop_engineering_contract"]["task_evidence"] == "tool_observation_artifact_verification_and_hook_replay"
        assert (
            capabilities["loop_engineering_contract"]["client_transcript"]
            == "sdk_like_status_messages_tool_handoff_approval_question_guardrail_summary_and_source_endpoints"
        )
        assert (
            capabilities["loop_engineering_contract"]["subagent_handoff"]
            == "parent_task_child_summaries_and_join_status"
        )
        assert capabilities["loop_engineering_contract"]["loop_evidence_contract"] == "trace_eval_verification_termination_contract_per_loop"
        assert capabilities["loop_engineering_contract"]["loop_evidence"] == "client_scoped_loop_iteration_task_evidence_summary"
        assert "trace_capture" in capabilities["agentic_ai_harness_contract"]["agent_sdk_primitives"]
        assert capabilities["agentic_ai_harness_contract"]["loop_iteration_contract"] == [
            "declare_trace_sources_eval_targets_and_verification_commands",
            "record_iteration_task_id_and_evidence_refs",
            "terminate_on_max_iterations_or_explicit_pause_cancel",
            "promote_repeatable_gaps_to_eval_cases",
        ]
        assert (
            capabilities["agentic_ai_harness_contract"]["improvement_loop"]
            == [
                "collect_trace_or_runtime_evidence",
                "classify_failure_or_gap",
                "turn_gap_into_eval_or_verifier",
                "patch_harness_or_agent_runtime",
                "rerun_verification",
                "persist_evidence_in_events",
            ]
        )
        assert {
            item["id"] for item in capabilities["agentic_loop_templates"]
        } >= {"agents_sdk_trace_eval_codex", "backend_owned_tool_loop_hardening"}
        task = runtime.prepare_task(
            {
                "prompt": "verify the harness",
                "cwd": str(root),
                "agent": "harness-verifier",
                "skills": ["harness-task"],
                "sandbox": "read-only",
                "model": None,
                "llm_provider": {
                    "provider": "ollama",
                    "api_style": "chat_completions",
                    "base_url": "http://localhost:11434/v1",
                    "model": "local-test",
                    "api_key_env": None,
                    "supports_tool_calling": True,
                    "supports_stateful_responses": False,
                    "notes": None,
                },
                "metadata": {},
                "verification": {"commands": [], "timeout_seconds": 30},
            }
        )
        completed = runtime.run_task(task["task_id"])
        assert completed["status"] == "completed"
        assert completed["thread_id"] == "thr_test"
        assert completed["verification_results"] == []
        assert completed["updated_at"] >= completed["created_at"]
        failing_runtime = DevelopmentHarness(root, HarnessStore(Path(temp) / "failing-harness.sqlite3"), FailingExecutor())
        failing_task = failing_runtime.prepare_task(
            {
                "prompt": "verify executor failure closes the task",
                "cwd": str(root),
                "agent": "harness-worker",
                "skills": ["agentic-harness-loop"],
                "sandbox": "read-only",
                "model": None,
                "metadata": {},
                "verification": {"commands": [], "timeout_seconds": 30},
            }
        )
        failed = failing_runtime.run_task(failing_task["task_id"])
        assert failed["status"] == "failed"
        assert failed["lease"] is None
        assert failed["updated_at"] >= failed["created_at"]
        assert failed["error"]["code"] == "CODEX_EXEC_EXCEPTION"
        assert failed["result"]["stage"] == "codex_exec"
        failing_events = failing_runtime.store.events(failing_task["task_id"])
        assert "task.failed" in {item["event_type"] for item in failing_events}
        assert any(
            item["event_type"] == "hook.after_codex_exec" and item.get("status") == "failed"
            for item in failing_events
        )
        failing_sdk = failing_runtime.task_sdk_events(failing_task["task_id"])
        failure_notification = next(
            item
            for item in failing_sdk["events"]
            if item.get("subtype") == "task_notification" and item.get("status") == "failed"
        )
        assert failure_notification["summary"].startswith("CODEX_EXEC_EXCEPTION:")
        assert not failure_notification["summary"].startswith("{")
        assert failure_notification["returncode"] == -1
        assert failure_notification["error"]["code"] == "CODEX_EXEC_EXCEPTION"
        assert failure_notification["error"]["stage"] == "codex_exec"
        assert any(item.get("subtype") == "session_state_changed" and item.get("state") == "idle" for item in failing_sdk["events"])
        failing_transcript = failing_runtime.task_client_transcript(failing_task["task_id"])
        assert failing_transcript["status_line"]["task_status"] == "failed"
        assert failing_transcript["status_line"]["lease"] is None
        assert failing_transcript["status_line"]["returncode"] == -1
        assert failing_transcript["status_line"]["error"]["code"] == "CODEX_EXEC_EXCEPTION"
        assert failing_transcript["session_info"]["error"]["stage"] == "codex_exec"
        failing_evidence = failing_runtime.task_evidence(failing_task["task_id"])
        assert {hook["event_name"] for hook in failing_evidence["hook_executions"]} >= {
            "before_task_run",
            "before_codex_exec",
            "after_codex_exec",
            "after_task_run",
        }
        annotated_session = runtime.annotate_task(
            task["task_id"],
            {
                "title": "Harness verification session",
                "tag": "sdk-gap",
                "summary": "Verified SDK-like harness task metadata.",
            },
            expected_state_version=completed["state_version"],
        )
        assert annotated_session["custom_title"] == "Harness verification session"
        assert annotated_session["tag"] == "sdk-gap"
        assert annotated_session["summary"] == "Verified SDK-like harness task metadata."
        session_info = runtime.task_session_info(task["task_id"])
        assert session_info["session"]["title"] == "Harness verification session"
        session_list = runtime.list_task_sessions(task["client_id"])
        assert session_list["sessions"][0]["task_id"] == task["task_id"]
        sdk_init = runtime.sdk_initialize(task["client_id"])
        assert sdk_init["commands"]
        assert {agent["name"] for agent in sdk_init["agents"]} >= {
            "harness-explorer",
            "harness-worker",
            "harness-verifier",
        }
        assert sdk_init["models"][0]["provider"]["api_style"] in {"responses", "chat_completions"}
        assert sdk_init["account"]["subscriptionType"] == "developer_harness"
        sdk_mcp = runtime.sdk_mcp_status()
        assert sdk_mcp["mcpServers"] == [] or sdk_mcp["mcpServers"][0]["status"] in {
            "disabled",
            "connected",
            "failed",
            "needs-auth",
            "pending",
        }
        forked_task = runtime.fork_task(
            task["task_id"],
            {
                "prompt": "forked harness verification",
                "idempotency_key": "forked-harness-verification",
                "metadata": {"fork_reason": "verify"},
            },
            client_id=task["client_id"],
            expected_state_version=session_info["session"]["state_version"],
        )
        assert forked_task["request"]["prompt"] == "forked harness verification"
        assert forked_task["request"]["metadata"]["forked_from_task_id"] == task["task_id"]
        assert runtime.task_session_info(forked_task["task_id"])["session"]["forked_from_task_id"] == task["task_id"]
        assert len(runtime.store.events(task["task_id"])) >= 4
        tool_call = runtime.record_tool_call(
            task["task_id"],
            {
                "tool_name": "local_search",
                "input": {"query": "jacket sizing"},
                "status": "completed",
                "metadata": {"source": "harness-test"},
            },
        )
        observation = runtime.record_observation(
            task["task_id"],
            {
                "call_id": tool_call["call_id"],
                "tool_name": "local_search",
                "result": {"matches": []},
                "evidence_refs": ["artifact:test-log"],
                "status": "completed",
            },
        )
        running_tool_call = runtime.record_tool_call(
            task["task_id"],
            {
                "call_id": "call_running_then_observed",
                "tool_name": "provider_probe",
                "input": {"provider": "openai"},
                "status": "running",
                "metadata": {"source": "harness-test"},
            },
        )
        running_observation = runtime.record_observation(
            task["task_id"],
            {
                "call_id": running_tool_call["call_id"],
                "tool_name": "provider_probe",
                "result": {"status": "completed"},
                "evidence_refs": ["diagnostic:live-provider"],
                "status": "completed",
            },
        )
        handoff_observation = runtime.record_observation(
            task["task_id"],
            {
                "call_id": "call_handoff_verify",
                "tool_name": "agent_handoff",
                "result": {
                    "item_type": "handoff_output_item",
                    "tool_name": "transfer_to_harness_specialist",
                    "raw_type": "function_call_output",
                    "output": {"assistant": "Harness Specialist Agent"},
                    "agent": "Harness Router Agent",
                    "source_agent": "Harness Router Agent",
                    "target_agent": "Harness Specialist Agent",
                },
                "evidence_refs": ["trace_item:handoff_output_item"],
                "status": "completed",
                "metadata": {"source": "verify"},
            },
        )
        approval_request_observation = runtime.record_observation(
            task["task_id"],
            {
                "call_id": "call_approval_verify",
                "tool_name": "agent_approval_request",
                "result": {
                    "item_type": "ToolApprovalItem",
                    "tool_name": "approval_required_signal",
                    "arguments": {"marker": "AGENTS_SDK_APPROVAL_TOOL_OK"},
                    "raw_type": "function_call",
                    "agent": "Harness Approval Agent",
                },
                "evidence_refs": ["trace_item:ToolApprovalItem"],
                "status": "completed",
                "metadata": {"source": "verify"},
            },
        )
        approval_decision_observation = runtime.record_observation(
            task["task_id"],
            {
                "call_id": "call_approval_verify",
                "tool_name": "agent_approval_decision",
                "result": {
                    "decision": "approved",
                    "item_type": "ToolApprovalItem",
                    "tool_name": "approval_required_signal",
                    "arguments": {"marker": "AGENTS_SDK_APPROVAL_TOOL_OK"},
                    "raw_type": "function_call",
                    "agent": "Harness Approval Agent",
                },
                "evidence_refs": ["approval_decision:1"],
                "status": "completed",
                "metadata": {"source": "verify"},
            },
        )
        guardrail_observation = runtime.record_observation(
            task["task_id"],
            {
                "tool_name": "agent_guardrail_result",
                "result": {
                    "surface": "output_guardrail_results",
                    "guardrail_type": "output_guardrail",
                    "guardrail_name": "harness_output_guardrail",
                    "guardrail_class": "OutputGuardrail",
                    "tripwire_triggered": False,
                    "output_info": {
                        "status": "checked",
                        "marker": "AGENTS_SDK_GUARDRAIL_TOOL_OK",
                    },
                    "agent": "Harness Guardrail Agent",
                    "agent_output": "AGENTS_SDK_GUARDRAIL_FINAL_OK",
                },
                "evidence_refs": ["guardrail:output_guardrail_results:1"],
                "status": "completed",
                "metadata": {"source": "verify"},
            },
        )
        tripwire_guardrail_observation = runtime.record_observation(
            task["task_id"],
            {
                "tool_name": "agent_guardrail_result",
                "result": {
                    "surface": "output_guardrail_results",
                    "guardrail_type": "output_guardrail",
                    "guardrail_name": "harness_tripwire_guardrail",
                    "guardrail_class": "OutputGuardrail",
                    "tripwire_triggered": True,
                    "output_info": {
                        "status": "blocked",
                        "marker": "AGENTS_SDK_GUARDRAIL_TRIPWIRE_TOOL_OK",
                        "reason": "intentional_live_smoke_tripwire",
                    },
                    "agent": "Harness Tripwire Agent",
                    "agent_output": "AGENTS_SDK_GUARDRAIL_TRIPWIRE_FINAL_OK",
                },
                "evidence_refs": ["guardrail:output_guardrail_results:2"],
                "status": "failed",
                "metadata": {"source": "verify"},
            },
        )
        phase_observation = runtime.record_observation(
            task["task_id"],
            {
                "tool_name": "agent_run_phase",
                "result": {
                    "index": 2,
                    "name": "after_approval",
                    "final_output": "AGENTS_SDK_APPROVAL_FINAL_OK",
                    "new_item_count": 2,
                    "raw_response_count": 1,
                    "interruption_count": 0,
                },
                "evidence_refs": ["artifact:agent_trace:verify_trace.json", "phase:2"],
                "status": "completed",
                "metadata": {"source": "verify"},
            },
        )
        session_observation = runtime.record_observation(
            task["task_id"],
            {
                "tool_name": "agent_session",
                "result": {
                    "session_id": "trace_verify_session",
                    "backend": "SQLiteSession",
                    "db_path": "harness_outputs/sdk-live-smoke/verify.session.sqlite3",
                    "turn_count": 2,
                    "final_item_count": 4,
                    "final_item_types": ["message", "message", "message", "message"],
                    "turns": [
                        {
                            "index": 1,
                            "phase": "session_initial",
                            "final_output": "SESSION_TURN_1_OK",
                            "item_count_before": 0,
                            "item_count_after": 2,
                        },
                        {
                            "index": 2,
                            "phase": "session_turn_2",
                            "final_output": "AGENTS_SDK_SESSION_FINAL_OK",
                            "item_count_before": 2,
                            "item_count_after": 4,
                        },
                    ],
                },
                "evidence_refs": ["artifact:agent_trace:verify_trace.json", "agent_session"],
                "status": "completed",
                "metadata": {"source": "verify"},
            },
        )
        final_output_observation = runtime.record_observation(
            task["task_id"],
            {
                "tool_name": "agent_final_output",
                "result": {"final_output": "AGENTS_SDK_INGESTED_FINAL_OK"},
                "evidence_refs": ["artifact:agent_trace:verify_trace.json"],
                "status": "completed",
                "metadata": {"source": "verify"},
            },
        )
        model_response_observation = runtime.record_observation(
            task["task_id"],
            {
                "tool_name": "agent_model_response",
                "result": {
                    "response_id": "resp_context_usage_verify",
                    "model": "gpt-5-mini",
                    "model_source": "trace",
                    "provider": "openai",
                    "api_style": "responses",
                    "usage": {
                        "requests": 2,
                        "input_tokens": 120,
                        "output_tokens": 30,
                        "total_tokens": 150,
                        "input_tokens_details": {"cached_tokens": 12},
                    },
                },
                "evidence_refs": ["artifact:agent_trace:verify_trace.json", "raw_response:context_usage"],
                "status": "completed",
                "metadata": {"source": "verify"},
            },
        )
        run_error_observation = runtime.record_observation(
            task["task_id"],
            {
                "tool_name": "agent_run_error",
                "result": {
                    "status": "guardrail_tripwire_triggered",
                    "error_type": "OutputGuardrailTripwireTriggered",
                    "error": "Guardrail OutputGuardrail triggered tripwire",
                    "scenario": "guardrail_tripwire",
                    "trace_id": "trace_tripwire_verify",
                    "external_api_called": True,
                },
                "evidence_refs": ["artifact:agent_trace:verify_trace.json"],
                "status": "failed",
                "metadata": {"source": "verify"},
            },
        )
        stream_event_observation = runtime.record_observation(
            task["task_id"],
            {
                "call_id": "call_stream_verify",
                "tool_name": "agent_stream_event",
                "result": {
                    "index": 2,
                    "type": "run_item_stream_event",
                    "class": "RunItemStreamEvent",
                    "name": "tool_called",
                    "raw_event_type": "response.output_item.added",
                    "item_type": "tool_call_item",
                    "item_class": "ToolCallItem",
                    "tool_name": "record_signal",
                    "call_id": "call_stream_verify",
                    "agent": "Harness Stream Agent",
                },
                "evidence_refs": ["artifact:agent_trace:verify_trace.json", "stream_event:2"],
                "status": "completed",
                "metadata": {"source": "verify"},
            },
        )
        stream_handoff_observation = runtime.record_observation(
            task["task_id"],
            {
                "call_id": "call_stream_handoff_verify",
                "tool_name": "agent_stream_event",
                "result": {
                    "index": 3,
                    "type": "run_item_stream_event",
                    "class": "RunItemStreamEvent",
                    "name": "handoff_output",
                    "raw_event_type": "response.output_item.done",
                    "item_type": "handoff_output_item",
                    "item_class": "HandoffOutputItem",
                    "tool_name": "transfer_to_harness_specialist",
                    "call_id": "call_stream_handoff_verify",
                    "agent": "Harness Specialist Agent",
                },
                "evidence_refs": ["artifact:agent_trace:verify_trace.json", "stream_event:3"],
                "status": "completed",
                "metadata": {"source": "verify"},
            },
        )
        question_observation = runtime.record_observation(
            task["task_id"],
            {
                "tool_name": "agent_question",
                "result": {
                    "question_type": "missing_info_request",
                    "questions": [
                        {
                            "field": "baseline_evidence",
                            "question": "기준 옷의 실측/소재 근거가 필요합니다.",
                        }
                    ],
                    "reason": "baseline_evidence_required_before_precise_similarity_report",
                    "evidence_status": "baseline_evidence_incomplete",
                    "source_observation": "write_purchase_report",
                    "agent": "Harness Question Agent",
                },
                "evidence_refs": ["trace_item:missing_info_request"],
                "status": "waiting_for_user",
                "metadata": {"source": "verify"},
            },
        )
        guidance_observation = runtime.record_observation(
            task["task_id"],
            {
                "tool_name": "agent_guidance",
                "result": {
                    "subtype": "continue_candidate_enrichment",
                    "guidance_type": "backend_enrichment_required",
                    "reason": "continue_search_for_closer_measurements",
                    "candidate_key": "candidate_weak_fit",
                    "missing_fields": ["size_measurements"],
                    "required_action": "Call the suggested backend tools before reporting.",
                    "ask_user_allowed": False,
                    "next_tool_suggestions": [
                        {
                            "tool": "search_web_candidates",
                            "arguments": {
                                "query": "tee length 68 shoulder 52 chest 55 sleeve 20",
                                "refresh_web": False,
                            },
                            "reason": "continue_search_for_closer_measurement_match",
                        }
                    ],
                    "close_fit_search_budget": {
                        "attempt_count": 1,
                        "attempt_limit": 24,
                        "remaining_attempts": 23,
                        "attempted_queries": ["tee length 68 shoulder 52 chest 55 sleeve 20"],
                    },
                    "search_recovery_policy": "revise_queries_and_continue_until_close_fit_or_budget_exhausted",
                    "agent": "Harness Guidance Agent",
                },
                "evidence_refs": ["artifact:agent_trace:verify_trace.json", "agent_guidance:1"],
                "status": "completed",
                "metadata": {"source": "verify"},
            },
        )
        eval_case_verification_observation = runtime.record_observation(
            task["task_id"],
            {
                "tool_name": "eval_case_verification",
                "result": {
                    "source": "harness_eval_case_verify",
                    "status": "failed",
                    "execute": True,
                    "eval_case_id": 61,
                    "name": "Agents SDK matrix readback failure: verify",
                    "source_task_id": "task_matrix_aggregate_verify",
                    "failure_label": "aggregate:matrix_summary_payload_matches",
                    "verifier_command": (
                        "python .agents/skills/agentic-harness-loop/scripts/"
                        "agents_sdk_smoke_matrix.py --validate-matrix-readback-task-id "
                        "task_matrix_aggregate_verify"
                    ),
                    "expected_status": "completed",
                    "expected_status_matches": False,
                    "failed_checks": [
                        "eval_case_verifier_completed",
                        "eval_case_expected_status_matches",
                    ],
                    "execution": {
                        "status": "failed",
                        "executed_command": f"{sys.executable} agents_sdk_smoke_matrix.py",
                        "returncode": 1,
                        "stdout_tail": "{\"status\": \"failed\"}",
                        "stderr_tail": "",
                    },
                    "claim_boundary": (
                        "Stored eval-case verification evidence only; this observation does not prove "
                        "current provider connectivity."
                    ),
                },
                "evidence_refs": ["eval_case:61", "task:task_matrix_aggregate_verify"],
                "status": "failed",
                "metadata": {"source": "verify"},
            },
        )
        permission_decision = runtime.record_permission_decision(
            task["task_id"],
            {
                "call_id": tool_call["call_id"],
                "tool_name": "local_search",
                "behavior": "allow",
                "input": {"query": "jacket sizing"},
                "decision_reason": {"type": "mode", "mode": "default"},
                "status": "applied",
            },
        )
        progress = runtime.record_task_progress(
            task["task_id"],
            {
                "items": [
                    {
                        "item_id": "inspect",
                        "content": "Inspect Claude SDK task progress behavior",
                        "active_form": "Inspecting Claude SDK task progress behavior",
                        "status": "completed",
                    },
                    {
                        "item_id": "implement",
                        "content": "Implement harness progress evidence",
                        "active_form": "Implementing harness progress evidence",
                        "status": "completed",
                    },
                    {
                        "item_id": "document",
                        "content": "Document progress evidence gap",
                        "active_form": "Documenting progress evidence gap",
                        "status": "completed",
                    },
                ],
                "metadata": {"source": "verify"},
            },
        )
        artifact = runtime.record_artifact(
            task["task_id"],
            {
                "kind": "trace",
                "path": "harness_outputs/test-trace.json",
                "digest": "sha256:test",
            },
        )
        evidence = runtime.task_evidence(task["task_id"])
        assert evidence["tool_calls"][0]["call_id"] == tool_call["call_id"]
        assert evidence["tool_calls"][1]["call_id"] == running_tool_call["call_id"]
        assert evidence["tool_calls"][1]["status"] == "completed"
        assert evidence["tool_calls"][1]["completed_at"]
        assert evidence["observations"][0]["observation_id"] == observation["observation_id"]
        assert evidence["observations"][1]["observation_id"] == running_observation["observation_id"]
        assert evidence["observations"][2]["observation_id"] == handoff_observation["observation_id"]
        assert evidence["observations"][3]["observation_id"] == approval_request_observation["observation_id"]
        assert evidence["observations"][4]["observation_id"] == approval_decision_observation["observation_id"]
        assert evidence["observations"][5]["observation_id"] == guardrail_observation["observation_id"]
        assert evidence["observations"][6]["observation_id"] == tripwire_guardrail_observation["observation_id"]
        assert evidence["observations"][7]["observation_id"] == phase_observation["observation_id"]
        assert evidence["observations"][8]["observation_id"] == session_observation["observation_id"]
        assert evidence["observations"][9]["observation_id"] == final_output_observation["observation_id"]
        assert evidence["observations"][10]["observation_id"] == model_response_observation["observation_id"]
        assert evidence["observations"][11]["observation_id"] == run_error_observation["observation_id"]
        assert evidence["observations"][12]["observation_id"] == stream_event_observation["observation_id"]
        assert evidence["observations"][13]["observation_id"] == stream_handoff_observation["observation_id"]
        assert evidence["observations"][14]["observation_id"] == question_observation["observation_id"]
        assert evidence["observations"][15]["observation_id"] == guidance_observation["observation_id"]
        assert (
            evidence["observations"][16]["observation_id"]
            == eval_case_verification_observation["observation_id"]
        )
        assert evidence["permission_decisions"][0]["decision_id"] == permission_decision["decision_id"]
        assert evidence["permission_decisions"][0]["behavior"] == "allow"
        assert evidence["task_progress"][0]["progress_id"] == progress["progress_id"]
        assert evidence["task_progress"][0]["verification_nudge_needed"] is True
        assert evidence["artifacts"][0]["artifact_id"] == artifact["artifact_id"]
        context_usage = runtime.sdk_context_usage(task["client_id"])
        assert context_usage["totalTokens"] >= 1
        assert context_usage["sourceTaskCount"] >= 1
        assert task["task_id"] in context_usage["sourceTaskIds"]
        assert context_usage["eventCount"] >= 1
        assert context_usage["evidenceCounts"]["observations"] >= 1
        assert "not tokenizer-backed" in context_usage["claim_boundary"]
        assert context_usage["messageBreakdown"]["toolCallTokens"] >= 1
        assert context_usage["apiUsage"]["source"] == "stored_agent_model_response_observations"
        assert context_usage["apiUsage"]["responseCount"] == 1
        assert context_usage["apiUsage"]["requestCount"] == 2
        assert context_usage["apiUsage"]["taskCount"] == 1
        assert context_usage["apiUsage"]["inputTokens"] == 120
        assert context_usage["apiUsage"]["outputTokens"] == 30
        assert context_usage["apiUsage"]["totalTokens"] == 150
        assert context_usage["apiUsage"]["cachedTokens"] == 12
        assert task["task_id"] in context_usage["apiUsage"]["taskIds"]
        assert context_usage["apiUsage"]["taskUsage"][task["task_id"]] == {
            "responseCount": 1,
            "requestCount": 2,
            "inputTokens": 120,
            "outputTokens": 30,
            "totalTokens": 150,
            "cachedTokens": 12,
            "evidenceRefCount": 2,
            "modelCounts": {"gpt-5-mini": 1},
            "statusCounts": {"completed": 1},
        }
        assert context_usage["apiUsage"]["models"] == ["gpt-5-mini"]
        assert context_usage["apiUsage"]["providers"] == ["openai"]
        assert context_usage["apiUsage"]["apiStyles"] == ["responses"]
        assert context_usage["apiUsage"]["modelCounts"] == {"gpt-5-mini": 1}
        assert context_usage["apiUsage"]["statusCounts"] == {"completed": 1}
        assert context_usage["apiUsage"]["providerCounts"] == {"openai": 1}
        assert context_usage["apiUsage"]["apiStyleCounts"] == {"responses": 1}
        assert context_usage["apiUsage"]["modelUsage"]["gpt-5-mini"] == {
            "responseCount": 1,
            "requestCount": 2,
            "inputTokens": 120,
            "outputTokens": 30,
            "totalTokens": 150,
            "cachedTokens": 12,
            "evidenceRefCount": 2,
            "taskIds": [task["task_id"]],
            "statusCounts": {"completed": 1},
            "providerCounts": {"openai": 1},
            "apiStyleCounts": {"responses": 1},
        }
        assert context_usage["apiUsage"]["statusUsage"]["completed"] == {
            "responseCount": 1,
            "requestCount": 2,
            "inputTokens": 120,
            "outputTokens": 30,
            "totalTokens": 150,
            "cachedTokens": 12,
            "evidenceRefCount": 2,
            "taskIds": [task["task_id"]],
            "modelCounts": {"gpt-5-mini": 1},
            "providerCounts": {"openai": 1},
            "apiStyleCounts": {"responses": 1},
        }
        assert context_usage["apiUsage"]["providerUsage"]["openai"] == {
            "responseCount": 1,
            "requestCount": 2,
            "inputTokens": 120,
            "outputTokens": 30,
            "totalTokens": 150,
            "cachedTokens": 12,
            "evidenceRefCount": 2,
            "taskIds": [task["task_id"]],
            "modelCounts": {"gpt-5-mini": 1},
            "statusCounts": {"completed": 1},
            "apiStyleCounts": {"responses": 1},
        }
        assert context_usage["apiUsage"]["apiStyleUsage"]["responses"] == {
            "responseCount": 1,
            "requestCount": 2,
            "inputTokens": 120,
            "outputTokens": 30,
            "totalTokens": 150,
            "cachedTokens": 12,
            "evidenceRefCount": 2,
            "taskIds": [task["task_id"]],
            "modelCounts": {"gpt-5-mini": 1},
            "statusCounts": {"completed": 1},
            "providerCounts": {"openai": 1},
        }
        assert context_usage["apiUsage"]["evidenceRefCount"] == 2
        assert context_usage["apiUsage"]["responses"][0]["response_id"] == "resp_context_usage_verify"
        assert context_usage["apiUsage"]["responses"][0]["model_source"] == "trace"
        assert context_usage["apiUsage"]["responses"][0]["provider"] == "openai"
        assert context_usage["apiUsage"]["responses"][0]["api_style"] == "responses"
        assert context_usage["apiUsage"]["responses"][0]["request_count"] == 2
        assert context_usage["apiUsage"]["responses"][0]["evidence_refs"] == [
            "artifact:agent_trace:verify_trace.json",
            "raw_response:context_usage",
        ]
        assert "not proof" in context_usage["apiUsage"]["claim_boundary"]
        assert {hook["event_name"] for hook in evidence["hook_executions"]} >= {
            "before_task_run",
            "before_codex_exec",
            "after_codex_exec",
            "after_task_run",
        }
        transcript = runtime.task_client_transcript(task["task_id"])
        assert transcript["session_id"] == "thr_test"
        assert transcript["session_info"]["custom_title"] == "Harness verification session"
        assert transcript["status_line"]["task_status"] == "completed"
        assert transcript["status_line"]["counts"]["tool_calls"] == 2
        assert transcript["status_line"]["counts"]["permission_decisions"] == 1
        assert transcript["status_line"]["counts"]["task_progress"] == 1
        assert transcript["status_line"]["task_progress"]["counts"]["completed"] == 3
        assert transcript["task_progress"]["verification_nudge_needed"] is True
        assert transcript["status_line"]["verification_status"] == "not_run"
        assert transcript["messages"][0]["role"] == "user"
        assert any(item["type"] == "streamlined_text" and item["text"] == "verified" for item in transcript["messages"])
        assert transcript["tool_use_summary"]["items"][0]["tool_name"] == "local_search"
        assert transcript["tool_use_summary"]["items"][0]["has_observation"] is True
        assert transcript["tool_use_summary"]["items"][1]["tool_name"] == "provider_probe"
        assert transcript["tool_use_summary"]["items"][1]["status"] == "completed"
        assert transcript["tool_use_summary"]["items"][1]["has_observation"] is True
        assert transcript["tool_progress"][0]["type"] == "tool_progress"
        assert transcript["tool_progress"][0]["tool_use_id"] == tool_call["call_id"]
        assert transcript["tool_progress"][0]["tool_name"] == "local_search"
        assert transcript["tool_progress"][0]["parent_tool_use_id"] is None
        assert transcript["tool_progress"][1]["tool_use_id"] == running_tool_call["call_id"]
        assert transcript["tool_progress"][1]["status"] == "completed"
        assert transcript["tool_progress"][1]["completed_at"]
        assert transcript["agent_handoffs"][0]["tool_use_id"] == "call_handoff_verify"
        assert transcript["agent_handoffs"][0]["source_agent"] == "Harness Router Agent"
        assert transcript["agent_handoffs"][0]["target_agent"] == "Harness Specialist Agent"
        assert transcript["agent_handoffs"][0]["agent"] == "Harness Router Agent"
        assert transcript["agent_handoffs"][0]["raw_type"] == "function_call_output"
        assert transcript["agent_handoffs"][0]["evidence_refs"] == ["trace_item:handoff_output_item"]
        assert transcript["agent_approvals"][0]["tool_use_id"] == "call_approval_verify"
        assert transcript["agent_approvals"][0]["subtype"] == "approval_request"
        assert transcript["agent_approvals"][0]["tool_name"] == "approval_required_signal"
        assert transcript["agent_approvals"][0]["approval_arguments"]["marker"] == "AGENTS_SDK_APPROVAL_TOOL_OK"
        assert transcript["agent_approvals"][0]["agent"] == "Harness Approval Agent"
        assert transcript["agent_approvals"][0]["raw_type"] == "function_call"
        assert transcript["agent_approvals"][0]["evidence_refs"] == ["trace_item:ToolApprovalItem"]
        assert transcript["agent_approvals"][1]["subtype"] == "approval_decision"
        assert transcript["agent_approvals"][1]["decision"] == "approved"
        assert transcript["agent_approvals"][1]["approval_arguments"]["marker"] == "AGENTS_SDK_APPROVAL_TOOL_OK"
        assert transcript["agent_approvals"][1]["raw_type"] == "function_call"
        assert transcript["agent_approvals"][1]["agent"] == "Harness Approval Agent"
        assert transcript["agent_approvals"][1]["evidence_refs"] == ["approval_decision:1"]
        assert transcript["agent_questions"][0]["subtype"] == "missing_info_request"
        assert transcript["agent_questions"][0]["question_count"] == 1
        assert transcript["agent_questions"][0]["questions"][0]["field"] == "baseline_evidence"
        assert transcript["agent_questions"][0]["reason"] == "baseline_evidence_required_before_precise_similarity_report"
        assert transcript["agent_questions"][0]["evidence_status"] == "baseline_evidence_incomplete"
        assert transcript["agent_questions"][0]["source_observation"] == "write_purchase_report"
        assert transcript["agent_questions"][0]["agent"] == "Harness Question Agent"
        assert transcript["agent_questions"][0]["evidence_refs"] == ["trace_item:missing_info_request"]
        assert "not prove current provider connectivity" in transcript["agent_questions"][0]["claim_boundary"]
        assert transcript["agent_guidance"][0]["subtype"] == "continue_candidate_enrichment"
        assert transcript["agent_guidance"][0]["guidance_type"] == "backend_enrichment_required"
        assert transcript["agent_guidance"][0]["reason"] == "continue_search_for_closer_measurements"
        assert transcript["agent_guidance"][0]["candidate_key"] == "candidate_weak_fit"
        assert transcript["agent_guidance"][0]["missing_fields"] == ["size_measurements"]
        assert transcript["agent_guidance"][0]["ask_user_allowed"] is False
        assert transcript["agent_guidance"][0]["suggested_tool_count"] == 1
        assert transcript["agent_guidance"][0]["next_tool_suggestions"][0]["tool"] == "search_web_candidates"
        assert transcript["agent_guidance"][0]["close_fit_search_budget"]["remaining_attempts"] == 23
        assert transcript["agent_guidance"][0]["search_recovery_policy"] == (
            "revise_queries_and_continue_until_close_fit_or_budget_exhausted"
        )
        assert transcript["agent_guidance"][0]["agent"] == "Harness Guidance Agent"
        assert transcript["agent_guidance"][0]["evidence_refs"] == [
            "artifact:agent_trace:verify_trace.json",
            "agent_guidance:1",
        ]
        assert "not prove current provider connectivity" in transcript["agent_guidance"][0]["claim_boundary"]
        assert transcript["agent_eval_case_verifications"][0]["type"] == "agent_eval_case_verification"
        assert (
            transcript["agent_eval_case_verifications"][0]["subtype"]
            == "aggregate:matrix_summary_payload_matches"
        )
        assert transcript["agent_eval_case_verifications"][0]["eval_case_id"] == 61
        assert (
            transcript["agent_eval_case_verifications"][0]["source_task_id"]
            == "task_matrix_aggregate_verify"
        )
        assert (
            transcript["agent_eval_case_verifications"][0]["failure_label"]
            == "aggregate:matrix_summary_payload_matches"
        )
        assert transcript["agent_eval_case_verifications"][0]["expected_status"] == "completed"
        assert transcript["agent_eval_case_verifications"][0]["expected_status_matches"] is False
        assert transcript["agent_eval_case_verifications"][0]["execution_status"] == "failed"
        assert transcript["agent_eval_case_verifications"][0]["returncode"] == 1
        assert transcript["agent_eval_case_verifications"][0]["status"] == "failed"
        assert transcript["agent_eval_case_verifications"][0]["failed_checks"] == [
            "eval_case_verifier_completed",
            "eval_case_expected_status_matches",
        ]
        assert transcript["agent_eval_case_verifications"][0]["evidence_refs"] == [
            "eval_case:61",
            "task:task_matrix_aggregate_verify",
        ]
        assert "not prove current provider connectivity" in (
            transcript["agent_eval_case_verifications"][0]["claim_boundary"]
        )
        assert transcript["agent_guardrails"][0]["subtype"] == "output_guardrail"
        assert transcript["agent_guardrails"][0]["guardrail_name"] == "harness_output_guardrail"
        assert transcript["agent_guardrails"][0]["tripwire_triggered"] is False
        assert transcript["agent_guardrails"][0]["output_info"]["status"] == "checked"
        assert transcript["agent_guardrails"][0]["agent"] == "Harness Guardrail Agent"
        assert transcript["agent_guardrails"][0]["agent_output"] == "AGENTS_SDK_GUARDRAIL_FINAL_OK"
        assert transcript["agent_guardrails"][0]["evidence_refs"] == ["guardrail:output_guardrail_results:1"]
        assert transcript["agent_guardrails"][1]["guardrail_name"] == "harness_tripwire_guardrail"
        assert transcript["agent_guardrails"][1]["tripwire_triggered"] is True
        assert transcript["agent_guardrails"][1]["status"] == "failed"
        assert transcript["agent_guardrails"][1]["output_info"]["status"] == "blocked"
        assert transcript["agent_guardrails"][1]["reason"] == "intentional_live_smoke_tripwire"
        assert transcript["agent_guardrails"][1]["agent"] == "Harness Tripwire Agent"
        assert transcript["agent_guardrails"][1]["agent_output"] == "AGENTS_SDK_GUARDRAIL_TRIPWIRE_FINAL_OK"
        assert transcript["agent_guardrails"][1]["evidence_refs"] == ["guardrail:output_guardrail_results:2"]
        assert transcript["agent_run_errors"][0]["error_type"] == "OutputGuardrailTripwireTriggered"
        assert transcript["agent_run_errors"][0]["status"] == "failed"
        assert transcript["agent_run_errors"][0]["external_api_called"] is True
        assert transcript["agent_run_errors"][0]["evidence_refs"] == ["artifact:agent_trace:verify_trace.json"]
        assert transcript["agent_final_outputs"][0]["type"] == "final_output"
        assert transcript["agent_final_outputs"][0]["subtype"] == "agent_trace_final_output"
        assert transcript["agent_final_outputs"][0]["summary"] == "AGENTS_SDK_INGESTED_FINAL_OK"
        assert transcript["agent_final_outputs"][0]["output_source"] == "observation.agent_final_output"
        assert transcript["agent_final_outputs"][0]["evidence_refs"] == ["artifact:agent_trace:verify_trace.json"]
        assert "not prove current provider connectivity" in transcript["agent_final_outputs"][0]["claim_boundary"]
        assert transcript["agent_phases"][0]["phase_name"] == "after_approval"
        assert transcript["agent_phases"][0]["summary"] == "after_approval, 1 response(s), 2 item(s), 0 interruption(s)."
        assert transcript["agent_sessions"][0]["agent_session_id"] == "trace_verify_session"
        assert transcript["agent_sessions"][0]["backend"] == "SQLiteSession"
        assert transcript["agent_sessions"][0]["turn_count"] == 2
        assert transcript["agent_sessions"][0]["final_item_count"] == 4
        assert transcript["agent_sessions"][0]["evidence_refs"] == [
            "artifact:agent_trace:verify_trace.json",
            "agent_session",
        ]
        assert "2 turn(s)" in transcript["agent_sessions"][0]["summary"]
        assert transcript["agent_model_responses"][0]["response_id"] == "resp_context_usage_verify"
        assert transcript["agent_model_responses"][0]["model"] == "gpt-5-mini"
        assert transcript["agent_model_responses"][0]["model_source"] == "trace"
        assert transcript["agent_model_responses"][0]["provider"] == "openai"
        assert transcript["agent_model_responses"][0]["api_style"] == "responses"
        assert transcript["agent_model_responses"][0]["usage"]["total_tokens"] == 150
        assert transcript["agent_model_responses"][0]["evidence_refs"] == [
            "artifact:agent_trace:verify_trace.json",
            "raw_response:context_usage",
        ]
        assert "not prove current provider connectivity" in transcript["agent_model_responses"][0]["claim_boundary"]
        runtime_transcript_summary = runtime.task_client_transcript(task["task_id"], summary=True)
        assert runtime_transcript_summary["agent_model_response_provider_counts"] == {"openai": 1}
        assert runtime_transcript_summary["agent_model_response_api_style_counts"] == {"responses": 1}
        assert runtime_transcript_summary["agent_eval_case_verification_count"] == 1
        assert runtime_transcript_summary["agent_eval_case_verification_failed_count"] == 1
        assert runtime_transcript_summary["agent_eval_case_failure_label_counts"] == {
            "aggregate:matrix_summary_payload_matches": 1
        }
        assert transcript["agent_stream_events"][0]["stream_index"] == 2
        assert transcript["agent_stream_events"][0]["stream_event_name"] == "tool_called"
        assert transcript["agent_stream_events"][0]["tool_use_id"] == "call_stream_verify"
        assert transcript["agent_stream_events"][0]["tool_name"] == "record_signal"
        assert transcript["agent_stream_events"][0]["agent"] == "Harness Stream Agent"
        assert transcript["agent_stream_events"][0]["evidence_refs"] == [
            "artifact:agent_trace:verify_trace.json",
            "stream_event:2",
        ]
        assert transcript["agent_stream_events"][1]["stream_index"] == 3
        assert transcript["agent_stream_events"][1]["stream_event_name"] == "handoff_output"
        assert transcript["agent_stream_events"][1]["tool_use_id"] == "call_stream_handoff_verify"
        assert transcript["agent_stream_events"][1]["tool_name"] == "transfer_to_harness_specialist"
        assert transcript["agent_stream_events"][1]["agent"] == "Harness Specialist Agent"
        assert transcript["agent_stream_events"][1]["item_class"] == "HandoffOutputItem"
        assert transcript["agent_stream_events"][1]["evidence_refs"] == [
            "artifact:agent_trace:verify_trace.json",
            "stream_event:3",
        ]
        assert transcript["permission_decisions"][0]["behavior"] == "allow"
        assert transcript["permission_decisions"][0]["decision_reason"]["mode"] == "default"
        assert transcript["source_endpoints"]["client_transcript"].endswith(
            f"/tasks/{task['task_id']}/client-transcript"
        )
        sdk_events = runtime.task_sdk_events(task["task_id"])
        runtime_sdk_summary = runtime.task_sdk_events(task["task_id"], summary=True)
        assert runtime_sdk_summary["agent_model_response_provider_counts"] == {"openai": 1}
        assert runtime_sdk_summary["agent_model_response_api_style_counts"] == {"responses": 1}
        assert runtime_sdk_summary["agent_eval_case_verification_count"] == 1
        assert runtime_sdk_summary["agent_eval_case_verification_failed_count"] == 1
        assert runtime_sdk_summary["agent_eval_case_failure_label_counts"] == {
            "aggregate:matrix_summary_payload_matches": 1
        }
        sdk_subtypes = [item.get("subtype") for item in sdk_events["events"]]
        assert sdk_events["raw_event_count"] > 0
        assert sdk_events["projected_event_count"] == len(sdk_events["events"])
        assert sdk_events["next_after"] >= sdk_events["terminal_event_id"]
        assert sdk_events["cursor_policy"]["cursor_basis"] == "raw_harness_event_id"
        assert sdk_events["cursor_policy"]["cursor_semantics"] == "exclusive"
        assert sdk_events["terminal_event_included"] is True
        assert sdk_events["final_output_included"] is True
        assert "task_queued" in sdk_subtypes
        assert "task_started" in sdk_subtypes
        assert "task_progress" in sdk_subtypes
        assert "task_notification" in sdk_subtypes
        assert "session_state_changed" in sdk_subtypes
        assert any(
            item.get("type") == "final_output"
            and item.get("summary") == "verified"
            and item.get("output_source") == "result.last_message"
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "final_output"
            and item.get("subtype") == "agent_trace_final_output"
            and item.get("summary") == "AGENTS_SDK_INGESTED_FINAL_OK"
            and item.get("output_source") == "observation.agent_final_output"
            and item.get("evidence_refs") == ["artifact:agent_trace:verify_trace.json"]
            for item in sdk_events["events"]
        )
        assert any(item.get("state") == "queued" for item in sdk_events["events"])
        assert any(item.get("type") == "tool_progress" for item in sdk_events["events"])
        assert any(
            item.get("type") == "tool_progress"
            and item.get("tool_use_id") == running_tool_call["call_id"]
            and item.get("status") == "completed"
            and item.get("completed_at")
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "agent_handoff"
            and item.get("tool_use_id") == "call_handoff_verify"
            and item.get("source_agent") == "Harness Router Agent"
            and item.get("target_agent") == "Harness Specialist Agent"
            and item.get("agent") == "Harness Router Agent"
            and item.get("raw_type") == "function_call_output"
            and item.get("evidence_refs") == ["trace_item:handoff_output_item"]
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "agent_approval"
            and item.get("subtype") == "approval_request"
            and item.get("tool_use_id") == "call_approval_verify"
            and item.get("tool_name") == "approval_required_signal"
            and item.get("approval_arguments", {}).get("marker") == "AGENTS_SDK_APPROVAL_TOOL_OK"
            and item.get("agent") == "Harness Approval Agent"
            and item.get("raw_type") == "function_call"
            and item.get("evidence_refs") == ["trace_item:ToolApprovalItem"]
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "agent_approval"
            and item.get("subtype") == "approval_decision"
            and item.get("decision") == "approved"
            and item.get("approval_arguments", {}).get("marker") == "AGENTS_SDK_APPROVAL_TOOL_OK"
            and item.get("raw_type") == "function_call"
            and item.get("agent") == "Harness Approval Agent"
            and item.get("evidence_refs") == ["approval_decision:1"]
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "agent_guardrail"
            and item.get("subtype") == "output_guardrail"
            and item.get("guardrail_name") == "harness_output_guardrail"
            and item.get("tripwire_triggered") is False
            and item.get("output_info", {}).get("status") == "checked"
            and item.get("agent") == "Harness Guardrail Agent"
            and item.get("agent_output") == "AGENTS_SDK_GUARDRAIL_FINAL_OK"
            and item.get("evidence_refs") == ["guardrail:output_guardrail_results:1"]
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "agent_guardrail"
            and item.get("guardrail_name") == "harness_tripwire_guardrail"
            and item.get("tripwire_triggered") is True
            and item.get("status") == "failed"
            and item.get("output_info", {}).get("status") == "blocked"
            and item.get("reason") == "intentional_live_smoke_tripwire"
            and item.get("agent") == "Harness Tripwire Agent"
            and item.get("agent_output") == "AGENTS_SDK_GUARDRAIL_TRIPWIRE_FINAL_OK"
            and item.get("evidence_refs") == ["guardrail:output_guardrail_results:2"]
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "agent_run_error"
            and item.get("error_type") == "OutputGuardrailTripwireTriggered"
            and item.get("status") == "failed"
            and item.get("external_api_called") is True
            and item.get("evidence_refs") == ["artifact:agent_trace:verify_trace.json"]
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "agent_phase"
            and item.get("phase_name") == "after_approval"
            and item.get("summary") == "after_approval, 1 response(s), 2 item(s), 0 interruption(s)."
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "agent_session"
            and item.get("agent_session_id") == "trace_verify_session"
            and item.get("backend") == "SQLiteSession"
            and item.get("turn_count") == 2
            and item.get("final_item_count") == 4
            and item.get("evidence_refs") == [
                "artifact:agent_trace:verify_trace.json",
                "agent_session",
            ]
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "agent_model_response"
            and item.get("response_id") == "resp_context_usage_verify"
            and item.get("model") == "gpt-5-mini"
            and item.get("model_source") == "trace"
            and item.get("provider") == "openai"
            and item.get("api_style") == "responses"
            and item.get("usage", {}).get("total_tokens") == 150
            and item.get("evidence_refs") == [
                "artifact:agent_trace:verify_trace.json",
                "raw_response:context_usage",
            ]
            and "not prove current provider connectivity" in item.get("claim_boundary", "")
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "agent_stream_event"
            and item.get("stream_event_name") == "tool_called"
            and item.get("tool_use_id") == "call_stream_verify"
            and item.get("tool_name") == "record_signal"
            and item.get("agent") == "Harness Stream Agent"
            and item.get("evidence_refs") == [
                "artifact:agent_trace:verify_trace.json",
                "stream_event:2",
            ]
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "agent_stream_event"
            and item.get("stream_event_name") == "handoff_output"
            and item.get("tool_use_id") == "call_stream_handoff_verify"
            and item.get("tool_name") == "transfer_to_harness_specialist"
            and item.get("agent") == "Harness Specialist Agent"
            and item.get("item_class") == "HandoffOutputItem"
            and item.get("evidence_refs") == [
                "artifact:agent_trace:verify_trace.json",
                "stream_event:3",
            ]
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "agent_question"
            and item.get("subtype") == "missing_info_request"
            and item.get("question_count") == 1
            and (item.get("questions") or [{}])[0].get("field") == "baseline_evidence"
            and item.get("reason") == "baseline_evidence_required_before_precise_similarity_report"
            and item.get("evidence_status") == "baseline_evidence_incomplete"
            and item.get("source_observation") == "write_purchase_report"
            and item.get("agent") == "Harness Question Agent"
            and item.get("evidence_refs") == ["trace_item:missing_info_request"]
            and "not prove current provider connectivity" in item.get("claim_boundary", "")
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "agent_guidance"
            and item.get("subtype") == "continue_candidate_enrichment"
            and item.get("guidance_type") == "backend_enrichment_required"
            and item.get("reason") == "continue_search_for_closer_measurements"
            and item.get("candidate_key") == "candidate_weak_fit"
            and item.get("missing_fields") == ["size_measurements"]
            and item.get("ask_user_allowed") is False
            and item.get("suggested_tool_count") == 1
            and (item.get("next_tool_suggestions") or [{}])[0].get("tool") == "search_web_candidates"
            and item.get("close_fit_search_budget", {}).get("remaining_attempts") == 23
            and item.get("search_recovery_policy") == (
                "revise_queries_and_continue_until_close_fit_or_budget_exhausted"
            )
            and item.get("agent") == "Harness Guidance Agent"
            and item.get("evidence_refs") == [
                "artifact:agent_trace:verify_trace.json",
                "agent_guidance:1",
            ]
            and "not prove current provider connectivity" in item.get("claim_boundary", "")
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "agent_eval_case_verification"
            and item.get("subtype") == "aggregate:matrix_summary_payload_matches"
            and item.get("eval_case_id") == 61
            and item.get("source_task_id") == "task_matrix_aggregate_verify"
            and item.get("failure_label") == "aggregate:matrix_summary_payload_matches"
            and item.get("expected_status") == "completed"
            and item.get("expected_status_matches") is False
            and item.get("execution_status") == "failed"
            and item.get("returncode") == 1
            and item.get("status") == "failed"
            and item.get("failed_checks") == [
                "eval_case_verifier_completed",
                "eval_case_expected_status_matches",
            ]
            and item.get("evidence_refs") == ["eval_case:61", "task:task_matrix_aggregate_verify"]
            and "not prove current provider connectivity" in item.get("claim_boundary", "")
            for item in sdk_events["events"]
        )
        assert any(
            item.get("type") == "permission_decision"
            and item.get("tool_use_id") == tool_call["call_id"]
            and item.get("tool_name") == "local_search"
            and item.get("behavior") == "allow"
            for item in sdk_events["events"]
        )
        assert any(item.get("state") == "running" for item in sdk_events["events"])
        assert any(item.get("state") == "idle" for item in sdk_events["events"])
        progress_event = next(item for item in sdk_events["events"] if item.get("subtype") == "task_progress")
        assert progress_event["usage"]["tool_uses"] == 2
        assert progress_event["progress_items"][0]["item_id"] == "inspect"
        terminal_delta = runtime.task_sdk_events(task["task_id"], after=sdk_events["terminal_event_id"] - 1)
        assert terminal_delta["raw_event_count"] >= 1
        assert terminal_delta["terminal_event_included"] is True
        assert terminal_delta["final_output_included"] is True
        assert any(item.get("type") == "final_output" for item in terminal_delta["events"])
        caught_up_projection = runtime.task_sdk_events(task["task_id"], after=sdk_events["next_after"])
        assert caught_up_projection["raw_event_count"] == 0
        assert caught_up_projection["projected_event_count"] == 0
        assert caught_up_projection["events"] == []
        assert caught_up_projection["terminal_event_included"] is False
        assert caught_up_projection["final_output_included"] is False
        after_projection = runtime.task_sdk_events(task["task_id"], after=sdk_events["events"][-1]["source_event_id"])
        assert after_projection["events"] == []
        child_task = runtime.prepare_task(
            {
                "prompt": "child verifier task",
                "cwd": str(root),
                "sandbox": "read-only",
                "parent_task_id": task["task_id"],
                "client_id": task["client_id"],
                "join_policy": "all",
                "metadata": {},
                "verification": {"commands": [], "timeout_seconds": 30},
            }
        )
        child_completed = runtime.run_task(child_task["task_id"])
        assert child_completed["parent_task_id"] == task["task_id"]
        subagents = runtime.task_subagents(task["task_id"])
        assert subagents["counts"]["total"] == 1
        assert subagents["counts"]["completed"] == 1
        assert subagents["subagents"][0]["task_id"] == child_task["task_id"]
        assert subagents["subagents"][0]["last_message"] == "verified"
        transcript_with_child = runtime.task_client_transcript(task["task_id"])
        assert transcript_with_child["status_line"]["subagents"]["completed"] == 1
        assert transcript_with_child["source_endpoints"]["subagents"].endswith(
            f"/tasks/{task['task_id']}/subagents"
        )
        event_types = {item["event_type"] for item in runtime.store.events(task["task_id"])}
        assert {
            "tool_call.recorded",
            "tool_call.status_updated",
            "observation.recorded",
            "permission_decision.recorded",
            "task.progress_updated",
            "artifact.recorded",
        } <= event_types
        verified_task = runtime.prepare_task(
            {
                "prompt": "run verifier evidence",
                "cwd": str(root),
                "sandbox": "read-only",
                "metadata": {},
                "verification": {"commands": ["python -c \"print('harness verifier evidence')\""], "timeout_seconds": 30},
            }
        )
        verified_completed = runtime.run_task(verified_task["task_id"])
        assert verified_completed["status"] == "completed"
        assert verified_completed["verification_results"][0]["status"] == "completed"
        assert verified_completed["verification_results"][0]["executed_command"]
        verified_evidence = runtime.task_evidence(verified_task["task_id"])
        assert verified_evidence["verification_results"][0]["command"].startswith("python -c")
        assert verified_evidence["verification_results"][0]["executed_command"]
        verified_transcript = runtime.task_client_transcript(verified_task["task_id"])
        assert verified_transcript["status_line"]["verification_status"] == "completed"
        assert verified_transcript["harness_verification_results"][0]["type"] == "harness_verification_result"
        assert verified_transcript["harness_verification_results"][0]["subtype"] == "completed"
        assert verified_transcript["harness_verification_results"][0]["command"].startswith("python -c")
        assert verified_transcript["harness_verification_results"][0]["executed_command"]
        assert verified_transcript["harness_verification_results"][0]["status"] == "completed"
        assert verified_transcript["harness_verification_results"][0]["returncode"] == 0
        assert "harness verifier evidence" in verified_transcript["harness_verification_results"][0]["stdout_tail"]
        assert "not prove current provider connectivity" in (
            verified_transcript["harness_verification_results"][0]["claim_boundary"]
        )
        verified_transcript_summary = runtime.task_client_transcript(verified_task["task_id"], summary=True)
        assert verified_transcript_summary["harness_verification_result_count"] == 1
        assert verified_transcript_summary["harness_verification_failed_count"] == 0
        assert verified_transcript_summary["harness_verification_status_counts"] == {"completed": 1}
        assert verified_transcript_summary["item_type_counts"]["harness_verification_result"] == 1
        verified_sdk_events = runtime.task_sdk_events(verified_task["task_id"])
        assert any(
            item.get("type") == "harness_verification_result"
            and item.get("subtype") == "completed"
            and item.get("command", "").startswith("python -c")
            and item.get("executed_command")
            and item.get("status") == "completed"
            and item.get("returncode") == 0
            and "harness verifier evidence" in item.get("stdout_tail", "")
            and "not prove current provider connectivity" in item.get("claim_boundary", "")
            for item in verified_sdk_events["events"]
        )
        verified_sdk_summary = runtime.task_sdk_events(verified_task["task_id"], summary=True)
        assert verified_sdk_summary["harness_verification_result_count"] == 1
        assert verified_sdk_summary["harness_verification_failed_count"] == 0
        assert verified_sdk_summary["harness_verification_status_counts"] == {"completed": 1}
        eval_case = runtime.record_eval_case(
            task["client_id"],
            {
                "name": "tool loop missing local provider",
                "source_task_id": task["task_id"],
                "failure_label": "configuration_required",
                "input": {"provider": "local_missing"},
                "expected": {"status": "configuration_required"},
                "verifier_command": "python -B verify.py",
                "evidence_refs": [f"task:{task['task_id']}", f"call:{tool_call['call_id']}"],
                "metadata": {"kind": "harness-regression"},
            },
        )
        assert eval_case["eval_case_id"] >= 1
        assert runtime.list_eval_cases(task["client_id"])[0]["name"] == "tool loop missing local provider"
        assert "eval_case.recorded" in {item["event_type"] for item in runtime.store.events(task["task_id"])}
        loop_task = runtime.prepare_task(
            {
                "prompt": "improve the agent harness from trace evidence",
                "cwd": str(root),
                "sandbox": "read-only",
                "metadata": {
                    "agentic_loop_template": "agents_sdk_trace_eval_codex",
                    "agentic_loop": {
                        "trace_sources": ["harness_events", "agent_run_store"],
                        "eval_targets": ["tool_loop_regression"],
                        "verification_commands": ["python -B verify_production_agent_runtime.py"],
                    },
                },
                "verification": {"commands": [], "timeout_seconds": 30},
            }
        )
        loop_completed = runtime.run_task(loop_task["task_id"])
        assert loop_completed["status"] == "completed"
        loop_prompt = runtime.executor.calls[-1]["prompt"]
        assert "Agentic loop harness mode" in loop_prompt
        assert "agents_sdk_trace_eval_codex" in loop_prompt
        assert "trace/store/API evidence" in loop_prompt
        assert "mock, cached, fixture, and deterministic fallback behavior as harness evidence" in loop_prompt
        loop = runtime.create_loop(
            {
                "name": "bounded verification",
                "task": task["request"],
                "interval_seconds": 60,
                "max_iterations": 1,
            }
        )
        assert loop["evidence_contract"]["required_iteration_evidence"] == [
            "task_events",
            "task_evidence",
            "verification_results",
        ]
        assert loop["evidence_contract"]["promotion_rule"] == "repeatable_trace_or_runtime_gap_should_be_recorded_as_eval_case"
        tick = runtime.tick_loop(loop["loop_id"], force=True)
        assert tick["loop"]["status"] == "completed"
        assert tick["loop"]["iteration_records"][0]["task_id"] == tick["task"]["task_id"]
        assert f"evidence:{tick['task']['task_id']}" in tick["loop"]["iteration_records"][0]["evidence_refs"]
        loop_evidence = runtime.loop_evidence(loop["loop_id"])
        assert loop_evidence["loop"]["loop_id"] == loop["loop_id"]
        assert loop_evidence["iterations"][0]["task_id"] == tick["task"]["task_id"]
        assert loop_evidence["iterations"][0]["counts"]["events"] >= 1
        assert loop_evidence["iterations"][0]["verification_status"] == "not_run"
        heartbeat_task = runtime.prepare_task(
            {
                "prompt": "heartbeat lease",
                "cwd": str(root),
                "client_id": "heartbeat-client",
                "sandbox": "read-only",
                "metadata": {},
                "verification": {"commands": [], "timeout_seconds": 30},
            }
        )
        claimed_for_heartbeat = runtime.store.claim_next_task(
            "heartbeat-client",
            client_id="heartbeat-client",
            lease_seconds=1,
        )
        assert claimed_for_heartbeat["task_id"] == heartbeat_task["task_id"]
        original_expiry = claimed_for_heartbeat["lease"]["expires_at"]
        heartbeat = runtime.worker_heartbeat(
            heartbeat_task["task_id"],
            owner="heartbeat-client",
            lease_epoch=claimed_for_heartbeat["lease"]["epoch"],
            lease_seconds=300,
            client_id="heartbeat-client",
        )
        assert heartbeat["lease"]["expires_at"] > original_expiry
        try:
            runtime.worker_heartbeat(
                heartbeat_task["task_id"],
                owner="heartbeat-client",
                lease_epoch=claimed_for_heartbeat["lease"]["epoch"] + 1,
                client_id="heartbeat-client",
            )
            raise AssertionError("stale heartbeat epoch should fail")
        except Exception as exc:
            assert "lease epoch mismatch" in str(exc)
        assert "task.heartbeat" in {item["event_type"] for item in runtime.store.events(heartbeat_task["task_id"])}
        running_pause_task = runtime.prepare_task(
            {
                "prompt": "pause running lease",
                "cwd": str(root),
                "client_id": "running-pause-client",
                "sandbox": "read-only",
                "metadata": {},
                "verification": {"commands": [], "timeout_seconds": 30},
            }
        )
        claimed_for_pause = runtime.store.claim_next_task(
            "running-pause-worker",
            client_id="running-pause-client",
            lease_seconds=300,
        )
        assert claimed_for_pause["task_id"] == running_pause_task["task_id"]
        paused_running = runtime.command_task(
            running_pause_task["task_id"],
            "pause",
            expected_state_version=claimed_for_pause["state_version"],
        )
        assert paused_running["status"] == "paused"
        assert paused_running["lease"] is None
        assert running_pause_task["task_id"] in runtime.executor.cancelled
        running_pause_events = runtime.store.events(running_pause_task["task_id"])
        assert "task.paused" in {item["event_type"] for item in running_pause_events}
        paused_event = next(item for item in running_pause_events if item["event_type"] == "task.paused")
        assert paused_event["payload"]["cancelled_process"] is True

        blocking_executor = BlockingExecutor()
        blocking_runtime = DevelopmentHarness(root, HarnessStore(Path(temp) / "blocking-harness.sqlite3"), blocking_executor)
        blocking_task = blocking_runtime.prepare_task(
            {
                "prompt": "cancel while worker is still returning",
                "cwd": str(root),
                "client_id": "running-cancel-client",
                "sandbox": "read-only",
                "metadata": {},
                "verification": {"commands": [], "timeout_seconds": 30},
            }
        )
        blocking_result: dict[str, Any] = {}

        def run_blocking_task() -> None:
            blocking_result["task"] = blocking_runtime.run_task(blocking_task["task_id"])

        blocking_thread = Thread(target=run_blocking_task)
        blocking_thread.start()
        assert blocking_executor.started.wait(timeout=5)
        running_blocking = blocking_runtime.store.get_task(blocking_task["task_id"])
        assert running_blocking is not None
        cancelled_blocking = blocking_runtime.command_task(
            blocking_task["task_id"],
            "cancel",
            expected_state_version=running_blocking["state_version"],
        )
        assert cancelled_blocking["status"] == "cancelled"
        blocking_thread.join(timeout=5)
        assert not blocking_thread.is_alive()
        final_blocking = blocking_runtime.store.get_task(blocking_task["task_id"])
        assert final_blocking is not None
        assert final_blocking["status"] == "cancelled"
        assert blocking_result["task"]["status"] == "cancelled"
        blocking_events = blocking_runtime.store.events(blocking_task["task_id"])
        blocking_event_types = [item["event_type"] for item in blocking_events]
        assert "task.cancelled" in blocking_event_types
        assert "task.worker_result_ignored" in blocking_event_types
        assert "task.completed" not in blocking_event_types
        assert blocking_task["task_id"] in blocking_executor.cancelled

        resume_lease_task = runtime.prepare_task(
            {
                "prompt": "resume stale lease",
                "cwd": str(root),
                "client_id": "resume-lease-client",
                "sandbox": "read-only",
                "metadata": {},
                "verification": {"commands": [], "timeout_seconds": 30},
            }
        )
        claimed_for_resume = runtime.store.claim_next_task(
            "resume-lease-worker",
            client_id="resume-lease-client",
            lease_seconds=300,
        )
        assert claimed_for_resume["task_id"] == resume_lease_task["task_id"]
        legacy_paused = dict(claimed_for_resume)
        legacy_paused["status"] = "paused"
        legacy_paused = runtime.store.save_task(legacy_paused)
        resumed_legacy = runtime.command_task(
            resume_lease_task["task_id"],
            "resume",
            expected_state_version=legacy_paused["state_version"],
        )
        assert resumed_legacy["status"] == "queued"
        assert resumed_legacy["lease"] is None
        resumed_claim = runtime.store.claim_next_task(
            "resume-lease-worker-after-resume",
            client_id="resume-lease-client",
            lease_seconds=300,
        )
        assert resumed_claim["task_id"] == resume_lease_task["task_id"]
        assert resumed_claim["lease"]["owner"] == "resume-lease-worker-after-resume"
        queue_task = runtime.prepare_task(
            {
                "prompt": "initial queue task",
                "cwd": str(root),
                "sandbox": "read-only",
                "metadata": {},
                "verification": {"commands": [], "timeout_seconds": 30},
            }
        )
        queue_completed = runtime.run_task(queue_task["task_id"])
        assert queue_completed["thread_id"] == "thr_test"
        later_message = runtime.enqueue_task_message(
            queue_task["task_id"],
            {
                "prompt": "run this later",
                "priority": "later",
                "message_uuid": "msg_later",
            },
            expected_state_version=queue_completed["state_version"],
        )
        assert later_message["status"] == "queued"
        next_message = runtime.enqueue_task_message(
            queue_task["task_id"],
            {
                "prompt": "run this next",
                "priority": "next",
                "message_uuid": "msg_next",
            },
            expected_state_version=later_message["state_version"],
        )
        cancel_later = runtime.cancel_queued_message(
            queue_task["task_id"],
            "msg_later",
            expected_state_version=next_message["state_version"],
        )
        assert cancel_later["cancelled"] is True
        drained = runtime.run_task(queue_task["task_id"])
        assert drained["status"] == "completed"
        assert runtime.executor.calls[-1]["thread_id"] == "thr_test"
        assert runtime.executor.calls[-1]["prompt"] == "run this next"
        queued_state = runtime.store.get_task(queue_task["task_id"])
        queued_statuses = {item["message_uuid"]: item["status"] for item in queued_state["queued_messages"]}
        assert queued_statuses == {"msg_later": "cancelled", "msg_next": "completed"}
        queue_transcript = runtime.task_client_transcript(queue_task["task_id"])
        assert queue_transcript["status_line"]["queued_messages"]["cancelled"] == 1
        assert queue_transcript["status_line"]["queued_messages"]["completed"] == 1
        assert "task.message_queued" in {item["event_type"] for item in runtime.store.events(queue_task["task_id"])}
        assert "task.message_cancelled" in {item["event_type"] for item in runtime.store.events(queue_task["task_id"])}
        assert "task.message_dequeued" in {item["event_type"] for item in runtime.store.events(queue_task["task_id"])}
        queue_sdk_events = runtime.task_sdk_events(queue_task["task_id"])
        assert any(
            item.get("type") == "task_control"
            and item.get("subtype") == "queued_message_created"
            and item.get("message_uuid") == "msg_next"
            for item in queue_sdk_events["events"]
        )
        assert any(
            item.get("type") == "task_control"
            and item.get("subtype") == "queued_message_cancelled"
            and item.get("message_uuid") == "msg_later"
            for item in queue_sdk_events["events"]
        )
        assert any(
            item.get("type") == "task_control"
            and item.get("subtype") == "queued_message_dequeued"
            and item.get("message_uuid") == "msg_next"
            for item in queue_sdk_events["events"]
        )
        probe_result = runtime.probe_llm_provider(
            {
                "provider": "local_missing",
                "api_style": "chat_completions",
                "base_url": None,
            },
            client_id="probe-client",
        )
        assert probe_result["status"] == "configuration_required"
        assert probe_result["provider_name"] == "local_missing"
        assert probe_result["api_style"] == "chat_completions"
        assert probe_result["provider_request_attempted"] is False
        assert probe_result["provider_response_received"] is False
        assert probe_result["external_api_called"] is False
        assert isinstance(probe_result["diagnostic_id"], int)
        diagnostics = runtime.store.list_provider_diagnostics("probe-client")
        assert diagnostics[0]["diagnostic_id"] == probe_result["diagnostic_id"]
        assert diagnostics[0]["status"] == "configuration_required"
        assert diagnostics[0]["result"]["error"]["code"] == "LLM_BASE_URL_REQUIRED"
        original_requests_get = runtime_module.requests.get

        def raise_connection_error(*args: Any, **kwargs: Any) -> Any:
            raise runtime_module.requests.ConnectionError("synthetic connection refused")

        runtime_module.requests.get = raise_connection_error
        try:
            not_connected_probe = runtime.probe_llm_provider(
                {
                    "provider": "local_closed_port",
                    "api_style": "chat_completions",
                    "base_url": "http://127.0.0.1:9/v1",
                    "model": "closed-port",
                },
                client_id="probe-client",
            )
        finally:
            runtime_module.requests.get = original_requests_get
        assert not_connected_probe["status"] == "not_connected"
        assert not_connected_probe["error"]["code"] == "LLM_PROVIDER_NOT_CONNECTED"
        assert not_connected_probe["provider_name"] == "local_closed_port"
        assert not_connected_probe["api_style"] == "chat_completions"
        assert not_connected_probe["model"] == "closed-port"
        assert not_connected_probe["provider_request_attempted"] is True
        assert not_connected_probe["provider_response_received"] is False
        assert not_connected_probe["external_api_called"] is False
        diagnostics = runtime.store.list_provider_diagnostics("probe-client")
        assert diagnostics[0]["diagnostic_id"] == not_connected_probe["diagnostic_id"]
        assert diagnostics[1]["diagnostic_id"] == probe_result["diagnostic_id"]
        assert diagnostics[0]["status"] == "not_connected"
        assert diagnostics[0]["result"]["error"]["code"] == "LLM_PROVIDER_NOT_CONNECTED"

        class FakeProbeResponse:
            status_code = 200
            text = "{}"

            @staticmethod
            def json() -> dict[str, Any]:
                return {"data": [{"id": "gpt-probe-test"}]}

        def fake_successful_get(*args: Any, **kwargs: Any) -> Any:
            return FakeProbeResponse()

        runtime_module.requests.get = fake_successful_get
        try:
            completed_probe = runtime.probe_llm_provider(
                {
                    "provider": "openai",
                    "api_style": "responses",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-probe-test",
                    "api_key_env": "OPENAI_API_KEY",
                },
                client_id="probe-client",
            )
        finally:
            runtime_module.requests.get = original_requests_get
        assert completed_probe["status"] == "completed"
        assert completed_probe["provider_name"] == "openai"
        assert completed_probe["api_style"] == "responses"
        assert completed_probe["model"] == "gpt-probe-test"
        assert completed_probe["api_key_env"] == "OPENAI_API_KEY"
        assert completed_probe["endpoint"] == "https://api.openai.com/v1/models"
        assert completed_probe["models_preview"] == ["gpt-probe-test"]
        assert completed_probe["provider_request_attempted"] is True
        assert completed_probe["provider_response_received"] is True
        assert completed_probe["external_api_called"] is True
        worker = runtime.worker_tick("verifier_worker")
        assert worker["status"] == "claimed"
        latest_completed = runtime.store.get_task(completed["task_id"])
        commanded = runtime.command_task(
            completed["task_id"],
            "resume",
            expected_state_version=latest_completed["state_version"],
        )
        assert commanded["status"] == "queued"

    with TemporaryDirectory() as temp:
        api_runtime = DevelopmentHarness(root, HarnessStore(Path(temp) / "api-harness.sqlite3"), FakeExecutor())
        original_harness = harness_routes.harness
        harness_routes.harness = lambda: api_runtime
        try:
            client = TestClient(app)
            health = client.get("/api/harness/v1/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"
            ready = client.get("/api/harness/v1/ready")
            assert ready.status_code == 200
            assert ready.json()["status"] == "ready"
            assert ready.json()["capabilities_status"] == "ready"
            openapi = client.get("/openapi.json")
            assert openapi.status_code == 200
            openapi_paths = set(openapi.json().get("paths", {}))
            expected_harness_paths = {
                "/api/harness/v1/health",
                "/api/harness/v1/ready",
                "/api/harness/v1/capabilities",
                "/api/harness/v1/sdk/initialize",
                "/api/harness/v1/sdk/mcp-status",
                "/api/harness/v1/sdk/context-usage",
                "/api/harness/v1/sessions",
                "/api/harness/v1/tasks",
                "/api/harness/v1/tasks/{task_id}",
                "/api/harness/v1/tasks/{task_id}/session-info",
                "/api/harness/v1/tasks/{task_id}/annotations",
                "/api/harness/v1/tasks/{task_id}/fork",
                "/api/harness/v1/tasks/{task_id}/events",
                "/api/harness/v1/tasks/{task_id}/evidence",
                "/api/harness/v1/tasks/{task_id}/client-transcript",
                "/api/harness/v1/tasks/{task_id}/sdk-events",
                "/api/harness/v1/tasks/{task_id}/reply",
                "/api/harness/v1/tasks/{task_id}/queued-messages",
                "/api/harness/v1/tasks/{task_id}/queued-messages/cancel",
                "/api/harness/v1/tasks/{task_id}/commands",
                "/api/harness/v1/tasks/{task_id}/subagents",
                "/api/harness/v1/tasks/{task_id}/tool-calls",
                "/api/harness/v1/tasks/{task_id}/observations",
                "/api/harness/v1/tasks/{task_id}/permission-decisions",
                "/api/harness/v1/tasks/{task_id}/progress",
                "/api/harness/v1/tasks/{task_id}/artifacts",
                "/api/harness/v1/workers/tick",
                "/api/harness/v1/workers/heartbeat",
                "/api/harness/v1/loops",
                "/api/harness/v1/loops/{loop_id}/evidence",
                "/api/harness/v1/loops/{loop_id}/tick",
                "/api/harness/v1/llm/providers",
                "/api/harness/v1/llm/probe",
                "/api/harness/v1/llm/diagnostics",
                "/api/harness/v1/eval-cases",
            }
            assert expected_harness_paths <= openapi_paths
            assert "/api/dev-harness/capabilities" in openapi_paths
            response = client.get("/api/harness/v1/capabilities")
            assert response.status_code == 200
            assert response.json()["runtime"] == "codex_cli_jsonl"
            assert {item["name"] for item in response.json()["skills"]} >= {
                "harness-task",
                "harness-loop",
                "agentic-harness-loop",
                "local-llm-provider-diagnostics",
            }
            assert any(item["event"] == "Stop" for item in response.json()["hooks"])
            assert response.json()["loop_engineering_contract"]["isolation"] == "stateful_operations_require_x_harness_client_id"
            assert response.json()["loop_engineering_contract"]["health_ready"] == "health_is_public_ready_reports_persistence_and_capability_status"
            assert response.json()["loop_engineering_contract"]["idempotency"] == "client_scoped_idempotency_key_body_or_header"
            assert (
                response.json()["loop_engineering_contract"]["sdk_initialize"]
                == "client_friendly_initialize_mcp_status_and_context_usage_snapshots"
            )
            assert (
                response.json()["loop_engineering_contract"]["tool_progress"]
                == "sdk_tool_progress_projection_from_recorded_tool_calls"
            )
            assert (
                response.json()["loop_engineering_contract"]["session_metadata"]
                == "sdk_like_session_info_title_tag_summary_and_task_fork"
            )
            assert (
                response.json()["loop_engineering_contract"]["workspace_isolation"]
                == "workspace_mode_client_isolated_creates_separate_cwd_and_agents_md"
            )
            assert response.json()["loop_engineering_contract"]["workers"] == "lease_owner_epoch_expires_at_with_heartbeat"
            assert (
                response.json()["loop_engineering_contract"]["queued_messages"]
                == "durable_now_next_later_input_queue_with_cancel"
            )
            assert (
                response.json()["loop_engineering_contract"]["permission_decisions"]
                == "structured_allow_ask_deny_passthrough_records"
            )
            assert response.json()["loop_engineering_contract"]["provider_diagnostics"] == "client_scoped_models_probe_records"
            assert response.json()["loop_engineering_contract"]["tool_evidence"] == "typed_tool_calls_observations_and_artifacts"
            assert response.json()["loop_engineering_contract"]["eval_cases"] == "client_scoped_trace_gap_regression_cases"
            assert response.json()["loop_engineering_contract"]["task_evidence"] == "tool_observation_artifact_verification_and_hook_replay"
            assert (
                response.json()["loop_engineering_contract"]["client_transcript"]
                == "sdk_like_status_messages_tool_handoff_approval_question_guardrail_summary_and_source_endpoints"
            )
            assert (
                response.json()["loop_engineering_contract"]["subagent_handoff"]
                == "parent_task_child_summaries_and_join_status"
            )
            assert response.json()["loop_engineering_contract"]["loop_evidence_contract"] == "trace_eval_verification_termination_contract_per_loop"
            assert response.json()["loop_engineering_contract"]["loop_evidence"] == "client_scoped_loop_iteration_task_evidence_summary"
            assert response.json()["read_model_contract"]["source"] == "developer_harness_read_model_contract"
            assert response.json()["read_model_contract"]["cursor"]["semantics"] == "exclusive"
            assert "agent_eval_case_verification" in response.json()["read_model_contract"]["sdk_event_types"]
            assert "harness_verification_result" in response.json()["read_model_contract"]["sdk_event_types"]
            assert "agent_eval_case_verifications" in response.json()["read_model_contract"]["client_transcript_sections"]
            assert "harness_verification_results" in response.json()["read_model_contract"]["client_transcript_sections"]
            assert "agent_eval_case_verification_count" in response.json()["read_model_contract"]["summary_fields"]
            assert "harness_verification_result_count" in response.json()["read_model_contract"]["summary_fields"]
            assert "trace_capture" in response.json()["agentic_ai_harness_contract"]["agent_sdk_primitives"]
            assert "promote_repeatable_gaps_to_eval_cases" in response.json()["agentic_ai_harness_contract"]["loop_iteration_contract"]
            assert any(
                item["id"] == "agents_sdk_trace_eval_codex"
                for item in response.json()["agentic_loop_templates"]
            )
            headers_a = {"X-Harness-Client-Id": "client-a"}
            headers_b = {"X-Harness-Client-Id": "client-b"}
            sdk_init_response = client.get("/api/harness/v1/sdk/initialize", headers=headers_a)
            assert sdk_init_response.status_code == 200
            assert sdk_init_response.json()["client_id"] == "client-a"
            assert sdk_init_response.json()["output_style"] == "default"
            assert sdk_init_response.json()["models"]
            assert sdk_init_response.json()["account"]["subscriptionType"] == "developer_harness"
            sdk_mcp_response = client.get("/api/harness/v1/sdk/mcp-status", headers=headers_a)
            assert sdk_mcp_response.status_code == 200
            assert sdk_mcp_response.json()["client_id"] == "client-a"
            assert "mcpServers" in sdk_mcp_response.json()
            empty_context_response = client.get("/api/harness/v1/sdk/context-usage", headers=headers_b)
            assert empty_context_response.status_code == 200
            assert empty_context_response.json()["totalTokens"] == 0
            providers = client.get("/api/harness/v1/llm/providers")
            assert providers.status_code == 200
            assert any(item["provider"] == "ollama" for item in providers.json()["providers"])
            probe = client.post(
                "/api/harness/v1/llm/probe",
                json={
                    "provider": {
                        "provider": "local_missing",
                        "api_style": "chat_completions",
                        "base_url": None,
                    }
                },
            )
            assert probe.status_code == 200
            assert probe.json()["status"] == "configuration_required"
            assert probe.json()["provider_name"] == "local_missing"
            assert probe.json()["api_style"] == "chat_completions"
            assert probe.json()["provider_request_attempted"] is False
            assert probe.json()["provider_response_received"] is False
            assert probe.json()["external_api_called"] is False
            assert "diagnostic_id" not in probe.json()
            legacy = client.get("/api/dev-harness/capabilities")
            assert legacy.status_code == 200

            assert client.get("/api/harness/v1/tasks").status_code == 400
            assert client.get("/api/harness/v1/llm/diagnostics").status_code == 400
            assert client.get("/api/harness/v1/eval-cases").status_code == 400

            headers_a_idempotent = {"X-Harness-Client-Id": "client-a", "Idempotency-Key": "verify-isolation"}
            recorded_probe = client.post(
                "/api/harness/v1/llm/probe",
                headers=headers_a,
                json={
                    "provider": {
                        "provider": "local_missing",
                        "api_style": "chat_completions",
                        "base_url": None,
                    }
                },
            )
            assert recorded_probe.status_code == 200
            assert isinstance(recorded_probe.json()["diagnostic_id"], int)
            assert recorded_probe.json()["provider_request_attempted"] is False
            assert recorded_probe.json()["external_api_called"] is False
            assert client.get("/api/harness/v1/llm/diagnostics", headers=headers_b).json()["diagnostics"] == []
            diagnostics_response = client.get("/api/harness/v1/llm/diagnostics", headers=headers_a)
            assert diagnostics_response.status_code == 200
            assert diagnostics_response.json()["diagnostics"][0]["diagnostic_id"] == (
                recorded_probe.json()["diagnostic_id"]
            )
            assert diagnostics_response.json()["diagnostics"][0]["status"] == "configuration_required"
            assert (
                diagnostics_response.json()["diagnostics"][0]["result"]["external_api_called"]
                is False
            )
            task_payload = {
                "prompt": "verify api isolation",
                "cwd": str(root),
                "agent": "harness-verifier",
                "skills": ["harness-task"],
                "sandbox": "read-only",
            }
            calls_before_start_false = len(getattr(api_runtime.executor, "calls", []))
            evidence_holder = client.post(
                "/api/harness/v1/tasks?start=false",
                json={
                    **task_payload,
                    "prompt": "evidence holder should not run",
                    "idempotency_key": "api-evidence-holder",
                },
                headers=headers_a,
            )
            assert evidence_holder.status_code == 200, evidence_holder.text
            assert evidence_holder.json()["status"] == "paused"
            assert evidence_holder.json()["lease"] is None
            assert len(getattr(api_runtime.executor, "calls", [])) == calls_before_start_false
            assert client.get(
                f"/api/harness/v1/tasks/{evidence_holder.json()['task_id']}",
                headers=headers_b,
            ).status_code == 404
            evidence_holder_events = client.get(
                f"/api/harness/v1/tasks/{evidence_holder.json()['task_id']}/events",
                headers=headers_a,
            )
            assert evidence_holder_events.status_code == 200
            assert "task.paused" in evidence_holder_events.text
            created = client.post(
                "/api/harness/v1/tasks?background=false",
                json=task_payload,
                headers=headers_a_idempotent,
            )
            assert created.status_code == 200, created.text
            created_task = created.json()
            assert created_task["status"] == "completed"
            assert created_task["client_id"] == "client-a"
            replay_call_count = len(getattr(api_runtime.executor, "calls", []))
            replay_event_count = len(api_runtime.store.events(created_task["task_id"]))
            start_false_replay = client.post(
                "/api/harness/v1/tasks?start=false",
                json=task_payload,
                headers=headers_a_idempotent,
            )
            assert start_false_replay.status_code == 200, start_false_replay.text
            assert start_false_replay.json()["task_id"] == created_task["task_id"]
            assert start_false_replay.json()["status"] == "completed"
            assert start_false_replay.json()["state_version"] == created_task["state_version"]
            assert len(getattr(api_runtime.executor, "calls", [])) == replay_call_count
            assert len(api_runtime.store.events(created_task["task_id"])) == replay_event_count
            background_replay = client.post(
                "/api/harness/v1/tasks",
                json=task_payload,
                headers=headers_a_idempotent,
            )
            assert background_replay.status_code == 200, background_replay.text
            assert background_replay.json()["task_id"] == created_task["task_id"]
            assert background_replay.json()["status"] == "completed"
            assert len(getattr(api_runtime.executor, "calls", [])) == replay_call_count
            assert len(api_runtime.store.events(created_task["task_id"])) == replay_event_count
            annotation = client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/annotations",
                headers=headers_a,
                json={
                    "title": "API harness session",
                    "tag": "api-gap",
                    "summary": "API metadata verification.",
                    "expected_state_version": created_task["state_version"],
                },
            )
            assert annotation.status_code == 200, annotation.text
            assert annotation.json()["custom_title"] == "API harness session"
            assert annotation.json()["tag"] == "api-gap"
            assert client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/annotations",
                headers=headers_b,
                json={"title": "cross-client must fail"},
            ).status_code == 404
            session_info_response = client.get(
                f"/api/harness/v1/tasks/{created_task['task_id']}/session-info",
                headers=headers_a,
            )
            assert session_info_response.status_code == 200
            assert session_info_response.json()["session"]["title"] == "API harness session"
            assert client.get(
                f"/api/harness/v1/tasks/{created_task['task_id']}/session-info",
                headers=headers_b,
            ).status_code == 404
            sessions_response = client.get("/api/harness/v1/sessions", headers=headers_a)
            assert sessions_response.status_code == 200
            annotated_sessions = [
                item
                for item in sessions_response.json()["sessions"]
                if item.get("task_id") == created_task["task_id"]
            ]
            assert annotated_sessions
            assert annotated_sessions[0]["custom_title"] == "API harness session"
            assert client.get("/api/harness/v1/sessions", headers=headers_b).json()["sessions"] == []
            fork_response = client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/fork",
                headers=headers_a,
                json={
                    "prompt": "api forked task",
                    "idempotency_key": "api-forked-task",
                    "expected_state_version": annotation.json()["state_version"],
                    "metadata": {"fork_reason": "api-test"},
                },
            )
            assert fork_response.status_code == 200, fork_response.text
            assert fork_response.json()["request"]["metadata"]["forked_from_task_id"] == created_task["task_id"]
            fork_sdk_response = client.get(
                f"/api/harness/v1/tasks/{fork_response.json()['task_id']}/sdk-events",
                headers=headers_a,
            )
            assert fork_sdk_response.status_code == 200, fork_sdk_response.text
            assert any(
                item.get("subtype") == "task_queued"
                for item in fork_sdk_response.json()["events"]
            )
            assert any(
                item.get("subtype") == "session_state_changed" and item.get("state") == "queued"
                for item in fork_sdk_response.json()["events"]
            )
            fork_cancel = client.post(
                f"/api/harness/v1/tasks/{fork_response.json()['task_id']}/commands",
                headers=headers_a,
                json={
                    "command": "cancel",
                    "expected_state_version": fork_response.json()["state_version"],
                },
            )
            assert fork_cancel.status_code == 200, fork_cancel.text
            cancelled_sdk_response = client.get(
                f"/api/harness/v1/tasks/{fork_response.json()['task_id']}/sdk-events",
                headers=headers_a,
            )
            assert cancelled_sdk_response.status_code == 200, cancelled_sdk_response.text
            cancelled_sdk_events = cancelled_sdk_response.json()["events"]
            assert any(
                item.get("subtype") == "task_notification" and item.get("status") == "stopped"
                for item in cancelled_sdk_events
            )
            assert any(
                item.get("subtype") == "session_state_changed" and item.get("state") == "idle"
                for item in cancelled_sdk_events
            )
            assert "task.cancelled" in {
                item["event_type"]
                for item in api_runtime.store.events(fork_response.json()["task_id"])
            }
            cancelled_transcript = client.get(
                f"/api/harness/v1/tasks/{fork_response.json()['task_id']}/client-transcript",
                headers=headers_a,
            )
            assert cancelled_transcript.status_code == 200, cancelled_transcript.text
            cancelled_last_event_id = cancelled_transcript.json()["status_line"]["last_event_id"]
            replay_result: dict[str, Any] = {}

            def read_cancelled_replay() -> None:
                with client.stream(
                    "GET",
                    f"/api/harness/v1/tasks/{fork_response.json()['task_id']}/events?after={cancelled_last_event_id}",
                    headers=headers_a,
                ) as replay_response:
                    replay_result["status_code"] = replay_response.status_code
                    replay_result["body"] = b"".join(replay_response.iter_bytes())

            replay_thread = Thread(target=read_cancelled_replay, daemon=True)
            replay_thread.start()
            replay_thread.join(2)
            assert not replay_thread.is_alive(), "cancelled task SSE replay should terminate without keepalive timeout"
            assert replay_result["status_code"] == 200
            assert replay_result["body"] == b""
            paused_response = client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/fork",
                headers=headers_a,
                json={
                    "prompt": "api paused task",
                    "idempotency_key": "api-paused-task",
                    "expected_state_version": annotation.json()["state_version"],
                    "metadata": {"fork_reason": "api-pause-test"},
                },
            )
            assert paused_response.status_code == 200, paused_response.text
            pause_command = client.post(
                f"/api/harness/v1/tasks/{paused_response.json()['task_id']}/commands",
                headers=headers_a,
                json={
                    "command": "pause",
                    "expected_state_version": paused_response.json()["state_version"],
                },
            )
            assert pause_command.status_code == 200, pause_command.text
            paused_sdk_response = client.get(
                f"/api/harness/v1/tasks/{paused_response.json()['task_id']}/sdk-events",
                headers=headers_a,
            )
            assert paused_sdk_response.status_code == 200, paused_sdk_response.text
            paused_sdk_events = paused_sdk_response.json()["events"]
            assert any(
                item.get("subtype") == "task_notification" and item.get("status") == "stopped"
                for item in paused_sdk_events
            )
            assert any(
                item.get("subtype") == "session_state_changed" and item.get("state") == "idle"
                for item in paused_sdk_events
            )
            assert "task.paused" in {
                item["event_type"]
                for item in api_runtime.store.events(paused_response.json()["task_id"])
            }
            paused_transcript = client.get(
                f"/api/harness/v1/tasks/{paused_response.json()['task_id']}/client-transcript",
                headers=headers_a,
            )
            assert paused_transcript.status_code == 200, paused_transcript.text
            paused_last_event_id = paused_transcript.json()["status_line"]["last_event_id"]
            paused_replay_result: dict[str, Any] = {}

            def read_paused_replay() -> None:
                with client.stream(
                    "GET",
                    f"/api/harness/v1/tasks/{paused_response.json()['task_id']}/events?after={paused_last_event_id}",
                    headers=headers_a,
                ) as replay_response:
                    paused_replay_result["status_code"] = replay_response.status_code
                    paused_replay_result["body"] = b"".join(replay_response.iter_bytes())

            paused_replay_thread = Thread(target=read_paused_replay, daemon=True)
            paused_replay_thread.start()
            paused_replay_thread.join(2)
            assert not paused_replay_thread.is_alive(), "paused task SSE replay should terminate without keepalive timeout"
            assert paused_replay_result["status_code"] == 200
            assert paused_replay_result["body"] == b""
            paused_state_version = paused_transcript.json()["status_line"]["state_version"]
            resume_command = client.post(
                f"/api/harness/v1/tasks/{paused_response.json()['task_id']}/commands",
                headers=headers_a,
                json={
                    "command": "resume",
                    "expected_state_version": paused_state_version,
                },
            )
            assert resume_command.status_code == 200, resume_command.text
            assert resume_command.json()["status"] == "queued"
            resumed_sdk_response = client.get(
                f"/api/harness/v1/tasks/{paused_response.json()['task_id']}/sdk-events",
                headers=headers_a,
            )
            assert resumed_sdk_response.status_code == 200, resumed_sdk_response.text
            resumed_sdk_events = resumed_sdk_response.json()["events"]
            assert any(
                item.get("subtype") == "task_resumed"
                for item in resumed_sdk_events
            )
            assert any(
                item.get("subtype") == "session_state_changed" and item.get("state") == "queued"
                for item in resumed_sdk_events
            )
            assert "task.resume" in {
                item["event_type"]
                for item in api_runtime.store.events(paused_response.json()["task_id"])
            }
            cleanup_resume = client.post(
                f"/api/harness/v1/tasks/{paused_response.json()['task_id']}/commands",
                headers=headers_a,
                json={
                    "command": "cancel",
                    "expected_state_version": resume_command.json()["state_version"],
                },
            )
            assert cleanup_resume.status_code == 200, cleanup_resume.text
            assert client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/fork",
                headers=headers_b,
                json={"prompt": "cross-client fork must fail"},
            ).status_code == 404
            created_again = client.post(
                "/api/harness/v1/tasks?background=false",
                json=task_payload,
                headers=headers_a_idempotent,
            )
            assert created_again.status_code == 200
            assert created_again.json()["task_id"] == created_task["task_id"]
            assert client.get(f"/api/harness/v1/tasks/{created_task['task_id']}", headers=headers_b).status_code == 404
            assert not client.get("/api/harness/v1/tasks", headers=headers_b).json()["tasks"]
            events = client.get(f"/api/harness/v1/tasks/{created_task['task_id']}/events", headers=headers_a)
            assert events.status_code == 200
            assert "event: harness_event" in events.text
            assert client.get(f"/api/harness/v1/tasks/{created_task['task_id']}/events", headers=headers_b).status_code == 404
            assert client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/commands",
                headers=headers_b,
                json={
                    "command": "resume",
                    "expected_state_version": created_task["state_version"],
                },
            ).status_code == 404
            assert client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/reply?background=false",
                headers=headers_b,
                json={
                    "prompt": "cross-client reply must not resume this task",
                    "expected_state_version": created_task["state_version"],
                },
            ).status_code == 404
            assert client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/subagents?background=false",
                headers=headers_b,
                json={
                    "prompt": "cross-client subagent must not attach to this task",
                    "cwd": str(root),
                    "sandbox": "read-only",
                },
            ).status_code == 404
            child_created = client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/subagents?background=false",
                headers=headers_a,
                json={
                    "prompt": "api child task",
                    "idempotency_key": "api-child-task",
                    "cwd": str(root),
                    "sandbox": "read-only",
                    "join_policy": "all",
                },
            )
            assert child_created.status_code == 200, child_created.text
            assert child_created.json()["parent_task_id"] == created_task["task_id"]
            child_replay_call_count = len(getattr(api_runtime.executor, "calls", []))
            parent_event_count = len(api_runtime.store.events(created_task["task_id"]))
            child_event_count = len(api_runtime.store.events(child_created.json()["task_id"]))
            child_replay = client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/subagents",
                headers=headers_a,
                json={
                    "prompt": "api child task",
                    "idempotency_key": "api-child-task",
                    "cwd": str(root),
                    "sandbox": "read-only",
                    "join_policy": "all",
                },
            )
            assert child_replay.status_code == 200, child_replay.text
            assert child_replay.json()["task_id"] == child_created.json()["task_id"]
            assert child_replay.json()["status"] == "completed"
            assert len(getattr(api_runtime.executor, "calls", [])) == child_replay_call_count
            assert len(api_runtime.store.events(created_task["task_id"])) == parent_event_count
            assert len(api_runtime.store.events(child_created.json()["task_id"])) == child_event_count
            assert client.get(
                f"/api/harness/v1/tasks/{created_task['task_id']}/subagents",
                headers=headers_b,
            ).status_code == 404
            api_subagents = client.get(
                f"/api/harness/v1/tasks/{created_task['task_id']}/subagents",
                headers=headers_a,
            )
            assert api_subagents.status_code == 200, api_subagents.text
            assert api_subagents.json()["counts"]["completed"] == 1
            assert api_subagents.json()["subagents"][0]["task_id"] == child_created.json()["task_id"]
            evidence_call = client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/tool-calls",
                headers=headers_a,
                json={
                    "tool_name": "adapter_probe",
                    "input": {"provider": "local"},
                    "status": "completed",
                },
            )
            assert evidence_call.status_code == 200, evidence_call.text
            evidence_observation = client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/observations",
                headers=headers_a,
                json={
                    "call_id": evidence_call.json()["call_id"],
                    "tool_name": "adapter_probe",
                    "result": {"status": "configuration_required"},
                    "evidence_refs": ["diagnostic:missing-base-url"],
                    "status": "completed",
                },
            )
            assert evidence_observation.status_code == 200, evidence_observation.text
            evidence_permission = client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/permission-decisions",
                headers=headers_a,
                json={
                    "call_id": evidence_call.json()["call_id"],
                    "tool_name": "adapter_probe",
                    "behavior": "ask",
                    "input": {"provider": "local"},
                    "decision_reason": {"type": "mode", "mode": "default"},
                    "message": "adapter_probe requires permission",
                    "blocked_path": "harness_outputs/provider-diagnostic.json",
                    "suggestions": [
                        {
                            "type": "addRules",
                            "destination": "session",
                            "rules": [{"toolName": "adapter_probe"}],
                            "behavior": "allow",
                        }
                    ],
                },
            )
            assert evidence_permission.status_code == 200, evidence_permission.text
            assert client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/permission-decisions",
                headers=headers_b,
                json={"tool_name": "adapter_probe", "behavior": "deny"},
            ).status_code == 404
            evidence_progress = client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/progress",
                headers=headers_a,
                json={
                    "items": [
                        {
                            "item_id": "api-inspect",
                            "content": "Inspect API progress evidence",
                            "active_form": "Inspecting API progress evidence",
                            "status": "completed",
                        },
                        {
                            "item_id": "api-verify",
                            "content": "Run verification for API progress evidence",
                            "active_form": "Running verification for API progress evidence",
                            "status": "completed",
                        },
                    ],
                    "metadata": {"source": "api-test"},
                },
            )
            assert evidence_progress.status_code == 200, evidence_progress.text
            assert evidence_progress.json()["counts"]["completed"] == 2
            assert evidence_progress.json()["verification_nudge_needed"] is False
            assert client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/progress",
                headers=headers_b,
                json={"items": []},
            ).status_code == 404
            evidence_artifact = client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/artifacts",
                headers=headers_a,
                json={
                    "kind": "provider_diagnostic",
                    "path": "harness_outputs/provider-diagnostic.json",
                    "digest": "sha256:diagnostic",
                },
            )
            assert evidence_artifact.status_code == 200, evidence_artifact.text
            assert client.get(f"/api/harness/v1/tasks/{created_task['task_id']}/evidence", headers=headers_b).status_code == 404
            evidence_response = client.get(
                f"/api/harness/v1/tasks/{created_task['task_id']}/evidence",
                headers=headers_a,
            )
            assert evidence_response.status_code == 200
            assert evidence_response.json()["tool_calls"][0]["tool_name"] == "adapter_probe"
            assert evidence_response.json()["observations"][0]["evidence_refs"] == ["diagnostic:missing-base-url"]
            assert evidence_response.json()["permission_decisions"][0]["behavior"] == "ask"
            assert evidence_response.json()["permission_decisions"][0]["blocked_path"] == "harness_outputs/provider-diagnostic.json"
            assert evidence_response.json()["task_progress"][0]["counts"]["completed"] == 2
            assert evidence_response.json()["artifacts"][0]["kind"] == "provider_diagnostic"
            assert evidence_response.json()["subagents"][0]["task_id"] == child_created.json()["task_id"]
            context_response = client.get("/api/harness/v1/sdk/context-usage", headers=headers_a)
            assert context_response.status_code == 200
            context_payload = context_response.json()
            assert context_payload["totalTokens"] > 0
            assert context_payload["sourceTaskCount"] >= 1
            assert created_task["task_id"] in context_payload["sourceTaskIds"]
            assert context_payload["eventCount"] >= 1
            assert context_payload["evidenceCounts"]["tool_calls"] >= 1
            assert "not tokenizer-backed" in context_payload["claim_boundary"]
            assert context_payload["messageBreakdown"]["toolCallTokens"] >= 1
            assert {hook["event_name"] for hook in evidence_response.json()["hook_executions"]} >= {
                "before_task_run",
                "before_codex_exec",
                "after_codex_exec",
                "after_task_run",
            }
            assert (
                client.get(
                    f"/api/harness/v1/tasks/{created_task['task_id']}/client-transcript",
                    headers=headers_b,
                ).status_code
                == 404
            )
            transcript_response = client.get(
                f"/api/harness/v1/tasks/{created_task['task_id']}/client-transcript",
                headers=headers_a,
            )
            assert transcript_response.status_code == 200
            transcript_payload = transcript_response.json()
            transcript_summary_response = client.get(
                f"/api/harness/v1/tasks/{created_task['task_id']}/client-transcript?summary=true",
                headers=headers_a,
            )
            assert transcript_summary_response.status_code == 200, transcript_summary_response.text
            transcript_summary_payload = transcript_summary_response.json()
            assert transcript_summary_payload["source"] == "harness_client_transcript_summary"
            assert transcript_summary_payload["summary_requested"] is True
            assert transcript_summary_payload["task_id"] == created_task["task_id"]
            assert transcript_summary_payload["task_status"] == created_task["status"]
            assert transcript_summary_payload["tool_progress_count"] == 1
            assert transcript_summary_payload["item_type_counts"]["tool_progress"] == 1
            assert "sdk_events" in transcript_summary_payload["source_endpoints_present"]
            assert transcript_payload["session_info"]["custom_title"] == "API harness session"
            assert transcript_payload["status_line"]["client_id"] == "client-a"
            assert transcript_payload["status_line"]["counts"]["tool_calls"] == 1
            assert transcript_payload["status_line"]["counts"]["permission_decisions"] == 1
            assert transcript_payload["status_line"]["counts"]["task_progress"] == 1
            assert transcript_payload["task_progress"]["items"][1]["item_id"] == "api-verify"
            assert transcript_payload["status_line"]["subagents"]["completed"] == 1
            assert transcript_payload["subagents"][0]["task_id"] == child_created.json()["task_id"]
            assert transcript_payload["permission_decisions"][0]["behavior"] == "ask"
            assert transcript_payload["tool_use_summary"]["tool_summary"].startswith("1 tool call")
            assert transcript_payload["tool_progress"][0]["tool_name"] == "adapter_probe"
            assert transcript_payload["messages"][0]["type"] == "user_message"
            assert any(
                item["type"] == "streamlined_text" and item["text"] == "verified"
                for item in transcript_payload["messages"]
            )
            assert transcript_payload["source_endpoints"]["sdk_events"].endswith(
                f"/tasks/{created_task['task_id']}/sdk-events"
            )
            assert (
                client.get(
                    f"/api/harness/v1/tasks/{created_task['task_id']}/sdk-events",
                    headers=headers_b,
                ).status_code
                == 404
            )
            sdk_events_response = client.get(
                f"/api/harness/v1/tasks/{created_task['task_id']}/sdk-events",
                headers=headers_a,
            )
            assert sdk_events_response.status_code == 200
            sdk_events_payload = sdk_events_response.json()
            sdk_events_summary_response = client.get(
                f"/api/harness/v1/tasks/{created_task['task_id']}/sdk-events?summary=true",
                headers=headers_a,
            )
            assert sdk_events_summary_response.status_code == 200, sdk_events_summary_response.text
            sdk_events_summary_payload = sdk_events_summary_response.json()
            assert sdk_events_summary_payload["source"] == "harness_sdk_events_summary"
            assert sdk_events_summary_payload["summary_requested"] is True
            assert sdk_events_summary_payload["task_id"] == created_task["task_id"]
            assert sdk_events_summary_payload["projected_event_count"] == sdk_events_payload["projected_event_count"]
            assert sdk_events_summary_payload["event_type_counts"]["tool_progress"] >= 1
            assert sdk_events_summary_payload["tool_progress_count"] >= 1
            assert sdk_events_summary_payload["final_output_included"] is True
            assert sdk_events_summary_payload["terminal_event_included"] is True
            assert sdk_events_summary_payload["cursor_policy"]["cursor_basis"] == "raw_harness_event_id"
            sdk_subtypes = [item["subtype"] for item in sdk_events_payload["events"] if item.get("type") == "system"]
            assert sdk_events_payload["projected_event_count"] == len(sdk_events_payload["events"])
            assert sdk_events_payload["cursor_policy"]["cursor_basis"] == "raw_harness_event_id"
            assert sdk_events_payload["cursor_policy"]["cursor_semantics"] == "exclusive"
            assert sdk_events_payload["terminal_event_included"] is True
            assert sdk_events_payload["final_output_included"] is True
            assert "task_started" in sdk_subtypes
            assert "task_progress" in sdk_subtypes
            assert "task_notification" in sdk_subtypes
            assert any(item.get("type") == "tool_progress" for item in sdk_events_payload["events"])
            assert any(
                item.get("type") == "permission_decision"
                and item.get("tool_name") == "adapter_probe"
                and item.get("behavior") == "ask"
                for item in sdk_events_payload["events"]
            )
            assert any(item.get("type") == "final_output" for item in sdk_events_payload["events"])
            assert sdk_events_payload["events"][0]["session_id"] == created_task["thread_id"]
            terminal_delta_response = client.get(
                f"/api/harness/v1/tasks/{created_task['task_id']}/sdk-events?after={sdk_events_payload['terminal_event_id'] - 1}",
                headers=headers_a,
            )
            assert terminal_delta_response.status_code == 200
            terminal_delta_payload = terminal_delta_response.json()
            assert terminal_delta_payload["raw_event_count"] >= 1
            assert terminal_delta_payload["terminal_event_included"] is True
            assert terminal_delta_payload["final_output_included"] is True
            assert any(item.get("type") == "final_output" for item in terminal_delta_payload["events"])
            caught_up_payload = client.get(
                f"/api/harness/v1/tasks/{created_task['task_id']}/sdk-events?after={sdk_events_payload['next_after']}",
                headers=headers_a,
            ).json()
            assert caught_up_payload["events"] == []
            assert caught_up_payload["raw_event_count"] == 0
            assert caught_up_payload["projected_event_count"] == 0
            assert caught_up_payload["terminal_event_included"] is False
            assert caught_up_payload["final_output_included"] is False
            api_queue = client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/queued-messages",
                headers=headers_a,
                json={
                    "prompt": "queued api follow-up",
                    "priority": "next",
                    "message_uuid": "api_msg_next",
                    "expected_state_version": transcript_payload["status_line"]["state_version"],
                },
            )
            assert api_queue.status_code == 200, api_queue.text
            assert api_queue.json()["message_uuid"] == "api_msg_next"
            assert client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/queued-messages",
                headers=headers_b,
                json={"prompt": "cross-client queue must fail", "priority": "later"},
            ).status_code == 404
            api_cancel = client.post(
                f"/api/harness/v1/tasks/{created_task['task_id']}/queued-messages/cancel",
                headers=headers_a,
                json={
                    "message_uuid": "api_msg_next",
                    "expected_state_version": api_queue.json()["state_version"],
                },
            )
            assert api_cancel.status_code == 200, api_cancel.text
            assert api_cancel.json()["cancelled"] is True
            api_transcript_after_queue = client.get(
                f"/api/harness/v1/tasks/{created_task['task_id']}/client-transcript",
                headers=headers_a,
            )
            assert api_transcript_after_queue.json()["status_line"]["queued_messages"]["cancelled"] == 1
            eval_response = client.post(
                "/api/harness/v1/eval-cases",
                headers=headers_a,
                json={
                    "name": "provider configuration regression",
                    "source_task_id": created_task["task_id"],
                    "failure_label": "configuration_required",
                    "input": {"provider": "local_missing"},
                    "expected": {"status": "configuration_required"},
                    "verifier_command": "python -B verify.py",
                    "evidence_refs": [f"task:{created_task['task_id']}"],
                    "metadata": {"source": "api-test"},
                },
            )
            assert eval_response.status_code == 200, eval_response.text
            assert eval_response.json()["client_id"] == "client-a"
            assert client.get("/api/harness/v1/eval-cases", headers=headers_b).json()["eval_cases"] == []
            eval_list = client.get("/api/harness/v1/eval-cases", headers=headers_a)
            assert eval_list.status_code == 200
            assert eval_list.json()["eval_cases"][0]["name"] == "provider configuration regression"
            assert client.post(
                "/api/harness/v1/eval-cases",
                headers=headers_b,
                json={
                    "name": "cross-client source must fail",
                    "source_task_id": created_task["task_id"],
                    "input": {},
                    "expected": {},
                },
            ).status_code == 404

            queued_a = api_runtime.prepare_task(
                {
                    "prompt": "queued client a",
                    "cwd": str(root),
                    "client_id": "client-a",
                    "sandbox": "read-only",
                    "skills": [],
                    "metadata": {},
                    "verification": {"commands": [], "timeout_seconds": 30},
                }
            )
            assert queued_a["status"] == "queued"
            assert client.post("/api/harness/v1/workers/tick", headers=headers_b).json()["status"] == "idle"
            claimed = client.post("/api/harness/v1/workers/tick", headers=headers_a)
            assert claimed.status_code == 200
            assert claimed.json()["status"] == "claimed"
            assert claimed.json()["task"]["client_id"] == "client-a"
            queued_heartbeat = api_runtime.prepare_task(
                {
                    "prompt": "api heartbeat",
                    "cwd": str(root),
                    "client_id": "client-a",
                    "sandbox": "read-only",
                    "skills": [],
                    "metadata": {},
                    "verification": {"commands": [], "timeout_seconds": 30},
                }
            )
            claimed_heartbeat = api_runtime.store.claim_next_task("client-a", client_id="client-a", lease_seconds=1)
            assert claimed_heartbeat["task_id"] == queued_heartbeat["task_id"]
            heartbeat_response = client.post(
                "/api/harness/v1/workers/heartbeat",
                headers=headers_a,
                json={
                    "task_id": queued_heartbeat["task_id"],
                    "lease_epoch": claimed_heartbeat["lease"]["epoch"],
                    "lease_seconds": 300,
                },
            )
            assert heartbeat_response.status_code == 200, heartbeat_response.text
            assert heartbeat_response.json()["task"]["lease"]["expires_at"] > claimed_heartbeat["lease"]["expires_at"]
            assert client.post(
                "/api/harness/v1/workers/heartbeat",
                headers=headers_b,
                json={
                    "task_id": queued_heartbeat["task_id"],
                    "lease_epoch": claimed_heartbeat["lease"]["epoch"],
                },
            ).status_code == 404
            assert client.post(
                "/api/harness/v1/workers/heartbeat",
                headers=headers_a,
                json={
                    "task_id": queued_heartbeat["task_id"],
                    "lease_epoch": claimed_heartbeat["lease"]["epoch"] + 1,
                },
            ).status_code == 409

            loop = client.post(
                "/api/harness/v1/loops",
                headers=headers_a,
                json={
                    "name": "isolated loop",
                    "task": {
                        "prompt": "loop task",
                        "cwd": str(root),
                        "sandbox": "read-only",
                    },
                    "interval_seconds": 60,
                    "max_iterations": 1,
                },
            )
            assert loop.status_code == 200, loop.text
            loop_id = loop.json()["loop_id"]
            assert loop.json()["evidence_contract"]["termination_conditions"][0] == "max_iterations"
            assert not client.get("/api/harness/v1/loops", headers=headers_b).json()["loops"]
            assert client.post(f"/api/harness/v1/loops/{loop_id}/tick", headers=headers_b, json={"force": True}).status_code == 404
            loop_tick = client.post(f"/api/harness/v1/loops/{loop_id}/tick", headers=headers_a, json={"force": True})
            assert loop_tick.status_code == 200
            assert loop_tick.json()["loop"]["iteration_records"][0]["evidence_refs"][0].startswith("task:")
            assert client.get(f"/api/harness/v1/loops/{loop_id}/evidence", headers=headers_b).status_code == 404
            loop_evidence_response = client.get(f"/api/harness/v1/loops/{loop_id}/evidence", headers=headers_a)
            assert loop_evidence_response.status_code == 200, loop_evidence_response.text
            assert loop_evidence_response.json()["loop"]["loop_id"] == loop_id
            assert loop_evidence_response.json()["iterations"][0]["counts"]["events"] >= 1
            assert loop_evidence_response.json()["iterations"][0]["verification_status"] == "not_run"

            safe_loop = client.post(
                "/api/harness/v1/loops",
                headers=headers_a,
                json={
                    "name": "safe tick loop",
                    "task": {
                        "prompt": "prepare only",
                        "cwd": str(root),
                        "sandbox": "read-only",
                    },
                    "interval_seconds": 60,
                    "max_iterations": 1,
                },
            )
            assert safe_loop.status_code == 200, safe_loop.text
            safe_loop_id = safe_loop.json()["loop_id"]
            safe_tick = client.post(
                f"/api/harness/v1/loops/{safe_loop_id}/tick",
                headers=headers_a,
                json={"force": True, "start": False},
            )
            assert safe_tick.status_code == 200, safe_tick.text
            safe_task_id = safe_tick.json()["task"]["task_id"]
            assert safe_tick.json()["task"]["status"] == "queued"
            safe_events = api_runtime.store.events(safe_task_id)
            assert [item["event_type"] for item in safe_events] == ["task.queued"]

            isolated = client.post(
                "/api/harness/v1/tasks?background=false",
                json={
                    "prompt": "verify isolated workspace",
                    "workspace_mode": "client_isolated",
                    "sandbox": "read-only",
                    "idempotency_key": "isolated-workspace",
                },
                headers=headers_b,
            )
            assert isolated.status_code == 200, isolated.text
            isolated_task = isolated.json()
            isolated_cwd = Path(isolated_task["request"]["cwd"])
            assert isolated_cwd != root
            assert root not in isolated_cwd.parents
            assert (isolated_cwd / "AGENTS.md").exists()
            assert isolated_task["request"]["instruction_scope"] == "client_isolated_workspace_agents_md"
            assert isolated_task["result"]["cwd"] == str(isolated_cwd)
        finally:
            harness_routes.harness = original_harness
    print("Development harness verification passed.")


if __name__ == "__main__":
    main()
