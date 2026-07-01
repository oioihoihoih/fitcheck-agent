from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {"hook_event_name": "unknown", "raw_input_unavailable": True}

    root = Path.cwd()
    output = root / "harness_outputs" / "agentic_harness_evidence_hints.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "hook_event_name": payload.get("hook_event_name") or payload.get("event") or "unknown",
        "evidence_contract": {
            "provider_claims": "Require /llm/probe diagnostics before claiming a provider is connected.",
            "tool_loop_claims": "Record tool-calls, observations, artifacts, and task evidence.",
            "eval_claims": "Promote repeatable trace/runtime gaps through /eval-cases.",
            "worker_claims": "Use lease owner, epoch, expiry, and heartbeat for long-running workers.",
            "boundary": "Mock, cached, fixture, and deterministic fallback results are harness evidence only.",
        },
    }
    with output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
