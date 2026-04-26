"""Auto-check wiring tests for pipu uninstall."""

import json

from click.testing import CliRunner
from packaging.version import Version

from pipu_cli.cli import cli
from pipu_cli.package_management import EnvReport, UninstalledResult


def _stub_uninstall(monkeypatch):
    monkeypatch.setattr(
        "pipu_cli.cli.run_pip_uninstall",
        lambda *a, **kw: [
            UninstalledResult(
                name="foo",
                previous_version=Version("1.0.0"),
                uninstalled=True,
            ),
        ],
    )


def test_uninstall_auto_check_default_invokes(monkeypatch):
    _stub_uninstall(monkeypatch)
    calls = {"count": 0}
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: (calls.__setitem__("count", calls["count"] + 1),
                      EnvReport(python_path=None, package_count=0))[1],
    )
    result = CliRunner().invoke(cli, ["uninstall", "-y", "foo"])
    assert result.exit_code == 0
    assert calls["count"] == 1


def test_uninstall_no_check_skips(monkeypatch):
    _stub_uninstall(monkeypatch)
    calls = {"count": 0}
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: (calls.__setitem__("count", calls["count"] + 1),
                      EnvReport(python_path=None, package_count=0))[1],
    )
    result = CliRunner().invoke(cli, ["uninstall", "-y", "--no-check", "foo"])
    assert result.exit_code == 0
    assert calls["count"] == 0


def test_uninstall_json_has_post_check(monkeypatch):
    _stub_uninstall(monkeypatch)
    monkeypatch.setattr(
        "pipu_cli.cli.build_env_report",
        lambda **kw: EnvReport(python_path=None, package_count=1),
    )
    result = CliRunner().invoke(cli, ["uninstall", "-y", "-o", "json", "foo"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "post_check" in payload
    assert payload["post_check"]["package_count"] == 1
