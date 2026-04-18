"""Pretty printing functions for pipu CLI."""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.table import Table
from rich.prompt import Confirm, Prompt

from packaging.version import Version

from pipu_cli.package_management import UpgradePackageInfo, UpgradedPackage, BlockedPackageInfo, InstalledResult, UninstalledResult


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


def _extract_error_summary(reason: str, max_len: int = 80) -> str:
    """Extract the most informative error line from pip output for table display.

    Scans for known error patterns (CMake Error, ERROR:, etc.) and picks the
    first one that contains a useful diagnostic message.

    :param reason: Full failure reason text
    :param max_len: Maximum length for table cell
    :returns: Single-line error summary
    """
    lines = reason.strip().splitlines()
    if not lines:
        return "failed"

    # Look for the first line matching a known error pattern
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # CMake errors: the message is on the next non-empty line(s)
        if stripped.startswith("CMake Error"):
            for next_line in lines[i + 1:]:
                candidate = next_line.strip()
                if candidate and not candidate.startswith(("Call Stack", "--", "/")):
                    if len(candidate) > max_len:
                        return candidate[:max_len - 3] + "..."
                    return candidate
            continue

        # pip ERROR: lines
        if stripped.startswith("ERROR:"):
            msg = stripped[6:].strip()
            if msg:
                if len(msg) > max_len:
                    return msg[:max_len - 3] + "..."
                return msg
            continue

        # ModuleNotFoundError, ImportError, etc.
        if "Error:" in stripped and not stripped.startswith(("×", "╰")):
            if len(stripped) > max_len:
                return stripped[:max_len - 3] + "..."
            return stripped

    # Fallback: find last non-decorative line
    for line in reversed(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith(("×", "╰", "─", "note:")):
            if len(stripped) > max_len:
                return stripped[:max_len - 3] + "..."
            return stripped

    return "failed"


def print_upgrade_results(
    results: List[UpgradedPackage],
    console: Optional[Console] = None,
    verbose: bool = False,
) -> None:
    """Print a summary of package upgrade results using a Rich Table.

    :param results: List of UpgradedPackage objects with upgrade status
    :param console: Optional Rich console instance (creates new one if not provided)
    :param verbose: If True, print full error output for failed packages
    """
    if console is None:
        console = Console()

    if not results:
        console.print("[yellow]No packages were processed.[/yellow]")
        return

    successful = [pkg for pkg in results if pkg.upgraded]
    failed = [pkg for pkg in results if not pkg.upgraded]

    checkmark = "[green]\u2713[/green]"
    cross = "[red]\u2717[/red]"

    console.print(f"\n[bold green]Upgraded {len(successful)} package(s)[/bold green]")

    table = Table()
    table.add_column("Package", style="cyan", no_wrap=True)
    table.add_column("Result", no_wrap=True)

    for pkg in results:
        if pkg.upgraded:
            table.add_row(pkg.name, f"{checkmark} {pkg.previous_version} -> [green]{pkg.version}[/green]")
        else:
            reason = _extract_error_summary(pkg.failure_reason or "failed")
            table.add_row(pkg.name, f"{cross} [red]{reason}[/red]")

    console.print(table)

    if failed and verbose:
        for pkg in failed:
            if pkg.failure_reason:
                console.print(f"\n[bold red]{pkg.name}[/bold red] error details:")
                console.print(f"[dim]{pkg.failure_reason}[/dim]")

    if failed:
        console.print(f"\n[bold]Summary:[/bold] {len(successful)}/{len(results)} packages upgraded successfully")
    else:
        console.print("\n[bold green]All packages upgraded successfully![/bold green]")


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


def print_uninstall_results(
    results: List[UninstalledResult],
    console: Optional[Console] = None,
) -> None:
    """Print a summary of package uninstall results.

    :param results: List of UninstalledResult objects with uninstall status.
    :param console: Optional Rich console instance.
    """
    if console is None:
        console = Console()

    if not results:
        console.print("[yellow]No packages were processed.[/yellow]")
        return

    successful = [pkg for pkg in results if pkg.uninstalled]
    failed = [pkg for pkg in results if not pkg.uninstalled]

    already_absent = [pkg for pkg in successful if pkg.already_absent]
    actually_removed = [pkg for pkg in successful if not pkg.already_absent]

    if actually_removed:
        console.print(f"\n[bold green]Successfully uninstalled {len(actually_removed)} package(s):[/bold green]")
        for pkg in actually_removed:
            ver = f" ({pkg.previous_version})" if pkg.previous_version else ""
            console.print(f"  - {pkg.name}{ver}")

    if already_absent:
        console.print(f"\n[yellow]{len(already_absent)} package(s) already not installed:[/yellow]")
        for pkg in already_absent:
            console.print(f"  - {pkg.name}")

    if failed:
        console.print(f"\n[bold yellow]{len(failed)} package(s) could not be uninstalled:[/bold yellow]")
        table = Table(show_header=True, header_style="bold yellow")
        table.add_column("Package", style="cyan")
        table.add_column("Reason", style="dim")
        for pkg in failed:
            table.add_row(pkg.name, pkg.failure_reason or "Unknown failure")
        console.print(table)

    console.print()
    if failed:
        console.print(f"[bold]Summary:[/bold] {len(successful)}/{len(results)} packages uninstalled successfully")
    else:
        console.print("[bold green]All packages uninstalled successfully![/bold green]")


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


def print_group_upgrade_matrix(
    env_upgrades: Dict[str, List[UpgradePackageInfo]],
    env_names: Dict[str, str],
    console: Optional[Console] = None,
) -> None:
    """Print a matrix table showing upgrades across environments.

    :param env_upgrades: Dict mapping short env name to upgrade list
    :param env_names: Dict mapping short env name to full path
    :param console: Optional Rich console
    """
    if console is None:
        console = Console()

    # Collect all package names across environments
    all_packages: Dict[str, Dict[str, UpgradePackageInfo]] = {}
    for env_name, upgrades in env_upgrades.items():
        for pkg in upgrades:
            if pkg.name not in all_packages:
                all_packages[pkg.name] = {}
            all_packages[pkg.name][env_name] = pkg

    if not all_packages:
        console.print("[yellow]No packages to upgrade.[/yellow]")
        return

    env_order = list(env_names.keys())

    table = Table(title=f"[bold]{len(all_packages)} Package(s) to Upgrade[/bold]")
    table.add_column("Package", style="cyan", no_wrap=True)
    for env_name in env_order:
        table.add_column(env_name, style="magenta", justify="center")

    for pkg_name in sorted(all_packages.keys()):
        row = [pkg_name]
        for env_name in env_order:
            entry = all_packages[pkg_name].get(env_name)
            if entry:
                row.append(f"{entry.version} -> [green]{entry.latest_version}[/green]")
            else:
                row.append("[dim]-[/dim]")
        table.add_row(*row)

    console.print(table)


def print_group_results_matrix(
    env_results: Dict[str, List[UpgradedPackage]],
    env_names: Dict[str, str],
    console: Optional[Console] = None,
) -> None:
    """Print a matrix table showing upgrade results across environments.

    :param env_results: Dict mapping short env name to results list
    :param env_names: Dict mapping short env name to full path
    :param console: Optional Rich console
    """
    if console is None:
        console = Console()

    all_packages: Dict[str, Dict[str, UpgradedPackage]] = {}
    total_upgraded = 0
    for env_name, results in env_results.items():
        for pkg in results:
            if pkg.name not in all_packages:
                all_packages[pkg.name] = {}
            all_packages[pkg.name][env_name] = pkg
            if pkg.upgraded:
                total_upgraded += 1

    if not all_packages:
        return

    env_order = list(env_names.keys())
    num_envs = len(env_order)

    console.print(f"[bold green]Upgraded {total_upgraded} package(s) across {num_envs} environment(s)[/bold green]")

    table = Table()
    table.add_column("Package", style="cyan", no_wrap=True)
    for env_name in env_order:
        table.add_column(env_name, style="magenta", justify="center")

    checkmark = "[green]\u2713[/green]"
    cross = "[red]\u2717[/red]"

    failed_details: list[tuple[str, str, str]] = []

    for pkg_name in sorted(all_packages.keys()):
        row = [pkg_name]
        for env_name in env_order:
            entry = all_packages[pkg_name].get(env_name)
            if entry is None:
                row.append("[dim]-[/dim]")
            elif entry.upgraded:
                row.append(f"{checkmark} {entry.previous_version}->[green]{entry.version}[/green]")
            else:
                summary = _extract_error_summary(entry.failure_reason or "failed")
                row.append(f"{cross} [red]{summary}[/red]")
                if entry.failure_reason:
                    failed_details.append((pkg_name, env_name, entry.failure_reason))
        table.add_row(*row)

    console.print(table)

    if failed_details:
        for pkg_name, env_name, reason in failed_details:
            console.print(f"\n[bold red]{pkg_name}[/bold red] ({env_name}) error details:")
            console.print(f"[dim]{reason}[/dim]")


def print_group_blocked_table(
    blocked_packages: List[Tuple[str, BlockedPackageInfo]],
    console: Optional[Console] = None,
) -> None:
    """Print a table of blocked packages across group environments.

    :param blocked_packages: List of (env_short_name, BlockedPackageInfo) tuples
    :param console: Optional Rich console
    """
    if console is None:
        console = Console()

    if not blocked_packages:
        return

    console.print(f"\n[bold yellow]{len(blocked_packages)} package(s) blocked by constraints[/bold yellow]")

    table = Table()
    table.add_column("Package", style="cyan", no_wrap=True)
    table.add_column("Available", style="green")
    table.add_column("Env", style="magenta")
    table.add_column("Blocked By", style="red")

    for env_name, pkg in blocked_packages:
        table.add_row(
            pkg.name,
            str(pkg.latest_version),
            env_name,
            ", ".join(pkg.blocked_by),
        )

    console.print(table)


def print_group_install_matrix(
    env_versions: Dict[str, Dict[str, Optional[Version]]],
    package_names: List[str],
    env_names: Dict[str, str],
    upgrade: bool = True,
    console: Optional[Console] = None,
) -> None:
    """Print a matrix showing install plan across environments.

    :param env_versions: Dict of env short name -> dict of canonical pkg name -> current version (or None).
    :param package_names: Original package spec strings.
    :param env_names: Dict of short name -> full path.
    :param upgrade: Whether -U flag will be used.
    :param console: Optional Rich console.
    """
    if console is None:
        console = Console()

    env_order = list(env_names.keys())
    action = "Install/Upgrade" if upgrade else "Install"

    table = Table(title=f"[bold]{len(package_names)} Package(s) to {action}[/bold]")
    table.add_column("Package", style="cyan", no_wrap=True)
    for env_name in env_order:
        table.add_column(env_name, style="magenta", justify="center")

    for pkg_spec in package_names:
        row = [pkg_spec]
        for env_name in env_order:
            cur_ver = env_versions.get(env_name, {}).get(pkg_spec)
            if cur_ver is not None:
                if upgrade:
                    row.append(f"{cur_ver} -> [green]latest[/green]")
                else:
                    row.append(f"[dim]{cur_ver} (installed)[/dim]")
            else:
                row.append("[green]new[/green]")
        table.add_row(*row)

    console.print(table)


def print_group_install_results_matrix(
    env_results: Dict[str, List[InstalledResult]],
    env_names: Dict[str, str],
    console: Optional[Console] = None,
) -> None:
    """Print a results matrix for installs across environments.

    :param env_results: Dict of env short name -> list of InstalledResult.
    :param env_names: Dict of short name -> full path.
    :param console: Optional Rich console.
    """
    if console is None:
        console = Console()

    all_packages: Dict[str, Dict[str, InstalledResult]] = {}
    total_installed = 0
    for env_name, results in env_results.items():
        for pkg in results:
            if pkg.name not in all_packages:
                all_packages[pkg.name] = {}
            all_packages[pkg.name][env_name] = pkg
            if pkg.installed:
                total_installed += 1

    if not all_packages:
        return

    env_order = list(env_names.keys())
    num_envs = len(env_order)

    console.print(f"[bold green]Installed {total_installed} package(s) across {num_envs} environment(s)[/bold green]")

    table = Table()
    table.add_column("Package", style="cyan", no_wrap=True)
    for env_name in env_order:
        table.add_column(env_name, style="magenta", justify="center")

    checkmark = "[green]\u2713[/green]"
    cross = "[red]\u2717[/red]"

    failed_details: list[tuple[str, str, str]] = []

    for pkg_name in sorted(all_packages.keys()):
        row = [pkg_name]
        for env_name in env_order:
            entry = all_packages[pkg_name].get(env_name)
            if entry is None:
                row.append("[dim]-[/dim]")
            elif entry.installed:
                if entry.previous_version is None:
                    row.append(f"{checkmark} [green]{entry.version}[/green] (new)")
                elif entry.version > entry.previous_version:
                    row.append(f"{checkmark} {entry.previous_version}->[green]{entry.version}[/green]")
                else:
                    row.append(f"{checkmark} {entry.version}")
            else:
                summary = entry.failure_reason or "failed"
                if len(summary) > 30:
                    summary = summary[:27] + "..."
                row.append(f"{cross} [red]{summary}[/red]")
                if entry.failure_reason:
                    failed_details.append((pkg_name, env_name, entry.failure_reason))
        table.add_row(*row)

    console.print(table)

    if failed_details:
        for pkg_name, env_name, reason in failed_details:
            console.print(f"\n[bold red]{pkg_name}[/bold red] ({env_name}) error details:")
            console.print(f"[dim]{reason}[/dim]")


def print_group_uninstall_matrix(
    env_versions: Dict[str, Dict[str, Optional[Version]]],
    package_names: List[str],
    env_names: Dict[str, str],
    console: Optional[Console] = None,
) -> None:
    """Print a matrix showing uninstall plan across environments.

    :param env_versions: Dict of env short name -> dict of canonical pkg name -> current version (or None).
    :param package_names: Original package name strings.
    :param env_names: Dict of short name -> full path.
    :param console: Optional Rich console.
    """
    if console is None:
        console = Console()

    env_order = list(env_names.keys())

    table = Table(title=f"[bold]{len(package_names)} Package(s) to Uninstall[/bold]")
    table.add_column("Package", style="cyan", no_wrap=True)
    for env_name in env_order:
        table.add_column(env_name, style="magenta", justify="center")

    for pkg_name in package_names:
        row = [pkg_name]
        for env_name in env_order:
            cur_ver = env_versions.get(env_name, {}).get(pkg_name)
            if cur_ver is not None:
                row.append(f"[red]{cur_ver}[/red]")
            else:
                row.append("[dim]-[/dim]")
        table.add_row(*row)

    console.print(table)


def print_group_uninstall_results_matrix(
    env_results: Dict[str, List[UninstalledResult]],
    env_names: Dict[str, str],
    console: Optional[Console] = None,
) -> None:
    """Print a results matrix for uninstalls across environments.

    :param env_results: Dict of env short name -> list of UninstalledResult.
    :param env_names: Dict of short name -> full path.
    :param console: Optional Rich console.
    """
    if console is None:
        console = Console()

    all_packages: Dict[str, Dict[str, UninstalledResult]] = {}
    total_uninstalled = 0
    for env_name, results in env_results.items():
        for pkg in results:
            if pkg.name not in all_packages:
                all_packages[pkg.name] = {}
            all_packages[pkg.name][env_name] = pkg
            if pkg.uninstalled:
                total_uninstalled += 1

    if not all_packages:
        return

    env_order = list(env_names.keys())
    num_envs = len(env_order)

    console.print(f"[bold green]Uninstalled {total_uninstalled} package(s) across {num_envs} environment(s)[/bold green]")

    table = Table()
    table.add_column("Package", style="cyan", no_wrap=True)
    for env_name in env_order:
        table.add_column(env_name, style="magenta", justify="center")

    checkmark = "[green]\u2713[/green]"
    cross = "[red]\u2717[/red]"

    failed_details: list[tuple[str, str, str]] = []

    for pkg_name in sorted(all_packages.keys()):
        row = [pkg_name]
        for env_name in env_order:
            entry = all_packages[pkg_name].get(env_name)
            if entry is None:
                row.append("[dim]-[/dim]")
            elif entry.uninstalled and entry.already_absent:
                row.append("[dim]-[/dim]")
            elif entry.uninstalled:
                ver = f" ({entry.previous_version})" if entry.previous_version else ""
                row.append(f"{checkmark} removed{ver}")
            else:
                summary = entry.failure_reason or "failed"
                if len(summary) > 30:
                    summary = summary[:27] + "..."
                row.append(f"{cross} [red]{summary}[/red]")
                if entry.failure_reason:
                    failed_details.append((pkg_name, env_name, entry.failure_reason))
        table.add_row(*row)

    console.print(table)

    if failed_details:
        for pkg_name, env_name, reason in failed_details:
            console.print(f"\n[bold red]{pkg_name}[/bold red] ({env_name}) error details:")
            console.print(f"[dim]{reason}[/dim]")
