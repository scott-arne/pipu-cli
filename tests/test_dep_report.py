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


def test_problems_missing_dependency(make_installed_packages):
    installed = make_installed_packages(
        ("requests", "2.31.0", {"urllib3": "<3,>=1.21"}),
        # urllib3 intentionally absent
    )
    report = build_dep_report("requests", installed=installed)
    assert len(report.problems) == 1
    p = report.problems[0]
    assert p.kind == "missing"
    assert p.package == "urllib3"
    assert "urllib3" in p.detail
    assert "requests" in p.detail


def test_problems_version_violates_constraint(make_installed_packages):
    installed = make_installed_packages(
        ("requests", "2.31.0", {"urllib3": "<2"}),
        ("urllib3", "2.2.2", {}),
    )
    report = build_dep_report("requests", installed=installed)
    assert len(report.problems) == 1
    p = report.problems[0]
    assert p.kind == "violates"
    assert p.package == "urllib3"
    assert p.required_by == "requests"
    assert p.specifier == "<2"
    assert p.installed_version == Version("2.2.2")
    assert "urllib3" in p.detail
    assert "<2" in p.detail


def test_problems_violates_on_required_by_side(make_installed_packages):
    """A parent that imposes a constraint PACKAGE fails to satisfy is also caught."""
    installed = make_installed_packages(
        ("requests", "2.31.0", {}),
        ("httpx", "0.27.0", {"requests": ">=3.0"}),  # violated
    )
    report = build_dep_report("requests", installed=installed)
    assert any(
        p.kind == "violates" and p.package == "requests" and p.required_by == "httpx"
        for p in report.problems
    )


def test_problems_broken_editable(make_installed_packages):
    installed = make_installed_packages(
        ("mylib", "0.1.0", {}, {"is_editable": True, "editable_location": "/nope"}),
    )
    report = build_dep_report(
        "mylib", installed=installed,
        editable_exists=lambda p: False,
    )
    assert any(
        p.kind == "broken-editable" and p.package == "mylib" for p in report.problems
    )


def test_problems_dedup_same_constraint_across_parents(make_installed_packages):
    installed = make_installed_packages(
        ("urllib3", "2.2.2", {}),
        ("a", "1.0", {"urllib3": "<2"}),
        ("b", "1.0", {"urllib3": "<2"}),
        ("c", "1.0", {"urllib3": "<2"}),
    )
    report = build_dep_report("urllib3", installed=installed)
    violates = [p for p in report.problems if p.kind == "violates"]
    # Each parent imposes the same constraint but with distinct required_by;
    # we want one per (kind, package, required_by, specifier) tuple, so 3.
    assert len(violates) == 3
    assert sorted(p.required_by for p in violates) == ["a", "b", "c"]


def test_problems_dedup_exact_duplicate(make_installed_packages):
    """A constraint appearing twice (e.g. reachable via two tree paths) dedups."""
    installed = make_installed_packages(
        ("urllib3", "2.2.2", {}),
        ("a", "1.0", {"urllib3": "<2"}),
    )
    report = build_dep_report("urllib3", installed=installed, depth=0)
    violates = [p for p in report.problems if p.kind == "violates" and p.required_by == "a"]
    assert len(violates) == 1


def test_problems_sort_order(make_installed_packages):
    installed = make_installed_packages(
        (
            "subj", "1.0.0",
            {"absent-dep": ">=1", "bad-ver": "<1"},
            {"is_editable": True, "editable_location": "/nope"},
        ),
        ("bad-ver", "2.0.0", {}),
    )
    report = build_dep_report(
        "subj", installed=installed,
        editable_exists=lambda p: False,
    )
    kinds = [p.kind for p in report.problems]
    # missing first, violates next, broken-editable last
    assert kinds.index("missing") < kinds.index("violates")
    assert kinds.index("violates") < kinds.index("broken-editable")


def test_depth_2_recurses_one_more_hop(make_installed_packages):
    installed = make_installed_packages(
        ("a", "1.0", {"b": ">=1"}),
        ("b", "1.0", {"c": ">=1"}),
        ("c", "1.0", {}),
    )
    report = build_dep_report("a", installed=installed, depth=2)
    # requires: a -> b -> c
    assert len(report.requires) == 1
    b_node = report.requires[0]
    assert b_node.edge.name == "b"
    assert [n.edge.name for n in b_node.children] == ["c"]


def test_depth_3_three_hops(make_installed_packages):
    installed = make_installed_packages(
        ("a", "1.0", {"b": ">=1"}),
        ("b", "1.0", {"c": ">=1"}),
        ("c", "1.0", {"d": ">=1"}),
        ("d", "1.0", {}),
    )
    report = build_dep_report("a", installed=installed, depth=3)
    b = report.requires[0]
    c = b.children[0]
    d = c.children[0]
    assert d.edge.name == "d"
    assert d.children == []


def test_depth_0_unlimited(make_installed_packages):
    installed = make_installed_packages(
        ("a", "1.0", {"b": ">=1"}),
        ("b", "1.0", {"c": ">=1"}),
        ("c", "1.0", {}),
    )
    report = build_dep_report("a", installed=installed, depth=0)
    assert report.requires[0].children[0].edge.name == "c"


def test_cycle_terminates_cleanly(make_installed_packages):
    installed = make_installed_packages(
        ("a", "1.0", {"b": ">=1"}),
        ("b", "1.0", {"a": ">=1"}),  # cycle
    )
    report = build_dep_report("a", installed=installed, depth=0)
    b = report.requires[0]
    assert b.edge.name == "b"
    assert len(b.children) == 1
    cycle_node = b.children[0]
    assert cycle_node.edge.name == "a"
    assert cycle_node.is_cycle is True
    assert cycle_node.children == []


def test_negative_depth_raises(make_installed_packages):
    installed = make_installed_packages(("a", "1.0", {}))
    with pytest.raises(ValueError):
        build_dep_report("a", installed=installed, depth=-1)
