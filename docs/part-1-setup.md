# Part 1: Setup & connectivity

## What you are building

The workshop treats an agent as a software boundary:

```text
Agent = Model + Harness
```

The model emits tokens. The harness supplies database state, memory, retrieval, tools, identity, retries, budgets, and observable traces. Part 1 connects the notebook to Oracle; the later parts implement the five core harness blocks.

## Canonical environment

The lightweight workshop uses one `AGENT` database user for both harness state and a small retail demo schema. The demo tables are `customers`, `products`, `orders`, `order_items`, and the `v_revenue` view. The AppBook creates any missing tables and deterministic rows when it starts.

In Codespaces, `.devcontainer/postCreate.sh` runs [`scripts/seed_oracle.py`](../scripts/seed_oracle.py) to provision the user and required embedder. `.devcontainer/start-app.sh` starts the AppBook on port 8000. The bootstrap is idempotent and safe to retry.

| Setting | Default |
|---|---|
| Database DSN inside compose | `oracle:1521/FREEPDB1` |
| Database DSN from the host | `localhost:1521/FREEPDB1` |
| Agent user | `AGENT` |
| Agent password | `AgentPw_2026` |
| Required embedder | `ALL_MINILM_L12_V2` (384 dimensions) |
| Optional reranker | `RERANK_XENC` |
| AppBook | `http://localhost:8000` |

The notebook reads `ORA_DSN`, `ORA_AGENT_USER`, and `ORA_AGENT_PWD`. If `ORA_DEMO_USER` is not set, it scans the `AGENT` schema. Do not commit API keys or passwords; use `app/.env` or Codespaces secrets.

## Models and network boundary

`ALL_MINILM_L12_V2` is loaded inside Oracle and called with `VECTOR_EMBEDDING(...)`; embedding and retrieval do not call a hosted vector service. `RERANK_XENC` is optional. If it is absent, the notebook and AppBook keep the same retrieval interface and return the candidate order without cross-encoder rescoring.

The chat model is selected with `LLM_PROVIDER`:

- `oci` uses `OCI_GENAI_API_KEY`, `OCI_GENAI_ENDPOINT`, and the OpenAI-compatible OCI endpoint.
- `openai` uses `OPENAI_API_KEY` and an OpenAI-compatible model endpoint.

The AppBook normalizes a bare OCI regional endpoint by adding `/openai/v1`. The notebook does the same when it initializes its client.

## Connect in the notebook

Part 1 has one small TODO. Run the import and connection cells from the repository root:

```python
ORA_DSN    = os.environ.get("ORA_DSN", "localhost:1521/FREEPDB1")
AGENT_USER = os.environ.get("ORA_AGENT_USER", "AGENT")
AGENT_PASS = os.environ.get("ORA_AGENT_PWD", "AgentPw_2026")
DEMO_USER  = os.environ.get("ORA_DEMO_USER", AGENT_USER)
agent_conn = connect(AGENT_USER, AGENT_PASS, ORA_DSN)
```

The `connect` helper retries because a Docker healthcheck can pass before Oracle is ready to accept application sessions. After the connection succeeds, Part 2 creates the OAMP client and begins scanning catalog metadata.

## TODO 1: Talk to the bare model

The chat-client cell ends with your first TODO: set `QUESTION`, run the cell, and read the answer. There is no harness here, no memory, no retrieval, no tools; it is the reasoning core on its own. Remember this baseline, because Part 7 wraps the same call in a context block, retrieved tool schemas, and a dispatch loop.

**Solution:**

```python
QUESTION = "In one sentence: what does an agent harness add to a language model?"
```

The checkpoint at the end of the cell fails until `QUESTION` is non-empty and the model answers.

## Verify the AppBook

In another terminal:

```bash
curl http://localhost:8000/api/health
```

The endpoint returns JSON even while Oracle is warming. A healthy response has `harness.ready: true`; an unhealthy response includes the first-line database error so the UI can explain what needs attention. If the app is not serving, run:

```bash
bash .devcontainer/start-app.sh
 tail -60 /tmp/total-recall-app.log
```

## Key takeaways

- Use a dedicated agent user; database grants are part of the security boundary.
- Keep model readiness separate from chat-key readiness: database probes can work without an LLM key.
- Keep the notebook and AppBook paths explicit: notebook from the repository root, AppBook from `app/`, port 8000.
- Continue to the [Part 2 guide](part-2-oamp-memory.md) and stop at each notebook assertion before moving on.
