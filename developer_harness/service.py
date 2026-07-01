from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .runtime import DevelopmentHarness
from .store import HarnessStore


ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def harness() -> DevelopmentHarness:
    return DevelopmentHarness(
        ROOT,
        HarnessStore(ROOT / "harness_outputs" / "developer_harness.sqlite3"),
    )

