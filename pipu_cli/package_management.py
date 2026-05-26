"""Package management functions for pipu-cli."""

import logging
import os
import os.path
import re
import subprocess
import sys
import tempfile
import threading
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass, field
from email.parser import Parser
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, runtime_checkable

from packaging.utils import canonicalize_name
from packaging.version import Version, InvalidVersion
from packaging.requirements import Requirement, InvalidRequirement
from packaging.specifiers import SpecifierSet, InvalidSpecifier
from pip._internal.metadata import get_default_environment
from pip._internal.configuration import Configuration
from pip._internal.index.package_finder import PackageFinder
from pip._internal.index.collector import LinkCollector
from pip._internal.models.search_scope import SearchScope
from pip._internal.network.session import PipSession
from pip._internal.models.selection_prefs import SelectionPreferences
from pip._internal.models.release_control import ReleaseControl

from pipu_cli._subprocess import InterruptToken, PipResult, run_pip

# Set up module logger
logger = logging.getLogger(__name__)


@runtime_checkable
class OutputStream(Protocol):
    """Protocol for output streams used in package installation."""
    def write(self, text: str, /) -> int | None:
        """Write text to the stream.

        Args:
            text: The text to write (positional-only to match StringIO signature).

        Returns:
            The number of characters written (like StringIO) or None.
        """
        ...

    def flush(self) -> None:
        """Flush the stream."""
        ...


@dataclass(frozen=True)
class Package:
    """Information about a package."""
    name: str
    version: Version


@dataclass(frozen=True)
class InstalledPackage(Package):
    """Information about an installed package."""
    constrained_dependencies: Dict[str, str] = field(default_factory=dict, hash=False, compare=False)
    is_editable: bool = False
    editable_location: Optional[str] = None


@dataclass(frozen=True)
class UpgradePackageInfo(Package):
    """Information about an installed package that can be upgraded."""
    upgradable: bool
    latest_version: Version
    is_editable: bool = False
    editable_location: Optional[str] = None


@dataclass(frozen=True)
class UpgradedPackage(Package):
    """Information about a package that has been upgraded."""
    upgraded: bool
    previous_version: Version
    is_editable: bool = False
    editable_location: Optional[str] = None
    failure_reason: Optional[str] = None


CONSTRAINED_BY_RESOLVER_REASON = (
    "Version unchanged \u2014 may be constrained by dependency resolver"
)
EDITABLE_SOURCE_UNCHANGED_REASON = "Editable source version unchanged"


def is_resolver_constrained_upgrade(pkg: UpgradedPackage) -> bool:
    """Return True when pip completed but kept a package at its old version."""
    return (
        not pkg.upgraded
        and pkg.failure_reason == CONSTRAINED_BY_RESOLVER_REASON
    )


def is_editable_source_unchanged(pkg: UpgradedPackage) -> bool:
    """Return True when an editable reinstall succeeded but stayed unchanged."""
    return (
        not pkg.upgraded
        and pkg.is_editable
        and pkg.failure_reason == EDITABLE_SOURCE_UNCHANGED_REASON
    )


def is_failed_upgrade_result(pkg: UpgradedPackage) -> bool:
    """Return True for actual upgrade errors, excluding known no-op outcomes."""
    return (
        not pkg.upgraded
        and not is_resolver_constrained_upgrade(pkg)
        and not is_editable_source_unchanged(pkg)
    )


@dataclass(frozen=True)
class BlockedPackageInfo(Package):
    """Information about a package that cannot be upgraded."""
    latest_version: Version
    blocked_by: List[str]  # List of "package_name (constraint)" strings
    is_editable: bool = False
    editable_location: Optional[str] = None


@dataclass(frozen=True)
class InstalledResult(Package):
    """Result of a pip install operation for a single package."""
    installed: bool
    previous_version: Optional[Version] = None  # None = freshly installed
    failure_reason: Optional[str] = None


@dataclass(frozen=True)
class UninstalledResult:
    """Result of a pip uninstall operation for a single package."""
    name: str
    previous_version: Optional[Version]
    uninstalled: bool
    already_absent: bool = False
    failure_reason: Optional[str] = None


@dataclass(frozen=True)
class DepEdge:
    """A single dependency relationship seen from the subject package.

    :param name: Canonical (PEP 503) name of the *other* package in the edge.
    :param installed_version: Installed version of the other package, or
        ``None`` if it is not installed.
    :param specifier: PEP 440 specifier string imposed on this edge
        (e.g. ``">=2.28"``). Empty string if unconstrained.
    :param is_editable: Whether the other package is installed editable.
    :param editable_location: Editable install path, if any.
    """

    name: str
    installed_version: Optional[Version]
    specifier: str
    is_editable: bool = False
    editable_location: Optional[str] = None


@dataclass(frozen=True)
class DepNode:
    """A node in either branch of the dep tree.

    :param edge: The edge that reaches this node.
    :param children: Child nodes (further hops). Empty at ``depth == 1``
        or when the node terminates a cycle.
    :param is_cycle: ``True`` if this node terminates a cycle and has no
        children by construction.
    """

    edge: "DepEdge"
    children: List["DepNode"] = field(default_factory=list, hash=False, compare=False)
    is_cycle: bool = False


@dataclass(frozen=True)
class DepProblem:
    """A correctness problem to surface in the error panel.

    :param kind: One of ``"missing"``, ``"violates"``, ``"broken-editable"``.
    :param package: Package the problem attaches to.
    :param detail: Human-readable one-line summary.
    :param required_by: For ``"violates"``, the package imposing the
        constraint.
    :param specifier: For ``"violates"``, the violated specifier.
    :param installed_version: For ``"violates"``, the installed version.
    """

    kind: str
    package: str
    detail: str
    required_by: Optional[str] = None
    specifier: Optional[str] = None
    installed_version: Optional[Version] = None


@dataclass(frozen=True)
class DepReport:
    """Full inspection result for one PACKAGE in one environment.

    :param package: The subject :class:`InstalledPackage`.
    :param required_by: Tree branch of packages that require the subject.
    :param requires: Tree branch of packages the subject requires.
    :param problems: Deduped, sorted list of :class:`DepProblem` entries.
    """

    package: "InstalledPackage"
    required_by: List["DepNode"] = field(default_factory=list, hash=False, compare=False)
    requires: List["DepNode"] = field(default_factory=list, hash=False, compare=False)
    problems: List["DepProblem"] = field(default_factory=list, hash=False, compare=False)


@dataclass(frozen=True)
class EnvReport:
    """Full consistency-check result for one environment.

    :param python_path: Python interpreter path, or ``None`` for the
        local env.
    :param package_count: Total installed distributions scanned
        (duplicates count separately).
    :param problems: Deduped, sorted list of :class:`DepProblem` entries.
        Same kinds / dedup / sort order as :class:`DepReport.problems`.
    """

    python_path: Optional[str]
    package_count: int
    problems: List["DepProblem"] = field(default_factory=list, hash=False, compare=False)


class PackageNotInstalledError(Exception):
    """Raised when the requested PACKAGE is not installed in the target env."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} is not installed in this environment")
        self.name = name


@dataclass(frozen=True)
class ParsedSpec:
    """Result of parsing a user-facing package spec.

    :param name: Canonicalized (PEP 503) package name.
    :param specifier: The :class:`~packaging.specifiers.SpecifierSet`;
        empty for bare names and for file/URL inputs.
    :param raw: The original input string, untouched.
    """
    name: str
    specifier: SpecifierSet
    raw: str

    @property
    def constraint_str(self) -> Optional[str]:
        """Return the specifier as a string or ``None`` when empty.

        Back-compat shim for call sites written against the old
        ``cli.parse_package_spec -> tuple[str, Optional[str]]`` contract.

        .. note::
            Multi-clause specifiers are normalized to
            :class:`~packaging.specifiers.SpecifierSet`'s canonical sorted
            order (e.g. ``">=2.30,<3.0"`` stringifies to ``"<3.0,>=2.30"``).
            Callers comparing the result to a raw input string should
            compare :class:`~packaging.specifiers.SpecifierSet` objects
            directly to stay order-insensitive.

        :returns: ``str(self.specifier)`` if non-empty, else ``None``.
        """
        s = str(self.specifier)
        return s if s else None


def parse_package_spec(spec: str) -> ParsedSpec:
    """Parse a pip-style requirement string or local package file path.

    Accepts:

    - Plain names: ``requests``, ``Requests``.
    - PEP 508 specifiers: ``requests==2.31.0``, ``requests>=2.30,<3.0``,
      ``requests[security]>=2.30``.
    - Local wheel / sdist paths: ``./pkg-1.0-py3-none-any.whl``,
      ``/tmp/my-pkg-1.2.3.tar.gz`` -- the specifier is empty in this case.

    :param spec: User-supplied requirement string or file path.
    :returns: :class:`ParsedSpec` with canonicalized name, specifier, raw input.
    :raises ValueError: When ``spec`` is neither a valid PEP 508 requirement
        nor an existing local wheel/sdist path.
    """
    stripped = spec.strip()

    # File-path detection (mirrors the old _parse_package_name logic).
    if os.path.isfile(os.path.expanduser(stripped)):
        filename = os.path.basename(stripped)
        whl = re.match(r'^([A-Za-z0-9]([A-Za-z0-9._]*[A-Za-z0-9])?)-', filename)
        if filename.endswith('.whl') and whl:
            return ParsedSpec(
                name=canonicalize_name(whl.group(1)),
                specifier=SpecifierSet(),
                raw=spec,
            )
        sdist = re.match(r'^([A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?)[-][\d]', filename)
        if sdist:
            return ParsedSpec(
                name=canonicalize_name(sdist.group(1)),
                specifier=SpecifierSet(),
                raw=spec,
            )
        # Last-resort: first token before the first dash-digit
        fallback = filename.split('-')[0].split('.')[0]
        return ParsedSpec(
            name=canonicalize_name(fallback),
            specifier=SpecifierSet(),
            raw=spec,
        )

    try:
        req = Requirement(stripped)
    except InvalidRequirement as e:
        raise ValueError(f"Invalid package spec {spec!r}: {e}") from e
    return ParsedSpec(
        name=canonicalize_name(req.name),
        specifier=req.specifier,
        raw=spec,
    )


def inspect_installed_packages(timeout: int = 10, python_path: Optional[str] = None) -> List[InstalledPackage]:
    """
    Inspect currently installed Python packages and return detailed information.

    This function uses pip's internal APIs to gather information about all installed
    packages in the current environment, including their versions, editable status,
    and constrained dependencies.

    :param timeout: Timeout in seconds for subprocess calls (default: 10)
    :param python_path: Path to Python interpreter (default: None for current environment)
    :returns: List of PackageInfo objects containing package details
    :raises RuntimeError: If unable to inspect installed packages
    """
    if python_path is not None:
        return _inspect_remote_packages(timeout, python_path)

    # Populate the local-env orphan cache before the main walk so that
    # later build_dep_report calls can surface stale .egg-info / .dist-info
    # directories even though pip hides them.
    try:
        _ORPHAN_METADATA_CACHE[""] = _detect_local_orphan_metadata()
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"Local orphan metadata scan failed: {e}")
        _ORPHAN_METADATA_CACHE[""] = {}

    try:
        # Get editable packages first
        editable_packages = _get_editable_packages(timeout)

        # Get all installed packages
        env = get_default_environment()
        installed_dists = list(env.iter_all_distributions())

        packages = []

        for dist in installed_dists:
            try:
                # Get package name
                package_name = dist.metadata["name"]
                canonical_name = canonicalize_name(package_name)

                # Get package version
                try:
                    package_version = Version(str(dist.version))
                except InvalidVersion:
                    logger.warning(f"Invalid version for {package_name}: {dist.version}. Skipping.")
                    continue

                # Check if package is editable and get its location
                is_editable = canonical_name in editable_packages
                editable_location = editable_packages.get(canonical_name) if is_editable else None

                # Extract constrained dependencies
                constrained_dependencies = _extract_constrained_dependencies(dist)

                # Create PackageInfo object
                package_info = InstalledPackage(
                    name=package_name,
                    version=package_version,
                    is_editable=is_editable,
                    editable_location=editable_location,
                    constrained_dependencies=constrained_dependencies
                )

                packages.append(package_info)

            except Exception as e:
                logger.warning(f"Error processing package {dist.metadata.get('name', 'unknown')}: {e}")
                continue

        # Sort packages alphabetically by name
        packages.sort(key=lambda p: p.name.lower())

        return packages

    except Exception as e:
        raise RuntimeError(f"Failed to inspect installed packages: {e}") from e


def _get_editable_packages(timeout: int, python_path: Optional[str] = None) -> Dict[str, str]:
    """
    Get packages installed in editable mode using pip list --editable.

    :param timeout: Timeout in seconds for subprocess call
    :param python_path: Path to Python interpreter
    :returns: Dictionary mapping canonical package names to their source locations
    """
    editable_packages: Dict[str, str] = {}

    try:
        # Use pip list --editable to get editable packages
        executable = python_path if python_path is not None else sys.executable
        result = subprocess.run(
            [executable, '-m', 'pip', 'list', '--editable'],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout
        )

        # Parse the output
        lines = result.stdout.strip().split('\n')

        # Find and skip the header
        header_found = False
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Skip header lines
            if not header_found:
                if line.startswith('Package') or line.startswith('-'):
                    header_found = True
                continue

            # Skip separator lines
            if line.startswith('-'):
                continue

            # Parse package lines: "package_name version /path/to/project"
            parts = line.split()
            if len(parts) >= 3:
                pkg_name = parts[0]
                location = ' '.join(parts[2:])
                canonical_name = canonicalize_name(pkg_name)
                editable_packages[canonical_name] = location

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Could not detect editable packages: {e}")
        return {}
    except Exception as e:
        logger.error(f"Unexpected error detecting editable packages: {e}")
        return {}

    return editable_packages


_REMOTE_CONSTRAINT_SCRIPT = r"""
import json, os, sys
try:
    from packaging.requirements import Requirement, InvalidRequirement
    from packaging.utils import canonicalize_name
