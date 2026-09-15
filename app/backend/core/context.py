"""Layer 8 — context accounting, compaction, and offload.

A prompt has exactly one section that grows without bound: the conversation. Everything else
(catalog hits, skill manifest, recipes, the context card) is retrieved by meaning and already
bounded. This module keeps the conversation bounded too:

- ``compact_turns`` summarizes the older turns (the chat model when a key is present, an
  extractive recap otherwise) and stores the summary *and* the raw transcript slice in
  ``agent_context_archive`` — the summary re-enters the prompt, the raw text does not.
- ``maybe_offload`` keeps a large tool result out of the window and leaves a reference to the
  archive row that still holds the full payload.
- ``trim_to_budget`` is the mid-run guard for tool-heavy runs: the oldest tool messages shrink to
  one-line references. Message structure is preserved, because the Chat Completions API rejects a
  conversation where an assistant ``tool_calls`` turn is not followed by its ``tool`` results.
- ``recall_context`` (the tool) searches those rows by meaning, which is why the archive is a
  vector table and not a log file.

Without this, a long session re-sends the whole transcript plus every full tool result and quality
falls off before the hard token limit (context rot).
"""
from __future__ import annotations

import re
import uuid

from backend.config import settings
from backend.core import db, memory
from backend.core.llm_client import MODEL, client

CHARS_PER_TOKEN = 4
COMPACT_CHUNK = 4          # turns per archived summary; small so compaction happens in a demo
KEEP_RECENT_MESSAGES = 4   # messages trim_to_budget never rewrites
SUMMARY_LIMIT_CHARS = 12000

_SUMMARY_SYSTEM = ("You compress conversation history for a retail-analytics agent. Keep decisions, "
                   "constraints, numbers, open questions, and anything the user asked to remember; "
                   "drop pleasantries and restatements. Plain text, 140 words maximum.")


