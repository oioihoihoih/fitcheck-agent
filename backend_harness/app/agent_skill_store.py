from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any


DEFAULT_SKILL_ROOT = Path(__file__).resolve().parents[1] / "agent_skills"


@dataclass(frozen=True)
class AgentSkill:
    name: str
    description: str
    instructions: str
    tools: tuple[str, ...]
    triggers: tuple[str, ...]
    source_path: str
    priority: int = 0

    def catalog_entry(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tools": list(self.tools),
            "source_path": self.source_path,
        }

    def loaded_payload(self) -> dict[str, Any]:
        return {
            **self.catalog_entry(),
            "instructions": self.instructions,
        }


class AgentSkillStore:
    """Backend-owned progressive-disclosure store for FitCheck SOP skills."""

    def __init__(self, root: Path = DEFAULT_SKILL_ROOT) -> None:
        self.root = root
        self._skills = {skill.name: skill for skill in self._discover()}

    def catalog(self) -> list[dict[str, Any]]:
        return [
            skill.catalog_entry()
            for skill in sorted(self._skills.values(), key=lambda item: (-item.priority, item.name))
        ]

    def get(self, name: str) -> AgentSkill:
        if name not in self._skills:
            raise KeyError(f"Unknown agent skill: {name}")
        return self._skills[name]

    def match(self, text: str, *, has_url: bool = False, has_attachments: bool = False) -> list[AgentSkill]:
        normalized = _normalize(text)
        scored: list[tuple[int, AgentSkill]] = []
        for skill in self._skills.values():
            if skill.name == "owned-baseline-url" and not has_url:
                continue
            score = skill.priority
            trigger_hits = 0
            for trigger in skill.triggers:
                if _normalize(trigger) in normalized:
                    score += 20
                    trigger_hits += 1
            description_terms = {
                term
                for term in re.findall(r"[0-9a-zA-Z가-힣]{2,}", skill.description.lower())
                if term not in _STOP_TERMS
            }
            score += min(8, sum(1 for term in description_terms if term in normalized))
            if has_url and trigger_hits and skill.name == "owned-baseline-url":
                score += 16
            if has_attachments and skill.name == "uploaded-image-evidence":
                score += 20
            if score > skill.priority:
                scored.append((score, skill))
        return [skill for _, skill in sorted(scored, key=lambda item: (-item[0], -item[1].priority, item[1].name))]

    def _discover(self) -> list[AgentSkill]:
        if not self.root.exists():
            return []
        skills: list[AgentSkill] = []
        for path in sorted(self.root.glob("*/SKILL.md")):
            skill = _parse_skill(path, self.root)
            if skill is not None:
                skills.append(skill)
        return skills


def _parse_skill(path: Path, root: Path) -> AgentSkill | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    _, frontmatter, body = text.split("---", 2)
    metadata: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"')
    name = metadata.get("name", "").strip()
    description = metadata.get("description", "").strip()
    if not name or not description:
        return None
    return AgentSkill(
        name=name,
        description=description,
        instructions=body.strip(),
        tools=tuple(_split_csv(metadata.get("tools", ""))),
        triggers=tuple(_split_csv(metadata.get("triggers", ""))),
        source_path=path.relative_to(root.parent).as_posix(),
        priority=int(metadata.get("priority", "0") or 0),
    )


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


_STOP_TERMS = {
    "and",
    "the",
    "for",
    "from",
    "with",
    "when",
    "user",
    "using",
    "사용자",
    "상품",
    "도구",
}
