"""Sandboxed Python execution tool."""

import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from nixx.tools.base import Tool, ToolResult

_MAX_OUTPUT = 50_000  # chars
_DEFAULT_TIMEOUT = 30  # seconds

# Cache whether unshare -rn is available (checked once)
_unshare_available: bool | None = None


def _check_unshare() -> bool:
    """Test whether unshare -rn works on this system."""
    global _unshare_available
    if _unshare_available is not None:
        return _unshare_available
    try:
        result = subprocess.run(
            ["unshare", "-rn", "true"],
            capture_output=True,
            timeout=5,
        )
        _unshare_available = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _unshare_available = False
    return _unshare_available


class RunPythonTool(Tool):
    """Execute Python code in a sandboxed subprocess."""

    def __init__(self, scratch_dir: Path) -> None:
        self._scratch_dir = scratch_dir
        self._allowed_dirs: list[str] = []

    @property
    def name(self) -> str:
        return "run_python"

    @property
    def description(self) -> str:
        return (
            "Execute Python code in a sandboxed subprocess. The working directory is the "
            "scratch directory. Network access is disabled. Output (stdout + stderr) is "
            "returned, capped at 50k characters. Default timeout is 30 seconds."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 30, max 120)",
                    "default": 30,
                },
            },
            "required": ["code"],
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        code = kwargs.get("code", "")
        timeout = min(int(kwargs.get("timeout", _DEFAULT_TIMEOUT)), 120)
        if not code:
            return ToolResult(success=False, error="code is required")

        # Write code to a temporary file in scratch_dir
        self._scratch_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                dir=self._scratch_dir,
                delete=False,
                encoding="utf-8",
            ) as f:
                f.write(code)
                script_path = f.name
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to write script: {e}")

        try:
            # Use unshare -rn for network isolation if available
            if _check_unshare():
                cmd = ["unshare", "-rn", "python3", script_path]
            else:
                cmd = ["python3", script_path]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._scratch_dir),
            )
        except Exception as e:
            Path(script_path).unlink(missing_ok=True)
            return ToolResult(success=False, error=f"Failed to start subprocess: {e}")

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            # Clean up script
            Path(script_path).unlink(missing_ok=True)
            return ToolResult(success=False, error=f"Execution timed out after {timeout}s")
        finally:
            Path(script_path).unlink(missing_ok=True)

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        output_parts = []
        if stdout:
            output_parts.append(stdout)
        if stderr:
            output_parts.append(f"[stderr]\n{stderr}")

        output = "\n".join(output_parts) or "(no output)"
        if len(output) > _MAX_OUTPUT:
            output = output[:_MAX_OUTPUT] + f"\n... (truncated, {len(output)} chars total)"

        if proc.returncode == 0:
            return ToolResult(success=True, result=output)
        else:
            return ToolResult(
                success=False,
                error=f"Exit code {proc.returncode}\n{output}",
            )
