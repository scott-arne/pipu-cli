"""Shared orchestration for per-environment group operations.

Consumes a resolved group (env short name -> python executable path) and a
per-env worker callable. Handles :class:`ThreadPoolExecutor` setup,
``KeyboardInterrupt`` propagation via an :class:`InterruptToken`, and
ordered result collection.

Internal module (leading underscore) consumed by the CLI layer. Keeps the
``_run_group_*`` functions in ``pipu_cli.cli`` isomorphic: each extracts a
per-op single-env worker and fans it out through :func:`run_per_env_parallel`.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

import click

from pipu_cli._subprocess import InterruptToken
from pipu_cli.groups import get_group
from pipu_cli.pretty import extract_env_short_name

R = TypeVar("R")


@dataclass(frozen=True)
class GroupContext:
    """Resolved context for a group run.

    :param name: Group name (for display only).
    :param envs: Mapping of environment short name -> python executable path.
        Iteration order is insertion order and is used to order results.
    """

    name: str
    envs: dict[str, str]


def prepare_group(
    group_name: str,
    *,
    console=None,
    output: str = "human",
) -> GroupContext:
    """Load + validate a group definition.

    Mirrors the historical per-command preamble used by
    ``_run_group_upgrade`` / ``_run_group_outdated`` / ``_run_group_install``
    / ``_run_group_uninstall``: resolves the group via
    :func:`pipu_cli.groups.get_group`, drops env paths that don't exist with
    a warning, and raises on missing/empty groups.

    In JSON mode the structured error payload is printed to stdout before
    raising, so machine consumers still see ``{"error": ...}``. The
    subsequent raise uses :class:`click.exceptions.Exit` to surface a
    non-zero exit code without Click prepending ``"Error: "`` to stderr.
    In human mode the error is raised as :class:`click.ClickException`,
    which Click renders at the command boundary.

    :param group_name: Name of the group to resolve.
    :param console: Rich console for human-mode warnings. Required when
        ``output == "human"`` for the per-env skip warnings; the terminal
        "group not found" / "no valid envs" errors are surfaced via
        ``ClickException`` and don't need the console.
    :param output: Output mode (``"human"`` or ``"json"``). Controls the
        shape of the error message emitted on failure.
    :returns: A resolved :class:`GroupContext` with at least one env.
    :raises click.ClickException: In human mode, when the group is missing
        or contains no valid environments.
    :raises click.exceptions.Exit: In JSON mode, after the structured
        error payload has been written to stdout.
    """
    environments = get_group(group_name)
    if environments is None:
        if output == "json":
            print(json.dumps({"error": f"Group '{group_name}' not found"}))
            raise click.exceptions.Exit(1)
        raise click.ClickException(f"Group not found: {group_name}")

    env_name_map: dict[str, str] = {}
    used_names: set[str] = set()
    for env_path in environments:
        if not os.path.exists(env_path):
            if output != "json" and console is not None:
                console.print(f"[yellow]Warning: Skipping {env_path} (path not found)[/yellow]")
            continue
        short = extract_env_short_name(env_path, existing_names=used_names)
        env_name_map[short] = env_path
        used_names.add(short)

    if not env_name_map:
        if output == "json":
            print(json.dumps({"error": "No valid environments in group"}))
            raise click.exceptions.Exit(1)
        raise click.ClickException("No valid environments in group.")

    return GroupContext(name=group_name, envs=env_name_map)


def run_per_env_parallel(
    ctx: GroupContext,
    worker: Callable[[str, str, InterruptToken], R],
    *,
    max_workers: Optional[int] = None,
) -> dict[str, R]:
    """Run ``worker(env_name, python_path, token)`` for every env in parallel.

    Results are collected in the order ``ctx.envs`` iterates (insertion
    order), independent of completion order. On ``KeyboardInterrupt`` the
    shared :class:`InterruptToken` is flipped, giving every worker a chance
    to observe the cancel and tear down its subprocess; the caller re-raises
    after a brief drain window.

    :param ctx: Resolved group context.
    :param worker: Per-env callable; receives the interrupt token so it can
        pass it through to :func:`pipu_cli._subprocess.run_pip`.
    :param max_workers: Cap on the thread pool. Defaults to ``len(ctx.envs)``.
    :returns: A dict keyed by env short name, in the same order as
        ``ctx.envs``.
    :raises KeyboardInterrupt: Re-raised after flipping the token and
        waiting briefly for in-flight workers to wind down.
    """
    env_items = list(ctx.envs.items())
    if not env_items:
        return {}

    pool_size = max_workers if max_workers is not None else len(env_items)
    pool_size = max(1, pool_size)

    token = InterruptToken()
    results: dict[str, R] = {}

    with ThreadPoolExecutor(max_workers=pool_size) as executor:
        futures = {
            executor.submit(worker, name, path, token): name
            for name, path in env_items
        }
        try:
            for future in as_completed(futures):
                name = futures[future]
                results[name] = future.result()
        except KeyboardInterrupt:
            token.set()
            # Give workers a brief window to observe the token and tear
            # down subprocesses cleanly before we abandon them.
            wait(futures, timeout=5)
            for future in futures:
                future.cancel()
            raise

    # Re-order results by ctx.envs insertion order.
    return {name: results[name] for name, _ in env_items if name in results}