# ── accounting ─────────────────────────────────────────────────────────────
def tokens(text) -> int:
    """Rough token estimate (~4 chars/token). The same divisor the UI has always shown."""
    n = len(str(text or ""))
    return max(1, n // CHARS_PER_TOKEN) if n else 0


def messages_tokens(messages) -> int:
    return sum(tokens(m.get("content")) for m in messages)


def section(name: str, label: str, text, note: str = "") -> dict:
    body = str(text or "")
    return {"name": name, "label": label, "chars": len(body), "tokens": tokens(body),
            "preview": body[:170].replace("\n", " "), "note": note}


def message_rows(messages, cap: int = 240) -> list[dict]:
    """Per-message token accounting for the live window panel (what actually goes to the model)."""
    rows = []
    for m in messages:
        body = str(m.get("content") or "")
        rows.append({"role": m.get("role"), "chars": len(body), "tokens": tokens(body),
                     "preview": body[:cap].replace("\n", " ")})
    return rows


def new_run_id() -> str:
    return uuid.uuid4().hex[:6]


def _short_ref(raw_hex: str) -> str:
    """Display id for an archive row. SYS_GUID() shares the first and the last 8 hex chars
    between rows minted in the same window (only the middle bytes vary), so the middle is the
    part worth showing — a prefix or suffix shortcut makes two rows look identical."""
    return str(raw_hex)[8:16]


# ── conversation history ───────────────────────────────────────────────────
def split_history(thread_id: str, keep: int) -> tuple[list[dict], list[dict]]:
    """Split the thread into (recent tail sent as messages, older turns compaction may archive)."""
    turns = []
    for row in memory.thread_messages(thread_id)[-400:]:
        role = str(row.get("MESSAGE_ROLE") or "").lower()
        content = str(row.get("CONTENT") or "")
        if not content:
            continue
        if role == "agent":          # OAMP stores agent replies as 'agent'
            role = "assistant"
        if role in ("user", "assistant"):
            turns.append({"role": role, "content": content})
    if keep <= 0 or len(turns) <= keep:
        return turns, []
    return turns[-keep:], turns[:-keep]


def _turn_rows(thread_id: str) -> list[dict]:
    db.ensure_context_archive()
    return db.q("""SELECT RAWTOHEX(id) AS id, seq_from, seq_to, summary
                   FROM agent_context_archive
                   WHERE thread_id=:t AND kind='turns' ORDER BY seq_to""", {"t": thread_id})


def _recap(rows) -> str:
    return "\n".join(f"[archived turns {r['SEQ_FROM']}-{r['SEQ_TO']}] {str(r['SUMMARY']).strip()}"
                     for r in rows)


def existing_recap(thread_id: str, older) -> tuple[str, dict | None]:
    """Read-only recount for the no-model preview: what is already archived, what would be."""
    if not older:
        return "", None
    boundary = (len(older) // COMPACT_CHUNK) * COMPACT_CHUNK
    if boundary <= 0:
        return "", {"scope": "turns", "archived_msgs": 0, "would_archive": 0}
    rows = _turn_rows(thread_id)
    done = max((r["SEQ_TO"] for r in rows), default=0)
    return _recap(rows), {"scope": "turns", "archived_msgs": done,
                          "would_archive": max(0, boundary - done)}


# ── summarization (model when available, extractive otherwise) ─────────────
def _extractive(text: str, limit: int = 600) -> str:
    """Fallback recap: the first line of each turn, so compaction works without a model key."""
    parts, size = [], 0
    for line in text.splitlines():
        line = re.sub(r"^\[(user|assistant)\]\s*", "", line.strip())
        if not line:
            continue
        parts.append(line[:200])
        size += len(parts[-1])
        if size >= limit:
            break
    return "extractive recap: " + " | ".join(parts)[:limit]


def summarize(text: str) -> tuple[str, str]:
    """(summary, method). A missing or failing model degrades to an extractive recap rather than
    failing the run — compaction is a context-engineering step, not the user's answer."""
    if settings.llm_api_key:
        try:
            resp = client.chat.completions.create(
                model=MODEL, max_tokens=320,
                messages=[{"role": "system", "content": _SUMMARY_SYSTEM},
                          {"role": "user", "content": text[:SUMMARY_LIMIT_CHARS]}])
            out = (resp.choices[0].message.content or "").strip()
            if out:
                return out, "llm"
        except Exception as e:
            print(f"[context] summary call failed ({str(e).splitlines()[0][:90]}); "
                  "using an extractive recap.")
    return _extractive(text), "extractive"


def compact_turns(thread_id: str, older) -> tuple[str, dict | None]:
    """Archive the next chunk of older turns and return the recap + what changed.

    Boundaries are multiples of COMPACT_CHUNK, so a rerun archives the same slice (the MERGE is
    keyed on the boundary) instead of summarizing the same turns on every message.
    """
    if not older:
        return "", None
    boundary = (len(older) // COMPACT_CHUNK) * COMPACT_CHUNK
    if boundary <= 0:
        return "", None
    rows = _turn_rows(thread_id)
    done = max((r["SEQ_TO"] for r in rows), default=0)
    if boundary <= done:
        return _recap(rows), {"scope": "turns", "archived_msgs": done, "archived_now": 0,
                              "method": "reused", "saved": 0, "before": 0, "after": 0}
    slice_ = older[done:boundary]
    body = "\n\n".join(f"[{t['role']}] {t['content']}" for t in slice_)
    raw = "\n\n".join(f"[{t['role']}] {t['content']}" for t in older[:boundary])
    summary, method = summarize(body)
    label = f"turns {done}-{boundary}"
    db.x(f'''MERGE INTO agent_context_archive d
        USING (SELECT :t AS thread_id, 'turns' AS kind, :seq AS source_seq FROM dual) s
        ON (d.thread_id=s.thread_id AND d.kind=s.kind AND d.source_seq=s.source_seq)
        WHEN MATCHED THEN UPDATE SET summary=:s, body=:b, body_chars=:c, seq_from=:f, seq_to=:e,
            embedding=VECTOR_EMBEDDING({db.EMB} USING :emb AS DATA)
        WHEN NOT MATCHED THEN INSERT (thread_id, kind, source_seq, label, seq_from, seq_to,
            summary, body, body_chars, embedding)
            VALUES (:t,'turns',:seq,:label,:f,:e,:s,:b,:c, VECTOR_EMBEDDING({db.EMB} USING :emb AS DATA))''',
         {"t": thread_id, "seq": f"turns:{done}-{boundary}", "label": label, "f": done, "e": boundary,
          "s": summary, "b": body, "c": len(body), "emb": summary})
    recap = _recap(_turn_rows(thread_id))
    return recap, {"scope": "turns", "archived_msgs": boundary, "archived_now": len(slice_),
                   "method": method, "seq_from": done, "seq_to": boundary, "label": label,
                   "summary": summary[:400], "before": tokens(raw), "after": tokens(recap),
                   "saved": max(0, tokens(raw) - tokens(recap))}


# ── offload ────────────────────────────────────────────────────────────────
def maybe_offload(thread_id: str, run_id: str, tool: str, payload: str) -> tuple[str, dict | None]:
    """Keep a large tool result out of the window; leave a reference to its archive row."""
    if len(payload) <= settings.offload_chars:
        return payload, None
    db.ensure_context_archive()
    raw_id = uuid.uuid4()
    ref = _short_ref(raw_id.hex)
    summary = f"{tool} returned {len(payload):,} chars: {payload[:200]}"
    seq = f"{tool}:{run_id}:{ref}"
    db.x(f'''INSERT INTO agent_context_archive
        (id, thread_id, kind, source_seq, label, summary, body, body_chars, embedding)
        VALUES (:id,:t,'tool_result',:seq,:label,:s,:b,:c,
                VECTOR_EMBEDDING({db.EMB} USING :emb AS DATA))''',
         {"id": raw_id.bytes, "t": thread_id, "seq": seq, "label": tool, "s": summary,
          "b": payload, "c": len(payload), "emb": summary})
    head = payload[:400].replace("\n", " ")
    content = (f"[offloaded from the window: {len(payload):,} chars are in agent_context_archive "
               f"row {ref} (tool {tool}). Search it by meaning with recall_context. Head: {head} …]")
    return content, {"tool": tool, "chars": len(payload), "tokens": tokens(payload), "ref": ref,
                     "before": tokens(payload), "after": tokens(content),
                     "saved": max(0, tokens(payload) - tokens(content))}


def trim_to_budget(messages, budget: int) -> dict | None:
    """Mid-run guard: shrink the oldest tool results (never drop them) when the run overshoots.

    Returns None when nothing could actually be shortened, so the stream does not fill with
    no-op compaction events (the gauge over 100% is then the honest signal).
    """
    before = messages_tokens(messages)
    if before <= budget:
        return None
    cutoff = len(messages) - KEEP_RECENT_MESSAGES
    for i, m in enumerate(messages):
        if i == 0 or i >= cutoff:
            continue
        role, content = m.get("role"), m.get("content")
        if role == "system" or not content:
            continue
        cap = 300 if role == "tool" else 800
        if len(content) > cap:
            m["content"] = (content[:cap] +
                            " …[compacted to keep the window inside budget; re-run the tool for the full result]")
    after = messages_tokens(messages)
    if after >= before:
        return None
    return {"scope": "runtime", "messages": len(messages), "before": before, "after": after,
            "saved": before - after}


# ── archive reads (the vector store side of offloading) ────────────────────
def archive_rows(thread_id: str, query: str | None = None, k: int = 8) -> list[dict]:
    db.ensure_context_archive()
    cols = ("""SELECT RAWTOHEX(id) AS ref, kind, label, source_seq, seq_from, seq_to,
               TO_CHAR(created_at,'HH24:MI:SS') AS at, body_chars,
               SUBSTR(summary,1,260) AS summary, SUBSTR(body,1,200) AS head""")
    if query:
        rows = db.q(f"""{cols},
                   VECTOR_DISTANCE(embedding, VECTOR_EMBEDDING({db.EMB} USING :q AS DATA), COSINE) AS dist
                   FROM agent_context_archive WHERE thread_id=:t
                   ORDER BY dist FETCH FIRST :k ROWS ONLY""", {"q": query, "t": thread_id, "k": k})
    else:
        rows = db.q(f"""{cols} FROM agent_context_archive WHERE thread_id=:t
                   ORDER BY created_at DESC FETCH FIRST :k ROWS ONLY""", {"t": thread_id, "k": k})
    for r in rows:
        r["REF"] = _short_ref(r["REF"])
        if r.get("DIST") is not None:
            r["DIST"] = round(float(r["DIST"]), 4)
    return rows


def recall_tool(query: str, k: int = 5) -> list[dict]:
    """The `recall_context` tool: search everything that ever left the window, by meaning."""
    db.ensure_context_archive()
    rows = db.q(f'''SELECT RAWTOHEX(id) AS ref, thread_id, kind, label,
                 TO_CHAR(created_at,'YYYY-MM-DD HH24:MI') AS at, body_chars,
                 SUBSTR(summary,1,300) AS summary, SUBSTR(body,1,240) AS head,
                 VECTOR_DISTANCE(embedding, VECTOR_EMBEDDING({db.EMB} USING :q AS DATA), COSINE) AS dist
                 FROM agent_context_archive ORDER BY dist FETCH FIRST :k ROWS ONLY''',
                {"q": query, "k": k})
    return [{"ref": _short_ref(r["REF"]), "kind": r["KIND"], "label": r["LABEL"], "thread_id": r["THREAD_ID"],
             "at": r["AT"], "chars": r["BODY_CHARS"], "dist": round(float(r["DIST"]), 4),
             "summary": r["SUMMARY"], "head": r["HEAD"]} for r in rows]
