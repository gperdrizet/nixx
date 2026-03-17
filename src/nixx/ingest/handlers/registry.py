"""Handler registry for ingest.

Built-in handlers are registered in priority order.
"""

from __future__ import annotations

import logging

from nixx.ingest.handlers.base import IngestHandler
from nixx.ingest.handlers.file import FileHandler
from nixx.ingest.handlers.web import WebHandler

logger = logging.getLogger(__name__)

# Built-in handlers in priority order. FileHandler is last - it matches
# anything without "://" so it acts as the default fallback.
_BUILTIN_HANDLERS: list[IngestHandler] = [
    WebHandler(),
    FileHandler(),
]


class HandlerRegistry:
    """Ordered list of handlers. First match wins."""

    def __init__(self) -> None:
        self._handlers: list[IngestHandler] = list(_BUILTIN_HANDLERS)

    def get_handler(self, source: str) -> IngestHandler:
        """Return the first handler that can handle source.

        Raises ValueError if no handler matches (shouldn't happen with
        FileHandler as fallback, but protects against misconfiguration).
        """
        for handler in self._handlers:
            if handler.can_handle(source):
                return handler
        raise ValueError(
            f"No handler found for: {source!r}. "
            f"Registered handlers: {[h.name for h in self._handlers]}"
        )

    @property
    def handlers(self) -> list[IngestHandler]:
        return list(self._handlers)
