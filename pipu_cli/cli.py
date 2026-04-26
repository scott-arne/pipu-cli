"""CLI interface for pipu using rich_click."""

import json
import logging
import multiprocessing as mp
import sys
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Dict, List, Optional

import rich_click as click
from click.core import ParameterSource
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from pipu_cli._group_runner import GroupContext, prepare_group, run_per_env_parallel
from pipu_cli._subprocess import InterruptToken
from pipu_cli.package_management import (
    BlockedPackageInfo,
    Package,
    build_dep_report,
    build_env_report,
    inspect_installed_packages,
    get_latest_versions,
    get_latest_versions_parallel,
    parse_package_spec,
    PackageNotInstalledError,
    resolve_upgradable_packages,
    resolve_upgradable_packages_with_reasons,
    reinstall_editable_packages,
    run_pip_install,
    run_pip_uninstall,
)
from packaging.utils import canonicalize_name
from packaging.version import Version, InvalidVersion
from pipu_cli.pretty import (
    print_dep_report,
    print_env_report,
    print_upgradable_packages_table,
    print_upgrade_results,
    print_install_results,
    print_uninstall_results,
    print_blocked_packages_table,
    ConsoleStream,
    select_packages_interactively,
)
from pipu_cli.output import (
    build_install_payload,
    build_uninstall_payload,
    build_upgrade_payload,
    dep_report_to_json,
    env_report_to_json,
    package_to_dict,
)
from pipu_cli._options import (
    cache_ttl_option,
    debug_option,
    exclude_option,
    fix_option,
    group_option,
    interactive_option,
    no_cache_option,
    no_check_option,
    output_option,
    parallel_option,
    pre_option,
    yes_option,
)
from pipu_cli.ui import UpgradeUI
from pipu_cli.download import download_packages, install_from_local
from pipu_cli.config_file import load_config, get_config_value
from pipu_cli.groups import (
    add_environment,
    remove_environment,
    delete_group,
    list_groups,
    get_group,
    validate_python_path,
)
from pipu_cli.config import DEFAULT_CACHE_TTL, DEFAULT_CHECK_AFTER_CHANGES
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
click.rich_click.OPTION_GROUPS = {
    "pipu check": [
        {"name": "Fix options", "options": ["--fix", "--interactive"]},
        {"name": "Output options", "options": ["--by", "--output", "--debug"]},
        {"name": "Environments", "options": ["--group"]},
    ],
}


@click.group(invoke_without_command=True)
@click.version_option(package_name="pipu-cli", message="%(version)s")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """
    [bold cyan]pipu[/bold cyan] - A cute Python package updater

    Automatically checks for package updates and upgrades them with proper
    constraint resolution.

    Running [cyan]pipu[/cyan] with no subcommand defaults to [cyan]pipu upgrade[/cyan].

    Run [cyan]pipu <command> --help[/cyan] for command-specific help.
    """
    # If no subcommand provided, default to upgrade
    if ctx.invoked_subcommand is None:
        ctx.invoke(upgrade)


def _apply_config_defaults(
    ctx: click.Context,
    config: dict,
    field_defaults: dict[str, Any],
) -> dict[str, Any]:
    """Resolve params that weren't explicitly set via CLI against config.

    Implements the "CLI > project > user config" precedence used by every
    ``@click.command`` that reads from :func:`pipu_cli.config_file.load_config`:
    for each field whose Click :class:`ParameterSource` is ``DEFAULT``, the
    value is pulled from the merged config with the supplied baseline
    fallback. If the user set the flag on the command line, the CLI value
    wins and ``field_defaults[name]`` is untouched.

    :param ctx: Click context (for :meth:`click.Context.get_parameter_source`).
    :param config: Merged config dict from project + user config files.
    :param field_defaults: Mapping of param name -> baseline default used
        when neither CLI nor config provides a value.
    :returns: Dict keyed by the same param names, holding the resolved
        value for each.
    """
    resolved: dict[str, Any] = {}
    for name, default in field_defaults.items():
        if ctx.get_parameter_source(name) == ParameterSource.DEFAULT:
            resolved[name] = get_config_value(config, name, default)
        else:
            resolved[name] = ctx.params.get(name, default)
    return resolved


def _configure_debug_logging(console: Console, debug: bool, output: str) -> None:
    """Enable DEBUG-level logging when ``--debug`` is set in a human-mode run.

    Extracted from the five identical ``if debug and output != "json":``
    blocks in ``update`` / ``upgrade`` / ``outdated`` / ``install`` /
    ``uninstall``. Silences the noisier pip loggers and prints the
    "Debug mode enabled" banner that users rely on as a sanity check.

    :param console: Rich console the banner is written to.
    :param debug: Whether ``--debug`` was passed (CLI or via config).
    :param output: Output mode; debug output is suppressed in JSON mode
        so stdout stays machine-parseable.
    """
    if not (debug and output != "json"):
        return
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(message)s',
        handlers=[RichHandler(console=console, show_time=False, show_path=False, markup=True)]
    )
    logging.getLogger('pip._internal').setLevel(logging.WARNING)
    logging.getLogger('pip._vendor').setLevel(logging.WARNING)
    console.print("[dim]Debug mode enabled[/dim]\n")


