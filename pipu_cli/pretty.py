"""Pretty printing functions for pipu CLI."""

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from rich.console import Console
from rich.table import Table
from rich.prompt import Confirm, Prompt

from pipu_cli.package_management import UpgradePackageInfo, UpgradedPackage, BlockedPackageInfo, InstalledResult


class ConsoleStream:
    """A stream adapter that writes to a Rich Console.

    This class implements the write/flush protocol expected by
    package_management.OutputStream, allowing pip output to be
    displayed through Rich's console.
    """

    def __init__(self, console: Console) -> None:
        """Initialize with a Rich console instance.

        :param console: Rich Console to write output to
        """
        self.console = console

    def write(self, text: str) -> None:
        """Write text to the console if non-empty.

        :param text: Text to write
        """
        if text and text.strip():
            self.console.print(text, end="")

    def flush(self) -> None:
        """Flush the stream (no-op for console)."""
        pass


def print_upgradable_packages_table(
    packages: List[UpgradePackageInfo],
    console: Optional[Console] = None
) -> None:
    """
    Print a table of upgradable packages with version information.

    :param packages: List of UpgradePackageInfo objects to display
    :param console: Optional Rich console instance (creates new one if not provided)
    """
    if console is None:
        console = Console()

    if not packages:
        console.print("[yellow]No packages need upgrading.[/yellow]")
        return

    # Filter to only upgradable packages
    upgradable = [pkg for pkg in packages if pkg.upgradable]

    if not upgradable:
        console.print("[yellow]No packages can be upgraded (all blocked by constraints).[/yellow]")
        return

    # Create table
    num_upgradable = len(upgradable)
    table = Table(title=f"[bold]{num_upgradable} Package(s) Available for Upgrade[/bold]")
    table.add_column("Package", style="cyan", no_wrap=True)
    table.add_column("Current", style="magenta")
    table.add_column("Latest", style="green")
    table.add_column("Editable", style="yellow")

    for pkg in upgradable:
        editable_mark = "Yes" if pkg.is_editable else ""
        table.add_row(
            pkg.name,
            str(pkg.version),
            str(pkg.latest_version),
            editable_mark
        )

    console.print(table)


def print_blocked_packages_table(
    packages: List[BlockedPackageInfo],
    console: Optional[Console] = None
) -> None:
    """
    Print a table of blocked packages with reasons.

    :param packages: List of BlockedPackageInfo objects to display
    :param console: Optional Rich console instance
    """
    if console is None:
        console = Console()

    if not packages:
        return

    num_blocked = len(packages)
    table = Table(title=f"[bold yellow]{num_blocked} Package(s) Blocked by Constraints[/bold yellow]")
    table.add_column("Package", style="cyan", no_wrap=True)
    table.add_column("Current", style="magenta")
    table.add_column("Available", style="green")
    table.add_column("Blocked By", style="red")

    for pkg in packages:
        blocked_by = ", ".join(pkg.blocked_by)

        table.add_row(
            pkg.name,
            str(pkg.version),
            str(pkg.latest_version),
            blocked_by
        )

    console.print(table)


def print_upgrade_results(
    results: List[UpgradedPackage],
    console: Optional[Console] = None
) -> None:
    """
    Print a summary of package upgrade results.

    :param results: List of UpgradedPackage objects with upgrade status
    :param console: Optional Rich console instance (creates new one if not provided)
    """
    if console is None:
        console = Console()

    if not results:
        console.print("[yellow]No packages were processed.[/yellow]")
        return

    # Separate successful and failed upgrades
    successful = [pkg for pkg in results if pkg.upgraded]
    failed = [pkg for pkg in results if not pkg.upgraded]

    # Print success summary
    if successful:
        num_successful = len(successful)
        console.print(f"\n[bold green]Successfully upgraded {num_successful} package(s):[/bold green]")
        for pkg in successful:
            prev_ver = str(pkg.previous_version)
            curr_ver = str(pkg.version)
            console.print(f"  - {pkg.name}: {prev_ver} -> {curr_ver}")

    # Print failure summary
    if failed:
        num_failed = len(failed)
        console.print(f"\n[bold yellow]{num_failed} package(s) could not be upgraded:[/bold yellow]")

        table = Table(show_header=True, header_style="bold yellow")
        table.add_column("Package", style="cyan")
        table.add_column("Current Version", style="magenta")
        table.add_column("Reason", style="dim")

        for pkg in failed:
            reason = pkg.failure_reason or "Unknown failure"
            table.add_row(
                pkg.name,
                str(pkg.version),
                reason
            )

        console.print(table)

    # Overall summary
    console.print()
    if failed:
        num_successful = len(successful)
        num_total = len(results)
        console.print(f"[bold]Summary:[/bold] {num_successful}/{num_total} packages upgraded successfully")
    else:
        console.print("[bold green]All packages upgraded successfully![/bold green]")


def print_install_results(
    results: List[InstalledResult],
    console: Optional[Console] = None
) -> None:
    """Print a summary of package install results.

    :param results: List of InstalledResult objects with install status
    :param console: Optional Rich console instance
    """
    if console is None:
        console = Console()

    if not results:
        console.print("[yellow]No packages were processed.[/yellow]")
        return

    successful = [pkg for pkg in results if pkg.installed]
    failed = [pkg for pkg in results if not pkg.installed]

    if successful:
        console.print(f"\n[bold green]Successfully installed/updated {len(successful)} package(s):[/bold green]")
        for pkg in successful:
            if pkg.previous_version is None:
                console.print(f"  - {pkg.name}: (new) -> {pkg.version}")
            elif pkg.version > pkg.previous_version:
                console.print(f"  - {pkg.name}: {pkg.previous_version} -> {pkg.version}")
            else:
                console.print(f"  - {pkg.name}: {pkg.version} (unchanged)")

    if failed:
        console.print(f"\n[bold yellow]{len(failed)} package(s) could not be installed:[/bold yellow]")
        table = Table(show_header=True, header_style="bold yellow")
        table.add_column("Package", style="cyan")
        table.add_column("Reason", style="dim")
        for pkg in failed:
            table.add_row(pkg.name, pkg.failure_reason or "Unknown failure")
        console.print(table)

    console.print()
    if failed:
        console.print(f"[bold]Summary:[/bold] {len(successful)}/{len(results)} packages installed successfully")
    else:
        console.print("[bold green]All packages installed successfully![/bold green]")


