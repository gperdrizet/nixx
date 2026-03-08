"""Ingest handler registry.

Built-in handlers are registered in priority order. User-defined handlers
(loaded from NixxConfig.handlers_dir) are prepended so they take precedence.

Usage::

    registry = HandlerRegistry()
    handler = registry.get_handler("https://example.com")
    text, kind = await handler.read("https://example.com")
    chunks = handler.chunk(text)
"""

from nixx.ingest.handlers.base import IngestHandler
from nixx.ingest.handlers.registry import HandlerRegistry

__all__ = ["IngestHandler", "HandlerRegistry"]
