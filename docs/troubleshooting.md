> **Canonical lightweight path:** use the included compose service, run `python scripts/seed_oracle.py` once, start the AppBook with `bash .devcontainer/start-app.sh`, and open port 8000. The old supply-chain/MLE commands below apply only to the advanced reference notebook.

# Troubleshooting Guide

This guide covers the most common issues encountered during the Enterprise Data Agent Workshop and how to resolve them.

---

## Oracle Database Issues

### ORA-51962: The vector memory area is out of space

**Symptom:** Creating a vector index (`CREATE VECTOR INDEX ... ORGANIZATION INMEMORY ...`) raises `ORA-51962`.

**Cause:** `vector_memory_size` is `0` in the running Oracle instance. HNSW vector indexes live in a dedicated SGA pool that defaults to off on the Free image.

**Fix (two-step — bounce required):**

```python
import oracledb
conn = oracledb.connect(user="sys", password="OraclePwd_2025",
                        dsn="localhost:1521/FREEPDB1",
                        mode=oracledb.AUTH_MODE_SYSDBA)
conn.cursor().execute("ALTER SYSTEM SET vector_memory_size = 512M SCOPE=SPFILE CONTAINER=ALL")
conn.commit(); conn.close()
```

Then bounce the database:

```bash
docker compose -f .devcontainer/docker-compose.yml restart oracle
```

Wait ~60 seconds for FREEPDB1 to report `READ WRITE`, restart the Jupyter kernel, and re-run from §1.

---

### ORA-04036: PGA memory used by the instance exceeds PGA_AGGREGATE_LIMIT

**Symptom:** `DBMS_VECTOR.RERANK` falls back to plain cosine ordering with `ORA-04036`.

**Cause:** The Free image ships with `pga_aggregate_limit = 2G`, which is too tight for the reranker's transient PGA use.

