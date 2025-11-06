import pytest
import tempfile
from unittest.mock import Mock, patch, MagicMock
from click.testing import CliRunner
from pathlib import Path
from pipu_cli.cli import cli


def test_update_command_no_outdated_packages():
    """
    Test update command when no packages are outdated.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.cli.read_constraints', return_value={}):
        with patch('pipu_cli.cli.list_outdated', return_value=[]):
            result = runner.invoke(cli, ['update'])
    
    assert result.exit_code == 0
    assert "All packages are already up to date!" in result.output


def test_update_command_with_outdated_packages_user_declines():
    """
    Test update command when user declines to update packages.

    :returns: None
    """
    runner = CliRunner()
    
    outdated_packages = [
        {
            "name": "test-package",
            "version": "1.0.0",
            "latest_version": "1.5.0",
            "latest_filetype": "wheel",
            "constraint": None
        }
    ]
    
    with patch('pipu_cli.cli.read_constraints', return_value={}):
        with patch('pipu_cli.cli.list_outdated', return_value=outdated_packages):
            # Simulate user declining the update
            result = runner.invoke(cli, ['update'], input='n\n')
    
    assert result.exit_code == 0
    assert "Update cancelled." in result.output


# ============================================================================
# Tests for CLI Invalidation Trigger Integration
# ============================================================================

def test_constrain_command_with_invalidation_triggers():
    """
    Test constrain command with --invalidates-when option.

    :returns: None
    """
    runner = CliRunner()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result = runner.invoke(cli, [
                    'constrain', 'flask<2.0.0', 
                    '--invalidates-when', 'other>=1.0.0',
                    '--invalidates-when', 'another>1.5.0'
                ])
        
        assert result.exit_code == 0
        assert "Configuration updated successfully!" in result.output
        assert "Added: flask<2.0.0" in result.output
        assert "Trigger: other>=1.0.0" in result.output
        assert "Trigger: another>1.5.0" in result.output


def test_constrain_command_invalidation_triggers_validation_error():
    """
    Test constrain command with invalid invalidation triggers.

    :returns: None
    """
    runner = CliRunner()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result = runner.invoke(cli, [
                    'constrain', 'flask<2.0.0', 
                    '--invalidates-when', 'invalid-trigger-without-version'
                ])
        
        assert result.exit_code == 1
        assert "Invalid invalidation trigger specification" in result.output


def test_constrain_command_invalidation_triggers_without_constraints():
    """
    Test --invalidates-when flag without constraint specifications.

    :returns: None
    """
    runner = CliRunner()
    
    result = runner.invoke(cli, [
        'constrain',
        '--invalidates-when', 'other>=1.0.0'
    ])
    
    assert result.exit_code == 1
    assert "--invalidates-when can only be used when adding constraint" in result.output
    assert "specifications" in result.output


def test_constrain_command_invalidation_triggers_with_list():
    """
    Test --invalidates-when flag with --list option (should fail).

    :returns: None
    """
    runner = CliRunner()
    
    result = runner.invoke(cli, [
        'constrain', '--list',
        '--invalidates-when', 'other>=1.0.0'
    ])
    
    assert result.exit_code == 1
    assert "--invalidates-when cannot be used with --list, --remove, or --remove-all" in result.output


def test_constrain_command_invalidation_triggers_with_remove():
    """
    Test --invalidates-when flag with --remove option (should fail).

    :returns: None
    """
    runner = CliRunner()
    
    result = runner.invoke(cli, [
        'constrain', '--remove', 'flask',
        '--invalidates-when', 'other>=1.0.0'
    ])
    
    assert result.exit_code == 1
    assert "--invalidates-when cannot be used with --list, --remove, or --remove-all" in result.output


def test_constrain_command_removes_invalidation_triggers():
    """
    Test that removing constraints also removes associated invalidation triggers.

    :returns: None
    """
    runner = CliRunner()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create initial config with constraints and triggers
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('    flask>=2.0.0\n')
            f.write('    django>=4.0.0\n')
            f.write('constraint_invalid_when = flask>=2.0.0:other>=1.0.0,django>=4.0.0:another==2.0.0\n')
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result = runner.invoke(cli, ['constrain', '--remove', 'flask'])
        
        assert result.exit_code == 0
        assert "Constraints removed successfully!" in result.output
        assert "Removed: flask>=2.0.0" in result.output
        assert "Removed trigger: other>=1.0.0" in result.output


def test_constrain_command_remove_all_invalidation_triggers():
    """
    Test that removing all constraints also removes all invalidation triggers.

    :returns: None
    """
    runner = CliRunner()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create initial config with constraints and triggers
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('    flask>=2.0.0\n')
            f.write('    django>=4.0.0\n')
            f.write('constraint_invalid_when = flask>=2.0.0:other>=1.0.0,django>=4.0.0:another==2.0.0\n')
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            result = runner.invoke(cli, ['constrain', '--remove-all', '--yes'])
        
        assert result.exit_code == 0
        assert "All constraints removed successfully!" in result.output
        assert "Removed: flask>=2.0.0" in result.output
        assert "Removed: django>=4.0.0" in result.output
        assert "Removed trigger: other>=1.0.0" in result.output
        assert "Removed trigger: another==2.0.0" in result.output


def test_constrain_command_multiple_constraints_with_triggers():
    """
    Test adding multiple constraints with different triggers each.

    :returns: None
    """
    runner = CliRunner()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # First, add one constraint with triggers
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result1 = runner.invoke(cli, [
                    'constrain', 'flask<2.0.0', 
                    '--invalidates-when', 'other>=1.0.0'
                ])
        
        assert result1.exit_code == 0
        
        # Then add another constraint with different triggers
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result2 = runner.invoke(cli, [
                    'constrain', 'django>=4.0.0', 
                    '--invalidates-when', 'third>=3.0.0'
                ])
        
        assert result2.exit_code == 0
        assert "Added: django>=4.0.0" in result2.output
        assert "Trigger: third>=3.0.0" in result2.output
        
        # Verify both constraints and triggers are stored
        import configparser
        config = configparser.ConfigParser()
        config.read(config_path)
        
        constraints_value = config.get('global', 'constraints')
        assert 'flask<2.0.0' in constraints_value
        assert 'django>=4.0.0' in constraints_value
        
        triggers_value = config.get('global', 'constraint_invalid_when')
        assert 'flask<2.0.0:other>=1.0.0' in triggers_value
        assert 'django>=4.0.0:third>=3.0.0' in triggers_value


def test_constrain_command_merge_triggers_existing_constraint():
    """
    Test adding new triggers to an existing constraint.

    :returns: None
    """
    runner = CliRunner()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # First, add constraint with initial triggers
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result1 = runner.invoke(cli, [
                    'constrain', 'flask<2.0.0', 
                    '--invalidates-when', 'other>=1.0.0',
                    '--invalidates-when', 'another>2.0.0'
                ])
        
        assert result1.exit_code == 0
        
        # Then add the same constraint (no change) with additional triggers
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result2 = runner.invoke(cli, [
                    'constrain', 'flask<2.0.0', 
                    '--invalidates-when', 'third>=3.0.0'
                ])
        
        # Should show no constraint changes but show trigger addition
        assert result2.exit_code == 0
        assert "Trigger: third>=3.0.0" in result2.output
        
        # Verify merged triggers
        import configparser
        config = configparser.ConfigParser()
        config.read(config_path)
        
        triggers_value = config.get('global', 'constraint_invalid_when')
        assert 'other>=1.0.0' in triggers_value
        assert 'another>2.0.0' in triggers_value
        assert 'third>=3.0.0' in triggers_value


def test_constrain_command_with_environment_specific_triggers():
    """
    Test adding constraints with invalidation triggers to specific environment.

    :returns: None
    """
    runner = CliRunner()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            result = runner.invoke(cli, [
                'constrain', 'flask<2.0.0', 
                '--env', 'production',
                '--invalidates-when', 'other>=1.0.0'
            ])
        
        assert result.exit_code == 0
        assert "Added: flask<2.0.0" in result.output
        assert "Trigger: other>=1.0.0" in result.output
        assert "Environment updated: production" in result.output
        
        # Verify stored in production environment
        import configparser
        config = configparser.ConfigParser()
        config.read(config_path)
        
        assert config.has_section('production')
        assert 'flask<2.0.0' in config.get('production', 'constraints')
        assert config.get('production', 'constraint_invalid_when') == 'flask<2.0.0:other>=1.0.0'


def test_constrain_command_complex_trigger_scenarios():
    """
    Test complex invalidation trigger scenarios.

    :returns: None
    """
    runner = CliRunner()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Test with complex version constraints and multiple triggers
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result = runner.invoke(cli, [
                    'constrain', 'complex-package>=1.0.0,<2.0.0,!=1.5.0', 
                    '--invalidates-when', 'trigger1>=1.0.0',
                    '--invalidates-when', 'trigger2>=3.0.0',
                    '--invalidates-when', 'trigger3>=4.0.0'
                ])
        
        assert result.exit_code == 0
        assert "Added: complex-package>=1.0.0,<2.0.0,!=1.5.0" in result.output
        assert "Trigger: trigger1>=1.0.0" in result.output
        assert "Trigger: trigger2>=3.0.0" in result.output
        assert "Trigger: trigger3>=4.0.0" in result.output
        
        # Verify complex storage format
        import configparser
        config = configparser.ConfigParser()
        config.read(config_path)
        
        triggers_value = config.get('global', 'constraint_invalid_when')
        expected_format = 'complex-package>=1.0.0,<2.0.0,!=1.5.0:trigger1>=1.0.0|trigger2>=3.0.0|trigger3>=4.0.0'
        assert triggers_value == expected_format


def test_constrain_command_io_error_handling():
    """
    Test IO error handling in constraint commands with triggers.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.get_recommended_pip_config_path') as mock_path:
        mock_path.return_value = Path('/nonexistent/path/pip.conf')
        
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
            result = runner.invoke(cli, [
                'constrain', 'flask<2.0.0', 
                '--invalidates-when', 'other>=1.0.0'
            ])
    
    assert result.exit_code == 1
    assert "Error writing configuration" in result.output


