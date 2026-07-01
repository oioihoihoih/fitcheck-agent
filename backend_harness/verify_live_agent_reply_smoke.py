from __future__ import annotations

import json
import os
import sys
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener


DEFAULT_INITIAL_GOAL = "평소 신는 화이트 레더 스니커즈와 비슷한 신발을 찾아줘"
DEFAULT_INITIAL_FREE_TEXT = (
    "내가 자주 신는 신발은 화이트 레더 스니커즈야. "
    "발볼이 너무 좁지 않고 청바지랑 잘 어울려서 좋아했어. "
    "비슷한 미니멀한 스니커즈를 웹검색으로 찾아 비교해줘."
)
DEFAULT_REPLY = "이번에는 검정색 스니커즈로 다시 찾아봐. 발볼은 너무 좁지 않은 걸로."


def main() -> None:
    base_url = os.getenv("FITCHECK_AGENT_BASE_URL", "http://127.0.0.1:8000").rstrip("/") + "/"
    if not _truthy(os.getenv("FITCHECK_RUN_LIVE_AGENT_REPLY_SMOKE")):
        _print(
            {
                "status": "skipped",
                "reason": "Set FITCHECK_RUN_LIVE_AGENT_REPLY_SMOKE=1 to verify the live reply endpoint.",
                "base_url": base_url,
                "checks": [
                    "initial_agent_run",
                    "reply_endpoint",
                    "user_reply_trace",
                    "reply_context_resolution",
                    "reply_search_tool_path",
                    "optional_reply_completion",
                ],
            }
        )
        return

    timeout = float(os.getenv("FITCHECK_LIVE_AGENT_REPLY_TIMEOUT", "240"))
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    headers = {"X-FitCheck-Session-Id": os.getenv("FITCHECK_LIVE_AGENT_REPLY_SESSION_ID", "live_agent_reply_smoke")}
    status = _request_json(opener, "GET", urljoin(base_url, "api/status"), timeout=timeout, headers=headers)
    if status.get("llm", {}).get("status") != "connected":
        _fail({"reason": "llm_not_connected", "base_url": base_url, "llm": status.get("llm")})

    allow_cached_search = _truthy(os.getenv("FITCHECK_LIVE_AGENT_REPLY_ALLOW_CACHED_SEARCH", "1"))
    initial_payload = {
        "goal": os.getenv("FITCHECK_LIVE_AGENT_REPLY_INITIAL_GOAL", DEFAULT_INITIAL_GOAL),
        "free_text": os.getenv("FITCHECK_LIVE_AGENT_REPLY_INITIAL_FREE_TEXT", DEFAULT_INITIAL_FREE_TEXT),
        "refresh_web": _truthy(os.getenv("FITCHECK_LIVE_AGENT_REPLY_REFRESH_WEB")),
    }
    if allow_cached_search:
        initial_payload["allow_cached_harness_search"] = True
    initial = _request_json(
        opener,
        "POST",
        urljoin(base_url, "api/agent/runs"),
        payload=initial_payload,
        timeout=timeout,
        headers=headers,
    )
    if not initial.get("run_id"):
        _fail({"reason": "initial_run_not_created", "initial": _summarize_run(initial)})
    if initial.get("status") == "running":
        _fail({"reason": "initial_run_still_running", "initial": _summarize_run(initial)})

    reply_text = os.getenv("FITCHECK_LIVE_AGENT_REPLY_TEXT", DEFAULT_REPLY)
    reply_payload = {
        "message": reply_text,
        "answers": [{"field": "search_instruction", "value": reply_text}],
        "attachments": [],
    }
    replied = _request_json(
        opener,
        "POST",
        urljoin(base_url, f"api/agent/runs/{initial['run_id']}/reply"),
        payload=reply_payload,
        timeout=timeout,
        headers=headers,
    )
    fetched = _request_json(
        opener,
        "GET",
        urljoin(base_url, f"api/agent/runs/{initial['run_id']}"),
        timeout=timeout,
        headers=headers,
    )

    context_failures = _reply_context_failures(fetched, reply_text)
    boundary_failures = _reply_boundary_failures(initial, replied, fetched)
    completion_failures = _reply_completion_failures(fetched)
    require_completion = _truthy(os.getenv("FITCHECK_LIVE_AGENT_REPLY_REQUIRE_COMPLETION"))
    failures = boundary_failures + context_failures
    if require_completion:
        failures.extend(completion_failures)

    result = {
        "status": "failed" if failures else "completed",
        "base_url": base_url,
        "allow_cached_harness_search": allow_cached_search,
        "require_completion": require_completion,
        "initial_run": _summarize_run(initial),
        "reply_run": _summarize_run(replied),
        "fetched_run": _summarize_run(fetched),
        "reply_context": _reply_context_summary(fetched),
        "boundary_failures": boundary_failures,
        "context_failures": context_failures,
        "completion_failures": completion_failures,
        "failures": failures,
    }
    _print(result)
    if failures:
        raise SystemExit(1)


