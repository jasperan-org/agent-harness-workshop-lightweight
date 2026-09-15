"""Layer 7 — The Agent Loop, and Layer 8 — Context Engineering."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from backend.config import settings
from backend.core import agent, context
from backend.core.sse import sse_response
from backend.schemas import AgentReq, ThreadReq

router = APIRouter(prefix="/api", tags=["agent"])


@router.post("/agent/run")
async def run(req: AgentReq):
    return sse_response(agent.run_agent(req.prompt, req.thread_id))


@router.post("/context/preview")
async def context_preview(req: AgentReq) -> dict:
    """The window the loop would send, assembled without calling the model.

    Embeddings and the catalog are in-database, so this works even with no chat key configured.
    """
    return await agent.preview(req.prompt, req.thread_id)


@router.get("/context/archive")
async def context_archive(thread_id: str = "ctx", q: str = "", k: int = 8) -> dict:
    """Context that left the window: compacted turns and offloaded tool results. `q` searches it."""
    rows = await run_in_threadpool(context.archive_rows, thread_id, q or None, k)
    return {"thread_id": thread_id, "query": q or None, "rows": rows}


@router.post("/context/compact")
async def context_compact(req: ThreadReq) -> dict:
    """Archive the next chunk of older turns now, instead of waiting for the next turn."""
    recent, older = await run_in_threadpool(context.split_history, req.thread_id, settings.history_messages)
    recap, info = await run_in_threadpool(context.compact_turns, req.thread_id, older)
    return {"recap": recap, "info": info, "older": len(older), "keep_recent": len(recent),
            "chunk": context.COMPACT_CHUNK, "history_messages": settings.history_messages}


@router.get("/context/series")
def context_series(turns: int = 16, card_size: int = 900, tool_blob: int = 3600) -> dict:
    """The 'money shot' series: context size per turn, engineering OFF vs ON."""
    def sim(engineering: bool):
        sizes, hist = [], []
        for _ in range(turns):
            hist += [40, (80 if engineering else tool_blob), 60]   # user, tool result, assistant
            convo = card_size if engineering else sum(hist)
            sizes.append(convo + (sum(hist[-2:]) if engineering else 0))
        return sizes
    return {"off": sim(False), "on": sim(True)}
