"""CLI interface for pipu using rich_click."""

import logging
import sys
import time

import rich_click as click
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn

from pipu_cli.package_management import (
    inspect_installed_packages,
    get_latest_versions,
    resolve_upgradable_packages,
    resolve_upgradable_packages_with_reasons,
    install_packages,
)
from pipu_cli.pretty import (
    print_upgradable_packages_table,
    print_upgrade_results,
    print_blocked_packages_table,
    ConsoleStream,
)
from pipu_cli.output import JsonOutputFormatter
from pipu_cli.config_file import load_config, get_config_value


# Configure rich_click
click.rich_click.USE_RICH_MARKUP = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.GROUP_ARGUMENTS_OPTIONS = True


@click.command()
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
def cli(packages: tuple, timeout: int, pre: bool, yes: bool, debug: bool, dry_run: bool, exclude: str, show_blocked: bool, output: str, update_requirements: str) -> None:
    """
    [bold cyan]pipu[/bold cyan] - A cute Python package updater

    Automatically checks for package updates and upgrades them with proper
    constraint resolution.

    Optionally specify PACKAGES to upgrade only those packages.
    """
    console = Console()

    # Load configuration file
    config = load_config()

    # CLI options override config file values
    # Only use config values if CLI options are at their defaults
    if timeout == 10:  # Default timeout
        timeout = get_config_value(config, 'timeout', 10)
    if not exclude:  # No exclusions specified
        exclude_list = get_config_value(config, 'exclude', [])
        if exclude_list:
            exclude = ','.join(exclude_list)
    if not pre:  # Pre-release not specified
        pre = get_config_value(config, 'pre', False)
    if not yes:  # Auto-confirm not specified
        yes = get_config_value(config, 'yes', False)
    if not debug:  # Debug not specified
        debug = get_config_value(config, 'debug', False)
    if not dry_run:  # Dry-run not specified
        dry_run = get_config_value(config, 'dry_run', False)
    if not show_blocked:  # Show-blocked not specified
        show_blocked = get_config_value(config, 'show_blocked', False)
    if output == "human":  # Default output format
        output = get_config_value(config, 'output', 'human')

    # Initialize JSON formatter if needed
    json_formatter = JsonOutputFormatter() if output == "json" else None

    # Configure logging based on debug flag
    if debug and output != "json":
        # Use Rich's logging handler for clean integration with console
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(message)s',
            handlers=[RichHandler(
                console=console,
                show_time=False,
                show_path=False,
                markup=True
            )]
        )

        # Silence verbose pip internal logging
        logging.getLogger('pip._internal').setLevel(logging.WARNING)
        logging.getLogger('pip._vendor').setLevel(logging.WARNING)

        console.print("[dim]Debug mode enabled[/dim]\n")

    try:
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

        # Step 2: Check for updates
        if output != "json":
            console.print("\n[bold]Step 2/5:[/bold] Checking for updates...")
        step2_start = time.time()
        if output != "json":
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True
            ) as progress:
                task = progress.add_task("Querying package indexes...", total=None)
                latest_versions = get_latest_versions(
                    installed_packages,
                    timeout=timeout,
                    include_prereleases=pre
                )
                progress.update(task, completed=True)
        else:
            latest_versions = get_latest_versions(
                installed_packages,
                timeout=timeout,
                include_prereleases=pre
            )
        step2_time = time.time() - step2_start

        num_updates = len(latest_versions)
        if output != "json":
            console.print(f"  Found {num_updates} packages with newer versions available")
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
                latest_versions,
                installed_packages
            )
        else:
            all_upgradable = resolve_upgradable_packages(
                latest_versions,
                installed_packages
            )
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
        can_upgrade = [
            pkg for pkg in upgradable_packages
            if pkg.name.lower() not in excluded_names
        ]

        # Filter to specific packages if provided
        if packages:
            requested_packages = {name.lower() for name in packages}
            can_upgrade = [pkg for pkg in can_upgrade if pkg.name.lower() in requested_packages]

            if debug:
                console.print(f"  [dim]Filtering to: {', '.join(packages)}[/dim]")

        if not can_upgrade:
            if output == "json":
                # Output JSON for empty upgradable list
                json_data = json_formatter.format_all(
                    upgradable=[],
                    blocked=blocked_packages if show_blocked else None
                )
                print(json_data)
            else:
                console.print("\n[yellow]No packages can be upgraded (all blocked by constraints).[/yellow]")
                # Show blocked packages if requested, even when no upgradable packages
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
            # In JSON mode, output the upgradable packages and stop (unless installing)
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

            # Show blocked packages if requested
            if show_blocked and blocked_packages:
                console.print()
                print_blocked_packages_table(blocked_packages, console=console)

            # In dry-run mode, stop here
            if dry_run:
                console.print("\n[bold cyan]Dry run complete.[/bold cyan] No packages were modified.")
                sys.exit(0)

        if not yes and output != "json":
            console.print()
            confirm = click.confirm("Do you want to proceed with the upgrade?", default=True)
            if not confirm:
                console.print("[yellow]Upgrade cancelled.[/yellow]")
                sys.exit(0)

        # Step 5: Install packages
        if output != "json":
            console.print("\n[bold]Step 5/5:[/bold] Upgrading packages...\n")
        step5_start = time.time()

        stream = ConsoleStream(console) if output != "json" else None
        results = install_packages(can_upgrade, output_stream=stream, timeout=300)
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
                console.print(f"[dim]  Step 1 (Inspect): {step1_time:.2f}s ({step1_time/total_time*100:.1f}%)[/dim]")
                console.print(f"[dim]  Step 2 (Check updates): {step2_time:.2f}s ({step2_time/total_time*100:.1f}%)[/dim]")
                console.print(f"[dim]  Step 3 (Resolve constraints): {step3_time:.2f}s ({step3_time/total_time*100:.1f}%)[/dim]")
                console.print(f"[dim]  Step 5 (Install): {step5_time:.2f}s ({step5_time/total_time*100:.1f}%)[/dim]")

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


if __name__ == "__main__":
    cli()