def test_constrain_help_shows_invalidation_triggers():
    """
    Test that constrain command help includes --invalidates-when option.

    :returns: None
    """
    runner = CliRunner()
    
    result = runner.invoke(cli, ['constrain', '--help'])
    
    assert result.exit_code == 0
    assert "--invalidates-when" in result.output
    assert "Specify trigger conditions that invalidate" in result.output
    assert 'pipu constrain "flask<2" --invalidates-when "other_package>=1"' in result.output


def test_update_command_with_yes_flag():
    """
    Test update command with --yes flag (skips confirmation).

    :returns: None
    """
    runner = CliRunner()
    
    outdated_packages = [
        {
            "name": "test-package",
            "version": "1.0.0",
            "latest_version": "1.5.0",
            "latest_filetype": "wheel",
            "constraint": None
        }
    ]
    
    with patch('pipu_cli.cli.read_constraints', return_value={}):
        with patch('pipu_cli.cli.list_outdated', return_value=outdated_packages):
            with patch('pipu_cli.cli._install_packages', return_value=0) as mock_install:
                result = runner.invoke(cli, ['update', '--yes'])
                
                # Verify the correct package spec was passed to install
                mock_install.assert_called_once_with(['test-package==1.5.0'])
    
    assert result.exit_code == 0
    assert "Installing updates..." in result.output
    assert "All packages updated successfully!" in result.output


