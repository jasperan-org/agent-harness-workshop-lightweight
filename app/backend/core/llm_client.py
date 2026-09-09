"""Shared LLM client — OCI Generative AI via its OpenAI-compatible endpoint, so Oracle powers the
model too (not just embeddings / retrieval / memory). Set LLM_PROVIDER=openai to use OpenAI directly.

Key rotation: when multiple OCI keys are configured (OCI_GENAI_API_KEY[_2/_3/_4]), each app process
picks a random starting key and fails over to the next on a 429 / rate-limit / auth error — so a
large workshop cohort spreads load across the keys. With a single key it's a transparent pass-through.

`client` and `async_client` keep the exact `.chat.completions.create(...)` surface the routers use;
the rotation happens inside that call. Uses a placeholder key when none is set so the app still
imports and serves the frontend (chat then fails with a clear error rather than crashing startup).
"""
from __future__ import annotations

import sys
from pathlib import Path

from openai import AsyncOpenAI, OpenAI

from backend.config import settings

# Import the shared rotation module from the repo root (one dir above app/).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
try:
    from oci_key_rotation import (KeyRotator, call_with_failover,
                                  call_with_failover_async, load_oci_keys)
    _ROTATION = True
except Exception:  # pragma: no cover — rotation module not found; fall back to single key
    _ROTATION = False

_base = settings.oci_endpoint if settings.llm_provider == "oci" else None
MODEL = settings.model
MAX_TOKENS = settings.max_tokens

_keys = load_oci_keys() if (_ROTATION and settings.llm_provider == "oci") else []
_rotator = KeyRotator(_keys) if _keys else None


def text_of(response) -> str:
    """Extract the assistant text from an OpenAI ChatCompletion response."""
    try:
        return response.choices[0].message.content or ""
    except Exception:
        return ""


if _rotator is not None:
    # ---- rotating proxies: same .chat.completions.create(...) surface, with failover ----
    class _RotatingCompletions:
        def __init__(self, build):
            self._build = build  # build(key) -> a fresh OpenAI/AsyncOpenAI client

        def create(self, **kwargs):
            return call_with_failover(
                self._build, lambda c: c.chat.completions.create(**kwargs), _rotator,
                on_event=lambda m: print(" ", m),
            )

    class _RotatingAsyncCompletions:
        def __init__(self, build):
            self._build = build

        async def create(self, **kwargs):
            return await call_with_failover_async(
                self._build, lambda c: c.chat.completions.create(**kwargs), _rotator,
                on_event=lambda m: print(" ", m),
            )

    class _RotatingChat:
        def __init__(self, completions):
            self.completions = completions

    class _RotatingClient:
        def __init__(self, build, async_=False):
            comp = (_RotatingAsyncCompletions if async_ else _RotatingCompletions)(build)
            self.chat = _RotatingChat(comp)

    client = _RotatingClient(lambda key: OpenAI(api_key=key, base_url=_base))
    async_client = _RotatingClient(lambda key: AsyncOpenAI(api_key=key, base_url=_base), async_=True)
    print(f"[llm_client] OCI key rotation active: {len(_rotator)} key(s); "
          f"this worker starts on key #{_rotator.current_index() + 1}.")
else:
    # ---- single-key path (no rotation configured / non-OCI provider) ----
    _key = settings.llm_api_key or "LLM_API_KEY_NOT_SET"
    client = OpenAI(api_key=_key, base_url=_base)
    async_client = AsyncOpenAI(api_key=_key, base_url=_base)
