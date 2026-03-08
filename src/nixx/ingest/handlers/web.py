"""Web handler: fetch a URL, strip HTML, return plain text."""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from nixx.ingest.handlers.base import IngestHandler


class WebHandler(IngestHandler):
    """Handles http:// and https:// URLs."""

    name = "web"

    def can_handle(self, source: str) -> bool:
        return source.startswith("http://") or source.startswith("https://")

    async def read(self, source: str) -> tuple[str, str]:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(
                source,
                headers={"User-Agent": "nixx-ingest/0.1 (https://github.com/gperdrizet/nixx)"},
            )
            response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "html" in content_type:
            return _strip_html(response.text), "web"
        return response.text, "web"


def _strip_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text(separator="\n").splitlines())
    return "\n".join(line for line in lines if line)