class PostCheck:
    """Encapsulates the post-mutation consistency-check dispatch.

    Each mutating command constructs one :class:`PostCheck` from the
    resolved ``check_after_changes`` config and the per-invocation
    ``--no-check`` flag. Callers invoke :meth:`run` at each successful
    exit; when disabled the call is a no-op, so helpers don't need to
    branch on the flag themselves.

    :param console: Rich console for human-mode rendering.
    :param output: ``"human"`` or ``"json"``.
    :param enabled: Whether the check should run. Pre-computed as
        ``check_after_changes and not no_check`` so call-sites don't
        repeat the predicate.
    """

    def __init__(self, *, console: Console, output: str, enabled: bool) -> None:
        self.console = console
        self.output = output
        self.enabled = enabled

    @classmethod
    def from_flags(
        cls,
        *,
        console: Console,
        output: str,
        check_after_changes: bool,
        no_check: bool,
    ) -> "PostCheck":
        """Build a :class:`PostCheck` from raw config + flag values."""
        return cls(
            console=console,
            output=output,
            enabled=check_after_changes and not no_check,
        )

    def run(
        self,
        *,
        python_path: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run the auto-check against ``python_path`` if enabled.

        :param python_path: Env to check; ``None`` for local.
        :param result: JSON payload to merge ``post_check`` into. Created
            empty if the caller has no payload (human mode).
        :returns: The (possibly mutated) ``result`` dict.
        """
        payload = {} if result is None else result
        if not self.enabled:
            return payload

        report = build_env_report(python_path=python_path)
        if self.output == "json":
            payload["post_check"] = env_report_to_json(report)
        else:
            self.console.print()
            print_env_report(self.console, report, group_by="problem")
        return payload

    def run_per_env(
        self,
        envs: Dict[str, str],
        *,
        title_prefix: str = "Check",
    ) -> None:
        """Run the check against every env in ``envs`` (human mode).

        Each env gets a cyan Panel header naming the env before its
        report, matching the group-mode banner style used elsewhere in
        the CLI.

        :param envs: Ordered ``{short_name: python_path}`` mapping.
        :param title_prefix: Banner prefix; defaults to ``"Check"``.
        """
        if not self.enabled:
            return
        for env_name, env_path in envs.items():
            self.console.print()
            self.console.print(Panel(
                env_path, title=f"{title_prefix}: {env_name}",
                border_style="cyan", expand=False,
            ))
            self.run(python_path=env_path)


def _print_cache_diagnostics(
    console: Console,
    *,
    no_cache: bool,
    cache_enabled: bool,
    cache_ttl: Optional[int],
    output: str,
) -> bool:
    """Resolve cache TTL, check freshness, and render the "using cached data" line.

    Extracted from the duplicate blocks in ``upgrade`` (cli.py:781-786)
    and ``outdated`` (cli.py:1020-1025). The ``no_cache`` CLI flag is
    folded in at the ``cache_enabled`` site, so this helper only needs
    the already-combined ``cache_enabled`` value; ``no_cache`` is accepted
    for symmetry with call sites and to keep the decision local.

    :param console: Rich console used for the human-mode banner.
    :param no_cache: Whether ``--no-cache`` was passed.
    :param cache_enabled: Already-combined flag (config cache_enabled AND
        not ``--no-cache``).
    :param cache_ttl: CLI/config TTL override in seconds; ``None`` means
        "use :data:`pipu_cli.config.DEFAULT_CACHE_TTL`".
    :param output: Output mode; the banner is suppressed in JSON mode.
    :returns: ``True`` if the cache should be used for this run.
    """
    effective_cache_ttl = DEFAULT_CACHE_TTL if cache_ttl is None else cache_ttl
    use_cache = cache_enabled and not no_cache and is_cache_fresh(effective_cache_ttl)
    if use_cache and output != "json":
        cache_age = get_cache_age_seconds()
        console.print(f"[dim]Using cached data ({format_cache_age(cache_age)})[/dim]\n")
    return use_cache


@cli.command()
@click.pass_context
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
    "--parallel", "-p",
    type=int,
    default=min(4, mp.cpu_count()),
    help=f"Number of parallel requests for version checking (default: {min(4, mp.cpu_count())})"
)
@click.option(
    "--debug",
    is_flag=True,
    help="Enable debug logging"
)
@click.option(
    "--output", "-o",
    type=click.Choice(["human", "json"]),
    default="human",
    help="Output format (human-readable or json)"
)
def update(ctx: click.Context, timeout: int, pre: bool, parallel: int, debug: bool, output: str) -> None:
    """
    Refresh the package version cache.

    Fetches the latest version information from PyPI for all installed
    packages and stores it locally. This speeds up subsequent upgrade
    commands by avoiding repeated network requests.

    Constraint resolution is performed at upgrade time, not during update.

    \b
    Examples:
      pipu update              Update cache with defaults
      pipu update --parallel 4 Update with parallel requests
      pipu update --pre        Include pre-release versions
    """
    console = Console()

    # Load configuration file
    config = load_config()
    resolved = _apply_config_defaults(
        ctx,
        config,
        {
            'timeout': 10,
            'pre': False,
            'debug': False,
            'parallel': min(4, mp.cpu_count()),
            'output': 'human',
        },
    )
    timeout = resolved['timeout']
    pre = resolved['pre']
    debug = resolved['debug']
    parallel = resolved['parallel']
    output = resolved['output']

    _configure_debug_logging(console, debug, output)

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
        console.show_cursor(True)
        sys.exit(130)
    except Exception as e:
        if output == "json":
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"\n[bold red]Error:[/bold red] {e}")
        sys.exit(1)


# --- Helper functions for upgrade command ---

def _step1_inspect_packages(
    console: Console, output: str, timeout: int, debug: bool,
    total_steps: int = 5, python_path: Optional[str] = None,
    ui: Optional[UpgradeUI] = None,
) -> tuple[list, float]:
    """Step 1: Inspect installed packages."""
    if output != "json":
        if ui is not None:
            ui.start_phase("Inspecting installed packages...")
        else:
            console.print(f"[bold]Step 1/{total_steps}:[/bold] Inspecting installed packages...")

    step_start = time.time()
    if output != "json" and ui is None:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True
        ) as progress:
            progress.add_task("Loading packages...", total=None)
            installed_packages = inspect_installed_packages(timeout=timeout, python_path=python_path)
    else:
        installed_packages = inspect_installed_packages(timeout=timeout, python_path=python_path)
    step_time = time.time() - step_start

    if output != "json":
        if ui is not None:
            ui.complete_phase(f"Found {len(installed_packages)} installed packages")
        else:
            console.print(f"  Found {len(installed_packages)} installed packages")
        if debug:
            console.print(f"  [dim]Time: {step_time:.2f}s[/dim]")

    return installed_packages, step_time


def _step2_get_latest_versions(
    console: Console, output: str, debug: bool,
    installed_packages: list, use_cache: bool, cache_enabled: bool,
    timeout: int, pre: bool, parallel: int, total_steps: int = 5,
    python_path: Optional[str] = None,
    ui: Optional[UpgradeUI] = None,
) -> tuple[dict, float, bool]:
    """Step 2: Get latest versions from cache or network."""
    if output != "json":
        if ui is not None:
            ui.start_phase("Checking for updates...")
        elif use_cache:
            console.print(f"\n[bold]Step 2/{total_steps}:[/bold] Loading cached version data...")
        else:
            console.print(f"\n[bold]Step 2/{total_steps}:[/bold] Fetching latest versions from PyPI...")

    step_start = time.time()
    latest_versions: dict = {}
    cache_was_used = False

    if use_cache:
        cache_data = load_cache(python_path=python_path)
        if cache_data and cache_data.latest_versions:
            for installed_pkg in installed_packages:
                name_lower = installed_pkg.name.lower()
                if name_lower in cache_data.latest_versions:
                    cached_version = cache_data.latest_versions[name_lower]
                    try:
                        latest_ver = Version(cached_version)
                        if latest_ver > installed_pkg.version:
                            latest_pkg = Package(name=installed_pkg.name, version=latest_ver)
                            latest_versions[installed_pkg] = latest_pkg
                    except InvalidVersion:
                        pass
            cache_was_used = True
        else:
            use_cache = False

    if not use_cache:
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

        if cache_enabled:
            version_cache = build_version_cache(latest_versions)
            save_cache(version_cache, include_prereleases=pre, python_path=python_path)

    step_time = time.time() - step_start

    if output != "json":
        if ui is not None:
            ui.complete_phase(f"{len(latest_versions)} packages with newer versions available")
        else:
            console.print(f"  Found {len(latest_versions)} packages with newer versions available")
            if cache_was_used:
                console.print("  [dim](from cache)[/dim]")
        if debug:
            console.print(f"  [dim]Time: {step_time:.2f}s[/dim]")

    return latest_versions, step_time, cache_was_used


def _parse_excludes(exclude_tuple: tuple) -> str:
    """Flatten repeatable --exclude values into comma-separated string.

    :param exclude_tuple: Tuple of exclude values from Click multiple option
    :returns: Comma-separated string of package names
    """
    result: list[str] = []
    for item in exclude_tuple:
        result.extend(name.strip() for name in item.split(",") if name.strip())
    return ",".join(result)


def _step3_resolve_packages(
    console: Console, output: str, debug: bool,
    latest_versions: dict, installed_packages: list, show_blocked: bool,
    exclude: str, packages: tuple, total_steps: int = 5,
    ui: Optional[UpgradeUI] = None,
) -> tuple[list, list, dict, float]:
    """Step 3: Resolve upgradable packages and apply filters."""
    if output != "json":
        if ui is not None:
            ui.start_phase("Resolving dependency constraints...")
        else:
            console.print(f"\n[bold]Step 3/{total_steps}:[/bold] Resolving dependency constraints...")
    step_start = time.time()

    if show_blocked:
        upgradable_packages, blocked_packages = resolve_upgradable_packages_with_reasons(
            latest_versions, installed_packages
        )
    else:
        all_upgradable = resolve_upgradable_packages(latest_versions, installed_packages)
        upgradable_packages = [pkg for pkg in all_upgradable if pkg.upgradable]
        blocked_packages = []

    step_time = time.time() - step_start

    # Apply exclusions
    excluded_names = set()
    if exclude:
        excluded_names = {name.strip().lower() for name in exclude.split(',')}
        if debug and excluded_names:
            console.print(f"  [dim]Excluding: {', '.join(sorted(excluded_names))}[/dim]")

    can_upgrade = [pkg for pkg in upgradable_packages if pkg.name.lower() not in excluded_names]

    # Parse package specifications and filter
    package_constraints: dict = {}
    if packages:
        requested_packages = set()
        for spec in packages:
            parsed = parse_package_spec(spec)
            requested_packages.add(parsed.name)
            if parsed.constraint_str:
                package_constraints[parsed.name] = parsed.constraint_str

        can_upgrade = [pkg for pkg in can_upgrade if canonicalize_name(pkg.name) in requested_packages]

        if debug:
            console.print(f"  [dim]Filtering to: {', '.join(packages)}[/dim]")
            if package_constraints:
                console.print(f"  [dim]Version constraints: {package_constraints}[/dim]")

    if output != "json":
        if ui is not None:
            ui.complete_phase(f"{len(can_upgrade)} safe to upgrade")
        else:
            console.print(f"  {len(can_upgrade)} packages can be safely upgraded")
        if debug:
            console.print(f"  [dim]Time: {step_time:.2f}s[/dim]")

    return can_upgrade, blocked_packages, package_constraints, step_time


def _download_and_install_phase(
    console: Console, output: str,
    can_upgrade: list, package_constraints: dict,
    python_path: Optional[str] = None,
    ui: Optional[UpgradeUI] = None,
    debug: bool = False,
    parallel: int = 1,
) -> tuple[list, float]:
    """Download and install packages."""
    editable_packages = [pkg for pkg in can_upgrade if pkg.is_editable]
    non_editable_packages = [pkg for pkg in can_upgrade if not pkg.is_editable]

    step_start = time.time()

    # Save state for potential rollback
    from pipu_cli.rollback import save_state
    pre_upgrade_packages = [
        {"name": pkg.name, "version": str(pkg.version)}
        for pkg in can_upgrade
    ]
    description = "Pre-upgrade state"
    if python_path is not None:
        description = f"Pre-upgrade state ({python_path})"
    save_state(pre_upgrade_packages, description)

    results = []

    if non_editable_packages:
        # Build pinned specs
        specs = []
        for pkg in non_editable_packages:
            name_key = canonicalize_name(pkg.name)
            if name_key in package_constraints:
                specs.append(f"{pkg.name}{package_constraints[name_key]}")
            else:
                specs.append(f"{pkg.name}=={pkg.latest_version}")

        with tempfile.TemporaryDirectory(prefix="pipu-") as tmp_dir:
            dest_dir = Path(tmp_dir)

            # Download phase
            if ui and output != "json":
                tracker = ui.show_download_progress(specs)

                def on_download_start(spec: str) -> None:
                    tracker.start(spec)

                def on_download(spec: str, success: bool, error_msg: str) -> None:
                    if success:
                        tracker.complete(spec)
                    else:
                        tracker.fail(spec)

                try:
                    download_packages(
                        specs=specs, dest_dir=dest_dir,
                        python_path=python_path,
                        max_workers=parallel,
                        progress_callback=on_download,
                        start_callback=on_download_start,
                    )
                except RuntimeError:
                    pass
                finally:
                    tracker.finish()
            else:
                download_packages(
                    specs=specs, dest_dir=dest_dir,
                    python_path=python_path,
                    max_workers=parallel,
                )

            # Install phase
            if ui and output != "json":
                install_tracker = ui.show_install_progress(specs)

                def on_install(spec: str) -> None:
                    install_tracker.complete(spec)

                regular_results = install_from_local(
                    dest_dir=dest_dir, specs=specs,
                    python_path=python_path,
                    progress_callback=on_install,
                )
                install_tracker.finish()
            else:
                regular_results = install_from_local(
                    dest_dir=dest_dir, specs=specs,
                    python_path=python_path,
                )

            results.extend(regular_results)

    # Editable packages bypass download pipeline
    if editable_packages:
        if ui and output != "json":
            ui.start_phase(f"Reinstalling {len(editable_packages)} editable package(s)...")
        stream = None
        if debug:
            stream = ConsoleStream(console)
        editable_results = reinstall_editable_packages(
            editable_packages, output_stream=stream, timeout=300, python_path=python_path,
        )
        if ui and output != "json":
            ui.complete_phase(f"{len([r for r in editable_results if r.upgraded])} reinstalled")
        results.extend(editable_results)

    step_time = time.time() - step_start
    return results, step_time


@cli.command()
@click.pass_context
@click.argument('packages', nargs=-1)
@click.option(
    "--timeout",
    type=int,
    default=10,
    help="Network timeout in seconds for package queries"
)
@pre_option
@yes_option("Automatically confirm upgrade without prompting")
@debug_option
@click.option(
    "--dry-run",
    is_flag=True,
    hidden=True,
    help="Show what would be upgraded without actually upgrading"
)
@exclude_option
@click.option(
    "--show-blocked", "-b",
    is_flag=True,
    help="Show packages that cannot be upgraded and why"
)
@output_option
@click.option(
    "--update-requirements",
    type=click.Path(exists=True),
    default=None,
    help="Update the specified requirements.txt file with new versions"
)
@parallel_option
@click.option(
    "--interactive", "-i",
    is_flag=True,
    hidden=True,
    help="Interactively select packages to upgrade"
)
@no_cache_option
@cache_ttl_option
@no_check_option("upgrade")
@group_option("Run upgrade across all environments in a named group")
def upgrade(ctx: click.Context, packages: tuple[str, ...], timeout: int, pre: bool, yes: bool, debug: bool, dry_run: bool,
            exclude: tuple, show_blocked: bool, output: str, update_requirements: Optional[str],
            parallel: int, interactive: bool, no_cache: bool, cache_ttl: Optional[int],
            no_check: bool, group_name: Optional[str] = None) -> None:
    """
    Upgrade installed packages.

    By default, upgrades all packages that have newer versions available.
    Optionally specify PACKAGES to upgrade only those packages.

    Uses cached version data if available and fresh. Run [cyan]pipu update[/cyan]
    to refresh the cache manually.

    \b
    Examples:
      pipu upgrade                    Upgrade all packages
      pipu upgrade requests numpy     Upgrade specific packages
      pipu upgrade --no-cache         Force fresh version check
      pipu upgrade -e numpy -e pandas Exclude packages
    """
    console = Console()

    # Load configuration file
    config = load_config()

    # Apply config file values only when CLI option is at its default
    resolved = _apply_config_defaults(
        ctx,
        config,
        {
            'timeout': 10,
            'pre': False,
            'yes': False,
            'debug': False,
            'dry_run': False,
            'show_blocked': False,
            'output': 'human',
            'cache_ttl': DEFAULT_CACHE_TTL,
            'check_after_changes': DEFAULT_CHECK_AFTER_CHANGES,
        },
    )
    timeout = resolved['timeout']
    pre = resolved['pre']
    yes = resolved['yes']
    debug = resolved['debug']
    dry_run = resolved['dry_run']
    show_blocked = resolved['show_blocked']
    output = resolved['output']
    cache_ttl = resolved['cache_ttl']

    post_check = PostCheck.from_flags(
        console=console, output=output,
        check_after_changes=resolved['check_after_changes'],
        no_check=no_check,
    )

    # exclude has its own parsing pathway
    if ctx.get_parameter_source('exclude') == ParameterSource.DEFAULT:
        exclude_list = get_config_value(config, 'exclude', [])
        exclude_str = ','.join(exclude_list) if exclude_list else ""
    else:
        exclude_str = _parse_excludes(exclude)

    # Check if caching is enabled
    cache_enabled = get_config_value(config, 'cache_enabled', True) and not no_cache

    # Group mode: dispatch to group execution loop
    if group_name is not None:
        _run_group_upgrade(
            group_name=group_name, console=console, output=output,
            timeout=timeout, pre=pre, yes=yes, debug=debug,
            exclude_str=exclude_str, show_blocked=show_blocked,
            parallel=parallel, no_cache=no_cache, cache_ttl=cache_ttl,
            packages=packages, package_constraints={},
            update_requirements=update_requirements,
            cache_enabled=cache_enabled,
            post_check=post_check,
        )
        return

    # Interactive mode deprecation warning
    if interactive:
        if output != "json":
            console.print("[yellow]Warning: --interactive/-i is deprecated. "
                         "Use positional args instead: pipu upgrade requests numpy[/yellow]\n")

    # Interactive mode only works in human output mode
    if interactive and output == "json":
        console.print("[yellow]Warning: --interactive is not compatible with --output json. Ignoring --interactive.[/yellow]")
        interactive = False

    # Configure logging
    _configure_debug_logging(console, debug, output)
    if debug and output != "json":
        # Show cache diagnostics
        info = get_cache_info()
        console.print("[dim]Cache diagnostics:[/dim]")
        console.print(f"  [dim]Cache path: {info['path']}[/dim]")
        console.print(f"  [dim]Environment ID: {info['environment_id']}[/dim]")
        console.print(f"  [dim]Python: {info['python_executable']}[/dim]")
        if info['exists']:
            console.print(f"  [dim]Packages cached: {info.get('package_count', 0)}[/dim]")
            console.print(f"  [dim]Cache age: {info.get('age_human', 'unknown')}[/dim]")
        else:
            console.print("  [dim]No cache exists[/dim]")
        console.print()

    ui = UpgradeUI(console) if output != "json" else None

    try:
        with (ui if ui is not None else nullcontext()):
            # Check cache freshness
            use_cache = _print_cache_diagnostics(
                console,
                no_cache=no_cache,
                cache_enabled=cache_enabled,
                cache_ttl=cache_ttl,
                output=output,
            )

            # Step 1: Inspect installed packages
            installed_packages, step1_time = _step1_inspect_packages(
                console, output, timeout, debug, ui=ui,
            )

            if not installed_packages:
                payload: Dict[str, Any] = {"error": "No packages found"}
                post_check.run(result=payload)
                if output == "json":
                    print(json.dumps(payload, indent=2))
                else:
                    console.print("[yellow]No packages found.[/yellow]")
                sys.exit(0)

            # Step 2: Get latest versions (from cache or network)
            latest_versions, step2_time, _ = _step2_get_latest_versions(
                console, output, debug, installed_packages, use_cache, cache_enabled,
                timeout, pre, parallel, ui=ui,
            )

            if not latest_versions:
                uptodate_payload: Dict[str, Any] = {"upgradable": [], "blocked": [], "results": [], "summary": {"total": 0, "upgraded": 0, "failed": 0}}
                post_check.run(result=uptodate_payload)
                if output == "json":
                    print(json.dumps(uptodate_payload, indent=2))
                else:
                    console.print("\n[bold green]All packages are up to date![/bold green]")
                sys.exit(0)

            # Step 3: Resolve upgradable packages
            can_upgrade, blocked_packages, package_constraints, step3_time = _step3_resolve_packages(
                console, output, debug, latest_versions, installed_packages, show_blocked,
                exclude_str, packages, ui=ui,
            )

            if not can_upgrade:
                if output == "json":
                    blocked_payload = build_upgrade_payload(
                        upgradable=[],
                        blocked=blocked_packages if show_blocked else None
                    )
                    post_check.run(result=blocked_payload)
                    print(json.dumps(blocked_payload, indent=2))
                else:
                    post_check.run()
                    console.print("\n[yellow]No packages can be upgraded (all blocked by constraints).[/yellow]")
                    if show_blocked and blocked_packages:
                        console.print()
                        print_blocked_packages_table(blocked_packages, console=console)
                sys.exit(0)

            # Step 4: Display table and ask for confirmation
            if output == "json":
                if dry_run:
                    dryrun_payload = build_upgrade_payload(
                        upgradable=can_upgrade,
                        blocked=blocked_packages if show_blocked else None
                    )
                    post_check.run(result=dryrun_payload)
                    print(json.dumps(dryrun_payload, indent=2))
                    sys.exit(0)
            else:
                console.print()
                print_upgradable_packages_table(can_upgrade, console=console)

                if show_blocked and blocked_packages:
                    console.print()
                    print_blocked_packages_table(blocked_packages, console=console)

                if interactive:
                    can_upgrade = select_packages_interactively(can_upgrade, console)
                    if not can_upgrade:
                        post_check.run()
                        console.print("[yellow]No packages selected for upgrade.[/yellow]")
                        sys.exit(0)

                if dry_run:
                    post_check.run()
                    console.print("\n[bold cyan]Dry run complete.[/bold cyan] No packages were modified.")
                    sys.exit(0)

            # Skip confirmation if interactive mode (already confirmed) or --yes flag
            if not yes and not interactive and output != "json":
                console.print()
                confirm = click.confirm(f"Upgrade {len(can_upgrade)} package(s)?", default=True)
                if not confirm:
                    post_check.run()
                    console.print("[yellow]Upgrade cancelled.[/yellow]")
                    sys.exit(0)

            # Download and install packages
            results, install_time = _download_and_install_phase(
                console, output, can_upgrade, package_constraints, ui=ui, debug=debug,
                parallel=parallel,
            )

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
                results_payload = build_upgrade_payload(
                    upgradable=can_upgrade,
                    blocked=blocked_packages if show_blocked else None,
                    results=results
                )
                post_check.run(result=results_payload)
                print(json.dumps(results_payload, indent=2))
            else:
                print_upgrade_results(results, console=console, verbose=debug)

                if debug:
                    console.print(f"\n[dim]Install time: {install_time:.2f}s[/dim]")
                    total_time = step1_time + step2_time + step3_time + install_time
                    console.print(f"[dim]Total time: {total_time:.2f}s[/dim]")

                post_check.run()

            # Exit with appropriate code
            failed = [pkg for pkg in results if not pkg.upgraded]
            if failed:
                sys.exit(1)
            else:
                sys.exit(0)

    except KeyboardInterrupt:
        sys.exit(130)
    except click.Abort:
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}")
        sys.exit(1)


@cli.command()
@click.pass_context
@click.option("--timeout", type=int, default=10, help="Network timeout in seconds for package queries")
@pre_option
@debug_option
@exclude_option
@click.option("--show-blocked", "-b", is_flag=True, default=True,
              help="Show packages blocked by constraints (default: enabled)")
@output_option
@parallel_option
@no_cache_option
@cache_ttl_option
@group_option("Show outdated packages across all environments in a named group")
def outdated(ctx, timeout, pre, debug, exclude, show_blocked, output, parallel, no_cache, cache_ttl,
             group_name=None):
    """
    Show outdated packages.

    Displays packages that have newer versions available, along with any
    packages blocked by dependency constraints. Does not install anything.

    \b
    Examples:
      pipu outdated              Show all outdated packages
      pipu outdated --no-cache   Force fresh version check
      pipu outdated -o json      Machine-readable output
    """
    console = Console()

    # Load config and apply defaults (same pattern as upgrade)
    config = load_config()
    resolved = _apply_config_defaults(
        ctx,
        config,
        {
            'timeout': 10,
            'pre': False,
            'debug': False,
            'show_blocked': True,
            'output': 'human',
            'parallel': min(4, mp.cpu_count()),
            'cache_ttl': DEFAULT_CACHE_TTL,
        },
    )
    timeout = resolved['timeout']
    pre = resolved['pre']
    debug = resolved['debug']
    show_blocked = resolved['show_blocked']
    output = resolved['output']
    parallel = resolved['parallel']
    cache_ttl = resolved['cache_ttl']

    # Process excludes
    if ctx.get_parameter_source('exclude') == ParameterSource.DEFAULT:
        exclude_list = get_config_value(config, 'exclude', [])
        exclude_str = ','.join(exclude_list) if exclude_list else ""
    else:
        exclude_str = _parse_excludes(exclude)

    cache_enabled = get_config_value(config, 'cache_enabled', True) and not no_cache

    if group_name is not None:
        _run_group_outdated(
            group_name=group_name, console=console, output=output,
            timeout=timeout, pre=pre, debug=debug,
            exclude_str=exclude_str, show_blocked=show_blocked,
            parallel=parallel, no_cache=no_cache, cache_ttl=cache_ttl,
            cache_enabled=cache_enabled,
        )
        return

    _configure_debug_logging(console, debug, output)
    if debug and output != "json":
        # Show cache diagnostics
        info = get_cache_info()
        console.print("[dim]Cache diagnostics:[/dim]")
        console.print(f"  [dim]Cache path: {info['path']}[/dim]")
        console.print(f"  [dim]Environment ID: {info['environment_id']}[/dim]")
        console.print(f"  [dim]Python: {info['python_executable']}[/dim]")
        if info['exists']:
            console.print(f"  [dim]Packages cached: {info.get('package_count', 0)}[/dim]")
            console.print(f"  [dim]Cache age: {info.get('age_human', 'unknown')}[/dim]")
        else:
            console.print("  [dim]No cache exists[/dim]")
        console.print()

    try:
        use_cache = _print_cache_diagnostics(
            console,
            no_cache=no_cache,
            cache_enabled=cache_enabled,
            cache_ttl=cache_ttl,
            output=output,
        )

        # Step 1: Inspect
        installed_packages, step1_time = _step1_inspect_packages(console, output, timeout, debug, total_steps=3)
        if not installed_packages:
            if output == "json":
                print('{"upgradable": [], "blocked": [], "results": [], "summary": {"total": 0, "upgraded": 0, "failed": 0}}')
            else:
                console.print("[yellow]No packages found.[/yellow]")
            sys.exit(0)

        # Step 2: Get latest versions
        latest_versions, step2_time, _ = _step2_get_latest_versions(
            console, output, debug, installed_packages, use_cache, cache_enabled,
            timeout, pre, parallel, total_steps=3
        )

        if not latest_versions:
            if output == "json":
                print('{"upgradable": [], "blocked": [], "results": [], "summary": {"total": 0, "upgraded": 0, "failed": 0}}')
            else:
                console.print("\n[bold green]All packages are up to date![/bold green]")
            sys.exit(0)

        # Step 3: Resolve
        can_upgrade, blocked_packages, _, step3_time = _step3_resolve_packages(
            console, output, debug, latest_versions, installed_packages, show_blocked,
            exclude_str, (), total_steps=3
        )

        # Display results
        if output == "json":
            print(json.dumps(build_upgrade_payload(
                upgradable=can_upgrade,
                blocked=blocked_packages if show_blocked else None,
            ), indent=2))
        else:
            if can_upgrade:
                console.print("\n[bold]Packages with updates available:\n")
                print_upgradable_packages_table(can_upgrade, console=console)

            if not can_upgrade:
                console.print("\n[yellow]No packages can be upgraded (all blocked by constraints).[/yellow]")

            if show_blocked and blocked_packages:
                console.print()
                print_blocked_packages_table(blocked_packages, console=console)

            if debug:
                total_time = step1_time + step2_time + step3_time
                console.print(f"\n[dim]Total time: {total_time:.2f}s[/dim]")

        sys.exit(0)

    except KeyboardInterrupt:
        console.show_cursor(True)
        sys.exit(130)
    except Exception as e:
        if output == "json":
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"\n[bold red]Error:[/bold red] {e}")
        sys.exit(1)


@cli.command()
@click.pass_context
@click.argument("package", nargs=1)
@click.option("--depth", type=click.IntRange(min=0), default=1,
              help="Recursion depth (default 1; 0 = unlimited)")
@click.option("--check", is_flag=True,
              help="Exit non-zero if any problems are found")
@output_option
@debug_option
@group_option("Inspect PACKAGE in every environment of a named group")
def deps(ctx: click.Context, package: str, depth: int, check: bool,
         output: str, debug: bool, group_name: Optional[str]) -> None:
    """
    Inspect a package's required-by and requires relationships.

    Shows, for [cyan]PACKAGE[/cyan] installed in the current environment,
    the installed packages that [bold]require[/bold] it and the packages
    it [bold]requires[/bold], rendered as a tree. Missing dependencies,
    constraint violations, and broken editable installs are listed in a
    Problems panel.

    \b
    Examples:
      pipu deps requests              Direct required-by and requires
      pipu deps requests --depth 3    Recurse three hops on each side
      pipu deps requests --depth 0    Unlimited (with cycle detection)
      pipu deps requests --check      Exit non-zero if any problem found
      pipu deps requests -o json      Machine-readable output
    """
    console = Console()

    config = load_config()
    resolved = _apply_config_defaults(
        ctx,
        config,
        {
            "depth": 1,
            "check": False,
            "output": "human",
            "debug": False,
        },
    )
    depth = resolved["depth"]
    check = resolved["check"]
    output = resolved["output"]
    debug = resolved["debug"]

    _configure_debug_logging(console, debug, output)

    if group_name is not None:
        _run_group_deps(
            group_name=group_name, console=console, output=output,
            package=package, depth=depth, check=check,
        )
        return

    try:
        report = build_dep_report(package, depth=depth)
    except PackageNotInstalledError as e:
        if output == "json":
            print(json.dumps({"error": "package-not-installed", "package": e.name}, indent=2))
        else:
            console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if output == "json":
        print(json.dumps(dep_report_to_json(report, depth=depth), indent=2))
    else:
        print_dep_report(console, report)

    if check and report.problems:
        sys.exit(1)
    sys.exit(0)


def _run_group_deps(
    *,
    group_name: str,
    console: Console,
    output: str,
    package: str,
    depth: int,
    check: bool,
) -> None:
    """Execute ``pipu deps`` across every environment of a saved group.

    Renders each environment's report sequentially (like
    ``_run_group_outdated``) so per-env panels don't interleave. In JSON
    mode, aggregates per-env results into the group schema.

    :param group_name: Name of the group to iterate.
    :param console: Rich console for human-mode output.
    :param output: ``"human"`` or ``"json"``.
    :param package: User-supplied package name (same in every env).
    :param depth: Forwarded to :func:`build_dep_report` and the JSON payload.
    :param check: If ``True``, exit ``1`` when any env has problems.
    """
    group_ctx = prepare_group(group_name, console=console, output=output)

    any_problems = False
    per_env_json_payloads: List[tuple] = []  # [(env_name, dict)]

    for env_name, env_path in group_ctx.envs.items():
        if output != "json":
            console.print()
            console.print(Panel(env_path, title=f"Environment: {env_name}",
                                border_style="cyan", expand=False))
            console.print()

        try:
            report = build_dep_report(package, depth=depth, python_path=env_path)
        except PackageNotInstalledError as e:
            any_problems = True  # treat not-installed as a problem in --check
            if output == "json":
                per_env_json_payloads.append((env_name, {
                    "error": "package-not-installed", "package": e.name,
                }))
            else:
                console.print(f"[red]{e}[/red]")
            continue

        if output == "json":
            per_env_json_payloads.append((env_name, dep_report_to_json(report, depth=depth)))
        else:
            print_dep_report(console, report)

        if report.problems:
            any_problems = True

    if output == "json":
        payload = {
            "group": group_name,
            "environments": [
                {"env": env, "report": report_dict}
                for env, report_dict in per_env_json_payloads
            ],
        }
        print(json.dumps(payload, indent=2))

    if check and any_problems:
        sys.exit(1)
    sys.exit(0)


@cli.command()
@click.pass_context
@click.option("--by", type=click.Choice(["problem", "package"]), default="problem",
              help="Group problems by kind (default) or by package")
@fix_option
@interactive_option
@output_option
@debug_option
@group_option("Check every environment in a named group")
def check(ctx: click.Context, by: str, output: str, debug: bool,
          fix: bool, interactive: bool,
          group_name: Optional[str]) -> None:
    """
    Check an environment for consistency problems.

    Scans installed packages for missing dependencies, version constraint
    violations, broken editable installs, duplicate distributions, and
    orphaned metadata. Exits non-zero if any problems are found.

    Pass ``--fix`` to auto-apply remediations for stale-metadata
    (delete) and violates (install satisfying version).
    ``--interactive`` prompts before each action.

    \b
    Examples:
      pipu check              Scan current environment
      pipu check --by package Group findings by package
      pipu check -o json      Machine-readable output
      pipu check --fix        Auto-fix stale-metadata + violates
      pipu check --fix --interactive  Prompt per action
      pipu check -g prod      Check every env in a group
    """
    console = Console()

    if interactive and output == "json":
        console.print(
            "[red]error: --interactive is incompatible with -o json[/red]"
        )
        sys.exit(1)

    config = load_config()
    resolved = _apply_config_defaults(
        ctx, config,
        {"by": "problem", "output": "human", "debug": False},
    )
    by = resolved["by"]
    output = resolved["output"]
    debug = resolved["debug"]

    _configure_debug_logging(console, debug, output)

    if group_name is not None:
        _run_group_check(
            group_name=group_name, console=console,
            output=output, group_by=by,
            fix=fix, interactive=interactive,
        )
        return

    report = build_env_report()

    if output == "json":
        if fix:
            from pipu_cli._fix_cli import run_fix
            fixes, exit_code = run_fix(
                report=report, console=console, output="json",
                interactive=interactive,
            )
            print(json.dumps(env_report_to_json(report, fixes=fixes), indent=2))
            sys.exit(exit_code)
        print(json.dumps(env_report_to_json(report), indent=2))
    else:
        print_env_report(console, report, group_by=by)
        if fix:
            from pipu_cli._fix_cli import run_fix
            _, exit_code = run_fix(
                report=report, console=console, output="human",
                interactive=interactive,
            )
            sys.exit(exit_code)

    sys.exit(1 if report.problems else 0)


def _run_group_check(
    *,
    group_name: str,
    console: Console,
    output: str,
    group_by: str,
    fix: bool = False,
    interactive: bool = False,
) -> None:
    """Execute ``pipu check`` across every env of a saved group.

    Iterates envs sequentially so per-env panels don't interleave. In
    JSON mode, aggregates per-env reports (and fix results, when
    ``--fix`` is set) into the group schema.

    :param group_name: Name of the group to iterate.
    :param console: Rich console for human-mode output.
    :param output: ``"human"`` or ``"json"``.
    :param group_by: Forwarded to :func:`print_env_report` in human mode.
    :param fix: Enable fix mode per env.
    :param interactive: Prompt per action per env (human only; incompatible
        with ``output == "json"``, but the caller has already rejected
        that combination).
    """
    group_ctx = prepare_group(group_name, console=console, output=output)

    per_env_reports: List[tuple] = []
    per_env_fixes: Dict[str, List[Any]] = {}
    any_problems = False
    any_fix_failure = False

    for env_name, env_path in group_ctx.envs.items():
        if output != "json":
            console.print()
            console.print(Panel(env_path, title=f"Environment: {env_name}",
                                border_style="cyan", expand=False))
            console.print()

        report = build_env_report(python_path=env_path)
        per_env_reports.append((env_name, report))
        if report.problems:
            any_problems = True

        if output != "json":
            print_env_report(console, report, group_by=group_by)

        if fix:
            from pipu_cli._fix_cli import run_fix
            fixes, env_exit = run_fix(
                report=report, console=console, output=output,
                interactive=interactive,
            )
            per_env_fixes[env_name] = fixes
            if env_exit != 0:
                any_fix_failure = True

    if output == "json":
        envs_json: List[Dict[str, Any]] = []
        for env_name, report in per_env_reports:
            fixes_for_env = per_env_fixes.get(env_name) if fix else None
            envs_json.append({
                "env": env_name,
                "report": env_report_to_json(report, fixes=fixes_for_env),
            })
        print(json.dumps(
            {"group": group_name, "environments": envs_json}, indent=2,
        ))

    sys.exit(
        1 if any_fix_failure or (not fix and any_problems) else 0,
    )


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
@click.option(
    "--output", "-o",
    type=click.Choice(["human", "json"]),
    default="human",
    help="Output format (human-readable or json)"
)
def rollback(list_states_flag: bool, dry_run: bool, yes: bool, state: Optional[str], output: str) -> None:
    """
    Restore packages to a previous state.

    Before each upgrade, pipu saves the current package versions. Use this
    command to restore packages to their pre-upgrade state.

    \b
    Examples:
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
            if output == "json":
                print(json.dumps({"states": []}, indent=2))
            else:
                console.print("[yellow]No saved states found.[/yellow]")
                console.print(f"[dim]States are saved in: {ROLLBACK_DIR}[/dim]")
            sys.exit(0)

        if output == "json":
            print(json.dumps({"states": states}, indent=2))
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
            if output == "json":
                print(json.dumps({"error": f"State file not found: {state}"}, indent=2))
            else:
                console.print(f"[red]State file not found:[/red] {state}")
                console.print("[dim]Use 'pipu rollback --list' to see available states[/dim]")
            sys.exit(1)

        with open(state_path, 'r') as f:
            state_data = json.load(f)
    else:
        state_data = get_latest_state()

    if state_data is None:
        if output == "json":
            print(json.dumps({"error": "No saved state found"}, indent=2))
        else:
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

    if output == "json":
        if dry_run:
            print(json.dumps({"packages": packages, "timestamp": formatted_ts, "description": description}, indent=2))
            sys.exit(0)
    else:
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

    if output != "json":
        console.print("\n[bold]Rolling back packages...[/bold]\n")

    result = rollback_to_state(state_data, dry_run=False)

    if output == "json":
        print(json.dumps({
            "rolled_back": [p.spec for p in result.succeeded],
            "failed": [
                {"spec": p.spec, "reason": p.reason}
                for p in result.failed
            ],
        }, indent=2))
    else:
        if result.succeeded:
            console.print(
                f"\n[bold green]Successfully rolled back {len(result.succeeded)} package(s):[/bold green]"
            )
            for pkg in result.succeeded:
                console.print(f"  - {pkg.spec}")
        if result.failed:
            console.print(
                f"\n[bold red]Failed to roll back {len(result.failed)} package(s):[/bold red]"
            )
            for pkg in result.failed:
                console.print(f"  - {pkg.spec}: {pkg.reason}")
        if not result.succeeded and not result.failed:
            console.print("[yellow]No packages were rolled back.[/yellow]")

    sys.exit(0 if result.ok else 1)


