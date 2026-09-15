"""Cognitive memory via the Oracle AI Agent Memory package (OAMP), with an
in-database ONNX embedder. OAMP uses one dedicated connection guarded by a lock
(it is not built for concurrent pooled use); calls are short and serialized.

Also holds procedural *workflow* memory (capture/recall with embedding dedup),
which lives in a plain vector table via the shared pool.
"""
from __future__ import annotations

import json
import threading

import numpy as np
import oracledb
from oracleagentmemory.apis.embedders.embedder import IEmbedder

from backend.config import settings
from backend.core import db

_lock = threading.Lock()
_oamp = None
_conn = None
_threads: dict = {}
EMB = settings.embed_model
U, A = settings.user_id, settings.agent_id


class _InDBOnnxEmbedder(IEmbedder):
    """In-database ONNX embedder for OAMP.

    Must subclass IEmbedder: the package reads ``embedder.embedding_dimension`` when the
    memory client is constructed, and the ABC is what infers it (and max_input_tokens) from
    the first embed call. Without the base class, _ensure() raised
    "AttributeError: '_InDBOnnxEmbedder' object has no attribute 'embedding_dimension'" and
    every /api/memory/* route returned HTTP 500. Mirrors OracleONNXEmbedder in the notebook.
    """

    def __init__(self, conn):
        self.conn = conn
    def embed(self, texts, *, is_query=False):
        out = []
        cur = self.conn.cursor()
        try:
            for t in texts:
                cur.execute(f"SELECT VECTOR_EMBEDDING({EMB} USING :t AS DATA) v FROM dual", {"t": t})
                out.append(list(cur.fetchone()[0]))
        finally:
            cur.close()
        return np.array(out, dtype=np.float32)
    async def embed_async(self, texts, *, is_query=False):
        return self.embed(texts, is_query=is_query)


def _ensure():
    global _oamp, _conn
    if _oamp is not None:
        return _oamp
    from oracleagentmemory.core import OracleAgentMemory
    from oracleagentmemory.core.llms import Llm
    _conn = oracledb.connect(user=settings.ora_user, password=settings.ora_password, dsn=settings.ora_dsn)
    _oamp = OracleAgentMemory(connection=_conn, embedder=_InDBOnnxEmbedder(_conn),
                              llm=(Llm(f"openai/{settings.model}", api_base=settings.oci_endpoint, api_key=settings.llm_api_key)
                                   if settings.llm_provider == "oci" else Llm(f"openai/{settings.model}")),
                              extract_memories=False,
                              table_name_prefix=settings.oamp_prefix, schema_policy="create_if_necessary")
    for fn, args in [(_oamp.add_user, (U, "An analytics engineer who prefers concise, decision-first answers.")),
                     (_oamp.add_agent, (A, "A retail-analytics agent that grounds answers in the database."))]:
        try:
            fn(*args)
        except Exception:
            pass
    return _oamp


# Oracle closes idle connections and the OAMP client keeps one for the life of the process, so a
# long-quiet appbook answers the first /api/memory/* or Layer 8 request with a dead-socket error
# (DPY-4011). Reconnect once and retry; the cached thread objects go with the old connection.
_CONN_LOST = db._CONN_LOST   # same dead-socket family the OracleVS store recovers from


def _reset_oamp():
    global _oamp, _conn
    try:
        if _conn is not None:
            _conn.close()
    except Exception:
        pass
    _oamp = None
    _conn = None
    _threads.clear()


def _call(fn):
    """Run fn(client) under the OAMP lock, reconnecting once if the database dropped the socket."""
    with _lock:
        try:
            return fn(_ensure())
        except Exception as e:
            if not any(tok in str(e) for tok in _CONN_LOST):
                raise
            print(f"[memory] OAMP connection lost ({str(e).splitlines()[0][:80]}) — reconnecting.")
            _reset_oamp()
            return fn(_ensure())


def remember(content: str) -> str:
    return _call(lambda o: o.add_memory(content, user_id=U, agent_id=A))