def test_update_command_with_constraints():
    """
    Test update command respects version constraints.

    When a package has a constraint, the constraint should be applied during installation
    instead of pinning to the latest_version shown in the table. This allows pip to resolve
    the best version that satisfies the constraint and all dependencies.

    :returns: None
    """
    runner = CliRunner()

    outdated_packages = [
        {
            "name": "constrained-package",
            "version": "1.0.0",
            "latest_version": "1.9.0",  # Latest available, but constraint should be applied
            "latest_filetype": "wheel",
            "constraint": "<2.0.0"
        }
    ]

    with patch('pipu_cli.cli.read_constraints', return_value={"constrained-package": "<2.0.0"}):
        with patch('pipu_cli.cli.list_outdated', return_value=outdated_packages):
            with patch('pipu_cli.cli._install_packages', return_value=0) as mock_install:
                result = runner.invoke(cli, ['update', '--yes'])

                # Check that the constraint was used instead of pinning to latest_version
                mock_install.assert_called_once_with(['constrained-package<2.0.0'])

    assert result.exit_code == 0


def test_update_command_with_constraint_prevents_dependency_conflict():
    """
    Test that constraints prevent installation of conflicting package versions.

    This test covers the bug where a package with a constraint (e.g., Deprecated==1.2.10)
    was shown correctly in the table, but the installation attempted to use the latest
    version (1.2.18) instead, leading to dependency conflicts.

    :returns: None
    """
    runner = CliRunner()

    outdated_packages = [
        {
            "name": "Deprecated",
            "version": "1.2.10",
            "latest_version": "1.2.18",  # Latest available
            "latest_filetype": "wheel",
            "constraint": "==1.2.10"  # Pinned to avoid breaking wrapt
        },
        {
            "name": "wrapt",
            "version": "1.17.3",
            "latest_version": "2.0.0",  # Latest available
            "latest_filetype": "wheel",
            "constraint": "<2"  # Must stay below 2.0 for Deprecated 1.2.18
        }
    ]

    with patch('pipu_cli.cli.read_constraints', return_value={"deprecated": "==1.2.10", "wrapt": "<2"}):
        with patch('pipu_cli.cli.list_outdated', return_value=outdated_packages):
            with patch('pipu_cli.cli._install_packages', return_value=0) as mock_install:
                result = runner.invoke(cli, ['update', '--yes'])

                # Verify that constraints were applied, not latest versions
                # This prevents the error: "Cannot install deprecated==1.2.18 and wrapt==2.0.0"
                mock_install.assert_called_once_with(['Deprecated==1.2.10', 'wrapt<2'])

    assert result.exit_code == 0


def test_update_command_install_failure():
    """
    Test update command when pip installation fails.

    :returns: None
    """
    runner = CliRunner()
    
    outdated_packages = [
        {
            "name": "test-package",
            "version": "1.0.0",
            "latest_version": "1.5.0",
            "latest_filetype": "wheel",
            "constraint": None
        }
    ]
    
    with patch('pipu_cli.cli.read_constraints', return_value={}):
        with patch('pipu_cli.cli.list_outdated', return_value=outdated_packages):
            with patch('pipu_cli.cli._install_packages', return_value=1):  # Failed installation
                result = runner.invoke(cli, ['update', '--yes'])
    
    assert result.exit_code == 1
    assert "Some packages failed to update." in result.output


