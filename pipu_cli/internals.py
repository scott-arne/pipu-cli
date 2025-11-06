import logging
import time
import threading
from pip._internal.metadata import get_default_environment
from pip._internal.index.package_finder import PackageFinder
from pip._internal.index.collector import LinkCollector
from pip._internal.models.search_scope import SearchScope
from pip._internal.network.session import PipSession
from pip._internal.models.selection_prefs import SelectionPreferences
from pip._internal.configuration import Configuration
from rich.console import Console
from rich.table import Table
from typing import List, Dict, Any, Optional, Union, Set, Callable, Tuple
from packaging.version import Version, InvalidVersion
from packaging.specifiers import SpecifierSet, InvalidSpecifier
from queue import Queue, Empty
from .config import EDITABLE_PACKAGES_CACHE_TTL

# Import configuration
from .config import (
    DEFAULT_NETWORK_TIMEOUT,
    DEFAULT_NETWORK_RETRIES,
    MAX_CONSECUTIVE_NETWORK_ERRORS,
    RETRY_DELAY,
    SUBPROCESS_TIMEOUT
)
from .thread_safe import ThreadSafeCache

# Set up module logger
logger = logging.getLogger(__name__)


def _call_with_timeout(func: Callable, timeout: float, *args, **kwargs):
    """
    Execute a function with a hard timeout by running it in a daemon thread.

    :param func: Function to execute
    :param timeout: Timeout in seconds
    :param args: Positional arguments for the function
    :param kwargs: Keyword arguments for the function
    :returns: Result from the function
    :raises TimeoutError: If the function doesn't complete within timeout
    :raises RuntimeError: If the function completes but produces no result
    """
    result_queue = Queue()
    exception_queue = Queue()
    completed = threading.Event()

    def wrapper():
        try:
            result = func(*args, **kwargs)
            result_queue.put(result)
        except Exception as e:
            exception_queue.put(e)
        finally:
            completed.set()

    thread = threading.Thread(target=wrapper, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    # Check if the function completed
    if not completed.is_set():
        # Thread is still running - timeout occurred
        raise TimeoutError(f"Operation timed out after {timeout} seconds")

    # Check if an exception was raised (check this first)
    try:
        exception = exception_queue.get_nowait()
        raise exception
    except Empty:
        pass

    # Get the result
    try:
        return result_queue.get_nowait()
    except Empty:
        raise RuntimeError("Function completed but produced no result")


def _check_constraint_satisfaction(version: str, constraint: str) -> bool:
    """
    Check if a version satisfies the given constraint.

    :param version: Version string to check
    :param constraint: Constraint specification (e.g., ">=1.0.0,<2.0.0")
    :returns: True if version satisfies constraint, False otherwise
    """
    try:
        pkg_version = Version(version)
        specifier_set = SpecifierSet(constraint)
        return pkg_version in specifier_set
    except (InvalidVersion, InvalidSpecifier):
        # If we can't parse the version or constraint, assume it doesn't satisfy
        return False


def get_constraint_color(version: str, constraint: Optional[str]) -> str:
    """
    Get the appropriate color for displaying a version based on constraint satisfaction.

    :param version: Version string to check
    :param constraint: Optional constraint specification
    :returns: Color name ("green" if satisfied/no constraint, "red" if violated)
    """
    if not constraint:
        return "green"

    return "green" if _check_constraint_satisfaction(version, constraint) else "red"


def format_invalid_when_display(invalid_when: Optional[str]) -> str:
    """
    Format 'Invalid When' trigger list for display with appropriate color coding.

    :param invalid_when: Comma-separated list of trigger packages or None
    :returns: Formatted string with yellow color markup or dim dash
    """
    if invalid_when:
        return f"[yellow]{invalid_when}[/yellow]"
    else:
        return "[dim]-[/dim]"


def _format_constraint_for_display(constraint: Optional[str], latest_version: str) -> str:
    """
    Format constraint for display with appropriate color coding.

    :param constraint: Constraint specification or None
    :param latest_version: The latest available version
    :returns: Formatted constraint string with color markup
    """
    if not constraint:
        return "[dim]-[/dim]"

    # Check if the latest version satisfies the constraint
    try:
        satisfies = _check_constraint_satisfaction(latest_version, constraint)
        if satisfies:
            return f"[green]{constraint}[/green]"
        else:
            return f"[red]{constraint}[/red]"
    except Exception as e:
        logger.debug(f"Error checking constraint satisfaction for {constraint}: {e}")
        return f"[yellow]{constraint}[/yellow]"


def list_outdated(
    console: Optional[Console] = None,
    print_table: bool = True,
    constraints: Optional[Dict[str, str]] = None,
    ignores: Optional[Union[List[str], Set[str]]] = None,
    pre: bool = False,
    progress_callback: Optional[Callable[[str], None]] = None,
    result_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    timeout: int = DEFAULT_NETWORK_TIMEOUT,
    retries: int = DEFAULT_NETWORK_RETRIES,
    cancel_event: Optional[Any] = None,
    invalidation_triggers: Optional[Dict[str, List[str]]] = None
) -> List[Dict[str, Any]]:
    """
    Check for outdated packages and optionally print results.

    Queries the configured package indexes to find packages that have newer versions
    available than what is currently installed. Respects pip configuration for
    index URLs and trusted hosts. Filters out packages that would violate constraints
    if updated to the latest version and excludes ignored packages entirely.

    :param console: Rich console object for output. If None, creates a new one.
    :param print_table: Whether to print the results table. Defaults to True.
    :param constraints: Dictionary mapping package names to version constraints.
                       If None, no constraint filtering is applied.
    :param ignores: List of package names to ignore completely. If None, no packages are ignored.
    :param pre: Include pre-release versions. Defaults to False.
    :param progress_callback: Optional callback function to receive progress updates. Called with package name being checked.
    :param result_callback: Optional callback function to receive individual package results as they're processed.
    :param timeout: Network timeout in seconds for checking each package. Defaults to 10 seconds.
    :param retries: Number of retries before raising an error on network failure. Defaults to 0.
    :param cancel_event: Optional threading.Event to signal cancellation. If set, the function will exit early.
    :param invalidation_triggers: Optional dictionary mapping package names to lists of trigger package names.
    :returns: List of dictionaries containing outdated package information.
              Each dict has keys: name, version, latest_version, latest_filetype, constraint, invalid_when, editable
    :raises ConnectionError: If network errors occur after all retries are exhausted
    :raises Exception: May raise exceptions from pip internals during package discovery
    """
    if console is None:
        console = Console(width=120)

    # Get installed packages using importlib.metadata
    env = get_default_environment()
    installed_dists = env.iter_all_distributions()

    # Read pip configuration to get index URLs
    try:
        config = Configuration(isolated=False, load_only=None)
        config.load()
    except Exception:
        # If we can't load configuration (permissions, malformed config, etc.), use defaults
        config = None

    # Get index URLs from configuration safely
    try:
        index_url = config.get_value("global.index-url") if config else None
    except Exception:
        index_url = None
    index_url = index_url or "https://pypi.org/simple/"

    try:
        extra_index_urls = config.get_value("global.extra-index-url") if config else []
    except Exception:
        extra_index_urls = []
    extra_index_urls = extra_index_urls or []

    # Parse extra_index_urls - pip config returns multi-line values as a single string
    if isinstance(extra_index_urls, str):
        # Split by newlines and clean up each URL (remove comments and whitespace)
        extra_index_urls = [
            url.strip()
            for url in extra_index_urls.split('\n')
            if url.strip() and not url.strip().startswith('#')
        ]
    elif not isinstance(extra_index_urls, list):
        extra_index_urls = []

    # Combine all index URLs
    all_index_urls = [index_url] + extra_index_urls

    # Get trusted hosts from configuration safely
    try:
        trusted_hosts = config.get_value("global.trusted-host") if config else []
    except Exception:
        trusted_hosts = []
    trusted_hosts = trusted_hosts or []

    # Parse trusted_hosts - pip config returns multi-line values as a single string
    if isinstance(trusted_hosts, str):
        # Split by newlines and clean up each host (remove comments and whitespace)
        trusted_hosts = [
            host.strip()
            for host in trusted_hosts.split('\n')
            if host.strip() and not host.strip().startswith('#')
        ]
    elif not isinstance(trusted_hosts, list):
        trusted_hosts = []

    # Set up pip session and package finder to check for updates
    try:
        session = PipSession()
        # Set timeout on the session
        session.timeout = timeout

        # Add trusted hosts to the session
        for host in trusted_hosts:
            # Strip whitespace and skip empty strings
            host = host.strip()
            if host:
                session.add_trusted_host(host, source="pip configuration")
    except Exception as e:
        # If we can't create a session (network issues, permissions, etc.), raise error
        raise ConnectionError(f"Failed to create network session: {e}") from e

    selection_prefs = SelectionPreferences(allow_yanked=False)

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

    # Use provided constraints or default to empty dict
    if constraints is None:
        constraints = {}

    # Use provided ignores or default to empty set
    if ignores is None:
        ignores = set()
    # Normalize ignores to lowercase for case-insensitive matching and ensure O(1) lookups
    ignores_lower = {pkg.lower() for pkg in ignores}

    # Use provided invalidation triggers or default to empty dict
    if invalidation_triggers is None:
        invalidation_triggers = {}

    # Detect editable packages for preservation during updates
    editable_packages = get_editable_packages()

    outdated_packages = []

    # Track consecutive network errors to fail fast
    consecutive_network_errors = 0

    # Show spinner while checking for updates
    with console.status("[bold green]Checking for package updates...") as status:
        for dist in installed_dists:
            # Check for cancellation at the start of each iteration
            if cancel_event and cancel_event.is_set():
                logger.info("Package check cancelled by user")
                break

            try:
                package_name = dist.metadata["name"]
                package_name_lower = package_name.lower()

                # Normalize package name for constraint lookups
                from packaging.utils import canonicalize_name
                package_name_canonical = canonicalize_name(package_name)

                # Skip ignored packages completely
                if package_name_lower in ignores_lower:
                    continue

                # Update status with current package being checked
                status.update(f"[bold green]Checking {package_name}...")

                # Call progress callback if provided (stop status temporarily to avoid conflicts)
                if progress_callback:
                    status.stop()
                    progress_callback(package_name)
                    status.start()

                # Find the best candidate (latest version) with retry logic
                candidates = None
                for attempt in range(retries + 1):
                    try:
                        # Use hard timeout wrapper to ensure we don't hang
                        logger.debug(f"About to check {package_name} with {timeout}s timeout (attempt {attempt + 1}/{retries + 1})")
                        candidates = _call_with_timeout(
                            package_finder.find_all_candidates,
                            timeout,
                            dist.canonical_name
                        )
                        logger.debug(f"Successfully retrieved candidates for {package_name}")
                        # Success - reset consecutive error counter
                        consecutive_network_errors = 0
                        break
                    except (TimeoutError, Exception) as e:
                        logger.debug(f"Error checking {package_name}: {type(e).__name__}: {e}")
                        # Check if it's a network-related error (TimeoutError always is)
                        error_str = str(e).lower()
                        is_network_error = isinstance(e, TimeoutError) or any(keyword in error_str for keyword in [
                            'connection', 'timeout', 'network', 'unreachable',
                            'proxy', 'ssl', 'certificate', 'dns', 'resolve'
                        ])

                        if is_network_error:
                            consecutive_network_errors += 1
                            if consecutive_network_errors >= MAX_CONSECUTIVE_NETWORK_ERRORS:
                                # Too many consecutive failures - raise error
                                raise ConnectionError(
                                    f"Network connectivity issues detected after checking {package_name}. "
                                    f"Failed to reach package index. Please check your network connection "
                                    f"and proxy settings (HTTP_PROXY, HTTPS_PROXY)."
                                ) from e

                            # Not the last retry - wait briefly before retrying
                            if attempt < retries:
                                time.sleep(RETRY_DELAY)
                                continue

                        # Non-network error or last retry - skip this package
                        if attempt >= retries:
                            logger.debug(f"Failed to check {package_name} after {retries + 1} attempts: {e}")
                            break

                if candidates is None:
                    # All retries exhausted, skip this package
                    continue

                if candidates:
                    # Filter candidates based on pre-release preference
                    if not pre:
                        # Exclude pre-release versions (alpha, beta, dev, rc)
                        stable_candidates = []
                        for candidate in candidates:
                            try:
                                version_obj = Version(str(candidate.version))
                                if not version_obj.is_prerelease:
                                    stable_candidates.append(candidate)
                            except InvalidVersion:
                                # If version parsing fails, skip this candidate
                                continue
                        # Use stable candidates if available, otherwise fall back to all candidates
                        candidates_to_check = stable_candidates if stable_candidates else candidates
                    else:
                        candidates_to_check = candidates

                    # Get the latest version from filtered candidates
                    if candidates_to_check:
                        latest_candidate = max(candidates_to_check, key=lambda c: c.version)
                        latest_version = str(latest_candidate.version)
                        current_version = str(dist.version)

                        # Determine the actual file type of the latest candidate
                        file_type = "unknown"
                        if hasattr(latest_candidate, 'link') and latest_candidate.link:
                            filename = latest_candidate.link.filename
                            if filename.endswith('.whl'):
                                file_type = "wheel"
                            elif filename.endswith(('.tar.gz', '.zip')):
                                file_type = "sdist"
                            elif filename.endswith('.egg'):
                                file_type = "egg"
                            else:
                                # Extract file extension for other types
                                if '.' in filename:
                                    file_type = filename.split('.')[-1]
                                else:
                                    file_type = "unknown"
                        else:
                            # Fallback to wheel if no link information available
                            file_type = "wheel"

                        if latest_version != current_version:
                            # Check if there's a constraint for this package
                            constraint = constraints.get(package_name_canonical)

                            # Include all outdated packages - constraints will be shown in the table
                            # with appropriate color coding (red=violating, green=satisfying)
                            # Get invalidation triggers for this package
                            package_triggers = invalidation_triggers.get(package_name_canonical, [])
                            invalid_when_display = ", ".join(package_triggers) if package_triggers else None

                            package_result = {
                                "name": package_name,
                                "version": current_version,
                                "latest_version": latest_version,
                                "latest_filetype": file_type,
                                "constraint": constraint,
                                "invalid_when": invalid_when_display,
                                "editable": package_name_canonical in editable_packages
                            }
                            outdated_packages.append(package_result)

                            # Call result callback with individual package result
                            if result_callback:
                                status.stop()
                                result_callback(package_result)
                                status.start()
                        else:
                            # Package is up to date - call callback with current info
                            if result_callback:
                                constraint = constraints.get(package_name_canonical)
                                up_to_date_result = {
                                    "name": package_name,
                                    "version": current_version,
                                    "latest_version": current_version,
                                    "latest_filetype": file_type,
                                    "constraint": constraint,
                                    "editable": package_name_canonical in editable_packages
                                }
                                status.stop()
                                result_callback(up_to_date_result)
                                status.start()

            except ConnectionError:
                # Re-raise ConnectionError so it propagates to the caller
                raise
            except Exception:
                # Skip packages that can't be checked
                continue

    # Sort packages alphabetically by name
    outdated_packages.sort(key=lambda x: x["name"].lower())

    # Print results if requested
    if print_table:
        if not outdated_packages:
            console.print("[green]All packages are up to date![/green]")
        else:
            # Create a rich table matching TUI styling
            table = Table(title="Outdated Packages")
            table.add_column("", width=3)  # Selection indicator column
            table.add_column("Package", style="cyan", no_wrap=True)
            table.add_column("Version", style="magenta")
            table.add_column("Latest", no_wrap=True)  # Color conditionally per row
            table.add_column("Type", style="yellow")
            table.add_column("Constraint", no_wrap=True)
            table.add_column("Constraint Invalid When", no_wrap=True)

            for package in outdated_packages:
                constraint = package.get("constraint")
                latest_version = package["latest_version"]

                # Determine if package will be updated (same logic as TUI)
                if constraint:
                    will_update = _check_constraint_satisfaction(latest_version, constraint)
                else:
                    will_update = True

                # Show indicator: ✓ for packages that will be updated
                if will_update:
                    indicator = "[bold green]✓[/bold green]"
                else:
                    indicator = "[dim]✗[/dim]"

                # Format latest version with conditional coloring (matching TUI)
                color = get_constraint_color(latest_version, constraint)
                latest_display = f"[{color}]{latest_version}[/{color}]"

                # Format constraint display
                constraint_display = _format_constraint_for_display(constraint, latest_version)

                # Format invalid when display (matching TUI)
                invalid_when = package.get("invalid_when")
                invalid_when_display = format_invalid_when_display(invalid_when)

                table.add_row(
                    indicator,
                    package["name"],
                    package["version"],
                    latest_display,
                    package["latest_filetype"],
                    constraint_display,
                    invalid_when_display
                )

            console.print(table)

            # Print legend explaining the indicators
            console.print("\n[dim]Legend:[/dim]")
            console.print("  [bold green]✓[/bold green] = Will be updated  |  [dim]✗[/dim] = Blocked by constraint")

    return outdated_packages


# Thread-safe cache for editable packages to avoid repeated subprocess calls


# Initialize thread-safe cache
_editable_packages_cache = ThreadSafeCache[Dict[str, str]](ttl=EDITABLE_PACKAGES_CACHE_TTL)


def _fetch_editable_packages() -> Dict[str, str]:
    """
    Internal function to fetch editable packages from pip.

    This is the factory function used by the cache.

    :returns: Dictionary mapping package names (canonical) to their project locations
    :raises RuntimeError: If unable to query pip for editable packages
    """
    import subprocess
    import sys
    from packaging.utils import canonicalize_name

    editable_packages = {}

    try:
        # Use pip list --editable to get definitive list of editable packages
        result = subprocess.run([
            sys.executable, '-m', 'pip', 'list', '--editable'
        ], capture_output=True, text=True, check=True, timeout=SUBPROCESS_TIMEOUT)

        # Parse the output to get package names and locations
        lines = result.stdout.strip().split('\n')

        # Find the header line and skip it
        header_found = False
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Skip header lines (look for "Package" header or separator lines)
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
                # The location is everything from the 3rd part onwards (in case paths have spaces)
                location = ' '.join(parts[2:])

                # Canonicalize package name for consistent lookups
                canonical_name = canonicalize_name(pkg_name)
                editable_packages[canonical_name] = location

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Could not detect editable packages: {e}")
        # Return empty dict on error - caller can handle gracefully
        return {}
    except (OSError, ValueError) as e:
        logger.error(f"Unexpected error detecting editable packages: {e}")
        return {}

    return editable_packages


def get_editable_packages() -> Dict[str, str]:
    """
    Detect packages installed in editable mode.

    Uses pip list --editable to get a definitive list of packages installed
    in development/editable mode. These packages maintain a live link to their
    source code directory and should be reinstalled with -e flag to preserve
    this behavior.

    Results are cached for 60 seconds to avoid repeated subprocess calls.
    The cache is thread-safe for concurrent access.

    :returns: Dictionary mapping package names (canonical) to their project locations
    """
    try:
        return _editable_packages_cache.get(_fetch_editable_packages).copy()
    except Exception as e:
        logger.warning(f"Failed to get editable packages: {e}")
        return {}


def is_package_editable(package_name: str, editable_packages: Optional[Dict[str, str]] = None) -> bool:
    """
    Check if a package is installed in editable mode.

    :param package_name: Name of the package to check
    :param editable_packages: Optional cached dict of editable packages (from get_editable_packages)
    :returns: True if package is installed in editable mode, False otherwise
    """
    from packaging.utils import canonicalize_name

    if editable_packages is None:
        editable_packages = get_editable_packages()

    canonical_name = canonicalize_name(package_name)
    return canonical_name in editable_packages


def update_packages_preserving_editable(
    packages_to_update: List[Dict[str, Any]],
    console: Optional[Console] = None,
    timeout: Optional[int] = None,
    cancel_event: Optional[Any] = None
) -> Tuple[List[str], List[str]]:
    """
    Update packages while preserving editable installations.

    For packages installed in editable mode, this function will:
    1. Check if they're in editable mode
    2. Use the original source directory with pip install -e
    3. For regular packages, use normal pip install

    When updating packages, constraints for those packages are temporarily excluded
    to avoid conflicts between constraints and package dependencies.

    :param packages_to_update: List of package dictionaries with keys: name, latest_version, editable
    :param console: Optional Rich console for output
    :param timeout: Optional timeout for pip operations
    :param cancel_event: Optional threading.Event to signal cancellation
    :returns: Tuple of (successful_updates, failed_updates) with package names
    """
    import subprocess
    import sys
    import tempfile
    import os
    from packaging.utils import canonicalize_name

    if console is None:
        console = Console()

    successful_updates = []
    failed_updates = []

    # Get all current constraints and create a filtered version that excludes packages being updated
    from .package_constraints import read_constraints
    all_constraints = read_constraints()

    # Get canonical names of packages being updated
    packages_being_updated = {canonicalize_name(pkg["name"]) for pkg in packages_to_update}

    # Filter out constraints for packages being updated to avoid conflicts
    filtered_constraints = {
        pkg: constraint
        for pkg, constraint in all_constraints.items()
        if pkg not in packages_being_updated
    }

    # Create a temporary constraints file if there are any constraints to apply
    constraint_file = None
    constraint_file_path = None
    try:
        if filtered_constraints:
            constraint_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
            constraint_file_path = constraint_file.name
            for pkg, constraint in filtered_constraints.items():
                constraint_file.write(f"{pkg}{constraint}\n")
            constraint_file.close()

        # Helper function to run subprocess with cancellation support
        def run_with_cancel(cmd, timeout=None):
            """Run a subprocess command that can be cancelled."""
            # Set up environment with constraint file if available
            env = os.environ.copy()
            if constraint_file_path:
                env['PIP_CONSTRAINT'] = constraint_file_path

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env
            )

            try:
                stdout, stderr = process.communicate(timeout=timeout)
                return process.returncode, stdout, stderr
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()  # Clean up
                raise
            except:
                # If we're interrupted or cancelled, kill the process
                if process.poll() is None:  # Process still running
                    process.kill()
                    try:
                        process.communicate(timeout=1)
                    except Exception:
                        pass
                raise

        # Get current editable packages to find source directories
        editable_packages = get_editable_packages()

        for package_info in packages_to_update:
            # Check for cancellation before processing each package
            if cancel_event and cancel_event.is_set():
                console.print("[yellow]Update cancelled by user[/yellow]")
                break

            package_name = package_info["name"]
            is_editable = package_info.get("editable", False)

            try:
                console.print(f"Updating {package_name}...")

                if is_editable:
                    # Package is editable, reinstall from source directory
                    canonical_name = canonicalize_name(package_name)
                    source_path = editable_packages.get(canonical_name)

                    if source_path:
                        console.print(f"  📝 Reinstalling editable package from: {source_path}")

                        # First uninstall the current version
                        uninstall_cmd = [sys.executable, "-m", "pip", "uninstall", package_name, "-y"]
                        returncode, stdout, stderr = run_with_cancel(uninstall_cmd, timeout=timeout)

                        if returncode != 0:
                            console.print(f"  [red]Failed to uninstall {package_name}: {stderr}[/red]")
                            failed_updates.append(package_name)
                            continue

                        # Then reinstall in editable mode
                        install_cmd = [sys.executable, "-m", "pip", "install", "-e", source_path]
                        returncode, stdout, stderr = run_with_cancel(install_cmd, timeout=timeout)

                        if returncode == 0:
                            console.print(f"  [green]✓ Successfully updated editable {package_name}[/green]")
                            successful_updates.append(package_name)
                        else:
                            console.print(f"  [red]Failed to reinstall editable {package_name}: {stderr}[/red]")
                            failed_updates.append(package_name)
                    else:
                        console.print(f"  [yellow]Could not find source path for editable {package_name}, updating normally[/yellow]")
                        # Fall through to normal update
                        is_editable = False

                if not is_editable:
                    # Regular package update
                    # Use --upgrade instead of pinning to specific versions to allow pip's
                    # dependency resolver to handle interdependent packages correctly
                    # (e.g., pydantic and pydantic-core, boto3 and botocore)
                    install_cmd = [sys.executable, "-m", "pip", "install", "--upgrade", package_name]

                    returncode, stdout, stderr = run_with_cancel(install_cmd, timeout=timeout)

                    if returncode == 0:
                        console.print(f"  [green]✓ Successfully updated {package_name}[/green]")
                        successful_updates.append(package_name)
                    else:
                        console.print(f"  [red]Failed to update {package_name}: {stderr}[/red]")
                        failed_updates.append(package_name)

            except subprocess.TimeoutExpired:
                console.print(f"  [red]Timeout updating {package_name}[/red]")
                failed_updates.append(package_name)
            except Exception as e:
                console.print(f"  [red]Error updating {package_name}: {e}[/red]")
                failed_updates.append(package_name)

        return successful_updates, failed_updates

    finally:
        # Clean up temporary constraint file
        if constraint_file_path and os.path.exists(constraint_file_path):
            try:
                os.unlink(constraint_file_path)
            except Exception:
                pass  # Best effort cleanup