**Fix:** Raise it at the CDB level (PDBs can't change this parameter):

```bash
docker compose -f .devcontainer/docker-compose.yml exec -T oracle sqlplus -s / as sysdba <<'SQL'
ALTER SESSION SET CONTAINER = CDB$ROOT;
ALTER SYSTEM SET pga_aggregate_limit = 4G SCOPE=BOTH;
EXIT
SQL
```

`SCOPE=BOTH` means the change applies live and persists across restarts. No bounce needed.

---

### ORA-12541 / Connection refused

**Symptom:** Any database connection attempt fails with `Connection refused` or `DPY-6005`.

**Cause:** The Oracle container isn't running.

**Fix:**

```bash
docker ps
```

If the compose-managed Oracle service isn't listed, start it:

```bash
docker compose -f .devcontainer/docker-compose.yml up -d oracle
```

Wait 30 seconds and retry the connection cell.

---

### ORA-01017: Invalid username or password

**Symptom:** Connecting as `AGENT` fails — from the notebook (`could not connect to oracle:1521/FREEPDB1`), from the appbook (`harness.ready: false`), or from `sqlplus`. Related: `ORA-28000: The account is locked`.

**Cause:** the database lives in a named volume that outlives the container, so its `AGENT` user can hold a password the current environment doesn't use — a volume seeded by an older Codespace, an `ORA_AGENT_PWD` that changed, or an account **locked** by repeated failed logins (the notebook and the lifecycle probe both retry, so ten bad attempts happen easily).

**Fix:** re-run the bootstrap. It is idempotent and now *converges* the credential — it alters the password and unlocks the account, rather than only creating the user when absent:

```bash
python scripts/seed_oracle.py        # or:  bash .devcontainer/start-app.sh
```

Then re-run the notebook's connection cell (its `connect()` no longer retries a rejected password — retrying only burns `FAILED_LOGIN_ATTEMPTS`).

If you want to do it by hand as SYSDBA, inside the Oracle container:

```bash
C="$(docker ps --filter name=oracle --format '{{.Names}}' | head -1)"
docker exec -i "$C" bash -lc "sqlplus -s -L / as sysdba" <<< \
  'ALTER SESSION SET CONTAINER=FREEPDB1; ALTER USER AGENT IDENTIFIED BY "AgentPw_2026" ACCOUNT UNLOCK;'
```

**If the *admin* login is rejected too** — `✖ no admin credential worked` — the volume was initialised with a different `ORACLE_PWD` or by another image. No SQL can repair that; recreate the volume and let a fresh database initialise:

```bash
docker compose -f .devcontainer/docker-compose.yml down
docker volume rm $(docker volume ls -q | grep oracle-data)
# then: Codespaces → Rebuild Container   (or  docker compose up -d)
```

---

### Oracle container starts but never becomes ready

**Symptom:** `docker ps` shows the Oracle service running, but the connection cell still fails.

**Cause:** Oracle's listener takes a few seconds longer than the container healthcheck signals.

**Fix:** Check the logs:

```bash
docker compose -f .devcontainer/docker-compose.yml logs --tail=20 oracle
```

If you see `DATABASE IS READY TO USE!`, Oracle is up. The pre-built `connect()` helper retries automatically; if it's still failing after 5 attempts, restart the kernel and try again.

---

## Codespace and Environment Issues

### Codespace shows "Setting up your Codespace" for more than 10 minutes

**Symptom:** The Codespace is stuck on the loading screen.

**Cause:** First-time Oracle Free image initialisation used to take 3-5 minutes, because the
`:latest` image builds the database on first start. The container now uses the **`:latest-lite`**
image, which ships a pre-built database and is ready in about 30 seconds.

**Fix:** Normally under a minute once the image is in the Docker cache. If it still stalls:

```bash
docker compose -f .devcontainer/docker-compose.yml logs --tail=20 oracle
```

---

### `OPENAI_API_KEY` / `OCI_GENAI_API_KEY` is None

**Symptom:** A cell fails because `os.environ.get(...)` returns `None`.

**Cause:** The Codespaces secret was added after this Codespace was created, so it wasn't injected at startup. (Secrets are only injected at creation time.)

**Fix:** Set the key manually for this session only:

```python
import os
os.environ["OPENAI_API_KEY"] = "sk-..."     # or
os.environ["OCI_GENAI_API_KEY"] = "..."
```

Do not commit this to git. For permanent fixes, stop the Codespace, add the secret in repo settings, and create a new one.

---

### Jupyter kernel "Python 3.11" not found

**Symptom:** The notebook asks you to select a kernel and Python 3.11 isn't listed.

**Fix:**

```bash
pip install -q ipykernel && python -m ipykernel install --user --name python3 --display-name "Python 3.11"
```

Reload the VS Code window (`Cmd/Ctrl + Shift + P` → `Developer: Reload Window`) and select the kernel again.

---

## App returns HTTP 500 / 502

Start with the app's own harness status — it reports the first line of any startup failure:

```bash
curl -s localhost:8000/api/health | python -m json.tool
# {"model": "xai.grok-4.20-non-reasoning", "api_key_set": true,
#  "harness": {"ready": true, "oracle": true, "rerank": false, "error": null}}
```

`harness.ready` only turns true after the schema, semantic catalog and registries are built.
While it is false the status badge says "warming…" and the semantic / retrieval / agent routes fail.

| Symptom | Cause | Fix |
|---|---|---|
| 500 on retrieval, semantic, skills; `harness.error` mentions `DBMS_VECTOR_CHAIN.UTL_TO_EMBEDDINGS` | The vector store was embedding through `DBMS_VECTOR_CHAIN`, which slim images omit, so `initialize()` died before seeding anything | Fixed: `db.py` embeds via the SQL `VECTOR_EMBEDDING(...)` function. Restart the app. |
| `ORA-04042` during setup, then no `ALL_MINILM_L12_V2` in the DB | An unavailable package grant aborted `seed_oracle.py` before the ONNX loader ran | Re-run `python scripts/seed_oracle.py` (the grant is now optional and it reports what it skipped). |
| Agent loop / chat: `404 ... Entity with key xai.grok-4-1-fast-reasoning not found` | Retired model id | Set `LLM_MODEL` in `app/.env` to a live id — `xai.grok-4.20-non-reasoning` (default) — and restart. |
| Chat shows `Chat request failed: cannot use a decompressobj multiple times` | OCI returns the SSE stream `content-encoding: zstd`, which httpx's streaming decoder cannot consume | Fixed: the OpenAI clients send `Accept-Encoding: identity` (`llm_client.py`). Restart the app. |
| Memory routes 500 with `'_InDBOnnxEmbedder' object has no attribute 'embedding_dimension'` | OAMP's embedder must subclass `IEmbedder` so it can infer the dimension | Fixed in `memory.py` (mirrors the notebook's `OracleONNXEmbedder`). Restart the app. |
| Keyword or hybrid retrieval 500s: `ORA-00904: "CONTAINS": invalid identifier` | The image has no Oracle Text (no `CTXSYS` / `CONTAINS`) | Use the 26ai image from `docker-compose.yml`; `db.py` also falls back to substring matching. |
| 500 with `DPY-4011` / `DPY-1001: not connected to database` on the first retrieval, semantic, memory or Layer 8 request after a quiet spell | Oracle closed the idle dedicated OracleVS / OAMP connections, and the process kept the dead sockets | Fixed: `db._store_call()` and `memory._call()` rebuild the connection once and retry the call. Restart the app to pick up the fix. |
| Layer 8 (or chat) shows `No chat-model API key is configured` while `harness.ready` is true | There is no `OCI_GENAI_API_KEY` (or `OPENAI_API_KEY`) in the app environment | Set the key in `app/.env` and restart — or use **Preview assembly (no model call)**: every Layer 8 retrieval and accounting step is database-side and needs no key. |
| 502 from the forwarded URL with nothing in the request log | The app process died or never bound port 8000 | `tail -n 50 /tmp/total-recall-app.log`, then restart (below). |

