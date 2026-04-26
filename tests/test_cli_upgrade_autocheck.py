"""Auto-check wiring tests for pipu upgrade."""

import json

from click.testing import CliRunner

from pipu_cli.cli import cli
from pipu_cli.package_management import DepProblem, EnvReport


def _stub_empty_upgrade(monkeypatch):
    """Neutralize upgrade so the test can focus on auto-check plumbing."""
    monkeypatch.setattr(
        "pipu_cli.cli.inspect_installed_packages",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "pipu_cli.package_management.inspect_installed_packages",
        lambda *a, **kw: [],
    )


def test_upgrade_auto_check_default_invokes(monkeypatch):
    _stub_empty_upgrade(monkeypatch)
    calls = {"count": 0}

    def fake_build(**kw):
        calls["count"] += 1
        return EnvReport(python_path=None, package_count=0)

    monkeypatch.setattr("pipu_cli.cli.build_env_report", fake_build)
    result = CliRunner().invoke(cli, ["upgrade", "-y"])
    # Exit code comes from upgrade (nothing to upgrade → 0), not auto-check.
    assert result.exit_code == 0
    assert calls["count"] == 1


def test_upgrade_no_check_skips(monkeypatch):
    _stub_empty_upgrade(monkeypatch)
    calls = {"count": 0}
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: (calls.__setitem__("count", calls["count"] + 1),
                      EnvReport(python_path=None, package_count=0))[1],
    )
    result = CliRunner().invoke(cli, ["upgrade", "-y", "--no-check"])
    assert result.exit_code == 0
    assert calls["count"] == 0


def test_upgrade_auto_check_does_not_change_exit_code(monkeypatch):
    _stub_empty_upgrade(monkeypatch)
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: EnvReport(
            python_path=None, package_count=5,
            problems=[DepProblem(kind="missing", package="p", detail="p missing")],
        ),
    )
    result = CliRunner().invoke(cli, ["upgrade", "-y"])
    # Auto-check found a problem, but the upgrade itself succeeded → exit 0.
    assert result.exit_code == 0


def test_upgrade_json_has_post_check(monkeypatch):
    _stub_empty_upgrade(monkeypatch)
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: EnvReport(python_path=None, package_count=3),
    )
    result = CliRunner().invoke(cli, ["upgrade", "-y", "-o", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "post_check" in payload
    assert payload["post_check"]["package_count"] == 3


def test_upgrade_json_no_check_omits_post_check(monkeypatch):
    _stub_empty_upgrade(monkeypatch)
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: EnvReport(python_path=None, package_count=3),
    )
    result = CliRunner().invoke(cli, ["upgrade", "-y", "-o", "json", "--no-check"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "post_check" not in payload
