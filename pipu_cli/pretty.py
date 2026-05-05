"""Pretty printing functions for pipu CLI."""

from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol, Tuple, TypeVar

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich.prompt import Confirm, Prompt

from packaging.version import Version

from pipu_cli.package_management import (
    UpgradePackageInfo,
    UpgradedPackage,
    BlockedPackageInfo,
    InstalledResult,
    UninstalledResult,
    DepNode,
    DepReport,
    DepProblem,
    EnvReport,
)
from pipu_cli.ui import CHECKMARK, CROSS, STYLES


_SUCCESS = STYLES["success"]
_FAILURE = STYLES["failure"]

_CHECK_MARKUP = f"[{_SUCCESS}]{CHECKMARK}[/{_SUCCESS}]"
_CROSS_MARKUP = f"[{_FAILURE}]{CROSS}[/{_FAILURE}]"


class _NamedRecord(Protocol):
    """Structural protocol for per-package matrix records."""

    @property
    def name(self) -> str: ...


_R = TypeVar("_R", bound=_NamedRecord)


def _build_package_matrix(
    per_env: Dict[str, List[_R]],
    *, key: Callable[[_R], str] = lambda r: r.name,
) -> Tuple[List[str], List[str], Dict[Tuple[str, str], _R]]:
    """Return ``(package_rows, env_columns, cell_lookup)`` for a group matrix.

    :param per_env: Map of env short-name -> list of per-package records
        (e.g. ``UpgradedPackage``, ``InstalledResult``, ``UninstalledResult``).
    :param key: Callable extracting the row key from a package-level record.
        Defaults to reading ``record.name``.
    :returns: ``(sorted_row_keys, env_order, {(row_key, env_name): record})``.
    """
    env_order = list(per_env.keys())
    cells: Dict[Tuple[str, str], _R] = {}
    row_keys: set = set()
    for env, records in per_env.items():
        for pkg in records:
            k = key(pkg)
            row_keys.add(k)
            cells[(k, env)] = pkg
    return sorted(row_keys), env_order, cells


def _collect_error_details(
    per_env: Dict[str, List[_R]],
    *, failed: Callable[[_R], bool],
) -> List[Tuple[str, str, str]]:
    """Collect ``(package, env, reason)`` tuples for every failed per-env record.

    :param per_env: Map of env short-name -> list of per-package records.
    :param failed: Predicate selecting failed records that also carry a
        ``failure_reason`` attribute.
    :returns: List of ``(package_name, env_name, failure_reason)`` tuples
        in iteration order.
    """
    details: List[Tuple[str, str, str]] = []
    for env, records in per_env.items():
        for pkg in records:
            reason = getattr(pkg, "failure_reason", None)
            if failed(pkg) and reason:
                details.append((pkg.name, env, reason))
    return details


def _format_error_summary(reason: str, *, max_len: int = 80) -> str:
    """Render a stable single-line error summary for a table cell.

    :param reason: Full failure reason text from pip output.
    :param max_len: Truncation width. The prior 30 vs 80 mismatch across
        matrix renderers is resolved by using 80 everywhere.
    :returns: A single-line summary, truncated with an ellipsis if needed.
    """
    return _extract_error_summary(reason, max_len=max_len)


