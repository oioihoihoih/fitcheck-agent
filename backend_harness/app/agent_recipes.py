from __future__ import annotations

import re
from typing import Any

from .agent_skill_store import AgentSkillStore


_SKILL_STORE = AgentSkillStore()
_LEGACY_RECIPE_IDS = {
    "outfit-pairing": "outfit_pairing_free_target",
    "owned-baseline-url": "owned_baseline_url_enrichment",
    "uploaded-image-evidence": "uploaded_image_evidence",
    "web-search-retry": "explicit_web_search_retry",
    "discover-similar-products": "similar_product_discovery",
}


def match_agent_recipes(run_text: str, *, has_url: bool = False, has_attachments: bool = False) -> list[dict[str, Any]]:
    return [
        {
            "id": _LEGACY_RECIPE_IDS.get(skill.name, skill.name.replace("-", "_")),
            "skill_name": skill.name,
            "title": skill.name.replace("-", " ").title(),
            "when": skill.description,
            "tool_sequence": list(skill.tools),
            "policy": "Load the skill through load_agent_skill for its full SOP instructions.",
            "source_path": skill.source_path,
        }
        for skill in _SKILL_STORE.match(
            run_text,
            has_url=has_url,
            has_attachments=has_attachments,
        )
    ]


def agent_skill_catalog() -> list[dict[str, Any]]:
    return _SKILL_STORE.catalog()


def load_agent_skill(name: str) -> dict[str, Any]:
    return _SKILL_STORE.get(name).loaded_payload()


def recipe_tool_suggestions(recipes: list[dict[str, Any]], *, query: str, baseline_source_url: str) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    recipe_ids = {str(recipe.get("id") or "") for recipe in recipes}
    if "outfit_pairing_free_target" in recipe_ids:
        suggestions.append(
            {
                "tool": "load_closet_memory",
                "arguments": {},
                "reason": "recipe_outfit_pairing_load_owned_baseline",
            }
        )
        if baseline_source_url:
            suggestions.append(
                {
                    "tool": "enrich_closet_item_from_web",
                    "arguments": {"refresh_web": False},
                    "reason": "recipe_outfit_pairing_enrich_baseline_url",
                }
            )
        if query:
            suggestions.append(
                {
                    "tool": "search_web_candidates",
                    "arguments": {"query": query, "refresh_web": False},
                    "reason": "recipe_outfit_pairing_search_target_item",
                }
            )
    if "explicit_web_search_retry" in recipe_ids and query:
        suggestions.append(
            {
                "tool": "search_web_candidates",
                "arguments": {"query": query, "refresh_web": True},
                "reason": "recipe_explicit_web_search_retry",
            }
        )
    return suggestions


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _is_outfit_pairing(text: str) -> bool:
    return any(cue in text for cue in ["같이 입", "함께 입", "어울리", "코디", "매치", "스타일링"]) or any(
        cue in text for cue in ["with", "pair", "outfit", "styling"]
    )


def _has_owned_item_cue(text: str) -> bool:
    return any(cue in text for cue in ["샀", "구매", "예전", "옛날", "가지고", "자주 입", "owned", "bought", "purchased"])


def _asks_for_web_search(text: str) -> bool:
    return any(cue in text for cue in ["웹서치", "웹 검색", "검색해", "찾아봐", "search web", "web search"])
