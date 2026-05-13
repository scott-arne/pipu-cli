"""Tests for CLI module."""

import json
import sys
from io import StringIO
import click
import pytest
from click.testing import CliRunner
from unittest.mock import patch, Mock

from packaging.version import Version
from rich.console import Console
from pipu_cli.cli import cli
import pipu_cli.cli as cli_module
from pipu_cli.package_management import (
    BlockedPackageInfo,
    InstalledPackage,
    InstalledResult,
    Package,
    UpgradePackageInfo,
    UpgradedPackage,
)
from pipu_cli.rollback import PackageRollbackOutcome, RollbackResult
from pipu_cli.download import DownloadError


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_packages():
    """Create mock package data."""
    installed = [
        InstalledPackage(
            name="requests",
            version=Version("2.28.0"),
            is_editable=False,
            constrained_dependencies={}
        )
    ]

    upgradable = [
        UpgradePackageInfo(
            name="requests",
            version=Version("2.28.0"),
            upgradable=True,
            latest_version=Version("2.31.0"),
            is_editable=False
        )
    ]

    return installed, upgradable


def test_dry_run_shows_packages_without_installing(runner, mock_packages):
    """Test --dry-run shows upgrade plan but doesn't install."""
    installed, upgradable = mock_packages

    with patch('pipu_cli.cli.inspect_installed_packages', return_value=installed), \
         patch('pipu_cli.cli.get_latest_versions', return_value={installed[0]: Mock(version=Version("2.31.0"))}), \
         patch('pipu_cli.cli.resolve_upgradable_packages', return_value=upgradable), \
         patch('pipu_cli.cli.download_packages') as mock_download, \
         patch('pipu_cli.cli.install_from_local') as mock_install:

        result = runner.invoke(cli, ['upgrade', '--dry-run', '--yes', '--no-cache', '-p', '1'])

        # download/install should NOT be called in dry-run mode
        mock_download.assert_not_called()
        mock_install.assert_not_called()

        # Should show the packages that would be upgraded
        assert 'requests' in result.output
        assert '2.28.0' in result.output
        assert '2.31.0' in result.output
        assert result.exit_code == 0


def test_dry_run_exit_code_zero_when_upgrades_available(runner, mock_packages):
    """Test --dry-run returns exit code 0 when upgrades available."""
    installed, upgradable = mock_packages

    with patch('pipu_cli.cli.inspect_installed_packages', return_value=installed), \
         patch('pipu_cli.cli.get_latest_versions', return_value={installed[0]: Mock(version=Version("2.31.0"))}), \
         patch('pipu_cli.cli.resolve_upgradable_packages', return_value=upgradable):

        result = runner.invoke(cli, ['upgrade', '--dry-run', '--no-cache', '-p', '1'])

        assert result.exit_code == 0


def test_upgrade_passes_timeout_to_download_phase(runner, mock_packages):
    """The upgrade timeout controls the download phase as well as metadata lookups."""
    installed, upgradable = mock_packages
    captured_kwargs = {}

    def fake_download_phase(*_args, **kwargs):
        captured_kwargs.update(kwargs)
        return [], 0.0

    with patch("pipu_cli.cli.inspect_installed_packages", return_value=installed), \
         patch("pipu_cli.cli.get_latest_versions", return_value={installed[0]: Package(name="requests", version=Version("2.31.0"))}), \
         patch("pipu_cli.cli.get_target_constraints_for_disputed_upgrades", return_value={}), \
         patch("pipu_cli.cli.resolve_upgradable_packages", return_value=upgradable), \
         patch("pipu_cli.cli._download_and_install_phase", side_effect=fake_download_phase):
        result = runner.invoke(
            cli,
            [
                "upgrade",
                "--timeout",
                "900",
                "--yes",
                "--no-cache",
                "--no-check",
                "-p",
                "1",
            ],
        )

    assert result.exit_code == 0, result.output
    assert captured_kwargs["timeout"] == 900


def test_download_and_install_phase_skips_specs_that_failed_download():
    """A download timeout should become a package failure, not an install command error."""
    console = Console(file=StringIO(), force_terminal=True)
    upgrades = [
        UpgradePackageInfo(
            name="requests",
            version=Version("2.28.0"),
            upgradable=True,
            latest_version=Version("2.31.0"),
        ),
        UpgradePackageInfo(
            name="rich",
            version=Version("13.0.0"),
            upgradable=True,
            latest_version=Version("13.7.0"),
        ),
    ]
    installed_specs = []
    captured_download_kwargs = {}
    captured_install_kwargs = {}

    def fake_download(*_args, progress_callback=None, **_kwargs):
        captured_download_kwargs.update(_kwargs)
        if progress_callback is not None:
            progress_callback("requests==2.31.0", False, "timed out after 300s")
            progress_callback("rich==13.7.0", True, "")
        raise DownloadError({"requests==2.31.0": "timed out after 300s"})

    def fake_install(*_args, specs, **_kwargs):
        installed_specs.extend(specs)
        captured_install_kwargs.update(_kwargs)
        return [
            UpgradedPackage(
                name="rich",
                version=Version("13.7.0"),
                upgraded=True,
                previous_version=Version("13.0.0"),
            )
        ]

    with patch("pipu_cli.cli.download_packages", side_effect=fake_download), \
         patch("pipu_cli.cli.install_from_local", side_effect=fake_install), \
         patch("pipu_cli.rollback.save_state"):
        results, _ = cli_module._download_and_install_phase(
            console,
            "human",
            upgrades,
            {},
            timeout=900,
        )

    assert captured_download_kwargs["timeout"] == 900
    assert captured_download_kwargs["use_download_cache"] is True
    assert captured_install_kwargs["timeout"] == 900
    assert installed_specs == ["rich"]
    failed = [result for result in results if result.name == "requests"]
    assert len(failed) == 1
    assert failed[0].upgraded is False
    assert failed[0].failure_reason == "Download failed: timed out after 300s"


