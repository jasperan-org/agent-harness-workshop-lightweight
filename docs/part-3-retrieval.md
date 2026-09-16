# Part 3: Retrieval Strategies

Tables are storage. Retrieval is what makes them useful. This Part builds the read path on top of the OAMP store from Part 2 — and tests each layer in isolation before we plug it into the agent loop.

## The Two Retrieval Surfaces

| Layer | What it gives you | When to use |
|---|---|---|
| **Vector search** | Cosine similarity over embedded memories. Strong on *meaning*. | "Where do we record customer orders?" |
| **Hybrid (vector + Oracle Text + RRF)** | Vector and full-text combined via Reciprocal Rank Fusion. Strong on both *meaning* and *exact tokens*. | "order_items.discount percentage" — semantic concept + precise acronym |

Both run server-side. No round-trips to a separate vector DB. No Python embedder. No second service.

## The `AGENT` retail demo schema

The AppBook startup creates a deterministic retail dataset in the `AGENT` schema. The notebook scans the same schema when `DEMO_USER` defaults to `AGENT`:

| Table/view | What it represents |
|---|---|
| `customers` | Customer identity, country, segment, and signup date |
| `products` | Product name, category, price, and cost |
| `orders` | One row per customer order; `status` is `paid` or `refunded`; `channel` is `web`, `store`, or `partner` |
| `order_items` | Product lines, quantities, prices, and `discount` percentage |
| `v_revenue` | Paid-order revenue by line, category, channel, segment, and country |

The seed comments encode the facts the scanner should make retrievable. In particular, `order_items.discount` is a percentage from 0 to 100, and `v_revenue.net_revenue` is calculated from price, quantity, and that discount. This gives the later agent demo a concrete distinction between schema knowledge (`search_knowledge`) and live facts (`run_sql`).

## TODO 4: Implement `retrieve_knowledge`

`retrieve_knowledge(query, k, kinds=None)` is a two-stage call that the agent loop will use on every turn:

1. **Cosine search** — `memory_client.search(...)` returns `k * 4` candidates ordered by vector distance.
2. **Cross-encoder rerank** — those candidates run through `rerank(...)`, which calls `DBMS_VECTOR.RERANK` server-side when the optional `RERANK_XENC` model is loaded. When no reranker is loaded, `rerank` is a pass-through that just slices to top-k — the call site is unchanged either way.

We **filter out** memories with `metadata.kind == "tool_output"` so the log of past tool calls doesn't pollute knowledge retrieval.

**Why oversample by 4×?** The reranker is only as good as the candidate set you give it. Asking the cosine retriever for `k * 4` rows gives the cross-encoder enough signal to do meaningful reordering.

**Solution:**

```python
def retrieve_knowledge(query: str, k: int = 5,
                       kinds: list[str] | None = None) -> list[dict]:
    """Semantic search over the agent's long-term memory."""
    # Defensive: LLMs sometimes pass `kinds` as a comma-separated string.
    if isinstance(kinds, str):
        kinds = [s.strip() for s in kinds.split(",") if s.strip()]
    if kinds == []:
        kinds = None

    # Stage 1: oversample by 4× so the reranker has signal to chew on.
    cosine_fetch = k * 4
    hits = memory_client.search(
        query,
        user_id=USER_ID, agent_id=AGENT_ID,
        record_types=["memory"],
        max_results=cosine_fetch,
    )

    candidates: list[dict] = []
    for h in hits:
        meta = h.metadata or {}
        kind_value = meta.get("kind")
        # Drop tool-output memories from knowledge retrieval.
        if kind_value == "tool_output":
            continue
        if kinds is not None and (kind_value is None or kind_value not in kinds):
            continue
        candidates.append({
            "kind":     kind_value or "?",
            "subject":  meta.get("subject", ""),
            "body":     h.content,
            "metadata": meta,
            "distance": float(h.distance),
        })

    # Stage 2: cross-encoder rerank (in-DB if loaded; otherwise pass-through).
    return rerank(query, candidates, top_k=k, content_key="body")
```

After you implement it, the next cell scans `AGENT` and runs a probe query. You should see facts for tables such as `AGENT.ORDERS` and columns such as `AGENT.ORDER_ITEMS.DISCOUNT`, with the comments from the seed included when available.

## TODO 5: Implement `hybrid_rrf_search_memories`

