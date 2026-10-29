from __future__ import annotations

import json
import os
from pathlib import Path

from app.api import ApiHarness
from app.tools.web_candidate_builder import build_web_candidate_fixtures
import run_keyword_web_probe


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    output_dir = project_root / "harness_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    db_path = output_dir / "web_candidates_match_db.json"
    result_path = output_dir / "web_candidates_match_result.json"
    generated_candidate_dir = output_dir / "web_keyword_probe" / "generated_candidates"
    generated_candidate_dir.mkdir(parents=True, exist_ok=True)

    for generated_path in (db_path, result_path):
        if generated_path.exists():
            generated_path.unlink(missing_ok=True)
    for generated_path in generated_candidate_dir.glob("*.json"):
        generated_path.unlink(missing_ok=True)

    run_keyword_web_probe.main()
    probe_path = output_dir / "web_keyword_probe" / "keyword_web_probe_result.json"
    probe_result = json.loads(probe_path.read_text(encoding="utf-8"))

    candidate_fixtures = build_web_candidate_fixtures(probe_result)
    candidate_paths = []
    for candidate in candidate_fixtures:
        candidate_path = generated_candidate_dir / f"{candidate['candidate_key']}.json"
        _write_json_atomic(candidate_path, candidate)
        candidate_paths.append(candidate_path)

    api = ApiHarness(db_path=db_path)
    closet_result = api.post_closet_items_from_mobile_form(
        {
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
    )
    closet_item_id = closet_result["closet_item"]["id"]

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
        match_results.append(
            api.post_matches(
                {
                    "product_analysis_id": product_result["product_analysis"]["id"],
                    "closet_item_ids": [closet_item_id],
                }
            )
        )

    result = {
        "status": "completed",
        "probe_path": str(probe_path),
        "generated_candidate_paths": [str(path) for path in candidate_paths],
        "closet_result": closet_result,
        "product_results": product_results,
        "match_results": match_results,
        "summary_table": [_summary_row(match_result, product_results) for match_result in match_results],
    }

    _write_json_atomic(result_path, result)
    print(json.dumps(result["summary_table"], ensure_ascii=False, indent=2))
    print(f"\nSaved: {result_path}")


def _summary_row(match_result: dict, product_results: list[dict]) -> dict:
    report = match_result["match_report"]
    match = report["match_result"]
    product = next(
        item["product_analysis"]
        for item in product_results
        if item["product_analysis"]["id"] == report["product_analysis_id"]
    )
    spec = product["normalized_spec"]
    return {
        "candidate_key": spec.get("candidate_key"),
        "name": spec.get("name"),
        "recommendation": match["recommendation"],
        "overall": match["score"]["overall"],
        "size": match["score"]["size"],
        "material": match["score"]["material"],
        "price": match["score"]["price"],
        "confidence": match["score"]["confidence"],
        "measurement_deltas": match["measurement_deltas"],
        "missing_fields": match["missing_fields"],
        "source_url": product["raw_input"].get("source_url"),
    }


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


if __name__ == "__main__":
    main()