except ImportError:
    from pip._vendor.packaging.requirements import Requirement, InvalidRequirement
    from pip._vendor.packaging.utils import canonicalize_name

# Authoritative view: pip's own environment. Drives the installed set
# (deduped the way `pip list` / `pip show` see it).
from pip._internal.metadata import get_default_environment

# Raw view: importlib.metadata. Any path here that doesn't fall inside
# a pip-reported install or editable source location is orphaned
# metadata (stale .egg-info from a deleted editable install, leftover
# .dist-info, etc.) — worth flagging because it breaks tools that use
# importlib.metadata directly.
try:
    from importlib.metadata import distributions as _im_distributions
except ImportError:
    from importlib_metadata import distributions as _im_distributions  # type: ignore[no-redef]


def _inside(path, parent):
    if not path or not parent:
        return False
    try:
        path = os.path.realpath(path)
        parent = os.path.realpath(parent)
    except Exception:
        return False
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


packages = []
pip_locations = {}
env = get_default_environment()
for dist in env.iter_all_distributions():
    try:
        name = dist.metadata["Name"]
    except Exception:
        name = None
    if not name:
        continue
    try:
        version = str(dist.version)
    except Exception:
        continue
    constraints = {}
    try:
        requires = dist.metadata.get_all("Requires-Dist") or []
    except Exception:
        requires = []
    for req_string in requires:
        try:
            req = Requirement(req_string)
        except InvalidRequirement:
            continue
        if req.marker is not None:
            marker_str = str(req.marker)
            if "extra" in marker_str:
                continue
            try:
                if not req.marker.evaluate():
                    continue
            except Exception:
                continue
        if req.specifier:
            constraints[canonicalize_name(req.name)] = str(req.specifier)
    packages.append({"name": name, "version": version, "constraints": constraints})
    canonical = canonicalize_name(name)
    known_locations = pip_locations.setdefault(canonical, [])
    known_locations.append(str(getattr(dist, "location", "") or ""))
    editable_location = str(getattr(dist, "editable_project_location", "") or "")
    if editable_location:
        known_locations.append(editable_location)

orphans = {}
for dist in _im_distributions():
    try:
        name = dist.metadata["Name"]
    except Exception:
        name = None
    if not name:
        continue
    canonical = canonicalize_name(name)
    path = str(getattr(dist, "_path", "") or "")
    if not path:
        continue
    locs = pip_locations.get(canonical, [])
    if any(_inside(path, loc) for loc in locs):
        continue
    try:
        version = str(dist.version)
    except Exception:
        version = ""
    orphans.setdefault(canonical, []).append({"version": version, "path": path})

json.dump({"packages": packages, "orphans": orphans}, sys.stdout)
"""


def _get_remote_packages(timeout: int, python_path: str) -> Dict[str, Any]:
    """Run the remote-introspection script and return the parsed payload.

    :param timeout: Subprocess timeout in seconds.
    :param python_path: Python interpreter to invoke.
    :returns: Dict with ``packages`` (list of ``{"name","version","constraints"}``)
        and ``orphans`` (dict of canonical name -> list of
        ``{"version","path"}`` entries for metadata directories pip doesn't see).
    :raises RuntimeError: On subprocess failure or unparseable output.
    """
    import json as json_module

    try:
        result = subprocess.run(
            [python_path, "-c", _REMOTE_CONSTRAINT_SCRIPT],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"Remote package inspection failed: {e}") from e
    try:
        return json_module.loads(result.stdout)
    except json_module.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse remote package data: {e}") from e


# Per-environment orphan metadata cache, populated by inspect_installed_packages
# and consumed by build_dep_report. Keyed by python_path (or "" for local env).
# Value shape: { canonical_name: [ { "version": str, "path": str }, ... ] }.
_ORPHAN_METADATA_CACHE: Dict[str, Dict[str, List[Dict[str, str]]]] = {}


def _detect_local_orphan_metadata() -> Dict[str, List[Dict[str, str]]]:
    """Return canonical-name -> list of orphan ``.dist-info``/``.egg-info`` paths.

    A path is orphaned if it is not inside any location pip's own environment
    reports for that package. This catches stale ``.egg-info`` directories
    left behind in source checkouts and rogue ``.dist-info`` dirs that
    ``pip list`` hides but ``importlib.metadata`` still sees.

    :returns: Dict of canonical package name -> list of
        ``{"version": ..., "path": ...}`` entries.
    """
    try:
        from importlib.metadata import distributions as _im_distributions
    except ImportError:  # pragma: no cover - Python <3.10 fallback
        return {}

    pip_locations: Dict[str, List[str]] = {}
    try:
        env = get_default_environment()
        for dist in env.iter_all_distributions():
            try:
                name = dist.metadata["Name"]
            except Exception:
                continue
            if not name:
                continue
            canonical = canonicalize_name(name)
            known_locations = pip_locations.setdefault(canonical, [])
            loc = str(getattr(dist, "location", "") or "")
            known_locations.append(loc)
            editable_location = str(
                getattr(dist, "editable_project_location", "") or ""
            )
            if editable_location:
                known_locations.append(editable_location)
    except Exception as e:
        logger.debug(f"Could not enumerate pip environment for orphan check: {e}")
        return {}

    orphans: Dict[str, List[Dict[str, str]]] = {}
    for im_dist in _im_distributions():
        try:
            name = im_dist.metadata["Name"]
        except Exception:
            continue
        if not name:
            continue
        canonical = canonicalize_name(name)
        path = str(getattr(im_dist, "_path", "") or "")
        if not path:
            continue
        locs = pip_locations.get(canonical, [])
        if any(_path_inside(path, loc) for loc in locs):
            continue
        try:
            version = str(im_dist.version)
        except Exception:
            version = ""
        orphans.setdefault(canonical, []).append({"version": version, "path": path})
    return orphans


def _path_inside(path: str, parent: str) -> bool:
    """Return True if ``path`` is equal to or a descendant of ``parent``.

    Both arguments are resolved through ``os.path.realpath`` so symlinks
    don't produce false negatives. Empty arguments return ``False``.
    """
    if not path or not parent:
        return False
    try:
        resolved_path = os.path.realpath(path)
        resolved_parent = os.path.realpath(parent)
    except Exception:
        return False
    if resolved_path == resolved_parent:
        return True
    return resolved_path.startswith(resolved_parent.rstrip(os.sep) + os.sep)


def _inspect_remote_packages(timeout: int, python_path: str) -> List[InstalledPackage]:
    """Inspect packages in a remote Python environment via subprocess.

    :param timeout: Timeout in seconds for subprocess calls
    :param python_path: Path to Python interpreter
    :returns: List of InstalledPackage objects
    :raises RuntimeError: If unable to inspect remote packages
    """
    try:
        editable_packages = _get_editable_packages(timeout, python_path=python_path)
        payload = _get_remote_packages(timeout, python_path)

        # Payload is either the new {"packages": [...], "orphans": {...}} shape
        # or the legacy list-of-dicts (kept for cache back-compat if any exists).
        if isinstance(payload, dict):
            pip_packages = payload.get("packages", [])
            orphans = payload.get("orphans", {}) or {}
        else:
            pip_packages = payload
            orphans = {}

        _ORPHAN_METADATA_CACHE[python_path] = orphans

        packages: List[InstalledPackage] = []
        for pkg_data in pip_packages:
            try:
                package_name = pkg_data["name"]
                canonical_name = canonicalize_name(package_name)

                try:
                    package_version = Version(pkg_data["version"])
                except InvalidVersion:
                    logger.warning(f"Invalid version for {package_name}: {pkg_data['version']}. Skipping.")
                    continue

                is_editable = canonical_name in editable_packages
                editable_location = editable_packages.get(canonical_name) if is_editable else None

                packages.append(InstalledPackage(
                    name=package_name,
                    version=package_version,
                    is_editable=is_editable,
                    editable_location=editable_location,
                    constrained_dependencies=dict(pkg_data.get("constraints", {})),
                ))
            except Exception as e:
                logger.warning(f"Error processing remote package {pkg_data.get('name', 'unknown')}: {e}")
                continue

        packages.sort(key=lambda p: p.name.lower())
        return packages

    except Exception as e:
        raise RuntimeError(f"Failed to inspect remote packages at {python_path}: {e}") from e


def get_orphan_metadata(python_path: Optional[str] = None) -> Dict[str, List[Dict[str, str]]]:
    """Return any orphaned metadata directories observed for an environment.

    Must be called after :func:`inspect_installed_packages` has populated
    the cache for the target environment (either local or remote). Returns
    an empty dict if nothing has been cached.

    :param python_path: Target env path, or ``None`` for local.
    :returns: Dict of canonical name -> list of ``{"version", "path"}``.
    """
    return _ORPHAN_METADATA_CACHE.get(python_path or "", {})


def _extract_constrained_dependencies(dist: Any) -> Dict[str, str]:
    """
    Extract constrained dependencies from a package's metadata.

    A dependency is considered "constrained" if it has any version specifier
    (e.g., "requests>=2.28.0", "numpy>=1.20.0,<2.0.0", "pandas==1.5.0").

    Only unconditional dependencies and dependencies whose markers are satisfied
    in the current environment are included. Dependencies that are conditional on
    extras (e.g., "dask<2025.3.0; extra == 'dask'") are skipped because we cannot
    determine which extras were installed.

    The constraint strings returned can be used with packaging.specifiers.SpecifierSet
    for version comparison operations.

    :param dist: Distribution object from pip's metadata API
    :returns: Dictionary mapping dependency names to their constraint specifiers
    """
    constrained_dependencies: Dict[str, str] = {}

    try:
        # Get the Requires-Dist metadata
        requires = dist.metadata.get_all("Requires-Dist")
        if not requires:
            return constrained_dependencies

        for req_string in requires:
            try:
                # Parse the requirement
                req = Requirement(req_string)

                # Skip requirements with markers that don't apply
                if req.marker:
                    marker_str = str(req.marker)
                    # Skip extra-only dependencies - we can't know which extras were installed
                    # These look like: extra == "dev", extra == 'test', etc.
                    if 'extra' in marker_str:
                        logger.debug(f"Skipping extra-only dependency: {req_string}")
                        continue
                    # For other markers (e.g., python_version, sys_platform), evaluate them
                    try:
                        if not req.marker.evaluate():
                            logger.debug(f"Skipping dependency with unsatisfied marker: {req_string}")
                            continue
                    except Exception as e:
                        logger.debug(f"Could not evaluate marker for {req_string}: {e}")
                        # If we can't evaluate, skip to be conservative
                        continue

                # Check if this requirement has any version specifier
                if req.specifier:
                    # Convert the specifier to a string (e.g., ">=1.0.0,<2.0.0")
                    constraint_str = str(req.specifier)
                    canonical_dep_name = canonicalize_name(req.name)
                    constrained_dependencies[canonical_dep_name] = constraint_str

            except InvalidRequirement as e:
                logger.warning(f"Invalid requirement specification: {req_string}. Error: {e}")
                continue

    except Exception as e:
        logger.warning(f"Error extracting dependencies for {dist.metadata.get('name', 'unknown')}: {e}")

    return constrained_dependencies


class _MetadataTextDistribution:
    """Distribution-like adapter for package metadata text."""

    def __init__(self, metadata_text: str) -> None:
        self.metadata = Parser().parsestr(metadata_text)


def _extract_constrained_dependencies_from_metadata_text(
    metadata_text: str,
) -> Dict[str, str]:
    """Extract constrained dependencies from raw package metadata text.

    :param metadata_text: Contents of a wheel ``METADATA`` file.
    :returns: Canonical dependency name -> specifier string.
    """
    return _extract_constrained_dependencies(
        _MetadataTextDistribution(metadata_text)
    )


def _extract_wheel_constraints(wheel_path: Path) -> Optional[Dict[str, str]]:
    """Extract constrained dependencies from a downloaded wheel.

    :param wheel_path: Path to a ``.whl`` file.
    :returns: Constraints from ``*.dist-info/METADATA``, or ``None`` if
        the metadata cannot be found or parsed.
    """
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            metadata_names = [
                name for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if not metadata_names:
                return None
            metadata_text = archive.read(metadata_names[0]).decode(
                "utf-8", errors="replace"
            )
            return _extract_constrained_dependencies_from_metadata_text(
                metadata_text
            )
    except Exception as e:
        logger.debug("Could not read wheel metadata from %s: %s", wheel_path, e)
        return None


def _download_target_package_constraints(
    package: Package,
    *,
    timeout: int = 300,
    include_prereleases: bool = False,
    python_path: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Download a target wheel and extract its dependency constraints.

    Only wheels are accepted. Source distributions can have dynamic
    dependencies that are not reliably visible without a build, so they
    are treated as unavailable and the caller can fail closed.

    :param package: Target package/version to inspect.
    :param timeout: ``pip download`` timeout in seconds.
    :param include_prereleases: Include pre-release candidates.
    :param python_path: Python interpreter used to run pip.
    :returns: Target dependency constraints, or ``None`` if unavailable.
    """
    executable = python_path or sys.executable
    spec = f"{package.name}=={package.version}"
    with tempfile.TemporaryDirectory(prefix="pipu-metadata-") as tmp_dir:
        dest_dir = Path(tmp_dir)
        cmd = [
            executable,
            "-m",
            "pip",
            "download",
            "--dest",
            str(dest_dir),
            "--no-deps",
            "--only-binary=:all:",
        ]
        if include_prereleases:
            cmd.append("--pre")
        cmd.append(spec)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )
        except Exception as e:
            logger.debug("Could not download metadata target %s: %s", spec, e)
            return None

        if result.returncode != 0:
            logger.debug(
                "Could not download metadata target %s: %s",
                spec,
                (result.stderr or result.stdout or "").strip(),
            )
            return None

        wheels = sorted(dest_dir.glob("*.whl"))
        if not wheels:
            return None
        return _extract_wheel_constraints(wheels[0])


