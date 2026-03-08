"""Read content from a file path or URL and return plain text."""

from __future__ import annotations

from pathlib import Path

import httpx
from bs4 import BeautifulSoup

# File extensions treated as plain text without any transformation.
_TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".rst",
    ".py",
    ".js",
    ".ts",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".sh",
    ".html",
    ".htm",
    ".css",
    ".go",
    ".rs",
    ".c",
    ".cpp",
    ".h",
    ".java",
    ".rb",
    ".sql",
}


async def read(source: str) -> tuple[str, str]:
    """Return (text, kind) for a file path or URL.

    kind is 'document' for local files and 'web' for URLs.
    Raises ValueError for unsupported file types.
    Raises httpx.HTTPError for unreachable URLs.
    """
    if source.startswith("http://") or source.startswith("https://"):
        return await _fetch_url(source), "web"
    return _read_file(Path(source)), "document"


def _read_file(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    suffix = path.suffix.lower()
    if suffix not in _TEXT_SUFFIXES:
        raise ValueError(
            f"Unsupported file type: {suffix}. " f"Supported: {', '.join(sorted(_TEXT_SUFFIXES))}"
        )
    text = path.read_text(encoding="utf-8", errors="replace")
    # Strip HTML tags for .html/.htm files.
    if suffix in {".html", ".htm"}:
        text = _strip_html(text)
    return text


async def _fetch_url(url: str) -> str:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(
            url, headers={"User-Agent": "nixx-ingest/0.1 (https://github.com/gperdrizet/nixx)"}
        )
        response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "html" in content_type:
        return _strip_html(response.text)
    return response.text


def _strip_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    # Remove script and style blocks before extracting text.
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text(separator="\n").splitlines())
    return "\n".join(line for line in lines if line)
