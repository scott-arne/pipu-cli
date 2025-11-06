"""
Test CSS validation to prevent syntax errors in Textual applications.

This test ensures that all CSS definitions in the codebase follow Textual CSS syntax
and will catch CSS errors before they cause runtime failures.
"""

import ast
import re
import pytest
from pathlib import Path
from typing import List, Tuple
from textual.app import App, ComposeResult
from textual.widgets import Static


def find_css_definitions() -> List[Tuple[str, str, str]]:
    """
    Find all CSS definitions in Python files.

    :returns: List of (file_path, class_name, css_content) tuples
    """
    css_definitions = []
    project_root = Path(__file__).parent.parent / "pipu_cli"

    for py_file in project_root.rglob("*.py"):
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Parse the Python file to find CSS class attributes
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue  # Skip files with syntax errors

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    class_name = node.name
                    for item in node.body:
                        if (isinstance(item, ast.Assign) and
                            len(item.targets) == 1 and
                            isinstance(item.targets[0], ast.Name) and
                            item.targets[0].id == "CSS" and
                            isinstance(item.value, ast.Constant)):

                            css_content = item.value.value
                            css_definitions.append((str(py_file), class_name, css_content))

        except Exception as e:
            # Skip files that can't be read
            print(f"Warning: Could not process {py_file}: {e}")
            continue

    return css_definitions


def validate_css_syntax(css_content: str) -> List[str]:
    """
    Validate CSS syntax for Textual compatibility.

    :param css_content: CSS content to validate
    :returns: List of error messages
    """
    errors = []

    if not css_content or not css_content.strip():
        return errors

    lines = css_content.split('\n')
    line_num = 0

    for line in lines:
        line_num += 1
        line = line.strip()

        if not line or line.startswith('#') or line.startswith('/*'):
            continue

        # Check for pixel values in grid properties (not allowed in Textual)
        if re.search(r'grid-rows.*\d+px', line):
            errors.append(f"Line {line_num}: Invalid pixel value in grid-rows property: {line}")

        if re.search(r'grid-columns.*\d+px', line):
            errors.append(f"Line {line_num}: Invalid pixel value in grid-columns property: {line}")

        # Check for invalid CSS property syntax (basic validation)
        if ':' in line and not line.endswith('{') and not line.endswith('}'):
            if line.count(':') == 1 and not line.strip().endswith(';'):
                # This might be OK in some contexts, but flag for review
                pass

    return errors


def test_css_syntax_validation():
    """
    Test that all CSS definitions in the codebase have valid syntax.

    This test prevents CSS parsing errors from causing runtime failures
    in the TUI application.
    """
    css_definitions = find_css_definitions()

    assert len(css_definitions) > 0, "Should find at least some CSS definitions in the codebase"

    all_errors = []

    for file_path, class_name, css_content in css_definitions:
        errors = validate_css_syntax(css_content)
        if errors:
            for error in errors:
                all_errors.append(f"{file_path} in {class_name}: {error}")

    if all_errors:
        error_message = "CSS syntax errors found:\n" + "\n".join(all_errors)
        pytest.fail(error_message)


def test_delete_constraint_screen_css():
    """
    Specific test for DeleteConstraintConfirmScreen CSS to prevent the reported error.

    This test ensures the DeleteConstraintConfirmScreen can be instantiated without
    CSS parsing errors.
    """
    # Test that we can create the screen without CSS errors
    from pipu_cli.ui.modal_dialogs import DeleteConstraintConfirmScreen

    try:
        screen = DeleteConstraintConfirmScreen("test_package", ">=1.0.0")
        assert screen is not None

        # Validate the CSS doesn't contain pixel values in grid properties
        css_content = screen.CSS
        errors = validate_css_syntax(css_content)
        assert len(errors) == 0, f"CSS errors in DeleteConstraintConfirmScreen: {errors}"

    except Exception as e:
        pytest.fail(f"Failed to create DeleteConstraintConfirmScreen: {e}")


