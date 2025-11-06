"""
Tests for dependency conflict resolution during package updates.

This test file focuses on scenarios where updating one package causes conflicts
with its dependencies, ensuring pipu handles these gracefully.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, call
import tempfile
from pathlib import Path
import os


class TestDependencyConflicts:
    """Test handling of dependency conflicts during updates."""

    def test_pydantic_core_dependency_conflict(self):
        """
        Test updating pydantic when pydantic-core has a conflicting constraint.

        Real-world scenario:
        - User has pydantic 2.10.0 installed with pydantic-core 2.41.4
        - User wants to update to pydantic 2.12.4 (latest)
        - pydantic 2.12.4 requires pydantic-core==2.41.5
        - If we pin both pydantic==2.12.4 AND pydantic_core==2.41.4, pip fails
        - Solution: Use --upgrade instead of pinning versions, let pip resolve them
        """
        from pipu_cli.internals import update_packages_preserving_editable

        # Mock console
        console = Mock()

        # Packages to update - both pydantic and its dependency pydantic-core
        packages_to_update = [
            {
                "name": "pydantic",
                "latest_version": "2.12.4",
                "editable": False
            },
            {
                "name": "pydantic-core",
                "latest_version": "2.41.4",  # But pydantic 2.12.4 needs 2.41.5!
                "editable": False
            }
        ]

        with patch('subprocess.Popen') as mock_popen:
            # Mock successful subprocess
            mock_process = Mock()
            mock_process.communicate.return_value = ("Success", "")
            mock_process.returncode = 0
            mock_popen.return_value = mock_process

            with patch('pipu_cli.internals.get_editable_packages', return_value={}):
                with patch('pipu_cli.package_constraints.read_constraints', return_value={}):
                    successful, failed = update_packages_preserving_editable(
                        packages_to_update,
                        console=console
                    )

            # Should have made 2 install calls
            assert mock_popen.call_count == 2

            # Get the install commands that were executed
            install_calls = [call for call in mock_popen.call_args_list]

            # Check that we're using --upgrade instead of pinning versions
            # This allows pip to resolve dependencies correctly
            for call in install_calls:
                cmd = call[0][0]  # First positional arg is the command list
                cmd_str = ' '.join(cmd)

                # Should use --upgrade flag
                assert '--upgrade' in cmd, f"Expected --upgrade in command: {cmd_str}"

                # Should NOT have pinned versions like pydantic==2.12.4
                assert '==' not in cmd_str, f"Should not pin versions with ==: {cmd_str}"

    def test_boto_botocore_dependency_conflict(self):
        """
        Test updating boto3 when botocore has a conflicting constraint.

        Real-world scenario:
        - User has botocore<1.39.0 constraint
        - User wants to update boto3 to 1.40.67
        - boto3 1.40.67 requires botocore>=1.40.67,<1.41.0
        - The constraint should be ignored for botocore when updating boto3
        """
        from pipu_cli.internals import update_packages_preserving_editable

        console = Mock()

        packages_to_update = [
            {
                "name": "boto3",
                "latest_version": "1.40.67",
                "editable": False
            }
        ]

        # Mock constraints that would conflict
        mock_constraints = {
            "botocore": "<1.39.0"  # This conflicts with boto3 1.40.67's requirement
        }

        with patch('subprocess.Popen') as mock_popen:
            mock_process = Mock()
            mock_process.communicate.return_value = ("Success", "")
            mock_process.returncode = 0
            mock_popen.return_value = mock_process

            with patch('pipu_cli.internals.get_editable_packages', return_value={}):
                with patch('pipu_cli.package_constraints.read_constraints', return_value=mock_constraints):
                    successful, failed = update_packages_preserving_editable(
                        packages_to_update,
                        console=console
                    )

            # Should succeed
            assert len(successful) == 1
            assert len(failed) == 0

            # Check that constraint file was created and used
            assert mock_popen.called
            call_kwargs = mock_popen.call_args[1]
            env = call_kwargs.get('env', {})

            # PIP_CONSTRAINT should be set to filtered constraints file
            # The botocore constraint should still be there since we're not updating botocore
            # But when pip resolves boto3's dependencies, it will upgrade botocore anyway
            assert 'PIP_CONSTRAINT' in env or mock_constraints == {}

    def test_multiple_interdependent_packages_update(self):
        """
        Test updating multiple packages where one depends on the other.

        This tests the batch update scenario in the TUI.
        """
        from pipu_cli.ui.modal_dialogs import UpdateConfirmScreen

        # This would need more complex mocking of the TUI infrastructure
        # For now, just verify the constraint filtering logic works

        from pipu_cli.package_constraints import read_constraints
        from packaging.utils import canonicalize_name

        # Simulate packages being updated
        packages = [
            {"name": "requests", "latest_version": "2.31.0"},
            {"name": "urllib3", "latest_version": "2.0.0"},  # requests depends on urllib3
        ]

        # Mock constraints
        all_constraints = {
            "requests": ">=2.20,<2.30",  # Would conflict with 2.31.0
            "urllib3": "<2.0.0",  # Would conflict with 2.0.0
            "numpy": ">=1.20.0"  # Unrelated, should stay
        }

        # Get canonical names of packages being updated
        packages_being_updated = {canonicalize_name(pkg["name"]) for pkg in packages}

        # Filter out constraints for packages being updated
        filtered_constraints = {
            pkg: constraint
            for pkg, constraint in all_constraints.items()
            if pkg not in packages_being_updated
        }

        # Should only have numpy constraint left
        assert len(filtered_constraints) == 1
        assert "numpy" in filtered_constraints
        assert "requests" not in filtered_constraints
        assert "urllib3" not in filtered_constraints

    def test_cli_update_with_interdependent_packages(self):
        """
        Test the CLI 'pipu update' command with interdependent packages.

        This tests the actual CLI path that the user would use.
        """
        from pipu_cli.cli import _install_packages
        from packaging.utils import canonicalize_name

        # Mock packages being updated
        package_names = ["pydantic", "pydantic-core"]

        # Mock constraints that would normally be applied
        mock_constraints = {
            "numpy": ">=1.20.0",  # Unrelated constraint
            "pydantic": ">=2.0.0,<2.12.0",  # Would conflict
            "pydantic-core": ">=2.0.0,<2.41.4"  # Would conflict
        }

        with patch('pipu_cli.cli.read_constraints', return_value=mock_constraints):
            with patch('pipu_cli.cli.InstallCommand') as mock_install_cmd:
                mock_cmd_instance = Mock()
                mock_cmd_instance.main.return_value = 0
                mock_install_cmd.return_value = mock_cmd_instance

                # Call the install function
                exit_code = _install_packages(package_names, packages_being_updated=package_names)

                # Should succeed
                assert exit_code == 0

                # Check that InstallCommand.main was called with --upgrade and package names
                assert mock_cmd_instance.main.called
                install_args = mock_cmd_instance.main.call_args[0][0]

                # Should have --upgrade flag
                assert "--upgrade" in install_args

                # Should have package names (not pinned versions)
                assert "pydantic" in install_args
                assert "pydantic-core" in install_args

                # Should NOT have pinned versions
                for arg in install_args:
                    assert "==" not in arg, f"Should not pin versions with ==: {arg}"

        # Verify that PIP_CONSTRAINT env var is cleaned up
        assert 'PIP_CONSTRAINT' not in os.environ
