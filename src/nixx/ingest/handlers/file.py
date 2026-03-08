"""File handler: read a local file and return plain text."""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

from nixx.ingest.handlers.base import IngestHandler

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


class FileHandler(IngestHandler):
    """Handles local file paths. Acts as the fallback handler."""

    name = "file"

    def can_handle(self, source: str) -> bool:
        # Matches anything that isn't a URL scheme - acts as default fallback.
        return "://" not in source

    async def read(self, source: str) -> tuple[str, str]:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        suffix = path.suffix.lower()
        if suffix not in _TEXT_SUFFIXES:
            raise ValueError(
                f"Unsupported file type: {suffix}. "
                f"Supported: {', '.join(sorted(_TEXT_SUFFIXES))}"
            )
        text = path.read_text(encoding="utf-8", errors="replace")
        if suffix in {".html", ".htm"}:
            text = _strip_html(text)
        return text, "document"


def _strip_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text(separator="\n").splitlines())
    return "\n".join(line for line in lines if line)
