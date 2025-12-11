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
    install_packages,
)
from pipu_cli.pretty import print_upgradable_packages_table, print_upgrade_results, ConsoleStream


# Configure rich_click
click.rich_click.USE_RICH_MARKUP = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.GROUP_ARGUMENTS_OPTIONS = True


@click.command()
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
def cli(timeout: int, pre: bool, yes: bool, debug: bool):
    """
    [bold cyan]pipu[/bold cyan] - A cute Python package updater

    Automatically checks for package updates and upgrades them with proper
    constraint resolution.
    """
    console = Console()

    # Configure logging based on debug flag
    if debug:
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
        console.print("[bold]Step 1/5:[/bold] Inspecting installed packages...")
        step1_start = time.time()
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True
        ) as progress:
            task = progress.add_task("Loading packages...", total=None)
            installed_packages = inspect_installed_packages(timeout=timeout)
            progress.update(task, completed=True)
        step1_time = time.time() - step1_start

        num_installed = len(installed_packages)
        console.print(f"  Found {num_installed} installed packages")
        if debug:
            console.print(f"  [dim]Time: {step1_time:.2f}s[/dim]")

        if not installed_packages:
            console.print("[yellow]No packages found.[/yellow]")
            sys.exit(0)

        # Step 2: Check for updates
        console.print("\n[bold]Step 2/5:[/bold] Checking for updates...")
        step2_start = time.time()
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
        step2_time = time.time() - step2_start

        num_updates = len(latest_versions)
        console.print(f"  Found {num_updates} packages with newer versions available")
        if debug:
            console.print(f"  [dim]Time: {step2_time:.2f}s[/dim]")

        if not latest_versions:
            console.print("\n[bold green]All packages are up to date![/bold green]")
            sys.exit(0)

        # Step 3: Resolve upgradable packages
        console.print("\n[bold]Step 3/5:[/bold] Resolving dependency constraints...")
        step3_start = time.time()
        upgradable_packages = resolve_upgradable_packages(
            latest_versions,
            installed_packages
        )
        step3_time = time.time() - step3_start

        # Filter to only upgradable packages
        can_upgrade = [pkg for pkg in upgradable_packages if pkg.upgradable]

        if not can_upgrade:
            console.print("\n[yellow]No packages can be upgraded (all blocked by constraints).[/yellow]")
            sys.exit(0)

        num_upgradable = len(can_upgrade)
        console.print(f"  {num_upgradable} packages can be safely upgraded")
        if debug:
            console.print(f"  [dim]Time: {step3_time:.2f}s[/dim]")

        # Step 4: Display table and ask for confirmation
        console.print("\n[bold]Step 4/5:[/bold] Packages ready for upgrade:\n")
        print_upgradable_packages_table(can_upgrade, console=console)

        if not yes:
            console.print()
            confirm = click.confirm("Do you want to proceed with the upgrade?", default=True)
            if not confirm:
                console.print("[yellow]Upgrade cancelled.[/yellow]")
                sys.exit(0)

        # Step 5: Install packages
        console.print("\n[bold]Step 5/5:[/bold] Upgrading packages...\n")
        step5_start = time.time()

        stream = ConsoleStream(console)
        results = install_packages(can_upgrade, output_stream=stream, timeout=300)
        step5_time = time.time() - step5_start

        # Print results summary
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
