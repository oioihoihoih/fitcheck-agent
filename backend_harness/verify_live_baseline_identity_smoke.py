from __future__ import annotations

import json
import os
import sys
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener


DEFAULT_INITIAL_GOAL = "자주 입는 차콜 반팔티랑 비슷한 옷을 찾아줘"
DEFAULT_INITIAL_FREE_TEXT = (
    "자주 입는 차콜 반팔티랑 비슷한 옷을 찾아줘. "
    "핏은 여유롭고 오래 입어도 괜찮았어. 색은 꼭 같지 않아도 돼."
)
DEFAULT_IDENTITY_REPLY = "가까이 유니언즈의 사이즈 L 인데, 웹서치로 찾아봐"
DEFAULT_CONFIRMATION_REPLY = "첫 번째 후보가 내가 말한 가까이 유니언즈 L 반팔티가 맞아."
DEFAULT_REUSE_GOAL = "저장한 반팔티랑 비슷한 옷을 찾아줘"
DEFAULT_REUSE_FREE_TEXT = "저장한 반팔티랑 비슷한 옷을 다시 찾아줘. 색은 꼭 같지 않아도 돼."


def main() -> None:
    base_url = os.getenv("FITCHECK_AGENT_BASE_URL", "http://127.0.0.1:8000").rstrip("/") + "/"
    if not _truthy(os.getenv("FITCHECK_RUN_LIVE_BASELINE_IDENTITY_SMOKE")):
        _print(
            {
                "status": "skipped",
                "reason": "Set FITCHECK_RUN_LIVE_BASELINE_IDENTITY_SMOKE=1 to verify the live baseline identity flow.",
                "base_url": base_url,
                "checks": [
                    "api_status_llm_connected",
                    "initial_run_created",
                    "identity_reply_persisted",
                    "model_selected_tool_after_reply",
                    "baseline_identity_candidate_or_confirmed_memory",
                    "confirmation_reply_backend_tool_completion",
                    "confirmation_provider_context_reset",
                    "no_llm_response_400",
                    "optional_confirmed_memory_reuse",
                ],
            }
        )
        return

    timeout = float(os.getenv("FITCHECK_LIVE_BASELINE_IDENTITY_TIMEOUT", "300"))
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    headers = {
        "X-FitCheck-Session-Id": os.getenv(
            "FITCHECK_LIVE_BASELINE_IDENTITY_SESSION_ID",
            "live_baseline_identity_smoke",
        )
    }

    status = _request_json(opener, "GET", urljoin(base_url, "api/status"), timeout=timeout, headers=headers)
    if status.get("llm", {}).get("status") != "connected":
        _fail({"reason": "llm_not_connected", "llm": _redact_llm(status.get("llm") or {})})

    initial = _request_json(
        opener,
        "POST",
        urljoin(base_url, "api/agent/runs"),
        payload={
            "goal": os.getenv("FITCHECK_LIVE_BASELINE_INITIAL_GOAL", DEFAULT_INITIAL_GOAL),
            "free_text": os.getenv("FITCHECK_LIVE_BASELINE_INITIAL_FREE_TEXT", DEFAULT_INITIAL_FREE_TEXT),
            "refresh_web": _truthy(os.getenv("FITCHECK_LIVE_BASELINE_REFRESH_WEB")),
        },
        timeout=timeout,
        headers=headers,
    )
    if not initial.get("run_id"):
        _fail({"reason": "initial_run_not_created", "initial": _summarize_run(initial)})

    identity_reply = os.getenv("FITCHECK_LIVE_BASELINE_IDENTITY_REPLY", DEFAULT_IDENTITY_REPLY)
    replied = _post_reply(opener, base_url, initial["run_id"], identity_reply, timeout, headers)
    fetched = _request_json(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{initial['run_id']}"),
        timeout=timeout,
        headers=headers,
    )

    failures = _baseline_identity_failures(fetched, identity_reply)
    confirmed = None
    reuse = None
    confirmation_skipped_reason = None
    unconfirmed = (fetched.get("state") or {}).get("unconfirmed_baseline_identity_candidates") or []
    if not failures and _truthy(os.getenv("FITCHECK_LIVE_BASELINE_CONFIRM_IDENTITY")) and unconfirmed:
        confirmation_reply = os.getenv("FITCHECK_LIVE_BASELINE_CONFIRMATION_REPLY", DEFAULT_CONFIRMATION_REPLY)
        confirmed = _post_reply(opener, base_url, initial["run_id"], confirmation_reply, timeout, headers)
        fetched = _request_json(
            opener,
            "GET",
            urljoin(base_url, f"api/agent/runs/{initial['run_id']}"),
            timeout=timeout,
            headers=headers,
        )
        failures.extend(_baseline_confirmation_failures(fetched, confirmation_reply))
        if not failures and _truthy(os.getenv("FITCHECK_LIVE_BASELINE_VERIFY_REUSE")):
            reuse = _request_json(
                opener,
                "POST",
                urljoin(base_url, "api/agent/runs"),
                payload={
                    "goal": os.getenv("FITCHECK_LIVE_BASELINE_REUSE_GOAL", DEFAULT_REUSE_GOAL),
                    "free_text": os.getenv("FITCHECK_LIVE_BASELINE_REUSE_FREE_TEXT", DEFAULT_REUSE_FREE_TEXT),
                    "refresh_web": _truthy(os.getenv("FITCHECK_LIVE_BASELINE_REUSE_REFRESH_WEB")),
                },
                timeout=timeout,
                headers=headers,
            )
            failures.extend(_durable_memory_reuse_failures(reuse))
    elif not failures and _truthy(os.getenv("FITCHECK_LIVE_BASELINE_CONFIRM_IDENTITY")):
        if _truthy(os.getenv("FITCHECK_LIVE_BASELINE_REQUIRE_CONFIRMABLE")):
            failures.append("baseline_identity_confirmable_candidate_missing")
        else:
            confirmation_skipped_reason = "no_confirmable_baseline_candidate_after_lookup"

    result = {
        "status": "failed" if failures else "completed",
        "base_url": base_url,
        "initial_run": _summarize_run(initial),
        "identity_reply_run": _summarize_run(replied),
        "confirmation_reply_run": _summarize_run(confirmed) if confirmed else None,
        "confirmation_skipped_reason": confirmation_skipped_reason,
        "reuse_run": _summarize_run(reuse) if reuse else None,
        "fetched_run": _summarize_run(fetched),
        "baseline_identity": (fetched.get("state") or {}).get("baseline_identity"),
        "closet_memory_source": ((fetched.get("state") or {}).get("closet_item") or {}).get("memory_source"),
        "unconfirmed_candidate_count": len((fetched.get("state") or {}).get("unconfirmed_baseline_identity_candidates") or []),
        "tool_calls_after_latest_reply": _tool_calls_after_latest_reply(fetched),
        "llm_responses_after_latest_reply": _llm_responses_after_latest_reply(fetched),
        "provider_response_context_reset": ((fetched.get("state") or {}).get("provider_response_context_reset") or {}),
        "reuse_summary": _durable_memory_reuse_summary(reuse) if reuse else None,
        "failures": failures,
    }
    _print(result)
    if failures:
        raise SystemExit(1)