def _find_disputed_target_packages(
    upgrade_candidates: Dict[InstalledPackage, Package],
    all_installed: List[InstalledPackage],
) -> Dict[str, Package]:
    """Find upgrading packages whose target metadata is needed for safety.

    A target release may tighten dependency constraints that affect packages
    pipu plans to pin in place during the offline install. Fetch metadata only
    for actual upgrades that either have constrained dependencies themselves or
    are named by another installed package's constrained dependency edge.
    """
    dependency_names = {
        canonicalize_name(dep_name)
        for installed in all_installed
        for dep_name in installed.constrained_dependencies
    }
    return {
        canonicalize_name(pkg.name): latest_pkg
        for pkg, latest_pkg in upgrade_candidates.items()
        if latest_pkg.version > pkg.version
        and (
            bool(pkg.constrained_dependencies)
            or canonicalize_name(pkg.name) in dependency_names
        )
    }


def get_target_constraints_for_disputed_upgrades(
    upgrade_candidates: Dict[InstalledPackage, Package],
    all_installed: List[InstalledPackage],
    *,
    timeout: int = 300,
    include_prereleases: bool = False,
    python_path: Optional[str] = None,
    constraints_cache: Optional[Dict[str, Optional[Dict[str, str]]]] = None,
) -> Dict[str, Optional[Dict[str, str]]]:
    """Fetch target constraints for packages with planned target upgrades.

    :param upgrade_candidates: Installed package -> target package.
    :param all_installed: Full installed package list for the environment.
    :param timeout: Metadata download timeout.
    :param include_prereleases: Include pre-release targets.
    :param python_path: Python interpreter used to run pip.
    :param constraints_cache: Optional mutable cache keyed by canonical
        package name, target version, and pre-release mode for compatible
        resolver calls.
    :returns: Canonical package name -> target constraints, or ``None``
        when target metadata was unavailable.
    """
    metadata_targets = _find_disputed_target_packages(
        upgrade_candidates, all_installed
    )
    if not metadata_targets:
        return {}

    cache = constraints_cache if constraints_cache is not None else {}
    result: Dict[str, Optional[Dict[str, str]]] = {}
    for canonical_name, package in metadata_targets.items():
        cache_key = "|".join(
            (
                canonical_name,
                str(package.version),
                "pre" if include_prereleases else "final",
                python_path or "",
            )
        )
        if cache_key not in cache:
            cache[cache_key] = _download_target_package_constraints(
                package,
                timeout=timeout,
                include_prereleases=include_prereleases,
                python_path=python_path,
            )
        result[canonical_name] = cache[cache_key]
    return result


def _build_pip_session(
    *, timeout: int, include_prereleases: bool = False,
) -> tuple[PipSession, PackageFinder]:
    """Construct a pip network session and a configured package finder.

    Centralizes the Configuration / PipSession / PackageFinder wiring
    used by both the parallel and serial version-probing code paths.

    :param timeout: Network timeout in seconds, applied to ``session.timeout``.
    :param include_prereleases: When ``True``, the finder allows prereleases
        via :class:`ReleaseControl` ``{":all:"}``.
    :returns: The ``(session, finder)`` pair.
    :raises ConnectionError: If :class:`PipSession` construction fails.
    """
    config: Optional[Configuration]
    try:
        config = Configuration(isolated=False, load_only=None)
        config.load()
    except Exception as e:
        logger.warning(f"Could not load pip configuration: {e}")
        config = None

    index_url: Optional[str] = None
    if config:
        try:
            index_url = config.get_value("global.index-url")
        except Exception:
            pass
    index_url = index_url or "https://pypi.org/simple/"

    extra_index_urls: List[str] = []
    if config:
        try:
            raw_extra_urls = config.get_value("global.extra-index-url")
            if raw_extra_urls:
                if isinstance(raw_extra_urls, str):
                    extra_index_urls = [
                        url.strip()
                        for url in raw_extra_urls.split('\n')
                        if url.strip() and not url.strip().startswith('#')
                    ]
                elif isinstance(raw_extra_urls, list):
                    extra_index_urls = raw_extra_urls
        except Exception:
            pass

    all_index_urls = [index_url] + extra_index_urls

    trusted_hosts: List[str] = []
    if config:
        try:
            raw_trusted_hosts = config.get_value("global.trusted-host")
            if raw_trusted_hosts:
                if isinstance(raw_trusted_hosts, str):
                    trusted_hosts = [
                        host.strip()
                        for host in raw_trusted_hosts.split('\n')
                        if host.strip() and not host.strip().startswith('#')
                    ]
                elif isinstance(raw_trusted_hosts, list):
                    trusted_hosts = raw_trusted_hosts
        except Exception:
            pass

    try:
        session = PipSession()
        session.timeout = timeout
        for host in trusted_hosts:
            host = host.strip()
            if host:
                session.add_trusted_host(host, source="pip configuration")
    except Exception as e:
        raise ConnectionError(f"Failed to create network session: {e}") from e

    release_control = ReleaseControl(all_releases={":all:"}) if include_prereleases else None
    selection_prefs = SelectionPreferences(
        allow_yanked=False,
        release_control=release_control,
    )
    search_scope = SearchScope.create(
        find_links=[],
        index_urls=all_index_urls,
        no_index=False,
    )
    link_collector = LinkCollector(
        session=session,
        search_scope=search_scope,
    )
    package_finder = PackageFinder.create(
        link_collector=link_collector,
        selection_prefs=selection_prefs,
    )
    return session, package_finder


