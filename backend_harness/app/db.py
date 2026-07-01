from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonStore:
    """Small JSON DB used only by the local workflow harness."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"closet_items": [], "product_analyses": [], "match_reports": []}
        with self.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("closet_items", [])
        data.setdefault("product_analyses", [])
        data.setdefault("match_reports", [])
        return data

    def _save(self, data: dict[str, Any]) -> None:
        tmp_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(self.path)

    def insert_closet_item(self, item: dict[str, Any]) -> dict[str, Any]:
        data = self._load()
        items = data.setdefault("closet_items", [])
        item_id = f"closet_{len(items) + 1:03d}"
        record = {
            "id": item_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **item,
        }
        items.append(record)
        self._save(data)
        return record

    def get_closet_item(self, closet_item_id: str) -> dict[str, Any] | None:
        data = self._load()
        for item in data.get("closet_items", []):
            if item.get("id") == closet_item_id:
                return item
        return None

    def list_closet_items(self) -> list[dict[str, Any]]:
        return self._load().get("closet_items", [])

    def insert_product_analysis(self, analysis: dict[str, Any]) -> dict[str, Any]:
        data = self._load()
        items = data.setdefault("product_analyses", [])
        analysis_id = f"product_{len(items) + 1:03d}"
        record = {
            "id": analysis_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **analysis,
        }
        items.append(record)
        self._save(data)
        return record

    def get_product_analysis(self, product_analysis_id: str) -> dict[str, Any] | None:
        data = self._load()
        for item in data.get("product_analyses", []):
            if item.get("id") == product_analysis_id:
                return item
        return None

    def insert_match_report(self, report: dict[str, Any]) -> dict[str, Any]:
        data = self._load()
        items = data.setdefault("match_reports", [])
        report_id = f"report_{len(items) + 1:03d}"
        record = {
            "id": report_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **report,
        }
        items.append(record)
        self._save(data)
        return record

    def get_match_report(self, report_id: str) -> dict[str, Any] | None:
        data = self._load()
        for item in data.get("match_reports", []):
            if item.get("id") == report_id:
                return item
        return None
