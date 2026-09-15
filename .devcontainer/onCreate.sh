#!/usr/bin/env bash
# Runs ONCE, before `postCreate.sh`.
#
# This split exists so that **Codespaces prebuilds** can do the slow work ahead of time: a prebuild
# runs the configuration up to and including `onCreateCommand`, then snapshots the container.
# (`postCreateCommand` is explicitly NOT part of a prebuild.) So everything slow that needs neither
# a database nor an API key lives here — Python dependencies and the 127 MB ONNX embedder — and
# everything that needs a live database stays in postCreate.sh.
#
# With a prebuild configured (repo Settings → Codespaces → Prebuild), a new codespace then starts
# with the dependencies and the embedder already in place, and only has to load the model into
# Oracle. Without a prebuild this is simply the first step of container creation, exactly as before.
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

echo "▸ Pre-downloading the in-database ONNX embedder (127 MB)…"
# seed_oracle.py loads this file into Oracle; fetching it here means a prebuild snapshot carries it,
# so a new codespace never pays for the download.
python - <<'PY'
import sys

sys.path.insert(0, "scripts")
import seed_oracle  # noqa: E402  (no side effects: main() is __main__-guarded)

print("  " + seed_oracle.ensure_model_file(seed_oracle.EMBED_ONNX_PATH, seed_oracle.EMBED_ONNX_URL))
PY

echo "✓ Dependencies and embedder are in place."
