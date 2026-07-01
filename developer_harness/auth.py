from __future__ import annotations

import os

from fastapi import Depends, Header, HTTPException, Request


def harness_identity(
    request: Request,
    authorization: str | None = Header(default=None),
    x_harness_api_key: str | None = Header(default=None),
    x_harness_client_id: str | None = Header(default=None),
) -> dict[str, str]:
    expected = os.getenv("HARNESS_API_KEY")
    presented = x_harness_api_key or _bearer_token(authorization)
    if expected and presented != expected:
        raise HTTPException(status_code=401, detail="invalid harness api key")
    client_id = (
        x_harness_client_id
        or request.headers.get("x-fitcheck-session-id")
        or "anonymous"
    )
    source = (
        "x_harness_client_id"
        if x_harness_client_id
        else "x_fitcheck_session_id"
        if request.headers.get("x-fitcheck-session-id")
        else "default"
    )
    return {"client_id": client_id, "client_id_source": source, "auth": "api_key" if expected else "not_configured"}


def isolated_harness_identity(identity: dict[str, str] = Depends(harness_identity)) -> dict[str, str]:
    if identity.get("client_id_source") != "x_harness_client_id":
        raise HTTPException(
            status_code=400,
            detail="X-Harness-Client-Id is required for isolated harness state.",
        )
    return identity


def _bearer_token(value: str | None) -> str | None:
    if not value:
        return None
    prefix = "bearer "
    if value.lower().startswith(prefix):
        return value[len(prefix) :].strip()
    return None
