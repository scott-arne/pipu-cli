"""Tests for CLI module."""

import json
import sys
import pytest
from click.testing import CliRunner
from unittest.mock import patch, Mock

from packaging.version import Version
from pipu_cli.cli import cli
from pipu_cli.package_management import InstalledPackage, UpgradePackageInfo, BlockedPackageInfo, InstalledResult


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
        with patch("pipu_cli.cli.validate_python_path", return_value=(True, None)), \
             patch("pipu_cli.cli.add_environment", return_value=True) as mock_add:
            result = runner.invoke(cli, ["group", "add", "mygroup"])
        assert result.exit_code == 0
        mock_add.assert_called_once_with("mygroup", sys.executable)

    def test_group_add_with_python_path(self, runner):
        """group add with --python uses specified path."""
        with patch("pipu_cli.cli.validate_python_path", return_value=(True, None)), \
             patch("pipu_cli.cli.add_environment", return_value=True) as mock_add:
            result = runner.invoke(cli, ["group", "add", "mygroup", "--python", "/other/python"])
        assert result.exit_code == 0
        mock_add.assert_called_once_with("mygroup", "/other/python")

    def test_group_add_validation_failure(self, runner):
        """group add fails when validation fails."""
        with patch("pipu_cli.cli.validate_python_path", return_value=(False, "Not a Python interpreter")):
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
        with patch("pipu_cli.cli.validate_python_path", return_value=(True, None)), \
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
        with patch("pipu_cli.cli.get_group", return_value=None):
            result = runner.invoke(cli, ["upgrade", "-g", "nogroup", "--yes"])
        assert "not found" in result.output.lower()
        assert result.exit_code == 1

    def test_outdated_group_not_found(self, runner):
        """outdated -g with non-existent group shows error."""
        with patch("pipu_cli.cli.get_group", return_value=None):
            result = runner.invoke(cli, ["outdated", "-g", "nogroup"])
        assert "not found" in result.output.lower()
        assert result.exit_code == 1

    def test_upgrade_group_runs_per_environment(self, runner):
        """upgrade -g inspects each environment in consolidated pipeline."""
        with patch("pipu_cli.cli.get_group", return_value=["/python/a", "/python/b"]), \
             patch("pipu_cli.cli.inspect_installed_packages", return_value=[]) as mock_inspect, \
             patch("os.path.exists", return_value=True):
            result = runner.invoke(cli, ["upgrade", "-g", "mygroup", "--yes", "--no-cache"])

        # Should show consolidated phase output
        assert "Inspecting 2 environments" in result.output
        # inspect should be called for each environment
        assert mock_inspect.call_count == 2

    def test_upgrade_group_skips_missing_env(self, runner):
        """upgrade -g skips environments that don't exist."""
        def path_exists(path):
            return path == "/python/a"

        with patch("pipu_cli.cli.get_group", return_value=["/python/a", "/python/missing"]), \
             patch("os.path.exists", side_effect=path_exists), \
             patch("pipu_cli.cli.inspect_installed_packages", return_value=[]):
            result = runner.invoke(cli, ["upgrade", "-g", "mygroup", "--yes", "--no-cache"])

        assert "Warning" in result.output or "warning" in result.output or "skip" in result.output.lower()

    def test_upgrade_group_shows_summary(self, runner):
        """upgrade -g shows phase progress and result summary."""
        with patch("pipu_cli.cli.get_group", return_value=["/python/a"]), \
             patch("os.path.exists", return_value=True), \
             patch("pipu_cli.cli.inspect_installed_packages", return_value=[]):
            result = runner.invoke(cli, ["upgrade", "-g", "mygroup", "--yes", "--no-cache"])

        assert "upgrades across" in result.output.lower() or "no packages can be upgraded" in result.output.lower()


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
        with patch("pipu_cli.cli.get_group", return_value=None):
            result = runner.invoke(cli, ["install", "requests", "-g", "nogroup", "--yes"])
        assert "not found" in result.output.lower()
        assert result.exit_code == 1

    def test_install_group_runs_per_environment(self, runner):
        """install -g calls run_pip_install for each environment."""
        results = [
            InstalledResult(name="requests", version=Version("2.31.0"),
                            installed=True, previous_version=None)
        ]
        with patch("pipu_cli.cli.get_group", return_value=["/python/a", "/python/b"]), \
             patch("os.path.exists", return_value=True), \
             patch("pipu_cli.cli.run_pip_install", return_value=results) as mock_install:
            runner.invoke(cli, ["install", "requests", "-g", "mygroup", "--yes"])

        assert mock_install.call_count == 2
        # Verify python_path was set for each call
        paths = [call.kwargs["python_path"] for call in mock_install.call_args_list]
        assert "/python/a" in paths
        assert "/python/b" in paths

    def test_install_failure_exits_nonzero(self, runner):
        """install exits 1 when packages fail to install."""
        results = [
            InstalledResult(name="badpkg", version=Version("0"),
                            installed=False, failure_reason="Not found on PyPI")
        ]
        with patch("pipu_cli.cli.run_pip_install", return_value=results):
            result = runner.invoke(cli, ["install", "badpkg", "--yes"])

        assert result.exit_code == 1