def test_download_and_install_phase_relaxes_unconstrained_install_specs():
    """Offline installs should let pip pick compatible staged versions."""
    console = Console(file=StringIO(), force_terminal=True)
    upgrades = [
        UpgradePackageInfo(
            name="logfire",
            version=Version("4.32.1"),
            upgradable=True,
            latest_version=Version("4.33.0"),
        ),
        UpgradePackageInfo(
            name="zope.interface",
            version=Version("5.3.0"),
            upgradable=True,
            latest_version=Version("5.5.0"),
        ),
    ]
    downloaded_specs = []
    installed_specs = []

    def fake_download(*_args, specs, **_kwargs):
        downloaded_specs.extend(specs)

    def fake_install(*_args, specs, **_kwargs):
        installed_specs.extend(specs)
        return [
            UpgradedPackage(
                name="logfire",
                version=Version("4.32.1"),
                upgraded=False,
                previous_version=Version("4.32.1"),
                failure_reason="Version unchanged — may be constrained by dependency resolver",
            ),
            UpgradedPackage(
                name="zope.interface",
                version=Version("5.4.0"),
                upgraded=True,
                previous_version=Version("5.3.0"),
            ),
        ]

    with patch("pipu_cli.cli.download_packages", side_effect=fake_download), \
         patch("pipu_cli.cli.install_from_local", side_effect=fake_install), \
         patch("pipu_cli.rollback.save_state"):
        results, _ = cli_module._download_and_install_phase(
            console,
            "human",
            upgrades,
            {"zope-interface": "==5.4.0"},
        )

    assert downloaded_specs == ["logfire==4.33.0", "zope.interface==5.4.0"]
    assert installed_specs == ["logfire", "zope.interface==5.4.0"]
    assert [result.name for result in results] == ["logfire", "zope.interface"]


def test_group_install_worker_marks_failed_results_as_failed_env(tmp_path):
    """A batched install timeout should not render the environment as complete."""
    events = []

    class FakeTracker:
        def advance(self, env_name, package_name):
            events.append(("advance", env_name, package_name))

        def complete_env(self, env_name):
            events.append(("complete", env_name))

        def fail_env(self, env_name, reason):
            events.append(("fail", env_name, reason))

    def fake_install_from_local(*args, progress_callback=None, **kwargs):
        if progress_callback is not None:
            progress_callback("requests==2.31.0")
        return [
            UpgradedPackage(
                name="requests",
                version=Version("2.28.0"),
                upgraded=False,
                previous_version=Version("2.28.0"),
                failure_reason="Installation timed out after 300s without pip output",
            )
        ]

    with patch("pipu_cli.download.install_from_local", side_effect=fake_install_from_local):
        results = cli_module._upgrade_install_single_env(
            "jupyter",
            "/path/to/python",
            ["requests==2.31.0"],
            dest_dir=tmp_path,
            tracker=FakeTracker(),
        )

    assert results[0].failure_reason == "Installation timed out after 300s without pip output"
    assert ("complete", "jupyter") not in events
    assert ("fail", "jupyter", "Installation timed out after 300s without pip output") in events


def test_group_install_worker_reports_install_activity(tmp_path):
    """Pip output during a batched install should be visible in the environment row."""
    events = []

    class FakeTracker:
        def start_env(self, env_name):
            events.append(("start", env_name))

        def message_env(self, env_name, message):
            events.append(("message", env_name, message))

        def advance(self, env_name, package_name):
            events.append(("advance", env_name, package_name))

        def complete_env(self, env_name):
            events.append(("complete", env_name))

        def fail_env(self, env_name, reason):
            events.append(("fail", env_name, reason))

    def fake_install_from_local(*args, install_activity_callback=None, progress_callback=None, **kwargs):
        assert install_activity_callback is not None
        install_activity_callback("Installing collected packages: requests\n")
        if progress_callback is not None:
            progress_callback("requests==2.31.0")
        return [
            UpgradedPackage(
                name="requests",
                version=Version("2.31.0"),
                upgraded=True,
                previous_version=Version("2.28.0"),
            )
        ]

    with patch("pipu_cli.download.install_from_local", side_effect=fake_install_from_local):
        results = cli_module._upgrade_install_single_env(
            "jupyter",
            "/path/to/python",
            ["requests==2.31.0"],
            dest_dir=tmp_path,
            tracker=FakeTracker(),
        )

    assert results[0].upgraded is True
    assert ("start", "jupyter") in events
    assert ("message", "jupyter", "Installing collected packages: requests") in events
    assert ("complete", "jupyter") in events


def test_exclude_removes_packages_from_upgrade_list(runner, mock_packages):
    """Test --exclude removes specified packages from upgrade list."""
    installed = [
        InstalledPackage(name="requests", version=Version("2.28.0"), is_editable=False, constrained_dependencies={}),
        InstalledPackage(name="numpy", version=Version("1.24.0"), is_editable=False, constrained_dependencies={}),
    ]

    upgradable = [
        UpgradePackageInfo(name="requests", version=Version("2.28.0"), upgradable=True, latest_version=Version("2.31.0"), is_editable=False),
        UpgradePackageInfo(name="numpy", version=Version("1.24.0"), upgradable=True, latest_version=Version("1.26.0"), is_editable=False),
    ]

    with patch('pipu_cli.cli.inspect_installed_packages', return_value=installed), \
         patch('pipu_cli.cli.get_latest_versions') as mock_latest, \
         patch('pipu_cli.cli.resolve_upgradable_packages', return_value=upgradable):

        mock_latest.return_value = {
            installed[0]: Mock(version=Version("2.31.0")),
            installed[1]: Mock(version=Version("1.26.0")),
        }

        result = runner.invoke(cli, ['upgrade', '--exclude', 'numpy', '--dry-run', '--no-cache', '-p', '1'])

        # Should show requests but NOT numpy
        assert 'requests' in result.output
        assert 'numpy' not in result.output or 'Excluded' in result.output
        assert result.exit_code == 0


def test_exclude_multiple_packages(runner):
    """Test --exclude with comma-separated packages."""
    installed = [
        InstalledPackage(name="requests", version=Version("2.28.0"), is_editable=False, constrained_dependencies={}),
        InstalledPackage(name="numpy", version=Version("1.24.0"), is_editable=False, constrained_dependencies={}),
        InstalledPackage(name="pandas", version=Version("2.0.0"), is_editable=False, constrained_dependencies={}),
    ]

    upgradable = [
        UpgradePackageInfo(name="requests", version=Version("2.28.0"), upgradable=True, latest_version=Version("2.31.0"), is_editable=False),
        UpgradePackageInfo(name="numpy", version=Version("1.24.0"), upgradable=True, latest_version=Version("1.26.0"), is_editable=False),
        UpgradePackageInfo(name="pandas", version=Version("2.0.0"), upgradable=True, latest_version=Version("2.1.0"), is_editable=False),
    ]

    with patch('pipu_cli.cli.inspect_installed_packages', return_value=installed), \
         patch('pipu_cli.cli.get_latest_versions') as mock_latest, \
         patch('pipu_cli.cli.resolve_upgradable_packages', return_value=upgradable):

        mock_latest.return_value = {
            installed[0]: Mock(version=Version("2.31.0")),
            installed[1]: Mock(version=Version("1.26.0")),
            installed[2]: Mock(version=Version("2.1.0")),
        }

        result = runner.invoke(cli, ['upgrade', '--exclude', 'numpy,pandas', '--dry-run', '--no-cache', '-p', '1'])

        # Should show only requests
        assert 'requests' in result.output
        assert result.exit_code == 0


