"""Requirements file management for pipu."""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from pipu_cli.package_management import UpgradedPackage

# Matches trailing inline comment preceded by whitespace. The capture group
# for the comment preserves the exact whitespace before the ``#`` so round-
# tripping a spec line is byte-accurate.
_INLINE_COMMENT_RE = re.compile(r"^(.*?)(\s+#.*)$")


def _split_inline_comment(spec_part: str) -> Tuple[str, str]:
    """Split a spec line into ``(spec, comment_suffix)``.

    The comment suffix includes the leading whitespace and ``#`` so the line
    can be reassembled verbatim. If no inline comment exists the suffix is
    an empty string.

    :param spec_part: The line content without the trailing newline.
    :returns: Tuple of ``(spec_without_comment, comment_suffix)``.
    """
    match = _INLINE_COMMENT_RE.match(spec_part)
    if match:
        return match.group(1), match.group(2)
    return spec_part, ""


def _try_parse_requirement(spec: str) -> Optional[Requirement]:
    """Return a :class:`Requirement` or ``None`` if the spec is malformed.

    :param spec: The requirement spec string (no inline comment, no newline).
    :returns: Parsed :class:`Requirement` or ``None`` if invalid.
    """
    try:
        return Requirement(spec)
    except InvalidRequirement:
        return None


def parse_requirements_file(path: Path) -> Dict[str, str]:
    """Parse a requirements.txt file.

    Lines starting with ``#`` or ``-`` (comments, ``-r`` includes, ``-e``
    editable installs, ``--extra-index-url`` and other options) are skipped.
    Malformed requirement lines are skipped silently. Package names are
    keyed by their PEP 503 canonical form so ``zope.interface``,
    ``Zope-Interface`` and ``zope_interface`` all resolve to the same key.

    :param path: Path to requirements file.
    :returns: Dict mapping canonicalized package names to their raw lines.
    """
    packages: Dict[str, str] = {}

    if not path.exists():
        return packages

    with open(path, 'r') as f:
        for raw_line in f:
            line = raw_line.strip()

            # Skip comments and empty lines.
            if not line or line.startswith('#'):
                continue

            # Skip options like -r, -e, --extra-index-url, etc.
            if line.startswith('-'):
                continue

            # Strip any inline comment before parsing so Requirement() does
            # not choke on the trailing ``# ...`` suffix.
            spec, _ = _split_inline_comment(line)

            req = _try_parse_requirement(spec)
            if req is None:
                continue

            packages[canonicalize_name(req.name)] = line

    return packages


def _format_upgraded_line(
    original_req: Requirement,
    upgrade: UpgradedPackage,
    pin_versions: bool,
    comment_suffix: str,
) -> str:
    """Build the replacement line for an upgraded requirement.

    Preserves the display name, extras and environment markers from the
    original line, substitutes a fresh version specifier, then reattaches
    any inline comment suffix and a trailing newline.

    :param original_req: The parsed requirement as it appears in the file.
    :param upgrade: The upgrade result providing the new version.
    :param pin_versions: If ``True`` use ``==``, otherwise use ``>=``.
    :param comment_suffix: Inline-comment suffix to preserve verbatim.
    :returns: The complete replacement line including trailing newline.
    """
    name = original_req.name
    if original_req.extras:
        # ``sorted`` mirrors packaging's own rendering for stability.
        extras = "[" + ",".join(sorted(original_req.extras)) + "]"
    else:
        extras = ""

    operator = "==" if pin_versions else ">="
    spec = f"{name}{extras}{operator}{upgrade.version}"

    if original_req.marker is not None:
        spec = f"{spec}; {original_req.marker}"

    return f"{spec}{comment_suffix}\n"


def update_requirements_file(
    path: Path,
    upgraded_packages: List[UpgradedPackage],
    pin_versions: bool = True,
) -> int:
    """Update a requirements file with upgraded package versions.

    Comment lines, blank lines and pip option lines (those starting with
    ``-``, e.g. ``-r``, ``-e``, ``--extra-index-url``) are preserved
    verbatim. For spec lines that parse as valid requirements and whose
    PEP 503 canonical name matches an upgraded package, the version is
    rewritten while preserving the original display name casing, any
    extras, environment markers and inline comments. Malformed lines are
    also preserved verbatim and do not count toward the return value.

    :param path: Path to requirements file.
    :param upgraded_packages: List of upgraded packages.
    :param pin_versions: Whether to pin exact versions (default: ``True``).
    :returns: Number of packages updated.
    """
    if not path.exists():
        return 0

    with open(path, 'r') as f:
        lines = f.readlines()

    # Build map keyed by canonical name; only successful upgrades participate.
    upgraded_map: Dict[str, UpgradedPackage] = {
        canonicalize_name(pkg.name): pkg
        for pkg in upgraded_packages
        if pkg.upgraded
    }

    updated_count = 0
    new_lines: List[str] = []

    for line in lines:
        stripped = line.strip()

        # Preserve comments, empty lines and pip options byte-for-byte.
        if not stripped or stripped.startswith('#') or stripped.startswith('-'):
            new_lines.append(line)
            continue

        # Separate any inline comment so the requirement parser sees clean input.
        spec_part, comment_suffix = _split_inline_comment(stripped)

        req = _try_parse_requirement(spec_part)
        if req is None:
            # Malformed requirement: preserve verbatim, do not count.
            new_lines.append(line)
            continue

        key = canonicalize_name(req.name)
        upgrade = upgraded_map.get(key)
        if upgrade is None:
            new_lines.append(line)
            continue

        new_lines.append(
            _format_upgraded_line(req, upgrade, pin_versions, comment_suffix)
        )
        updated_count += 1

    with open(path, 'w') as f:
        f.writelines(new_lines)

    return updated_count
