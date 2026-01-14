"""CLI interface for pipu using rich_click."""

import json
import logging
import sys
import time
from typing import Optional

import rich_click as click
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from pipu_cli.package_management import (
    Package,
    inspect_installed_packages,
    get_latest_versions,
    get_latest_versions_parallel,
    resolve_upgradable_packages,
    resolve_upgradable_packages_with_reasons,
    install_packages,
    reinstall_editable_packages,
)
from packaging.version import Version
from pipu_cli.pretty import (
    print_upgradable_packages_table,
    print_upgrade_results,
    print_blocked_packages_table,
    ConsoleStream,
    select_packages_interactively,
)
from pipu_cli.output import JsonOutputFormatter
from pipu_cli.config_file import load_config, get_config_value
from pipu_cli.config import DEFAULT_CACHE_TTL
from pipu_cli.cache import (
    is_cache_fresh,
    load_cache,
    save_cache,
    build_version_cache,
    get_cache_info,
    format_cache_age,
    get_cache_age_seconds,
    clear_cache,
    clear_all_caches,
)


# Configure rich_click
click.rich_click.USE_RICH_MARKUP = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.GROUP_ARGUMENTS_OPTIONS = True


def parse_package_spec(spec: str) -> tuple[str, Optional[str]]:
    """Parse a package specification like 'requests==2.31.0' or 'requests>=2.30'.

    :param spec: Package specification string
    :returns: Tuple of (package_name, version_constraint or None)
    """
    # Common specifier patterns (check longest operators first to avoid partial matches)
    for op in ['==', '>=', '<=', '~=', '!=', '>', '<']:
        if op in spec:
            parts = spec.split(op, 1)
            return (parts[0].strip(), op + parts[1].strip())

    return (spec.strip(), None)


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """
    [bold cyan]pipu[/bold cyan] - A cute Python package updater

    Automatically checks for package updates and upgrades them with proper
    constraint resolution.

    [bold]Commands:[/bold]
      pipu update      Refresh package version cache
      pipu upgrade     Upgrade packages (default command)
      pipu rollback    Restore packages to a previous state

    Run [cyan]pipu <command> --help[/cyan] for command-specific help.
    """
    # If no subcommand provided, default to upgrade
    if ctx.invoked_subcommand is None:
        ctx.invoke(upgrade)


