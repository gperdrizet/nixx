"""Web search tool using a local SearXNG instance."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from nixx.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)


class WebSearchTool(Tool):
    """Search the web via a local SearXNG instance and return titles, URLs, and snippets."""

    def __init__(self, searxng_url: str = "http://localhost:8888", max_results: int = 5) -> None:
        self._searxng_url = searxng_url.rstrip("/")
        self._max_results = max_results

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web. Returns titles, URLs, and text snippets for the top results. "
            "Use this to look up current information, documentation, news, or anything "
            "else that requires a web search."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                }
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        query: str = kwargs.get("query", "")
        if not query:
            return ToolResult(success=False, error="query is required")
        try:
            async with httpx.AsyncClient(
                timeout=20.0,
                headers={"X-Forwarded-For": "127.0.0.1"},
            ) as client:
                resp = await client.get(
                    f"{self._searxng_url}/search",
                    params={"q": query, "format": "json", "language": "en"},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException:
            return ToolResult(success=False, error="Search timed out.")
        except httpx.HTTPError as exc:
            return ToolResult(success=False, error=f"HTTP error: {exc}")
        except Exception as exc:
            return ToolResult(success=False, error=f"Search failed: {exc}")

        raw_results = data.get("results", [])[: self._max_results]
        if not raw_results:
            return ToolResult(success=True, result="No results found.")

        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(raw_results, 1):
            title = r.get("title", "").strip()
            url = r.get("url", "").strip()
            snippet = r.get("content", "").strip()
            if not title or not url:
                continue
            lines.append(f"{i}. {title}")
            lines.append(f"   {url}")
            if snippet:
                lines.append(f"   {snippet}")
        return ToolResult(success=True, result="\n".join(lines))
