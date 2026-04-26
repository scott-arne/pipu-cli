"""Tests for the fixer module."""

from packaging.version import Version

from pipu_cli.fixer import FixResult, build_fix_plan
from pipu_cli.package_management import DepProblem, EnvReport


def test_fix_result_is_frozen():
    import pytest
    r = FixResult(
        problem=DepProblem(kind="missing", package="foo", detail="foo missing"),
        action="delete", target="/x", status="unfixable", detail=None,
    )
    with pytest.raises(Exception):
        r.status = "succeeded"  # type: ignore[misc]


def test_build_fix_plan_empty_report():
    report = EnvReport(python_path=None, package_count=0, problems=[])
    plan = build_fix_plan(report)
    assert plan.python_path is None
    assert plan.stale_metadata == []
    assert plan.violates == []
    assert plan.unfixable == []


def test_build_fix_plan_partitions_by_kind():
    problems = [
        DepProblem(kind="missing", package="pandas", detail="pandas missing"),
        DepProblem(kind="broken-editable", package="mylib", detail="broken"),
        DepProblem(kind="duplicate-install", package="foo", detail="duplicate"),
        DepProblem(kind="stale-metadata", package="cnotebook",
                   detail="cnotebook has orphaned metadata: /old"),
        DepProblem(kind="violates", package="urllib3",
                   detail="urllib3 2.2.2 violates httpx<2",
                   required_by="httpx", specifier="<2",
                   installed_version=Version("2.2.2")),
    ]
    report = EnvReport(python_path="/p", package_count=5, problems=problems)
    plan = build_fix_plan(report)
    assert plan.python_path == "/p"
    assert [p.package for p in plan.stale_metadata] == ["cnotebook"]
    assert [p.package for p in plan.violates] == ["urllib3"]
    assert sorted(p.kind for p in plan.unfixable) == [
        "broken-editable", "duplicate-install", "missing",
    ]


def test_build_fix_plan_sorts_within_kind():
    problems = [
        DepProblem(kind="stale-metadata", package="zeta", detail="zeta orphan"),
        DepProblem(kind="stale-metadata", package="alpha", detail="alpha orphan"),
        DepProblem(kind="violates", package="numpy",
                   detail="numpy 2.0 violates scipy<2", required_by="scipy",
                   specifier="<2", installed_version=Version("2.0")),
        DepProblem(kind="violates", package="flask",
                   detail="flask 3 violates app<3", required_by="app",
                   specifier="<3", installed_version=Version("3.0")),
    ]
    report = EnvReport(python_path=None, package_count=4, problems=problems)
    plan = build_fix_plan(report)
    assert [p.package for p in plan.stale_metadata] == ["alpha", "zeta"]
    assert [p.package for p in plan.violates] == ["flask", "numpy"]
