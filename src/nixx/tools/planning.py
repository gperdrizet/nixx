"""Planning tool - read, write, and manage a structured plan in the scratch directory."""

from pathlib import Path
from typing import Any

from nixx.tools.base import Tool, ToolResult

_PLAN_FILE = ".plan.md"


class ReadPlanTool(Tool):
    """Read the current plan."""

    def __init__(self, scratch_dir: Path) -> None:
        self._scratch_dir = scratch_dir

    @property
    def name(self) -> str:
        return "read_plan"

    @property
    def description(self) -> str:
        return (
            "Read the current plan. Returns the structured plan if one exists, "
            "or a message indicating no plan is set."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> ToolResult:
        plan_path = self._scratch_dir / _PLAN_FILE
        if not plan_path.exists():
            return ToolResult(success=True, result="No plan set.")
        try:
            content = plan_path.read_text(encoding="utf-8")
            return ToolResult(success=True, result=content)
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class WritePlanTool(Tool):
    """Write or update the plan."""

    def __init__(self, scratch_dir: Path) -> None:
        self._scratch_dir = scratch_dir

    @property
    def name(self) -> str:
        return "write_plan"

    @property
    def description(self) -> str:
        return (
            "Write or replace the current plan. Use markdown with checkboxes "
            "(- [ ] / - [x]) for tracking progress. The plan is injected into "
            "your context automatically when it exists."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The plan content in markdown format",
                }
            },
            "required": ["content"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        content = kwargs.get("content", "")
        if not content:
            return ToolResult(success=False, error="content is required")
        plan_path = self._scratch_dir / _PLAN_FILE
        try:
            plan_path.write_text(content, encoding="utf-8")
            return ToolResult(success=True, result="Plan updated.")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


def get_current_plan(scratch_dir: Path) -> str | None:
    """Read the current plan file if it exists. Used by the server to inject into context."""
    plan_path = scratch_dir / _PLAN_FILE
    if plan_path.exists():
        try:
            return plan_path.read_text(encoding="utf-8")
        except Exception:
            return None
    return None
