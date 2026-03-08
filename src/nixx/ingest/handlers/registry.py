"""Handler registry with plugin auto-discovery.

Built-in handlers are registered in priority order. User-defined handlers
are loaded from NixxConfig.handlers_dir (default: ~/.config/nixx/handlers/)
and prepended so they take priority.

To add a custom handler, create a .py file in handlers_dir containing a
class that subclasses IngestHandler. No registration needed - it's picked
up automatically at server startup.

Example (~/.config/nixx/handlers/notion.py)::

    from nixx.ingest.handlers.base import IngestHandler

    class NotionHandler(IngestHandler):
        name = "notion"

        def can_handle(self, source: str) -> bool:
            return "notion.so" in source

        async def read(self, source: str) -> tuple[str, str]:
            ...  # call Notion API, return (text, "notion")
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
from pathlib import Path

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

    def __init__(self, handlers_dir: Path | None = None) -> None:
        self._handlers: list[IngestHandler] = []
        if handlers_dir is not None:
            self._load_plugins(handlers_dir)
        self._handlers.extend(_BUILTIN_HANDLERS)

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

    def _load_plugins(self, handlers_dir: Path) -> None:
        """Import any IngestHandler subclasses found in handlers_dir/*.py."""
        if not handlers_dir.is_dir():
            return
        for py_file in sorted(handlers_dir.glob("*.py")):
            try:
                spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)  # type: ignore[union-attr]
                for _name, obj in inspect.getmembers(module, inspect.isclass):
                    if (
                        issubclass(obj, IngestHandler)
                        and obj is not IngestHandler
                        and obj.name != "base"
                    ):
                        self._handlers.append(obj())
                        logger.info("Loaded plugin handler: %s from %s", obj.name, py_file)
            except Exception:
                logger.exception("Failed to load handler plugin: %s", py_file)