def _fetch_latest_version(
    installed_pkg: "InstalledPackage",
    *,
    specifier: Optional[SpecifierSet] = None,
    include_prereleases: bool = False,
    timeout: int = 10,
) -> Optional[tuple["InstalledPackage", "Package"]]:
    """Query the configured indexes for the latest version of one package.

    Builds a per-call :class:`PipSession` / :class:`PackageFinder` so
    concurrent callers never share pip's non-thread-safe session state.
    The session is always closed via :class:`ExitStack` callback, even
    when candidate parsing raises.

    :param installed_pkg: The installed package to probe.
    :param specifier: Optional version specifier that target candidates must
        satisfy.
    :param include_prereleases: When ``True``, prerelease candidates are
        eligible. When ``False``, they are filtered out unless every
        candidate is a prerelease.
    :param timeout: Network timeout applied to the underlying
        :class:`PipSession`.
    :returns: ``(installed_pkg, latest_package)`` on success, or ``None``
        if no candidates parse as valid :class:`Version` values.
    """
    with ExitStack() as stack:
        session, finder = _build_pip_session(
            timeout=timeout, include_prereleases=include_prereleases
        )
        stack.callback(session.close)

        canonical_name = canonicalize_name(installed_pkg.name)
        candidates = finder.find_all_candidates(canonical_name)
        if not candidates:
            logger.debug("No candidates found for %s", installed_pkg.name)
            return None

        parsed: List[tuple[Version, Any]] = []
        for candidate in candidates:
            try:
                version_obj = Version(str(candidate.version))
            except InvalidVersion:
                continue
            if specifier is not None and str(specifier):
                if not specifier.contains(version_obj, prereleases=True):
                    continue
            parsed.append((version_obj, candidate))

        if not parsed:
            return None

        if include_prereleases:
            scan = parsed
        else:
            stable = [pair for pair in parsed if not pair[0].is_prerelease]
            scan = stable if stable else parsed

        latest_version, _ = max(scan, key=lambda pair: pair[0])
        return installed_pkg, Package(name=installed_pkg.name, version=latest_version)

    return None


def get_latest_version_for_spec(
    parsed_spec: ParsedSpec,
    timeout: int = 10,
    include_prereleases: bool = False,
) -> Optional[Package]:
    """Resolve the newest index version satisfying a parsed package spec.

    :param parsed_spec: Parsed user-supplied package spec.
    :param timeout: Network timeout in seconds for package queries.
    :param include_prereleases: Include pre-release versions when resolving.
    :returns: Target package/version, or ``None`` if no matching candidate was
        found.
    """
    probe = InstalledPackage(
        name=parsed_spec.name,
        version=Version("0"),
        constrained_dependencies={},
    )
    fetched = _fetch_latest_version(
        probe,
        specifier=parsed_spec.specifier,
        include_prereleases=include_prereleases,
        timeout=timeout,
    )
    if fetched is None:
        return None
    return fetched[1]


