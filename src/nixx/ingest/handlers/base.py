"""Base class for ingest handlers.

Subclasses must implement:
- name: a short identifier (e.g. "web", "file")
- can_handle(source) -> bool
- read(source) -> (text, kind)

Optionally override chunk() to use a different splitting strategy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from nixx.ingest.chunker import chunk as default_chunk


class IngestHandler(ABC):
    """Abstract base for all ingest handlers."""

    #: Short identifier used in logging and error messages.
    name: str = "base"

    @abstractmethod
    def can_handle(self, source: str) -> bool:
        """Return True if this handler knows how to read source."""
        ...

    @abstractmethod
    async def read(self, source: str) -> tuple[str, str]:
        """Return (text, kind) for the given source.

        kind is a short string stored in sources.type, e.g. "web", "document".
        Raise FileNotFoundError or ValueError for bad input.
        Raise httpx.HTTPError for network failures.
        """
        ...

    def chunk(self, text: str) -> list[str]:
        """Split text into chunks ready for embedding.

        Override to use a different strategy (e.g. split by file for repos).
        """
        return default_chunk(text)
