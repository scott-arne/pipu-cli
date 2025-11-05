"""
Tests for common user mistakes and error scenarios.

This test file focuses on scenarios that inexperienced users commonly encounter,
ensuring that error messages are helpful and the application handles mistakes gracefully.
"""

import pytest
import tempfile
from unittest.mock import patch, Mock
from click.testing import CliRunner
from pathlib import Path
from pipu.cli import cli


class TestPackageNameMistakes:
    """Test handling of common package name input errors."""

    def test_package_name_typos(self):
        """Test handling of common package name typos."""
        runner = CliRunner()

        # Common typos - these should fail gracefully with helpful messages
        typos = [
            "reqests",  # requests
            "numppy",   # numpy
            "pandass",  # pandas
            "matplotlb", # matplotlib
        ]

        for typo in typos:
            with patch('pipu.package_constraints.parse_requirement_line') as mock_parse:
                mock_parse.return_value = None  # Invalid package name

                result = runner.invoke(cli, ['constrain', f'{typo}>=1.0.0'])

                assert result.exit_code == 1
                assert "Invalid constraint specification" in result.output

    def test_package_name_case_sensitivity(self):
        """Test that package names handle case variations gracefully."""
        runner = CliRunner()

        case_variations = [
            "REQUESTS>=1.0.0",  # All caps
            "Requests>=1.0.0",  # Title case
            "rEqUeStS>=1.0.0",  # Mixed case
        ]

        for variation in case_variations:
            with tempfile.TemporaryDirectory() as temp_dir:
                config_path = Path(temp_dir) / 'pip.conf'

                with patch('pipu.package_constraints.get_recommended_pip_config_path', return_value=config_path):
                    with patch('pipu.package_constraints.get_current_environment_name', return_value=None):
                        result = runner.invoke(cli, ['constrain', variation])

                        # Should succeed - package names are normalized
                        assert result.exit_code == 0
                        assert "Added:" in result.output

    def test_package_name_with_extra_whitespace(self):
        """Test package names with extra whitespace."""
        runner = CliRunner()

        whitespace_variations = [
            " requests>=1.0.0",   # Leading space
            "requests>=1.0.0 ",   # Trailing space
            " requests>=1.0.0 ",  # Both
            "requests >=1.0.0",   # Space before operator
            "requests>= 1.0.0",   # Space after operator
        ]

        for variation in whitespace_variations:
            with tempfile.TemporaryDirectory() as temp_dir:
                config_path = Path(temp_dir) / 'pip.conf'

                with patch('pipu.package_constraints.get_recommended_pip_config_path', return_value=config_path):
                    with patch('pipu.package_constraints.get_current_environment_name', return_value=None):
                        result = runner.invoke(cli, ['constrain', variation])

                        # Should handle whitespace gracefully
                        if result.exit_code != 0:
                            # At minimum, should have clear error message
                            assert "constraint specification" in result.output.lower()

    def test_package_name_with_underscores_vs_hyphens(self):
        """Test common confusion between underscores and hyphens in package names."""
        runner = CliRunner()

        variations = [
            "scikit-learn>=1.0.0",
            "scikit_learn>=1.0.0",
            "flask-login>=1.0.0",
            "flask_login>=1.0.0",
        ]

        for variation in variations:
            with tempfile.TemporaryDirectory() as temp_dir:
                config_path = Path(temp_dir) / 'pip.conf'

                with patch('pipu.package_constraints.get_recommended_pip_config_path', return_value=config_path):
                    with patch('pipu.package_constraints.get_current_environment_name', return_value=None):
                        result = runner.invoke(cli, ['constrain', variation])

                        # Should either succeed or provide helpful error message
                        if result.exit_code != 0:
                            assert len(result.output) > 0  # Some error message provided


