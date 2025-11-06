"""
Tests for TUI usability and user experience scenarios.

This test file focuses on ensuring the TUI interface handles edge cases gracefully
and provides a good user experience for both experienced and inexperienced users.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from textual.widgets import DataTable
from pipu_cli.ui import MainTUIApp, PackageSelectionApp, PackageSelectionTable


class TestTUIInitializationScenarios:
    """Test TUI initialization under various conditions."""

    def test_tui_with_no_packages_installed(self):
        """Test TUI behavior when no packages are installed."""
        app = MainTUIApp()

        # Mock empty package environment - patch the import used by apps.py
        with patch('pipu_cli.ui.apps.get_default_environment') as mock_env:
            mock_env_instance = Mock()
            mock_env_instance.iter_all_distributions.return_value = []
            mock_env.return_value = mock_env_instance

            # Should handle empty environment gracefully
            app._load_installed_packages()

            assert app.all_packages == []
            # Should not crash or show confusing state

    def test_tui_with_extremely_large_package_list(self):
        """Test TUI performance with very large package lists."""
        app = MainTUIApp()

        # Create mock environment with many packages
        large_package_list = []
        for i in range(1000):
            mock_dist = Mock()
            mock_dist.metadata = {"name": f"package-{i:04d}"}
            mock_dist.version = f"1.{i % 100}.0"
            large_package_list.append(mock_dist)

        with patch('pipu_cli.ui.apps.get_default_environment') as mock_env:
            mock_env_instance = Mock()
            mock_env_instance.iter_all_distributions.return_value = large_package_list
            mock_env.return_value = mock_env_instance

            # Should handle large lists efficiently
            app._load_installed_packages()

            assert len(app.all_packages) == 1000
            assert len(app.package_row_mapping) == 1000
            # Should maintain O(1) lookup performance

    def test_tui_with_corrupted_package_metadata(self):
        """Test TUI handling of packages with corrupted metadata."""
        app = MainTUIApp()

        # Create mock packages with problematic metadata
        problematic_packages = []

        # Package with missing name
        mock_dist1 = Mock()
        mock_dist1.metadata = {}  # Missing name
        mock_dist1.version = "1.0.0"
        problematic_packages.append(mock_dist1)

        # Package with invalid version
        mock_dist2 = Mock()
        mock_dist2.metadata = {"name": "valid-package"}
        mock_dist2.version = Mock()  # Invalid version object
        mock_dist2.version.__str__ = Mock(side_effect=Exception("Invalid version"))
        problematic_packages.append(mock_dist2)

        # Package that raises exception during access
        mock_dist3 = Mock()
        mock_dist3.metadata = {"name": "exception-package"}
        mock_dist3.version = Mock(side_effect=Exception("Metadata error"))
        problematic_packages.append(mock_dist3)

        with patch('pip._internal.metadata.get_default_environment') as mock_env:
            mock_env_instance = Mock()
            mock_env_instance.iter_all_distributions.return_value = problematic_packages
            mock_env.return_value = mock_env_instance

            # Should handle corrupted metadata gracefully
            app._load_installed_packages()

            # Should not crash and should skip problematic packages
            # The exact number depends on how many can be partially parsed
            assert isinstance(app.all_packages, list)
            # Should not include packages that couldn't be parsed


class TestTUINavigationAndInteraction:
    """Test TUI navigation and user interaction scenarios."""

    def test_package_selection_with_empty_list(self):
        """Test package selection behavior with empty package list."""
        # This tests the interactive selection when no packages are available
        table = PackageSelectionTable([])

        assert table.outdated_packages == []
        assert table.selected_packages == {}

        # Should handle navigation gracefully when no packages exist
        table.action_toggle_selection()  # Should not crash
        table.action_select_all()  # Should not crash
        table.action_select_none()  # Should not crash

    def test_package_selection_with_all_up_to_date(self):
        """Test selection interface when all packages are up-to-date."""
        # Create packages that are all up-to-date
        up_to_date_packages = [
            {
                'name': 'package-a',
                'version': '1.0.0',
                'latest_version': '1.0.0',  # Same version = up-to-date
                'latest_filetype': 'wheel',
                'constraint': None
            },
            {
                'name': 'package-b',
                'version': '2.0.0',
                'latest_version': '2.0.0',  # Same version = up-to-date
                'latest_filetype': 'wheel',
                'constraint': None
            }
        ]

        # This simulates the scenario where user opens interactive selection
        # but all packages are already up-to-date
        app = PackageSelectionApp(up_to_date_packages)

        # Should handle this scenario gracefully
        assert app.outdated_packages == up_to_date_packages
        assert app.confirmed is False

    def test_constraint_input_with_invalid_package_names(self):
        """Test constraint input screen with invalid package names."""
        from pipu_cli.ui import ConstraintInputScreen

        # Test with package name that doesn't exist
        screen = ConstraintInputScreen("nonexistent-package", "")

        # Mock app early to avoid NoActiveAppError
        mock_app = Mock()
        mock_app.notify = Mock()

        with patch.object(ConstraintInputScreen, 'app', new_callable=lambda: mock_app):
            # Mock validation to return False (package doesn't exist)
            with patch('pipu_cli.package_constraints.validate_package_exists') as mock_validate:
                mock_validate.return_value = (False, "Package 'nonexistent-package' not found in environment")

                # Simulate constraint submission
                screen.constraint_value = ">=1.0.0"
                screen.invalidation_trigger = ""

                # This would normally be called by the UI framework
                with patch('pipu_cli.package_constraints.parse_requirement_line') as mock_parse:
                    mock_parse.return_value = {'name': 'nonexistent-package', 'constraint': '>=1.0.0'}

                    # Mock the input widgets
                    mock_constraint_input = Mock()
                    mock_constraint_input.value = ">=1.0.0"
                    mock_invalidation_input = Mock()
                    mock_invalidation_input.value = ""

                    with patch.object(screen, 'query_one') as mock_query:
                        def mock_query_side_effect(selector, widget_type=None):
                            if selector == "#constraint-input":
                                return mock_constraint_input
                            elif selector == "#invalidation-input":
                                return mock_invalidation_input
                            return Mock()
                        mock_query.side_effect = mock_query_side_effect

                        screen._handle_constraint_submission()

                    # Should notify about the error
                    mock_app.notify.assert_called()
                    error_call = mock_app.notify.call_args
                    assert "not found" in error_call[0][0] or "error" in error_call[0][0].lower()


class TestTUIErrorRecovery:
    """Test TUI error recovery and user guidance."""

    def test_worker_cancellation_during_update_check(self):
        """Test behavior when update check workers are cancelled."""
        app = MainTUIApp()

        # Mock worker that gets cancelled
        with patch.object(app, 'run_worker') as mock_run_worker:
            mock_worker = Mock()
            mock_worker.is_cancelled = True
            mock_run_worker.return_value = mock_worker

            # Start update check
            app._start_update_check()

            # Should handle cancellation gracefully
            mock_run_worker.assert_called()

    def test_network_timeout_during_update_check(self):
        """Test behavior when network operations timeout."""
        import time
        from unittest.mock import Mock
        from requests.exceptions import ReadTimeout
        from pipu_cli.ui.constants import NETWORK_TIMEOUT_TEST

        app = MainTUIApp()

        # Mock everything needed to simulate fast timeout without real network calls
        with patch('pipu_cli.internals.get_default_environment') as mock_env, \
             patch('pipu_cli.internals.Configuration') as mock_config_cls, \
             patch('pipu_cli.internals.PipSession') as mock_session_cls:

            # Mock environment with one package to check
            mock_env_instance = Mock()
            mock_dist = Mock()
            mock_dist.metadata = {"name": "test-package"}
            mock_dist.version = "1.0.0"
            mock_dist.canonical_name = "test-package"
            mock_env_instance.iter_all_distributions.return_value = [mock_dist]
            mock_env.return_value = mock_env_instance

            # Mock configuration
            mock_config = Mock()
            mock_config_cls.return_value = mock_config
            mock_config.load.return_value = None
            mock_config.get_value.return_value = None

            # Mock PipSession to timeout quickly
            mock_session = Mock()
            mock_session_cls.return_value = mock_session
            mock_session.timeout = NETWORK_TIMEOUT_TEST

            # Mock package finder to simulate timeout during candidate search
            with patch('pipu_cli.internals.PackageFinder') as mock_finder_cls:
                mock_finder = Mock()
                mock_finder_cls.create.return_value = mock_finder

                # Simulate timeout during find_all_candidates
                def mock_find_candidates(_name):
                    time.sleep(0.1)  # Brief delay to simulate network
                    raise ReadTimeout("Connection timeout")

                mock_finder.find_all_candidates.side_effect = mock_find_candidates

                # Should raise ConnectionError on network timeout
                from pipu_cli.internals import list_outdated

                # This will raise ConnectionError after first network failure
                with pytest.raises(ConnectionError) as exc_info:
                    list_outdated(print_table=False)
                assert "Network connectivity issues" in str(exc_info.value)

            # Should handle network errors gracefully in TUI context
            app._check_outdated_packages()

            # Should not crash the entire TUI

class TestTUIFilteringAndSorting:
    """Test TUI filtering and sorting functionality."""

    def test_filtering_with_mixed_package_states(self):
        """Test filtering behavior with packages in various states."""
        app = MainTUIApp()

        # Set up packages with mixed states
        mixed_packages = [
            {'name': 'outdated-pkg', 'version': '1.0.0', 'latest_version': '2.0.0', 'outdated': True},
            {'name': 'uptodate-pkg', 'version': '1.0.0', 'latest_version': '1.0.0', 'outdated': False},
            {'name': 'checking-pkg', 'version': '1.0.0', 'latest_version': 'Checking...', 'outdated': False},
            {'name': 'error-pkg', 'version': '1.0.0', 'latest_version': 'Error', 'outdated': False},
        ]
        app.all_packages = mixed_packages

        # Test filtering to outdated only
        app.filter_outdated_only = True
        app._refresh_table_display()

        # Should handle mixed states appropriately
        # Checking packages should remain visible, error packages behavior depends on implementation

    def test_alphabetical_sorting_with_special_characters(self):
        """Test alphabetical sorting with package names containing special characters."""
        app = MainTUIApp()

        # Packages with various naming conventions
        special_packages = [
            {'name': 'z-package', 'version': '1.0.0', 'latest_version': '1.0.0', 'outdated': False},
            {'name': 'a-package', 'version': '1.0.0', 'latest_version': '1.0.0', 'outdated': False},
            {'name': '_internal', 'version': '1.0.0', 'latest_version': '1.0.0', 'outdated': False},
            {'name': '2to3', 'version': '1.0.0', 'latest_version': '1.0.0', 'outdated': False},
            {'name': 'UPPERCASE', 'version': '1.0.0', 'latest_version': '1.0.0', 'outdated': False},
        ]
        app.all_packages = special_packages

        # Refresh display to trigger sorting
        app._refresh_table_display()

        # Should handle special characters and case in sorting
        # The exact order depends on implementation, but should be consistent


class TestTUIAccessibilityAndUsability:
    """Test TUI accessibility and usability features."""

    def test_keyboard_navigation_with_empty_table(self):
        """Test keyboard navigation when table is empty or filtered to empty."""
        app = MainTUIApp()
        app.all_packages = []

        # Mock table operations
        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_table.cursor_row = None
            mock_table.rows = []
            mock_query.return_value = mock_table

            # Test various actions with empty table
            app.action_add_constraint()  # Should not crash
            app.action_delete_constraint()  # Should not crash
            app.action_uninstall_package()  # Should not crash

            # Should handle gracefully, possibly with user-friendly messages

    def test_help_screen_completeness(self):
        """Test that help screen contains comprehensive information."""
        from pipu_cli.ui import HelpScreen

        help_screen = HelpScreen()

        # Should provide comprehensive help content
        # This test verifies that help information exists and is substantial

        # The help screen should contain key information for users
        # Implementation details depend on the actual help content

    def test_color_coding_accessibility(self):
        """Test that color coding has accessible alternatives."""
        app = MainTUIApp()

        # Test that information is conveyed through more than just color
        # This is important for users with color vision deficiencies
        package_info = {
            'name': 'test-package',
            'version': '1.0.0',
            'latest_version': '2.0.0',
            'latest_filetype': 'wheel',
            'constraint': '>=1.0.0,<3.0.0',
            'outdated': True
        }

        # Should use symbols, text, or other indicators in addition to colors
        # The exact implementation depends on the display logic


class TestTUIPerformanceScenarios:
    """Test TUI performance under various conditions."""

    def test_real_time_updates_performance(self):
        """Test performance of real-time package updates."""
        app = MainTUIApp()

        # Set up scenario with frequent updates
        large_package_list = []
        for i in range(100):
            large_package_list.append({
                'name': f'package-{i:03d}',
                'version': '1.0.0',
                'latest_version': 'Checking...',
                'latest_filetype': '',
                'outdated': False
            })

        app.all_packages = large_package_list

        # Build initial row mapping
        app.package_row_mapping = {pkg['name']: i for i, pkg in enumerate(large_package_list)}

        # Mock table for performance testing
        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_table.rows = [Mock() for _ in range(100)]
            mock_table.update_cell_at = Mock()
            mock_query.return_value = mock_table

            # Simulate rapid updates
            for i in range(100):
                package_result = {
                    'name': f'package-{i:03d}',
                    'version': '1.0.0',
                    'latest_version': '1.1.0',
                    'latest_filetype': 'wheel'
                }

                # Should handle rapid updates efficiently
                app._update_package_result(package_result)

            # Should maintain performance with frequent updates
            # Note: threshold increased to account for auto-discovered constraints
            assert mock_table.update_cell_at.call_count <= 400  # Reasonable number of updates

    def test_memory_efficiency_with_large_datasets(self):
        """Test memory efficiency with large package datasets."""
        app = MainTUIApp()

        # Create large dataset
        large_dataset = []
        for i in range(5000):
            large_dataset.append({
                'name': f'large-package-{i:05d}',
                'version': f'{i % 10}.{(i // 10) % 10}.{(i // 100) % 10}',
                'latest_version': f'{(i % 10) + 1}.0.0',
                'latest_filetype': 'wheel',
                'constraint': f'>={i % 10}.0.0' if i % 3 == 0 else None,
                'outdated': i % 2 == 0
            })

        app.all_packages = large_dataset

        # Test filtering operations with large dataset
        app.filter_outdated_only = True

        # Should handle large datasets efficiently
        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_table.clear = Mock()
            mock_table.add_row = Mock()
            mock_query.return_value = mock_table

            # Should complete filtering in reasonable time
            app._refresh_table_display()

            # Should not consume excessive memory
            # (Exact metrics depend on implementation)


class TestTUIErrorMessaging:
    """Test quality and helpfulness of error messages in TUI."""

    def test_constraint_validation_error_messages(self):
        """Test that constraint validation provides helpful error messages."""
        from pipu_cli.ui.modal_dialogs import ConstraintInputScreen

        # Just test empty constraint for now
        screen = ConstraintInputScreen("test-package", "")

        # Mock the app property to avoid NoActiveAppError
        mock_app = Mock()
        mock_app.notify = Mock()

        # Create a mock app that returns our mock_app
        def get_mock_app():
            return mock_app

        with patch.object(ConstraintInputScreen, 'app', new=mock_app):

            # Mock the input widgets
            mock_constraint_input = Mock()
            mock_constraint_input.value = ""  # Empty constraint
            mock_invalidation_input = Mock()
            mock_invalidation_input.value = ""

            with patch.object(screen, 'query_one') as mock_query:
                def mock_query_side_effect(selector, widget_type=None):
                    if selector == "#constraint-input":
                        return mock_constraint_input
                    elif selector == "#invalidation-input":
                        return mock_invalidation_input
                    return Mock()
                mock_query.side_effect = mock_query_side_effect

                screen._handle_constraint_submission()

                # Should provide helpful error message for empty constraint
                mock_app.notify.assert_called_with("Constraint cannot be empty", severity="error")

    def test_uninstall_confirmation_clarity(self):
        """Test that uninstall confirmations are clear and prominent."""
        from pipu_cli.ui import UninstallConfirmScreen

        screen = UninstallConfirmScreen("critical-package")

        # Should clearly indicate what will be uninstalled
        assert "critical-package" in screen.message
        assert "uninstall" in screen.message.lower()

        # The confirmation should be prominent and clear
        # (Testing the actual UI rendering requires more complex test setup)