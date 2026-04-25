"""Tests for upgrade UI display."""

from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

from pipu_cli.ui import UpgradeUI


class TestUpgradeUIPhases:
    """Tests for spinner/checkmark phase transitions."""

    def test_start_phase_creates_spinner(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True, highlight=False)
        ui = UpgradeUI(console)
        ui.start_phase("Inspecting installed packages...")
        # Phase should be tracked as active
        assert ui._active_phase is not None

    def test_complete_phase_shows_checkmark_and_summary(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True, highlight=False)
        ui = UpgradeUI(console)
        ui.start_phase("Inspecting installed packages...")
        ui.complete_phase("Found 182 packages")
        output = buf.getvalue()
        assert "Found 182 packages" in output
        assert ui._active_phase is None

    def test_multiple_phases_cascade(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True, highlight=False)
        ui = UpgradeUI(console)
        ui.start_phase("Inspecting installed packages...")
        ui.complete_phase("Found 182 packages")
        ui.start_phase("Checking for updates...")
        ui.complete_phase("12 newer versions available")
        ui.start_phase("Resolving dependency constraints...")
        ui.complete_phase("3 safe to upgrade")
        output = buf.getvalue()
        assert "Found 182 packages" in output
        assert "12 newer versions available" in output
        assert "3 safe to upgrade" in output

    def test_complete_phase_without_start_raises(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True, highlight=False)
        ui = UpgradeUI(console)
        try:
            ui.complete_phase("summary")
            assert False, "Should have raised"
        except RuntimeError:
            pass

    def test_cleanup_stops_active_phase(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True, highlight=False)
        ui = UpgradeUI(console)
        ui.start_phase("Working...")
        assert ui._active_phase is not None
        ui.cleanup()
        assert ui._active_phase is None
        assert ui._active_task_id is None
        assert ui._active_description is None

    def test_cleanup_safe_when_no_active_phase(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True, highlight=False)
        ui = UpgradeUI(console)
        ui.cleanup()
        assert ui._active_phase is None


class TestDownloadTracker:
    """Tests for download progress tracking."""

    def test_show_download_progress_returns_tracker(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True)
        ui = UpgradeUI(console)
        tracker = ui.show_download_progress(["requests==2.31.0", "rich==13.7.0"])
        assert tracker is not None

    def test_download_tracker_complete_marks_done(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True)
        ui = UpgradeUI(console)
        tracker = ui.show_download_progress(["requests==2.31.0"])
        tracker.complete("requests==2.31.0")
        assert tracker._completed == 1
        assert tracker._failed == 0
        tracker.finish()
        output = buf.getvalue()
        assert "100%" in output

    def test_download_tracker_fail_marks_error(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True)
        ui = UpgradeUI(console)
        tracker = ui.show_download_progress(["requests==2.31.0"])
        tracker.fail("requests==2.31.0")
        assert tracker._failed == 1
        assert tracker._completed == 0
        tracker.finish()
        output = buf.getvalue()
        assert "100%" in output


class TestInstallTracker:
    """Tests for install progress tracking."""

    def test_show_install_progress_returns_tracker(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True)
        ui = UpgradeUI(console)
        tracker = ui.show_install_progress(["requests==2.31.0", "rich==13.7.0"])
        assert tracker is not None

    def test_install_tracker_complete_marks_done(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True)
        ui = UpgradeUI(console)
        tracker = ui.show_install_progress(["requests==2.31.0"])
        tracker.complete("requests==2.31.0")
        assert tracker._completed == 1
        tracker.finish()
        output = buf.getvalue()
        assert "100%" in output


class TestGroupInstallTracker:
    """Tests for per-environment progress bars during group install."""

    def test_show_group_install_progress_returns_tracker(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True)
        ui = UpgradeUI(console)
        tracker = ui.show_group_install_progress(
            env_names=["main", "ml", "web"],
            env_totals={"main": 3, "ml": 2, "web": 2},
        )
        assert tracker is not None

    def test_group_tracker_advance_updates_bar(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True)
        ui = UpgradeUI(console)
        tracker = ui.show_group_install_progress(
            env_names=["main", "ml"],
            env_totals={"main": 2, "ml": 1},
        )
        tracker.advance("main", "requests")
        tracker.advance("main", "numpy")
        tracker.complete_env("main")
        tracker.finish()
        output = buf.getvalue()
        assert "main" in output

    def test_group_tracker_fail_env(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True)
        ui = UpgradeUI(console)
        tracker = ui.show_group_install_progress(
            env_names=["main"],
            env_totals={"main": 2},
        )
        tracker.fail_env("main", "pip error")
        tracker.finish()
        output = buf.getvalue()
        assert "main" in output


class TestContextManagerCursorRestoration:
    """Context manager protocol guarantees cursor restoration on exception."""

    def test_upgrade_ui_restores_cursor_on_exception(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True, highlight=False)
        with patch.object(console, "show_cursor", wraps=console.show_cursor) as spy:
            with pytest.raises(KeyboardInterrupt):
                with UpgradeUI(console) as ui:
                    ui.start_phase("Working...")
                    raise KeyboardInterrupt
            assert any(
                call.args == (True,) or call.kwargs.get("visible") is True
                for call in spy.call_args_list
            ), f"show_cursor(True) was not called; calls={spy.call_args_list}"

    def test_download_tracker_restores_cursor_on_exception(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True, highlight=False)
        ui = UpgradeUI(console)
        with patch.object(console, "show_cursor", wraps=console.show_cursor) as spy:
            with pytest.raises(KeyboardInterrupt):
                with ui.show_download_progress(["requests==2.31.0"]) as tracker:
                    tracker.start("requests==2.31.0")
                    raise KeyboardInterrupt
            assert any(
                call.args == (True,) or call.kwargs.get("visible") is True
                for call in spy.call_args_list
            ), f"show_cursor(True) was not called; calls={spy.call_args_list}"

    def test_group_install_tracker_restores_cursor_on_exception(self):
        buf = StringIO()

        console = Console(file=buf, force_terminal=True, highlight=False)
        ui = UpgradeUI(console)
        with patch.object(console, "show_cursor", wraps=console.show_cursor) as spy:
            with pytest.raises(KeyboardInterrupt):
                with ui.show_group_install_progress(
                    env_names=["main"],
                    env_totals={"main": 2},
                ) as tracker:
                    tracker.advance("main", "requests")
                    raise KeyboardInterrupt
            assert any(
                call.args == (True,) or call.kwargs.get("visible") is True
                for call in spy.call_args_list
            ), f"show_cursor(True) was not called; calls={spy.call_args_list}"
