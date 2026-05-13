"""Group management for pipu-cli.

Groups allow users to manage named collections of Python environments
and run pipu commands across all environments in a group.

Groups are stored at ~/.config/pipu/groups.toml.
"""

import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import click

if sys.version_info >= (3, 11):
    import tomllib
else:
    # tomli is installed only for Python versions that need the fallback.
    import tomli as tomllib  # pyright: ignore[reportMissingImports]

logger = logging.getLogger(__name__)

GROUPS_FILE = Path.home() / ".config" / "pipu" / "groups.toml"

_GROUP_NAME_RE = re.compile(r"\A[A-Za-z0-9_.-]*[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def _toml_quote(value: str) -> str:
    """Escape a string for a TOML basic-quoted scalar.

    Handles the characters that would otherwise break parsing when the
    stored path contains backslashes (Windows paths) or embedded double
    quotes.

    :param value: Raw string to serialize.
    :returns: The TOML basic-string literal, including surrounding quotes.
    """
    escape_map = {
        "\\": "\\\\",
        '"': '\\"',
        "\r": "\\r",
        "\n": "\\n",
    }
    return '"' + "".join(escape_map.get(ch, ch) for ch in value) + '"'


def validate_group_name(name: str) -> None:
    """Validate a group name against the allowed character set.

    Group names must contain only ``[A-Za-z0-9_.-]`` characters and must
    include at least one alphanumeric character, so they cannot collide
    with TOML special characters, contain path separators, or consist of
    pure punctuation.

    :param name: Candidate group name.
    :raises click.ClickException: If the name does not match the allowed
        pattern.
    """
    if not _GROUP_NAME_RE.match(name or ""):
        raise click.ClickException(
            f"Invalid group name: {name!r}. Group names must match "
            "[A-Za-z0-9_.-]+ with at least one letter or digit."
        )


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
            lines.append(f"    {_toml_quote(env_path)},")
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
    :raises click.ClickException: If ``group_name`` is not a valid group name.
    """
    validate_group_name(group_name)

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
    :raises click.ClickException: If ``group_name`` is not a valid group name.
    """
    validate_group_name(group_name)

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
    :raises click.ClickException: If ``group_name`` is not a valid group name.
    """
    validate_group_name(group_name)

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
    :raises click.ClickException: If ``group_name`` is not a valid group name.
    """
    validate_group_name(group_name)

    groups = load_groups()
    return groups.get(group_name)


def validate_python_path(path: str) -> str:
    """Resolve symlinks and validate the interpreter path.

    The path is resolved to its canonical form (following symlinks) and
    must point to an existing, executable file. The resolved path is
    returned so callers can persist the canonical location, which
    prevents two different symlinks pointing at the same physical
    interpreter from being stored as distinct group entries.

    :param path: Interpreter path to validate.
    :returns: The canonical, symlink-resolved path as a string.
    :raises click.ClickException: If the path does not exist, is not a
        regular file, or is not executable.
    """
    try:
        resolved = Path(path).resolve(strict=True)
    except FileNotFoundError as e:
        raise click.ClickException(f"Path does not exist: {path}") from e
    except OSError as e:
        raise click.ClickException(f"Cannot resolve {path}: {e}") from e

    if not resolved.is_file():
        raise click.ClickException(f"Not a file: {path}")

    if not os.access(resolved, os.X_OK):
        raise click.ClickException(f"Path is not executable: {resolved}")

    return str(resolved)
