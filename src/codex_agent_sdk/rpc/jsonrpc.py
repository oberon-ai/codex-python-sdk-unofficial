"""Minimal JSON-RPC envelope helpers.

The transport layer owns byte-oriented JSONL framing. This module owns the first
typed step above that boundary: turning one decoded JSON line into one raw
JSON-RPC envelope object without yet imposing app-server-specific schema models.
"""

from __future__ import annotations

import json
from typing import Any, cast

from ..errors import MessageDecodeError

JsonRpcEnvelope = dict[str, Any]


def parse_jsonrpc_envelope(
    line: str,
    *,
    stderr_tail: str | None = None,
) -> JsonRpcEnvelope:
    """Parse one decoded JSONL line into a raw JSON-RPC envelope object.

    The helper intentionally validates only the minimal invariant the next layer
    depends on: each frame must decode to a top-level JSON object.
    """

    try:
        parsed = json.loads(line)
    except json.JSONDecodeError as exc:
        raise MessageDecodeError(
            line,
            original_error=exc,
            stderr_tail=stderr_tail,
        ) from exc

    if not isinstance(parsed, dict):
        error = ValueError("JSON-RPC envelope must be a JSON object")
        raise MessageDecodeError(
            line,
            original_error=error,
            stderr_tail=stderr_tail,
        ) from error

    return cast(JsonRpcEnvelope, parsed)


__all__ = [
    "JsonRpcEnvelope",
    "parse_jsonrpc_envelope",
]
