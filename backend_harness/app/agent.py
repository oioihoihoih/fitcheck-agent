from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import JsonStore
from .schemas import ToolCall
from .tools.closet_lookup import lookup_existing_closet_item
from .tools.image_classifier import classify_closet_asset
from .tools.input_normalizer import (
    infer_closet_traits,
    parse_closet_text,
    read_utf8_text,
)
from .tools.llm_client import generate_closet_summary_stub
from .tools.ocr_extractor import extract_ocr_for_asset


class ClosetOnboardingAgent:
    """Orchestrates the current closet-item onboarding workflow."""

    def __init__(self, db: JsonStore) -> None:
        self.db = db

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        steps: list[ToolCall] = []

        text_path = Path(payload["text_path"])
        raw_text = read_utf8_text(text_path)
        steps.append(
            ToolCall(
                name="read_mobile_form_text",
                status="completed",
                output={"path": str(text_path), "chars": len(raw_text)},
            )
        )

        parsed = parse_closet_text(raw_text)
        steps.append(
            ToolCall(
                name="parse_closet_text",
                status="completed",
                output={
                    "name": parsed.get("name"),
                    "brand": parsed.get("brand"),
                    "measurements": parsed.get("measurements"),
                },
            )
        )

        traits = infer_closet_traits(parsed)
        steps.append(
            ToolCall(
                name="infer_closet_traits",
                status="completed",
                output=traits,
            )
        )

        lookup_result = lookup_existing_closet_item(parsed, traits)
        steps.append(
            ToolCall(
                name="lookup_existing_closet_item",
                status=lookup_result["status"],
                output={
                    "query": lookup_result["query"],
                    "provider": lookup_result["provider"],
                    "match_count": len(lookup_result["matches"]),
                    "enrichment": lookup_result["enrichment"],
                    "missing_after_lookup": lookup_result["missing_after_lookup"],
                },
            )
        )

        assets: list[dict[str, Any]] = []
        for asset in payload.get("uploaded_assets", []):
            asset_path = Path(asset["path"])
            classification = classify_closet_asset(
                asset_path=asset_path,
                asset_type_hint=asset.get("asset_type_hint"),
            )
            steps.append(
                ToolCall(
                    name="classify_closet_asset",
                    status="completed",
                    output=classification,
                )
            )

            ocr_result = extract_ocr_for_asset(asset_path, classification)
            steps.append(
                ToolCall(
                    name="extract_ocr_for_asset",
                    status=ocr_result["status"],
                    output=ocr_result,
                )
            )

            assets.append(
                {
                    "path": str(asset_path),
                    "asset_type_hint": asset.get("asset_type_hint"),
                    "classification": classification,
                    "ocr": ocr_result,
                }
            )

        closet_item = {
            "user_id": payload["user_id"],
            "category": traits["category"],
            "brand": parsed.get("brand"),
            "name": parsed.get("name"),
            "color": traits.get("color"),
            "sleeve_type": traits.get("sleeve_type"),
            "fit_guess": traits.get("fit_guess"),
            "preference_reasons": parsed.get("preference_reasons", []),
            "preference_signals": traits.get("preference_signals", []),
            "size_label": payload.get("size_label"),
            "measurements": parsed.get("measurements", {}),
            "material": payload.get("material", {}),
            "lookup_enrichment": lookup_result["enrichment"],
            "lookup_matches": lookup_result["matches"],
            "missing_after_lookup": lookup_result["missing_after_lookup"],
            "fit_notes": _merge_notes(
                payload.get("fit_notes", ""),
                parsed.get("preference_reasons", []),
                "핏",
            ),
            "design_notes": _merge_notes(
                payload.get("design_notes", ""),
                parsed.get("preference_reasons", []),
                "디자인",
            ),
            "finish_notes": _merge_notes(
                payload.get("finish_notes", ""),
                parsed.get("preference_reasons", []),
                "오래",
            ),
            "price": payload.get("price"),
            "preference_score": payload.get("preference_score", 5),
            "source": {
                "input_channel": payload.get("input_channel", "mobile_web"),
                "text_path": str(text_path),
                "measurement_source": "manual_text",
                "measurement_confidence": 1.0,
            },
            "assets": assets,
        }

        stored = self.db.insert_closet_item(closet_item)
        steps.append(
            ToolCall(
                name="db.insert_closet_item",
                status="completed",
                output={"closet_item_id": stored["id"]},
            )
        )

        summary = generate_closet_summary_stub(stored)
        steps.append(
            ToolCall(
                name="generate_closet_summary",
                status="completed",
                output={"provider": summary["provider"]},
            )
        )

        return {
            "status": "completed",
            "api": "POST /closet/items",
            "closet_item": stored,
            "summary": summary,
            "tool_calls": [step.to_dict() for step in steps],
        }


def _merge_notes(existing_note: str, reasons: list[str], keyword: str) -> str:
    matched = [reason for reason in reasons if keyword in reason]
    parts = [part for part in [existing_note, ", ".join(matched)] if part]
    return " / ".join(parts)
