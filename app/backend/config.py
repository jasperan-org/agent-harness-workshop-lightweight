"""Central configuration for the Total Recall appbook.

Reads the same environment the notebook uses (Oracle creds, Anthropic key, model
names) so the app runs against the very harness the notebook builds. A single
``settings`` object is imported across the backend.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent.parent          # .../appbook
BACKEND_DIR = APP_DIR / "backend"
FRONTEND_DIR = APP_DIR / "frontend"

# Load .env in priority order (app-local wins, then the project root).
for candidate in (APP_DIR.parent / ".env", APP_DIR / ".env"):
    if candidate.exists():
        load_dotenv(candidate, override=True)


class Settings:
    # Oracle AI Database (the harness the notebook built).
    ora_user: str = os.environ.get("ORA_AGENT_USER", "AGENT")
    ora_password: str = os.environ.get("ORA_AGENT_PWD", "AgentPw_2026")
    ora_dsn: str = os.environ.get("ORA_DSN", "localhost:1521/FREEPDB1")
    oracle_enabled: bool = os.environ.get("ORACLE_ENABLED", "1") not in {"0", "false", "False"}

    # Models (loaded into the database by the notebook).
    embed_model: str = os.environ.get("EMBED_MODEL", "ALL_MINILM_L12_V2")
    rerank_model: str = os.environ.get("RERANK_MODEL", "RERANK_XENC")
    vector_dim: int = int(os.environ.get("VECTOR_DIM", "384"))

    # Cognitive memory (OAMP).
    oamp_prefix: str = os.environ.get("OAMP_PREFIX", "OAMP_")

    # Chat model — OCI Generative AI via its OpenAI-compatible endpoint (Oracle powers the model too).
    # The only outbound network call. Set LLM_PROVIDER=openai to use OpenAI directly instead.
    llm_provider: str = os.environ.get("LLM_PROVIDER", "oci")
    oci_endpoint: str = os.environ.get(
        "OCI_GENAI_ENDPOINT", "https://inference.generativeai.us-phoenix-1.oci.oraclecloud.com"
    )
    # OCI_GENAI_API_KEY when LLM_PROVIDER=oci; OPENAI_API_KEY when LLM_PROVIDER=openai.
    llm_api_key: str | None = os.environ.get("OCI_GENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    model: str = os.environ.get("LLM_MODEL", "xai.grok-4-1-fast-reasoning")
    max_tokens: int = int(os.environ.get("TR_MAX_TOKENS", "1536"))

    # Identity used for OAMP memory in the app.
    user_id: str = os.environ.get("TR_USER_ID", "appbook_user")
    agent_id: str = os.environ.get("TR_AGENT_ID", "total_recall")


settings = Settings()
