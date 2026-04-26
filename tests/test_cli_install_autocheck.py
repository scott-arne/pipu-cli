"""Auto-check wiring tests for pipu install."""

import json

from click.testing import CliRunner
from packaging.version import Version

from pipu_cli.cli import cli
from pipu_cli.package_management import EnvReport, InstalledResult


def _stub_install(monkeypatch):
    """Neutralize the install path so focus is on auto-check plumbing."""
    monkeypatch.setattr(
        "pipu_cli.cli.run_pip_install",
        lambda *a, **kw: [
            InstalledResult(
                name="foo", version=Version("1.0.0"),
                installed=True, previous_version=None,
            ),
        ],
    )


def test_install_auto_check_default_invokes(monkeypatch):
    _stub_install(monkeypatch)
    calls = {"count": 0}
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: (calls.__setitem__("count", calls["count"] + 1),
                      EnvReport(python_path=None, package_count=0))[1],
    )
    result = CliRunner().invoke(cli, ["install", "-y", "foo"])
    assert result.exit_code == 0
    assert calls["count"] == 1


def test_install_no_check_skips(monkeypatch):
    _stub_install(monkeypatch)
    calls = {"count": 0}
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: (calls.__setitem__("count", calls["count"] + 1),
                      EnvReport(python_path=None, package_count=0))[1],
    )
    result = CliRunner().invoke(cli, ["install", "-y", "--no-check", "foo"])
    assert result.exit_code == 0
    assert calls["count"] == 0


def test_install_json_has_post_check(monkeypatch):
    _stub_install(monkeypatch)
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: EnvReport(python_path=None, package_count=2),
    )
    result = CliRunner().invoke(cli, ["install", "-y", "-o", "json", "foo"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "post_check" in payload
    assert payload["post_check"]["package_count"] == 2
