"""Download-then-install pipeline for pipu upgrade."""

import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from packaging.utils import canonicalize_name
from packaging.version import Version

from pipu_cli.package_management import (
    UpgradedPackage,
    _get_local_package_versions,
    _get_remote_package_versions,
    parse_package_spec,
)


def _canonical_name_for_spec(spec: str) -> str:
    """Return a canonical key for ``spec``, tolerating VCS/URL inputs.

    ``parse_package_spec`` raises :class:`ValueError` for inputs that
    aren't valid PEP 508 requirements or local files (e.g. ``git+https://``
    URLs). Such specs are still valid pip arguments, so we fall back to
    :func:`packaging.utils.canonicalize_name` on the raw spec to produce a
    stable lookup key without aborting the batch.
    """
    try:
        return parse_package_spec(spec).name
    except ValueError:
        return canonicalize_name(spec)

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 300


def _download_single(
    spec: str,
    dest_dir: Path,
    executable: str,
    pre: bool,
    timeout: int,
) -> Tuple[str, bool, str]:
    """Download a single package. Returns (spec, success, error_message)."""
    cmd = [executable, "-m", "pip", "download", "--dest", str(dest_dir), "--no-deps"]
    if pre:
        cmd.append("--pre")
    cmd.append(spec)

    logger.debug(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            msg = result.stderr.strip() or f"pip exit code {result.returncode}"
            logger.warning(f"Failed to download {spec}: {msg}")
            return (spec, False, msg)
        return (spec, True, "")
    except subprocess.TimeoutExpired:
        logger.warning(f"Download timed out for {spec} after {timeout}s")
        return (spec, False, f"timed out after {timeout}s")
    except OSError as e:
        # e.g. missing interpreter (FileNotFoundError), permission denied.
        # Surface via the result tuple so progress callbacks / trackers see it
        # instead of a raw traceback killing the thread pool.
        logger.warning(f"OS error downloading {spec}: {e}")
        return (spec, False, f"OS error: {e}")


def download_packages(
    specs: List[str],
    dest_dir: Path,
    python_path: Optional[str] = None,
    pre: bool = False,
    timeout: int = DOWNLOAD_TIMEOUT,
    max_workers: int = 1,
    progress_callback: Optional[Callable[[str, bool, str], None]] = None,
    start_callback: Optional[Callable[[str], None]] = None,
) -> List[Path]:
    """Download packages to a local directory using pip download.

    :param specs: Version-pinned package specs (e.g., ["requests==2.31.0"])
    :param dest_dir: Directory to download wheels/sdists into
    :param python_path: Python interpreter path (default: current Python)
    :param pre: Include pre-release versions
    :param timeout: Per-package subprocess timeout in seconds
    :param max_workers: Number of parallel downloads
    :param progress_callback: Called with (spec, success, error_msg) after each download
    :param start_callback: Called with (spec) when a download begins
    :returns: List of paths to downloaded files
    :raises RuntimeError: If any downloads fail
    """
    if not specs:
        return []

    executable = python_path or sys.executable
    failed: List[str] = []

    if max_workers > 1:
        def _download_with_start(spec: str) -> Tuple[str, bool, str]:
            if start_callback:
                start_callback(spec)
            return _download_single(spec, dest_dir, executable, pre, timeout)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_download_with_start, spec): spec for spec in specs}
            for future in as_completed(futures):
                spec, success, error_msg = future.result()
                if not success:
                    failed.append(spec)
                if progress_callback:
                    progress_callback(spec, success, error_msg)
    else:
        for spec in specs:
            if start_callback:
                start_callback(spec)
            spec, success, error_msg = _download_single(spec, dest_dir, executable, pre, timeout)
            if not success:
                failed.append(spec)
            if progress_callback:
                progress_callback(spec, success, error_msg)

    if failed:
        raise RuntimeError(f"Failed to download: {', '.join(failed)}")

    return list(dest_dir.iterdir())


def download_packages_for_group(
    env_upgrade_plans: Dict[str, List[str]],
    dest_dir: Path,
    pre: bool = False,
    timeout: int = DOWNLOAD_TIMEOUT,
    max_workers: int = 1,
    progress_callback: Optional[Callable[[str, bool, str], None]] = None,
    start_callback: Optional[Callable[[str], None]] = None,
) -> List[Path]:
    """Download deduplicated packages for a group of environments.

    :param env_upgrade_plans: Dict mapping env short names to lists of pinned specs
    :param dest_dir: Shared directory to download into
    :param pre: Include pre-release versions
    :param timeout: Per-package subprocess timeout in seconds
    :param max_workers: Number of parallel downloads
    :param progress_callback: Called with (spec, success, error_msg) after each download
    :param start_callback: Called with (spec) when a download begins
    :returns: List of paths to downloaded files
    """
    if not env_upgrade_plans:
        return []

    unique_specs: List[str] = list(dict.fromkeys(
        spec for specs in env_upgrade_plans.values() for spec in specs
    ))

    return download_packages(
        specs=unique_specs,
        dest_dir=dest_dir,
        pre=pre,
        timeout=timeout,
        max_workers=max_workers,
        progress_callback=progress_callback,
        start_callback=start_callback,
    )


def install_from_local(
    dest_dir: Path,
    specs: List[str],
    python_path: Optional[str] = None,
    timeout: int = DOWNLOAD_TIMEOUT,
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
    canonical_names = [_canonical_name_for_spec(s) for s in specs]

    if python_path:
        pre_versions = _get_remote_package_versions(python_path, canonical_names)
    else:
        pre_versions = _get_local_package_versions(canonical_names)

    failed_specs: Dict[str, str] = {}
    for spec in specs:
        cmd = [
            executable, "-m", "pip", "install",
            "--find-links", str(dest_dir),
            "--no-deps", spec,
        ]

        logger.debug(f"Running: {' '.join(cmd)}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )

        if result.returncode != 0:
            error_output = result.stderr.strip() or result.stdout.strip()
            failed_specs[spec] = error_output or f"pip exit code {result.returncode}"

        if progress_callback:
            progress_callback(spec)

    if python_path:
        post_versions = _get_remote_package_versions(python_path, canonical_names)
    else:
        post_versions = _get_local_package_versions(canonical_names)

    results: List[UpgradedPackage] = []
    for spec in specs:
        name = _canonical_name_for_spec(spec)
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
