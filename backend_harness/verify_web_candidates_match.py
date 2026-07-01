from __future__ import annotations

import json
from pathlib import Path

import run_web_candidates_match


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    result_path = root / "harness_outputs" / "web_candidates_match_result.json"
    run_web_candidates_match.main()
    result = json.loads(result_path.read_text(encoding="utf-8"))

    if result["summary_table"]:
        raise SystemExit(
            "Default web candidate harness should not emit fixed product candidates: "
            f"{json.dumps(result['summary_table'], ensure_ascii=False)}"
        )
    if result["generated_candidate_paths"]:
        raise SystemExit(
            "Default web candidate harness should not generate product fixtures without explicit sources: "
            f"{json.dumps(result['generated_candidate_paths'], ensure_ascii=False)}"
        )

    print("Web candidate verification passed: no default hard-coded candidates.")
    print(json.dumps({"candidate_count": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
