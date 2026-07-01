from __future__ import annotations

from fastapi import APIRouter

from ..schemas import WebCandidateRunRequest, WebCandidateRunResponse


router = APIRouter(prefix="/api")


@router.post("/run/web-candidates", response_model=WebCandidateRunResponse)
def run_web_candidates(intake_snapshot: WebCandidateRunRequest) -> dict:
    return _disabled_web_candidate_harness()


@router.get("/results/web-candidates", response_model=WebCandidateRunResponse)
def get_web_candidates() -> dict:
    return _disabled_web_candidate_harness()


def _disabled_web_candidate_harness() -> dict:
    return {
        "status": "disabled",
        "closet_item": None,
        "summary_table": [],
        "candidates": [],
        "keyword_probe": {},
        "agent_run": {
            "mode": "deprecated_deterministic_harness",
            "status": "disabled",
            "reason": "Fixed web candidate demo output has been removed from the API surface.",
        },
        "design_meta": {
            "message": "Use /api/agent/runs with a configured live search provider for agentic candidate discovery.",
        },
        "image_assets": {},
    }