def _post_reply(opener, base_url: str, run_id: str, message: str, timeout: float, headers: dict[str, str]) -> dict[str, Any]:
    return _request_json(
        opener,
        "POST",
        urljoin(base_url, f"api/agent/runs/{run_id}/reply"),
        payload={
            "message": message,
            "answers": [{"field": "user_reply", "value": message}],
            "attachments": [],
        },
        timeout=timeout,
        headers=headers,
    )


def _baseline_identity_failures(run: dict[str, Any], reply_text: str) -> list[str]:
    failures: list[str] = []
    if run.get("status") == "failed":
        error = run.get("error") or {}
        failures.append(f"run_failed_after_identity_reply:{error.get('code') or 'unknown'}")
    if run.get("status") == "failed" and (run.get("error") or {}).get("code") == "LLM_RESPONSE_FAILED":
        message = str((run.get("error") or {}).get("message") or "")
        if "400" in message or "Bad Request" in message:
            failures.append("llm_response_400_after_identity_reply")
    if (run.get("llm") or {}).get("external_api_called") is not True:
        failures.append("external_api_called_not_recorded")
    if reply_text not in json.dumps((run.get("input") or {}).get("user_replies") or [], ensure_ascii=False):
        failures.append("identity_reply_not_persisted")
    after_reply = _tool_calls_after_latest_reply(run)
    if not after_reply:
        failures.append("model_selected_tool_after_reply_missing")
    if after_reply and "enrich_closet_item_from_web" not in after_reply:
        failures.append(f"baseline_enrichment_tool_after_reply_missing:{','.join(after_reply)}")
    state = run.get("state") or {}
    baseline_identity = state.get("baseline_identity") or ((state.get("closet_item") or {}).get("baseline_identity") or {})
    unconfirmed = state.get("unconfirmed_baseline_identity_candidates") or []
    description_only_statuses = {
        "description_only_baseline",
        "visual_only_description_baseline",
        "insufficient_identity_constraints",
        "no_baseline_candidate",
        "no_confirmable_baseline_candidate",
        "rejected_by_user",
    }
    if (
        not unconfirmed
        and baseline_identity.get("status")
        not in {"needs_identity_confirmation", "memory_identity_available", *description_only_statuses}
    ):
        failures.append(f"baseline_identity_candidate_missing:{baseline_identity.get('status')}")
    if baseline_identity.get("status") == "needs_identity_confirmation" and not unconfirmed:
        failures.append("baseline_identity_needs_confirmation_without_candidates")
    return failures