def get_latest_versions_parallel(
    installed_packages: List[InstalledPackage],
    timeout: int = 10,
    include_prereleases: bool = False,
    max_workers: int = 10,
    progress_callback: Optional[Callable] = None
) -> Dict[InstalledPackage, Package]:
    """
    Get the latest available versions for a list of installed packages using parallel queries.

    This function queries PyPI (or configured package indexes) to find the latest
    version available for each installed package using concurrent requests. It respects
    pip configuration settings including index-url, extra-index-url, and trusted-host.

    :param installed_packages: List of InstalledPackage objects to check
    :param timeout: Network timeout in seconds for package queries (default: 10)
    :param include_prereleases: Whether to include pre-release versions (default: False)
    :param max_workers: Maximum concurrent requests (default: 10)
    :param progress_callback: Optional thread-safe callback function(current, total) for progress updates
    :returns: Dictionary mapping InstalledPackage objects to Package objects with latest version
    :raises ConnectionError: If unable to connect to package indexes
    :raises RuntimeError: If unable to load pip configuration
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Thread-safe result storage and progress tracking
    result: Dict[InstalledPackage, Package] = {}
    result_lock = threading.Lock()
    progress_lock = threading.Lock()
    completed_count = [0]  # Mutable container for thread-safe counter
    total_packages = len(installed_packages)

    def worker(installed_pkg: InstalledPackage) -> Optional[InstalledPackage]:
        """Fetch the latest version for one package via an isolated session."""
        fetched = _fetch_latest_version(
            installed_pkg,
            include_prereleases=include_prereleases,
            timeout=timeout,
        )
        if fetched is None:
            return None

        pkg, latest_pkg = fetched
        with result_lock:
            result[pkg] = latest_pkg
        return pkg

    # Execute parallel queries
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker, pkg) for pkg in installed_packages]

        for future in as_completed(futures):
            future.result()
            with progress_lock:
                completed_count[0] += 1
                if progress_callback:
                    progress_callback(completed_count[0], total_packages)

    return result


def get_latest_versions(
    installed_packages: List[InstalledPackage],
    timeout: int = 10,
    include_prereleases: bool = False,
    progress_callback: Optional[Callable] = None
) -> Dict[InstalledPackage, Package]:
    """
    Get the latest available versions for a list of installed packages.

    This function queries PyPI (or configured package indexes) to find the latest
    version available for each installed package. It respects pip configuration
    settings including index-url, extra-index-url, and trusted-host.

    :param installed_packages: List of InstalledPackage objects to check
    :param timeout: Network timeout in seconds for package queries (default: 10)
    :param include_prereleases: Whether to include pre-release versions (default: False)
    :param progress_callback: Optional callback function(current, total) for progress updates
    :returns: Dictionary mapping InstalledPackage objects to Package objects with latest version
    :raises ConnectionError: If unable to connect to package indexes
    :raises RuntimeError: If unable to load pip configuration
    """
    result: Dict[InstalledPackage, Package] = {}
    total_packages = len(installed_packages)

    for idx, installed_pkg in enumerate(installed_packages):
        try:
            fetched = _fetch_latest_version(
                installed_pkg,
                include_prereleases=include_prereleases,
                timeout=timeout,
            )
        except ConnectionError:
            raise
        except Exception as e:
            logger.warning(f"Error checking {installed_pkg.name}: {e}")
            fetched = None

        if fetched is not None:
            pkg, latest_pkg = fetched
            result[pkg] = latest_pkg

        if progress_callback:
            progress_callback(idx + 1, total_packages)

    if progress_callback:
        progress_callback(total_packages, total_packages)

    return result


def resolve_upgradable_packages(
    upgrade_candidates: Dict[InstalledPackage, Package],
    all_installed: List[InstalledPackage],
    target_constraints: Optional[Dict[str, Optional[Dict[str, str]]]] = None,
) -> List[UpgradePackageInfo]:
    """Resolve upgradable packages, discarding block reasons.

    Thin wrapper around :func:`resolve_upgradable_packages_with_reasons`.
    The plain list includes every candidate (upgradable and blocked), with
    the ``upgradable`` flag set accordingly, matching the prior contract.

    :param upgrade_candidates: Dict mapping installed packages to their latest available versions.
    :param all_installed: List of all installed packages (for constraint checking).
    :param target_constraints: Optional canonical package name -> target
        version constraints. ``None`` for a package means target metadata
        could not be inspected, so disputed dependency upgrades fail closed.
    :returns: List of UpgradePackageInfo objects, each flagged upgradable or not.
    """
    upgradable, _blocked = resolve_upgradable_packages_with_reasons(
        upgrade_candidates, all_installed, target_constraints=target_constraints,
    )
    # Preserve prior contract: return one entry per candidate with
    # upgradable flag set. _with_reasons returns only truly upgradable
    # entries in the first tuple element, so we rebuild the full list.
    upgradable_names = {p.name for p in upgradable}
    result: List[UpgradePackageInfo] = list(upgradable)
    for pkg, latest in upgrade_candidates.items():
        if pkg.name in upgradable_names:
            continue
        result.append(UpgradePackageInfo(
            name=pkg.name,
            version=pkg.version,
            upgradable=False,
            latest_version=latest.version,
            is_editable=pkg.is_editable,
            editable_location=pkg.editable_location,
        ))
    return result


def resolve_upgradable_packages_with_reasons(
    upgrade_candidates: Dict[InstalledPackage, Package],
    all_installed: List[InstalledPackage],
    target_constraints: Optional[Dict[str, Optional[Dict[str, str]]]] = None,
) -> tuple[List[UpgradePackageInfo], List[BlockedPackageInfo]]:
    """
    Resolve upgradable packages and provide detailed blocking reasons.

    Returns both upgradable packages and blocked packages with reasons.

    :param upgrade_candidates: Dict mapping installed packages to their latest available versions
    :param all_installed: List of all installed packages (for constraint checking)
    :param target_constraints: Optional canonical package name -> target
        version constraints. When a currently constraining package is also
        upgrading, these target constraints determine whether the disputed
        dependency upgrade remains safe. ``None`` means target metadata
        was unavailable, so the disputed dependency upgrade is blocked.
    :returns: Tuple of (upgradable_packages, blocked_packages_with_reasons)
    """
    # Build a reverse dependency map
    constraints_on: Dict[str, List[tuple[InstalledPackage, str]]] = {}

    for pkg in all_installed:
        for dep_name, specifier_str in pkg.constrained_dependencies.items():
            if dep_name not in constraints_on:
                constraints_on[dep_name] = []
            constraints_on[dep_name].append((pkg, specifier_str))

    # Filter to only actual upgrades
    actual_upgrades = {
        pkg: latest_pkg
        for pkg, latest_pkg in upgrade_candidates.items()
        if latest_pkg.version > pkg.version
    }

    # Track blocking reasons for each package
    blocking_reasons: Dict[str, List[str]] = {}

    installed_by_name: Dict[str, InstalledPackage] = {
        canonicalize_name(pkg.name): pkg for pkg in all_installed
    }
    actual_upgrades_by_name: Dict[str, Package] = {
        canonicalize_name(pkg.name): latest_pkg
        for pkg, latest_pkg in actual_upgrades.items()
    }

    normalized_target_constraints: Optional[Dict[str, Optional[Dict[str, str]]]]
    if target_constraints is None:
        normalized_target_constraints = None
    else:
        normalized_target_constraints = {}
        for pkg_name, constraints in target_constraints.items():
            canonical_pkg = canonicalize_name(pkg_name)
            if constraints is None:
                normalized_target_constraints[canonical_pkg] = None
                continue
            normalized_target_constraints[canonical_pkg] = {
                canonicalize_name(dep_name): specifier
                for dep_name, specifier in constraints.items()
            }

    def block(canonical_name: str, reason: str) -> None:
        packages_to_remove.add(canonical_name)
        if canonical_name not in blocking_reasons:
            blocking_reasons[canonical_name] = []
        if reason not in blocking_reasons[canonical_name]:
            blocking_reasons[canonical_name].append(reason)

    def target_allows(
        *,
        constraining_pkg: InstalledPackage,
        constrained_name: str,
        constrained_version: Version,
    ) -> tuple[bool, Optional[str]]:
        """Return whether target metadata keeps the dependency upgrade safe."""
        if normalized_target_constraints is None:
            return True, None

        constraining_canonical = canonicalize_name(constraining_pkg.name)
        target = normalized_target_constraints.get(constraining_canonical)
        if target is None:
            return False, f"{constraining_pkg.name} target metadata unavailable"

        target_specifier_str = target.get(constrained_name)
        if not target_specifier_str:
            return True, None

        try:
            target_specifier = SpecifierSet(target_specifier_str)
        except InvalidSpecifier:
            return False, f"{constraining_pkg.name} target constraint invalid"

        if constrained_version in target_specifier:
            return True, None
        return False, f"{constraining_pkg.name} target requires {target_specifier_str}"

    # Fixed-point iteration
    upgrading_packages = {canonicalize_name(pkg.name) for pkg in actual_upgrades.keys()}
    max_iterations = len(upgrading_packages) + 1

    def target_dependencies_are_safe(
        target_pkg: InstalledPackage,
        canonical_name: str,
    ) -> bool:
        """Check target requirements against versions pipu will leave pinned."""
        if normalized_target_constraints is None:
            return True

        target = normalized_target_constraints.get(canonical_name)
        if target is None:
            return True

        for dep_name, specifier_str in target.items():
            installed_dep = installed_by_name.get(dep_name)
            if installed_dep is None:
                continue

            try:
                specifier = SpecifierSet(specifier_str)
            except InvalidSpecifier:
                block(canonical_name, f"{target_pkg.name} target constraint invalid")
                return False

            latest_dep = actual_upgrades_by_name.get(dep_name)
            if latest_dep is not None and dep_name in upgrading_packages:
                if latest_dep.version not in specifier:
                    block(dep_name, f"{target_pkg.name} target requires {specifier_str}")
                continue

            if installed_dep.version not in specifier:
                block(
                    canonical_name,
                    f"{target_pkg.name} target requires {dep_name}{specifier_str}",
                )
                return False

        return True

    for _iteration in range(1, max_iterations + 1):
        packages_to_remove: set[str] = set()

        for installed_pkg, latest_pkg in actual_upgrades.items():
            canonical_name = canonicalize_name(installed_pkg.name)

            if canonical_name not in upgrading_packages or canonical_name in packages_to_remove:
                continue

            if not target_dependencies_are_safe(installed_pkg, canonical_name):
                continue

            latest_version = latest_pkg.version

            if canonical_name in constraints_on:
                for constraining_pkg, specifier_str in constraints_on[canonical_name]:
                    try:
                        specifier = SpecifierSet(specifier_str)
                        satisfies = latest_version in specifier

                        constraining_canonical = canonicalize_name(constraining_pkg.name)
                        if constraining_canonical in upgrading_packages:
                            if normalized_target_constraints is None:
                                continue
                            target_safe, target_reason = target_allows(
                                constraining_pkg=constraining_pkg,
                                constrained_name=canonical_name,
                                constrained_version=latest_version,
                            )
                            if not target_safe:
                                block(canonical_name, target_reason or "Unknown constraint")
                                break
                            continue

                        if not satisfies:
                            block(canonical_name, f"{constraining_pkg.name} requires {specifier_str}")
                            break
                    except (InvalidSpecifier, Exception):
                        constraining_canonical = canonicalize_name(constraining_pkg.name)
                        if constraining_canonical not in upgrading_packages:
                            block(canonical_name, f"{constraining_pkg.name} (invalid constraint)")
                            break
                        if normalized_target_constraints is not None:
                            block(canonical_name, f"{constraining_pkg.name} target metadata unavailable")
                            break

        if not packages_to_remove:
            break

        upgrading_packages -= packages_to_remove

    # Build result lists
    upgradable = []
    blocked = []

    for installed_pkg, latest_pkg in upgrade_candidates.items():
        canonical_name = canonicalize_name(installed_pkg.name)
        latest_version = latest_pkg.version

        is_actual_upgrade = latest_version > installed_pkg.version
        can_upgrade = is_actual_upgrade and canonical_name in upgrading_packages

        if can_upgrade:
            upgradable.append(UpgradePackageInfo(
                name=installed_pkg.name,
                version=installed_pkg.version,
                upgradable=True,
                latest_version=latest_version,
                is_editable=installed_pkg.is_editable,
                editable_location=installed_pkg.editable_location
            ))
        elif is_actual_upgrade:
            # Blocked package
            reasons = blocking_reasons.get(canonical_name, ["Unknown constraint"])
            blocked.append(BlockedPackageInfo(
                name=installed_pkg.name,
                version=installed_pkg.version,
                latest_version=latest_version,
                blocked_by=reasons,
                is_editable=installed_pkg.is_editable,
                editable_location=installed_pkg.editable_location
            ))

    return upgradable, blocked


def _make_failed_upgrade_results(
    packages: List[UpgradePackageInfo],
    reason: str,
) -> List[UpgradedPackage]:
    """Construct :class:`UpgradedPackage` entries for a batched install failure.

    :param packages: The candidates that would have been upgraded.
    :param reason: Failure reason attached to each result.
    :returns: One :class:`UpgradedPackage` per input, all with ``upgraded=False``.
    """
    return [
        UpgradedPackage(
            name=pkg.name,
            version=pkg.version,
            upgraded=False,
            previous_version=pkg.version,
            is_editable=pkg.is_editable,
            editable_location=pkg.editable_location,
            failure_reason=reason,
        )
        for pkg in packages
    ]


def _get_remote_package_versions(
    python_path: str,
    canonical_names: List[str],
    timeout: int = 30
) -> Dict[str, Version]:
    """Get current versions of packages from a remote environment.

    :param python_path: Path to Python interpreter
    :param canonical_names: List of canonical package names to check
    :param timeout: Timeout for subprocess call
    :returns: Dictionary mapping canonical names to Version objects
    """
    import json as json_module

    versions: Dict[str, Version] = {}
    try:
        result = subprocess.run(
            [python_path, '-m', 'pip', 'list', '--format=json'],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout
        )
        pip_packages = json_module.loads(result.stdout)
        name_set = set(canonical_names)
        for pkg_data in pip_packages:
            canonical = canonicalize_name(pkg_data["name"])
            if canonical in name_set:
                try:
                    versions[canonical] = Version(pkg_data["version"])
                except InvalidVersion:
                    pass
    except Exception as e:
        logger.warning(f"Failed to get remote package versions: {e}")

    return versions


def _get_local_package_versions(canonical_names: List[str]) -> Dict[str, Version]:
    """Get current versions of packages from the local environment.

    :param canonical_names: List of canonical package names to look up
    :returns: Dictionary mapping canonical names to Version objects
    """
    versions: Dict[str, Version] = {}
    name_set = set(canonical_names)
    env = get_default_environment()
    for dist in env.iter_all_distributions():
        try:
            package_name = dist.metadata["name"]
            canonical_name = canonicalize_name(package_name)
            if canonical_name in name_set:
                try:
                    versions[canonical_name] = Version(str(dist.version))
                except InvalidVersion:
                    logger.warning(f"Invalid version for {package_name}: {dist.version}")
        except Exception as e:
            logger.warning(f"Error processing package {dist.metadata.get('name', 'unknown')}: {e}")
    return versions


def install_packages(
    packages_to_upgrade: List[UpgradePackageInfo],
    output_stream: Optional[OutputStream] = None,
    timeout: int = 300,
    version_constraints: Optional[Dict[str, str]] = None,
    python_path: Optional[str] = None,
    interrupt_token: Optional[InterruptToken] = None,
) -> List[UpgradedPackage]:
    """
    Install/upgrade packages using pip.

    This function upgrades all packages in a single pip command to allow pip's
    dependency resolver to handle mutual constraints properly. After installation,
    it checks which packages were successfully upgraded by comparing installed
    versions with previous versions.

    :param packages_to_upgrade: List of UpgradePackageInfo objects to upgrade.
    :param output_stream: Optional stream implementing ``write()`` and ``flush()``
        for live progress updates.
    :param timeout: Timeout in seconds for the installation (default: 300).
    :param version_constraints: Optional dict mapping package names (lowercase)
        to version specifiers (e.g. ``"==2.31.0"``).
    :param python_path: Path to Python interpreter (default: ``None`` for the
        current environment).
    :param interrupt_token: Optional cross-thread cancellation token. When set,
        the pip subprocess is terminated and every candidate is returned with
        ``failure_reason="Installation interrupted"``.
    :returns: List of UpgradedPackage objects with upgrade status.
    """
    if not packages_to_upgrade:
        return []

    # Build a map of package name (canonical) to package info
    package_map = {
        canonicalize_name(pkg.name): pkg
        for pkg in packages_to_upgrade
    }

    # Construct pip install command with all packages at once
    # This allows pip to resolve mutual constraints properly
    # Apply version constraints if provided
    package_specs = []
    for pkg in packages_to_upgrade:
        pkg_name_lower = pkg.name.lower()
        if version_constraints and pkg_name_lower in version_constraints:
            # Use the specified version constraint
            constraint = version_constraints[pkg_name_lower]
            package_specs.append(f"{pkg.name}{constraint}")
        else:
            # Just upgrade to latest
            package_specs.append(pkg.name)

    argv = ['-m', 'pip', 'install', '--upgrade'] + package_specs

    try:
        # Write initial message to output stream
        if output_stream:
            output_stream.write(f"Upgrading {len(package_specs)} package(s)...\n")
            output_stream.flush()

        result: PipResult = run_pip(
            argv,
            python_path=python_path,
            output_stream=output_stream,
            timeout=timeout,
            stream_output=True,
            interrupt_token=interrupt_token,
        )

        if result.timed_out:
            if output_stream:
                output_stream.write("ERROR: Timeout during package upgrade\n")
                output_stream.flush()
            logger.error("Timeout during package upgrade")
            return _make_failed_upgrade_results(packages_to_upgrade, "Installation timed out")

        if result.interrupted:
            logger.warning("Package upgrade interrupted")
            return _make_failed_upgrade_results(packages_to_upgrade, "Installation interrupted")

        if result.returncode != 0:
            logger.warning(f"Package upgrade failed with return code {result.returncode}")
            captured = (result.stderr or result.stdout or "").strip()
            reason = (
                f"pip exit code {result.returncode}: {captured[:500]}"
                if captured else f"pip exit code {result.returncode}"
            )
            return _make_failed_upgrade_results(packages_to_upgrade, reason)

        # Installation succeeded - now determine which packages were actually upgraded
        if python_path is not None:
            current_versions = _get_remote_package_versions(
                python_path, list(package_map.keys()), timeout=30
            )
        else:
            current_versions = _get_local_package_versions(list(package_map.keys()))

        # Build results by comparing current vs previous versions
        results = []
        for pkg_info in packages_to_upgrade:
            canonical_name = canonicalize_name(pkg_info.name)
            previous_version = pkg_info.version

            # Check if package was upgraded
            current_version = current_versions.get(canonical_name)

            if current_version is not None and current_version > previous_version:
                # Package was successfully upgraded
                upgraded_pkg = UpgradedPackage(
                    name=pkg_info.name,
                    version=current_version,
                    upgraded=True,
                    previous_version=previous_version,
                    is_editable=pkg_info.is_editable,
                    editable_location=pkg_info.editable_location
                )
                results.append(upgraded_pkg)
                logger.info(f"Successfully upgraded {pkg_info.name} from {previous_version} to {current_version}")
            else:
                # Package was not upgraded (constraints prevented it, or already at target)
                actual_version = current_version if current_version is not None else previous_version
                upgraded_pkg = UpgradedPackage(
                    name=pkg_info.name,
                    version=actual_version,
                    upgraded=False,
                    previous_version=previous_version,
                    is_editable=pkg_info.is_editable,
                    editable_location=pkg_info.editable_location,
                    failure_reason=CONSTRAINED_BY_RESOLVER_REASON,
                )
                results.append(upgraded_pkg)
                logger.info(f"Package {pkg_info.name} was not upgraded (still at {actual_version})")

        return results

    except Exception as e:
        # Non-subprocess errors (argv assembly, post-install version lookup, etc.)
        if output_stream:
            output_stream.write(f"ERROR: Failed to upgrade packages: {e}\n")
            output_stream.flush()

        logger.error(f"Error upgrading packages: {e}")

        return _make_failed_upgrade_results(packages_to_upgrade, f"Installation failed: {e}")


def reinstall_editable_packages(
    editable_packages: List[UpgradePackageInfo],
    output_stream: Optional[OutputStream] = None,
    timeout: int = 300,
    python_path: Optional[str] = None,
    interrupt_token: Optional[InterruptToken] = None,
) -> List[UpgradedPackage]:
    """
    Reinstall editable packages to update their version metadata.

    Uses ``pip install --config-settings editable_mode=compat -e <path>`` to
    reinstall each editable package. This updates the package version in the
    environment while maintaining the editable install.

    :param editable_packages: List of UpgradePackageInfo objects for editable packages.
    :param output_stream: Optional stream implementing ``write()`` and
        ``flush()`` for live progress updates.
    :param timeout: Timeout in seconds for each installation (default: 300).
    :param python_path: Path to Python interpreter (default: ``None`` for the
        current environment).
    :param interrupt_token: Optional cross-thread cancellation token. When set,
        the in-flight pip subprocess is terminated and the current package is
        recorded with ``failure_reason="Installation interrupted"``. Remaining
        packages in the list are then skipped. Skipped packages are omitted
        from the result list entirely.
    :returns: List of UpgradedPackage objects with upgrade status.
    """
    if not editable_packages:
        return []

    results: List[UpgradedPackage] = []

    for pkg in editable_packages:
        if not pkg.editable_location:
            logger.warning(f"Editable package {pkg.name} has no location, skipping")
            results.append(UpgradedPackage(
                name=pkg.name,
                version=pkg.version,
                upgraded=False,
                previous_version=pkg.version,
                is_editable=True,
                editable_location=pkg.editable_location,
                failure_reason="Editable package has no source location"
            ))
            continue

        if output_stream:
            output_stream.write(f"Reinstalling editable package: {pkg.name} from {pkg.editable_location}\n")
            output_stream.flush()

        argv = [
            '-m', 'pip', 'install',
            '--no-build-isolation',
            '--config-settings', 'editable_mode=compat',
            '-e', pkg.editable_location,
        ]

        try:
            result = run_pip(
                argv,
                python_path=python_path,
                timeout=timeout,
                stream_output=False,
                interrupt_token=interrupt_token,
            )

            # Spinner UI suppresses live streaming; mirror captured output once here
            # so the debug log / caller sees what pip actually said.
            if output_stream and result.stdout:
                output_stream.write(result.stdout)
            if output_stream and result.stderr:
                output_stream.write(result.stderr)
            if output_stream:
                output_stream.flush()

            if result.timed_out:
                if output_stream:
                    output_stream.write(f"ERROR: Timeout reinstalling {pkg.name}\n")
                    output_stream.flush()
                results.append(UpgradedPackage(
                    name=pkg.name,
                    version=pkg.version,
                    upgraded=False,
                    previous_version=pkg.version,
                    is_editable=True,
                    editable_location=pkg.editable_location,
                    failure_reason="Installation timed out",
                ))
                logger.error(f"Timeout reinstalling editable package {pkg.name}")
                continue

            if result.interrupted:
                results.append(UpgradedPackage(
                    name=pkg.name,
                    version=pkg.version,
                    upgraded=False,
                    previous_version=pkg.version,
                    is_editable=True,
                    editable_location=pkg.editable_location,
                    failure_reason="Installation interrupted",
                ))
                logger.warning(f"Interrupted reinstalling editable package {pkg.name}")
                continue

            if result.returncode != 0:
                captured = (result.stderr or result.stdout or "").strip()
                reason = (
                    f"pip exit code {result.returncode}: {captured[:500]}"
                    if captured else f"pip exit code {result.returncode}"
                )
                results.append(UpgradedPackage(
                    name=pkg.name,
                    version=pkg.version,
                    upgraded=False,
                    previous_version=pkg.version,
                    is_editable=True,
                    editable_location=pkg.editable_location,
                    failure_reason=reason,
                ))
                logger.warning(f"Failed to reinstall editable package {pkg.name}")
                continue

            # Get the new version after reinstall
            canonical_name = canonicalize_name(pkg.name)
            new_version = pkg.version  # Default to old version

            if python_path is not None:
                remote_versions = _get_remote_package_versions(
                    python_path, [canonical_name], timeout=30
                )
                if canonical_name in remote_versions:
                    new_version = remote_versions[canonical_name]
            else:
                env = get_default_environment()
                for dist in env.iter_all_distributions():
                    dist_name = dist.metadata.get("name", "")
                    if canonicalize_name(dist_name) == canonical_name:
                        try:
                            new_version = Version(str(dist.version))
                        except InvalidVersion:
                            pass
                        break

            upgraded = new_version > pkg.version
            failure_reason = None
            if not upgraded:
                failure_reason = (
                    EDITABLE_SOURCE_UNCHANGED_REASON
                    if new_version == pkg.version
                    else "Editable source version did not increase"
                )

            results.append(UpgradedPackage(
                name=pkg.name,
                version=new_version,
                upgraded=upgraded,
                previous_version=pkg.version,
                is_editable=True,
                editable_location=pkg.editable_location,
                failure_reason=failure_reason,
            ))
            logger.info(f"Reinstalled editable package {pkg.name}: {pkg.version} -> {new_version}")

        except Exception as e:
            if output_stream:
                output_stream.write(f"ERROR: Failed to reinstall {pkg.name}: {e}\n")
                output_stream.flush()
            results.append(UpgradedPackage(
                name=pkg.name,
                version=pkg.version,
                upgraded=False,
                previous_version=pkg.version,
                is_editable=True,
                editable_location=pkg.editable_location,
                failure_reason=f"Installation failed: {e}",
            ))
            logger.error(f"Error reinstalling editable package {pkg.name}: {e}")

    return results


def run_pip_install(
    package_specs: List[str],
    upgrade: bool = True,
    output_stream: Optional[OutputStream] = None,
    timeout: int = 300,
    python_path: Optional[str] = None,
    pre: bool = False,
    interrupt_token: Optional[InterruptToken] = None,
) -> List[InstalledResult]:
    """Install packages using pip.

    Wraps ``pip install`` (with ``-U`` by default) and reports what was
    installed, updated, or left unchanged. Delegates subprocess lifecycle,
    output streaming, timeout enforcement, and cancellation to
    :func:`pipu_cli._subprocess.run_pip`.

    :param package_specs: Package specifications (e.g. ``["requests", "numpy==1.24"]``)
    :param upgrade: Add ``-U`` flag to pip install (default: True)
    :param output_stream: Optional stream for real-time pip output
    :param timeout: Subprocess timeout in seconds (default: 300)
    :param python_path: Path to Python interpreter for remote environments
    :param pre: Include pre-release versions
    :param interrupt_token: Optional cross-thread cancellation token. When set,
        the pip subprocess is terminated and every candidate is returned with
        ``failure_reason="Installation interrupted"``.
    :returns: List of InstalledResult objects describing the outcome
    """
    if not package_specs:
        return []

    # Extract canonical names for version lookups. VCS URLs, git+/http(s) specs,
    # and similar inputs are not PEP 508 requirements and raise ValueError from
    # parse_package_spec; fall back to canonicalize_name(spec) so pip still sees
    # the raw spec and version snapshotting has a stable dict key.
    canonical_names: List[str] = []
    for spec in package_specs:
        try:
            canonical_names.append(parse_package_spec(spec).name)
        except ValueError:
            canonical_names.append(canonicalize_name(spec))
    # Map canonical name back to the original spec for result reporting
    name_to_spec = dict(zip(canonical_names, package_specs))

    # Snapshot pre-install versions
    if python_path is not None:
        pre_versions = _get_remote_package_versions(python_path, canonical_names, timeout=30)
    else:
        pre_versions = _get_local_package_versions(canonical_names)

    def _failed_results(reason: str) -> List[InstalledResult]:
        return [
            InstalledResult(
                name=name_to_spec[cn],
                version=pre_versions.get(cn, Version("0")),
                installed=False,
                previous_version=pre_versions.get(cn),
                failure_reason=reason,
            )
            for cn in canonical_names
        ]

    # Build pip argv (python interpreter is supplied by run_pip)
    argv: List[str] = ['-m', 'pip', 'install']
    if upgrade:
        argv.append('-U')
    if pre:
        argv.append('--pre')
    argv.extend(package_specs)

    try:
        if output_stream:
            output_stream.write(f"Installing {len(package_specs)} package(s)...\n")
            output_stream.flush()

        result: PipResult = run_pip(
            argv,
            python_path=python_path,
            output_stream=output_stream,
            timeout=timeout,
            stream_output=True,
            interrupt_token=interrupt_token,
        )

        if result.timed_out:
            if output_stream:
                output_stream.write("ERROR: Timeout during package installation\n")
                output_stream.flush()
            logger.warning("Timeout during package installation")
            return _failed_results("Installation timed out")

        if result.interrupted:
            logger.warning("Package installation interrupted")
            return _failed_results("Installation interrupted")

        if result.returncode != 0:
            logger.warning(f"pip install failed with return code {result.returncode}")
            captured = (result.stderr or result.stdout or "").strip()
            reason = (
                f"pip exit code {result.returncode}: {captured[:500]}"
                if captured else f"pip exit code {result.returncode}"
            )
            return _failed_results(reason)

        # Snapshot post-install versions
        if python_path is not None:
            post_versions = _get_remote_package_versions(python_path, canonical_names, timeout=30)
        else:
            post_versions = _get_local_package_versions(canonical_names)

        # Build results
        results: List[InstalledResult] = []
        for cn in canonical_names:
            pre_ver = pre_versions.get(cn)
            post_ver = post_versions.get(cn)

            if post_ver is not None:
                results.append(InstalledResult(
                    name=name_to_spec[cn],
                    version=post_ver,
                    installed=True,
                    previous_version=pre_ver,
                ))
            else:
                # Package not found after install — likely a failure pip didn't report
                results.append(InstalledResult(
                    name=name_to_spec[cn],
                    version=pre_ver if pre_ver is not None else Version("0"),
                    installed=False,
                    previous_version=pre_ver,
                    failure_reason="Package not found after installation",
                ))

        return results

    except Exception as e:
        # Non-subprocess errors (argv assembly, post-install version lookup, etc.)
        if output_stream:
            output_stream.write(f"ERROR: Failed to install packages: {e}\n")
            output_stream.flush()

        logger.error(f"Error installing packages: {e}")

        return _failed_results(f"Installation failed: {e}")


def run_pip_uninstall(
    package_names: List[str],
    output_stream: Optional[OutputStream] = None,
    timeout: int = 300,
    python_path: Optional[str] = None,
    interrupt_token: Optional[InterruptToken] = None,
) -> List[UninstalledResult]:
    """Uninstall packages using pip.

    Delegates subprocess lifecycle, output streaming, timeout enforcement,
    and cancellation to :func:`pipu_cli._subprocess.run_pip`. Packages that
    are already absent at the time of the call are short-circuited to
    ``UninstalledResult(uninstalled=True, already_absent=True)`` without
    invoking pip.

    :param package_names: Package names to uninstall.
    :param output_stream: Optional stream for real-time pip output.
    :param timeout: Subprocess timeout in seconds (default: 300).
    :param python_path: Path to Python interpreter for remote environments.
    :param interrupt_token: Optional cross-thread cancellation token. When set,
        the pip subprocess is terminated and every remaining candidate is
        returned with ``failure_reason="Uninstall interrupted"``.
    :returns: List of UninstalledResult objects describing the outcome.
    """
    if not package_names:
        return []

    canonical_names: List[str] = [canonicalize_name(name) for name in package_names]
    name_to_orig = dict(zip(canonical_names, package_names))

    if python_path is not None:
        pre_versions = _get_remote_package_versions(python_path, canonical_names, timeout=30)
    else:
        pre_versions = _get_local_package_versions(canonical_names)

    not_installed = [cn for cn in canonical_names if cn not in pre_versions]
    if not_installed:
        results: List[UninstalledResult] = []
        for cn in canonical_names:
            if cn in not_installed:
                results.append(UninstalledResult(
                    name=name_to_orig[cn],
                    previous_version=None,
                    uninstalled=True,
                    already_absent=True,
                ))
            else:
                results.append(UninstalledResult(
                    name=name_to_orig[cn],
                    previous_version=pre_versions.get(cn),
                    uninstalled=False,
                    failure_reason=None,
                ))
        to_uninstall = [cn for cn in canonical_names if cn not in not_installed]
        if not to_uninstall:
            return results
        canonical_names = to_uninstall
        partial_results = results
    else:
        partial_results = []

    def _failed_results(reason: str) -> List[UninstalledResult]:
        return partial_results + [
            UninstalledResult(
                name=name_to_orig[cn],
                previous_version=pre_versions.get(cn),
                uninstalled=False,
                failure_reason=reason,
            )
            for cn in canonical_names
        ]

    # Build pip argv (python interpreter is supplied by run_pip)
    argv: List[str] = ['-m', 'pip', 'uninstall', '-y']
    argv.extend([name_to_orig[cn] for cn in canonical_names])

    try:
        if output_stream:
            output_stream.write(f"Uninstalling {len(canonical_names)} package(s)...\n")
            output_stream.flush()

        result: PipResult = run_pip(
            argv,
            python_path=python_path,
            output_stream=output_stream,
            timeout=timeout,
            stream_output=True,
            interrupt_token=interrupt_token,
        )

        if result.timed_out:
            if output_stream:
                output_stream.write("ERROR: Timeout during package uninstallation\n")
                output_stream.flush()
            logger.warning("Timeout during package uninstallation")
            return _failed_results("Uninstall timed out")

        if result.interrupted:
            logger.warning("Package uninstallation interrupted")
            return _failed_results("Uninstall interrupted")

        if result.returncode != 0:
            logger.warning(f"pip uninstall failed with return code {result.returncode}")
            captured = (result.stderr or result.stdout or "").strip()
            reason = (
                f"pip exit code {result.returncode}: {captured[:500]}"
                if captured else f"pip exit code {result.returncode}"
            )
            return _failed_results(reason)

        if python_path is not None:
            post_versions = _get_remote_package_versions(python_path, canonical_names, timeout=30)
        else:
            post_versions = _get_local_package_versions(canonical_names)

        results = list(partial_results)
        for cn in canonical_names:
            if cn not in post_versions:
                results.append(UninstalledResult(
                    name=name_to_orig[cn],
                    previous_version=pre_versions.get(cn),
                    uninstalled=True,
                ))
            else:
                results.append(UninstalledResult(
                    name=name_to_orig[cn],
                    previous_version=pre_versions.get(cn),
                    uninstalled=False,
                    failure_reason="Package still present after uninstall",
                ))

        return results

    except Exception as e:
        # Non-subprocess errors (argv assembly, post-uninstall version lookup, etc.)
        if output_stream:
            output_stream.write(f"ERROR: Failed to uninstall packages: {e}\n")
            output_stream.flush()

        logger.error(f"Error uninstalling packages: {e}")

        return _failed_results(f"Uninstallation failed: {e}")


def _build_forward_reverse(
    installed: List[InstalledPackage],
) -> tuple[
    Dict[str, InstalledPackage],
    Dict[str, List[tuple[InstalledPackage, str]]],
    Dict[str, List[InstalledPackage]],
]:
    """Build forward, reverse, and duplicate-install indices.

    Duplicates arise when ``installed`` contains two or more distributions
    sharing a canonical name (e.g. the same package found under multiple
    site-packages directories). The forward index keeps the highest-version
    representative; the returned ``duplicates`` map contains every
    canonical name with count >= 2 along with all contributing
    distributions. Duplicates are a correctness concern the caller should
    surface, not something to silently hide.

    The reverse index dedupes on ``(parent canonical name, parent version,
    specifier, editable_location)`` so duplicate distributions don't
    create literally identical tree rows, while genuinely distinct
    versions remain as separate edges.

    :param installed: All installed packages (may contain duplicates).
    :returns: ``(forward, reverse, duplicates)``.
    """
    by_name: Dict[str, List[InstalledPackage]] = {}
    for pkg in installed:
        by_name.setdefault(canonicalize_name(pkg.name), []).append(pkg)

    forward: Dict[str, InstalledPackage] = {
        name: max(pkgs, key=lambda p: p.version)
        for name, pkgs in by_name.items()
    }
    duplicates: Dict[str, List[InstalledPackage]] = {
        name: pkgs for name, pkgs in by_name.items() if len(pkgs) > 1
    }

    reverse: Dict[str, List[tuple[InstalledPackage, str]]] = {}
    seen_per_dep: Dict[str, set] = {}
    for parent in installed:
        for dep_name, spec in parent.constrained_dependencies.items():
            dep_canonical = canonicalize_name(dep_name)
            seen = seen_per_dep.setdefault(dep_canonical, set())
            key = (
                canonicalize_name(parent.name),
                str(parent.version),
                spec,
                parent.editable_location,
            )
            if key in seen:
                continue
            seen.add(key)
            reverse.setdefault(dep_canonical, []).append((parent, spec))
    return forward, reverse, duplicates


def build_dep_report(
    package_name: str,
    *,
    depth: int = 1,
    installed: Optional[List[InstalledPackage]] = None,
    python_path: Optional[str] = None,
    editable_exists: Callable[[str], bool] = os.path.exists,
) -> "DepReport":
    """Inspect PACKAGE's neighborhood and return a :class:`DepReport`.

    Pure function. No network. When ``installed`` is omitted, falls back
    to :func:`inspect_installed_packages`. ``editable_exists`` is injected
    so tests can simulate missing paths without touching the filesystem.

    :param package_name: User-supplied package name (will be canonicalized).
    :param depth: Recursion depth on both branches. ``1`` = direct only.
        ``0`` = unlimited (cycles still terminate).
    :param installed: Pre-built list; default: call
        :func:`inspect_installed_packages`.
    :param python_path: Forwarded to ``inspect_installed_packages`` when
        ``installed`` is not provided.
    :param editable_exists: Predicate used to test whether an editable
        install path exists on disk. Defaults to :func:`os.path.exists`.
    :returns: A fully populated :class:`DepReport`.
    :raises PackageNotInstalledError: If ``package_name`` (after
        canonicalization) is not in the installed set.
    """
    if depth < 0:
        raise ValueError("depth must be >= 0")

    pkgs = installed if installed is not None else inspect_installed_packages(python_path=python_path)
    canonical = canonicalize_name(package_name)
    forward, reverse, duplicates = _build_forward_reverse(pkgs)

    if canonical not in forward:
        raise PackageNotInstalledError(canonical)

    subject = forward[canonical]
    orphan_metadata = get_orphan_metadata(python_path)

    required_by = _walk_required_by(canonical, forward, reverse, depth=depth, visited=frozenset({canonical}))
    requires = _walk_requires(canonical, forward, depth=depth, visited=frozenset({canonical}))
    problems = _collect_problems(
        subject, required_by, requires, forward, duplicates, orphan_metadata, editable_exists,
    )

    return DepReport(
        package=subject,
        required_by=required_by,
        requires=requires,
        problems=problems,
    )


def _walk_required_by(
    current: str,
    forward: Dict[str, InstalledPackage],
    reverse: Dict[str, List[tuple[InstalledPackage, str]]],
    *,
    depth: int,
    visited: frozenset,
) -> List["DepNode"]:
    """Walk the required-by branch from ``current`` up to ``depth`` hops.

    :param current: Canonical name of the node whose parents to enumerate.
    :param forward: Canonical name -> InstalledPackage.
    :param reverse: Canonical name -> list of (parent pkg, specifier).
    :param depth: Remaining hops. ``0`` means unlimited (bounded by visited).
    :param visited: Canonical names already on the current path; prevents
        cycles.
    :returns: Immediate DepNode parents, each with their own recursed children.
    """
    nodes: List[DepNode] = []
    parents = reverse.get(current, [])
    for parent_pkg, spec in sorted(parents, key=lambda ps: ps[0].name):
        parent_name = canonicalize_name(parent_pkg.name)
        edge = DepEdge(
            name=parent_name,
            installed_version=parent_pkg.version,
            specifier=spec,
            is_editable=parent_pkg.is_editable,
            editable_location=parent_pkg.editable_location,
        )
        if parent_name in visited:
            nodes.append(DepNode(edge=edge, children=[], is_cycle=True))
            continue
        next_depth = depth - 1 if depth > 0 else 0
        recurse = depth == 0 or next_depth > 0
        if recurse:
            children = _walk_required_by(
                parent_name, forward, reverse,
                depth=next_depth,
                visited=visited | {parent_name},
            )
        else:
            children = []
        nodes.append(DepNode(edge=edge, children=children, is_cycle=False))
    return nodes


def _walk_requires(
    current: str,
    forward: Dict[str, InstalledPackage],
    *,
    depth: int,
    visited: frozenset,
) -> List["DepNode"]:
    """Walk the requires branch from ``current`` up to ``depth`` hops.

    :param current: Canonical name whose children to enumerate.
    :param forward: Canonical name -> InstalledPackage.
    :param depth: Remaining hops. ``0`` means unlimited.
    :param visited: Canonical names already on the current path.
    :returns: Immediate DepNode children, each recursed.
    """
    nodes: List[DepNode] = []
    pkg = forward.get(current)
    if pkg is None:
        return nodes
    for dep_name, spec in sorted(pkg.constrained_dependencies.items()):
        canonical_dep = canonicalize_name(dep_name)
        dep_pkg = forward.get(canonical_dep)
        if dep_pkg is not None:
            edge = DepEdge(
                name=canonical_dep,
                installed_version=dep_pkg.version,
                specifier=spec,
                is_editable=dep_pkg.is_editable,
                editable_location=dep_pkg.editable_location,
            )
        else:
            edge = DepEdge(name=canonical_dep, installed_version=None, specifier=spec)
        if canonical_dep in visited:
            nodes.append(DepNode(edge=edge, children=[], is_cycle=True))
            continue
        next_depth = depth - 1 if depth > 0 else 0
        recurse = depth == 0 or next_depth > 0
        if recurse and dep_pkg is not None:
            children = _walk_requires(
                canonical_dep, forward,
                depth=next_depth,
                visited=visited | {canonical_dep},
            )
        else:
            children = []
        nodes.append(DepNode(edge=edge, children=children, is_cycle=False))
    return nodes


def _safe_specifier_set(spec: str) -> Optional[SpecifierSet]:
    """Parse a specifier string, returning ``None`` if it's not valid PEP 440."""
    try:
        return SpecifierSet(spec)
    except InvalidSpecifier:
        return None


