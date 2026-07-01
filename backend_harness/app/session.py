from __future__ import annotations

from uuid import uuid4

from fastapi import Request, Response


SESSION_COOKIE = "fitcheck_session_id"
SESSION_HEADER = "x-fitcheck-session-id"


def get_or_create_session_id(request: Request, response: Response) -> str:
    session_id = (
        request.headers.get(SESSION_HEADER)
        or request.cookies.get(SESSION_COOKIE)
        or f"sess_{uuid4().hex[:16]}"
    )
    response.headers["X-FitCheck-Session-Id"] = session_id
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 24 * 30,
    )
    return session_id

