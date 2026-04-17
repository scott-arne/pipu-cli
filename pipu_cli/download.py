"""Download-then-install pipeline for pipu upgrade."""

import logging
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

from packaging.version import Version

from pipu_cli.package_management import (
    UpgradedPackage,
    _get_local_package_versions,
    _get_remote_package_versions,
    _parse_package_name,
)

logger = logging.getLogger(__name__)


def download_packages(
    specs: List[str],
    dest_dir: Path,
    python_path: Optional[str] = None,
    pre: bool = False,
    timeout: int = 300,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> List[Path]:
    """Download packages to a local directory using pip download.

    :param specs: Version-pinned package specs (e.g., ["requests==2.31.0"])
    :param dest_dir: Directory to download wheels/sdists into
    :param python_path: Python interpreter path (default: current Python)
    :param pre: Include pre-release versions
    :param timeout: Subprocess timeout in seconds
    :param progress_callback: Called with package spec after each download completes
    :returns: List of paths to downloaded files
    :raises RuntimeError: If a download fails
    """
    if not specs:
        return []

    executable = python_path or sys.executable
    downloaded: List[Path] = []

    failed: List[str] = []

    for spec in specs:
        cmd = [executable, "-m", "pip", "download", "--dest", str(dest_dir), "--no-deps"]
        if pre:
            cmd.append("--pre")
        cmd.append(spec)

        logger.debug(f"Running: {' '.join(cmd)}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )

        if result.returncode != 0:
            logger.warning(f"Failed to download {spec}: {result.stderr.strip()}")
            failed.append(spec)

        if progress_callback:
            progress_callback(spec)

    if failed:
        raise RuntimeError(f"Failed to download: {', '.join(failed)}")

    # Collect all downloaded files
    downloaded = list(dest_dir.iterdir())
    return downloaded


def install_from_local(
    dest_dir: Path,
    specs: List[str],
    python_path: Optional[str] = None,
    timeout: int = 300,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> List[UpgradedPackage]:
    """Install packages from a local directory.

    :param dest_dir: Directory containing downloaded wheels/sdists
    :param specs: Version-pinned package specs to install
    :param python_path: Python interpreter path (default: current Python)
    :param timeout: Subprocess timeout in seconds
    :param progress_callback: Called with package spec after each install completes
    :returns: List of UpgradedPackage results
    """
    if not specs:
        return []

    executable = python_path or sys.executable
    canonical_names = [_parse_package_name(s) for s in specs]

    # Snapshot pre-install versions
    if python_path:
        pre_versions = _get_remote_package_versions(python_path, canonical_names)
    else:
        pre_versions = _get_local_package_versions(canonical_names)

    # Install each package
    failed_specs: Dict[str, str] = {}
    for spec in specs:
        cmd = [
            executable, "-m", "pip", "install",
            "--no-index", "--find-links", str(dest_dir),
            "--no-deps", spec,
        ]

        logger.debug(f"Running: {' '.join(cmd)}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )

        if result.returncode != 0:
            failed_specs[spec] = result.stderr.strip()

        if progress_callback:
            progress_callback(spec)

    # Snapshot post-install versions
    if python_path:
        post_versions = _get_remote_package_versions(python_path, canonical_names)
    else:
        post_versions = _get_local_package_versions(canonical_names)

    # Build results
    results: List[UpgradedPackage] = []
    for spec in specs:
        name = _parse_package_name(spec)
        pre_ver = pre_versions.get(name)
        post_ver = post_versions.get(name)

        if spec in failed_specs:
            results.append(UpgradedPackage(
                name=name,
                version=pre_ver or Version("0"),
                upgraded=False,
                previous_version=pre_ver or Version("0"),
                failure_reason=failed_specs[spec],
            ))
        elif post_ver and pre_ver and post_ver > pre_ver:
            results.append(UpgradedPackage(
                name=name,
                version=post_ver,
                upgraded=True,
                previous_version=pre_ver,
            ))
        elif post_ver:
            results.append(UpgradedPackage(
                name=name,
                version=post_ver,
                upgraded=post_ver != pre_ver if pre_ver else True,
                previous_version=pre_ver or post_ver,
            ))
        else:
            results.append(UpgradedPackage(
                name=name,
                version=pre_ver or Version("0"),
                upgraded=False,
                previous_version=pre_ver or Version("0"),
                failure_reason="Package not found after install",
            ))

    return results