@cli.command()
@click.option(
    "--timeout",
    type=int,
    default=10,
    help="Network timeout in seconds for package queries"
)
@click.option(
    "--pre",
    is_flag=True,
    help="Include pre-release versions"
)
@click.option(
    "--parallel",
    type=int,
    default=1,
    help="Number of parallel requests for version checking (default: 1)"
)
@click.option(
    "--debug",
    is_flag=True,
    help="Enable debug logging"
)
@click.option(
    "--output",
    type=click.Choice(["human", "json"]),
    default="human",
    help="Output format (human-readable or json)"
)
def update(timeout: int, pre: bool, parallel: int, debug: bool, output: str) -> None:
    """
    Refresh the package version cache.

    Fetches the latest version information from PyPI for all installed
    packages and stores it locally. This speeds up subsequent upgrade
    commands by avoiding repeated network requests.

    Constraint resolution is performed at upgrade time, not during update.

    [bold]Examples:[/bold]
      pipu update              Update cache with defaults
      pipu update --parallel 4 Update with parallel requests
      pipu update --pre        Include pre-release versions
    """
    console = Console()

    # Load configuration file
    config = load_config()
    if timeout == 10:
        timeout = get_config_value(config, 'timeout', 10)
    if not pre:
        pre = get_config_value(config, 'pre', False)
    if not debug:
        debug = get_config_value(config, 'debug', False)
    if parallel == 1:
        parallel = get_config_value(config, 'parallel', 1)

    # Configure logging
    if debug and output != "json":
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(message)s',
            handlers=[RichHandler(console=console, show_time=False, show_path=False, markup=True)]
        )
        logging.getLogger('pip._internal').setLevel(logging.WARNING)
        logging.getLogger('pip._vendor').setLevel(logging.WARNING)
        console.print("[dim]Debug mode enabled[/dim]\n")

    try:
        # Step 1: Inspect installed packages
        if output != "json":
            console.print("[bold]Step 1/2:[/bold] Inspecting installed packages...")

        step1_start = time.time()
        if output != "json":
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True
            ) as progress:
                task = progress.add_task("Loading packages...", total=None)
                installed_packages = inspect_installed_packages(timeout=timeout)
                progress.update(task, completed=True)
        else:
            installed_packages = inspect_installed_packages(timeout=timeout)
        step1_time = time.time() - step1_start

        num_installed = len(installed_packages)
        if output != "json":
            console.print(f"  Found {num_installed} installed packages")
            if debug:
                console.print(f"  [dim]Time: {step1_time:.2f}s[/dim]")

        if not installed_packages:
            if output == "json":
                print('{"error": "No packages found"}')
            else:
                console.print("[yellow]No packages found.[/yellow]")
            sys.exit(0)

        # Step 2: Fetch latest versions from PyPI and save to cache
        if output != "json":
            console.print("\n[bold]Step 2/2:[/bold] Fetching latest versions from PyPI...")

        step2_start = time.time()
        if output != "json":
            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
                transient=True
            ) as progress:
                task = progress.add_task("Checking packages...", total=len(installed_packages))

                def update_progress(current: int, total: int) -> None:
                    progress.update(task, completed=current)

                if parallel > 1:
                    latest_versions = get_latest_versions_parallel(
                        installed_packages, timeout=timeout, include_prereleases=pre,
                        max_workers=parallel, progress_callback=update_progress
                    )
                else:
                    latest_versions = get_latest_versions(
                        installed_packages, timeout=timeout, include_prereleases=pre,
                        progress_callback=update_progress
                    )
        else:
            if parallel > 1:
                latest_versions = get_latest_versions_parallel(
                    installed_packages, timeout=timeout, include_prereleases=pre, max_workers=parallel
                )
            else:
                latest_versions = get_latest_versions(
                    installed_packages, timeout=timeout, include_prereleases=pre
                )
        step2_time = time.time() - step2_start

        # Build and save cache (only latest versions, no constraint resolution)
        cache_data = build_version_cache(latest_versions)
        cache_path = save_cache(cache_data, include_prereleases=pre)

        num_with_updates = len(latest_versions)

        if output == "json":
            result = {
                "status": "success",
                "packages_checked": num_installed,
                "packages_with_updates": num_with_updates,
                "cache_path": str(cache_path)
            }
            print(json.dumps(result, indent=2))
        else:
            console.print(f"  Cached {num_with_updates} packages with updates available")
            if debug:
                console.print(f"  [dim]Time: {step2_time:.2f}s[/dim]")
                console.print(f"  [dim]Cache saved to: {cache_path}[/dim]")

            console.print("\n[bold green]Cache updated![/bold green] Run [cyan]pipu upgrade[/cyan] to upgrade your packages.")

            total_time = step1_time + step2_time
            if debug:
                console.print(f"[dim]Total time: {total_time:.2f}s[/dim]")

        sys.exit(0)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(130)
    except Exception as e:
        if output == "json":
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"\n[bold red]Error:[/bold red] {e}")
        sys.exit(1)


