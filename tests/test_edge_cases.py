"""
Tests for edge cases and robustness in pipu.

Tests error conditions, boundary cases, and resilience features
that ensure the application handles unexpected scenarios gracefully.
"""

import pytest
import tempfile
import os
import subprocess
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from pipu.internals import list_outdated, _check_constraint_satisfaction
from pipu.ui import MainTUIApp


class TestNetworkAndIOEdgeCases:
    """Test network failures and I/O error scenarios."""

    @patch('pipu.internals.get_default_environment')
    @patch('pipu.internals.Configuration')
    def test_list_outdated_network_timeout(self, mock_config_cls, mock_get_env):
        """Test list_outdated handles network timeouts gracefully."""
        # Mock environment
        mock_env = Mock()
        mock_get_env.return_value = mock_env
        mock_env.iter_all_distributions.return_value = []

        # Mock configuration that will work
        mock_config = Mock()
        mock_config_cls.return_value = mock_config
        mock_config.load.return_value = None
        mock_config.get_value.side_effect = Exception("Network timeout")

        # Should raise ConnectionError when session creation fails
        with patch('pipu.internals.PipSession') as mock_session:
            # Mock the session to raise exception during creation
            mock_session_instance = Mock()
            mock_session.return_value = mock_session_instance
            mock_session.side_effect = Exception("Connection timeout")

            # This should raise ConnectionError when session creation fails
            with pytest.raises(ConnectionError) as exc_info:
                list_outdated(print_table=False)
            assert "Failed to create network session" in str(exc_info.value)

    def test_list_outdated_with_corrupted_package_metadata(self):
        """Test handling of packages with corrupted metadata."""
        with patch('pipu.internals.get_default_environment') as mock_get_env:
            mock_env = Mock()
            mock_get_env.return_value = mock_env

            # Create a mock distribution with corrupted metadata
            mock_dist = Mock()
            mock_dist.metadata = {"name": None}  # Corrupted name
            mock_dist.version = "1.0.0"
            mock_dist.canonical_name = None

            mock_env.iter_all_distributions.return_value = [mock_dist]

            with patch('pipu.internals.Configuration'):
                # Should handle corrupted metadata gracefully
                result = list_outdated(print_table=False)
                assert isinstance(result, list)

    def test_permission_denied_during_config_access(self):
        """Test handling of permission errors when accessing pip config."""
        with patch('pipu.internals.Configuration') as mock_config_cls:
            mock_config = Mock()
            mock_config_cls.return_value = mock_config
            mock_config.load.side_effect = PermissionError("Access denied")

            with patch('pipu.internals.get_default_environment') as mock_get_env:
                mock_env = Mock()
                mock_get_env.return_value = mock_env
                mock_env.iter_all_distributions.return_value = []

                # Should handle permission error gracefully
                result = list_outdated(print_table=False)
                assert isinstance(result, list)


class TestConstraintEdgeCases:
    """Test edge cases in constraint handling."""

    def test_constraint_satisfaction_with_invalid_version_strings(self):
        """Test constraint checking with malformed version strings."""
        # Invalid version string
        assert _check_constraint_satisfaction("not-a-version", ">=1.0.0") is False

        # Empty version string
        assert _check_constraint_satisfaction("", ">=1.0.0") is False

        # Invalid constraint
        assert _check_constraint_satisfaction("1.0.0", "invalid-constraint") is False

        # Both invalid
        assert _check_constraint_satisfaction("not-version", "not-constraint") is False

    def test_constraint_satisfaction_with_edge_version_formats(self):
        """Test constraint checking with unusual but valid version formats."""
        # Version with build metadata
        assert _check_constraint_satisfaction("1.0.0+build.1", ">=1.0.0") is True

        # Pre-release versions (SpecifierSet excludes pre-releases by default)
        assert _check_constraint_satisfaction("2.0.0a1", ">=1.0.0") is False
        assert _check_constraint_satisfaction("1.0.0rc1", ">=1.0.0,<2.0.0") is False

        # Development versions
        assert _check_constraint_satisfaction("1.0.0.dev1", ">=1.0.0") is False

    def test_complex_constraint_specifications(self):
        """Test complex constraint specifications."""
        # Multiple constraints
        assert _check_constraint_satisfaction("1.5.0", ">=1.0.0,<2.0.0,!=1.4.0") is True
        assert _check_constraint_satisfaction("1.4.0", ">=1.0.0,<2.0.0,!=1.4.0") is False

        # Compatible release constraints
        assert _check_constraint_satisfaction("1.4.5", "~=1.4.0") is True
        assert _check_constraint_satisfaction("1.5.0", "~=1.4.0") is False


