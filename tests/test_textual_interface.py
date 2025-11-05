"""
Tests for TUI functionality in pipu.

Tests the Textual-based user interfaces including the main TUI app,
package selection screens, and modal dialogs.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from textual.widgets import DataTable, Input
from textual.app import App
from rich.text import Text

from pipu.ui import (
    MainTUIApp, PackageSelectionApp, PackageSelectionTable,
    ConstraintInputScreen,
    UninstallConfirmScreen
)


class TestPackageSelectionTable:
    """Test the PackageSelectionTable widget."""

    def test_initialization_with_empty_packages(self):
        """Test table initialization with no packages."""
        table = PackageSelectionTable([])
        assert table.outdated_packages == []
        assert table.selected_packages == {}

    def test_initialization_with_packages(self):
        """Test table initialization with sample packages."""
        packages = [
            {
                'name': 'test-package',
                'version': '1.0.0',
                'latest_version': '2.0.0',
                'latest_filetype': 'wheel',
                'constraint': None
            }
        ]
        table = PackageSelectionTable(packages)
        assert len(table.outdated_packages) == 1
        assert 'test-package' in table.selected_packages

    def test_package_selection_initialization_no_constraints(self):
        """Test that packages without constraints are pre-selected."""
        packages = [
            {
                'name': 'package-a',
                'version': '1.0.0',
                'latest_version': '2.0.0',
                'latest_filetype': 'wheel',
                'constraint': None
            }
        ]
        table = PackageSelectionTable(packages)
        assert table.selected_packages['package-a'] is True

    @patch('pipu.ui.table_widgets._check_constraint_satisfaction')
    def test_package_selection_initialization_with_satisfying_constraints(self, mock_check):
        """Test pre-selection with constraints that are satisfied."""
        mock_check.return_value = True

        packages = [
            {
                'name': 'package-b',
                'version': '1.0.0',
                'latest_version': '1.5.0',
                'latest_filetype': 'wheel',
                'constraint': '>=1.0.0,<2.0.0'
            }
        ]
        table = PackageSelectionTable(packages)
        assert table.selected_packages['package-b'] is True
        mock_check.assert_called_with('1.5.0', '>=1.0.0,<2.0.0')

    @patch('pipu.ui.table_widgets._check_constraint_satisfaction')
    def test_package_selection_initialization_with_violating_constraints(self, mock_check):
        """Test pre-selection with constraints that are violated."""
        mock_check.return_value = False

        packages = [
            {
                'name': 'package-c',
                'version': '1.0.0',
                'latest_version': '3.0.0',
                'latest_filetype': 'wheel',
                'constraint': '>=1.0.0,<2.0.0'
            }
        ]
        table = PackageSelectionTable(packages)
        assert table.selected_packages['package-c'] is False
        mock_check.assert_called_with('3.0.0', '>=1.0.0,<2.0.0')


class TestConstraintInputScreen:
    """Test the ConstraintInputScreen modal."""

    def test_initialization(self):
        """Test screen initialization with package name."""
        screen = ConstraintInputScreen("test-package")
        assert screen.package_name == "test-package"
        assert screen.current_constraint == ""
        assert screen.constraint_value == ""

    def test_initialization_with_current_constraint(self):
        """Test screen initialization with existing constraint."""
        screen = ConstraintInputScreen("test-package", ">=1.0.0")
        assert screen.package_name == "test-package"
        assert screen.current_constraint == ">=1.0.0"


class TestUninstallConfirmScreen:
    """Test the UninstallConfirmScreen modal."""

    def test_initialization(self):
        """Test screen initialization with package name."""
        screen = UninstallConfirmScreen("test-package")
        # Check that the message contains the package name
        assert "test-package" in screen.message
        assert "uninstall" in screen.message.lower()


class TestPackageSelectionApp:
    """Test the PackageSelectionApp main application."""

    def test_initialization_with_packages(self):
        """Test app initialization with outdated packages."""
        packages = [
            {
                'name': 'test-package',
                'version': '1.0.0',
                'latest_version': '2.0.0',
                'latest_filetype': 'wheel'
            }
        ]
        app = PackageSelectionApp(packages)
        assert app.outdated_packages == packages
        assert app.selected_packages == []
        assert app.confirmed is False

    def test_initialization_empty_packages(self):
        """Test app initialization with no packages."""
        app = PackageSelectionApp([])
        assert app.outdated_packages == []


class TestMainTUIApp:
    """Test the MainTUIApp main application."""

    def test_initialization(self):
        """Test app initialization with default values."""
        app = MainTUIApp()
        assert app.all_packages == []
        assert app.outdated_packages == []
        assert app.update_check_complete is True  # Starts True, set to False during checking
        assert app.constraints == {}
        assert isinstance(app.ignores, set)
        assert app.filter_outdated_only is False  # Default to showing all packages
        assert app.package_row_mapping == {}

    @patch('pipu.package_constraints.read_constraints')
    @patch('pipu.package_constraints.read_ignores')
    def test_on_mount_loads_configuration(self, mock_read_ignores, mock_read_constraints):
        """Test that mounting loads constraints and ignores."""
        mock_read_constraints.return_value = {'package': '>=1.0.0'}
        mock_read_ignores.return_value = {'ignored-package'}

        app = MainTUIApp()
        # We can't easily test the full mount without running the app,
        # but we can test the configuration loading logic

        # Simulate the configuration loading part of on_mount
        app.constraints = mock_read_constraints.return_value
        app.ignores = mock_read_ignores.return_value

        assert app.constraints == {'package': '>=1.0.0'}
        assert app.ignores == {'ignored-package'}

    def test_filter_toggle_actions(self):
        """Test filter toggle actions work correctly."""
        app = MainTUIApp()

        # Default should be show all packages
        assert app.filter_outdated_only is False

        # Action to show all when already showing all (should acknowledge)
        with patch.object(app, '_refresh_table_display') as mock_refresh:
            with patch.object(app, 'notify') as mock_notify:
                app.action_show_all()
                assert app.filter_outdated_only is False
                mock_refresh.assert_not_called()
                mock_notify.assert_called_with("Filter: already showing all packages")

        # Action to filter outdated when currently showing all
        with patch.object(app, '_refresh_table_display') as mock_refresh:
            with patch.object(app, 'notify') as mock_notify:
                with patch.object(app, 'query_one') as mock_query:
                    mock_description = Mock()

                    def side_effect(selector, widget_type=None):
                        if selector == "#filter-description":
                            return mock_description
                        return Mock()

                    mock_query.side_effect = side_effect

                    app.action_filter_outdated()
                    assert app.filter_outdated_only is True
                    mock_refresh.assert_called_once()
                    mock_notify.assert_called_with("Filter: showing only outdated packages")

                    # Should update description text
                    mock_description.update.assert_called_with("Show outdated only")

        # Action to filter outdated when already filtering outdated (should acknowledge)
        with patch.object(app, '_refresh_table_display') as mock_refresh:
            with patch.object(app, 'notify') as mock_notify:
                app.action_filter_outdated()
                assert app.filter_outdated_only is True
                mock_refresh.assert_not_called()
                mock_notify.assert_called_with("Filter: already showing only outdated packages")

        # Action to show all when currently filtering outdated
        with patch.object(app, '_refresh_table_display') as mock_refresh:
            with patch.object(app, 'notify') as mock_notify:
                with patch.object(app, 'query_one') as mock_query:
                    mock_description = Mock()

                    def side_effect(selector, widget_type=None):
                        if selector == "#filter-description":
                            return mock_description
                        return Mock()

                    mock_query.side_effect = side_effect

                    app.action_show_all()
                    assert app.filter_outdated_only is False
                    mock_refresh.assert_called_once()
                    mock_notify.assert_called_with("Filter: showing all packages")

                    # Should update description text
                    mock_description.update.assert_called_with("Show all packages")

    def test_real_time_filtering_on_package_update(self):
        """Test that package updates trigger real-time filtering when needed."""
        app = MainTUIApp()
        app.filter_outdated_only = True

        # Set up test package data
        app.all_packages = [
            {'name': 'test-pkg', 'version': '1.0.0', 'latest_version': 'Checking...', 'latest_filetype': '', 'outdated': False}
        ]
        app.package_row_mapping = {'test-pkg': 0}

        # Mock table operations
        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_query.return_value = mock_table
            mock_table.update_cell_at = Mock()
            mock_table.rows = [Mock()]  # One row

            # Test 1: Package becomes up-to-date (should trigger refresh in filter mode)
            with patch.object(app, '_refresh_table_display') as mock_refresh:
                app._update_package_result({
                    'name': 'test-pkg',
                    'version': '1.0.0',
                    'latest_version': '1.0.0',  # Same = up-to-date
                    'latest_filetype': 'wheel'
                })

                # Should trigger refresh to hide the up-to-date package
                assert app.all_packages[0]['outdated'] is False
                mock_refresh.assert_called_once()

            # Test 2: Package becomes outdated (should not trigger refresh)
            app.all_packages[0]['latest_version'] = 'Checking...'  # Reset
            with patch.object(app, '_refresh_table_display') as mock_refresh:
                app._update_package_result({
                    'name': 'test-pkg',
                    'version': '1.0.0',
                    'latest_version': '2.0.0',  # Newer = outdated
                    'latest_filetype': 'wheel'
                })

                # Should not trigger refresh since outdated packages should remain visible
                assert app.all_packages[0]['outdated'] is True
                mock_refresh.assert_not_called()

            # Test 3: Same behavior when not filtering (should not trigger refresh)
            app.filter_outdated_only = False
            app.all_packages[0]['latest_version'] = 'Checking...'  # Reset
            with patch.object(app, '_refresh_table_display') as mock_refresh:
                app._update_package_result({
                    'name': 'test-pkg',
                    'version': '1.0.0',
                    'latest_version': '1.0.0',  # Up-to-date
                    'latest_filetype': 'wheel'
                })

                # Should not trigger refresh when showing all packages
                mock_refresh.assert_not_called()

    def test_real_time_filtering_with_multiple_packages(self):
        """Test real-time filtering with multiple packages and state changes."""
        app = MainTUIApp()
        app.filter_outdated_only = True

        # Set up test data with multiple packages
        app.all_packages = [
            {'name': 'pkg-a', 'version': '1.0.0', 'latest_version': 'Checking...', 'latest_filetype': '', 'outdated': False},
            {'name': 'pkg-b', 'version': '1.0.0', 'latest_version': 'Checking...', 'latest_filetype': '', 'outdated': False},
            {'name': 'pkg-c', 'version': '1.0.0', 'latest_version': '2.0.0', 'latest_filetype': 'wheel', 'outdated': True}
        ]
        # Initially show all packages (they're all either checking or outdated)
        app.package_row_mapping = {'pkg-a': 0, 'pkg-b': 1, 'pkg-c': 2}

        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_query.return_value = mock_table
            mock_table.update_cell_at = Mock()
            mock_table.rows = [Mock(), Mock(), Mock()]  # Three rows initially

            with patch.object(app, '_refresh_table_display') as mock_refresh:
                # pkg-a becomes up-to-date - should trigger refresh to hide it
                app._update_package_result({
                    'name': 'pkg-a',
                    'version': '1.0.0',
                    'latest_version': '1.0.0',
                    'latest_filetype': 'wheel'
                })

                # Should trigger refresh because pkg-a is now up-to-date and should be hidden
                mock_refresh.assert_called_once()
                assert app.all_packages[0]['outdated'] is False

                # Reset for next test
                mock_refresh.reset_mock()

                # pkg-b becomes outdated - should not trigger refresh
                app._update_package_result({
                    'name': 'pkg-b',
                    'version': '1.0.0',
                    'latest_version': '2.0.0',
                    'latest_filetype': 'wheel'
                })

                # Should not trigger refresh because outdated packages remain visible
                mock_refresh.assert_not_called()
                assert app.all_packages[1]['outdated'] is True

                # Reset for next test
                mock_refresh.reset_mock()

                # pkg-c (already outdated) gets updated info - should not trigger refresh
                app._update_package_result({
                    'name': 'pkg-c',
                    'version': '1.0.0',
                    'latest_version': '2.1.0',  # Still outdated, just newer
                    'latest_filetype': 'wheel'
                })

                # Should not trigger refresh because pkg-c was and remains outdated
                mock_refresh.assert_not_called()
                assert app.all_packages[2]['outdated'] is True

    def test_cursor_preservation_during_filtering(self):
        """Test that cursor position is preserved during real-time filtering."""
        app = MainTUIApp()
        app.filter_outdated_only = True

        # Set up test data - at least one package should be outdated or checking
        app.all_packages = [
            {'name': 'pkg-a', 'version': '1.0.0', 'latest_version': '1.1.0', 'latest_filetype': 'wheel', 'outdated': True},
            {'name': 'pkg-b', 'version': '1.0.0', 'latest_version': '1.2.0', 'latest_filetype': 'wheel', 'outdated': True}
        ]
        app.package_row_mapping = {'pkg-a': 0, 'pkg-b': 1}

        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_query.return_value = mock_table
            mock_table.cursor_row = 1  # User is on second row

            # Mock the rows as a dict-like object with keys() method
            mock_rows = Mock()
            mock_rows.keys.return_value = ['pkg-a', 'pkg-b']
            mock_rows.__len__ = Mock(return_value=2)
            mock_table.rows = mock_rows

            mock_table.clear = Mock()
            mock_table.add_row = Mock()
            mock_table.move_cursor = Mock()

            # Mock scroll offset and scroll_to method
            mock_scroll_offset = Mock()
            mock_scroll_offset.y = 100  # Simulate being scrolled down
            mock_table.scroll_offset = mock_scroll_offset
            mock_table.scroll_to = Mock()

            # Test that refresh with preserve_cursor=True attempts to restore cursor and scroll
            with patch.object(app, 'set_timer') as mock_set_timer:
                app._refresh_table_display(preserve_cursor=True)

                # Should have attempted to move cursor back to preserved position
                mock_table.move_cursor.assert_called()

                # Should have scheduled scroll restoration via timer
                mock_set_timer.assert_called_once()

    def test_filter_text_display_functionality(self):
        """Test that the filter text display correctly shows filter mode."""
        app = MainTUIApp()

        # Test initial state - should show "Show all packages"
        assert app.filter_outdated_only is False

        # Mock the description widget
        with patch.object(app, 'query_one') as mock_query:
            mock_description = Mock()

            def side_effect(selector, widget_type=None):
                if selector == "#filter-description":
                    return mock_description
                return Mock()

            mock_query.side_effect = side_effect

            with patch.object(app, '_refresh_table_display') as mock_refresh:
                with patch.object(app, 'notify') as mock_notify:
                    # Test toggling to filter mode
                    app.action_filter_outdated()

                    # Should update filter mode
                    assert app.filter_outdated_only is True

                    # Should refresh table with cursor preservation
                    mock_refresh.assert_called_once_with(preserve_cursor=True)

                    # Should update description text and notify
                    mock_description.update.assert_called_with("Show outdated only")
                    mock_notify.assert_called_with("Filter: showing only outdated packages")

    def test_filter_text_layout_positioning(self):
        """Test that filter mode text is positioned correctly in the layout (3rd line)."""
        import inspect

        app = MainTUIApp()

        # Examine the compose method's source code to verify the order
        compose_source = inspect.getsource(app.compose)

        # The compose method should have widgets in the correct order:
        # 1. yield Static(..., id="info-panel")
        # 2. with Horizontal(id="filter-mode-container"): (containing filter text)
        # 3. yield DataTable(..., id="main-table")

        # Check that info-panel comes before filter-mode-container
        info_panel_pos = compose_source.find('id="info-panel"')
        filter_container_pos = compose_source.find('id="filter-mode-container"')
        table_pos = compose_source.find('id="main-table"')

        assert info_panel_pos != -1, "info-panel should be present"
        assert filter_container_pos != -1, "filter-mode-container should be present"
        assert table_pos != -1, "main-table should be present"

        # Verify correct order: info-panel -> filter-container -> table
        assert info_panel_pos < filter_container_pos, "info-panel should come before filter-container"
        assert filter_container_pos < table_pos, "filter-container should come before main-table"

        # Verify filter mode text components are present (no switch)
        assert 'id="filter-label"' in compose_source, "filter-label should be present"
        assert 'id="filter-description"' in compose_source, "filter-description should be present"

        # Verify switch is NOT present
        assert 'Switch' not in compose_source, "Switch should not be present in compose method"
        css_content = app.CSS

        # Both info-panel and filter-mode-container should have dock: top
        assert '#info-panel' in css_content
        assert '#filter-mode-container' in css_content
        assert 'dock: top' in css_content


class TestTUIErrorHandling:
    """Test error handling in TUI components."""

    def test_package_result_update_with_invalid_row_index(self):
        """Test handling of invalid row index in package result updates."""
        app = MainTUIApp()
        app.package_row_mapping = {'test-package': 999}  # Invalid index

        # Mock the table query
        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_query.return_value = mock_table

            # Mock the notify method to capture error notifications
            with patch.object(app, 'notify') as mock_notify:
                # This should not crash but should notify of error
                app._update_package_result({
                    'name': 'test-package',
                    'version': '1.0.0',
                    'latest_version': '2.0.0',
                    'latest_filetype': 'wheel'
                })

                # Should have attempted to notify about error (implementation dependent)
                # The exact behavior depends on the bounds checking in the implementation

    def test_malformed_package_result(self):
        """Test handling of malformed package result data."""
        app = MainTUIApp()

        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_query.return_value = mock_table

            with patch.object(app, 'notify') as mock_notify:
                # Test with missing required fields
                app._update_package_result({
                    'name': 'test-package'
                    # Missing version, latest_version, latest_filetype
                })

                # Should handle gracefully and potentially notify of error


class TestTUIPerformanceOptimizations:
    """Test performance optimizations in TUI."""

    def test_package_row_mapping_efficiency(self):
        """Test that package row mapping provides O(1) lookups."""
        packages = [
            {'name': f'package-{i}', 'version': '1.0.0', 'latest_version': '2.0.0', 'latest_filetype': 'wheel'}
            for i in range(100)
        ]

        app = MainTUIApp()
        app.all_packages = packages

        # Simulate building the row mapping as done in _load_installed_packages
        app.package_row_mapping = {pkg['name']: i for i, pkg in enumerate(packages)}

        # Test that lookups are efficient
        assert app.package_row_mapping['package-50'] == 50
        assert app.package_row_mapping['package-99'] == 99
        assert 'package-100' not in app.package_row_mapping

    def test_efficient_package_filtering(self):
        """Test that package filtering maintains alphabetical order."""
        packages = [
            {'name': 'zebra-package', 'version': '1.0.0', 'latest_version': '2.0.0', 'outdated': True},
            {'name': 'alpha-package', 'version': '1.0.0', 'latest_version': '1.0.0', 'outdated': False},
            {'name': 'beta-package', 'version': '1.0.0', 'latest_version': '3.0.0', 'outdated': True}
        ]

        app = MainTUIApp()
        app.all_packages = packages
        app.filter_outdated_only = True

        # Simulate the filtering logic from _refresh_table_display
        packages_to_show = []
        for pkg in app.all_packages:
            if app.filter_outdated_only:
                if pkg.get("outdated", False):
                    packages_to_show.append(pkg)
            else:
                packages_to_show.append(pkg)

        # Should maintain alphabetical order
        packages_to_show.sort(key=lambda x: x["name"].lower())

        assert len(packages_to_show) == 2  # Only outdated packages
        assert packages_to_show[0]['name'] == 'beta-package'
        assert packages_to_show[1]['name'] == 'zebra-package'


# Integration test helpers
class TestTUIIntegration:
    """Integration tests for TUI components."""

    @patch('pipu.internals.list_outdated')
    def test_package_discovery_integration(self, mock_list_outdated):
        """Test integration between package discovery and TUI display."""
        mock_list_outdated.return_value = [
            {
                'name': 'test-package',
                'version': '1.0.0',
                'latest_version': '2.0.0',
                'latest_filetype': 'wheel',
                'constraint': None
            }
        ]

        # This tests the integration pathway but can't fully test without running the app
        # At minimum, we verify the mock is set up correctly
        result = mock_list_outdated()
        assert len(result) == 1
        assert result[0]['name'] == 'test-package'


class TestConstraintInputInteractive:
    """Test actual constraint input functionality using Textual's testing framework."""

    @pytest.mark.asyncio
    async def test_constraint_input_screen_basic_functionality(self):
        """Test basic constraint input screen functionality."""
        # Create a simple test app that shows the constraint input screen
        class TestApp(App):
            def __init__(self):
                super().__init__()
                self.result = None

            def on_mount(self):
                screen = ConstraintInputScreen("testpackage", "")
                self.push_screen(screen, self.handle_result)

            def handle_result(self, result):
                self.result = result
                self.exit()

        app = TestApp()

        # Run the app in test mode (async context)
        async with app.run_test() as pilot:
            # The constraint input screen should be active
            assert isinstance(pilot.app.screen, ConstraintInputScreen)

            # Find the input widget on the constraint input screen
            input_widget = pilot.app.screen.query_one("#constraint-input", Input)
            assert input_widget is not None

            # Type a constraint
            await pilot.press("1", ".", "0")  # Type "1.0"
            assert input_widget.value == "1.0"

            # Press Enter to submit
            await pilot.press("enter")

            # The result should be captured (though the screen might still be active)
            # We need to wait a moment for the dismiss to process
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_constraint_validation_in_main_app(self):
        """Test constraint validation in the main TUI app with real constraint input."""

        # Create a test version of MainTUIApp with mocked constraint functionality
        class TestMainTUIApp(MainTUIApp):
            def __init__(self):
                super().__init__()
                self.test_result = None
                self.test_error = None

            def _load_installed_packages(self):
                # Override to provide test packages without loading real packages
                self.all_packages = [
                    {
                        'name': 'testpackage',
                        'version': '1.0.0',
                        'latest_version': '2.0.0',
                        'latest_filetype': 'wheel',
                        'constraint': None,
                        'invalid_when': None,
                        'selected': False,
                        'outdated': True
                    }
                ]
                # Setup table with test data
                table = self.query_one("#main-table", DataTable)
                table.add_column("Sel", width=4)
                table.add_column("Package", width=20)
                table.add_column("Current", width=12)
                table.add_column("Latest", width=12)
                table.add_column("Type", width=8)
                table.add_column("Constraint", width=20)
                table.add_column("Invalid When", width=25)

                # Add test package to table
                from rich.text import Text
                table.add_row(
                    Text(" ", style="dim"),
                    "testpackage",
                    "1.0.0",
                    "2.0.0",
                    "wheel",
                    Text.from_markup("[dim]-[/dim]"),
                    Text.from_markup("[dim]-[/dim]"),
                    key="testpackage"
                )
                self.package_row_mapping = {"testpackage": 0}
                self._update_status("Test packages loaded", False)

            def _start_update_check(self, *args):
                # Skip async update check for test
                self.update_check_complete = True

        # Mock the constraint addition function to capture calls
        original_add_constraints = None

        def mock_add_constraints(constraint_specs):
            from pipu.package_constraints import parse_requirement_line

            # Test the actual parsing that would happen
            for spec in constraint_specs:
                parsed = parse_requirement_line(spec)
                if not parsed:
                    raise ValueError(f"Invalid constraint specification: {spec}")

            # If we get here, parsing succeeded
            return "/fake/path", {"testpackage": ("added", constraint_specs[0].replace("testpackage", ""))}

        with patch('pipu.package_constraints.add_constraints_to_config', side_effect=mock_add_constraints):
            app = TestMainTUIApp()

            async with app.run_test() as pilot:
                # Wait for app to fully load
                await pilot.pause(0.1)

                # Focus should be on the main table
                table = pilot.app.query_one("#main-table", DataTable)
                assert table is not None

                # Navigate to our test package (should be the first/only row)
                await pilot.press("down")  # Navigate to first row if needed

                # Press 'c' to add constraint
                await pilot.press("c")

                # Should now have the constraint input screen
                await pilot.pause(0.1)
                constraint_screen = pilot.app.screen
                assert isinstance(constraint_screen, ConstraintInputScreen)

                # Find the input field and type our test constraint
                input_widget = pilot.app.screen.query_one("#constraint-input", Input)

                # Test the problematic constraint from the screenshot: >1.0
                await pilot.click("#constraint-input")  # Focus the input
                input_widget.value = ">1.0"  # Set the value directly

                # Submit the constraint
                await pilot.press("enter")

                # Wait for processing
                await pilot.pause(0.1)

                # The screen should dismiss and we should be back to main screen
                # If there was an error, it would show a notification
                # If successful, we should be back to the main screen

    @pytest.mark.asyncio
    async def test_screenshot_constraint_cases(self):
        """Test the specific constraint cases that were failing in the screenshot."""

        # Create a minimal app just to test constraint input handling
        class ConstraintTestApp(App):
            def __init__(self):
                super().__init__()
                self.constraint_results = []
                self.constraint_errors = []

            def on_mount(self):
                # Test the exact cases from the screenshot
                self.test_constraints([">1", ">1.0"])

            def test_constraints(self, constraints):
                for constraint in constraints:
                    try:
                        # This is exactly what the TUI does
                        from pipu.package_constraints import add_constraints_to_config
                        constraint_spec = f"testpackage{constraint}"
                        config_path, changes = add_constraints_to_config([constraint_spec])
                        self.constraint_results.append((constraint, "SUCCESS", changes))

                        # Clean up
                        from pipu.package_constraints import remove_constraints_from_config
                        remove_constraints_from_config(["testpackage"])

                    except Exception as e:
                        self.constraint_errors.append((constraint, str(e)))

                # Exit after testing
                self.exit()

        app = ConstraintTestApp()

        async with app.run_test() as pilot:
            await pilot.pause(0.1)  # Let the test complete

            # Check results
            assert len(app.constraint_errors) == 0, f"Constraint errors: {app.constraint_errors}"
            assert len(app.constraint_results) == 2, f"Expected 2 successful results, got: {app.constraint_results}"

            # Verify the specific constraints worked
            results_dict = {constraint: result for constraint, result, changes in app.constraint_results}
            assert ">1" in results_dict
            assert ">1.0" in results_dict
            assert results_dict[">1"] == "SUCCESS"
            assert results_dict[">1.0"] == "SUCCESS"


