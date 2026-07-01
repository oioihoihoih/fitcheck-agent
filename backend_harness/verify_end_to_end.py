from __future__ import annotations

import json
from pathlib import Path

import run_end_to_end


EXPECTED_RECOMMENDATIONS = {
    "candidate_fit_good": "buy",
    "candidate_risky_short_tight": "avoid",
    "candidate_missing_info": "need_more_info",
}


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    result_path = project_root / "harness_outputs" / "end_to_end_result.json"
    run_end_to_end.main()

    with result_path.open("r", encoding="utf-8") as f:
        result = json.load(f)

    product_key_by_id = {
        item["product_analysis"]["id"]: item["product_analysis"]["candidate_key"]
        for item in result["product_results"]
    }

    actual = {}
    for match_result in result["match_results"]:
        if "match_report" not in match_result:
            raise SystemExit(
                "End-to-end verification failed: "
                f"match_report missing in {json.dumps(match_result, ensure_ascii=False)}"
            )
        report = match_result["match_report"]
        key = product_key_by_id[report["product_analysis_id"]]
        actual[key] = report["match_result"]["recommendation"]

    failures = []
    for key, expected in EXPECTED_RECOMMENDATIONS.items():
        observed = actual.get(key)
        if observed != expected:
            failures.append({"candidate_key": key, "expected": expected, "observed": observed})

    if failures:
        raise SystemExit(f"End-to-end verification failed: {json.dumps(failures, ensure_ascii=False)}")

    print("End-to-end verification passed.")
    print(json.dumps(actual, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
