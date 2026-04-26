"""Unit tests for the _maybe_run_auto_check helper."""

from io import StringIO

from rich.console import Console

from pipu_cli.cli import _maybe_run_auto_check
from pipu_cli.package_management import EnvReport


def _make_console() -> tuple[Console, StringIO]:
    buf = StringIO()
    return Console(file=buf, force_terminal=False, width=120, color_system=None), buf


def test_no_check_flag_skips(monkeypatch, make_installed_packages):
    # Spy on build_env_report to confirm it never runs.
    calls = []
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: (calls.append(kw), EnvReport(python_path=None, package_count=0))[1],
    )
    console, _ = _make_console()
    payload = {"upgraded": []}
    result = _maybe_run_auto_check(
        console=console, output="json", python_path=None,
        check_after_changes=True, no_check=True, result=payload,
    )
    assert calls == []
    assert "post_check" not in result


def test_config_false_skips(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: (calls.append(kw), EnvReport(python_path=None, package_count=0))[1],
    )
    console, _ = _make_console()
    payload = {"upgraded": []}
    result = _maybe_run_auto_check(
        console=console, output="json", python_path=None,
        check_after_changes=False, no_check=False, result=payload,
    )
    assert calls == []
    assert "post_check" not in result


def test_enabled_json_embeds_post_check(monkeypatch):
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: EnvReport(python_path=kw.get("python_path"), package_count=7),
    )
    console, _ = _make_console()
    payload = {"upgraded": []}
    result = _maybe_run_auto_check(
        console=console, output="json", python_path="/p",
        check_after_changes=True, no_check=False, result=payload,
    )
    assert "post_check" in result
    assert result["post_check"]["package_count"] == 7
    assert result["post_check"]["environment"] == "/p"


def test_enabled_human_prints_report(monkeypatch):
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: EnvReport(python_path=None, package_count=3),
    )
    console, buf = _make_console()
    payload = {"upgraded": []}
    _maybe_run_auto_check(
        console=console, output="human", python_path=None,
        check_after_changes=True, no_check=False, result=payload,
    )
    assert "3 packages" in buf.getvalue()