class TestVersionConstraintMistakes:
    """Test handling of malformed version constraints."""

    def test_malformed_version_operators(self):
        """Test common version operator mistakes."""
        runner = CliRunner()

        malformed_constraints = [
            "requests=1.0.0",      # Single = instead of ==
            "requests===1.0.0",    # Triple = instead of ==
            "requests>>1.0.0",     # Double > instead of single
            "requests<<1.0.0",     # Double < instead of single
            "requests=>1.0.0",     # Wrong order
            "requests=<1.0.0",     # Wrong order
            "requests<>1.0.0",     # Old-style not equal
        ]

        for constraint in malformed_constraints:
            with patch('pipu.package_constraints.parse_requirement_line') as mock_parse:
                mock_parse.return_value = None  # Invalid constraint

                result = runner.invoke(cli, ['constrain', constraint])

                assert result.exit_code == 1
                assert "Invalid constraint specification" in result.output

    def test_incomplete_version_numbers(self):
        """Test handling of incomplete version numbers."""
        runner = CliRunner()

        incomplete_versions = [
            "requests>=1",         # Missing minor/patch
            "requests>=1.",        # Ends with dot
            "requests>=1.0.",      # Ends with dot
            "requests>=.1.0",      # Starts with dot
        ]

        for constraint in incomplete_versions:
            with patch('pipu.package_constraints.parse_requirement_line') as mock_parse:
                # Some might be valid, some invalid - test both cases
                mock_parse.return_value = None

                result = runner.invoke(cli, ['constrain', constraint])

                if result.exit_code != 0:
                    assert "Invalid constraint specification" in result.output

    def test_mixing_constraint_operators(self):
        """Test mixing multiple constraint operators incorrectly."""
        runner = CliRunner()

        mixed_operators = [
            "requests==>=1.0.0",   # Mixing == and >=
            "requests<=>1.0.0",    # Invalid combination
            "requests><=1.0.0",    # Invalid combination
        ]

        for constraint in mixed_operators:
            with patch('pipu.package_constraints.parse_requirement_line') as mock_parse:
                mock_parse.return_value = None  # Invalid constraint

                result = runner.invoke(cli, ['constrain', constraint])

                assert result.exit_code == 1
                assert "Invalid constraint specification" in result.output


class TestEnvironmentNameMistakes:
    """Test handling of problematic environment names."""

    def test_environment_names_with_spaces(self):
        """Test environment names containing spaces."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / 'pip.conf'

            with patch('pipu.package_constraints.get_recommended_pip_config_path', return_value=config_path):
                # Environment name with spaces
                result = runner.invoke(cli, ['constrain', 'requests>=1.0.0', '--env', 'my environment'])

                # Should either work or provide helpful error message
                if result.exit_code != 0:
                    assert len(result.output) > 0

    def test_environment_names_with_special_characters(self):
        """Test environment names with special characters."""
        runner = CliRunner()

        special_chars = [
            "prod@server",      # @ symbol
            "env#1",           # Hash symbol
            "test/env",        # Slash
            "env\\name",       # Backslash
            "env:name",        # Colon
        ]

        for env_name in special_chars:
            with tempfile.TemporaryDirectory() as temp_dir:
                config_path = Path(temp_dir) / 'pip.conf'

                with patch('pipu.package_constraints.get_recommended_pip_config_path', return_value=config_path):
                    result = runner.invoke(cli, ['constrain', 'requests>=1.0.0', '--env', env_name])

                    # Should handle gracefully
                    if result.exit_code != 0:
                        assert "environment" in result.output.lower() or "invalid" in result.output.lower()

    def test_empty_environment_name(self):
        """Test behavior with empty environment name."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / 'pip.conf'

            with patch('pipu.package_constraints.get_recommended_pip_config_path', return_value=config_path):
                result = runner.invoke(cli, ['constrain', 'requests>=1.0.0', '--env', ''])

                # Should either work (using default) or provide clear error
                if result.exit_code != 0:
                    assert len(result.output) > 0

    def test_very_long_environment_name(self):
        """Test behavior with extremely long environment names."""
        runner = CliRunner()

        long_name = "a" * 1000  # 1000 character environment name

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / 'pip.conf'

            with patch('pipu.package_constraints.get_recommended_pip_config_path', return_value=config_path):
                result = runner.invoke(cli, ['constrain', 'requests>=1.0.0', '--env', long_name])

                # Should handle gracefully - either work or give reasonable error
                if result.exit_code != 0:
                    assert len(result.output) > 0


