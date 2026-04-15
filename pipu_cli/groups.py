"""Group management for pipu-cli.

Groups allow users to manage named collections of Python environments
and run pipu commands across all environments in a group.

Groups are stored at ~/.config/pipu/groups.toml.
"""

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

logger = logging.getLogger(__name__)

GROUPS_FILE = Path.home() / ".config" / "pipu" / "groups.toml"


def load_groups() -> Dict[str, List[str]]:
    """Load all groups from the groups file.

    :returns: Dictionary mapping group names to lists of Python paths
    """
    if not GROUPS_FILE.exists():
        return {}

    try:
        with open(GROUPS_FILE, "rb") as f:
            data = tomllib.load(f)
        groups_section = data.get("groups", {})
        return {
            name: group_data.get("environments", [])
            for name, group_data in groups_section.items()
        }
    except Exception as e:
        logger.warning(f"Failed to load groups from {GROUPS_FILE}: {e}")
        return {}


def save_groups(groups: Dict[str, List[str]]) -> None:
    """Save groups to the groups file using atomic write.

    :param groups: Dictionary mapping group names to lists of Python paths
    """
    GROUPS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Build TOML content manually to avoid tomli-w dependency
    lines = []
    for name, environments in sorted(groups.items()):
        lines.append(f"[groups.{name}]")
        lines.append("environments = [")
        for env_path in environments:
            lines.append(f'    "{env_path}",')
        lines.append("]")
        lines.append("")

    content = "\n".join(lines)

    # Atomic write: write to temp file, then rename
    fd, tmp_path = tempfile.mkstemp(
        dir=str(GROUPS_FILE.parent), suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_path, str(GROUPS_FILE))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def add_environment(
    group_name: str,
    python_path: Optional[str] = None,
) -> bool:
    """Add a Python environment to a group.

    :param group_name: Name of the group
    :param python_path: Path to Python interpreter (defaults to sys.executable)
    :returns: True if added, False if already present
    """
    if python_path is None:
        python_path = sys.executable

    groups = load_groups()

    if group_name not in groups:
        groups[group_name] = []

    if python_path in groups[group_name]:
        return False

    groups[group_name].append(python_path)
    save_groups(groups)
    return True


def remove_environment(
    group_name: str,
    python_path: Optional[str] = None,
) -> bool:
    """Remove a Python environment from a group.

    If this removes the last environment, the group is deleted.

    :param group_name: Name of the group
    :param python_path: Path to Python interpreter (defaults to sys.executable)
    :returns: True if removed, False if not found
    """
    if python_path is None:
        python_path = sys.executable

    groups = load_groups()

    if group_name not in groups:
        return False

    if python_path not in groups[group_name]:
        return False

    groups[group_name].remove(python_path)

    # Delete group if empty
    if not groups[group_name]:
        del groups[group_name]

    save_groups(groups)
    return True


def delete_group(group_name: str) -> bool:
    """Delete an entire group.

    :param group_name: Name of the group
    :returns: True if deleted, False if not found
    """
    groups = load_groups()

    if group_name not in groups:
        return False

    del groups[group_name]
    save_groups(groups)
    return True


def list_groups() -> Dict[str, List[str]]:
    """List all groups and their environments.

    :returns: Dictionary mapping group names to lists of Python paths
    """
    return load_groups()


def get_group(group_name: str) -> Optional[List[str]]:
    """Get a specific group's environments.

    :param group_name: Name of the group
    :returns: List of Python paths or None if group doesn't exist
    """
    groups = load_groups()
    return groups.get(group_name)


def validate_python_path(python_path: str) -> Tuple[bool, Optional[str]]:
    """Validate that a path points to a Python interpreter.

    Checks:
    1. Path exists and is a file
    2. Running it with --version returns output starting with "Python"

    :param python_path: Path to validate
    :returns: Tuple of (is_valid, error_message_or_none)
    """
    path = Path(python_path)

    if not path.exists():
        return False, f"Path does not exist: {python_path}"

    if not path.is_file():
        return False, f"Path is not a file: {python_path}"

    try:
        result = subprocess.run(
            [python_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = (result.stdout + result.stderr).strip()
        if not output.startswith("Python"):
            return False, f"Not a Python interpreter (output: {output})"
        return True, None
    except subprocess.TimeoutExpired:
        return False, f"Timed out running {python_path} --version"
    except OSError as e:
        return False, f"Cannot execute {python_path}: {e}"
