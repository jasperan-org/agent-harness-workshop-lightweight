#!/usr/bin/env bash
# Runs once after the container is created (and after onCreate; a Codespaces *prebuild* stops at
# onCreate, which is why the install steps live in .devcontainer/onCreate.sh instead of here).
# Everything in this file needs a live database.
set -euo pipefail

echo "▸ Writing app/.env so the appbook reads the same env the notebook uses…"
# The appbook is auto-started by a non-interactive lifecycle hook that doesn't reliably inherit the
# OCI/Oracle env; a real app/.env (loaded by backend/config.py) makes its config match the notebook.
bash scripts/write_app_env.sh || true

echo "▸ Provisioning the Oracle AI Database (AGENT user + in-DB ONNX embedder)…"
# Creates the least-privilege AGENT schema and loads the 384-dim embedder so the appbook can warm.
# Idempotent and retrying — safe to re-run. The appbook builds its own tables / registries / seeded
# commerce schema on startup; the notebook builds the same harness as you work through it.
python scripts/seed_oracle.py || echo "  ⚠ Oracle provisioning did NOT complete — the in-DB ONNX embedder may be missing, which breaks every semantic/retrieval call. Re-run: python scripts/seed_oracle.py"

cat <<'EOF'

✓ Setup complete.
  • The appbook auto-starts on port 8000 (a preview opens). Restart it:  cd app && ./run.sh
  • App log:        /tmp/total-recall-app.log
  • Build the harness yourself:  notebook_student.ipynb   (answer key: notebook_complete.ipynb)
  • Core guides:  docs/part-1-setup.md … docs/part-7-agent-loop.md
  • Advanced reference:  enterprise_data_agent.ipynb, plus docs/part-4-dbfs.md, docs/part-5-mle.md, docs/part-9-duality-views.md, docs/part-11-tool-output-offload.md
EOF
