"""Tool for fetching and reading a webpage as plain text."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from bs4 import BeautifulSoup

from nixx.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Accept-Language": "en-US,en;q=0.9",
}
_MAX_CHARS = 8000


def _strip_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text(separator="\n").splitlines())
    return "\n".join(line for line in lines if line)


class ReadWebpageTool(Tool):
    """Fetch a URL and return its text content, stripped of HTML."""

    @property
    def name(self) -> str:
        return "read_webpage"

    @property
    def description(self) -> str:
        return (
            "Fetch a URL and return the page content as plain text, stripped of HTML. "
            f"Returns up to {_MAX_CHARS} characters. Use this to read the full content "
            "of a search result or any other webpage."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch.",
                }
            },
            "required": ["url"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        url: str = kwargs.get("url", "")
        if not url or not url.startswith(("http://", "https://")):
            return ToolResult(
                success=False, error=f"Invalid URL (must start with http/https): {url}"
            )
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS, follow_redirects=True, timeout=20.0
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except httpx.TimeoutException:
            return ToolResult(success=False, error="Request timed out.")
        except httpx.HTTPStatusError as exc:
            return ToolResult(success=False, error=f"HTTP {exc.response.status_code}: {url}")
        except httpx.HTTPError as exc:
            return ToolResult(success=False, error=f"HTTP error: {exc}")

        content_type = resp.headers.get("content-type", "")
        if "html" in content_type:
            text = _strip_html(resp.text)
        else:
            text = resp.text

        if not text.strip():
            return ToolResult(success=True, result="Page returned no readable content.")

        truncated = text[:_MAX_CHARS]
        suffix = f"\n\n[truncated at {_MAX_CHARS} chars]" if len(text) > _MAX_CHARS else ""
        return ToolResult(success=True, result=truncated + suffix)
