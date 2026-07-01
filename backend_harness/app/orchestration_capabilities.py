from __future__ import annotations

from importlib import metadata, util
import os
from typing import Any


BACKEND_OWNED_BOUNDARIES = [
    "tool_registry",
    "tool_execution",
    "search_fetch_ocr_provider_adapters",
    "run_state_and_observations",
    "closet_memory",
    "match_scoring",
    "missing_information_questions",
    "grounded_final_report_validation",
]


def get_orchestration_status(llm_status: dict[str, Any] | None = None) -> dict[str, Any]:
    llm_status = llm_status or {}
    return {
        "current_runtime": "fitcheck_backend_owned_tool_loop",
        "decision": "keep_responses_api_runtime_use_agents_sdk_only_for_future_multi_agent_or_trace_upgrade",
        "recommended_path": "responses_api_primary_agents_sdk_optional_codex_sdk_development_only",
        "backend_owned_boundaries": BACKEND_OWNED_BOUNDARIES,
        "responses_api": {
            "role": "primary_product_llm_tool_calling_surface",
            "api_style": llm_status.get("api_style"),
            "transport": llm_status.get("transport"),
            "openai_python_sdk_installed": _package_installed("openai"),
            "openai_python_sdk_version": _package_version("openai"),
            "status": "active_when_llm_connected" if llm_status.get("status") == "connected" else "requires_llm_configuration",
            "portable_to_local_openai_compatible": True,
        },
        "agents_sdk": {
            "role": "optional_orchestration_for_multi_agent_handoffs_guardrails_and_tracing",
            "package": "openai-agents",
            "import_name": "agents",
            "installed": _package_installed("agents"),
            "version": _package_version("openai-agents") or _package_version("agents"),
            "enabled_for_product_runtime": _truthy_env("FITCHECK_ENABLE_AGENTS_SDK_RUNTIME"),
            "fitcheck_fit": "conditional",
            "adoption_gate": (
                "Only enable after verifying local/provider compatibility, backend-owned tool execution, "
                "SQLite run state persistence, SSE trace parity, and report grounding parity."
            ),
        },
        "codex_sdk": {
            "role": "development_orchestration_and_coding_workflow_automation",
            "package": "openai-codex",
            "import_name": "openai_codex",
            "installed": _package_installed("openai_codex"),
            "version": _package_version("openai-codex"),
            "enabled_for_product_runtime": False,
            "fitcheck_fit": "not_product_runtime_orchestrator",
            "reason": (
                "Codex SDK drives Codex coding sessions/app-server. FitCheck end-user product orchestration must keep "
                "backend-owned commerce/search/OCR/scoring/report tools and local OpenAI-compatible support."
            ),
        },
    }


def _package_installed(import_name: str) -> bool:
    return util.find_spec(import_name) is not None


def _package_version(distribution_name: str) -> str | None:
    try:
        return metadata.version(distribution_name)
    except metadata.PackageNotFoundError:
        return None


def _truthy_env(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}
