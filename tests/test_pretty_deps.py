"""Snapshot-style tests for print_dep_report."""

from io import StringIO

import pytest
from packaging.version import Version
from rich.console import Console

from pipu_cli.package_management import (
    DepEdge,
    DepNode,
    DepProblem,
    DepReport,
    InstalledPackage,
)
from pipu_cli.pretty import print_dep_report


def _console() -> Console:
    return Console(file=StringIO(), force_terminal=False, width=120, color_system=None)


def _render(report: DepReport) -> str:
    console = _console()
    print_dep_report(console, report)
    return console.file.getvalue()


def _subject(name="requests", version="2.31.0", **kwargs) -> InstalledPackage:
    return InstalledPackage(name=name, version=Version(version), **kwargs)


def _edge(name, version=None, specifier="", **kw):
    return DepEdge(
        name=name,
        installed_version=Version(version) if version else None,
        specifier=specifier,
        **kw,
    )


def test_clean_report_has_no_problems_panel():
    report = DepReport(
        package=_subject(),
        required_by=[DepNode(edge=_edge("httpx", "0.27.0", ">=2.28"))],
        requires=[DepNode(edge=_edge("urllib3", "2.2.2", "<3,>=1.21"))],
        problems=[],
    )
    out = _render(report)
    assert "requests" in out
    assert "Required by" in out
    assert "Requires" in out
    assert "urllib3" in out
    assert "Problems" not in out


def test_problems_panel_rendered():
    report = DepReport(
        package=_subject(),
        required_by=[],
        requires=[DepNode(edge=_edge("urllib3", "2.2.2", "<2"))],
        problems=[
            DepProblem(
                kind="violates",
                package="urllib3",
                detail="urllib3 2.2.2 violates requests<2",
                required_by="requests",
                specifier="<2",
                installed_version=Version("2.2.2"),
            )
        ],
    )
    out = _render(report)
    assert "Problems" in out
    assert "urllib3 2.2.2 violates requests<2" in out


def test_empty_branches_show_none_marker():
    report = DepReport(
        package=_subject(name="lonely", version="1.0.0"),
        required_by=[],
        requires=[],
        problems=[],
    )
    out = _render(report)
    assert "none" in out.lower()


def test_missing_dependency_rendering():
    report = DepReport(
        package=_subject(),
        required_by=[],
        requires=[DepNode(edge=_edge("ghost", None, ">=1"))],
        problems=[
            DepProblem(
                kind="missing",
                package="ghost",
                detail="ghost is required by requests but is not installed",
            )
        ],
    )
    out = _render(report)
    assert "ghost" in out
    assert "not installed" in out


def test_cycle_marker_rendered():
    cycle = DepNode(edge=_edge("requests", "2.31.0", ">=1"), is_cycle=True)
    report = DepReport(
        package=_subject(),
        required_by=[DepNode(edge=_edge("httpx", "0.27.0", ">=2.28"), children=[cycle])],
        requires=[],
        problems=[],
    )
    out = _render(report)
    assert "cycle" in out.lower()


def test_editable_subject_shown_in_header():
    report = DepReport(
        package=_subject(is_editable=True, editable_location="/src/requests"),
        required_by=[],
        requires=[],
        problems=[],
    )
    out = _render(report)
    assert "/src/requests" in out