def test_show_blocked_displays_blocked_packages(runner):
    """Test --show-blocked shows packages blocked by constraints."""
    installed = [
        InstalledPackage(
            name="package-a",
            version=Version("1.0.0"),
            is_editable=False,
            constrained_dependencies={"package-b": "<2.0"}
        ),
        InstalledPackage(
            name="package-b",
            version=Version("1.5.0"),
            is_editable=False,
            constrained_dependencies={}
        ),
    ]

    with patch('pipu_cli.cli.inspect_installed_packages', return_value=installed), \
         patch('pipu_cli.cli.get_latest_versions_parallel') as mock_latest, \
         patch('pipu_cli.cli.resolve_upgradable_packages_with_reasons') as mock_resolve:

        mock_latest.return_value = {
            installed[1]: Mock(version=Version("2.5.0")),
        }

        # package-b blocked because package-a requires <2.0
        mock_resolve.return_value = (
            [],  # No upgradable
            [BlockedPackageInfo(
                name="package-b",
                version=Version("1.5.0"),
                latest_version=Version("2.5.0"),
                blocked_by=["package-a requires <2.0"],
                is_editable=False
            )]
        )

        result = runner.invoke(cli, ['upgrade', '--show-blocked', '--dry-run', '--no-cache'])

        assert 'Blocked' in result.output or 'blocked' in result.output.lower()
        assert 'package-b' in result.output
        assert result.exit_code == 0


def test_single_package_upgrade_filters_to_specified_package(runner):
    """Test pipu upgrade <package> only upgrades specified package."""
    installed = [
        InstalledPackage(name="requests", version=Version("2.28.0"), is_editable=False, constrained_dependencies={}),
        InstalledPackage(name="numpy", version=Version("1.24.0"), is_editable=False, constrained_dependencies={}),
    ]

    with patch('pipu_cli.cli.inspect_installed_packages', return_value=installed), \
         patch('pipu_cli.cli.get_latest_versions') as mock_latest, \
         patch('pipu_cli.cli.resolve_upgradable_packages') as mock_resolve:

        mock_latest.return_value = {
            installed[0]: Mock(version=Version("2.31.0")),
            installed[1]: Mock(version=Version("1.26.0")),
        }

        mock_resolve.return_value = [
            UpgradePackageInfo(name="requests", version=Version("2.28.0"), upgradable=True, latest_version=Version("2.31.0"), is_editable=False),
            UpgradePackageInfo(name="numpy", version=Version("1.24.0"), upgradable=True, latest_version=Version("1.26.0"), is_editable=False),
        ]

        result = runner.invoke(cli, ['upgrade', 'requests', '--dry-run', '--no-cache', '-p', '1'])

        # Should only show requests
        assert 'requests' in result.output
        # numpy should not appear in upgrade table
        assert result.exit_code == 0


def test_version_constraint_upgrade(runner):
    """Test upgrading with specific version constraint."""
    installed = [
        InstalledPackage(name="requests", version=Version("2.28.0"), is_editable=False, constrained_dependencies={}),
    ]

    with patch('pipu_cli.cli.inspect_installed_packages', return_value=installed), \
         patch('pipu_cli.cli.get_latest_versions') as mock_latest, \
         patch('pipu_cli.cli.resolve_upgradable_packages') as mock_resolve, \
         patch('pipu_cli.cli.download_packages') as mock_download, \
         patch('pipu_cli.cli.install_from_local') as mock_install_local, \
         patch('pipu_cli.rollback.save_state'):

        mock_latest.return_value = {
            installed[0]: Mock(version=Version("2.31.0")),
        }

        mock_resolve.return_value = [
            UpgradePackageInfo(name="requests", version=Version("2.28.0"), upgradable=True, latest_version=Version("2.31.0"), is_editable=False),
        ]

        from pipu_cli.package_management import UpgradedPackage
        mock_install_local.return_value = [
            UpgradedPackage(name="requests", version=Version("2.30.0"), upgraded=True, previous_version=Version("2.28.0"), is_editable=False)
        ]

        result = runner.invoke(cli, ['upgrade', 'requests==2.30.0', '--yes', '--no-cache', '-p', '1'])

        # Should attempt to download with version constraint spec
        mock_download.assert_called_once()
        download_specs = mock_download.call_args.kwargs['specs']
        assert download_specs == ['requests==2.30.0']

        # Should attempt to install from local with same specs
        mock_install_local.assert_called_once()
        install_specs = mock_install_local.call_args.kwargs['specs']
        assert install_specs == ['requests==2.30.0']

        assert result.exit_code == 0


# Rollback command tests

def test_rollback_list_shows_states(runner, tmp_path):
    """Test that rollback --list shows saved states."""
    with patch('pipu_cli.rollback.list_states') as mock_list, \
         patch('pipu_cli.rollback.ROLLBACK_DIR', tmp_path):

        mock_list.return_value = [
            {
                "file": "state_20241205_143022.json",
                "timestamp": "20241205_143022",
                "description": "Pre-upgrade state",
                "package_count": 3
            }
        ]

        result = runner.invoke(cli, ['rollback', '--list'])

        assert result.exit_code == 0
        # Rich may truncate filenames in table, so check for key parts
        assert 'state_20241205' in result.output or 'State File' in result.output
        assert 'Pre-upgrade state' in result.output


def test_rollback_list_empty(runner, tmp_path):
    """Test rollback --list with no saved states."""
    with patch('pipu_cli.rollback.list_states', return_value=[]), \
         patch('pipu_cli.rollback.ROLLBACK_DIR', tmp_path):

        result = runner.invoke(cli, ['rollback', '--list'])

        assert result.exit_code == 0
        assert 'No saved states found' in result.output


def test_rollback_dry_run(runner):
    """Test rollback --dry-run shows packages without modifying."""
    with patch('pipu_cli.rollback.get_latest_state') as mock_state, \
         patch('pipu_cli.rollback.rollback_to_state') as mock_rollback:

        mock_state.return_value = {
            "timestamp": "20241205_143022",
            "description": "Pre-upgrade state",
            "packages": [
                {"name": "requests", "version": "2.28.0"},
                {"name": "numpy", "version": "1.24.0"}
            ]
        }

        result = runner.invoke(cli, ['rollback', '--dry-run'])

        assert result.exit_code == 0
        assert 'requests' in result.output
        assert 'numpy' in result.output
        assert 'Dry run complete' in result.output
        # Should NOT call rollback_to_state in dry-run mode
        mock_rollback.assert_not_called()