@cli.command()
@click.option(
    "--all", "-a",
    "clean_all",
    is_flag=True,
    help="Clean up files for all environments"
)
@click.option(
    "--group", "-g",
    "group_name",
    default=None,
    help="Clean caches for all environments in a named group"
)
def clean(clean_all: bool, group_name: Optional[str]) -> None:
    """
    Clean up pipu caches and temporary files.

    Removes cached package version data and other temporary files
    created by pipu. By default, cleans up files for the current
    Python environment only.

    \b
    Examples:
      pipu clean              Clean current environment
      pipu clean --all        Clean all environments
      pipu clean -g mygroup   Clean all environments in a group
    """
    console = Console()

    if clean_all and group_name:
        console.print("[red]Cannot use --all and --group together.[/red]")
        sys.exit(1)

    if clean_all:
        count = clear_all_caches()
        if count > 0:
            console.print(f"[bold green]Cleared {count} cache(s).[/bold green]")
        else:
            console.print("[yellow]No caches to clear.[/yellow]")
    elif group_name:
        environments = get_group(group_name)
        if environments is None:
            console.print(f"[red]Group '{group_name}' not found.[/red]")
            sys.exit(1)

        cleared = 0
        for env_path in environments:
            if clear_cache(python_path=env_path):
                cleared += 1

        if cleared > 0:
            console.print(f"[bold green]Cleared {cleared} cache(s) for group '{group_name}'.[/bold green]")
        else:
            console.print(f"[yellow]No caches to clear for group '{group_name}'.[/yellow]")
    else:
        if clear_cache():
            console.print("[bold green]Cache cleared for current environment.[/bold green]")
        else:
            console.print("[yellow]No cache to clear for current environment.[/yellow]")

    sys.exit(0)


