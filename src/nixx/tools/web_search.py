"""Web search tool using DuckDuckGo HTML endpoint."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from bs4 import BeautifulSoup

from nixx.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)

_DDG_URL = "https://html.duckduckgo.com/html/"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Accept-Language": "en-US,en;q=0.9",
}


class WebSearchTool(Tool):
    """Search the web via DuckDuckGo and return titles, URLs, and snippets."""

    def __init__(self, max_results: int = 5) -> None:
        self._max_results = max_results

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web using DuckDuckGo. Returns titles, URLs, and text snippets "
            "for the top results. Use this to look up current information, documentation, "
            "news, or anything else that requires a web search."
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
                headers=_HEADERS, follow_redirects=True, timeout=15.0
            ) as client:
                resp = await client.post(_DDG_URL, data={"q": query})
                resp.raise_for_status()
        except httpx.TimeoutException:
            return ToolResult(success=False, error="Search timed out.")
        except httpx.HTTPError as exc:
            return ToolResult(success=False, error=f"HTTP error: {exc}")

        soup = BeautifulSoup(resp.text, "html.parser")
        results = []

        for result in soup.select(".result")[: self._max_results]:
            title_el = result.select_one(".result__title a")
            snippet_el = result.select_one(".result__snippet")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            url = title_el.get("href", "")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            if title and url:
                results.append({"title": title, "url": url, "snippet": snippet})

        if not results:
            return ToolResult(success=True, result="No results found.")

        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   {r['url']}")
            if r["snippet"]:
                lines.append(f"   {r['snippet']}")
        return ToolResult(success=True, result="\n".join(lines))