def _baseline_confirmation_failures(run: dict[str, Any], reply_text: str) -> list[str]:
    failures: list[str] = []
    if run.get("status") == "failed":
        failures.append(f"run_failed_after_confirmation_reply:{(run.get('error') or {}).get('code') or 'unknown'}")
    if run.get("status") != "completed":
        failures.append(f"confirmation_reply_not_completed:{run.get('status')}")
    if reply_text not in json.dumps((run.get("input") or {}).get("user_replies") or [], ensure_ascii=False):
        failures.append("confirmation_reply_not_persisted")
    after_reply = _tool_calls_after_latest_reply(run)
    if "confirm_baseline_identity" not in after_reply:
        failures.append(f"confirm_baseline_identity_tool_missing:{','.join(after_reply)}")
    llm_after_reply = _llm_responses_after_latest_reply(run)
    if llm_after_reply:
        failures.append(f"llm_response_after_confirmation_reply:{len(llm_after_reply)}")
    confirmation_observations = _tool_observations(run, "confirm_baseline_identity")
    if not confirmation_observations:
        failures.append("confirm_baseline_identity_observation_missing")
        confirmation_call_id = None
    else:
        confirmation_call_id = confirmation_observations[-1].get("call_id")
        if not confirmation_call_id:
            failures.append("confirm_baseline_identity_observation_call_id_missing")
    state = run.get("state") or {}
    baseline_identity = state.get("baseline_identity") or ((state.get("closet_item") or {}).get("baseline_identity") or {})
    if baseline_identity.get("status") != "memory_identity_available":
        failures.append(f"baseline_identity_not_confirmed:{baseline_identity.get('status')}")
    final_report = run.get("final_report") or {}
    if final_report.get("grounding") != "confirm_baseline_identity_tool":
        failures.append(f"confirmation_final_report_grounding:{final_report.get('grounding')}")
    reset = state.get("provider_response_context_reset") or {}
    if reset.get("reason") != "baseline_identity_confirmation_completed_without_model_ack":
        failures.append(f"confirmation_context_reset_reason:{reset.get('reason')}")
    if confirmation_call_id and reset.get("terminal_backend_call_id") != confirmation_call_id:
        failures.append(
            "confirmation_context_reset_call_id_mismatch:"
            f"{reset.get('terminal_backend_call_id')}!={confirmation_call_id}"
        )
    return failures


