"""Tests for CLI module."""

import pytest
from click.testing import CliRunner
from unittest.mock import patch, Mock

from packaging.version import Version
from pipu_cli.cli import cli
from pipu_cli.package_management import InstalledPackage, UpgradePackageInfo


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
