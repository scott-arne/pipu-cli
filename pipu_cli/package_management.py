"""Package management functions for pipu-cli."""

import logging
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, IO, List, Optional, Protocol, Callable, runtime_checkable

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


@dataclass(frozen=True)
class BlockedPackageInfo(Package):
    """Information about a package that cannot be upgraded."""
    latest_version: Version
    blocked_by: List[str]  # List of "package_name (constraint)" strings
    is_editable: bool = False
    editable_location: Optional[str] = None


def inspect_installed_packages(timeout: int = 10) -> List[InstalledPackage]:
    """
    Inspect currently installed Python packages and return detailed information.

    This function uses pip's internal APIs to gather information about all installed
    packages in the current environment, including their versions, editable status,
    and constrained dependencies.

    :param timeout: Timeout in seconds for subprocess calls (default: 10)
    :returns: List of PackageInfo objects containing package details
    :raises RuntimeError: If unable to inspect installed packages
    """
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


def _get_editable_packages(timeout: int) -> Dict[str, str]:
    """
    Get packages installed in editable mode using pip list --editable.

    :param timeout: Timeout in seconds for subprocess call
    :returns: Dictionary mapping canonical package names to their source locations
    """
    editable_packages = {}

    try:
        # Use pip list --editable to get editable packages
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'list', '--editable'],
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
    constrained_dependencies = {}

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

    # Load pip configuration to get index URLs and trusted hosts
    try:
        config = Configuration(isolated=False, load_only=None)
        config.load()
    except Exception as e:
        logger.warning(f"Could not load pip configuration: {e}")
        config = None

    # Get index URL (primary package index)
    index_url = None
    if config:
        try:
            index_url = config.get_value("global.index-url")
        except Exception:
            pass
    index_url = index_url or "https://pypi.org/simple/"

    # Get extra index URLs (additional package indexes)
    extra_index_urls = []
    if config:
        try:
            raw_extra_urls = config.get_value("global.extra-index-url")
            if raw_extra_urls:
                # Handle both string and list formats
                if isinstance(raw_extra_urls, str):
                    # Split by newlines and filter out comments/empty lines
                    extra_index_urls = [
                        url.strip()
                        for url in raw_extra_urls.split('\n')
                        if url.strip() and not url.strip().startswith('#')
                    ]
                elif isinstance(raw_extra_urls, list):
                    extra_index_urls = raw_extra_urls
        except Exception:
            pass

    # Combine all index URLs
    all_index_urls = [index_url] + extra_index_urls

    # Get trusted hosts (hosts that don't require HTTPS verification)
    trusted_hosts = []
    if config:
        try:
            raw_trusted_hosts = config.get_value("global.trusted-host")
            if raw_trusted_hosts:
                # Handle both string and list formats
                if isinstance(raw_trusted_hosts, str):
                    # Split by newlines and filter out comments/empty lines
                    trusted_hosts = [
                        host.strip()
                        for host in raw_trusted_hosts.split('\n')
                        if host.strip() and not host.strip().startswith('#')
                    ]
                elif isinstance(raw_trusted_hosts, list):
                    trusted_hosts = raw_trusted_hosts
        except Exception:
            pass

    # Create pip session for network requests
    try:
        session = PipSession()
        session.timeout = timeout

        # Add trusted hosts to session
        for host in trusted_hosts:
            host = host.strip()
            if host:
                session.add_trusted_host(host, source="pip configuration")
    except Exception as e:
        raise ConnectionError(f"Failed to create network session: {e}") from e

    # Set up package finder with configured indexes
    selection_prefs = SelectionPreferences(
        allow_yanked=False,
        allow_all_prereleases=include_prereleases
    )

    search_scope = SearchScope.create(
        find_links=[],
        index_urls=all_index_urls,
        no_index=False
    )

    link_collector = LinkCollector(
        session=session,
        search_scope=search_scope
    )

    package_finder = PackageFinder.create(
        link_collector=link_collector,
        selection_prefs=selection_prefs
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
    # Load pip configuration to get index URLs and trusted hosts
    try:
        config = Configuration(isolated=False, load_only=None)
        config.load()
    except Exception as e:
        logger.warning(f"Could not load pip configuration: {e}")
        config = None

    # Get index URL (primary package index)
    index_url = None
    if config:
        try:
            index_url = config.get_value("global.index-url")
        except Exception:
            pass
    index_url = index_url or "https://pypi.org/simple/"

    # Get extra index URLs (additional package indexes)
    extra_index_urls = []
    if config:
        try:
            raw_extra_urls = config.get_value("global.extra-index-url")
            if raw_extra_urls:
                # Handle both string and list formats
                if isinstance(raw_extra_urls, str):
                    # Split by newlines and filter out comments/empty lines
                    extra_index_urls = [
                        url.strip()
                        for url in raw_extra_urls.split('\n')
                        if url.strip() and not url.strip().startswith('#')
                    ]
                elif isinstance(raw_extra_urls, list):
                    extra_index_urls = raw_extra_urls
        except Exception:
            pass

    # Combine all index URLs
    all_index_urls = [index_url] + extra_index_urls

    # Get trusted hosts (hosts that don't require HTTPS verification)
    trusted_hosts = []
    if config:
        try:
            raw_trusted_hosts = config.get_value("global.trusted-host")
            if raw_trusted_hosts:
                # Handle both string and list formats
                if isinstance(raw_trusted_hosts, str):
                    # Split by newlines and filter out comments/empty lines
                    trusted_hosts = [
                        host.strip()
                        for host in raw_trusted_hosts.split('\n')
                        if host.strip() and not host.strip().startswith('#')
                    ]
                elif isinstance(raw_trusted_hosts, list):
                    trusted_hosts = raw_trusted_hosts
        except Exception:
            pass

    # Create pip session for network requests
    try:
        session = PipSession()
        session.timeout = timeout

        # Add trusted hosts to session
        for host in trusted_hosts:
            host = host.strip()
            if host:
                session.add_trusted_host(host, source="pip configuration")
    except Exception as e:
        raise ConnectionError(f"Failed to create network session: {e}") from e

    # Set up package finder with configured indexes
    selection_prefs = SelectionPreferences(
        allow_yanked=False,
        allow_all_prereleases=include_prereleases
    )

    search_scope = SearchScope.create(
        find_links=[],
        index_urls=all_index_urls,
        no_index=False
    )

    link_collector = LinkCollector(
        session=session,
        search_scope=search_scope
    )

    package_finder = PackageFinder.create(
        link_collector=link_collector,
        selection_prefs=selection_prefs
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
    """
    Resolve which packages can be safely upgraded considering dependency constraints.

    This function uses a fixed-point iteration algorithm to handle circular dependencies.
    It repeatedly refines the set of upgradable packages until it stabilizes (reaches a
    fixed point where no more packages need to be removed).

    A package can be upgraded if:
    1. Its new version doesn't violate constraints from packages NOT being upgraded, OR
    2. ALL packages whose constraints would be violated ARE being upgraded

    The algorithm:
    1. Start with all packages that have newer versions available
    2. Check constraints for each package against current upgrading set
    3. Remove packages that violate constraints
    4. Repeat steps 2-3 until no changes occur (fixed point)

    Examples:
    - If Package A constrains "B<2.0" and B upgrades to 1.9: B is upgradable (constraint satisfied)
    - If Package A constrains "B<2.0" and B upgrades to 2.5, and A is NOT upgrading: B is NOT upgradable
    - If Package A constrains "B<2.0" and B upgrades to 2.5, and A IS upgrading: B is upgradable
    - If A requires B==1.0 and B wants to upgrade, C depends on B:
      * B is NOT upgradable (violates A's exact constraint)
      * C cannot use "B is upgrading" to justify its upgrade
      * Fixed-point iteration removes B from upgrading set in first pass
      * Second pass sees B not upgrading, removes C if it violates B's constraints

    Performance:
    - Time complexity: O(n * m * k) where:
      * n = number of packages with updates available
      * m = number of iterations (typically 1-3, max n)
      * k = average constraints per package (typically small)
    - Space complexity: O(n) for upgrading_packages set and constraints_on map
    - Convergence: Guaranteed (monotonically shrinking set, terminates when empty or stable)
    - Practical performance: Fast for typical package sets (tested with 182 packages)

    :param upgrade_candidates: Dict mapping installed packages to their latest available versions
    :param all_installed: List of all installed packages (for constraint checking)
    :returns: List of UpgradePackageInfo objects indicating which packages can be upgraded
    """
    # Build a reverse dependency map: package_name -> [(constraining_package, specifier)]
    # This tells us which packages have constraints on a given package
    constraints_on: Dict[str, List[tuple[InstalledPackage, str]]] = {}

    for pkg in all_installed:
        for dep_name, specifier_str in pkg.constrained_dependencies.items():
            if dep_name not in constraints_on:
                constraints_on[dep_name] = []
            constraints_on[dep_name].append((pkg, specifier_str))

    # Filter to only actual upgrades (latest > installed)
    actual_upgrades = {
        pkg: latest_pkg
        for pkg, latest_pkg in upgrade_candidates.items()
        if latest_pkg.version > pkg.version
    }

    # Fixed-point iteration: start with all actual upgrades, iteratively remove violators
    upgrading_packages = {canonicalize_name(pkg.name) for pkg in actual_upgrades.keys()}

    max_iterations = len(upgrading_packages) + 1  # Safety limit
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        packages_to_remove = set()

        # Check each potential upgrade against current upgrading set
        for installed_pkg, latest_pkg in actual_upgrades.items():
            canonical_name = canonicalize_name(installed_pkg.name)

            # Skip if already removed in a previous iteration
            if canonical_name not in upgrading_packages:
                continue

            latest_version = latest_pkg.version

            # Check all constraints on this package
            if canonical_name in constraints_on:
                for constraining_pkg, specifier_str in constraints_on[canonical_name]:
                    try:
                        specifier = SpecifierSet(specifier_str)
                        satisfies = latest_version in specifier

                        if not satisfies:
                            # Constraint violated - check if constraining package is being upgraded
                            constraining_canonical = canonicalize_name(constraining_pkg.name)
                            if constraining_canonical not in upgrading_packages:
                                # Constraint violated by non-upgrading package - cannot upgrade
                                packages_to_remove.add(canonical_name)
                                logger.debug(
                                    f"Iteration {iteration}: Cannot upgrade {installed_pkg.name} to {latest_version}: "
                                    f"violates constraint {specifier_str} from {constraining_pkg.name} "
                                    f"which is not being upgraded"
                                )
                                break
                            else:
                                # Constraint violated but constraining package is being upgraded
                                logger.debug(
                                    f"Iteration {iteration}: Can upgrade {installed_pkg.name} to {latest_version}: "
                                    f"violates constraint {specifier_str} from {constraining_pkg.name} "
                                    f"but {constraining_pkg.name} is also being upgraded"
                                )

                    except (InvalidSpecifier, Exception) as e:
                        logger.warning(
                            f"Invalid specifier '{specifier_str}' for {canonical_name} "
                            f"from {constraining_pkg.name}: {e}"
                        )
                        # If we can't parse the specifier, be conservative and block the upgrade
                        # unless the constraining package is being upgraded
                        constraining_canonical = canonicalize_name(constraining_pkg.name)
                        if constraining_canonical not in upgrading_packages:
                            packages_to_remove.add(canonical_name)
                            break

        # Remove packages that violate constraints
        if not packages_to_remove:
            # Fixed point reached - no more packages to remove
            logger.debug(f"Fixed point reached after {iteration} iteration(s)")
            break

        logger.debug(f"Iteration {iteration}: Removing {len(packages_to_remove)} package(s): {packages_to_remove}")
        upgrading_packages -= packages_to_remove

    if iteration >= max_iterations:
        logger.warning(f"Fixed-point iteration did not converge after {max_iterations} iterations")

    # Build result list with upgradability determined by final upgrading set
    result = []
    for installed_pkg, latest_pkg in upgrade_candidates.items():
        canonical_name = canonicalize_name(installed_pkg.name)
        latest_version = latest_pkg.version

        # Check if this is actually an upgrade and made it through fixed-point iteration
        can_upgrade = (
            latest_version > installed_pkg.version and
            canonical_name in upgrading_packages
        )

        result.append(UpgradePackageInfo(
            name=installed_pkg.name,
            version=installed_pkg.version,
            upgradable=can_upgrade,
            latest_version=latest_version,
            is_editable=installed_pkg.is_editable,
            editable_location=installed_pkg.editable_location
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
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
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
                                # Track blocking reason
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


def _stream_reader(
    pipe: IO[str],
    stream: Optional[OutputStream],
    lock: threading.Lock
) -> None:
    """
    Read lines from a pipe and write to a stream with thread-safe locking.

    This helper function is used to read output from subprocess pipes (stdout/stderr)
    and write it to an output stream in real-time. The lock ensures thread-safe
    access when multiple threads write to the same stream.

    :param pipe: Input pipe to read from (stdout or stderr from subprocess)
    :param stream: Output stream to write to (or None to discard)
    :param lock: Threading lock for synchronized writes
    """
    try:
        for line in iter(pipe.readline, ''):
            if line and stream:
                with lock:
                    stream.write(line)
                    stream.flush()
    except Exception as e:
        logger.warning(f"Error reading from pipe: {e}")
    finally:
        pipe.close()


def install_packages(
    packages_to_upgrade: List[UpgradePackageInfo],
    output_stream: Optional[OutputStream] = None,
    timeout: int = 300,
    version_constraints: Optional[Dict[str, str]] = None
) -> List[UpgradedPackage]:
    """
    Install/upgrade packages using pip.

    This function upgrades all packages in a single pip command to allow pip's
    dependency resolver to handle mutual constraints properly. After installation,
    it checks which packages were successfully upgraded by comparing installed
    versions with previous versions.

    :param packages_to_upgrade: List of UpgradePackageInfo objects to upgrade
    :param output_stream: Optional stream implementing write() and flush() for live progress updates
    :param timeout: Timeout in seconds for the installation (default: 300)
    :param version_constraints: Optional dict mapping package names (lowercase) to version specifiers (e.g., "==2.31.0")
    :returns: List of UpgradedPackage objects with upgrade status
    :raises RuntimeError: If pip command cannot be executed
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

    cmd = [
        sys.executable, '-m', 'pip', 'install',
        '--upgrade'
    ] + package_specs

    process = None
    try:
        # Write initial message to output stream
        if output_stream:
            output_stream.write(f"Upgrading {len(package_specs)} package(s)...\n")
            output_stream.flush()

        # Run pip install with real-time output streaming
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1  # Line-buffered
        )

        # Create threads for concurrent reading of stdout and stderr
        lock = threading.Lock()
        stdout_thread = threading.Thread(
            target=_stream_reader,
            args=(process.stdout, output_stream, lock),
            daemon=True
        )
        stderr_thread = threading.Thread(
            target=_stream_reader,
            args=(process.stderr, output_stream, lock),
            daemon=True
        )

        # Start threads
        stdout_thread.start()
        stderr_thread.start()

        # Wait for threads to complete
        stdout_thread.join()
        stderr_thread.join()

        # Wait for process to finish
        returncode = process.wait(timeout=timeout)

        # Check overall installation status
        if returncode != 0:
            # Entire installation failed
            logger.warning(f"Package upgrade failed with return code {returncode}")
            # Mark all packages as not upgraded
            return [
                UpgradedPackage(
                    name=pkg.name,
                    version=pkg.version,
                    upgraded=False,
                    previous_version=pkg.version,
                    is_editable=pkg.is_editable,
                    editable_location=pkg.editable_location
                )
                for pkg in packages_to_upgrade
            ]

        # Installation succeeded - now determine which packages were actually upgraded
        # Query current installed versions
        env = get_default_environment()
        current_versions = {}

        for dist in env.iter_all_distributions():
            try:
                package_name = dist.metadata["name"]
                canonical_name = canonicalize_name(package_name)

                # Only track packages we attempted to upgrade
                if canonical_name in package_map:
                    try:
                        current_version = Version(str(dist.version))
                        current_versions[canonical_name] = current_version
                    except InvalidVersion:
                        logger.warning(f"Invalid version for {package_name}: {dist.version}")
                        continue
            except Exception as e:
                logger.warning(f"Error processing package {dist.metadata.get('name', 'unknown')}: {e}")
                continue

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
                    editable_location=pkg_info.editable_location
                )
                results.append(upgraded_pkg)
                logger.info(f"Package {pkg_info.name} was not upgraded (still at {actual_version})")

        return results

    except subprocess.TimeoutExpired:
        # Timeout occurred - kill the process and ensure cleanup
        if process is not None:
            try:
                process.kill()
                process.wait()  # Ensure process is cleaned up
            except Exception as e:
                logger.warning(f"Error cleaning up timed-out process: {e}")

        if output_stream:
            output_stream.write("ERROR: Timeout during package upgrade\n")
            output_stream.flush()

        logger.error("Timeout during package upgrade")

        # Mark all packages as not upgraded
        return [
            UpgradedPackage(
                name=pkg.name,
                version=pkg.version,
                upgraded=False,
                previous_version=pkg.version,
                is_editable=pkg.is_editable,
                editable_location=pkg.editable_location
            )
            for pkg in packages_to_upgrade
        ]

    except Exception as e:
        # Other errors
        if output_stream:
            output_stream.write(f"ERROR: Failed to upgrade packages: {e}\n")
            output_stream.flush()

        logger.error(f"Error upgrading packages: {e}")

        # Mark all packages as not upgraded
        return [
            UpgradedPackage(
                name=pkg.name,
                version=pkg.version,
                upgraded=False,
                previous_version=pkg.version,
                is_editable=pkg.is_editable,
                editable_location=pkg.editable_location
            )
            for pkg in packages_to_upgrade
        ]


def reinstall_editable_packages(
    editable_packages: List[UpgradePackageInfo],
    output_stream: Optional[OutputStream] = None,
    timeout: int = 300,
) -> List[UpgradedPackage]:
    """
    Reinstall editable packages to update their version metadata.

    Uses `pip install --config-settings editable_mode=compat -e <path>` to reinstall
    each editable package. This updates the package version in the environment
    while maintaining the editable install.

    :param editable_packages: List of UpgradePackageInfo objects for editable packages
    :param output_stream: Optional stream implementing write() and flush() for live progress updates
    :param timeout: Timeout in seconds for each installation (default: 300)
    :returns: List of UpgradedPackage objects with upgrade status
    """
    if not editable_packages:
        return []

    results = []

    for pkg in editable_packages:
        if not pkg.editable_location:
            logger.warning(f"Editable package {pkg.name} has no location, skipping")
            results.append(UpgradedPackage(
                name=pkg.name,
                version=pkg.version,
                upgraded=False,
                previous_version=pkg.version,
                is_editable=True,
                editable_location=pkg.editable_location
            ))
            continue

        if output_stream:
            output_stream.write(f"Reinstalling editable package: {pkg.name} from {pkg.editable_location}\n")
            output_stream.flush()

        cmd = [
            sys.executable, '-m', 'pip', 'install',
            '--config-settings', 'editable_mode=compat',
            '-e', pkg.editable_location
        ]

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )

            # Read output
            stdout, stderr = process.communicate(timeout=timeout)
            returncode = process.returncode

            if output_stream and stdout:
                output_stream.write(stdout)
            if output_stream and stderr:
                output_stream.write(stderr)
            if output_stream:
                output_stream.flush()

            if returncode == 0:
                # Get the new version after reinstall
                env = get_default_environment()
                canonical_name = canonicalize_name(pkg.name)
                new_version = pkg.version  # Default to old version

                for dist in env.iter_all_distributions():
                    dist_name = dist.metadata.get("name", "")
                    if canonicalize_name(dist_name) == canonical_name:
                        try:
                            new_version = Version(str(dist.version))
                        except InvalidVersion:
                            pass
                        break

                results.append(UpgradedPackage(
                    name=pkg.name,
                    version=new_version,
                    upgraded=new_version > pkg.version,
                    previous_version=pkg.version,
                    is_editable=True,
                    editable_location=pkg.editable_location
                ))
                logger.info(f"Reinstalled editable package {pkg.name}: {pkg.version} -> {new_version}")
            else:
                results.append(UpgradedPackage(
                    name=pkg.name,
                    version=pkg.version,
                    upgraded=False,
                    previous_version=pkg.version,
                    is_editable=True,
                    editable_location=pkg.editable_location
                ))
                logger.warning(f"Failed to reinstall editable package {pkg.name}")

        except subprocess.TimeoutExpired:
            if output_stream:
                output_stream.write(f"ERROR: Timeout reinstalling {pkg.name}\n")
                output_stream.flush()
            results.append(UpgradedPackage(
                name=pkg.name,
                version=pkg.version,
                upgraded=False,
                previous_version=pkg.version,
                is_editable=True,
                editable_location=pkg.editable_location
            ))
            logger.error(f"Timeout reinstalling editable package {pkg.name}")

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
                editable_location=pkg.editable_location
            ))
            logger.error(f"Error reinstalling editable package {pkg.name}: {e}")

    return results