@dataclass(frozen=True)
class _ProblemEdge:
    """Normalized edge record fed to :func:`_collect_problems_over_edges`.

    :param owner_name: Canonical name of the package whose version is
        being constrained by this edge (on the requires branch, this is
        the edge's own name; on the required-by branch, this is the
        *parent* node's name).
    :param owner_version: Installed version of ``owner_name``, or
        ``None`` when not installed (requires-branch only).
    :param owner_is_editable: Whether ``owner_name`` is editable.
    :param owner_editable_location: Editable install path, if any.
    :param constraint_source: Canonical name of the package imposing
        the constraint (on the requires branch, this is the parent
        node; on the required-by branch, this is the edge's own name).
    :param specifier: PEP 440 specifier string; empty if unconstrained.
    :param branch: ``"requires"`` or ``"required_by"`` — only controls
        how ``missing`` is emitted (only the requires branch can emit
        missing, since a parent can't be "missing" in our model).
    """

    owner_name: str
    owner_version: Optional[Version]
    owner_is_editable: bool
    owner_editable_location: Optional[str]
    constraint_source: str
    specifier: str
    branch: str


def _collect_problems_over_edges(
    edges: Iterable["_ProblemEdge"],
    *,
    duplicates: Dict[str, List[InstalledPackage]],
    orphan_metadata: Dict[str, List[Dict[str, str]]],
    editable_exists: Callable[[str], bool],
    problem_target_names: Optional[set[str]] = None,
    subject_editable: Optional[InstalledPackage] = None,
) -> List["DepProblem"]:
    """Scan a flat edge stream for problems. Pure, no tree knowledge.

    :param edges: Iterable of :class:`_ProblemEdge` records.
    :param duplicates: Canonical-name -> list of duplicate installs.
    :param orphan_metadata: Canonical-name -> list of
        ``{"version", "path"}`` orphan metadata entries.
    :param editable_exists: Injected ``os.path.exists`` for testability.
    :param problem_target_names: If given, ``duplicate-install`` and
        ``stale-metadata`` problems are only emitted for names in this
        set. Used by :func:`build_dep_report` to scope its problem panel
        to the visible tree; omit or pass ``None`` to report all
        duplicates and orphans (:func:`build_env_report` uses this).
    :param subject_editable: An optional subject :class:`InstalledPackage`
        whose broken-editable state should be emitted even if it doesn't
        appear as an edge. Used by :func:`build_dep_report` (subject
        view). Omit for env-wide scans.
    :returns: Deduped, sorted list of :class:`DepProblem`.
    """
    seen: set[tuple] = set()
    problems: List[DepProblem] = []

    def emit(problem: DepProblem) -> None:
        key = (problem.kind, problem.package, problem.required_by, problem.specifier)
        if key in seen:
            return
        seen.add(key)
        problems.append(problem)

    # 1) Subject's own broken-editable (deps-only path).
    if subject_editable is not None and subject_editable.is_editable and subject_editable.editable_location \
            and not editable_exists(subject_editable.editable_location):
        emit(DepProblem(
            kind="broken-editable",
            package=canonicalize_name(subject_editable.name),
            detail=f"{subject_editable.name} editable path missing: {subject_editable.editable_location}",
        ))

    # 2) Per-edge scans.
    for e in edges:
        if e.branch == "requires" and e.owner_version is None:
            emit(DepProblem(
                kind="missing",
                package=e.owner_name,
                detail=f"{e.owner_name} is required by {e.constraint_source} but is not installed",
            ))
        elif e.specifier and e.owner_version is not None:
            spec_set = _safe_specifier_set(e.specifier)
            if spec_set is not None and not spec_set.contains(e.owner_version, prereleases=True):
                emit(DepProblem(
                    kind="violates",
                    package=e.owner_name,
                    detail=(
                        f"{e.owner_name} {e.owner_version} violates "
                        f"{e.owner_name}{e.specifier} required by {e.constraint_source}"
                    ),
                    required_by=e.constraint_source,
                    specifier=e.specifier,
                    installed_version=e.owner_version,
                ))
        if e.owner_is_editable and e.owner_editable_location and not editable_exists(e.owner_editable_location):
            emit(DepProblem(
                kind="broken-editable",
                package=e.owner_name,
                detail=f"{e.owner_name} editable path missing: {e.owner_editable_location}",
            ))

    # 3) Duplicate installs (scoped or env-wide).
    for name, variants in duplicates.items():
        if problem_target_names is not None and name not in problem_target_names:
            continue
        versions = ", ".join(sorted({str(p.version) for p in variants}))
        emit(DepProblem(
            kind="duplicate-install",
            package=name,
            detail=f"{name} has {len(variants)} installed distributions ({versions})",
        ))

    # 4) Orphaned metadata (scoped or env-wide).
    for name, entries in orphan_metadata.items():
        if problem_target_names is not None and name not in problem_target_names:
            continue
        paths = ", ".join(entry.get("path", "?") for entry in entries)
        emit(DepProblem(
            kind="stale-metadata",
            package=name,
            detail=f"{name} has orphaned metadata: {paths}",
        ))

    kind_order = {
        "missing": 0,
        "violates": 1,
        "broken-editable": 2,
        "duplicate-install": 3,
        "stale-metadata": 4,
    }
    problems.sort(key=lambda p: (kind_order.get(p.kind, 99), p.package, p.required_by or ""))
    return problems