@cli.group("group")
def group_cmd() -> None:
    """Manage groups of Python environments.

    Groups allow you to run pipu commands across multiple Python
    environments at once.

    \b
    Examples:
      pipu group list                          List all groups
      pipu group add mygroup                   Add current env to group
      pipu group add mygroup --python /path    Add specific env
      pipu group remove mygroup                Remove current env
      pipu group delete mygroup                Delete entire group
    """
    pass


@group_cmd.command("list")
def group_list() -> None:
    """List all groups and their environments."""
    console = Console()
    groups = list_groups()

    if not groups:
        console.print("[yellow]No groups defined. Use `pipu group add` to create one.[/yellow]")
        sys.exit(0)

    table = Table(title="[bold]Environment Groups[/bold]")
    table.add_column("Group Name", style="cyan", no_wrap=True)
    table.add_column("Environments", style="magenta", justify="right")
    table.add_column("Python Paths", style="dim")

    for name, environments in sorted(groups.items()):
        paths = "\n".join(environments)
        table.add_row(name, str(len(environments)), paths)

    console.print(table)
    sys.exit(0)


@group_cmd.command("add")
@click.argument("group_name")
@click.option("--python", "python_path", default=None,
              help="Path to Python interpreter (defaults to current Python)")
@click.option("--force", "-f", is_flag=True,
              help="Skip Python path validation")
