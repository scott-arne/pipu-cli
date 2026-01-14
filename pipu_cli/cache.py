"""Package version caching for pipu.

This module provides caching of latest package versions from PyPI to speed up
repeated runs of pipu. The cache is per-environment, identified by the
Python executable path, making it compatible with venv, conda, mise, etc.

The cache stores only the latest available versions - constraint resolution
is performed at upgrade time with the current installed package state.
"""

import hashlib
import json
import logging
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

from packaging.version import Version

from pipu_cli.config import DEFAULT_CACHE_TTL, CACHE_BASE_DIR


logger = logging.getLogger(__name__)


@dataclass
class CacheData:
    """Cache data structure - stores latest versions from PyPI."""
    environment_id: str
    python_executable: str
    updated_at: str  # ISO format timestamp
    include_prereleases: bool
    # Maps package name (lowercase) to latest version string
    latest_versions: Dict[str, str]


def get_environment_id() -> str:
    """Get a unique identifier for the current Python environment.

    Uses a hash of the Python executable path to uniquely identify
    the environment. This works with venv, conda, mise, and other
    environment managers.

    :returns: Short hash identifying the environment
    """
    executable = sys.executable
    hash_obj = hashlib.sha256(executable.encode())
    return hash_obj.hexdigest()[:12]


def get_cache_dir() -> Path:
    """Get the cache directory for the current environment.

    :returns: Path to environment-specific cache directory
    """
    env_id = get_environment_id()
    return CACHE_BASE_DIR / env_id


def get_cache_path() -> Path:
    """Get the path to the cache file for the current environment.

    :returns: Path to the cache JSON file
    """
    return get_cache_dir() / "versions.json"


def load_cache() -> Optional[CacheData]:
    """Load cache data from disk.

    :returns: CacheData object or None if cache doesn't exist or is invalid
    """
    cache_path = get_cache_path()

    if not cache_path.exists():
        logger.debug(f"Cache file does not exist: {cache_path}")
        return None

    try:
        with open(cache_path, 'r') as f:
            data = json.load(f)

        # Validate the cache is for the current environment
        env_id = get_environment_id()
        if data.get("environment_id") != env_id:
            logger.debug("Cache environment mismatch, ignoring")
            return None

        return CacheData(
            environment_id=data["environment_id"],
            python_executable=data["python_executable"],
            updated_at=data["updated_at"],
            include_prereleases=data.get("include_prereleases", False),
            latest_versions=data.get("latest_versions", {})
        )
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.debug(f"Failed to load cache: {e}")
        return None


def save_cache(latest_versions: Dict[str, str], include_prereleases: bool = False) -> Path:
    """Save latest version data to the cache.

    :param latest_versions: Dictionary mapping package names (lowercase) to latest version strings
    :param include_prereleases: Whether prereleases were included in version check
    :returns: Path to the saved cache file
    """
    cache_dir = get_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_path = get_cache_path()

    cache_data = CacheData(
        environment_id=get_environment_id(),
        python_executable=sys.executable,
        updated_at=datetime.now(timezone.utc).isoformat(),
        include_prereleases=include_prereleases,
        latest_versions=latest_versions
    )

    with open(cache_path, 'w') as f:
        json.dump(asdict(cache_data), f, indent=2)

    logger.debug(f"Cache saved to {cache_path}")
    return cache_path


def is_cache_fresh(ttl_seconds: int = DEFAULT_CACHE_TTL) -> bool:
    """Check if the cache is fresh (within TTL).

    :param ttl_seconds: Time-to-live in seconds
    :returns: True if cache exists and is within TTL
    """
    cache = load_cache()
    if cache is None:
        return False

    try:
        updated_at = datetime.fromisoformat(cache.updated_at)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        age = datetime.now(timezone.utc) - updated_at
        is_fresh = age.total_seconds() < ttl_seconds

        logger.debug(f"Cache age: {age.total_seconds():.0f}s, TTL: {ttl_seconds}s, Fresh: {is_fresh}")
        return is_fresh
    except (ValueError, TypeError) as e:
        logger.debug(f"Failed to check cache freshness: {e}")
        return False


def get_cache_age_seconds() -> Optional[float]:
    """Get the age of the cache in seconds.

    :returns: Age in seconds or None if cache doesn't exist
    """
    cache = load_cache()
    if cache is None:
        return None

    try:
        updated_at = datetime.fromisoformat(cache.updated_at)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        age = datetime.now(timezone.utc) - updated_at
        return age.total_seconds()
    except (ValueError, TypeError):
        return None


def format_cache_age(seconds: Optional[float]) -> str:
    """Format cache age as human-readable string.

    :param seconds: Age in seconds
    :returns: Formatted string like "5 minutes ago" or "2 hours ago"
    """
    if seconds is None:
        return "never"

    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    else:
        days = int(seconds / 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"


def clear_cache() -> bool:
    """Delete the cache file for the current environment.

    :returns: True if cache was deleted, False if it didn't exist
    """
    cache_path = get_cache_path()

    if cache_path.exists():
        cache_path.unlink()
        logger.debug(f"Cache cleared: {cache_path}")
        return True

    return False


def clear_all_caches() -> int:
    """Delete all cache files for all environments.

    :returns: Number of cache directories deleted
    """
    if not CACHE_BASE_DIR.exists():
        return 0

    count = 0
    for cache_dir in CACHE_BASE_DIR.iterdir():
        if cache_dir.is_dir():
            cache_file = cache_dir / "versions.json"
            if cache_file.exists():
                cache_file.unlink()
            try:
                cache_dir.rmdir()
                count += 1
            except OSError:
                pass  # Directory not empty

    return count


def get_cache_info() -> Dict[str, Any]:
    """Get information about the current cache.

    :returns: Dictionary with cache metadata
    """
    cache = load_cache()
    cache_path = get_cache_path()

    info: Dict[str, Any] = {
        "exists": cache is not None,
        "path": str(cache_path),
        "environment_id": get_environment_id(),
        "python_executable": sys.executable,
    }

    if cache:
        info["updated_at"] = cache.updated_at
        info["package_count"] = len(cache.latest_versions)
        info["include_prereleases"] = cache.include_prereleases
        age_seconds = get_cache_age_seconds()
        info["age_seconds"] = age_seconds
        info["age_human"] = format_cache_age(age_seconds)

    return info


def build_version_cache(
    latest_versions: Dict[Any, Any]
) -> Dict[str, str]:
    """Build cache data from pipu's version check results.

    :param latest_versions: Dict mapping InstalledPackage to Package with latest version
    :returns: Dictionary mapping package names (lowercase) to latest version strings
    """
    result: Dict[str, str] = {}

    for installed_pkg, latest_pkg in latest_versions.items():
        name_lower = installed_pkg.name.lower()
        result[name_lower] = str(latest_pkg.version)

    return result