def recall(query: str, k: int = 5):
    res = _call(lambda o: o.search(query, user_id=U, agent_id=A, max_results=k))
    hits = []
    for r in res:
        record = getattr(r, "record", None)
        distance = getattr(r, "distance", None)
        if distance is None and record is not None:
            distance = record.get("distance") if isinstance(record, dict) else getattr(record, "distance", None)
        try:
            distance = round(float(distance), 4) if distance is not None else None
        except (TypeError, ValueError):
            distance = None
        hits.append({"content": r.content, "distance": distance})
    return hits


def _thread(client, thread_id: str):
    if thread_id not in _threads:
        try:
            _threads[thread_id] = client.create_thread(thread_id=thread_id, user_id=U, agent_id=A)
        except Exception:
            _threads[thread_id] = client.get_thread(thread_id)
    return _threads[thread_id]


def add_turn(thread_id: str, role: str, content: str):
    return _call(lambda o: _thread(o, thread_id).add_messages([{"role": role, "content": content}]))


def get_turns(thread_id: str):
    try:
        return _call(lambda o: [{"role": m.role, "content": m.content}
                                for m in _thread(o, thread_id).get_messages()])
    except Exception:
        return []


def context_card(thread_id: str, recent: int = 6, relevant: int = 4):
    try:
        return _call(lambda o: _thread(o, thread_id).get_context_card(
            fallback_message_count=recent, max_relevant_results=relevant,
            max_recent_messages=recent).content)
    except Exception:
        return None


# ── procedural workflow memory (shared pool, no OAMP) ──────────────────────
def capture_workflow(intent: str, steps, tools_used):
    sims = db.q(f'''SELECT id, occurrences,
                 VECTOR_DISTANCE(embedding, VECTOR_EMBEDDING({EMB} USING :q AS DATA), COSINE) d
                 FROM agent_workflow ORDER BY d FETCH APPROX FIRST 1 ROWS ONLY''', {"q": intent})
    if sims and sims[0]["D"] is not None and sims[0]["D"] < 0.15:
        db.x("UPDATE agent_workflow SET occurrences=occurrences+1, last_seen=SYSTIMESTAMP WHERE id=:i",
             {"i": sims[0]["ID"]})
        return {"status": "merged"}
    db.x(f'''INSERT INTO agent_workflow (intent, steps, tools_used, embedding)
             VALUES (:i, :s, :u, VECTOR_EMBEDDING({EMB} USING :i AS DATA))''',
         {"i": intent, "s": json.dumps(steps), "u": ",".join(tools_used)})
    return {"status": "inserted"}


def recall_workflow(query: str, k: int = 3):
    return db.q(f'''SELECT RAWTOHEX(id) id, intent, steps, tools_used, occurrences,
                 VECTOR_DISTANCE(embedding, VECTOR_EMBEDDING({EMB} USING :q AS DATA), COSINE) d
                 FROM agent_workflow ORDER BY d FETCH APPROX FIRST :k ROWS ONLY''', {"q": query, "k": k})


# ── conversation history (past threads, read straight from OAMP_MESSAGE) ───
def list_threads(prefix="mc-", limit=40):
    """Past conversations: thread id, message count, last activity, and a preview
    (the first user message). Filtered to this app's user and the given id prefix."""
    try:
        return db.q("""
          SELECT m.thread_id, m.msgs, TO_CHAR(m.last,'YYYY-MM-DD HH24:MI') AS last_at, p.content AS preview
          FROM (SELECT thread_id, COUNT(*) msgs, MAX(created_at) last
                FROM OAMP_MESSAGE WHERE thread_id LIKE :p AND user_id=:u GROUP BY thread_id) m
          JOIN (SELECT thread_id, content FROM (
                  SELECT thread_id, content,
                         ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY order_seq) rn
                  FROM OAMP_MESSAGE WHERE message_role='user' AND user_id=:u) WHERE rn=1) p
            ON p.thread_id = m.thread_id
          ORDER BY m.last DESC FETCH FIRST :k ROWS ONLY
        """, {"p": prefix + "%", "u": U, "k": limit})
    except Exception:
        return []


def thread_messages(thread_id: str):
    """All messages of one thread, in order, for re-opening a past conversation."""
    try:
        return db.q("""SELECT message_role, content FROM OAMP_MESSAGE
                       WHERE thread_id=:t AND user_id=:u ORDER BY order_seq""",
                    {"t": thread_id, "u": U})
    except Exception:
        return []