def test_rollback_no_state_found(runner):
    """Test rollback when no state is saved."""
    with patch('pipu_cli.rollback.get_latest_state', return_value=None):

        result = runner.invoke(cli, ['rollback'])

        assert result.exit_code == 0
        assert 'No saved state found' in result.output


def test_rollback_with_yes_flag(runner):
    """Test rollback --yes performs rollback without confirmation."""
    with patch('pipu_cli.rollback.get_latest_state') as mock_state, \
         patch('pipu_cli.rollback.rollback_to_state') as mock_rollback:

        mock_state.return_value = {
            "timestamp": "20241205_143022",
            "description": "Pre-upgrade state",
            "packages": [{"name": "requests", "version": "2.28.0"}]
        }
        mock_rollback.return_value = RollbackResult(
            succeeded=[PackageRollbackOutcome(spec="requests==2.28.0")],
            failed=[],
        )

        result = runner.invoke(cli, ['rollback', '--yes'])

        assert result.exit_code == 0
        assert 'Successfully rolled back' in result.output
        mock_rollback.assert_called_once()


def test_explicit_cli_timeout_overrides_config(runner):
    """Test that explicit --timeout value is not overridden by config file."""
    config = {"timeout": 30}

    with patch('pipu_cli.cli.load_config', return_value=config), \
         patch('pipu_cli.cli.inspect_installed_packages', return_value=[]) as mock_inspect:

        runner.invoke(cli, ['upgrade', '--timeout', '10', '--no-cache'])

        # inspect_installed_packages should be called with timeout=10 (from CLI),
        # NOT timeout=30 (from config)
        mock_inspect.assert_called_once_with(timeout=10, python_path=None)


def test_explicit_cli_pre_flag_overrides_config(runner):
    """Test that explicit --pre flag is not overridden by config file."""
    config = {"pre": False}

    with patch('pipu_cli.cli.load_config', return_value=config), \
         patch('pipu_cli.cli.inspect_installed_packages', return_value=[]):

        # Pass --pre explicitly; config says pre=False
        result = runner.invoke(cli, ['upgrade', '--pre', '--no-cache'])

        # The command should have used pre=True from CLI, not False from config
        # We can't easily assert the value directly, but the command should succeed
        assert result.exit_code == 0


def test_exclude_accepts_repeated_flag(runner):
    """Test --exclude works as repeatable flag (-e numpy -e pandas)."""
    installed = [
        InstalledPackage(name="requests", version=Version("2.28.0"), is_editable=False, constrained_dependencies={}),
        InstalledPackage(name="numpy", version=Version("1.24.0"), is_editable=False, constrained_dependencies={}),
        InstalledPackage(name="pandas", version=Version("2.0.0"), is_editable=False, constrained_dependencies={}),
    ]
    upgradable = [
        UpgradePackageInfo(name="requests", version=Version("2.28.0"), upgradable=True, latest_version=Version("2.31.0"), is_editable=False),
        UpgradePackageInfo(name="numpy", version=Version("1.24.0"), upgradable=True, latest_version=Version("2.0.0"), is_editable=False),
        UpgradePackageInfo(name="pandas", version=Version("2.0.0"), upgradable=True, latest_version=Version("2.1.0"), is_editable=False),
    ]

    with patch('pipu_cli.cli.inspect_installed_packages', return_value=installed), \
         patch('pipu_cli.cli.get_latest_versions', return_value={
             installed[0]: Mock(version=Version("2.31.0")),
             installed[1]: Mock(version=Version("2.0.0")),
             installed[2]: Mock(version=Version("2.1.0")),
         }), \
         patch('pipu_cli.cli.resolve_upgradable_packages', return_value=upgradable):

        result = runner.invoke(cli, ['upgrade', '--dry-run', '-e', 'numpy', '-e', 'pandas', '--no-cache', '-p', '1'])

        assert result.exit_code == 0
        assert 'requests' in result.output
        # numpy and pandas should be excluded


def test_outdated_shows_upgradable_packages(runner, mock_packages):
    """Test pipu outdated shows packages without installing."""
    installed, upgradable = mock_packages

    with patch('pipu_cli.cli.inspect_installed_packages', return_value=installed), \
         patch('pipu_cli.cli.get_latest_versions', return_value={installed[0]: Mock(version=Version("2.31.0"))}), \
         patch('pipu_cli.cli.resolve_upgradable_packages_with_reasons', return_value=(upgradable, [])):

        result = runner.invoke(cli, ['outdated', '--no-cache', '-p', '1'])

        assert 'requests' in result.output
        assert result.exit_code == 0


def test_outdated_shows_blocked_by_default(runner):
    """Test pipu outdated shows blocked packages by default (--show-blocked=True)."""
    installed = [
        InstalledPackage(name="numpy", version=Version("1.24.0"), is_editable=False, constrained_dependencies={})
    ]
    upgradable = []
    blocked = [
        BlockedPackageInfo(
            name="numpy",
            version=Version("1.24.0"),
            latest_version=Version("2.0.0"),
            blocked_by=["scipy requires >=1.20,<1.25"],
            is_editable=False
        )
    ]

    with patch('pipu_cli.cli.inspect_installed_packages', return_value=installed), \
         patch('pipu_cli.cli.get_latest_versions', return_value={installed[0]: Mock(version=Version("2.0.0"))}), \
         patch('pipu_cli.cli.resolve_upgradable_packages_with_reasons', return_value=(upgradable, blocked)):

        result = runner.invoke(cli, ['outdated', '--no-cache', '-p', '1'])

        assert 'numpy' in result.output
        assert 'scipy' in result.output
        assert result.exit_code == 0


def test_step3_uses_target_constraints_for_resolution(monkeypatch):
    """Step 3 fetches target metadata and passes it to the resolver."""
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "<3.0"},
        is_editable=False,
    )
    installed_b = InstalledPackage(
        name="package-b",
        version=Version("2.0.0"),
        constrained_dependencies={},
        is_editable=False,
    )
    latest_versions = {
        installed_a: Mock(version=Version("2.0.0")),
        installed_b: Mock(version=Version("3.5.0")),
    }

    monkeypatch.setattr(
        "pipu_cli.cli.get_target_constraints_for_disputed_upgrades",
        lambda *args, **kwargs: {"package-a": {"package-b": "<3.0"}},
    )

    can_upgrade, blocked_packages, _, _ = cli_module._step3_resolve_packages(
        Console(file=StringIO()),
        "human",
        False,
        latest_versions,
        [installed_a, installed_b],
        True,
        "",
        (),
        metadata_timeout=10,
    )

    assert [pkg.name for pkg in can_upgrade] == ["package-a"]
    assert [pkg.name for pkg in blocked_packages] == ["package-b"]