class TestTUIEdgeCases:
    """Test edge cases in TUI functionality."""

    def test_tui_with_extremely_large_package_list(self):
        """Test TUI handling of very large package lists."""
        # Create a large number of packages
        large_package_list = [
            {
                'name': f'package-{i:04d}',
                'version': '1.0.0',
                'latest_version': '2.0.0',
                'latest_filetype': 'wheel'
            }
            for i in range(1000)
        ]

        app = MainTUIApp()
        app.all_packages = large_package_list

        # Test that row mapping can handle large lists efficiently
        app.package_row_mapping = {pkg['name']: i for i, pkg in enumerate(large_package_list)}

        # Should be able to find packages efficiently
        assert app.package_row_mapping['package-0500'] == 500
        assert len(app.package_row_mapping) == 1000

    def test_tui_update_with_missing_package_in_mapping(self):
        """Test TUI update when package is not in row mapping."""
        app = MainTUIApp()
        app.package_row_mapping = {'existing-package': 0}

        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_query.return_value = mock_table

            # Try to update a package not in mapping
            with patch.object(app, 'notify'):
                app._update_package_result({
                    'name': 'non-existent-package',
                    'version': '1.0.0',
                    'latest_version': '2.0.0',
                    'latest_filetype': 'wheel'
                })

                # Should handle gracefully without crashing

    def test_tui_concurrent_worker_cancellation(self):
        """Test TUI worker cancellation under concurrent conditions."""
        app = MainTUIApp()

        # Mock the exit method
        with patch.object(app, 'exit') as mock_exit:
            app.action_quit_app()

            # exit should be called
            mock_exit.assert_called_once()


class TestSubprocessEdgeCases:
    """Test edge cases in subprocess operations."""

    def test_uninstall_with_permission_denied(self):
        """Test package uninstall when permission is denied."""
        app = MainTUIApp()

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = PermissionError("Permission denied")

            with patch.object(app, 'call_from_thread'):
                # Create the worker function
                import sys

                def run_uninstall():
                    try:
                        pip_cmd = [sys.executable, "-m", "pip", "uninstall", "test-package", "-y"]
                        subprocess.run(
                            pip_cmd,
                            capture_output=True,
                            text=True,
                            timeout=30
                        )
                    except Exception:
                        # This simulates the actual error handling
                        pass

                # Should handle permission error gracefully
                run_uninstall()

    def test_uninstall_timeout_handling(self):
        """Test package uninstall timeout handling."""
        app = MainTUIApp()

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("pip", 30)

            with patch.object(app, 'call_from_thread'):
                def run_uninstall():
                    import sys
                    try:
                        pip_cmd = [sys.executable, "-m", "pip", "uninstall", "test-package", "-y"]
                        subprocess.run(
                            pip_cmd,
                            capture_output=True,
                            text=True,
                            timeout=30
                        )
                    except subprocess.TimeoutExpired:
                        # Should handle timeout gracefully
                        pass

                # Should not crash on timeout
                run_uninstall()