def _collect_problems(
    subject: InstalledPackage,
    required_by: List["DepNode"],
    requires: List["DepNode"],
    forward: Dict[str, InstalledPackage],
    duplicates: Dict[str, List[InstalledPackage]],
    orphan_metadata: Dict[str, List[Dict[str, str]]],
    editable_exists: Callable[[str], bool],
) -> List["DepProblem"]:
    """Subject-scoped problem scan for :func:`build_dep_report`.

    Flattens the two visible tree branches into a :class:`_ProblemEdge`
    stream and delegates to :func:`_collect_problems_over_edges`. The
    problem panel is scoped to names appearing in the visible tree (plus
    the subject itself), matching the existing deps behavior.
    """
    subject_name = canonicalize_name(subject.name)
    visible_names: set[str] = {subject_name}

    def walk_required_by(
        nodes: List[DepNode], *,
        parent_version: Optional[Version],
        parent_name: str,
    ) -> List[_ProblemEdge]:
        out: List[_ProblemEdge] = []
        for node in nodes:
            visible_names.add(node.edge.name)
            out.append(_ProblemEdge(
                owner_name=parent_name,
                owner_version=parent_version,
                owner_is_editable=False,
                owner_editable_location=None,
                constraint_source=node.edge.name,
                specifier=node.edge.specifier,
                branch="required_by",
            ))
            # Edge-level broken-editable for the edge's own package.
            out.append(_ProblemEdge(
                owner_name=node.edge.name,
                owner_version=node.edge.installed_version,
                owner_is_editable=node.edge.is_editable,
                owner_editable_location=node.edge.editable_location,
                constraint_source=parent_name,
                specifier="",  # suppress violates on this synthetic edge
                branch="required_by",
            ))
            if not node.is_cycle and node.children:
                out.extend(walk_required_by(
                    node.children,
                    parent_version=node.edge.installed_version,
                    parent_name=node.edge.name,
                ))
        return out

    def walk_requires(nodes: List[DepNode], *, parent_name: str) -> List[_ProblemEdge]:
        out: List[_ProblemEdge] = []
        for node in nodes:
            visible_names.add(node.edge.name)
            out.append(_ProblemEdge(
                owner_name=node.edge.name,
                owner_version=node.edge.installed_version,
                owner_is_editable=node.edge.is_editable,
                owner_editable_location=node.edge.editable_location,
                constraint_source=parent_name,
                specifier=node.edge.specifier,
                branch="requires",
            ))
            if not node.is_cycle and node.children:
                out.extend(walk_requires(node.children, parent_name=node.edge.name))
        return out

    edges: List[_ProblemEdge] = []
    edges.extend(walk_required_by(required_by, parent_version=subject.version, parent_name=subject_name))
    edges.extend(walk_requires(requires, parent_name=subject_name))

    return _collect_problems_over_edges(
        edges,
        duplicates=duplicates,
        orphan_metadata=orphan_metadata,
        editable_exists=editable_exists,
        problem_target_names=visible_names,
        subject_editable=subject,
    )


