from __future__ import annotations

import os
from pathlib import Path
from typing import Any


_LOADED_ENV_PATHS: list[str] = []


def load_project_env(root: Path | None = None) -> dict[str, Any]:
    """Load local .env files without overriding real environment variables."""

    project_root = root or Path(__file__).resolve().parents[2]
    candidates = [
        project_root / ".env",
        project_root / "backend_harness" / ".env",
    ]
    loaded: list[str] = []
    for env_path in candidates:
        if not env_path.exists():
            continue
        _load_env_file(env_path)
        loaded.append(str(env_path))
        if str(env_path) not in _LOADED_ENV_PATHS:
            _LOADED_ENV_PATHS.append(str(env_path))
    return {
        "loaded": bool(loaded),
        "loaded_files": loaded,
        "loaded_file_count": len(loaded),
    }


def env_status() -> dict[str, Any]:
    return {
        "loaded": bool(_LOADED_ENV_PATHS),
        "loaded_file_count": len(_LOADED_ENV_PATHS),
        "loaded_files": list(_LOADED_ENV_PATHS),
    }


def _load_env_file(env_path: Path) -> None:
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_inline_comment(value.strip()).strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        if char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index]
    return value


load_project_env()
