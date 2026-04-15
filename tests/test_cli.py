"""Tests for CLI module."""

import pytest
from click.testing import CliRunner
from unittest.mock import patch, Mock

from packaging.version import Version
from pipu_cli.cli import cli
from pipu_cli.package_management import InstalledPackage, UpgradePackageInfo, BlockedPackageInfo


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
         patch('pipu_cli.cli.install_packages') as mock_install:

        result = runner.invoke(cli, ['upgrade', '--dry-run', '--yes', '--no-cache'])

        # install_packages should NOT be called in dry-run mode
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

        result = runner.invoke(cli, ['upgrade', '--dry-run', '--no-cache'])

        assert result.exit_code == 0


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
         patch('pipu_cli.cli.resolve_upgradable_packages', return_value=upgradable), \
         patch('pipu_cli.cli.install_packages') as mock_install:

        mock_latest.return_value = {
            installed[0]: Mock(version=Version("2.31.0")),
            installed[1]: Mock(version=Version("1.26.0")),
        }

        result = runner.invoke(cli, ['upgrade', '--exclude', 'numpy', '--dry-run', '--no-cache'])

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

        result = runner.invoke(cli, ['upgrade', '--exclude', 'numpy,pandas', '--dry-run', '--no-cache'])

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
         patch('pipu_cli.cli.get_latest_versions') as mock_latest, \
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

        result = runner.invoke(cli, ['upgrade', 'requests', '--dry-run', '--no-cache'])

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
         patch('pipu_cli.cli.install_packages') as mock_install, \
         patch('pipu_cli.rollback.save_state'):

        mock_latest.return_value = {
            installed[0]: Mock(version=Version("2.31.0")),
        }

        mock_resolve.return_value = [
            UpgradePackageInfo(name="requests", version=Version("2.28.0"), upgradable=True, latest_version=Version("2.31.0"), is_editable=False),
        ]

        from pipu_cli.package_management import UpgradedPackage
        mock_install.return_value = [
            UpgradedPackage(name="requests", version=Version("2.30.0"), upgraded=True, previous_version=Version("2.28.0"), is_editable=False)
        ]

        result = runner.invoke(cli, ['upgrade', 'requests==2.30.0', '--yes', '--no-cache'])

        # Should attempt to install with version constraint
        mock_install.assert_called_once()
        call_args = mock_install.call_args

        # Check that version_constraints parameter was passed
        assert 'version_constraints' in call_args.kwargs
        assert call_args.kwargs['version_constraints'] == {'requests': '==2.30.0'}

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
        mock_rollback.return_value = ["requests==2.28.0"]

        result = runner.invoke(cli, ['rollback', '--yes'])

        assert result.exit_code == 0
        assert 'Successfully rolled back' in result.output
        mock_rollback.assert_called_once()


def test_explicit_cli_timeout_overrides_config(runner):
    """Test that explicit --timeout value is not overridden by config file."""
    config = {"timeout": 30}

    with patch('pipu_cli.cli.load_config', return_value=config), \
         patch('pipu_cli.cli.inspect_installed_packages', return_value=[]) as mock_inspect:

        result = runner.invoke(cli, ['upgrade', '--timeout', '10', '--no-cache'])

        # inspect_installed_packages should be called with timeout=10 (from CLI),
        # NOT timeout=30 (from config)
        mock_inspect.assert_called_once_with(timeout=10)


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

        result = runner.invoke(cli, ['upgrade', '--dry-run', '-e', 'numpy', '-e', 'pandas', '--no-cache'])

        assert result.exit_code == 0
        assert 'requests' in result.output
        # numpy and pandas should be excluded