@cli.command()
@click.argument('packages', nargs=-1)
@click.option(
    "--timeout",
    type=int,
    default=10,
    help="Network timeout in seconds for package queries"
)
@click.option(
    "--pre",
    is_flag=True,
    help="Include pre-release versions"
)
@click.option(
    "--yes", "-y",
    is_flag=True,
    help="Automatically confirm upgrade without prompting"
)
@click.option(
    "--debug",
    is_flag=True,
    help="Enable debug logging and show performance timing"
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be upgraded without actually upgrading"
)
@click.option(
    "--exclude",
    type=str,
    default="",
    help="Comma-separated list of packages to exclude from upgrade"
)
@click.option(
    "--show-blocked",
    is_flag=True,
    help="Show packages that cannot be upgraded and why"
)
@click.option(
    "--output",
    type=click.Choice(["human", "json"]),
    default="human",
    help="Output format (human-readable or json)"
)
@click.option(
    "--update-requirements",
    type=click.Path(exists=True),
    default=None,
    help="Update the specified requirements.txt file with new versions"
)
@click.option(
    "--parallel",
    type=int,
    default=1,
    help="Number of parallel requests for version checking (default: 1)"
)
@click.option(
    "--interactive", "-i",
    is_flag=True,
    help="Interactively select packages to upgrade"
)
@click.option(
    "--no-cache",
    is_flag=True,
    help="Skip cache and fetch fresh version data"
)
@click.option(
    "--cache-ttl",
    type=int,
    default=None,
    help=f"Cache freshness threshold in seconds (default: {DEFAULT_CACHE_TTL})"
)
def upgrade(packages: tuple[str, ...], timeout: int, pre: bool, yes: bool, debug: bool, dry_run: bool,
            exclude: str, show_blocked: bool, output: str, update_requirements: Optional[str],
            parallel: int, interactive: bool, no_cache: bool, cache_ttl: Optional[int]) -> None:
    """
    Upgrade installed packages.

    By default, upgrades all packages that have newer versions available.
    Optionally specify PACKAGES to upgrade only those packages.

    Uses cached version data if available and fresh. Run [cyan]pipu update[/cyan]
    to refresh the cache manually.

    [bold]Examples:[/bold]
      pipu upgrade                    Upgrade all packages
      pipu upgrade requests numpy     Upgrade specific packages
      pipu upgrade --dry-run          Preview without installing
      pipu upgrade -i                 Interactive package selection
      pipu upgrade --no-cache         Force fresh version check
    """
    console = Console()

    # Load configuration file
    config = load_config()

    # Apply config file values for options at defaults
    if timeout == 10:
        timeout = get_config_value(config, 'timeout', 10)
    if not exclude:
        exclude_list = get_config_value(config, 'exclude', [])
        if exclude_list:
            exclude = ','.join(exclude_list)
    if not pre:
        pre = get_config_value(config, 'pre', False)
    if not yes:
        yes = get_config_value(config, 'yes', False)
    if not debug:
        debug = get_config_value(config, 'debug', False)
    if not dry_run:
        dry_run = get_config_value(config, 'dry_run', False)
    if not show_blocked:
        show_blocked = get_config_value(config, 'show_blocked', False)
    if output == "human":
        output = get_config_value(config, 'output', 'human')
    if cache_ttl is None:
        cache_ttl = get_config_value(config, 'cache_ttl', DEFAULT_CACHE_TTL)

    # Check if caching is enabled
    cache_enabled = get_config_value(config, 'cache_enabled', True) and not no_cache

    # Initialize JSON formatter if needed
    json_formatter = JsonOutputFormatter() if output == "json" else None

    # Interactive mode only works in human output mode
    if interactive and output == "json":
        console.print("[yellow]Warning: --interactive is not compatible with --output json. Ignoring --interactive.[/yellow]")
        interactive = False

    # Configure logging
    if debug and output != "json":
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(message)s',
            handlers=[RichHandler(console=console, show_time=False, show_path=False, markup=True)]
        )
        logging.getLogger('pip._internal').setLevel(logging.WARNING)
        logging.getLogger('pip._vendor').setLevel(logging.WARNING)
        console.print("[dim]Debug mode enabled[/dim]\n")

    try:
        # Check cache freshness
        effective_cache_ttl = DEFAULT_CACHE_TTL if cache_ttl is None else cache_ttl
        use_cache = cache_enabled and is_cache_fresh(effective_cache_ttl)

        if use_cache and output != "json":
            cache_age = get_cache_age_seconds()
            console.print(f"[dim]Using cached data ({format_cache_age(cache_age)})[/dim]\n")

        # Step 1: Inspect installed packages
        if output != "json":
            console.print("[bold]Step 1/5:[/bold] Inspecting installed packages...")

        step1_start = time.time()
        if output != "json":
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True
            ) as progress:
                task = progress.add_task("Loading packages...", total=None)
                installed_packages = inspect_installed_packages(timeout=timeout)
                progress.update(task, completed=True)
        else:
            installed_packages = inspect_installed_packages(timeout=timeout)
        step1_time = time.time() - step1_start

        num_installed = len(installed_packages)
        if output != "json":
            console.print(f"  Found {num_installed} installed packages")
            if debug:
                console.print(f"  [dim]Time: {step1_time:.2f}s[/dim]")

        if not installed_packages:
            if output == "json":
                print('{"error": "No packages found"}')
            else:
                console.print("[yellow]No packages found.[/yellow]")
            sys.exit(0)

        # Step 2: Get latest versions (from cache or network)
        if output != "json":
            if use_cache:
                console.print("\n[bold]Step 2/5:[/bold] Loading cached version data...")
            else:
                console.print("\n[bold]Step 2/5:[/bold] Fetching latest versions from PyPI...")

        step2_start = time.time()
        latest_versions: dict = {}
        cache_was_used = False

        if use_cache:
            # Load latest versions from cache (skip PyPI queries entirely)
            cache_data = load_cache()
            if cache_data and cache_data.latest_versions:
                # Reconstruct latest_versions dict from cache
                # Maps InstalledPackage -> Package with latest version
                for installed_pkg in installed_packages:
                    name_lower = installed_pkg.name.lower()
                    if name_lower in cache_data.latest_versions:
                        cached_version = cache_data.latest_versions[name_lower]
                        try:
                            latest_ver = Version(cached_version)
                            # Only include if it's actually newer
                            if latest_ver > installed_pkg.version:
                                latest_pkg = Package(
                                    name=installed_pkg.name,
                                    version=latest_ver
                                )
                                latest_versions[installed_pkg] = latest_pkg
                        except Exception:
                            pass  # Skip invalid versions
                cache_was_used = True
            else:
                use_cache = False

        if not use_cache:
            # Fetch from network
            if output != "json":
                with Progress(
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    console=console,
                    transient=True
                ) as progress:
                    task = progress.add_task("Checking packages...", total=len(installed_packages))

                    def update_progress(current: int, total: int) -> None:
                        progress.update(task, completed=current)

                    if parallel > 1:
                        latest_versions = get_latest_versions_parallel(
                            installed_packages, timeout=timeout, include_prereleases=pre,
                            max_workers=parallel, progress_callback=update_progress
                        )
                    else:
                        latest_versions = get_latest_versions(
                            installed_packages, timeout=timeout, include_prereleases=pre,
                            progress_callback=update_progress
                        )
            else:
                if parallel > 1:
                    latest_versions = get_latest_versions_parallel(
                        installed_packages, timeout=timeout, include_prereleases=pre, max_workers=parallel
                    )
                else:
                    latest_versions = get_latest_versions(
                        installed_packages, timeout=timeout, include_prereleases=pre
                    )

            # Update cache with fresh data
            if cache_enabled:
                version_cache = build_version_cache(latest_versions)
                save_cache(version_cache, include_prereleases=pre)

        step2_time = time.time() - step2_start

        num_updates = len(latest_versions)
        if output != "json":
            console.print(f"  Found {num_updates} packages with newer versions available")
            if cache_was_used:
                console.print("  [dim](from cache)[/dim]")
            if debug:
                console.print(f"  [dim]Time: {step2_time:.2f}s[/dim]")

        if not latest_versions:
            if output == "json":
                print('{"upgradable": [], "upgradable_count": 0, "message": "All packages are up to date"}')
            else:
                console.print("\n[bold green]All packages are up to date![/bold green]")
            sys.exit(0)

        # Step 3: Resolve upgradable packages
        if output != "json":
            console.print("\n[bold]Step 3/5:[/bold] Resolving dependency constraints...")
        step3_start = time.time()

        if show_blocked:
            upgradable_packages, blocked_packages = resolve_upgradable_packages_with_reasons(
                latest_versions, installed_packages
            )
        else:
            all_upgradable = resolve_upgradable_packages(latest_versions, installed_packages)
            upgradable_packages = [pkg for pkg in all_upgradable if pkg.upgradable]
            blocked_packages = []

        step3_time = time.time() - step3_start

        # Apply exclusions
        excluded_names = set()
        if exclude:
            excluded_names = {name.strip().lower() for name in exclude.split(',')}
            if debug and excluded_names:
                console.print(f"  [dim]Excluding: {', '.join(sorted(excluded_names))}[/dim]")

        # Filter to only upgradable packages (excluding excluded ones)
        can_upgrade = [pkg for pkg in upgradable_packages if pkg.name.lower() not in excluded_names]

        # Parse package specifications and filter to specific packages if provided
        package_constraints = {}
        if packages:
            requested_packages = set()
            for spec in packages:
                name, constraint = parse_package_spec(spec)
                requested_packages.add(name.lower())
                if constraint:
                    package_constraints[name.lower()] = constraint

            can_upgrade = [pkg for pkg in can_upgrade if pkg.name.lower() in requested_packages]

            if debug:
                console.print(f"  [dim]Filtering to: {', '.join(packages)}[/dim]")
                if package_constraints:
                    console.print(f"  [dim]Version constraints: {package_constraints}[/dim]")

        if not can_upgrade:
            if output == "json":
                assert json_formatter is not None
                json_data = json_formatter.format_all(
                    upgradable=[],
                    blocked=blocked_packages if show_blocked else None
                )
                print(json_data)
            else:
                console.print("\n[yellow]No packages can be upgraded (all blocked by constraints).[/yellow]")
                if show_blocked and blocked_packages:
                    console.print()
                    print_blocked_packages_table(blocked_packages, console=console)
            sys.exit(0)

        num_upgradable = len(can_upgrade)
        if output != "json":
            console.print(f"  {num_upgradable} packages can be safely upgraded")
            if debug:
                console.print(f"  [dim]Time: {step3_time:.2f}s[/dim]")

        # Step 4: Display table and ask for confirmation
        if output == "json":
            assert json_formatter is not None
            if dry_run:
                json_data = json_formatter.format_all(
                    upgradable=can_upgrade,
                    blocked=blocked_packages if show_blocked else None
                )
                print(json_data)
                sys.exit(0)
        else:
            console.print("\n[bold]Step 4/5:[/bold] Packages ready for upgrade:\n")
            print_upgradable_packages_table(can_upgrade, console=console)

            if show_blocked and blocked_packages:
                console.print()
                print_blocked_packages_table(blocked_packages, console=console)

            if interactive:
                can_upgrade = select_packages_interactively(can_upgrade, console)
                if not can_upgrade:
                    console.print("[yellow]No packages selected for upgrade.[/yellow]")
                    sys.exit(0)

            if dry_run:
                console.print("\n[bold cyan]Dry run complete.[/bold cyan] No packages were modified.")
                sys.exit(0)

        # Skip confirmation if interactive mode (already confirmed) or --yes flag
        if not yes and not interactive and output != "json":
            console.print()
            confirm = click.confirm("Do you want to proceed with the upgrade?", default=True)
            if not confirm:
                console.print("[yellow]Upgrade cancelled.[/yellow]")
                sys.exit(0)

        # Separate editable and non-editable packages
        editable_packages = [pkg for pkg in can_upgrade if pkg.is_editable]
        non_editable_packages = [pkg for pkg in can_upgrade if not pkg.is_editable]

        # Step 5: Install packages
        if output != "json":
            total_to_upgrade = len(non_editable_packages) + len(editable_packages)
            console.print(f"[bold]Step 5/5:[/bold] Upgrading {total_to_upgrade} package(s)...\n")
        step5_start = time.time()

        # Save state for potential rollback
        from pipu_cli.rollback import save_state
        pre_upgrade_packages = [
            {"name": pkg.name, "version": str(pkg.version)}
            for pkg in can_upgrade
        ]
        save_state(pre_upgrade_packages, "Pre-upgrade state")

        stream = ConsoleStream(console) if output != "json" else None
        results = []

        # First, upgrade non-editable packages via pip install --upgrade
        if non_editable_packages:
            if output != "json":
                console.print(f"Upgrading {len(non_editable_packages)} regular package(s)...\n")
            regular_results = install_packages(
                non_editable_packages,
                output_stream=stream,
                timeout=300,
                version_constraints=package_constraints if package_constraints else None
            )
            results.extend(regular_results)

        # Then, reinstall editable packages to update their versions
        if editable_packages:
            if output != "json":
                console.print(f"\nReinstalling {len(editable_packages)} editable package(s)...\n")
            editable_results = reinstall_editable_packages(
                editable_packages,
                output_stream=stream,
                timeout=300
            )
            results.extend(editable_results)

        step5_time = time.time() - step5_start

        # Update requirements file if requested
        if update_requirements:
            from pathlib import Path
            from pipu_cli.requirements import update_requirements_file
            req_path = Path(update_requirements)
            updated = update_requirements_file(req_path, results)
            if updated and output != "json":
                console.print(f"\n[bold green]Updated {updated} package(s) in {update_requirements}[/bold green]")

        # Print results summary
        if output == "json":
            assert json_formatter is not None
            json_data = json_formatter.format_all(
                upgradable=can_upgrade,
                blocked=blocked_packages if show_blocked else None,
                results=results
            )
            print(json_data)
        else:
            print_upgrade_results(results, console=console)

            if debug:
                console.print(f"\n[dim]Step 5 time: {step5_time:.2f}s[/dim]")
                total_time = step1_time + step2_time + step3_time + step5_time
                console.print(f"[dim]Total time: {total_time:.2f}s[/dim]")

        # Exit with appropriate code
        failed = [pkg for pkg in results if not pkg.upgraded]
        if failed:
            sys.exit(1)
        else:
            sys.exit(0)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(130)
    except click.Abort:
        console.print("\n[yellow]Update cancelled by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}")
        sys.exit(1)