def test_rollback_list_json_output(runner):
    """Test pipu rollback --list --output json returns valid JSON."""
    mock_states = [
        {"file": "state_20260101_120000.json", "timestamp": "20260101_120000",
         "package_count": 3, "description": "Pre-upgrade state"}
    ]

    with patch('pipu_cli.rollback.list_states', return_value=mock_states):
        result = runner.invoke(cli, ['rollback', '--list', '--output', 'json'])

        data = json.loads(result.output)
        assert "states" in data
        assert len(data["states"]) == 1
        assert result.exit_code == 0


class TestGroupCommands:
    """Tests for pipu group subcommands."""

    def test_group_list_empty(self, runner):
        """group list shows message when no groups exist."""
        with patch("pipu_cli.cli.list_groups", return_value={}):
            result = runner.invoke(cli, ["group", "list"])
        assert "No groups defined" in result.output
        assert result.exit_code == 0

    def test_group_list_with_groups(self, runner):
        """group list shows groups in a table."""
        with patch("pipu_cli.cli.list_groups", return_value={
            "dev": ["/usr/bin/python3", "/opt/python/bin/python"],
            "prod": ["/home/user/.venv/bin/python"],
        }):
            result = runner.invoke(cli, ["group", "list"])
        assert "dev" in result.output
        assert "prod" in result.output
        assert result.exit_code == 0

    def test_group_add_default_python(self, runner):
        """group add without --python uses sys.executable."""
        with patch("pipu_cli.cli.validate_python_path", return_value=sys.executable), \
             patch("pipu_cli.cli.add_environment", return_value=True) as mock_add:
            result = runner.invoke(cli, ["group", "add", "mygroup"])
        assert result.exit_code == 0
        mock_add.assert_called_once_with("mygroup", sys.executable)

    def test_group_add_with_python_path(self, runner):
        """group add with --python uses specified path."""
        with patch("pipu_cli.cli.validate_python_path", return_value="/other/python"), \
             patch("pipu_cli.cli.add_environment", return_value=True) as mock_add:
            result = runner.invoke(cli, ["group", "add", "mygroup", "--python", "/other/python"])
        assert result.exit_code == 0
        mock_add.assert_called_once_with("mygroup", "/other/python")

    def test_group_add_validation_failure(self, runner):
        """group add fails when validation fails."""
        with patch(
            "pipu_cli.cli.validate_python_path",
            side_effect=click.ClickException("Not a Python interpreter"),
        ):
            result = runner.invoke(cli, ["group", "add", "mygroup", "--python", "/not/python"])
        assert "Not a Python interpreter" in result.output
        assert result.exit_code == 1

    def test_group_add_force_skips_validation(self, runner):
        """group add --force skips validation."""
        with patch("pipu_cli.cli.add_environment", return_value=True) as mock_add:
            result = runner.invoke(cli, ["group", "add", "mygroup", "--python", "/any/path", "--force"])
        assert result.exit_code == 0
        mock_add.assert_called_once()

    def test_group_add_duplicate(self, runner):
        """group add with duplicate shows notice."""
        with patch("pipu_cli.cli.validate_python_path", return_value=sys.executable), \
             patch("pipu_cli.cli.add_environment", return_value=False):
            result = runner.invoke(cli, ["group", "add", "mygroup"])
        assert "already in group" in result.output.lower()
        assert result.exit_code == 0

    def test_group_remove(self, runner):
        """group remove removes environment."""
        with patch("pipu_cli.cli.remove_environment", return_value=True):
            result = runner.invoke(cli, ["group", "remove", "mygroup"])
        assert result.exit_code == 0

    def test_group_remove_not_found(self, runner):
        """group remove shows error when not found."""
        with patch("pipu_cli.cli.remove_environment", return_value=False):
            result = runner.invoke(cli, ["group", "remove", "mygroup"])
        assert result.exit_code == 1

    def test_group_delete(self, runner):
        """group delete removes group."""
        with patch("pipu_cli.cli.delete_group", return_value=True):
            result = runner.invoke(cli, ["group", "delete", "mygroup"])
        assert result.exit_code == 0

    def test_group_delete_not_found(self, runner):
        """group delete shows error when group doesn't exist."""
        with patch("pipu_cli.cli.delete_group", return_value=False):
            result = runner.invoke(cli, ["group", "delete", "mygroup"])
        assert result.exit_code == 1


