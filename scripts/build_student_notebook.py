#!/usr/bin/env python3
"""Generate the *student* notebook + docs/todoN.md from total_recall_complete.ipynb.

Non-destructive: ``total_recall_complete.ipynb`` stays as the COMPLETE answer key. For each target
cell we emit, into ``total_recall_student.ipynb``:
  1. a markdown pointer cell  -> links to docs/todoN.md
  2. a placeholder code cell  -> raises NotImplementedError (the blank to fill)
  3. (the assert "check" cell that already follows the solution in the complete notebook is copied through)
The solution snippet written into each ``docs/todoN.md`` IS the real complete-cell source, so the
docs and the answer key can never drift. Re-run any time to regenerate.

The fifteen TODOs walk the harness bottom-up, one primitive at a time:
  substrate (fs write/read, grep) -> encoding (chunk, embed) -> retrieval (vector, RRF, rerank)
  -> continual learning (promote scratch -> long-term) -> tools (retrieve, safe SQL)
  -> skills (save, retrieve, promote a workflow, harvest) -> the agent loop
"""
from __future__ import annotations

import json
import pathlib
import re

WS = pathlib.Path(__file__).resolve().parent.parent
SRC = WS / "total_recall_complete.ipynb"
OUT = WS / "total_recall_student.ipynb"
DOCS = WS / "docs"
DOCS.mkdir(exist_ok=True)

POINTER = """\
### 🧩 TODO {num} — {title}

The implementation in the next cell has been removed. Open \
**[`docs/todo{num}.md`](docs/todo{num}.md)** for a guided explanation and the solution snippet, \
then replace the placeholder.

Run the **`✅ TODO {num} check`** cell right after — it must pass before you continue.\
"""

