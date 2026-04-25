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