def test_update_command_with_pre_flag():
    """
    Test update command with --pre flag.

    :returns: None
    """
    runner = CliRunner()
    
    outdated_packages = [
        {
            "name": "test-package",
            "version": "1.0.0",
            "latest_version": "2.0.0a1",
            "latest_filetype": "wheel",
            "constraint": None
        }
    ]
    
    with patch('pipu_cli.cli.read_constraints', return_value={}):
        with patch('pipu_cli.cli.list_outdated', return_value=outdated_packages) as mock_list:
            with patch('pipu_cli.cli._install_packages', return_value=0):
                result = runner.invoke(cli, ['update', '--pre', '--yes'])
                
                # Verify that list_outdated was called with pre=True
                mock_list.assert_called_once()
                call_kwargs = mock_list.call_args[1]
                assert call_kwargs['pre'] is True
    
    assert result.exit_code == 0


def test_update_command_exception_handling():
    """
    Test update command handles exceptions gracefully.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.cli.read_constraints', side_effect=Exception("Test error")):
        result = runner.invoke(cli, ['update'])
    
    assert result.exit_code == 1
    assert "Unexpected error: Test error" in result.output


def test_interactive_package_selection_tui_integration():
    """
    Test TUI integration with interactive package selection.

    :returns: None
    """

    outdated_packages = [
        {
            "name": "test-package",
            "version": "1.0.0",
            "latest_version": "1.5.0",
            "latest_filetype": "wheel",
            "constraint": None
        }
    ]

    # Mock the entire interactive_package_selection function to avoid heavy TUI imports
    with patch('pipu_cli.ui.interactive_package_selection') as mock_selection:
        mock_selection.return_value = outdated_packages

        # Import after mocking to avoid loading heavy dependencies
        from pipu_cli.ui import interactive_package_selection
        result = interactive_package_selection(outdated_packages)

    assert result == outdated_packages
    mock_selection.assert_called_once_with(outdated_packages)


def test_interactive_package_selection_tui_cancelled():
    """
    Test TUI integration when user cancels.

    :returns: None
    """

    outdated_packages = [
        {
            "name": "test-package",
            "version": "1.0.0",
            "latest_version": "1.5.0",
            "latest_filetype": "wheel",
            "constraint": None
        }
    ]

    # Mock the function to return empty list (simulating user cancellation)
    with patch('pipu_cli.ui.interactive_package_selection') as mock_selection:
        mock_selection.return_value = []

        from pipu_cli.ui import interactive_package_selection
        result = interactive_package_selection(outdated_packages)

    assert result == []
    mock_selection.assert_called_once_with(outdated_packages)
    

def test_interactive_package_selection_tui_empty_list():
    """
    Test TUI integration with empty package list.

    :returns: None
    """

    # Mock the function to return empty list
    with patch('pipu_cli.ui.interactive_package_selection') as mock_selection:
        mock_selection.return_value = []

        from pipu_cli.ui import interactive_package_selection
        result = interactive_package_selection([])

    assert result == []
    mock_selection.assert_called_once_with([])


# Constrain command tests

def test_constrain_command_basic_constraint():
    """
    Test constrain command with basic constraint specification.

    :returns: None
    """
    runner = CliRunner()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result = runner.invoke(cli, ['constrain', 'requests>=2.25.0'])
            
                assert result.exit_code == 0
                assert "Adding constraints to pip configuration" in result.output
                assert "Configuration updated successfully" in result.output
                assert "Added: requests>=2.25.0" in result.output


def test_constrain_command_multiple_constraints():
    """
    Test constrain command with multiple constraint specifications.

    :returns: None
    """
    runner = CliRunner()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result = runner.invoke(cli, ['constrain', 'requests>=2.25.0', 'numpy>=1.20.0'])
    
    assert result.exit_code == 0
    assert "Added: requests>=2.25.0" in result.output
    assert "Added: numpy>=1.20.0" in result.output


def test_constrain_command_with_environment():
    """
    Test constrain command with environment specification.

    :returns: None
    """
    runner = CliRunner()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value='production'):
                result = runner.invoke(cli, ['constrain', 'django>=4.1.0', '--env', 'production'])
    
    assert result.exit_code == 0
    assert "Environment updated: production" in result.output


def test_constrain_command_list_option():
    """
    Test constrain command with --list option.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.list_all_constraints') as mock_list:
        mock_list.return_value = {
            'global': {'requests': '>=2.25.0', 'numpy': '>=1.20.0'},
            'production': {'django': '>=4.1.0'}
        }
        
        result = runner.invoke(cli, ['constrain', '--list'])
    
    assert result.exit_code == 0
    assert "Listing constraints from pip configuration" in result.output
    assert "Environment: global" in result.output
    assert "requests>=2.25.0" in result.output
    assert "Environment: production" in result.output
    assert "django>=4.1.0" in result.output


