# Total Recall AppBook

The AppBook is a runnable, layer-by-layer demonstration of an enterprise agent harness. It pairs a FastAPI backend with a dependency-free JavaScript single-page app; there is no frontend build step.

## What it demonstrates

| Chapter | Live probe |
|---|---|
| 1. Foundation & Models | Database health, loaded ONNX models, and 384-dimensional embeddings |
| 2. Memory Substrate | Read/write/tail/grep scratch content stored in an Oracle SecureFile LOB table |
| 3. Encoding & Retrieval | Keyword, vector, hybrid, and optional cross-encoder reranking |
| 4. Cognitive Memory | OAMP context cards, durable facts, and multi-turn chat |
| 5. Semantic Layer | Catalog search over the seeded `AGENT` retail schema |
| 6. Skills & Automations | Meaning-based tool/skill lookup and database-backed automations |
| 7. The Agent Loop | Streamed context assembly, tool calls, tool results, and an answer |
| 8. Context Engineering | Context growth with engineering off versus on |
| 9. Mission Control | Chat, live context, and automation controls in one console |

The default business tables are `customers`, `products`, `orders`, `order_items`, and `v_revenue`. Startup is idempotent: it creates missing harness objects and demo rows without dropping or resetting existing data.

## Prerequisites

- Python 3.11+
- Oracle AI Database 26ai Free or a compatible Oracle instance
- An `AGENT` database user with permission to create the app's tables and indexes
- The `ALL_MINILM_L12_V2` in-database ONNX embedder (required for semantic features)
- The `RERANK_XENC` cross-encoder (optional; retrieval falls back gracefully)
- An OCI GenAI API key, or `LLM_PROVIDER=openai` with an OpenAI API key

The app's only outbound request is the chat-model request. Embeddings, retrieval, memory, scratch content, catalog data, and automations are database-backed.

## Getting started from the repository root

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r app/requirements.txt
cp app/.env.example app/.env
# Edit app/.env and set OCI_GENAI_API_KEY, or use LLM_PROVIDER=openai.
cd app
./run.sh
```

The server listens on `http://127.0.0.1:8000` by default. Set `HOST` and `PORT` before running if needed:

```bash
HOST=0.0.0.0 PORT=8000 ./run.sh
```

In GitHub Codespaces, `.devcontainer/start-app.sh` writes `app/.env`, starts the same server on port 8000, and waits for `/api/health` before reporting readiness. It retries Oracle initialization because the database listener can become available before its model packages and user sessions are ready.

## Configuration

Copy `app/.env.example` to `app/.env` and adjust the values for your environment:

```dotenv
ORA_DSN=localhost:1521/FREEPDB1
ORA_AGENT_USER=AGENT
ORA_AGENT_PWD=AgentPw_2026
LLM_PROVIDER=oci
OCI_GENAI_API_KEY=replace-me
OCI_GENAI_ENDPOINT=https://inference.generativeai.us-phoenix-1.oci.oraclecloud.com/openai/v1
LLM_MODEL=xai.grok-4.20-non-reasoning
EMBED_MODEL=ALL_MINILM_L12_V2
RERANK_MODEL=RERANK_XENC
VECTOR_DIM=384
ORACLE_ENABLED=1
```

A bare OCI regional endpoint is also accepted; the backend adds `/openai/v1` automatically. When `LLM_PROVIDER=openai`, set `OPENAI_API_KEY` and use an OpenAI model name. Provider-specific key selection prevents an OCI key from being sent to OpenAI accidentally.

If no model key is present, the AppBook still serves its shell and database probes. Chat and agent routes return a visible configuration error instead of silently producing an empty response.

## Database behavior

On startup, `backend.core.db.initialize()`:

1. Connects as the configured agent user.
2. Creates harness tables, indexes, scratch storage, tool/skill registries, and automation metadata when missing.
3. Seeds the small retail schema and deterministic demo rows.
4. Builds the semantic catalog and loads the default tools and skills.
5. Records readiness or the first-line error for `/api/health` and the UI status badge.

The startup path is safe to retry. A partial vector-store initialization is discarded before a later retry, so a temporary Oracle/model race does not leave the process with a permanently broken in-memory handle.

## Useful commands

From the repository root:

```bash
curl http://localhost:8000/api/health
bash .devcontainer/start-app.sh
```

From `app/`:

```bash
./run.sh
```

For a static smoke check without a database, import the app with its dependencies installed and call `/`, `/styles.css`, `/app.js`, `/images/total_recall.png`, `/api/health`, and `/api/context/series`. The frontend JavaScript can be syntax-checked with `node --check frontend/app.js`.

## Relationship to the notebook

The canonical notebook is the five-TODO implementation path in the repository root. It teaches the primitives directly. The AppBook is a separate application that exposes the current live layers through probes and a guided UI; it is not a generated notebook frontend and does not require the notebook cells to have been executed first.

`enterprise_data_agent.ipynb` and Parts 4/5/9/11 of `docs/` document optional advanced Oracle capabilities such as MLE and JSON Relational Duality Views. They remain useful reference material, but are intentionally outside the current AppBook's nine live chapters.