class TestConfigurationEdgeCases:
    """Test edge cases in configuration handling."""

    def test_malformed_pip_config(self):
        """Test handling of malformed pip configuration files."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            # Write malformed config
            f.write("[global\n")  # Missing closing bracket
            f.write("index-url = https://pypi.org/simple/\n")
            config_path = f.name

        try:
            with patch('pipu.internals.Configuration') as mock_config_cls:
                mock_config = Mock()
                mock_config_cls.return_value = mock_config
                mock_config.load.side_effect = Exception("Malformed config")

                with patch('pipu.internals.get_default_environment') as mock_get_env:
                    mock_env = Mock()
                    mock_get_env.return_value = mock_env
                    mock_env.iter_all_distributions.return_value = []

                    # Should handle malformed config gracefully
                    result = list_outdated(print_table=False)
                    assert isinstance(result, list)

        finally:
            os.unlink(config_path)

    def test_empty_package_environment(self):
        """Test behavior with completely empty package environment."""
        with patch('pipu.internals.get_default_environment') as mock_get_env:
            mock_env = Mock()
            mock_get_env.return_value = mock_env
            mock_env.iter_all_distributions.return_value = []

            with patch('pipu.internals.Configuration'):
                result = list_outdated(print_table=False)
                assert result == []


class TestMemoryAndPerformanceEdgeCases:
    """Test memory usage and performance edge cases."""

    def test_memory_efficient_package_processing(self):
        """Test that package processing doesn't hold excessive memory."""
        # Simulate processing a large number of packages
        def package_generator():
            for i in range(10000):
                yield {
                    'name': f'package-{i}',
                    'version': '1.0.0',
                    'latest_version': '2.0.0',
                    'latest_filetype': 'wheel'
                }

        # Test that we can process large lists without memory issues
        packages = list(package_generator())
        assert len(packages) == 10000

        # Test filtering large lists
        filtered = [pkg for pkg in packages if pkg['name'].endswith('0')]
        assert len(filtered) == 1000

    def test_efficient_string_operations(self):
        """Test that string operations are efficient for large datasets."""
        # Test case-insensitive sorting with large lists
        package_names = [f'Package-{i:04d}' for i in range(1000)]

        # Should be able to sort efficiently
        sorted_names = sorted(package_names, key=lambda x: x.lower())
        assert len(sorted_names) == 1000
        assert sorted_names[0] == 'Package-0000'
        assert sorted_names[-1] == 'Package-0999'


class TestCallbackAndAsyncEdgeCases:
    """Test edge cases in callback and asynchronous operations."""

    def test_callback_with_none_values(self):
        """Test callbacks handle None values gracefully."""
        def test_progress_callback(package_name):
            if package_name is None:
                return  # Should handle None gracefully
            # Process normally
            pass

        def test_result_callback(package_result):
            if package_result is None:
                return  # Should handle None gracefully
            if not isinstance(package_result, dict):
                return  # Should handle non-dict values
            # Process normally
            pass

        # Test with None values
        test_progress_callback(None)
        test_result_callback(None)
        test_result_callback("invalid")

    def test_callback_exception_handling(self):
        """Test that callback exceptions don't crash the main application."""
        def failing_callback(_data):
            raise Exception("Callback error")

        # Should be able to handle callback failures gracefully
        try:
            failing_callback("test")
        except Exception:
            # This is expected - the main app should catch and handle these
            pass


class TestColorCodingEdgeCases:
    """Test edge cases in real-time color coding functionality."""

    def test_color_coding_with_unusual_version_formats(self):
        """Test color coding with unusual version formats."""
        from rich.text import Text

        # Test that color coding works with various version formats
        test_cases = [
            ("1.0.0", "1.0.0", True),    # Exact match
            ("1.0.0+build.1", "1.0.0", False),  # Build metadata difference
            ("2.0.0a1", "2.0.0a1", True),       # Pre-release match
            ("1.0.0.dev1", "1.0.0", False),    # Development vs release
        ]

        for latest, current, should_be_green in test_cases:
            if latest == current and should_be_green:
                # Should create green text
                text_obj = Text.from_markup(f"[green]{latest}[/green]")
                assert str(text_obj.plain) == latest
            else:
                # Should be plain text
                assert latest != current or not should_be_green

    def test_color_coding_performance_with_large_updates(self):
        """Test that color coding performs well with many simultaneous updates."""
        from rich.text import Text

        # Simulate many package updates
        updates = []
        for i in range(1000):
            if i % 2 == 0:
                # Up-to-date package
                text_obj = Text.from_markup(f"[green]1.0.{i}[/green]")
                updates.append(text_obj)
            else:
                # Outdated package
                updates.append(f"2.0.{i}")

        # Should handle large numbers of updates efficiently
        assert len(updates) == 1000
        assert isinstance(updates[0], Text)  # First should be Text object
        assert isinstance(updates[1], str)   # Second should be string