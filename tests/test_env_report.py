"""Tests for EnvReport and build_env_report."""

from typing import List

import pytest
from packaging.version import Version

from pipu_cli.package_management import (
    DepProblem,
    EnvReport,
    InstalledPackage,
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