Restart the appbook in a Codespace (it auto-starts from `app/.env`):

```bash
pkill -f "uvicorn backend.main:app" ; bash .devcontainer/start-app.sh
```

`app/.env` is regenerated from the container environment on every start by
`scripts/write_app_env.sh`, so change `LLM_MODEL` in `.devcontainer/docker-compose.yml` (or restart
the Codespace) for a change to stick.

### The badge stays amber: `harness.ready` is false and `harness.error` names a database problem

**Symptom:** the appbook serves, but `/api/health` reports `"ready": false` with an error such as
`ORA-01017: invalid credential or not authorized; logon denied`, so every semantic, retrieval and
memory route fails.

**Cause:** the app warmed while the database was still being provisioned (a first boot bounces the
instance once, to allocate the vector pool) or before `seed_oracle.py` converged the `AGENT`
credential. Two things used to require a human: `_warm()` gave up after ~5 minutes, and
`start-app.sh` treated any HTTP 200 from `/api/health` as "already running" — so a wedged process
survived every later repair and kept its import-time credentials.

**Fix:** automatic now. `_warm()` retries until the harness is ready, `start-app.sh` restarts a
running app whose payload is not ready, and `seed_oracle.py` retries transient first-boot
interruptions instead of leaving the schema unprovisioned. If you are on an older revision, pull
the current one and run:

```bash
python scripts/seed_oracle.py        # converges the AGENT password + loads the embedder
pkill -f "uvicorn backend.main:app" ; bash .devcontainer/start-app.sh
```

If the seed reports that no admin credential worked, that volume was initialised with a different
`ORACLE_PWD`; recreate it (`docker compose -f .devcontainer/docker-compose.yml down -v`, then the
two commands above) — no SQL repairs it.

### Restarting, rebuilding, or creating a new Codespace

Nothing manual is needed. The database lives in the **`oracle-data-26ai`** volume: a volume created
by a different image cannot be opened by this one, so the name change means an old volume is simply
ignored and a fresh, compatible one is created on the next start. `postCreate.sh` provisions it (AGENT
schema + in-database ONNX embedder) and `start-app.sh` re-checks and re-provisions on **every** start,
so an empty or partially-built volume self-heals instead of leaving the app stuck on "warming…".

If the disk is tight (a Codespace has ~32 GB), reclaim the leftovers — the old data volume and,
if you ever pulled it, the 14 GB `:latest` image:

```bash
docker volume prune       # drops the unused oracle-data volume from the old image
docker image prune -a     # drops images no container references (incl. database/free:latest)
```

A brand-new Codespace needs nothing extra: `onCreate.sh` installs the app dependencies and
downloads the embedder (127 MB) — both of which a Codespaces **prebuild** can snapshot ahead of
time — then `postCreate` provisions the schema and `start-app.sh` starts the appbook, which is why
the port-8000 preview opens on its own. Set `OCI_GENAI_API_KEY` as a Codespaces secret *before*
creating it; without one the appbook still serves, but chat and the agent loop stay disabled.

