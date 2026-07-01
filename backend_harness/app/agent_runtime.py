from __future__ import annotations

import copy
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from .agent_recipes import agent_skill_catalog, load_agent_skill, match_agent_recipes
from datetime import datetime, timezone

from .agent_run_store import (
    RUN_LEASE_STALE_SECONDS,
    _lease_is_fresh,
    _parse_iso,
    append_trace,
    build_agent_run_store,
)
from .providers import provider_error_metadata_from_result
from .report_grounding import validate_report_grounding
from .tool_registry import ToolContext, ToolRegistry, build_fitcheck_tool_registry
from .tools.llm_client import ResponsesApiClient


FITCHECK_AGENT_INSTRUCTIONS = """
You are FitCheckAgent, a tool-using clothing discovery agent.

Rules:
- The user is not asking for a simple buy/no-buy verdict. They want to find multiple online clothing candidates that match what they like about their own clothes: fit, material, size, silhouette, color/design details, and wearing preference.
- Use tools before recommending or ranking candidates.
- The initial context includes an available_skills catalog with only skill names, descriptions, and tool dependencies. When a skill description matches the task, call load_agent_skill to retrieve its full SOP before continuing. Do not invent skill instructions from the catalog summary.
- Search for candidate products when the user describes a liked item or preference target, even if they did not provide a specific product URL.
- Use plan_context_and_sources when the user gives URLs, purchase history, short follow-up search commands, attachments, or mixed baseline/candidate evidence that must be classified before choosing the next tool.
- If the user describes a past purchase or owned baseline item, such as brand/category/size, call load_closet_memory and then enrich_closet_item_from_web before scoring candidates.
- Do not treat parse_user_goal as a mandatory first step. It is an optional normalization helper; when the raw conversation has enough cues, call search_web_candidates directly from the raw user text.
- For Korean requests like "비슷한 옷 추천", "비슷한 옷 찾아줘", or short follow-up replies such as "웹서치로 찾아봐", keep the accumulated run context and compare multiple candidates. After search_web_candidates, enrich the candidates with fetch_product_page, classify_product_assets, extract_size_material_info, and normalize_product_spec before calling score_candidate_match with candidate_key="all".
- For outfit-pairing requests such as "같이 입을 X", "어울리는 X", "코디할 X", or "match with X", treat any supplied owned-item URL as baseline context and treat X as a free-form target item query, not a fixed category enum. Search multiple target-item candidates, enrich them, then call score_candidate_match with candidate_key="all" and write_purchase_report with candidate_key="all"; do not ask for candidate measurements before producing a grounded shortlist.
- Preserve unfamiliar target words exactly. Category classification is optional metadata and must never block search or force a user question.
- If search observations are weak or empty, revise the plan autonomously: change the query, broaden or change sources, and call search_web_candidates again. Do not ask the user to solve ordinary search failure.
- If autonomous search is exhausted, call write_purchase_report with candidate_key="all" so the backend can produce a grounded no-result report.
- Never invent measurements, material, price, reviews, or source URLs.
- Treat size labels such as S/M/L/XL, "free", or one-size as weak search hints only. They are not comparable fit evidence across brands, categories, countries, or shops.
- For size-sensitive recommendations, prefer garment measurement evidence from product tables, size-chart OCR, fetched page text, or user-supplied measurements. If only a size label is observed, say that measurement evidence is missing instead of claiming a close fit.
- Do not stop after the first weak or merely acceptable candidate when the user asks for a similar fit. A first page, a tiny shortlist, or a size-label-only match is not enough. If score_candidate_match or write_purchase_report says no close measured match exists yet, follow the suggested search recovery tools with alternate queries and sources until a close measured match is found or the backend reports that the close-fit search budget is exhausted.
- Distinguish body/model measurements, ranking numbers, pack counts, and size-guide instructions from garment measurements. Do not turn those numbers into product fit facts.
- If candidate fields are missing, first use available backend tools: search_web_candidates, fetch_product_page, classify_product_assets, extract_size_material_info, normalize_product_spec. Call ask_missing_info only after those tool attempts still cannot resolve the fields.
- Baseline clothing information must be grounded too. Do not ask the user for baseline measurements/material until enrich_closet_item_from_web has attempted web search, page fetch, OCR, and normalization when a lookup query is available.
- After the user replies with missing candidate evidence such as measurements, material, or price, call normalize_product_spec for that candidate again before asking another missing-info question.
- If the user replies with a product URL for a candidate, call register_user_candidate_url before fetch_product_page.
- After the user replies with baseline_evidence such as owned-item measurements or material, call load_closet_memory again and then rescore before asking another baseline question.
- If the user uploaded images, call inspect_uploaded_images before making claims about them.
- Uploaded image metadata is not visual evidence by itself; do not infer color, fit, size, material, or condition from images unless a tool observation provides it.
- score_candidate_match must run before write_purchase_report.
- Final answers must be grounded in tool observations.
- Keep Korean user-facing output concise and factual.
""".strip()


EMPTY_SEARCH_ATTEMPT_LIMIT = 5

# System tools that keep the agent loop coherent. A caller's allow/deny list
# (docs/24 gap #10) must never be able to strand the loop by blocking these,
# so they are always permitted regardless of caller constraints.
ALWAYS_ALLOWED_TOOLS = frozenset(
    {
        "load_agent_skill",
        "plan_context_and_sources",
        "parse_user_goal",
        "load_closet_memory",
        "ask_missing_info",
        "confirm_baseline_identity",
        "write_purchase_report",
    }
)


def _run_controls(run: dict[str, Any]) -> dict[str, Any]:
    """Caller-supplied per-run controls, resolved from the run input payload."""
    controls = (run.get("input") or {}).get("controls")
    return controls if isinstance(controls, dict) else {}


def _effective_max_turns(run: dict[str, Any], default_max_turns: int) -> int:
    """Per-run max_turns cap (docs/24 gap #9). Caller value wins when provided."""
    requested = _run_controls(run).get("max_turns")
    try:
        if requested is not None:
            return max(1, min(int(requested), 40))
    except (TypeError, ValueError):
        pass
    return default_max_turns


def _tool_permission_denial(run: dict[str, Any], tool_name: str) -> dict[str, Any] | None:
    """Return a failed tool-result if the caller's allow/deny list blocks this
    tool (docs/24 gap #10). System-critical tools are always allowed. Returns
    None when the tool is permitted."""
    if tool_name in ALWAYS_ALLOWED_TOOLS:
        return None
    controls = _run_controls(run)
    denied = controls.get("denied_tools")
    allowed = controls.get("allowed_tools")
    blocked_reason = None
    if isinstance(denied, list) and tool_name in denied:
        blocked_reason = "denied_by_caller"
    elif isinstance(allowed, list) and allowed and tool_name not in allowed:
        blocked_reason = "not_in_caller_allowlist"
    if not blocked_reason:
        return None
    return {
        "status": "failed",
        "error": {
            "code": "TOOL_NOT_PERMITTED",
            "message": (
                f"Tool '{tool_name}' was blocked by the caller's run controls "
                f"({blocked_reason})."
            ),
            "tool": tool_name,
            "reason": blocked_reason,
        },
    }


def _tool_budget_exceeded(run: dict[str, Any]) -> bool:
    """True if the caller-set per-run tool budget (docs/24 gap #9) is spent."""
    controls = _run_controls(run)
    budget = controls.get("tool_budget")
    try:
        budget = int(budget) if budget is not None else None
    except (TypeError, ValueError):
        budget = None
    if budget is None:
        return False
    spent = int((run.get("state") or {}).get("tool_calls_executed") or 0)
    return spent >= budget


def _cancel_requested(run: dict[str, Any]) -> bool:
    """True if the caller asked to cancel this run (docs/24 gap #1)."""
    flag = (run.get("state") or {}).get("cancel_requested")
    return bool(flag)