def group_add(group_name: str, python_path: Optional[str], force: bool) -> None:
    """Add a Python environment to a group.

    Creates the group if it doesn't exist.

    \b
    Examples:
      pipu group add mygroup                    Add current Python
      pipu group add mygroup --python /path     Add specific Python
      pipu group add mygroup --python /path -f  Skip validation
    """
    console = Console()

    if python_path is None:
        python_path = sys.executable

    if not force:
        try:
            python_path = validate_python_path(python_path)
        except click.ClickException as e:
            console.print(f"[red]Invalid Python path:[/red] {e.message}")
            sys.exit(1)

    added = add_environment(group_name, python_path)
    if added:
        console.print(f"[green]Added[/green] {python_path} to group [cyan]{group_name}[/cyan]")
    else:
        console.print(f"[yellow]{python_path} is already in group {group_name}[/yellow]")
    sys.exit(0)


@group_cmd.command("remove")
@click.argument("group_name")
@click.option("--python", "python_path", default=None,
              help="Path to Python interpreter (defaults to current Python)")
def group_remove(group_name: str, python_path: Optional[str]) -> None:
    """Remove a Python environment from a group.

    If this removes the last environment, the group is deleted.

    \b
    Examples:
      pipu group remove mygroup                Remove current Python
      pipu group remove mygroup --python /path Remove specific Python
    """
    console = Console()

    if python_path is None:
        python_path = sys.executable

    removed = remove_environment(group_name, python_path)
    if not removed:
        console.print(f"[red]Environment not found[/red] in group [cyan]{group_name}[/cyan]")
        sys.exit(1)

    console.print(f"[green]Removed[/green] {python_path} from group [cyan]{group_name}[/cyan]")
    sys.exit(0)


@group_cmd.command("delete")
@click.argument("group_name")
def group_delete(group_name: str) -> None:
    """Delete an entire group.

    \b
    Examples:
      pipu group delete mygroup    Delete the group
    """
    console = Console()

    deleted = delete_group(group_name)
    if not deleted:
        console.print(f"[red]Group not found:[/red] [cyan]{group_name}[/cyan]")
        sys.exit(1)

    console.print(f"[green]Deleted[/green] group [cyan]{group_name}[/cyan]")
    sys.exit(0)


def _upgrade_install_single_env(
    env_name: str,
    env_path: str,
    specs: list[str],
    *,
    dest_dir: Path,
    tracker: Any = None,
    interrupt_token: Optional[InterruptToken] = None,
) -> list:
    """Install pre-downloaded wheels into a single env for the group upgrade path.

    :param env_name: Short name used for tracker display.
    :param env_path: Python executable path for the target env.
    :param specs: Wheel specs to install (already downloaded into ``dest_dir``).
    :param dest_dir: Shared temp dir holding the downloaded wheels.
    :param tracker: Optional group-install progress tracker.
    :param interrupt_token: Shared cancel signal; accepted for worker
        contract parity. :func:`install_from_local` does not yet plumb the
        token through to :func:`pipu_cli._subprocess.run_pip`, so the
        caller's ``KeyboardInterrupt`` handler still provides the
        primary cancellation path.
    :returns: List of per-package install results (empty on worker-level
        exception so that one failing env doesn't poison the fan-out).
    """
    del interrupt_token  # not consumed today; kept for signature parity
    from pipu_cli.download import install_from_local

    try:
        callback = None
        if tracker:
            def on_install(spec: str, en: str = env_name) -> None:
                pkg_name = spec.split("==")[0] if "==" in spec else spec
                tracker.advance(en, pkg_name)
            callback = on_install
        results = install_from_local(
            dest_dir=dest_dir, specs=specs,
            python_path=env_path,
            progress_callback=callback,
        )
        if tracker:
            tracker.complete_env(env_name)
        return results
    except Exception as e:
        if tracker:
            tracker.fail_env(env_name, str(e))
        return []