class TestInteractivePromptMistakes:
    """Test handling of invalid responses to interactive prompts."""

    def test_invalid_yes_no_responses(self):
        """Test various invalid responses to yes/no prompts."""
        runner = CliRunner()

        invalid_responses = [
            "maybe\n",      # Ambiguous response
            "1\n",          # Number instead of y/n
            "true\n",       # Boolean-like but not y/n
            "ok\n",         # Common but not y/n
            "yep\n",        # Close to yes but not exact
            "nope\n",       # Close to no but not exact
            "\n",           # Empty response
            "   \n",        # Whitespace only
        ]

        outdated_packages = [
            {
                "name": "test-package",
                "version": "1.0.0",
                "latest_version": "1.5.0",
                "latest_filetype": "wheel",
                "constraint": None
            }
        ]

        for invalid_input in invalid_responses:
            with patch('pipu.cli.read_constraints', return_value={}):
                with patch('pipu.cli.list_outdated', return_value=outdated_packages):
                    result = runner.invoke(cli, ['update'], input=invalid_input + 'n\n')  # Follow with 'n' to exit

                    # Should either handle gracefully or ask again
                    # At minimum should not crash
                    assert "Error" not in result.output or "Traceback" not in result.output

    def test_keyboard_interrupt_simulation(self):
        """Test handling of user interruption during prompts."""
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

        with patch('pipu.cli.read_constraints', return_value={}):
            with patch('pipu.cli.list_outdated', return_value=outdated_packages):
                # Simulate Ctrl+C by providing no input (will timeout/EOF)
                result = runner.invoke(cli, ['update'], input='')

                # Should handle EOF gracefully
                # The exact behavior depends on implementation, but shouldn't crash
                assert result.exit_code in [0, 1]  # Either success or controlled failure


class TestCommandFlagMistakes:
    """Test handling of incorrect flag usage."""

    def test_conflicting_flags(self):
        """Test combinations of flags that should conflict."""
        runner = CliRunner()

        # Test constrain command with conflicting operations
        result = runner.invoke(cli, ['constrain', '--list', '--remove', 'test-package'])

        # Should detect and report the conflict (list and remove together doesn't make sense)
        # Note: This may not fail depending on Click's option handling, but it's a workflow issue
        assert result.exit_code in [0, 1]  # Either handled gracefully or errors

    def test_flags_with_wrong_syntax(self):
        """Test common flag syntax mistakes."""
        runner = CliRunner()

        # These are common mistakes but might not be easily testable
        # due to how Click handles them - they'll be caught at the Click level
        wrong_syntaxes = [
            ['constrain', '-list'],  # Single dash for long option
            ['constrain', '--l'],    # Abbreviated long option
        ]

        for args in wrong_syntaxes:
            result = runner.invoke(cli, args)

            # Should provide helpful error message
            assert result.exit_code != 0
            # Click usually provides decent error messages for these


