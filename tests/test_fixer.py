"""Tests for the fixer module."""

from packaging.version import Version

from pipu_cli.fixer import FixResult, apply_stale_metadata_fix, apply_violates_fix, build_fix_plan
from pipu_cli.package_management import DepProblem, EnvReport, InstalledResult


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


def _violates_problem(package="urllib3", spec="<2", installed="2.2.2",
                      required_by="httpx") -> DepProblem:
    return DepProblem(
        kind="violates", package=package,
        detail=f"{package} {installed} violates {required_by}{spec}",
        required_by=required_by, specifier=spec,
        installed_version=Version(installed),
    )


def test_apply_violates_fix_single_problem_succeeds():
    seen_calls = []

    def installer(*, package_specs, **kw):
        seen_calls.append((package_specs, kw))
        return [
            InstalledResult(
                name="urllib3", version=Version("1.26.20"),
                installed=True, previous_version=Version("2.2.2"),
                failure_reason=None,
            ),
        ]

    results = apply_violates_fix(
        [_violates_problem()],
        python_path=None, installer=installer,
    )
    assert len(results) == 1
    assert results[0].status == "succeeded"
    assert results[0].action == "install"
    assert results[0].target == "urllib3<2"
    assert seen_calls[0][0] == ["urllib3<2"]


def test_apply_violates_fix_merges_multiple_problems_on_one_package():
    seen_calls = []

    def installer(*, package_specs, **kw):
        seen_calls.append(package_specs)
        return [
            InstalledResult(
                name="urllib3", version=Version("1.26.20"),
                installed=True, previous_version=Version("2.2.2"),
                failure_reason=None,
            ),
        ]

    problems = [
        _violates_problem(spec="<2", required_by="httpx"),
        _violates_problem(spec="<3", required_by="requests"),
    ]
    results = apply_violates_fix(
        problems, python_path=None, installer=installer,
    )
    assert len(seen_calls) == 1
    assert seen_calls[0] == ["urllib3<2,<3"]
    assert len(results) == 2
    assert all(r.status == "succeeded" for r in results)


def test_apply_violates_fix_failed_install_marks_all_problems_failed():
    def installer(*, package_specs, **kw):
        return [
            InstalledResult(
                name="urllib3", version=Version("2.2.2"),
                installed=False, previous_version=Version("2.2.2"),
                failure_reason="ResolutionImpossible: conflicts with scipy",
            ),
        ]

    problems = [
        _violates_problem(spec="<2", required_by="httpx"),
        _violates_problem(spec="<3", required_by="requests"),
    ]
    results = apply_violates_fix(
        problems, python_path=None, installer=installer,
    )
    assert len(results) == 2
    assert all(r.status == "failed" for r in results)
    assert all("ResolutionImpossible" in (r.detail or "") for r in results)


def test_apply_violates_fix_installer_raises_marks_failed():
    def installer(*, package_specs, **kw):
        raise RuntimeError("pip exploded")

    results = apply_violates_fix(
        [_violates_problem()], python_path=None, installer=installer,
    )
    assert len(results) == 1
    assert results[0].status == "failed"
    assert "pip exploded" in (results[0].detail or "")


def test_apply_violates_fix_handles_multiple_packages_independently():
    calls = []

    def installer(*, package_specs, **kw):
        calls.append(package_specs)
        name = package_specs[0].split("<")[0].split(">")[0].split("=")[0]
        succeeded = name != "broken"
        return [
            InstalledResult(
                name=name, version=Version("1.0"),
                installed=succeeded, previous_version=Version("2.0"),
                failure_reason=None if succeeded else "no such distribution",
            ),
        ]

    problems = [
        _violates_problem(package="alpha", spec="<2"),
        _violates_problem(package="broken", spec="<2"),
    ]
    results = apply_violates_fix(
        problems, python_path=None, installer=installer,
    )
    assert len(calls) == 2
    statuses = {r.problem.package: r.status for r in results}
    assert statuses == {"alpha": "succeeded", "broken": "failed"}