SPECS: list[dict] = [
    {
        "title": "The in-database scratch filesystem (write & read)",
        "locator": "class ScratchFS",
        "stub": """class ScratchFS:
    "A POSIX-like filesystem inside the database (one row per file, content in a SecureFile LOB)."
    def __init__(self, conn, mount="/scratch"):
        self.conn = conn
        self.mount = mount.rstrip("/")

    def _abs(self, path):
        path = "/" + path.strip("/")
        return path if path.startswith(self.mount) else self.mount + path

    def mkdir(self, path):
        p = self._abs(path)
        execute_sql(self.conn, '''MERGE INTO agent_scratch d USING (SELECT :p AS path FROM dual) s ON (d.path = s.path)
                        WHEN NOT MATCHED THEN INSERT (path, is_dir) VALUES (:p, 'Y')''', {"p": p})

    def write(self, path, content):
        # 🧩 TODO 1 — store the file in the database (UPSERT one row of agent_scratch). See docs/todo1.md
        raise NotImplementedError("Complete TODO 1 — open docs/todo1.md")

    def append(self, path, content):
        self.write(path, (self.read(path) if self.exists(path) else "") + content)

    def read(self, path):
        # 🧩 TODO 1 — read the file's content back out of the database. See docs/todo1.md
        raise NotImplementedError("Complete TODO 1 — open docs/todo1.md")

    def exists(self, path):
        return bool(fetch_rows(self.conn, "SELECT 1 FROM agent_scratch WHERE path = :p", {"p": self._abs(path)}))

    def list(self, path="/"):
        pre = self._abs(path).rstrip("/") + "/%"
        rows = fetch_rows(self.conn, "SELECT path FROM agent_scratch WHERE path LIKE :pre AND is_dir = 'N' ORDER BY path",
                          {"pre": pre})
        return [r["PATH"] for r in rows]

print("ScratchFS class ready.")
""",
        "teach": """\
![The Oracle Database file system — a filesystem agents already speak, backed by the database](../images/filesystem_database.png)

Before memory gets clever, an agent needs a **substrate** — somewhere to put working notes, partial
results, and scratch files. The classic choice is the OS filesystem; here we put it **inside the
database**, one row per file with the content in a SecureFile LOB. That single move buys us ACID
writes, transactional visibility, and (later) a promotion path straight into long-term memory — none
of which a loose file on disk gives you.

### What to implement
Fill in two methods of `ScratchFS` (keep `__init__`, `_abs`, `mkdir`, `append`, `exists`, `list`):
- `write(self, path, content)` — UPSERT one row. Compute `p = self._abs(path)`, encode text to bytes
  (`content.encode("utf-8") if isinstance(content, str) else content`), then run a `MERGE INTO
  agent_scratch … WHEN MATCHED THEN UPDATE SET content=:c, is_dir='N', promoted='N',
  updated_at=SYSTIMESTAMP WHEN NOT MATCHED THEN INSERT (path, content) VALUES (:p, :c)`.
- `read(self, path)` — `fetch_rows(self.conn, "SELECT content FROM agent_scratch WHERE path = :p",
  {"p": self._abs(path)})`; raise `FileNotFoundError` if empty; otherwise decode the BLOB to text
  (`b.decode("utf-8", errors="replace") if isinstance(b, (bytes, bytearray)) else (b or "")`).

> 💡 `MERGE` is the upsert: one statement that updates the row if the path exists and inserts it if
> not — so `write()` is idempotent and never throws a duplicate-key error.""",
    },
    {
        "title": "Grep the agent's scratch memory",
        "locator": "def grep_files",
        "stub": '''def write_file(path, content):
    """Agent tool: create or overwrite a scratch file (the agent's short-term memory)."""
    fs.write(path, content)
    return {"written": fs._abs(path), "bytes": len(content)}

def append_file(path, content):
    """Agent tool: append to a scratch file, creating it if needed."""
    fs.append(path, content)
    return {"appended": fs._abs(path)}

def read_file(path):
    return fs.read(path)

def tail_file(path, n=20):
    return "\\n".join(fs.read(path).splitlines()[-n:])

def grep_files(pattern, root="/", ignorecase=True):
    # 🧩 TODO 2 — search every scratch file line-by-line for a regex; return {path, line, text} hits. See docs/todo2.md
    raise NotImplementedError("Complete TODO 2 — open docs/todo2.md")

def list_files(root="/"):
    return fs.list(root)

print("File tools ready: write_file, append_file, read_file, tail_file, grep_files, list_files")
''',
        "teach": """\
A scratch filesystem is only useful if the agent can **search** it. `grep_files` is the agent-facing
tool that scans every scratch file line by line for a pattern — the same `grep` reflex you have at a
shell, but over memory that lives in the database. It builds directly on the `ScratchFS` you just
wrote (`fs.list` to enumerate, `fs.read` to open).

### What to implement
Fill in `grep_files(pattern, root="/", ignorecase=True)` (keep the other file tools):
1. `import re`; compile the pattern: `rx = re.compile(pattern, re.IGNORECASE if ignorecase else 0)`.
2. For each `p` in `fs.list(root)`, read it and walk the lines with `enumerate(fs.read(p).splitlines(),
   1)` (1-based). When `rx.search(line)` matches, append `{"path": p, "line": i, "text":
   line.strip()[:200]}`.
3. Wrap each file in `try/except` and `continue` past unreadable ones. Return the list of hits.

> 💡 Tools the agent calls are just Python functions returning plain data — here a list of
> match dicts. Searching memory is itself a tool: the agent greps its own notes the way you grep a
> codebase.""",
    },
    {
        "title": "Chunk text into overlapping windows",
        "locator": "def chunk_words",
        "stub": '''def chunk_words(text, size=200, overlap=20):
    """Split text into overlapping word windows (one idea per chunk)."""
    # 🧩 TODO 3 — split text into overlapping word windows for embedding. See docs/todo3.md
    raise NotImplementedError("Complete TODO 3 — open docs/todo3.md")
''',
        "teach": """\
You cannot embed a 50-page document as one vector — meaning would smear into mush, and retrieval would hand
back the whole document instead of the relevant passage. So you **chunk**: split into windows small enough to
carry one idea. Good chunking is what makes retrieval **precise** (you get the paragraph, not the book), keeps
each embedding **high-signal**, and respects the model's **context budget** on the way back in.

### Chunking techniques (a quick map)
- **Fixed-size by words/tokens, with overlap** — what we build here. Simple, predictable, model-agnostic; the
  overlap stops an idea that straddles a boundary from being lost. The honest default.
- **Sentence / paragraph** — split on natural boundaries so a chunk is a whole thought. Better coherence,
  variable size.
- **Recursive** — try paragraphs, then sentences, then words, splitting further only when a piece is too big.
  The common production default (e.g. LangChain's `RecursiveCharacterTextSplitter`).
- **Semantic** — start a new chunk where the embedding *meaning* shifts. Highest quality, highest cost.
- **Structure-aware** — split on Markdown headings, code fences, or table rows when the document has structure.

We use fixed-size word windows with overlap because it is the clearest teaching artifact and needs no extra
dependencies — the *technique* matters more than the splitter.

### What to implement
Fill in `chunk_words(text, size=200, overlap=20)`:
1. `words = text.split()`. If `len(words) <= size`, return `[text]` (nothing to split).
2. Otherwise walk a sliding window: starting at `i = 0`, append `" ".join(words[i:i + size])`, then
   step `i += size - overlap`, until `i >= len(words)`. Return the list.

> 💡 The step is `size - overlap`, **not** `size` — that is what makes consecutive windows share
> their last/first `overlap` words. Set `overlap = 0` and you get hard cuts; a small overlap keeps
> ideas that cross a boundary retrievable from either chunk.""",
    },
    {
        "title": "Semantic (vector) retrieval",
        "locator": "def vector_search",
        "stub": '''from langchain_oracledb.retrievers.text_search import OracleTextSearchRetriever

# Keyword retriever: Oracle Text CONTAINS search, provided by langchain_oracledb (no hand-written SQL).
# Built once over the AGENT_VSTORE text index and reused by the ladder. We pull a wide pool (k=30) and
# narrow by namespace in Python, so one retriever serves every namespace.
_keyword_retriever = OracleTextSearchRetriever(vector_store=store, k=30, fuzzy=True, return_scores=True)

def _as_row(doc, score):
    """Adapt a langchain Document to the dict-row shape the rest of the ladder already uses."""
    return {"ID": doc.id, "TEXT": doc.page_content, "META": doc.metadata, "score": score}

def keyword_search(query, namespace=None, k=5):
    docs = _keyword_retriever.invoke(query)
    if namespace:
        docs = [d for d in docs if d.metadata.get("namespace") == namespace]
    return [_as_row(d, d.metadata.get("score")) for d in docs[:k]]

def vector_search(query, namespace=None, k=5):
    # 🧩 TODO 4 — semantic retrieval: rank the store by cosine distance to the query. See docs/todo4.md
    raise NotImplementedError("Complete TODO 4 — open docs/todo4.md")

print("Rungs 1-2 ready: keyword_search (OracleTextSearchRetriever) + vector_search (similarity_search_with_score).")
''',
        "teach": """\
Rung 2 of the retrieval ladder: **vector search**. Keyword search (rung 1, written for you) finds
exact terms; vector search finds **meaning** — "revenue growth" should surface a note about "the
Outdoors category driving Q3 sales" even with no shared words. The `OracleVectorStore` does the
embedding and the cosine ranking; you just call it and adapt the result to the ladder's row shape.

### What to implement
Fill in `vector_search(query, namespace=None, k=5)` (keep `keyword_search` and `_as_row`):
1. Build an optional metadata filter: `flt = {"namespace": namespace} if namespace else None`.
2. `pairs = store.similarity_search_with_score(query, k=k, filter=flt)` — each pair is
   `(Document, cosine_distance)` where **smaller distance = closer in meaning**.
3. Return `[_as_row(d, dist) for d, dist in pairs]`.

> 💡 `similarity_search_with_score` embeds the query with the **same** in-database model used to embed
> the documents — that shared model is what makes the distances meaningful. The `_as_row` adapter
> keeps vector and keyword hits in one shape so the next rung (RRF) can fuse them.""",
    },
    {
        "title": "Fuse keyword + vector results with Reciprocal Rank Fusion",
        "locator": "def reciprocal_rank_fusion",
        "stub": '''def reciprocal_rank_fusion(ranked_lists, c=60):
    """Merge several ranked lists into one. Each row earns 1/(c+rank) from every list it appears in, so rows
    found by more than one method add up and rise. Returns rows sorted best-first, each tagged with 'rrf'."""
    # 🧩 TODO 5 — fuse the ranked lists with reciprocal rank fusion. See docs/todo5.md
    raise NotImplementedError("Complete TODO 5 — open docs/todo5.md")

def hybrid_search(query, namespace=None, k=5, pool=20):
    """Run keyword + vector over a candidate pool, then fuse with RRF and keep the top k."""
    vector_hits = vector_search(query, namespace, pool)
    keyword_hits = keyword_search(query, namespace, pool)
    return reciprocal_rank_fusion([vector_hits, keyword_hits])[:k]

print("Rung 4 ready: hybrid_search, built on a reusable reciprocal_rank_fusion helper.")
''',
        "teach": """\
Now you have two rankings that disagree — keyword (exact terms) and vector (meaning) — and need *one*
list. The trick is **Reciprocal Rank Fusion (RRF)**: ignore the raw, incomparable scores and use only
each row's **rank** in each list. A row earns `1/(c + rank)` from every list it appears in, and those
contributions **add up** — so anything found by *both* methods rises to the top.

### What to implement
Fill in `reciprocal_rank_fusion(ranked_lists, c=60)` (keep `hybrid_search` below it):
1. Walk every list; for each row at position `rank` (0-based), add `1.0 / (c + rank + 1)` to that
   row's running score, keyed by `row["ID"]`. Keep a `row_by_id` map so you can return the full row.
2. Sort the IDs by fused score, **highest first**.
3. Return the rows in that order, each tagged with its score:
   `dict(row_by_id[rid], rrf=round(score, 5))`.

> 💡 RRF needs no training and no score normalisation — it only trusts *ordering*. That is why it
> fuses a cosine-distance list and a keyword-relevance list, whose scores are on totally different
> scales, without either one drowning out the other.""",
    },
    {
        "title": "Rerank the shortlist with a cross-encoder",
        "locator": "def rerank",
        "stub": '''def rerank(query, candidates, k=5):
    """Cross-encoder rescoring via the in-DB reranker. Tags each row with 'rerank_score' (higher = more
    relevant) and returns them reordered by it, or the input order if the reranker is unavailable."""
    # 🧩 TODO 6 — rescore with the in-DB cross-encoder; fall back to input order if it is unavailable. See docs/todo6.md
    raise NotImplementedError("Complete TODO 6 — open docs/todo6.md")

print("Rung 5 ready: rerank (raw cross-encoder score; higher = more relevant).")
''',
        "teach": """\
The last rung. RRF gives a good shortlist, but it still only knows *ranks*. A **cross-encoder** reads
the query and each candidate **together** and scores true relevance — the most accurate signal, too
expensive to run over the whole corpus, perfect for re-ordering a shortlist of ~20. Here it runs
**inside the database** via `PREDICTION(...)`. Crucially, it must **degrade gracefully**: if the
reranker model is not loaded (this workshop loads only the embedder by default), fall back to the
order you were given.

### What to implement
Fill in `rerank(query, candidates, k=5)`:
1. **Fallback first:** `if not RERANK_AVAILABLE or not candidates: return [dict(c, rerank_score=None)
   for c in candidates[:k]]`.
2. Otherwise score each candidate in one query: build `docs = [str(c["TEXT"])[:2000] for c in
   candidates]`, then `fetch_rows(conn, "SELECT t.idx AS idx, PREDICTION({CFG['RERANK_MODEL']} USING
   (:q || ' [SEP] ' || t.doc) AS DATA) AS score FROM JSON_TABLE(:docs, '$[*]' COLUMNS (idx FOR
   ORDINALITY, doc VARCHAR2(4000) PATH '$')) t", {"q": query, "docs": json.dumps(docs)})`.
3. Attach scores, sort by `rerank_score` **descending**, return the top `k`.

> 💡 The retrieval ladder is now complete — keyword → vector → **RRF** → **rerank**. Each rung trades
> a little more compute for a little more precision; the graceful fallback means the ladder always
> returns *something* useful even when a rung is missing.""",
    },
    {
        "title": "Embed text inside the database",
        "locator": "class InDBOnnxEmbedder",
        "stub": '''import numpy as np
from oracleagentmemory.core import OracleAgentMemory
from oracleagentmemory.core.llms import Llm
from oracleagentmemory.apis.embedders.embedder import IEmbedder

class InDBOnnxEmbedder(IEmbedder):
    """An OAMP embedder that computes vectors with the in-database ONNX model (no external service)."""
    def __init__(self, conn, model):
        self.conn = conn; self.model = model
    def embed(self, texts, *, is_query=False):
        # 🧩 TODO 7 — embed each text to a 384-dim vector INSIDE Oracle via VECTOR_EMBEDDING. See docs/todo7.md
        raise NotImplementedError("Complete TODO 7 — open docs/todo7.md")
    async def embed_async(self, texts, *, is_query=False):
        return self.embed(texts, is_query=is_query)

print("InDBOnnxEmbedder ready.")
''',
        "teach": """\
OAMP (the cognitive-memory layer you are about to build on) needs an **embedder**. Instead of calling
an external API or loading PyTorch, the embedding is computed **inside Oracle** by the ONNX model you
loaded in Part 1 — from Python it is one SQL call. We wrap it as an OAMP `IEmbedder` so the whole
memory system runs against the in-database model.

### What to implement
Fill in `InDBOnnxEmbedder.embed(self, texts, *, is_query=False)` (keep `__init__` and `embed_async`):
1. Build a list `out = []`.
2. For each `t` in `texts`, run one query and collect the vector:
   `fetch_rows(self.conn, f"SELECT VECTOR_EMBEDDING({self.model} USING :t AS DATA) v FROM dual", {"t": t})`
   then append `list(r[0]["V"])`.
3. Return `np.array(out, dtype=np.float32)` — a `(len(texts), 384)` matrix.

> 💡 The *same* model embeds both documents and queries — that is what makes their vectors
> comparable. Mixing models is the classic vector-search bug. (`embed_async` just calls `embed`,
> so once this works the async path works too.)""",
    },
    {
        "title": "Promote scratch files into long-term memory",
        "locator": "def drain_promotion_queue_to_oamp",
        "stub": '''def drain_promotion_queue_to_oamp(limit=500):
    # 🧩 TODO 8 — consume staged chunks into OAMP long-term memory and mark them done. See docs/todo8.md
    raise NotImplementedError("Complete TODO 8 — open docs/todo8.md")

def promote_file_to_memory(path):
    execute_sql(conn, "BEGIN stage_scratch_for_promotion; END;")   # stage everything pending (incl. this file)
    n = drain_promotion_queue_to_oamp()
    return {"promoted_chunks": n, "path": path}

print("Consumer ready: drain_promotion_queue_to_oamp + promote_file_to_memory.")
''',
        "teach": """\
Scratch notes are short-term. **Continual learning** starts when the agent *promotes* them into
long-term memory: a scheduled in-database **producer** (`stage_scratch_for_promotion`, written for
you) chunks pending files into a `promotion_queue`; your job is the **consumer** that drains that
queue into OAMP. This is the bridge from a working file on the scratchpad to a durable, recallable
memory the agent can use in any future session.

### What to implement
Fill in `drain_promotion_queue_to_oamp(limit=500)` (keep `promote_file_to_memory`, which stages then
calls you):
1. Read unconsumed rows: `fetch_rows(conn, "SELECT id, path, chunk FROM promotion_queue WHERE
   consumed='N' FETCH FIRST :k ROWS ONLY", {"k": limit})`.
2. For each row, write it to long-term memory and mark it done: `mem.remember(r["CHUNK"])` (OAMP
   `add_memory`, with fact-extraction), then `execute_sql(conn, "UPDATE promotion_queue SET
   consumed='Y' WHERE id=:i", {"i": r["ID"]})`.
3. Mark each distinct source file promoted: for `p` in `{r["PATH"] for r in rows}`,
   `UPDATE agent_scratch SET promoted='Y' WHERE path=:p`. Return `len(rows)`.

> 💡 Producer/consumer split: the producer runs on a `DBMS_SCHEDULER` job (hourly), the consumer is
> this function. Decoupling them is what lets promotion happen continuously in the background — the
> agent keeps learning from its own scratch work without you orchestrating it.""",
    },
    {
        "title": "Make tools retrievable by meaning",
        "locator": "def retrieve_tools",
        "stub": '''def retrieve_tools(query, k=6):
    # 🧩 TODO 9 — HNSW search over the toolbox: rank tools by cosine distance to the query. See docs/todo9.md
    raise NotImplementedError("Complete TODO 9 — open docs/todo9.md")

print("retrieve_tools (HNSW search over the toolbox) ready.")
''',
        "teach": """\
An agent with 50 tools cannot put all 50 schemas in every prompt — that is wasted context and worse
decisions. Instead we store each tool's **JSON schema + an embedding of its description** in the
`agent_tools` table, indexed with **HNSW**, and retrieve only the handful relevant to the current
request. This is *just-in-time tools*: the toolbox is memory, searched by meaning.

### What to implement
Fill in `retrieve_tools(query, k=6)` — one SQL query against `agent_tools`:
1. Embed the query **in the database** and rank by cosine distance:
   `VECTOR_DISTANCE(embedding, VECTOR_EMBEDDING({CFG['EMBED_MODEL']} USING :q AS DATA), COSINE) dist`.
2. Select `name, description, tool_schema, ... dist`, `ORDER BY dist`, and
   `FETCH APPROX FIRST :k ROWS ONLY` (the `APPROX` keyword tells Oracle to use the HNSW index).
3. Return the rows via `fetch_rows(conn, sql, {"q": query, "k": k})`.

> 💡 `FETCH APPROX FIRST … ROWS ONLY` is what turns a brute-force distance scan into an
> index-accelerated nearest-neighbour search. The same one-line pattern powers tool, skill, and
> workflow retrieval throughout the harness.""",
    },
    {
        "title": "A safe, read-only SQL tool",
        "locator": "def run_sql",
        "stub": '''import re as _re

def list_sources():
    rows = fetch_rows(conn, "SELECT table_name FROM user_tables WHERE table_name IN "
                            "('CUSTOMERS','PRODUCTS','ORDERS','ORDER_ITEMS') ORDER BY table_name")
    return [r["TABLE_NAME"] for r in rows] + ["V_REVENUE (view)"]

_FORBIDDEN = _re.compile(r"\\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|MERGE|GRANT|CREATE)\\b", _re.I)
def run_sql(sql, max_rows=200):
    # 🧩 TODO 10 — make this a SAFE, read-only tool: ONE SELECT/WITH statement, no write/DDL, <= max_rows rows. See docs/todo10.md
    raise NotImplementedError("Complete TODO 10 — open docs/todo10.md")

def author_materialized_view(name, select_sql):
    chk = run_sql(select_sql, max_rows=1)
    if "error" in chk:
        return {"error": "select failed: " + chk["error"]}
    ddl_idempotent(conn, f"DROP MATERIALIZED VIEW {name}")
    ddl_idempotent(conn, f"CREATE MATERIALIZED VIEW {name} BUILD IMMEDIATE REFRESH COMPLETE ON DEMAND AS {select_sql}")
    return {"created": name}

print("Domain tools ready: list_sources, run_sql (read-only), author_materialized_view.")
''',
        "teach": """\
A **tool** is how the agent acts on the world: a plain Python function the model can call. `run_sql`
lets the agent *explore* the warehouse and *validate* a query before turning it into a materialized
view — but it must be **read-only**, so a guard rejects anything that is not a single, safe `SELECT`.
State only ever changes through the deliberate, audited `author_materialized_view` path.

### What to implement
Fill in `run_sql(sql, max_rows=200)` (keep `list_sources`, the `_FORBIDDEN` regex, and
`author_materialized_view`):
1. **One statement only** — reject if there is a `;` inside the trimmed body:
   `if ";" in sql.strip().rstrip(";"): return {"error": "one statement only"}`.
2. **SELECT / WITH only** — `if not sql.strip().upper().startswith(("SELECT", "WITH")): return {"error": "SELECT/WITH only"}`.
3. **No write/DDL keyword** — `if _FORBIDDEN.search(sql): return {"error": "write/DDL keyword rejected ..."}`.
4. **Run it** and cap the rows: `try: return {"rows": fetch_rows(conn, sql)[:max_rows]}` /
   `except Exception as e: return {"error": str(e).splitlines()[0]}`.

> 💡 Tools return plain data (here a dict) — that is what gets fed back to the model as the tool
> result. The read-only guard is a guardrail you hand the agent; the database's own privileges are
> the real boundary behind it.""",
    },
    {
        "title": "Save a SHA-versioned skill",
        "locator": "def save_skill",
        "stub": """create_table(conn, f'''CREATE TABLE agent_skills (
  name VARCHAR2(120) PRIMARY KEY, description VARCHAR2(600),
  sha VARCHAR2(64), source_url VARCHAR2(600),
  skill_md CLOB, tools_used VARCHAR2(600), source_workflow_id RAW(16),
  embedding VECTOR({CFG['VECTOR_DIM']}, FLOAT32),
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP, updated_at TIMESTAMP DEFAULT SYSTIMESTAMP)''')
create_hnsw_index("agent_skills")

def save_skill(name, description, skill_md, tools_used, source_workflow_id=None, source_url=None):
    # 🧩 TODO 11 — persist a SHA-versioned, embedded skill (UPSERT into agent_skills). See docs/todo11.md
    raise NotImplementedError("Complete TODO 11 — open docs/todo11.md")

print("Skillbox ready: agent_skills table + save_skill (SHA-versioned).")
""",
        "teach": """\
A **skill** is a `SKILL.md` playbook the agent can reuse — *procedural memory*. `save_skill` is the
write primitive: it stores the skill text, an **embedding** of it (so it is retrievable by meaning),
and a **SHA** of the body — the fingerprint that later lets `refresh_skills_from_source` notice when
an externally-sourced skill has changed. It is an UPSERT, so saving the same name twice updates it in
place rather than erroring.

### What to implement
Fill in `save_skill(name, description, skill_md, tools_used, source_workflow_id=None, source_url=None)`
(keep the table DDL and `create_hnsw_index` above it):
1. `sha = hashlib.sha256(skill_md.encode("utf-8")).hexdigest()`.
2. `MERGE INTO agent_skills … WHEN MATCHED THEN UPDATE SET description=:d, sha=:sha, source_url=:url,
   skill_md=:b, tools_used=:t, source_workflow_id=:w, updated_at=SYSTIMESTAMP, embedding =
   VECTOR_EMBEDDING({CFG['EMBED_MODEL']} USING :emb AS DATA) WHEN NOT MATCHED THEN INSERT (…) VALUES
   (…, VECTOR_EMBEDDING({CFG['EMBED_MODEL']} USING :emb AS DATA))`, binding `t=",".join(tools_used or
   [])` and `emb=f"{name}: {description}"`.
3. `return sha`.

> 💡 Embedding `f"{name}: {description}"` (not the whole body) keeps the skill's *index* about what it
> is *for* — so `retrieve_skills` matches on task intent, while the full `skill_md` is fetched only
> when the agent commits to using it.""",
    },
    {
        "title": "Skills as searchable memory",
        "locator": "def retrieve_skills",
        "stub": '''def retrieve_skills(query, k=5):                   # level 1: HNSW over the skillbox
    # 🧩 TODO 12 — rank skills by cosine distance to the query (HNSW over agent_skills). See docs/todo12.md
    raise NotImplementedError("Complete TODO 12 — open docs/todo12.md")

def build_skill_manifest(query, k=5):
    rows = retrieve_skills(query, k=k)
    return "\\n".join(f"- {r['NAME']}: {r['DESCRIPTION']}" for r in rows) or "(no skills yet)"

def load_skill(name):                              # level 2: the full SKILL.md body, on demand
    # 🧩 TODO 12 — load a named skill's full SKILL.md body + metadata from agent_skills. See docs/todo12.md
    raise NotImplementedError("Complete TODO 12 — open docs/todo12.md")

print("Skill retrieval ready: manifest (L1) + load_skill (L2).")
''',
        "teach": """\
**Skills** are read in two levels: a cheap **manifest** (name + description for the few relevant
skills) that always rides in context, and the **full body**, loaded on demand only when the agent
commits to using one. Same HNSW-over-a-registry pattern as the toolbox — and the same `save_skill`
write side you just built feeds it.

### What to implement
Fill in two functions (keep `build_skill_manifest` between them):
- `retrieve_skills(query, k=5)` — level 1, like `retrieve_tools` but over `agent_skills`: select
  `name, description, VECTOR_DISTANCE(embedding, VECTOR_EMBEDDING({CFG['EMBED_MODEL']} USING :q AS DATA), COSINE) dist`,
  `ORDER BY dist FETCH APPROX FIRST :k ROWS ONLY`; return `fetch_rows(conn, sql, {"q": query, "k": k})`.
- `load_skill(name)` — level 2: `fetch_rows(conn, "SELECT name, description, skill_md, tools_used, sha,
  source_url FROM agent_skills WHERE name=:n", {"n": name})`; return `r[0]` if found, else
  `{"error": "no such skill"}`.

> 💡 Two levels = context economy. The manifest is small enough to always carry; the full playbook
> (which can be long) is fetched only when needed. The agent reads *that it has* a skill before it
> pays to read the skill.""",
    },
    {
        "title": "Promote a workflow into a skill (continual learning in token space)",
        "locator": "def promote_workflow_to_skill",
        "stub": '''def promote_workflow_to_skill(workflow_id):
    # 🧩 TODO 13 — distil an executed workflow into a reusable SKILL.md, save it, retire the workflow. See docs/todo13.md
    raise NotImplementedError("Complete TODO 13 — open docs/todo13.md")

print("promote_workflow_to_skill ready (distils to SKILL.md, marks the workflow promoted).")
''',
        "teach": """\
![Distilling a workflow recipe into a reusable, higher-weight skill](../images/workflow_to_skill.png)

This is **continual learning in token space**: the agent did a task (a captured **workflow**), and
now it distils that trajectory into a reusable **skill**. The model rewrites the workflow's raw steps
into a parameterised `SKILL.md`; you save it and mark the workflow *promoted* so it stops showing up
as raw procedural memory. Next time a similar task arrives, the agent loads the polished skill instead
of re-deriving the steps from scratch.

### What to implement
Fill in `promote_workflow_to_skill(workflow_id)`:
1. Fetch the workflow (`SELECT id, intent, steps, tools_used FROM agent_workflow WHERE id=:i`); return
   `{"error": "no such workflow"}` if missing. Split `tools = [t for t in (wf["TOOLS_USED"] or
   "").split(",") if t]`.
2. Ask the model to distil it: build a prompt that returns JSON `{"name", "description", "body"}`,
   `llm.invoke([HumanMessage(content=prompt)]).content`, and parse the JSON slice. Wrap it in
   `try/except` with a **fallback** (derive `name` from the intent, `body = str(wf["STEPS"])`) so it
   still works offline.
3. `skill_md = _skill_md(name, desc, tools, body)`; `save_skill(name, desc, skill_md, tools,
   source_workflow_id=wf["ID"])`; `UPDATE agent_workflow SET promoted='Y' WHERE id=:i`. Return
   `{"promoted_skill": name, "skill_md": skill_md}`.

> 💡 The fallback matters: a learning step that *requires* a model call is fragile. Distil with the
> model when you can, degrade to the raw steps when you can't — either way the skill gets saved.""",
    },
    {
        "title": "Harvest recurring workflows into skills",
        "locator": "def harvest_skills",
        "stub": '''HARVEST_KNOBS = {"min_occurrences": 3, "recency_days": 30}

def harvest_skills():
    # 🧩 TODO 14 — promote only recurring, recent, reliable workflows into skills. See docs/todo14.md
    raise NotImplementedError("Complete TODO 14 — open docs/todo14.md")

print("Harvester ready: harvest_skills() promotes only recurring, reliable workflows.")
''',
        "teach": """\
The agent should not promote *every* one-off into a skill — only the patterns that **repeat** and
**work**. `harvest_skills` is that policy, and it is the engine of continual learning: it selects
workflows that are recurring (`occurrences >= min`), recent, and reliable (`successes > failures`),
and promotes each via the function you just wrote. Run it on a schedule and the agent grows its own
skill library from lived experience — no human curation.

### What to implement
Fill in `harvest_skills()` (keep `HARVEST_KNOBS` above it):
1. Select the qualifying workflows: `SELECT id FROM agent_workflow WHERE promoted='N' AND occurrences
   >= :m AND last_seen >= SYSTIMESTAMP - :d AND successes > failures ORDER BY successes DESC,
   occurrences DESC`, binding `m=HARVEST_KNOBS["min_occurrences"]`, `d=HARVEST_KNOBS["recency_days"]`.
2. Promote each and collect the names:
   `return [promote_workflow_to_skill(c["ID"]).get("promoted_skill") for c in candidates]`.

> 💡 The thresholds are the *governance* of learning. `occurrences >= 3` ignores flukes;
> `successes > failures` ignores patterns that don't actually work; the recency window forgets stale
> habits. Continual learning without a policy like this just accumulates noise.""",
    },
    {
        "title": "The agent loop",
        "locator": "def run_agent",
        "stub": '''def run_agent(text, thread_id="main", stream=False):
    # 🧩 TODO 15 — the agent loop: record the turn, assemble the context card, run the graph, then capture
    # the outcome. See docs/todo15.md
    raise NotImplementedError("Complete TODO 15 — open docs/todo15.md")

print("Agent graph compiled. run_agent records trajectory outcomes.")
''',
        "teach": """\
The capstone. An agent is not one model call — it is a **loop**, and `run_agent` is it. It ties
together everything you built: it **records** the user turn, **assembles** working memory (the OAMP
**context card**), runs the compiled LangGraph **state graph** (assemble context → call model → run
tools → persist), and then **closes the learning loop** by capturing whether the trajectory
succeeded as a reusable workflow — the very workflows the later promote/harvest TODOs turn into skills.

### What to implement
Fill in `run_agent(text, thread_id="main", stream=False)`:
1. **Record + perceive** — `mem.add_turn(thread_id, "user", text)`, then
   `card = mem.context_card(thread_id) or ""` (OAMP working memory, computed once per turn).
2. **Seed the state** — `state = {"messages": [HumanMessage(content=text)], "thread_id": thread_id,
   "iterations": 0, "started_at": _time.time(), "card": card}` and
   `cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}`.
3. **Run the graph** — `out = graph.invoke(state, cfg)` (or iterate `graph.stream(state, cfg)` when
   `stream=True`).
4. **Close the loop** — pull the final answer
   (`next((m.content for m in reversed(out["messages"]) if isinstance(m, AIMessage) and m.content), "")`),
   collect the tools used from the messages' `tool_calls`, call
   `mem.capture_workflow(text, [...], tools_used, success=bool(final))`, and `return out`.

The full reference is below — read it, then build the loop yourself.

> 💡 This one function is the entire "agent framework." No black box: you own context assembly
> (*context engineering*), tool retrieval, the model call, durable graph state, and the learning
> feedback that makes the agent *get better* with use.""",
    },
    {  # fill-in-the-blank TODO (numbered by cell position at build time)
        "title": "Talk to the reasoning core",
        "locator": "answer = llm.invoke([HumanMessage(content=QUESTION)])",
        "stub": """from langchain_core.messages import HumanMessage

# 🧩 TODO %%NUM%% — set QUESTION to anything you want to ask the bare model, then run this cell.
# (No memory, no tools, no database yet — just the reasoning core.) See docs/todo%%NUM%%.md
QUESTION = ""

try:
    answer = llm.invoke([HumanMessage(content=QUESTION)]).content
    print("Q:", QUESTION)
    print("A:", answer)
except Exception as e:
    print("Model call failed:", str(e).splitlines()[0])
    print("Make sure OCI_GENAI_API_KEY is set (see 1.3 above).")
""",
        "teach": """\
The model is the agent's **reasoning core** — the one part that actually *reasons*. Everything else in this
workshop (memory, retrieval, tools, the loop) is **harness** built around it. Before adding any of that, it is
worth feeling the raw core directly: one prompt in, one answer out, no memory and no tools.

### What to implement
Set `QUESTION` to any non-empty string and run the cell — that's the whole task; the check just confirms you
asked *something*. Try a few:
- "Explain reciprocal rank fusion to a new engineer in three sentences."
- "What's the difference between short-term and long-term memory for an agent?"
- "Write a one-line Oracle SQL that counts the rows in a table called ORDERS."

> 💡 Notice what the bare model **can't** do: it has no memory of earlier turns and can't touch your data.
> That gap is exactly what the harness fills — and what the rest of this notebook builds.""",
    },
    {
        "title": "Design the scratch table (SecureFile LOB)",
        "locator": "CREATE TABLE agent_scratch",
        "stub": """# 🧩 TODO %%NUM%% — complete the CREATE TABLE so the filesystem has a place to store files.
# Give agent_scratch: a BLOB 'content' column (the file body), 'is_dir' and 'promoted' CHAR(1) flags
# (DEFAULT 'N'), an 'updated_at' TIMESTAMP, and store the content LOB AS SECUREFILE. See docs/todo%%NUM%%.md
SCRATCH_DDL = '''CREATE TABLE agent_scratch (
  path        VARCHAR2(400) PRIMARY KEY
  -- ...add the remaining columns and the SECUREFILE LOB clause here...
)'''
create_table(conn, SCRATCH_DDL)
print("agent_scratch table ready (SecureFile LOB content; one row per file).")
""",
        "teach": """\
The agent's scratch filesystem (next section) needs a table to live in, and every file is **one row**: a
`path` primary key and the file's bytes in a `content` **BLOB**, stored as a **SecureFile LOB** (Oracle's
modern LOB storage — fast, compressible, fully transactional). Two flags ride along — `is_dir` (a directory
marker) and `promoted` (set once a note graduates to long-term memory in Part 4) — plus an `updated_at` stamp.

### What to implement
Complete `SCRATCH_DDL` so `agent_scratch` has, besides `path`:
- `content BLOB` — the file body.
- `is_dir CHAR(1) DEFAULT 'N'` and `promoted CHAR(1) DEFAULT 'N'` — single-char flags.
- `updated_at TIMESTAMP DEFAULT SYSTIMESTAMP`.
- a `LOB (content) STORE AS SECUREFILE` clause after the column list.

> 💡 Putting files in a table (not on disk) is the whole point: they become **ACID, backed up, access-
> controlled, and searchable** — and a row can later be *promoted* straight into long-term memory.""",
    },
    {
        "title": "In-database embeddings for the vector store",
        "locator": "db_embeddings = OracleEmbeddings",
        "stub": '''from langchain_oracledb.embeddings import OracleEmbeddings

# 🧩 TODO %%NUM%% — which provider makes OracleEmbeddings compute vectors INSIDE the database
# (with the ONNX model you loaded in Part 1), instead of calling an external embedding service?
# Fill in the provider value. See docs/todo%%NUM%%.md
db_embeddings = OracleEmbeddings(
    conn=conn,
    params={"provider": "", "model": CFG["EMBED_MODEL"]})
print("Embedder configured — fill in the provider, then run the check.")
''',
        "teach": """\
`OracleEmbeddings` can compute vectors in two very different places. With `provider="database"`, text is
embedded **inside Oracle** by the ONNX model you loaded in Part 1 (`VECTOR_EMBEDDING(...)`), so the data never
leaves the database and the query shares one vector space with the stored documents. The alternative is to
call an **external** embedding service over the network — another key, another vendor, your text leaving the box.

### What to implement
Set `params["provider"]` to **`"database"`**. The check confirms a probe text embeds to a 384-dim vector
entirely in Oracle.

> 💡 "Oracle everywhere" is the thesis of this workshop: embeddings, retrieval, memory, **and** the LLM all run
> against the database. Choosing the `database` provider is the first brick.""",
    },
    {
        "title": "Choose the vector distance strategy",
        "locator": "store = OracleVS",
        "stub": '''from langchain_oracledb.vectorstores import OracleVS, DistanceStrategy

# 🧩 TODO %%NUM%% — pick the distance strategy the store uses to measure vector similarity.
# Options on DistanceStrategy: COSINE, EUCLIDEAN_DISTANCE, DOT_PRODUCT, MAX_INNER_PRODUCT, JACCARD.
# Which compares meaning by ANGLE (the standard for normalized text embeddings)? See docs/todo%%NUM%%.md
VSTORE_TABLE = "AGENT_VSTORE"
store = OracleVS(
    client=conn,
    embedding_function=db_embeddings,
    table_name=VSTORE_TABLE,
    distance_strategy=None)
print("OracleVS ready over", VSTORE_TABLE, "| distance:", getattr(store, "distance_strategy", None))
''',
        "teach": """\
A vector store ranks neighbours by a **distance strategy** — the maths that decides how "close" two embeddings
are, which is to say *what "similar" means*.

### How vector similarity is computed
Each text becomes a point in 384-dimensional space. The common strategies:
- **COSINE** — the **angle** between two vectors, ignoring length. Two texts on the same topic point the same
  *direction* even if one is longer, so cosine is the standard for **normalized text embeddings** (what
  `ALL_MINILM_L12_V2` produces). Distance = `1 − cos(θ)`; 0 means identical direction.
- **EUCLIDEAN_DISTANCE** — straight-line distance between the points; sensitive to magnitude, so it can rank by
  length as much as by meaning unless the vectors are normalized.
- **DOT_PRODUCT / MAX_INNER_PRODUCT** — angle *and* magnitude together; fast, and equal to cosine **only** when
  the vectors are unit-length.
- **JACCARD** — set overlap, for binary/sparse vectors — not dense text embeddings.

### What to implement
Pass **`DistanceStrategy.COSINE`** as `distance_strategy`. The check confirms `store.distance_strategy` is
`COSINE`.

> 💡 Rule of thumb: **normalized text embeddings → cosine.** The other strategies exist for embeddings whose
> magnitude carries meaning (recommenders, binary fingerprints), which isn't our case here.""",
    },
]


