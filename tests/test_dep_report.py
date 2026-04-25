"""Tests for build_dep_report and its data model."""

from typing import List

import pytest
from packaging.version import Version

from pipu_cli.package_management import (
    DepEdge,
    DepNode,
    DepProblem,
    DepReport,
    InstalledPackage,
    PackageNotInstalledError,
    build_dep_report,
)


def test_dep_edge_is_frozen_dataclass():
    edge = DepEdge(
        name="urllib3",
        installed_version=Version("2.2.2"),
        specifier="<3,>=1.21",
    )
    assert edge.name == "urllib3"
    assert edge.specifier == "<3,>=1.21"
    assert edge.is_editable is False
    assert edge.editable_location is None
    with pytest.raises(Exception):
        edge.name = "other"  # type: ignore[misc]  # frozen


def test_dep_node_default_empty_children():
    edge = DepEdge(name="foo", installed_version=None, specifier="")
    node = DepNode(edge=edge)
    assert node.children == []
    assert node.is_cycle is False


def test_dep_problem_kinds_are_strings():
    p = DepProblem(kind="missing", package="foo", detail="foo is missing")
    assert p.kind == "missing"
    assert p.required_by is None


def test_package_not_installed_error_carries_name():
    err = PackageNotInstalledError("missingpkg")
    assert err.name == "missingpkg"
    assert "missingpkg" in str(err)


def test_build_dep_report_raises_when_package_absent(make_installed_packages):
    installed = make_installed_packages(("foo", "1.0.0", {}))
    with pytest.raises(PackageNotInstalledError) as exc:
        build_dep_report("missingpkg", installed=installed)
    assert exc.value.name == "missingpkg"


def test_build_dep_report_canonicalizes_name(make_installed_packages):
    installed = make_installed_packages(("My-Pkg", "1.0.0", {}))
    report = build_dep_report("my_pkg", installed=installed)  # fuzzy form
    assert report.package.name == "my-pkg"


def test_build_dep_report_depth1_both_branches(make_installed_packages):
    installed = make_installed_packages(
        ("requests", "2.31.0", {"urllib3": "<3,>=1.21", "idna": "<4,>=2.5"}),
        ("urllib3", "2.2.2", {}),
        ("idna", "3.7", {}),
        ("httpx", "0.27.0", {"requests": ">=2.28"}),
    )
    report = build_dep_report("requests", installed=installed)
    assert report.package.name == "requests"

    req_by_names = [n.edge.name for n in report.required_by]
    assert req_by_names == ["httpx"]
    assert report.required_by[0].edge.specifier == ">=2.28"
    assert report.required_by[0].edge.installed_version == Version("0.27.0")

    req_names = sorted(n.edge.name for n in report.requires)
    assert req_names == ["idna", "urllib3"]

    # depth=1 -> no grandchildren
    for n in report.required_by + report.requires:
        assert n.children == []

    assert report.problems == []


def test_build_dep_report_empty_branches(make_installed_packages):
    installed = make_installed_packages(("lonely", "1.0.0", {}))
    report = build_dep_report("lonely", installed=installed)
    assert report.required_by == []
    assert report.requires == []
    assert report.problems == []
