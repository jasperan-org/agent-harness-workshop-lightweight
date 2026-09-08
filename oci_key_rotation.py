"""Shared OCI Generative AI API-key rotation for the notebook and app.

Each process starts on a random configured key, then sticks with the active key
until a rate-limit, authentication, quota, overload, connection, or timeout
error advances it to the next key. The environment supports
``OCI_GENAI_API_KEY`` plus numbered slots through ``_8``.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any, Callable


def load_oci_keys() -> list[str]:
    """Collect configured OCI keys in order, de-duplicated and sanitized."""
    found: list[str] = []
    for name in ("OCI_GENAI_API_KEY", "OCI_GENAI_API_KEY_1"):
        value = (os.environ.get(name) or "").strip()
        if value and "REPLACE_ME" not in value and "NOT_SET" not in value:
            found.append(value)
            break
    for index in range(2, 9):
        value = (os.environ.get(f"OCI_GENAI_API_KEY_{index}") or "").strip()
        if value and "REPLACE_ME" not in value and "NOT_SET" not in value:
            found.append(value)

    keys: list[str] = []
    seen: set[str] = set()
    for value in found:
        if value not in seen:
            seen.add(value)
            keys.append(value)
    return keys


def is_rate_limit_error(exc: BaseException) -> bool:
    """Return whether an exception is worth failing over to another key."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status in (401, 403, 429, 500, 502, 503, 504, "401", "403", "429"):
        return True
    message = str(exc).lower()
    return any(token in message for token in (
        "429", "rate limit", "too many requests", "quota", "throttl",
        "temporarily", "overloaded", "service unavailable", "unauthorized",
        "forbidden", "connection", "timeout",
    ))


class KeyRotator:
    """Random starting key with sticky circular failover."""

    def __init__(self, keys: list[str], seed: Any | None = None):
        if not keys:
            raise RuntimeError(
                "No OCI GenAI API keys found. Set OCI_GENAI_API_KEY and optionally "
                "OCI_GENAI_API_KEY_2.._8."
            )
        self.keys = keys
        self._idx = random.Random(seed).randrange(len(keys))

    def current(self) -> str:
        return self.keys[self._idx]

    def current_index(self) -> int:
        return self._idx

    def advance(self) -> str:
        self._idx = (self._idx + 1) % len(self.keys)
        return self.current()

    def __len__(self) -> int:
        return len(self.keys)


def call_with_failover(
    make_client: Callable[[str], Any],
    do_call: Callable[[Any], Any],
    rotator: KeyRotator,
    *,
    base_delay: float = 0.8,
    on_event: Callable[[str], None] | None = None,
) -> Any:
    """Retry a call across at most two sweeps of the configured keys."""
    attempts = len(rotator) * 2
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return do_call(make_client(rotator.current()))
        except Exception as exc:  # noqa: BLE001 - re-raise after failover
            last = exc
            if not is_rate_limit_error(exc) or attempt == attempts - 1:
                raise
            rotator.advance()
            if on_event:
                on_event(
                    f"[key-rotation] switching to key #{rotator.current_index() + 1} "
                    f"of {len(rotator)} after {type(exc).__name__}"
                )
            time.sleep(base_delay * (2 ** min(attempt, 4)))
    raise last  # pragma: no cover


async def call_with_failover_async(
    make_client: Callable[[str], Any],
    do_call: Callable[[Any], Any],
    rotator: KeyRotator,
    *,
    base_delay: float = 0.8,
    on_event: Callable[[str], None] | None = None,
) -> Any:
    """Async counterpart to :func:`call_with_failover`."""
    import asyncio

    attempts = len(rotator) * 2
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return await do_call(make_client(rotator.current()))
        except Exception as exc:  # noqa: BLE001 - re-raise after failover
            last = exc
            if not is_rate_limit_error(exc) or attempt == attempts - 1:
                raise
            rotator.advance()
            if on_event:
                on_event(
                    f"[key-rotation] switching to key #{rotator.current_index() + 1} "
                    f"of {len(rotator)} after {type(exc).__name__}"
                )
            await asyncio.sleep(base_delay * (2 ** min(attempt, 4)))
    raise last  # pragma: no cover


def make_rotating_oamp_llm(model: str, endpoint: str, rotator: KeyRotator):
    """Build an OAMP-compatible LLM wrapper that rotates OCI keys on failure."""
    from oracleagentmemory.core.llms import Llm

    def make_llm(key: str):
        return Llm(f"openai/{model}", api_base=endpoint, api_key=key)

    class RotatingOAMPLlm:
        def generate(self, *args, **kwargs):
            return call_with_failover(
                make_llm,
                lambda client: client.generate(*args, **kwargs),
                rotator,
            )

        async def generate_async(self, *args, **kwargs):
            return await call_with_failover_async(
                make_llm,
                lambda client: client.generate_async(*args, **kwargs),
                rotator,
            )

        def __getattr__(self, name):
            return getattr(make_llm(rotator.current()), name)

    return RotatingOAMPLlm()
