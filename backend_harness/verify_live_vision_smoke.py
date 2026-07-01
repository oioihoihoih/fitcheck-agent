from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any


BASE_URL = os.getenv("FITCHECK_API_BASE_URL", "http://127.0.0.1:8023").rstrip("/")
SESSION_ID = os.getenv("FITCHECK_VISION_SMOKE_SESSION_ID", "fitcheck_live_vision_smoke")


def main() -> None:
    if os.getenv("FITCHECK_RUN_LIVE_VISION_SMOKE") != "1":
        print("Skipped live vision smoke. Set FITCHECK_RUN_LIVE_VISION_SMOKE=1 to run.")
        return

    image_path = _find_smoke_image()
    attachment = _upload_image(image_path)
    run = _create_agent_run(attachment)
    _assert_live_vision_run(run)
    follow_up = _reply_with_refresh(run["run_id"])
    _assert_follow_up_rescored_after_search(follow_up)
    _assert_no_visual_baseline_identity_confirmation(follow_up)
    report = _get_json(f"/api/agent/runs/{run['run_id']}/report")
    summary = ((report.get("final_report") or {}).get("summary") or "")
    if _contains_visible_mojibake(summary):
        raise SystemExit(f"live vision report contains mojibake: {summary}")
    print(
        json.dumps(
            {
                "status": "completed",
                "run_id": run.get("run_id"),
                "follow_up_status": follow_up.get("status"),
                "follow_up_trace_count": len(follow_up.get("trace") or []),
                "trace_count": len(run.get("trace") or []),
                "report_status": report.get("status"),
                "report_preview": summary[:400],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _find_smoke_image() -> Path:
    explicit = os.getenv("FITCHECK_LIVE_VISION_IMAGE")
    if explicit:
        path = Path(explicit)
        if path.exists():
            return path
        raise SystemExit(f"FITCHECK_LIVE_VISION_IMAGE does not exist: {path}")
    for path in Path(".").glob("*/*.jpg"):
        if "\\ud604" in ascii(str(path)):
            return path
    fallback = Path("webapp/assets/closet-item.jpg")
    if fallback.exists():
        return fallback
    raise SystemExit("No live vision smoke image found.")


def _upload_image(path: Path) -> dict[str, Any]:
    payload = {
        "filename": "live-vision-smoke.jpg",
        "media_type": "image/jpeg",
        "base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        "role": "current_clothing_photo",
    }
    response = _post_json("/api/uploads/images", payload)
    if response.get("status") != "completed":
        raise SystemExit(f"image upload failed: {response}")
    return response["attachment"]


def _create_agent_run(attachment: dict[str, Any]) -> dict[str, Any]:
    goal = (
        "\uc774 \uc637\uacfc \ube44\uc2b7\ud55c \ubc18\ud314 \ud2f0\uc154\uce20\ub97c \ucc3e\uc544\uc918. "
        "\ud54f\uc740 \uc5ec\uc720\ub86d\uace0 \uc7ac\uc9c8\uacfc \uc624\ub798 \uc785\ub294 \uc810\uc774 \ub9c8\uc74c\uc5d0 \ub4e4\uc5b4."
    )
    return _post_json(
        "/api/agent/runs",
        {
            "goal": goal,
            "free_text": goal,
            "attachments": [attachment],
        },
        timeout=180,
    )


def _assert_live_vision_run(run: dict[str, Any]) -> None:
    if run.get("status") not in {"completed", "waiting_for_user"}:
        raise SystemExit(f"unexpected live vision run status: {run.get('status')} {run.get('error')}")
    observations = (run.get("state") or {}).get("observations") or []
    inspect = [item.get("result") or {} for item in observations if item.get("tool") == "inspect_uploaded_images"]
    if not inspect:
        raise SystemExit("inspect_uploaded_images observation missing")
    latest_inspect = inspect[-1]
    if latest_inspect.get("vision_provider") != "openai_responses_vision":
        raise SystemExit(f"vision provider was not OpenAI Responses: {latest_inspect}")
    if latest_inspect.get("vision_external_api_called") is not True:
        raise SystemExit(f"vision external API call was not recorded: {latest_inspect}")
    visual = latest_inspect.get("visual_liked_item") or {}
    if visual.get("category") != "t_shirt":
        raise SystemExit(f"vision did not identify t-shirt baseline: {visual}")

    searches = [item.get("result") or {} for item in observations if item.get("tool") == "search_web_candidates"]
    if not searches:
        raise SystemExit("search_web_candidates observation missing after vision")
    first_query = str(searches[0].get("query") or "")
    category_terms = ["t-shirt", "tee", "\ud2f0\uc154\uce20", "\ubc18\ud314"]
    if not any(term in first_query.lower() for term in category_terms):
        raise SystemExit(f"vision-derived search query did not preserve garment category: {first_query}")
    if "?" in first_query and first_query.count("?") > 5:
        raise SystemExit(f"search query appears to be stale/mojibake text: {first_query}")

    trace_names = [step.get("name") for step in run.get("trace") or [] if step.get("step_type") == "tool_call"]
    for expected in ["inspect_uploaded_images", "search_web_candidates", "score_candidate_match"]:
        if expected not in trace_names:
            raise SystemExit(f"expected model-selected tool call missing: {expected}; trace={trace_names}")


def _reply_with_refresh(run_id: str) -> dict[str, Any]:
    return _post_json(
        f"/api/agent/runs/{run_id}/reply",
        {
            "message": (
                "\uc6f9\uc11c\uce58\ub85c \ub2e4\uc2dc \ucc3e\uc544\ubd10. "
                "\uad6d\ub0b4 \uc1fc\ud551\ubab0 \uc704\uc8fc\ub85c \ub354 \ucc3e\uc544\uc918."
            ),
            "answers": [],
            "attachments": [],
        },
        timeout=180,
    )


def _assert_follow_up_rescored_after_search(run: dict[str, Any]) -> None:
    trace = run.get("trace") or []
    latest_reply = _latest_step_index(trace, step_type="user_reply")
    if latest_reply is None:
        raise SystemExit("follow-up reply trace step missing")
    latest_search = _latest_step_index(trace, step_type="tool_output", name="search_web_candidates", after=latest_reply)
    if latest_search is None:
        raise SystemExit("follow-up search_web_candidates output missing")
    latest_score = _latest_step_index(trace, step_type="tool_output", name="score_candidate_match", after=latest_search)
    if latest_score is None:
        raise SystemExit("follow-up search was not scored before completion")
    latest_report = _latest_step_index(trace, step_type="tool_output", name="write_purchase_report", after=latest_search)
    if latest_report is not None and latest_report < latest_score:
        raise SystemExit("follow-up report was written before scoring latest search candidates")


def _assert_no_visual_baseline_identity_confirmation(run: dict[str, Any]) -> None:
    state = run.get("state") or {}
    missing_info = state.get("missing_info") or []
    for item in missing_info:
        if isinstance(item, dict) and item.get("field") == "baseline_identity_confirmation":
            raise SystemExit(f"visual-only baseline should not ask for exact product confirmation: {item}")
    identity = state.get("baseline_identity") or {}
    if identity.get("status") == "needs_identity_confirmation":
        raise SystemExit(f"visual-only baseline identity still requires confirmation: {identity}")


def _latest_step_index(
    trace: list[dict[str, Any]],
    *,
    step_type: str,
    name: str | None = None,
    after: int | None = None,
) -> int | None:
    for step in reversed(trace):
        if step.get("step_type") != step_type:
            continue
        if name is not None and step.get("name") != name:
            continue
        try:
            index = int(step.get("step_index"))
        except (TypeError, ValueError):
            continue
        if after is not None and index <= after:
            continue
        return index
    return None


def _post_json(path: str, payload: dict[str, Any], *, timeout: int = 60) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-FitCheck-Session-Id": SESSION_ID,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(path: str, *, timeout: int = 60) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"X-FitCheck-Session-Id": SESSION_ID},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _contains_visible_mojibake(text: str) -> bool:
    markers = ["\u00c3", "\u00c2", "\ufffd", "\ud6a1", "\ud6c2", "\u5360", "\uf9cd", "\u6e72", "\u5bc3"]
    return any(marker in text for marker in markers)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        sys.exit(1)