@cli.command()
@click.option(
    "--list", "-l",
    "list_states_flag",
    is_flag=True,
    help="List all saved rollback states"
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be restored without actually restoring"
)
@click.option(
    "--yes", "-y",
    is_flag=True,
    help="Automatically confirm rollback without prompting"
)
@click.option(
    "--state",
    type=str,
    default=None,
    help="Specific state file to rollback to (use --list to see available states)"
)
def rollback(list_states_flag: bool, dry_run: bool, yes: bool, state: Optional[str]) -> None:
    """
    Restore packages to a previous state.

    Before each upgrade, pipu saves the current package versions. Use this
    command to restore packages to their pre-upgrade state.

    [bold]Examples:[/bold]
      pipu rollback --list       List all saved states
      pipu rollback --dry-run    Preview what would be restored
      pipu rollback --yes        Rollback without confirmation
      pipu rollback --state FILE Rollback to a specific state
    """
    from pipu_cli.rollback import get_latest_state, rollback_to_state, list_states as get_states, ROLLBACK_DIR

    console = Console()

    # List saved states if requested
    if list_states_flag:
        states = get_states()
        if not states:
            console.print("[yellow]No saved states found.[/yellow]")
            console.print(f"[dim]States are saved in: {ROLLBACK_DIR}[/dim]")
            sys.exit(0)

        table = Table(title="[bold]Saved Rollback States[/bold]")
        table.add_column("#", style="dim", width=3)
        table.add_column("State File", style="cyan")
        table.add_column("Timestamp", style="green")
        table.add_column("Packages", style="magenta", justify="right")
        table.add_column("Description", style="dim")

        for idx, s in enumerate(states, 1):
            ts = s["timestamp"]
            if len(ts) == 15 and ts[8] == "_":
                formatted_ts = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}"
            else:
                formatted_ts = ts

            table.add_row(
                str(idx),
                s["file"],
                formatted_ts,
                str(s["package_count"]),
                s["description"] or "-"
            )

        console.print(table)
        console.print(f"\n[dim]States saved in: {ROLLBACK_DIR}[/dim]")
        console.print("[dim]Use --state <filename> to rollback to a specific state[/dim]")
        sys.exit(0)

    # Get the state to rollback to
    if state:
        state_path = ROLLBACK_DIR / state
        if not state_path.exists():
            console.print(f"[red]State file not found:[/red] {state}")
            console.print("[dim]Use 'pipu rollback --list' to see available states[/dim]")
            sys.exit(1)

        with open(state_path, 'r') as f:
            state_data = json.load(f)
    else:
        state_data = get_latest_state()

    if state_data is None:
        console.print("[yellow]No saved state found.[/yellow]")
        console.print("[dim]A state is automatically saved before each upgrade.[/dim]")
        sys.exit(0)

    # Show what will be rolled back
    packages = state_data.get("packages", [])
    timestamp = state_data.get("timestamp", "unknown")
    description = state_data.get("description", "")

    if len(timestamp) == 15 and timestamp[8] == "_":
        formatted_ts = f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]} {timestamp[9:11]}:{timestamp[11:13]}:{timestamp[13:15]}"
    else:
        formatted_ts = timestamp

    console.print(f"\n[bold]Rollback State:[/bold] {formatted_ts}")
    if description:
        console.print(f"[dim]{description}[/dim]")
    console.print()

    table = Table(title=f"[bold]{len(packages)} Package(s) to Restore[/bold]")
    table.add_column("Package", style="cyan")
    table.add_column("Version", style="green")

    for pkg in packages:
        table.add_row(pkg["name"], pkg["version"])

    console.print(table)

    if dry_run:
        console.print("\n[bold cyan]Dry run complete.[/bold cyan] No packages were modified.")
        sys.exit(0)

    if not yes:
        console.print()
        confirm = click.confirm("Do you want to proceed with the rollback?", default=True)
        if not confirm:
            console.print("[yellow]Rollback cancelled.[/yellow]")
            sys.exit(0)

    console.print("\n[bold]Rolling back packages...[/bold]\n")

    rolled_back = rollback_to_state(state_data, dry_run=False)

    if rolled_back:
        console.print(f"\n[bold green]Successfully rolled back {len(rolled_back)} package(s):[/bold green]")
        for pkg in rolled_back:
            console.print(f"  - {pkg}")
    else:
        console.print("[yellow]No packages were rolled back.[/yellow]")

    sys.exit(0)