### Codespace is "running in recovery mode due to a container error"

**Symptom:** The Codespace refuses to open, offering only "Codespaces: View Creation Log" and
"Rebuild Container".

**Cause:** Two things used to make this possible, both now fixed in `.devcontainer/`:

1. The database service was declared as `condition: service_healthy`, so if Oracle never reported
   healthy the `app` container was never started and the dev container failed to come up.
2. The database volume could not be opened (it was initialised by a different Oracle image), which
   kept Oracle unhealthy — which then triggered cause 1.

**Fix:** Rebuild the container (Cmd/Ctrl+Shift+P → "Codespaces: Rebuild Container"). The config now
uses `condition: service_started`, so the app container always starts and simply reports the database
state in `/api/health` instead of blocking the Codespace, and it mounts the fresh `oracle-data-26ai`
volume, so an incompatible volume is left unused rather than mounted.

If it still fails, the creation log is the source of truth (`Codespaces: View Creation Log`). A
failed **image pull** reads as `unauthorized: authentication required`; a failed **disk** as
`no space left on device` — prune as shown above and rebuild.

### `ORA-43853: ... cannot be used in non-automatic segment space management`

**Symptom:** Layer 3/5 return 500 and `/api/health` shows an `ORA-43853` harness error, while
Layer 1 (embeddings) works.

**Cause:** JSON columns, SecureFile LOBs and `VECTOR` columns may only live in a tablespace with
**automatic** segment space management (ASSM). The Free **lite** image ships a pre-built PDB whose
default permanent tablespace is `SYSTEM` (MANUAL) and which has no `USERS` tablespace, so a schema
created without an explicit tablespace cannot hold the harness's tables at all. The full `:latest`
image creates ASSM `USERS` when it builds the database on first start, which is why the same code
worked there.

**Fix:** Applied — `scripts/seed_oracle.py` now creates the ASSM `USERS` tablespace if it is missing
and points `AGENT` at it explicitly (idempotent, and it repairs schemas created before the fix).
Re-run it, or apply it by hand as SYSDBA in `FREEPDB1`:

```sql
CREATE TABLESPACE USERS DATAFILE '<pdb-datafile-dir>/users01.dbf' SIZE 100M REUSE
  AUTOEXTEND ON NEXT 100M MAXSIZE 4G SEGMENT SPACE MANAGEMENT AUTO;   -- REUSE must follow SIZE
ALTER USER AGENT DEFAULT TABLESPACE USERS QUOTA UNLIMITED ON USERS;
```

## OAMP / Memory Issues

### `ValueError: user already exists` (or agent already exists)

**Symptom:** A cell that calls `memory_client.add_user(...)` or `memory_client.add_agent(...)` raises `ValueError: user already exists`.

**Cause:** The user/agent was registered on a prior run; OAMP rejects duplicates.

**Fix:** Wrap the calls in `try/except ValueError` and check for `"already exists"` in the message — see the [Part 2 guide](part-2-oamp-memory.md).

---

### Scanner returns 0 facts

**Symptom:** `run_scan(agent_conn, owner=DEMO_USER)` reports `facts_total = 0`.

**Cause:** The `owner` argument is case-sensitive at the SQL layer (`ALL_TABLES.owner` is always uppercase). Or the AppBook startup did not complete the retail seed.

**Fix:** Confirm the user exists and has tables:

```python
with agent_conn.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM all_tables WHERE owner = 'AGENT'")
    print("table count:", cur.fetchone()[0])
```

It should be greater than zero and include the retail tables. If `0`, run `cd app && ./run.sh` and wait for `harness.ready` to become true.

---

### `retrieve_knowledge` returns no results

**Symptom:** Calling `retrieve_knowledge` after a successful scan returns an empty list.

**Cause:** The ONNX embedder didn't register, or the `kinds=` filter is too restrictive.

**Fix:** Check the embedder is loaded:

```python
with agent_conn.cursor() as cur:
    cur.execute("SELECT model_name FROM user_mining_models")
    print(list(cur))
```

Should include `ALL_MINILM_L12_V2`. If empty, run `python scripts/seed_oracle.py` and wait for the model load to complete.

