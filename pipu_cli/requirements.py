"""Requirements file management for pipu."""

import re
from pathlib import Path
from typing import List, Dict

from pipu_cli.package_management import UpgradedPackage


def parse_requirements_file(path: Path) -> Dict[str, str]:
    """Parse a requirements.txt file.

    :param path: Path to requirements file
    :returns: Dict mapping package names to their lines
    """
    packages = {}

    if not path.exists():
        return packages

    with open(path, 'r') as f:
        for line in f:
            line = line.strip()

            # Skip comments and empty lines
            if not line or line.startswith('#'):
                continue

            # Skip options like -r, -e, etc.
            if line.startswith('-'):
                continue

            # Extract package name (before any specifier)
            match = re.match(r'^([a-zA-Z0-9_-]+)', line)
            if match:
                pkg_name = match.group(1).lower()
                packages[pkg_name] = line

    return packages


def update_requirements_file(
    path: Path,
    upgraded_packages: List[UpgradedPackage],
    pin_versions: bool = True
) -> int:
    """Update a requirements file with upgraded package versions.

    :param path: Path to requirements file
    :param upgraded_packages: List of upgraded packages
    :param pin_versions: Whether to pin exact versions (default: True)
    :returns: Number of packages updated
    """
    if not path.exists():
        return 0

    # Read current file
    with open(path, 'r') as f:
        lines = f.readlines()

    # Build map of upgraded packages
    upgraded_map = {
        pkg.name.lower(): pkg
        for pkg in upgraded_packages
        if pkg.upgraded
    }

    updated_count = 0
    new_lines = []

    for line in lines:
        stripped = line.strip()

        # Keep comments and empty lines as-is
        if not stripped or stripped.startswith('#') or stripped.startswith('-'):
            new_lines.append(line)
            continue

        # Extract package name
        match = re.match(r'^([a-zA-Z0-9_-]+)', stripped)
        if match:
            pkg_name = match.group(1).lower()

            if pkg_name in upgraded_map:
                pkg = upgraded_map[pkg_name]
                if pin_versions:
                    new_line = f"{pkg.name}=={pkg.version}\n"
                else:
                    new_line = f"{pkg.name}>={pkg.version}\n"
                new_lines.append(new_line)
                updated_count += 1
                continue

        new_lines.append(line)

    # Write updated file
    with open(path, 'w') as f:
        f.writelines(new_lines)

    return updated_count
