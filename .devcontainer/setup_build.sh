#!/bin/bash
set -e

echo "============================================"
echo "  Enterprise Data Agent Workshop — Build"
echo "============================================"

WORKSPACE="${WORKSPACE:-$(pwd)}"

echo ""
echo "[1/5] Installing workshop notebook dependencies..."
pip install -q --no-cache-dir -r "$WORKSPACE/requirements.txt"

echo ""
echo "[2/5] Installing app backend dependencies..."
pip install -q --no-cache-dir -r "$WORKSPACE/app/backend/requirements.txt"

echo ""
echo "[3/5] Installing AppBook dependencies..."
pip install -q --no-cache-dir -r "$WORKSPACE/appbook/requirements.txt"

echo ""
echo "[4/5] Registering Jupyter kernel..."
python -m ipykernel install --user --name python3 --display-name "Python 3.11"

echo ""
echo "[5/5] Installing app frontend dependencies (npm)..."
cd "$WORKSPACE/app/frontend"
npm install --no-audit --no-fund --silent
cd "$WORKSPACE"

echo ""
echo "Build complete."
echo "  • Workshop notebook deps installed."
echo "  • App backend (Python) deps installed."
echo "  • AppBook (FastAPI) deps installed."
echo "  • App frontend (npm) deps installed."
echo "  Oracle + bootstrap + seed run on first start (postCreateCommand)."
echo "============================================"