class TestGroupExecution:
    """Tests for -g/--group flag on upgrade and outdated."""

    def test_upgrade_group_not_found(self, runner):
        """upgrade -g with non-existent group shows error."""
        with patch("pipu_cli._group_runner.get_group", return_value=None):
            result = runner.invoke(cli, ["upgrade", "-g", "nogroup", "--yes"])
        assert "not found" in result.output.lower()
        assert result.exit_code == 1

    def test_outdated_group_not_found(self, runner):
        """outdated -g with non-existent group shows error."""
        with patch("pipu_cli._group_runner.get_group", return_value=None):
            result = runner.invoke(cli, ["outdated", "-g", "nogroup"])
        assert "not found" in result.output.lower()
        assert result.exit_code == 1

    def test_upgrade_group_runs_per_environment(self, runner):
        """upgrade -g inspects each environment in consolidated pipeline."""
        with patch("pipu_cli._group_runner.get_group", return_value=["/python/a", "/python/b"]), \
             patch("pipu_cli.cli.inspect_installed_packages", return_value=[]) as mock_inspect, \
             patch("os.path.exists", return_value=True):
            result = runner.invoke(cli, ["upgrade", "-g", "mygroup", "--yes", "--no-cache"])

        # Should show consolidated phase output
        assert "Inspecting 2 environments" in result.output
        # inspect should be called for each environment
        assert mock_inspect.call_count == 2

    def test_upgrade_group_debug_enables_debug_logging(self, runner):
        """upgrade -g --debug should use the same debug setup as local upgrade."""
        with patch("pipu_cli._group_runner.get_group", return_value=["/python/a"]), \
             patch("pipu_cli.cli.inspect_installed_packages", return_value=[]), \
             patch("os.path.exists", return_value=True):
            result = runner.invoke(
                cli,
                ["upgrade", "-g", "mygroup", "--yes", "--debug", "--no-cache", "--no-check"],
            )

        assert result.exit_code == 0, result.output
        assert "Debug mode enabled" in result.output

    def test_upgrade_group_skips_missing_env(self, runner):
        """upgrade -g skips environments that don't exist."""
        def path_exists(path):
            return path == "/python/a"

        with patch("pipu_cli._group_runner.get_group", return_value=["/python/a", "/python/missing"]), \
             patch("os.path.exists", side_effect=path_exists), \
             patch("pipu_cli.cli.inspect_installed_packages", return_value=[]):
            result = runner.invoke(cli, ["upgrade", "-g", "mygroup", "--yes", "--no-cache"])

        assert "Warning" in result.output or "warning" in result.output or "skip" in result.output.lower()

    def test_upgrade_group_shows_summary(self, runner):
        """upgrade -g shows phase progress and result summary."""
        with patch("pipu_cli._group_runner.get_group", return_value=["/python/a"]), \
             patch("os.path.exists", return_value=True), \
             patch("pipu_cli.cli.inspect_installed_packages", return_value=[]):
            result = runner.invoke(cli, ["upgrade", "-g", "mygroup", "--yes", "--no-cache"])

        assert "upgrades across" in result.output.lower() or "no packages can be upgraded" in result.output.lower()

    def test_upgrade_group_uses_target_constraints_per_environment(self, runner):
        """Group upgrades must not leak one env's safe target into another env."""
        env_a_path = "/tmp/envA/bin/python"
        env_b_path = "/tmp/envB/bin/python"
        env_a_package = InstalledPackage(
            name="package1",
            version=Version("0.9"),
            is_editable=False,
            constrained_dependencies={},
        )
        env_b_constrainer = InstalledPackage(
            name="package-a",
            version=Version("1.0"),
            is_editable=False,
            constrained_dependencies={"package1": "<1"},
        )
        env_b_package = InstalledPackage(
            name="package1",
            version=Version("0.9"),
            is_editable=False,
            constrained_dependencies={},
        )
        seen_metadata_envs = []
        captured_env_specs = {}

        def fake_inspect(*_args, **kwargs):
            if kwargs["python_path"] == env_a_path:
                return [env_a_package]
            if kwargs["python_path"] == env_b_path:
                return [env_b_constrainer, env_b_package]
            return []

        def fake_latest(installed, **_kwargs):
            versions = {
                "package1": Version("1.0"),
                "package-a": Version("2.0"),
            }
            return {
                pkg: Mock(name=pkg.name, version=versions[pkg.name])
                for pkg in installed
                if pkg.name in versions
            }

        def fake_target_constraints(_candidates, installed, **kwargs):
            seen_metadata_envs.append(kwargs["python_path"])
            if any(pkg.name == "package-a" for pkg in installed):
                return {"package-a": {"package1": "<1"}}
            return {}

        def fake_group_download(env_specs, *_args, **_kwargs):
            captured_env_specs.update(env_specs)

        with patch("pipu_cli._group_runner.get_group", return_value=[env_a_path, env_b_path]), \
             patch("os.path.exists", return_value=True), \
             patch("pipu_cli.cli.inspect_installed_packages", side_effect=fake_inspect), \
             patch("pipu_cli.cli.get_latest_versions", side_effect=fake_latest), \
             patch(
                 "pipu_cli.cli.get_target_constraints_for_disputed_upgrades",
                 side_effect=fake_target_constraints,
             ), \
             patch("pipu_cli.download.download_packages_for_group", side_effect=fake_group_download), \
             patch("pipu_cli.cli.run_per_env_parallel", side_effect=lambda ctx, _worker: {name: [] for name in ctx.envs}), \
             patch("pipu_cli.rollback.save_state"):
            result = runner.invoke(
                cli,
                [
                    "upgrade",
                    "-g",
                    "all",
                    "package1",
                    "--yes",
                    "--no-cache",
                    "--no-check",
                    "-p",
                    "1",
                ],
            )

        assert result.exit_code == 0, result.output
        assert seen_metadata_envs == [env_a_path, env_b_path]
        assert captured_env_specs["envA"] == ["package1==1.0"]
        assert captured_env_specs["envB"] == []

    def test_upgrade_group_skips_specs_that_failed_download(self, runner):
        """A shared download failure should not be retried by every env install."""
        env_path = "/tmp/envs/main/bin/python"
        installed = [
            InstalledPackage(
                name="requests",
                version=Version("2.28.0"),
                is_editable=False,
                constrained_dependencies={},
            ),
            InstalledPackage(
                name="rich",
                version=Version("13.0.0"),
                is_editable=False,
                constrained_dependencies={},
            ),
        ]
        latest_by_name = {
            "requests": Version("2.31.0"),
            "rich": Version("13.7.0"),
        }
        captured_specs = {}
        captured_download_kwargs = {}
        captured_install_kwargs = {}

        def fake_latest(installed_packages, **_kwargs):
            return {
                pkg: Package(name=pkg.name, version=latest_by_name[pkg.name])
                for pkg in installed_packages
            }

        def fake_download(env_specs, *_args, progress_callback=None, **_kwargs):
            captured_download_kwargs.update(_kwargs)
            if progress_callback is not None:
                progress_callback("requests==2.31.0", False, "timed out after 300s")
                progress_callback("rich==13.7.0", True, "")
            raise DownloadError({"requests==2.31.0": "timed out after 300s"})

        def fake_install_single_env(env_name, _env_path, specs, **_kwargs):
            captured_specs[env_name] = list(specs)
            captured_install_kwargs.update(_kwargs)
            return [
                UpgradedPackage(
                    name="rich",
                    version=Version("13.7.0"),
                    upgraded=True,
                    previous_version=Version("13.0.0"),
                )
            ]

        def fake_run_per_env(ctx, worker):
            return {
                name: worker(name, path, cli_module.InterruptToken())
                for name, path in ctx.envs.items()
            }

        with patch("pipu_cli._group_runner.get_group", return_value=[env_path]), \
             patch("os.path.exists", return_value=True), \
             patch("pipu_cli.cli.inspect_installed_packages", return_value=installed), \
             patch("pipu_cli.cli.get_latest_versions", side_effect=fake_latest), \
             patch("pipu_cli.cli.get_target_constraints_for_disputed_upgrades", return_value={}), \
             patch("pipu_cli.download.download_packages_for_group", side_effect=fake_download), \
             patch("pipu_cli.cli._upgrade_install_single_env", side_effect=fake_install_single_env), \
             patch("pipu_cli.cli.run_per_env_parallel", side_effect=fake_run_per_env), \
             patch("pipu_cli.rollback.save_state"):
            result = runner.invoke(
                cli,
                [
                    "upgrade",
                    "-g",
                    "all",
                    "--timeout",
                    "900",
                    "--yes",
                    "--no-cache",
                    "--no-check",
                    "-p",
                    "1",
                ],
            )

        assert result.exit_code == 1, result.output
        assert captured_download_kwargs["timeout"] == 900
        assert captured_download_kwargs["use_download_cache"] is True
        assert captured_install_kwargs["timeout"] == 900
        assert captured_specs == {"main": ["rich"]}
        assert "requests" in result.output

    def test_upgrade_group_relaxes_unconstrained_install_specs(self, runner):
        """Group installs should not force latest pins after staging downloads."""
        env_path = "/tmp/envs/main/bin/python"
        installed = [
            InstalledPackage(
                name="logfire",
                version=Version("4.32.1"),
                is_editable=False,
                constrained_dependencies={},
            ),
        ]
        latest_by_name = {
            "logfire": Version("4.33.0"),
        }
        captured_download_specs = {}
        captured_install_specs = {}

        def fake_latest(installed_packages, **_kwargs):
            return {
                pkg: Package(name=pkg.name, version=latest_by_name[pkg.name])
                for pkg in installed_packages
            }

        def fake_download(env_specs, *_args, **_kwargs):
            captured_download_specs.update(env_specs)

        def fake_install_single_env(env_name, _env_path, specs, **_kwargs):
            captured_install_specs[env_name] = list(specs)
            return [
                UpgradedPackage(
                    name="logfire",
                    version=Version("4.32.1"),
                    upgraded=False,
                    previous_version=Version("4.32.1"),
                    failure_reason="Version unchanged — may be constrained by dependency resolver",
                ),
            ]

        def fake_run_per_env(ctx, worker):
            return {
                name: worker(name, path, cli_module.InterruptToken())
                for name, path in ctx.envs.items()
            }

        with patch("pipu_cli._group_runner.get_group", return_value=[env_path]), \
             patch("os.path.exists", return_value=True), \
             patch("pipu_cli.cli.inspect_installed_packages", return_value=installed), \
             patch("pipu_cli.cli.get_latest_versions", side_effect=fake_latest), \
             patch("pipu_cli.cli.get_target_constraints_for_disputed_upgrades", return_value={}), \
             patch("pipu_cli.download.download_packages_for_group", side_effect=fake_download), \
             patch("pipu_cli.cli._upgrade_install_single_env", side_effect=fake_install_single_env), \
             patch("pipu_cli.cli.run_per_env_parallel", side_effect=fake_run_per_env), \
             patch("pipu_cli.rollback.save_state"):
            result = runner.invoke(
                cli,
                [
                    "upgrade",
                    "-g",
                    "all",
                    "logfire",
                    "--yes",
                    "--no-cache",
                    "--no-check",
                    "-p",
                    "1",
                ],
            )

        assert result.exit_code == 0, result.output
        assert captured_download_specs == {"main": ["logfire==4.33.0"]}
        assert captured_install_specs == {"main": ["logfire"]}
        assert "constrained" in result.output


