"""File operation tools with directory permission checking."""

from pathlib import Path
from typing import Any

from nixx.tools.base import Tool, ToolResult
from nixx.tools.permissions import is_path_allowed
from nixx.tools.shadow import shadow_backup


class ReadFileTool(Tool):
    """Read a file from an allowed directory."""

    def __init__(self, scratch_dir: Path) -> None:
        self._scratch_dir = scratch_dir
        self._project_dir: str | None = None

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file. Accepts a relative path (within the scratch directory) "
            "or an absolute path (within the project directory)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within scratch directory, or absolute path in the project directory",
                }
            },
            "required": ["path"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path", "")
        if not path:
            return ToolResult(success=False, error="path is required")

        try:
            p = Path(path)
            full_path = p.resolve() if p.is_absolute() else (self._scratch_dir / path).resolve()

            if not is_path_allowed(full_path, self._scratch_dir, self._project_dir):
                return ToolResult(success=False, error="Path is outside allowed directories")

            if not full_path.exists():
                return ToolResult(success=False, error=f"File not found: {path}")

            if not full_path.is_file():
                return ToolResult(success=False, error=f"Not a file: {path}")

            content = full_path.read_text(encoding="utf-8")
            if len(content) > 1_000_000:
                content = content[:1_000_000] + "\n... (truncated, file exceeds 1MB)"
            return ToolResult(success=True, result=content)
        except UnicodeDecodeError:
            return ToolResult(success=False, error="File is not valid UTF-8 text")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class WriteFileTool(Tool):
    """Write a file to an allowed directory."""

    def __init__(self, scratch_dir: Path) -> None:
        self._scratch_dir = scratch_dir
        self._project_dir: str | None = None

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Write content to a file. Accepts a relative path (within the scratch directory) "
            "or an absolute path (within the project directory). Creates parent directories if needed. "
            "A shadow backup is created automatically before overwriting existing files."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within scratch directory, or absolute path in the project directory",
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
            p = Path(path)
            full_path = p.resolve() if p.is_absolute() else (self._scratch_dir / path).resolve()

            if not is_path_allowed(full_path, self._scratch_dir, self._project_dir):
                return ToolResult(success=False, error="Path is outside allowed directories")

            full_path.parent.mkdir(parents=True, exist_ok=True)
            shadow_backup(full_path)
            full_path.write_text(content, encoding="utf-8")
            return ToolResult(success=True, result=f"Wrote {len(content)} bytes to {path}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class ListDirTool(Tool):
    """List contents of a directory."""

    def __init__(self, scratch_dir: Path) -> None:
        self._scratch_dir = scratch_dir
        self._project_dir: str | None = None

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "List files and directories in a path. Accepts a relative path "
            "(within scratch directory) or an absolute path (within the project directory)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within scratch directory, absolute path in the project directory, or '.' for scratch root",
                    "default": ".",
                }
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path", ".") or "."

        try:
            p = Path(path)
            full_path = p.resolve() if p.is_absolute() else (self._scratch_dir / path).resolve()

            if not is_path_allowed(full_path, self._scratch_dir, self._project_dir):
                return ToolResult(success=False, error="Path is outside allowed directories")

            if not full_path.exists():
                return ToolResult(success=False, error=f"Directory not found: {path}")

            if not full_path.is_dir():
                return ToolResult(success=False, error=f"Not a directory: {path}")

            entries = []
            for entry in sorted(full_path.iterdir()):
                # Show relative path from the base where possible
                try:
                    rel_path = entry.relative_to(self._scratch_dir)
                except ValueError:
                    rel_path = entry
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
    """Delete a file from an allowed directory."""

    def __init__(self, scratch_dir: Path) -> None:
        self._scratch_dir = scratch_dir
        self._project_dir: str | None = None

    @property
    def name(self) -> str:
        return "delete_file"

    @property
    def description(self) -> str:
        return (
            "Delete a file. Accepts a relative path (within scratch directory) "
            "or an absolute path (within the project directory). A shadow backup is created automatically."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within scratch directory, or absolute path in the project directory",
                }
            },
            "required": ["path"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path", "")
        if not path:
            return ToolResult(success=False, error="path is required")

        try:
            p = Path(path)
            full_path = p.resolve() if p.is_absolute() else (self._scratch_dir / path).resolve()

            if not is_path_allowed(full_path, self._scratch_dir, self._project_dir):
                return ToolResult(success=False, error="Path is outside allowed directories")

            if not full_path.exists():
                return ToolResult(success=False, error=f"File not found: {path}")

            if full_path.is_dir():
                if any(full_path.iterdir()):
                    return ToolResult(success=False, error="Directory is not empty")
                full_path.rmdir()
                return ToolResult(success=True, result=f"Deleted directory: {path}")
            else:
                shadow_backup(full_path)
                full_path.unlink()
                return ToolResult(success=True, result=f"Deleted file: {path}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class EditFileTool(Tool):
    """Edit a file using find-and-replace."""

    def __init__(self, scratch_dir: Path) -> None:
        self._scratch_dir = scratch_dir
        self._project_dir: str | None = None

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing an exact string with new content. The old_string must "
            "appear exactly once in the file. A shadow backup is created before editing. "
            "Accepts relative paths (scratch directory) or absolute paths (project directory)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact text to find (must appear exactly once)",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text",
                },
            },
            "required": ["path", "old_string", "new_string"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path", "")
        old_string = kwargs.get("old_string", "")
        new_string = kwargs.get("new_string", "")
        if not path:
            return ToolResult(success=False, error="path is required")
        if not old_string:
            return ToolResult(success=False, error="old_string is required")

        try:
            p = Path(path)
            full_path = p.resolve() if p.is_absolute() else (self._scratch_dir / path).resolve()

            if not is_path_allowed(full_path, self._scratch_dir, self._project_dir):
                return ToolResult(success=False, error="Path is outside allowed directories")

            if not full_path.exists():
                return ToolResult(success=False, error=f"File not found: {path}")

            if not full_path.is_file():
                return ToolResult(success=False, error=f"Not a file: {path}")

            content = full_path.read_text(encoding="utf-8")
            count = content.count(old_string)
            if count == 0:
                return ToolResult(success=False, error="old_string not found in file")
            if count > 1:
                return ToolResult(
                    success=False,
                    error=f"old_string appears {count} times - must appear exactly once",
                )

            shadow_backup(full_path)
            new_content = content.replace(old_string, new_string, 1)
            full_path.write_text(new_content, encoding="utf-8")
            return ToolResult(success=True, result=f"Edited {path}: replaced 1 occurrence")
        except UnicodeDecodeError:
            return ToolResult(success=False, error="File is not valid UTF-8 text")
        except Exception as e:
            return ToolResult(success=False, error=str(e))
