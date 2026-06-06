"""Git-backed validation and commit tool for self-modification."""

import subprocess
from pathlib import Path
from typing import Any

from nixx.tools.base import Tool, ToolResult

_MAX_OUTPUT = 20_000


def _run(cmd: list[str], cwd: Path, timeout: int = 60) -> tuple[int, str]:
    """Run a command, return (returncode, combined stdout+stderr)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (result.stdout + result.stderr).strip()
        return result.returncode, out[:_MAX_OUTPUT]
    except subprocess.TimeoutExpired:
        return 1, f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except FileNotFoundError:
        return 1, f"Command not found: {cmd[0]}"


class ValidateAndCommitTool(Tool):
    """Validate changed Python files and commit to git after self-modification.

    Runs ruff check and py_compile on every staged/modified .py file,
    then commits with the provided message if all checks pass.
    Use this after editing any nixx source file.
    """

    def __init__(self, project_dir: str | None = None) -> None:
        self._project_dir = project_dir

    def set_project_dir(self, project_dir: str | None) -> None:
        self._project_dir = project_dir

    @property
    def name(self) -> str:
        return "validate_and_commit"

    @property
    def description(self) -> str:
        return (
            "Validate and commit self-modifications to the nixx codebase. "
            "Runs ruff check and syntax validation on all modified Python files. "
            "If checks pass, stages all changes and commits with the given message. "
            "Returns detailed output so you can diagnose failures. "
            "Use this after every self-modification before considering the task done."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Git commit message describing what was changed and why.",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": (
                        "If true, run checks but do not commit. "
                        "Useful for validating changes before committing. Default: false."
                    ),
                },
            },
            "required": ["message"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        message = kwargs.get("message", "").strip()
        dry_run = bool(kwargs.get("dry_run", False))

        if not message:
            return ToolResult(success=False, error="commit message is required")

        project_dir = self._project_dir
        if not project_dir:
            return ToolResult(
                success=False,
                error="No project directory set. Use /project <path> to set it first.",
            )

        repo = Path(project_dir).resolve()
        if not (repo / ".git").exists():
            return ToolResult(
                success=False,
                error=f"{repo} is not a git repository.",
            )

        lines: list[str] = []

        # ── Get list of modified Python files ────────────────────────────────
        rc, diff_out = _run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR"],
            cwd=repo,
        )
        if rc != 0:
            return ToolResult(success=False, error=f"git diff failed: {diff_out}")

        py_files = [repo / f for f in diff_out.splitlines() if f.endswith(".py")]

        lines.append(f"Modified Python files: {len(py_files)}")

        # ── ruff check ───────────────────────────────────────────────────────
        if py_files:
            ruff_cmd = ["ruff", "check"] + [str(f) for f in py_files]
            rc, ruff_out = _run(ruff_cmd, cwd=repo)
            if rc != 0:
                lines.append(f"\nruff check FAILED:\n{ruff_out}")
                return ToolResult(success=False, error="\n".join(lines))
            lines.append("ruff check: passed")

            # ── py_compile (syntax check) ─────────────────────────────────────
            import sys

            compile_errors = []
            for f in py_files:
                rc, out = _run(
                    [sys.executable, "-m", "py_compile", str(f)],
                    cwd=repo,
                )
                if rc != 0:
                    compile_errors.append(f"{f.name}: {out}")
            if compile_errors:
                lines.append("\nSyntax errors:\n" + "\n".join(compile_errors))
                return ToolResult(success=False, error="\n".join(lines))
            lines.append("syntax check: passed")
        else:
            lines.append("No Python files modified - skipping lint/syntax checks")

        # ── Show what will be committed ───────────────────────────────────────
        rc, stat_out = _run(["git", "diff", "--stat"], cwd=repo)
        if stat_out:
            lines.append(f"\nChanges:\n{stat_out}")

        if dry_run:
            lines.append("\nDry run - not committing.")
            return ToolResult(success=True, result="\n".join(lines))

        # ── Stage and commit ──────────────────────────────────────────────────
        rc, add_out = _run(["git", "add", "-A"], cwd=repo)
        if rc != 0:
            return ToolResult(success=False, error=f"git add failed: {add_out}")

        rc, commit_out = _run(["git", "commit", "-m", message, "--no-verify"], cwd=repo)
        if rc != 0:
            # Could be "nothing to commit" - not an error
            if "nothing to commit" in commit_out:
                lines.append("Nothing to commit (working tree clean).")
                return ToolResult(success=True, result="\n".join(lines))
            return ToolResult(success=False, error=f"git commit failed: {commit_out}")

        lines.append(f"\nCommitted:\n{commit_out}")
        return ToolResult(success=True, result="\n".join(lines))
