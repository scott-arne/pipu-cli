"""Tests for output formatting."""

import json
from packaging.version import Version

from pipu_cli.output import JsonOutputFormatter
from pipu_cli.package_management import UpgradePackageInfo, UpgradedPackage


def test_json_formatter_format_upgradable():
    """Test JSON formatting of upgradable packages."""
    packages = [
        UpgradePackageInfo(
            name="requests",
            version=Version("2.28.0"),
            upgradable=True,
            latest_version=Version("2.31.0"),
            is_editable=False
        )
    ]

    formatter = JsonOutputFormatter()
    output = formatter.format_upgradable(packages)

    data = json.loads(output)
    assert data["count"] == 1
    assert data["upgradable"][0]["name"] == "requests"
    assert data["upgradable"][0]["version"] == "2.28.0"
    assert data["upgradable"][0]["latest_version"] == "2.31.0"


def test_json_formatter_format_results():
    """Test JSON formatting of upgrade results."""
    results = [
        UpgradedPackage(
            name="requests",
            version=Version("2.31.0"),
            upgraded=True,
            previous_version=Version("2.28.0"),
            is_editable=False
        ),
        UpgradedPackage(
            name="numpy",
            version=Version("1.24.0"),
            upgraded=False,
            previous_version=Version("1.24.0"),
            is_editable=False
        )
    ]

    formatter = JsonOutputFormatter()
    output = formatter.format_results(results)

    data = json.loads(output)
    assert data["success_count"] == 1
    assert data["failure_count"] == 1
    assert data["total"] == 2


def test_json_formatter_format_all_standardized_schema():
    """Test format_all returns standardized schema with summary."""
    upgradable = [
        UpgradePackageInfo(
            name="requests",
            version=Version("2.28.0"),
            upgradable=True,
            latest_version=Version("2.31.0"),
            is_editable=False
        )
    ]
    results = [
        UpgradedPackage(
            name="requests",
            version=Version("2.31.0"),
            upgraded=True,
            previous_version=Version("2.28.0"),
            is_editable=False
        )
    ]

    formatter = JsonOutputFormatter()
    output = formatter.format_all(upgradable=upgradable, results=results)

    data = json.loads(output)

    # New schema: flat results list + summary dict
    assert "upgradable" in data
    assert "results" in data
    assert "summary" in data
    assert isinstance(data["results"], list)
    assert data["summary"]["total"] == 1
    assert data["summary"]["upgraded"] == 1
    assert data["summary"]["failed"] == 0

    # Old keys should NOT exist
    assert "upgradable_count" not in data
    assert "success_count" not in data


class TestGroupJsonOutput:
    """Tests for group JSON output."""

    def test_format_group_results_structure(self):
        """Group output is an array of per-environment results."""
        formatter = JsonOutputFormatter()

        env_results = [
            {
                "environment": "/python/a",
                "upgradable": [],
                "blocked": [],
                "results": [],
                "summary": {"total": 0, "upgraded": 0, "failed": 0},
            },
            {
                "environment": "/python/b",
                "upgradable": [],
                "blocked": [],
                "results": [],
                "summary": {"total": 0, "upgraded": 0, "failed": 0},
            },
        ]

        output = formatter.format_group_results(env_results)
        data = json.loads(output)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["environment"] == "/python/a"
        assert data[1]["environment"] == "/python/b"
        assert "upgradable" in data[0]
        assert "summary" in data[0]