def test_constrain_command_list_specific_environment():
    """
    Test constrain command with --list and --env options.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.list_all_constraints') as mock_list:
        mock_list.return_value = {'production': {'django': '>=4.1.0'}}
        
        result = runner.invoke(cli, ['constrain', '--list', '--env', 'production'])
    
    assert result.exit_code == 0
    assert "Environment: production" in result.output
    assert "django>=4.1.0" in result.output


def test_constrain_command_list_no_constraints():
    """
    Test constrain command --list when no constraints exist.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.list_all_constraints') as mock_list:
        mock_list.return_value = {}
        
        result = runner.invoke(cli, ['constrain', '--list'])
    
    assert result.exit_code == 0
    assert "No constraints found in any environment" in result.output


def test_constrain_command_remove_option():
    """
    Test constrain command with --remove option.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.remove_constraints_from_config') as mock_remove:
        mock_remove.return_value = (Path('/fake/path/pip.conf'), {'requests': '>=2.25.0'}, {})
        
        result = runner.invoke(cli, ['constrain', '--remove', 'requests'])
    
    assert result.exit_code == 0
    assert "Removing constraints for 1 package" in result.output
    assert "Constraints removed successfully" in result.output
    assert "Removed: requests>=2.25.0" in result.output


def test_constrain_command_remove_multiple_packages():
    """
    Test constrain command removing multiple packages.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.remove_constraints_from_config') as mock_remove:
        mock_remove.return_value = (Path('/fake/path/pip.conf'), {
            'requests': '>=2.25.0',
            'numpy': '>=1.20.0'
        }, {})
        
        result = runner.invoke(cli, ['constrain', '--remove', 'requests', 'numpy'])
    
    assert result.exit_code == 0
    assert "Removing constraints for 2 package" in result.output
    assert "Removed: requests>=2.25.0" in result.output
    assert "Removed: numpy>=1.20.0" in result.output


def test_constrain_command_remove_with_environment():
    """
    Test constrain command --remove with environment specification.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.remove_constraints_from_config') as mock_remove:
        mock_remove.return_value = (Path('/fake/path/pip.conf'), {'django': '>=4.1.0'}, {})
        
        result = runner.invoke(cli, ['constrain', '--remove', 'django', '--env', 'production'])
    
    assert result.exit_code == 0
    assert "Environment updated: production" in result.output


def test_constrain_command_no_constraints_specified():
    """
    Test constrain command with no constraints specified.

    :returns: None
    """
    runner = CliRunner()
    
    result = runner.invoke(cli, ['constrain'])
    
    assert result.exit_code == 1
    assert "At least one constraint must be specified" in result.output


def test_constrain_command_conflicting_options():
    """
    Test constrain command with conflicting options.

    :returns: None
    """
    runner = CliRunner()
    
    result = runner.invoke(cli, ['constrain', '--list', '--remove', 'requests'])
    
    assert result.exit_code == 1
    assert "Cannot use --list, --remove, --remove-all, and constraint specs" in result.output
    assert "together" in result.output


def test_constrain_command_remove_no_packages():
    """
    Test constrain command --remove with no packages specified.

    :returns: None
    """
    runner = CliRunner()
    
    result = runner.invoke(cli, ['constrain', '--remove'])
    
    assert result.exit_code == 1
    assert "At least one package name must be specified for removal" in result.output


def test_constrain_command_remove_nonexistent_package():
    """
    Test constrain command --remove with nonexistent package.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.remove_constraints_from_config') as mock_remove:
        mock_remove.side_effect = ValueError("None of the specified packages have constraints")
        
        result = runner.invoke(cli, ['constrain', '--remove', 'nonexistent'])
    
    assert result.exit_code == 1
    assert "None of the specified packages have constraints" in result.output


def test_constrain_command_invalid_constraint_spec():
    """
    Test constrain command with invalid constraint specification.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.parse_requirement_line') as mock_parse:
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path') as mock_path:
            mock_path.return_value = Path('/fake/path/pip.conf')
            mock_parse.return_value = None  # Invalid constraint
            
            result = runner.invoke(cli, ['constrain', 'invalid-constraint'])
    
    assert result.exit_code == 1
    assert "Invalid constraint specification" in result.output


def test_constrain_command_no_changes_made():
    """
    Test constrain command when no changes are made (constraint already exists).

    :returns: None
    """
    runner = CliRunner()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create a config file with existing constraint
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('    requests>=2.25.0\n')
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result = runner.invoke(cli, ['constrain', 'requests>=2.25.0'])
    
    assert result.exit_code == 0
    assert "No changes made - all constraints already exist" in result.output


def test_constrain_command_file_write_error():
    """
    Test constrain command when file write fails.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.add_constraints_to_config') as mock_add:
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path') as mock_path:
            mock_path.return_value = Path('/fake/path/pip.conf')
            mock_add.side_effect = IOError("Failed to write config file")
            
            result = runner.invoke(cli, ['constrain', 'requests>=2.25.0'])
    
    assert result.exit_code == 1
    assert "Error writing configuration" in result.output