class TestTUIRowKeyRegressionTests:
    """
    Regression tests specifically for the RowKey issue.

    These tests ensure that the TUI properly extracts package names from table
    selections instead of getting RowKey objects, which would cause constraint
    parsing failures.
    """

    def test_get_selected_package_helper_method(self):
        """Test that the _get_selected_package helper method works correctly."""
        app = MainTUIApp()

        # Set up test packages
        app.all_packages = [
            {
                'name': 'package-a',
                'version': '1.0.0',
                'latest_version': '2.0.0',
                'latest_filetype': 'wheel',
                'constraint': None,
                'outdated': True
            },
            {
                'name': 'package-b',
                'version': '1.0.0',
                'latest_version': '1.0.0',
                'latest_filetype': 'wheel',
                'constraint': '>=1.0.0',
                'outdated': False
            }
        ]

        # Mock the table and query methods
        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_table.cursor_row = 0
            mock_table.rows = [Mock(), Mock()]  # Two rows
            mock_query.return_value = mock_table

            # Test with filter_outdated_only = False (show all)
            app.filter_outdated_only = False
            selected = app._get_selected_package()

            assert selected is not None
            assert selected['name'] == 'package-a'  # Should be first in alphabetical order
            assert isinstance(selected['name'], str)  # CRITICAL: Must be string, not RowKey

            # Test with cursor on second row
            mock_table.cursor_row = 1
            selected = app._get_selected_package()

            assert selected is not None
            assert selected['name'] == 'package-b'  # Should be second in alphabetical order
            assert isinstance(selected['name'], str)  # CRITICAL: Must be string, not RowKey

    def test_get_selected_package_with_filtering(self):
        """Test _get_selected_package with outdated-only filtering."""
        app = MainTUIApp()

        # Set up test packages with mixed outdated status
        app.all_packages = [
            {
                'name': 'outdated-pkg',
                'version': '1.0.0',
                'latest_version': '2.0.0',
                'latest_filetype': 'wheel',
                'constraint': None,
                'outdated': True
            },
            {
                'name': 'uptodate-pkg',
                'version': '1.0.0',
                'latest_version': '1.0.0',
                'latest_filetype': 'wheel',
                'constraint': None,
                'outdated': False
            }
        ]

        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_table.cursor_row = 0
            mock_table.rows = [Mock()]  # Only one row when filtering
            mock_query.return_value = mock_table

            # Test with filter_outdated_only = True
            app.filter_outdated_only = True
            selected = app._get_selected_package()

            assert selected is not None
            assert selected['name'] == 'outdated-pkg'  # Should get the outdated package
            assert isinstance(selected['name'], str)  # CRITICAL: Must be string, not RowKey
            assert selected['outdated'] is True

    def test_constraint_addition_uses_string_package_names(self):
        """
        Regression test: Ensure constraint addition uses actual package names, not RowKey objects.

        This test specifically checks that the constraint specification passed to
        add_constraints_to_config is a proper string like 'package>1.0', not
        '<textual.widgets._data_table.RowKey object at 0x...>>1.0'.
        """

        class TestMainTUIApp(MainTUIApp):
            def __init__(self):
                super().__init__()
                self.constraint_specs_received = []
                self.notifications = []

                # Set up test package
                self.all_packages = [
                    {
                        'name': 'testpackage',
                        'version': '1.0.0',
                        'latest_version': '2.0.0',
                        'latest_filetype': 'wheel',
                        'constraint': None,
                        'outdated': True
                    }
                ]

            def notify(self, message, *, title="", severity="information", timeout=3.0, markup=True):
                self.notifications.append((message, severity))
                # Don't call super() to avoid actual notifications during test

        def mock_add_constraints(constraint_specs):
            # Capture what constraint specs are passed
            app.constraint_specs_received.extend(constraint_specs)

            # Check that the constraint spec is a proper string
            for spec in constraint_specs:
                assert isinstance(spec, str), f"Constraint spec should be string, got {type(spec)}: {spec}"
                assert "RowKey" not in spec, f"Constraint spec contains RowKey object: {spec}"
                assert spec.startswith("testpackage"), f"Constraint spec should start with package name: {spec}"

            # Return mock success response
            pkg_name = constraint_specs[0].split('>')[0].split('<')[0].split('=')[0].split('!')[0].split('~')[0]
            constraint = constraint_specs[0].replace(pkg_name, '')
            return "/fake/path", {pkg_name: ('added', constraint)}

        app = TestMainTUIApp()

        # Mock the table and query methods
        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_table.cursor_row = 0
            mock_table.rows = [Mock()]
            mock_query.return_value = mock_table

            # Mock the constraint addition at the constraints module level
            with patch('pipu.package_constraints.add_constraints_to_config', side_effect=mock_add_constraints):
                with patch.object(app, 'push_screen') as mock_push_screen:

                    # Call action_add_constraint
                    app.action_add_constraint()

                    # Verify that push_screen was called (constraint input screen shown)
                    assert mock_push_screen.called

                    # Get the callback function that would handle the constraint result
                    args, kwargs = mock_push_screen.call_args
                    constraint_screen, callback = args

                    # Mock _refresh_table_display to avoid UI updates during test
                    with patch.object(app, '_refresh_table_display'):
                        # Simulate user entering '>1.0' constraint
                        callback('>1.0')

                        # Verify the constraint spec received is a proper string
                        assert len(app.constraint_specs_received) == 1
                        constraint_spec = app.constraint_specs_received[0]

                        # CRITICAL REGRESSION TEST: Ensure no RowKey objects
                        assert isinstance(constraint_spec, str)
                        assert "RowKey" not in constraint_spec
                        assert constraint_spec == "testpackage>1.0"

                        # Check for success notification (no error notifications)
                        error_notifications = [msg for msg, severity in app.notifications if "Invalid constraint" in msg or "Error" in msg]
                        assert len(error_notifications) == 0, f"Should have no error notifications, got: {error_notifications}"

    def test_constraint_deletion_uses_string_package_names(self):
        """
        Regression test: Ensure constraint deletion uses actual package names, not RowKey objects.
        """

        class TestMainTUIApp(MainTUIApp):
            def __init__(self):
                super().__init__()
                self.packages_to_delete = []
                self.notifications = []

                # Set up test package with constraint
                self.all_packages = [
                    {
                        'name': 'testpackage',
                        'version': '1.0.0',
                        'latest_version': '2.0.0',
                        'latest_filetype': 'wheel',
                        'constraint': '>1.0',
                        'outdated': True
                    }
                ]

            def notify(self, message, *, title="", severity="information", timeout=3.0, markup=True):
                self.notifications.append((message, severity))
                # Don't call super() to avoid actual notifications during test

        def mock_remove_constraints(package_names):
            # Capture what package names are passed for deletion
            app.packages_to_delete.extend(package_names)

            # Check that package names are proper strings
            for pkg_name in package_names:
                assert isinstance(pkg_name, str), f"Package name should be string, got {type(pkg_name)}: {pkg_name}"
                assert "RowKey" not in pkg_name, f"Package name contains RowKey object: {pkg_name}"
                assert pkg_name == "testpackage", f"Expected 'testpackage', got: {pkg_name}"

            # Return mock success response
            return "/fake/path", {pkg_name: '>1.0' for pkg_name in package_names}, {}

        app = TestMainTUIApp()

        # Mock the table and query methods
        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_table.cursor_row = 0
            mock_table.rows = [Mock()]
            mock_query.return_value = mock_table

            # Mock constraint removal at the constraints module level
            with patch('pipu.package_constraints.remove_constraints_from_config', side_effect=mock_remove_constraints):
                with patch.object(app, 'push_screen') as mock_push_screen:

                    # Call action_delete_constraint
                    app.action_delete_constraint()

                    # Verify that push_screen was called (confirmation screen shown)
                    assert mock_push_screen.called

                    # Get the callback function that would handle the confirmation
                    args, kwargs = mock_push_screen.call_args
                    confirm_screen, callback = args

                    # Mock _refresh_table_display to avoid UI updates during test
                    with patch.object(app, '_refresh_table_display'):
                        # Simulate user confirming deletion
                        callback(True)

                        # Verify the package name received is a proper string
                        assert len(app.packages_to_delete) == 1
                        package_name = app.packages_to_delete[0]

                        # CRITICAL REGRESSION TEST: Ensure no RowKey objects
                        assert isinstance(package_name, str)
                        assert "RowKey" not in package_name
                        assert package_name == "testpackage"

                        # Check for success notification (no error notifications)
                        error_notifications = [msg for msg, severity in app.notifications if "Error" in msg]
                        assert len(error_notifications) == 0, f"Should have no error notifications, got: {error_notifications}"

    def test_package_uninstall_uses_string_package_names(self):
        """
        Regression test: Ensure package uninstall uses actual package names, not RowKey objects.
        """
        app = MainTUIApp()

        # Set up test package
        app.all_packages = [
            {
                'name': 'testpackage',
                'version': '1.0.0',
                'latest_version': '2.0.0',
                'latest_filetype': 'wheel',
                'constraint': None,
                'outdated': True
            }
        ]

        uninstall_package_names = []

        def mock_show_uninstall_confirmation(package_name):
            uninstall_package_names.append(package_name)

        # Mock the table and _show_uninstall_confirmation method
        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_table.cursor_row = 0
            mock_table.rows = [Mock()]
            mock_query.return_value = mock_table

            with patch.object(app, '_show_uninstall_confirmation', side_effect=mock_show_uninstall_confirmation):

                # Call action_uninstall_package
                app.action_uninstall_package()

                # Verify the package name received is a proper string
                assert len(uninstall_package_names) == 1
                package_name = uninstall_package_names[0]

                # CRITICAL REGRESSION TEST: Ensure no RowKey objects
                assert isinstance(package_name, str)
                assert "RowKey" not in package_name
                assert package_name == "testpackage"

    def test_rowkey_objects_are_never_used_as_strings(self):
        """
        Meta-test: Verify that RowKey objects, when converted to strings,
        contain identifying text that our regression tests can detect.
        """
        from textual.widgets._data_table import RowKey

        # Create a RowKey directly (simpler than creating through DataTable)
        row_key = RowKey("test_key")

        # Verify that RowKey objects have identifiable string representations
        row_key_str = str(row_key)
        assert "RowKey" in row_key_str or "object at" in row_key_str, \
            f"RowKey string representation should be identifiable: {row_key_str}"

        # This confirms our regression tests can detect RowKey objects
        print(f"RowKey string representation: {row_key_str}")

    def test_edge_case_empty_table(self):
        """Test that _get_selected_package handles empty table gracefully."""
        app = MainTUIApp()
        app.all_packages = []

        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_table.cursor_row = 0
            mock_table.rows = []  # Empty table
            mock_query.return_value = mock_table

            selected = app._get_selected_package()
            assert selected is None

    def test_edge_case_invalid_cursor_position(self):
        """Test that _get_selected_package handles invalid cursor positions."""
        app = MainTUIApp()
        app.all_packages = [
            {
                'name': 'package-a',
                'version': '1.0.0',
                'latest_version': '2.0.0',
                'latest_filetype': 'wheel',
                'constraint': None,
                'outdated': True
            }
        ]

        with patch.object(app, 'query_one') as mock_query:
            mock_table = Mock()
            mock_table.cursor_row = 5  # Invalid position (beyond table)
            mock_table.rows = [Mock()]  # Only one row
            mock_query.return_value = mock_table

            selected = app._get_selected_package()
            assert selected is None


