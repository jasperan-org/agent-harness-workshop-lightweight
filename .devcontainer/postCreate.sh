#!/usr/bin/env bash
# Runs once when the Codespace / dev container is created.
set -euo pipefail

echo "▸ Installing the appbook dependencies…"
python -m pip install --upgrade pip
python -m pip install -r app/requirements.txt

echo "▸ Installing the notebook-only dependencies (agent loop, durable graph state, charts)…"
# The appbook ships the runtime deps; the notebook additionally needs LangGraph + the Oracle
# checkpointer, the chat-model bindings, and matplotlib for the context-engineering chart.
python -m pip install jupyterlab ipykernel \
  langgraph langgraph-oracledb langchain langchain-openai openai \
  matplotlib pandas onnx nbconvert

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
  • Advanced reference:  notebook_complete_with_setup_code.ipynb, enterprise_data_agent.ipynb, and docs/part-4-dbfs.md, docs/part-5-mle.md, docs/part-9-duality-views.md, docs/part-11-tool-output-offload.md
EOF
