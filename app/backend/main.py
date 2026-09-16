"""Total Recall appbook — FastAPI application.

Warms the harness (connects to the AGENT schema, creates anything missing
idempotently) in the background on startup, mounts one router group per layer,
and serves the dependency-free SPA from the same origin.

Run from the ``app/`` directory:
    uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import FRONTEND_DIR
from backend.core import db
from backend.routers import agentloop, automations, layers, memory, skills


def _warm():
    # Oracle can report healthy before the listener accepts application sessions, and on a fresh
    # Codespace the lifecycle hooks may still be converging the AGENT credentials. Retry until the
    # harness is up. A bounded loop used to give up after ~5 minutes, and the process then kept its
    # *import-time* config forever: when the database was provisioned (or its AGENT password
    # converged) after that window, harness.ready stayed false until someone restarted uvicorn by
    # hand. Opening a Codespace must not need that hand.
    attempt = 0
    while True:
        attempt += 1
        try:
            db.initialize()
            return
        except Exception as e:
            # status() records the same first line for /api/health; printing it keeps
            # /tmp/total-recall-app.log self-explanatory while the database is unreachable.
            print(f"[warm] harness not ready (attempt {attempt}): {str(e).splitlines()[0][:160]}")
            time.sleep(min(5 * attempt, 15))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the harness without blocking startup (so the frontend serves immediately).
    threading.Thread(target=_warm, daemon=True).start()
    yield


app = FastAPI(title="Total Recall — Appbook", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for r in (layers.router, memory.router, skills.router, agentloop.router, automations.router):
    app.include_router(r)

app.mount("/images", StaticFiles(directory=str(FRONTEND_DIR.parent / "images")), name="images")
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)
