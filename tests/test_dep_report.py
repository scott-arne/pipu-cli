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