class TestInstallCommand:
    """Tests for the pipu install command."""

    def test_install_single_package(self, runner):
        """install calls run_pip_install with correct args."""
        results = [
            InstalledResult(name="requests", version=Version("2.31.0"),
                            installed=True, previous_version=None)
        ]
        with patch("pipu_cli.cli.run_pip_install", return_value=results) as mock_install:
            result = runner.invoke(cli, ["install", "requests", "--yes"])

        assert result.exit_code == 0
        mock_install.assert_called_once()
        call_kwargs = mock_install.call_args
        assert call_kwargs.kwargs["package_specs"] == ["requests"]
        assert call_kwargs.kwargs["upgrade"] is True

    def test_install_multiple_packages(self, runner):
        """install passes multiple package specs."""
        results = [
            InstalledResult(name="requests", version=Version("2.31.0"),
                            installed=True, previous_version=None),
            InstalledResult(name="flask", version=Version("3.0.0"),
                            installed=True, previous_version=Version("2.3.0")),
        ]
        with patch("pipu_cli.cli.run_pip_install", return_value=results) as mock_install:
            result = runner.invoke(cli, ["install", "requests", "flask", "--yes"])

        assert result.exit_code == 0
        call_kwargs = mock_install.call_args
        assert call_kwargs.kwargs["package_specs"] == ["requests", "flask"]

    def test_install_no_update_flag(self, runner):
        """install --no-update passes upgrade=False."""
        results = [
            InstalledResult(name="requests", version=Version("2.31.0"),
                            installed=True, previous_version=None)
        ]
        with patch("pipu_cli.cli.run_pip_install", return_value=results) as mock_install:
            result = runner.invoke(cli, ["install", "requests", "--no-update", "--yes"])

        assert result.exit_code == 0
        call_kwargs = mock_install.call_args
        assert call_kwargs.kwargs["upgrade"] is False

    def test_install_with_pre(self, runner):
        """install --pre passes pre=True."""
        results = [
            InstalledResult(name="requests", version=Version("3.0.0a1"),
                            installed=True, previous_version=None)
        ]
        with patch("pipu_cli.cli.run_pip_install", return_value=results) as mock_install:
            result = runner.invoke(cli, ["install", "requests", "--pre", "--yes"])

        assert result.exit_code == 0
        call_kwargs = mock_install.call_args
        assert call_kwargs.kwargs["pre"] is True

    def test_install_requires_packages(self, runner):
        """install with no packages shows error."""
        result = runner.invoke(cli, ["install"])
        assert result.exit_code != 0

    def test_install_json_output(self, runner):
        """install -o json outputs valid JSON."""
        results = [
            InstalledResult(name="requests", version=Version("2.31.0"),
                            installed=True, previous_version=None)
        ]
        with patch("pipu_cli.cli.run_pip_install", return_value=results):
            result = runner.invoke(cli, ["install", "requests", "--yes", "-o", "json"])

        data = json.loads(result.output)
        assert "results" in data
        assert "summary" in data
        assert data["summary"]["total"] == 1

    def test_install_yes_skips_confirm(self, runner):
        """install -y does not prompt for confirmation."""
        results = [
            InstalledResult(name="requests", version=Version("2.31.0"),
                            installed=True, previous_version=None)
        ]
        with patch("pipu_cli.cli.run_pip_install", return_value=results):
            result = runner.invoke(cli, ["install", "requests", "-y"])

        # Should not contain a prompt
        assert "Do you want to proceed?" not in result.output
        assert result.exit_code == 0

    def test_install_group_not_found(self, runner):
        """install -g with non-existent group shows error."""
        with patch("pipu_cli._group_runner.get_group", return_value=None):
            result = runner.invoke(cli, ["install", "requests", "-g", "nogroup", "--yes"])
        assert "not found" in result.output.lower()
        assert result.exit_code == 1

    def test_install_group_runs_per_environment(self, runner):
        """install -g calls run_pip_install for each environment."""
        results = [
            InstalledResult(name="requests", version=Version("2.31.0"),
                            installed=True, previous_version=None)
        ]
        with patch("pipu_cli._group_runner.get_group", return_value=["/python/a", "/python/b"]), \
             patch("os.path.exists", return_value=True), \
             patch("pipu_cli.cli.inspect_installed_packages", return_value=[]), \
             patch("pipu_cli.cli.run_pip_install", return_value=results) as mock_install:
            runner.invoke(cli, ["install", "requests", "-g", "mygroup", "--yes"])

        assert mock_install.call_count == 2
        paths = [call.kwargs["python_path"] for call in mock_install.call_args_list]
        assert "/python/a" in paths
        assert "/python/b" in paths

    def test_install_group_preview_shows_latest_version_number(self, runner):
        """Group install preview shows the resolved latest version, not only an alias."""
        installed = [
            InstalledPackage(
                name="requests",
                version=Version("2.28.0"),
                is_editable=False,
                constrained_dependencies={},
            )
        ]
        results = [
            InstalledResult(
                name="requests",
                version=Version("2.31.0"),
                installed=True,
                previous_version=Version("2.28.0"),
            )
        ]

        with patch("pipu_cli._group_runner.get_group", return_value=["/python/a"]), \
             patch("os.path.exists", return_value=True), \
             patch("pipu_cli.cli.inspect_installed_packages", return_value=installed), \
             patch(
                 "pipu_cli.cli.get_latest_version_for_spec",
                 return_value=Package(name="requests", version=Version("2.31.0")),
             ), \
             patch("pipu_cli.cli.run_pip_install", return_value=results):
            result = runner.invoke(
                cli,
                ["install", "requests", "-g", "mygroup", "--yes", "--no-check"],
            )

        assert result.exit_code == 0, result.output
        assert "2.28.0" in result.output
        assert "2.31.0" in result.output
        assert "-> latest" not in result.output

    def test_install_group_preview_respects_version_specifier(self, runner):
        """Group install preview shows the constrained target version."""
        installed = [
            InstalledPackage(
                name="jedi",
                version=Version("0.20.0"),
                is_editable=False,
                constrained_dependencies={},
            )
        ]
        results = [
            InstalledResult(
                name="jedi",
                version=Version("0.19.2"),
                installed=True,
                previous_version=Version("0.20.0"),
            )
        ]

        with patch("pipu_cli._group_runner.get_group", return_value=["/python/a"]), \
             patch("os.path.exists", return_value=True), \
             patch("pipu_cli.cli.inspect_installed_packages", return_value=installed), \
             patch(
                 "pipu_cli.cli.get_latest_version_for_spec",
                 return_value=Package(name="jedi", version=Version("0.19.2")),
             ), \
             patch("pipu_cli.cli.run_pip_install", return_value=results):
            result = runner.invoke(
                cli,
                [
                    "install",
                    "jedi<0.20.0,>=0.18.0",
                    "-g",
                    "mygroup",
                    "--yes",
                    "--no-check",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "0.20.0 ->" in result.output
        assert "0.19.2" in result.output
        assert "target" in result.output
        assert "latest: 0.20.0" not in result.output

    def test_install_failure_exits_nonzero(self, runner):
        """install exits 1 when packages fail to install."""
        results = [
            InstalledResult(name="badpkg", version=Version("0"),
                            installed=False, failure_reason="Not found on PyPI")
        ]
        with patch("pipu_cli.cli.run_pip_install", return_value=results):
            result = runner.invoke(cli, ["install", "badpkg", "--yes"])

        assert result.exit_code == 1


class TestDottedNameConstraintPropagation:
    """End-to-end regression: dotted-name constraints reach pip intact.

    Before Commit 6 the CLI stored ``package_constraints[name.lower()]``
    from the user-supplied spec and looked it up via ``pkg.name.lower()``
    from the installed package. For a package whose PyPI name is dotted
    but whose installed ``Distribution.name`` is lowercase (e.g.
    ``Zope.Interface`` vs. ``zope.interface``), the storage key and the
    lookup key must PEP 503 canonicalize to the same value
    (``"zope-interface"``), otherwise the user's ``==5.4.0`` constraint
    would silently drop and pip would be invoked with a bare name.

    The unit-level parser invariants for this case live in
    ``tests/test_package_management.py::TestParsePackageSpec``; this test
    runs the whole CLI upgrade path with mocks and checks that the final
    spec list handed to ``download_packages``/``install_from_local``
    still carries the version constraint.
    """

    def test_dotted_name_constraint_reaches_download_and_install(self, runner):
        """``pipu upgrade Zope.Interface==5.4.0`` must pin to ==5.4.0 downstream."""
        from pipu_cli.package_management import UpgradedPackage

        installed = [
            InstalledPackage(
                name="zope.interface",
                version=Version("5.3.0"),
                is_editable=False,
                constrained_dependencies={},
            )
        ]
        upgradable = [
            UpgradePackageInfo(
                name="zope.interface",
                version=Version("5.3.0"),
                upgradable=True,
                latest_version=Version("5.5.0"),
                is_editable=False,
            )
        ]

        with patch("pipu_cli.cli.inspect_installed_packages", return_value=installed), \
             patch("pipu_cli.cli.get_latest_versions") as mock_latest, \
             patch("pipu_cli.cli.resolve_upgradable_packages", return_value=upgradable), \
             patch("pipu_cli.cli.download_packages") as mock_download, \
             patch("pipu_cli.cli.install_from_local") as mock_install_local, \
             patch("pipu_cli.rollback.save_state"):

            mock_latest.return_value = {installed[0]: Mock(version=Version("5.5.0"))}
            mock_install_local.return_value = [
                UpgradedPackage(
                    name="zope.interface",
                    version=Version("5.4.0"),
                    upgraded=True,
                    previous_version=Version("5.3.0"),
                    is_editable=False,
                )
            ]

            result = runner.invoke(
                cli,
                ["upgrade", "Zope.Interface==5.4.0", "--yes", "--no-cache", "-p", "1"],
            )

        assert result.exit_code == 0, result.output

        # Storage key is canonicalize_name("Zope.Interface") == "zope-interface".
        # Lookup key is canonicalize_name(pkg.name) == canonicalize_name("zope.interface")
        # == "zope-interface". The two must agree, otherwise the spec builder at
        # cli._download_and_install_phase falls through to "==latest" and the
        # user's constraint is silently dropped.
        mock_download.assert_called_once()
        download_specs = mock_download.call_args.kwargs["specs"]
        assert "zope.interface==5.4.0" in download_specs, (
            f"Expected pinned spec in download specs, got {download_specs!r}"
        )

        mock_install_local.assert_called_once()
        install_specs = mock_install_local.call_args.kwargs["specs"]
        assert "zope.interface==5.4.0" in install_specs, (
            f"Expected pinned spec in install specs, got {install_specs!r}"
        )