def _run_group_upgrade(
    group_name: str, console: Console, output: str,
    timeout: int, pre: bool, yes: bool, debug: bool,
    exclude_str: str, show_blocked: bool,
    parallel: int, no_cache: bool, cache_ttl: Optional[int],
    packages: tuple, package_constraints: dict,
    update_requirements: Optional[str],
    cache_enabled: bool,
    post_check: PostCheck,
) -> None:
    """Execute upgrade across all environments in a group using consolidated pipeline."""
    group_ctx = prepare_group(group_name, console=console, output=output)
    env_name_map = group_ctx.envs
    valid_envs = list(env_name_map.values())
    reverse_map = {v: k for k, v in env_name_map.items()}  # full_path -> short_name

    ui = UpgradeUI(console) if output != "json" else None

    try:
        with (ui if ui is not None else nullcontext()):
            # Phase 1: Inspect all environments
            env_installed: dict[str, list] = {}
            if ui:
                ui.start_phase(f"Inspecting {len(valid_envs)} environments...")
            for env_path in valid_envs:
                installed = inspect_installed_packages(timeout=timeout, python_path=env_path)
                env_installed[reverse_map[env_path]] = installed
            if ui:
                total_pkgs = sum(len(v) for v in env_installed.values())
                ui.complete_phase(f"Found {total_pkgs} total packages")

            # Phase 2: Fetch latest versions (deduplicated across environments)
            if ui:
                ui.start_phase("Checking for updates across all environments...")
            # Merge all installed packages for a single version check
            all_installed_by_name: dict = {}
            for env_name, installed in env_installed.items():
                for pkg in installed:
                    key = pkg.name.lower()
                    if key not in all_installed_by_name or pkg.version < all_installed_by_name[key].version:
                        all_installed_by_name[key] = pkg
            all_installed = list(all_installed_by_name.values())

            effective_cache_ttl = DEFAULT_CACHE_TTL if cache_ttl is None else cache_ttl
            use_cache = cache_enabled and not no_cache
            cache_was_used = False

            latest_versions: dict = {}

            # Try cache first (using first env as cache key)
            if use_cache and is_cache_fresh(effective_cache_ttl, python_path=valid_envs[0]):
                cache_data = load_cache(python_path=valid_envs[0])
                if cache_data and cache_data.latest_versions:
                    for pkg in all_installed:
                        name_lower = pkg.name.lower()
                        if name_lower in cache_data.latest_versions:
                            try:
                                latest_ver = Version(cache_data.latest_versions[name_lower])
                                if latest_ver > pkg.version:
                                    latest_versions[pkg] = Package(name=pkg.name, version=latest_ver)
                            except InvalidVersion:
                                pass
                    cache_was_used = True

            if not cache_was_used:
                if parallel > 1:
                    latest_versions = get_latest_versions_parallel(
                        all_installed, timeout=timeout, include_prereleases=pre, max_workers=parallel,
                    )
                else:
                    latest_versions = get_latest_versions(
                        all_installed, timeout=timeout, include_prereleases=pre,
                    )
                # Save to cache
                if use_cache:
                    cache_dict = build_version_cache(latest_versions)
                    save_cache(cache_dict, include_prereleases=pre, python_path=valid_envs[0])

            # Re-key latest_versions by canonical name for cross-environment lookup
            latest_by_name: dict[str, Package] = {
                pkg.name.lower(): latest_versions[pkg] for pkg in latest_versions
            }

            if ui:
                ui.complete_phase(f"{len(latest_versions)} packages with newer versions")

            # Phase 3: Resolve constraints per environment
            if ui:
                ui.start_phase("Resolving dependency constraints...")
            env_upgrades: dict[str, list] = {}
            all_blocked: list[tuple[str, BlockedPackageInfo]] = []

            for env_name, installed in env_installed.items():
                env_latest = {pkg: latest_by_name[pkg.name.lower()] for pkg in installed if pkg.name.lower() in latest_by_name}
                if show_blocked:
                    upgradable, blocked = resolve_upgradable_packages_with_reasons(env_latest, installed)
                    for b in blocked:
                        all_blocked.append((env_name, b))
                else:
                    upgradable = [p for p in resolve_upgradable_packages(env_latest, installed) if p.upgradable]

                # Apply exclusions and package filters
                excluded_names = set()
                if exclude_str:
                    excluded_names = {n.strip().lower() for n in exclude_str.split(',')}
                can_upgrade = [p for p in upgradable if p.name.lower() not in excluded_names]
                if packages:
                    requested = {parse_package_spec(s).name for s in packages}
                    can_upgrade = [p for p in can_upgrade if canonicalize_name(p.name) in requested]

                env_upgrades[env_name] = can_upgrade

            total_upgradable = sum(len(v) for v in env_upgrades.values())
            if ui:
                ui.complete_phase(f"{total_upgradable} upgrades across {len(valid_envs)} environments")

            if total_upgradable == 0:
                if output != "json":
                    post_check.run_per_env(env_name_map)
                    console.print("\n[yellow]No packages can be upgraded.[/yellow]")
                    if show_blocked and all_blocked:
                        from pipu_cli.pretty import print_group_blocked_table
                        print_group_blocked_table(all_blocked, console=console)
                else:
                    group_results = []
                    for env_name in env_name_map:
                        env_path = env_name_map[env_name]
                        env_dict: Dict[str, Any] = {
                            "environment": env_path,
                            "upgradable": [],
                            "blocked": [package_to_dict(b) for en, b in all_blocked if en == env_name],
                            "results": [],
                            "summary": {"total": 0, "upgraded": 0, "failed": 0},
                        }
                        post_check.run(python_path=env_path, result=env_dict)
                        group_results.append(env_dict)
                    print(json.dumps(group_results, indent=2))
                sys.exit(0)

            # Phase 4: Show matrix table and confirm
            if output != "json":
                console.print()
                from pipu_cli.pretty import print_env_legend, print_group_upgrade_matrix
                print_env_legend(env_name_map, console=console)
                console.print()
                print_group_upgrade_matrix(env_upgrades, env_name_map, console=console)

                if show_blocked and all_blocked:
                    from pipu_cli.pretty import print_group_blocked_table
                    print_group_blocked_table(all_blocked, console=console)

                if not yes:
                    console.print()
                    confirm = click.confirm(
                        f"Upgrade {total_upgradable} packages across {len(valid_envs)} environments?",
                        default=True,
                    )
                    if not confirm:
                        console.print("[yellow]Upgrade cancelled.[/yellow]")
                        sys.exit(0)

            # Phase 5: Save rollback state for each environment
            from pipu_cli.rollback import save_state
            for env_name, upgrades in env_upgrades.items():
                if upgrades:
                    env_path = env_name_map[env_name]
                    pre_pkgs = [{"name": p.name, "version": str(p.version)} for p in upgrades]
                    save_state(pre_pkgs, f"Pre-upgrade state ({env_path})")

            # Phase 6: Shared download (editable packages bypass this)
            env_specs: dict[str, list[str]] = {}
            for env_name, upgrades in env_upgrades.items():
                specs = []
                for pkg in upgrades:
                    if pkg.is_editable:
                        continue
                    name_key = canonicalize_name(pkg.name)
                    if name_key in package_constraints:
                        specs.append(f"{pkg.name}{package_constraints[name_key]}")
                    else:
                        specs.append(f"{pkg.name}=={pkg.latest_version}")
                env_specs[env_name] = specs

            with tempfile.TemporaryDirectory(prefix="pipu-group-") as tmp_dir:
                dest_dir = Path(tmp_dir)

                from pipu_cli.download import download_packages_for_group

                if ui:
                    unique_specs = list(dict.fromkeys(
                        s for specs in env_specs.values() for s in specs
                    ))
                    tracker = ui.show_download_progress(unique_specs)
                    def on_download_start(spec: str) -> None:
                        tracker.start(spec)
                    def on_download(spec: str, success: bool, error_msg: str) -> None:
                        if success:
                            tracker.complete(spec)
                        else:
                            tracker.fail(spec)
                    try:
                        download_packages_for_group(
                            env_specs, dest_dir, pre=pre, max_workers=parallel,
                            progress_callback=on_download, start_callback=on_download_start,
                        )
                    except RuntimeError:
                        pass
                    finally:
                        tracker.finish()
                else:
                    download_packages_for_group(env_specs, dest_dir, pre=pre, max_workers=parallel)

                # Phase 7: Install per environment (fanned out via shared runner)
                env_order = list(env_name_map.keys())
                active_envs = [name for name in env_order if env_specs.get(name)]
                active_ctx = GroupContext(
                    name=group_ctx.name,
                    envs={name: env_name_map[name] for name in active_envs},
                )

                if ui:
                    env_totals = {name: len(env_specs.get(name, [])) for name in active_envs}
                    group_tracker = ui.show_group_install_progress(active_envs, env_totals)
                else:
                    group_tracker = None

                def _upgrade_worker(name: str, path: str, token: InterruptToken) -> list:
                    return _upgrade_install_single_env(
                        name, path, env_specs[name],
                        dest_dir=dest_dir, tracker=group_tracker,
                        interrupt_token=token,
                    )

                env_results = (
                    run_per_env_parallel(active_ctx, _upgrade_worker) if active_envs else {}
                )

                if group_tracker is not None:
                    group_tracker.finish()

                # Handle editable packages per environment
                for env_name, upgrades in env_upgrades.items():
                    editables = [p for p in upgrades if p.is_editable]
                    if editables:
                        env_path = env_name_map[env_name]
                        if ui:
                            ui.start_phase(f"Reinstalling {len(editables)} editable package(s) in {env_name}...")
                        ed_results = reinstall_editable_packages(
                            editables, timeout=300, python_path=env_path,
                        )
                        if ui:
                            ui.complete_phase("done")
                        if env_name in env_results:
                            env_results[env_name].extend(ed_results)
                        else:
                            env_results[env_name] = list(ed_results)

            # Phase 8: Show results
            if output == "json":
                group_results = []
                for env_name in env_order:
                    env_path = env_name_map[env_name]
                    results = env_results.get(env_name, [])
                    upgraded = len([r for r in results if r.upgraded])
                    failed = len([r for r in results if not r.upgraded])
                    env_result_dict: Dict[str, Any] = {
                        "environment": env_path,
                        "upgradable": [package_to_dict(p) for p in env_upgrades.get(env_name, [])],
                        "blocked": [package_to_dict(b) for en, b in all_blocked if en == env_name],
                        "results": [package_to_dict(r) for r in results],
                        "summary": {"total": upgraded + failed, "upgraded": upgraded, "failed": failed},
                    }
                    post_check.run(python_path=env_path, result=env_result_dict)
                    group_results.append(env_result_dict)
                print(json.dumps(group_results, indent=2))
            else:
                console.print()
                from pipu_cli.pretty import print_group_results_matrix, print_group_blocked_table
                print_group_results_matrix(env_results, env_name_map, console=console)
                if show_blocked and all_blocked:
                    print_group_blocked_table(all_blocked, console=console)

                post_check.run_per_env(env_name_map)

            total_failed = sum(
                len([r for r in env_results.get(n, []) if not r.upgraded])
                for n in env_order
            )
            if total_failed:
                sys.exit(1)
            sys.exit(0)

    except KeyboardInterrupt:
        sys.exit(130)


