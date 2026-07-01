from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent import ClosetOnboardingAgent
from .db import JsonStore
from .product_agent import MatchAgent, ProductAnalysisAgent


class ApiHarness:
    """Server boundary that mirrors the future FastAPI routes."""

    def __init__(self, db_path: Path) -> None:
        self.db = JsonStore(db_path)

    def post_closet_items_from_mobile_form(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Equivalent to POST /closet/items for the current onboarding dry-run."""
        required = ["user_id", "text_path", "uploaded_assets"]
        missing = [key for key in required if key not in payload]
        if missing:
            return {
                "status": "error",
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Required fields are missing.",
                    "missing_fields": missing,
                },
            }

        agent = ClosetOnboardingAgent(self.db)
        return agent.run(payload)

    def get_closet_item(self, closet_item_id: str) -> dict[str, Any] | None:
        """Equivalent to GET /closet/items/{closet_item_id}."""
        return self.db.get_closet_item(closet_item_id)

    def post_products_analyze_from_fixture(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Equivalent to POST /products/analyze-with-images for the dry-run."""
        required = ["user_id", "candidate_fixture_path"]
        missing = [key for key in required if key not in payload]
        if missing:
            return {
                "status": "error",
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Required fields are missing.",
                    "missing_fields": missing,
                },
            }

        agent = ProductAnalysisAgent(self.db)
        return agent.run(payload)

    def post_matches(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Equivalent to POST /matches."""
        required = ["product_analysis_id"]
        missing = [key for key in required if key not in payload]
        if missing:
            return {
                "status": "error",
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Required fields are missing.",
                    "missing_fields": missing,
                },
            }

        agent = MatchAgent(self.db)
        return agent.run(payload)

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        """Equivalent to GET /reports/{report_id}."""
        return self.db.get_match_report(report_id)
