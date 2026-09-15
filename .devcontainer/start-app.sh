#!/usr/bin/env bash
# Runs on every container start — provision-if-needed, then auto-start the appbook so the
# frontend loads. Both steps are idempotent and safe to re-run.
HERE="$(dirname "$0")"

# Give the appbook the SAME env the notebook has. This lifecycle hook is a non-interactive shell, so
# it doesn't reliably inherit the OCI/Oracle env that interactive sessions (and the notebook kernel)
# get — which is why the appbook's OCI calls were 502-ing. Materialise app/.env from whatever env IS
# visible; backend/config.py loads it so the server reads identical config. (Idempotent.)
bash "$HERE/../scripts/write_app_env.sh" || true

# Make the harness present before starting the app. The database lives in a named volume: it
# survives restarts, but a recreated/empty volume (docker compose down -v, a rebuilt container) or
# a fresh Codespace whose postCreate seeding raced the database leaves the AGENT user or the ONNX
# embedder missing — every semantic / retrieval / memory call then fails while the UI still serves.
# Sourcing app/.env first means this works even when the lifecycle shell has no Oracle env.
ENV_FILE="$HERE/../app/.env"
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
if ! python - <<'PY' 2>/dev/null
import os, sys, oracledb
oracledb.defaults.fetch_lobs = False
try:
    conn = oracledb.connect(user=os.environ.get("ORA_AGENT_USER", "AGENT"),
                            password=os.environ.get("ORA_AGENT_PWD", "AgentPw_2026"),
                            dsn=os.environ.get("ORA_DSN", "localhost:1521/FREEPDB1"))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM user_mining_models WHERE model_name=:m",
                {"m": os.environ.get("EMBED_MODEL", "ALL_MINILM_L12_V2")})
    ready = cur.fetchone()[0] > 0
    conn.close()
except Exception as e:
    # Say *why* the harness looks unprovisioned: a rejected login (a volume whose AGENT password
    # drifted, or an account locked by failed logins) needs the seed's credential convergence, not
    # just a model load — and it is the difference between "wait for it" and "fix it".
    first = str(e).splitlines()[0]
    if any(code in first for code in ("ORA-01017", "ORA-28000", "ORA-28001")):
        print(f"  {os.environ.get('ORA_AGENT_USER', 'AGENT')} login rejected: {first}")
        print("  → running the seed, which converges the password and unlocks the account")
    else:
        print(f"  harness not ready: {first}")
    ready = False
sys.exit(0 if ready else 1)
PY
then
  echo "▸ Oracle harness not provisioned — running scripts/seed_oracle.py (idempotent)…"
  python "$HERE/../scripts/seed_oracle.py" \
    || echo "  ⚠ seeding did not finish — the app will start but semantic layers stay warming. Re-run: python scripts/seed_oracle.py"
fi

cd "$HERE/../app" || exit 0

if curl -sf -o /dev/null http://127.0.0.1:8000/api/health 2>/dev/null; then
  echo "▸ Appbook already running on port 8000."
  exit 0
fi

# Launch fully detached so the server survives this lifecycle hook exiting.
# HOST=0.0.0.0 (set by docker-compose) makes the port forwardable in Codespaces.
if command -v setsid >/dev/null 2>&1; then
  setsid nohup python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 </dev/null >/tmp/total-recall-app.log 2>&1 &
else
  nohup python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 </dev/null >/tmp/total-recall-app.log 2>&1 &
fi
disown 2>/dev/null || true

for _ in $(seq 1 25); do
  sleep 1
  if curl -sf -o /dev/null http://127.0.0.1:8000/api/health 2>/dev/null; then
    echo "✓ Appbook is up on port 8000 — the preview will open."
    echo "  (The harness warms in a background thread; the status badge turns green once the DB is ready.)"
    exit 0
  fi
done

echo "⚠ Appbook did not bind to port 8000 within 25s. Last log lines:"
tail -n 30 /tmp/total-recall-app.log 2>/dev/null || true
exit 0 # never fail the lifecycle hook