Pure vector search is strong on meaning but **under-weights exact tokens**. If the user types an exact column name or an `ORA-00904` error code, they want the row that *literally contains the string*, not one that's vaguely similar.

The fix is **Reciprocal Rank Fusion** — run two retrievals (vector + full-text), combine the rankings:

$$\text{score}(d) = \frac{1}{k + r_{\text{vec}}(d)} + \frac{1}{k + r_{\text{text}}(d)}$$

where $r_{\text{vec}}$ and $r_{\text{text}}$ are 1-based ranks from each retriever (sentinel `999999` when a doc is missing from one list), and $k=60$ is the standard smoothing constant.

RRF doesn't care about absolute scores from each retriever — only the relative ranks — so it's robust to whatever scoring scheme each side uses.

Two prerequisites. The ONNX embedder lives in the database image, and the notebook creates the
`CTXSYS.CONTEXT` index it needs in §3.3a (it is idempotent), so both are in place by the time this
cell runs:

| Side | What we use |
|---|---|
| Vector | `VECTOR_DISTANCE(c.embedding, VECTOR_EMBEDDING(...) USING :q AS DATA, COSINE)` over `eda_onnx_record_chunks.embedding` (HNSW-indexed) |
| Full-text | `CONTAINS(m.content, :kw, 1) > 0` with `SCORE(1)` over a `CTXSYS.CONTEXT` index on `eda_onnx_memory.content` |

The fusion happens in **one SQL statement** — vector CTE, text CTE, FULL OUTER JOIN on memory id, RRF score computed inline, sort + fetch top-k. No round trip to Python.

**Solution:**

```python
MEMORY_TABLE = "eda_onnx_memory"

def hybrid_rrf_search_memories(query, k=5, per_list=30, rrf_k=60):
    sql = f"""
        WITH q_emb AS (
            SELECT TO_VECTOR(VECTOR_EMBEDDING({ONNX_EMBED_MODEL} USING :q AS DATA),
                             384, FLOAT64) AS emb
              FROM dual),
        vec AS (
            SELECT m.record_id,
                   DBMS_LOB.SUBSTR(m.content, 4000, 1) AS content, m.metadata,
                   ROW_NUMBER() OVER (
                       ORDER BY VECTOR_DISTANCE(c.embedding, q_emb.emb, COSINE)
                   ) AS r_vec
              FROM {MEMORY_TABLE} m
              JOIN eda_onnx_record_chunks c ON c.source_id = m.record_id
              CROSS JOIN q_emb
             WHERE m.user_id = :u AND m.agent_id = :a
               AND (JSON_VALUE(m.metadata, '$.kind') IS NULL
                    OR JSON_VALUE(m.metadata, '$.kind') <> 'tool_output')
             FETCH FIRST :n ROWS ONLY),
        txt AS (
            SELECT m.record_id,
                   DBMS_LOB.SUBSTR(m.content, 4000, 1) AS content, m.metadata,
                   ROW_NUMBER() OVER (ORDER BY SCORE(1) DESC) AS r_txt
              FROM {MEMORY_TABLE} m
             WHERE CONTAINS(m.content, :kw, 1) > 0
               AND m.user_id = :u AND m.agent_id = :a
               AND (JSON_VALUE(m.metadata, '$.kind') IS NULL
                    OR JSON_VALUE(m.metadata, '$.kind') <> 'tool_output')
             FETCH FIRST :n ROWS ONLY)
        SELECT COALESCE(v.record_id, t.record_id) AS record_id,
               COALESCE(v.content, t.content)     AS content,
               COALESCE(v.metadata, t.metadata)   AS metadata,
               NVL(v.r_vec, 999999) AS r_vec,
               NVL(t.r_txt, 999999) AS r_txt,
               ( 1.0/(:rrf_k + NVL(v.r_vec, 999999))
               + 1.0/(:rrf_k + NVL(t.r_txt, 999999)) ) AS rrf_score
          FROM vec v
          FULL OUTER JOIN txt t ON v.record_id = t.record_id
         ORDER BY rrf_score DESC
         FETCH FIRST :k ROWS ONLY
    """
    with agent_conn.cursor() as cur:
        kw = _text_query(query)   # {term} OR {term} … (§3.3) — never the raw sentence
        cur.execute(sql, q=query, kw=kw, u=USER_ID, a=AGENT_ID,
                    n=per_list, rrf_k=rrf_k, k=k)
        rows = []
        for rec_id, content, meta, r_vec, r_txt, rrf in cur:
            if hasattr(content, "read"):
                content = content.read()
            rows.append({"record_id": rec_id,
                         "kind": (meta or {}).get("kind", "memory"),
                         "subject": (meta or {}).get("subject", ""),
                         "content": str(content or "")[:500],
                         "r_vec": int(r_vec), "r_txt": int(r_txt),
                         "rrf_score": float(rrf)})
    return rows
```