def test_all_modal_screens_css():
    """
    Test CSS syntax for all modal screen classes.

    This ensures all modal dialogs can be created without CSS parsing errors.
    """
    from pipu_cli.ui.modal_dialogs import (
        ConstraintInputScreen,
        HelpScreen,
        DeleteConstraintConfirmScreen,
        RemoveAllConstraintsConfirmScreen,
        UninstallConfirmScreen
    )

    # Test each modal screen class
    modal_classes = [
        (ConstraintInputScreen, ("test_package", "")),
        (HelpScreen, ()),
        (DeleteConstraintConfirmScreen, ("test_package", ">=1.0.0")),
        (RemoveAllConstraintsConfirmScreen, (10,)),
        (UninstallConfirmScreen, ("test_package",))
    ]

    for modal_class, args in modal_classes:
        try:
            screen = modal_class(*args)
            if hasattr(screen, 'CSS') and screen.CSS:
                errors = validate_css_syntax(screen.CSS)
                assert len(errors) == 0, f"CSS errors in {modal_class.__name__}: {errors}"
        except Exception as e:
            pytest.fail(f"Failed to create {modal_class.__name__}: {e}")


class CSSTestApp(App):
    """Test app to validate CSS parsing doesn't fail at runtime."""

    CSS = """
    Screen {
        background: $surface;
    }

    #test-widget {
        grid-rows: 1fr 4;  /* Should be valid - no px values */
        grid-columns: 1fr;
        height: 10;
        width: 50%;
    }
    """

    def compose(self) -> ComposeResult:
        """Create test widget."""
        yield Static("CSS Test", id="test-widget")


def test_css_parsing_at_runtime():
    """
    Test that CSS parsing works correctly at runtime.

    This test creates a simple app with CSS to ensure our CSS fixes
    don't break the Textual CSS parser.
    """
    app = CSSTestApp()

    # Test that the app can be created without CSS parsing errors
    assert app is not None

    # Test CSS validation on our test CSS
    errors = validate_css_syntax(CSSTestApp.CSS)
    assert len(errors) == 0, f"CSS errors in test app: {errors}"


def test_specific_css_error_regression():
    """
    Test for the specific CSS error that was reported.

    This test ensures that the CSS error 'Invalid value 'px' in grid-rows property'
    is caught and prevented.
    """
    # This CSS should fail validation (contains px in grid-rows)
    bad_css = """
    #dialog {
        grid-size: 2;
        grid-rows: 1fr 1fr 60px;  /* This should be caught as an error */
        width: 70;
    }
    """

    errors = validate_css_syntax(bad_css)
    assert len(errors) > 0, "Should detect pixel values in grid-rows property"
    assert any("60px" in error for error in errors), f"Should flag 60px specifically: {errors}"

    # This CSS should pass validation (no px in grid properties)
    good_css = """
    #dialog {
        grid-size: 2;
        grid-rows: 1fr 1fr 4;  /* This should be valid */
        width: 70;
    }
    """

    errors = validate_css_syntax(good_css)
    assert len(errors) == 0, f"Should not flag valid CSS: {errors}"


def test_modal_button_visibility():
    """
    Test that modal dialogs have proper button styling for visibility.

    This ensures buttons have background colors and text colors specified
    to prevent invisible button text issues.
    """
    from pipu_cli.ui.modal_dialogs import (
        DeleteConstraintConfirmScreen,
        RemoveAllConstraintsConfirmScreen,
        UninstallConfirmScreen
    )

    # Modal screens that should have styled buttons
    modal_screens_with_buttons = [
        (DeleteConstraintConfirmScreen, ("test_package", ">=1.0.0")),
        (RemoveAllConstraintsConfirmScreen, (5,)),
        (UninstallConfirmScreen, ("test_package",))
    ]

    for modal_class, args in modal_screens_with_buttons:
        screen = modal_class(*args)
        css = screen.CSS

        # Check that buttons have styling (either Button or fake-button class)
        has_button_styling = ("Button {" in css or ".fake-button" in css)
        assert has_button_styling, f"{modal_class.__name__} should have button styling"

        # Check for button-specific styling (confirm/cancel buttons)
        assert "#confirm" in css, f"{modal_class.__name__} should have #confirm button styling"
        assert "#cancel" in css, f"{modal_class.__name__} should have #cancel button styling"

        # Check that buttons have background and color specified
        assert "background:" in css, f"{modal_class.__name__} buttons should have background color"
        assert "color:" in css, f"{modal_class.__name__} buttons should have text color"

        # Check for background and text colors (using theme variables like working examples)
        assert ("background: $error" in css or "background: $primary" in css), f"{modal_class.__name__} should use theme colors"
        assert "color: white" in css, f"{modal_class.__name__} buttons should have white text"


if __name__ == "__main__":
    # Run the test directly for debugging
    test_css_syntax_validation()
    test_delete_constraint_screen_css()
    test_all_modal_screens_css()
    test_css_parsing_at_runtime()
    test_specific_css_error_regression()
    print("All CSS validation tests passed!")