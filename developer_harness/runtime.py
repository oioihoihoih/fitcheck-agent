from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

import requests

from .catalog import load_catalog
from .read_model_summary import summarize_client_transcript, summarize_sdk_events
from .store import HarnessStore, LeaseConflictError, StateConflictError


def _resolve_codex_executable() -> str:
    configured = os.getenv("CODEX_BIN")
    if configured:
        return configured
    discovered = shutil.which("codex")
    if discovered:
        return discovered
    appdata = os.getenv("APPDATA")
    if appdata:
        return str(Path(appdata) / "npm" / "codex.cmd")
    return "codex"


class CodexCliExecutor:
    def __init__(self) -> None:
        self.processes: dict[str, subprocess.Popen[str]] = {}

    def execute(
        self,
        *,
        task_id: str,
        prompt: str,
        cwd: Path,
        sandbox: str,
        model: str | None,
        thread_id: str | None,
        on_event: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        executable = _resolve_codex_executable()
        command = [executable, "exec", "--json", "--skip-git-repo-check", "-C", str(cwd), "-s", sandbox]
        if model:
            command += ["-m", model]
        if thread_id:
            command += ["resume", thread_id, prompt]
        else:
            command += [prompt]
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.processes[task_id] = process
            detected_thread_id = thread_id
            last_message = ""
            assert process.stdout is not None
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    event = {"type": "stdout", "text": line}
                detected_thread_id = (
                    event.get("thread_id")
                    or (event.get("thread") or {}).get("id")
                    or detected_thread_id
                )
                if event.get("type") in {"item.completed", "agent_message"}:
                    item = event.get("item") or {}
                    last_message = str(item.get("text") or event.get("text") or last_message)
                on_event(event)
            stderr = process.stderr.read() if process.stderr else ""
            returncode = process.wait()
            return {
                "returncode": returncode,
                "thread_id": detected_thread_id,
                "last_message": last_message,
                "stderr": stderr[-4000:],
            }
        finally:
            self.processes.pop(task_id, None)

    def cancel(self, task_id: str) -> bool:
        process = self.processes.get(task_id)
        if not process or process.poll() is not None:
            return False
        process.terminate()
        return True


class DevelopmentHarness:
    def __init__(self, root: Path, store: HarnessStore, executor: Any | None = None) -> None:
        self.root = root.resolve()
        self.store = store
        self.executor = executor or CodexCliExecutor()

    def capabilities(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "runtime": "codex_cli_jsonl",
            "api_surface": "/api/harness/v1",
            "legacy_api_surface": "/api/dev-harness",
            "persistence": str(self.store.path),
            "loop_engineering_contract": {
                "health_ready": "health_is_public_ready_reports_persistence_and_capability_status",
                "idempotency": "client_scoped_idempotency_key_body_or_header",
                "events": "append_only_with_monotonic_seq",
                "sdk_events": "client_friendly_task_started_progress_handoff_approval_question_guardrail_notification_session_state_projection",
                "read_model_summaries": "sdk_events_and_client_transcript_summary_views_for_fast_controller_checks",
                "sdk_initialize": "client_friendly_initialize_mcp_status_and_context_usage_snapshots",
                "tool_progress": "sdk_tool_progress_projection_from_recorded_tool_calls",
                "state": "optimistic_state_version",
                "session_metadata": "sdk_like_session_info_title_tag_summary_and_task_fork",
                "isolation": "stateful_operations_require_x_harness_client_id",
                "workspace_isolation": "workspace_mode_client_isolated_creates_separate_cwd_and_agents_md",
                "workers": "lease_owner_epoch_expires_at_with_heartbeat",
                "queued_messages": "durable_now_next_later_input_queue_with_cancel",
                "hooks": "typed_lifecycle_records",
                "permission_decisions": "structured_allow_ask_deny_passthrough_records",
                "task_progress": "todo_write_style_progress_items_with_verification_nudge",
                "verification": "persisted_command_results",
                "llm_provider_profile": "openai_responses_or_openai_compatible_chat_completions",
                "provider_diagnostics": "client_scoped_models_probe_records",
                "tool_evidence": "typed_tool_calls_observations_and_artifacts",
                "eval_cases": "client_scoped_trace_gap_regression_cases",
                "task_evidence": "tool_observation_artifact_verification_and_hook_replay",
                "client_transcript": "sdk_like_status_messages_tool_handoff_approval_question_guardrail_summary_and_source_endpoints",
                "subagent_handoff": "parent_task_child_summaries_and_join_status",
                "loop_evidence_contract": "trace_eval_verification_termination_contract_per_loop",
                "loop_evidence": "client_scoped_loop_iteration_task_evidence_summary",
            },
            "agentic_ai_harness_contract": {
                "agent_sdk_primitives": [
                    "model_selected_tools",
                    "handoffs_or_agents_as_tools",
                    "guardrails",
                    "sessions_or_resumable_state",
                    "trace_capture",
                    "verification_gates",
                ],
                "improvement_loop": [
                    "collect_trace_or_runtime_evidence",
                    "classify_failure_or_gap",
                    "turn_gap_into_eval_or_verifier",
                    "patch_harness_or_agent_runtime",
                    "rerun_verification",
                    "persist_evidence_in_events",
                ],
                "loop_iteration_contract": [
                    "declare_trace_sources_eval_targets_and_verification_commands",
                    "record_iteration_task_id_and_evidence_refs",
                    "terminate_on_max_iterations_or_explicit_pause_cancel",
                    "promote_repeatable_gaps_to_eval_cases",
                ],
                "production_boundary": "mock_cached_fixture_deterministic_paths_are_harness_only",
            },
            "read_model_contract": _read_model_contract(),
            "agentic_loop_templates": self.agentic_loop_templates(),
            "llm_provider_templates": self.llm_provider_templates(),
            **load_catalog(self.root),
        }

    def agentic_loop_templates(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "agents_sdk_trace_eval_codex",
                "label": "Agents SDK trace -> eval -> Codex patch loop",
                "purpose": "Use stored traces, tool calls, guardrail outcomes, and verifier results to improve an agent harness.",
                "required_evidence": [
                    "trace_or_event_stream",
                    "failing_or_missing_eval",
                    "patch_target",
                    "verification_command",
                ],
                "recommended_task_metadata": {
                    "agentic_loop_template": "agents_sdk_trace_eval_codex",
                    "agentic_loop": {
                        "trace_sources": [],
                        "eval_targets": [],
                        "verification_commands": [],
                    },
                },
            },
            {
                "id": "backend_owned_tool_loop_hardening",
                "label": "Backend-owned tool loop hardening",
                "purpose": "Improve model-selected backend tool loops without confusing deterministic harness behavior with production AI.",
                "required_evidence": [
                    "stored_observations",
                    "tool_schema_or_adapter_contract",
                    "provider_status",
                    "focused_verifier",
                ],
                "recommended_task_metadata": {
                    "agentic_loop_template": "backend_owned_tool_loop_hardening",
                    "agentic_loop": {
                        "trace_sources": ["agent_run_store", "tool_observations"],
                        "eval_targets": [],
                        "verification_commands": [],
                    },
                },
            },
        ]

    def llm_provider_templates(self) -> list[dict[str, Any]]:
        return [
            {
                "provider": "openai",
                "api_style": "responses",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "supports_tool_calling": True,
                "supports_stateful_responses": True,
            },
            {
                "provider": "ollama",
                "api_style": "chat_completions",
                "base_url": "http://localhost:11434/v1",
                "api_key_env": None,
                "supports_tool_calling": True,
                "supports_stateful_responses": False,
                "notes": "Ollama exposes OpenAI-compatible local endpoints; stateful Responses previous_response_id is not assumed.",
            },
            {
                "provider": "lm_studio",
                "api_style": "chat_completions",
                "base_url": "http://localhost:1234/v1",
                "api_key_env": None,
                "supports_tool_calling": True,
                "supports_stateful_responses": False,
                "notes": "LM Studio local server can be used by pointing OpenAI-compatible clients at its base_url.",
            },
            {
                "provider": "vllm_or_other_openai_compatible",
                "api_style": "chat_completions",
                "base_url": "http://localhost:8000/v1",
                "api_key_env": None,
                "supports_tool_calling": True,
                "supports_stateful_responses": False,
            },
        ]

    def sdk_initialize(self, client_id: str) -> dict[str, Any]:
        catalog = load_catalog(self.root)
        return {
            "status": "completed",
            "client_id": client_id,
            "source": "developer_harness_sdk_initialize_snapshot",
            "commands": [
                {
                    "name": item["name"],
                    "description": item.get("description") or "",
                    "argumentHint": "",
                }
                for item in catalog.get("skills", [])
            ],
            "agents": [
                {
                    "name": item["name"],
                    "description": item.get("description") or "",
                    "model": None,
                }
                for item in catalog.get("agents", [])
            ],
            "output_style": "default",
            "available_output_styles": ["default"],
            "models": [
                {
                    "value": provider.get("model") or provider["provider"],
                    "displayName": provider["provider"],
                    "description": provider.get("notes")
                    or f"{provider['provider']} via {provider.get('api_style', 'unknown')}",
                    "supportsEffort": False,
                    "supportsAdaptiveThinking": False,
                    "supportsFastMode": False,
                    "supportsAutoMode": False,
                    "provider": provider,
                }
                for provider in self.llm_provider_templates()
            ],
            "account": {
                "email": None,
                "organization": None,
                "subscriptionType": "developer_harness",
                "tokenSource": "environment",
                "apiKeySource": "environment" if os.getenv("OPENAI_API_KEY") else None,
                "apiProvider": "firstParty",
            },
            "pid": os.getpid(),
            "fast_mode_state": {"enabled": False, "available": False, "reason": "not_implemented"},
            "mcp_status_endpoint": "/api/harness/v1/sdk/mcp-status",
            "context_usage_endpoint": "/api/harness/v1/sdk/context-usage",
        }

    def sdk_mcp_status(self) -> dict[str, Any]:
        catalog = load_catalog(self.root)
        return {
            "status": "completed",
            "source": "developer_harness_catalog_snapshot",
            "mcpServers": [
                {
                    "name": item.get("event") or "hook",
                    "status": "disabled",
                    "scope": "harness_hook_catalog",
                    "error": "Hook catalog entries are not live MCP server connections.",
                    "tools": [],
                }
                for item in catalog.get("hooks", [])
            ],
        }

    def sdk_context_usage(self, client_id: str) -> dict[str, Any]:
        tasks = [task for task in self.store.list_tasks() if task.get("client_id") == client_id]
        event_count = sum(len(self.store.events(task["task_id"])) for task in tasks)
        evidence_counts = {"tool_calls": 0, "observations": 0, "artifacts": 0, "verification_results": 0}
        api_usage = _empty_api_usage_summary()
        for task in tasks:
            evidence = self.store.task_evidence(task["task_id"])
            for key in evidence_counts:
                evidence_counts[key] += len(evidence.get(key) or [])
            _add_model_response_usage(
                api_usage,
                task,
                evidence.get("observations") or [],
                evidence.get("artifacts") or [],
            )
        categories = [
            {"name": "tasks", "tokens": len(tasks), "color": "blue", "isDeferred": False},
            {"name": "events", "tokens": event_count, "color": "green", "isDeferred": False},
            {
                "name": "evidence",
                "tokens": sum(evidence_counts.values()),
                "color": "purple",
                "isDeferred": False,
            },
        ]
        total = sum(item["tokens"] for item in categories)
        max_tokens = 1_000_000
        percentage = (total / max_tokens) * 100 if max_tokens else 0
        return {
            "status": "completed",
            "source": "developer_harness_context_usage_estimate",
            "claim_boundary": (
                "Context usage is estimated from stored harness tasks, events, and evidence. "
                "It is not tokenizer-backed model context accounting and does not prove live provider connectivity."
            ),
            "sourceTaskCount": len(tasks),
            "sourceTaskIds": [task.get("task_id") for task in tasks if task.get("task_id")],
            "eventCount": event_count,
            "evidenceCounts": dict(evidence_counts),
            "categories": categories,
            "totalTokens": total,
            "maxTokens": max_tokens,
            "rawMaxTokens": max_tokens,
            "percentage": percentage,
            "gridRows": [],
            "model": "developer_harness",
            "memoryFiles": [],
            "mcpTools": [],
            "deferredBuiltinTools": [],
            "systemTools": [],
            "systemPromptSections": [],
            "agents": [],
            "slashCommands": {
                "totalCommands": len(load_catalog(self.root).get("skills", [])),
                "includedCommands": len(load_catalog(self.root).get("skills", [])),
                "tokens": 0,
            },
            "skills": {
                "totalSkills": len(load_catalog(self.root).get("skills", [])),
                "includedSkills": len(load_catalog(self.root).get("skills", [])),
                "tokens": 0,
                "skillFrontmatter": [
                    {"name": item["name"], "source": item.get("path") or "", "tokens": 0}
                    for item in load_catalog(self.root).get("skills", [])
                ],
            },
            "autoCompactThreshold": None,
            "isAutoCompactEnabled": False,
            "messageBreakdown": {
                "toolCallTokens": evidence_counts["tool_calls"],
                "toolResultTokens": evidence_counts["observations"],
                "attachmentTokens": evidence_counts["artifacts"],
                "assistantMessageTokens": event_count,
                "userMessageTokens": len(tasks),
                "toolCallsByType": [],
                "attachmentsByType": [],
            },
            "apiUsage": _finalize_api_usage_summary(api_usage),
        }

    def probe_llm_provider(
        self,
        provider: dict[str, Any],
        timeout_seconds: int = 5,
        *,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        base_url = str(provider.get("base_url") or "").rstrip("/")
        if not base_url:
            result = {
                "status": "configuration_required",
                "error": {"code": "LLM_BASE_URL_REQUIRED", "message": "base_url is required for provider probe."},
                "provider": provider,
                **_provider_probe_summary(provider, endpoint=None, request_attempted=False),
            }
            return self._record_provider_diagnostic(client_id, result)
        headers = {}
        api_key_env = provider.get("api_key_env")
        if api_key_env and os.getenv(str(api_key_env)):
            headers["Authorization"] = f"Bearer {os.getenv(str(api_key_env))}"
        try:
            response = requests.get(f"{base_url}/models", headers=headers, timeout=timeout_seconds)
            ok = 200 <= response.status_code < 300
            payload: Any
            try:
                payload = response.json()
            except ValueError:
                payload = response.text[:1000]
            result = {
                "status": "completed" if ok else "failed",
                "provider": provider,
                "endpoint": f"{base_url}/models",
                "http_status": response.status_code,
                "models_preview": _models_preview(payload),
                "raw_preview": payload if isinstance(payload, str) else None,
                "agent_sdk_fit": _agent_sdk_fit(provider),
                **_provider_probe_summary(
                    provider,
                    endpoint=f"{base_url}/models",
                    request_attempted=True,
                    http_status=response.status_code,
                ),
            }
            return self._record_provider_diagnostic(client_id, result)
        except (requests.ConnectionError, requests.Timeout) as exc:
            result = {
                "status": "not_connected",
                "provider": provider,
                "endpoint": f"{base_url}/models",
                "error": {"code": "LLM_PROVIDER_NOT_CONNECTED", "message": str(exc)},
                "agent_sdk_fit": _agent_sdk_fit(provider),
                **_provider_probe_summary(
                    provider,
                    endpoint=f"{base_url}/models",
                    request_attempted=True,
                    http_status=None,
                ),
            }
            return self._record_provider_diagnostic(client_id, result)
        except requests.RequestException as exc:
            result = {
                "status": "failed",
                "provider": provider,
                "endpoint": f"{base_url}/models",
                "error": {"code": "LLM_PROVIDER_PROBE_FAILED", "message": str(exc)},
                "agent_sdk_fit": _agent_sdk_fit(provider),
                **_provider_probe_summary(
                    provider,
                    endpoint=f"{base_url}/models",
                    request_attempted=True,
                    http_status=None,
                ),
            }
            return self._record_provider_diagnostic(client_id, result)

    def _record_provider_diagnostic(self, client_id: str | None, result: dict[str, Any]) -> dict[str, Any]:
        if not client_id:
            return result
        diagnostic = self.store.record_provider_diagnostic(client_id, result)
        return {**result, "diagnostic_id": diagnostic["diagnostic_id"], "diagnostic_recorded_at": diagnostic["created_at"]}

    def prepare_task(self, request: dict[str, Any]) -> dict[str, Any]:
        request = dict(request)
        if request.get("workspace_mode") == "client_isolated":
            cwd = self._prepare_isolated_workspace(request)
        else:
            cwd = Path(request.get("cwd") or self.root).resolve()
        if not cwd.exists() or not cwd.is_dir():
            raise ValueError("cwd must be an existing directory")
        request = {**request, "cwd": str(cwd)}
        return self.store.create_task(request)

    def list_task_sessions(self, client_id: str, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        tasks = [
            task
            for task in self.store.list_tasks()
            if task.get("client_id") == client_id
        ]
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 200))
        page = tasks[offset : offset + limit]
        return {
            "status": "completed",
            "client_id": client_id,
            "limit": limit,
            "offset": offset,
            "total": len(tasks),
            "sessions": [self._task_session_info_from_task(task) for task in page],
        }

    def run_task(self, task_id: str, reply_prompt: str | None = None) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        request = task["request"]
        queued_message = None
        if reply_prompt is None and task.get("thread_id"):
            queued_message = _next_queued_message(task.get("queued_messages") or [])
            if queued_message:
                reply_prompt = str(queued_message.get("prompt") or "")
                _mark_queued_message_status(
                    task.get("queued_messages") or [],
                    str(queued_message["message_uuid"]),
                    "running",
                )
                task = self.store.save_task(task)
                self.store.append_event(
                    task_id,
                    "task.message_dequeued",
                    queued_message,
                    actor="worker",
                    status="running",
                )
        prompt = reply_prompt or self._compose_prompt(request)
        self._record_hook(task_id, "before_task_run", {"resume": bool(reply_prompt)})
        task["status"] = "running"
        task["error"] = None
        task["lease"] = task.get("lease") or {
            "owner": f"inline_{uuid4().hex[:8]}",
            "epoch": 1,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        }
        task = self.store.save_task(task)
        self.store.append_event(task_id, "task.started", {"resume": bool(reply_prompt)}, actor="worker", status="running")
        result: dict[str, Any] = {}
        stage = "before_codex_exec"
        try:
            self._record_hook(task_id, "before_codex_exec", {"cwd": request["cwd"], "sandbox": request["sandbox"]})
            stage = "codex_exec"
            result = self.executor.execute(
                task_id=task_id,
                prompt=prompt,
                cwd=Path(request["cwd"]),
                sandbox=request["sandbox"],
                model=request.get("model"),
                thread_id=task.get("thread_id") if reply_prompt else None,
                on_event=lambda event: self.store.append_event(task_id, "codex.event", event),
            )
            stage = "after_codex_exec"
            self._record_hook(task_id, "after_codex_exec", {"returncode": result.get("returncode")})
            task["thread_id"] = result.get("thread_id") or task.get("thread_id")
            task["result"] = result
            task["status"] = "completed" if result["returncode"] == 0 else "failed"
            if queued_message:
                _mark_queued_message_status(
                    task.get("queued_messages") or [],
                    str(queued_message["message_uuid"]),
                    "completed" if result["returncode"] == 0 else "failed",
                )
            if task["status"] == "failed":
                task["error"] = {"code": "CODEX_EXEC_FAILED", "message": result.get("stderr") or "Codex failed"}
            if task["status"] == "completed":
                stage = "verification"
                verification_results = self._run_verification(task)
                task["verification_results"] = verification_results
                if any(item.get("status") == "failed" for item in verification_results):
                    task["status"] = "failed"
                    task["error"] = {
                        "code": "VERIFICATION_FAILED",
                        "message": "One or more verification commands failed.",
                        "verification_results": verification_results,
                    }
        except Exception as exc:
            message = _truncate_text(str(exc) or exc.__class__.__name__, 1000)
            code = "VERIFICATION_EXCEPTION" if stage == "verification" else "CODEX_EXEC_EXCEPTION"
            result = {
                "returncode": -1,
                "thread_id": task.get("thread_id"),
                "last_message": "",
                "stderr": message,
                "exception_type": exc.__class__.__name__,
                "stage": stage,
            }
            task["result"] = result
            task["status"] = "failed"
            task["error"] = {"code": code, "message": message, "exception_type": exc.__class__.__name__, "stage": stage}
            if queued_message:
                _mark_queued_message_status(
                    task.get("queued_messages") or [],
                    str(queued_message["message_uuid"]),
                    "failed",
                )
            if stage == "codex_exec":
                self._record_hook(
                    task_id,
                    "after_codex_exec",
                    {"returncode": -1, "error": task["error"]},
                    status="failed",
                )
        latest_task = self.store.get_task(task_id) or task
        preserved_stop_status = latest_task.get("status") in {"cancelled", "paused"}
        if preserved_stop_status:
            latest_task["thread_id"] = result.get("thread_id") or latest_task.get("thread_id")
            latest_task["result"] = result
            latest_task["lease"] = None
            task = latest_task
            self.store.append_event(
                task_id,
                "task.worker_result_ignored",
                {"preserved_status": task["status"], "worker_result": result},
                actor="worker",
                status=task["status"],
            )
        else:
            task["lease"] = None
            self.store.append_event(task_id, f"task.{task['status']}", result, actor="worker", status=task["status"])
        self._record_hook(task_id, "after_task_run", {"status": task["status"]})
        return self.store.save_task(task)

    def enqueue_task_message(
        self,
        task_id: str,
        record: dict[str, Any],
        *,
        expected_state_version: int | None = None,
    ) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        if expected_state_version is not None and task.get("state_version") != expected_state_version:
            raise StateConflictError(task_id, expected_state_version, task.get("state_version"))
        queued = task.setdefault("queued_messages", [])
        priority = str(record.get("priority") or "later")
        message = {
            "message_uuid": str(record.get("message_uuid") or f"msg_{uuid4().hex[:12]}"),
            "prompt": str(record.get("prompt") or ""),
            "priority": priority,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": record.get("metadata") or {},
        }
        queued.append(message)
        cancelled_process = False
        if priority == "now" and task.get("status") == "running":
            cancelled_process = bool(getattr(self.executor, "cancel", lambda _task_id: False)(task_id))
            message["interrupt_requested"] = True
            message["cancelled_process"] = cancelled_process
        task = self.store.save_task(task, expected_state_version=expected_state_version)
        self.store.append_event(
            task_id,
            "task.message_queued",
            message,
            actor="api",
            status="queued",
        )
        return {**message, "task_id": task_id, "state_version": task["state_version"]}

    def cancel_queued_message(
        self,
        task_id: str,
        message_uuid: str,
        *,
        expected_state_version: int | None = None,
    ) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        if expected_state_version is not None and task.get("state_version") != expected_state_version:
            raise StateConflictError(task_id, expected_state_version, task.get("state_version"))
        queued = task.get("queued_messages") or []
        target = None
        for item in queued:
            if item.get("message_uuid") == message_uuid:
                target = item
                break
        if not target:
            return {
                "task_id": task_id,
                "message_uuid": message_uuid,
                "cancelled": False,
                "reason": "not_found_or_already_dequeued",
                "state_version": task.get("state_version"),
            }
        if target.get("status") != "queued":
            return {
                "task_id": task_id,
                "message_uuid": message_uuid,
                "cancelled": False,
                "reason": f"message_status_{target.get('status')}",
                "state_version": task.get("state_version"),
            }
        target["status"] = "cancelled"
        target["cancelled_at"] = datetime.now(timezone.utc).isoformat()
        task = self.store.save_task(task, expected_state_version=expected_state_version)
        self.store.append_event(
            task_id,
            "task.message_cancelled",
            target,
            actor="api",
            status="cancelled",
        )
        return {
            "task_id": task_id,
            "message_uuid": message_uuid,
            "cancelled": True,
            "state_version": task["state_version"],
        }

    def worker_tick(self, owner: str | None = None, *, client_id: str | None = None) -> dict[str, Any]:
        owner = owner or f"worker_{uuid4().hex[:8]}"
        task = self.store.claim_next_task(owner, client_id=client_id)
        if not task:
            return {"status": "idle", "worker": owner}
        return {"status": "claimed", "worker": owner, "task": self.run_task(task["task_id"])}

    def worker_heartbeat(
        self,
        task_id: str,
        owner: str,
        lease_epoch: int,
        lease_seconds: int = 300,
        *,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        return self.store.heartbeat_task(
            task_id,
            owner,
            lease_epoch,
            lease_seconds=lease_seconds,
            client_id=client_id,
        )

    def create_loop(self, payload: dict[str, Any]) -> dict[str, Any]:
        next_run = datetime.now(timezone.utc)
        payload = dict(payload)
        payload["evidence_contract"] = _loop_evidence_contract(payload)
        payload.setdefault("iteration_records", [])
        return self.store.create_loop(payload, next_run.isoformat())

    def tick_loop(self, loop_id: str, force: bool = False) -> dict[str, Any]:
        loop = self.store.get_loop(loop_id)
        if not loop:
            raise KeyError(loop_id)
        if loop["status"] != "active":
            return loop
        due = datetime.fromisoformat(loop["next_run_at"]) <= datetime.now(timezone.utc)
        if not due and not force:
            return loop
        task = self.prepare_task(loop["task"])
        loop["iteration"] += 1
        loop["last_task_id"] = task["task_id"]
        loop.setdefault("iteration_records", []).append(
            {
                "iteration": loop["iteration"],
                "task_id": task["task_id"],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "evidence_refs": [
                    f"task:{task['task_id']}",
                    f"events:{task['task_id']}",
                    f"evidence:{task['task_id']}",
                ],
                "verification_commands": list(
                    (loop.get("evidence_contract") or {}).get("verification_commands") or []
                ),
            }
        )
        if loop["iteration"] >= loop["max_iterations"]:
            loop["status"] = "completed"
            loop["next_run_at"] = None
        else:
            loop["next_run_at"] = (
                datetime.now(timezone.utc) + timedelta(seconds=loop["interval_seconds"])
            ).isoformat()
        self.store.save_loop(loop)
        return {"loop": loop, "task": task}

    def loop_evidence(self, loop_id: str) -> dict[str, Any]:
        loop = self.store.get_loop(loop_id)
        if not loop:
            raise KeyError(loop_id)
        task_evidence = []
        for record in loop.get("iteration_records") or []:
            task_id = record.get("task_id")
            if not task_id:
                continue
            task = self.store.get_task(str(task_id))
            if not task:
                task_evidence.append(
                    {
                        "iteration": record.get("iteration"),
                        "task_id": task_id,
                        "status": "missing",
                        "evidence": {},
                    }
                )
                continue
            evidence = self.task_evidence(task["task_id"])
            task_evidence.append(
                {
                    "iteration": record.get("iteration"),
                    "task_id": task["task_id"],
                    "status": task.get("status"),
                    "state_version": task.get("state_version"),
                    "evidence_refs": record.get("evidence_refs") or [],
                    "verification_status": _verification_status(evidence.get("verification_results") or []),
                    "counts": {
                        "events": len(self.store.events(task["task_id"])),
                        "tool_calls": len(evidence.get("tool_calls") or []),
                        "observations": len(evidence.get("observations") or []),
                        "permission_decisions": len(evidence.get("permission_decisions") or []),
                        "artifacts": len(evidence.get("artifacts") or []),
                        "verification_results": len(evidence.get("verification_results") or []),
                        "hook_executions": len(evidence.get("hook_executions") or []),
                    },
                    "evidence": evidence,
                }
            )
        return {
            "loop": loop,
            "evidence_contract": loop.get("evidence_contract") or {},
            "iterations": task_evidence,
            "eval_cases": self.list_eval_cases(str(loop.get("client_id") or ""), limit=200)
            if loop.get("client_id")
            else [],
        }

    def _compose_prompt(self, request: dict[str, Any]) -> str:
        context = []
        if request.get("workspace_mode") == "client_isolated":
            context.append(
                "This run uses an isolated harness workspace. Treat the workspace AGENTS.md as the session instruction file; "
                "do not assume repository-level task memory from the source project."
            )
        if request.get("llm_provider"):
            context.append(
                "Target LLM provider profile for this agent loop: "
                + json.dumps(request["llm_provider"], ensure_ascii=False, sort_keys=True)
                + "."
            )
        agentic_loop_prompt = _agentic_loop_prompt(request)
        if agentic_loop_prompt:
            context.append(agentic_loop_prompt)
        if request.get("agent"):
            context.append(f"Use the project custom agent role `{request['agent']}`.")
        if request.get("skills"):
            context.append("Use these project skills when applicable: " + ", ".join(request["skills"]) + ".")
        if context:
            return "\n".join(context) + "\n\n" + request["prompt"]
        return request["prompt"]

    def _prepare_isolated_workspace(self, request: dict[str, Any]) -> Path:
        client_id = _safe_name(str(request.get("client_id") or "anonymous"))
        workspace_id = _safe_name(str(request.get("idempotency_key") or uuid4().hex[:12]))
        base = Path(os.getenv("LOCALAPPDATA") or os.getenv("TEMP") or self.root.parent)
        workspace = (base / "com2us_developer_harness" / "client_workspaces" / client_id / workspace_id).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        instructions = str(request.get("isolated_instructions") or _default_isolated_instructions(client_id))
        agents_path = workspace / "AGENTS.md"
        if not agents_path.exists() or agents_path.read_text(encoding="utf-8") != instructions:
            agents_path.write_text(instructions, encoding="utf-8")
        readme = workspace / "README.md"
        if not readme.exists():
            readme.write_text(
                "# Isolated Harness Workspace\n\n"
                "This directory is generated by `developer_harness` for one client-scoped delegated run.\n"
                "It intentionally does not reuse the source repository AGENTS.md as task memory.\n",
                encoding="utf-8",
            )
        request["workspace_id"] = workspace_id
        request["workspace_artifact_root"] = str(workspace.parent)
        request["source_cwd"] = request.get("cwd")
        request["instruction_scope"] = "client_isolated_workspace_agents_md"
        return workspace

    def command_task(self, task_id: str, command: str, *, expected_state_version: int | None = None) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        if expected_state_version is not None and task.get("state_version") != expected_state_version:
            raise StateConflictError(task_id, expected_state_version, task.get("state_version"))
        cancelled_process = False
        if command == "cancel":
            task["status"] = "cancelled"
            task["lease"] = None
        elif command == "pause":
            task["status"] = "paused"
            task["lease"] = None
        elif command == "resume":
            task["status"] = "queued"
            task["lease"] = None
        else:
            raise ValueError(command)
        task = self.store.save_task(task, expected_state_version=expected_state_version)
        if command in {"cancel", "pause"}:
            cancelled_process = bool(getattr(self.executor, "cancel", lambda _task_id: False)(task_id))
        event_type = {
            "cancel": "task.cancelled",
            "pause": "task.paused",
        }.get(command, f"task.{command}")
        self.store.append_event(
            task_id,
            event_type,
            {"command": command, "cancelled_process": cancelled_process},
            actor="api",
            status=task["status"],
        )
        return task

    def annotate_task(
        self,
        task_id: str,
        annotation: dict[str, Any],
        *,
        expected_state_version: int | None = None,
    ) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        if expected_state_version is not None and task.get("state_version") != expected_state_version:
            raise StateConflictError(task_id, expected_state_version, task.get("state_version"))
        metadata = dict(task.get("session_metadata") or {})
        for key in ("title", "tag", "summary"):
            if key in annotation and annotation.get(key) is not None:
                metadata[key] = str(annotation[key]).strip()
        metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
        task["session_metadata"] = metadata
        task = self.store.save_task(task, expected_state_version=expected_state_version)
        self.store.append_event(
            task_id,
            "task.session_metadata_updated",
            metadata,
            actor="api",
            status="completed",
        )
        return self._task_session_info_from_task(task)

    def fork_task(
        self,
        task_id: str,
        payload: dict[str, Any],
        *,
        client_id: str,
        expected_state_version: int | None = None,
    ) -> dict[str, Any]:
        source = self.store.get_task(task_id)
        if not source:
            raise KeyError(task_id)
        if source.get("client_id") != client_id:
            raise PermissionError("source task belongs to another harness client")
        if expected_state_version is not None and source.get("state_version") != expected_state_version:
            raise StateConflictError(task_id, expected_state_version, source.get("state_version"))
        request = dict(source.get("request") or {})
        request["client_id"] = client_id
        if payload.get("prompt") is not None:
            request["prompt"] = str(payload["prompt"])
        if payload.get("idempotency_key"):
            request["idempotency_key"] = payload["idempotency_key"]
        else:
            request.pop("idempotency_key", None)
        metadata = dict(request.get("metadata") or {})
        metadata.update(payload.get("metadata") or {})
        metadata["forked_from_task_id"] = task_id
        metadata["forked_from_thread_id"] = source.get("thread_id")
        request["metadata"] = metadata
        forked = self.prepare_task(request)
        forked["session_metadata"] = {
            "title": f"Fork of {task_id}",
            "summary": f"Forked from task {task_id}.",
            "forked_from_task_id": task_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        forked = self.store.save_task(forked)
        self.store.append_event(
            task_id,
            "task.forked",
            {"forked_task_id": forked["task_id"], "forked_from_task_id": task_id},
            actor="api",
            status="queued",
        )
        return forked

    def record_tool_call(self, task_id: str, record: dict[str, Any]) -> dict[str, Any]:
        if not self.store.get_task(task_id):
            raise KeyError(task_id)
        return self.store.record_tool_call(task_id, record)

    def record_observation(self, task_id: str, record: dict[str, Any]) -> dict[str, Any]:
        if not self.store.get_task(task_id):
            raise KeyError(task_id)
        return self.store.record_observation(task_id, record)

    def record_permission_decision(self, task_id: str, record: dict[str, Any]) -> dict[str, Any]:
        if not self.store.get_task(task_id):
            raise KeyError(task_id)
        return self.store.record_permission_decision(task_id, record)

    def record_task_progress(
        self,
        task_id: str,
        record: dict[str, Any],
        *,
        expected_state_version: int | None = None,
    ) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        if expected_state_version is not None and task.get("state_version") != expected_state_version:
            raise StateConflictError(task_id, expected_state_version, task.get("state_version"))
        return self.store.record_task_progress(task_id, record)

    def record_artifact(self, task_id: str, record: dict[str, Any]) -> dict[str, Any]:
        if not self.store.get_task(task_id):
            raise KeyError(task_id)
        return self.store.record_artifact(task_id, record)

    def task_evidence(self, task_id: str) -> dict[str, list[dict[str, Any]]]:
        if not self.store.get_task(task_id):
            raise KeyError(task_id)
        return self.store.task_evidence(task_id)

    def task_subagents(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        children = (self.store.task_evidence(task_id).get("subagents") or [])
        return {
            "status": "completed",
            "task_id": task_id,
            "join_policy": (task.get("request") or {}).get("join_policy"),
            "counts": _subagent_counts(children),
            "subagents": children,
        }

    def task_session_info(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        return {
            "status": "completed",
            "session": self._task_session_info_from_task(task),
        }

    def task_client_transcript(self, task_id: str, *, summary: bool = False) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        events = self.store.events(task_id)
        evidence = self.store.task_evidence(task_id)
        evidence_counts = {
            "events": len(events),
            "tool_calls": len(evidence.get("tool_calls") or []),
            "observations": len(evidence.get("observations") or []),
            "permission_decisions": len(evidence.get("permission_decisions") or []),
            "task_progress": len(evidence.get("task_progress") or []),
            "artifacts": len(evidence.get("artifacts") or []),
            "verification_results": len(evidence.get("verification_results") or []),
            "hook_executions": len(evidence.get("hook_executions") or []),
            "subagents": len(evidence.get("subagents") or []),
        }
        last_event = events[-1] if events else None
        request = task.get("request") if isinstance(task.get("request"), dict) else {}
        transcript = {
            "status": "completed",
            "task_id": task_id,
            "session_id": task.get("thread_id") or task_id,
            "source": "developer_harness_task_projection",
            "session_info": self._task_session_info_from_task(task),
            "status_line": {
                "task_status": task.get("status"),
                "state_version": task.get("state_version"),
                "client_id": task.get("client_id"),
                "model": request.get("model"),
                "agent": request.get("agent"),
                "sandbox": request.get("sandbox"),
                "workspace_mode": request.get("workspace_mode"),
                "cwd": request.get("cwd"),
                "lease": task.get("lease"),
                "returncode": (task.get("result") or {}).get("returncode")
                if isinstance(task.get("result"), dict)
                else None,
                "error": _task_error_payload(task),
                "last_event_id": last_event.get("event_id") if last_event else None,
                "last_event_seq": last_event.get("seq") if last_event else 0,
                "latest_event_type": last_event.get("event_type") if last_event else None,
                "counts": evidence_counts,
                "queued_messages": _queued_message_counts(task.get("queued_messages") or []),
                "subagents": _subagent_counts(evidence.get("subagents") or []),
                "task_progress": _task_progress_status(evidence.get("task_progress") or []),
                "verification_status": _verification_status(
                    evidence.get("verification_results") or []
                ),
            },
            "messages": _task_transcript_messages(task, events),
            "task_progress": _task_progress_projection(task_id, evidence.get("task_progress") or []),
            "queued_messages": [
                {
                    "uuid": item.get("message_uuid"),
                    "priority": item.get("priority"),
                    "status": item.get("status"),
                    "created_at": item.get("created_at"),
                    "cancelled_at": item.get("cancelled_at"),
                    "interrupt_requested": bool(item.get("interrupt_requested")),
                    "text": str(item.get("prompt") or ""),
                }
                for item in task.get("queued_messages", [])
            ],
            "tool_use_summary": _task_tool_use_summary(evidence),
            "tool_progress": _tool_progress_projection(task, evidence.get("tool_calls") or []),
            "agent_phases": _agent_phase_projection(task, evidence.get("observations") or []),
            "agent_sessions": _agent_session_projection(task, evidence.get("observations") or []),
            "agent_stream_events": _agent_stream_event_projection(task, evidence.get("observations") or []),
            "agent_model_responses": _agent_model_response_projection(
                task,
                evidence.get("observations") or [],
                evidence.get("artifacts") or [],
            ),
            "agent_handoffs": _agent_handoff_projection(task, evidence.get("observations") or []),
            "agent_approvals": _agent_approval_projection(task, evidence.get("observations") or []),
            "agent_questions": _agent_question_projection(task, evidence.get("observations") or []),
            "agent_guidance": _agent_guidance_projection(task, evidence.get("observations") or []),
            "agent_eval_case_verifications": _agent_eval_case_verification_projection(
                task,
                evidence.get("observations") or [],
            ),
            "agent_guardrails": _agent_guardrail_projection(task, evidence.get("observations") or []),
            "agent_run_errors": _agent_run_error_projection(task, evidence.get("observations") or []),
            "agent_final_outputs": _agent_final_output_projection(task, evidence.get("observations") or []),
            "hook_progress": [
                {
                    "uuid": f"{task_id}:hook:{item.get('hook_id')}",
                    "event_name": item.get("event_name"),
                    "status": item.get("status"),
                    "created_at": item.get("created_at"),
                }
                for item in evidence.get("hook_executions", [])
            ],
            "permission_decisions": [
                {
                    "uuid": f"{task_id}:permission:{item.get('decision_id')}",
                    "decision_id": item.get("decision_id"),
                    "call_id": item.get("call_id"),
                    "tool_name": item.get("tool_name"),
                    "behavior": item.get("behavior"),
                    "status": item.get("status"),
                    "message": item.get("message"),
                    "blocked_path": item.get("blocked_path"),
                    "decision_reason": item.get("decision_reason"),
                    "created_at": item.get("created_at"),
                }
                for item in evidence.get("permission_decisions", [])
            ],
            "verification_summary": [
                {
                    "command": item.get("command"),
                    "status": item.get("status"),
                    "returncode": item.get("returncode"),
                    "duration_ms": item.get("duration_ms"),
                }
                for item in evidence.get("verification_results", [])
            ],
            "harness_verification_results": _verification_result_projection(
                task,
                evidence.get("verification_results") or [],
            ),
            "subagents": evidence.get("subagents") or [],
            "source_endpoints": {
                "task": f"/api/harness/v1/tasks/{task_id}",
                "events": f"/api/harness/v1/tasks/{task_id}/events",
                "evidence": f"/api/harness/v1/tasks/{task_id}/evidence",
                "client_transcript": f"/api/harness/v1/tasks/{task_id}/client-transcript",
                "sdk_events": f"/api/harness/v1/tasks/{task_id}/sdk-events",
                "subagents": f"/api/harness/v1/tasks/{task_id}/subagents",
                "task_progress": f"/api/harness/v1/tasks/{task_id}/progress",
            },
        }
        if summary:
            return summarize_client_transcript(transcript, task_id=task_id)
        return transcript

    def task_sdk_events(self, task_id: str, *, after: int = 0, summary: bool = False) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        after = max(0, int(after or 0))
        all_events = self.store.events(task_id, 0)
        events = [item for item in all_events if int(item.get("event_id") or 0) > after]
        evidence = self.store.task_evidence(task_id)
        projected = _sdk_event_projection(task, events, evidence.get("artifacts") or [])
        latest_event_id = max([int(item.get("event_id") or 0) for item in events] or [after])
        terminal_event_id = _terminal_event_id(all_events)
        sdk_events = {
            "status": "completed",
            "task_id": task_id,
            "session_id": task.get("thread_id") or task_id,
            "source": "developer_harness_sdk_event_projection",
            "after": after,
            "events": projected,
            "raw_event_count": len(events),
            "projected_event_count": len(projected),
            "latest_source_event_id": latest_event_id if events else None,
            "next_after": latest_event_id,
            "cursor_policy": {
                "cursor_field": "after",
                "cursor_basis": "raw_harness_event_id",
                "cursor_semantics": "exclusive",
                "next_after": latest_event_id,
            },
            "terminal_event_id": terminal_event_id,
            "terminal_event_included": bool(
                terminal_event_id
                and any(int(item.get("source_event_id") or 0) == terminal_event_id for item in projected)
            ),
            "final_output_included": any(item.get("type") == "final_output" for item in projected),
        }
        if summary:
            return summarize_sdk_events(sdk_events, task_id=task_id)
        return sdk_events

    def _task_session_info_from_task(self, task: dict[str, Any]) -> dict[str, Any]:
        request = task.get("request") if isinstance(task.get("request"), dict) else {}
        result = task.get("result") if isinstance(task.get("result"), dict) else {}
        session_metadata = task.get("session_metadata") if isinstance(task.get("session_metadata"), dict) else {}
        evidence = self.store.task_evidence(task["task_id"])
        prompt = str(request.get("prompt") or "")
        fallback_summary = str(result.get("last_message") or prompt)
        title = session_metadata.get("title") or request.get("agent") or _truncate_text(prompt, 80) or task["task_id"]
        return {
            "task_id": task.get("task_id"),
            "session_id": task.get("thread_id") or task.get("task_id"),
            "thread_id": task.get("thread_id"),
            "client_id": task.get("client_id"),
            "status": task.get("status"),
            "state_version": task.get("state_version"),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "cwd": request.get("cwd"),
            "workspace_mode": request.get("workspace_mode"),
            "agent": request.get("agent"),
            "model": request.get("model"),
            "summary": session_metadata.get("summary") or _truncate_text(fallback_summary, 500),
            "returncode": result.get("returncode"),
            "error": _task_error_payload(task),
            "custom_title": session_metadata.get("title"),
            "title": title,
            "tag": session_metadata.get("tag"),
            "forked_from_task_id": (request.get("metadata") or {}).get("forked_from_task_id")
            if isinstance(request.get("metadata"), dict)
            else None,
            "counts": {
                "events": len(self.store.events(task["task_id"])),
                "tool_calls": len(evidence.get("tool_calls") or []),
                "observations": len(evidence.get("observations") or []),
                "permission_decisions": len(evidence.get("permission_decisions") or []),
                "artifacts": len(evidence.get("artifacts") or []),
                "verification_results": len(evidence.get("verification_results") or []),
                "subagents": len(evidence.get("subagents") or []),
            },
        }

    def record_eval_case(self, client_id: str, record: dict[str, Any]) -> dict[str, Any]:
        source_task_id = record.get("source_task_id")
        if source_task_id:
            task = self.store.get_task(str(source_task_id))
            if not task:
                raise KeyError(str(source_task_id))
            if task.get("client_id") != client_id:
                raise PermissionError("source task belongs to another harness client")
        return self.store.record_eval_case(client_id, record)

    def list_eval_cases(self, client_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_eval_cases(client_id, limit=limit)

    def _record_hook(
        self,
        task_id: str,
        event_name: str,
        payload: dict[str, Any],
        *,
        status: str = "completed",
    ) -> None:
        self.store.record_hook(task_id, event_name, status, payload)
        self.store.append_event(task_id, f"hook.{event_name}", payload, actor="hook", status=status)

    def _run_verification(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        spec = (task.get("request") or {}).get("verification") or {}
        commands = [str(item) for item in spec.get("commands") or [] if str(item).strip()]
        timeout = int(spec.get("timeout_seconds") or 120)
        results: list[dict[str, Any]] = []
        for command in commands:
            executed_command = _portable_verification_command(command)
            started = perf_counter()
            try:
                completed = subprocess.run(
                    executed_command,
                    cwd=Path(task["request"]["cwd"]),
                    shell=True,
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                )
                result = {
                    "command": command,
                    "executed_command": executed_command,
                    "status": "completed" if completed.returncode == 0 else "failed",
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                    "duration_ms": round((perf_counter() - started) * 1000),
                }
            except subprocess.TimeoutExpired as exc:
                result = {
                    "command": command,
                    "executed_command": executed_command,
                    "status": "failed",
                    "returncode": None,
                    "stdout": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
                    "stderr": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
                    "duration_ms": round((perf_counter() - started) * 1000),
                    "error": {"code": "VERIFICATION_TIMEOUT", "timeout_seconds": timeout},
                }
            self.store.record_verification(task["task_id"], result)
            results.append(result)
        return results


def _portable_verification_command(command: str) -> str:
    stripped = command.strip()
    if stripped == "python" or stripped.startswith("python "):
        if shutil.which("python"):
            return command
        executable = _shell_quote(sys.executable)
        return f"{executable}{stripped[len('python'):]}"
    return command


def _shell_quote(value: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)


def _models_preview(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    result = []
    for item in data[:10]:
        if isinstance(item, dict) and item.get("id"):
            result.append(str(item["id"]))
    return result


def _provider_probe_summary(
    provider: dict[str, Any],
    *,
    endpoint: str | None,
    request_attempted: bool,
    http_status: int | None = None,
) -> dict[str, Any]:
    response_received = isinstance(http_status, int) and http_status > 0
    return {
        "provider_name": provider.get("provider"),
        "api_style": provider.get("api_style"),
        "model": provider.get("model"),
        "base_url": provider.get("base_url"),
        "api_key_env": provider.get("api_key_env"),
        "endpoint": endpoint,
        "provider_request_attempted": bool(request_attempted),
        "provider_response_received": response_received,
        "external_api_called": response_received,
    }


def _agent_sdk_fit(provider: dict[str, Any]) -> dict[str, Any]:
    api_style = provider.get("api_style")
    return {
        "can_use_openai_agents_sdk_model_layer": api_style in {"responses", "chat_completions"},
        "requires_backend_owned_tool_execution": True,
        "local_mode_requires_openai_compatible_tool_calls": api_style == "chat_completions",
        "stateful_response_ids_assumed": bool(provider.get("supports_stateful_responses")),
    }


def _empty_api_usage_summary() -> dict[str, Any]:
    return {
        "source": "stored_agent_model_response_observations",
        "responseCount": 0,
        "requestCount": 0,
        "taskCount": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "totalTokens": 0,
        "cachedTokens": 0,
        "taskIds": [],
        "taskUsage": {},
        "modelUsage": {},
        "statusUsage": {},
        "providerUsage": {},
        "apiStyleUsage": {},
        "modelCounts": {},
        "statusCounts": {},
        "providerCounts": {},
        "apiStyleCounts": {},
        "evidenceRefCount": 0,
        "responses": [],
    }


def _add_model_response_usage(
    summary: dict[str, Any],
    task: dict[str, Any],
    observations: list[dict[str, Any]],
    artifacts: list[dict[str, Any]] | None = None,
) -> None:
    for observation in observations:
        if observation.get("tool_name") != "agent_model_response":
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        if not usage:
            continue
        input_tokens = _usage_int(usage, "input_tokens", "inputTokens", "prompt_tokens", "promptTokens")
        output_tokens = _usage_int(
            usage,
            "output_tokens",
            "outputTokens",
            "completion_tokens",
            "completionTokens",
        )
        total_tokens = _usage_int(usage, "total_tokens", "totalTokens")
        if total_tokens == 0 and (input_tokens or output_tokens):
            total_tokens = input_tokens + output_tokens
        request_count = _usage_int(usage, "requests", "request_count", "requestCount") or 1
        cached_tokens = _usage_int(usage, "cached_tokens", "cachedTokens")
        details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached_tokens += _usage_int(details, "cached_tokens", "cachedTokens")
        task_id = str(task.get("task_id") or "")
        if task_id and task_id not in summary["taskIds"]:
            summary["taskIds"].append(task_id)
        if task_id:
            task_usage = summary.setdefault("taskUsage", {}).setdefault(
                task_id,
                {
                    "responseCount": 0,
                    "requestCount": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "totalTokens": 0,
                    "cachedTokens": 0,
                    "evidenceRefCount": 0,
                    "modelCounts": {},
                    "statusCounts": {},
                },
            )
        else:
            task_usage = None
        model = str(result.get("model") or "unknown")
        trace_metadata = _trace_artifact_metadata_for_observation(observation, artifacts or [])
        provider = str(result.get("provider") or trace_metadata.get("provider") or "unknown")
        api_style = str(
            result.get("api_style")
            or result.get("apiStyle")
            or trace_metadata.get("api_style")
            or trace_metadata.get("apiStyle")
            or "unknown"
        )
        summary["modelCounts"][model] = int(summary["modelCounts"].get(model) or 0) + 1
        status = str(observation.get("status") or "unknown")
        summary["statusCounts"][status] = int(summary["statusCounts"].get(status) or 0) + 1
        summary["providerCounts"][provider] = int(summary["providerCounts"].get(provider) or 0) + 1
        summary["apiStyleCounts"][api_style] = int(summary["apiStyleCounts"].get(api_style) or 0) + 1
        model_usage = summary.setdefault("modelUsage", {}).setdefault(
            model,
            _empty_usage_bucket(
                include_status_counts=True,
                include_provider_counts=True,
                include_api_style_counts=True,
            ),
        )
        status_usage = summary.setdefault("statusUsage", {}).setdefault(
            status,
            _empty_usage_bucket(
                include_model_counts=True,
                include_provider_counts=True,
                include_api_style_counts=True,
            ),
        )
        provider_usage = summary.setdefault("providerUsage", {}).setdefault(
            provider,
            _empty_usage_bucket(
                include_model_counts=True,
                include_status_counts=True,
                include_api_style_counts=True,
            ),
        )
        api_style_usage = summary.setdefault("apiStyleUsage", {}).setdefault(
            api_style,
            _empty_usage_bucket(
                include_model_counts=True,
                include_status_counts=True,
                include_provider_counts=True,
            ),
        )
        evidence_refs = observation.get("evidence_refs") or []
        if not isinstance(evidence_refs, list):
            evidence_refs = []
        evidence_ref_count = len(evidence_refs)
        summary["responseCount"] += 1
        summary["requestCount"] += request_count
        summary["inputTokens"] += input_tokens
        summary["outputTokens"] += output_tokens
        summary["totalTokens"] += total_tokens
        summary["cachedTokens"] += cached_tokens
        summary["evidenceRefCount"] += evidence_ref_count
        if task_usage is not None:
            _add_usage_bucket_values(
                task_usage,
                task_id=task_id,
                model=model,
                status=status,
                provider=provider,
                api_style=api_style,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                request_count=request_count,
                cached_tokens=cached_tokens,
                evidence_ref_count=evidence_ref_count,
                include_task_id=False,
            )
        _add_usage_bucket_values(
            model_usage,
            task_id=task_id,
            model=model,
            status=status,
            provider=provider,
            api_style=api_style,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            request_count=request_count,
            cached_tokens=cached_tokens,
            evidence_ref_count=evidence_ref_count,
        )
        _add_usage_bucket_values(
            status_usage,
            task_id=task_id,
            model=model,
            status=status,
            provider=provider,
            api_style=api_style,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            request_count=request_count,
            cached_tokens=cached_tokens,
            evidence_ref_count=evidence_ref_count,
        )
        _add_usage_bucket_values(
            provider_usage,
            task_id=task_id,
            model=model,
            status=status,
            provider=provider,
            api_style=api_style,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            request_count=request_count,
            cached_tokens=cached_tokens,
            evidence_ref_count=evidence_ref_count,
        )
        _add_usage_bucket_values(
            api_style_usage,
            task_id=task_id,
            model=model,
            status=status,
            provider=provider,
            api_style=api_style,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            request_count=request_count,
            cached_tokens=cached_tokens,
            evidence_ref_count=evidence_ref_count,
        )
        summary["responses"].append(
            {
                "task_id": task.get("task_id"),
                "observation_id": observation.get("observation_id"),
                "response_id": result.get("response_id"),
                "model": result.get("model"),
                "model_source": result.get("model_source"),
                "provider": provider,
                "api_style": api_style,
                "status": observation.get("status"),
                "request_count": request_count,
                "usage": usage,
                "evidence_refs": evidence_refs,
            }
        )


def _finalize_api_usage_summary(summary: dict[str, Any]) -> dict[str, Any] | None:
    if int(summary.get("responseCount") or 0) <= 0:
        return None
    task_usage = summary.get("taskUsage") if isinstance(summary.get("taskUsage"), dict) else {}
    model_usage = summary.get("modelUsage") if isinstance(summary.get("modelUsage"), dict) else {}
    status_usage = summary.get("statusUsage") if isinstance(summary.get("statusUsage"), dict) else {}
    provider_usage = summary.get("providerUsage") if isinstance(summary.get("providerUsage"), dict) else {}
    api_style_usage = summary.get("apiStyleUsage") if isinstance(summary.get("apiStyleUsage"), dict) else {}
    sorted_task_usage = {key: _normalized_usage_bucket(task_usage[key]) for key in sorted(task_usage)}
    sorted_model_usage = {key: _normalized_usage_bucket(model_usage[key]) for key in sorted(model_usage)}
    sorted_status_usage = {key: _normalized_usage_bucket(status_usage[key]) for key in sorted(status_usage)}
    sorted_provider_usage = {key: _normalized_usage_bucket(provider_usage[key]) for key in sorted(provider_usage)}
    sorted_api_style_usage = {key: _normalized_usage_bucket(api_style_usage[key]) for key in sorted(api_style_usage)}
    return {
        **summary,
        "taskCount": len(sorted_task_usage),
        "taskUsage": sorted_task_usage,
        "modelUsage": sorted_model_usage,
        "statusUsage": sorted_status_usage,
        "providerUsage": sorted_provider_usage,
        "apiStyleUsage": sorted_api_style_usage,
        "models": sorted(summary.get("modelCounts") or {}),
        "providers": sorted(summary.get("providerCounts") or {}),
        "apiStyles": sorted(summary.get("apiStyleCounts") or {}),
        "claim_boundary": (
            "Usage is aggregated from stored agent_model_response observations. "
            "It is not proof that a live provider is currently connected."
        ),
    }


def _empty_usage_bucket(
    *,
    include_model_counts: bool = False,
    include_status_counts: bool = False,
    include_provider_counts: bool = False,
    include_api_style_counts: bool = False,
) -> dict[str, Any]:
    bucket: dict[str, Any] = {
        "responseCount": 0,
        "requestCount": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "totalTokens": 0,
        "cachedTokens": 0,
        "evidenceRefCount": 0,
        "taskIds": [],
    }
    if include_model_counts:
        bucket["modelCounts"] = {}
    if include_status_counts:
        bucket["statusCounts"] = {}
    if include_provider_counts:
        bucket["providerCounts"] = {}
    if include_api_style_counts:
        bucket["apiStyleCounts"] = {}
    return bucket


def _add_usage_bucket_values(
    bucket: dict[str, Any],
    *,
    task_id: str,
    model: str,
    status: str,
    provider: str,
    api_style: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    request_count: int,
    cached_tokens: int,
    evidence_ref_count: int,
    include_task_id: bool = True,
) -> None:
    bucket["responseCount"] = int(bucket.get("responseCount") or 0) + 1
    bucket["requestCount"] = int(bucket.get("requestCount") or 0) + request_count
    bucket["inputTokens"] = int(bucket.get("inputTokens") or 0) + input_tokens
    bucket["outputTokens"] = int(bucket.get("outputTokens") or 0) + output_tokens
    bucket["totalTokens"] = int(bucket.get("totalTokens") or 0) + total_tokens
    bucket["cachedTokens"] = int(bucket.get("cachedTokens") or 0) + cached_tokens
    bucket["evidenceRefCount"] = int(bucket.get("evidenceRefCount") or 0) + evidence_ref_count
    task_ids = bucket.setdefault("taskIds", []) if include_task_id else []
    if include_task_id and task_id and isinstance(task_ids, list) and task_id not in task_ids:
        task_ids.append(task_id)
    model_counts = bucket.get("modelCounts")
    if isinstance(model_counts, dict):
        model_counts[model] = int(model_counts.get(model) or 0) + 1
    status_counts = bucket.get("statusCounts")
    if isinstance(status_counts, dict):
        status_counts[status] = int(status_counts.get(status) or 0) + 1
    provider_counts = bucket.get("providerCounts")
    if isinstance(provider_counts, dict):
        provider_counts[provider] = int(provider_counts.get(provider) or 0) + 1
    api_style_counts = bucket.get("apiStyleCounts")
    if isinstance(api_style_counts, dict):
        api_style_counts[api_style] = int(api_style_counts.get(api_style) or 0) + 1


def _normalized_usage_bucket(bucket: Any) -> dict[str, Any]:
    if not isinstance(bucket, dict):
        return {}
    normalized = dict(bucket)
    task_ids = normalized.get("taskIds")
    if isinstance(task_ids, list):
        normalized["taskIds"] = sorted(str(item) for item in task_ids if item)
    for key in ("modelCounts", "statusCounts", "providerCounts", "apiStyleCounts"):
        values = normalized.get(key)
        if isinstance(values, dict):
            normalized[key] = {name: values[name] for name in sorted(values)}
    return normalized


def _trace_artifact_metadata_for_observation(
    observation: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    trace_artifacts = [artifact for artifact in artifacts if artifact.get("kind") == "agent_trace"]
    if not trace_artifacts:
        return {}
    evidence_refs = observation.get("evidence_refs") or []
    if not isinstance(evidence_refs, list):
        evidence_refs = []
    ref_names = {
        str(ref).split("artifact:agent_trace:", 1)[1]
        for ref in evidence_refs
        if isinstance(ref, str) and ref.startswith("artifact:agent_trace:")
    }
    if ref_names:
        for artifact in trace_artifacts:
            path = str(artifact.get("path") or "")
            if Path(path).name in ref_names:
                metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
                return metadata
    if len(trace_artifacts) == 1:
        metadata = trace_artifacts[0].get("metadata") if isinstance(trace_artifacts[0].get("metadata"), dict) else {}
        return metadata
    return {}


def _usage_int(value: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key not in value:
            continue
        try:
            return max(0, int(value.get(key) or 0))
        except (TypeError, ValueError):
            continue
    return 0


def _agentic_loop_prompt(request: dict[str, Any]) -> str | None:
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    template = metadata.get("agentic_loop_template") or request.get("agentic_loop_template")
    loop = metadata.get("agentic_loop") if isinstance(metadata.get("agentic_loop"), dict) else {}
    if not template and not loop:
        return None
    return (
        "Agentic loop harness mode: "
        + json.dumps(
            {
                "template": template or "custom",
                "trace_sources": loop.get("trace_sources", []),
                "eval_targets": loop.get("eval_targets", []),
                "verification_commands": loop.get("verification_commands", []),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
        "Run this as an evidence loop: inspect current trace/store/API evidence, identify the smallest agent or harness gap, "
        "turn that gap into a verifier or eval when possible, patch the runtime or harness, rerun the named verification commands, "
        "and report only evidence-backed claims. Treat mock, cached, fixture, and deterministic fallback behavior as harness evidence, "
        "not proof that a production AI provider is connected."
    )


def _loop_evidence_contract(payload: dict[str, Any]) -> dict[str, Any]:
    explicit = payload.get("evidence_contract") if isinstance(payload.get("evidence_contract"), dict) else {}
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    agentic_loop = metadata.get("agentic_loop") if isinstance(metadata.get("agentic_loop"), dict) else {}
    verification = task.get("verification") if isinstance(task.get("verification"), dict) else {}
    verification_commands = _string_list(
        explicit.get("verification_commands")
        or agentic_loop.get("verification_commands")
        or verification.get("commands")
    )
    return {
        "trace_sources": _string_list(explicit.get("trace_sources") or agentic_loop.get("trace_sources")),
        "eval_targets": _string_list(explicit.get("eval_targets") or agentic_loop.get("eval_targets")),
        "verification_commands": verification_commands,
        "required_iteration_evidence": [
            "task_events",
            "task_evidence",
            "verification_results",
        ],
        "termination_conditions": _string_list(explicit.get("termination_conditions"))
        or [
            "max_iterations",
            "manual_pause_or_cancel",
            "verification_failure_blocks_completion_claim",
        ],
        "promotion_rule": "repeatable_trace_or_runtime_gap_should_be_recorded_as_eval_case",
        "claim_boundary": "loop_results_are_harness_evidence_until_provider_probe_or_live_verifier_proves_connectivity",
    }


def _verification_status(results: list[dict[str, Any]]) -> str:
    if not results:
        return "not_run"
    if any(item.get("status") == "failed" for item in results):
        return "failed"
    if all(item.get("status") == "completed" for item in results):
        return "completed"
    return "unknown"


def _read_model_contract() -> dict[str, Any]:
    return {
        "source": "developer_harness_read_model_contract",
        "endpoints": {
            "sdk_events": "/api/harness/v1/tasks/{task_id}/sdk-events",
            "sdk_events_summary": "/api/harness/v1/tasks/{task_id}/sdk-events?summary=true",
            "client_transcript": "/api/harness/v1/tasks/{task_id}/client-transcript",
            "client_transcript_summary": "/api/harness/v1/tasks/{task_id}/client-transcript?summary=true",
        },
        "cursor": {
            "field": "after",
            "basis": "raw_harness_event_id",
            "semantics": "exclusive",
        },
        "sdk_event_types": [
            "system",
            "tool_progress",
            "task_control",
            "permission_decision",
            "agent_model_response",
            "agent_phase",
            "agent_session",
            "agent_stream_event",
            "agent_handoff",
            "agent_approval",
            "agent_question",
            "agent_guidance",
            "agent_eval_case_verification",
            "agent_guardrail",
            "agent_run_error",
            "harness_verification_result",
            "final_output",
        ],
        "client_transcript_sections": [
            "messages",
            "task_progress",
            "queued_messages",
            "tool_progress",
            "agent_phases",
            "agent_sessions",
            "agent_stream_events",
            "agent_model_responses",
            "agent_handoffs",
            "agent_approvals",
            "agent_questions",
            "agent_guidance",
            "agent_eval_case_verifications",
            "agent_guardrails",
            "agent_run_errors",
            "agent_final_outputs",
            "harness_verification_results",
            "permission_decisions",
            "verification_summary",
            "subagents",
        ],
        "summary_fields": [
            "tool_progress_count",
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
            "missing_info_question_count",
            "agent_guidance_count",
            "agent_guidance_reason_counts",
            "agent_eval_case_verification_count",
            "agent_eval_case_verification_failed_count",
            "agent_eval_case_failure_label_counts",
            "agent_guardrail_count",
            "agent_run_error_count",
            "harness_verification_result_count",
            "harness_verification_failed_count",
            "harness_verification_status_counts",
            "final_output_count",
        ],
        "claim_boundary": (
            "Read models and summaries are projections over stored harness evidence; they do not prove "
            "current provider connectivity."
        ),
    }


def _verification_result_projection(
    task: dict[str, Any],
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    session_id = task.get("thread_id") or task["task_id"]
    projected = []
    for index, result in enumerate(results, start=1):
        if not isinstance(result, dict):
            continue
        projected.append(
            {
                "type": "harness_verification_result",
                "subtype": result.get("status") or "unknown",
                "uuid": f"{task['task_id']}:verification:{index}",
                "session_id": session_id,
                "task_id": task["task_id"],
                "verification_index": index,
                "command": result.get("command"),
                "executed_command": result.get("executed_command"),
                "status": result.get("status"),
                "returncode": result.get("returncode"),
                "duration_ms": result.get("duration_ms"),
                "stdout_tail": _truncate_text(str(result.get("stdout") or ""), 1200),
                "stderr_tail": _truncate_text(str(result.get("stderr") or ""), 1200),
                "error": result.get("error") if isinstance(result.get("error"), dict) else None,
                "summary": _verification_result_summary(result),
                "result": result,
                "claim_boundary": (
                    "Stored harness verification evidence only; this transcript record does not prove "
                    "current provider connectivity."
                ),
            }
        )
    return projected


def _verification_result_sdk_event(
    task: dict[str, Any],
    payload: dict[str, Any],
    base: dict[str, Any],
    *,
    event_status: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not payload:
        return None
    return {
        **base,
        "type": "harness_verification_result",
        "subtype": payload.get("status") or event_status or "unknown",
        "task_id": task["task_id"],
        "command": payload.get("command"),
        "executed_command": payload.get("executed_command"),
        "status": payload.get("status") or event_status,
        "returncode": payload.get("returncode"),
        "duration_ms": payload.get("duration_ms"),
        "stdout_tail": _truncate_text(str(payload.get("stdout") or ""), 1200),
        "stderr_tail": _truncate_text(str(payload.get("stderr") or ""), 1200),
        "error": payload.get("error") if isinstance(payload.get("error"), dict) else None,
        "summary": _verification_result_summary(payload),
        "result": payload,
        "claim_boundary": (
            "Stored harness verification evidence only; this event does not prove current provider connectivity."
        ),
    }


def _verification_result_summary(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "unknown").strip()
    command = _truncate_text(str(result.get("command") or "verification command"), 160)
    returncode = result.get("returncode")
    if returncode is None:
        return f"verification {status}: {command}."
    return f"verification {status}: {command} (rc={returncode})."


def _task_error_payload(task: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any] | None:
    error = task.get("error") if isinstance(task.get("error"), dict) else None
    result = result if isinstance(result, dict) else task.get("result") if isinstance(task.get("result"), dict) else {}
    if not error and not result.get("stderr"):
        return None
    payload: dict[str, Any] = {}
    if error:
        payload.update({key: value for key, value in error.items() if value is not None})
    if result.get("stderr") and not payload.get("message"):
        payload["message"] = str(result.get("stderr"))
    if result.get("exception_type") and not payload.get("exception_type"):
        payload["exception_type"] = result.get("exception_type")
    if result.get("stage") and not payload.get("stage"):
        payload["stage"] = result.get("stage")
    if result.get("returncode") is not None:
        payload["returncode"] = result.get("returncode")
    return payload or None


def _task_terminal_summary(task: dict[str, Any], result: dict[str, Any], status: str) -> str:
    last_message = str(result.get("last_message") or "").strip()
    if last_message:
        return _truncate_text(last_message, 500)
    error = _task_error_payload(task, result)
    if error:
        code = str(error.get("code") or status).strip()
        message = str(error.get("message") or "").strip()
        summary = f"{code}: {message}" if message else code
        return _truncate_text(summary, 500)
    if result.get("stderr"):
        return _truncate_text(str(result.get("stderr")), 500)
    return _truncate_text(f"Task {status}", 500)


def _task_transcript_messages(task: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    request = task.get("request") if isinstance(task.get("request"), dict) else {}
    messages = [
        {
            "uuid": f"{task['task_id']}:request",
            "session_id": task.get("thread_id") or task["task_id"],
            "role": "user",
            "type": "user_message",
            "text": str(request.get("prompt") or ""),
            "created_at": task.get("created_at"),
        }
    ]
    for event in events:
        if event.get("event_type") != "codex.event":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        text = _codex_event_text(payload)
        if not text:
            continue
        messages.append(
            {
                "uuid": f"{task['task_id']}:event:{event['event_id']}",
                "session_id": task.get("thread_id") or task["task_id"],
                "role": "assistant",
                "type": "streamlined_text",
                "text": text,
                "event_id": event.get("event_id"),
                "event_seq": event.get("seq"),
                "created_at": event.get("created_at"),
            }
        )
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    last_message = str(result.get("last_message") or "").strip()
    if last_message and not any(item.get("text") == last_message for item in messages):
        messages.append(
            {
                "uuid": f"{task['task_id']}:result",
                "session_id": task.get("thread_id") or task["task_id"],
                "role": "assistant",
                "type": "streamlined_text",
                "text": last_message,
                "created_at": task.get("updated_at"),
            }
        )
    return messages


def _codex_event_text(payload: dict[str, Any]) -> str:
    if payload.get("type") == "agent_message":
        return str(payload.get("text") or "").strip()
    if payload.get("type") == "item.completed":
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        return str(item.get("text") or payload.get("text") or "").strip()
    return ""


def _task_tool_use_summary(evidence: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    tool_calls = evidence.get("tool_calls") or []
    observations = evidence.get("observations") or []
    observations_by_call = {
        item.get("call_id"): item for item in observations if item.get("call_id")
    }
    items = []
    for call in tool_calls:
        observation = observations_by_call.get(call.get("call_id"))
        status = (observation or call).get("status")
        items.append(
            {
                "call_id": call.get("call_id"),
                "tool_name": call.get("tool_name"),
                "status": status,
                "has_observation": bool(observation),
                "evidence_refs": observation.get("evidence_refs", []) if observation else [],
            }
        )
    summary = "No tool calls recorded."
    if items:
        completed = sum(1 for item in items if item.get("status") == "completed")
        failed = sum(1 for item in items if item.get("status") == "failed")
        names = ", ".join(str(item.get("tool_name")) for item in items[:3])
        extra = "" if len(items) <= 3 else f", +{len(items) - 3} more"
        summary = f"{len(items)} tool call(s): {completed} completed, {failed} failed ({names}{extra})."
    return {
        "summary": summary,
        "tool_summary": summary,
        "items": items,
    }


def _tool_progress_projection(task: dict[str, Any], tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    session_id = task.get("thread_id") or task["task_id"]
    now_dt = datetime.now(timezone.utc)
    projected = []
    for call in tool_calls:
        metadata = call.get("metadata") if isinstance(call.get("metadata"), dict) else {}
        start = _parse_iso_datetime(call.get("started_at") or call.get("created_at"))
        end = _parse_iso_datetime(call.get("completed_at")) or now_dt
        elapsed = max(0, int(((end - start).total_seconds() if start else 0)))
        projected.append(
            {
                "type": "tool_progress",
                "tool_use_id": call.get("call_id"),
                "tool_name": call.get("tool_name"),
                "parent_tool_use_id": metadata.get("parent_tool_use_id"),
                "elapsed_time_seconds": elapsed,
                "task_id": task["task_id"],
                "status": call.get("status"),
                "uuid": f"{task['task_id']}:tool_progress:{call.get('call_pk') or call.get('call_id')}",
                "session_id": session_id,
                "started_at": call.get("started_at"),
                "completed_at": call.get("completed_at"),
            }
        )
    return projected


def _agent_handoff_projection(task: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    session_id = task.get("thread_id") or task["task_id"]
    projected = []
    for observation in observations:
        if observation.get("tool_name") != "agent_handoff":
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        projected.append(
            {
                "type": "agent_handoff",
                "uuid": f"{task['task_id']}:handoff:{observation.get('observation_id')}",
                "session_id": session_id,
                "task_id": task["task_id"],
                "observation_id": observation.get("observation_id"),
                "tool_use_id": observation.get("call_id"),
                "item_type": result.get("item_type"),
                "tool_name": result.get("tool_name"),
                "arguments": result.get("arguments"),
                "output": result.get("output"),
                "agent": result.get("agent"),
                "raw_type": result.get("raw_type"),
                "raw_item_id": result.get("raw_item_id"),
                "raw_status": result.get("raw_status"),
                "source_agent": result.get("source_agent") or result.get("agent"),
                "target_agent": result.get("target_agent"),
                "evidence_refs": observation.get("evidence_refs") or [],
                "status": observation.get("status"),
                "created_at": observation.get("created_at"),
                "summary": _handoff_summary(result),
                "result": result,
            }
        )
    return projected


def _handoff_summary(result: dict[str, Any]) -> str:
    item_type = str(result.get("item_type") or "agent_handoff")
    source = result.get("source_agent") or result.get("agent")
    target = result.get("target_agent")
    tool = result.get("tool_name")
    if source and target:
        return f"{source} handed off to {target}."
    if tool:
        return f"{item_type} via {tool}."
    return item_type


def _agent_phase_projection(task: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    session_id = task.get("thread_id") or task["task_id"]
    projected = []
    for observation in observations:
        if observation.get("tool_name") != "agent_run_phase":
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        projected.append(
            {
                "type": "agent_phase",
                "subtype": str(result.get("name") or "phase"),
                "uuid": f"{task['task_id']}:phase:{observation.get('observation_id')}",
                "session_id": session_id,
                "task_id": task["task_id"],
                "observation_id": observation.get("observation_id"),
                "phase_name": result.get("name"),
                "phase_index": result.get("index"),
                "status": observation.get("status"),
                "created_at": observation.get("created_at"),
                "summary": _agent_phase_summary(result),
                "result": result,
            }
        )
    return projected


def _agent_phase_sdk_event(
    task: dict[str, Any],
    payload: dict[str, Any],
    base: dict[str, Any],
) -> dict[str, Any] | None:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if not result:
        return None
    return {
        **base,
        "type": "agent_phase",
        "subtype": str(result.get("name") or "phase"),
        "task_id": task["task_id"],
        "phase_name": result.get("name"),
        "phase_index": result.get("index"),
        "status": payload.get("status"),
        "summary": _agent_phase_summary(result),
        "result": result,
    }


def _agent_phase_summary(result: dict[str, Any]) -> str:
    name = str(result.get("name") or "phase")
    parts = [name]
    if result.get("raw_response_count") is not None:
        parts.append(f"{result.get('raw_response_count')} response(s)")
    if result.get("new_item_count") is not None:
        parts.append(f"{result.get('new_item_count')} item(s)")
    if result.get("interruption_count") is not None:
        parts.append(f"{result.get('interruption_count')} interruption(s)")
    return ", ".join(parts) + "."


def _agent_session_projection(task: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    session_id = task.get("thread_id") or task["task_id"]
    projected = []
    for observation in observations:
        if observation.get("tool_name") != "agent_session":
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        projected.append(
            {
                "type": "agent_session",
                "subtype": result.get("backend") or "session",
                "uuid": f"{task['task_id']}:agent-session:{observation.get('observation_id')}",
                "session_id": session_id,
                "task_id": task["task_id"],
                "observation_id": observation.get("observation_id"),
                "agent_session_id": result.get("session_id"),
                "backend": result.get("backend"),
                "db_path": result.get("db_path"),
                "turn_count": result.get("turn_count"),
                "final_item_count": result.get("final_item_count"),
                "final_item_types": result.get("final_item_types") or [],
                "final_items_preview": result.get("final_items_preview") or [],
                "turns": result.get("turns") or [],
                "evidence_refs": observation.get("evidence_refs") or [],
                "status": observation.get("status"),
                "created_at": observation.get("created_at"),
                "summary": _agent_session_summary(result),
                "result": result,
            }
        )
    return projected


def _agent_session_summary(result: dict[str, Any]) -> str:
    backend = str(result.get("backend") or "session")
    turn_count = result.get("turn_count")
    final_item_count = result.get("final_item_count")
    if turn_count is not None and final_item_count is not None:
        return f"{backend} preserved {turn_count} turn(s), {final_item_count} stored item(s)."
    if turn_count is not None:
        return f"{backend} preserved {turn_count} turn(s)."
    return backend


def _agent_stream_event_projection(task: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    session_id = task.get("thread_id") or task["task_id"]
    projected = []
    for observation in observations:
        if observation.get("tool_name") != "agent_stream_event":
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        if not result:
            continue
        projected.append(
            {
                "type": "agent_stream_event",
                "subtype": result.get("type") or "stream_event",
                "uuid": f"{task['task_id']}:agent-stream:{observation.get('observation_id')}",
                "session_id": session_id,
                "task_id": task["task_id"],
                "observation_id": observation.get("observation_id"),
                "stream_index": result.get("index"),
                "stream_event_type": result.get("type"),
                "stream_event_name": result.get("name"),
                "raw_event_type": result.get("raw_event_type"),
                "item_type": result.get("item_type"),
                "item_class": result.get("item_class"),
                "tool_use_id": observation.get("call_id") or result.get("call_id"),
                "tool_name": result.get("tool_name"),
                "agent": result.get("agent"),
                "evidence_refs": observation.get("evidence_refs") or [],
                "status": observation.get("status"),
                "created_at": observation.get("created_at"),
                "summary": _agent_stream_event_summary(result),
                "result": result,
            }
        )
    return projected


def _agent_stream_event_sdk_event(
    task: dict[str, Any],
    payload: dict[str, Any],
    base: dict[str, Any],
) -> dict[str, Any] | None:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if not result:
        return None
    return {
        **base,
        "type": "agent_stream_event",
        "subtype": result.get("type") or "stream_event",
        "task_id": task["task_id"],
        "observation_id": payload.get("observation_id"),
        "stream_index": result.get("index"),
        "stream_event_type": result.get("type"),
        "stream_event_name": result.get("name"),
        "raw_event_type": result.get("raw_event_type"),
        "item_type": result.get("item_type"),
        "item_class": result.get("item_class"),
        "tool_use_id": payload.get("call_id") or result.get("call_id"),
        "tool_name": result.get("tool_name"),
        "agent": result.get("agent"),
        "evidence_refs": payload.get("evidence_refs") or [],
        "status": payload.get("status"),
        "summary": _agent_stream_event_summary(result),
        "result": result,
    }


def _agent_stream_event_summary(result: dict[str, Any]) -> str:
    event_type = str(result.get("type") or "stream_event")
    name = str(result.get("name") or result.get("raw_event_type") or "").strip()
    if name:
        return f"{event_type}: {name}."
    return f"{event_type}."


def _agent_model_response_projection(
    task: dict[str, Any],
    observations: list[dict[str, Any]],
    artifacts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    session_id = task.get("thread_id") or task["task_id"]
    projected = []
    for observation in observations:
        if observation.get("tool_name") != "agent_model_response":
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        if not result:
            continue
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        trace_metadata = _trace_artifact_metadata_for_observation(observation, artifacts or [])
        provider = result.get("provider") or trace_metadata.get("provider")
        api_style = result.get("api_style") or result.get("apiStyle") or trace_metadata.get("api_style")
        projected.append(
            {
                "type": "agent_model_response",
                "subtype": "raw_response",
                "uuid": f"{task['task_id']}:model-response:{observation.get('observation_id')}",
                "session_id": session_id,
                "task_id": task["task_id"],
                "observation_id": observation.get("observation_id"),
                "response_id": result.get("response_id"),
                "request_id": result.get("request_id"),
                "model": result.get("model"),
                "model_source": result.get("model_source"),
                "provider": provider,
                "api_style": api_style,
                "status": observation.get("status"),
                "usage": usage,
                "output_count": result.get("output_count"),
                "output_types": result.get("output_types") or [],
                "evidence_refs": observation.get("evidence_refs") or [],
                "created_at": observation.get("created_at"),
                "summary": _agent_model_response_summary(result),
                "claim_boundary": (
                    "Stored model response evidence only; this transcript record does not prove current provider connectivity."
                ),
            }
        )
    return projected


def _agent_model_response_sdk_event(
    task: dict[str, Any],
    payload: dict[str, Any],
    base: dict[str, Any],
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if not result:
        return None
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    trace_metadata = _trace_artifact_metadata_for_observation(payload, artifacts or [])
    provider = result.get("provider") or trace_metadata.get("provider")
    api_style = result.get("api_style") or result.get("apiStyle") or trace_metadata.get("api_style")
    return {
        **base,
        "type": "agent_model_response",
        "subtype": "raw_response",
        "task_id": task["task_id"],
        "observation_id": payload.get("observation_id"),
        "response_id": result.get("response_id"),
        "request_id": result.get("request_id"),
        "model": result.get("model"),
        "model_source": result.get("model_source"),
        "provider": provider,
        "api_style": api_style,
        "status": payload.get("status"),
        "usage": usage,
        "output_count": result.get("output_count"),
        "output_types": result.get("output_types") or [],
        "evidence_refs": payload.get("evidence_refs") or [],
        "summary": _agent_model_response_summary(result),
        "claim_boundary": (
            "Stored model response evidence only; this event does not prove current provider connectivity."
        ),
    }


def _agent_model_response_summary(result: dict[str, Any]) -> str:
    response_id = str(result.get("response_id") or "model_response")
    model = str(result.get("model") or "unknown_model")
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    total_tokens = _usage_int(usage, "total_tokens", "totalTokens")
    if total_tokens:
        return f"{response_id} via {model}, {total_tokens} token(s)."
    return f"{response_id} via {model}."


def _agent_approval_projection(task: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    session_id = task.get("thread_id") or task["task_id"]
    projected = []
    for observation in observations:
        tool_name = observation.get("tool_name")
        if tool_name not in {"agent_approval_request", "agent_approval_decision"}:
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        subtype = "approval_decision" if tool_name == "agent_approval_decision" else "approval_request"
        projected.append(
            {
                "type": "agent_approval",
                "subtype": subtype,
                "uuid": f"{task['task_id']}:approval:{observation.get('observation_id')}",
                "session_id": session_id,
                "task_id": task["task_id"],
                "observation_id": observation.get("observation_id"),
                "tool_use_id": observation.get("call_id"),
                "tool_name": result.get("tool_name") or tool_name,
                "approval_event": tool_name,
                "decision": result.get("decision"),
                "approval_arguments": result.get("arguments"),
                "agent": result.get("agent"),
                "item_type": result.get("item_type"),
                "raw_type": result.get("raw_type"),
                "evidence_refs": observation.get("evidence_refs") or [],
                "status": observation.get("status"),
                "created_at": observation.get("created_at"),
                "summary": _approval_summary(subtype, result),
                "result": result,
            }
        )
    return projected


def _approval_summary(subtype: str, result: dict[str, Any]) -> str:
    tool = result.get("tool_name")
    decision = result.get("decision")
    if subtype == "approval_decision":
        if tool and decision:
            return f"{tool} approval {decision}."
        if decision:
            return f"approval {decision}."
        return "approval decision recorded."
    if tool:
        return f"{tool} approval requested."
    return "approval requested."


def _agent_question_projection(task: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    session_id = task.get("thread_id") or task["task_id"]
    projected = []
    for observation in observations:
        if observation.get("tool_name") != "agent_question":
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        if not result:
            continue
        projected.append(
            {
                "type": "agent_question",
                "subtype": result.get("question_type") or result.get("subtype") or "missing_info_request",
                "uuid": f"{task['task_id']}:question:{observation.get('observation_id')}",
                "session_id": session_id,
                "task_id": task["task_id"],
                "observation_id": observation.get("observation_id"),
                "questions": result.get("questions") or [],
                "question_count": len(result.get("questions") or []),
                "reason": result.get("reason"),
                "evidence_status": result.get("evidence_status"),
                "source_observation": result.get("source_observation"),
                "agent": result.get("agent"),
                "evidence_refs": observation.get("evidence_refs") or [],
                "status": observation.get("status"),
                "created_at": observation.get("created_at"),
                "summary": _agent_question_summary(result),
                "result": result,
                "claim_boundary": (
                    "Stored question/interruption evidence only; this transcript record does not prove current provider connectivity."
                ),
            }
        )
    return projected


def _agent_question_sdk_event(
    task: dict[str, Any],
    payload: dict[str, Any],
    base: dict[str, Any],
) -> dict[str, Any] | None:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if not result:
        return None
    return {
        **base,
        "type": "agent_question",
        "subtype": result.get("question_type") or result.get("subtype") or "missing_info_request",
        "task_id": task["task_id"],
        "observation_id": payload.get("observation_id"),
        "questions": result.get("questions") or [],
        "question_count": len(result.get("questions") or []),
        "reason": result.get("reason"),
        "evidence_status": result.get("evidence_status"),
        "source_observation": result.get("source_observation"),
        "agent": result.get("agent"),
        "evidence_refs": payload.get("evidence_refs") or [],
        "status": payload.get("status"),
        "summary": _agent_question_summary(result),
        "result": result,
        "claim_boundary": (
            "Stored question/interruption evidence only; this event does not prove current provider connectivity."
        ),
    }


def _agent_question_summary(result: dict[str, Any]) -> str:
    questions = result.get("questions") if isinstance(result.get("questions"), list) else []
    if questions:
        first = questions[0]
        if isinstance(first, dict):
            question = str(first.get("question") or first.get("field") or "").strip()
        else:
            question = str(first or "").strip()
        if question:
            return question[:240]
    reason = str(result.get("reason") or result.get("evidence_status") or "").strip()
    if reason:
        return reason[:240]
    return "agent question recorded."


def _agent_guidance_projection(task: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    session_id = task.get("thread_id") or task["task_id"]
    projected = []
    for observation in observations:
        if observation.get("tool_name") != "agent_guidance":
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        if not result:
            continue
        suggestions = result.get("next_tool_suggestions") if isinstance(result.get("next_tool_suggestions"), list) else []
        projected.append(
            {
                "type": "agent_guidance",
                "subtype": result.get("subtype") or result.get("guidance_type") or "backend_runtime_guidance",
                "uuid": f"{task['task_id']}:guidance:{observation.get('observation_id')}",
                "session_id": session_id,
                "task_id": task["task_id"],
                "observation_id": observation.get("observation_id"),
                "guidance_type": result.get("guidance_type"),
                "reason": result.get("reason"),
                "candidate_key": result.get("candidate_key"),
                "missing_fields": result.get("missing_fields") or [],
                "required_action": result.get("required_action"),
                "ask_user_allowed": result.get("ask_user_allowed"),
                "suggested_tool_count": len(suggestions),
                "next_tool_suggestions": suggestions,
                "close_fit_search_budget": result.get("close_fit_search_budget"),
                "search_recovery_policy": result.get("search_recovery_policy"),
                "agent": result.get("agent"),
                "evidence_refs": observation.get("evidence_refs") or [],
                "status": observation.get("status"),
                "created_at": observation.get("created_at"),
                "summary": _agent_guidance_summary(result),
                "result": result,
                "claim_boundary": (
                    "Stored backend guidance evidence only; this transcript record does not prove current provider connectivity."
                ),
            }
        )
    return projected


def _agent_guidance_sdk_event(
    task: dict[str, Any],
    payload: dict[str, Any],
    base: dict[str, Any],
) -> dict[str, Any] | None:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if not result:
        return None
    suggestions = result.get("next_tool_suggestions") if isinstance(result.get("next_tool_suggestions"), list) else []
    return {
        **base,
        "type": "agent_guidance",
        "subtype": result.get("subtype") or result.get("guidance_type") or "backend_runtime_guidance",
        "task_id": task["task_id"],
        "observation_id": payload.get("observation_id"),
        "guidance_type": result.get("guidance_type"),
        "reason": result.get("reason"),
        "candidate_key": result.get("candidate_key"),
        "missing_fields": result.get("missing_fields") or [],
        "required_action": result.get("required_action"),
        "ask_user_allowed": result.get("ask_user_allowed"),
        "suggested_tool_count": len(suggestions),
        "next_tool_suggestions": suggestions,
        "close_fit_search_budget": result.get("close_fit_search_budget"),
        "search_recovery_policy": result.get("search_recovery_policy"),
        "agent": result.get("agent"),
        "evidence_refs": payload.get("evidence_refs") or [],
        "status": payload.get("status"),
        "summary": _agent_guidance_summary(result),
        "result": result,
        "claim_boundary": (
            "Stored backend guidance evidence only; this event does not prove current provider connectivity."
        ),
    }


def _agent_guidance_summary(result: dict[str, Any]) -> str:
    reason = str(result.get("reason") or result.get("guidance_type") or "").strip()
    suggestions = result.get("next_tool_suggestions") if isinstance(result.get("next_tool_suggestions"), list) else []
    if reason and suggestions:
        return f"{reason}: {len(suggestions)} suggested tool(s)."
    if reason:
        return reason[:240]
    if suggestions:
        return f"{len(suggestions)} suggested tool(s)."
    return "agent guidance recorded."


def _agent_eval_case_verification_projection(
    task: dict[str, Any],
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    session_id = task.get("thread_id") or task["task_id"]
    projected = []
    for observation in observations:
        if observation.get("tool_name") != "eval_case_verification":
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        if not result:
            continue
        execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
        projected.append(
            {
                "type": "agent_eval_case_verification",
                "subtype": result.get("failure_label") or result.get("status") or "eval_case_verification",
                "uuid": f"{task['task_id']}:eval-case-verification:{observation.get('observation_id')}",
                "session_id": session_id,
                "task_id": task["task_id"],
                "observation_id": observation.get("observation_id"),
                "eval_case_id": result.get("eval_case_id"),
                "name": result.get("name"),
                "source_task_id": result.get("source_task_id"),
                "failure_label": result.get("failure_label"),
                "verifier_command": result.get("verifier_command"),
                "expected_status": result.get("expected_status"),
                "expected_status_matches": result.get("expected_status_matches"),
                "failed_checks": result.get("failed_checks") or [],
                "execution_status": execution.get("status"),
                "returncode": execution.get("returncode"),
                "evidence_refs": observation.get("evidence_refs") or [],
                "status": observation.get("status"),
                "created_at": observation.get("created_at"),
                "summary": _agent_eval_case_verification_summary(result),
                "result": result,
                "claim_boundary": (
                    "Stored eval-case verification evidence only; this transcript record does not prove "
                    "current provider connectivity."
                ),
            }
        )
    return projected


def _agent_eval_case_verification_sdk_event(
    task: dict[str, Any],
    payload: dict[str, Any],
    base: dict[str, Any],
) -> dict[str, Any] | None:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if not result:
        return None
    execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
    return {
        **base,
        "type": "agent_eval_case_verification",
        "subtype": result.get("failure_label") or result.get("status") or "eval_case_verification",
        "task_id": task["task_id"],
        "observation_id": payload.get("observation_id"),
        "eval_case_id": result.get("eval_case_id"),
        "name": result.get("name"),
        "source_task_id": result.get("source_task_id"),
        "failure_label": result.get("failure_label"),
        "verifier_command": result.get("verifier_command"),
        "expected_status": result.get("expected_status"),
        "expected_status_matches": result.get("expected_status_matches"),
        "failed_checks": result.get("failed_checks") or [],
        "execution_status": execution.get("status"),
        "returncode": execution.get("returncode"),
        "evidence_refs": payload.get("evidence_refs") or [],
        "status": payload.get("status"),
        "summary": _agent_eval_case_verification_summary(result),
        "result": result,
        "claim_boundary": (
            "Stored eval-case verification evidence only; this event does not prove current provider "
            "connectivity."
        ),
    }


def _agent_eval_case_verification_summary(result: dict[str, Any]) -> str:
    eval_case = str(result.get("eval_case_id") or "eval case").strip()
    status = str(result.get("status") or "").strip()
    execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
    execution_status = str(execution.get("status") or "").strip()
    label = str(result.get("failure_label") or "").strip()
    visible_status = status or execution_status
    if label and visible_status:
        return f"eval case {eval_case} {visible_status}: {label}."
    if visible_status:
        return f"eval case {eval_case} {visible_status}."
    if label:
        return f"eval case {eval_case}: {label}."
    return f"eval case {eval_case} verification recorded."


def _agent_guardrail_projection(task: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    session_id = task.get("thread_id") or task["task_id"]
    projected = []
    for observation in observations:
        if observation.get("tool_name") != "agent_guardrail_result":
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        output_info = result.get("output_info") if isinstance(result.get("output_info"), dict) else {}
        projected.append(
            {
                "type": "agent_guardrail",
                "subtype": result.get("guardrail_type") or result.get("surface") or "guardrail",
                "uuid": f"{task['task_id']}:guardrail:{observation.get('observation_id')}",
                "session_id": session_id,
                "task_id": task["task_id"],
                "observation_id": observation.get("observation_id"),
                "guardrail_name": result.get("guardrail_name"),
                "guardrail_class": result.get("guardrail_class"),
                "tripwire_triggered": result.get("tripwire_triggered"),
                "behavior": result.get("behavior"),
                "output_info": output_info,
                "agent": result.get("agent") or output_info.get("agent_name"),
                "agent_output": result.get("agent_output") or output_info.get("agent_output"),
                "reason": output_info.get("reason") or result.get("reason"),
                "evidence_refs": observation.get("evidence_refs") or [],
                "status": observation.get("status"),
                "created_at": observation.get("created_at"),
                "summary": _guardrail_summary(result),
                "result": result,
            }
        )
    return projected


def _agent_run_error_projection(task: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    session_id = task.get("thread_id") or task["task_id"]
    projected = []
    for observation in observations:
        if observation.get("tool_name") != "agent_run_error":
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        projected.append(
            {
                "type": "agent_run_error",
                "subtype": result.get("error_type") or result.get("status") or "run_error",
                "uuid": f"{task['task_id']}:run-error:{observation.get('observation_id')}",
                "session_id": session_id,
                "task_id": task["task_id"],
                "observation_id": observation.get("observation_id"),
                "status": observation.get("status"),
                "error_type": result.get("error_type"),
                "error": result.get("error"),
                "scenario": result.get("scenario"),
                "trace_id": result.get("trace_id"),
                "external_api_called": result.get("external_api_called"),
                "evidence_refs": observation.get("evidence_refs") or [],
                "created_at": observation.get("created_at"),
                "summary": _agent_run_error_summary(result),
                "result": result,
            }
        )
    return projected


def _agent_run_error_summary(result: dict[str, Any]) -> str:
    error_type = result.get("error_type") or result.get("status") or "agent run error"
    error = result.get("error")
    if error:
        return f"{error_type}: {error}"
    return str(error_type)


def _agent_final_output_projection(task: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    session_id = task.get("thread_id") or task["task_id"]
    projected = []
    for observation in observations:
        if observation.get("tool_name") != "agent_final_output":
            continue
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        output = result.get("final_output")
        if output in (None, ""):
            continue
        if isinstance(output, str):
            summary = _truncate_text(output, 500)
        else:
            summary = _truncate_text(json.dumps(output, ensure_ascii=False, sort_keys=True), 500)
        projected.append(
            {
                "type": "final_output",
                "subtype": "agent_trace_final_output",
                "uuid": f"{task['task_id']}:final-output:{observation.get('observation_id')}",
                "session_id": session_id,
                "task_id": task["task_id"],
                "observation_id": observation.get("observation_id"),
                "status": observation.get("status"),
                "summary": summary,
                "output_source": "observation.agent_final_output",
                "output": output,
                "evidence_refs": observation.get("evidence_refs") or [],
                "created_at": observation.get("created_at"),
                "result": result,
                "claim_boundary": (
                    "Stored final-output observation only; this transcript record does not prove current provider connectivity."
                ),
            }
        )
    return projected


def _guardrail_summary(result: dict[str, Any]) -> str:
    name = result.get("guardrail_name") or result.get("guardrail_type") or "guardrail"
    if result.get("tripwire_triggered") is True:
        return f"{name} tripwire triggered."
    if result.get("tripwire_triggered") is False:
        return f"{name} passed."
    status = result.get("status")
    if status:
        return f"{name} {status}."
    return str(name)


def _task_progress_status(progress_records: list[dict[str, Any]]) -> dict[str, Any]:
    latest = progress_records[-1] if progress_records else {}
    return {
        "updates": len(progress_records),
        "counts": latest.get("counts") or {"pending": 0, "in_progress": 0, "completed": 0},
        "verification_nudge_needed": bool(latest.get("verification_nudge_needed")),
    }


def _task_progress_projection(task_id: str, progress_records: list[dict[str, Any]]) -> dict[str, Any]:
    latest = progress_records[-1] if progress_records else {}
    items = latest.get("items") or []
    return {
        "uuid": f"{task_id}:progress:{latest.get('progress_id')}" if latest else None,
        "progress_id": latest.get("progress_id"),
        "updated_at": latest.get("created_at"),
        "counts": latest.get("counts") or {"pending": 0, "in_progress": 0, "completed": 0},
        "verification_nudge_needed": bool(latest.get("verification_nudge_needed")),
        "items": [
            {
                "uuid": f"{task_id}:progress:{latest.get('progress_id')}:{item.get('item_id')}",
                "item_id": item.get("item_id"),
                "content": item.get("content"),
                "active_form": item.get("active_form"),
                "status": item.get("status"),
                "metadata": item.get("metadata") or {},
            }
            for item in items
        ],
        "history_count": len(progress_records),
    }


def _sdk_event_projection(
    task: dict[str, Any],
    events: list[dict[str, Any]],
    artifacts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    projected = []
    session_id = task.get("thread_id") or task["task_id"]
    request = task.get("request") if isinstance(task.get("request"), dict) else {}
    created_at = _parse_iso_datetime(task.get("created_at"))
    last_tool_name = None
    tool_uses = 0
    for event in events:
        event_type = event.get("event_type")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        base = {
            "uuid": f"{task['task_id']}:sdk:{event.get('event_id')}",
            "session_id": session_id,
            "source_event_id": event.get("event_id"),
            "source_event_seq": event.get("seq"),
            "timestamp": event.get("created_at"),
        }
        if event_type in {"task.queued", "task.resume"}:
            subtype = "task_resumed" if event_type == "task.resume" else "task_queued"
            description = (
                "Task resumed and queued"
                if event_type == "task.resume"
                else _truncate_text(str(request.get("prompt") or ""), 200) or "Task queued"
            )
            projected.append(
                {
                    **base,
                    "type": "system",
                    "subtype": subtype,
                    "task_id": task["task_id"],
                    "description": description,
                    "task_type": request.get("agent"),
                    "prompt": request.get("prompt"),
                }
            )
            projected.append(
                {
                    **base,
                    "uuid": f"{task['task_id']}:sdk:{event.get('event_id')}:state-queued",
                    "type": "system",
                    "subtype": "session_state_changed",
                    "state": "queued",
                }
            )
        elif event_type == "task.started":
            projected.append(
                {
                    **base,
                    "type": "system",
                    "subtype": "task_started",
                    "task_id": task["task_id"],
                    "description": _truncate_text(str(request.get("prompt") or ""), 200) or "Task started",
                    "task_type": request.get("agent"),
                    "prompt": request.get("prompt"),
                }
            )
            projected.append(
                {
                    **base,
                    "uuid": f"{task['task_id']}:sdk:{event.get('event_id')}:state-running",
                    "type": "system",
                    "subtype": "session_state_changed",
                    "state": "running",
                }
            )
        elif event_type in {"tool_call.recorded", "tool_call.status_updated"}:
            if event_type == "tool_call.recorded":
                tool_uses += 1
            last_tool_name = payload.get("tool_name") or last_tool_name
            projected.append(
                {
                    **base,
                    "type": "tool_progress",
                    "tool_use_id": payload.get("call_id"),
                    "tool_name": payload.get("tool_name"),
                    "parent_tool_use_id": (payload.get("metadata") or {}).get("parent_tool_use_id")
                    if isinstance(payload.get("metadata"), dict)
                    else None,
                    "elapsed_time_seconds": _duration_seconds(
                        _parse_iso_datetime(payload.get("started_at") or payload.get("created_at")),
                        _parse_iso_datetime(payload.get("completed_at"))
                        or _parse_iso_datetime(event.get("created_at")),
                    ),
                    "task_id": task["task_id"],
                    "status": payload.get("status"),
                    "started_at": payload.get("started_at"),
                    "completed_at": payload.get("completed_at"),
                }
            )
        elif event_type == "task.progress_updated":
            progress_items = payload.get("items") or []
            active = next((item for item in progress_items if item.get("status") == "in_progress"), None)
            description = (
                (active or {}).get("active_form")
                or _progress_counts_summary(payload.get("counts") or {})
                or "Task progress updated"
            )
            projected.append(
                {
                    **base,
                    "type": "system",
                    "subtype": "task_progress",
                    "task_id": task["task_id"],
                    "description": description,
                    "usage": {
                        "total_tokens": 0,
                        "tool_uses": tool_uses,
                        "duration_ms": _duration_ms(created_at, _parse_iso_datetime(event.get("created_at"))),
                    },
                    "last_tool_name": last_tool_name,
                    "summary": _progress_counts_summary(payload.get("counts") or {}),
                    "progress_items": progress_items,
                    "verification_nudge_needed": bool(payload.get("verification_nudge_needed")),
                }
            )
        elif event_type in {"task.message_queued", "task.message_dequeued", "task.message_cancelled"}:
            subtype = {
                "task.message_queued": "queued_message_created",
                "task.message_dequeued": "queued_message_dequeued",
                "task.message_cancelled": "queued_message_cancelled",
            }[event_type]
            projected.append(
                {
                    **base,
                    "type": "task_control",
                    "subtype": subtype,
                    "task_id": task["task_id"],
                    "message_uuid": payload.get("message_uuid"),
                    "priority": payload.get("priority"),
                    "status": payload.get("status"),
                    "interrupt_requested": bool(payload.get("interrupt_requested")),
                    "created_at": payload.get("created_at"),
                    "cancelled_at": payload.get("cancelled_at"),
                    "summary": _queued_message_summary(subtype, payload),
                    "payload": payload,
                }
            )
        elif event_type == "permission_decision.recorded":
            projected.append(
                {
                    **base,
                    "type": "permission_decision",
                    "subtype": str(payload.get("behavior") or "decision"),
                    "task_id": task["task_id"],
                    "decision_id": payload.get("decision_id"),
                    "tool_use_id": payload.get("call_id"),
                    "tool_name": payload.get("tool_name"),
                    "behavior": payload.get("behavior"),
                    "status": payload.get("status"),
                    "message": payload.get("message"),
                    "blocked_path": payload.get("blocked_path"),
                    "decision_reason": payload.get("decision_reason"),
                    "suggestions": payload.get("suggestions") or [],
                    "summary": _permission_decision_summary(payload),
                    "payload": payload,
                }
            )
        elif event_type == "verification.completed":
            verification_event = _verification_result_sdk_event(
                task,
                payload,
                base,
                event_status=event.get("status"),
            )
            if verification_event:
                projected.append(verification_event)
        elif event_type == "observation.recorded" and payload.get("tool_name") == "agent_model_response":
            model_response_event = _agent_model_response_sdk_event(task, payload, base, artifacts or [])
            if model_response_event:
                projected.append(model_response_event)
        elif event_type == "observation.recorded" and payload.get("tool_name") == "agent_run_phase":
            phase_event = _agent_phase_sdk_event(task, payload, base)
            if phase_event:
                projected.append(phase_event)
        elif event_type == "observation.recorded" and payload.get("tool_name") == "agent_session":
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            projected.append(
                {
                    **base,
                    "type": "agent_session",
                    "subtype": result.get("backend") or "session",
                    "task_id": task["task_id"],
                    "agent_session_id": result.get("session_id"),
                    "backend": result.get("backend"),
                    "db_path": result.get("db_path"),
                    "turn_count": result.get("turn_count"),
                    "final_item_count": result.get("final_item_count"),
                    "final_item_types": result.get("final_item_types") or [],
                    "final_items_preview": result.get("final_items_preview") or [],
                    "turns": result.get("turns") or [],
                    "evidence_refs": payload.get("evidence_refs") or [],
                    "status": payload.get("status"),
                    "summary": _agent_session_summary(result),
                    "result": result,
                }
            )
        elif event_type == "observation.recorded" and payload.get("tool_name") == "agent_stream_event":
            stream_event = _agent_stream_event_sdk_event(task, payload, base)
            if stream_event:
                projected.append(stream_event)
        elif event_type == "observation.recorded" and payload.get("tool_name") == "agent_handoff":
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            projected.append(
                {
                    **base,
                    "type": "agent_handoff",
                    "subtype": result.get("item_type") or "agent_handoff",
                    "task_id": task["task_id"],
                    "tool_use_id": payload.get("call_id"),
                    "tool_name": result.get("tool_name"),
                    "arguments": result.get("arguments"),
                    "output": result.get("output"),
                    "agent": result.get("agent"),
                    "raw_type": result.get("raw_type"),
                    "raw_item_id": result.get("raw_item_id"),
                    "raw_status": result.get("raw_status"),
                    "source_agent": result.get("source_agent") or result.get("agent"),
                    "target_agent": result.get("target_agent"),
                    "evidence_refs": payload.get("evidence_refs") or [],
                    "status": payload.get("status"),
                    "summary": _handoff_summary(result),
                    "result": result,
                }
            )
        elif event_type == "observation.recorded" and payload.get("tool_name") in {
            "agent_approval_request",
            "agent_approval_decision",
        }:
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            subtype = "approval_decision" if payload.get("tool_name") == "agent_approval_decision" else "approval_request"
            projected.append(
                {
                    **base,
                    "type": "agent_approval",
                    "subtype": subtype,
                    "task_id": task["task_id"],
                    "tool_use_id": payload.get("call_id"),
                    "tool_name": result.get("tool_name") or payload.get("tool_name"),
                    "approval_event": payload.get("tool_name"),
                    "decision": result.get("decision"),
                    "approval_arguments": result.get("arguments"),
                    "agent": result.get("agent"),
                    "item_type": result.get("item_type"),
                    "raw_type": result.get("raw_type"),
                    "evidence_refs": payload.get("evidence_refs") or [],
                    "status": payload.get("status"),
                    "summary": _approval_summary(subtype, result),
                    "result": result,
                }
            )
        elif event_type == "observation.recorded" and payload.get("tool_name") == "agent_question":
            question_event = _agent_question_sdk_event(task, payload, base)
            if question_event:
                projected.append(question_event)
        elif event_type == "observation.recorded" and payload.get("tool_name") == "agent_guidance":
            guidance_event = _agent_guidance_sdk_event(task, payload, base)
            if guidance_event:
                projected.append(guidance_event)
        elif event_type == "observation.recorded" and payload.get("tool_name") == "eval_case_verification":
            eval_case_event = _agent_eval_case_verification_sdk_event(task, payload, base)
            if eval_case_event:
                projected.append(eval_case_event)
        elif event_type == "observation.recorded" and payload.get("tool_name") == "agent_guardrail_result":
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            output_info = result.get("output_info") if isinstance(result.get("output_info"), dict) else {}
            projected.append(
                {
                    **base,
                    "type": "agent_guardrail",
                    "subtype": result.get("guardrail_type") or result.get("surface") or "guardrail",
                    "task_id": task["task_id"],
                    "guardrail_name": result.get("guardrail_name"),
                    "guardrail_class": result.get("guardrail_class"),
                    "tripwire_triggered": result.get("tripwire_triggered"),
                    "behavior": result.get("behavior"),
                    "output_info": output_info,
                    "agent": result.get("agent") or output_info.get("agent_name"),
                    "agent_output": result.get("agent_output") or output_info.get("agent_output"),
                    "reason": output_info.get("reason") or result.get("reason"),
                    "evidence_refs": payload.get("evidence_refs") or [],
                    "status": payload.get("status"),
                    "summary": _guardrail_summary(result),
                    "result": result,
                }
            )
        elif event_type == "observation.recorded" and payload.get("tool_name") == "agent_run_error":
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            projected.append(
                {
                    **base,
                    "type": "agent_run_error",
                    "subtype": result.get("error_type") or result.get("status") or "run_error",
                    "task_id": task["task_id"],
                    "status": payload.get("status"),
                    "error_type": result.get("error_type"),
                    "error": result.get("error"),
                    "scenario": result.get("scenario"),
                    "trace_id": result.get("trace_id"),
                    "external_api_called": result.get("external_api_called"),
                    "evidence_refs": payload.get("evidence_refs") or [],
                    "summary": _agent_run_error_summary(result),
                    "result": result,
                }
            )
        elif event_type == "observation.recorded" and payload.get("tool_name") == "agent_final_output":
            final_output_event = _agent_final_output_observation_sdk_event(task, payload, base)
            if final_output_event:
                projected.append(final_output_event)
        elif event_type in {"task.completed", "task.failed", "task.cancelled", "task.cancel", "task.paused", "task.pause"}:
            status = {
                "task.completed": "completed",
                "task.failed": "failed",
                "task.cancelled": "stopped",
                "task.cancel": "stopped",
                "task.paused": "stopped",
                "task.pause": "stopped",
            }[event_type]
            result = payload if isinstance(payload, dict) else {}
            error = _task_error_payload(task, result)
            projected.append(
                {
                    **base,
                    "type": "system",
                    "subtype": "task_notification",
                    "task_id": task["task_id"],
                    "status": status,
                    "output_file": "",
                    "summary": _task_terminal_summary(task, result, status),
                    "returncode": result.get("returncode"),
                    "error": error,
                    "usage": {
                        "total_tokens": 0,
                        "tool_uses": tool_uses,
                        "duration_ms": _duration_ms(created_at, _parse_iso_datetime(event.get("created_at"))),
                    },
                }
            )
            final_output_event = _task_final_output_sdk_event(task, result, status, base)
            if final_output_event:
                projected.append(final_output_event)
            projected.append(
                {
                    **base,
                    "uuid": f"{task['task_id']}:sdk:{event.get('event_id')}:state-idle",
                    "type": "system",
                    "subtype": "session_state_changed",
                    "state": "idle",
                }
            )
    return projected


def _queued_message_summary(subtype: str, payload: dict[str, Any]) -> str:
    priority = str(payload.get("priority") or "queued")
    message_uuid = str(payload.get("message_uuid") or "message")
    if subtype == "queued_message_cancelled":
        return f"Cancelled {priority} queued message {message_uuid}."
    if subtype == "queued_message_dequeued":
        return f"Dequeued {priority} queued message {message_uuid}."
    return f"Queued {priority} message {message_uuid}."


def _permission_decision_summary(payload: dict[str, Any]) -> str:
    behavior = str(payload.get("behavior") or "decision")
    tool_name = str(payload.get("tool_name") or "tool")
    return f"Permission decision for {tool_name}: {behavior}."


def _agent_final_output_observation_sdk_event(
    task: dict[str, Any],
    payload: dict[str, Any],
    base: dict[str, Any],
) -> dict[str, Any] | None:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    output: Any = result.get("final_output")
    if output in (None, ""):
        return None
    if isinstance(output, str):
        summary = _truncate_text(output, 500)
    else:
        summary = _truncate_text(json.dumps(output, ensure_ascii=False, sort_keys=True), 500)
    if not summary:
        return None
    task_id = str(task.get("task_id") or "")
    return {
        **base,
        "uuid": f"{base.get('uuid')}:final-output",
        "type": "final_output",
        "subtype": "agent_trace_final_output",
        "task_id": task_id,
        "tool_use_id": payload.get("call_id"),
        "status": payload.get("status"),
        "summary": summary,
        "output_source": "observation.agent_final_output",
        "output": output,
        "evidence_refs": payload.get("evidence_refs") or [],
        "source_endpoints": {
            "task": f"/api/harness/v1/tasks/{task_id}",
            "evidence": f"/api/harness/v1/tasks/{task_id}/evidence",
            "client_transcript": f"/api/harness/v1/tasks/{task_id}/client-transcript",
        },
    }


def _terminal_event_id(events: list[dict[str, Any]]) -> int | None:
    terminal_types = {"task.completed", "task.failed", "task.cancelled", "task.cancel", "task.paused", "task.pause"}
    terminal_ids = [
        int(item.get("event_id") or 0)
        for item in events
        if item.get("event_type") in terminal_types and int(item.get("event_id") or 0) > 0
    ]
    return max(terminal_ids) if terminal_ids else None


def _task_final_output_sdk_event(
    task: dict[str, Any],
    result: dict[str, Any],
    status: str,
    base: dict[str, Any],
) -> dict[str, Any] | None:
    output_source = "result.final_output"
    output: Any = result.get("final_output")
    if output in (None, ""):
        output_source = "result.last_message"
        output = result.get("last_message")
    if output in (None, ""):
        return None
    if isinstance(output, str):
        summary = _truncate_text(output, 500)
    else:
        summary = _truncate_text(json.dumps(output, ensure_ascii=False, sort_keys=True), 500)
    if not summary:
        return None
    task_id = str(task.get("task_id") or "")
    return {
        **base,
        "uuid": f"{base.get('uuid')}:final-output",
        "type": "final_output",
        "subtype": "task_final_output",
        "task_id": task_id,
        "status": status,
        "summary": summary,
        "output_source": output_source,
        "output": output,
        "source_endpoints": {
            "task": f"/api/harness/v1/tasks/{task_id}",
            "evidence": f"/api/harness/v1/tasks/{task_id}/evidence",
            "client_transcript": f"/api/harness/v1/tasks/{task_id}/client-transcript",
        },
    }


def _progress_counts_summary(counts: dict[str, Any]) -> str:
    pending = int(counts.get("pending") or 0)
    running = int(counts.get("in_progress") or 0)
    completed = int(counts.get("completed") or 0)
    total = pending + running + completed
    if total == 0:
        return ""
    return f"{completed}/{total} completed, {running} in progress, {pending} pending"


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(start: datetime | None, end: datetime | None) -> int:
    if not start or not end:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))


def _duration_seconds(start: datetime | None, end: datetime | None) -> int:
    if not start or not end:
        return 0
    return max(0, int((end - start).total_seconds()))


def _next_queued_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    priority_rank = {"now": 0, "next": 1, "later": 2}
    queued = [
        (priority_rank.get(str(item.get("priority") or "later"), 2), index, item)
        for index, item in enumerate(messages)
        if item.get("status") == "queued" and str(item.get("prompt") or "").strip()
    ]
    if not queued:
        return None
    queued.sort(key=lambda item: (item[0], item[1]))
    return queued[0][2]


def _mark_queued_message_status(
    messages: list[dict[str, Any]],
    message_uuid: str,
    status: str,
) -> None:
    timestamp_key = {
        "running": "started_at",
        "completed": "completed_at",
        "failed": "completed_at",
        "cancelled": "cancelled_at",
    }.get(status)
    for item in messages:
        if item.get("message_uuid") != message_uuid:
            continue
        item["status"] = status
        if timestamp_key:
            item[timestamp_key] = datetime.now(timezone.utc).isoformat()
        return


def _queued_message_counts(messages: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0}
    for item in messages:
        status = str(item.get("status") or "queued")
        if status not in counts:
            counts[status] = 0
        counts[status] += 1
    return counts


def _subagent_counts(children: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {"total": len(children)}
    for child in children:
        status = str(child.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _truncate_text(value: str, limit: int) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    cleaned = cleaned.strip("._")
    return cleaned[:80] or "anonymous"


def _default_isolated_instructions(client_id: str) -> str:
    return (
        "# Isolated Harness Session\n\n"
        "This workspace is owned by one harness client namespace.\n\n"
        f"- Harness client id: `{client_id}`\n"
        "- Do not treat the source repository AGENTS.md as this session's working memory.\n"
        "- Use only the prompt, files in this workspace, explicit API task state, and tool observations as session context.\n"
        "- If source repository access is needed, request an explicit path or use a task that intentionally runs in that repository cwd.\n"
    )
