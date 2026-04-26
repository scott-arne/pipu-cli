"""Shared Click option decorators for pipu commands.

Centralizing common options (``--output``, ``--debug``, ``--group``, ...)
keeps help text and default values consistent across commands. Import
these decorators into :mod:`pipu_cli.cli` rather than repeating each
option block.

Usage::

    from pipu_cli._options import output_option, debug_option

    @cli.command()
    @output_option
    @debug_option
    def foo(output: str, debug: bool) -> None: ...
"""

import multiprocessing as mp

import rich_click as click

from pipu_cli.config import DEFAULT_CACHE_TTL


# ---------------------------------------------------------------------------
# Parameterless decorators
# ---------------------------------------------------------------------------

output_option = click.option(
    "--output", "-o",
    type=click.Choice(["human", "json"]), default="human",
    help="Output format (human-readable or json)",
)

debug_option = click.option(
    "--debug", is_flag=True,
    help="Enable debug logging",
)

pre_option = click.option(
    "--pre", is_flag=True,
    help="Include pre-release versions",
)

exclude_option = click.option(
    "--exclude", "-e",
    multiple=True,
    help="Packages to exclude (repeatable, comma-separated)",
)

parallel_option = click.option(
    "--parallel", "-p",
    type=int, default=min(4, mp.cpu_count()),
    help=(
        "Number of parallel requests for version checking "
        f"(default: {min(4, mp.cpu_count())})"
    ),
)

no_cache_option = click.option(
    "--no-cache", is_flag=True,
    help="Skip cache and fetch fresh version data",
)

cache_ttl_option = click.option(
    "--cache-ttl", type=int, default=None,
    help=f"Cache freshness threshold in seconds (default: {DEFAULT_CACHE_TTL})",
)


# ---------------------------------------------------------------------------
# Parameterized decorators (command-specific help text)
# ---------------------------------------------------------------------------

def yes_option(help: str = "Skip confirmation prompt"):
    """``--yes / -y`` flag. Help text defaults to the install/uninstall phrasing."""
    return click.option("--yes", "-y", is_flag=True, help=help)


def no_check_option(action: str):
    """``--no-check`` flag. ``action`` fills in the post-{action} phrasing."""
    return click.option(
        "--no-check", is_flag=True,
        help=f"Skip the post-{action} consistency check",
    )


def group_option(help: str):
    """``--group / -g NAME`` flag. Help text is command-specific."""
    return click.option(
        "--group", "-g", "group_name",
        default=None, help=help,
    )


fix_option = click.option(
    "--fix", is_flag=True,
    help="Automatically fix problems where the remediation is obvious",
)

interactive_option = click.option(
    "--interactive", is_flag=True,
    help="Prompt before each fix action (requires --fix; incompatible with -o json)",
)
