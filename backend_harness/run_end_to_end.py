from __future__ import annotations

import json
from pathlib import Path

from app.api import ApiHarness


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    output_dir = project_root / "harness_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    db_path = output_dir / "end_to_end_db.json"
    e2e_path = output_dir / "end_to_end_result.json"
    for generated_path in (db_path, e2e_path):
        if generated_path.exists():
            generated_path.unlink()

    api = ApiHarness(db_path=db_path)

    closet_payload = {
        "user_id": "demo_user_001",
        "input_channel": "mobile_web",
        "text_path": str(project_root / "samples" / "closet" / "owned_item_info.txt"),
        "uploaded_assets": [
            {
                "path": str(project_root / "samples" / "closet" / "owned_item_photo.jpg"),
                "asset_type_hint": "item_photo",
            }
        ],
        "fit_notes": "평소 마음에 들어 자주 입는 기준 티셔츠로 등록",
        "design_notes": "차콜 색상, 가슴 쪽 작은 그래픽, 피그먼트 워싱 느낌",
        "finish_notes": "",
        "preference_score": 5,
    }
    closet_result = api.post_closet_items_from_mobile_form(closet_payload)
    closet_item_id = closet_result["closet_item"]["id"]

    candidate_paths = [
        project_root / "samples" / "product_candidates" / "candidate_fit_good.json",
        project_root / "samples" / "product_candidates" / "candidate_risky_short_tight.json",
        project_root / "samples" / "product_candidates" / "candidate_missing_info.json",
    ]

    product_results = []
    match_results = []
    for candidate_path in candidate_paths:
        product_result = api.post_products_analyze_from_fixture(
            {
                "user_id": "demo_user_001",
                "candidate_fixture_path": str(candidate_path),
            }
        )
        product_results.append(product_result)

        match_result = api.post_matches(
            {
                "product_analysis_id": product_result["product_analysis"]["id"],
                "closet_item_ids": [closet_item_id],
            }
        )
        match_results.append(match_result)

    e2e_result = {
        "status": "completed",
        "closet_result": closet_result,
        "product_results": product_results,
        "match_results": match_results,
        "summary_table": [
            _summary_row(match_result)
            for match_result in match_results
        ],
    }

    with e2e_path.open("w", encoding="utf-8") as f:
        json.dump(e2e_result, f, ensure_ascii=False, indent=2)

    print(json.dumps(e2e_result["summary_table"], ensure_ascii=False, indent=2))
    print(f"\nSaved: {e2e_path}")


def _summary_row(match_result: dict) -> dict:
    if "match_report" not in match_result:
        return {
            "status": match_result.get("status", "error"),
            "error": match_result.get("error", {"code": "MATCH_REPORT_MISSING"}),
            "tool_calls": match_result.get("tool_calls", []),
        }
    report = match_result["match_report"]
    match = report["match_result"]
    return {
        "report_id": report["id"],
        "product_analysis_id": report["product_analysis_id"],
        "recommendation": match["recommendation"],
        "overall": match["score"]["overall"],
        "size": match["score"]["size"],
        "material": match["score"]["material"],
        "price": match["score"]["price"],
        "confidence": match["score"]["confidence"],
        "risk_count": len(match["risks"]),
        "missing_fields": match["missing_fields"],
    }


if __name__ == "__main__":
    main()