@cli.command()
def cache() -> None:
    """
    Show cache information.

    Displays details about the package version cache for the current
    Python environment, including age and freshness status.

    [bold]Examples:[/bold]
      pipu cache           Show cache status
      pipu clean           Clear current environment cache
      pipu clean --all     Clear all environment caches
    """
    console = Console()

    # Show cache info
    info = get_cache_info()

    console.print("[bold]Cache Information[/bold]\n")
    console.print(f"  Environment ID: [cyan]{info['environment_id']}[/cyan]")
    console.print(f"  Python: [dim]{info['python_executable']}[/dim]")
    console.print(f"  Cache path: [dim]{info['path']}[/dim]")

    if info['exists']:
        console.print("\n  [green]Cache exists[/green]")
        console.print(f"  Updated: {info.get('age_human', 'unknown')}")
        console.print(f"  Packages cached: {info.get('package_count', 0)}")

        # Check if fresh
        if is_cache_fresh():
            console.print("  Status: [green]Fresh[/green] (within TTL)")
        else:
            console.print("  Status: [yellow]Stale[/yellow] (will refresh on next upgrade)")
    else:
        console.print("\n  [yellow]No cache[/yellow]")
        console.print("  Run [cyan]pipu update[/cyan] to create cache")

    sys.exit(0)


@cli.command()
@click.option(
    "--all", "-a",
    "clean_all",
    is_flag=True,
    help="Clean up files for all environments"
)
def clean(clean_all: bool) -> None:
    """
    Clean up pipu caches and temporary files.

    Removes cached package version data and other temporary files
    created by pipu. By default, cleans up files for the current
    Python environment only.

    [bold]Examples:[/bold]
      pipu clean           Clean current environment
      pipu clean --all     Clean all environments
    """
    console = Console()

    if clean_all:
        count = clear_all_caches()
        if count > 0:
            console.print(f"[bold green]Cleared {count} cache(s).[/bold green]")
        else:
            console.print("[yellow]No caches to clear.[/yellow]")
    else:
        if clear_cache():
            console.print("[bold green]Cache cleared for current environment.[/bold green]")
        else:
            console.print("[yellow]No cache to clear for current environment.[/yellow]")

    sys.exit(0)


if __name__ == "__main__":
    cli()
