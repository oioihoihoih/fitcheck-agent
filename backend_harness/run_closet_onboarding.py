from __future__ import annotations

import json
from pathlib import Path

from app.api import ApiHarness


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    output_dir = project_root / "harness_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    db_path = output_dir / "closet_onboarding_db.json"
    result_path = output_dir / "closet_onboarding_result.json"
    for generated_path in (db_path, result_path):
        if generated_path.exists():
            generated_path.unlink()

    api = ApiHarness(db_path=db_path)

    payload = {
        "user_id": "demo_user_001",
        "input_channel": "mobile_web",
        "text_path": str(project_root / "현재 내 옷 정보" / "옷 정보.txt"),
        "uploaded_assets": [
            {
                "path": str(project_root / "현재 내 옷 정보" / "내 옷 사진.jpg"),
                "asset_type_hint": "item_photo",
            }
        ],
        "fit_notes": "평소 마음에 들어 자주 입는 기준 티셔츠로 등록",
        "design_notes": "차콜 색상, 가슴 쪽 작은 그래픽, 피그먼트 워싱 느낌",
        "finish_notes": "",
        "preference_score": 5,
    }

    result = api.post_closet_items_from_mobile_form(payload)

    with result_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nSaved: {result_path}")


if __name__ == "__main__":
    main()