def _outdated_single_env(
    env_path: str,
    *,
    console: Console,
    output: str,
    timeout: int,
    pre: bool,
    debug: bool,
    exclude_str: str,
    show_blocked: bool,
    parallel: int,
    cache_enabled: bool,
    cache_ttl: Optional[int],
    interrupt_token: Optional[InterruptToken] = None,
) -> Optional[dict[str, Any]]:
    """Run the full outdated pipeline for a single env.

    Prints the env panel and the per-env sections (in human mode) as the
    original serial implementation did; returns the JSON-ready record when
    ``output == "json"`` so the caller can aggregate it into the group
    result list.

    :param env_path: Python executable path for this environment.
    :param console: Rich console for human-mode output.
    :param output: Output mode (``"human"`` or ``"json"``).
    :param timeout: Network timeout for inspect / version checks.
    :param pre: Include pre-release versions in the latest-version probe.
    :param debug: Debug mode flag (forwarded to step helpers).
    :param exclude_str: Comma-separated package names to exclude.
    :param show_blocked: Whether to surface blocked-by-constraint packages.
    :param parallel: Parallelism hint for the version fetcher.
    :param cache_enabled: Whether pip cache reads are permitted at all.
    :param cache_ttl: CLI/config TTL override in seconds.
    :param interrupt_token: Shared cancel signal; accepted for worker
        contract parity but not consumed inside this helper (the inspect /
        version-fetch paths don't spawn pip subprocesses that honor it).
    :returns: A per-env JSON record in JSON mode, else ``None``.
    """
    del interrupt_token  # not consumed today; kept for signature parity

    if output != "json":
        console.print()
        console.print(Panel(env_path, title="Environment", border_style="cyan", expand=False))
        console.print()

    try:
        effective_cache_ttl = DEFAULT_CACHE_TTL if cache_ttl is None else cache_ttl
        use_cache = cache_enabled and is_cache_fresh(effective_cache_ttl, python_path=env_path)

        if use_cache and output != "json":
            cache_age = get_cache_age_seconds(python_path=env_path)
            console.print(f"[dim]Using cached data ({format_cache_age(cache_age)})[/dim]\n")

        installed_packages, _ = _step1_inspect_packages(
            console, output, timeout, debug, total_steps=3, python_path=env_path
        )
        if not installed_packages:
            if output != "json":
                console.print("[yellow]No packages found.[/yellow]")
                return None
            return {
                "environment": env_path,
                "upgradable": [], "blocked": [], "results": [],
                "summary": {"total": 0, "upgraded": 0, "failed": 0},
            }

        latest_versions, _, _ = _step2_get_latest_versions(
            console, output, debug, installed_packages, use_cache, cache_enabled,
            timeout, pre, parallel, total_steps=3, python_path=env_path
        )
        if not latest_versions:
            if output != "json":
                console.print("\n[bold green]All packages are up to date![/bold green]")
                return None
            return {
                "environment": env_path,
                "upgradable": [], "blocked": [], "results": [],
                "summary": {"total": 0, "upgraded": 0, "failed": 0},
            }

        can_upgrade, blocked_packages, _, _ = _step3_resolve_packages(
            console, output, debug, latest_versions, installed_packages,
            show_blocked, exclude_str, (), total_steps=3
        )

        if output != "json":
            if can_upgrade:
                console.print("\n[bold]Packages with updates available:\n")
                print_upgradable_packages_table(can_upgrade, console=console)
            else:
                console.print("\n[yellow]No packages can be upgraded.[/yellow]")
            if show_blocked and blocked_packages:
                console.print()
                print_blocked_packages_table(blocked_packages, console=console)
            return None

        return {
            "environment": env_path,
            "upgradable": [package_to_dict(p) for p in can_upgrade],
            "blocked": [package_to_dict(p) for p in blocked_packages] if show_blocked else [],
            "results": [],
            "summary": {"total": 0, "upgraded": 0, "failed": 0},
        }

    except Exception as e:
        if output != "json":
            console.print(f"\n[red]Error in {env_path}:[/red] {e}")
        return None


def _run_group_outdated(
    group_name: str, console: Console, output: str,
    timeout: int, pre: bool, debug: bool,
    exclude_str: str, show_blocked: bool,
    parallel: int, no_cache: bool, cache_ttl: Optional[int],
    cache_enabled: bool,
) -> None:
    """Execute outdated check across all environments in a group.

    Kept serial so the per-env console panels don't interleave with each
    other. ``no_cache`` is already folded into ``cache_enabled`` by the
    caller and is accepted for signature parity with the outer command.
    """
    del no_cache  # already folded into cache_enabled at call site
    group_ctx = prepare_group(group_name, console=console, output=output)

    group_results: list[dict[str, Any]] = []

    try:
        for env_path in group_ctx.envs.values():
            record = _outdated_single_env(
                env_path,
                console=console, output=output,
                timeout=timeout, pre=pre, debug=debug,
                exclude_str=exclude_str, show_blocked=show_blocked,
                parallel=parallel, cache_enabled=cache_enabled,
                cache_ttl=cache_ttl,
            )
            if record is not None:
                group_results.append(record)

    except KeyboardInterrupt:
        console.show_cursor(True)
        sys.exit(130)

    if output == "json":
        print(json.dumps(group_results, indent=2))

    sys.exit(0)


@cli.command()
@click.pass_context
@click.argument('packages', nargs=-1, required=True)
@click.option(
    "--no-update",
    is_flag=True,
    help="Use plain pip install without -U flag (don't upgrade existing packages)"
)
@click.option(
    "--timeout",
    type=int,
    default=300,
    help="Installation timeout in seconds"
)
@pre_option
@yes_option()
@debug_option
@output_option
@no_check_option("install")
@group_option("Install across all environments in a named group")
def install(ctx: click.Context, packages: tuple[str, ...], no_update: bool, timeout: int,
            pre: bool, yes: bool, debug: bool, output: str, no_check: bool,
            group_name: Optional[str] = None) -> None:
    """
    Install packages using pip.

    By default uses pip install -U (install or update). Use --no-update
    for plain pip install without upgrading existing packages.

    \b
    Examples:
      pipu install requests flask       Install/update packages
      pipu install requests --no-update  Install without updating
      pipu install "numpy>=1.24"         Install with version constraint
      pipu install requests -g mygroup   Install across a group
    """
    console = Console()

    # Load configuration file
    config = load_config()
    resolved = _apply_config_defaults(
        ctx,
        config,
        {
            'timeout': 300,
            'pre': False,
            'yes': False,
            'debug': False,
            'output': 'human',
            'check_after_changes': DEFAULT_CHECK_AFTER_CHANGES,
        },
    )
    timeout = resolved['timeout']
    pre = resolved['pre']
    yes = resolved['yes']
    debug = resolved['debug']
    output = resolved['output']

    post_check = PostCheck.from_flags(
        console=console, output=output,
        check_after_changes=resolved['check_after_changes'],
        no_check=no_check,
    )

    # Group mode
    if group_name is not None:
        _run_group_install(
            group_name=group_name, console=console, output=output,
            packages=packages, no_update=no_update, timeout=timeout,
            pre=pre, yes=yes, debug=debug,
            post_check=post_check,
        )
        return

    _configure_debug_logging(console, debug, output)

    try:
        # Step 1: Show what will be installed and confirm
        if output != "json":
            console.print("[bold]Step 1/2:[/bold] Packages to install:\n")
            for pkg_spec in packages:
                console.print(f"  - {pkg_spec}")
            if no_update:
                console.print("\n  [dim](install only, no upgrade)[/dim]")
            else:
                console.print("\n  [dim](install or upgrade to latest)[/dim]")

        if not yes and output != "json":
            console.print()
            confirm = click.confirm("Do you want to proceed?", default=True)
            if not confirm:
                console.print("[yellow]Installation cancelled.[/yellow]")
                post_check.run()
                sys.exit(0)

        # Step 2: Install packages
        if output != "json":
            console.print(f"\n[bold]Step 2/2:[/bold] Installing {len(packages)} package(s)...\n")

        stream = ConsoleStream(console) if output != "json" else None
        results = run_pip_install(
            package_specs=list(packages),
            upgrade=not no_update,
            output_stream=stream,
            timeout=timeout,
            pre=pre,
        )

        # Display results
        if output == "json":
            payload = build_install_payload(results)
            post_check.run(result=payload)
            print(json.dumps(payload, indent=2))
        else:
            print_install_results(results, console=console)
            post_check.run()

        # Exit with appropriate code
        failed = [r for r in results if not r.installed]
        sys.exit(1 if failed else 0)

    except KeyboardInterrupt:
        console.show_cursor(True)
        sys.exit(130)
    except click.Abort:
        console.show_cursor(True)
        sys.exit(130)
    except Exception as e:
        if output == "json":
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"\n[bold red]Error:[/bold red] {e}")
        sys.exit(1)


def _install_single_env(
    env_name: str,
    env_path: str,
    *,
    packages: tuple,
    no_update: bool,
    timeout: int,
    pre: bool,
    tracker: Any = None,
    interrupt_token: Optional[InterruptToken] = None,
) -> list:
    """Install a package list into a single env for the group install path.

    :param env_name: Short name used for tracker display.
    :param env_path: Python executable path for the target env.
    :param packages: User-supplied package specs.
    :param no_update: When True, uses plain ``pip install`` (no ``-U``).
    :param timeout: Subprocess timeout in seconds.
    :param pre: Include pre-release versions.
    :param tracker: Optional group-install progress tracker.
    :param interrupt_token: Shared cancel signal; accepted for worker
        contract parity. :func:`run_pip_install` does not yet plumb the
        token through to :func:`pipu_cli._subprocess.run_pip`, so the
        caller's ``KeyboardInterrupt`` handler still provides the
        primary cancellation path.
    :returns: List of per-package install results (empty on worker-level
        exception so that one failing env doesn't poison the fan-out).
    """
    del interrupt_token  # not consumed today; kept for signature parity
    try:
        results = run_pip_install(
            package_specs=list(packages),
            upgrade=not no_update,
            timeout=timeout,
            python_path=env_path,
            pre=pre,
        )
        if tracker:
            tracker.complete_env(env_name)
        return results
    except Exception as e:
        if tracker:
            tracker.fail_env(env_name, str(e))
        return []


