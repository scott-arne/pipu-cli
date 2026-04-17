"""Download-then-install pipeline for pipu upgrade."""

import logging
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

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
