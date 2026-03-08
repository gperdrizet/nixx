"""Split text into overlapping chunks suitable for embedding."""

from __future__ import annotations

# Target chunk size in characters. At ~4 chars/token this is roughly 375 tokens,
# well within mxbai-embed-large's 512-token context window.
_CHUNK_SIZE = 1500
_OVERLAP = 200


def chunk(text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = _OVERLAP) -> list[str]:
    """Split text into overlapping chunks, preferring paragraph boundaries.

    Tries to split on double-newlines first so chunks don't cut mid-paragraph.
    Falls back to hard character splits if a paragraph is longer than chunk_size.
    Returns a list of non-empty strings.
    """
    if not text.strip():
        return []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        # If a single paragraph exceeds the chunk size, hard-split it.
        if len(para) > chunk_size:
            if current:
                chunks.append(current.strip())
                current = ""
            for i in range(0, len(para), chunk_size - overlap):
                piece = para[i : i + chunk_size]
                if piece.strip():
                    chunks.append(piece.strip())
            continue

        if len(current) + len(para) + 2 > chunk_size:
            if current:
                chunks.append(current.strip())
            # Start next chunk with overlap from the end of the previous one.
            current = current[-overlap:].strip() + "\n\n" + para if overlap else para
        else:
            current = (current + "\n\n" + para).strip() if current else para

    if current.strip():
        chunks.append(current.strip())

    return chunks