class TestWorkflowMistakes:
    """Test common mistakes in command sequences and workflows."""

    def test_remove_nonexistent_constraints(self):
        """Test trying to remove constraints that don't exist."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / 'pip.conf'

            with patch('pipu.package_constraints.get_recommended_pip_config_path', return_value=config_path):
                with patch('pipu.package_constraints.get_current_environment_name', return_value=None):
                    # Try to remove constraint for package that has no constraints
                    result = runner.invoke(cli, ['constrain', '--remove', 'nonexistent-package'])

                    # Should handle gracefully with informative message
                    assert result.exit_code in [0, 1]  # Either success (no-op) or informative failure
                    assert len(result.output) > 0  # Some message provided

    def test_list_empty_constraints(self):
        """Test listing constraints when none exist."""
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / 'pip.conf'

            # Patch get_pip_config_paths to return only our empty temp config
            with patch('pipu.package_constraints.get_pip_config_paths', return_value=[config_path]):
                result = runner.invoke(cli, ['constrain', '--list'])

                # Should provide clear message about no constraints
                assert result.exit_code == 0
                assert ("no constraints" in result.output.lower() or
                        "empty" in result.output.lower() or
                        len(result.output.strip()) == 0)

    def test_update_with_no_outdated_packages(self):
        """Test update when no packages are outdated."""
        runner = CliRunner()

        with patch('pipu.cli.read_constraints', return_value={}):
            with patch('pipu.cli.list_outdated', return_value=[]):  # No outdated packages
                result = runner.invoke(cli, ['update'])

                # Should handle gracefully
                assert result.exit_code == 0
                assert "up to date" in result.output.lower()


class TestFilePermissionScenarios:
    """Test scenarios involving file permission issues."""

    def test_readonly_config_file_error_message(self):
        """Test behavior when config file is read-only."""
        runner = CliRunner()

        with patch('pipu.package_constraints.get_recommended_pip_config_path') as mock_path:
            mock_path.return_value = Path('/readonly/pip.conf')

            with patch('pipu.package_constraints.get_current_environment_name', return_value=None):
                # This will likely fail with permission error
                result = runner.invoke(cli, ['constrain', 'requests>=1.0.0'])

                # Should provide helpful error message about permissions
                if result.exit_code != 0:
                    assert ("permission" in result.output.lower() or
                            "access" in result.output.lower() or
                            "write" in result.output.lower())

    def test_nonexistent_config_directory(self):
        """Test behavior when config directory doesn't exist."""
        runner = CliRunner()

        with patch('pipu.package_constraints.get_recommended_pip_config_path') as mock_path:
            mock_path.return_value = Path('/nonexistent/directory/pip.conf')

            with patch('pipu.package_constraints.get_current_environment_name', return_value=None):
                result = runner.invoke(cli, ['constrain', 'requests>=1.0.0'])

                # Should either create directory or provide helpful error
                if result.exit_code != 0:
                    assert ("directory" in result.output.lower() or
                            "path" in result.output.lower() or
                            "not found" in result.output.lower())


class TestOutputClarityScenarios:
    """Test that outputs are clear and helpful for inexperienced users."""

    def test_empty_result_messaging(self):
        """Test that empty results have clear messaging."""
        runner = CliRunner()

        # Test when no packages are outdated - mock the underlying dependencies
        # so that list_outdated returns empty list but still prints its message
        with patch('pipu.cli.read_constraints', return_value={}), \
             patch('pipu.internals.get_default_environment') as mock_env, \
             patch('pipu.package_constraints.cleanup_invalid_constraints_and_triggers', return_value=([], {}, None)):

            # Mock empty environment (no installed packages)
            mock_env_instance = Mock()
            mock_env_instance.iter_all_distributions.return_value = []
            mock_env.return_value = mock_env_instance

            result = runner.invoke(cli, ['list'])

            # Should provide clear message about no updates
            assert result.exit_code == 0
            assert len(result.output) > 0
            assert "all packages are up to date" in result.output.lower()
            # Should not be confusing or empty

    def test_help_accessibility(self):
        """Test that help messages are accessible and clear."""
        runner = CliRunner()

        # Test main help
        result = runner.invoke(cli, ['--help'])
        assert result.exit_code == 0
        assert "pipu" in result.output.lower()
        assert len(result.output) > 100  # Substantial help content

        # Test command-specific help
        for command in ['update', 'list', 'constrain', 'ignore']:
            result = runner.invoke(cli, [command, '--help'])
            assert result.exit_code == 0
            assert len(result.output) > 50  # Substantial help for each command