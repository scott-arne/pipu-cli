"""Tests for the PostCheck helper."""

from io import StringIO

from rich.console import Console

from pipu_cli.cli import PostCheck
from pipu_cli.package_management import EnvReport


def _make_console() -> tuple[Console, StringIO]:
    buf = StringIO()
    return Console(file=buf, force_terminal=False, width=120, color_system=None), buf


def test_from_flags_disabled_when_no_check(monkeypatch):
    console, _ = _make_console()
    pc = PostCheck.from_flags(
        console=console, output="json",
        check_after_changes=True, no_check=True,
    )
    assert pc.enabled is False


def test_from_flags_disabled_when_config_off():
    console, _ = _make_console()
    pc = PostCheck.from_flags(
        console=console, output="json",
        check_after_changes=False, no_check=False,
    )
    assert pc.enabled is False


def test_run_disabled_does_not_invoke_build(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: (calls.append(kw), EnvReport(python_path=None, package_count=0))[1],
    )
    console, _ = _make_console()
    pc = PostCheck(console=console, output="json", enabled=False)
    payload = {"upgraded": []}
    result = pc.run(result=payload)
    assert calls == []
    assert "post_check" not in result


def test_run_json_embeds_post_check(monkeypatch):
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: EnvReport(python_path=kw.get("python_path"), package_count=7),
    )
    console, _ = _make_console()
    pc = PostCheck(console=console, output="json", enabled=True)
    payload = {"upgraded": []}
    result = pc.run(python_path="/p", result=payload)
    assert result["post_check"]["package_count"] == 7
    assert result["post_check"]["environment"] == "/p"


def test_run_human_prints_report(monkeypatch):
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: EnvReport(python_path=None, package_count=3),
    )
    console, buf = _make_console()
    pc = PostCheck(console=console, output="human", enabled=True)
    pc.run()
    assert "3 packages" in buf.getvalue()


def test_run_per_env_disabled_noop(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: (calls.append(kw), EnvReport(python_path=None, package_count=0))[1],
    )
    console, _ = _make_console()
    pc = PostCheck(console=console, output="human", enabled=False)
    pc.run_per_env({"main": "/p", "tools": "/q"})
    assert calls == []


def test_run_per_env_iterates_envs(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: (seen.append(kw["python_path"]), EnvReport(python_path=kw["python_path"], package_count=0))[1],
    )
    console, buf = _make_console()
    pc = PostCheck(console=console, output="human", enabled=True)
    pc.run_per_env({"main": "/p", "tools": "/q"})
    assert seen == ["/p", "/q"]
    # Banners rendered
    out = buf.getvalue()
    assert "Check: main" in out
    assert "Check: tools" in out
