# Enterprise Data Agent Workshop

Build a memory-aware enterprise data agent on Oracle AI Database 26ai, then inspect the same harness in a working browser AppBook.

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/jasperan-org/agent-harness-workshop-lightweight)

## What this repository contains

There are two deliberately different learning surfaces:

1. **The canonical workshop notebook** — [`notebook_student.ipynb`](notebook_student.ipynb) has five coding TODOs. You implement memory scanning, retrieval, hybrid ranking, a safe SQL tool, and the agent loop. [`notebook_complete.ipynb`](notebook_complete.ipynb) is the answer key.
2. **The Total Recall AppBook** — [`app/`](app/) is a runnable FastAPI + vanilla JavaScript application with nine interactive harness layers. It is a guided demonstration of the ideas after (or alongside) the notebook.

The default demo data is a small retail schema owned by `AGENT`: `customers`, `products`, `orders`, `order_items`, and `v_revenue`. The current AppBook and canonical notebook use this same domain. The older supply-chain implementation is preserved only as advanced reference material in the Part 4/5/9/11 reference guides.

## The five-TODO learning path

| Block | Topic | Notebook checkpoint |
|---|---|---|
| 1 | Setup, Oracle connectivity, and an OpenAI-compatible chat helper | — |
| 2 | OAMP long-term memory and an Oracle catalog scanner | TODO 1 — `_scan_tables` |
| 3 | Semantic, reranked, and hybrid vector + Oracle Text retrieval | TODO 2 — `retrieve_knowledge`; TODO 3 — `hybrid_rrf_search_memories` |
| 4 | Vector-indexed tools and skills | TODO 4 — `tool_run_sql` |
| 5 | Context engineering and the bounded agent loop | TODO 5 — `agent_turn` |

Every TODO has a hard-stop assertion immediately below it. Use the [TODO checklist](docs/TODO-checklist.md) and the matching guides in [`docs/`](docs/) as you work.

## Start in GitHub Codespaces

1. Create a Codespace from the badge above.
2. Wait for the post-create step to finish. It installs dependencies, seeds the Oracle `AGENT` schema, and starts the AppBook on port **8000**.
3. Open [`notebook_student.ipynb`](notebook_student.ipynb) with the Python 3.11+ kernel and run cells from the top.
4. Open the forwarded **Total Recall AppBook** port at [http://localhost:8000](http://localhost:8000). The app can load while Oracle is still warming: it keeps retrying and the status badge turns green on its own, with no restart.

Useful recovery commands inside the Codespace:

```bash
bash .devcontainer/start-app.sh
curl http://localhost:8000/api/health
tail -60 /tmp/total-recall-app.log
```

The Oracle container uses the 26ai Free **lite** image — a pre-built database that is ready in about 30 seconds on a 2.7 GB image (same engine build and components as `:latest`, which takes 3-5 minutes and 14.1 GB). The bootstrap is idempotent and does not reset existing data.

## Run locally

You need Docker, Python 3.11+, an Oracle AI Database 26ai Free instance, and either OCI GenAI or OpenAI credentials.

```bash
git clone https://github.com/jasperan-org/agent-harness-workshop-lightweight
cd agent-harness-workshop-lightweight

# Start the included Oracle service. Use ORACLE_PORT if 1521 is already occupied.
ORACLE_PORT=1521 docker compose -f .devcontainer/docker-compose.yml up -d oracle

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r app/requirements.txt

cp app/.env.example app/.env
# Edit app/.env and set OCI_GENAI_API_KEY, or choose LLM_PROVIDER=openai.

# Create AGENT and load the required in-database embedder.
python scripts/seed_oracle.py

# The AppBook creates the retail tables and its own harness tables on startup.
cd app
./run.sh
```

Open [http://localhost:8000](http://localhost:8000). To run the notebook, use a second terminal from the repository root:

```bash
source .venv/bin/activate
jupyter lab notebook_student.ipynb
```

The app uses `ORA_DSN`, `ORA_AGENT_USER`, and `ORA_AGENT_PWD`. The included compose file defaults to `oracle:1521/FREEPDB1` inside Docker; for a host Oracle use `ORA_DSN=localhost:${ORACLE_PORT:-1521}/FREEPDB1` in `app/.env`.

## AppBook chapters

The AppBook is intentionally dependency-free in the browser and requires no frontend build step:

| Chapter | Demonstration |
|---|---|
| Foundation & Models | In-database embeddings and model readiness |
| Memory Substrate | Transactional scratch storage in an Oracle SecureFile LOB table |
| Encoding & Retrieval | Keyword, vector, hybrid, and optional reranked results |
| Cognitive Memory | OAMP context cards, durable facts, and chat turns |
| Semantic Layer | Meaning-based catalog search over the retail schema |
| Skills & Automations | Tool/skill retrieval, registration, and scheduled work |
| The Agent Loop | Context assembly, tool calls, results, and streamed answers |
| Context Engineering | A live context window: skill retrieval, runtime skill loading, compaction, offloads, and the vector archive |
| Mission Control | A single console for chat, context, and automations |

Read [`app/README.md`](app/README.md) for the AppBook-specific architecture and API notes.

## Starter prompts

Use these after the AppBook reports that Oracle is ready. They exercise the current seeded retail data and the live tool registry:

- “What’s in the AGENT retail schema? Briefly list the main entities and how they relate.”
- “How many paid orders does each sales channel have? Show a small table sorted by count descending.”
- “Which product categories have the highest revenue? Explain the SQL you used.”
- “Remember that orders with status `paid` are the source for revenue, and `order_items.discount` is a percentage from 0 to 100.”
- “How do I diagnose ORA-00904? Consult any guide you have.”

If no chat-model key is configured, the UI reports that explicitly; the database-backed probes and notebook setup still remain inspectable.

## How the notebooks fit together

| Notebook | Role |
|---|---|
| [`notebook_student.ipynb`](notebook_student.ipynb) | **Start here.** The five-TODO build path. Self-contained: it provisions the Oracle Text index its keyword leg needs, and runs end to end against the pre-seeded `AGENT` schema. |
| [`notebook_complete.ipynb`](notebook_complete.ipynb) | The same notebook with all five TODOs solved — the answer key, or a reference to diff your work against. |

## Repository map

```text
.devcontainer/                 Codespaces compose, bootstrap, and app startup
notebook_student.ipynb         Entry point: canonical five-TODO student notebook
notebook_complete.ipynb        The same notebook, all five TODOs solved
app/                            FastAPI + vanilla JavaScript Total Recall AppBook
docs/                           Core guides, advanced reference notes, and troubleshooting
scripts/seed_oracle.py          Safe AGENT-schema/model bootstrap helper
scripts/build_student_notebook.py
                               Validates the checked-in student and answer-key notebooks
requirements.txt                Notebook dependencies
```

## The central idea

```text
Agent = Model + Harness
```

The model produces tokens. The harness supplies state, memory, retrieval, tools, identity, retries, budgets, and observable traces. This workshop builds those boundaries explicitly so an agent can be tested and reasoned about as an ordinary software system.
