"""Tool registry for managing and executing tools."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nixx.tools.base import Tool, ToolResult
from nixx.tools.file_tools import DeleteFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nixx.tools.memory_tools import SearchTranscriptTool, ViewTranscriptTool

if TYPE_CHECKING:
    from nixx.memory.store import MemoryStore

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry for managing available tools."""

    def __init__(self, scratch_dir: Path, memory: MemoryStore | None = None) -> None:
        self._scratch_dir = scratch_dir
        self._memory = memory
        self._tools: dict[str, Tool] = {}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register the default file operation tools."""
        # Ensure scratch directory exists
        self._scratch_dir.mkdir(parents=True, exist_ok=True)

        tools: list[Tool] = [
            ReadFileTool(self._scratch_dir),
            WriteFileTool(self._scratch_dir),
            ListDirTool(self._scratch_dir),
            DeleteFileTool(self._scratch_dir),
        ]

        # Add memory tools if memory store is available
        if self._memory is not None:
            tools.extend(
                [
                    SearchTranscriptTool(self._memory),
                    ViewTranscriptTool(self._memory),
                ]
            )

        for tool in tools:
            self._tools[tool.name] = tool

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        """List all registered tools."""
        return list(self._tools.values())

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """Convert all tools to OpenAI tool format."""
        return [tool.to_openai_tool() for tool in self._tools.values()]

    async def execute(self, name: str, arguments: str | dict[str, Any]) -> ToolResult:
        """Execute a tool by name with JSON arguments.

        Args:
            name: Tool name
            arguments: JSON string or dict of arguments

        Returns:
            ToolResult with success status and result/error
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(success=False, error=f"Unknown tool: {name}")

        # Parse arguments if string
        if isinstance(arguments, str):
            try:
                args = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError as e:
                return ToolResult(success=False, error=f"Invalid JSON arguments: {e}")
        else:
            args = arguments

        logger.info("Executing tool %s with args: %s", name, args)
        try:
            result = await tool.execute(**args)
            logger.info("Tool %s result: success=%s", name, result.success)
            return result
        except Exception as e:
            logger.error("Tool %s failed: %s", name, e)
            return ToolResult(success=False, error=str(e))