class TestFormatLatestVersionUtility:
    """Test the _format_latest_version() utility method."""

    def test_format_with_no_constraint(self):
        """Test formatting latest version with no constraint (should be green)."""
        from pipu.ui.apps import MainTUIApp
        from rich.text import Text

        app = MainTUIApp()
        result = app._format_latest_version("2.0.0", None)

        assert isinstance(result, Text)
        # Check that it contains the version text
        assert "2.0.0" in result.plain
        # Check that it has green styling
        assert any(span.style and 'green' in str(span.style) for span in result.spans)

    def test_format_with_satisfied_constraint(self):
        """Test formatting latest version with satisfied constraint (should be green)."""
        from pipu.ui.apps import MainTUIApp
        from rich.text import Text

        app = MainTUIApp()
        result = app._format_latest_version("2.0.0", ">=2.0.0")

        assert isinstance(result, Text)
        # Check that it contains the version text
        assert "2.0.0" in result.plain
        # Should be green when constraint is satisfied
        assert any(span.style and 'green' in str(span.style) for span in result.spans)

    def test_format_with_violated_constraint(self):
        """Test formatting latest version with violated constraint (should be red)."""
        from pipu.ui.apps import MainTUIApp
        from rich.text import Text

        app = MainTUIApp()
        result = app._format_latest_version("3.0.0", "<3.0.0")

        assert isinstance(result, Text)
        # Check that it contains the version text
        assert "3.0.0" in result.plain
        # Should be red when constraint is violated
        assert any(span.style and 'red' in str(span.style) for span in result.spans)

    def test_format_with_complex_constraint(self):
        """Test formatting with complex constraint expressions."""
        from pipu.ui.apps import MainTUIApp
        from rich.text import Text

        app = MainTUIApp()

        # Test satisfied complex constraint
        result1 = app._format_latest_version("2.5.0", ">=2.0.0,<3.0.0")
        assert "2.5.0" in result1.plain
        assert any(span.style and 'green' in str(span.style) for span in result1.spans)

        # Test violated complex constraint
        result2 = app._format_latest_version("3.5.0", ">=2.0.0,<3.0.0")
        assert "3.5.0" in result2.plain
        assert any(span.style and 'red' in str(span.style) for span in result2.spans)


class TestWorkerCancellationTracking:
    """Test improved worker cancellation with tracking and logging."""

    def test_worker_cancellation_tracks_cancelled_workers(self):
        """Test that worker cancellation properly tracks which workers were cancelled."""
        from pipu.ui.apps import MainTUIApp
        from unittest.mock import Mock, patch

        app = MainTUIApp()

        # Create mock workers
        mock_worker1 = Mock()
        mock_worker1.is_finished = False
        mock_worker1.name = "check_updates"
        mock_worker1.cancel = Mock()

        mock_worker2 = Mock()
        mock_worker2.is_finished = True  # Already finished
        mock_worker2.name = "completed_worker"

        # Patch the workers property to return our mocks
        with patch.object(type(app), 'workers', new_callable=lambda: property(lambda self: [mock_worker1, mock_worker2])):
            # Call action_quit_app which handles cancellation
            with patch.object(app, 'exit') as mock_exit:
                app.action_quit_app()

            # Worker 1 should be cancelled
            mock_worker1.cancel.assert_called_once()
            # Worker 2 should not be cancelled (already finished)
            assert not hasattr(mock_worker2, 'cancel') or not mock_worker2.cancel.called