def _print_error_details(
    console: Console,
    details: List[Tuple[str, str, str]],
) -> None:
    """Print the multi-line error detail block following a failed matrix.

    :param console: Rich console to print to.
    :param details: Tuples of ``(package, env, reason)`` produced by
        :func:`_collect_error_details`.
    """
    for pkg_name, env_name, reason in details:
        console.print(f"\n[bold {_FAILURE}]{pkg_name}[/bold {_FAILURE}] ({env_name}) error details:")
        console.print(f"[dim]{reason}[/dim]")


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

    console.print(f"\n[bold {_SUCCESS}]Upgraded {len(successful)} package(s)[/bold {_SUCCESS}]")

    table = Table()
    table.add_column("Package", style="cyan", no_wrap=True)
    table.add_column("Result", no_wrap=True)

    for pkg in results:
        if pkg.upgraded:
            table.add_row(pkg.name, f"{_CHECK_MARKUP} {pkg.previous_version} -> [{_SUCCESS}]{pkg.version}[/{_SUCCESS}]")
        else:
            reason = _format_error_summary(pkg.failure_reason or "failed")
            table.add_row(pkg.name, f"{_CROSS_MARKUP} [{_FAILURE}]{reason}[/{_FAILURE}]")

    console.print(table)

    if failed and verbose:
        for pkg in failed:
            if pkg.failure_reason:
                console.print(f"\n[bold {_FAILURE}]{pkg.name}[/bold {_FAILURE}] error details:")
                console.print(f"[dim]{pkg.failure_reason}[/dim]")

    if failed:
        console.print(f"\n[bold]Summary:[/bold] {len(successful)}/{len(results)} packages upgraded successfully")
    else:
        console.print(f"\n[bold {_SUCCESS}]All packages upgraded successfully![/bold {_SUCCESS}]")


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
        console.print(f"\n[bold {_SUCCESS}]Successfully installed/updated {len(successful)} package(s):[/bold {_SUCCESS}]")
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
        console.print(f"[bold {_SUCCESS}]All packages installed successfully![/bold {_SUCCESS}]")


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
        console.print(f"\n[bold {_SUCCESS}]Successfully uninstalled {len(actually_removed)} package(s):[/bold {_SUCCESS}]")
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
        console.print(f"[bold {_SUCCESS}]All packages uninstalled successfully![/bold {_SUCCESS}]")


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
        marker = _CHECK_MARKUP if is_selected else ""
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

    row_keys, _, cells = _build_package_matrix(env_upgrades)

    if not row_keys:
        console.print("[yellow]No packages to upgrade.[/yellow]")
        return

    env_order = list(env_names.keys())

    table = Table(title=f"[bold]{len(row_keys)} Package(s) to Upgrade[/bold]")
    table.add_column("Package", style="cyan", no_wrap=True)
    for env_name in env_order:
        table.add_column(env_name, style="magenta", justify="center")

    for pkg_name in row_keys:
        row = [pkg_name]
        for env_name in env_order:
            entry = cells.get((pkg_name, env_name))
            if entry is not None:
                row.append(f"{entry.version} -> [{_SUCCESS}]{entry.latest_version}[/{_SUCCESS}]")
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

    row_keys, _, cells = _build_package_matrix(env_results)
    total_upgraded = sum(1 for pkg in cells.values() if pkg.upgraded)

    if not row_keys:
        return

    env_order = list(env_names.keys())
    num_envs = len(env_order)

    console.print(f"[bold {_SUCCESS}]Upgraded {total_upgraded} package(s) across {num_envs} environment(s)[/bold {_SUCCESS}]")

    table = Table()
    table.add_column("Package", style="cyan", no_wrap=True)
    for env_name in env_order:
        table.add_column(env_name, style="magenta", justify="center")

    failed_details = _collect_error_details(env_results, failed=lambda r: not r.upgraded)

    for pkg_name in row_keys:
        row = [pkg_name]
        for env_name in env_order:
            entry = cells.get((pkg_name, env_name))
            if entry is None:
                row.append("[dim]-[/dim]")
            elif entry.upgraded:
                row.append(f"{_CHECK_MARKUP} {entry.previous_version}->[{_SUCCESS}]{entry.version}[/{_SUCCESS}]")
            else:
                summary = _format_error_summary(entry.failure_reason or "failed")
                row.append(f"{_CROSS_MARKUP} [{_FAILURE}]{summary}[/{_FAILURE}]")
        table.add_row(*row)

    console.print(table)

    _print_error_details(console, failed_details)


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
    target_versions: Optional[Dict[str, Version]] = None,
    console: Optional[Console] = None,
) -> None:
    """Print a matrix showing install plan across environments.

    :param env_versions: Dict of env short name -> dict of canonical pkg name -> current version (or None).
    :param package_names: Original package spec strings.
    :param env_names: Dict of short name -> full path.
    :param upgrade: Whether -U flag will be used.
    :param target_versions: Optional original package spec -> latest
        resolved target version. Used for upgrade previews.
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
        target_version = target_versions.get(pkg_spec) if target_versions else None
        for env_name in env_order:
            cur_ver = env_versions.get(env_name, {}).get(pkg_spec)
            if cur_ver is not None:
                if upgrade:
                    if target_version is None:
                        row.append(f"{cur_ver} -> [{_SUCCESS}]unknown[/{_SUCCESS}]")
                    elif target_version > cur_ver:
                        row.append(
                            f"{cur_ver} -> [{_SUCCESS}]{target_version}[/{_SUCCESS}] "
                            "[dim](latest)[/dim]"
                        )
                    elif target_version < cur_ver:
                        row.append(
                            f"{cur_ver} -> [{_SUCCESS}]{target_version}[/{_SUCCESS}] "
                            "[dim](target)[/dim]"
                        )
                    else:
                        row.append(
                            f"{cur_ver} [dim](latest: {target_version})[/dim]"
                        )
                else:
                    row.append(f"[dim]{cur_ver} (installed)[/dim]")
            else:
                if upgrade and target_version is not None:
                    row.append(
                        f"[{_SUCCESS}]{target_version}[/{_SUCCESS}] "
                        "[dim](new, latest)[/dim]"
                    )
                else:
                    row.append(f"[{_SUCCESS}]new[/{_SUCCESS}]")
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

    row_keys, _, cells = _build_package_matrix(env_results)
    total_installed = sum(1 for pkg in cells.values() if pkg.installed)

    if not row_keys:
        return

    env_order = list(env_names.keys())
    num_envs = len(env_order)

    console.print(f"[bold {_SUCCESS}]Installed {total_installed} package(s) across {num_envs} environment(s)[/bold {_SUCCESS}]")

    table = Table()
    table.add_column("Package", style="cyan", no_wrap=True)
    for env_name in env_order:
        table.add_column(env_name, style="magenta", justify="center")

    failed_details = _collect_error_details(env_results, failed=lambda r: not r.installed)

    for pkg_name in row_keys:
        row = [pkg_name]
        for env_name in env_order:
            entry = cells.get((pkg_name, env_name))
            if entry is None:
                row.append("[dim]-[/dim]")
            elif entry.installed:
                if entry.previous_version is None:
                    row.append(f"{_CHECK_MARKUP} [{_SUCCESS}]{entry.version}[/{_SUCCESS}] (new)")
                elif entry.version > entry.previous_version:
                    row.append(f"{_CHECK_MARKUP} {entry.previous_version}->[{_SUCCESS}]{entry.version}[/{_SUCCESS}]")
                else:
                    row.append(f"{_CHECK_MARKUP} {entry.version}")
            else:
                summary = _format_error_summary(entry.failure_reason or "failed")
                row.append(f"{_CROSS_MARKUP} [{_FAILURE}]{summary}[/{_FAILURE}]")
        table.add_row(*row)

    console.print(table)

    _print_error_details(console, failed_details)


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
                row.append(f"[{_FAILURE}]{cur_ver}[/{_FAILURE}]")
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

    row_keys, _, cells = _build_package_matrix(env_results)
    total_uninstalled = sum(1 for pkg in cells.values() if pkg.uninstalled)

    if not row_keys:
        return

    env_order = list(env_names.keys())
    num_envs = len(env_order)

    console.print(f"[bold {_SUCCESS}]Uninstalled {total_uninstalled} package(s) across {num_envs} environment(s)[/bold {_SUCCESS}]")

    table = Table()
    table.add_column("Package", style="cyan", no_wrap=True)
    for env_name in env_order:
        table.add_column(env_name, style="magenta", justify="center")

    failed_details = _collect_error_details(env_results, failed=lambda r: not r.uninstalled)

    for pkg_name in row_keys:
        row = [pkg_name]
        for env_name in env_order:
            entry = cells.get((pkg_name, env_name))
            if entry is None:
                row.append("[dim]-[/dim]")
            elif entry.uninstalled and entry.already_absent:
                row.append("[dim]-[/dim]")
            elif entry.uninstalled:
                ver = f" ({entry.previous_version})" if entry.previous_version else ""
                row.append(f"{_CHECK_MARKUP} removed{ver}")
            else:
                summary = _format_error_summary(entry.failure_reason or "failed")
                row.append(f"{_CROSS_MARKUP} [{_FAILURE}]{summary}[/{_FAILURE}]")
        table.add_row(*row)

    console.print(table)

    _print_error_details(console, failed_details)


def _format_dep_node_label(node: DepNode, problem_targets: set) -> str:
    """Render one tree-leaf label for the human view.

    :param node: The node to render.
    :param problem_targets: Set of canonical names that appear in some
        :class:`DepProblem`; used to append the ``✗`` marker.
    :returns: A Rich markup string.
    """
    edge = node.edge
    name = edge.name
    if node.is_cycle:
        return f"[{_SUCCESS}]{name}[/{_SUCCESS}] (cycle)"
    if edge.installed_version is None:
        body = f"[{_FAILURE}]{name}[/{_FAILURE}] (not installed)"
        if edge.specifier:
            body += f"  [dim]{edge.specifier}[/dim]"
        return body
    style = _FAILURE if name in problem_targets else _SUCCESS
    version_str = f"[{style}]{name}[/{style}] [dim]{edge.installed_version}[/dim]"
    if edge.specifier:
        version_str += f"  [dim]{edge.specifier}[/dim]"
    if edge.is_editable and edge.editable_location:
        version_str += "  [dim](editable)[/dim]"
    if name in problem_targets:
        version_str += f"  {_CROSS_MARKUP}"
    return version_str


def _attach_dep_children(rich_branch: Tree, nodes: List[DepNode], problem_targets: set) -> None:
    """Recursively attach :class:`DepNode` children to a Rich Tree branch."""
    for node in nodes:
        child = rich_branch.add(_format_dep_node_label(node, problem_targets))
        if node.children:
            _attach_dep_children(child, node.children, problem_targets)


def print_dep_report(console: Console, report: DepReport) -> None:
    """Render a :class:`DepReport` to a Rich console.

    Layout:

    1. Header panel with ``name version`` (and editable path if applicable).
    2. Rich ``Tree`` with two branches: ``Required by`` / ``Requires``.
    3. Optional failure-styled panel listing problems.

    :param console: Rich console to render into.
    :param report: The report to render.
    """
    pkg = report.package

    header_lines = [f"[bold cyan]{pkg.name}[/bold cyan] [dim]{pkg.version}[/dim]"]
    if pkg.is_editable and pkg.editable_location:
        style = _FAILURE if any(
            p.kind == "broken-editable" and p.package == pkg.name for p in report.problems
        ) else "dim"
        header_lines.append(f"[{style}]editable: {pkg.editable_location}[/{style}]")
    console.print(Panel("\n".join(header_lines), expand=False, border_style="cyan"))

    problem_targets = {p.package for p in report.problems}

    root = Tree(f"[bold]{pkg.name}[/bold] [dim]{pkg.version}[/dim]")
    req_by_label = (
        f"[bold]Required by[/bold]  ({len(report.required_by)})"
        if report.required_by else "[bold]Required by[/bold]  (none)"
    )
    requires_label = (
        f"[bold]Requires[/bold]  ({len(report.requires)})"
        if report.requires else "[bold]Requires[/bold]  (none)"
    )
    req_by_branch = root.add(req_by_label)
    _attach_dep_children(req_by_branch, report.required_by, problem_targets)
    requires_branch = root.add(requires_label)
    _attach_dep_children(requires_branch, report.requires, problem_targets)

    console.print(root)

    if report.problems:
        lines = [f"{_CROSS_MARKUP} {p.detail}" for p in report.problems]
        console.print(Panel(
            "\n".join(lines),
            title=f"[bold {_FAILURE}]Problems ({len(report.problems)})[/bold {_FAILURE}]",
            border_style=_FAILURE,
            expand=False,
        ))


_KIND_LABEL = {
    "missing": "Missing",
    "violates": "Violates",
    "broken-editable": "Broken editable",
    "duplicate-install": "Duplicate install",
    "stale-metadata": "Stale metadata",
}

_KIND_ORDER = ("missing", "violates", "broken-editable", "duplicate-install", "stale-metadata")


def _summarize_counts(problems: List[DepProblem]) -> str:
    """Build the comma-separated summary line for the failure panel.

    :param problems: The problems to summarize.
    :returns: A string like ``"2 missing, 3 violates"`` listing only
        kinds with at least one entry, in :data:`_KIND_ORDER`.
    """
    counts: Dict[str, int] = {k: 0 for k in _KIND_ORDER}
    for p in problems:
        if p.kind in counts:
            counts[p.kind] += 1
    parts = [
        f"{counts[k]} {_KIND_LABEL[k].lower()}"
        for k in _KIND_ORDER if counts[k] > 0
    ]
    return ", ".join(parts)


def _short_detail(problem: DepProblem) -> str:
    """Short form of ``DepProblem.detail`` for tree leaves.

    Strips the redundant ``{package}`` / ``{package} {version}`` prefix
    so the leaf reads cleanly next to the tree glyph.

    :param problem: The problem whose detail should be shortened.
    :returns: Detail string with the leading package name/version removed.
    """
    detail = problem.detail
    pkg = problem.package
    if problem.installed_version is not None:
        prefix = f"{pkg} {problem.installed_version} "
    else:
        prefix = f"{pkg} "
    if detail.startswith(prefix):
        return detail[len(prefix):]
    return detail


def _build_tree_by_problem(report: EnvReport) -> Tree:
    root = Tree("[bold]Environment check[/bold]")
    for kind in _KIND_ORDER:
        kind_problems = [p for p in report.problems if p.kind == kind]
        if not kind_problems:
            continue
        branch = root.add(f"[bold]{_KIND_LABEL[kind]}[/bold] ({len(kind_problems)})")
        for p in kind_problems:
            if p.installed_version is not None:
                leaf = f"{_CROSS_MARKUP} [{_FAILURE}]{p.package}[/{_FAILURE}] [dim]{p.installed_version}[/dim]  {_short_detail(p)}"
            else:
                leaf = f"{_CROSS_MARKUP} [{_FAILURE}]{p.package}[/{_FAILURE}]  {_short_detail(p)}"
            branch.add(leaf)
    return root


def _build_tree_by_package(report: EnvReport) -> Tree:
    """Render a tree grouped alphabetically by package name.

    :param report: The report to render.
    :returns: A :class:`Tree` with one branch per package, each
        branch containing one leaf per problem on that package. Leaves
        carry the kind code verbatim (``missing``, ``violates``, ...) so
        consumers don't have to read the full ``DepProblem.detail``.
    """
    root = Tree("[bold]Environment check[/bold]")
    by_pkg: Dict[str, List[DepProblem]] = {}
    for p in report.problems:
        by_pkg.setdefault(p.package, []).append(p)

    for pkg_name in sorted(by_pkg.keys()):
        pkg_problems = by_pkg[pkg_name]
        # Version label if any problem carries an installed_version.
        version_label = ""
        for p in pkg_problems:
            if p.installed_version is not None:
                version_label = f" [dim]{p.installed_version}[/dim]"
                break
        branch = root.add(f"[bold]{pkg_name}[/bold]{version_label}")
        for p in pkg_problems:
            leaf = f"{_CROSS_MARKUP} [dim]{p.kind}[/dim]  {_short_detail(p)}"
            branch.add(leaf)
    return root


def print_env_report(
    console: Console,
    report: EnvReport,
    *,
    group_by: str = "problem",
) -> None:
    """Render an :class:`EnvReport` to a Rich console.

    :param console: Rich console to render into.
    :param report: The report to render.
    :param group_by: ``"problem"`` (default) or ``"package"``.
    """
    n_problems = len(report.problems)
    title = f"Environment check — {report.package_count} packages, {n_problems} problem(s)"

    if n_problems == 0:
        console.print(Panel(
            f"[{_SUCCESS}]{_CHECK_MARKUP} No consistency problems found[/{_SUCCESS}]",
            title=title, border_style=_SUCCESS, expand=False,
        ))
        return

    summary_line = _summarize_counts(report.problems)
    console.print(Panel(
        f"{_CROSS_MARKUP} {summary_line}",
        title=title, border_style=_FAILURE, expand=False,
    ))

    if group_by == "package":
        tree = _build_tree_by_package(report)
    else:
        tree = _build_tree_by_problem(report)
    console.print(tree)
