from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

HARNESS_DIR = Path(__file__).resolve().parent
ROOT_DIR = HARNESS_DIR.parent
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.routes.agent import router as agent_router
from app.routes.closet import router as closet_router
from app.routes.favorites import router as favorites_router
from app.routes.pages import router as pages_router
from app.routes.status import router as status_router
from app.routes.uploads import router as uploads_router
from app.routes.web_candidates import router as web_candidates_router
from app.server_context import OUTPUT_DIR, WEBAPP_DIR, WEBAPP_NEXT_DIST, active_webapp_dir
from developer_harness.routes import router as developer_harness_router


app = FastAPI(title="FitCheck Agent", version="0.2.0")
app.mount("/static", StaticFiles(directory=WEBAPP_DIR), name="static")
# Serve the modern Vite build's hashed assets (from /assets/*) when present.
if (WEBAPP_NEXT_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=WEBAPP_NEXT_DIST / "assets"), name="assets")
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")
# Keep a reference so route modules and reloads pick the right UI directory.
_ = active_webapp_dir

app.include_router(pages_router)
app.include_router(status_router)
app.include_router(uploads_router)
app.include_router(agent_router)
app.include_router(closet_router)
app.include_router(favorites_router)
app.include_router(web_candidates_router)
app.include_router(developer_harness_router, prefix="/api/harness/v1")
app.include_router(developer_harness_router, prefix="/api/dev-harness")
