"""Directory permissions - manage which directories nixx can access beyond scratch_dir."""

from __future__ import annotations

import json
from pathlib import Path

_STATE_KEY = "allowed_dirs"


def _parse_dirs(raw: str | None) -> list[str]:
    """Parse the JSON list stored in the state table."""
    if not raw:
        return []
    try:
        dirs = json.loads(raw)
        return dirs if isinstance(dirs, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _serialize_dirs(dirs: list[str]) -> str:
    return json.dumps(sorted(set(dirs)))


def is_path_allowed(path: Path, scratch_dir: Path, allowed_dirs: list[str]) -> bool:
    """Check if a resolved path is within scratch_dir or any allowed directory."""
    resolved = str(path.resolve())
    if resolved.startswith(str(scratch_dir.resolve())):
        return True
    for d in allowed_dirs:
        if resolved.startswith(str(Path(d).resolve())):
            return True
    return False


async def get_allowed_dirs(pool: object) -> list[str]:
    """Load allowed directories from the state table."""
    from nixx.memory.db import get_state

    raw = await get_state(pool, _STATE_KEY)  # type: ignore[arg-type]
    return _parse_dirs(raw)


async def grant_dir(pool: object, directory: str) -> list[str]:
    """Add a directory to the allow-list. Returns updated list."""
    from nixx.memory.db import get_state, set_state

    dirs = _parse_dirs(await get_state(pool, _STATE_KEY))  # type: ignore[arg-type]
    resolved = str(Path(directory).resolve())
    if resolved not in dirs:
        dirs.append(resolved)
    await set_state(pool, _STATE_KEY, _serialize_dirs(dirs))  # type: ignore[arg-type]
    return sorted(set(dirs))


async def revoke_dir(pool: object, directory: str) -> list[str]:
    """Remove a directory from the allow-list. Returns updated list."""
    from nixx.memory.db import get_state, set_state

    dirs = _parse_dirs(await get_state(pool, _STATE_KEY))  # type: ignore[arg-type]
    resolved = str(Path(directory).resolve())
    dirs = [d for d in dirs if d != resolved]
    await set_state(pool, _STATE_KEY, _serialize_dirs(dirs))  # type: ignore[arg-type]
    return sorted(set(dirs))
