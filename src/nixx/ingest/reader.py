"""Compatibility shim: read() delegates to the built-in handler registry.

Prefer using HandlerRegistry directly (via IngestPipeline). This module
exists so that code and tests written against the original reader API
continue to work unchanged.
"""

from __future__ import annotations

from nixx.ingest.handlers import HandlerRegistry

_registry = HandlerRegistry()


async def read(source: str) -> tuple[str, str]:
    """Return (text, kind) for a file path or URL.

    Delegates to the first matching handler in the default registry
    (no plugin directory - built-ins only).
    """
    handler = _registry.get_handler(source)
    return await handler.read(source)
