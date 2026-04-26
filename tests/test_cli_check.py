"""End-to-end CLI tests for pipu check."""

import json

import pytest
from click.testing import CliRunner

from pipu_cli.cli import cli


@pytest.fixture
def patch_inspect(monkeypatch, make_installed_packages):
    def _apply(packages):
        monkeypatch.setattr(
            "pipu_cli.cli.inspect_installed_packages",
            lambda *a, **kw: packages,
        )
        monkeypatch.setattr(
            "pipu_cli.package_management.inspect_installed_packages",
            lambda *a, **kw: packages,
        )
    return _apply


def test_check_clean_env(patch_inspect, make_installed_packages):
    packages = make_installed_packages(
        ("foo", "1.0.0", {"bar": ">=1"}),
        ("bar", "1.5.0", {}),
    )
    patch_inspect(packages)
    result = CliRunner().invoke(cli, ["check"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "No consistency problems" in result.output


def test_check_with_problems_exits_one(patch_inspect, make_installed_packages):
    packages = make_installed_packages(
        ("foo", "1.0.0", {"bar": "<1"}),
        ("bar", "2.0.0", {}),
    )
    patch_inspect(packages)
    result = CliRunner().invoke(cli, ["check"])
    assert result.exit_code == 1
    assert "Violates" in result.output


def test_check_json_clean(patch_inspect, make_installed_packages):
    packages = make_installed_packages(("foo", "1.0.0", {}))
    patch_inspect(packages)
    result = CliRunner().invoke(cli, ["check", "-o", "json"], catch_exceptions=False)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["total"] == 0
    for k in ("missing", "violates", "broken-editable", "duplicate-install", "stale-metadata"):
        assert k in payload["summary"]


def test_check_json_with_problems(patch_inspect, make_installed_packages):
    packages = make_installed_packages(
        ("foo", "1.0.0", {"bar": ">=1"}),
    )
    patch_inspect(packages)
    result = CliRunner().invoke(cli, ["check", "-o", "json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["summary"]["missing"] == 1


def test_check_by_package(patch_inspect, make_installed_packages):
    packages = make_installed_packages(
        ("foo", "1.0.0", {"bar": ">=1"}),
    )
    patch_inspect(packages)
    result = CliRunner().invoke(cli, ["check", "--by", "package"])
    assert result.exit_code == 1
    assert "bar" in result.output
    assert "missing" in result.output
