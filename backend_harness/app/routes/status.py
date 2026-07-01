from __future__ import annotations

from fastapi import APIRouter, Request, Response

from ..config_env import env_status
from ..orchestration_capabilities import get_orchestration_status
from ..providers import get_provider_status
from ..schemas import ApiStatusResponse
from ..server_context import AGENT_RUNS_PATH, KEYWORD_RESULT_PATH, WEB_RESULT_PATH
from ..session import get_or_create_session_id
from ..tools.llm_client import get_llm_status


router = APIRouter(prefix="/api")


@router.get("/status", response_model=ApiStatusResponse)
def status(request: Request, response: Response) -> dict:
    session_id = get_or_create_session_id(request, response)
    llm_status = get_llm_status()
    return {
        "status": "ready",
        "session_id": session_id,
        "has_web_result": WEB_RESULT_PATH.exists(),
        "has_keyword_probe": KEYWORD_RESULT_PATH.exists(),
        "llm": llm_status,
        "providers": get_provider_status(),
        "environment": env_status(),
        "orchestration": get_orchestration_status(llm_status),
        "production_agent": {
            "route": "/api/agent/runs",
            "events_route": "/api/agent/runs/{run_id}/events",
            "requires_llm_status": "connected",
            "run_store": str(AGENT_RUNS_PATH),
            "store_type": "sqlite",
        },
    }
