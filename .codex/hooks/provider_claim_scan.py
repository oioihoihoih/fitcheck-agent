from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys


CLAIM_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bconnected\b",
        r"\blive\s+llm\b",
        r"\bproduction\s+agent\b",
        r"\bprovider\s+connected\b",
        r"연결(?:됨|되었|됐다|됐)",
        r"실제\s*(?:LLM|에이전트|agent)",
        r"프로덕션\s*(?:AI|agent|에이전트)",
    ]
]

EVIDENCE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"/llm/probe",
        r"/models",
        r"diagnostic",
        r"probe status",
        r"configuration_required",
        r"not_connected",
        r"검증",
        r"진단",
    ]
]


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {"hook_event_name": "unknown", "raw_input_unavailable": True}

    text = json.dumps(payload, ensure_ascii=False)
    has_claim = any(pattern.search(text) for pattern in CLAIM_PATTERNS)
    has_evidence = any(pattern.search(text) for pattern in EVIDENCE_PATTERNS)
    if not has_claim:
        return

    root = Path.cwd()
    output = root / "harness_outputs" / "provider_claim_scan.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "hook_event_name": payload.get("hook_event_name") or payload.get("event") or "unknown",
        "claim_detected": True,
        "evidence_terms_detected": has_evidence,
        "required_evidence": [
            "POST /api/harness/v1/llm/probe result",
            "GET /api/harness/v1/llm/diagnostics persisted record",
            "tool-loop verifier when claiming tool-calling support",
        ],
        "warning": "Provider connectivity claims need probe-backed harness evidence.",
    }
    with output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
