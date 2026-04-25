"""End-to-end CLI tests for pipu deps."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from packaging.version import Version

from pipu_cli.cli import cli
from pipu_cli.package_management import InstalledPackage


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


def _clean_env(make):
    return make(
        ("requests", "2.31.0", {"urllib3": "<3,>=1.21"}),
        ("urllib3", "2.2.2", {}),
    )


def test_deps_happy_path(patch_inspect, make_installed_packages):
    patch_inspect(_clean_env(make_installed_packages))
    result = CliRunner().invoke(cli, ["deps", "requests"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "requests" in result.output
    assert "urllib3" in result.output
    assert "Problems" not in result.output


def test_deps_package_not_installed(patch_inspect, make_installed_packages):
    patch_inspect(_clean_env(make_installed_packages))
    result = CliRunner().invoke(cli, ["deps", "doesnotexist"])
    assert result.exit_code == 1
    assert "doesnotexist" in result.output
    assert "not installed" in result.output.lower()


def test_deps_json_output_shape(patch_inspect, make_installed_packages):
    patch_inspect(_clean_env(make_installed_packages))
    result = CliRunner().invoke(cli, ["deps", "requests", "-o", "json"], catch_exceptions=False)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["package"]["name"] == "requests"
    assert payload["depth"] == 1
    assert isinstance(payload["required_by"], list)
    assert isinstance(payload["requires"], list)
    assert payload["problems"] == []


def test_deps_json_on_package_not_installed(patch_inspect, make_installed_packages):
    patch_inspect(_clean_env(make_installed_packages))
    result = CliRunner().invoke(cli, ["deps", "nope", "-o", "json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload == {"error": "package-not-installed", "package": "nope"}


def test_deps_check_clean_exits_zero(patch_inspect, make_installed_packages):
    patch_inspect(_clean_env(make_installed_packages))
    result = CliRunner().invoke(cli, ["deps", "requests", "--check"], catch_exceptions=False)
    assert result.exit_code == 0


def test_deps_check_with_problems_exits_one(patch_inspect, make_installed_packages):
    broken = make_installed_packages(
        ("requests", "2.31.0", {"urllib3": "<2"}),
        ("urllib3", "2.2.2", {}),
    )
    patch_inspect(broken)
    result = CliRunner().invoke(cli, ["deps", "requests", "--check"])
    assert result.exit_code == 1
    assert "Problems" in result.output


def test_deps_depth_3(patch_inspect, make_installed_packages):
    pkgs = make_installed_packages(
        ("a", "1.0", {"b": ">=1"}),
        ("b", "1.0", {"c": ">=1"}),
        ("c", "1.0", {"d": ">=1"}),
        ("d", "1.0", {}),
    )
    patch_inspect(pkgs)
    result = CliRunner().invoke(cli, ["deps", "a", "--depth", "3", "-o", "json"], catch_exceptions=False)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["depth"] == 3
    b = payload["requires"][0]
    c = b["children"][0]
    d = c["children"][0]
    assert d["name"] == "d"


def test_deps_depth_unlimited(patch_inspect, make_installed_packages):
    pkgs = make_installed_packages(
        ("a", "1.0", {"b": ">=1"}),
        ("b", "1.0", {"c": ">=1"}),
        ("c", "1.0", {}),
    )
    patch_inspect(pkgs)
    result = CliRunner().invoke(cli, ["deps", "a", "--depth", "0", "-o", "json"], catch_exceptions=False)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["requires"][0]["children"][0]["name"] == "c"


def test_deps_group_mode_human(monkeypatch, make_installed_packages):
    env_main = _clean_env(make_installed_packages)
    env_tools = make_installed_packages(
        ("requests", "2.31.0", {}),
    )
    calls = {}

    def fake_inspect(*, python_path=None, **kw):
        return env_main if "main" in (python_path or "") else env_tools

    monkeypatch.setattr("pipu_cli.package_management.inspect_installed_packages", fake_inspect)
    monkeypatch.setattr(
        "pipu_cli.cli.prepare_group",
        lambda name, **kw: _FakeGroupCtx(
            name, {"main": "/envs/main/python", "tools": "/envs/tools/python"}
        ),
    )
    result = CliRunner().invoke(cli, ["deps", "requests", "-g", "prod"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "main" in result.output
    assert "tools" in result.output


def test_deps_group_mode_json(monkeypatch, make_installed_packages):
    env = _clean_env(make_installed_packages)
    monkeypatch.setattr(
        "pipu_cli.package_management.inspect_installed_packages",
        lambda *a, **kw: env,
    )
    monkeypatch.setattr(
        "pipu_cli.cli.prepare_group",
        lambda name, **kw: _FakeGroupCtx(
            name, {"main": "/envs/main/python", "tools": "/envs/tools/python"}
        ),
    )
    result = CliRunner().invoke(
        cli, ["deps", "requests", "-g", "prod", "-o", "json"], catch_exceptions=False
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["group"] == "prod"
    assert [e["env"] for e in payload["environments"]] == ["main", "tools"]


def test_deps_group_check_nonzero_when_any_env_has_problems(monkeypatch, make_installed_packages):
    healthy = _clean_env(make_installed_packages)
    broken = make_installed_packages(
        ("requests", "2.31.0", {"urllib3": "<2"}),
        ("urllib3", "2.2.2", {}),
    )

    def fake_inspect(*, python_path=None, **kw):
        return healthy if "main" in (python_path or "") else broken

    monkeypatch.setattr("pipu_cli.package_management.inspect_installed_packages", fake_inspect)
    monkeypatch.setattr(
        "pipu_cli.cli.prepare_group",
        lambda name, **kw: _FakeGroupCtx(
            name, {"main": "/envs/main/python", "tools": "/envs/tools/python"}
        ),
    )
    result = CliRunner().invoke(cli, ["deps", "requests", "-g", "prod", "--check"])
    assert result.exit_code == 1
    # Both envs rendered before exiting.
    assert "main" in result.output
    assert "tools" in result.output


class _FakeGroupCtx:
    def __init__(self, name, envs):
        self.name = name
        self.envs = envs
