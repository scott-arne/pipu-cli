"""Tests for JSON serialization of DepReport."""

import json
from packaging.version import Version

from pipu_cli.package_management import (
    DepEdge,
    DepNode,
    DepProblem,
    DepReport,
    InstalledPackage,
)
from pipu_cli.output import dep_report_to_json, dep_report_group_to_json


def _report(*, problems=(), required_by=(), requires=()) -> DepReport:
    return DepReport(
        package=InstalledPackage(
            name="requests",
            version=Version("2.31.0"),
        ),
        required_by=list(required_by),
        requires=list(requires),
        problems=list(problems),
    )


def test_json_single_env_shape():
    node = DepNode(edge=DepEdge(
        name="urllib3",
        installed_version=Version("2.2.2"),
        specifier="<3,>=1.21",
    ))
    report = _report(requires=(node,))
    data = dep_report_to_json(report, depth=1)
    # Round-trip through JSON to make sure everything is serializable.
    payload = json.loads(json.dumps(data))

    assert payload["package"] == {
        "name": "requests",
        "version": "2.31.0",
        "is_editable": False,
        "editable_location": None,
    }
    assert payload["depth"] == 1
    assert payload["required_by"] == []
    assert len(payload["requires"]) == 1
    r = payload["requires"][0]
    assert r["name"] == "urllib3"
    assert r["installed_version"] == "2.2.2"
    assert r["specifier"] == "<3,>=1.21"
    assert r["children"] == []
    assert payload["problems"] == []


def test_json_missing_installed_version_is_null():
    node = DepNode(edge=DepEdge(name="ghost", installed_version=None, specifier=">=1"))
    data = dep_report_to_json(_report(requires=(node,)), depth=1)
    assert data["requires"][0]["installed_version"] is None


def test_json_problems_serialized():
    problem = DepProblem(
        kind="violates",
        package="urllib3",
        detail="urllib3 2.2.2 violates httpx<2",
        required_by="httpx",
        specifier="<2",
        installed_version=Version("2.2.2"),
    )
    data = dep_report_to_json(_report(problems=(problem,)), depth=1)
    p = data["problems"][0]
    assert p["kind"] == "violates"
    assert p["installed_version"] == "2.2.2"
    assert p["required_by"] == "httpx"


def test_json_cycle_node_shape():
    cycle = DepNode(
        edge=DepEdge(name="a", installed_version=Version("1.0"), specifier=">=1"),
        is_cycle=True,
    )
    data = dep_report_to_json(_report(requires=(cycle,)), depth=0)
    n = data["requires"][0]
    assert n.get("cycle") is True
    assert n["children"] == []


def test_json_group_shape():
    report = _report()
    data = dep_report_group_to_json(
        group_name="prod",
        per_env=[("main", report), ("tools", report)],
        depth=1,
    )
    assert data["group"] == "prod"
    assert [e["env"] for e in data["environments"]] == ["main", "tools"]
    assert data["environments"][0]["report"]["package"]["name"] == "requests"
