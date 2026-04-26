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


class _FakeGroupCtx:
    def __init__(self, name, envs):
        self.name = name
        self.envs = envs


def test_check_group_human(monkeypatch, make_installed_packages):
    env_main = make_installed_packages(("foo", "1.0.0", {}))
    env_tools = make_installed_packages(
        ("foo", "1.0.0", {"bar": ">=1"}),  # missing dep
    )

    def fake_inspect(*, python_path=None, **kw):
        return env_main if "main" in (python_path or "") else env_tools

    monkeypatch.setattr("pipu_cli.package_management.inspect_installed_packages", fake_inspect)
    monkeypatch.setattr(
        "pipu_cli.cli.prepare_group",
        lambda name, **kw: _FakeGroupCtx(
            name, {"main": "/envs/main/python", "tools": "/envs/tools/python"}
        ),
    )
    result = CliRunner().invoke(cli, ["check", "-g", "prod"])
    # 'tools' env has a problem; group exit is 1.
    assert result.exit_code == 1
    assert "main" in result.output
    assert "tools" in result.output


def test_check_group_json(monkeypatch, make_installed_packages):
    pkgs = make_installed_packages(("foo", "1.0.0", {}))
    monkeypatch.setattr(
        "pipu_cli.package_management.inspect_installed_packages",
        lambda *a, **kw: pkgs,
    )
    monkeypatch.setattr(
        "pipu_cli.cli.prepare_group",
        lambda name, **kw: _FakeGroupCtx(
            name, {"main": "/envs/main/python", "tools": "/envs/tools/python"}
        ),
    )
    result = CliRunner().invoke(cli, ["check", "-g", "prod", "-o", "json"], catch_exceptions=False)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["group"] == "prod"
    assert [e["env"] for e in payload["environments"]] == ["main", "tools"]


def test_check_fix_auto_deletes_orphan(patch_inspect, make_installed_packages, monkeypatch):
    import pipu_cli.package_management as pm
    packages = make_installed_packages(("foo", "1.0.0", {}))
    patch_inspect(packages)
    monkeypatch.setitem(
        pm._ORPHAN_METADATA_CACHE, "",
        {"foo": [{"version": "1.0.0", "path": "/tmp/foo.egg-info"}]},
    )
    removed = []
    monkeypatch.setattr("pipu_cli._fix_cli.shutil.rmtree",
                        lambda p: removed.append(p))
    monkeypatch.setattr("pipu_cli._fix_cli.save_state",
                        lambda pkgs, desc: None)

    result = CliRunner().invoke(cli, ["check", "--fix"], catch_exceptions=False)
    assert result.exit_code == 0
    assert removed == ["/tmp/foo.egg-info"]
    assert "deleted" in result.output


def test_check_fix_json_embeds_fixes(patch_inspect, make_installed_packages, monkeypatch):
    import pipu_cli.package_management as pm
    packages = make_installed_packages(("foo", "1.0.0", {}))
    patch_inspect(packages)
    monkeypatch.setitem(
        pm._ORPHAN_METADATA_CACHE, "",
        {"foo": [{"version": "1.0.0", "path": "/tmp/foo.egg-info"}]},
    )
    monkeypatch.setattr("pipu_cli._fix_cli.shutil.rmtree", lambda p: None)
    monkeypatch.setattr("pipu_cli._fix_cli.save_state", lambda *a, **kw: None)

    result = CliRunner().invoke(cli, ["check", "--fix", "-o", "json"],
                                catch_exceptions=False)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "fixes" in payload
    assert "fix_summary" in payload
    assert payload["fix_summary"]["applied"] == 1


def test_check_fix_interactive_json_is_rejected():
    result = CliRunner().invoke(
        cli, ["check", "--fix", "--interactive", "-o", "json"],
    )
    assert result.exit_code == 1
    assert "--interactive" in result.output


def test_check_without_fix_is_read_only(patch_inspect, make_installed_packages, monkeypatch):
    packages = make_installed_packages(
        ("foo", "1.0.0", {"bar": ">=1"}),
    )
    patch_inspect(packages)
    monkeypatch.setattr("pipu_cli._fix_cli.shutil.rmtree",
                        lambda p: (_ for _ in ()).throw(
                            AssertionError("should not delete")))
    result = CliRunner().invoke(cli, ["check"])
    assert result.exit_code == 1
    assert "Fix summary" not in result.output
    assert "Fixing" not in result.output
