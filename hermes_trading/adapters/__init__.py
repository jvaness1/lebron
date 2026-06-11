"""Data adapters.

Each adapter exposes `async def fetch(...) -> dict` and every payload carries
a `schema_version` field. If a payload's schema_version doesn't match what the
caller expects, the adapter raises SchemaError so the loop halts loudly rather
than trading on data it doesn't understand.

All adapters default to free, public, keyless endpoints. Premium keys (if set
in .env) override the defaults but are never required.
"""
from __future__ import annotations


class SchemaError(RuntimeError):
    """Raised when an adapter payload doesn't match the expected schema_version."""


def require_schema(payload: dict, expected: str, *, source: str) -> dict:
    got = payload.get("schema_version")
    if got != expected:
        raise SchemaError(f"{source}: expected schema_version {expected!r}, got {got!r}")
    return payload


from . import price, onchain, news, macro  # noqa: E402  (re-export for convenience)

__all__ = ["SchemaError", "require_schema", "price", "onchain", "news", "macro"]