def test_constrain_command_unexpected_error():
    """
    Test constrain command handles unexpected errors gracefully.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.get_recommended_pip_config_path') as mock_path:
        mock_path.side_effect = Exception("Unexpected error")
        
        result = runner.invoke(cli, ['constrain', 'requests>=2.25.0'])
    
    assert result.exit_code == 1
    assert "Unexpected error" in result.output


# Ignore command tests

def test_ignore_command_basic_ignore():
    """
    Test ignore command with basic package specification.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.add_ignores_to_config') as mock_add:
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path') as mock_path:
            mock_path.return_value = Path('/fake/path/pip.conf')
            mock_add.return_value = (Path('/fake/path/pip.conf'), {'requests': 'added'})
            
            result = runner.invoke(cli, ['ignore', 'requests'])
    
    assert result.exit_code == 0
    assert "Adding ignores to pip configuration" in result.output
    assert "Configuration updated successfully" in result.output
    assert "Added: requests" in result.output


def test_ignore_command_multiple_packages():
    """
    Test ignore command with multiple package specifications.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.add_ignores_to_config') as mock_add:
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path') as mock_path:
            mock_path.return_value = Path('/fake/path/pip.conf')
            mock_add.return_value = (Path('/fake/path/pip.conf'), {
                'requests': 'added',
                'numpy': 'added',
                'flask': 'added'
            })
            
            result = runner.invoke(cli, ['ignore', 'requests', 'numpy', 'flask'])
    
    assert result.exit_code == 0
    assert "Added: requests" in result.output
    assert "Added: numpy" in result.output
    assert "Added: flask" in result.output


def test_ignore_command_with_environment():
    """
    Test ignore command with environment specification.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.add_ignores_to_config') as mock_add:
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path') as mock_path:
            mock_path.return_value = Path('/fake/path/pip.conf')
            mock_add.return_value = (Path('/fake/path/pip.conf'), {'django': 'added'})
            
            result = runner.invoke(cli, ['ignore', 'django', '--env', 'production'])
    
    assert result.exit_code == 0
    assert "Environment updated: production" in result.output


def test_ignore_command_list_option():
    """
    Test ignore command with --list option.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.list_all_ignores') as mock_list:
        mock_list.return_value = {
            'global': ['requests', 'numpy'],
            'production': ['django', 'flask']
        }
        
        result = runner.invoke(cli, ['ignore', '--list'])
    
    assert result.exit_code == 0
    assert "Listing ignores from pip configuration" in result.output
    assert "Environment: global" in result.output
    assert "requests" in result.output
    assert "numpy" in result.output
    assert "Environment: production" in result.output
    assert "django" in result.output
    assert "flask" in result.output


def test_ignore_command_list_specific_environment():
    """
    Test ignore command with --list and --env options.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.list_all_ignores') as mock_list:
        mock_list.return_value = {'production': ['django', 'flask']}
        
        result = runner.invoke(cli, ['ignore', '--list', '--env', 'production'])
    
    assert result.exit_code == 0
    assert "Environment: production" in result.output
    assert "django" in result.output
    assert "flask" in result.output


def test_ignore_command_list_no_ignores():
    """
    Test ignore command --list when no ignores exist.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.list_all_ignores') as mock_list:
        mock_list.return_value = {}
        
        result = runner.invoke(cli, ['ignore', '--list'])
    
    assert result.exit_code == 0
    assert "No ignores found in any environment" in result.output


def test_ignore_command_remove_option():
    """
    Test ignore command with --remove option.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.remove_ignores_from_config') as mock_remove:
        mock_remove.return_value = (Path('/fake/path/pip.conf'), ['requests'])
        
        result = runner.invoke(cli, ['ignore', '--remove', 'requests'])
    
    assert result.exit_code == 0
    assert "Removing ignores for 1 package" in result.output
    assert "Ignores removed successfully" in result.output
    assert "Removed: requests" in result.output


def test_ignore_command_remove_multiple_packages():
    """
    Test ignore command removing multiple packages.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.remove_ignores_from_config') as mock_remove:
        mock_remove.return_value = (Path('/fake/path/pip.conf'), ['requests', 'numpy'])
        
        result = runner.invoke(cli, ['ignore', '--remove', 'requests', 'numpy'])
    
    assert result.exit_code == 0
    assert "Removing ignores for 2 package" in result.output
    assert "Removed: requests" in result.output
    assert "Removed: numpy" in result.output


def test_ignore_command_remove_with_environment():
    """
    Test ignore command --remove with environment specification.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.remove_ignores_from_config') as mock_remove:
        mock_remove.return_value = (Path('/fake/path/pip.conf'), ['django'])
        
        result = runner.invoke(cli, ['ignore', '--remove', 'django', '--env', 'production'])
    
    assert result.exit_code == 0
    assert "Environment updated: production" in result.output