def _parse_selection(selection: str, max_index: int) -> List[int]:
    """Parse a selection string supporting ranges and comma-separated values.

    Examples:
        "1,2,3" -> [0, 1, 2]
        "1-3" -> [0, 1, 2]
        "1-3, 5" -> [0, 1, 2, 4]
        "1, 3-5, 7" -> [0, 2, 3, 4, 6]

    :param selection: User input string
    :param max_index: Maximum valid index (1-based)
    :returns: List of 0-based indices
    :raises ValueError: If selection cannot be parsed
    """
    indices = set()
    parts = selection.split(',')

    for part in parts:
        part = part.strip()
        if not part:
            continue

        if '-' in part:
            # Handle range like "1-3"
            range_parts = part.split('-')
            if len(range_parts) != 2:
                raise ValueError(f"Invalid range: {part}")
            start = int(range_parts[0].strip())
            end = int(range_parts[1].strip())
            if start > end:
                start, end = end, start
            for i in range(start, end + 1):
                if 1 <= i <= max_index:
                    indices.add(i - 1)  # Convert to 0-based
        else:
            # Handle single number
            num = int(part)
            if 1 <= num <= max_index:
                indices.add(num - 1)  # Convert to 0-based

    return sorted(indices)


def select_packages_interactively(
    packages: List[UpgradePackageInfo],
    console: Console
) -> List[UpgradePackageInfo]:
    """Allow user to interactively select which packages to upgrade.

    :param packages: Available packages to choose from
    :param console: Rich console for output
    :returns: Selected packages
    """
    console.print("\n[bold]Select packages to upgrade:[/bold]")
    console.print("[dim](Enter numbers, ranges, or 'all'. Examples: 1,3,5 or 1-3 or 1-3,5)[/dim]\n")

    for idx, pkg in enumerate(packages, 1):
        console.print(f"  {idx}. {pkg.name}: {pkg.version} -> {pkg.latest_version}")

    console.print()
    selection = Prompt.ask("Selection", default="all")

    if selection.lower() == "all":
        selected = packages
    else:
        try:
            indices = _parse_selection(selection, len(packages))
            selected = [packages[i] for i in indices]

            if not selected:
                console.print("[yellow]No valid packages selected, using all packages.[/yellow]")
                selected = packages
        except (ValueError, IndexError):
            console.print("[yellow]Invalid selection, using all packages.[/yellow]")
            selected = packages

    # Show confirmation table with selected packages highlighted
    selected_names = {pkg.name for pkg in selected}
    num_selected = len(selected)

    console.print()
    table = Table(title=f"[bold]{num_selected} Package(s) Selected for Upgrade[/bold]")
    table.add_column("", style="bold green", no_wrap=True, width=3)
    table.add_column("Package", style="cyan", no_wrap=True)
    table.add_column("Current", style="magenta")
    table.add_column("Latest", style="green")

    for pkg in packages:
        is_selected = pkg.name in selected_names
        marker = "[green]\u2713[/green]" if is_selected else ""
        style = "" if is_selected else "dim"
        table.add_row(
            marker,
            f"[{style}]{pkg.name}[/{style}]" if style else pkg.name,
            f"[{style}]{pkg.version}[/{style}]" if style else str(pkg.version),
            f"[{style}]{pkg.latest_version}[/{style}]" if style else str(pkg.latest_version),
        )

    console.print(table)

    # Ask for confirmation
    if not Confirm.ask("\nProceed with upgrade?", default=True):
        console.print("[yellow]Upgrade cancelled.[/yellow]")
        return []

    return selected


def extract_env_short_name(python_path: str, existing_names: Optional[set] = None) -> str:
    """Extract a short environment name from a Python interpreter path.

    :param python_path: Full path to Python interpreter
    :param existing_names: Set of already-used names for collision handling
    :returns: Short name (e.g., "main" from ".../envs/main/bin/python")
    """
    parts = Path(python_path).parts

    # Look for bin/ or Scripts/ and take the parent directory name
    # Skip system directories like /usr, /opt, etc.
    name = None
    for i, part in enumerate(parts):
        if part in ("bin", "Scripts") and i > 0:
            parent = parts[i - 1]
            # Skip system directories - use the executable name instead
            if parent not in ("usr", "opt", "local"):
                name = parent
                break

    # If no suitable parent found, use the executable name
    if name is None:
        name = Path(python_path).name

    if existing_names is None:
        return name

    if name not in existing_names:
        return name

    # Handle collisions
    counter = 2
    while f"{name}-{counter}" in existing_names:
        counter += 1
    return f"{name}-{counter}"


def print_env_legend(
    env_names: Dict[str, str],
    console: Optional[Console] = None,
) -> None:
    """Print a legend mapping short environment names to full paths.

    :param env_names: Dict mapping short name to full path
    :param console: Optional Rich console
    """
    if console is None:
        console = Console()

    max_name_len = max(len(name) for name in env_names)
    console.print("[dim]Environments:[/dim]")
    for name, path in env_names.items():
        console.print(f"  [dim][cyan]{name:<{max_name_len}}[/cyan] = {path}[/dim]")