For `kinds=`, try without a filter first to confirm there are memories at all:

```python
retrieve_knowledge("table", k=5)  # no kinds filter
```

---

## Tool Issues

### `ValueError: tool 'foo' has no docstring`

**Symptom:** `@register` raises this error.

**Cause:** The `_build_schema` helper requires a non-empty docstring — it's the tool's public spec, embedded for retrieval.

**Fix:** Add a docstring describing what the tool does and *when* to call it. "Use this when..." is good phrasing.

---

### `openai.BadRequestError: 400 ... messages with role 'tool' must be a response to a preceding message with 'tool_calls'`

**Symptom:** The agent loop raises this on the second LLM call.

**Cause:** You appended a `tool` message to `messages` without first appending the assistant's `tool_calls` message.

**Fix:** Make sure the loop body is in this order:

1. `resp = chat(messages, tools=tool_schemas)`
2. **Append the assistant message with `tool_calls`** to `messages`
3. For each `tc` in `msg.tool_calls`, dispatch and append a `tool` message with `tool_call_id=tc.id`

See [Part 7 guide](part-7-agent-loop.md) for the exact pattern.

---

### Agent calls the same tool with the same args repeatedly

**Symptom:** The trace shows the same `(tool, args)` over and over until budget exhaustion.

**Cause:** GPT-class models occasionally loop on a tool when the result is empty or unhelpful.

**Fix:** The complete-notebook version of `agent_turn` adds a 3-deep dedupe — short-circuiting identical dispatches. For the workshop demo, keep `max_iterations` low (≤ 8) and let the budget catch it.

---


## Appbook UI Issues

### Layer 8 "Ask the agent" answers render as narrow side-by-side columns

**Symptom:** In *Context Engineering*, an answer with more than one block (heading, paragraph, table, list, code) shows those blocks squashed next to each other in one row instead of stacking in the chat bubble. Short or single-block answers look normal, which is why error-only replies never showed it.

**Cause:** The legacy `.msg { display:flex }` rule, written for the original appbook's avatar + `.bubble` markup, also matched Layer 8's plain-text messages. Every block element `renderRich()` emits became a flex item, so the row layout flattened the answer.

**Fix:** `.cx-log .msg { display:block; }` in `app/frontend/styles.css`. Mission Control guards the same case with `.mc-log .msg.mc-msg`; the Layer 4 memory chat still renders through the unguarded `.msg` rule.

---


## Checking System Status

If something isn't working and you're not sure where, run this diagnostic cell:

```python
import oracledb, os

print("=== Environment ===")
for k in ("OPENAI_API_KEY", "OCI_GENAI_API_KEY", "LLM_PROVIDER", "LLM_MODEL"):
    v = os.environ.get(k)
    print(f"  {k}: {'SET' if v else 'NOT SET'}")

print("\n=== Oracle Connection ===")
try:
    conn = oracledb.connect(user="AGENT", password="AgentPw_2026",
                            dsn="localhost:1521/FREEPDB1")
    cur = conn.cursor()
    cur.execute("SELECT BANNER FROM v$version WHERE rownum = 1")
    print("  AGENT user:", cur.fetchone()[0])
    cur.execute("SELECT model_name FROM user_mining_models")
    print("  ONNX models:", [r[0] for r in cur])
    cur.execute("SELECT COUNT(*) FROM all_tables WHERE owner = 'AGENT'")
    print("  AGENT tables:", cur.fetchone()[0])
    conn.close()
except Exception as e:
    print("  AGENT connection: FAILED:", e)

print("\n=== Vector Memory ===")
try:
    conn = oracledb.connect(user="sys", password="OraclePwd_2025",
                            dsn="localhost:1521/FREEPDB1",
                            mode=oracledb.AUTH_MODE_SYSDBA)
    cur = conn.cursor()
    cur.execute("SELECT value FROM v$parameter WHERE name = 'vector_memory_size'")
    val = int(cur.fetchone()[0] or 0)
    print(f"  vector_memory_size: {val // (1024**2)}M" if val > 0 else "  vector_memory_size: 0 (HNSW will fail)")
    conn.close()
except Exception as e:
    print("  SYS connection: FAILED:", e)
```

Share the output with the facilitator if you need help.
