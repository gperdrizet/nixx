"""File operation tools for the scratch directory."""

from pathlib import Path
from typing import Any

from nixx.tools.base import Tool, ToolResult


class ReadFileTool(Tool):
    """Read a file from the scratch directory."""

    def __init__(self, scratch_dir: Path) -> None:
        self._scratch_dir = scratch_dir

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read the contents of a file from the scratch directory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file within the scratch directory",
                }
            },
            "required": ["path"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path", "")
        if not path:
            return ToolResult(success=False, error="path is required")

        try:
            full_path = (self._scratch_dir / path).resolve()
            # Security: ensure path is within scratch_dir
            if not str(full_path).startswith(str(self._scratch_dir.resolve())):
                return ToolResult(success=False, error="Path is outside scratch directory")

            if not full_path.exists():
                return ToolResult(success=False, error=f"File not found: {path}")

            if not full_path.is_file():
                return ToolResult(success=False, error=f"Not a file: {path}")

            content = full_path.read_text(encoding="utf-8")
            # Limit size to prevent memory issues
            if len(content) > 1_000_000:
                content = content[:1_000_000] + "\n... (truncated, file exceeds 1MB)"
            return ToolResult(success=True, result=content)
        except UnicodeDecodeError:
            return ToolResult(success=False, error="File is not valid UTF-8 text")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class WriteFileTool(Tool):
    """Write a file to the scratch directory."""

    def __init__(self, scratch_dir: Path) -> None:
        self._scratch_dir = scratch_dir

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file in the scratch directory. Creates parent directories if needed."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file within the scratch directory",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path", "")
        content = kwargs.get("content", "")
        if not path:
            return ToolResult(success=False, error="path is required")

        try:
            full_path = (self._scratch_dir / path).resolve()
            # Security: ensure path is within scratch_dir
            if not str(full_path).startswith(str(self._scratch_dir.resolve())):
                return ToolResult(success=False, error="Path is outside scratch directory")

            # Create parent directories
            full_path.parent.mkdir(parents=True, exist_ok=True)

            full_path.write_text(content, encoding="utf-8")
            return ToolResult(success=True, result=f"Wrote {len(content)} bytes to {path}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class ListDirTool(Tool):
    """List contents of a directory in the scratch directory."""

    def __init__(self, scratch_dir: Path) -> None:
        self._scratch_dir = scratch_dir

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "List files and directories in a path within the scratch directory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to list (empty or '.' for root)",
                    "default": ".",
                }
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path", ".") or "."

        try:
            full_path = (self._scratch_dir / path).resolve()
            # Security: ensure path is within scratch_dir
            if not str(full_path).startswith(str(self._scratch_dir.resolve())):
                return ToolResult(success=False, error="Path is outside scratch directory")

            if not full_path.exists():
                return ToolResult(success=False, error=f"Directory not found: {path}")

            if not full_path.is_dir():
                return ToolResult(success=False, error=f"Not a directory: {path}")

            entries = []
            for entry in sorted(full_path.iterdir()):
                rel_path = entry.relative_to(self._scratch_dir)
                if entry.is_dir():
                    entries.append(f"{rel_path}/")
                else:
                    size = entry.stat().st_size
                    entries.append(f"{rel_path} ({size} bytes)")

            if not entries:
                return ToolResult(success=True, result="(empty directory)")
            return ToolResult(success=True, result="\n".join(entries))
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class DeleteFileTool(Tool):
    """Delete a file from the scratch directory."""

    def __init__(self, scratch_dir: Path) -> None:
        self._scratch_dir = scratch_dir

    @property
    def name(self) -> str:
        return "delete_file"

    @property
    def description(self) -> str:
        return "Delete a file from the scratch directory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file to delete",
                }
            },
            "required": ["path"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path", "")
        if not path:
            return ToolResult(success=False, error="path is required")

        try:
            full_path = (self._scratch_dir / path).resolve()
            # Security: ensure path is within scratch_dir
            if not str(full_path).startswith(str(self._scratch_dir.resolve())):
                return ToolResult(success=False, error="Path is outside scratch directory")

            if not full_path.exists():
                return ToolResult(success=False, error=f"File not found: {path}")

            if full_path.is_dir():
                # Only delete empty directories
                if any(full_path.iterdir()):
                    return ToolResult(success=False, error="Directory is not empty")
                full_path.rmdir()
                return ToolResult(success=True, result=f"Deleted directory: {path}")
            else:
                full_path.unlink()
                return ToolResult(success=True, result=f"Deleted file: {path}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))
