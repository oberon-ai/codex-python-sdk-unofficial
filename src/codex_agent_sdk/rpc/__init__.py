"""JSON-RPC framing and connection management layer."""

from .jsonrpc import JsonRpcEnvelope, parse_jsonrpc_envelope

__all__ = [
    "JsonRpcEnvelope",
    "parse_jsonrpc_envelope",
]
