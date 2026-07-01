from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import JsonStore
from .schemas import ToolCall
from .tools.llm_client import generate_match_report_summary
from .tools.product_tools import (
    analyze_product_assets,
    normalize_product_spec,
)
from .tools.match_tools import (
    calculate_match,
    choose_closet_targets,
)


class ProductAnalysisAgent:
    """Analyzes a candidate product from a fixture that simulates uploaded assets."""

    def __init__(self, db: JsonStore) -> None:
        self.db = db

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        steps: list[ToolCall] = []
        fixture_path = Path(payload["candidate_fixture_path"])
        with fixture_path.open("r", encoding="utf-8") as f:
            fixture = json.load(f)
        steps.append(
            ToolCall(
                name="read_candidate_fixture",
                status="completed",
                output={
                    "path": str(fixture_path),
                    "candidate_key": fixture.get("candidate_key"),
                },
            )
        )

        asset_results = analyze_product_assets(fixture.get("assets", []))
        steps.append(
            ToolCall(
                name="analyze_product_assets",
                status="completed",
                output={
                    "asset_count": len(asset_results),
                    "ocr_targets": [
                        asset["image_type"]
                        for asset in asset_results
                        if asset["ocr"]["status"] == "completed"
                    ],
                },
            )
        )

        normalized_spec = normalize_product_spec(fixture, asset_results)
        steps.append(
            ToolCall(
                name="normalize_product_spec",
                status="completed",
                output={
                    "category": normalized_spec.get("category"),
                    "measurements": normalized_spec.get("measurements"),
                    "material": normalized_spec.get("material"),
                    "missing_fields": normalized_spec.get("missing_fields"),
                    "overall_confidence": normalized_spec.get("overall_confidence"),
                },
            )
        )

        stored = self.db.insert_product_analysis(
            {
                "user_id": payload["user_id"],
                "source_type": "fixture",
                "source_path": str(fixture_path),
                "candidate_key": fixture.get("candidate_key"),
                "raw_input": fixture,
                "asset_results": asset_results,
                "normalized_spec": normalized_spec,
                "status": "completed",
            }
        )
        steps.append(
            ToolCall(
                name="db.insert_product_analysis",
                status="completed",
                output={"product_analysis_id": stored["id"]},
            )
        )

        return {
            "status": "completed",
            "api": "POST /products/analyze-with-images",
            "product_analysis": stored,
            "tool_calls": [step.to_dict() for step in steps],
        }


class MatchAgent:
    """Compares a candidate product with registered closet items."""

    def __init__(self, db: JsonStore) -> None:
        self.db = db

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        steps: list[ToolCall] = []
        product = self.db.get_product_analysis(payload["product_analysis_id"])
        if product is None:
            return {
                "status": "error",
                "error": {
                    "code": "PRODUCT_ANALYSIS_NOT_FOUND",
                    "message": "Product analysis does not exist.",
                },
            }

        closet_targets = choose_closet_targets(
            closet_items=self.db.list_closet_items(),
            product_spec=product["normalized_spec"],
            closet_item_ids=payload.get("closet_item_ids"),
        )
        steps.append(
            ToolCall(
                name="choose_closet_targets",
                status="completed" if closet_targets else "no_target",
                output={
                    "target_count": len(closet_targets),
                    "closet_item_ids": [item["id"] for item in closet_targets],
                },
            )
        )

        if not closet_targets:
            return {
                "status": "error",
                "error": {
                    "code": "NO_COMPARABLE_CLOSET_ITEM",
                    "message": "No comparable closet item was found.",
                },
                "tool_calls": [step.to_dict() for step in steps],
            }

        match_result = calculate_match(product["normalized_spec"], closet_targets[0])
        steps.append(
            ToolCall(
                name="calculate_match",
                status="completed",
                output={
                    "overall": match_result["score"]["overall"],
                    "recommendation": match_result["recommendation"],
                    "risk_count": len(match_result["risks"]),
                },
            )
        )

        summary = generate_match_report_summary(product, closet_targets[0], match_result)
        steps.append(
            ToolCall(
                name="generate_match_report_summary",
                status="completed",
                output={"provider": summary["provider"]},
            )
        )

        stored = self.db.insert_match_report(
            {
                "product_analysis_id": product["id"],
                "closet_item_ids": [closet_targets[0]["id"]],
                "match_result": match_result,
                "summary": summary,
                "status": "completed",
            }
        )
        steps.append(
            ToolCall(
                name="db.insert_match_report",
                status="completed",
                output={"match_report_id": stored["id"]},
            )
        )

        return {
            "status": "completed",
            "api": "POST /matches",
            "match_report": stored,
            "tool_calls": [step.to_dict() for step in steps],
        }
