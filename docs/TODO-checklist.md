# Workshop TODO checklist

The canonical workshop is the nine-TODO path in `notebook_student.ipynb`. Each checkpoint is an assertion in the notebook: most sit in the cell right after their TODO, and a few run later in the same section so the data they need (registered tools, ingested skills) exists first. A failure identifies the unfinished block before later work depends on it.

- [ ] **TODO 1 — ask the bare model** in Part 1. Set `QUESTION` and run the chat client with no memory, retrieval, or tools.
- [ ] **TODO 2 — `OracleONNXEmbedder.embed`** in Part 2. Embed text with the in-database ONNX model, one `VECTOR_EMBEDDING` SELECT per text.
- [ ] **TODO 3 — `_scan_tables`** in Part 2. Read Oracle catalog metadata and emit `Fact` objects describing the `AGENT` retail tables.
- [ ] **TODO 4 — `retrieve_knowledge`** in Part 3. Oversample OAMP memories, filter them, and rerank the useful candidates.
- [ ] **TODO 5 — `hybrid_rrf_search_memories`** in Part 3. Fuse vector and Oracle Text ranks with Reciprocal Rank Fusion in one SQL statement.
- [ ] **TODO 6 — `retrieve_tools`** in Part 6. Rank the `toolbox` by cosine distance to the query, rerank the shortlist, and merge the always-on tools.
- [ ] **TODO 7 — `tool_run_sql`** in Part 6. Register a safe, read-only `SELECT`/`WITH` tool and return bounded JSON results.
- [ ] **TODO 8 — `tool_list_skills`** in Part 6. Search the `skillbox` semantically and return top-k skills as JSON.
- [ ] **TODO 9 — `agent_turn`** in Part 7. Assemble context, call the model, dispatch tools, enforce iteration/time limits, and produce a final answer.

## Before you start

- [ ] Codespace or local Oracle is reachable.
- [ ] `ALL_MINILM_L12_V2` is available in the database.
- [ ] The notebook kernel has the dependencies from `requirements.txt`.
- [ ] `notebook_student.ipynb` opens from the repository root.

## After the nine TODOs

- [ ] Run the three-turn notebook demo on one thread.
- [ ] Open the AppBook at `http://localhost:8000`.
- [ ] Check the Foundation, Retrieval, Memory, Semantic Layer, Agent Loop, and Mission Control chapters.
- [ ] Try a retail question such as “How many paid orders does each sales channel have?”

Parts 4/5/9/11 are advanced reference material for the original supply-chain/Oracle-capabilities version. They are not additional required TODOs in the lightweight workshop.
