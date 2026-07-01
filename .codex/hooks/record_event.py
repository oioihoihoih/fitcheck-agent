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
    output = root / "harness_outputs" / "codex_hook_events.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "event": payload,
    }
    with output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()