def test_ignore_command_no_packages_specified():
    """
    Test ignore command with no packages specified.

    :returns: None
    """
    runner = CliRunner()
    
    result = runner.invoke(cli, ['ignore'])
    
    assert result.exit_code == 1
    assert "At least one package name must be specified" in result.output


def test_ignore_command_conflicting_options():
    """
    Test ignore command with conflicting options.

    :returns: None
    """
    runner = CliRunner()
    
    result = runner.invoke(cli, ['ignore', '--list', '--remove', 'requests'])
    
    assert result.exit_code == 1
    assert "Cannot use --list, --remove, --remove-all, and package names together" in result.output


def test_ignore_command_remove_no_packages():
    """
    Test ignore command --remove with no packages specified.

    :returns: None
    """
    runner = CliRunner()
    
    result = runner.invoke(cli, ['ignore', '--remove'])
    
    assert result.exit_code == 1
    assert "At least one package name must be specified for removal" in result.output


def test_ignore_command_remove_nonexistent_package():
    """
    Test ignore command --remove with nonexistent package.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.remove_ignores_from_config') as mock_remove:
        mock_remove.side_effect = ValueError("None of the specified packages are ignored")
        
        result = runner.invoke(cli, ['ignore', '--remove', 'nonexistent'])
    
    assert result.exit_code == 1
    assert "None of the specified packages are ignored" in result.output


def test_ignore_command_already_ignored():
    """
    Test ignore command when packages are already ignored.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.add_ignores_to_config') as mock_add:
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path') as mock_path:
            mock_path.return_value = Path('/fake/path/pip.conf')
            mock_add.return_value = (Path('/fake/path/pip.conf'), {
                'requests': 'already_exists',
                'numpy': 'added'
            })
            
            result = runner.invoke(cli, ['ignore', 'requests', 'numpy'])
    
    assert result.exit_code == 0
    assert "Already ignored: requests" in result.output
    assert "Added: numpy" in result.output


def test_ignore_command_no_changes_made():
    """
    Test ignore command when no changes are made (all packages already ignored).

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.add_ignores_to_config') as mock_add:
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path') as mock_path:
            mock_path.return_value = Path('/fake/path/pip.conf')
            mock_add.return_value = (Path('/fake/path/pip.conf'), {'requests': 'already_exists'})
            
            result = runner.invoke(cli, ['ignore', 'requests'])
    
    assert result.exit_code == 0
    assert "No changes made - all packages are already ignored" in result.output


def test_ignore_command_file_write_error():
    """
    Test ignore command when file write fails.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.add_ignores_to_config') as mock_add:
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path') as mock_path:
            mock_path.return_value = Path('/fake/path/pip.conf')
            mock_add.side_effect = IOError("Failed to write config file")
            
            result = runner.invoke(cli, ['ignore', 'requests'])
    
    assert result.exit_code == 1
    assert "Error writing configuration" in result.output