def cell_md(text: str, cid: str) -> dict:
    return {"cell_type": "markdown", "id": cid, "metadata": {}, "source": text.splitlines(keepends=True)}


def cell_code(text: str, cid: str) -> dict:
    return {"cell_type": "code", "id": cid, "metadata": {}, "execution_count": None,
            "outputs": [], "source": text.splitlines(keepends=True)}


def renum(text: str, num: int) -> str:
    """Rewrite every TODO-number reference (labels, doc links, check ids, %%NUM%% placeholders) to `num`."""
    text = re.sub(r"TODO \d+", f"TODO {num}", text)
    text = re.sub(r"todo\d+\.md", f"todo{num}.md", text)
    text = re.sub(r"todo\d+-check", f"todo{num}-check", text)
    return text.replace("%%NUM%%", str(num))


def write_doc(num: int, spec: dict, solution: str) -> None:
    body = (
        f"# 🧩 TODO {num} — {spec['title']}\n\n"
        f"{spec['teach']}\n\n"
        f"## ✅ Solution\n\n"
        f"Replace the placeholder cell with this, then run the **`✅ TODO {num} check`** cell:\n\n"
        f"```python\n{solution.rstrip()}\n```\n\n"
        f"_Generated from `total_recall_complete.ipynb` — the exact reference implementation._\n"
    )
    (DOCS / f"todo{num}.md").write_text(body, encoding="utf-8")