def _durable_memory_reuse_failures(run: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if run.get("status") == "failed":
        failures.append(f"reuse_run_failed:{(run.get('error') or {}).get('code') or 'unknown'}")
    load_results = _tool_results(run, "load_closet_memory")
    if not load_results:
        failures.append("reuse_load_closet_memory_missing")
        return failures
    first_load = load_results[0]
    if first_load.get("durable_memory_status") != "loaded":
        failures.append(f"reuse_durable_memory_not_loaded:{first_load.get('durable_memory_status')}")
    if not first_load.get("durable_memory_match"):
        failures.append("reuse_durable_memory_match_missing")
    closet = first_load.get("closet_item") or {}
    if closet.get("memory_source") != "durable_closet_memory":
        failures.append(f"reuse_closet_memory_source:{closet.get('memory_source')}")
    baseline_identity = closet.get("baseline_identity") or {}
    if baseline_identity.get("status") != "memory_identity_available":
        failures.append(f"reuse_baseline_identity_status:{baseline_identity.get('status')}")
    tool_calls = [step.get("name") for step in run.get("trace") or [] if step.get("step_type") == "tool_call"]
    if not tool_calls or tool_calls[0] != "load_closet_memory":
        failures.append(f"reuse_first_tool_not_load_closet_memory:{','.join(str(item) for item in tool_calls[:5])}")
    return failures


def _durable_memory_reuse_summary(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not run:
        return None
    load_results = _tool_results(run, "load_closet_memory")
    first_load = load_results[0] if load_results else {}
    match = first_load.get("durable_memory_match") or {}
    closet = first_load.get("closet_item") or {}
    return {
        "status": run.get("status"),
        "run_id": run.get("run_id"),
        "load_closet_memory_count": len(load_results),
        "first_load_durable_status": first_load.get("durable_memory_status"),
        "first_load_match_score": match.get("score"),
        "first_load_match_reasons": match.get("reasons", []),
        "first_load_memory_source": closet.get("memory_source"),
        "first_load_baseline_identity_status": (closet.get("baseline_identity") or {}).get("status"),
    }


def _tool_results(run: dict[str, Any], tool_name: str) -> list[dict[str, Any]]:
    return [
        observation.get("result") or {}
        for observation in (run.get("state") or {}).get("observations") or []
        if observation.get("tool") == tool_name
    ]


def _tool_observations(run: dict[str, Any], tool_name: str) -> list[dict[str, Any]]:
    return [
        observation
        for observation in (run.get("state") or {}).get("observations") or []
        if observation.get("tool") == tool_name
    ]


def _tool_calls_after_latest_reply(run: dict[str, Any]) -> list[str]:
    trace = run.get("trace") or []
    latest_reply_index = max(
        [int(step.get("step_index") or 0) for step in trace if step.get("step_type") == "user_reply"] or [0]
    )
    return [
        str(step.get("name"))
        for step in trace
        if step.get("step_type") == "tool_call" and int(step.get("step_index") or 0) > latest_reply_index
    ]


def _llm_responses_after_latest_reply(run: dict[str, Any]) -> list[str]:
    trace = run.get("trace") or []
    latest_reply_index = max(
        [int(step.get("step_index") or 0) for step in trace if step.get("step_type") == "user_reply"] or [0]
    )
    return [
        str(step.get("name"))
        for step in trace
        if step.get("step_type") == "llm_response" and int(step.get("step_index") or 0) > latest_reply_index
    ]


def _summarize_run(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not run:
        return None
    trace = run.get("trace") or []
    observations = (run.get("state") or {}).get("observations") or []
    return {
        "status": run.get("status"),
        "run_id": run.get("run_id"),
        "error_code": (run.get("error") or {}).get("code"),
        "llm_external_api_called": (run.get("llm") or {}).get("external_api_called"),
        "trace_count": len(trace),
        "observation_count": len(observations),
        "tool_calls": [step.get("name") for step in trace if step.get("step_type") == "tool_call"][-12:],
        "tool_outputs": [step.get("name") for step in trace if step.get("step_type") == "tool_output"][-12:],
    }


def _request_json(
    opener,
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {url} failed with HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise SystemExit(f"{method} {url} failed: {exc.reason}") from exc
    return json.loads(body)


def _redact_llm(llm: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": llm.get("provider"),
        "base_url": llm.get("base_url"),
        "model": llm.get("model"),
        "api_style": llm.get("api_style"),
        "status": llm.get("status"),
        "configured_source": llm.get("configured_source"),
    }


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _fail(payload: dict[str, Any]) -> None:
    _print({"status": "failed", **payload})
    raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise
    except SystemExit:
        raise
    except Exception as exc:
        _print({"status": "failed", "reason": type(exc).__name__, "message": str(exc)})
        raise SystemExit(1) from exc
