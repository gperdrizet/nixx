"""Shadow backup system - automatic snapshots before file modifications."""

import shutil
import time
from pathlib import Path

_SHADOW_ROOT = Path.home() / ".nixx" / "shadows"


def shadow_backup(file_path: Path) -> Path | None:
    """Create a timestamped shadow copy of a file before modification.

    Returns the shadow path if a backup was created, None if the file didn't exist.
    """
    if not file_path.exists() or not file_path.is_file():
        return None

    _SHADOW_ROOT.mkdir(parents=True, exist_ok=True)

    # Build a shadow path that preserves the original directory structure
    # e.g. /home/user/nixx_scratch/foo/bar.txt → ~/.nixx/shadows/nixx_scratch/foo/bar.txt.1711612345
    try:
        rel = file_path.resolve().relative_to(Path.home())
    except ValueError:
        # File is outside home dir - use full path components minus the root /
        rel = Path(*file_path.resolve().parts[1:])

    timestamp = int(time.time())
    shadow_path = _SHADOW_ROOT / rel.parent / f"{rel.name}.{timestamp}"
    shadow_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(file_path, shadow_path)
    return shadow_path