def _collect_env_problems(
    installed: List[InstalledPackage],
    *,
    duplicates: Dict[str, List[InstalledPackage]],
    orphan_metadata: Dict[str, List[Dict[str, str]]],
    editable_exists: Callable[[str], bool],
) -> List["DepProblem"]:
    """Env-wide problem scan.

    Walks every installed package's ``constrained_dependencies`` as the
    edge stream and emits broken-editable for every editable package.
    Duplicates and orphan metadata are emitted env-wide (no
    ``problem_target_names`` filter).

    :param installed: All installed packages (may contain duplicates).
    :param duplicates: From :func:`_build_forward_reverse`.
    :param orphan_metadata: From :func:`get_orphan_metadata`.
    :param editable_exists: Injected ``os.path.exists`` for testability.
    :returns: Deduped, sorted list of :class:`DepProblem`.
    """
    forward: Dict[str, InstalledPackage] = {canonicalize_name(p.name): p for p in installed}

    edges: List[_ProblemEdge] = []
    for pkg in installed:
        pkg_canonical = canonicalize_name(pkg.name)
        # Edge for the editable status of the package itself.
        edges.append(_ProblemEdge(
            owner_name=pkg_canonical,
            owner_version=pkg.version,
            owner_is_editable=pkg.is_editable,
            owner_editable_location=pkg.editable_location,
            constraint_source=pkg_canonical,
            specifier="",
            branch="requires",
        ))
        for dep_name, spec in pkg.constrained_dependencies.items():
            dep_canonical = canonicalize_name(dep_name)
            dep_pkg = forward.get(dep_canonical)
            edges.append(_ProblemEdge(
                owner_name=dep_canonical,
                owner_version=dep_pkg.version if dep_pkg is not None else None,
                owner_is_editable=dep_pkg.is_editable if dep_pkg is not None else False,
                owner_editable_location=dep_pkg.editable_location if dep_pkg is not None else None,
                constraint_source=pkg_canonical,
                specifier=spec,
                branch="requires",
            ))

    return _collect_problems_over_edges(
        edges,
        duplicates=duplicates,
        orphan_metadata=orphan_metadata,
        editable_exists=editable_exists,
        problem_target_names=None,  # env-wide
        subject_editable=None,
    )


def build_env_report(
    *,
    installed: Optional[List[InstalledPackage]] = None,
    python_path: Optional[str] = None,
    editable_exists: Callable[[str], bool] = os.path.exists,
) -> "EnvReport":
    """Inspect an environment and return an :class:`EnvReport`.

    Pure function. Mirrors :func:`build_dep_report` shape but returns a
    whole-env snapshot.

    :param installed: Pre-built list; default: call
        :func:`inspect_installed_packages(python_path=python_path)`.
    :param python_path: Forwarded to ``inspect_installed_packages`` when
        ``installed`` is not provided; also recorded on the result.
    :param editable_exists: Injected for testability.
    :returns: An :class:`EnvReport`.
    """
    pkgs = installed if installed is not None else inspect_installed_packages(python_path=python_path)
    _forward, _reverse, duplicates = _build_forward_reverse(pkgs)
    orphan_metadata = get_orphan_metadata(python_path)
    problems = _collect_env_problems(
        pkgs,
        duplicates=duplicates,
        orphan_metadata=orphan_metadata,
        editable_exists=editable_exists,
    )
    return EnvReport(
        python_path=python_path,
        package_count=len(pkgs),
        problems=problems,
    )
