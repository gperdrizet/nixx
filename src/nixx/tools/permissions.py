"""Directory permissions - scratch_dir + optional project_dir."""

from __future__ import annotations

from pathlib import Path

_STATE_KEY = "project_dir"


def is_path_allowed(path: Path, scratch_dir: Path, project_dir: str | None) -> bool:
    """Check if a resolved path is within scratch_dir or project_dir."""
    resolved = str(path.resolve())
    if resolved.startswith(str(scratch_dir.resolve())):
        return True
    if project_dir:
        if resolved.startswith(str(Path(project_dir).resolve())):
            return True
    return False


async def get_project_dir(pool: object) -> str | None:
    """Load the project directory from the state table."""
    from nixx.memory.db import get_state

    raw = await get_state(pool, _STATE_KEY)  # type: ignore[arg-type]
    return raw if raw else None


async def set_project_dir(pool: object, directory: str | None) -> str | None:
    """Set or clear the project directory. Returns the resolved path or None."""
    from nixx.memory.db import set_state

    if directory:
        resolved = str(Path(directory).resolve())
        await set_state(pool, _STATE_KEY, resolved)  # type: ignore[arg-type]
        return resolved
    else:
        await set_state(pool, _STATE_KEY, "")  # type: ignore[arg-type]
        return None
