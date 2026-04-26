"""Tests for the fixer module."""

from packaging.version import Version

from pipu_cli.fixer import FixResult, apply_stale_metadata_fix, build_fix_plan
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


def _stale_problem(package="cnotebook", path="/old/cnotebook.egg-info") -> DepProblem:
    return DepProblem(
        kind="stale-metadata", package=package,
        detail=f"{package} has orphaned metadata: {path}",
    )


def test_apply_stale_metadata_fix_succeeds():
    removed = []
    path = "/old/cnotebook.egg-info"
    result = apply_stale_metadata_fix(
        _stale_problem(path=path),
        python_path=None,
        verifier=lambda pp: {"cnotebook": [{"path": path, "version": "0.1"}]},
        remover=lambda p: removed.append(p),
    )
    assert removed == [path]
    assert result.status == "succeeded"
    assert result.action == "delete"
    assert result.target == path
    assert result.detail is None


def test_apply_stale_metadata_fix_skips_when_no_longer_orphan():
    removed = []
    result = apply_stale_metadata_fix(
        _stale_problem(),
        python_path=None,
        verifier=lambda pp: {},
        remover=lambda p: removed.append(p),
    )
    assert removed == []
    assert result.status == "skipped"
    assert "no longer classified as orphan" in (result.detail or "")


def test_apply_stale_metadata_fix_skips_when_path_suffix_unknown():
    removed = []
    bad = "/old/cnotebook/random.txt"
    result = apply_stale_metadata_fix(
        _stale_problem(path=bad),
        python_path=None,
        verifier=lambda pp: {"cnotebook": [{"path": bad, "version": "0.1"}]},
        remover=lambda p: removed.append(p),
    )
    assert removed == []
    assert result.status == "skipped"


def test_apply_stale_metadata_fix_fails_when_remover_raises():
    def boom(p: str) -> None:
        raise PermissionError("denied")

    result = apply_stale_metadata_fix(
        _stale_problem(),
        python_path=None,
        verifier=lambda pp: {
            "cnotebook": [{"path": "/old/cnotebook.egg-info", "version": "0.1"}],
        },
        remover=boom,
    )
    assert result.status == "failed"
    assert "denied" in (result.detail or "")