def _run_group_install(
    group_name: str, console: Console, output: str,
    packages: tuple, no_update: bool, timeout: int,
    pre: bool, yes: bool, debug: bool,
    post_check: PostCheck,
) -> None:
    """Execute install across all environments in a group."""
    del debug  # signature parity; install's debug handling happens upstream
    from pipu_cli.pretty import (
        print_env_legend,
        print_group_install_matrix, print_group_install_results_matrix,
    )

    group_ctx = prepare_group(group_name, console=console, output=output)
    env_name_map = group_ctx.envs
    valid_envs = list(env_name_map.values())
    reverse_map = {v: k for k, v in env_name_map.items()}

    ui = UpgradeUI(console) if output != "json" else None

    try:
        with (ui if ui is not None else nullcontext()):
            # Phase 1: Inspect current state across all environments
            canonical_pkgs = [parse_package_spec(p).name for p in packages]
            env_versions: dict[str, dict[str, Optional[Version]]] = {}

            if ui:
                ui.start_phase(f"Inspecting {len(valid_envs)} environments...")

            for env_path in valid_envs:
                installed = inspect_installed_packages(timeout=timeout, python_path=env_path)
                installed_map: dict[str, Version] = {canonicalize_name(p.name): p.version for p in installed}
                short = reverse_map[env_path]
                pkg_versions: dict[str, Optional[Version]] = {}
                for i, spec in enumerate(packages):
                    pkg_versions[spec] = installed_map.get(canonical_pkgs[i])
                env_versions[short] = pkg_versions

            if ui:
                ui.complete_phase(f"{len(valid_envs)} environments inspected")

            # Phase 2: Show matrix and confirm
            if output != "json":
                console.print()
                print_env_legend(env_name_map, console=console)
                console.print()
                print_group_install_matrix(
                    env_versions, list(packages), env_name_map,
                    upgrade=not no_update, console=console,
                )

                if not yes:
                    action = "install/upgrade" if not no_update else "install"
                    console.print()
                    confirm = click.confirm(
                        f"{action.capitalize()} {len(packages)} package(s) across {len(valid_envs)} environments?",
                        default=True,
                    )
                    if not confirm:
                        console.print("[yellow]Installation cancelled.[/yellow]")
                        post_check.run_per_env(env_name_map)
                        sys.exit(0)

            # Phase 3: Parallel install across environments via shared runner
            env_order = list(env_name_map.keys())

            if ui:
                env_totals = {name: len(packages) for name in env_order}
                group_tracker = ui.show_group_install_progress(env_order, env_totals)
            else:
                group_tracker = None

            def _install_worker(name: str, path: str, token: InterruptToken) -> list:
                return _install_single_env(
                    name, path,
                    packages=packages, no_update=no_update,
                    timeout=timeout, pre=pre,
                    tracker=group_tracker, interrupt_token=token,
                )

            env_results = run_per_env_parallel(group_ctx, _install_worker)

            if group_tracker is not None:
                group_tracker.finish()

            # Phase 4: Show results
            if output == "json":
                group_results = []
                for env_name in env_order:
                    env_path = env_name_map[env_name]
                    env_res = env_results.get(env_name, [])
                    n_installed = len([r for r in env_res if r.installed])
                    n_failed = len([r for r in env_res if not r.installed])
                    env_dict = {
                        "environment": env_path,
                        "results": [package_to_dict(r) for r in env_res],
                        "summary": {
                            "total": len(env_res),
                            "installed": n_installed,
                            "failed": n_failed,
                        },
                    }
                    post_check.run(python_path=env_path, result=env_dict)
                    group_results.append(env_dict)
                print(json.dumps(group_results, indent=2))
            else:
                console.print()
                print_group_install_results_matrix(env_results, env_name_map, console=console)

                post_check.run_per_env(env_name_map)

            total_failed = sum(
                len([r for r in env_results.get(n, []) if not r.installed])
                for n in env_order
            )
            if total_failed:
                sys.exit(1)
            sys.exit(0)

    except KeyboardInterrupt:
        sys.exit(130)


@cli.command()
@click.pass_context
@click.argument('packages', nargs=-1, required=True)
@click.option(
    "--timeout",
    type=int,
    default=300,
    help="Uninstallation timeout in seconds"
)
@yes_option()
@debug_option
@output_option
@no_check_option("uninstall")
@group_option("Uninstall across all environments in a named group")
def uninstall(ctx: click.Context, packages: tuple[str, ...], timeout: int,
              yes: bool, debug: bool, output: str, no_check: bool,
              group_name: Optional[str] = None) -> None:
    """Uninstall packages using pip.

    \b
    Examples:
      pipu uninstall requests flask       Uninstall packages
      pipu uninstall requests -y          Skip confirmation
      pipu uninstall requests -g mygroup  Uninstall across a group
    """
    console = Console()

    config = load_config()
    resolved = _apply_config_defaults(
        ctx,
        config,
        {
            'timeout': 300,
            'yes': False,
            'debug': False,
            'output': 'human',
            'check_after_changes': DEFAULT_CHECK_AFTER_CHANGES,
        },
    )
    timeout = resolved['timeout']
    yes = resolved['yes']
    debug = resolved['debug']
    output = resolved['output']

    post_check = PostCheck.from_flags(
        console=console, output=output,
        check_after_changes=resolved['check_after_changes'],
        no_check=no_check,
    )

    if group_name is not None:
        _run_group_uninstall(
            group_name=group_name, console=console, output=output,
            packages=packages, timeout=timeout,
            yes=yes,
            post_check=post_check,
        )
        return

    _configure_debug_logging(console, debug, output)

    try:
        if output != "json":
            console.print("[bold]Step 1/2:[/bold] Packages to uninstall:\n")
            for pkg in packages:
                console.print(f"  - {pkg}")

        if not yes and output != "json":
            console.print()
            confirm = click.confirm("Do you want to proceed?", default=True)
            if not confirm:
                console.print("[yellow]Uninstallation cancelled.[/yellow]")
                post_check.run()
                sys.exit(0)

        if output != "json":
            console.print(f"\n[bold]Step 2/2:[/bold] Uninstalling {len(packages)} package(s)...\n")

        stream = ConsoleStream(console) if output != "json" else None
        results = run_pip_uninstall(
            package_names=list(packages),
            output_stream=stream,
            timeout=timeout,
        )

        if output == "json":
            payload = build_uninstall_payload(results)
            payload = post_check.run(result=payload)
            print(json.dumps(payload, indent=2))
        else:
            print_uninstall_results(results, console=console)
            post_check.run()

        failed = [r for r in results if not r.uninstalled]
        sys.exit(1 if failed else 0)

    except KeyboardInterrupt:
        console.show_cursor(True)
        sys.exit(130)
    except click.Abort:
        console.show_cursor(True)
        sys.exit(130)
    except Exception as e:
        if output == "json":
            print(json.dumps({"error": str(e)}))
        else:
            console.print(f"\n[bold red]Error:[/bold red] {e}")
        sys.exit(1)


def _uninstall_single_env(
    env_name: str,
    env_path: str,
    *,
    packages: tuple,
    timeout: int,
    tracker: Any = None,
    interrupt_token: Optional[InterruptToken] = None,
) -> list:
    """Uninstall a package list from a single env for the group uninstall path.

    :param env_name: Short name used for tracker display.
    :param env_path: Python executable path for the target env.
    :param packages: User-supplied package names.
    :param timeout: Subprocess timeout in seconds.
    :param tracker: Optional group-install progress tracker (reused for
        uninstall since the tracker is op-agnostic).
    :param interrupt_token: Shared cancel signal; accepted for worker
        contract parity.
    :returns: List of per-package uninstall results (empty on worker-level
        exception so that one failing env doesn't poison the fan-out).
    """
    del interrupt_token  # not consumed today; kept for signature parity
    try:
        results = run_pip_uninstall(
            package_names=list(packages),
            timeout=timeout,
            python_path=env_path,
        )
        if tracker:
            tracker.complete_env(env_name)
        return results
    except Exception as e:
        if tracker:
            tracker.fail_env(env_name, str(e))
        return []


def _run_group_uninstall(
    group_name: str, console: Console, output: str,
    packages: tuple, timeout: int,
    yes: bool,
    *,
    post_check: PostCheck,
) -> None:
    """Execute uninstall across all environments in a group."""
    from pipu_cli.pretty import (
        print_env_legend,
        print_group_uninstall_matrix, print_group_uninstall_results_matrix,
    )

    group_ctx = prepare_group(group_name, console=console, output=output)
    env_name_map = group_ctx.envs
    valid_envs = list(env_name_map.values())
    reverse_map = {v: k for k, v in env_name_map.items()}

    ui = UpgradeUI(console) if output != "json" else None

    try:
        with (ui if ui is not None else nullcontext()):
            # Phase 1: Inspect current state across all environments
            canonical_pkgs = [parse_package_spec(p).name for p in packages]
            env_versions: dict[str, dict[str, Optional[Version]]] = {}

            if ui:
                ui.start_phase(f"Inspecting {len(valid_envs)} environments...")

            for env_path in valid_envs:
                installed = inspect_installed_packages(timeout=timeout, python_path=env_path)
                installed_map: dict[str, Version] = {canonicalize_name(p.name): p.version for p in installed}
                short = reverse_map[env_path]
                pkg_versions: dict[str, Optional[Version]] = {}
                for i, pkg_name in enumerate(packages):
                    pkg_versions[pkg_name] = installed_map.get(canonical_pkgs[i])
                env_versions[short] = pkg_versions

            if ui:
                ui.complete_phase(f"{len(valid_envs)} environments inspected")

            # Phase 2: Show matrix and confirm
            if output != "json":
                console.print()
                print_env_legend(env_name_map, console=console)
                console.print()
                print_group_uninstall_matrix(
                    env_versions, list(packages), env_name_map, console=console,
                )

                if not yes:
                    console.print()
                    confirm = click.confirm(
                        f"Uninstall {len(packages)} package(s) across {len(valid_envs)} environments?",
                        default=True,
                    )
                    if not confirm:
                        console.print("[yellow]Uninstallation cancelled.[/yellow]")
                        post_check.run_per_env(env_name_map, title_prefix="Check")
                        sys.exit(0)

            # Phase 3: Parallel uninstall across environments via shared runner
            env_order = list(env_name_map.keys())

            if ui:
                env_totals = {name: len(packages) for name in env_order}
                group_tracker = ui.show_group_install_progress(env_order, env_totals)
            else:
                group_tracker = None

            def _uninstall_worker(name: str, path: str, token: InterruptToken) -> list:
                return _uninstall_single_env(
                    name, path,
                    packages=packages, timeout=timeout,
                    tracker=group_tracker, interrupt_token=token,
                )

            env_results = run_per_env_parallel(group_ctx, _uninstall_worker)

            if group_tracker is not None:
                group_tracker.finish()

            # Phase 4: Show results
            if output == "json":
                group_results = []
                for env_name in env_order:
                    env_path = env_name_map[env_name]
                    results = env_results.get(env_name, [])
                    uninstalled = len([r for r in results if r.uninstalled])
                    failed = len([r for r in results if not r.uninstalled])
                    env_dict = {
                        "environment": env_path,
                        "results": [{
                            "name": r.name,
                            "previous_version": str(r.previous_version) if r.previous_version else None,
                            "uninstalled": r.uninstalled,
                            "already_absent": r.already_absent,
                            "failure_reason": r.failure_reason,
                        } for r in results],
                        "summary": {
                            "total": len(results),
                            "uninstalled": uninstalled,
                            "failed": failed,
                        },
                    }
                    post_check.run(python_path=env_path, result=env_dict)
                    group_results.append(env_dict)
                print(json.dumps(group_results, indent=2))
            else:
                console.print()
                print_group_uninstall_results_matrix(env_results, env_name_map, console=console)
                post_check.run_per_env(env_name_map, title_prefix="Check")


            total_failed = sum(
                len([r for r in env_results.get(n, []) if not r.uninstalled])
                for n in env_order
            )
            if total_failed:
                sys.exit(1)
            sys.exit(0)

    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    cli()
