#!/usr/bin/env bash
# Runs on every container start — auto-starts the appbook so the frontend loads.
HERE="$(dirname "$0")"

# Give the appbook the SAME env the notebook has. This lifecycle hook is a non-interactive shell, so
# it doesn't reliably inherit the OCI/Oracle env that interactive sessions (and the notebook kernel)
# get — which is why the appbook's OCI calls were 502-ing. Materialise app/.env from whatever env IS
# visible; backend/config.py loads it so the server reads identical config. (Idempotent.)
bash "$HERE/../scripts/write_app_env.sh" || true

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
