"""Memory/transcript tools for searching and viewing conversation history."""

from typing import TYPE_CHECKING, Any

from nixx.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from nixx.memory.store import MemoryStore


class SearchTranscriptTool(Tool):
    """Search the conversation transcript using full-text search."""

    def __init__(self, memory: "MemoryStore") -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "search_transcript"

    @property
    def description(self) -> str:
        return (
            "Search the conversation transcript for keywords or phrases. "
            "Returns matching messages with their buffer IDs, which can be used "
            "with view_transcript to see surrounding context."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (keywords or phrase)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 10)",
                    "default": 10,
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query", "")
        limit = kwargs.get("limit", 10)

        if not query:
            return ToolResult(success=False, error="query is required")

        try:
            results = await self._memory.recall_episodic(query, top_k=limit)

            if not results:
                return ToolResult(success=True, result="No matching transcript entries found.")

            lines = [f"Found {len(results)} matching entries:\n"]
            for r in results:
                buf_id = r.get("buffer_id", "?")
                role = r.get("role", "?")
                rank = r.get("rank", 0)
                content = r["content"][:200].replace("\n", " ")
                if len(r["content"]) > 200:
                    content += "..."
                lines.append(f"#{buf_id} [{role}] (rank {rank:.3f}): {content}")

            lines.append("\nUse view_transcript with a buffer ID to see surrounding context.")
            return ToolResult(success=True, result="\n".join(lines))
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class ViewTranscriptTool(Tool):
    """View a range of transcript entries by buffer ID."""

    def __init__(self, memory: "MemoryStore") -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "view_transcript"

    @property
    def description(self) -> str:
        return (
            "View transcript entries by buffer ID. Use after search_transcript "
            "to see the full context around a search result."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "start_id": {
                    "type": "integer",
                    "description": "Buffer ID to start from",
                },
                "end_id": {
                    "type": "integer",
                    "description": "Buffer ID to end at (default: start_id + 10)",
                },
            },
            "required": ["start_id"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        start_id = kwargs.get("start_id")
        end_id = kwargs.get("end_id")

        if start_id is None:
            return ToolResult(success=False, error="start_id is required")

        try:
            start_id = int(start_id)
        except (TypeError, ValueError):
            return ToolResult(success=False, error="start_id must be an integer")

        if end_id is None:
            end_id = start_id + 10
        else:
            try:
                end_id = int(end_id)
            except (TypeError, ValueError):
                return ToolResult(success=False, error="end_id must be an integer")

        try:
            from nixx.memory.db import get_buffer_entries

            entries = await get_buffer_entries(self._memory._pool, start_id, end_id)
            entries = [e for e in entries if e["role"] != "marker"]

            if not entries:
                return ToolResult(success=True, result="No transcript entries found in that range.")

            lines = [f"Transcript #{start_id} to #{end_id}:\n"]
            for e in entries:
                role = e["role"]
                content = e["content"]
                buf_id = e["id"]
                lines.append(f"#{buf_id} [{role}]: {content}")

            return ToolResult(success=True, result="\n\n".join(lines))
        except Exception as e:
            return ToolResult(success=False, error=str(e))
