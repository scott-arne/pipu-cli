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

        result = runner.invoke(cli, ['--dry-run', '--yes'])

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

        result = runner.invoke(cli, ['--dry-run'])

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

        result = runner.invoke(cli, ['--exclude', 'numpy', '--dry-run'])

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

        result = runner.invoke(cli, ['--exclude', 'numpy,pandas', '--dry-run'])

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

        result = runner.invoke(cli, ['--show-blocked', '--dry-run'])

        assert 'Blocked' in result.output or 'blocked' in result.output.lower()
        assert 'package-b' in result.output
        assert result.exit_code == 0