The hard-stop assert below your implementation runs the SQL against a real query and asserts the shape — `r_vec` and `r_txt` are ints (rank sentinels), `rrf_score` is a float, and at least one hit comes back.

## The three-way retrieval probe (just run)

Your `hybrid_rrf_search_memories(query, k)` (TODO 5) returns a list with each hit annotated by `r_vec`, `r_txt`, and `rrf_score`. Run the same query through:

- `retrieve_knowledge(probe_q, k=3)` — vector only (your TODO 4)
- `keyword_search_memories(probe_q, k=3)` — Oracle Text only
- `hybrid_rrf_search_memories(probe_q, k=3)` — fused via RRF (your TODO 5)

with this query:

```python
probe_q = "order_items.discount percentage"
```

The query is deliberately mixed — the column name is a precise token (Oracle Text wins) and *"how a discount is measured"* is a fuzzy semantic concept (vector wins). Hybrid should rank the best-matching memory above either alone.

**Solution** — just call each retriever in turn and print the results:

```python
print("=" * 80)
print(f"QUERY: {probe_q!r}")
print("=" * 80)

print("\n--- A) VECTOR ONLY ---")
for h in retrieve_knowledge(probe_q, k=3):
    print(f"  [{h['kind']:12s}] {h['subject'][:40]:40s}  {h['body'][:100]}")

print("\n--- B) KEYWORD ONLY ---")
for h in keyword_search_memories(probe_q, k=3):
    print(f"  [{h['kind']:12s}] score={h['score_txt']:6.2f}  {h['subject'][:40]:40s}")

print("\n--- C) HYBRID via RRF ---")
for h in hybrid_rrf_search_memories(probe_q, k=3):
    print(f"  rrf={h['rrf_score']:.4f}  r_vec={h['r_vec']:>3}  r_txt={h['r_txt']:>3}  {h['subject'][:40]}")
```

Watch the `r_vec` / `r_txt` columns: a row whose `r_vec` is low (top of vector list) but `r_txt` is `999999` (missing from keyword list) still gets a fair RRF score from its vector half — and vice versa. Memories that show up in **both** lists get the highest combined score.

## Key Takeaways — Part 3

- **Vector search alone misses exact tokens.** A user typing an exact column name or `ORA-00904` wants the row that *literally contains the string*. Pure cosine retrieval underweights this. Hybrid (vector + Oracle Text via RRF) closes the gap server-side in one SQL.
- **Reranking is one SQL primitive.** `DBMS_VECTOR.RERANK` runs the cross-encoder server-side; the call gracefully degrades to cosine ordering when no reranker is loaded.
- **Oversample before rerank.** A reranker is only useful with enough candidates to reorder. `k * 4` candidates from cosine, then rerank to top-k.
- **RRF is rank-based, not score-based.** No need to normalize cosine distance against `SCORE(1)` — they're from different worlds. Fusing on rank dodges the calibration problem.

## Troubleshooting

**`AttributeError: 'NoneType' object has no attribute 'metadata'`** — `memory_client.search` returned no hits. Run the scan cell first to populate the store.

**`ORA-29855: error occurred in the execution of ODCIINDEXCREATE`** — on some images creating a `CTXSYS.CONTEXT` index needs the `CTXAPP` role. The notebook creates the index in §3.3a and `scripts/seed_oracle.py` grants `EXECUTE ON CTX_DDL`; on a stricter image, `GRANT CTXAPP TO AGENT` as `SYSDBA` and re-run §3.3a.

**Hybrid or keyword query returns nothing** — `CONTAINS()` takes an Oracle Text *expression*, and a plain
multi-word string is parsed as a **phrase** — `order item discount percentage` means those four words
adjacent, in that order, so almost nothing matches (quoting it makes the phrase explicit, which is
worse, not better). Join the terms with an operator instead: `{term} OR {term} …`, which is what the
notebook's `_text_query()` (§3.3) does. If the leg is empty *after* that, the index is missing —
`DRG-10599: column is not indexed` means §3.3a has not run against this database yet.
