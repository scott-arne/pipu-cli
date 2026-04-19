"""Package management functions for pipu-cli."""

import logging
import os.path
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Callable, runtime_checkable

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


def _inspect_remote_packages(timeout: int, python_path: str) -> List[InstalledPackage]:
    """Inspect packages in a remote Python environment via subprocess.

    :param timeout: Timeout in seconds for subprocess calls
    :param python_path: Path to Python interpreter
    :returns: List of InstalledPackage objects
    :raises RuntimeError: If unable to inspect remote packages
    """
    import json as json_module

    try:
        # Get editable packages first
        editable_packages = _get_editable_packages(timeout, python_path=python_path)

        # Get all installed packages via pip list --format=json
        result = subprocess.run(
            [python_path, '-m', 'pip', 'list', '--format=json'],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout
        )

        pip_packages = json_module.loads(result.stdout)
        packages = []

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

                package_info = InstalledPackage(
                    name=package_name,
                    version=package_version,
                    is_editable=is_editable,
                    editable_location=editable_location,
                    # Skip constraint extraction for remote environments
                    constrained_dependencies={},
                )
                packages.append(package_info)
            except Exception as e:
                logger.warning(f"Error processing remote package {pkg_data.get('name', 'unknown')}: {e}")
                continue

        packages.sort(key=lambda p: p.name.lower())
        return packages

    except Exception as e:
        raise RuntimeError(f"Failed to inspect remote packages at {python_path}: {e}") from e


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

    session, package_finder = _build_pip_session(
        timeout=timeout, include_prereleases=include_prereleases,
    )

    # Thread-safe result storage and progress tracking
    result: Dict[InstalledPackage, Package] = {}
    result_lock = threading.Lock()
    progress_lock = threading.Lock()
    completed_count = [0]  # Mutable container for thread-safe counter
    total_packages = len(installed_packages)

    def check_package(installed_pkg: InstalledPackage) -> Optional[tuple[InstalledPackage, Package]]:
        """Check a single package for updates."""
        try:
            # Get canonical name for querying
            canonical_name = canonicalize_name(installed_pkg.name)

            # Find all available versions
            candidates = package_finder.find_all_candidates(canonical_name)

            if not candidates:
                logger.debug(f"No candidates found for {installed_pkg.name}")
                return None

            # Filter out pre-releases if not requested
            if not include_prereleases:
                stable_candidates = []
                for candidate in candidates:
                    try:
                        version_obj = Version(str(candidate.version))
                        if not version_obj.is_prerelease:
                            stable_candidates.append(candidate)
                    except InvalidVersion:
                        continue

                # Use stable candidates if available, otherwise use all
                candidates = stable_candidates if stable_candidates else candidates

            # Get the latest version
            if candidates:
                latest_candidate = max(candidates, key=lambda c: c.version)
                latest_version = Version(str(latest_candidate.version))

                # Create Package object with latest version
                latest_package = Package(
                    name=installed_pkg.name,
                    version=latest_version
                )

                logger.debug(f"Found latest version for {installed_pkg.name}: {latest_version}")
                return (installed_pkg, latest_package)

        except Exception as e:
            logger.warning(f"Error checking {installed_pkg.name}: {e}")
            return None

        return None

    # Execute parallel queries
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        futures = {
            executor.submit(check_package, pkg): pkg
            for pkg in installed_packages
        }

        # Process results as they complete
        for future in as_completed(futures):
            result_tuple = future.result()

            # Update result if package was found
            if result_tuple:
                installed_pkg, latest_pkg = result_tuple
                with result_lock:
                    result[installed_pkg] = latest_pkg

            # Update progress
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
    session, package_finder = _build_pip_session(
        timeout=timeout, include_prereleases=include_prereleases,
    )

    # Query latest version for each package
    result: Dict[InstalledPackage, Package] = {}
    total_packages = len(installed_packages)

    for idx, installed_pkg in enumerate(installed_packages):
        # Report progress if callback provided
        if progress_callback:
            progress_callback(idx, total_packages)

        try:
            # Get canonical name for querying
            canonical_name = canonicalize_name(installed_pkg.name)

            # Find all available versions
            candidates = package_finder.find_all_candidates(canonical_name)

            if not candidates:
                logger.debug(f"No candidates found for {installed_pkg.name}")
                continue

            # Filter out pre-releases if not requested
            if not include_prereleases:
                stable_candidates = []
                for candidate in candidates:
                    try:
                        version_obj = Version(str(candidate.version))
                        if not version_obj.is_prerelease:
                            stable_candidates.append(candidate)
                    except InvalidVersion:
                        continue

                # Use stable candidates if available, otherwise use all
                candidates = stable_candidates if stable_candidates else candidates

            # Get the latest version
            if candidates:
                latest_candidate = max(candidates, key=lambda c: c.version)
                latest_version = Version(str(latest_candidate.version))

                # Create Package object with latest version
                latest_package = Package(
                    name=installed_pkg.name,
                    version=latest_version
                )

                result[installed_pkg] = latest_package
                logger.debug(f"Found latest version for {installed_pkg.name}: {latest_version}")

        except Exception as e:
            logger.warning(f"Error checking {installed_pkg.name}: {e}")
            continue

    # Report completion
    if progress_callback:
        progress_callback(total_packages, total_packages)

    return result


