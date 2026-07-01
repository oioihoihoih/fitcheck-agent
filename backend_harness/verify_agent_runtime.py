from __future__ import annotations

import json
from pathlib import Path

import run_web_candidates_match
from app.clothing_purchase_agent import ClothingPurchaseAgent


EXPECTED_TOOL_CALLS = [
    "decide_search_strategy",
    "ensure_candidate_cache",
    "load_keyword_probe",
    "normalize_free_text_with_llm",
    "merge_intake_fields",
    "build_closet_memory",
    "load_candidate_products",
    "match_candidates",
    "rank_candidates",
    "guardrail_check",
]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output_dir = root / "harness_outputs"
    web_result_path = output_dir / "web_candidates_match_result.json"
    keyword_result_path = output_dir / "web_keyword_probe" / "keyword_web_probe_result.json"
    run_web_candidates_match.main()

    agent = ClothingPurchaseAgent(
        web_result_path=web_result_path,
        keyword_result_path=keyword_result_path,
        refresh_web_candidates=run_web_candidates_match.main,
    )

    free_text_result = agent.run(
        {
            "free_text": (
                "다른 옷 테스트. 회색 후드티인데 총장 50 어깨 40 "
                "가슴단면 45 소매 10. 폴리에스터 100%, 100000원. "
                "핏이 중요하고 봉제가 튼튼한 게 좋음"
            )
        }
    )
    free_text_closet = free_text_result["closet_item"]
    _assert_equal(
        free_text_closet["measurements"],
        {"length": 50.0, "shoulder": 40.0, "chest": 45.0, "sleeve": 10.0},
        "free text measurements",
    )
    _assert_equal(free_text_closet["material"], {"polyester": 100}, "free text material")
    _assert_equal(free_text_closet["price"], 100000, "free text price")
    _assert_equal(free_text_result["summary_table"], [], "default legacy harness candidates")
    _assert_equal(
        [call["name"] for call in free_text_result["agent_run"]["tool_calls"]],
        EXPECTED_TOOL_CALLS,
        "agent tool order",
    )

    structured_result = agent.run(
        {
            "free_text": "",
            "name": "직접 입력 테스트 셔츠",
            "brand": "테스트브랜드",
            "preference_reasons": "핏, 봉제",
            "material_text": "면 100%",
            "price_text": "30000원",
            "finish_notes": "봉제가 튼튼함",
            "measurements": {"length": 52, "shoulder": 41, "chest": 46, "sleeve": 11},
        }
    )
    structured_closet = structured_result["closet_item"]
    _assert_equal(structured_closet["name"], "직접 입력 테스트 셔츠", "structured name")
    _assert_equal(
        structured_closet["measurements"],
        {"length": 52.0, "shoulder": 41.0, "chest": 46.0, "sleeve": 11.0},
        "structured measurements",
    )
    _assert_equal(structured_closet["material"], {"cotton": 100}, "structured material")
    _assert_equal(structured_closet["price"], 30000, "structured price")
    _assert_equal(structured_result["summary_table"], [], "structured default legacy harness candidates")

    print("Agent runtime verification passed.")
    print(
        json.dumps(
            {
                "free_text_candidate_count": len(free_text_result["summary_table"]),
                "structured_candidate_count": len(structured_result["summary_table"]),
                "llm_status": free_text_result["agent_run"]["llm_status"],
                "tools": EXPECTED_TOOL_CALLS,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _assert_equal(observed: object, expected: object, label: str) -> None:
    if observed != expected:
        raise SystemExit(
            f"{label} failed: expected {expected!r}, observed {observed!r}"
        )


if __name__ == "__main__":
    main()