BANNER = cell_md(
    "> ### 🎓 This is the **student** notebook\n>\n"
    "> Several implementations have been replaced with numbered **`TODO`** placeholders. Each TODO has a "
    "**`docs/todoN.md`** explainer (with a copy-paste solution) and a **`✅ TODO N check`** cell that must "
    "pass before you continue. Work top to bottom — the TODO numbers run in reading order. Everything else "
    "runs as-is; the full answer key is `total_recall_complete.ipynb`.",
    "student-banner")


def build() -> None:
    nb = json.loads(SRC.read_text(encoding="utf-8"))
    cells = nb["cells"]
    out_complete: list[dict] = []
    out_student: list[dict] = []
    seen: set[str] = set()
    num = 0
    i = 0
    while i < len(cells):
        cell = cells[i]
        text = "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]
        spec = next((s for s in SPECS if cell["cell_type"] == "code" and s["locator"] in text), None)
        if spec is None:
            out_complete.append(cell)
            out_student.append(cell)
            i += 1
            continue
        if spec["locator"] in seen:
            raise SystemExit(f"locator {spec['locator']!r} matched more than one cell")
        seen.add(spec["locator"])
        num += 1
        if not (i + 1 < len(cells) and str(cells[i + 1].get("id", "")).endswith("-check")):
            raise SystemExit(f"the solution for {spec['locator']!r} must be immediately followed by a *-check cell")
        check = cells[i + 1]
        check["source"] = renum("".join(check["source"]), num).splitlines(keepends=True)
        check["id"] = f"todo{num}-check"
        check["outputs"] = []
        check["execution_count"] = None
        write_doc(num, spec, text)                                   # real cell source -> the doc's solution
        out_complete.append(cell)
        out_complete.append(check)
        out_student.append(cell_md(POINTER.format(num=num, title=spec["title"]), f"todo{num}-md"))
        out_student.append(cell_code(renum(spec["stub"], num), f"todo{num}-blank"))
        out_student.append(check)
        i += 2
    missing = {s["locator"] for s in SPECS} - seen
    if missing:
        raise SystemExit(f"these TODO locators matched no cell: {missing}")
    # the complete notebook is the single source of truth for the checks: write back the renumbered ones
    nb["cells"] = out_complete
    SRC.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
    # the student notebook: same cells, each solution swapped for pointer + blank, banner after the title
    student_nb = {k: v for k, v in nb.items() if k != "cells"}
    student_nb["cells"] = [out_student[0], BANNER] + out_student[1:]
    OUT.write_text(json.dumps(student_nb, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT.name}: {len(student_nb['cells'])} cells, {num} TODOs (+{num} docs in {DOCS.name}/)")


if __name__ == "__main__":
    build()
