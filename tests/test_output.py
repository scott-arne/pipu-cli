"""Tests for JSON payload builders."""

import json

from packaging.version import Version

from pipu_cli.output import (
    build_upgrade_payload,
    build_install_payload,
    build_uninstall_payload,
    package_to_dict,
)
from pipu_cli.package_management import (
    UpgradePackageInfo,
    UpgradedPackage,
    BlockedPackageInfo,
    InstalledResult,
    UninstalledResult,
)


def test_package_to_dict_stringifies_version():
    pkg = UpgradePackageInfo(
        name="requests",
        version=Version("2.28.0"),
        upgradable=True,
        latest_version=Version("2.31.0"),
        is_editable=False,
    )
    data = package_to_dict(pkg)
    assert data["name"] == "requests"
    assert data["version"] == "2.28.0"
    assert data["latest_version"] == "2.31.0"


def test_build_upgrade_payload_with_results():
    upgradable = [
        UpgradePackageInfo(
            name="requests", version=Version("2.28.0"),
            upgradable=True, latest_version=Version("2.31.0"),
            is_editable=False,
        ),
    ]
    results = [
        UpgradedPackage(
            name="requests", version=Version("2.31.0"),
            upgraded=True, previous_version=Version("2.28.0"),
            is_editable=False,
        ),
    ]
    payload = build_upgrade_payload(upgradable=upgradable, results=results)
    assert payload["summary"] == {
        "total": 1,
        "upgraded": 1,
        "constrained": 0,
        "failed": 0,
    }
    assert isinstance(payload["results"], list)
    # Round-trip through JSON to prove the payload is serializable.
    assert json.loads(json.dumps(payload))["summary"]["upgraded"] == 1


def test_build_upgrade_payload_counts_resolver_constraints_separately():
    upgradable = [
        UpgradePackageInfo(
            name="opentelemetry-sdk", version=Version("1.39.1"),
            upgradable=True, latest_version=Version("1.41.1"),
            is_editable=False,
        ),
    ]
    results = [
        UpgradedPackage(
            name="opentelemetry-sdk", version=Version("1.39.1"),
            upgraded=False, previous_version=Version("1.39.1"),
            is_editable=False,
            failure_reason="Version unchanged — may be constrained by dependency resolver",
        ),
    ]
    payload = build_upgrade_payload(upgradable=upgradable, results=results)
    assert payload["summary"] == {
        "total": 1,
        "upgraded": 0,
        "constrained": 1,
        "failed": 0,
    }


def test_build_upgrade_payload_without_results():
    payload = build_upgrade_payload(upgradable=[])
    assert payload["results"] == []
    assert payload["summary"] == {"total": 0, "upgraded": 0, "constrained": 0, "failed": 0}
    assert payload["blocked"] == []


def test_build_upgrade_payload_with_blocked():
    blocked = [
        BlockedPackageInfo(
            name="numpy", version=Version("1.24.0"),
            latest_version=Version("2.0.0"),
            blocked_by=["scipy"],
            is_editable=False,
        ),
    ]
    payload = build_upgrade_payload(upgradable=[], blocked=blocked)
    assert len(payload["blocked"]) == 1
    assert payload["blocked"][0]["name"] == "numpy"


def test_build_install_payload():
    results = [
        InstalledResult(
            name="requests",
            version=Version("2.31.0"),
            installed=True,
            previous_version=None,
        ),
        InstalledResult(
            name="flask",
            version=Version("2.3.0"),
            installed=True,
            previous_version=Version("2.2.0"),
        ),
    ]
    payload = build_install_payload(results)
    assert payload["summary"]["total"] == 2
    assert payload["summary"]["installed"] == 1  # requests is new
    assert payload["summary"]["updated"] == 1  # flask was upgraded
    assert payload["summary"]["failed"] == 0
    # Round-trip through JSON to prove the payload is serializable.
    assert json.loads(json.dumps(payload))["summary"]["total"] == 2


def test_build_uninstall_payload():
    results = [
        UninstalledResult(
            name="requests",
            previous_version=Version("2.31.0"),
            uninstalled=True,
            already_absent=False,
            failure_reason=None,
        ),
        UninstalledResult(
            name="flask",
            previous_version=None,
            uninstalled=False,
            already_absent=True,
            failure_reason=None,
        ),
    ]
    payload = build_uninstall_payload(results)
    assert payload["summary"]["total"] == 2
    assert payload["summary"]["uninstalled"] == 1
    assert payload["summary"]["already_absent"] == 1
    # failed = not uninstalled, which includes already_absent case
    assert payload["summary"]["failed"] == 1
    # Round-trip through JSON to prove the payload is serializable.
    assert json.loads(json.dumps(payload))["summary"]["uninstalled"] == 1
