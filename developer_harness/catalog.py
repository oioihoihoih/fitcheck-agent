from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


def load_catalog(root: Path) -> dict[str, list[dict[str, Any]]]:
    return {
        "skills": _skills(root / ".agents" / "skills"),
        "agents": _agents(root / ".codex" / "agents"),
        "hooks": _hooks(root / ".codex" / "hooks.json"),
    }


def _skills(directory: Path) -> list[dict[str, Any]]:
    result = []
    for path in sorted(directory.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        result.append(
            {
                "name": _frontmatter(text, "name") or path.parent.name,
                "description": _frontmatter(text, "description") or "",
                "path": str(path),
            }
        )
    return result


def _agents(directory: Path) -> list[dict[str, Any]]:
    result = []
    for path in sorted(directory.glob("*.toml")):
        text = path.read_text(encoding="utf-8")
        result.append(
            {
                "name": _toml_string(text, "name") or path.stem,
                "description": _toml_string(text, "description") or "",
                "path": str(path),
            }
        )
    return result


def _hooks(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        {"event": event, "matcher_groups": len(groups)}
        for event, groups in (data.get("hooks") or {}).items()
    ]


def _frontmatter(text: str, key: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*[\"']?(.+?)[\"']?\s*$", text)
    return match.group(1).strip() if match else None


def _toml_string(text: str, key: str) -> str | None:
    match = re.search(rf'(?m)^{re.escape(key)}\s*=\s*"([^"]*)"', text)
    return match.group(1) if match else None

