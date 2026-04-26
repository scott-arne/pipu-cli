"""Tests for EnvReport and build_env_report."""

import pytest

from pipu_cli.package_management import (
    DepProblem,
    EnvReport,
)


def test_env_report_defaults():
    report = EnvReport(python_path=None, package_count=0)
    assert report.python_path is None
    assert report.package_count == 0
    assert report.problems == []


def test_env_report_accepts_problems():
    problems = [
        DepProblem(kind="missing", package="foo", detail="foo not installed"),
    ]
    report = EnvReport(python_path="/usr/bin/python", package_count=5, problems=problems)
    assert report.python_path == "/usr/bin/python"
    assert report.package_count == 5
    assert report.problems == problems


def test_env_report_is_frozen():
    report = EnvReport(python_path=None, package_count=0)
    with pytest.raises(Exception):
        report.package_count = 10  # type: ignore[misc]


def test_build_env_report_clean(make_installed_packages):
    from pipu_cli.package_management import build_env_report

    installed = make_installed_packages(
        ("foo", "1.0.0", {"bar": ">=1"}),
        ("bar", "1.5.0", {}),
    )
    report = build_env_report(installed=installed)
    assert report.problems == []
    assert report.package_count == 2
    assert report.python_path is None


def test_build_env_report_python_path_round_trip(make_installed_packages):
    from pipu_cli.package_management import build_env_report

    installed = make_installed_packages(("foo", "1.0.0", {}))
    report = build_env_report(installed=installed, python_path="/other/python")
    assert report.python_path == "/other/python"
    assert report.package_count == 1


def test_build_env_report_empty_env():
    from pipu_cli.package_management import build_env_report

    report = build_env_report(installed=[])
    assert report.package_count == 0
    assert report.problems == []


def test_env_missing_dependency(make_installed_packages):
    from pipu_cli.package_management import build_env_report

    installed = make_installed_packages(
        ("requests", "2.31.0", {"urllib3": "<3,>=1.21"}),
    )
    report = build_env_report(installed=installed)
    missing = [p for p in report.problems if p.kind == "missing"]
    assert len(missing) == 1
    assert missing[0].package == "urllib3"


def test_env_violates(make_installed_packages):
    from pipu_cli.package_management import build_env_report

    installed = make_installed_packages(
        ("requests", "2.31.0", {"urllib3": "<2"}),
        ("urllib3", "2.2.2", {}),
    )
    report = build_env_report(installed=installed)
    violates = [p for p in report.problems if p.kind == "violates"]
    assert len(violates) == 1
    assert violates[0].package == "urllib3"
    assert violates[0].required_by == "requests"


def test_env_broken_editable(make_installed_packages):
    from pipu_cli.package_management import build_env_report

    installed = make_installed_packages(
        ("mylib", "0.1.0", {}, {"is_editable": True, "editable_location": "/nope"}),
    )
    report = build_env_report(installed=installed, editable_exists=lambda p: False)
    broken = [p for p in report.problems if p.kind == "broken-editable"]
    assert len(broken) == 1
    assert broken[0].package == "mylib"


def test_env_duplicate_install(make_installed_packages):
    from pipu_cli.package_management import build_env_report

    installed = make_installed_packages(
        ("foo", "1.0.0", {}),
        ("foo", "1.0.1", {}),
    )
    report = build_env_report(installed=installed)
    dupes = [p for p in report.problems if p.kind == "duplicate-install"]
    assert len(dupes) == 1
    assert dupes[0].package == "foo"
    assert "1.0.0" in dupes[0].detail and "1.0.1" in dupes[0].detail


def test_env_stale_metadata(make_installed_packages, monkeypatch):
    from pipu_cli import package_management as pm
    from pipu_cli.package_management import build_env_report

    installed = make_installed_packages(("foo", "1.0.0", {}))
    monkeypatch.setitem(
        pm._ORPHAN_METADATA_CACHE, "",
        {"foo": [{"version": "1.0.0", "path": "/old/foo.egg-info"}]},
    )
    report = build_env_report(installed=installed)
    stale = [p for p in report.problems if p.kind == "stale-metadata"]
    assert len(stale) == 1
    assert stale[0].package == "foo"
    assert "/old/foo.egg-info" in stale[0].detail


def test_env_mixed_problems_sort_order(make_installed_packages, monkeypatch):
    from pipu_cli import package_management as pm
    from pipu_cli.package_management import build_env_report

    installed = make_installed_packages(
        ("a", "1.0.0", {"absent": ">=1", "bad": "<1"}),
        ("bad", "2.0.0", {}),
        ("dup", "1.0.0", {}),
        ("dup", "1.0.1", {}),
        ("ed", "1.0.0", {}, {"is_editable": True, "editable_location": "/nope"}),
    )
    monkeypatch.setitem(
        pm._ORPHAN_METADATA_CACHE, "",
        {"ghost": [{"version": "0.1", "path": "/old/ghost.egg-info"}]},
    )
    report = build_env_report(installed=installed, editable_exists=lambda p: False)
    kinds = [p.kind for p in report.problems]
    # Order: missing → violates → broken-editable → duplicate-install → stale-metadata.
    kind_order = {
        "missing": 0,
        "violates": 1,
        "broken-editable": 2,
        "duplicate-install": 3,
        "stale-metadata": 4,
    }
    assert kinds == sorted(kinds, key=lambda k: kind_order[k])
    # All five kinds present at least once.
    assert set(kinds) == {"missing", "violates", "broken-editable", "duplicate-install", "stale-metadata"}


def test_env_package_count_counts_duplicates(make_installed_packages):
    from pipu_cli.package_management import build_env_report

    installed = make_installed_packages(
        ("foo", "1.0.0", {}),
        ("foo", "1.0.1", {}),
        ("bar", "1.0.0", {}),
    )
    report = build_env_report(installed=installed)
    assert report.package_count == 3
