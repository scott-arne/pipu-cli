"""Tests for upgrade UI display."""

from io import StringIO
from rich.console import Console

from pipu_cli.ui import UpgradeUI


class TestUpgradeUIPhases:
    """Tests for spinner/checkmark phase transitions."""

    def test_start_phase_creates_spinner(self):
        console = Console(file=StringIO(), force_terminal=True, highlight=False)
        ui = UpgradeUI(console)
        ui.start_phase("Inspecting installed packages...")
        # Phase should be tracked as active
        assert ui._active_phase is not None

    def test_complete_phase_shows_checkmark_and_summary(self):
        console = Console(file=StringIO(), force_terminal=True, highlight=False)
        ui = UpgradeUI(console)
        ui.start_phase("Inspecting installed packages...")
        ui.complete_phase("Found 182 packages")
        output = console.file.getvalue()
        assert "Found 182 packages" in output
        assert ui._active_phase is None

    def test_multiple_phases_cascade(self):
        console = Console(file=StringIO(), force_terminal=True, highlight=False)
        ui = UpgradeUI(console)
        ui.start_phase("Inspecting installed packages...")
        ui.complete_phase("Found 182 packages")
        ui.start_phase("Checking for updates...")
        ui.complete_phase("12 newer versions available")
        ui.start_phase("Resolving dependency constraints...")
        ui.complete_phase("3 safe to upgrade")
        output = console.file.getvalue()
        assert "Found 182 packages" in output
        assert "12 newer versions available" in output
        assert "3 safe to upgrade" in output

    def test_complete_phase_without_start_raises(self):
        console = Console(file=StringIO(), force_terminal=True, highlight=False)
        ui = UpgradeUI(console)
        try:
            ui.complete_phase("summary")
            assert False, "Should have raised"
        except RuntimeError:
            pass


class TestDownloadTracker:
    """Tests for download progress tracking."""

    def test_show_download_progress_returns_tracker(self):
        console = Console(file=StringIO(), force_terminal=True)
        ui = UpgradeUI(console)
        tracker = ui.show_download_progress(["requests==2.31.0", "rich==13.7.0"])
        assert tracker is not None

    def test_download_tracker_complete_marks_done(self):
        console = Console(file=StringIO(), force_terminal=True)
        ui = UpgradeUI(console)
        tracker = ui.show_download_progress(["requests==2.31.0"])
        tracker.complete("requests==2.31.0")
        tracker.finish()
        output = console.file.getvalue()
        assert "requests" in output

    def test_download_tracker_fail_marks_error(self):
        console = Console(file=StringIO(), force_terminal=True)
        ui = UpgradeUI(console)
        tracker = ui.show_download_progress(["requests==2.31.0"])
        tracker.fail("requests==2.31.0", "network error")
        tracker.finish()
        output = console.file.getvalue()
        assert "requests" in output


class TestInstallTracker:
    """Tests for install progress tracking."""

    def test_show_install_progress_returns_tracker(self):
        console = Console(file=StringIO(), force_terminal=True)
        ui = UpgradeUI(console)
        tracker = ui.show_install_progress(["requests==2.31.0", "rich==13.7.0"])
        assert tracker is not None

    def test_install_tracker_complete_marks_done(self):
        console = Console(file=StringIO(), force_terminal=True)
        ui = UpgradeUI(console)
        tracker = ui.show_install_progress(["requests==2.31.0"])
        tracker.complete("requests==2.31.0")
        tracker.finish()
        output = console.file.getvalue()
        assert "requests" in output