def _reply_boundary_failures(initial: dict[str, Any], replied: dict[str, Any], fetched: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if not replied.get("run_id") or replied.get("run_id") != initial.get("run_id"):
        failures.append("reply_run_id_mismatch")
    if fetched.get("run_id") != initial.get("run_id"):
        failures.append("fetched_run_id_mismatch")
    if fetched.get("llm", {}).get("external_api_called") is not True:
        failures.append("external_api_called_not_recorded")
    if not fetched.get("input", {}).get("user_replies"):
        failures.append("user_reply_not_persisted")
    if not any(step.get("step_type") == "user_reply" for step in fetched.get("trace") or []):
        failures.append("user_reply_trace_missing")
    return failures


def _reply_context_failures(run: dict[str, Any], reply_text: str) -> list[str]:
    failures: list[str] = []
    trace = run.get("trace") or []
    latest_reply_index = max(
        [int(step.get("step_index") or 0) for step in trace if step.get("step_type") == "user_reply"] or [0]
    )
    if latest_reply_index <= 0:
        return ["user_reply_trace_missing"]
    plan_outputs = [
        step
        for step in trace
        if step.get("step_type") == "tool_output"
        and step.get("name") == "plan_context_and_sources"
        and int(step.get("step_index") or 0) > latest_reply_index
    ]
    if not plan_outputs:
        failures.append("reply_plan_context_tool_output_missing")
    search_calls = [
        step
        for step in trace
        if step.get("step_type") == "tool_call"
        and step.get("name") == "search_web_candidates"
        and int(step.get("step_index") or 0) > latest_reply_index
    ]
    if not search_calls:
        failures.append("reply_search_tool_call_missing")
    constraints = run.get("state", {}).get("search_constraints") or {}
    if constraints.get("category") != "shoes":
        failures.append(f"reply_category_not_shoes:{constraints.get('category')}")
    if constraints.get("color_query_label") != "블랙":
        failures.append(f"reply_color_not_black:{constraints.get('color_query_label')}")
    query_hint = str(constraints.get("query_hint") or "")
    if "블랙" not in query_hint or "신발" not in query_hint:
        failures.append(f"reply_query_hint_not_black_shoes:{query_hint}")
    if reply_text not in json.dumps(run.get("input", {}).get("user_replies") or [], ensure_ascii=False):
        failures.append("reply_text_not_persisted")
    return failures


def _reply_completion_failures(run: dict[str, Any]) -> list[str]:
    if run.get("status") != "completed":
        return [f"reply_not_completed:{run.get('status')}:{(run.get('error') or {}).get('code') or 'unknown'}"]
    final_report = run.get("final_report") or {}
    if final_report.get("grounded") is not True:
        return ["reply_final_report_not_grounded"]
    return [f"reply_final_report_text:{failure}" for failure in _user_visible_report_text_failures(str(final_report.get("summary") or ""))]


def _reply_context_summary(run: dict[str, Any]) -> dict[str, Any]:
    constraints = run.get("state", {}).get("search_constraints") or {}
    trace = run.get("trace") or []
    latest_reply_index = max(
        [int(step.get("step_index") or 0) for step in trace if step.get("step_type") == "user_reply"] or [0]
    )
    after_reply_tools = [
        step.get("name")
        for step in trace
        if step.get("step_type") == "tool_call" and int(step.get("step_index") or 0) > latest_reply_index
    ]
    return {
        "latest_reply_step_index": latest_reply_index,
        "search_constraints": constraints,
        "tool_calls_after_reply": after_reply_tools,
    }


def _summarize_run(run: dict[str, Any]) -> dict[str, Any]:
    trace = run.get("trace") or []
    observations = run.get("state", {}).get("observations") or []
    return {
        "status": run.get("status"),
        "run_id": run.get("run_id"),
        "error_code": (run.get("error") or {}).get("code"),
        "trace_count": len(trace),
        "observation_count": len(observations),
        "llm_external_api_called": run.get("llm", {}).get("external_api_called"),
        "final_report_grounded": (run.get("final_report") or {}).get("grounded"),
        "final_report_summary_preview": str((run.get("final_report") or {}).get("summary") or "")[:220],
    }


def _user_visible_report_text_failures(text: str) -> list[str]:
    if not text.strip():
        return ["missing_summary"]
    forbidden_fragments = [
        "a candidate",
        "is missing evidence",
        "No additional risk",
        "page fetch",
        "Material evidence",
        "Candidate price",
        "measurement evidence is missing",
        "differs from the liked baseline",
    ]
    failures = [f"english_fallback:{fragment}" for fragment in forbidden_fragments if fragment in text]
    mojibake_markers = ["�", "Ã", "Â", "횄", "횂", "占", "筌", "疫", "野", "鍮", "留", "湲", "寃"]
    failures.extend(f"mojibake:{marker}" for marker in mojibake_markers if marker in text)
    return failures


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
        request_headers["Content-Type"] = "application/json"
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
        sys.exit(130)
