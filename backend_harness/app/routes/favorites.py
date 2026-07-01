from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from ..schemas import FavoriteListResponse, FavoriteUpsertRequest
from ..server_context import agent_run_store
from ..session import get_or_create_session_id


# docs/25 P1-2 (북극성 2): saved candidates. A shortlist otherwise lives only in a
# run's trace and vanishes; favorites let the user collect candidates across turns
# and sessions and compare them against their closet. Session-scoped like closet.
router = APIRouter(prefix="/api/favorites")


@router.get("", response_model=FavoriteListResponse)
def list_favorites(request: Request, response: Response) -> dict:
    session_id = get_or_create_session_id(request, response)
    items = agent_run_store().list_favorites(session_id)
    return {"status": "completed", "session_id": session_id, "items": items}


@router.post("", response_model=FavoriteListResponse)
def save_favorite(payload: FavoriteUpsertRequest, request: Request, response: Response) -> dict:
    session_id = get_or_create_session_id(request, response)
    store = agent_run_store()
    item = payload.model_dump(mode="json", exclude_none=True)
    # De-dup by candidate_key within a session: saving the same candidate updates
    # it rather than piling up duplicates.
    key = item.get("candidate_key")
    if key:
        for existing in store.list_favorites(session_id):
            if existing.get("candidate_key") == key:
                item["id"] = existing.get("id")
                break
    saved = store.upsert_favorite(session_id, item)
    return {"status": "completed", "session_id": session_id, "items": [saved]}


@router.delete("/{item_id}", response_model=FavoriteListResponse)
def delete_favorite(item_id: str, request: Request, response: Response) -> JSONResponse | dict:
    session_id = get_or_create_session_id(request, response)
    store = agent_run_store()
    if not store.delete_favorite(session_id, item_id):
        return JSONResponse(
            status_code=404,
            content={"status": "not_found", "session_id": session_id, "items": []},
        )
    return {"status": "completed", "session_id": session_id, "items": store.list_favorites(session_id)}