class LlmClient(Protocol):
    external_api_called: bool

    def get_status(self) -> dict[str, Any]:
        ...

    def create_response(
        self,
        *,
        instructions: str,
        input_items: str | list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> dict[str, Any]:
        ...


class FitCheckAgentRuntime:
    """Production-agent prototype with backend-owned tool execution."""

    def __init__(
        self,
        *,
        run_store_path: Path,
        web_result_path: Path,
        keyword_result_path: Path,
        refresh_web_candidates,
        llm_client: LlmClient | None = None,
        tool_registry: ToolRegistry | None = None,
        max_turns: int = 18,
    ) -> None:
        self.store = build_agent_run_store(run_store_path)
        self.web_result_path = web_result_path
        self.keyword_result_path = keyword_result_path
        self.refresh_web_candidates = refresh_web_candidates
        self.llm_client = llm_client or ResponsesApiClient()
        self.tool_registry = tool_registry or build_fitcheck_tool_registry()
        self.max_turns = max_turns

    def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.prepare_run(payload)
        if run.get("status") != "running":
            return run
        return self._run_loop(run)

    def prepare_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not _has_initial_user_input(payload):
            return {
                "run_id": None,
                "status": "invalid_request",
                "error": {
                    "code": "INITIAL_USER_INPUT_REQUIRED",
                    "message": "Agent run requires goal, free_text, candidate_url, or attachments.",
                },
            }

        llm_status = self.llm_client.get_status()
        if llm_status.get("status") != "connected":
            return {
                "run_id": None,
                "status": "configuration_required",
                "llm": llm_status,
                "message": "Production AI agent requires LLM configuration before it can run.",
            }

        run = self.store.create_run(payload, llm_status)
        append_trace(
            run,
            step_type="run",
            name="create_agent_run",
            status="completed",
            input_payload=_safe_run_input(payload),
            output_payload={"run_id": run["run_id"]},
        )
        return self.store.update_run(run)

    def continue_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            return {"status": "not_found", "run_id": run_id}
        if run.get("status") == "failed" and _retryable_failed_run_can_resume(run):
            run["status"] = "running"
            run["error"] = None
            append_trace(
                run,
                step_type="guardrail",
                name="resume_retryable_failed_run",
                status="running",
                output_payload={
                    "pending_tool_output_count": len(run.get("state", {}).get("pending_tool_outputs") or []),
                },
            )
            run = self.store.update_run(run)
        if run.get("status") != "running":
            return run
        return self._run_loop(run)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.store.get_run(run_id)

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        """Request cancellation of a run (docs/24 gap #1).

        A running loop stops cooperatively at its next turn boundary. A run that
        is not actively looping (e.g. an abandoned 'running' row with a stale
        lease, or a waiting_for_user run) is finalized to 'cancelled' immediately.
        """
        run = self.store.get_run(run_id)
        if not run:
            return {"status": "not_found", "run_id": run_id}
        status = run.get("status")
        if status in {"completed", "failed", "cancelled", "configuration_required"}:
            return run
        self.store.request_cancel(run_id)
        run = self.store.get_run(run_id) or run
        lease_fresh = _lease_is_fresh((run.get("state") or {}).get("active_lease"))
        if status == "running" and lease_fresh:
            # A live loop owns it; it will observe cancel at its next turn.
            return run
        # No live loop (waiting_for_user, or stale/abandoned running): finalize now.
        return self._finalize_cancelled_run(run)

    def _enqueue_steering_message(
        self, run: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Append a mid-run user message to the durable steering inbox
        (docs/24 gaps #2/#3). The running loop consumes it at a turn boundary."""
        state = run.setdefault("state", {})
        inbox = state.setdefault("pending_user_messages", [])
        inbox.append(
            {
                "message": payload.get("message"),
                "answers": payload.get("answers") or [],
                "attachments": payload.get("attachments") or [],
                "queued_at": _completed_at(),
            }
        )
        append_trace(
            run,
            step_type="user_reply",
            name="queue_steering_message",
            status="queued",
            input_payload={"queued_message": payload.get("message")},
            output_payload={"queue_depth": len(inbox)},
        )
        saved = self.store.update_run(run)
        return {
            "run_id": run.get("run_id"),
            "status": "queued",
            "queued": True,
            "queue_depth": len(inbox),
            "message": "메시지를 큐에 넣었습니다. 진행 중인 작업이 다음 안전 지점에서 반영합니다.",
            "state": saved.get("state", {}),
        }

    def reap_abandoned_run(self, run_id: str, *, cancelled: bool) -> dict[str, Any] | None:
        """Finalize a 'running' run whose loop died without releasing its lease
        (docs/24 gap #1). Idempotent: re-checks state under a fresh read."""
        run = self.store.get_run(run_id)
        if not run:
            return None
        state = run.get("state") or {}
        if run.get("status") != "running" or _lease_is_fresh(state.get("active_lease")):
            return run
        if not cancelled:
            # Don't reap a run that was touched recently — it may be starting up
            # or between heartbeats on a long LLM call (docs/24 #1 conservative reap).
            updated = _parse_iso(run.get("updated_at"))
            if updated is not None:
                age = (datetime.now(timezone.utc) - updated).total_seconds()
                if age < RUN_LEASE_STALE_SECONDS:
                    return run
        state.pop("active_lease", None)
        if cancelled:
            return self._finalize_cancelled_run(run)
        run.setdefault("state", {})["pending_tool_outputs"] = []
        run["status"] = "failed"
        run["completed_at"] = _completed_at()
        run["error"] = {
            "code": "RUN_ABANDONED",
            "message": "The run loop stopped without finalizing (client disconnect or crash).",
        }
        append_trace(
            run,
            step_type="guardrail",
            name="reap_abandoned_run",
            status="failed",
            output_payload={"reason": "stale_lease_no_live_loop"},
        )
        return self.store.update_run(run)

    def reply(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.prepare_reply(run_id, payload)
        if run.get("status") not in {"running"}:
            return run
        return self._run_loop(run)

    def prepare_reply(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            return {"status": "not_found", "run_id": run_id}
        if not _same_session(run, payload):
            return {"status": "not_found", "run_id": run_id}
        if not _has_reply_user_input(payload):
            return {
                "run_id": run_id,
                "status": "invalid_request",
                "error": {
                    "code": "REPLY_USER_INPUT_REQUIRED",
                    "message": "Agent reply requires message, answers, or attachments.",
                },
            }
        if run.get("status") == "running":
            # docs/24 gaps #2/#3: don't drop mid-run input. Enqueue it in a
            # durable inbox so the running loop applies it at the next safe turn.
            return self._enqueue_steering_message(run, payload)

        llm_status = self.llm_client.get_status()
        if llm_status.get("status") != "connected":
            run["status"] = "configuration_required"
            run["llm"] = {
                **run.get("llm", {}),
                "status": llm_status.get("status"),
                "provider": llm_status.get("provider"),
                "model": llm_status.get("model"),
                "api_style": llm_status.get("api_style"),
                "tool_execution": llm_status.get("tool_execution"),
                "hosted_tools_supported": llm_status.get("hosted_tools_supported"),
                "local_function_tools_supported": llm_status.get("local_function_tools_supported"),
            }
            run["error"] = {"code": "LLM_CONFIGURATION_REQUIRED", "llm": llm_status}
            append_trace(
                run,
                step_type="guardrail",
                name="require_llm_configuration",
                status="configuration_required",
                output_payload=llm_status,
            )
            return self.store.update_run(run)

        run.setdefault("input", {}).setdefault("user_replies", []).append(payload)
        if payload.get("attachments"):
            run.setdefault("input", {}).setdefault("attachments", []).extend(payload["attachments"])
        pending_outputs = run.setdefault("state", {}).get("pending_tool_outputs") or []
        if pending_outputs:
            pending_outputs.append(_user_reply_input_item(payload, run))
            run.setdefault("state", {})["pending_tool_outputs"] = pending_outputs
        state = run.setdefault("state", {})
        # docs/24 gaps #7/#8/#16: before dropping derived context to force a
        # re-plan, capture a durable summary of the prior turn (resolved baseline,
        # the shortlist it produced with prices, and the last search constraints)
        # so the next turn can resolve context-dependent follow-ups such as
        # "두번째꺼 자세히" (ordinal) or "더 싼걸로" (comparative price anchor).
        prior_turn_summary = _capture_prior_turn_summary(run)
        state["prior_turn_summary"] = prior_turn_summary
        # docs/24 #8: deterministically resolve an ordinal/positional follow-up
        # ("두번째꺼", "the second one", "1번") to the concrete prior shortlist item
        # so the model expands that exact candidate instead of cold re-searching.
        resolved_ordinal = _resolve_ordinal_reference(payload, prior_turn_summary)
        if resolved_ordinal:
            state["resolved_shortlist_reference"] = resolved_ordinal
        else:
            state.pop("resolved_shortlist_reference", None)
        state.pop("parsed_liked_item", None)
        for stale_key in [
            "context_snapshot",
            "context_plan",
            "execution_plan",
            "search_constraints",
            "source_refs",
            "grounding_policy",
        ]:
            state.pop(stale_key, None)
        baseline_rejection = _baseline_identity_rejection_from_latest_reply(run)
        if baseline_rejection:
            state.setdefault("rejected_baseline_identity_candidates", []).extend(
                baseline_rejection["rejected_candidates"]
            )
            state.pop("unconfirmed_baseline_identity_candidates", None)
            closet = state.get("closet_item") or {}
            identity = closet.get("baseline_identity") or {}
            rejected_key = str(identity.get("selected_candidate_key") or "")
            if (
                str(closet.get("memory_source") or "") == "web_lookup_unconfirmed_baseline"
                or rejected_key
            ):
                state.pop("closet_item", None)
                state["baseline_identity"] = {
                    "status": "rejected_by_user",
                    "reason": "latest_reply_rejected_unconfirmed_identity_candidate",
                    "rejection_text": baseline_rejection["rejection_text"],
                    "rejected_candidate_count": len(baseline_rejection["rejected_candidates"]),
                }
            append_trace(
                run,
                step_type="guardrail",
                name="reject_unconfirmed_baseline_identity_candidate",
                status="completed",
                output_payload=baseline_rejection,
            )
        if not state.get("unconfirmed_baseline_identity_candidates"):
            # docs/24 #7: keep a CONFIRMED baseline across turns; only drop an
            # unconfirmed/web-lookup baseline so a follow-up doesn't forget the
            # established closet item and regress to "baseline unknown".
            closet = state.get("closet_item") or {}
            identity_status = str((closet.get("baseline_identity") or {}).get("status") or "")
            memory_source = str(closet.get("memory_source") or "")
            baseline_confirmed = (
                identity_status == "confirmed"
                or memory_source in {"closet_memory", "user_confirmed", "confirmed_baseline"}
            )
            if not baseline_confirmed:
                state.pop("closet_item", None)
        state["missing_info"] = []
        run["final_report"] = None
        run["completed_at"] = None
        append_trace(
            run,
            step_type="user_reply",
            name="user_reply",
            status="completed",
            input_payload=payload,
        )
        run["status"] = "running"
        run["error"] = None
        return self.store.update_run(run)

    def _run_loop(self, run: dict[str, Any]) -> dict[str, Any]:
        """Concurrency-guarded wrapper around the agent loop (docs/24 #15).

        Acquires an exclusive run-loop lease so a second concurrent
        start/continue/reply cannot double-execute the same run, and always
        releases it on exit. If the lease is already held by a live loop, the
        current caller is refused with AGENT_RUN_ALREADY_RUNNING.
        """
        run_id = run.get("run_id")
        acquired, latest = self.store.try_acquire_run_lease(run_id)
        if not acquired:
            if latest is not None:
                return latest
            return {
                "run_id": run_id,
                "status": "invalid_request",
                "error": {
                    "code": "AGENT_RUN_ALREADY_RUNNING",
                    "message": "This run is already executing; wait for it to finish.",
                },
            }
        # Adopt the freshly-leased state (carries active_lease + prior state).
        run = latest
        try:
            return self._run_loop_inner(run)
        finally:
            self._release_run_lease(run_id)

    def _release_run_lease(self, run_id: str | None) -> None:
        if not run_id:
            return
        run = self.store.get_run(run_id)
        if not run:
            return
        state = run.setdefault("state", {})
        if state.pop("active_lease", None) is not None:
            self.store.update_run(run)

    def _finalize_cancelled_run(self, run: dict[str, Any]) -> dict[str, Any]:
        """Stop a run because the user cancelled it (docs/24 gap #1)."""
        state = run.setdefault("state", {})
        cancel_meta = state.pop("cancel_requested", None)
        if not isinstance(cancel_meta, dict):
            cancel_meta = {}
        state["pending_tool_outputs"] = []
        state["missing_info"] = []
        run["status"] = "cancelled"
        run["completed_at"] = _completed_at()
        run["error"] = None
        run["final_report"] = {
            "summary": "요청에 따라 실행을 취소했습니다.",
            "grounded": True,
            "grounding": "run_cancelled_by_user",
            "report_type": "cancelled",
        }
        append_trace(
            run,
            step_type="guardrail",
            name="cancel_agent_run",
            status="cancelled",
            output_payload={"requested_at": cancel_meta.get("requested_at")},
        )
        return self.store.update_run(run)

    def _cancel_requested_in_store(self, run: dict[str, Any]) -> Any:
        """Re-read the cancel flag from the store (set by a concurrent /cancel).
        Returns the flag value (dict) or None/falsey when not cancelled."""
        run_id = run.get("run_id")
        if not run_id:
            return None
        stored = self.store.get_run(run_id)
        return (stored or {}).get("state", {}).get("cancel_requested")

    def _merge_store_steering_inbox(self, run: dict[str, Any]) -> None:
        """Pull steering messages a concurrent /reply wrote to the store into the
        in-memory run so this loop can consume them (docs/24 #2/#3)."""
        run_id = run.get("run_id")
        if not run_id:
            return
        stored = self.store.get_run(run_id)
        stored_inbox = ((stored or {}).get("state") or {}).get("pending_user_messages") or []
        if not stored_inbox:
            return
        state = run.setdefault("state", {})
        current = state.get("pending_user_messages") or []
        seen = {json.dumps(m, ensure_ascii=False, sort_keys=True) for m in current}
        for msg in stored_inbox:
            key = json.dumps(msg, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                current.append(msg)
                seen.add(key)
        state["pending_user_messages"] = current

    def _drain_steering_inbox(self, run: dict[str, Any]) -> None:
        """Apply mid-run steering messages queued via /reply (docs/24 #2/#3).

        Each queued message is injected into the model input stream so the
        running loop redirects on the next turn, exactly as a between-turn reply
        would. The inbox is cleared once consumed.
        """
        state = run.setdefault("state", {})
        inbox = state.get("pending_user_messages") or []
        if not inbox:
            return
        pending_outputs = state.get("pending_tool_outputs") or []
        applied = 0
        for queued in inbox:
            reply_payload = {
                "message": queued.get("message"),
                "answers": queued.get("answers") or [],
                "attachments": queued.get("attachments") or [],
            }
            run.setdefault("input", {}).setdefault("user_replies", []).append(reply_payload)
            if reply_payload["attachments"]:
                run.setdefault("input", {}).setdefault("attachments", []).extend(
                    reply_payload["attachments"]
                )
            pending_outputs.append(_user_reply_input_item(reply_payload, run))
            applied += 1
        state["pending_tool_outputs"] = pending_outputs
        consumed_keys = [
            json.dumps(m, ensure_ascii=False, sort_keys=True) for m in inbox
        ]
        state["pending_user_messages"] = []
        # Clear the store's inbox for the consumed messages so the preserve-on-
        # update merge does not resurrect them (docs/24 #2/#3).
        self.store.clear_steering_inbox(run.get("run_id"), consumed_keys)
        # A steering message may redirect the goal; drop stale derived context so
        # the next turn re-plans from the updated conversation (docs/24 #2).
        for stale_key in ("context_snapshot", "execution_plan"):
            state.pop(stale_key, None)
        append_trace(
            run,
            step_type="user_reply",
            name="apply_steering_messages",
            status="completed",
            output_payload={"applied_count": applied},
        )

    def _run_loop_inner(self, run: dict[str, Any]) -> dict[str, Any]:
        pending_tool_outputs = run.setdefault("state", {}).get("pending_tool_outputs") or []
        effective_max_turns = _effective_max_turns(run, self.max_turns)

        for _ in range(effective_max_turns):
            if run.get("status") != "running":
                return self.store.update_run(run)

            # Cooperative cancellation at a safe turn boundary (docs/24 #1).
            # The cancel flag is written to the store by a *different* request
            # (POST /cancel), so re-read it from the store each turn — the
            # in-memory `run` dict this loop holds would never see it otherwise.
            store_cancel = self._cancel_requested_in_store(run)
            if _cancel_requested(run) or store_cancel:
                if isinstance(store_cancel, dict):
                    run.setdefault("state", {})["cancel_requested"] = store_cancel
                return self._finalize_cancelled_run(run)
            self.store.heartbeat_run_lease(run)
            # Apply steering messages queued mid-run by a concurrent /reply
            # (docs/24 #2/#3). These are also written to the store by another
            # request, so merge the store's inbox into the in-memory run first.
            self._merge_store_steering_inbox(run)
            self._drain_steering_inbox(run)

            # docs/24 #8: a backend-resolved ordinal follow-up must expand the
            # specific prior candidate, not re-derive the baseline. Pin the model
            # to that candidate on the first turn after the reply.
            resolved_ref = run.setdefault("state", {}).get("resolved_shortlist_reference")
            if (
                resolved_ref
                and not run["state"].get("resolved_shortlist_reference_directed")
                and not _tool_calls_after_latest_user_reply(run)
            ):
                candidate_key = resolved_ref.get("candidate_key")
                run["state"]["resolved_shortlist_reference_directed"] = True
                run["state"]["pending_tool_outputs"] = [
                    {
                        "role": "user",
                        "content": (
                            "The user's follow-up refers to a specific prior shortlist item. "
                            f"The backend resolved it to candidate_key='{candidate_key}' "
                            f"(rank {resolved_ref.get('rank')}). Do NOT search again and do NOT "
                            "re-derive the baseline. Directly call score_candidate_match for that "
                            "candidate_key using the already-observed data (fetch_product_page / "
                            "extract_size_material_info / normalize_product_spec for that one "
                            "candidate only if a field is missing), then write_purchase_report."
                        ),
                    }
                ]
                append_trace(
                    run,
                    step_type="guardrail",
                    name="direct_resolved_shortlist_expansion",
                    status="running",
                    output_payload={"candidate_key": candidate_key, "rank": resolved_ref.get("rank")},
                )

            baseline_confirmation_args = _baseline_identity_confirmation_args_from_latest_reply(run)
            if (
                baseline_confirmation_args
                and "confirm_baseline_identity" not in _tool_calls_after_latest_user_reply(run)
            ):
                confirmation_observation = self._execute_backend_suggested_tool(
                    run,
                    "confirm_baseline_identity",
                    baseline_confirmation_args,
                    reason="latest_reply_confirms_baseline_identity_candidate",
                    index=1,
                )
                confirmation_result = confirmation_observation.get("result") or {}
                if confirmation_result.get("status") == "waiting_for_user":
                    run["status"] = "waiting_for_user"
                    run["error"] = None
                    run.setdefault("state", {})["pending_tool_outputs"] = []
                    run.setdefault("state", {})["missing_info"] = confirmation_result.get("questions", [])
                    append_trace(
                        run,
                        step_type="guardrail",
                        name="await_baseline_identity_candidate_selection",
                        status="waiting_for_user",
                        output_payload={
                            "confirmation_observation_call_id": confirmation_observation.get("call_id"),
                        },
                    )
                    return self.store.update_run(run)
                if confirmation_result.get("status") == "completed":
                    if _latest_reply_requests_post_confirmation_continuation(run):
                        pending_tool_outputs = [
                            {
                                "role": "user",
                                "content": (
                                    "Baseline identity is now confirmed and stored. Continue the original "
                                    "recommendation task from the confirmed closet item. Do not stop at a "
                                    "memory-save acknowledgement; search, enrich, score, and write a grounded report."
                                ),
                            }
                        ]
                        run.setdefault("state", {})["pending_tool_outputs"] = pending_tool_outputs
                        run.setdefault("state", {})["force_response_context_reset"] = "baseline_identity_confirmed_after_waiting"
                        run.setdefault("state", {})["missing_info"] = []
                        append_trace(
                            run,
                            step_type="guardrail",
                            name="continue_after_baseline_identity_confirmation",
                            status="running",
                            output_payload={
                                "confirmation_observation_call_id": confirmation_observation.get("call_id"),
                                "reason": "latest_reply_requested_recommendation_continuation",
                            },
                        )
                        continue
                    run["status"] = "completed"
                    run["error"] = None
                    run["completed_at"] = _completed_at()
                    _reset_provider_response_context_after_backend_terminal(
                        run,
                        reason="baseline_identity_confirmation_completed_without_model_ack",
                        call_id=confirmation_observation.get("call_id"),
                    )
                    run.setdefault("state", {})["missing_info"] = []
                    confirmed = confirmation_result.get("confirmed_candidate") or {}
                    summary = "기준 옷 후보를 확인해 closet memory에 저장했습니다."
                    if confirmed.get("name"):
                        summary += f" 확인한 후보: {confirmed['name']}."
                    if confirmed.get("source_url"):
                        summary += f" 근거 URL: {confirmed['source_url']}"
                    run["final_report"] = {
                        "summary": summary,
                        "grounded": True,
                        "grounding": "confirm_baseline_identity_tool",
                        "confirmed_candidate": confirmed,
                    }
                    append_trace(
                        run,
                        step_type="guardrail",
                        name="complete_after_baseline_identity_confirmation",
                        status="completed",
                        output_payload={
                            "confirmation_observation_call_id": confirmation_observation.get("call_id"),
                            "durable_memory_status": confirmation_result.get("durable_memory_status"),
                        },
                    )
                    return self.store.update_run(run)

            if _should_backend_preplan_latest_user_reply(run):
                preplan_observation = self._execute_backend_suggested_tool(
                    run,
                    "plan_context_and_sources",
                    {},
                    reason="latest_user_reply_requires_context_resolution",
                    index=1,
                )
                preplan_result = preplan_observation.get("result") or {}
                preplan_status = str(preplan_result.get("status") or "completed")
                if preplan_status == "waiting_for_user":
                    run["status"] = "waiting_for_user"
                    run["error"] = None
                    run.setdefault("state", {})["pending_tool_outputs"] = []
                    questions = preplan_result.get("questions") or preplan_result.get("next_questions") or []
                    if questions:
                        run["state"]["missing_info"] = questions
                    append_trace(
                        run,
                        step_type="guardrail",
                        name="await_latest_reply_context_resolution",
                        status="waiting_for_user",
                        output_payload={
                            "preplan_observation_call_id": preplan_observation.get("call_id"),
                            "reason": preplan_result.get("reason"),
                        },
                    )
                    return self.store.update_run(run)

                pending_tool_outputs = _append_backend_observation_context(
                    run,
                    pending_tool_outputs,
                    preplan_observation,
                    instruction=(
                        "The latest user reply has been resolved into backend-owned context, source refs, "
                        "search constraints, and grounding policy. Continue from this plan and do not ignore "
                        "its suggested backend tools."
                    ),
                    trace_name="preplan_latest_user_reply_context",
                )
                guidance = _deferred_enrichment_guidance(preplan_result)
                if guidance:
                    pending_tool_outputs = _append_backend_runtime_guidance(
                        run,
                        pending_tool_outputs,
                        guidance,
                        trace_name="continue_latest_reply_context_followup",
                    )
                    if _guidance_should_run_immediate_backend_recovery(guidance):
                        recovery = self._execute_backend_recovery_from_guidance(
                            run,
                            pending_tool_outputs,
                            rejected_final_text="",
                        )
                        if recovery.get("executed"):
                            pending_tool_outputs = recovery["pending_tool_outputs"]
                            if run.get("status") in {"completed", "failed", "waiting_for_user", "configuration_required"}:
                                return self.store.update_run(run)
                            continue

            if _should_backend_preload_closet_memory(run, self.store):
                memory_observation = self._execute_backend_suggested_tool(
                    run,
                    "load_closet_memory",
                    {},
                    reason="latest_user_message_references_durable_closet_memory",
                    index=1,
                )
                memory_result = memory_observation.get("result") or {}
                pending_tool_outputs = [
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "backend_recovery_observations": [
                                    {
                                        "tool": "load_closet_memory",
                                        "arguments": {},
                                        "result": _summarize_tool_output(memory_result),
                                    }
                                ],
                                "instruction": (
                                    "The user referenced saved closet memory. Continue from the loaded closet memory; "
                                    "use it as baseline context for search, scoring, and reporting."
                                ),
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
                run.setdefault("state", {})["pending_tool_outputs"] = pending_tool_outputs

            _refresh_agent_skill_context(run)
            input_items: str | list[dict[str, Any]]
            if pending_tool_outputs:
                input_items = pending_tool_outputs
            else:
                input_items = _initial_model_input(run)

            interrupted_call_ids = _unacknowledged_provider_tool_call_ids(run, pending_tool_outputs)
            if (
                not pending_tool_outputs
                and interrupted_call_ids
                and run.setdefault("state", {}).get("previous_response_id")
            ):
                _reset_provider_response_context_after_interrupted_tool_calls(
                    run,
                    missing_call_ids=interrupted_call_ids,
                )
                append_trace(
                    run,
                    step_type="guardrail",
                    name="reset_response_context_after_interrupted_tool_calls",
                    status="completed",
                    output_payload={
                        "missing_provider_tool_call_ids": interrupted_call_ids,
                        "previous_response_id_omitted": True,
                    },
                )
                input_items = _initial_model_input(run)

            pairing_mismatch = _pending_tool_result_pairing_mismatch(run, pending_tool_outputs)
            if pairing_mismatch:
                run["status"] = "failed"
                run["error"] = {
                    "code": "TOOL_RESULT_PAIRING_MISMATCH",
                    "message": "Pending tool outputs do not match the latest model tool calls.",
                    **pairing_mismatch,
                }
                run.setdefault("state", {})["pending_tool_outputs"] = []
                append_trace(
                    run,
                    step_type="guardrail",
                    name="stop_tool_result_pairing_mismatch",
                    status="failed",
                    output_payload=run["error"],
                )
                return self.store.update_run(run)

            instructions = _agent_instructions_for_run(run)
            context_budget = _record_context_budget_summary(
                run,
                instructions=instructions,
                input_items=input_items,
                pending_tool_outputs=bool(pending_tool_outputs),
            )
            input_items_for_llm = _apply_context_compaction_to_input(
                run,
                input_items=input_items,
                budget_summary=context_budget,
                pending_tool_outputs=bool(pending_tool_outputs),
            )
            input_items_for_llm = _apply_response_context_reset_to_input(
                run,
                input_items=input_items_for_llm,
                budget_summary=context_budget,
                pending_tool_outputs=pending_tool_outputs,
            )
            prepare_input_items = getattr(self.llm_client, "prepare_input_items", None)
            if callable(prepare_input_items):
                input_items_for_llm = prepare_input_items(
                    run=run,
                    instructions=instructions,
                    input_items=input_items_for_llm,
                )
            previous_response_id = _previous_response_id_for_request(run, context_budget)
            if context_budget.get("response_context_reset_applied"):
                append_trace(
                    run,
                    step_type="context",
                    name="reset_response_context",
                    status="completed",
                    output_payload={
                        "reason": context_budget.get("response_context_reset_reason"),
                        "previous_response_id_omitted": True,
                        "estimated_input_tokens_before": context_budget.get("estimated_input_tokens"),
                        "compacted_estimated_input_tokens": context_budget.get("response_context_reset_estimated_input_tokens"),
                    },
                )

            start = perf_counter()
            try:
                response = self.llm_client.create_response(
                    instructions=instructions,
                    input_items=input_items_for_llm,
                    tools=self.tool_registry.schemas(),
                    previous_response_id=previous_response_id,
                )
            except Exception as exc:
                pending_tool_output_count = len(pending_tool_outputs)
                error_payload = _llm_response_error_payload(
                    exc,
                    pending_tool_output_count=pending_tool_output_count,
                    pending_tool_outputs_present=bool(pending_tool_outputs),
                )
                interrupted_call_ids = _unacknowledged_provider_tool_call_ids(run, pending_tool_outputs)
                if _is_provider_missing_tool_output_error(error_payload) and interrupted_call_ids:
                    error_payload["retryable"] = True
                    error_payload["resume_available"] = True
                    error_payload["missing_provider_tool_call_ids"] = interrupted_call_ids
                run.setdefault("state", {})["pending_tool_outputs"] = (
                    pending_tool_outputs if pending_tool_outputs else []
                )
                append_trace(
                    run,
                    step_type="llm_response",
                    name=_llm_request_name(run),
                    status="failed",
                    input_payload={
                        "pending_tool_outputs": bool(pending_tool_outputs),
                        "pending_tool_output_count": pending_tool_output_count,
                    },
                    output_payload={
                        **error_payload,
                    },
                )
                recovered = self._recover_with_backend_report_after_llm_failure(
                    run,
                    error_payload=error_payload,
                )
                if recovered:
                    return self.store.update_run(run)
                run["status"] = "failed"
                run["error"] = error_payload
                append_trace(
                    run,
                    step_type="guardrail",
                    name="stop_llm_response_failed",
                    status="failed",
                    output_payload={
                        **error_payload,
                    },
                )
                return self.store.update_run(run)

            duration_ms = round((perf_counter() - start) * 1000)
            _update_context_budget_summary_from_response(context_budget, response)
            _update_usage_accounting_from_response(
                run,
                response,
                duration_ms=duration_ms,
            )
            run.setdefault("llm", {})["external_api_called"] = bool(
                getattr(self.llm_client, "external_api_called", False)
            )
            run["state"]["previous_response_id"] = response.get("id")
            run["state"]["pending_tool_outputs"] = []
            response_context_items = _response_context_items_for_next_llm(response)
            append_trace(
                run,
                step_type="llm_response",
                name=_llm_request_name(run),
                status="completed",
                input_payload={"pending_tool_outputs": bool(pending_tool_outputs)},
                output_payload={
                    "response_id": response.get("id"),
                    "api_style": response.get("api_style") or run.get("llm", {}).get("api_style"),
                    "raw_response_id": response.get("raw_response_id"),
                    "provider_request_id": response.get("provider_request_id") or response.get("request_id"),
                    "transport": response.get("transport"),
                    "tool_call_count": len(_extract_tool_calls(response)),
                    "reasoning_context_item_count": len(response_context_items),
                    "duration_ms": duration_ms,
                },
            )
            self._checkpoint(run)

            tool_calls = _extract_tool_calls(response)
            if not tool_calls:
                final_text = _extract_output_text(response)
                if _pending_contains_backend_guidance(pending_tool_outputs):
                    recovery = self._execute_backend_recovery_from_guidance(
                        run,
                        pending_tool_outputs,
                        rejected_final_text=final_text,
                    )
                    if recovery.get("executed"):
                        pending_tool_outputs = recovery["pending_tool_outputs"]
                        if run.get("status") in {"completed", "failed", "waiting_for_user", "configuration_required"}:
                            return self.store.update_run(run)
                        continue
                if not run.setdefault("state", {}).get("observations"):
                    run["status"] = "failed"
                    run["error"] = {
                        "code": "NO_TOOL_CALLS_BEFORE_FINAL",
                        "message": "Agent final text was rejected because no backend tools were called.",
                    }
                    append_trace(
                        run,
                        step_type="guardrail",
                        name="require_tool_observation_before_final",
                        status="failed",
                        output_payload=run["error"],
                    )
                    return self.store.update_run(run)
                report_observation = _latest_report_observation_after_latest_user_reply(run)
                if not report_observation:
                    completed_score = _latest_completed_score_observation_after_latest_user_reply(run)
                    if completed_score:
                        report_observation = self._execute_backend_required_report_tool(
                            run,
                            completed_score,
                            rejected_final_text=final_text,
                        )
                        report_result = report_observation.get("result") or {}
                        if report_result.get("status") == "waiting_for_user":
                            run["status"] = "waiting_for_user"
                            run["error"] = None
                            run["state"]["pending_tool_outputs"] = []
                            append_trace(
                                run,
                                step_type="guardrail",
                                name="adopt_backend_required_missing_info_question",
                                status="waiting_for_user",
                                output_payload={
                                    "report_observation_call_id": report_observation.get("call_id"),
                                    "missing_fields": report_result.get("missing_fields", []),
                                },
                            )
                            return self.store.update_run(run)
                        guidance = _deferred_enrichment_guidance(report_result)
                        if guidance:
                            pending_tool_outputs = _append_backend_runtime_guidance(
                                run,
                                pending_tool_outputs,
                                guidance,
                                trace_name="continue_backend_required_report_enrichment",
                            )
                            if _guidance_should_run_immediate_backend_recovery(guidance):
                                recovery = self._execute_backend_recovery_from_guidance(
                                    run,
                                    pending_tool_outputs,
                                    rejected_final_text=final_text,
                                )
                                if recovery.get("executed"):
                                    pending_tool_outputs = recovery["pending_tool_outputs"]
                                    if run.get("status") in {"completed", "failed", "waiting_for_user", "configuration_required"}:
                                        return self.store.update_run(run)
                            continue
                        if report_result.get("status") == "completed" and report_result.get("grounded") is not False:
                            if report_result.get("report_type") == "no_result" and not _latest_empty_search_after_latest_user_reply(run):
                                guidance = _deferred_enrichment_guidance(report_result)
                                if guidance:
                                    pending_tool_outputs = _append_backend_runtime_guidance(
                                        run,
                                        pending_tool_outputs,
                                        guidance,
                                        trace_name="continue_report_enrichment",
                                    )
                                    continue
                                # Search recovery is still available, so a no-result report cannot be adopted yet.
                                report_result = {
                                    **report_result,
                                    "status": "failed",
                                    "error": {
                                        "code": "SEARCH_RECOVERY_REQUIRED_BEFORE_NO_RESULT_REPORT",
                                        "message": "No-result report is not allowed while search recovery suggestions remain.",
                                    },
                                }
                                report_observation["result"] = report_result
                            tool_report_text = str(report_result.get("final_report") or "").strip()
                            if report_result.get("status") == "completed" and tool_report_text:
                                run["status"] = "completed"
                                run["error"] = None
                                run["completed_at"] = _completed_at()
                                run["state"]["pending_tool_outputs"] = []
                                _store_completed_report_followups(run, report_result)
                                run["final_report"] = {
                                    "summary": tool_report_text,
                                    "grounded": True,
                                    "grounding": "write_purchase_report_tool_backend_required",
                                    "grounding_validation": report_result.get("grounding_validation"),
                                    "report_type": report_result.get("report_type"),
                                    **_report_metadata_from_tool_result(report_result),
                                    "llm_final_text": final_text,
                                }
                                append_trace(
                                    run,
                                    step_type="guardrail",
                                    name="adopt_backend_required_report_tool_output",
                                    status="completed",
                                    output_payload={
                                        "report_observation_call_id": report_observation.get("call_id"),
                                        "llm_final_text_present": bool(final_text),
                                    },
                                )
                                return self.store.update_run(run)
                            guidance = _deferred_enrichment_guidance(report_result)
                            if guidance:
                                pending_tool_outputs = _append_backend_runtime_guidance(
                                    run,
                                    pending_tool_outputs,
                                    guidance,
                                    trace_name="continue_report_enrichment",
                                )
                                continue
                    empty_search = _latest_empty_search_after_latest_user_reply(run)
                    if empty_search:
                        if self._complete_with_backend_no_result_report(
                            run,
                            rejected_final_text=final_text,
                            reason="model_final_after_exhausted_search",
                        ):
                            return self.store.update_run(run)
                    deferred_score = _latest_deferred_score_observation_after_latest_user_reply(run)
                    if deferred_score:
                        score_result = deferred_score.get("result") or {}
                        questions = score_result.get("questions") or score_result.get("next_questions") or []
                        if questions:
                            run["status"] = "waiting_for_user"
                            run["error"] = None
                            run["state"]["pending_tool_outputs"] = []
                            run["state"]["missing_info"] = questions
                            append_trace(
                                run,
                                step_type="guardrail",
                                name="adopt_deferred_score_missing_info_question",
                                status="waiting_for_user",
                                output_payload={
                                    "score_observation_call_id": deferred_score.get("call_id"),
                                    "reason": score_result.get("reason"),
                                    "evidence_status": score_result.get("evidence_status"),
                                    "llm_final_text_present": bool(final_text),
                                },
                            )
                            return self.store.update_run(run)
                    if _has_completed_candidate_scores_after_latest_user_reply(run):
                        score_observation = self._execute_backend_suggested_tool(
                            run,
                            "score_candidate_match",
                            {"candidate_key": "all"},
                            reason="score_all_after_individual_candidate_scores_before_final",
                            index=1,
                        )
                        scored_result = score_observation.get("result") or {}
                        if scored_result.get("status") == "completed" and not scored_result.get("score_deferred"):
                            report_observation = self._execute_backend_suggested_tool(
                                run,
                                "write_purchase_report",
                                {"candidate_key": "all"},
                                reason="write_all_report_after_individual_candidate_scores_before_final",
                                index=2,
                            )
                            report_result = report_observation.get("result") or {}
                            report_text = str(report_result.get("final_report") or "").strip()
                            if (
                                report_result.get("status") == "completed"
                                and report_result.get("grounded") is not False
                                and report_text
                            ):
                                run["status"] = "completed"
                                run["error"] = None
                                run["completed_at"] = _completed_at()
                                run["state"]["pending_tool_outputs"] = []
                                _reset_provider_response_context_after_backend_terminal(
                                    run,
                                    reason="individual_candidate_scores_aggregated_before_final",
                                    call_id=report_observation.get("call_id"),
                                )
                                _store_completed_report_followups(run, report_result)
                                run["final_report"] = {
                                    "summary": report_text,
                                    "grounded": True,
                                    "grounding": "write_purchase_report_all_after_individual_candidate_scores",
                                    "grounding_validation": report_result.get("grounding_validation"),
                                    "report_type": report_result.get("report_type"),
                                    **_report_metadata_from_tool_result(report_result),
                                    "llm_final_text": final_text,
                                }
                                append_trace(
                                    run,
                                    step_type="guardrail",
                                    name="complete_all_report_after_individual_candidate_scores",
                                    status="completed",
                                    output_payload={
                                        "score_observation_call_id": score_observation.get("call_id"),
                                        "report_observation_call_id": report_observation.get("call_id"),
                                        "llm_final_text_present": bool(final_text),
                                    },
                                )
                                return self.store.update_run(run)
                    run["status"] = "failed"
                    run["error"] = {
                        "code": "FINAL_REPORT_TOOL_REQUIRED",
                        "message": (
                            "Agent final text was rejected because write_purchase_report "
                            "did not complete after the latest user input."
                        ),
                    }
                    append_trace(
                        run,
                        step_type="guardrail",
                        name="require_grounded_report_tool_before_final",
                        status="failed",
                        output_payload=run["error"],
                    )
                    return self.store.update_run(run)
                report_result = report_observation.get("result") or {}
                if report_result.get("status") == "waiting_for_user":
                    run["status"] = "waiting_for_user"
                    run["error"] = None
                    run["state"]["pending_tool_outputs"] = []
                    append_trace(
                        run,
                        step_type="guardrail",
                        name="adopt_report_missing_info_question",
                        status="waiting_for_user",
                        output_payload={
                            "report_observation_call_id": report_observation.get("call_id"),
                            "missing_fields": report_result.get("missing_fields", []),
                        },
                    )
                    return self.store.update_run(run)
                if report_result.get("status") != "completed" or report_result.get("grounded") is False:
                    guidance = _deferred_enrichment_guidance(report_result)
                    if guidance:
                        pending_tool_outputs = _append_backend_runtime_guidance(
                            run,
                            pending_tool_outputs,
                            guidance,
                            trace_name="continue_report_enrichment",
                        )
                        if _guidance_should_run_immediate_backend_recovery(guidance):
                            recovery = self._execute_backend_recovery_from_guidance(
                                run,
                                pending_tool_outputs,
                                rejected_final_text=final_text,
                            )
                            if recovery.get("executed"):
                                pending_tool_outputs = recovery["pending_tool_outputs"]
                                if run.get("status") in {"completed", "failed", "waiting_for_user", "configuration_required"}:
                                    return self.store.update_run(run)
                        continue
                    run["status"] = "failed"
                    run["error"] = {
                        "code": "FINAL_REPORT_TOOL_NOT_GROUNDED",
                        "message": "Agent final text was rejected because the latest write_purchase_report result was not grounded.",
                        "write_purchase_report": _summarize_tool_output(report_result),
                    }
                    append_trace(
                        run,
                        step_type="guardrail",
                        name="require_grounded_report_tool_before_final",
                        status="failed",
                        output_payload=run["error"],
                    )
                    return self.store.update_run(run)
                if _single_candidate_report_should_be_aggregated(run, report_result):
                    score_observation = self._execute_backend_suggested_tool(
                        run,
                        "score_candidate_match",
                        {"candidate_key": "all"},
                        reason="score_all_after_single_candidate_report_with_multiple_search_candidates",
                        index=1,
                    )
                    scored_result = score_observation.get("result") or {}
                    if scored_result.get("status") == "completed" and not scored_result.get("score_deferred"):
                        aggregate_report_observation = self._execute_backend_suggested_tool(
                            run,
                            "write_purchase_report",
                            {"candidate_key": "all"},
                            reason="write_all_report_after_single_candidate_report_with_multiple_search_candidates",
                            index=2,
                        )
                        aggregate_report_result = aggregate_report_observation.get("result") or {}
                        aggregate_report_text = str(aggregate_report_result.get("final_report") or "").strip()
                        if (
                            aggregate_report_result.get("status") == "completed"
                            and aggregate_report_result.get("grounded") is not False
                            and aggregate_report_text
                        ):
                            run["status"] = "completed"
                            run["error"] = None
                            run["completed_at"] = _completed_at()
                            run["state"]["pending_tool_outputs"] = []
                            _reset_provider_response_context_after_backend_terminal(
                                run,
                                reason="single_candidate_report_aggregated_before_final",
                                call_id=aggregate_report_observation.get("call_id"),
                            )
                            _store_completed_report_followups(run, aggregate_report_result)
                            run["final_report"] = {
                                "summary": aggregate_report_text,
                                "grounded": True,
                                "grounding": "write_purchase_report_all_after_single_candidate_report",
                                "grounding_validation": aggregate_report_result.get("grounding_validation"),
                                "report_type": aggregate_report_result.get("report_type"),
                                **_report_metadata_from_tool_result(aggregate_report_result),
                                "llm_final_text": final_text,
                            }
                            append_trace(
                                run,
                                step_type="guardrail",
                                name="complete_all_report_after_single_candidate_report",
                                status="completed",
                                output_payload={
                                    "single_report_observation_call_id": report_observation.get("call_id"),
                                    "score_observation_call_id": score_observation.get("call_id"),
                                    "aggregate_report_observation_call_id": aggregate_report_observation.get("call_id"),
                                    "latest_search_candidate_count": _latest_search_candidate_count_after_latest_user_reply(run),
                                    "llm_final_text_present": bool(final_text),
                                },
                            )
                            return self.store.update_run(run)
                    guidance = _deferred_enrichment_guidance(scored_result)
                    if guidance:
                        pending_tool_outputs = _append_backend_runtime_guidance(
                            run,
                            pending_tool_outputs,
                            guidance,
                            trace_name="continue_aggregate_report_enrichment",
                        )
                        continue
                tool_report_text = str(report_result.get("final_report") or "").strip()
                if tool_report_text:
                    run["status"] = "completed"
                    run["error"] = None
                    run["completed_at"] = _completed_at()
                    _store_completed_report_followups(run, report_result)
                    run["final_report"] = {
                        "summary": tool_report_text,
                        "grounded": True,
                        "grounding": "write_purchase_report_tool",
                        "grounding_validation": report_result.get("grounding_validation"),
                        "report_type": report_result.get("report_type"),
                        **_report_metadata_from_tool_result(report_result),
                        "llm_final_text": final_text,
                    }
                    append_trace(
                        run,
                        step_type="guardrail",
                        name="adopt_grounded_report_tool_output",
                        status="completed",
                        output_payload={
                            "report_observation_call_id": report_observation.get("call_id"),
                            "llm_final_text_present": bool(final_text),
                        },
                    )
                    return self.store.update_run(run)
                guidance = _deferred_enrichment_guidance(report_result)
                if guidance:
                    pending_tool_outputs = _append_backend_runtime_guidance(
                        run,
                        pending_tool_outputs,
                        guidance,
                        trace_name="continue_report_enrichment",
                    )
                    continue
                if not final_text:
                    run["status"] = "failed"
                    run["error"] = {
                        "code": "EMPTY_AGENT_RESPONSE",
                        "message": "LLM returned neither tool calls nor final text, and write_purchase_report had no final_report text.",
                    }
                    return self.store.update_run(run)
                grounding = validate_report_grounding(report_text=final_text, run=run)
                if not grounding["valid"]:
                    run["status"] = "failed"
                    run["error"] = {
                        "code": "FINAL_REPORT_GROUNDING_FAILED",
                        "grounding": grounding,
                    }
                    append_trace(
                        run,
                        step_type="guardrail",
                        name="validate_final_report_grounding",
                        status="failed",
                        output_payload=run["error"],
                    )
                    return self.store.update_run(run)
                run["status"] = "completed"
                run["error"] = None
                run["completed_at"] = _completed_at()
                run["final_report"] = {
                    "summary": final_text,
                    "grounded": grounding["valid"],
                    "grounding": "llm_final_after_tool_observations",
                    "grounding_validation": grounding,
                }
                return self.store.update_run(run)

            pending_tool_outputs = list(response_context_items)
            parallel_tool_name = _parallel_candidate_tool_batch_name(tool_calls)
            parallel_results_by_call_id: dict[str, dict[str, Any]] = {}
            if parallel_tool_name:
                parallel_run_snapshot = copy.deepcopy(run)
                append_trace(
                    run,
                    step_type="runtime",
                    name="execute_parallel_tool_batch",
                    status="started",
                    input_payload={
                        "tool": parallel_tool_name,
                        "call_count": len(tool_calls),
                        "parallel_group": "candidate_analysis",
                        "policy": "same_tool_candidate_batch",
                    },
                )
                for call in tool_calls:
                    tool_definition = _maybe_tool_definition(self.tool_registry, call["name"])
                    append_trace(
                        run,
                        step_type="tool_call",
                        name=call["name"],
                        status="started",
                        input_payload={
                            "arguments": call["arguments"],
                            "arguments_parse_error": call.get("arguments_parse_error"),
                            "call_id": call["call_id"],
                            "parallel_group": "candidate_analysis",
                            **_tool_definition_metadata(tool_definition),
                        },
                    )
                    _mark_execution_plan_tool(run, call["name"], "in_progress")
                self._checkpoint(run)
                parallel_results_by_call_id = self._execute_parallel_candidate_tool_batch(
                    parallel_run_snapshot,
                    tool_calls,
                )
                append_trace(
                    run,
                    step_type="runtime",
                    name="execute_parallel_tool_batch",
                    status="completed",
                    output_payload={
                        "tool": parallel_tool_name,
                        "call_count": len(tool_calls),
                        "parallel_group": "candidate_analysis",
                    },
                )
                self._checkpoint(run)
            for call in tool_calls:
                tool_name = call["name"]
                arguments = call["arguments"]
                tool_definition = _maybe_tool_definition(self.tool_registry, tool_name)
                if parallel_tool_name:
                    parallel_record = parallel_results_by_call_id.get(call["call_id"], {})
                    result = parallel_record.get("result") or {
                        "status": "failed",
                        "error": {
                            "code": "PARALLEL_TOOL_RESULT_MISSING",
                            "message": f"Parallel execution did not return a result for call_id {call['call_id']}.",
                        },
                    }
                    status = str(parallel_record.get("status") or result.get("status") or "completed")
                    tool_duration_ms = int(parallel_record.get("duration_ms") or 0)
                    _merge_parallel_tool_state(run, parallel_record.get("state") or {}, call)
                else:
                    append_trace(
                        run,
                        step_type="tool_call",
                        name=tool_name,
                        status="started",
                        input_payload={
                            "arguments": arguments,
                            "arguments_parse_error": call.get("arguments_parse_error"),
                            "call_id": call["call_id"],
                            **_tool_definition_metadata(tool_definition),
                        },
                    )
                    _mark_execution_plan_tool(run, tool_name, "in_progress")
                    self._checkpoint(run)
                    tool_start = perf_counter()
                    if call.get("arguments_parse_error"):
                        result = _tool_argument_json_parse_failed(tool_name, call)
                        status = "failed"
                    else:
                        try:
                            result = self._execute_tool(
                                run,
                                tool_name,
                                arguments,
                                tool_definition=tool_definition,
                                call_id=call["call_id"],
                            )
                            status = result.get("status", "completed")
                        except Exception as exc:
                            result = {
                                "status": "failed",
                                "error": {
                                    "code": "TOOL_EXECUTION_FAILED",
                                    "message": str(exc),
                                },
                            }
                            status = "failed"
                    tool_duration_ms = round((perf_counter() - tool_start) * 1000)
                tool_metadata = {
                    **_tool_definition_metadata(tool_definition),
                    "duration_ms": tool_duration_ms,
                    **({"parallel_group": "candidate_analysis"} if parallel_tool_name else {}),
                }

                observation = {
                    "tool": tool_name,
                    "call_id": call["call_id"],
                    "arguments": arguments,
                    "result": result,
                    **tool_metadata,
                    **_observation_metadata(result),
                }
                run.setdefault("state", {}).setdefault("observations", []).append(observation)
                append_trace(
                    run,
                    step_type="tool_output",
                    name=tool_name,
                    status=status,
                    output_payload={
                        "call_id": call["call_id"],
                        **_summarize_tool_output(result),
                        **tool_metadata,
                    },
                )
                _mark_execution_plan_tool(run, tool_name, status)
                self._checkpoint(run)

                if status == "failed":
                    if tool_name == "search_web_candidates":
                        provider_error_metadata = provider_error_metadata_from_result(result)
                        run["status"] = "failed"
                        run["error"] = {
                            "code": "SEARCH_PROVIDER_FAILED",
                            "message": "Search provider failed before candidate discovery could continue.",
                            "tool": tool_name,
                            "provider_error": result.get("error"),
                            **provider_error_metadata,
                        }
                        run["state"]["pending_tool_outputs"] = []
                        append_trace(
                            run,
                            step_type="guardrail",
                            name="stop_search_provider_failed",
                            status="failed",
                            output_payload=run["error"],
                        )
                        return self.store.update_run(run)
                    repeated_failure = _repeated_failed_tool_call(run, observation)
                    if repeated_failure:
                        run["status"] = "failed"
                        run["error"] = {
                            "code": "REPEATED_FAILED_TOOL_CALL",
                            "message": "Agent repeated the same failed backend tool call without correcting it.",
                            **repeated_failure,
                        }
                        run["state"]["pending_tool_outputs"] = []
                        append_trace(
                            run,
                            step_type="guardrail",
                            name="stop_repeated_failed_tool_call",
                            status="failed",
                            output_payload=run["error"],
                        )
                        return self.store.update_run(run)

                if status == "configuration_required":
                    run["status"] = "configuration_required"
                    provider_error_metadata = provider_error_metadata_from_result(result)
                    run["error"] = {
                        **(
                            result.get("error")
                            or {
                                "code": "TOOL_CONFIGURATION_REQUIRED",
                                "message": f"Backend tool {tool_name} requires configuration before continuing.",
                            }
                        ),
                        **provider_error_metadata,
                    }
                    run["state"]["pending_tool_outputs"] = []
                    append_trace(
                        run,
                        step_type="guardrail",
                        name="stop_tool_configuration_required",
                        status="configuration_required",
                        output_payload={
                            "tool": tool_name,
                            "error": run["error"],
                            "required_configuration": result.get("required_configuration", []),
                        },
                    )
                    return self.store.update_run(run)

                if status == "waiting_for_user":
                    run["status"] = "waiting_for_user"
                    run["error"] = None
                    pending_tool_outputs.append(_function_call_output(call["call_id"], result))
                    run["state"]["pending_tool_outputs"] = pending_tool_outputs
                    questions = result.get("questions") or result.get("next_questions") or []
                    if questions:
                        run["state"]["missing_info"] = questions
                    return self.store.update_run(run)

                pending_tool_outputs.append(_function_call_output(call["call_id"], result))
                if _deferred_score_requires_user_confirmation(result):
                    run["status"] = "waiting_for_user"
                    run["error"] = None
                    run["state"]["pending_tool_outputs"] = []
                    questions = result.get("questions") or result.get("next_questions") or []
                    if questions:
                        run["state"]["missing_info"] = questions
                    append_trace(
                        run,
                        step_type="guardrail",
                        name="adopt_deferred_score_missing_info_question",
                        status="waiting_for_user",
                        output_payload={
                            "tool": tool_name,
                            "call_id": call["call_id"],
                            "reason": result.get("reason"),
                            "evidence_status": result.get("evidence_status"),
                        },
                    )
                    return self.store.update_run(run)
                guidance = _deferred_enrichment_guidance(result)
                if guidance:
                    repeated_empty_search = _repeated_empty_search(run, observation)
                    if repeated_empty_search:
                        if self._complete_with_backend_no_result_report(
                            run,
                            rejected_final_text="",
                            reason="repeated_empty_search_after_backend_expansion",
                        ):
                            append_trace(
                                run,
                                step_type="guardrail",
                                name="stop_repeated_empty_search",
                                status="completed",
                                output_payload=repeated_empty_search,
                            )
                            return self.store.update_run(run)
                    empty_search_limit = _empty_search_attempt_limit_exceeded(run, observation)
                    if empty_search_limit:
                        if self._complete_with_backend_no_result_report(
                            run,
                            rejected_final_text="",
                            reason="empty_search_attempt_limit_exhausted",
                        ):
                            append_trace(
                                run,
                                step_type="guardrail",
                                name="stop_empty_search_attempt_limit",
                                status="completed",
                                output_payload=empty_search_limit,
                            )
                            return self.store.update_run(run)
                    repeated_deferred = _repeated_deferred_user_question(run, observation)
                    if repeated_deferred:
                        run["status"] = "failed"
                        run["error"] = {
                            "code": "REPEATED_PREMATURE_MISSING_INFO",
                            "message": "Agent repeated ask_missing_info before executing suggested enrichment tools.",
                            **repeated_deferred,
                        }
                        run["state"]["pending_tool_outputs"] = []
                        append_trace(
                            run,
                            step_type="guardrail",
                            name="stop_repeated_premature_missing_info",
                            status="failed",
                            output_payload=run["error"],
                        )
                        return self.store.update_run(run)
                    pending_tool_outputs = _append_backend_runtime_guidance(
                        run,
                        pending_tool_outputs,
                        guidance,
                        trace_name="continue_candidate_enrichment",
                    )
                    if _guidance_should_run_immediate_backend_recovery(guidance):
                        recovery = self._execute_backend_recovery_from_guidance(
                            run,
                            pending_tool_outputs,
                            rejected_final_text="",
                        )
                        if recovery.get("executed"):
                            pending_tool_outputs = recovery["pending_tool_outputs"]
                            if run.get("status") in {"completed", "failed", "waiting_for_user", "configuration_required"}:
                                return self.store.update_run(run)

            run["state"]["pending_tool_outputs"] = pending_tool_outputs
            self.store.update_run(run)

        if _latest_empty_search_after_latest_user_reply(run) and self._complete_with_backend_no_result_report(
            run,
            rejected_final_text="",
            reason="max_turns_after_exhausted_search",
        ):
            return self.store.update_run(run)
        deferred_recovery_run = self._recover_deferred_score_before_max_turn_report(run)
        if deferred_recovery_run is not None:
            return deferred_recovery_run
        completed_score = _latest_completed_score_observation_after_latest_user_reply(run)
        if (
            completed_score
            and str((completed_score.get("result") or {}).get("candidate_key") or (completed_score.get("arguments") or {}).get("candidate_key") or "").lower()
            not in {"all", "*", "__all__"}
            and _latest_search_candidate_count_after_latest_user_reply(run) >= 2
        ):
            all_score_observation = self._execute_backend_suggested_tool(
                run,
                "score_candidate_match",
                {"candidate_key": "all"},
                reason="score_all_after_max_turns_individual_candidate_score_with_multiple_search_candidates",
                index=1,
            )
            all_score_result = all_score_observation.get("result") or {}
            if all_score_result.get("status") == "completed":
                completed_score = all_score_observation
        if completed_score and (completed_score.get("result") or {}).get("score_deferred"):
            deferred_recovery_run = self._recover_deferred_score_before_max_turn_report(run)
            if deferred_recovery_run is not None:
                return deferred_recovery_run
        if completed_score:
            report_observation = self._execute_backend_required_report_tool(
                run,
                completed_score,
                rejected_final_text="",
                reason="max_turns_after_completed_score",
            )
            report_result = report_observation.get("result") or {}
            if report_result.get("status") == "completed" and report_result.get("grounded") is not False:
                if _single_candidate_report_should_be_aggregated(run, report_result):
                    score_observation = self._execute_backend_suggested_tool(
                        run,
                        "score_candidate_match",
                        {"candidate_key": "all"},
                        reason="score_all_after_max_turns_single_candidate_report_with_multiple_search_candidates",
                        index=1,
                    )
                    scored_result = score_observation.get("result") or {}
                    if scored_result.get("status") == "completed" and not scored_result.get("score_deferred"):
                        aggregate_report_observation = self._execute_backend_suggested_tool(
                            run,
                            "write_purchase_report",
                            {"candidate_key": "all"},
                            reason="write_all_report_after_max_turns_single_candidate_report_with_multiple_search_candidates",
                            index=2,
                        )
                        aggregate_report_result = aggregate_report_observation.get("result") or {}
                        aggregate_report_text = str(aggregate_report_result.get("final_report") or "").strip()
                        if (
                            aggregate_report_result.get("status") == "completed"
                            and aggregate_report_result.get("grounded") is not False
                            and aggregate_report_text
                        ):
                            run["status"] = "completed"
                            run["error"] = None
                            run["completed_at"] = _completed_at()
                            _reset_provider_response_context_after_backend_terminal(
                                run,
                                reason="max_turns_single_candidate_report_aggregated",
                                call_id=aggregate_report_observation.get("call_id"),
                            )
                            run.setdefault("state", {})["missing_info"] = []
                            run["final_report"] = {
                                "summary": aggregate_report_text,
                                "grounded": True,
                                "grounding": "write_purchase_report_all_after_single_candidate_report",
                                "grounding_validation": aggregate_report_result.get("grounding_validation"),
                                "report_type": aggregate_report_result.get("report_type"),
                                **_report_metadata_from_tool_result(aggregate_report_result),
                            }
                            append_trace(
                                run,
                                step_type="guardrail",
                                name="complete_all_report_after_single_candidate_report",
                                status="completed",
                                output_payload={
                                    "single_report_observation_call_id": report_observation.get("call_id"),
                                    "score_observation_call_id": score_observation.get("call_id"),
                                    "aggregate_report_observation_call_id": aggregate_report_observation.get("call_id"),
                                    "latest_search_candidate_count": _latest_search_candidate_count_after_latest_user_reply(run),
                                },
                            )
                            return self.store.update_run(run)
                report_text = str(report_result.get("final_report") or "").strip()
                if report_text:
                    report_candidate_key = str(
                        report_observation.get("arguments", {}).get("candidate_key")
                        or report_result.get("candidate_key")
                        or ""
                    ).lower()
                    is_all_candidate_report = report_candidate_key in {"all", "*", "__all__"} or report_result.get("report_type") == "shortlist"
                    run["status"] = "completed"
                    run["error"] = None
                    run["completed_at"] = _completed_at()
                    _reset_provider_response_context_after_backend_terminal(
                        run,
                        reason="max_turns_fallback_report_without_model_ack",
                        call_id=report_observation.get("call_id"),
                    )
                    run.setdefault("state", {})["missing_info"] = []
                    run["final_report"] = {
                        "summary": report_text,
                        "grounded": True,
                        "grounding": (
                            "write_purchase_report_all_from_observed_matches_after_max_turns"
                            if is_all_candidate_report
                            else "write_purchase_report_after_max_turns"
                        ),
                        "grounding_validation": report_result.get("grounding_validation"),
                        "report_type": report_result.get("report_type"),
                        **_report_metadata_from_tool_result(report_result),
                    }
                    append_trace(
                        run,
                        step_type="guardrail",
                        name=(
                            "complete_all_report_from_observed_matches_after_max_turns"
                            if is_all_candidate_report
                            else "complete_report_after_max_turns"
                        ),
                        status="completed",
                        output_payload={
                            "report_observation_call_id": report_observation.get("call_id"),
                            "report_type": report_result.get("report_type"),
                        },
                    )
                    return self.store.update_run(run)
            guidance = _deferred_enrichment_guidance(report_result)
            if guidance:
                pending_tool_outputs = _append_backend_runtime_guidance(
                    run,
                    run.setdefault("state", {}).get("pending_tool_outputs") or [],
                    guidance,
                    trace_name="max_turns_report_guidance_recovery_before_fallback",
                )
                recovery = self._execute_backend_recovery_from_guidance(
                    run,
                    pending_tool_outputs,
                    rejected_final_text="",
                )
                if run.get("status") in {"completed", "failed", "waiting_for_user", "configuration_required"}:
                    return self.store.update_run(run)
                if recovery.get("executed"):
                    recovered_deferred = self._recover_deferred_score_before_max_turn_report(run)
                    if recovered_deferred is not None:
                        return recovered_deferred
                    recovered_score = _latest_completed_score_observation_after_latest_user_reply(run)
                    if recovered_score:
                        recovered_report_observation = self._execute_backend_required_report_tool(
                            run,
                            recovered_score,
                            rejected_final_text="",
                            reason="max_turns_after_report_guidance_recovery",
                        )
                        recovered_report_result = recovered_report_observation.get("result") or {}
                        recovered_report_text = str(recovered_report_result.get("final_report") or "").strip()
                        if (
                            recovered_report_result.get("status") == "completed"
                            and recovered_report_result.get("grounded") is not False
                            and recovered_report_text
                        ):
                            report_candidate_key = str(
                                recovered_report_result.get("candidate_key")
                                or (recovered_report_observation.get("arguments") or {}).get("candidate_key")
                                or ""
                            ).lower()
                            all_candidate_report = report_candidate_key in {"all", "*", "__all__"} or recovered_report_result.get(
                                "report_type"
                            ) == "shortlist"
                            run["status"] = "completed"
                            run["error"] = None
                            run["completed_at"] = _completed_at()
                            _reset_provider_response_context_after_backend_terminal(
                                run,
                                reason="max_turns_report_guidance_recovery_completed_without_model_ack",
                                call_id=recovered_report_observation.get("call_id"),
                            )
                            run.setdefault("state", {})["missing_info"] = []
                            run["final_report"] = {
                                "summary": recovered_report_text,
                                "grounded": True,
                                "grounding": (
                                    "write_purchase_report_all_after_max_turns_report_guidance_recovery"
                                    if all_candidate_report
                                    else "write_purchase_report_after_max_turns_report_guidance_recovery"
                                ),
                                "grounding_validation": recovered_report_result.get("grounding_validation"),
                                "report_type": recovered_report_result.get("report_type"),
                                **_report_metadata_from_tool_result(recovered_report_result),
                            }
                            append_trace(
                                run,
                                step_type="guardrail",
                                name=(
                                    "complete_all_report_after_max_turns_report_guidance_recovery"
                                    if all_candidate_report
                                    else "complete_report_after_max_turns_report_guidance_recovery"
                                ),
                                status="completed",
                                output_payload={
                                    "blocked_report_observation_call_id": report_observation.get("call_id"),
                                    "score_observation_call_id": recovered_score.get("call_id"),
                                    "report_observation_call_id": recovered_report_observation.get("call_id"),
                                    "report_type": recovered_report_result.get("report_type"),
                                    "reason": guidance.get("reason"),
                                },
                            )
                            return self.store.update_run(run)
                    run.setdefault("state", {})["pending_tool_outputs"] = recovery.get("pending_tool_outputs") or []
                    return self._run_loop(self.store.update_run(run))
            fallback_report_suggestion = _write_all_report_suggestion(report_result)
            if fallback_report_suggestion:
                fallback_observation = self._execute_backend_suggested_tool(
                    run,
                    "write_purchase_report",
                    fallback_report_suggestion.get("arguments") or {"candidate_key": "all"},
                    reason=str(fallback_report_suggestion.get("reason") or "write_all_report_after_single_candidate_report_failed"),
                    index=1,
                )
                fallback_result = fallback_observation.get("result") or {}
                fallback_text = str(fallback_result.get("final_report") or "").strip()
                if (
                    fallback_result.get("status") == "completed"
                    and fallback_result.get("grounded") is not False
                    and fallback_text
                ):
                    run["status"] = "completed"
                    run["error"] = None
                    run["completed_at"] = _completed_at()
                    _reset_provider_response_context_after_backend_terminal(
                        run,
                        reason="max_turns_all_report_without_model_ack",
                        call_id=fallback_observation.get("call_id"),
                    )
                    run.setdefault("state", {})["missing_info"] = []
                    run["final_report"] = {
                        "summary": fallback_text,
                        "grounded": True,
                        "grounding": "write_purchase_report_all_after_single_candidate_report_failed",
                        "grounding_validation": fallback_result.get("grounding_validation"),
                        "report_type": fallback_result.get("report_type"),
                        **_report_metadata_from_tool_result(fallback_result),
                    }
                    append_trace(
                        run,
                        step_type="guardrail",
                        name="complete_all_report_after_single_candidate_report_failed",
                        status="completed",
                        output_payload={
                            "failed_report_observation_call_id": report_observation.get("call_id"),
                            "fallback_report_observation_call_id": fallback_observation.get("call_id"),
                            "report_type": fallback_result.get("report_type"),
                        },
                    )
                    return self.store.update_run(run)
                continued_run = self._continue_from_report_guidance_after_max_turns(
                    run,
                    fallback_result,
                    trace_name="max_turns_fallback_report_guidance_recovery",
                )
                if continued_run is not None:
                    return continued_run
            score_all_suggestion = _score_all_suggestion(report_result)
            if score_all_suggestion:
                score_observation = self._execute_backend_suggested_tool(
                    run,
                    "score_candidate_match",
                    score_all_suggestion.get("arguments") or {"candidate_key": "all"},
                    reason=str(score_all_suggestion.get("reason") or "score_latest_search_candidates_before_report"),
                    index=1,
                )
                scored_result = score_observation.get("result") or {}
                if scored_result.get("status") == "completed" and scored_result.get("score_deferred"):
                    deferred_recovery_run = self._recover_deferred_score_before_max_turn_report(run)
                    if deferred_recovery_run is not None:
                        return deferred_recovery_run
                if scored_result.get("status") == "completed" and not scored_result.get("score_deferred"):
                    fallback_observation = self._execute_backend_suggested_tool(
                        run,
                        "write_purchase_report",
                        {"candidate_key": "all"},
                        reason="write_all_report_after_max_turns_scoring_recovery",
                        index=2,
                    )
                    fallback_result = fallback_observation.get("result") or {}
                    fallback_text = str(fallback_result.get("final_report") or "").strip()
                    if (
                        fallback_result.get("status") == "completed"
                        and fallback_result.get("grounded") is not False
                        and fallback_text
                    ):
                        run["status"] = "completed"
                        run["error"] = None
                        run["completed_at"] = _completed_at()
                        _reset_provider_response_context_after_backend_terminal(
                            run,
                            reason="max_turns_scoring_recovery_report_without_model_ack",
                            call_id=fallback_observation.get("call_id"),
                        )
                        run.setdefault("state", {})["missing_info"] = []
                        run["final_report"] = {
                            "summary": fallback_text,
                            "grounded": True,
                            "grounding": "write_purchase_report_all_after_max_turns_scoring_recovery",
                            "grounding_validation": fallback_result.get("grounding_validation"),
                            "report_type": fallback_result.get("report_type"),
                            **_report_metadata_from_tool_result(fallback_result),
                        }
                        append_trace(
                            run,
                            step_type="guardrail",
                            name="complete_all_report_after_max_turns_scoring_recovery",
                            status="completed",
                            output_payload={
                                "failed_report_observation_call_id": report_observation.get("call_id"),
                                "score_observation_call_id": score_observation.get("call_id"),
                                "fallback_report_observation_call_id": fallback_observation.get("call_id"),
                                "report_type": fallback_result.get("report_type"),
                            },
                        )
                        return self.store.update_run(run)
        if run.get("state", {}).get("match_results"):
            fallback_observation = self._execute_backend_suggested_tool(
                run,
                "write_purchase_report",
                {"candidate_key": "all"},
                reason="write_all_report_from_observed_matches_after_max_turns",
                index=1,
            )
            fallback_result = fallback_observation.get("result") or {}
            fallback_text = str(fallback_result.get("final_report") or "").strip()
            if (
                fallback_result.get("status") == "completed"
                and fallback_result.get("grounded") is not False
                and fallback_text
            ):
                report_candidate_key = str(
                    fallback_result.get("candidate_key")
                    or (fallback_observation.get("arguments") or {}).get("candidate_key")
                    or ""
                ).lower()
                all_candidate_report = report_candidate_key in {"all", "*", "__all__"} or fallback_result.get(
                    "report_type"
                ) == "shortlist"
                run["status"] = "completed"
                run["error"] = None
                run["completed_at"] = _completed_at()
                _reset_provider_response_context_after_backend_terminal(
                    run,
                    reason="max_turns_observed_matches_report_without_model_ack",
                    call_id=fallback_observation.get("call_id"),
                )
                run.setdefault("state", {})["missing_info"] = []
                run["final_report"] = {
                    "summary": fallback_text,
                    "grounded": True,
                    "grounding": "write_purchase_report_all_from_observed_matches_after_max_turns",
                    "grounding_validation": fallback_result.get("grounding_validation"),
                    "report_type": fallback_result.get("report_type"),
                    **_report_metadata_from_tool_result(fallback_result),
                }
                append_trace(
                    run,
                    step_type="guardrail",
                    name="complete_all_report_from_observed_matches_after_max_turns",
                    status="completed",
                    output_payload={
                        "fallback_report_observation_call_id": fallback_observation.get("call_id"),
                        "report_type": fallback_result.get("report_type"),
                    },
                )
                return self.store.update_run(run)
            continued_run = self._continue_from_report_guidance_after_max_turns(
                run,
                fallback_result,
                trace_name="max_turns_observed_matches_report_guidance_recovery",
            )
            if continued_run is not None:
                return continued_run
            score_all_suggestion = _score_all_suggestion(fallback_result)
            if score_all_suggestion:
                score_observation = self._execute_backend_suggested_tool(
                    run,
                    "score_candidate_match",
                    score_all_suggestion.get("arguments") or {"candidate_key": "all"},
                    reason=str(score_all_suggestion.get("reason") or "score_latest_search_candidates_before_report"),
                    index=2,
                )
                scored_result = score_observation.get("result") or {}
                if scored_result.get("status") == "completed" and scored_result.get("score_deferred"):
                    deferred_recovery_run = self._recover_deferred_score_before_max_turn_report(run)
                    if deferred_recovery_run is not None:
                        return deferred_recovery_run
                if scored_result.get("status") == "completed" and not scored_result.get("score_deferred"):
                    second_report_observation = self._execute_backend_suggested_tool(
                        run,
                        "write_purchase_report",
                        {"candidate_key": "all"},
                        reason="write_all_report_after_max_turns_latest_search_score",
                        index=3,
                    )
                    second_report_result = second_report_observation.get("result") or {}
                    second_report_text = str(second_report_result.get("final_report") or "").strip()
                    if (
                        second_report_result.get("status") == "completed"
                        and second_report_result.get("grounded") is not False
                        and second_report_text
                    ):
                        run["status"] = "completed"
                        run["error"] = None
                        run["completed_at"] = _completed_at()
                        _reset_provider_response_context_after_backend_terminal(
                            run,
                            reason="max_turns_latest_search_score_report_without_model_ack",
                            call_id=second_report_observation.get("call_id"),
                        )
                        run.setdefault("state", {})["missing_info"] = []
                        run["final_report"] = {
                            "summary": second_report_text,
                            "grounded": True,
                            "grounding": "write_purchase_report_all_after_max_turns_latest_search_score",
                            "grounding_validation": second_report_result.get("grounding_validation"),
                            "report_type": second_report_result.get("report_type"),
                            **_report_metadata_from_tool_result(second_report_result),
                        }
                        append_trace(
                            run,
                            step_type="guardrail",
                            name="complete_all_report_after_max_turns_latest_search_score",
                            status="completed",
                            output_payload={
                                "failed_report_observation_call_id": fallback_observation.get("call_id"),
                                "score_observation_call_id": score_observation.get("call_id"),
                                "fallback_report_observation_call_id": second_report_observation.get("call_id"),
                                "report_type": second_report_result.get("report_type"),
                            },
                        )
                        return self.store.update_run(run)
                    continued_run = self._continue_from_report_guidance_after_max_turns(
                        run,
                        second_report_result,
                        trace_name="max_turns_latest_search_report_guidance_recovery",
                    )
                    if continued_run is not None:
                        return continued_run
        if _latest_search_has_normalized_candidates_after_latest_user_reply(run):
            score_observation = self._execute_backend_suggested_tool(
                run,
                "score_candidate_match",
                {"candidate_key": "all"},
                reason="score_normalized_candidates_after_max_turns",
                index=1,
            )
            scored_result = score_observation.get("result") or {}
            if scored_result.get("status") == "completed" and scored_result.get("score_deferred"):
                deferred_recovery_run = self._recover_deferred_score_before_max_turn_report(run)
                if deferred_recovery_run is not None:
                    return deferred_recovery_run
            if scored_result.get("status") == "completed" and not scored_result.get("score_deferred"):
                report_observation = self._execute_backend_suggested_tool(
                    run,
                    "write_purchase_report",
                    {"candidate_key": "all"},
                    reason="write_all_report_after_max_turns_normalized_candidate_score",
                    index=2,
                )
                report_result = report_observation.get("result") or {}
                report_text = str(report_result.get("final_report") or "").strip()
                if (
                    report_result.get("status") == "completed"
                    and report_result.get("grounded") is not False
                    and report_text
                ):
                    run["status"] = "completed"
                    run["error"] = None
                    run["completed_at"] = _completed_at()
                    _reset_provider_response_context_after_backend_terminal(
                        run,
                        reason="max_turns_normalized_candidates_report_without_model_ack",
                        call_id=report_observation.get("call_id"),
                    )
                    run.setdefault("state", {})["missing_info"] = []
                    run["final_report"] = {
                        "summary": report_text,
                        "grounded": True,
                        "grounding": "write_purchase_report_all_after_max_turns_normalized_candidate_score",
                        "grounding_validation": report_result.get("grounding_validation"),
                        "report_type": report_result.get("report_type"),
                        **_report_metadata_from_tool_result(report_result),
                    }
                    append_trace(
                        run,
                        step_type="guardrail",
                        name="complete_all_report_after_max_turns_normalized_candidate_score",
                        status="completed",
                        output_payload={
                            "score_observation_call_id": score_observation.get("call_id"),
                            "report_observation_call_id": report_observation.get("call_id"),
                            "report_type": report_result.get("report_type"),
                        },
                    )
                    return self.store.update_run(run)
                continued_run = self._continue_from_report_guidance_after_max_turns(
                    run,
                    report_result,
                    trace_name="max_turns_normalized_candidate_report_guidance_recovery",
                )
                if continued_run is not None:
                    return continued_run
        if _has_normalized_candidates_after_latest_user_reply(run):
            score_observation = self._execute_backend_suggested_tool(
                run,
                "score_candidate_match",
                {"candidate_key": "all"},
                reason="score_observed_normalized_candidates_after_max_turns",
                index=1,
            )
            scored_result = score_observation.get("result") or {}
            if scored_result.get("status") == "completed" and scored_result.get("score_deferred"):
                deferred_recovery_run = self._recover_deferred_score_before_max_turn_report(run)
                if deferred_recovery_run is not None:
                    return deferred_recovery_run
            if scored_result.get("status") == "completed" and not scored_result.get("score_deferred"):
                report_observation = self._execute_backend_suggested_tool(
                    run,
                    "write_purchase_report",
                    {"candidate_key": "all"},
                    reason="write_all_report_after_max_turns_observed_normalized_candidate_score",
                    index=2,
                )
                report_result = report_observation.get("result") or {}
                report_text = str(report_result.get("final_report") or "").strip()
                if (
                    report_result.get("status") == "completed"
                    and report_result.get("grounded") is not False
                    and report_text
                ):
                    run["status"] = "completed"
                    run["error"] = None
                    run["completed_at"] = _completed_at()
                    _reset_provider_response_context_after_backend_terminal(
                        run,
                        reason="max_turns_observed_normalized_candidates_report_without_model_ack",
                        call_id=report_observation.get("call_id"),
                    )
                    run.setdefault("state", {})["missing_info"] = []
                    run["final_report"] = {
                        "summary": report_text,
                        "grounded": True,
                        "grounding": "write_purchase_report_all_after_max_turns_observed_normalized_candidate_score",
                        "grounding_validation": report_result.get("grounding_validation"),
                        "report_type": report_result.get("report_type"),
                        **_report_metadata_from_tool_result(report_result),
                    }
                    append_trace(
                        run,
                        step_type="guardrail",
                        name="complete_all_report_after_max_turns_observed_normalized_candidate_score",
                        status="completed",
                        output_payload={
                            "score_observation_call_id": score_observation.get("call_id"),
                            "report_observation_call_id": report_observation.get("call_id"),
                            "report_type": report_result.get("report_type"),
                        },
                    )
                    return self.store.update_run(run)
        deferred_score = _latest_deferred_score_observation_after_latest_user_reply(run)
        if deferred_score:
            guidance = _deferred_enrichment_guidance(deferred_score.get("result") or {})
            if guidance:
                pending_tool_outputs = _append_backend_runtime_guidance(
                    run,
                    run.setdefault("state", {}).get("pending_tool_outputs") or [],
                    guidance,
                    trace_name="max_turns_deferred_score_recovery",
                )
                recovery = self._execute_backend_recovery_from_guidance(
                    run,
                    pending_tool_outputs,
                    rejected_final_text="",
                )
                if recovery.get("executed") and run.get("status") not in {
                    "completed",
                    "failed",
                    "waiting_for_user",
                    "configuration_required",
                }:
                    completed_score = _latest_completed_score_observation_after_latest_user_reply(run)
                    if completed_score:
                        report_observation = self._execute_backend_required_report_tool(
                            run,
                            completed_score,
                            rejected_final_text="",
                            reason="max_turns_after_deferred_score_recovery",
                        )
                        report_result = report_observation.get("result") or {}
                        report_text = str(report_result.get("final_report") or "").strip()
                        if (
                            report_result.get("status") == "completed"
                            and report_result.get("grounded") is not False
                            and report_text
                        ):
                            run["status"] = "completed"
                            run["error"] = None
                            run["completed_at"] = _completed_at()
                            _reset_provider_response_context_after_backend_terminal(
                                run,
                                reason="max_turns_deferred_score_recovery_report_without_model_ack",
                                call_id=report_observation.get("call_id"),
                            )
                            run.setdefault("state", {})["missing_info"] = []
                            run["final_report"] = {
                                "summary": report_text,
                                "grounded": True,
                                "grounding": "write_purchase_report_after_max_turns_deferred_score_recovery",
                                "grounding_validation": report_result.get("grounding_validation"),
                                "report_type": report_result.get("report_type"),
                                **_report_metadata_from_tool_result(report_result),
                            }
                            append_trace(
                                run,
                                step_type="guardrail",
                                name="complete_report_after_max_turns_deferred_score_recovery",
                                status="completed",
                                output_payload={
                                    "deferred_score_observation_call_id": deferred_score.get("call_id"),
                                    "score_observation_call_id": completed_score.get("call_id"),
                                    "report_observation_call_id": report_observation.get("call_id"),
                                    "report_type": report_result.get("report_type"),
                                },
                            )
                            return self.store.update_run(run)
                    run["state"]["pending_tool_outputs"] = recovery.get("pending_tool_outputs") or []
        run["status"] = "failed"
        run["error"] = {
            "code": "MAX_AGENT_TURNS_EXCEEDED",
            "message": f"Exceeded max_turns={self.max_turns}",
            "max_turns": self.max_turns,
        }
        run.setdefault("state", {})["pending_tool_outputs"] = []
        append_trace(
            run,
            step_type="guardrail",
            name="stop_max_agent_turns_exceeded",
            status="failed",
            output_payload=run["error"],
        )
        return self.store.update_run(run)

    def _checkpoint(self, run: dict[str, Any]) -> None:
        self.store.update_run(run)

    def _execute_tool(
        self,
        run: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any],
        *,
        tool_definition=None,
        call_id: str | None = None,
    ) -> dict[str, Any]:
        start = perf_counter()
        _record_tool_lifecycle_hook(
            run,
            hook_event="PreToolUse",
            tool_name=tool_name,
            call_id=call_id,
            status="completed",
            input_payload={"arguments": arguments},
        )
        # Caller-owned run controls: permission gate and tool budget (docs/24 #9/#10).
        permission_denial = _tool_permission_denial(run, tool_name)
        if permission_denial is not None:
            _record_tool_lifecycle_hook(
                run,
                hook_event="PostToolUseFailure",
                tool_name=tool_name,
                call_id=call_id,
                status="failed",
                duration_ms=round((perf_counter() - start) * 1000),
                output_payload={"error": permission_denial["error"]},
            )
            return permission_denial
        if tool_name not in ALWAYS_ALLOWED_TOOLS and _tool_budget_exceeded(run):
            budget_denial = {
                "status": "failed",
                "error": {
                    "code": "TOOL_BUDGET_EXCEEDED",
                    "message": (
                        "The caller-set per-run tool budget has been exhausted; "
                        "no further tool calls will run."
                    ),
                    "tool": tool_name,
                },
            }
            _record_tool_lifecycle_hook(
                run,
                hook_event="PostToolUseFailure",
                tool_name=tool_name,
                call_id=call_id,
                status="failed",
                duration_ms=round((perf_counter() - start) * 1000),
                output_payload={"error": budget_denial["error"]},
            )
            return budget_denial
        state = run.setdefault("state", {})
        state["tool_calls_executed"] = int(state.get("tool_calls_executed") or 0) + 1
        try:
            if tool_definition is None:
                result = {
                    "status": "failed",
                    "error": {
                        "code": "UNKNOWN_TOOL",
                        "message": f"Model requested unknown backend tool: {tool_name}",
                        "tool": tool_name,
                        "available_tools": self.tool_registry.names(),
                    },
                }
                _record_tool_lifecycle_hook(
                    run,
                    hook_event="PostToolUseFailure",
                    tool_name=tool_name,
                    call_id=call_id,
                    status="failed",
                    duration_ms=round((perf_counter() - start) * 1000),
                    output_payload={"error": result["error"]},
                )
                return result
            tool = tool_definition or self.tool_registry.get(tool_name)
            context = ToolContext(
                web_result_path=self.web_result_path,
                keyword_result_path=self.keyword_result_path,
                refresh_web_candidates=self.refresh_web_candidates,
                run=run,
                memory_store=self.store,
            )
            timeout_seconds = getattr(tool, "timeout_seconds", None)
            if not timeout_seconds or timeout_seconds <= 0:
                result = tool.execute(arguments, context)
            else:
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"fitcheck_tool_{tool_name}")
                future = executor.submit(tool.execute, arguments, context)
                try:
                    result = future.result(timeout=float(timeout_seconds))
                except TimeoutError:
                    future.cancel()
                    result = _tool_timeout_failed(tool_name, timeout_seconds)
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)
        except Exception as exc:
            _record_tool_lifecycle_hook(
                run,
                hook_event="PostToolUseFailure",
                tool_name=tool_name,
                call_id=call_id,
                status="failed",
                duration_ms=round((perf_counter() - start) * 1000),
                output_payload={
                    "error": {
                        "code": "TOOL_EXECUTION_FAILED",
                        "message": str(exc),
                    }
                },
            )
            raise
        status = str(result.get("status") or "completed")
        _record_tool_lifecycle_hook(
            run,
            hook_event="PostToolUse" if status == "completed" else "PostToolUseFailure",
            tool_name=tool_name,
            call_id=call_id,
            status=status,
            duration_ms=round((perf_counter() - start) * 1000),
            output_payload=_summarize_tool_output(result),
        )
        return result

    def _execute_parallel_candidate_tool_batch(
        self,
        run_snapshot: dict[str, Any],
        tool_calls: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        max_workers = min(4, len(tool_calls))

        def execute_one(call: dict[str, Any]) -> dict[str, Any]:
            tool_name = call["name"]
            arguments = call["arguments"]
            tool_definition = _maybe_tool_definition(self.tool_registry, tool_name)
            worker_run = copy.deepcopy(run_snapshot)
            tool_start = perf_counter()
            try:
                result = self._execute_tool(
                    worker_run,
                    tool_name,
                    arguments,
                    tool_definition=tool_definition,
                    call_id=call["call_id"],
                )
                status = result.get("status", "completed")
            except Exception as exc:
                result = {
                    "status": "failed",
                    "error": {
                        "code": "TOOL_EXECUTION_FAILED",
                        "message": str(exc),
                    },
                }
                status = "failed"
            return {
                "call_id": call["call_id"],
                "result": result,
                "status": status,
                "duration_ms": round((perf_counter() - tool_start) * 1000),
                "state": worker_run.get("state", {}),
            }

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="fitcheck_parallel_tool") as executor:
            futures = [executor.submit(execute_one, call) for call in tool_calls]
            records = [future.result() for future in futures]
        return {str(record["call_id"]): record for record in records}

    def _execute_backend_required_report_tool(
        self,
        run: dict[str, Any],
        score_observation: dict[str, Any] | None,
        *,
        rejected_final_text: str,
        reason: str = "model_final_text_without_required_report_tool",
    ) -> dict[str, Any]:
        tool_name = "write_purchase_report"
        arguments = {
            "candidate_key": _candidate_key_for_backend_required_report(run, score_observation)
        }
        call_id = f"backend_required_report_{len(run.get('trace', [])) + 1}"
        tool_definition = _maybe_tool_definition(self.tool_registry, tool_name)
        append_trace(
            run,
            step_type="tool_call",
            name=tool_name,
            status="started",
            input_payload={
                "arguments": arguments,
                "call_id": call_id,
                "backend_enforced": True,
                "reason": reason,
                "rejected_final_text_excerpt": rejected_final_text[:500],
                **_tool_definition_metadata(tool_definition),
            },
        )
        _mark_execution_plan_tool(run, tool_name, "in_progress")
        self._checkpoint(run)
        tool_start = perf_counter()
        try:
            result = self._execute_tool(
                run,
                tool_name,
                arguments,
                tool_definition=tool_definition,
                call_id=call_id,
            )
            status = result.get("status", "completed")
        except Exception as exc:
            result = {
                "status": "failed",
                "error": {
                    "code": "TOOL_EXECUTION_FAILED",
                    "message": str(exc),
                },
            }
            status = "failed"
        tool_metadata = {
            **_tool_definition_metadata(tool_definition),
            "duration_ms": round((perf_counter() - tool_start) * 1000),
        }
        observation = {
            "tool": tool_name,
            "call_id": call_id,
            "arguments": arguments,
            "result": result,
            **tool_metadata,
            **_observation_metadata(result),
        }
        run.setdefault("state", {}).setdefault("observations", []).append(observation)
        append_trace(
            run,
            step_type="tool_output",
            name=tool_name,
            status=status,
            output_payload={
                "call_id": call_id,
                "backend_enforced": True,
                **_summarize_tool_output(result),
                **tool_metadata,
            },
        )
        _mark_execution_plan_tool(run, tool_name, status)
        self._checkpoint(run)
        return observation

    def _recover_deferred_score_before_max_turn_report(self, run: dict[str, Any]) -> dict[str, Any] | None:
        deferred_score = _latest_deferred_score_observation_after_latest_user_reply(run)
        if not deferred_score:
            return None
        deferred_result = deferred_score.get("result") or {}
        guidance = _deferred_enrichment_guidance(deferred_result)
        if not guidance or not _guidance_allows_backend_recovery(guidance):
            return None

        pending_tool_outputs = _append_backend_runtime_guidance(
            run,
            run.setdefault("state", {}).get("pending_tool_outputs") or [],
            guidance,
            trace_name="max_turns_deferred_score_recovery_before_report",
        )
        recovery = self._execute_backend_recovery_from_guidance(
            run,
            pending_tool_outputs,
            rejected_final_text="",
        )
        if run.get("status") in {"completed", "failed", "waiting_for_user", "configuration_required"}:
            return self.store.update_run(run)
        if not recovery.get("executed"):
            return None

        latest_deferred_after_recovery = _latest_deferred_score_observation_after_latest_user_reply(run)
        completed_score = (
            None
            if latest_deferred_after_recovery
            else _latest_completed_score_observation_after_latest_user_reply(run)
        )
        if completed_score:
            report_observation = self._execute_backend_required_report_tool(
                run,
                completed_score,
                rejected_final_text="",
                reason="max_turns_after_deferred_score_recovery_before_report",
            )
            report_result = report_observation.get("result") or {}
            report_text = str(report_result.get("final_report") or "").strip()
            if report_result.get("status") == "waiting_for_user":
                run["status"] = "waiting_for_user"
                run["error"] = None
                run.setdefault("state", {})["pending_tool_outputs"] = []
                questions = report_result.get("questions") or report_result.get("next_questions") or []
                if questions:
                    run["state"]["missing_info"] = questions
                append_trace(
                    run,
                    step_type="guardrail",
                    name="await_missing_info_after_max_turns_deferred_score_recovery",
                    status="waiting_for_user",
                    output_payload={
                        "deferred_score_observation_call_id": deferred_score.get("call_id"),
                        "report_observation_call_id": report_observation.get("call_id"),
                    },
                )
                return self.store.update_run(run)
            if (
                report_result.get("status") == "completed"
                and report_result.get("grounded") is not False
                and report_text
            ):
                report_candidate_key = str(
                    report_result.get("candidate_key")
                    or (report_observation.get("arguments") or {}).get("candidate_key")
                    or ""
                ).lower()
                all_candidate_report = report_candidate_key in {"all", "*", "__all__"} or report_result.get(
                    "report_type"
                ) == "shortlist"
                run["status"] = "completed"
                run["error"] = None
                run["completed_at"] = _completed_at()
                _reset_provider_response_context_after_backend_terminal(
                    run,
                    reason="max_turns_deferred_score_recovery_report_before_fallback",
                    call_id=report_observation.get("call_id"),
                )
                run.setdefault("state", {})["missing_info"] = []
                run["final_report"] = {
                    "summary": report_text,
                    "grounded": True,
                    "grounding": (
                        "write_purchase_report_all_after_max_turns_deferred_score_recovery_before_fallback"
                        if all_candidate_report
                        else "write_purchase_report_after_max_turns_deferred_score_recovery_before_fallback"
                    ),
                    "grounding_validation": report_result.get("grounding_validation"),
                    "report_type": report_result.get("report_type"),
                    **_report_metadata_from_tool_result(report_result),
                }
                append_trace(
                    run,
                    step_type="guardrail",
                    name=(
                        "complete_all_report_after_max_turns_deferred_score_recovery_before_fallback"
                        if all_candidate_report
                        else "complete_report_after_max_turns_deferred_score_recovery_before_fallback"
                    ),
                    status="completed",
                    output_payload={
                        "deferred_score_observation_call_id": deferred_score.get("call_id"),
                        "score_observation_call_id": completed_score.get("call_id"),
                        "report_observation_call_id": report_observation.get("call_id"),
                        "report_type": report_result.get("report_type"),
                    },
                )
                return self.store.update_run(run)

        run.setdefault("state", {})["pending_tool_outputs"] = recovery.get("pending_tool_outputs") or []
        append_trace(
            run,
            step_type="guardrail",
            name="extend_loop_after_max_turns_deferred_score_recovery",
            status="running",
            output_payload={
                "deferred_score_observation_call_id": deferred_score.get("call_id"),
                "reason": guidance.get("reason"),
                "pending_tool_output_count": len(run["state"].get("pending_tool_outputs") or []),
            },
        )
        updated_run = self.store.update_run(run)
        if _latest_deferred_score_observation_after_latest_user_reply(updated_run):
            return self._recover_deferred_score_before_max_turn_report(updated_run)
        return self._run_loop(updated_run)

    def _continue_from_report_guidance_after_max_turns(
        self,
        run: dict[str, Any],
        result: dict[str, Any],
        *,
        trace_name: str,
    ) -> dict[str, Any] | None:
        guidance = _deferred_enrichment_guidance(result)
        if not guidance:
            return None
        pending_tool_outputs = _append_backend_runtime_guidance(
            run,
            run.setdefault("state", {}).get("pending_tool_outputs") or [],
            guidance,
            trace_name=trace_name,
        )
        recovery = self._execute_backend_recovery_from_guidance(
            run,
            pending_tool_outputs,
            rejected_final_text="",
        )
        if run.get("status") in {"completed", "failed", "waiting_for_user", "configuration_required"}:
            return self.store.update_run(run)
        if recovery.get("executed"):
            run.setdefault("state", {})["pending_tool_outputs"] = recovery.get("pending_tool_outputs") or []
            return self._run_loop(self.store.update_run(run))
        return None

    def _execute_backend_recovery_from_guidance(
        self,
        run: dict[str, Any],
        pending_tool_outputs: list[dict[str, Any]],
        *,
        rejected_final_text: str,
    ) -> dict[str, Any]:
        guidance = _latest_backend_runtime_guidance_from_pending(pending_tool_outputs)
        if not guidance:
            return {"executed": False, "pending_tool_outputs": pending_tool_outputs}
        if not _guidance_allows_backend_recovery(guidance):
            return {"executed": False, "pending_tool_outputs": pending_tool_outputs}
        suggestions = _backend_recovery_suggestions(guidance)
        if not suggestions:
            return {"executed": False, "pending_tool_outputs": pending_tool_outputs}
        state = run.setdefault("state", {})
        signature = json.dumps(
            {
                "reason": guidance.get("reason"),
                "suggestions": suggestions,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        recovery_counts = state.setdefault("backend_guidance_recovery_counts", {})
        count = int(recovery_counts.get(signature) or 0)
        if count >= 2 and not _close_fit_recovery_budget_allows_repeated_guidance(guidance):
            if str(guidance.get("reason") or "") == "baseline_evidence_recovery_required_before_report":
                questions = _baseline_evidence_questions_from_recovery_guidance(guidance)
                run["status"] = "waiting_for_user"
                run["error"] = None
                state["missing_info"] = questions
                state["pending_tool_outputs"] = []
                append_trace(
                    run,
                    step_type="guardrail",
                    name="await_baseline_evidence_after_recovery_exhausted",
                    status="waiting_for_user",
                    output_payload={
                        "reason": guidance.get("reason"),
                        "suggestion_count": len(suggestions),
                        "signature": signature,
                        "missing_fields": guidance.get("missing_fields") or [],
                        "question_count": len(questions),
                    },
                )
                return {"executed": True, "pending_tool_outputs": []}
            if str(guidance.get("reason") or "") == "baseline_identity_required_before_candidate_search":
                if self._complete_with_backend_no_result_report(
                    run,
                    rejected_final_text=rejected_final_text,
                    reason="baseline_identity_recovery_exhausted",
                ):
                    append_trace(
                        run,
                        step_type="guardrail",
                        name="complete_no_result_after_baseline_identity_recovery_exhausted",
                        status="completed",
                        output_payload={
                            "reason": guidance.get("reason"),
                            "suggestion_count": len(suggestions),
                            "signature": signature,
                        },
                    )
                    return {"executed": True, "pending_tool_outputs": []}
            if str(guidance.get("reason") or "") in {
                "candidate_enrichment_required_before_scoring",
                "candidate_enrichment_required_before_report",
            } and self._complete_with_partial_candidate_report_after_repeated_recovery(
                run,
                guidance=guidance,
                rejected_final_text=rejected_final_text,
                signature=signature,
                suggestion_count=len(suggestions),
            ):
                return {"executed": True, "pending_tool_outputs": []}
            append_trace(
                run,
                step_type="guardrail",
                name="stop_repeated_backend_guidance_recovery",
                status="failed",
                output_payload={
                    "reason": guidance.get("reason"),
                    "suggestion_count": len(suggestions),
                    "signature": signature,
                },
            )
            run["status"] = "failed"
            run["error"] = {
                "code": "REPEATED_BACKEND_GUIDANCE_RECOVERY",
                "message": "Backend recovery already executed the same suggested tools repeatedly without resolving the run.",
                "reason": guidance.get("reason"),
            }
            state["pending_tool_outputs"] = []
            return {"executed": False, "pending_tool_outputs": []}
        if count >= 2:
            append_trace(
                run,
                step_type="guardrail",
                name="continue_repeated_close_fit_recovery_within_budget",
                status="running",
                output_payload={
                    "reason": guidance.get("reason"),
                    "suggestion_count": len(suggestions),
                    "signature": signature,
                    "close_fit_search_budget": guidance.get("close_fit_search_budget"),
                },
            )
        recovery_counts[signature] = count + 1
        append_trace(
            run,
            step_type="backend_recovery",
            name="execute_guidance_suggestions_after_model_no_tool_call",
            status="started",
            input_payload={
                "reason": guidance.get("reason"),
                "suggestion_count": len(suggestions),
                "rejected_final_text_present": bool(rejected_final_text),
            },
        )
        self._checkpoint(run)

        observations = []
        for index, suggestion in enumerate(suggestions, start=1):
            tool_name = str(suggestion.get("tool") or "")
            arguments = suggestion.get("arguments") if isinstance(suggestion.get("arguments"), dict) else {}
            if not tool_name:
                continue
            observation = self._execute_backend_suggested_tool(
                run,
                tool_name,
                arguments,
                reason=str(guidance.get("reason") or "backend_guidance_recovery"),
                index=index,
            )
            observations.append(observation)
            status = str((observation.get("result") or {}).get("status") or "")
            if status in {"configuration_required", "waiting_for_user"}:
                break

        rescore_guidance = None
        if _backend_recovery_should_rescore_after_enrichment(guidance, observations):
            rescore_observation = self._execute_backend_suggested_tool(
                run,
                "score_candidate_match",
                {"candidate_key": "all"},
                reason="rescore_after_backend_enrichment_recovery",
                index=len(observations) + 1,
            )
            observations.append(rescore_observation)
            rescore_guidance = _deferred_enrichment_guidance(rescore_observation.get("result") or {})

        updated_pending = _without_backend_runtime_guidance_items(pending_tool_outputs)
        updated_pending.append(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "backend_recovery_observations": [
                            {
                                "tool": item.get("tool"),
                                "arguments": item.get("arguments", {}),
                                "result": _summarize_tool_output(item.get("result") or {}),
                            }
                            for item in observations
                        ],
                        "instruction": (
                            "Continue from these backend-owned recovery observations. "
                            "Choose the next backend tool needed for scoring or reporting; do not repeat a tool unless arguments change."
                        ),
                    },
                    ensure_ascii=False,
                ),
            }
        )
        if rescore_guidance:
            updated_pending = _append_backend_runtime_guidance(
                run,
                updated_pending,
                rescore_guidance,
                trace_name="continue_backend_recovery_rescore_guidance",
            )
        append_trace(
            run,
            step_type="backend_recovery",
            name="execute_guidance_suggestions_after_model_no_tool_call",
            status="completed",
            output_payload={
                "executed_tool_count": len(observations),
                "tools": [item.get("tool") for item in observations],
            },
        )
        state["pending_tool_outputs"] = updated_pending
        return {"executed": bool(observations), "pending_tool_outputs": updated_pending}

    def _complete_with_partial_candidate_report_after_repeated_recovery(
        self,
        run: dict[str, Any],
        *,
        guidance: dict[str, Any],
        rejected_final_text: str,
        signature: str,
        suggestion_count: int,
    ) -> bool:
        if not _has_reportable_match_results_for_runtime(run):
            return False
        score_observation = self._execute_backend_suggested_tool(
            run,
            "score_candidate_match",
            {"candidate_key": "all"},
            reason="score_all_after_repeated_candidate_recovery",
            index=1,
        )
        score_result = score_observation.get("result") or {}
        if score_result.get("status") != "completed" or score_result.get("score_deferred"):
            return False
        report_observation = self._execute_backend_suggested_tool(
            run,
            "write_purchase_report",
            {"candidate_key": "all"},
            reason="write_all_report_after_repeated_candidate_recovery",
            index=2,
        )
        report_result = report_observation.get("result") or {}
        report_text = str(report_result.get("final_report") or "").strip()
        if (
            report_result.get("status") != "completed"
            or report_result.get("grounded") is False
            or not report_text
        ):
            return False
        run["status"] = "completed"
        run["error"] = None
        run["completed_at"] = _completed_at()
        _reset_provider_response_context_after_backend_terminal(
            run,
            reason="repeated_candidate_recovery_completed_from_observed_matches",
            call_id=report_observation.get("call_id"),
        )
        run.setdefault("state", {})["missing_info"] = []
        run.setdefault("state", {})["pending_tool_outputs"] = []
        _store_completed_report_followups(run, report_result)
        run["final_report"] = {
            "summary": report_text,
            "grounded": True,
            "grounding": "write_purchase_report_all_after_repeated_candidate_recovery",
            "grounding_validation": report_result.get("grounding_validation"),
            "report_type": report_result.get("report_type"),
            **_report_metadata_from_tool_result(report_result),
            "llm_final_text": rejected_final_text,
        }
        append_trace(
            run,
            step_type="guardrail",
            name="complete_all_report_after_repeated_candidate_recovery",
            status="completed",
            output_payload={
                "reason": guidance.get("reason"),
                "signature": signature,
                "suggestion_count": suggestion_count,
                "score_observation_call_id": score_observation.get("call_id"),
                "report_observation_call_id": report_observation.get("call_id"),
                "report_type": report_result.get("report_type"),
            },
        )
        return True

    def _execute_backend_suggested_tool(
        self,
        run: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any],
        *,
        reason: str,
        index: int,
    ) -> dict[str, Any]:
        call_id = f"backend_recovery_{len(run.get('trace', [])) + 1}_{index}"
        tool_definition = _maybe_tool_definition(self.tool_registry, tool_name)
        append_trace(
            run,
            step_type="tool_call",
            name=tool_name,
            status="started",
            input_payload={
                "arguments": arguments,
                "call_id": call_id,
                "backend_recovery": True,
                "reason": reason,
                **_tool_definition_metadata(tool_definition),
            },
        )
        _mark_execution_plan_tool(run, tool_name, "in_progress")
        self._checkpoint(run)
        tool_start = perf_counter()
        try:
            result = self._execute_tool(run, tool_name, arguments, tool_definition=tool_definition)
            status = result.get("status", "completed")
        except Exception as exc:
            result = {
                "status": "failed",
                "error": {
                    "code": "TOOL_EXECUTION_FAILED",
                    "message": str(exc),
                },
            }
            status = "failed"
        tool_metadata = {
            **_tool_definition_metadata(tool_definition),
            "duration_ms": round((perf_counter() - tool_start) * 1000),
        }
        observation = {
            "tool": tool_name,
            "call_id": call_id,
            "arguments": arguments,
            "result": result,
            **tool_metadata,
            **_observation_metadata(result),
        }
        run.setdefault("state", {}).setdefault("observations", []).append(observation)
        append_trace(
            run,
            step_type="tool_output",
            name=tool_name,
            status=status,
            output_payload={
                "call_id": call_id,
                "backend_recovery": True,
                **_summarize_tool_output(result),
                **tool_metadata,
            },
        )
        _mark_execution_plan_tool(run, tool_name, status)
        self._checkpoint(run)
        return observation

    def _complete_with_backend_no_result_report(
        self,
        run: dict[str, Any],
        *,
        rejected_final_text: str,
        reason: str,
    ) -> bool:
        report_observation = self._execute_backend_required_report_tool(
            run,
            None,
            rejected_final_text=rejected_final_text,
            reason=reason,
        )
        report_result = report_observation.get("result") or {}
        if (
            report_result.get("status") != "completed"
            or report_result.get("grounded") is False
            or report_result.get("report_type") != "no_result"
        ):
            return False
        report_text = str(report_result.get("final_report") or "").strip()
        if not report_text:
            return False
        run["status"] = "completed"
        run["error"] = None
        run["completed_at"] = _completed_at()
        _reset_provider_response_context_after_backend_terminal(
            run,
            reason="backend_no_result_report_completed_without_model_ack",
            call_id=report_observation.get("call_id"),
        )
        run["state"]["missing_info"] = []
        run["final_report"] = {
            "summary": report_text,
            "grounded": True,
            "grounding": "write_purchase_report_no_result",
            "grounding_validation": report_result.get("grounding_validation"),
            "report_type": "no_result",
            "search_attempt_count": report_result.get("search_attempt_count"),
            "attempted_queries": report_result.get("attempted_queries", []),
        }
        append_trace(
            run,
            step_type="guardrail",
            name="complete_grounded_no_result_report",
            status="completed",
            output_payload={
                "reason": reason,
                "report_observation_call_id": report_observation.get("call_id"),
                "search_attempt_count": report_result.get("search_attempt_count"),
            },
        )
        return True

    def _recover_with_backend_report_after_llm_failure(
        self,
        run: dict[str, Any],
        *,
        error_payload: dict[str, Any],
    ) -> bool:
        if not error_payload.get("retryable"):
            return False
        baseline_recovery = self._recover_baseline_identity_lookup_after_llm_failure(
            run,
            error_payload=error_payload,
        )
        if baseline_recovery:
            return True
        score_observation = _latest_completed_score_observation_after_latest_user_reply(run)
        if not score_observation:
            return False
        report_observation = self._execute_backend_required_report_tool(
            run,
            score_observation,
            rejected_final_text="",
            reason="retryable_llm_failure_after_completed_score",
        )
        report_result = report_observation.get("result") or {}
        if report_result.get("status") == "waiting_for_user":
            run["status"] = "waiting_for_user"
            run["error"] = None
            run["final_report"] = None
            run.setdefault("state", {})["pending_tool_outputs"] = []
            append_trace(
                run,
                step_type="guardrail",
                name="recover_retryable_llm_failure_with_missing_info",
                status="waiting_for_user",
                output_payload={
                    "llm_error": error_payload,
                    "report_observation_call_id": report_observation.get("call_id"),
                },
            )
            return True
        if report_result.get("status") != "completed" or report_result.get("grounded") is False:
            return False
        report_text = str(report_result.get("final_report") or "").strip()
        if not report_text:
            return False
        run["status"] = "completed"
        run["error"] = None
        run["completed_at"] = _completed_at()
        _reset_provider_response_context_after_backend_terminal(
            run,
            reason="backend_llm_failure_report_completed_without_model_ack",
            call_id=report_observation.get("call_id"),
        )
        run.setdefault("state", {})["missing_info"] = []
        _store_completed_report_followups(run, report_result)
        run["final_report"] = {
            "summary": report_text,
            "grounded": True,
            "grounding": "write_purchase_report_after_retryable_llm_failure",
            "grounding_validation": report_result.get("grounding_validation"),
            "report_type": report_result.get("report_type"),
            "recovered_from_llm_error": error_payload,
        }
        append_trace(
            run,
            step_type="guardrail",
            name="recover_retryable_llm_failure_with_grounded_report",
            status="completed",
            output_payload={
                "llm_error": error_payload,
                "report_observation_call_id": report_observation.get("call_id"),
            },
        )
        return True

    def _recover_baseline_identity_lookup_after_llm_failure(
        self,
        run: dict[str, Any],
        *,
        error_payload: dict[str, Any],
    ) -> bool:
        state = run.setdefault("state", {})
        closet_item = state.get("closet_item") or {}
        baseline_identity = state.get("baseline_identity") or closet_item.get("baseline_identity") or {}
        if baseline_identity.get("status") != "needs_identity_confirmation":
            return False
        latest_tools = _tool_calls_after_latest_user_reply(run)
        if "enrich_closet_item_from_web" in latest_tools and state.get("unconfirmed_baseline_identity_candidates"):
            candidates = state.get("unconfirmed_baseline_identity_candidates") or []
        else:
            observation = self._execute_backend_suggested_tool(
                run,
                "enrich_closet_item_from_web",
                {"refresh_web": False},
                reason="retryable_llm_failure_after_baseline_identity_planning",
                index=1,
            )
            result = observation.get("result") or {}
            if result.get("status") != "completed":
                return False
            candidates = result.get("identity_candidates") or state.get("unconfirmed_baseline_identity_candidates") or []
        if not candidates:
            return False
        run["status"] = "waiting_for_user"
        run["error"] = None
        run["final_report"] = None
        state["pending_tool_outputs"] = []
        state["missing_info"] = [
            {
                "field": "baseline_identity_confirmation",
                "question": "검색된 기준 옷 후보 중 실제로 자주 입는 옷이 맞는 상품을 확인해 주세요.",
                "candidates": candidates,
            }
        ]
        append_trace(
            run,
            step_type="guardrail",
            name="recover_retryable_llm_failure_with_baseline_identity_candidates",
            status="waiting_for_user",
            output_payload={
                "llm_error": error_payload,
                "candidate_count": len(candidates),
            },
        )
        return True


def _extract_tool_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in response.get("output", []) or []:
        item_type = item.get("type")
        if item_type not in {"function_call", "tool_call", "function"}:
            continue
        function = item.get("function") or {}
        raw_arguments = item.get("arguments")
        if raw_arguments is None:
            raw_arguments = function.get("arguments")
        if raw_arguments is None:
            raw_arguments = "{}"
        arguments_parse_error: dict[str, Any] | None = None
        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                arguments = {}
                arguments_parse_error = {
                    "code": "invalid_json",
                    "message": exc.msg,
                    "line": exc.lineno,
                    "column": exc.colno,
                    "position": exc.pos,
                    "raw_arguments_excerpt": raw_arguments[:500],
                }
        else:
            arguments = raw_arguments
        calls.append(
            {
                "call_id": item.get("call_id") or item.get("id") or item.get("tool_call_id"),
                "name": item.get("name") or function.get("name"),
                "arguments": arguments,
                "raw_arguments": raw_arguments if isinstance(raw_arguments, str) else None,
                "arguments_parse_error": arguments_parse_error,
            }
        )
    return [call for call in calls if call["call_id"] and call["name"]]


def _response_context_items_for_next_llm(response: dict[str, Any]) -> list[dict[str, Any]]:
    context_items: list[dict[str, Any]] = []
    for item in response.get("output", []) or []:
        if item.get("type") == "reasoning":
            context_items.append(dict(item))
    return context_items


def _record_context_budget_summary(
    run: dict[str, Any],
    *,
    instructions: str,
    input_items: str | list[dict[str, Any]],
    pending_tool_outputs: bool,
) -> dict[str, Any]:
    state = run.setdefault("state", {})
    records = state.setdefault("context_budget", [])
    input_text = input_items if isinstance(input_items, str) else json.dumps(input_items, ensure_ascii=False)
    input_kind = "pending_tool_outputs" if pending_tool_outputs else "initial_model_input"
    summary = {
        "sequence": len(records) + 1,
        "source": "fitcheck_backend_runtime",
        "measurement": "estimated",
        "input_kind": input_kind,
        "created_at": _completed_at(),
        "instruction_chars": len(instructions),
        "input_chars": len(input_text),
        "instruction_bytes": len(instructions.encode("utf-8")),
        "input_bytes": len(input_text.encode("utf-8")),
        "estimated_instruction_tokens": _estimate_token_count(instructions),
        "estimated_input_tokens": _estimate_token_count(input_text),
        "pending_tool_output_count": len(input_items) if isinstance(input_items, list) else 0,
        "trace_count_before_request": len(run.get("trace") or []),
        "observation_count_before_request": len(state.get("observations") or []),
        "hook_lifecycle_count_before_request": len(state.get("hook_lifecycle") or []),
    }
    summary["estimated_total_tokens"] = (
        int(summary["estimated_instruction_tokens"]) + int(summary["estimated_input_tokens"])
    )
    records.append(summary)
    _maybe_record_context_compaction_summary(run, summary)
    return summary


def _update_context_budget_summary_from_response(summary: dict[str, Any], response: dict[str, Any]) -> None:
    usage = response.get("usage")
    if isinstance(usage, dict):
        summary["provider_usage"] = usage
        usage_counts = _provider_usage_counts(usage)
        for source_key, target_key in {
            "input_tokens": "provider_input_tokens",
            "output_tokens": "provider_output_tokens",
            "total_tokens": "provider_total_tokens",
            "cache_read_input_tokens": "provider_cache_read_input_tokens",
            "cache_creation_input_tokens": "provider_cache_creation_input_tokens",
            "web_search_requests": "provider_web_search_requests",
        }.items():
            if source_key in usage_counts and target_key not in summary:
                summary[target_key] = usage_counts[source_key]
        cost = _provider_usage_cost_usd(usage_counts)
        if cost is not None:
            summary["provider_cost_usd"] = cost
    summary["response_id"] = response.get("id")
    summary["api_style"] = response.get("api_style")
    summary["raw_response_id"] = response.get("raw_response_id")
    summary["provider_request_id"] = response.get("provider_request_id") or response.get("request_id")
    summary["transport"] = response.get("transport")


def _update_usage_accounting_from_response(
    run: dict[str, Any],
    response: dict[str, Any],
    *,
    duration_ms: int,
) -> None:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return
    counts = _provider_usage_counts(usage)
    state = run.setdefault("state", {})
    accounting = state.setdefault(
        "usage_accounting",
        {
            "schema_version": 1,
            "source": "provider_usage",
            "status": "recorded",
            "request_count": 0,
            "provider_input_tokens": 0,
            "provider_output_tokens": 0,
            "provider_total_tokens": 0,
            "provider_cache_read_input_tokens": 0,
            "provider_cache_creation_input_tokens": 0,
            "provider_web_search_requests": 0,
            "api_duration_ms": 0,
            "cost_usd": 0.0,
            "cost_status": "not_configured",
            "cost_pricing_source": "env",
            "cost_pricing_env": {
                "input": "FITCHECK_LLM_INPUT_COST_PER_1M",
                "output": "FITCHECK_LLM_OUTPUT_COST_PER_1M",
                "cache_read_input": "FITCHECK_LLM_CACHE_READ_INPUT_COST_PER_1M",
                "cache_creation_input": "FITCHECK_LLM_CACHE_CREATION_INPUT_COST_PER_1M",
            },
            "has_unknown_model_cost": True,
            "by_model": {},
            "requests": [],
        },
    )
    response_id = str(response.get("id") or "")
    raw_response_id = str(response.get("raw_response_id") or "")
    provider_request_id = str(response.get("provider_request_id") or response.get("request_id") or "")
    counted_ids = {
        str(item.get("response_id") or item.get("raw_response_id") or item.get("provider_request_id") or "")
        for item in accounting.get("requests", [])
        if isinstance(item, dict)
    }
    if response_id and response_id in counted_ids:
        return
    model = str((run.get("llm") or {}).get("model") or response.get("model") or "unknown")
    cost = _provider_usage_cost_usd(counts)
    accounting["request_count"] = int(accounting.get("request_count") or 0) + 1
    accounting["api_duration_ms"] = int(accounting.get("api_duration_ms") or 0) + int(duration_ms)
    for key in (
        "provider_input_tokens",
        "provider_output_tokens",
        "provider_total_tokens",
        "provider_cache_read_input_tokens",
        "provider_cache_creation_input_tokens",
        "provider_web_search_requests",
    ):
        count_key = key.removeprefix("provider_")
        accounting[key] = int(accounting.get(key) or 0) + int(counts.get(count_key) or 0)
    if cost is not None:
        accounting["cost_usd"] = round(float(accounting.get("cost_usd") or 0.0) + cost, 8)
        accounting["cost_status"] = "calculated"
        accounting["has_unknown_model_cost"] = False
    by_model = accounting.setdefault("by_model", {})
    model_usage = by_model.setdefault(
        model,
        {
            "request_count": 0,
            "provider_input_tokens": 0,
            "provider_output_tokens": 0,
            "provider_total_tokens": 0,
            "provider_cache_read_input_tokens": 0,
            "provider_cache_creation_input_tokens": 0,
            "provider_web_search_requests": 0,
            "api_duration_ms": 0,
            "cost_usd": 0.0,
        },
    )
    model_usage["request_count"] = int(model_usage.get("request_count") or 0) + 1
    model_usage["api_duration_ms"] = int(model_usage.get("api_duration_ms") or 0) + int(duration_ms)
    for key in (
        "provider_input_tokens",
        "provider_output_tokens",
        "provider_total_tokens",
        "provider_cache_read_input_tokens",
        "provider_cache_creation_input_tokens",
        "provider_web_search_requests",
    ):
        count_key = key.removeprefix("provider_")
        model_usage[key] = int(model_usage.get(key) or 0) + int(counts.get(count_key) or 0)
    if cost is not None:
        model_usage["cost_usd"] = round(float(model_usage.get("cost_usd") or 0.0) + cost, 8)
    accounting.setdefault("requests", []).append(
        {
            "sequence": int(accounting["request_count"]),
            "model": model,
            "response_id": response_id or None,
            "raw_response_id": raw_response_id or None,
            "provider_request_id": provider_request_id or None,
            "api_style": response.get("api_style"),
            "transport": response.get("transport"),
            "duration_ms": duration_ms,
            "usage": counts,
            "cost_usd": cost,
        }
    )


def _provider_usage_counts(usage: dict[str, Any]) -> dict[str, int]:
    input_tokens = _int_usage_value(usage.get("input_tokens"), usage.get("prompt_tokens"))
    output_tokens = _int_usage_value(usage.get("output_tokens"), usage.get("completion_tokens"))
    total_tokens = _int_usage_value(usage.get("total_tokens"), input_tokens + output_tokens)
    input_details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    cache_read_tokens = _int_usage_value(
        usage.get("cache_read_input_tokens"),
        input_details.get("cached_tokens") if isinstance(input_details, dict) else None,
    )
    cache_creation_tokens = _int_usage_value(usage.get("cache_creation_input_tokens"))
    server_tool_use = usage.get("server_tool_use") or {}
    web_search_requests = _int_usage_value(
        server_tool_use.get("web_search_requests") if isinstance(server_tool_use, dict) else None
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_read_input_tokens": cache_read_tokens,
        "cache_creation_input_tokens": cache_creation_tokens,
        "web_search_requests": web_search_requests,
    }


def _provider_usage_cost_usd(counts: dict[str, int]) -> float | None:
    rates = {
        "input_tokens": _env_float("FITCHECK_LLM_INPUT_COST_PER_1M"),
        "output_tokens": _env_float("FITCHECK_LLM_OUTPUT_COST_PER_1M"),
        "cache_read_input_tokens": _env_float("FITCHECK_LLM_CACHE_READ_INPUT_COST_PER_1M"),
        "cache_creation_input_tokens": _env_float("FITCHECK_LLM_CACHE_CREATION_INPUT_COST_PER_1M"),
    }
    configured = {key: value for key, value in rates.items() if value is not None}
    if not configured:
        return None
    total = 0.0
    cache_read_tokens = int(counts.get("cache_read_input_tokens") or 0)
    cache_creation_tokens = int(counts.get("cache_creation_input_tokens") or 0)
    input_tokens = int(counts.get("input_tokens") or 0)
    if "cache_read_input_tokens" in configured or "cache_creation_input_tokens" in configured:
        input_tokens = max(0, input_tokens - cache_read_tokens - cache_creation_tokens)
    billable_counts = {
        **counts,
        "input_tokens": input_tokens,
    }
    for key, rate in configured.items():
        total += (float(billable_counts.get(key) or 0) / 1_000_000.0) * float(rate)
    return round(total, 8)


def _env_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw in (None, ""):
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _int_usage_value(*values: Any) -> int:
    for value in values:
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def _estimate_token_count(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _maybe_record_context_compaction_summary(run: dict[str, Any], budget_summary: dict[str, Any]) -> None:
    threshold = _context_compaction_threshold()
    if threshold <= 0 or int(budget_summary.get("estimated_total_tokens") or 0) < threshold:
        return
    state = run.setdefault("state", {})
    records = state.setdefault("context_compactions", [])
    if records and records[-1].get("source_budget_sequence") == budget_summary.get("sequence"):
        return
    latest_observations = list(state.get("observations") or [])[-5:]
    pending_outputs = list(state.get("pending_tool_outputs") or [])
    record = {
        "sequence": len(records) + 1,
        "source": "fitcheck_backend_runtime",
        "mode": "deterministic_evidence_summary",
        "status": "recorded",
        "created_at": _completed_at(),
        "threshold_estimated_tokens": threshold,
        "source_budget_sequence": budget_summary.get("sequence"),
        "estimated_total_tokens": budget_summary.get("estimated_total_tokens"),
        "preserved": {
            "latest_user_input": _latest_user_input_summary(run),
            "latest_observations": [_compact_observation_summary(item) for item in latest_observations],
            "pending_tool_output_count": len(pending_outputs),
            "pending_tool_call_ids": [
                str(item.get("call_id"))
                for item in pending_outputs
                if isinstance(item, dict) and item.get("type") == "function_call_output" and item.get("call_id")
            ],
            "loaded_skills": [
                item.get("skill_name")
                for item in state.get("loaded_agent_skills", []) or []
                if isinstance(item, dict) and item.get("skill_name")
            ],
            "grounding_constraints": {
                "final_report_requires_write_purchase_report": True,
                "facts_must_be_trace_grounded": True,
                "latest_report_grounding": (run.get("final_report") or {}).get("grounding"),
            },
        },
    }
    records.append(record)


def _apply_context_compaction_to_input(
    run: dict[str, Any],
    *,
    input_items: str | list[dict[str, Any]],
    budget_summary: dict[str, Any],
    pending_tool_outputs: bool,
) -> str | list[dict[str, Any]]:
    if pending_tool_outputs or not isinstance(input_items, str):
        return input_items
    state = run.setdefault("state", {})
    records = state.get("context_compactions") or []
    if not records:
        return input_items
    record = records[-1]
    if record.get("source_budget_sequence") != budget_summary.get("sequence"):
        return input_items
    preserved = record.get("preserved") or {}
    compacted_payload = {
        "context_compaction": {
            "source": "fitcheck_backend_runtime",
            "mode": record.get("mode"),
            "source_budget_sequence": record.get("source_budget_sequence"),
            "estimated_total_tokens_before": record.get("estimated_total_tokens"),
            "preserved": preserved,
            "instruction": (
                "Use this deterministic compacted context as the complete prior run context. "
                "All final claims must still be grounded in preserved observations or later tool observations."
            ),
        }
    }
    compacted_input = json.dumps(compacted_payload, ensure_ascii=False)
    compacted_tokens = _estimate_token_count(compacted_input)
    record["status"] = "applied"
    record["applied_to_request_sequence"] = budget_summary.get("sequence")
    record["compacted_estimated_input_tokens"] = compacted_tokens
    record["estimated_input_tokens_before"] = budget_summary.get("estimated_input_tokens")
    budget_summary["compaction_applied"] = True
    budget_summary["compaction_sequence"] = record.get("sequence")
    budget_summary["compacted_estimated_input_tokens"] = compacted_tokens
    return compacted_input


def _apply_response_context_reset_to_input(
    run: dict[str, Any],
    *,
    input_items: str | list[dict[str, Any]],
    budget_summary: dict[str, Any],
    pending_tool_outputs: list[dict[str, Any]],
) -> str | list[dict[str, Any]]:
    if not pending_tool_outputs:
        return input_items
    if not run.setdefault("state", {}).get("previous_response_id"):
        return input_items
    reset_reason = _response_context_reset_reason(run, budget_summary)
    if not reset_reason:
        return input_items
    compacted_payload = {
        "response_context_reset": {
            "source": "fitcheck_backend_runtime",
            "mode": "pending_tool_output_summary_without_previous_response_id",
            "reason": reset_reason,
            "latest_user_input": _latest_user_input_summary(run),
            "pending_tool_outputs": _compact_pending_tool_outputs(run, pending_tool_outputs),
            "latest_observations": [
                _compact_observation_summary(item)
                for item in list(run.setdefault("state", {}).get("observations") or [])[-8:]
                if isinstance(item, dict)
            ],
            "loaded_skills": [
                item.get("skill_name")
                for item in run.setdefault("state", {}).get("loaded_agent_skills", []) or []
                if isinstance(item, dict) and item.get("skill_name")
            ],
            "instruction": (
                "The previous Responses API thread was reset to control token growth. "
                "Continue from these backend-owned observations. Choose the next tool from current state; "
                "do not repeat completed tools unless the query or candidate changed. "
                "Final claims must still come from stored observations and write_purchase_report."
            ),
        }
    }
    compacted_input = [{"role": "user", "content": json.dumps(compacted_payload, ensure_ascii=False)}]
    compacted_text = json.dumps(compacted_input, ensure_ascii=False)
    budget_summary["response_context_reset_applied"] = True
    budget_summary["response_context_reset_reason"] = reset_reason
    budget_summary["response_context_reset_estimated_input_tokens"] = _estimate_token_count(compacted_text)
    budget_summary["previous_response_id_omitted"] = True
    return compacted_input


def _response_context_reset_reason(run: dict[str, Any], budget_summary: dict[str, Any]) -> str:
    forced = str(run.setdefault("state", {}).pop("force_response_context_reset", "") or "")
    if forced:
        return forced
    threshold = _response_context_reset_provider_token_threshold()
    previous_provider_tokens = _latest_provider_input_tokens(run, exclude_sequence=budget_summary.get("sequence"))
    if threshold > 0 and previous_provider_tokens >= threshold:
        return f"previous_provider_input_tokens_{previous_provider_tokens}_gte_{threshold}"
    estimated_threshold = _response_context_reset_estimated_token_threshold()
    estimated_total = int(budget_summary.get("estimated_total_tokens") or 0)
    if estimated_threshold > 0 and estimated_total >= estimated_threshold:
        return f"estimated_total_tokens_{estimated_total}_gte_{estimated_threshold}"
    return ""


def _response_context_reset_provider_token_threshold() -> int:
    raw = os.getenv("FITCHECK_RESPONSE_CONTEXT_RESET_PROVIDER_TOKEN_THRESHOLD", "30000")
    try:
        return max(0, int(raw))
    except ValueError:
        return 30000


def _response_context_reset_estimated_token_threshold() -> int:
    raw = os.getenv("FITCHECK_RESPONSE_CONTEXT_RESET_ESTIMATED_TOKEN_THRESHOLD", "12000")
    try:
        return max(0, int(raw))
    except ValueError:
        return 12000


def _latest_provider_input_tokens(run: dict[str, Any], *, exclude_sequence: Any = None) -> int:
    for item in reversed(run.setdefault("state", {}).get("context_budget") or []):
        if not isinstance(item, dict):
            continue
        if exclude_sequence is not None and item.get("sequence") == exclude_sequence:
            continue
        try:
            value = int(item.get("provider_input_tokens") or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return 0


def _compact_pending_tool_outputs(
    run: dict[str, Any],
    pending_tool_outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for item in pending_tool_outputs or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call_output":
            call_id = str(item.get("call_id") or "")
            result = _decode_function_call_output(item)
            observation = _observation_for_call_id(run, call_id)
            compacted.append(
                {
                    "kind": "function_call_output",
                    "call_id": call_id,
                    "tool": observation.get("tool") if observation else _tool_name_for_call_id(run, call_id),
                    "arguments": (observation.get("arguments") if observation else {}) or {},
                    "result": _summarize_tool_output(result),
                }
            )
            continue
        if item.get("role") == "user":
            compacted.append(
                {
                    "kind": "user_context",
                    "content_excerpt": str(item.get("content") or "")[:1200],
                }
            )
            continue
        compacted.append({"kind": str(item.get("type") or "context"), "content_excerpt": json.dumps(item, ensure_ascii=False)[:1200]})
    return compacted


def _decode_function_call_output(item: dict[str, Any]) -> dict[str, Any]:
    output = item.get("output")
    if isinstance(output, dict):
        return output
    if isinstance(output, str):
        try:
            decoded = json.loads(output)
            return decoded if isinstance(decoded, dict) else {"status": "completed", "value": decoded}
        except json.JSONDecodeError:
            return {"status": "completed", "text_excerpt": output[:1200]}
    return {"status": "completed"}


def _observation_for_call_id(run: dict[str, Any], call_id: str) -> dict[str, Any] | None:
    if not call_id:
        return None
    for observation in reversed(run.setdefault("state", {}).get("observations") or []):
        if isinstance(observation, dict) and str(observation.get("call_id") or "") == call_id:
            return observation
    return None


def _tool_name_for_call_id(run: dict[str, Any], call_id: str) -> str:
    if not call_id:
        return ""
    for step in reversed(run.get("trace") or []):
        if step.get("step_type") != "tool_call":
            continue
        payload = step.get("input") or step.get("input_payload") or {}
        if isinstance(payload, dict) and str(payload.get("call_id") or "") == call_id:
            return str(step.get("name") or "")
    return ""


def _previous_response_id_for_request(run: dict[str, Any], budget_summary: dict[str, Any]) -> str | None:
    if budget_summary.get("response_context_reset_applied"):
        return None
    return run.get("state", {}).get("previous_response_id")


def _reset_provider_response_context_after_backend_terminal(
    run: dict[str, Any],
    *,
    reason: str,
    call_id: str | None = None,
) -> None:
    state = run.setdefault("state", {})
    previous_response_id = state.get("previous_response_id")
    state["pending_tool_outputs"] = []
    state["previous_response_id"] = None
    state["provider_response_context_reset"] = {
        "reason": reason,
        "previous_response_id": previous_response_id,
        "terminal_backend_call_id": call_id,
        "policy": (
            "Backend completed the run from stored observations without sending the last tool output "
            "back through the provider conversation. The next user reply must start from persisted "
            "run state instead of previous_response_id."
        ),
    }


def _reset_provider_response_context_after_interrupted_tool_calls(
    run: dict[str, Any],
    *,
    missing_call_ids: list[str],
) -> None:
    state = run.setdefault("state", {})
    previous_response_id = state.get("previous_response_id")
    state["pending_tool_outputs"] = []
    state["previous_response_id"] = None
    state["provider_response_context_reset"] = {
        "reason": "interrupted_tool_calls_without_outputs",
        "previous_response_id": previous_response_id,
        "missing_provider_tool_call_ids": list(missing_call_ids),
        "policy": (
            "The prior provider response requested tool calls, but the local run has no matching "
            "pending tool outputs or persisted observations. Resume from compact persisted run "
            "state instead of previous_response_id to avoid provider tool-output pairing errors."
        ),
    }


def _context_compaction_threshold() -> int:
    raw = os.getenv("FITCHECK_CONTEXT_COMPACTION_ESTIMATED_TOKEN_THRESHOLD", "8000")
    try:
        return max(0, int(raw))
    except ValueError:
        return 8000


def _latest_user_input_summary(run: dict[str, Any]) -> dict[str, Any]:
    input_payload = run.get("input") or {}
    replies = [reply for reply in input_payload.get("user_replies", []) or [] if isinstance(reply, dict)]
    latest_reply = replies[-1] if replies else {}
    return {
        "goal": run.get("goal") or input_payload.get("goal"),
        "free_text_excerpt": str(input_payload.get("free_text") or "")[:500],
        "candidate_url": input_payload.get("candidate_url"),
        "attachment_count": len(input_payload.get("attachments") or []),
        "latest_reply_message_excerpt": str(latest_reply.get("message") or "")[:500],
        "latest_reply_answer_count": len(latest_reply.get("answers") or []) if latest_reply else 0,
    }


def _compact_observation_summary(observation: dict[str, Any]) -> dict[str, Any]:
    result = observation.get("result") or {}
    summary = {
        "tool": observation.get("tool"),
        "call_id": observation.get("call_id"),
        "status": observation.get("result_status") or result.get("status"),
        "candidate_key": result.get("candidate_key") or (observation.get("arguments") or {}).get("candidate_key"),
    }
    for key in [
        "provider",
        "provider_mode",
        "candidate_count",
        "recommendation",
        "score_deferred",
        "evidence_status",
    ]:
        value = observation.get(key) if key in observation else result.get(key)
        if value is not None:
            summary[key] = value
    error = result.get("error")
    if isinstance(error, dict):
        summary["error_code"] = error.get("code")
    return summary


def _extract_output_text(response: dict[str, Any]) -> str:
    if response.get("output_text"):
        return str(response["output_text"])
    parts: list[str] = []
    for item in response.get("output", []) or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                parts.append(str(content["text"]))
    return "\n".join(parts).strip()

def _function_call_output(call_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": json.dumps(result, ensure_ascii=False),
    }


def _pending_tool_result_pairing_mismatch(
    run: dict[str, Any],
    pending_tool_outputs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    pending_call_ids = [
        str(item.get("call_id") or "")
        for item in pending_tool_outputs or []
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    pending_call_ids = [call_id for call_id in pending_call_ids if call_id]
    if not pending_call_ids:
        return None

    expected_call_ids = _latest_pending_tool_call_ids_from_trace(run)
    missing_results = [call_id for call_id in expected_call_ids if call_id not in pending_call_ids]
    orphan_results = [call_id for call_id in pending_call_ids if call_id not in expected_call_ids]
    duplicate_results = sorted(
        {
            call_id
            for call_id in pending_call_ids
            if pending_call_ids.count(call_id) > 1
        }
    )
    if not missing_results and not orphan_results and not duplicate_results:
        return None
    return {
        "latest_tool_call_ids": expected_call_ids,
        "pending_tool_result_ids": pending_call_ids,
        "missing_results": missing_results,
        "orphan_results": orphan_results,
        "duplicate_results": duplicate_results,
    }


def _unacknowledged_provider_tool_call_ids(
    run: dict[str, Any],
    pending_tool_outputs: list[dict[str, Any]],
) -> list[str]:
    expected_call_ids = _latest_pending_tool_call_ids_from_trace(run)
    if not expected_call_ids:
        return []

    acknowledged_call_ids = {
        str(item.get("call_id") or "")
        for item in pending_tool_outputs or []
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    }
    acknowledged_call_ids.update(
        str(item.get("call_id") or "")
        for item in run.setdefault("state", {}).get("observations") or []
        if isinstance(item, dict) and item.get("call_id")
    )
    for step in run.get("trace") or []:
        if step.get("step_type") != "tool_output":
            continue
        output = step.get("output") or {}
        if isinstance(output, dict) and output.get("call_id"):
            acknowledged_call_ids.add(str(output.get("call_id")))

    return [
        call_id
        for call_id in expected_call_ids
        if call_id and call_id not in acknowledged_call_ids
    ]


def _latest_pending_tool_call_ids_from_trace(run: dict[str, Any]) -> list[str]:
    trace = list(run.get("trace") or [])
    latest_llm_index = -1
    for index, step in enumerate(trace):
        if (
            step.get("step_type") == "llm_response"
            and step.get("status") == "completed"
            and int((step.get("output") or {}).get("tool_call_count") or 0) > 0
        ):
            latest_llm_index = index
    call_ids: list[str] = []
    for step in trace[latest_llm_index + 1 :]:
        if step.get("step_type") == "llm_response":
            break
        if step.get("step_type") != "tool_call":
            continue
        step_input = step.get("input") or {}
        if step_input.get("backend_recovery") or step_input.get("backend_enforced"):
            continue
        call_id = str(step_input.get("call_id") or "").strip()
        if call_id:
            call_ids.append(call_id)
    return call_ids


def _parallel_candidate_tool_batch_name(tool_calls: list[dict[str, Any]]) -> str | None:
    if len(tool_calls) < 2:
        return None
    names = {str(call.get("name") or "") for call in tool_calls}
    if len(names) != 1:
        return None
    tool_name = next(iter(names))
    if tool_name not in {
        "fetch_product_page",
        "classify_product_assets",
        "extract_size_material_info",
        "normalize_product_spec",
        "score_candidate_match",
    }:
        return None
    candidate_keys: list[str] = []
    for call in tool_calls:
        if call.get("arguments_parse_error"):
            return None
        arguments = call.get("arguments") or {}
        candidate_key = str(arguments.get("candidate_key") or "").strip()
        if not candidate_key or candidate_key.lower() in {"all", "*", "__all__"}:
            return None
        candidate_keys.append(candidate_key)
    if len(set(candidate_keys)) != len(candidate_keys):
        return None
    return tool_name


def _merge_parallel_tool_state(
    run: dict[str, Any],
    worker_state: dict[str, Any],
    call: dict[str, Any],
) -> None:
    candidate_key = str((call.get("arguments") or {}).get("candidate_key") or "").strip()
    if not candidate_key:
        return
    live_product = (worker_state.get("live_products") or {}).get(candidate_key)
    if live_product:
        run.setdefault("state", {}).setdefault("live_products", {})[candidate_key] = live_product
    worker_matches = [
        match
        for match in worker_state.get("match_results", []) or []
        if isinstance(match, dict) and str(match.get("candidate_key") or "") == candidate_key
    ]
    if worker_matches:
        existing = run.setdefault("state", {}).setdefault("match_results", [])
        existing_keys = {
            json.dumps(match, ensure_ascii=False, sort_keys=True)
            for match in existing
            if isinstance(match, dict)
        }
        for match in worker_matches:
            fingerprint = json.dumps(match, ensure_ascii=False, sort_keys=True)
            if fingerprint not in existing_keys:
                existing.append(match)
                existing_keys.add(fingerprint)
    _merge_hook_lifecycle_for_call(run, worker_state, call.get("call_id"))


def _record_tool_lifecycle_hook(
    run: dict[str, Any],
    *,
    hook_event: str,
    tool_name: str,
    call_id: str | None,
    status: str,
    duration_ms: int | None = None,
    input_payload: dict[str, Any] | None = None,
    output_payload: dict[str, Any] | None = None,
) -> None:
    record = {
        "sequence": len(run.setdefault("state", {}).setdefault("hook_lifecycle", [])) + 1,
        "hook_event": hook_event,
        "tool": tool_name,
        "call_id": call_id,
        "status": status,
        "mode": "record_only",
        "source": "fitcheck_backend_runtime",
        "created_at": _completed_at(),
    }
    if duration_ms is not None:
        record["duration_ms"] = duration_ms
    if input_payload:
        record["input"] = input_payload
    if output_payload:
        record["output"] = output_payload
    run["state"]["hook_lifecycle"].append(record)


def _merge_hook_lifecycle_for_call(
    run: dict[str, Any],
    worker_state: dict[str, Any],
    call_id: str | None,
) -> None:
    if not call_id:
        return
    records = [
        record
        for record in worker_state.get("hook_lifecycle", []) or []
        if isinstance(record, dict) and record.get("call_id") == call_id
    ]
    if not records:
        return
    target = run.setdefault("state", {}).setdefault("hook_lifecycle", [])
    existing = {
        (
            record.get("hook_event"),
            record.get("tool"),
            record.get("call_id"),
            record.get("created_at"),
        )
        for record in target
        if isinstance(record, dict)
    }
    for record in records:
        fingerprint = (
            record.get("hook_event"),
            record.get("tool"),
            record.get("call_id"),
            record.get("created_at"),
        )
        if fingerprint in existing:
            continue
        copied = dict(record)
        copied["sequence"] = len(target) + 1
        copied["parallel_group"] = "candidate_analysis"
        target.append(copied)
        existing.add(fingerprint)


def _llm_response_error_payload(
    exc: Exception,
    *,
    pending_tool_output_count: int,
    pending_tool_outputs_present: bool,
) -> dict[str, Any]:
    message = str(exc)
    provider_code = _llm_provider_error_code(exc)
    retryable = provider_code in {
        "HTTP_408_TIMEOUT",
        "HTTP_409_CONFLICT",
        "HTTP_429_RATE_LIMITED",
        "HTTP_500_SERVER_ERROR",
        "HTTP_502_BAD_GATEWAY",
        "HTTP_503_UNAVAILABLE",
        "HTTP_504_GATEWAY_TIMEOUT",
        "LLM_TIMEOUT",
        "LLM_CONNECTION_ERROR",
    }
    result_like = {
        "status": "failed",
        "error": {
            "code": provider_code or "LLM_RESPONSE_FAILED",
            "message": message,
        },
        "retry_errors": [message],
    }
    provider_metadata = provider_error_metadata_from_result(result_like)
    if provider_code:
        provider_metadata["provider_error_code"] = provider_code
        provider_metadata["provider_error_category"] = _llm_provider_error_category(provider_code)
    request_id = getattr(exc, "provider_request_id", None) or getattr(exc, "request_id", None)
    if request_id:
        provider_metadata["provider_request_id"] = str(request_id)
    return {
        "code": "LLM_RESPONSE_FAILED",
        "message": message,
        "retryable": retryable,
        "resume_available": pending_tool_outputs_present,
        "pending_tool_output_count": pending_tool_output_count,
        **provider_metadata,
    }


def _llm_provider_error_code(exc: Exception) -> str | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is None:
        match = re.search(r"\b(408|409|429|500|502|503|504)\b", str(exc))
        status_code = int(match.group(1)) if match else None
    if status_code == 408:
        return "HTTP_408_TIMEOUT"
    if status_code == 409:
        return "HTTP_409_CONFLICT"
    if status_code == 429:
        return "HTTP_429_RATE_LIMITED"
    if status_code == 500:
        return "HTTP_500_SERVER_ERROR"
    if status_code == 502:
        return "HTTP_502_BAD_GATEWAY"
    if status_code == 503:
        return "HTTP_503_UNAVAILABLE"
    if status_code == 504:
        return "HTTP_504_GATEWAY_TIMEOUT"
    lowered = str(exc).lower()
    if "timeout" in lowered or "timed out" in lowered:
        return "LLM_TIMEOUT"
    if "connection" in lowered or "connect" in lowered:
        return "LLM_CONNECTION_ERROR"
    return None


def _llm_provider_error_category(provider_code: str) -> str:
    if provider_code in {"HTTP_408_TIMEOUT", "HTTP_504_GATEWAY_TIMEOUT", "LLM_TIMEOUT"}:
        return "timeout"
    if provider_code == "HTTP_429_RATE_LIMITED":
        return "rate_limit"
    if provider_code == "LLM_CONNECTION_ERROR":
        return "connection"
    if provider_code in {"HTTP_500_SERVER_ERROR", "HTTP_502_BAD_GATEWAY", "HTTP_503_UNAVAILABLE"}:
        return "provider_unavailable"
    if provider_code == "HTTP_409_CONFLICT":
        return "provider_busy"
    return "provider_error"


_ORDINAL_PATTERNS: list[tuple[int, str]] = [
    (1, r"첫\s*번째|첫번째|첫\s*째|1\s*번|1\s*번째|일\s*번|first|1st|#\s*1|맨\s*처음|처음\s*꺼|처음\s*거"),
    (2, r"두\s*번째|두번째|둘\s*째|2\s*번|2\s*번째|이\s*번째|second|2nd|#\s*2"),
    (3, r"세\s*번째|세번째|셋\s*째|3\s*번|3\s*번째|삼\s*번째|third|3rd|#\s*3"),
    (4, r"네\s*번째|네번째|넷\s*째|4\s*번|4\s*번째|사\s*번째|fourth|4th|#\s*4"),
    (5, r"다섯\s*번째|5\s*번|5\s*번째|오\s*번째|fifth|5th|#\s*5"),
]


def _resolve_ordinal_reference(
    payload: dict[str, Any], prior_turn_summary: dict[str, Any]
) -> dict[str, Any] | None:
    """Map an ordinal/positional phrase in a reply to a prior shortlist item
    (docs/24 #8). Returns the resolved item, or None if no clear match."""
    message = str(payload.get("message") or "")
    if not message.strip():
        return None
    shortlist = (prior_turn_summary or {}).get("shortlist") or []
    if not shortlist:
        return None
    lowered = message.lower()
    for rank, pattern in _ORDINAL_PATTERNS:
        if re.search(pattern, lowered):
            for item in shortlist:
                if item.get("rank") == rank and item.get("candidate_key"):
                    return {
                        "rank": rank,
                        "candidate_key": item.get("candidate_key"),
                        "matched_phrase_rank": rank,
                        "resolution": "backend_ordinal_to_prior_shortlist",
                    }
    return None


def _capture_prior_turn_summary(run: dict[str, Any]) -> dict[str, Any]:
    """Compact, durable summary of the turn a reply is following (docs/24 #7/#8/#16).

    Carries the resolved baseline, the ordered shortlist (so ordinals like
    "두번째꺼" resolve to a concrete item), and observed prices (so a comparative
    "더 싼걸로" has a numeric anchor). Kept small to bound token/context cost.
    """
    state = run.get("state") or {}
    closet = state.get("closet_item") or {}
    match_results = state.get("match_results") or []
    shortlist: list[dict[str, Any]] = []
    for index, match in enumerate(match_results, start=1):
        if not isinstance(match, dict):
            continue
        score = match.get("score") or {}
        shortlist.append(
            {
                "rank": index,
                "candidate_key": match.get("candidate_key"),
                "recommendation": match.get("recommendation"),
                "overall_score": score.get("overall"),
                "price_score": score.get("price"),
            }
        )
    final_report = run.get("final_report") or {}
    return {
        "captured_from_status": run.get("status"),
        "baseline_closet_item": {
            "category": closet.get("category"),
            "brand": closet.get("brand"),
            "name": closet.get("name") or closet.get("display_name"),
            "baseline_identity": (closet.get("baseline_identity") or {}).get("status"),
        }
        if closet
        else None,
        "shortlist": shortlist,
        "shortlist_size": len(shortlist),
        "prior_report_type": final_report.get("report_type"),
        "prior_llm_final_text": (final_report.get("llm_final_text") or "")[:1500],
        "prior_search_constraints": state.get("search_constraints") or {},
    }


def _user_reply_input_item(payload: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    matched_recipes = _matched_agent_recipes(run)
    prior_turn_summary = (run.get("state") or {}).get("prior_turn_summary")
    content: dict[str, Any] = {
        "user_reply": payload,
        "user_replies": [payload],
        "available_skills": agent_skill_catalog(),
        "backend_matched_skills": [
            {
                "name": recipe.get("skill_name"),
                "description": recipe.get("when"),
                "source_path": recipe.get("source_path"),
            }
            for recipe in matched_recipes
        ],
        "instruction": (
            "Use this new user reply to choose the next backend tool. "
            "If a skill description matches, call load_agent_skill before its domain tools. "
            "If the user asks to search again, use the accumulated raw input and replies as search context."
        ),
    }
    resolved_ref = (run.get("state") or {}).get("resolved_shortlist_reference")
    if resolved_ref:
        content["resolved_shortlist_reference"] = resolved_ref
        content["backend_resolved_ordinal_instruction"] = (
            f"The backend resolved this reply to prior shortlist rank {resolved_ref.get('rank')} "
            f"(candidate_key='{resolved_ref.get('candidate_key')}'). Expand THAT exact candidate "
            "(fetch/extract/normalize/score then report). Do not cold-search or ask which item."
        )
    if prior_turn_summary:
        # docs/24 #7/#8/#16: give the model the prior turn's baseline + ordered
        # shortlist + prices so context-dependent follow-ups resolve concretely.
        content["prior_turn_summary"] = prior_turn_summary
        content["follow_up_resolution_instruction"] = (
            "This reply may refer to the prior turn. Resolve ordinals/positional "
            "references (e.g. '두번째꺼', 'the second one', '1번') to the matching "
            "prior_turn_summary.shortlist item by its rank, and expand THAT exact "
            "candidate_key instead of starting a new cold search. For comparative "
            "requests (e.g. '더 싼걸로', 'cheaper'), anchor to the prices shown in "
            "prior_turn_summary and search for candidates below that anchor. Keep "
            "the resolved baseline from prior_turn_summary.baseline_closet_item; do "
            "not discard it or claim the baseline is unknown when it is present here."
        )
    return {
        "role": "user",
        "content": json.dumps(content, ensure_ascii=False),
    }


def _initial_model_input(run: dict[str, Any]) -> str:
    applied_recipes = _matched_agent_recipes(run)
    payload = {
        "goal": run.get("goal"),
        "input": run.get("input", {}),
        "agent_memory": {
            "source": "backend_owned_progressive_disclosure_skills",
            "available_skills": agent_skill_catalog(),
            "matched_recipes": applied_recipes,
            "loaded_skills": run.get("state", {}).get("loaded_agent_skills", []),
            "policy": (
                "Catalog entries are summaries only. Call load_agent_skill for the full SOP when a description "
                "matches the task. The model chooses tools; backend-owned tools execute and persist observations."
            ),
        },
        "required_behavior": [
            "Inspect state before choosing tools.",
            "Load a relevant skill through load_agent_skill before following its SOP.",
            "Understand the liked clothing elements before ranking candidates.",
            "Use plan_context_and_sources to classify URLs and search constraints when the user input mixes baseline evidence, candidate URLs, attachments, or short follow-up commands.",
            "Do not require parse_user_goal before search; call search_web_candidates directly when raw input is enough.",
            "Use load_closet_memory and search_web_candidates before scoring.",
            "For a past purchase or owned baseline item, call enrich_closet_item_from_web before score_candidate_match.",
            "If input.attachments is non-empty, call inspect_uploaded_images before using the images.",
            "Use ask_missing_info only after search/fetch/OCR/normalize attempts still leave critical fields missing.",
            "After a user reply that supplies candidate measurements/material/price, call normalize_product_spec again before ask_missing_info.",
            "After a user reply that supplies a candidate product URL, call register_user_candidate_url before fetch_product_page.",
            "After a user reply that supplies baseline_evidence, call load_closet_memory again before score_candidate_match or ask_missing_info.",
            "Use write_purchase_report only after score_candidate_match.",
        ],
    }
    # docs/24 #7/#8/#16: carry the prior turn's baseline + ordered shortlist +
    # prices so a follow-up reply resolves ordinals/comparatives concretely and
    # does not lose the established baseline.
    prior_turn_summary = (run.get("state") or {}).get("prior_turn_summary")
    if prior_turn_summary:
        payload["prior_turn_summary"] = prior_turn_summary
        payload["required_behavior"].append(
            "This is a follow-up turn: resolve ordinals like '두번째꺼'/'the second one' "
            "to prior_turn_summary.shortlist by rank and expand that exact candidate_key "
            "instead of a cold re-search; anchor comparative price requests like '더 싼걸로' "
            "to prior_turn_summary prices; and keep the resolved baseline_closet_item."
        )
    resolved_ref = (run.get("state") or {}).get("resolved_shortlist_reference")
    if resolved_ref:
        payload["resolved_shortlist_reference"] = resolved_ref
        # docs/24 #8: an ordinal follow-up targets a known prior candidate, so
        # drop the baseline-first enrichment steps for this turn — they send the
        # model back into cold baseline re-derivation instead of expanding the
        # resolved item.
        payload["required_behavior"] = [
            step
            for step in payload["required_behavior"]
            if "enrich_closet_item_from_web" not in step
            and "load_closet_memory and search_web_candidates before scoring" not in step
        ]
        payload["required_behavior"].insert(
            0,
            "FOLLOW-UP RESOLUTION (highest priority): the backend already resolved "
            f"this reply to prior shortlist item rank {resolved_ref.get('rank')} "
            f"(candidate_key='{resolved_ref.get('candidate_key')}'). Immediately expand "
            "THAT candidate — score_candidate_match on its candidate_key using the "
            "already-observed data, then write_purchase_report with that candidate. Do "
            "NOT start a new search, do NOT re-derive the baseline, and do NOT ask the "
            "user which item they meant. If a field is missing, fetch_product_page / "
            "extract_size_material_info / normalize_product_spec for that one candidate only.",
        )
    return json.dumps(payload, ensure_ascii=False)


def _matched_agent_recipes(run: dict[str, Any]) -> list[dict[str, Any]]:
    existing = run.get("state", {}).get("matched_agent_skills")
    if isinstance(existing, list):
        return existing
    run_text = _run_text_for_recipe_matching(run)
    return match_agent_recipes(
        run_text,
        has_url=bool(re.search(r"https?://\S+", run_text, re.IGNORECASE)),
        has_attachments=bool(run.get("input", {}).get("attachments")),
    )


def _refresh_agent_skill_context(run: dict[str, Any]) -> None:
    run_text = _run_text_for_recipe_matching(run)
    matched = match_agent_recipes(
        run_text,
        has_url=bool(re.search(r"https?://\S+", run_text, re.IGNORECASE)),
        has_attachments=bool(run.get("input", {}).get("attachments")),
    )
    state = run.setdefault("state", {})
    state["matched_agent_skills"] = matched
    fingerprint = json.dumps(
        [recipe.get("skill_name") for recipe in matched],
        ensure_ascii=False,
        sort_keys=True,
    )
    if state.get("agent_skill_match_fingerprint") == fingerprint:
        return
    state["agent_skill_match_fingerprint"] = fingerprint
    append_trace(
        run,
        step_type="context",
        name="match_agent_skills",
        status="completed",
        input_payload={"text_length": len(run_text)},
        output_payload={
            "matched_skills": [
                {
                    "name": recipe.get("skill_name"),
                    "description": recipe.get("when"),
                    "source_path": recipe.get("source_path"),
                }
                for recipe in matched
            ],
            "available_skill_count": len(agent_skill_catalog()),
            "selection_policy": "metadata_first_progressive_disclosure",
        },
    )


def _agent_instructions_for_run(run: dict[str, Any]) -> str:
    loaded_sections: list[str] = []
    for skill_name in run.get("state", {}).get("loaded_agent_skills", []) or []:
        try:
            skill = load_agent_skill(str(skill_name))
        except KeyError:
            continue
        loaded_sections.append(
            f"\n\n## Loaded FitCheck Skill: {skill['name']}\n"
            f"Source: {skill['source_path']}\n"
            f"{skill['instructions']}"
        )
    return FITCHECK_AGENT_INSTRUCTIONS + "".join(loaded_sections)


def _run_text_for_recipe_matching(run: dict[str, Any]) -> str:
    input_payload = run.get("input", {}) or {}
    parts = [run.get("goal"), input_payload.get("goal"), input_payload.get("free_text"), input_payload.get("candidate_url")]
    for reply in input_payload.get("user_replies", []) or []:
        if not isinstance(reply, dict):
            continue
        parts.append(reply.get("message"))
        for answer in reply.get("answers", []) or []:
            if isinstance(answer, dict):
                parts.extend([answer.get("field"), answer.get("value")])
    return " ".join(str(part or "") for part in parts).strip()


def _baseline_identity_rejection_from_latest_reply(run: dict[str, Any]) -> dict[str, Any] | None:
    state = run.get("state") or {}
    candidates = [
        item
        for item in state.get("unconfirmed_baseline_identity_candidates", []) or []
        if isinstance(item, dict)
    ]
    if not candidates:
        return None
    replies = run.get("input", {}).get("user_replies", []) or []
    if not replies or not isinstance(replies[-1], dict):
        return None
    reply = replies[-1]
    parts = [reply.get("message")]
    for answer in reply.get("answers", []) or []:
        if isinstance(answer, dict):
            parts.extend([answer.get("field"), answer.get("value")])
    text = re.sub(r"\s+", " ", " ".join(str(part or "") for part in parts)).strip()
    if not text:
        return None
    if not re.search(r"(아니|아님|틀렸|달라|그거\s*아니|잘못|no\b|not\s+it|wrong|different)", text, re.IGNORECASE):
        return None
    rejected_candidates = []
    for item in candidates:
        rejected_candidates.append(
            {
                "candidate_key": item.get("candidate_key"),
                "name": item.get("name"),
                "source_url": item.get("source_url"),
                "provider": item.get("provider"),
                "rejection_text": text,
                "reason": "user_rejected_unconfirmed_baseline_identity",
            }
        )
    return {
        "rejection_text": text,
        "rejected_candidates": rejected_candidates,
        "next_action": "revise_baseline_search_with_latest_user_constraints",
    }


def _baseline_identity_confirmation_args_from_latest_reply(run: dict[str, Any]) -> dict[str, Any] | None:
    state = run.get("state") or {}
    candidates = [
        item
        for item in state.get("unconfirmed_baseline_identity_candidates", []) or []
        if isinstance(item, dict)
    ]
    if not candidates:
        return None
    replies = run.get("input", {}).get("user_replies", []) or []
    if not replies or not isinstance(replies[-1], dict):
        return None
    reply = replies[-1]
    parts = [reply.get("message")]
    candidate_key = ""
    source_url = ""
    for answer in reply.get("answers", []) or []:
        if not isinstance(answer, dict):
            continue
        field = str(answer.get("field") or "")
        value = str(answer.get("value") or "")
        parts.extend([field, value])
        if field in {"candidate_key", "baseline_identity_candidate_key"}:
            candidate_key = value.strip()
        if field in {"source_url", "baseline_identity_source_url"}:
            source_url = value.strip()
    text = re.sub(r"\s+", " ", " ".join(str(part or "") for part in parts)).strip()
    if not text:
        return None
    for item in candidates:
        key = str(item.get("candidate_key") or "")
        url = str(item.get("source_url") or "")
        if key and key in text:
            candidate_key = key
            break
        if url and url in text:
            source_url = url
            break
    if not candidate_key and not source_url and re.search(r"(첫|1\s*번|첫\s*번째|맞|확인|confirm|yes)", text, re.IGNORECASE):
        candidate_key = str(candidates[0].get("candidate_key") or "")
    if not candidate_key and not source_url:
        return None
    result = {
        "candidate_key": candidate_key,
        "confirmation_text": text,
    }
    if source_url:
        result["source_url"] = source_url
    return result


def _latest_reply_requests_post_confirmation_continuation(run: dict[str, Any]) -> bool:
    replies = run.get("input", {}).get("user_replies", []) or []
    if not replies or not isinstance(replies[-1], dict):
        return False
    reply = replies[-1]
    parts = [reply.get("message")]
    for answer in reply.get("answers", []) or []:
        if isinstance(answer, dict):
            parts.append(str(answer.get("value") or ""))
    text = re.sub(r"\s+", " ", " ".join(str(part or "") for part in parts)).strip().lower()
    if not text:
        return False
    return bool(
        re.search(r"\b(continue|recommend|search|find|compare|shortlist)\b", text, re.IGNORECASE)
        or any(keyword in text for keyword in ["계속", "추천", "찾아", "검색", "비슷", "비교", "후보"])
    )


def _should_backend_preload_closet_memory(run: dict[str, Any], store: Any | None = None) -> bool:
    if "load_closet_memory" in _tool_calls_after_latest_user_reply(run):
        return False
    state = run.get("state") or {}
    if (state.get("closet_item") or {}).get("memory_source") == "durable_closet_memory":
        return False
    text = _latest_user_turn_text(run)
    if not text:
        text = _run_text_for_recipe_matching(run)
    if not _text_mentions_durable_closet_memory(text):
        return False
    session_id = str(run.get("session_id") or run.get("input", {}).get("session_id") or "").strip()
    if not store or not session_id or not hasattr(store, "list_closet_items"):
        return False
    try:
        return any(
            isinstance(item, dict)
            and (item.get("baseline_identity") or {}).get("status") == "memory_identity_available"
            for item in store.list_closet_items(session_id)
        )
    except Exception:
        return False


def _latest_user_turn_text(run: dict[str, Any]) -> str:
    replies = run.get("input", {}).get("user_replies", []) or []
    if replies and isinstance(replies[-1], dict):
        reply = replies[-1]
        parts = [reply.get("message")]
        for answer in reply.get("answers", []) or []:
            if isinstance(answer, dict):
                parts.extend([answer.get("field"), answer.get("value")])
        return re.sub(r"\s+", " ", " ".join(str(part or "") for part in parts)).strip()
    input_payload = run.get("input", {}) or {}
    return re.sub(
        r"\s+",
        " ",
        " ".join(str(part or "") for part in [run.get("goal"), input_payload.get("goal"), input_payload.get("free_text")]),
    ).strip()


def _text_mentions_durable_closet_memory(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    explicit_memory = any(
        keyword in normalized
        for keyword in ["closet memory", "saved closet", "saved item", "saved clothing", "\uc800\uc7a5\ud55c \uc637"]
    )
    memory_reference = any(
        keyword in normalized
        for keyword in ["\uc800\uc7a5", "\uae30\uc5b5", "\ud655\uc778\ud55c", "closet", "memory", "saved"]
    )
    clothing_reference = any(
        keyword in normalized
        for keyword in [
            "\uc637",
            "\uc0c1\ud488",
            "\uae30\uc900",
            "\ub0b4 \uc637",
            "\ubc18\ud314",
            "\ud2f0",
            "\ud6c4\ub4dc",
            "\uc154\uce20",
            "item",
            "clothing",
            "shirt",
            "tee",
            "hoodie",
        ]
    )
    return explicit_memory or (memory_reference and clothing_reference)


def _latest_report_observation(run: dict[str, Any]) -> dict[str, Any] | None:
    for observation in reversed(run.get("state", {}).get("observations", [])):
        if observation.get("tool") == "write_purchase_report":
            return observation
    return None


def _latest_empty_search_after_latest_user_reply(run: dict[str, Any]) -> dict[str, Any] | None:
    latest_reply_step = _latest_trace_step_index(run, step_type="user_reply")
    for observation in reversed(run.get("state", {}).get("observations", [])):
        if observation.get("tool") != "search_web_candidates":
            continue
        call_id = observation.get("call_id")
        if latest_reply_step is not None and call_id:
            output_step = _observation_trace_index(run, observation)
            if output_step is not None and output_step <= latest_reply_step:
                return None
        result = observation.get("result") or {}
        if (
            result.get("search_deferred")
            and int(result.get("candidate_count") or 0) == 0
            and _empty_search_recovery_exhausted(run, result)
        ):
            return observation
        return None
    return None


def _latest_search_has_normalized_candidates_after_latest_user_reply(run: dict[str, Any]) -> bool:
    latest_reply_step = _latest_trace_step_index(run, step_type="user_reply")
    latest_search_step: int | None = None
    for observation in reversed(run.get("state", {}).get("observations", [])):
        if observation.get("tool") != "search_web_candidates":
            continue
        observation_step = _observation_trace_index(run, observation)
        if latest_reply_step is not None and (observation_step is None or observation_step <= latest_reply_step):
            continue
        result = observation.get("result") or {}
        if result.get("status") == "completed" and int(result.get("candidate_count") or 0) > 0:
            latest_search_step = observation_step
            break
    if latest_search_step is None:
        return False
    normalized_candidate_keys: set[str] = set()
    for observation in run.get("state", {}).get("observations", []) or []:
        if observation.get("tool") != "normalize_product_spec" or observation.get("baseline_internal_pipeline"):
            continue
        observation_step = _observation_trace_index(run, observation)
        if observation_step is None or observation_step <= latest_search_step:
            continue
        result = observation.get("result") or {}
        if result.get("status") != "completed":
            continue
        candidate_key = str(result.get("candidate_key") or (observation.get("arguments") or {}).get("candidate_key") or "")
        if candidate_key and candidate_key.lower() not in {"all", "*", "__all__"}:
            normalized_candidate_keys.add(candidate_key)
    return bool(normalized_candidate_keys)


def _latest_search_candidate_count_after_latest_user_reply(run: dict[str, Any]) -> int:
    latest_reply_step = _latest_trace_step_index(run, step_type="user_reply")
    for observation in reversed(run.get("state", {}).get("observations", []) or []):
        if observation.get("tool") != "search_web_candidates":
            continue
        observation_step = _observation_trace_index(run, observation)
        if latest_reply_step is not None and (observation_step is None or observation_step <= latest_reply_step):
            continue
        result = observation.get("result") or {}
        if result.get("status") != "completed":
            continue
        try:
            return int(result.get("candidate_count") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _single_candidate_report_should_be_aggregated(run: dict[str, Any], report_result: dict[str, Any]) -> bool:
    if str(report_result.get("candidate_key") or "").lower() in {"all", "*", "__all__"}:
        return False
    if str(report_result.get("report_type") or "") != "single_candidate":
        return False
    if _latest_search_candidate_count_after_latest_user_reply(run) < 2:
        return False
    if _latest_completed_all_score_observation_after_latest_user_reply(run):
        return True
    return _has_normalized_candidates_after_latest_user_reply(run)


def _has_normalized_candidates_after_latest_user_reply(run: dict[str, Any]) -> bool:
    latest_reply_step = _latest_trace_step_index(run, step_type="user_reply")
    for observation in run.get("state", {}).get("observations", []) or []:
        if observation.get("tool") != "normalize_product_spec" or observation.get("baseline_internal_pipeline"):
            continue
        observation_step = _observation_trace_index(run, observation)
        if latest_reply_step is not None and (observation_step is None or observation_step <= latest_reply_step):
            continue
        result = observation.get("result") or {}
        if result.get("status") != "completed":
            continue
        candidate_key = str(result.get("candidate_key") or (observation.get("arguments") or {}).get("candidate_key") or "")
        if candidate_key and candidate_key.lower() not in {"all", "*", "__all__", "baseline_current_liked_item"}:
            return True
    return False


def _latest_completed_all_score_observation_after_latest_user_reply(run: dict[str, Any]) -> dict[str, Any] | None:
    latest_reply_step = _latest_trace_step_index(run, step_type="user_reply")
    for observation in reversed(run.get("state", {}).get("observations", []) or []):
        if observation.get("tool") != "score_candidate_match":
            continue
        if latest_reply_step is not None:
            observation_step = _observation_trace_index(run, observation)
            if observation_step is None or observation_step <= latest_reply_step:
                continue
        result = observation.get("result") or {}
        if result.get("status") != "completed" or result.get("score_deferred"):
            continue
        candidate_key = str(result.get("candidate_key") or (observation.get("arguments") or {}).get("candidate_key") or "")
        if candidate_key.lower() in {"all", "*", "__all__"}:
            return observation
    return None


def _empty_search_recovery_exhausted(run: dict[str, Any], result: dict[str, Any]) -> bool:
    suggestions = result.get("next_tool_suggestions") or []
    if suggestions:
        return _run_requests_explicit_web_search(run)
    if result.get("automatic_recovery_exhausted"):
        return True
    return True


def _run_requests_explicit_web_search(run: dict[str, Any]) -> bool:
    parts = [
        run.get("goal"),
        (run.get("input") or {}).get("goal"),
        (run.get("input") or {}).get("free_text"),
    ]
    for reply in (run.get("input") or {}).get("user_replies", []) or []:
        if not isinstance(reply, dict):
            continue
        parts.append(reply.get("message"))
        for answer in reply.get("answers", []) or []:
            if isinstance(answer, dict):
                parts.append(str(answer.get("value") or ""))
    text = " ".join(str(part or "") for part in parts).lower()
    return any(
        cue in text
        for cue in [
            "web search",
            "search web",
            "웹검색",
            "웹 검색",
            "웹서치",
            "웹 서치",
        ]
    )


def _latest_report_observation_after_latest_user_reply(run: dict[str, Any]) -> dict[str, Any] | None:
    latest_reply_step = _latest_trace_step_index(run, step_type="user_reply")
    if latest_reply_step is None:
        return _latest_report_observation(run)
    if not _has_trace_after(
        run,
        step_type="tool_output",
        name="write_purchase_report",
        after_step_index=latest_reply_step,
    ):
        return None
    return _latest_report_observation(run)


def _latest_completed_score_observation_after_latest_user_reply(run: dict[str, Any]) -> dict[str, Any] | None:
    latest_reply_step = _latest_trace_step_index(run, step_type="user_reply")
    for observation in reversed(run.get("state", {}).get("observations", [])):
        if observation.get("tool") != "score_candidate_match":
            continue
        if latest_reply_step is not None:
            observation_step = _observation_trace_index(run, observation)
            if observation_step is None or observation_step <= latest_reply_step:
                continue
        result = observation.get("result") or {}
        if result.get("status") == "completed" and not result.get("score_deferred"):
            return observation
    return None


def _has_completed_candidate_scores_after_latest_user_reply(run: dict[str, Any]) -> bool:
    latest_reply_step = _latest_trace_step_index(run, step_type="user_reply")
    saw_individual_score = False
    for observation in run.get("state", {}).get("observations", []) or []:
        if observation.get("tool") != "score_candidate_match":
            continue
        if latest_reply_step is not None:
            observation_step = _observation_trace_index(run, observation)
            if observation_step is None or observation_step <= latest_reply_step:
                continue
        result = observation.get("result") or {}
        if result.get("status") != "completed" or result.get("score_deferred"):
            continue
        candidate_key = str(result.get("candidate_key") or (observation.get("arguments") or {}).get("candidate_key") or "")
        if candidate_key.lower() in {"all", "*", "__all__"}:
            return False
        if candidate_key:
            saw_individual_score = True
    return saw_individual_score


def _has_reportable_match_results_for_runtime(run: dict[str, Any]) -> bool:
    matches = run.get("state", {}).get("match_results", [])
    if not isinstance(matches, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("status") == "completed"
        and item.get("candidate_key")
        and isinstance(item.get("score"), dict)
        for item in matches
    )


def _latest_deferred_score_observation_after_latest_user_reply(run: dict[str, Any]) -> dict[str, Any] | None:
    latest_reply_step = _latest_trace_step_index(run, step_type="user_reply")
    for observation in reversed(run.get("state", {}).get("observations", [])):
        if observation.get("tool") == "write_purchase_report":
            if latest_reply_step is not None:
                observation_step = _observation_trace_index(run, observation)
                if observation_step is None or observation_step <= latest_reply_step:
                    continue
            result = observation.get("result") or {}
            if result.get("status") == "completed" and result.get("grounded") is not False:
                return None
            continue
        if observation.get("tool") != "score_candidate_match":
            continue
        if latest_reply_step is not None:
            observation_step = _observation_trace_index(run, observation)
            if observation_step is None or observation_step <= latest_reply_step:
                continue
        result = observation.get("result") or {}
        if result.get("status") == "completed" and result.get("score_deferred"):
            return observation
        if result.get("status") == "completed":
            return None
    return None


def _deferred_score_requires_user_confirmation(result: dict[str, Any]) -> bool:
    if not result.get("score_deferred"):
        return False
    return str(result.get("reason") or "") in {
        "baseline_identity_confirmation_required_before_scoring",
        "baseline_identity_clarification_required_before_scoring",
    }


def _tool_calls_after_latest_user_reply(run: dict[str, Any]) -> list[str]:
    latest_reply_step = _latest_trace_step_index(run, step_type="user_reply")
    if latest_reply_step is None:
        return []
    return [
        str(step.get("name"))
        for step in run.get("trace", []) or []
        if step.get("step_type") == "tool_call"
        and int(step.get("step_index") or 0) > latest_reply_step
    ]


def _should_backend_preplan_latest_user_reply(run: dict[str, Any]) -> bool:
    if _latest_trace_step_index(run, step_type="user_reply") is None:
        return False
    return "plan_context_and_sources" not in _tool_calls_after_latest_user_reply(run)


def _candidate_key_for_report_guidance(score_observation: dict[str, Any]) -> str:
    result = score_observation.get("result") or {}
    if str(result.get("candidate_key") or "").lower() in {"all", "*", "__all__"}:
        return "all"
    ranked = result.get("ranked_matches")
    if isinstance(ranked, list):
        for item in ranked:
            if isinstance(item, dict) and item.get("candidate_key"):
                return str(item["candidate_key"])
    if result.get("candidate_key"):
        return str(result["candidate_key"])
    return "all"


def _candidate_key_for_backend_required_report(
    run: dict[str, Any],
    score_observation: dict[str, Any] | None,
) -> str:
    candidate_key = (
        _candidate_key_for_report_guidance(score_observation)
        if score_observation is not None
        else "all"
    )
    if (
        candidate_key.lower() not in {"all", "*", "__all__"}
        and _latest_search_candidate_count_after_latest_user_reply(run) >= 2
        and _has_completed_candidate_scores_after_latest_user_reply(run)
    ):
        return "all"
    return candidate_key


def _write_all_report_suggestion(result: dict[str, Any]) -> dict[str, Any] | None:
    for suggestion in result.get("next_tool_suggestions") or []:
        if not isinstance(suggestion, dict):
            continue
        if str(suggestion.get("tool") or "") != "write_purchase_report":
            continue
        arguments = suggestion.get("arguments") if isinstance(suggestion.get("arguments"), dict) else {}
        if str(arguments.get("candidate_key") or "") == "all":
            return {**suggestion, "arguments": arguments}
    return None


def _score_all_suggestion(result: dict[str, Any]) -> dict[str, Any] | None:
    for suggestion in result.get("next_tool_suggestions") or []:
        if not isinstance(suggestion, dict):
            continue
        if str(suggestion.get("tool") or "") != "score_candidate_match":
            continue
        arguments = suggestion.get("arguments") if isinstance(suggestion.get("arguments"), dict) else {}
        if str(arguments.get("candidate_key") or "") == "all":
            return {**suggestion, "arguments": arguments}
    return None


def _latest_trace_step_index(run: dict[str, Any], *, step_type: str) -> int | None:
    for step in reversed(run.get("trace", [])):
        if step.get("step_type") == step_type:
            try:
                return int(step.get("step_index"))
            except (TypeError, ValueError):
                return None
    return None


def _observation_trace_index(run: dict[str, Any], observation: dict[str, Any]) -> int | None:
    call_id = observation.get("call_id")
    tool = observation.get("tool")
    if not call_id or not tool:
        return None
    for step in run.get("trace", []):
        if step.get("step_type") != "tool_output" or step.get("name") != tool:
            continue
        output = step.get("output") or {}
        if output.get("call_id") != call_id:
            continue
        try:
            return int(step.get("step_index"))
        except (TypeError, ValueError):
            return None
    return None


def _has_trace_after(
    run: dict[str, Any],
    *,
    step_type: str,
    name: str,
    after_step_index: int,
) -> bool:
    for step in run.get("trace", []):
        if step.get("step_type") != step_type or step.get("name") != name:
            continue
        try:
            step_index = int(step.get("step_index"))
        except (TypeError, ValueError):
            continue
        if step_index > after_step_index:
            return True
    return False


def _safe_run_input(payload: dict[str, Any]) -> dict[str, Any]:
    safe = dict(payload)
    if "api_key" in safe:
        safe["api_key"] = "[redacted]"
    return safe


def _has_initial_user_input(payload: dict[str, Any]) -> bool:
    return (
        _has_non_empty_text(payload.get("goal"))
        or _has_non_empty_text(payload.get("free_text"))
        or _has_non_empty_text(payload.get("candidate_url"))
        or bool(payload.get("attachments"))
    )


def _has_reply_user_input(payload: dict[str, Any]) -> bool:
    return (
        _has_non_empty_text(payload.get("message"))
        or bool(payload.get("answers"))
        or bool(payload.get("attachments"))
    )


def _has_non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _same_session(run: dict[str, Any], payload: dict[str, Any]) -> bool:
    run_session_id = run.get("session_id") or run.get("input", {}).get("session_id")
    payload_session_id = payload.get("session_id")
    if not run_session_id:
        return True
    if not payload_session_id:
        return False
    return str(run_session_id) == str(payload_session_id)


def _llm_request_name(run: dict[str, Any]) -> str:
    api_style = run.get("llm", {}).get("api_style")
    if api_style == "chat_completions":
        return "chat.completions.create"
    return "responses.create"


def _summarize_tool_output(result: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "status": result.get("status"),
    }
    for key in [
        "candidate_key",
        "candidate_count",
        "provider",
        "provider_mode",
        "ocr_provider",
        "ocr_provider_mode",
        "external_api_called",
        "timeout_seconds",
        "retry_count",
        "retry_errors",
        "provider_error_code",
        "provider_error_category",
        "provider_error_message_preview",
        "provider_retry_error_codes",
        "provider_latest_retry_error_code",
        "provider_latest_retry_error_category",
        "evidence_status",
        "search_deferred",
        "query",
        "model_query",
        "search_constraints",
        "source_refs",
        "grounding_policy",
        "execution_plan",
        "applied_recipes",
        "search_attempt",
        "baseline_related_candidate_merge",
        "query_quality",
        "domain_policy",
        "score_deferred",
        "recommendation",
        "score",
        "questions",
        "deferred_user_question",
        "question_policy",
        "next_tool_suggestions",
        "validation_errors",
        "raw_arguments_excerpt",
        "grounded",
        "grounding_validation",
        "final_report",
        "error",
    ]:
        if key in result:
            summary[key] = result[key]
    summary.update(provider_error_metadata_from_result({**result, **summary}))
    retry_badges = _retry_badges_from_result(result)
    if retry_badges:
        summary["retry_badges"] = retry_badges
    return summary


def _mark_execution_plan_tool(run: dict[str, Any], tool_name: str, status: str) -> None:
    plan = run.setdefault("state", {}).get("execution_plan")
    if not isinstance(plan, dict):
        return
    normalized_status = _plan_status_from_tool_status(status)
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return
    for step in steps:
        if not isinstance(step, dict):
            continue
        tools = step.get("tools") or []
        if tool_name not in tools:
            continue
        step.setdefault("tool_statuses", {})[tool_name] = normalized_status
        if normalized_status == "failed":
            step["status"] = "failed"
        elif normalized_status == "in_progress":
            if step.get("status") not in {"completed", "failed"}:
                step["status"] = "in_progress"
        elif normalized_status == "completed":
            tool_statuses = step.get("tool_statuses") or {}
            if all(tool_statuses.get(tool) == "completed" for tool in tools if tool in tool_statuses):
                step["status"] = "completed"
            elif step.get("status") != "failed":
                step["status"] = "in_progress"
        break
    _refresh_execution_plan_summary(plan)


def _plan_status_from_tool_status(status: str) -> str:
    if status in {"failed", "configuration_required"}:
        return "failed"
    if status in {"completed", "waiting_for_user"}:
        return "completed"
    return "in_progress"


def _refresh_execution_plan_summary(plan: dict[str, Any]) -> None:
    steps = [step for step in plan.get("steps") or [] if isinstance(step, dict)]
    if not steps:
        return
    if any(step.get("status") == "failed" for step in steps):
        plan["status"] = "failed"
    elif all(step.get("status") in {"completed", "skipped"} for step in steps):
        plan["status"] = "completed"
    elif any(step.get("status") == "in_progress" for step in steps):
        plan["status"] = "in_progress"
    else:
        plan["status"] = "pending"
    candidate_step = next((step for step in steps if step.get("id") == "candidate_enrichment"), None)
    for group in plan.get("parallel_groups") or []:
        if not isinstance(group, dict) or group.get("id") != "candidate_analysis":
            continue
        if candidate_step:
            group["status"] = candidate_step.get("status", "pending")
            group["tool_statuses"] = candidate_step.get("tool_statuses", {})


def _retry_badges_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    provider = result.get("provider") or result.get("ocr_provider")
    has_retry_metadata = any(
        key in result
        for key in [
            "retry_count",
            "retry_errors",
            "timeout_seconds",
        ]
    )
    if not provider and not has_retry_metadata:
        return []

    try:
        retry_count = int(result.get("retry_count") or 0)
    except (TypeError, ValueError):
        retry_count = 0
    raw_retry_errors = result.get("retry_errors") or []
    if isinstance(raw_retry_errors, list):
        retry_errors = raw_retry_errors
    else:
        retry_errors = [str(raw_retry_errors)]
    status = result.get("status")
    error_metadata = provider_error_metadata_from_result(result)
    provider_error_category = error_metadata.get("provider_error_category")
    provider_failed = (
        status == "failed"
        or result.get("fetch_status") == "failed"
        or bool(result.get("fetch_error"))
        or provider_error_category
        in {
            "configuration",
            "provider_not_connected",
            "timeout",
            "provider_process_failed",
            "local_file_missing",
            "asset_boundary",
            "ocr_error",
        }
    )
    if status == "configuration_required" or provider_error_category in {"configuration", "provider_not_connected"}:
        badge_status = "not_started"
        label = "not configured"
    elif retry_count <= 0 and provider_failed:
        badge_status = "failed_no_retry"
        label = "failed without retry"
    elif retry_count <= 0:
        badge_status = "not_retried"
        label = "no retry"
    elif provider_failed:
        badge_status = "retry_failed"
        label = f"retry failed {retry_count}x"
    else:
        badge_status = "retried"
        label = f"retried {retry_count}x"

    latest_error = retry_errors[-1] if retry_errors else None
    badge: dict[str, Any] = {
        "id": f"R{retry_count}",
        "label": label,
        "status": badge_status,
        "provider": provider,
        "provider_mode": result.get("provider_mode")
        or result.get("ocr_provider_mode")
        or result.get("mode"),
        "retry_count": retry_count,
        "retry_error_count": len(retry_errors),
        "source_kind": "provider_runtime",
    }
    if "timeout_seconds" in result:
        badge["timeout_seconds"] = result.get("timeout_seconds")
    if "external_api_called" in result:
        badge["external_api_called"] = result.get("external_api_called")
    if latest_error:
        badge["latest_error_preview"] = str(latest_error)[:160]
    for key in [
        "provider_error_code",
        "provider_error_category",
        "provider_retry_error_codes",
        "provider_latest_retry_error_code",
        "provider_latest_retry_error_category",
        "provider_evidence_error_codes",
        "provider_latest_evidence_error_code",
        "provider_latest_evidence_error_category",
    ]:
        if key in error_metadata:
            badge[key] = error_metadata[key]
    return [badge]


def _deferred_enrichment_guidance(result: dict[str, Any]) -> dict[str, Any] | None:
    suggestions = result.get("next_tool_suggestions") or []
    if not suggestions:
        return None
    reason = str(result.get("reason") or "")
    if not reason:
        if result.get("search_deferred"):
            reason = "search_recovery_required"
        elif result.get("score_deferred"):
            reason = "score_recovery_required"
        elif result.get("deferred_user_question"):
            reason = "tool_enrichment_required_before_user_question"
    if not (
        result.get("deferred_user_question")
        or result.get("search_deferred")
        or result.get("score_deferred")
        or reason
        in {
            "candidate_fields_must_be_enriched_with_tools_before_asking_user",
            "candidate_enrichment_required_before_scoring",
            "candidate_enrichment_required_before_report",
            "candidate_not_close_enough_for_similar_fit_report",
            "continue_search_for_closer_measurements",
            "continue_search_for_closer_measurements_before_report",
            "search_query_expansion_required",
            "search_recovery_required",
            "user_supplied_evidence_requires_normalize",
            "baseline_evidence_recovery_required_before_report",
            "baseline_enrichment_required_before_scoring",
            "score_required_after_baseline_enrichment",
            "user_supplied_baseline_evidence_requires_memory_reload",
            "user_supplied_candidate_url_requires_registration",
            "context_sources_require_backend_tool_followup",
            "baseline_identity_required_before_candidate_search",
        }
    ):
        return None

    guidance = {
        "guidance_type": "backend_enrichment_required",
        "candidate_key": result.get("candidate_key"),
        "reason": reason,
        "missing_fields": result.get("missing_fields", []),
        "next_tool_suggestions": _dedupe_tool_suggestions(suggestions),
        "required_action": (
            "Call the suggested backend tools before asking the user, scoring, "
            "or writing a report."
        ),
        "ask_user_allowed": False,
    }
    close_fit_budget = result.get("close_fit_search_budget")
    if isinstance(close_fit_budget, dict):
        guidance["close_fit_search_budget"] = close_fit_budget
    search_recovery_policy = result.get("search_recovery_policy")
    if search_recovery_policy:
        guidance["search_recovery_policy"] = search_recovery_policy
    return guidance


def _append_backend_runtime_guidance(
    run: dict[str, Any],
    pending_tool_outputs: list[dict[str, Any]],
    guidance: dict[str, Any],
    *,
    trace_name: str,
) -> list[dict[str, Any]]:
    run.setdefault("state", {}).setdefault("enrichment_guidance", []).append(guidance)
    updated_pending = list(pending_tool_outputs)
    updated_pending.append(
        {
            "role": "user",
            "content": json.dumps(
                {
                    "backend_runtime_guidance": guidance,
                },
                ensure_ascii=False,
            ),
        }
    )
    append_trace(
        run,
        step_type="tool_guidance",
        name=trace_name,
        status="completed",
        output_payload=guidance,
    )
    run.setdefault("state", {})["pending_tool_outputs"] = updated_pending
    return updated_pending


def _append_backend_observation_context(
    run: dict[str, Any],
    pending_tool_outputs: list[dict[str, Any]],
    observation: dict[str, Any],
    *,
    instruction: str,
    trace_name: str,
) -> list[dict[str, Any]]:
    result = observation.get("result") or {}
    updated_pending = list(pending_tool_outputs)
    updated_pending.append(
        {
            "role": "user",
            "content": json.dumps(
                {
                    "backend_recovery_observations": [
                        {
                            "tool": observation.get("tool"),
                            "arguments": observation.get("arguments", {}),
                            "result": _summarize_tool_output(result),
                        }
                    ],
                    "instruction": instruction,
                },
                ensure_ascii=False,
            ),
        }
    )
    append_trace(
        run,
        step_type="tool_guidance",
        name=trace_name,
        status="completed",
        output_payload={
            "tool": observation.get("tool"),
            "call_id": observation.get("call_id"),
            "result": _summarize_tool_output(result),
        },
    )
    run.setdefault("state", {})["pending_tool_outputs"] = updated_pending
    return updated_pending


def _pending_contains_backend_guidance(pending_tool_outputs: list[dict[str, Any]]) -> bool:
    return _latest_backend_runtime_guidance_from_pending(pending_tool_outputs) is not None


def _latest_backend_runtime_guidance_from_pending(
    pending_tool_outputs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for item in reversed(pending_tool_outputs or []):
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, str) or "backend_runtime_guidance" not in content:
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            continue
        guidance = parsed.get("backend_runtime_guidance")
        if isinstance(guidance, dict):
            return guidance
    return None


def _without_backend_runtime_guidance_items(
    pending_tool_outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        item
        for item in pending_tool_outputs or []
        if not _pending_item_is_backend_runtime_guidance(item)
    ]


def _pending_item_is_backend_runtime_guidance(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    content = item.get("content")
    if not isinstance(content, str) or "backend_runtime_guidance" not in content:
        return False
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed.get("backend_runtime_guidance"), dict)


def _guidance_allows_backend_recovery(guidance: dict[str, Any]) -> bool:
    return str(guidance.get("reason") or "") in {
        "candidate_fields_must_be_enriched_with_tools_before_asking_user",
        "candidate_enrichment_required_before_scoring",
        "candidate_enrichment_required_before_report",
        "candidate_not_close_enough_for_similar_fit_report",
        "continue_search_for_closer_measurements",
        "continue_search_for_closer_measurements_before_report",
        "search_recovery_required",
        "search_query_expansion_required",
        "baseline_evidence_recovery_required_before_report",
        "baseline_enrichment_required_before_scoring",
        "score_required_after_baseline_enrichment",
        "user_supplied_evidence_requires_normalize",
        "user_supplied_baseline_evidence_requires_memory_reload",
        "user_supplied_candidate_url_requires_registration",
        "context_sources_require_backend_tool_followup",
        "baseline_identity_required_before_candidate_search",
    }


def _retryable_failed_run_can_resume(run: dict[str, Any]) -> bool:
    error = run.get("error") if isinstance(run.get("error"), dict) else {}
    if not error:
        return False
    pending = run.get("state", {}).get("pending_tool_outputs") or []
    if error.get("retryable") and error.get("resume_available") and pending:
        return True
    if _is_provider_missing_tool_output_error(error):
        return bool(_unacknowledged_provider_tool_call_ids(run, pending))
    return False


def _is_provider_missing_tool_output_error(error: dict[str, Any]) -> bool:
    if not isinstance(error, dict):
        return False
    message = str(error.get("message") or error.get("provider_error_message_preview") or "")
    return (
        str(error.get("code") or "") == "LLM_RESPONSE_FAILED"
        and "No tool output found for function call" in message
    )


def _guidance_should_run_immediate_backend_recovery(guidance: dict[str, Any]) -> bool:
    return str(guidance.get("reason") or "") in {
        "candidate_enrichment_required_before_report",
        "candidate_not_close_enough_for_similar_fit_report",
        "continue_search_for_closer_measurements",
        "continue_search_for_closer_measurements_before_report",
        "search_recovery_required",
        "search_query_expansion_required",
        "baseline_evidence_recovery_required_before_report",
        "score_required_after_baseline_enrichment",
        "context_sources_require_backend_tool_followup",
        "baseline_identity_required_before_candidate_search",
    }


def _close_fit_recovery_budget_allows_repeated_guidance(guidance: dict[str, Any]) -> bool:
    if str(guidance.get("reason") or "") not in {
        "candidate_not_close_enough_for_similar_fit_report",
        "continue_search_for_closer_measurements",
        "continue_search_for_closer_measurements_before_report",
    }:
        return False
    budget = guidance.get("close_fit_search_budget")
    if not isinstance(budget, dict):
        return False
    try:
        return int(budget.get("remaining_attempts") or 0) > 0
    except (TypeError, ValueError):
        return False


def _backend_recovery_should_rescore_after_enrichment(
    guidance: dict[str, Any],
    observations: list[dict[str, Any]],
) -> bool:
    if str(guidance.get("reason") or "") not in {
        "candidate_enrichment_required_before_scoring",
        "candidate_enrichment_required_before_report",
        "candidate_not_close_enough_for_similar_fit_report",
        "baseline_evidence_recovery_required_before_report",
        "continue_search_for_closer_measurements",
        "continue_search_for_closer_measurements_before_report",
    }:
        return False
    if not observations:
        return False
    if any(item.get("tool") == "score_candidate_match" for item in observations):
        return False
    return all(
        str((item.get("result") or {}).get("status") or "") == "completed"
        for item in observations
    )


def _baseline_evidence_questions_from_recovery_guidance(guidance: dict[str, Any]) -> list[dict[str, Any]]:
    missing_fields = [str(field) for field in guidance.get("missing_fields") or [] if str(field).strip()]
    if not missing_fields:
        missing_fields = [
            "material",
            "measurements.length",
            "measurements.shoulder",
            "measurements.chest",
            "measurements.sleeve",
        ]
    return [
        {
            "field": "baseline_evidence",
            "closet_item_id": str(guidance.get("closet_item_id") or "current_liked_item"),
            "missing_fields": ", ".join(sorted(set(missing_fields))),
            "question": (
                "기준 아이템의 실측, 소재 근거가 부족합니다. 원래 상품 URL, 정확한 구매 이력 상품명, "
                "라벨/사이즈표 이미지, 또는 실측/소재 텍스트를 보내주면 다시 비교하겠습니다."
            ),
        }
    ]


def _backend_recovery_suggestions(guidance: dict[str, Any]) -> list[dict[str, Any]]:
    suggestions = _dedupe_tool_suggestions(guidance.get("next_tool_suggestions") or [])
    allowed_tools = {
        "register_user_candidate_url",
        "search_web_candidates",
        "fetch_product_page",
        "classify_product_assets",
        "extract_size_material_info",
        "normalize_product_spec",
        "score_candidate_match",
        "load_closet_memory",
        "enrich_closet_item_from_web",
    }
    filtered = [
        suggestion
        for suggestion in suggestions
        if str(suggestion.get("tool") or "") in allowed_tools
    ]
    filtered = _expand_candidate_enrichment_recovery_suggestions(guidance, filtered)
    return filtered[: _backend_recovery_tool_limit(guidance)]


def _backend_recovery_tool_limit(guidance: dict[str, Any]) -> int:
    reason = str(guidance.get("reason") or "")
    if reason.startswith("user_supplied_") or reason.startswith("baseline_"):
        return 3
    if reason in {"candidate_enrichment_required_before_scoring", "candidate_enrichment_required_before_report"}:
        return 12
    return 8


def _expand_candidate_enrichment_recovery_suggestions(
    guidance: dict[str, Any],
    suggestions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reason = str(guidance.get("reason") or "")
    if reason not in {"candidate_enrichment_required_before_scoring", "candidate_enrichment_required_before_report"}:
        return suggestions
    expanded: list[dict[str, Any]] = []
    for suggestion in suggestions:
        expanded.append(suggestion)
        tool_name = str(suggestion.get("tool") or "")
        arguments = suggestion.get("arguments") if isinstance(suggestion.get("arguments"), dict) else {}
        candidate_key = str(arguments.get("candidate_key") or "")
        if not candidate_key or candidate_key == "all":
            continue
        followups_by_tool = {
            "fetch_product_page": ["classify_product_assets", "extract_size_material_info", "normalize_product_spec"],
            "classify_product_assets": ["extract_size_material_info", "normalize_product_spec"],
            "extract_size_material_info": ["normalize_product_spec"],
        }
        for followup_tool in followups_by_tool.get(tool_name, []):
            expanded.append(
                {
                    "tool": followup_tool,
                    "arguments": {"candidate_key": candidate_key},
                    "reason": f"complete_candidate_enrichment_after_{tool_name}",
                }
            )
    return _dedupe_tool_suggestions(expanded)


def _dedupe_tool_suggestions(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = []
    seen = set()
    for suggestion in suggestions:
        signature = json.dumps(
            {
                "tool": suggestion.get("tool"),
                "arguments": suggestion.get("arguments", {}),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(suggestion)
    return deduped


def _observation_metadata(result: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"result_status": result.get("status")}
    key_map = {
        "provider": "provider",
        "provider_mode": "provider_mode",
        "ocr_provider": "provider",
        "ocr_provider_mode": "provider_mode",
        "external_api_called": "external_api_called",
        "timeout_seconds": "provider_timeout_seconds",
        "retry_count": "provider_retry_count",
        "retry_errors": "provider_retry_errors",
        "provider_error_code": "provider_error_code",
        "provider_error_category": "provider_error_category",
        "provider_error_message_preview": "provider_error_message_preview",
        "provider_retry_error_codes": "provider_retry_error_codes",
        "provider_latest_retry_error_code": "provider_latest_retry_error_code",
        "provider_latest_retry_error_category": "provider_latest_retry_error_category",
        "evidence_status": "evidence_status",
        "score_deferred": "score_deferred",
        "grounded": "grounded",
    }
    for source_key, target_key in key_map.items():
        if source_key in result and target_key not in metadata:
            metadata[target_key] = result[source_key]
    metadata.update(
        {
            key: value
            for key, value in provider_error_metadata_from_result(result).items()
            if key not in metadata
        }
    )
    retry_badges = _retry_badges_from_result(result)
    if retry_badges:
        metadata["retry_badges"] = retry_badges
    return metadata


def _tool_definition_metadata(tool) -> dict[str, Any]:
    return {
        "tool_timeout_seconds": getattr(tool, "timeout_seconds", None),
        "tool_risk": getattr(tool, "risk", None),
    }


def _maybe_tool_definition(tool_registry, tool_name: str):
    try:
        return tool_registry.get(tool_name)
    except Exception:
        return None


def _completed_at() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _store_completed_report_followups(run: dict[str, Any], report_result: dict[str, Any]) -> None:
    state = run.setdefault("state", {})
    followups = report_result.get("next_questions") or report_result.get("questions") or []
    if followups:
        state["optional_follow_up_questions"] = followups
    else:
        state.pop("optional_follow_up_questions", None)
    state["missing_info"] = []


def _report_metadata_from_tool_result(report_result: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ["candidate_audit", "excluded_candidates", "evidence_coverage"]:
        value = report_result.get(key)
        if value not in (None, "", [], {}):
            metadata[key] = value
    return metadata


def _repeated_failed_tool_call(run: dict[str, Any], latest_observation: dict[str, Any]) -> dict[str, Any] | None:
    latest_signature = _failed_tool_signature(latest_observation)
    if not latest_signature:
        return None
    repeat_count = 0
    for observation in reversed(run.get("state", {}).get("observations", [])):
        signature = _failed_tool_signature(observation)
        if signature != latest_signature:
            break
        repeat_count += 1
    if repeat_count < 2:
        return None
    return {
        "tool": latest_observation.get("tool"),
        "arguments": latest_observation.get("arguments", {}),
        "error_code": latest_signature["error_code"],
        "repeat_count": repeat_count,
    }


def _repeated_deferred_user_question(run: dict[str, Any], latest_observation: dict[str, Any]) -> dict[str, Any] | None:
    latest_signature = _deferred_question_signature(latest_observation)
    if not latest_signature:
        return None
    repeat_count = 0
    for observation in reversed(run.get("state", {}).get("observations", [])):
        signature = _deferred_question_signature(observation)
        if signature != latest_signature:
            break
        repeat_count += 1
    if repeat_count < 2:
        return None
    return {
        "tool": latest_observation.get("tool"),
        "arguments": latest_observation.get("arguments", {}),
        "reason": latest_signature["reason"],
        "repeat_count": repeat_count,
    }


def _repeated_empty_search(run: dict[str, Any], latest_observation: dict[str, Any]) -> dict[str, Any] | None:
    latest_signature = _empty_search_signature(latest_observation)
    if not latest_signature:
        return None
    repeat_count = 0
    for observation in reversed(run.get("state", {}).get("observations", [])):
        signature = _empty_search_signature(observation)
        if signature != latest_signature:
            break
        repeat_count += 1
    if repeat_count < 2:
        return None
    return {
        "tool": latest_observation.get("tool"),
        "arguments": latest_observation.get("arguments", {}),
        "query": latest_signature["query"],
        "repeat_count": repeat_count,
    }


def _empty_search_attempt_limit_exceeded(
    run: dict[str, Any],
    latest_observation: dict[str, Any],
) -> dict[str, Any] | None:
    if not _empty_search_signature(latest_observation):
        return None
    attempts: list[dict[str, Any]] = []
    for observation in reversed(run.get("state", {}).get("observations", [])):
        signature = _empty_search_signature(observation)
        if not signature:
            break
        attempts.append(signature)
    attempts.reverse()
    if len(attempts) < EMPTY_SEARCH_ATTEMPT_LIMIT:
        return None
    return {
        "tool": latest_observation.get("tool"),
        "attempt_count": len(attempts),
        "attempt_limit": EMPTY_SEARCH_ATTEMPT_LIMIT,
        "queries": [item["query"] for item in attempts[-EMPTY_SEARCH_ATTEMPT_LIMIT:]],
    }


def _empty_search_signature(observation: dict[str, Any]) -> dict[str, Any] | None:
    result = observation.get("result") or {}
    if observation.get("tool") != "search_web_candidates":
        return None
    if not result.get("search_deferred"):
        return None
    if int(result.get("candidate_count") or 0) != 0:
        return None
    return {
        "tool": observation.get("tool"),
        "query": str((observation.get("arguments") or {}).get("query") or result.get("query") or ""),
    }


def _deferred_question_signature(observation: dict[str, Any]) -> dict[str, Any] | None:
    result = observation.get("result") or {}
    if observation.get("tool") != "ask_missing_info" or not result.get("deferred_user_question"):
        return None
    try:
        arguments_key = json.dumps(observation.get("arguments", {}), ensure_ascii=False, sort_keys=True)
    except TypeError:
        arguments_key = repr(observation.get("arguments", {}))
    return {
        "tool": observation.get("tool"),
        "arguments_key": arguments_key,
        "reason": str(result.get("reason") or ""),
    }


def _failed_tool_signature(observation: dict[str, Any]) -> dict[str, Any] | None:
    result = observation.get("result") or {}
    if result.get("status") != "failed":
        return None
    error_code = _result_error_code(result)
    try:
        arguments_key = json.dumps(observation.get("arguments", {}), ensure_ascii=False, sort_keys=True)
    except TypeError:
        arguments_key = repr(observation.get("arguments", {}))
    return {
        "tool": observation.get("tool"),
        "arguments_key": arguments_key,
        "error_code": error_code,
    }


def _result_error_code(result: dict[str, Any]) -> str:
    error = result.get("error")
    if isinstance(error, dict) and error.get("code"):
        return str(error["code"])
    if isinstance(error, str) and error:
        return error[:80]
    if result.get("reason"):
        return str(result["reason"])
    return "unknown_failed_tool_result"


def _tool_argument_json_parse_failed(tool_name: str, call: dict[str, Any]) -> dict[str, Any]:
    parse_error = call.get("arguments_parse_error") or {}
    return {
        "status": "failed",
        "error": {
            "code": "TOOL_ARGUMENT_JSON_PARSE_FAILED",
            "message": f"{tool_name} arguments were not valid JSON.",
            "details": parse_error,
        },
        "validation_errors": [
            {
                "path": "$",
                "code": "invalid_json",
                "message": parse_error.get("message") or "Invalid JSON arguments.",
                "line": parse_error.get("line"),
                "column": parse_error.get("column"),
                "position": parse_error.get("position"),
            }
        ],
        "raw_arguments_excerpt": parse_error.get("raw_arguments_excerpt"),
    }


def _tool_timeout_failed(tool_name: str, timeout_seconds: float) -> dict[str, Any]:
    return {
        "status": "failed",
        "error": {
            "code": "TOOL_TIMEOUT_EXCEEDED",
            "message": f"Backend tool {tool_name} exceeded timeout_seconds={timeout_seconds}.",
            "tool": tool_name,
            "timeout_seconds": timeout_seconds,
        },
        "provider_error_code": "TOOL_TIMEOUT_EXCEEDED",
        "provider_error_category": "timeout",
        "provider_error_message_preview": f"Backend tool exceeded {timeout_seconds}s.",
        "timeout_seconds": timeout_seconds,
    }
