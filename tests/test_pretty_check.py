"""Snapshot-style tests for print_env_report."""

from io import StringIO

from packaging.version import Version
from rich.console import Console

from pipu_cli.package_management import DepProblem, EnvReport
from pipu_cli.pretty import print_env_report


def _render(report: EnvReport, group_by: str = "problem") -> str:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    print_env_report(console, report, group_by=group_by)
    return buf.getvalue()


def test_clean_env_shows_success_header():
    report = EnvReport(python_path=None, package_count=42)
    out = _render(report)
    assert "42 packages" in out
    assert "No consistency problems" in out
    assert "Missing" not in out  # no tree branches


def test_by_problem_groups_by_kind():
    problems = [
        DepProblem(kind="missing", package="pandas", detail="pandas not installed"),
        DepProblem(kind="violates", package="urllib3",
                   detail="urllib3 2.2.2 violates httpx<2",
                   required_by="httpx", specifier="<2",
                   installed_version=Version("2.2.2")),
        DepProblem(kind="stale-metadata", package="cnotebook",
                   detail="cnotebook has orphaned metadata: /old/cnotebook.egg-info"),
    ]
    report = EnvReport(python_path=None, package_count=10, problems=problems)
    out = _render(report)
    assert "Missing (1)" in out
    assert "Violates (1)" in out
    assert "Stale metadata (1)" in out
    # Order: missing before violates before stale-metadata.
    assert out.index("Missing") < out.index("Violates") < out.index("Stale metadata")


def test_header_reflects_counts():
    problems = [
        DepProblem(kind="missing", package="a", detail="a missing"),
        DepProblem(kind="missing", package="b", detail="b missing"),
        DepProblem(kind="violates", package="c",
                   detail="c 1.0 violates d<1", required_by="d", specifier="<1",
                   installed_version=Version("1.0")),
    ]
    report = EnvReport(python_path=None, package_count=20, problems=problems)
    out = _render(report)
    assert "3 problem" in out  # singular/plural either ok
    assert "20 packages" in out
