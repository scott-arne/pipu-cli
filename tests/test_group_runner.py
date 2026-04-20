"""Tests for pipu_cli._group_runner.

Covers the shared orchestration helpers ``prepare_group`` and
``run_per_env_parallel``. For the InterruptToken propagation semantics
see ``tests/test_subprocess_runner.py``; this suite focuses on the runner
layer's contract.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import click
import pytest
from rich.console import Console

from pipu_cli._group_runner import GroupContext, prepare_group, run_per_env_parallel
from pipu_cli._subprocess import InterruptToken


def test_run_per_env_parallel_orders_by_ctx_envs():
    """Results are keyed in ctx.envs insertion order regardless of completion."""
    ctx = GroupContext(
        name="g",
        envs={"a": "/py/a", "b": "/py/b", "c": "/py/c"},
    )

    # Make 'a' intentionally the slowest completer so completion order
    # would differ from insertion order if the runner cared.
    delays = {"a": 0.15, "b": 0.05, "c": 0.02}

    def worker(name: str, path: str, token: InterruptToken) -> str:
        time.sleep(delays[name])
        return path

    out = run_per_env_parallel(ctx, worker)
    assert list(out.keys()) == ["a", "b", "c"]
    assert out == {"a": "/py/a", "b": "/py/b", "c": "/py/c"}


def test_run_per_env_parallel_propagates_keyboardinterrupt():
    """First worker raising KeyboardInterrupt flips the token and re-raises."""
    ctx = GroupContext(
        name="g",
        envs={"a": "/py/a", "b": "/py/b", "c": "/py/c"},
    )

    started = threading.Event()
    seen_set: dict[str, bool] = {}

    def worker(name: str, path: str, token: InterruptToken) -> str:
        if name == "a":
            started.set()
            # Raising KeyboardInterrupt from a pool worker surfaces to
            # the main thread via future.result().
            raise KeyboardInterrupt
        # Other workers wait briefly for the interrupt, then record whether
        # they saw the token flip.
        started.wait(timeout=2)
        # Poll for up to ~1s so the test isn't flaky under load.
        for _ in range(100):
            if token.is_set():
                break
            time.sleep(0.01)
        seen_set[name] = token.is_set()
        return path

    with pytest.raises(KeyboardInterrupt):
        run_per_env_parallel(ctx, worker)

    # At least one of the non-'a' workers should have observed the flip.
    # We don't assert on both because once cancel() fires, an un-started
    # worker may never run.
    assert any(seen_set.values()) or not seen_set, (
        f"Expected at least one worker to see token.is_set() after cancel, "
        f"got {seen_set!r}"
    )


def test_run_per_env_parallel_empty_envs_returns_empty():
    """An empty group produces an empty result dict with no pool churn."""
    ctx = GroupContext(name="empty", envs={})

    def worker(name, path, token):  # pragma: no cover - unreachable
        raise AssertionError("worker must not be called for empty ctx")

    assert run_per_env_parallel(ctx, worker) == {}


def test_prepare_group_raises_on_missing_group():
    """When get_group returns None, prepare_group raises ClickException."""
    console = Console()
    with patch("pipu_cli._group_runner.get_group", return_value=None):
        with pytest.raises(click.ClickException) as exc_info:
            prepare_group("nogroup", console=console, output="human")
    assert "nogroup" in exc_info.value.message
    assert exc_info.value.exit_code == 1


def test_prepare_group_json_output_prints_error_json(capsys):
    """In JSON mode the missing-group error is emitted as structured JSON.

    The raise uses ``click.exceptions.Exit`` so that Click doesn't prepend
    an ``Error:`` line to stderr on top of the structured payload.
    """
    with patch("pipu_cli._group_runner.get_group", return_value=None):
        with pytest.raises(click.exceptions.Exit) as exc_info:
            prepare_group("nogroup", console=None, output="json")
    assert exc_info.value.exit_code == 1
    captured = capsys.readouterr()
    assert '"error"' in captured.out
    assert "nogroup" in captured.out


def test_prepare_group_filters_invalid_envs():
    """Paths failing os.path.exists are dropped with a warning; valid ones survive."""
    console = Console()

    def fake_exists(path):
        return path != "/python/missing"

    with patch(
        "pipu_cli._group_runner.get_group",
        return_value=["/python/a", "/python/missing", "/python/b"],
    ), patch("os.path.exists", side_effect=fake_exists):
        ctx = prepare_group("mygroup", console=console, output="human")

    assert ctx is not None
    assert ctx.name == "mygroup"
    # /python/missing should have been filtered out.
    assert "/python/missing" not in ctx.envs.values()
    assert "/python/a" in ctx.envs.values()
    assert "/python/b" in ctx.envs.values()
    assert len(ctx.envs) == 2


def test_prepare_group_all_invalid_exits():
    """If every env path is invalid the runner raises with a friendly message."""
    console = Console()
    with patch(
        "pipu_cli._group_runner.get_group",
        return_value=["/python/gone1", "/python/gone2"],
    ), patch("os.path.exists", return_value=False):
        with pytest.raises(click.ClickException) as exc_info:
            prepare_group("mygroup", console=console, output="human")
    assert exc_info.value.exit_code == 1
    assert "No valid environments" in exc_info.value.message
