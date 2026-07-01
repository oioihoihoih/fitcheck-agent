from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from ..schemas import ClosetItemUpsertRequest, ClosetListResponse
from ..server_context import agent_run_store
from ..session import get_or_create_session_id


# docs/25 P0-3: expose the existing closet_items store as an HTTP surface so the
# product's core premise (compare online candidates against MY clothes) has a
# real user-reachable data layer. Every operation is scoped to the caller's
# session id; a caller can never see or mutate another session's items.
router = APIRouter(prefix="/api/closet")


@router.get("", response_model=ClosetListResponse)
def list_closet(request: Request, response: Response) -> dict:
    session_id = get_or_create_session_id(request, response)
    store = agent_run_store()
    items = store.list_closet_items(session_id)
    return {"status": "completed", "session_id": session_id, "items": items}


@router.post("", response_model=ClosetListResponse)
def create_closet_item(
    payload: ClosetItemUpsertRequest, request: Request, response: Response
) -> dict:
    session_id = get_or_create_session_id(request, response)
    store = agent_run_store()
    item = payload.model_dump(mode="json", exclude_none=True)
    # A create never overwrites an existing item: drop any client id so the store
    # mints a fresh uuid. Updates go through PATCH (which keeps the id).
    item.pop("id", None)
    saved = store.upsert_closet_item(session_id, item)
    return {"status": "completed", "session_id": session_id, "items": [saved]}


@router.patch("/{item_id}", response_model=ClosetListResponse)
def update_closet_item(
    item_id: str, payload: ClosetItemUpsertRequest, request: Request, response: Response
) -> JSONResponse | dict:
    session_id = get_or_create_session_id(request, response)
    store = agent_run_store()
    existing = store.get_closet_item(session_id, item_id)
    if not existing:
        return JSONResponse(
            status_code=404,
            content={"status": "not_found", "session_id": session_id, "items": []},
        )
    patch = payload.model_dump(mode="json", exclude_none=True)
    patch.pop("id", None)
    merged = {**existing, **patch, "id": item_id}
    saved = store.upsert_closet_item(session_id, merged)
    return {"status": "completed", "session_id": session_id, "items": [saved]}


@router.delete("/{item_id}", response_model=ClosetListResponse)
def delete_closet_item(item_id: str, request: Request, response: Response) -> JSONResponse | dict:
    session_id = get_or_create_session_id(request, response)
    store = agent_run_store()
    removed = store.delete_closet_item(session_id, item_id)
    if not removed:
        return JSONResponse(
            status_code=404,
            content={"status": "not_found", "session_id": session_id, "items": []},
        )
    return {"status": "completed", "session_id": session_id, "items": store.list_closet_items(session_id)}
