"""Tests for JSON serialization of EnvReport."""

import json
from packaging.version import Version

from pipu_cli.package_management import DepProblem, EnvReport
from pipu_cli.output import env_report_to_json, env_report_group_to_json


def test_env_json_clean():
    report = EnvReport(python_path=None, package_count=5, problems=[])
    data = env_report_to_json(report)
    payload = json.loads(json.dumps(data))  # round-trip
    assert payload["environment"] is None
    assert payload["package_count"] == 5
    assert payload["problems"] == []
    assert payload["summary"] == {
        "missing": 0, "violates": 0, "broken-editable": 0,
        "duplicate-install": 0, "stale-metadata": 0, "total": 0,
    }


def test_env_json_with_problems():
    problems = [
        DepProblem(
            kind="violates", package="urllib3",
            detail="urllib3 2.2.2 violates httpx<2",
            required_by="httpx", specifier="<2",
            installed_version=Version("2.2.2"),
        ),
        DepProblem(kind="missing", package="pandas", detail="pandas is required by X"),
    ]
    report = EnvReport(python_path="/p", package_count=10, problems=problems)
    data = env_report_to_json(report)
    assert data["environment"] == "/p"
    assert data["package_count"] == 10
    assert data["summary"]["missing"] == 1
    assert data["summary"]["violates"] == 1
    assert data["summary"]["total"] == 2
    # installed_version must be a string or None in JSON, never a Version.
    violates = [p for p in data["problems"] if p["kind"] == "violates"][0]
    assert violates["installed_version"] == "2.2.2"
    missing = [p for p in data["problems"] if p["kind"] == "missing"][0]
    assert missing["installed_version"] is None


def test_env_json_group_shape():
    report = EnvReport(python_path=None, package_count=0)
    data = env_report_group_to_json(
        group_name="prod",
        per_env=[("main", report), ("tools", report)],
    )
    assert data["group"] == "prod"
    assert [e["env"] for e in data["environments"]] == ["main", "tools"]
    assert data["environments"][0]["report"]["package_count"] == 0
