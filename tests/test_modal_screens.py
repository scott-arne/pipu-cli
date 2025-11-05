"""
Tests for modal screen components in the TUI.

This module tests modal dialog screens including base classes,
confirmation dialogs, and screen refactorings.
"""
import pytest
from unittest.mock import Mock, patch
from rich.text import Text


class TestBaseConfirmationScreen:
    """Test the BaseConfirmationScreen base class."""

    def test_initialization_with_defaults(self):
        """Test BaseConfirmationScreen initialization with default parameters."""
        from pipu.ui.modal_dialogs import BaseConfirmationScreen

        screen = BaseConfirmationScreen(message="Test message")
        assert screen.message == "Test message"
        assert screen.confirm_text == "Confirm"
        assert screen.cancel_text == "Cancel"
        assert screen.confirm_variant == "success"
        assert screen.cancel_variant == "primary"

    def test_initialization_with_custom_parameters(self):
        """Test BaseConfirmationScreen initialization with custom parameters."""
        from pipu.ui.modal_dialogs import BaseConfirmationScreen

        screen = BaseConfirmationScreen(
            message="Custom message",
            confirm_text="Yes",
            cancel_text="No",
            confirm_variant="error",
            cancel_variant="warning"
        )
        assert screen.message == "Custom message"
        assert screen.confirm_text == "Yes"
        assert screen.cancel_text == "No"
        assert screen.confirm_variant == "error"
        assert screen.cancel_variant == "warning"

    def test_compose_creates_proper_widgets(self):
        """Test that compose() creates the proper widget structure."""
        from pipu.ui.modal_dialogs import BaseConfirmationScreen

        screen = BaseConfirmationScreen(message="Test message")
        widgets = list(screen.compose())

        # Should return a Grid widget
        assert len(widgets) == 1
        grid = widgets[0]
        assert grid.id == "dialog"

    def test_css_includes_button_styling(self):
        """Test that CSS includes required button styling."""
        from pipu.ui.modal_dialogs import BaseConfirmationScreen

        screen = BaseConfirmationScreen(message="Test")
        css = screen.CSS

        # Check for essential CSS elements
        assert "BaseConfirmationScreen" in css
        assert "#dialog" in css
        assert "#question" in css
        assert "#actions" in css
        assert "#confirm" in css
        assert "#cancel" in css
        assert "color: white" in css


class TestUninstallConfirmScreenRefactored:
    """Test that UninstallConfirmScreen works correctly after refactoring."""

    def test_inherits_from_base_confirmation_screen(self):
        """Test that UninstallConfirmScreen properly inherits from BaseConfirmationScreen."""
        from pipu.ui.modal_dialogs import UninstallConfirmScreen, BaseConfirmationScreen

        screen = UninstallConfirmScreen("test-package")
        assert isinstance(screen, BaseConfirmationScreen)

    def test_message_contains_package_name(self):
        """Test that the message contains the package name."""
        from pipu.ui.modal_dialogs import UninstallConfirmScreen

        screen = UninstallConfirmScreen("my-package")
        assert "my-package" in screen.message

    def test_uses_error_variant_for_confirm(self):
        """Test that confirm button uses error variant for uninstall."""
        from pipu.ui.modal_dialogs import UninstallConfirmScreen

        screen = UninstallConfirmScreen("test-package")
        assert screen.confirm_variant == "error"
        assert screen.confirm_text == "Yes, Uninstall"