def resolve_upgradable_packages(
    upgrade_candidates: Dict[InstalledPackage, Package],
    all_installed: List[InstalledPackage]
) -> List[UpgradePackageInfo]:
    """Resolve upgradable packages, discarding block reasons.

    Thin wrapper around :func:`resolve_upgradable_packages_with_reasons`.
    The plain list includes every candidate (upgradable and blocked), with
    the ``upgradable`` flag set accordingly, matching the prior contract.

    :param upgrade_candidates: Dict mapping installed packages to their latest available versions.
    :param all_installed: List of all installed packages (for constraint checking).
    :returns: List of UpgradePackageInfo objects, each flagged upgradable or not.
    """
    upgradable, _blocked = resolve_upgradable_packages_with_reasons(
        upgrade_candidates, all_installed
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
    all_installed: List[InstalledPackage]
) -> tuple[List[UpgradePackageInfo], List[BlockedPackageInfo]]:
    """
    Resolve upgradable packages and provide detailed blocking reasons.

    Returns both upgradable packages and blocked packages with reasons.

    :param upgrade_candidates: Dict mapping installed packages to their latest available versions
    :param all_installed: List of all installed packages (for constraint checking)
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

    # Fixed-point iteration
    upgrading_packages = {canonicalize_name(pkg.name) for pkg in actual_upgrades.keys()}
    max_iterations = len(upgrading_packages) + 1

    for _iteration in range(1, max_iterations + 1):
        packages_to_remove = set()

        for installed_pkg, latest_pkg in actual_upgrades.items():
            canonical_name = canonicalize_name(installed_pkg.name)

            if canonical_name not in upgrading_packages:
                continue

            latest_version = latest_pkg.version

            if canonical_name in constraints_on:
                for constraining_pkg, specifier_str in constraints_on[canonical_name]:
                    try:
                        specifier = SpecifierSet(specifier_str)
                        satisfies = latest_version in specifier

                        if not satisfies:
                            constraining_canonical = canonicalize_name(constraining_pkg.name)
                            if constraining_canonical not in upgrading_packages:
                                packages_to_remove.add(canonical_name)
                                reason = f"{constraining_pkg.name} requires {specifier_str}"
                                if canonical_name not in blocking_reasons:
                                    blocking_reasons[canonical_name] = []
                                blocking_reasons[canonical_name].append(reason)
                                break
                    except (InvalidSpecifier, Exception):
                        constraining_canonical = canonicalize_name(constraining_pkg.name)
                        if constraining_canonical not in upgrading_packages:
                            packages_to_remove.add(canonical_name)
                            reason = f"{constraining_pkg.name} (invalid constraint)"
                            if canonical_name not in blocking_reasons:
                                blocking_reasons[canonical_name] = []
                            blocking_reasons[canonical_name].append(reason)
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
                    failure_reason="Version unchanged \u2014 may be constrained by dependency resolver"
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

            # For editable packages, a successful pip install is sufficient
            # to consider the reinstall successful. The version is determined
            # by the local source, not PyPI, so it may not increase.
            results.append(UpgradedPackage(
                name=pkg.name,
                version=new_version,
                upgraded=True,
                previous_version=pkg.version,
                is_editable=True,
                editable_location=pkg.editable_location,
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