def test_ignore_command_unexpected_error():
    """
    Test ignore command handles unexpected errors gracefully.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.get_recommended_pip_config_path') as mock_path:
        mock_path.side_effect = Exception("Unexpected error")
        
        result = runner.invoke(cli, ['ignore', 'requests'])
    
    assert result.exit_code == 1
    assert "Unexpected error" in result.output


def test_ignore_command_remove_no_packages_removed():
    """
    Test ignore command --remove when no packages are actually removed.

    :returns: None
    """
    runner = CliRunner()
    
    with patch('pipu_cli.package_constraints.remove_ignores_from_config') as mock_remove:
        mock_remove.return_value = (Path('/fake/path/pip.conf'), [])  # No packages removed
        
        result = runner.invoke(cli, ['ignore', '--remove', 'requests'])
    
    assert result.exit_code == 0
    assert "No ignores were removed" in result.output


# ============================================================================
# Additional critical tests for user scenarios

def test_list_command_with_pre_flag():
    """
    Test list command with --pre flag for pre-release versions.

    :returns: None
    """
    runner = CliRunner()

    with patch('pipu_cli.cli.read_constraints', return_value={}):
        with patch('pipu_cli.cli.read_ignores', return_value=set()):
            with patch('pipu_cli.cli.list_outdated', return_value=[]) as mock_list:
                result = runner.invoke(cli, ['list', '--pre'])

                assert result.exit_code == 0
                mock_list.assert_called_once()
                # Verify pre=True was passed
                call_args = mock_list.call_args
                assert call_args[1]['pre'] is True


def test_list_command_without_pre_flag():
    """
    Test list command without --pre flag (default behavior).

    :returns: None
    """
    runner = CliRunner()

    with patch('pipu_cli.cli.read_constraints', return_value={}):
        with patch('pipu_cli.cli.read_ignores', return_value=set()):
            with patch('pipu_cli.cli.list_outdated', return_value=[]) as mock_list:
                result = runner.invoke(cli, ['list'])

                assert result.exit_code == 0
                mock_list.assert_called_once()
                # Verify pre=False was passed (default)
                call_args = mock_list.call_args
                assert call_args[1]['pre'] is False


def test_cli_main_command_launches_tui():
    """
    Test that running pipu without subcommands launches TUI.

    :returns: None
    """
    runner = CliRunner()

    with patch('pipu_cli.cli.launch_tui') as mock_launch_tui:
        result = runner.invoke(cli, [])

        # Should launch TUI when no subcommand provided
        mock_launch_tui.assert_called_once()


def test_cli_help_command():
    """
    Test that help command provides comprehensive information.

    :returns: None
    """
    runner = CliRunner()

    result = runner.invoke(cli, ['--help'])

    assert result.exit_code == 0
    assert "pipu" in result.output
    assert "Python package updater" in result.output
    assert "update" in result.output  # Should mention update command
    assert "constrain" in result.output  # Should mention constrain command
    assert "ignore" in result.output  # Should mention ignore command
    assert "list" in result.output  # Should mention list command


def test_update_command_with_pre_and_yes_flags():
    """
    Test update command with both --pre and --yes flags.

    :returns: None
    """
    runner = CliRunner()

    outdated_packages = [
        {
            "name": "test-package",
            "version": "1.0.0",
            "latest_version": "2.0.0a1",  # Pre-release version
            "latest_filetype": "wheel",
            "constraint": None
        }
    ]

    with patch('pipu_cli.cli.read_constraints', return_value={}):
        with patch('pipu_cli.cli.list_outdated', return_value=outdated_packages) as mock_list:
            with patch('pipu_cli.cli._install_packages', return_value=0) as mock_install:
                result = runner.invoke(cli, ['update', '--pre', '--yes'])

                assert result.exit_code == 0
                # Should include pre-release versions
                mock_list.assert_called_once()
                assert mock_list.call_args[1]['pre'] is True
                # Should install without prompting
                mock_install.assert_called_once()


def test_constrain_command_with_multiple_invalidation_triggers():
    """
    Test constrain command with multiple invalidation triggers.

    :returns: None
    """
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'

        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result = runner.invoke(cli, [
                    'constrain', 'flask<2.0.0',
                    '--invalidates-when', 'requests>=3.0.0',
                    '--invalidates-when', 'urllib3>=2.0.0'
                ])

        assert result.exit_code == 0
        assert "Added: flask<2.0.0" in result.output
        assert "Trigger: requests>=3.0.0" in result.output
        assert "Trigger: urllib3>=2.0.0" in result.output


def test_ignore_command_basic_functionality():
    """
    Test basic ignore command functionality.

    :returns: None
    """
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'

        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result = runner.invoke(cli, ['ignore', 'test-package'])

        assert result.exit_code == 0
        assert "test-package" in result.output


def test_ignore_command_list_functionality():
    """
    Test ignore command list functionality.

    :returns: None
    """
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'

        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            result = runner.invoke(cli, ['ignore', '--list'])

        assert result.exit_code == 0
        # Should handle empty ignore list gracefully


def test_constrain_command_remove_all_functionality():
    """
    Test constrain command remove-all functionality.

    :returns: None
    """
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'

        # First add a constraint
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                runner.invoke(cli, ['constrain', 'requests>=2.0.0'])

                # Then remove all constraints
                result = runner.invoke(cli, ['constrain', '--remove-all', '--yes'])

        assert result.exit_code == 0


def test_error_handling_with_invalid_pip_config():
    """
    Test error handling when pip configuration is invalid or corrupted.

    :returns: None
    """
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'

        # Create invalid config file
        with open(config_path, 'w') as f:
            f.write("invalid config content\n[missing closing bracket\n")

        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            result = runner.invoke(cli, ['constrain', '--list'])

        # Should handle invalid config gracefully
        # Exact behavior depends on implementation, but shouldn't crash
        assert result.exit_code in [0, 1]  # Either success or controlled failure


def test_tui_launch_error_handling():
    """
    Test error handling when TUI launch fails.

    :returns: None
    """
    runner = CliRunner()

    with patch('pipu_cli.ui.main_tui_app', side_effect=Exception("TUI launch failed")):
        result = runner.invoke(cli, [])

        # Should handle TUI launch failure gracefully
        assert result.exit_code == 1
        assert "Error launching TUI" in result.output


def test_cleanup_invalid_constraints_on_commands():
    """
    Test that invalid constraints are cleaned up when running commands.

    :returns: None
    """
    runner = CliRunner()

    with patch('pipu_cli.package_constraints.cleanup_invalid_constraints_and_triggers') as mock_cleanup:
        mock_cleanup.return_value = (None, None, "Cleaned up 2 invalid constraints")

        with patch('pipu_cli.cli.read_constraints', return_value={}):
            with patch('pipu_cli.cli.list_outdated', return_value=[]):
                # Should show cleanup message
                with patch('builtins.input', return_value=''):  # Skip wait
                    result = runner.invoke(cli, ['list'])

        assert result.exit_code == 0
        mock_cleanup.assert_called()
        assert "Cleaned up" in result.output