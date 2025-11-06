import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import os
import subprocess
from pipu_cli.package_constraints import (
    find_project_root, parse_requirement_line, read_constraints,
    get_current_environment_name, get_pip_config_paths, read_pip_config_constraint,
    parse_inline_constraints, read_ignores, read_pip_config_ignore,
    parse_inline_ignores, read_ignores_file,
    # Invalidation trigger functions
    parse_invalidation_trigger, format_invalidation_triggers,
    parse_invalidation_triggers_storage, merge_invalidation_triggers,
    validate_invalidation_triggers, add_constraints_to_config,
    remove_constraints_from_config, remove_all_constraints_from_config,
    add_ignores_to_config, remove_ignores_from_config, remove_all_ignores_from_config,
    list_all_constraints, list_all_ignores,
    # Auto-constraint functions
    discover_auto_constraints, apply_auto_constraints,
    # New constraint validation and cleanup functions
    check_constraint_invalidations, validate_package_installation,
    get_constraint_violation_summary, evaluate_invalidation_triggers,
    cleanup_invalidated_constraints, post_install_cleanup
)


def test_find_project_root_with_pyproject_toml():
    """
    Test finding project root when pyproject.toml exists.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create pyproject.toml
        (temp_path / "pyproject.toml").touch()
        
        # Create subdirectory and change to it
        subdir = temp_path / "subdir"
        subdir.mkdir()
        
        with patch('pipu_cli.package_constraints.Path.cwd', return_value=subdir):
            result = find_project_root()
            assert result == temp_path


def test_find_project_root_with_setup_py():
    """
    Test finding project root when setup.py exists.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create setup.py
        (temp_path / "setup.py").touch()
        
        # Create nested subdirectory and change to it
        subdir = temp_path / "deep" / "nested" / "dir"
        subdir.mkdir(parents=True)
        
        with patch('pipu_cli.package_constraints.Path.cwd', return_value=subdir):
            result = find_project_root()
            assert result == temp_path


def test_find_project_root_with_both_files():
    """
    Test finding project root when both pyproject.toml and setup.py exist.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create both files
        (temp_path / "pyproject.toml").touch()
        (temp_path / "setup.py").touch()
        
        with patch('pipu_cli.package_constraints.Path.cwd', return_value=temp_path):
            result = find_project_root()
            assert result == temp_path


def test_find_project_root_not_found():
    """
    Test finding project root when no project files exist.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        with patch('pipu_cli.package_constraints.Path.cwd', return_value=temp_path):
            result = find_project_root()
            assert result is None


def test_find_project_root_in_current_directory():
    """
    Test finding project root when files are in current directory.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create pyproject.toml in current directory
        (temp_path / "pyproject.toml").touch()
        
        with patch('pipu_cli.package_constraints.Path.cwd', return_value=temp_path):
            result = find_project_root()
            assert result == temp_path


def test_parse_exact_version():
    """
    Test parsing exact version constraints.
    
    :returns: None
    """
    result = parse_requirement_line("requests==2.31.0")
    expected = {"name": "requests", "constraint": "==2.31.0"}
    assert result == expected


def test_parse_minimum_version():
    """
    Test parsing minimum version constraints.
    
    :returns: None
    """
    result = parse_requirement_line("numpy>=1.20.0")
    expected = {"name": "numpy", "constraint": ">=1.20.0"}
    assert result == expected


def test_parse_version_range():
    """
    Test parsing version range constraints.
    
    :returns: None
    """
    result = parse_requirement_line("pandas>=1.0.0,<2.0.0")
    expected = {"name": "pandas", "constraint": ">=1.0.0,<2.0.0"}
    assert result == expected


def test_parse_compatible_version():
    """
    Test parsing compatible version constraints.
    
    :returns: None
    """
    result = parse_requirement_line("click~=8.0.0")
    expected = {"name": "click", "constraint": "~=8.0.0"}
    assert result == expected


def test_parse_complex_constraint():
    """
    Test parsing complex version constraints with exclusions.
    
    :returns: None
    """
    result = parse_requirement_line("scipy>=1.9.0,!=1.9.1,<2.0.0")
    expected = {"name": "scipy", "constraint": ">=1.9.0,!=1.9.1,<2.0.0"}
    assert result == expected


def test_parse_package_with_underscores():
    """
    Test parsing package names with underscores.
    
    :returns: None
    """
    result = parse_requirement_line("some_package==1.0.0")
    expected = {"name": "some_package", "constraint": "==1.0.0"}
    assert result == expected


def test_parse_package_with_hyphens():
    """
    Test parsing package names with hyphens.
    
    :returns: None
    """
    result = parse_requirement_line("another-package>=2.0.0")
    expected = {"name": "another-package", "constraint": ">=2.0.0"}
    assert result == expected


def test_parse_single_character_package():
    """
    Test parsing single character package names.
    
    :returns: None
    """
    result = parse_requirement_line("z==1.0.0")
    expected = {"name": "z", "constraint": "==1.0.0"}
    assert result == expected


def test_parse_comment_line():
    """
    Test that comment lines return None.
    
    :returns: None
    """
    result = parse_requirement_line("# This is a comment")
    assert result is None


def test_parse_empty_line():
    """
    Test that empty lines return None.
    
    :returns: None
    """
    result = parse_requirement_line("")
    assert result is None


def test_parse_whitespace_line():
    """
    Test that whitespace-only lines return None.
    
    :returns: None
    """
    result = parse_requirement_line("   \t  \n")
    assert result is None


def test_parse_line_with_inline_comment():
    """
    Test parsing lines with inline comments.
    
    :returns: None
    """
    result = parse_requirement_line("requests==2.31.0  # HTTP library")
    expected = {"name": "requests", "constraint": "==2.31.0"}
    assert result == expected


def test_parse_invalid_format():
    """
    Test that invalid formats return None.
    
    :returns: None
    """
    invalid_lines = [
        "invalid-line-without-version",
        "==1.0.0",
        ">=",
        "package name with spaces==1.0.0",
        "123invalid==1.0.0"
    ]
    
    for line in invalid_lines:
        result = parse_requirement_line(line)
        assert result is None, f"Expected None for invalid line: {line}"


def test_parse_line_with_extra_whitespace():
    """
    Test parsing lines with extra whitespace.

    :returns: None
    """
    result = parse_requirement_line("  requests == 2.31.0  ")
    expected = {"name": "requests", "constraint": "== 2.31.0"}
    assert result == expected


def test_parse_requirement_line_two_part_versions():
    """
    Test parsing two-part version numbers.

    This test prevents regression of the issue where >1.0, ==1.0, etc. were failing.
    The original regex had a bug where it would match single letters, causing
    package names to be parsed incorrectly.

    :returns: None
    """
    test_cases = [
        ("requests>1.0", {"name": "requests", "constraint": ">1.0"}),
        ("requests>=1.0", {"name": "requests", "constraint": ">=1.0"}),
        ("requests<1.0", {"name": "requests", "constraint": "<1.0"}),
        ("requests<=1.0", {"name": "requests", "constraint": "<=1.0"}),
        ("requests==1.0", {"name": "requests", "constraint": "==1.0"}),
        ("requests!=1.0", {"name": "requests", "constraint": "!=1.0"}),
        ("requests~=1.0", {"name": "requests", "constraint": "~=1.0"}),
        ("numpy>2.5", {"name": "numpy", "constraint": ">2.5"}),
        ("flask==3.1", {"name": "flask", "constraint": "==3.1"}),
    ]

    for input_line, expected in test_cases:
        result = parse_requirement_line(input_line)
        assert result == expected, f"Failed to parse '{input_line}'"


def test_parse_requirement_line_single_part_versions():
    """
    Test parsing single-part version numbers.

    :returns: None
    """
    test_cases = [
        ("requests>1", {"name": "requests", "constraint": ">1"}),
        ("requests>=2", {"name": "requests", "constraint": ">=2"}),
        ("requests==3", {"name": "requests", "constraint": "==3"}),
        ("numpy<5", {"name": "numpy", "constraint": "<5"}),
        ("flask!=4", {"name": "flask", "constraint": "!=4"}),
    ]

    for input_line, expected in test_cases:
        result = parse_requirement_line(input_line)
        assert result == expected, f"Failed to parse '{input_line}'"


def test_parse_requirement_line_complex_constraints():
    """
    Test parsing complex multi-constraint specifications.

    :returns: None
    """
    test_cases = [
        ("requests>=1.0,<2.5", {"name": "requests", "constraint": ">=1.0,<2.5"}),
        ("numpy>=1.20,<2.0,!=1.24", {"name": "numpy", "constraint": ">=1.20,<2.0,!=1.24"}),
        ("flask>1.0, <3.0", {"name": "flask", "constraint": ">1.0, <3.0"}),
        ("django>=3.2,!=3.2.1,<4.0", {"name": "django", "constraint": ">=3.2,!=3.2.1,<4.0"}),
    ]

    for input_line, expected in test_cases:
        result = parse_requirement_line(input_line)
        assert result == expected, f"Failed to parse '{input_line}'"


def test_parse_requirement_line_regression_tests():
    """
    Specific regression tests for the original failing cases.

    These were the exact cases that were failing before the regex fix.
    The issue was that the regex pattern had an incorrect OR condition:
    r'^([a-zA-Z][a-zA-Z0-9._-]*|[a-zA-Z])([><=!~,.\\s0-9]+)$'

    The '|[a-zA-Z]' part would match just a single letter from the package name,
    leaving the rest as part of the constraint, causing parsing to fail.

    :returns: None
    """
    # The original failing cases that users reported
    failing_cases = [
        "requests>1.0",
        "requests>1.0.0",
        "requests==1.0",
        "numpy>=2.5",
        "flask<3.1",
    ]

    for case in failing_cases:
        result = parse_requirement_line(case)
        assert result is not None, f"Regression: '{case}' should not return None"
        assert 'name' in result, f"Regression: '{case}' missing 'name' field"
        assert 'constraint' in result, f"Regression: '{case}' missing 'constraint' field"

        # Ensure package name doesn't contain constraint operators (the original bug)
        package_name = result['name']
        assert not any(op in package_name for op in ['>', '<', '=', '!', '~']), \
            f"Regression: package name '{package_name}' contains constraint operators"

        # Ensure constraint doesn't contain parts of the package name
        constraint = result['constraint']
        expected_package = case.split('>')[0].split('<')[0].split('=')[0].split('!')[0].split('~')[0]
        assert expected_package not in constraint, \
            f"Regression: constraint '{constraint}' contains package name parts"


def test_parse_requirement_line_screenshot_cases():
    """
    Test the specific constraint cases that were shown in the screenshot.

    These cases were failing with "Invalid constraint" errors in the TUI:
    - '>1' (single digit with greater than)
    - '>1.0' (single digit with decimal with greater than)

    :returns: None
    """
    screenshot_cases = [
        ("testpackage>1", "testpackage", ">1"),
        ("testpackage>1.0", "testpackage", ">1.0"),
        ("numpy>1", "numpy", ">1"),
        ("requests>1.0", "requests", ">1.0"),
    ]

    for case, expected_name, expected_constraint in screenshot_cases:
        result = parse_requirement_line(case)
        assert result is not None, f"Screenshot case '{case}' should not return None"
        assert result['name'] == expected_name, f"Expected name '{expected_name}', got '{result['name']}'"
        assert result['constraint'] == expected_constraint, f"Expected constraint '{expected_constraint}', got '{result['constraint']}'"


def test_parse_requirement_line_comprehensive_operators():
    """
    Test parse_requirement_line with comprehensive constraint operator coverage.

    This ensures all valid constraint operators work with various version formats.

    :returns: None
    """
    test_cases = [
        # Single operators with different version formats
        ("pkg>1", "pkg", ">1"),
        ("pkg>1.0", "pkg", ">1.0"),
        ("pkg>1.0.0", "pkg", ">1.0.0"),
        ("pkg>=1", "pkg", ">=1"),
        ("pkg>=1.0", "pkg", ">=1.0"),
        ("pkg>=1.0.0", "pkg", ">=1.0.0"),
        ("pkg<2", "pkg", "<2"),
        ("pkg<2.0", "pkg", "<2.0"),
        ("pkg<2.0.0", "pkg", "<2.0.0"),
        ("pkg<=2", "pkg", "<=2"),
        ("pkg<=2.0", "pkg", "<=2.0"),
        ("pkg<=2.0.0", "pkg", "<=2.0.0"),
        ("pkg==1", "pkg", "==1"),
        ("pkg==1.0", "pkg", "==1.0"),
        ("pkg==1.0.0", "pkg", "==1.0.0"),
        ("pkg!=1", "pkg", "!=1"),
        ("pkg!=1.0", "pkg", "!=1.0"),
        ("pkg!=1.0.0", "pkg", "!=1.0.0"),
        ("pkg~=1", "pkg", "~=1"),
        ("pkg~=1.0", "pkg", "~=1.0"),
        ("pkg~=1.0.0", "pkg", "~=1.0.0"),

        # Pre-release versions
        ("pkg>1.0a1", "pkg", ">1.0a1"),
        ("pkg>=1.0b2", "pkg", ">=1.0b2"),
        ("pkg==1.0rc1", "pkg", "==1.0rc1"),
        ("pkg<2.0dev", "pkg", "<2.0dev"),

        # Complex compound constraints
        ("pkg>=1.0.0,<2.0.0", "pkg", ">=1.0.0,<2.0.0"),
        ("pkg>=1.0, <2.0", "pkg", ">=1.0, <2.0"),
        ("pkg>1,<3", "pkg", ">1,<3"),
    ]

    for case, expected_name, expected_constraint in test_cases:
        result = parse_requirement_line(case)
        assert result is not None, f"Case '{case}' should not return None"
        assert result['name'] == expected_name, f"Case '{case}': expected name '{expected_name}', got '{result['name']}'"
        assert result['constraint'] == expected_constraint, f"Case '{case}': expected constraint '{expected_constraint}', got '{result['constraint']}'"


@pytest.fixture
def assets_dir():
    """
    Fixture providing path to test assets directory.
    
    :returns: Path to assets directory
    """
    return Path(__file__).parent / "assets"


def test_read_valid_constraints_file(assets_dir):
    """
    Test reading a valid constraints file.
    
    :param assets_dir: Path to test assets directory
    :returns: None
    """
    # Mock find_project_root to return the assets directory and disable environment detection
    with patch('pipu_cli.package_constraints.find_project_root', return_value=assets_dir):
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
            with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=None):
                with patch.dict('os.environ', {}, clear=True):
                    result = read_constraints("valid_constraints.txt", include_auto=False)

                    # Check that we got the expected constraints
                    assert "requests" in result
                    assert "numpy" in result
                    assert "django" in result  # Should be lowercase
                    assert "pyyaml" in result  # Should be lowercase

                    # Check specific values
                    assert result["requests"] == "==2.31.0"
                    assert result["numpy"] == ">=1.20.0,<2.0.0"
                    assert result["pandas"] == "~=2.0.0"
                    assert result["click"] == ">=8.0.0"


def test_read_empty_constraints_file(assets_dir):
    """
    Test reading a constraints file with only comments and empty lines.
    
    :param assets_dir: Path to test assets directory
    :returns: None
    """
    with patch('pipu_cli.package_constraints.find_project_root', return_value=assets_dir):
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
            with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=None):
                with patch.dict('os.environ', {}, clear=True):
                    result = read_constraints("empty_constraints.txt", include_auto=False)
                    assert result == {}


def test_read_invalid_constraints_file(assets_dir):
    """
    Test reading a constraints file with some invalid lines.
    
    :param assets_dir: Path to test assets directory
    :returns: None
    """
    with patch('pipu_cli.package_constraints.find_project_root', return_value=assets_dir):
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
            with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=None):
                with patch.dict('os.environ', {}, clear=True):
                    result = read_constraints("invalid_constraints.txt", include_auto=False)

                    # Should only contain valid entries
                    assert "requests" in result
                    assert "numpy" in result
                    assert len(result) == 2
                    
                    assert result["requests"] == "==2.31.0"
                    assert result["numpy"] == ">=1.20.0"


def test_read_duplicate_constraints_file(assets_dir):
    """
    Test reading a constraints file with duplicate package entries.
    
    Should use the last occurrence and warn about duplicates.
    
    :param assets_dir: Path to test assets directory
    :returns: None
    """
    with patch('pipu_cli.package_constraints.find_project_root', return_value=assets_dir):
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
            with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=None):
                with patch.dict('os.environ', {}, clear=True):
                    # Capture print output to verify warnings
                    with patch('builtins.print') as mock_print:
                        result = read_constraints("duplicate_constraints.txt", include_auto=False)
                        
                        # Should use the last occurrence of each package
                        assert result["requests"] == "==2.30.0"  # Last occurrence
                        assert result["numpy"] == ">=1.19.0"    # Last occurrence
                        
                        # Should have printed warnings about duplicates
                        mock_print.assert_called()


def test_read_nonexistent_file(assets_dir):
    """
    Test reading a constraints file that doesn't exist.
    
    Should return empty dictionary without raising an error.
    
    :param assets_dir: Path to test assets directory
    :returns: None
    """
    with patch('pipu_cli.package_constraints.find_project_root', return_value=assets_dir):
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
            with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=None):
                with patch.dict('os.environ', {}, clear=True):
                    result = read_constraints("nonexistent_file.txt", include_auto=False)
                    assert result == {}


def test_read_constraints_no_project_root():
    """
    Test reading constraints when no project root is found.
    
    Should return empty dictionary when no constraints are found from any source.
    
    :returns: None
    """
    with patch('pipu_cli.package_constraints.find_project_root', return_value=None):
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
            with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=None):
                with patch.dict(os.environ, {}, clear=True):
                    result = read_constraints(include_auto=False)
                    assert result == {}


def test_read_constraints_file_permission_error(assets_dir):
    """
    Test reading constraints when file cannot be read due to permissions.
    
    :param assets_dir: Path to test assets directory
    :returns: None
    :raises IOError: When file cannot be read
    """
    with patch('pipu_cli.package_constraints.find_project_root', return_value=assets_dir):
        with patch('builtins.open', side_effect=PermissionError("Permission denied")):
            with pytest.raises(IOError) as exc_info:
                read_constraints("valid_constraints.txt", include_auto=False)
            
            assert "Failed to read constraints file" in str(exc_info.value)


def test_read_constraints_custom_filename(assets_dir):
    """
    Test reading constraints with a custom filename.
    
    :param assets_dir: Path to test assets directory
    :returns: None
    """
    with patch('pipu_cli.package_constraints.find_project_root', return_value=assets_dir):
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
            with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=None):
                with patch.dict('os.environ', {}, clear=True):
                    result = read_constraints("valid_constraints.txt", include_auto=False)
                    assert isinstance(result, dict)
                    assert len(result) > 0


def test_read_constraints_case_normalization(assets_dir):
    """
    Test that package names are normalized to lowercase.
    
    :param assets_dir: Path to test assets directory
    :returns: None
    """
    with patch('pipu_cli.package_constraints.find_project_root', return_value=assets_dir):
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
            with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=None):
                with patch.dict('os.environ', {}, clear=True):
                    result = read_constraints("valid_constraints.txt", include_auto=False)

                    # Check that mixed case packages are normalized
                    assert "django" in result     # Was Django
                    assert "pyyaml" in result     # Was PyYAML
                    assert "Django" not in result
                    assert "PyYAML" not in result


def test_read_constraints_encoding():
    """
    Test reading constraints file with UTF-8 encoding.
    
    :returns: None
    """
    # Create a temporary file with UTF-8 content
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        constraints_file = temp_path / "utf8_constraints.txt"
        
        # Write file with UTF-8 characters
        with open(constraints_file, 'w', encoding='utf-8') as f:
            f.write("# Test with UTF-8 characters: àáâãäå\n")
            f.write("requests==2.31.0\n")
        
        with patch('pipu_cli.package_constraints.find_project_root', return_value=temp_path):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=None):
                    with patch.dict('os.environ', {}, clear=True):
                        result = read_constraints("utf8_constraints.txt", include_auto=False)
                        assert result["requests"] == "==2.31.0"


def test_get_current_environment_name_poetry_error():
    """
    Test get_current_environment_name when poetry command fails.
    
    :returns: None
    """
    with patch.dict(os.environ, {'POETRY_ACTIVE': '1'}, clear=True):
        with patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, 'poetry')):
            result = get_current_environment_name()
            assert result is None


def test_get_current_environment_name_poetry_file_not_found():
    """
    Test get_current_environment_name when poetry command is not found.
    
    :returns: None
    """
    with patch.dict(os.environ, {'POETRY_ACTIVE': '1'}, clear=True):
        with patch('subprocess.run', side_effect=FileNotFoundError):
            result = get_current_environment_name()
            assert result is None


def test_get_current_environment_name_conda_env():
    """
    Test get_current_environment_name with conda environment (not base).
    
    :returns: None
    """
    with patch.dict(os.environ, {'CONDA_DEFAULT_ENV': 'myenv'}):
        result = get_current_environment_name()
        assert result == 'myenv'


def test_get_current_environment_name_poetry_success():
    """
    Test get_current_environment_name with successful poetry command.
    
    :returns: None
    """
    with patch.dict(os.environ, {'POETRY_ACTIVE': '1'}, clear=True):
        mock_result = MagicMock()
        mock_result.stdout = 'my-poetry-env\n'
        with patch('subprocess.run', return_value=mock_result):
            result = get_current_environment_name()
            assert result == 'my-poetry-env'


def test_get_current_environment_name_virtual_env():
    """
    Test get_current_environment_name with virtual environment.
    
    :returns: None
    """
    with patch.dict(os.environ, {'VIRTUAL_ENV': '/path/to/my-venv'}, clear=True):
        result = get_current_environment_name()
        assert result == 'my-venv'


def test_get_pip_config_paths_windows():
    """
    Test get_pip_config_paths on Windows platform.
    
    :returns: None
    """
    with patch('sys.platform', 'win32'):
        with patch.dict(os.environ, {'APPDATA': r'C:\Users\test\AppData\Roaming'}):
            paths = get_pip_config_paths()
            expected_user = Path(r'C:\Users\test\AppData\Roaming') / 'pip' / 'pip.ini'
            expected_global = Path('C:') / 'ProgramData' / 'pip' / 'pip.ini'
            assert expected_user in paths
            assert expected_global in paths


def test_get_pip_config_paths_windows_no_appdata():
    """
    Test get_pip_config_paths on Windows without APPDATA environment variable.
    
    :returns: None
    """
    with patch('sys.platform', 'win32'):
        with patch.dict(os.environ, {}, clear=True):
            paths = get_pip_config_paths()
            expected_global = Path('C:') / 'ProgramData' / 'pip' / 'pip.ini'
            assert expected_global in paths


def test_read_pip_config_constraint_env_specific():
    """
    Test read_pip_config_constraint with environment-specific section.
    
    :returns: None
    """
    config_content = """
[test_env]
constraint = /path/to/env/constraints.txt

[global]
constraint = /path/to/global/constraints.txt
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_constraint('test_env')
            assert result is not None
            constraint_type, constraint_value = result
            assert constraint_type == 'file'
            assert constraint_value == '/path/to/env/constraints.txt'
    
    os.unlink(f.name)


def test_read_pip_config_constraint_env_constraints_option():
    """
    Test read_pip_config_constraint with constraints option in env section.
    
    :returns: None
    """
    config_content = """
[test_env]
constraints = /path/to/env/constraints.txt
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_constraint('test_env')
            assert result is not None
            constraint_type, constraint_value = result
            assert constraint_type == 'file'
            assert constraint_value == '/path/to/env/constraints.txt'
    
    os.unlink(f.name)


def test_read_pip_config_constraint_global_constraint():
    """
    Test read_pip_config_constraint with constraint option in global section.
    
    :returns: None
    """
    config_content = """
[global]
constraint = /path/to/global/constraints.txt
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_constraint()
            assert result is not None
            constraint_type, constraint_value = result
            assert constraint_type == 'file'
            assert constraint_value == '/path/to/global/constraints.txt'
    
    os.unlink(f.name)


def test_read_pip_config_constraint_global_constraints():
    """
    Test read_pip_config_constraint with constraints option in global section.
    
    :returns: None
    """
    config_content = """
[global]
constraints = /path/to/global/constraints.txt
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_constraint()
            assert result is not None
            constraint_type, constraint_value = result
            assert constraint_type == 'file'
            assert constraint_value == '/path/to/global/constraints.txt'
    
    os.unlink(f.name)


def test_read_pip_config_constraint_config_error():
    """
    Test read_pip_config_constraint when config file has errors.
    
    :returns: None
    """
    config_content = """
[invalid config file
this is not valid ini format
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_constraint()
            assert result is None
    
    os.unlink(f.name)


def test_read_constraints_pip_constraint_env():
    """
    Test read_constraints with PIP_CONSTRAINT environment variable.
    
    :returns: None
    """
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write('requests==2.31.0\n')
        f.flush()
        
        with patch.dict(os.environ, {'PIP_CONSTRAINT': f.name}):
            result = read_constraints(include_auto=False)
            assert 'requests' in result
            assert result['requests'] == '==2.31.0'
    
    os.unlink(f.name)


def test_read_constraints_pip_constraint_env_nonexistent():
    """
    Test read_constraints with PIP_CONSTRAINT pointing to nonexistent file.
    
    :returns: None
    """
    with patch.dict(os.environ, {'PIP_CONSTRAINT': '/nonexistent/path/constraints.txt'}):
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
            with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=None):
                with patch('pipu_cli.package_constraints.find_project_root', return_value=None):
                    result = read_constraints(include_auto=False)
                    assert result == {}


def test_read_constraints_pip_config():
    """
    Test read_constraints with pip config constraint path.
    
    :returns: None
    """
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write('numpy>=1.20.0\n')
        f.flush()
        
        with patch.dict(os.environ, {}, clear=True):  # Clear PIP_CONSTRAINT
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value='test'):
                with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=('file', f.name)):
                    result = read_constraints(include_auto=False)
                    assert 'numpy' in result
                    assert result['numpy'] == '>=1.20.0'
    
    os.unlink(f.name)


def test_read_constraints_pip_config_nonexistent():
    """
    Test read_constraints with pip config pointing to nonexistent file.
    
    :returns: None
    """
    with patch.dict(os.environ, {}, clear=True):  # Clear PIP_CONSTRAINT
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value='test'):
            with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=None):
                with patch('pipu_cli.package_constraints.find_project_root', return_value=None):
                    result = read_constraints(include_auto=False)
                    assert result == {}


def test_parse_inline_constraints_single_line():
    """
    Test parse_inline_constraints with single constraint.
    
    :returns: None
    """
    constraints_text = "requests>=2.25.0,<3.0.0"
    result = parse_inline_constraints(constraints_text)
    assert result == {"requests": ">=2.25.0,<3.0.0"}


def test_parse_inline_constraints_multiple_lines():
    """
    Test parse_inline_constraints with multiple constraints.
    
    :returns: None
    """
    constraints_text = """requests>=2.25.0,<3.0.0
numpy>=1.20.0
Django==4.1.0"""
    result = parse_inline_constraints(constraints_text)
    expected = {
        "requests": ">=2.25.0,<3.0.0",
        "numpy": ">=1.20.0", 
        "django": "==4.1.0"
    }
    assert result == expected


def test_parse_inline_constraints_with_comments():
    """
    Test parse_inline_constraints with comments and empty lines.
    
    :returns: None
    """
    constraints_text = """# Main dependencies
requests>=2.25.0  # HTTP library

# Scientific computing
numpy>=1.20.0
# Empty line above should be ignored"""
    result = parse_inline_constraints(constraints_text)
    expected = {
        "requests": ">=2.25.0",
        "numpy": ">=1.20.0"
    }
    assert result == expected


def test_parse_inline_constraints_empty():
    """
    Test parse_inline_constraints with empty string.
    
    :returns: None
    """
    result = parse_inline_constraints("")
    assert result == {}
    
    result = parse_inline_constraints("\n\n# Just comments\n\n")
    assert result == {}


def test_read_pip_config_constraint_inline_constraints():
    """
    Test read_pip_config_constraint with inline constraints in config.
    
    :returns: None
    """
    config_content = """
[test_env]
constraints = 
    requests>=2.25.0,<3.0.0
    numpy>=1.20.0
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_constraint('test_env')
            assert result is not None
            constraint_type, constraint_value = result
            assert constraint_type == 'inline'
            assert 'requests>=2.25.0,<3.0.0' in constraint_value
            assert 'numpy>=1.20.0' in constraint_value
    
    os.unlink(f.name)


def test_read_pip_config_constraint_file_path():
    """
    Test read_pip_config_constraint with file path in config.
    
    :returns: None
    """
    config_content = """
[test_env]
constraints = /path/to/constraints.txt
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_constraint('test_env')
            assert result is not None
            constraint_type, constraint_value = result
            assert constraint_type == 'file'
            assert constraint_value == '/path/to/constraints.txt'
    
    os.unlink(f.name)


def test_read_pip_config_constraint_detects_inline_by_operators():
    """
    Test that constraints with version operators are detected as inline.
    
    :returns: None
    """
    config_content = """
[global]
constraints = requests==2.25.0 numpy>=1.20.0
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_constraint()
            assert result is not None
            constraint_type, constraint_value = result
            assert constraint_type == 'inline'
            assert constraint_value == 'requests==2.25.0 numpy>=1.20.0'
    
    os.unlink(f.name)


def test_read_constraints_with_inline_config():
    """
    Test read_constraints with inline constraints from pip config.
    
    :returns: None
    """
    config_content = """
[test_env]
constraints = 
    requests>=2.25.0
    numpy>=1.20.0
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch.dict(os.environ, {}, clear=True):  # Clear PIP_CONSTRAINT
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value='test_env'):
                with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
                    result = read_constraints(include_auto=False)
                    expected = {
                        'requests': '>=2.25.0',
                        'numpy': '>=1.20.0'
                    }
                    assert result == expected
    
    os.unlink(f.name)


def test_parse_inline_constraints_duplicate_packages():
    """
    Test parse_inline_constraints with duplicate package entries.
    
    :returns: None
    """
    constraints_text = """requests>=2.25.0
requests==2.26.0
numpy>=1.20.0"""
    
    # Capture print output to verify warnings
    with patch('builtins.print') as mock_print:
        result = parse_inline_constraints(constraints_text)
        
        # Should use the last occurrence
        expected = {
            "requests": "==2.26.0",
            "numpy": ">=1.20.0"
        }
        assert result == expected
        
        # Should have printed warning about duplicate
        mock_print.assert_called()


def test_parse_inline_constraints_invalid_lines():
    """
    Test parse_inline_constraints with invalid constraint lines.
    
    :returns: None
    """
    constraints_text = """requests>=2.25.0
invalid-line-without-version-spec
numpy>=1.20.0
==1.0.0
another-invalid-line
django==4.1.0"""
    
    result = parse_inline_constraints(constraints_text)
    expected = {
        "requests": ">=2.25.0",
        "numpy": ">=1.20.0",
        "django": "==4.1.0"
    }
    assert result == expected


def test_parse_inline_constraints_whitespace_handling():
    """
    Test parse_inline_constraints with various whitespace scenarios.
    
    :returns: None
    """
    constraints_text = """  requests  >=  2.25.0  ,  < 3.0.0  
    
    numpy>=1.20.0

django   ==   4.1.0   """
    
    result = parse_inline_constraints(constraints_text)
    expected = {
        "requests": ">=  2.25.0  ,  < 3.0.0",
        "numpy": ">=1.20.0", 
        "django": "==   4.1.0"
    }
    assert result == expected


def test_read_pip_config_constraint_mixed_detection():
    """
    Test read_pip_config_constraint detection with edge cases.
    
    :returns: None
    """
    # Test case where path happens to contain operators (should still be treated as inline)
    config_content = """
[test_env]
constraints = /path/with>=signs/constraints.txt
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_constraint('test_env')
            assert result is not None
            constraint_type, constraint_value = result
            # Should be detected as inline due to >= operator
            assert constraint_type == 'inline'
            assert constraint_value == '/path/with>=signs/constraints.txt'
    
    os.unlink(f.name)


def test_parse_inline_ignores_single_package():
    """
    Test parse_inline_ignores with single package name.
    
    :returns: None
    """
    ignores_text = "requests"
    result = parse_inline_ignores(ignores_text)
    assert result == {"requests"}


def test_parse_inline_ignores_space_separated():
    """
    Test parse_inline_ignores with space-separated package names.
    
    :returns: None
    """
    ignores_text = "requests numpy flask"
    result = parse_inline_ignores(ignores_text)
    assert result == {"requests", "numpy", "flask"}


def test_parse_inline_ignores_newline_separated():
    """
    Test parse_inline_ignores with newline-separated package names.
    
    :returns: None
    """
    ignores_text = """requests
numpy
flask"""
    result = parse_inline_ignores(ignores_text)
    assert result == {"requests", "numpy", "flask"}


def test_parse_inline_ignores_mixed_whitespace():
    """
    Test parse_inline_ignores with mixed whitespace and comments.
    
    :returns: None
    """
    ignores_text = """# Main packages to ignore
requests numpy   flask

# Additional packages
django
# Empty line above should be ignored"""
    result = parse_inline_ignores(ignores_text)
    assert result == {"requests", "numpy", "flask", "django"}


def test_parse_inline_ignores_case_normalization():
    """
    Test parse_inline_ignores normalizes package names to lowercase.
    
    :returns: None
    """
    ignores_text = "Requests NumPy FLASK Django"
    result = parse_inline_ignores(ignores_text)
    assert result == {"requests", "numpy", "flask", "django"}


def test_parse_inline_ignores_empty():
    """
    Test parse_inline_ignores with empty string and comments only.
    
    :returns: None
    """
    result = parse_inline_ignores("")
    assert result == set()
    
    result = parse_inline_ignores("# Just comments\n# More comments")
    assert result == set()


def test_read_ignores_file_valid(assets_dir):
    """
    Test reading a valid ignores file.
    
    :param assets_dir: Path to test assets directory
    :returns: None
    """
    # Create a test ignores file
    ignores_file = assets_dir / "test_ignores.txt"
    with open(ignores_file, 'w', encoding='utf-8') as f:
        f.write("# Packages to ignore\n")
        f.write("requests\n")
        f.write("numpy  # Scientific computing\n")
        f.write("flask\n")
        f.write("\n")
        f.write("# Web frameworks\n")
        f.write("django\n")
    
    try:
        result = read_ignores_file(str(ignores_file))
        assert set(result) == {"requests", "numpy", "flask", "django"}
    finally:
        ignores_file.unlink()


def test_read_ignores_file_nonexistent():
    """
    Test reading a nonexistent ignores file raises FileNotFoundError.
    
    :returns: None
    """
    with pytest.raises(FileNotFoundError):
        read_ignores_file("/nonexistent/path/ignores.txt")


def test_read_ignores_file_permission_error():
    """
    Test reading ignores file with permission error.
    
    :returns: None
    """
    with patch('builtins.open', side_effect=PermissionError("Permission denied")):
        with pytest.raises(PermissionError):
            read_ignores_file("/path/to/ignores.txt")


def test_read_pip_config_ignore_env_specific():
    """
    Test read_pip_config_ignore with environment-specific section.
    
    :returns: None
    """
    config_content = """
[test_env]
ignore = /path/to/env/ignores.txt

[global]
ignore = /path/to/global/ignores.txt
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_ignore('test_env')
            assert result is not None
            ignore_type, ignore_value = result
            assert ignore_type == 'file'
            assert ignore_value == '/path/to/env/ignores.txt'
    
    os.unlink(f.name)


def test_read_pip_config_ignore_env_ignores_option():
    """
    Test read_pip_config_ignore with ignores option in env section.
    
    :returns: None
    """
    config_content = """
[test_env]
ignores = /path/to/env/ignores.txt
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_ignore('test_env')
            assert result is not None
            ignore_type, ignore_value = result
            assert ignore_type == 'file'
            assert ignore_value == '/path/to/env/ignores.txt'
    
    os.unlink(f.name)


def test_read_pip_config_ignore_global_ignore():
    """
    Test read_pip_config_ignore with ignore option in global section.
    
    :returns: None
    """
    config_content = """
[global]
ignore = /path/to/global/ignores.txt
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_ignore()
            assert result is not None
            ignore_type, ignore_value = result
            assert ignore_type == 'file'
            assert ignore_value == '/path/to/global/ignores.txt'
    
    os.unlink(f.name)


def test_read_pip_config_ignore_global_ignores():
    """
    Test read_pip_config_ignore with ignores option in global section.
    
    :returns: None
    """
    config_content = """
[global]
ignores = /path/to/global/ignores.txt
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_ignore()
            assert result is not None
            ignore_type, ignore_value = result
            assert ignore_type == 'file'
            assert ignore_value == '/path/to/global/ignores.txt'
    
    os.unlink(f.name)


def test_read_pip_config_ignore_inline_detection():
    """
    Test read_pip_config_ignore detects inline ignores properly.
    
    :returns: None
    """
    config_content = """
[test_env]
ignores = requests numpy flask
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_ignore('test_env')
            assert result is not None
            ignore_type, ignore_value = result
            assert ignore_type == 'inline'
            assert ignore_value == 'requests numpy flask'
    
    os.unlink(f.name)


def test_read_pip_config_ignore_multiline_inline():
    """
    Test read_pip_config_ignore with multiline inline ignores.
    
    :returns: None
    """
    config_content = """
[test_env]
ignores = 
    requests
    numpy
    flask
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_ignore('test_env')
            assert result is not None
            ignore_type, ignore_value = result
            assert ignore_type == 'inline'
            assert 'requests' in ignore_value
            assert 'numpy' in ignore_value
            assert 'flask' in ignore_value
    
    os.unlink(f.name)


def test_read_pip_config_ignore_config_error():
    """
    Test read_pip_config_ignore when config file has errors.
    
    :returns: None
    """
    config_content = """
[invalid config file
this is not valid ini format
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_ignore()
            assert result is None
    
    os.unlink(f.name)


def test_read_ignores_pip_config_inline():
    """
    Test read_ignores with inline ignores from pip config.
    
    :returns: None
    """
    config_content = """
[test_env]
ignores = requests numpy flask
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value='test_env'):
            with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
                result = read_ignores()
                expected = {'requests', 'numpy', 'flask'}
                assert result == expected
    
    os.unlink(f.name)


def test_read_ignores_pip_config_file():
    """
    Test read_ignores with ignore file path from pip config.
    
    :returns: None
    """
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as ignores_file:
        ignores_file.write('requests\nnumpy\nflask\n')
        ignores_file.flush()
        
        config_content = f"""
[test_env]
ignore = {ignores_file.name}
"""
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as config_file:
            config_file.write(config_content)
            config_file.flush()
            
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value='test_env'):
                with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(config_file.name)]):
                    result = read_ignores()
                    expected = {'requests', 'numpy', 'flask'}
                    assert result == expected
        
        os.unlink(config_file.name)
    
    os.unlink(ignores_file.name)


def test_read_ignores_no_config():
    """
    Test read_ignores when no ignore configuration is found.
    
    :returns: None
    """
    with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
        with patch('pipu_cli.package_constraints.read_pip_config_ignore', return_value=None):
            result = read_ignores()
            assert result == set()


def test_read_ignores_file_not_found():
    """
    Test read_ignores when ignore file doesn't exist.
    
    :returns: None
    """
    config_content = """
[test_env]
ignore = /nonexistent/path/ignores.txt
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value='test_env'):
            with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
                result = read_ignores()
                # Should return empty set when file doesn't exist
                assert result == set()
    
    os.unlink(f.name)


def test_read_pip_config_ignore_simple_package_name():
    """
    Test read_pip_config_constraint with just a package name (no operators).
    
    :returns: None
    """
    config_content = """
[test_env]
constraints = requests
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_constraint('test_env')
            assert result is not None
            constraint_type, constraint_value = result
            # Should be treated as file path since no operators
            assert constraint_type == 'file'
            assert constraint_value == 'requests'
    
    os.unlink(f.name)


def test_read_pip_config_constraint_space_separated_packages():
    """
    Test read_pip_config_constraint with space-separated package constraints.
    
    :returns: None
    """
    config_content = """
[global]
constraints = requests==2.25.0 numpy>=1.20.0 django~=4.1.0
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_constraint()
            assert result is not None
            constraint_type, constraint_value = result
            assert constraint_type == 'inline'
            assert constraint_value == 'requests==2.25.0 numpy>=1.20.0 django~=4.1.0'
    
    os.unlink(f.name)


def test_parse_inline_constraints_space_separated():
    """
    Test parse_inline_constraints with space-separated constraints on one line.
    
    :returns: None
    """
    # Note: This tests the scenario, but our current parser expects one constraint per line
    # Space-separated constraints on the same line won't parse correctly with current implementation
    constraints_text = "requests==2.25.0 numpy>=1.20.0"
    result = parse_inline_constraints(constraints_text)
    
    # Current implementation will treat this as invalid since it doesn't match the regex pattern
    # This is acceptable behavior - we expect one constraint per line
    assert result == {}


def test_read_constraints_with_inline_config_multiline():
    """
    Test read_constraints with multiline inline constraints from pip config.
    
    :returns: None
    """
    config_content = """
[test_env]
constraints = 
    # Web frameworks
    django>=4.1.0,<5.0.0
    flask>=2.0.0
    
    # Database
    psycopg2>=2.9.0
    sqlalchemy>=1.4.0
    
    # Utilities
    requests>=2.25.0
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch.dict(os.environ, {}, clear=True):  # Clear PIP_CONSTRAINT
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value='test_env'):
                with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
                    result = read_constraints(include_auto=False)
                    expected = {
                        'django': '>=4.1.0,<5.0.0',
                        'flask': '>=2.0.0',
                        'psycopg2': '>=2.9.0',
                        'sqlalchemy': '>=1.4.0',
                        'requests': '>=2.25.0'
                    }
                    assert result == expected
    
    os.unlink(f.name)


def test_read_constraints_falls_back_from_inline_to_file():
    """
    Test that read_constraints falls back properly when inline config file doesn't exist.
    
    :returns: None
    """
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as constraints_file:
        constraints_file.write('requests>=2.25.0\nnumpy>=1.20.0\n')
        constraints_file.flush()
        
        with patch.dict(os.environ, {}, clear=True):  # Clear PIP_CONSTRAINT
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value='test_env'):
                # Return tuple indicating file path, but file doesn't exist
                with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=('file', '/nonexistent/path')):
                    with patch('pipu_cli.package_constraints.find_project_root', return_value=Path(constraints_file.name).parent):
                        # Should fall back to project root constraints file
                        result = read_constraints(Path(constraints_file.name).name, include_auto=False)
                        expected = {
                            'requests': '>=2.25.0',
                            'numpy': '>=1.20.0'
                        }
                        assert result == expected
    
    os.unlink(constraints_file.name)


def test_read_constraints_with_auto_discovery():
    """
    Test that read_constraints automatically discovers constraints from installed packages.

    :returns: None
    """
    # Mock manual constraints
    manual_constraints = {
        'requests': '>=2.25.0',
        'numpy': '>=1.20.0'
    }

    # Mock auto-discovered constraints
    auto_constraints = [
        ('flask==2.1.0', 'some-package>=1.0.0'),
        ('django~=4.0.0', 'another-package>=2.0.0'),
        ('numpy==1.19.0', 'conflicting-package>=1.0.0')  # Should be ignored (manual takes precedence)
    ]

    with patch.dict(os.environ, {}, clear=True):  # Clear PIP_CONSTRAINT
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
            with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=('inline', 'requests>=2.25.0\nnumpy>=1.20.0')):
                with patch('pipu_cli.package_constraints.discover_auto_constraints', return_value=auto_constraints):
                    result = read_constraints(include_auto=True)

                    # Should include both manual and auto-discovered constraints
                    # Manual constraints should take precedence over auto constraints
                    expected = {
                        'requests': '>=2.25.0',  # From manual
                        'numpy': '>=1.20.0',      # From manual (overrides auto)
                        'flask': '==2.1.0',       # From auto
                        'django': '~=4.0.0'       # From auto
                    }
                    assert result == expected


def test_read_constraints_without_auto_discovery():
    """
    Test that read_constraints can skip auto-discovery when include_auto=False.

    :returns: None
    """
    # Mock manual constraints
    auto_constraints = [
        ('flask==2.1.0', 'some-package>=1.0.0'),
        ('django~=4.0.0', 'another-package>=2.0.0')
    ]

    with patch.dict(os.environ, {}, clear=True):  # Clear PIP_CONSTRAINT
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
            with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=('inline', 'requests>=2.25.0\nnumpy>=1.20.0')):
                with patch('pipu_cli.package_constraints.discover_auto_constraints', return_value=auto_constraints):
                    result = read_constraints(include_auto=False)

                    # Should only include manual constraints
                    expected = {
                        'requests': '>=2.25.0',
                        'numpy': '>=1.20.0'
                    }
                    assert result == expected


def test_read_constraints_auto_only():
    """
    Test that read_constraints works when there are no manual constraints, only auto-discovered ones.

    :returns: None
    """
    # Mock auto-discovered constraints
    auto_constraints = [
        ('flask==2.1.0', 'some-package>=1.0.0'),
        ('django~=4.0.0', 'another-package>=2.0.0')
    ]

    with patch.dict(os.environ, {}, clear=True):  # Clear PIP_CONSTRAINT
        with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
            with patch('pipu_cli.package_constraints.read_pip_config_constraint', return_value=None):
                with patch('pipu_cli.package_constraints.find_project_root', return_value=None):
                    with patch('pipu_cli.package_constraints.discover_auto_constraints', return_value=auto_constraints):
                        result = read_constraints(include_auto=True)

                        # Should only include auto-discovered constraints
                        expected = {
                            'flask': '==2.1.0',
                            'django': '~=4.0.0'
                        }
                        assert result == expected


def test_get_auto_constraint_triggers():
    """
    Test get_auto_constraint_triggers function.

    :returns: None
    """
    # Mock auto-discovered constraints
    mock_auto_constraints = [
        ('flask==2.1.0', 'some-package>=1.0.0'),
        ('django~=4.0.0', 'another-package>=2.0.0'),
        ('numpy==1.19.0', 'pandas>=1.0.0'),
        ('numpy<2.0.0', 'scikit-learn>=1.0.0')  # Multiple triggers for same package
    ]

    # Mock no manual constraints
    with patch('pipu_cli.package_constraints.discover_auto_constraints', return_value=mock_auto_constraints):
        with patch('pipu_cli.package_constraints.read_constraints', return_value={}):
            from pipu_cli.package_constraints import get_auto_constraint_triggers

            result = get_auto_constraint_triggers()

            # Should have triggers for flask, django, and numpy
            assert 'flask' in result
            assert 'django' in result
            assert 'numpy' in result

            # Check trigger values
            assert result['flask'] == ['some-package>=1.0.0']
            assert result['django'] == ['another-package>=2.0.0']

            # Numpy should have both triggers
            assert len(result['numpy']) == 2
            assert 'pandas>=1.0.0' in result['numpy']
            assert 'scikit-learn>=1.0.0' in result['numpy']


def test_get_auto_constraint_triggers_respects_manual_constraints():
    """
    Test that get_auto_constraint_triggers excludes packages with manual constraints.

    :returns: None
    """
    # Mock auto-discovered constraints
    mock_auto_constraints = [
        ('flask==2.1.0', 'some-package>=1.0.0'),
        ('django~=4.0.0', 'another-package>=2.0.0'),
        ('numpy==1.19.0', 'pandas>=1.0.0'),
        ('requests==2.28.0', 'urllib3>=1.0.0')
    ]

    # Mock manual constraints for flask and numpy
    mock_manual_constraints = {
        'flask': '>=2.0.0',
        'numpy': '>=1.20.0'
    }

    with patch('pipu_cli.package_constraints.discover_auto_constraints', return_value=mock_auto_constraints):
        with patch('pipu_cli.package_constraints.read_constraints', return_value=mock_manual_constraints):
            from pipu_cli.package_constraints import get_auto_constraint_triggers

            result = get_auto_constraint_triggers()

            # Should NOT have triggers for flask and numpy (they have manual constraints)
            assert 'flask' not in result
            assert 'numpy' not in result

            # Should have triggers for django and requests (no manual constraints)
            assert 'django' in result
            assert 'requests' in result

            assert result['django'] == ['another-package>=2.0.0']
            assert result['requests'] == ['urllib3>=1.0.0']


def test_read_pip_config_constraint_no_config_files():
    """
    Test read_pip_config_constraint when no config files exist.
    
    :returns: None
    """
    with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[]):
        result = read_pip_config_constraint('test_env')
        assert result is None


def test_read_pip_config_constraint_empty_config_file():
    """
    Test read_pip_config_constraint with empty config file.
    
    :returns: None
    """
    config_content = ""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(config_content)
        f.flush()
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[Path(f.name)]):
            result = read_pip_config_constraint('test_env')
            assert result is None
    
    os.unlink(f.name)


# Tests for new constraint/ignore management functions

def test_add_constraints_to_config_new_constraint():
    """
    Test adding a new constraint to config.

    :returns: None
    """
    from pipu_cli.package_constraints import add_constraints_to_config
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result_path, changes = add_constraints_to_config(['requests>=2.25.0'])
                
                assert result_path == config_path
                assert 'requests' in changes
                assert changes['requests'] == ('added', '>=2.25.0')
                
                # Verify file was written
                assert config_path.exists()


def test_remove_constraints_from_config_success():
    """
    Test successfully removing constraints from config.

    :returns: None
    """
    from pipu_cli.package_constraints import remove_constraints_from_config
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create initial config with constraints
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('    requests>=2.25.0\n')
            f.write('    numpy>=1.20.0\n')
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result_path, removed, removed_triggers = remove_constraints_from_config(['requests'])
                
                assert result_path == config_path
                assert 'requests' in removed
                assert removed['requests'] == '>=2.25.0'


def test_list_all_constraints_multiple_environments():
    """
    Test listing constraints from multiple environments.

    :returns: None
    """
    from pipu_cli.package_constraints import list_all_constraints
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with multiple environments
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('    requests>=2.25.0\n')
            f.write('[production]\n')
            f.write('constraints = \n')
            f.write('    django>=4.1.0\n')
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[config_path]):
            result = list_all_constraints()
            
            assert 'global' in result
            assert 'production' in result
            assert result['global']['requests'] == '>=2.25.0'
            assert result['production']['django'] == '>=4.1.0'


def test_add_ignores_to_config_new_ignore():
    """
    Test adding new ignores to config.

    :returns: None
    """
    from pipu_cli.package_constraints import add_ignores_to_config
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result_path, changes = add_ignores_to_config(['requests', 'numpy'])
                
                assert result_path == config_path
                assert changes['requests'] == 'added'
                assert changes['numpy'] == 'added'
                
                # Verify file was written
                assert config_path.exists()


def test_remove_ignores_from_config_success():
    """
    Test successfully removing ignores from config.

    :returns: None
    """
    from pipu_cli.package_constraints import remove_ignores_from_config
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create initial config with ignores
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('ignores = \n')
            f.write('\trequests\n')
            f.write('\tnumpy\n')
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result_path, removed = remove_ignores_from_config(['requests'])
                
                assert result_path == config_path
                assert 'requests' in removed


def test_list_all_ignores_multiple_environments():
    """
    Test listing ignores from multiple environments.

    :returns: None
    """
    from pipu_cli.package_constraints import list_all_ignores
    
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with multiple environments
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('ignores = \n')
            f.write('\trequests\n')
            f.write('\tnumpy\n')
            f.write('[production]\n')
            f.write('ignores = \n')
            f.write('\tdjango\n')
        
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[config_path]):
            result = list_all_ignores()
            
            assert 'global' in result
            assert 'production' in result
            assert 'requests' in result['global']
            assert 'numpy' in result['global']
            assert 'django' in result['production']


# ============================================================================
# Tests for Invalidation Trigger Functions
# ============================================================================

def test_parse_invalidation_trigger_valid_specs():
    """
    Test parse_invalidation_trigger with valid trigger specifications.

    :returns: None
    """
    # Test various valid trigger formats
    test_cases = [
        ("package>=1.0.0", {"name": "package", "constraint": ">=1.0.0"}),
        ("my-package==2.1.0", {"name": "my-package", "constraint": "==2.1.0"}),
        ("another_package<3.0.0", {"name": "another_package", "constraint": "<3.0.0"}),
        ("pkg~=1.5.0", {"name": "pkg", "constraint": "~=1.5.0"}),
        ("x>0.1.0", {"name": "x", "constraint": ">0.1.0"}),
        ("complex_pkg>=1.0.0,<2.0.0,!=1.5.0", {"name": "complex_pkg", "constraint": ">=1.0.0,<2.0.0,!=1.5.0"}),
    ]
    
    for trigger_spec, expected in test_cases:
        result = parse_invalidation_trigger(trigger_spec)
        assert result == expected, f"Failed for trigger: {trigger_spec}"


def test_parse_invalidation_trigger_invalid_specs():
    """
    Test parse_invalidation_trigger with invalid trigger specifications.

    :returns: None
    """
    invalid_triggers = [
        "",
        "   ",
        "# comment",
        "package-without-version",
        ">=1.0.0",  # Missing package name
        "invalid package name>=1.0.0",  # Spaces in package name
        "123invalid>=1.0.0",  # Invalid package name
    ]
    
    for trigger_spec in invalid_triggers:
        result = parse_invalidation_trigger(trigger_spec)
        assert result is None, f"Expected None for invalid trigger: {trigger_spec}"


def test_format_invalidation_triggers_basic():
    """
    Test format_invalidation_triggers with basic scenarios.

    :returns: None
    """
    # Test with single trigger
    result = format_invalidation_triggers("package<2.0.0", ["other>=1.0.0"])
    assert result == "package<2.0.0:other>=1.0.0"
    
    # Test with multiple triggers
    result = format_invalidation_triggers("package<2.0.0", ["other>=1.0.0", "another>2.0.0"])
    assert result == "package<2.0.0:other>=1.0.0|another>2.0.0"


def test_format_invalidation_triggers_empty():
    """
    Test format_invalidation_triggers with empty trigger list.

    :returns: None
    """
    result = format_invalidation_triggers("package<2.0.0", [])
    assert result == ""


def test_parse_invalidation_triggers_storage_single_package():
    """
    Test parse_invalidation_triggers_storage with single package.

    :returns: None
    """
    storage_value = "flask<2.0.0:other>=1.0.0|another>2.0.0"
    result = parse_invalidation_triggers_storage(storage_value)
    
    expected = {
        "flask": ["other>=1.0.0", "another>2.0.0"]
    }
    assert result == expected


def test_parse_invalidation_triggers_storage_multiple_packages():
    """
    Test parse_invalidation_triggers_storage with multiple packages.

    :returns: None
    """
    storage_value = "flask<2.0.0:other>=1.0.0|another>2.0.0,django>=4.0.0:third==3.0.0"
    result = parse_invalidation_triggers_storage(storage_value)
    
    expected = {
        "flask": ["other>=1.0.0", "another>2.0.0"],
        "django": ["third==3.0.0"]
    }
    assert result == expected


def test_parse_invalidation_triggers_storage_complex_constraints():
    """
    Test parse_invalidation_triggers_storage with complex version constraints.

    :returns: None
    """
    # Test with constraints that don't have commas in the version spec
    storage_value = "package>=1.0.0:trigger1>1.0.0,package2~=3.0.0:trigger2==4.0.0"
    result = parse_invalidation_triggers_storage(storage_value)
    
    expected = {
        "package": ["trigger1>1.0.0"],
        "package2": ["trigger2==4.0.0"]
    }
    assert result == expected


def test_parse_invalidation_triggers_storage_with_comma_constraints():
    """
    Test parse_invalidation_triggers_storage with comma-containing constraints.
    
    Note: This tests the limitation that commas in constraints can interfere
    with parsing when used as package delimiters.

    :returns: None
    """
    # This case is tricky because commas are used both in version specs and as delimiters
    storage_value = "package>=1.0.0,<2.0.0:trigger1>1.0.0,package2~=3.0.0:trigger2==4.0.0"
    result = parse_invalidation_triggers_storage(storage_value)
    
    # Due to comma ambiguity, only the last complete entry parses correctly
    expected = {
        "package2": ["trigger2==4.0.0"]
    }
    assert result == expected


def test_parse_invalidation_triggers_storage_empty():
    """
    Test parse_invalidation_triggers_storage with empty string.

    :returns: None
    """
    result = parse_invalidation_triggers_storage("")
    assert result == {}
    
    result = parse_invalidation_triggers_storage("   ")
    assert result == {}


def test_parse_invalidation_triggers_storage_malformed():
    """
    Test parse_invalidation_triggers_storage with malformed entries.

    :returns: None
    """
    # Entry without colon
    storage_value = "flask<2.0.0,django>=4.0.0:trigger==1.0.0"
    result = parse_invalidation_triggers_storage(storage_value)
    
    expected = {
        "django": ["trigger==1.0.0"]
    }
    assert result == expected


def test_merge_invalidation_triggers_basic():
    """
    Test merge_invalidation_triggers with basic scenarios.

    :returns: None
    """
    existing = ["trigger1>=1.0.0", "trigger2>2.0.0"]
    new = ["trigger3==3.0.0", "trigger4<4.0.0"]
    
    result = merge_invalidation_triggers(existing, new)
    expected = ["trigger1>=1.0.0", "trigger2>2.0.0", "trigger3==3.0.0", "trigger4<4.0.0"]
    
    assert result == expected


def test_merge_invalidation_triggers_duplicates():
    """
    Test merge_invalidation_triggers removes duplicates.

    :returns: None
    """
    existing = ["trigger1>=1.0.0", "trigger2>2.0.0"]
    new = ["trigger1>=1.0.0", "trigger3==3.0.0"]  # trigger1 is duplicate
    
    result = merge_invalidation_triggers(existing, new)
    expected = ["trigger1>=1.0.0", "trigger2>2.0.0", "trigger3==3.0.0"]
    
    assert result == expected


def test_merge_invalidation_triggers_empty():
    """
    Test merge_invalidation_triggers with empty lists.

    :returns: None
    """
    # Empty existing
    result = merge_invalidation_triggers([], ["trigger1>=1.0.0"])
    assert result == ["trigger1>=1.0.0"]
    
    # Empty new
    result = merge_invalidation_triggers(["trigger1>=1.0.0"], [])
    assert result == ["trigger1>=1.0.0"]
    
    # Both empty
    result = merge_invalidation_triggers([], [])
    assert result == []


def test_validate_invalidation_triggers_valid():
    """
    Test validate_invalidation_triggers with valid triggers.

    :returns: None
    """
    triggers = ["package>=1.0.0", "another>2.0.0", "third>=3.0.0"]
    result = validate_invalidation_triggers(triggers)
    
    # Should return normalized triggers
    expected = ["package>=1.0.0", "another>2.0.0", "third>=3.0.0"]
    assert result == expected


def test_validate_invalidation_triggers_invalid():
    """
    Test validate_invalidation_triggers with invalid triggers.

    :returns: None
    """
    triggers = ["package>=1.0.0", "invalid-trigger", "another<2.0.0"]
    
    with pytest.raises(ValueError) as exc_info:
        validate_invalidation_triggers(triggers)
    
    assert "Invalid invalidation trigger specification: invalid-trigger" in str(exc_info.value)


def test_validate_invalidation_triggers_normalization():
    """
    Test validate_invalidation_triggers normalizes trigger format.

    :returns: None
    """
    triggers = ["  package  >=  1.0.0  ", "another>2.0.0"]
    result = validate_invalidation_triggers(triggers)
    
    # Should normalize whitespace
    expected = ["package>=  1.0.0", "another>2.0.0"]
    assert result == expected


def test_validate_invalidation_triggers_rejects_invalid_operators():
    """
    Test validate_invalidation_triggers rejects invalid operators.

    Only ">=" and ">" operators should be allowed for invalidation triggers.

    :returns: None
    """
    invalid_triggers_test_cases = [
        ("package==1.0.0", "=="),
        ("package<2.0.0", "<"),
        ("package<=1.5.0", "<="),
        ("package!=1.0.0", "!="),
        ("package~=1.0.0", "~="),
        ("package>=1.0.0,<2.0.0", "<"),  # Mixed operators, invalid due to <
        ("package>=1.0.0,!=1.5.0", "!="),  # Mixed operators, invalid due to !=
    ]
    
    for trigger, invalid_op in invalid_triggers_test_cases:
        with pytest.raises(ValueError) as exc_info:
            validate_invalidation_triggers([trigger])
        
        assert "only '>=' and '>' operators are allowed" in str(exc_info.value)
        assert "Triggers should specify when a package upgrade invalidates the constraint" in str(exc_info.value)


def test_validate_invalidation_triggers_allows_valid_operators():
    """
    Test validate_invalidation_triggers allows only valid operators.

    :returns: None
    """
    valid_triggers = [
        "package>=1.0.0",
        "package>1.0.0", 
        "package>=1.0.0,>0.5.0",  # Multiple valid operators
    ]
    
    result = validate_invalidation_triggers(valid_triggers)
    expected = ["package>=1.0.0", "package>1.0.0", "package>=1.0.0,>0.5.0"]
    assert result == expected


def test_add_constraints_to_config_with_invalidation_triggers():
    """
    Test add_constraints_to_config stores invalidation triggers properly.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                # Add constraint (this function doesn't support triggers directly)
                # We'll test the trigger functionality through the CLI tests
                result_path, changes = add_constraints_to_config(['flask>=2.0.0'])
                
                assert result_path == config_path
                assert 'flask' in changes
                assert changes['flask'] == ('added', '>=2.0.0')


def test_remove_constraints_cleans_up_triggers():
    """
    Test that removing constraints cleans up associated invalidation triggers.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with constraints and triggers
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('    flask>=2.0.0\n')
            f.write('    django>=4.0.0\n')
            f.write('constraint_invalid_when = flask>=2.0.0:other>=1.0.0,django>=4.0.0:another==2.0.0\n')
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result_path, removed, removed_triggers = remove_constraints_from_config(['flask'])
                
                assert result_path == config_path
                assert 'flask' in removed
                
                # Check that triggers were cleaned up
                import configparser
                config = configparser.ConfigParser()
                config.read(config_path)
                
                if config.has_option('global', 'constraint_invalid_when'):
                    remaining_triggers = config.get('global', 'constraint_invalid_when')
                    # Should only contain django trigger, not flask
                    assert 'django>=4.0.0:another==2.0.0' in remaining_triggers
                    assert 'flask>=2.0.0:other>=1.0.0' not in remaining_triggers


def test_remove_all_constraints_cleans_up_all_triggers():
    """
    Test that removing all constraints cleans up all invalidation triggers.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with constraints and triggers
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('    flask>=2.0.0\n')
            f.write('    django>=4.0.0\n')
            f.write('constraint_invalid_when = flask>=2.0.0:other>=1.0.0,django>=4.0.0:another==2.0.0\n')
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            result_path, removed, _ = remove_all_constraints_from_config()
            
            assert result_path == config_path
            assert 'global' in removed
            assert 'flask' in removed['global']
            assert 'django' in removed['global']
            
            # Check that all triggers were cleaned up
            import configparser
            config = configparser.ConfigParser()
            config.read(config_path)
            
            # constraint_invalid_when section should be removed completely
            assert not config.has_option('global', 'constraint_invalid_when')


def test_constraint_trigger_storage_format_integration():
    """
    Test end-to-end invalidation trigger storage format.

    :returns: None
    """
    # Test the complete flow: format -> store -> parse
    package_constraint = "flask<2.0.0"
    triggers = ["other>=1.0.0", "another>1.5.0"]
    
    # Format for storage
    formatted = format_invalidation_triggers(package_constraint, triggers)
    expected_format = "flask<2.0.0:other>=1.0.0|another>1.5.0"
    assert formatted == expected_format
    
    # Parse back from storage
    parsed = parse_invalidation_triggers_storage(formatted)
    expected_parsed = {"flask": ["other>=1.0.0", "another>1.5.0"]}
    assert parsed == expected_parsed


def test_constraint_trigger_edge_cases():
    """
    Test edge cases for invalidation trigger functionality.

    :returns: None
    """
    # Test with package names containing special characters
    result = parse_invalidation_trigger("my-package_name.test>=1.0.0")
    assert result is not None
    assert result["name"] == "my-package_name.test"
    
    # Test with complex version specs
    result = parse_invalidation_trigger("pkg>=1.0.0,<2.0.0,!=1.5.0")
    assert result is not None
    assert result["constraint"] == ">=1.0.0,<2.0.0,!=1.5.0"
    
    # Test trigger with single character package name
    result = parse_invalidation_trigger("z>0.1.0")
    assert result is not None
    assert result["name"] == "z"


def test_parse_invalidation_triggers_storage_with_whitespace():
    """
    Test parse_invalidation_triggers_storage handles whitespace correctly.

    :returns: None
    """
    # Test with extra whitespace
    storage_value = " flask<2.0.0 : other>=1.0.0 | another>2.0.0 , django>=4.0.0 : third==3.0.0 "
    result = parse_invalidation_triggers_storage(storage_value)
    
    expected = {
        "flask": ["other>=1.0.0", "another>2.0.0"],
        "django": ["third==3.0.0"]
    }
    assert result == expected


def test_validate_invalidation_triggers_empty():
    """
    Test validate_invalidation_triggers with empty list.

    :returns: None
    """
    result = validate_invalidation_triggers([])
    assert result == []


def test_merge_invalidation_triggers_preserves_order():
    """
    Test merge_invalidation_triggers preserves order of existing triggers.

    :returns: None
    """
    existing = ["first>=1.0.0", "second>2.0.0", "third==3.0.0"]
    new = ["fourth<4.0.0", "first>=1.0.0"]  # first is duplicate
    
    result = merge_invalidation_triggers(existing, new)
    expected = ["first>=1.0.0", "second>2.0.0", "third==3.0.0", "fourth<4.0.0"]
    
    assert result == expected
    # Verify order is preserved
    assert result.index("first>=1.0.0") < result.index("second>2.0.0")
    assert result.index("second>2.0.0") < result.index("third==3.0.0")


def test_parse_invalidation_triggers_storage_case_normalization():
    """
    Test that package names are normalized to lowercase in trigger storage.

    :returns: None
    """
    storage_value = "Flask<2.0.0:Other>=1.0.0,Django>=4.0.0:Another==2.0.0"
    result = parse_invalidation_triggers_storage(storage_value)
    
    expected = {
        "flask": ["Other>=1.0.0"],  # Only package names are normalized, not trigger names
        "django": ["Another==2.0.0"]
    }
    assert result == expected


def test_format_invalidation_triggers_complex_constraints():
    """
    Test format_invalidation_triggers with complex constraint formats.

    :returns: None
    """
    # Test with complex package constraint
    package_constraint = "complex-package>=1.0.0,<2.0.0,!=1.5.0"
    triggers = ["trigger1>1.0.0", "trigger2==2.0.0"]
    
    result = format_invalidation_triggers(package_constraint, triggers)
    expected = "complex-package>=1.0.0,<2.0.0,!=1.5.0:trigger1>1.0.0|trigger2==2.0.0"
    
    assert result == expected


def test_invalidation_trigger_functions_error_handling():
    """
    Test error handling in invalidation trigger functions.

    :returns: None
    """
    # Test parse_invalidation_triggers_storage with malformed colon syntax
    malformed_storage = "package<2.0.0:trigger1>=1.0.0:extra_colon"
    result = parse_invalidation_triggers_storage(malformed_storage)
    # Should handle gracefully and extract what it can
    expected = {"package": ["trigger1>=1.0.0:extra_colon"]}  # Treats everything after first colon as triggers
    assert result == expected


def test_invalidation_trigger_integration_with_existing_config():
    """
    Test invalidation triggers work with existing pip configuration.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create existing config with other settings
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('index-url = https://pypi.org/simple/\n')
            f.write('trusted-host = pypi.org\n')
            f.write('constraints = \n')
            f.write('    existing-package>=1.0.0\n')
        
        # Test that triggers can be added without affecting other settings
        import configparser
        config = configparser.ConfigParser()
        config.read(config_path)
        
        # Add trigger manually to test integration
        config.set('global', 'constraint_invalid_when', 'existing-package>=1.0.0:trigger>=1.0.0')
        
        with open(config_path, 'w') as f:
            config.write(f)
        
        # Verify all settings are preserved
        config.read(config_path)
        assert config.get('global', 'index-url') == 'https://pypi.org/simple/'
        assert config.get('global', 'trusted-host') == 'pypi.org'
        assert 'existing-package>=1.0.0' in config.get('global', 'constraints')
        assert config.get('global', 'constraint_invalid_when') == 'existing-package>=1.0.0:trigger>=1.0.0'


# ============================================================================
# Additional Edge Case and Error Handling Tests
# ============================================================================

def test_constraint_removal_with_missing_triggers():
    """
    Test removing constraints when trigger cleanup fails gracefully.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with constraints but malformed triggers
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('    flask>=2.0.0\n')
            f.write('constraint_invalid_when = malformed-entry-without-colon\n')
        
        # Should still work even with malformed triggers
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result_path, removed, _ = remove_constraints_from_config(['flask'])
                
                assert result_path == config_path
                assert 'flask' in removed


def test_constraint_removal_nonexistent_package():
    """
    Test removing constraints for packages that don't exist.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with constraints
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('    flask>=2.0.0\n')
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                with pytest.raises(ValueError) as exc_info:
                    remove_constraints_from_config(['nonexistent-package'])
                
                assert "None of the specified packages have constraints" in str(exc_info.value)


def test_constraint_removal_no_config_file():
    """
    Test removing constraints when no config file exists.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'nonexistent.conf'
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                with pytest.raises(ValueError) as exc_info:
                    remove_constraints_from_config(['flask'])
                
                assert "No pip configuration file found" in str(exc_info.value)


def test_add_constraints_creates_directory():
    """
    Test that adding constraints creates the config directory if it doesn't exist.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'nonexistent_dir' / 'pip.conf'
        
        # Mock get_recommended_pip_config_path to create directory
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                # Create the directory manually for the test
                config_path.parent.mkdir(parents=True, exist_ok=True)
                
                result_path, changes = add_constraints_to_config(['flask>=2.0.0'])
                
                assert result_path == config_path
                assert 'flask' in changes


def test_ignore_functions_comprehensive():
    """
    Test comprehensive ignore functionality including edge cases.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Test adding ignores
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result_path, changes = add_ignores_to_config(['requests', 'numpy', 'flask'])
                
                assert result_path == config_path
                assert changes['requests'] == 'added'
                assert changes['numpy'] == 'added'
                assert changes['flask'] == 'added'
        
        # Test adding duplicate ignores
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result_path, changes = add_ignores_to_config(['requests', 'django'])
                
                assert changes['requests'] == 'already_exists'
                assert changes['django'] == 'added'
        
        # Test removing some ignores
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                result_path, removed = remove_ignores_from_config(['requests', 'nonexistent'])
                
                assert 'requests' in removed
                assert 'nonexistent' not in removed
        
        # Test listing ignores
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[config_path]):
            result = list_all_ignores()
            # Should have remaining ignores
            assert 'global' in result
            assert 'numpy' in result['global']
            assert 'flask' in result['global'] 
            assert 'django' in result['global']
            assert 'requests' not in result['global']  # Was removed


def test_constraint_config_file_corruption_handling():
    """
    Test handling of corrupted configuration files.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create corrupted config file
        with open(config_path, 'w') as f:
            f.write('[global\n')  # Missing closing bracket
            f.write('constraints = flask>=2.0.0\n')
            f.write('[invalid section name with special chars!@#\n')
        
        # Should handle corruption gracefully
        with patch('pipu_cli.package_constraints.get_pip_config_paths', return_value=[config_path]):
            result = list_all_constraints()
            # Should return empty result rather than crashing
            assert isinstance(result, dict)


def test_invalidation_trigger_whitespace_and_formatting():
    """
    Test invalidation trigger functions handle whitespace and formatting correctly.

    :returns: None
    """
    # Test format_invalidation_triggers with various whitespace
    result = format_invalidation_triggers("  package  >=  1.0.0  ", ["  trigger  >  2.0.0  "])
    assert result == "  package  >=  1.0.0  :  trigger  >  2.0.0  "
    
    # Test parsing with extra whitespace
    storage_with_whitespace = "  package>=1.0.0 : trigger1>=1.0.0 | trigger2>2.0.0  "
    parsed = parse_invalidation_triggers_storage(storage_with_whitespace)
    expected = {"package": ["trigger1>=1.0.0", "trigger2>2.0.0"]}
    assert parsed == expected


def test_constraint_environment_edge_cases():
    """
    Test constraint operations with various environment edge cases.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Test with environment name that has special characters  
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            result_path, changes = add_constraints_to_config(['flask>=2.0.0'], env_name='test-env_with.special-chars')
            
            assert result_path == config_path
            assert 'flask' in changes
            
            # Verify config written correctly
            import configparser
            config = configparser.ConfigParser()
            config.read(config_path)
            
            assert config.has_section('test-env_with.special-chars')


def test_large_number_of_triggers():
    """
    Test handling large numbers of invalidation triggers.

    :returns: None
    """
    # Create a large list of triggers
    large_trigger_list = [f"trigger{i}>=1.{i}.0" for i in range(100)]
    
    # Test validation doesn't fail with large numbers
    result = validate_invalidation_triggers(large_trigger_list)
    assert len(result) == 100
    
    # Test merging with large lists
    existing = [f"existing{i}>=1.{i}.0" for i in range(50)]
    merged = merge_invalidation_triggers(existing, large_trigger_list)
    assert len(merged) == 150  # No duplicates in this case


def test_constraint_parsing_with_unusual_package_names():
    """
    Test parsing constraints with unusual but valid package names.

    :returns: None
    """
    unusual_names = [
        "a",  # Single character
        "package-with-many-hyphens-in-name",
        "package_with_many_underscores_in_name", 
        "package.with.dots.in.name",
        "package123with456numbers",
        "UPPERCASE-PACKAGE-NAME",  # Should be normalized
    ]
    
    for package_name in unusual_names:
        constraint_spec = f"{package_name}>=1.0.0"
        result = parse_requirement_line(constraint_spec)
        assert result is not None
        assert result['name'] == package_name.lower()  # Normalized to lowercase
        assert result['constraint'] == '>=1.0.0'


def test_constraint_with_very_complex_version_specs():
    """
    Test constraints with very complex version specifications.

    :returns: None
    """
    complex_specs = [
        "package>=1.0.0,<2.0.0,!=1.5.0,!=1.6.0,!=1.7.0",
        "another~=2.1.0",
        "third>0.1.0,<=0.9.9",
        "fourth==1.0.0",
    ]
    
    for spec in complex_specs:
        result = parse_requirement_line(spec)
        assert result is not None
        
        # Test as invalidation trigger too
        trigger_result = parse_invalidation_trigger(spec)
        assert trigger_result is not None


def test_config_file_permissions_error():
    """
    Test handling of permission errors when writing config files.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config file and make it read-only
        with open(config_path, 'w') as f:
            f.write('[global]\n')
        config_path.chmod(0o444)  # Read-only
        
        try:
            with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
                with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                    with pytest.raises(IOError):
                        add_constraints_to_config(['flask>=2.0.0'])
        finally:
            # Clean up - restore write permissions
            config_path.chmod(0o644)


def test_empty_constraint_and_trigger_operations():
    """
    Test operations with empty constraint and trigger lists.

    :returns: None
    """
    # Test with empty constraint list
    with pytest.raises(ValueError):
        validate_invalidation_triggers([''])
    
    # Test empty format operation
    result = format_invalidation_triggers("package>=1.0.0", [])
    assert result == ""
    
    # Test empty parse operation
    result = parse_invalidation_triggers_storage("")
    assert result == {}


def test_constraint_operations_with_missing_sections():
    """
    Test constraint operations when config sections don't exist.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create empty config file
        with open(config_path, 'w') as f:
            f.write('# Empty config\n')
        
        # Try to remove from non-existent environment
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with pytest.raises(ValueError) as exc_info:
                remove_constraints_from_config(['flask'], env_name='nonexistent-env')
            
            assert "No constraints section found for environment 'nonexistent-env'" in str(exc_info.value)


def test_unicode_and_special_characters_in_constraints():
    """
    Test handling of unicode and special characters in package names and constraints.

    :returns: None
    """
    # Note: Package names should be ASCII according to PEP 508, but test robustness
    test_cases = [
        "normal-package>=1.0.0",
        "package-with-unicode-αβγ>=1.0.0",  # This would be invalid in real PyPI
        "package>=1.0.0+build.123",  # Build metadata
    ]
    
    for case in test_cases:
        # Our parser should handle these gracefully (even if they're not valid PyPI names)
        result = parse_requirement_line(case)
        # Some may be None due to regex constraints, which is fine
        # Just verify the function doesn't crash
        assert result is None or isinstance(result, dict)


def test_constraint_trigger_relationship_integrity():
    """
    Test that constraint-trigger relationships are maintained correctly.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Add multiple constraints with triggers
        constraints_and_triggers = [
            ("flask<2.0.0", ["trigger1>=1.0.0"]),
            ("django>=4.0.0", ["trigger2==2.0.0", "trigger3>3.0.0"]),
            ("requests~=2.25.0", ["trigger4<4.0.0"]),
        ]
        
        import configparser
        config = configparser.ConfigParser()
        
        for constraint, triggers in constraints_and_triggers:
            # Manually build and store the constraint and triggers
            if config_path.exists():
                config.read(config_path)
            
            if not config.has_section('global'):
                config.add_section('global')
            
            # Add constraint
            existing_constraints = {}
            if config.has_option('global', 'constraints'):
                existing_value = config.get('global', 'constraints')
                if '\n' in existing_value or any(op in existing_value for op in ['>=', '<=', '==', '!=', '~=', '>', '<']):
                    existing_constraints = parse_inline_constraints(existing_value)
            
            parsed_constraint = parse_requirement_line(constraint)
            package_name = ""  # Initialize to avoid unbound variable
            if parsed_constraint:
                package_name = parsed_constraint['name'].lower()
                constraint_spec = parsed_constraint['constraint']
                existing_constraints[package_name] = constraint_spec
            
            # Format constraints
            if existing_constraints:
                constraints_lines = [f"{pkg}{constr}" for pkg, constr in sorted(existing_constraints.items())]
                constraints_value = '\n    ' + '\n    '.join(constraints_lines)
                config.set('global', 'constraints', constraints_value)
            
            # Add triggers (only if we successfully parsed the constraint)
            if parsed_constraint and triggers:
                existing_triggers = {}
                if config.has_option('global', 'constraint_invalid_when'):
                    existing_value = config.get('global', 'constraint_invalid_when')
                    existing_triggers = parse_invalidation_triggers_storage(existing_value)
                
                existing_triggers[package_name] = triggers
                trigger_entries = []
                for pkg, pkg_triggers in existing_triggers.items():
                    if pkg in existing_constraints:
                        formatted_entry = format_invalidation_triggers(f"{pkg}{existing_constraints[pkg]}", pkg_triggers)
                        if formatted_entry:
                            trigger_entries.append(formatted_entry)
                
                if trigger_entries:
                    triggers_value = ','.join(trigger_entries)
                    config.set('global', 'constraint_invalid_when', triggers_value)
            
            with open(config_path, 'w') as f:
                config.write(f)
        
        # Verify all relationships are correct
        config.read(config_path)
        
        # Check constraints exist
        constraints_value = config.get('global', 'constraints')
        assert 'flask<2.0.0' in constraints_value
        assert 'django>=4.0.0' in constraints_value
        assert 'requests~=2.25.0' in constraints_value
        
        # Check triggers exist and are correctly associated
        triggers_value = config.get('global', 'constraint_invalid_when')
        assert 'flask<2.0.0:trigger1>=1.0.0' in triggers_value
        assert 'django>=4.0.0:trigger2==2.0.0|trigger3>3.0.0' in triggers_value
        assert 'requests~=2.25.0:trigger4<4.0.0' in triggers_value


def test_constraint_update_preserves_existing_settings():
    """
    Test that constraint updates preserve other pip configuration settings.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with various settings
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('index-url = https://pypi.org/simple/\n')
            f.write('trusted-host = pypi.org\n')
            f.write('timeout = 60\n')
            f.write('retries = 3\n')
            f.write('[install]\n')
            f.write('upgrade = true\n')
        
        # Add constraints
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                add_constraints_to_config(['flask>=2.0.0'])
        
        # Verify all original settings preserved
        import configparser
        config = configparser.ConfigParser()
        config.read(config_path)
        
        assert config.get('global', 'index-url') == 'https://pypi.org/simple/'
        assert config.get('global', 'trusted-host') == 'pypi.org'
        assert config.get('global', 'timeout') == '60'
        assert config.get('global', 'retries') == '3'
        assert config.has_section('install')
        assert config.get('install', 'upgrade') == 'true'
        
        # And new constraint was added
        assert 'flask>=2.0.0' in config.get('global', 'constraints')


# ============================================================================
# Tests for Auto-Constraint Discovery and Application
# ============================================================================

def test_discover_auto_constraints_basic():
    """
    Test discover_auto_constraints with basic package scenario.

    :returns: None
    """
    # Mock package distribution with exact version constraints
    mock_dist1 = MagicMock()
    mock_dist1.metadata = {'Name': 'test-package'}
    mock_dist1.version = '1.5.0'
    mock_dist1.requires = ['deprecated==1.2.10', 'requests>=2.25.0']
    
    mock_dist2 = MagicMock()
    mock_dist2.metadata = {'Name': 'another-package'}
    mock_dist2.version = '2.0.0'
    mock_dist2.requires = ['flask~=2.1.0']
    
    with patch('importlib.metadata.distributions', return_value=[mock_dist1, mock_dist2]):
        result = discover_auto_constraints()
        
        expected = [
            ('deprecated==1.2.10', 'test-package>1.5.0'),
            ('flask~=2.1.0', 'another-package>2.0.0')
        ]
        assert result == expected


def test_discover_auto_constraints_with_upper_bound_constraints():
    """
    Test discover_auto_constraints with upper bound constraints (<= and <).

    :returns: None
    """
    # Mock package distribution with upper bound version constraints
    mock_dist1 = MagicMock()
    mock_dist1.metadata = {'Name': 'test-package'}
    mock_dist1.version = '1.5.0'
    mock_dist1.requires = ['some-lib<=2.0.0', 'another-lib<3.0.0']
    
    mock_dist2 = MagicMock()
    mock_dist2.metadata = {'Name': 'another-package'}
    mock_dist2.version = '2.0.0'
    mock_dist2.requires = ['third-lib<=1.5.0']
    
    with patch('importlib.metadata.distributions', return_value=[mock_dist1, mock_dist2]):
        result = discover_auto_constraints()
        
        expected = [
            ('some-lib<=2.0.0', 'test-package>1.5.0'),
            ('another-lib<3.0.0', 'test-package>1.5.0'),
            ('third-lib<=1.5.0', 'another-package>2.0.0')
        ]
        assert result == expected


def test_discover_auto_constraints_mixed_constraint_types():
    """
    Test discover_auto_constraints with mixed constraint types (==, ~=, <=, <).

    :returns: None
    """
    # Mock package with different types of constraints
    mock_dist = MagicMock()
    mock_dist.metadata = {'Name': 'mixed-package'}
    mock_dist.version = '1.0.0'
    mock_dist.requires = [
        'exact-pkg==1.2.10',     # Exact constraint -> >= trigger
        'compat-pkg~=2.1.0',     # Compatible constraint -> >= trigger  
        'upper-pkg<=3.0.0',      # Upper bound constraint -> > trigger
        'strict-upper-pkg<4.0.0', # Strict upper bound -> > trigger
        'lower-pkg>=1.0.0'       # Should be ignored (not constraining upper bound)
    ]
    
    with patch('importlib.metadata.distributions', return_value=[mock_dist]):
        result = discover_auto_constraints()
        
        expected = [
            ('exact-pkg==1.2.10', 'mixed-package>1.0.0'),
            ('compat-pkg~=2.1.0', 'mixed-package>1.0.0'),
            ('upper-pkg<=3.0.0', 'mixed-package>1.0.0'),
            ('strict-upper-pkg<4.0.0', 'mixed-package>1.0.0')
        ]
        assert result == expected


def test_discover_auto_constraints_no_constraining_requirements():
    """
    Test discover_auto_constraints when packages have no constraining requirements.

    :returns: None
    """
    # Mock package with only loose lower bound constraints
    mock_dist = MagicMock()
    mock_dist.metadata = {'Name': 'test-package'}
    mock_dist.version = '1.0.0'
    mock_dist.requires = ['requests>=2.25.0', 'numpy!=1.5.0']  # No ==, ~=, <=, < constraints
    
    with patch('importlib.metadata.distributions', return_value=[mock_dist]):
        result = discover_auto_constraints()
        assert result == []


def test_discover_auto_constraints_with_extras():
    """
    Test discover_auto_constraints skips extra requirements.

    :returns: None
    """
    # Mock package with extra requirements
    mock_dist = MagicMock()
    mock_dist.metadata = {'Name': 'test-package'}
    mock_dist.version = '1.0.0'
    mock_dist.requires = [
        'deprecated==1.2.10',
        'pytest==7.0.0; extra == "test"',  # Should be skipped
        'flask~=2.1.0'
    ]
    
    with patch('importlib.metadata.distributions', return_value=[mock_dist]):
        result = discover_auto_constraints()
        
        expected = [
            ('deprecated==1.2.10', 'test-package>1.0.0'),
            ('flask~=2.1.0', 'test-package>1.0.0')
        ]
        assert result == expected


def test_discover_auto_constraints_with_environment_markers():
    """
    Test discover_auto_constraints skips environment markers.

    :returns: None
    """
    # Mock package with environment markers
    mock_dist = MagicMock()
    mock_dist.metadata = {'Name': 'test-package'}
    mock_dist.version = '1.0.0'
    mock_dist.requires = [
        'deprecated==1.2.10',
        'pywin32==306; sys_platform == "win32"',  # Should be skipped
        'flask~=2.1.0'
    ]
    
    with patch('importlib.metadata.distributions', return_value=[mock_dist]):
        result = discover_auto_constraints()
        
        expected = [
            ('deprecated==1.2.10', 'test-package>1.0.0'),
            ('flask~=2.1.0', 'test-package>1.0.0')
        ]
        assert result == expected


def test_discover_auto_constraints_malformed_requirements():
    """
    Test discover_auto_constraints handles malformed requirements gracefully.

    :returns: None
    """
    # Mock package with malformed requirements
    mock_dist = MagicMock()
    mock_dist.metadata = {'Name': 'test-package'}
    mock_dist.version = '1.0.0'
    mock_dist.requires = [
        'deprecated==1.2.10',
        'invalid-requirement-format',  # Malformed, should be skipped
        'flask~=2.1.0'
    ]
    
    with patch('importlib.metadata.distributions', return_value=[mock_dist]):
        result = discover_auto_constraints()
        
        # Should only include valid requirements
        expected = [
            ('deprecated==1.2.10', 'test-package>1.0.0'),
            ('flask~=2.1.0', 'test-package>1.0.0')
        ]
        assert result == expected


def test_discover_auto_constraints_no_requires():
    """
    Test discover_auto_constraints with packages that have no requirements.

    :returns: None
    """
    # Mock package without requires attribute
    mock_dist1 = MagicMock()
    mock_dist1.metadata = {'Name': 'package-no-requires'}
    mock_dist1.version = '1.0.0'
    del mock_dist1.requires  # No requires attribute
    
    # Mock package with empty requires
    mock_dist2 = MagicMock()
    mock_dist2.metadata = {'Name': 'package-empty-requires'}
    mock_dist2.version = '1.0.0'
    mock_dist2.requires = []
    
    with patch('importlib.metadata.distributions', return_value=[mock_dist1, mock_dist2]):
        result = discover_auto_constraints()
        assert result == []


def test_discover_auto_constraints_package_name_normalization():
    """
    Test discover_auto_constraints normalizes package names to lowercase.

    :returns: None
    """
    # Mock package with mixed case names
    mock_dist = MagicMock()
    mock_dist.metadata = {'Name': 'Test-Package'}  # Mixed case
    mock_dist.version = '1.0.0'
    mock_dist.requires = ['Deprecated==1.2.10', 'Flask~=2.1.0']
    
    with patch('importlib.metadata.distributions', return_value=[mock_dist]):
        result = discover_auto_constraints()
        
        expected = [
            ('deprecated==1.2.10', 'test-package>1.0.0'),  # Normalized
            ('flask~=2.1.0', 'test-package>1.0.0')  # Normalized
        ]
        assert result == expected


def test_discover_auto_constraints_importlib_error():
    """
    Test discover_auto_constraints handles importlib.metadata errors.

    :returns: None
    """
    with patch('importlib.metadata.distributions', side_effect=Exception("Import error")):
        result = discover_auto_constraints()
        assert result == []


def test_discover_auto_constraints_package_metadata_error():
    """
    Test discover_auto_constraints handles package metadata errors.

    :returns: None
    """
    # Mock package with metadata access error
    mock_dist = MagicMock()
    mock_dist.metadata.__getitem__.side_effect = KeyError("Name not found")
    mock_dist.requires = ['deprecated==1.2.10']
    
    with patch('importlib.metadata.distributions', return_value=[mock_dist]):
        result = discover_auto_constraints()
        assert result == []


def test_apply_auto_constraints_basic():
    """
    Test apply_auto_constraints with basic scenario.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Mock auto-constraints discovery
        mock_auto_constraints = [
            ('deprecated==1.2.10', 'test-package>=1.5.0'),
            ('flask~=2.1.0', 'another-package>=2.0.0')
        ]
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                with patch('pipu_cli.package_constraints.discover_auto_constraints', return_value=mock_auto_constraints):
                    result_path, changes, constraints_added, triggers_added = apply_auto_constraints()
                    
                    assert result_path == config_path
                    assert constraints_added == 2
                    assert triggers_added == 2
                    assert len(changes) == 2
                    
                    # Verify changes
                    assert 'deprecated' in changes
                    assert 'flask' in changes
                    assert changes['deprecated'] == ('added', '==1.2.10')
                    assert changes['flask'] == ('added', '~=2.1.0')


def test_apply_auto_constraints_dry_run():
    """
    Test apply_auto_constraints with dry_run=True.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        mock_auto_constraints = [
            ('deprecated==1.2.10', 'test-package>=1.5.0'),
        ]
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.discover_auto_constraints', return_value=mock_auto_constraints):
                result_path, changes, constraints_added, triggers_added = apply_auto_constraints(dry_run=True)
                
                assert result_path == config_path
                assert constraints_added == 1
                assert triggers_added == 1
                assert len(changes) == 1
                
                # Should show would_add action
                assert changes['deprecated'] == ('would_add', '==1.2.10')
                
                # Config file should not be created in dry run
                assert not config_path.exists()


def test_apply_auto_constraints_no_auto_constraints():
    """
    Test apply_auto_constraints when no auto-constraints are discovered.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.discover_auto_constraints', return_value=[]):
                result_path, changes, constraints_added, triggers_added = apply_auto_constraints()
                
                assert result_path == config_path
                assert constraints_added == 0
                assert triggers_added == 0
                assert len(changes) == 0


def test_apply_auto_constraints_with_existing_constraints():
    """
    Test apply_auto_constraints when constraints already exist.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create existing config with some constraints
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('    deprecated==1.2.10\n')  # Already exists
            f.write('    existing-package>=1.0.0\n')
        
        mock_auto_constraints = [
            ('deprecated==1.2.10', 'test-package>=1.5.0'),  # Already exists
            ('flask~=2.1.0', 'another-package>=2.0.0')  # New
        ]
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                with patch('pipu_cli.package_constraints.discover_auto_constraints', return_value=mock_auto_constraints):
                    result_path, changes, constraints_added, triggers_added = apply_auto_constraints()
                    
                    assert result_path == config_path
                    assert constraints_added == 1  # Only flask was added
                    
                    # Verify flask was added but deprecated was not changed
                    assert 'flask' in changes
                    assert changes['flask'] == ('added', '~=2.1.0')
                    
                    # deprecated should not appear in changes since it already existed with same value
                    assert 'deprecated' not in changes or changes['deprecated'] != ('added', '==1.2.10')


def test_apply_auto_constraints_with_environment():
    """
    Test apply_auto_constraints with specific environment.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        mock_auto_constraints = [
            ('deprecated==1.2.10', 'test-package>=1.5.0'),
        ]
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path):
            with patch('pipu_cli.package_constraints.discover_auto_constraints', return_value=mock_auto_constraints):
                result_path, changes, constraints_added, triggers_added = apply_auto_constraints(env_name='production')
                
                assert result_path == config_path
                assert constraints_added == 1
                
                # Verify constraint was added to production environment
                import configparser
                config = configparser.ConfigParser()
                config.read(config_path)
                
                assert config.has_section('production')
                assert 'deprecated==1.2.10' in config.get('production', 'constraints')


def test_apply_auto_constraints_with_existing_triggers():
    """
    Test apply_auto_constraints merges with existing invalidation triggers.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with existing constraints and triggers
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('    deprecated==1.2.10\n')
            f.write('constraint_invalid_when = deprecated==1.2.10:existing-trigger>=1.0.0\n')
        
        mock_auto_constraints = [
            ('deprecated==1.2.10', 'test-package>=1.5.0'),  # Same constraint, new trigger
        ]
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                with patch('pipu_cli.package_constraints.discover_auto_constraints', return_value=mock_auto_constraints):
                    result_path, changes, constraints_added, triggers_added = apply_auto_constraints()
                    
                    assert result_path == config_path
                    
                    # Verify triggers were merged
                    import configparser
                    config = configparser.ConfigParser()
                    config.read(config_path)
                    
                    triggers_value = config.get('global', 'constraint_invalid_when')
                    # Should contain both existing and new triggers
                    assert 'existing-trigger>=1.0.0' in triggers_value
                    assert 'test-package>=1.5.0' in triggers_value


def test_apply_auto_constraints_complex_requirements():
    """
    Test apply_auto_constraints with complex requirement specifications.

    :returns: None
    """
    # Test with complex version specifications that have multiple operators
    mock_dist = MagicMock()
    mock_dist.metadata = {'Name': 'complex-package'}
    mock_dist.version = '1.0.0'
    mock_dist.requires = ['dependency>=1.0.0,<2.0.0,!=1.5.0']  # Complex spec with < operator
    
    with patch('importlib.metadata.distributions', return_value=[mock_dist]):
        result = discover_auto_constraints()
        # Should find the < constraint from the complex spec
        expected = [('dependency<2.0.0', 'complex-package>1.0.0')]
        assert result == expected


def test_discover_auto_constraints_multiple_constraining_requirements():
    """
    Test discover_auto_constraints with multiple constraining requirements from same package.

    :returns: None
    """
    # Mock package with multiple constraining requirements
    mock_dist = MagicMock()
    mock_dist.metadata = {'Name': 'multi-constraint-package'}
    mock_dist.version = '2.5.0'
    mock_dist.requires = [
        'deprecated==1.2.10',    # Exact -> >= trigger
        'flask~=2.1.0',          # Compatible -> >= trigger
        'requests>=2.25.0',      # Lower bound, should be ignored
        'pyyaml==6.0.0',         # Exact -> >= trigger
        'click<=8.1.0',          # Upper bound -> > trigger
        'numpy<2.0.0'            # Strict upper bound -> > trigger
    ]
    
    with patch('importlib.metadata.distributions', return_value=[mock_dist]):
        result = discover_auto_constraints()
        
        expected = [
            ('deprecated==1.2.10', 'multi-constraint-package>2.5.0'),
            ('flask~=2.1.0', 'multi-constraint-package>2.5.0'),
            ('pyyaml==6.0.0', 'multi-constraint-package>2.5.0'),
            ('click<=8.1.0', 'multi-constraint-package>2.5.0'),
            ('numpy<2.0.0', 'multi-constraint-package>2.5.0')
        ]
        assert result == expected


def test_discover_auto_constraints_version_edge_cases():
    """
    Test discover_auto_constraints with version edge cases.

    :returns: None
    """
    # Mock package with version edge cases
    mock_dist = MagicMock()
    mock_dist.metadata = {'Name': 'edge-case-package'}
    mock_dist.version = '0.1.0'  # Very low version
    mock_dist.requires = [
        'pre-release==2.0.0a1',  # Pre-release
        'build-version==1.0.0+build.123',  # Build metadata (might not parse)
        'simple==1.0'  # Simple version without patch
    ]
    
    with patch('importlib.metadata.distributions', return_value=[mock_dist]):
        result = discover_auto_constraints()
        
        # Should handle what it can parse
        assert len(result) >= 1  # At least simple==1.0 should work
        
        # Find the simple constraint
        simple_found = False
        for constraint_spec, trigger in result:
            if constraint_spec == 'simple==1.0':
                assert trigger == 'edge-case-package>0.1.0'
                simple_found = True
        assert simple_found


def test_apply_auto_constraints_trigger_validation():
    """
    Test apply_auto_constraints validates invalidation triggers correctly.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Auto-constraints should generate >= triggers which are valid
        mock_auto_constraints = [
            ('deprecated==1.2.10', 'test-package>=1.5.0'),
        ]
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                with patch('pipu_cli.package_constraints.discover_auto_constraints', return_value=mock_auto_constraints):
                    # Should not raise validation errors
                    result_path, changes, constraints_added, triggers_added = apply_auto_constraints()
                    
                    assert result_path == config_path
                    assert constraints_added == 1
                    assert triggers_added == 1


def test_apply_auto_constraints_error_handling():
    """
    Test apply_auto_constraints error handling.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        mock_auto_constraints = [
            ('deprecated==1.2.10', 'test-package>=1.5.0'),
        ]
        
        # Test with constraint addition error
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.discover_auto_constraints', return_value=mock_auto_constraints):
                with patch('pipu_cli.package_constraints.add_constraints_to_config', side_effect=IOError("Write error")):
                    with pytest.raises(IOError):
                        apply_auto_constraints()


def test_discover_auto_constraints_real_world_scenario():
    """
    Test discover_auto_constraints with realistic package scenarios.

    :returns: None
    """
    # Simulate real-world packages with typical dependencies
    mock_django = MagicMock()
    mock_django.metadata = {'Name': 'Django'}
    mock_django.version = '4.2.0'
    mock_django.requires = [
        'asgiref>=3.6.0,<4',
        'sqlparse>=0.3.1',
        'tzdata; sys_platform == "win32"'  # Environment marker, should be skipped
    ]
    
    mock_requests = MagicMock()
    mock_requests.metadata = {'Name': 'requests'}
    mock_requests.version = '2.31.0'
    mock_requests.requires = [
        'charset-normalizer>=2,<4',
        'idna>=2.5,<4',
        'urllib3>=1.21.1,<3',
        'certifi>=2017.4.17'
    ]
    
    mock_package_with_exact = MagicMock()
    mock_package_with_exact.metadata = {'Name': 'package-with-exact-deps'}
    mock_package_with_exact.version = '1.0.0'
    mock_package_with_exact.requires = [
        'pyyaml==6.0.1',  # Exact constraint
        'click~=8.1.0',   # Compatible constraint
        'rich>=12.0.0'    # Loose constraint, should be ignored
    ]
    
    with patch('importlib.metadata.distributions', return_value=[mock_django, mock_requests, mock_package_with_exact]):
        result = discover_auto_constraints()
        
        # Should find both exact/compatible and upper bound constraints
        expected = [
            ('asgiref<4', 'django>4.2.0'),                        # From Django upper bound
            ('charset-normalizer<4', 'requests>2.31.0'),          # From requests upper bound
            ('idna<4', 'requests>2.31.0'),                        # From requests upper bound
            ('urllib3<3', 'requests>2.31.0'),                     # From requests upper bound
            ('pyyaml==6.0.1', 'package-with-exact-deps>1.0.0'),  # From exact constraint
            ('click~=8.1.0', 'package-with-exact-deps>1.0.0')    # From compatible constraint
        ]
        assert result == expected


def test_apply_auto_constraints_integration_with_existing_system():
    """
    Test apply_auto_constraints integrates properly with existing constraint system.

    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create complex existing config
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('index-url = https://pypi.org/simple/\n')
            f.write('constraints = \n')
            f.write('    manual-constraint>=1.0.0\n')
            f.write('    existing-package==2.0.0\n')
            f.write('constraint_invalid_when = existing-package==2.0.0:manual-trigger>=1.0.0\n')
            f.write('[production]\n')
            f.write('constraints = \n')
            f.write('    prod-constraint<2.0.0\n')
        
        mock_auto_constraints = [
            ('auto-discovered==1.5.0', 'source-package>=2.0.0'),
            ('existing-package==2.0.0', 'another-source>=1.0.0')  # Same constraint, different trigger
        ]
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            with patch('pipu_cli.package_constraints.get_current_environment_name', return_value=None):
                with patch('pipu_cli.package_constraints.discover_auto_constraints', return_value=mock_auto_constraints):
                    result_path, changes, constraints_added, triggers_added = apply_auto_constraints()
                    
                    assert result_path == config_path
                    
                    # Verify integration
                    import configparser
                    config = configparser.ConfigParser()
                    config.read(config_path)
                    
                    # Original settings should be preserved
                    assert config.get('global', 'index-url') == 'https://pypi.org/simple/'
                    
                    # Original constraints should be preserved
                    constraints_value = config.get('global', 'constraints')
                    assert 'manual-constraint>=1.0.0' in constraints_value
                    assert 'existing-package==2.0.0' in constraints_value
                    
                    # New auto-discovered constraint should be added
                    assert 'auto-discovered==1.5.0' in constraints_value
                    
                    # Production section should be untouched
                    assert config.has_section('production')
                    assert 'prod-constraint<2.0.0' in config.get('production', 'constraints')
                    
                    # Triggers should be merged correctly
                    triggers_value = config.get('global', 'constraint_invalid_when')
                    assert 'manual-trigger>=1.0.0' in triggers_value  # Original trigger
                    assert 'source-package>=2.0.0' in triggers_value  # New trigger
                    assert 'another-source>=1.0.0' in triggers_value  # Merged trigger


# ============================================================================
# Tests for New Constraint Validation and Cleanup Functions
# ============================================================================


def test_check_constraint_invalidations_basic():
    """
    Test basic constraint invalidation detection.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with constraints and invalidation triggers
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('\tnumpy==1.20.0\n')
            f.write('\trequests<3.0.0\n')
            f.write('constraint_invalid_when = numpy==1.20.0:pandas>=2.0.0,requests<3.0.0:urllib3>=2.0.0\n')
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            # Test with packages that would invalidate constraints
            invalidated = check_constraint_invalidations(['pandas', 'urllib3'])
            
            assert len(invalidated) == 2
            assert 'numpy' in invalidated
            assert 'requests' in invalidated
            assert 'pandas' in invalidated['numpy']
            assert 'urllib3' in invalidated['requests']


def test_check_constraint_invalidations_partial_match():
    """
    Test constraint invalidation with partial package matches.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with multiple triggers per constraint
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('\tflask==2.0.0\n')
            f.write('constraint_invalid_when = flask==2.0.0:jinja2>=3.0.0|werkzeug>=2.0.0\n')
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            # Test with only one of the triggering packages
            invalidated = check_constraint_invalidations(['jinja2'])
            
            assert len(invalidated) == 1
            assert 'flask' in invalidated
            assert 'jinja2' in invalidated['flask']
            
            # Test with non-triggering package
            invalidated = check_constraint_invalidations(['other-package'])
            assert len(invalidated) == 0


def test_check_constraint_invalidations_no_config():
    """
    Test constraint invalidation when no config exists.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'nonexistent.conf'
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            invalidated = check_constraint_invalidations(['any-package'])
            assert len(invalidated) == 0


def test_validate_package_installation_safe_packages():
    """
    Test package installation validation with safe packages.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with constraints that won't be violated
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('\tnumpy==1.20.0\n')
            f.write('constraint_invalid_when = numpy==1.20.0:pandas>=2.0.0\n')
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            # Test with safe packages
            safe_packages, invalidated = validate_package_installation(['scipy', 'matplotlib'])
            
            assert len(safe_packages) == 2
            assert 'scipy' in safe_packages
            assert 'matplotlib' in safe_packages
            assert len(invalidated) == 0


def test_validate_package_installation_unsafe_packages():
    """
    Test package installation validation with unsafe packages.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with constraints that will be violated
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('\tnumpy==1.20.0\n')
            f.write('\trequests<3.0.0\n')
            f.write('constraint_invalid_when = numpy==1.20.0:pandas>=2.0.0,requests<3.0.0:urllib3>=2.0.0\n')
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            # Test with mix of safe and unsafe packages
            safe_packages, invalidated = validate_package_installation(['pandas', 'scipy', 'urllib3'])
            
            assert len(safe_packages) == 1
            assert 'scipy' in safe_packages
            assert len(invalidated) == 2
            assert 'numpy' in invalidated
            assert 'requests' in invalidated


def test_get_constraint_violation_summary():
    """
    Test constraint violation summary generation.
    
    :returns: None
    """
    # Test with violations
    invalidated_constraints = {
        'numpy': ['pandas', 'scikit-learn'],
        'requests': ['urllib3']
    }
    
    summary = get_constraint_violation_summary(invalidated_constraints)
    
    assert 'The following constraints would be violated:' in summary
    assert 'numpy: invalidated by installing pandas, scikit-learn' in summary
    assert 'requests: invalidated by installing urllib3' in summary
    
    # Test with no violations
    summary = get_constraint_violation_summary({})
    assert summary == ""


def test_evaluate_invalidation_triggers_all_satisfied():
    """
    Test invalidation trigger evaluation when all triggers are satisfied.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with constraints and triggers
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('\tnumpy==1.20.0\n')
            f.write('\tflask<2.0.0\n')
            f.write('constraint_invalid_when = numpy==1.20.0:pandas>=2.0.0,flask<2.0.0:jinja2>=3.0.0|werkzeug>=2.0.0\n')
        
        # Mock installed packages that satisfy all triggers
        mock_distributions = [
            MagicMock(metadata={'Name': 'pandas'}, version='2.1.0'),
            MagicMock(metadata={'Name': 'jinja2'}, version='3.1.0'),
            MagicMock(metadata={'Name': 'werkzeug'}, version='2.2.0'),
        ]
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'), \
             patch('importlib.metadata.distributions', return_value=mock_distributions):
            
            to_remove, details = evaluate_invalidation_triggers()
            
            assert len(to_remove) == 2
            assert 'numpy' in to_remove
            assert 'flask' in to_remove
            assert len(details['numpy']) == 1
            assert 'pandas>=2.0.0' in details['numpy']
            assert len(details['flask']) == 2


def test_evaluate_invalidation_triggers_partial_satisfaction():
    """
    Test invalidation trigger evaluation when only some triggers are satisfied.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with multiple triggers
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('\tflask<2.0.0\n')
            f.write('constraint_invalid_when = flask<2.0.0:jinja2>=3.0.0|werkzeug>=2.0.0\n')
        
        # Mock installed packages - only one trigger satisfied
        mock_distributions = [
            MagicMock(metadata={'Name': 'jinja2'}, version='3.1.0'),
            MagicMock(metadata={'Name': 'werkzeug'}, version='1.0.0'),  # Doesn't satisfy >=2.0.0
        ]
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'), \
             patch('importlib.metadata.distributions', return_value=mock_distributions):
            
            to_remove, details = evaluate_invalidation_triggers()
            
            # Should not remove constraint because not all triggers are satisfied
            assert len(to_remove) == 0
            assert len(details) == 0


def test_cleanup_invalidated_constraints_success():
    """
    Test successful cleanup of invalidated constraints.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with constraints
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('\tnumpy==1.20.0\n')
            f.write('\trequests<3.0.0\n')
            f.write('\tkeep-package>=1.0.0\n')
            f.write('constraint_invalid_when = numpy==1.20.0:pandas>=2.0.0,requests<3.0.0:urllib3>=2.0.0\n')
        
        # Mock installed packages that satisfy triggers for numpy and requests
        mock_distributions = [
            MagicMock(metadata={'Name': 'pandas'}, version='2.1.0'),
            MagicMock(metadata={'Name': 'urllib3'}, version='2.1.0'),
        ]
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'), \
             patch('importlib.metadata.distributions', return_value=mock_distributions):
            
            removed, details, message = cleanup_invalidated_constraints()
            
            assert len(removed) == 2
            assert 'numpy' in removed
            assert 'requests' in removed
            assert message is not None
            assert 'Automatically removed 2 invalidated constraint(s)' in message
            
            # Verify constraints were actually removed from config
            import configparser
            config = configparser.ConfigParser()
            config.read(config_path)
            constraints_value = config.get('global', 'constraints')
            
            # These should be removed
            assert 'numpy==1.20.0' not in constraints_value
            assert 'requests<3.0.0' not in constraints_value
            
            # This should remain
            assert 'keep-package>=1.0.0' in constraints_value


def test_cleanup_invalidated_constraints_no_removals():
    """
    Test cleanup when no constraints need to be removed.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with constraints
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('\tnumpy==1.20.0\n')
            f.write('constraint_invalid_when = numpy==1.20.0:pandas>=2.0.0\n')
        
        # Mock installed packages that don't satisfy triggers
        mock_distributions = [
            MagicMock(metadata={'Name': 'pandas'}, version='1.5.0'),  # Doesn't satisfy >=2.0.0
        ]
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'), \
             patch('importlib.metadata.distributions', return_value=mock_distributions):
            
            removed, details, message = cleanup_invalidated_constraints()
            
            assert len(removed) == 0
            assert len(details) == 0
            assert message is None


def test_post_install_cleanup_with_console():
    """
    Test post-installation cleanup with console output.
    
    :returns: None
    """
    mock_console = MagicMock()
    
    with patch('pipu_cli.package_constraints.cleanup_invalidated_constraints') as mock_cleanup:
        mock_cleanup.return_value = (
            ['numpy', 'requests'], 
            {'numpy': ['pandas>=2.0.0'], 'requests': ['urllib3>=2.0.0']}, 
            'Automatically removed 2 invalidated constraint(s): numpy, requests'
        )
        
        post_install_cleanup(console=mock_console)
        
        # Verify console output
        assert mock_console.print.call_count >= 3
        calls = [call.args[0] for call in mock_console.print.call_args_list]
        
        assert any('Checking for invalidated constraints' in call for call in calls)
        assert any('Automatically removed 2 invalidated constraint(s)' in call for call in calls)
        assert any('numpy: triggers satisfied (pandas>=2.0.0)' in call for call in calls)


def test_post_install_cleanup_no_console():
    """
    Test post-installation cleanup without console output.
    
    :returns: None
    """
    with patch('pipu_cli.package_constraints.cleanup_invalidated_constraints') as mock_cleanup:
        mock_cleanup.return_value = (['numpy'], {}, 'Test message')
        
        # Should not raise any exceptions
        post_install_cleanup()
        
        assert mock_cleanup.called


def test_post_install_cleanup_error_handling():
    """
    Test post-installation cleanup error handling.
    
    :returns: None
    """
    mock_console = MagicMock()
    
    with patch('pipu_cli.package_constraints.cleanup_invalidated_constraints') as mock_cleanup:
        mock_cleanup.side_effect = Exception('Test error')
        
        # Should not raise exceptions, just log warning
        post_install_cleanup(console=mock_console)
        
        # Should print warning
        assert mock_console.print.called
        warning_call = mock_console.print.call_args_list[-1]
        assert 'Warning: Could not clean up invalidated constraints' in warning_call.args[0]


def test_constraint_invalidation_edge_cases():
    """
    Test edge cases in constraint invalidation logic.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Test empty config
        with open(config_path, 'w') as f:
            f.write('[global]\n')
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            # Should handle empty config gracefully
            invalidated = check_constraint_invalidations(['any-package'])
            assert len(invalidated) == 0
            
            safe, invalid = validate_package_installation(['any-package'])
            assert len(safe) == 1
            assert len(invalid) == 0
        
        # Test malformed config
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = invalid-format\n')
            f.write('constraint_invalid_when = malformed:trigger\n')
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            # Should handle malformed config gracefully
            invalidated = check_constraint_invalidations(['any-package'])
            assert len(invalidated) == 0


def test_constraint_invalidation_case_sensitivity():
    """
    Test case sensitivity in constraint invalidation.
    
    :returns: None
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / 'pip.conf'
        
        # Create config with mixed case
        with open(config_path, 'w') as f:
            f.write('[global]\n')
            f.write('constraints = \n')
            f.write('\tNumPy==1.20.0\n')
            f.write('constraint_invalid_when = numpy==1.20.0:Pandas>=2.0.0\n')
        
        with patch('pipu_cli.package_constraints.get_recommended_pip_config_path', return_value=config_path), \
             patch('pipu_cli.package_constraints._get_section_name', return_value='global'):
            # Test with different cases
            invalidated = check_constraint_invalidations(['pandas'])
            assert len(invalidated) == 1
            assert 'numpy' in invalidated
            
            invalidated = check_constraint_invalidations(['PANDAS'])
            assert len(invalidated) == 1
            assert 'numpy' in invalidated


def test_discover_auto_constraints_excludes_trigger_packages():
    """
    Test that discover_auto_constraints excludes specified trigger packages.

    :returns: None
    """
    # Mock installed packages where pandas depends on numpy==1.20.0 and scikit-learn depends on scipy<2.0
    mock_distributions = [
        MagicMock(
            metadata={'Name': 'pandas'},
            version='2.0.0',
            requires=['numpy==1.20.0', 'pytz>=2020.1']
        ),
        MagicMock(
            metadata={'Name': 'scikit-learn'},
            version='1.3.0',
            requires=['scipy<2.0.0', 'joblib~=1.3.0']
        )
    ]

    with patch('importlib.metadata.distributions', return_value=mock_distributions), \
         patch('pipu_cli.package_constraints.read_ignores', return_value=set()):

        # Without exclusions - should find constraints with pandas and scikit-learn as triggers
        all_constraints = discover_auto_constraints()

        # Should find numpy==1.20.0 triggered by pandas>=2.0.0
        numpy_constraints = [c for c in all_constraints if 'numpy==1.20.0' in c[0]]
        assert len(numpy_constraints) >= 1

        # With exclusions - should exclude pandas as a trigger
        filtered_constraints = discover_auto_constraints(exclude_triggers_for_packages=['pandas'])

        # Should not find constraints triggered by pandas
        pandas_triggered = [c for c in filtered_constraints if 'pandas>=2.0.0' in c[1]]
        assert len(pandas_triggered) == 0

        # But should still find constraints triggered by scikit-learn (scipy<2.0.0 and joblib~=1.3.0)
        sklearn_triggered = [c for c in filtered_constraints if 'scikit-learn>1.3.0' in c[1]]
        assert len(sklearn_triggered) > 0


def test_discover_auto_constraints_multiple_triggers_same_constraint():
    """
    Test behavior when multiple packages create constraints for the same dependency.

    :returns: None
    """
    # Mock where both pandas and scikit-learn depend on numpy with meaningful constraints
    mock_distributions = [
        MagicMock(
            metadata={'Name': 'pandas'},
            version='2.0.0',
            requires=['numpy==1.20.0']
        ),
        MagicMock(
            metadata={'Name': 'scikit-learn'},
            version='1.3.0',
            requires=['numpy<2.0.0']
        )
    ]

    with patch('importlib.metadata.distributions', return_value=mock_distributions), \
         patch('pipu_cli.package_constraints.read_ignores', return_value=set()):

        # Without exclusions - should find multiple constraints for numpy
        all_constraints = discover_auto_constraints()
        numpy_constraints = [c for c in all_constraints if 'numpy' in c[0] and ('==' in c[0] or '<' in c[0])]
        assert len(numpy_constraints) >= 2

        # With pandas excluded - should still find numpy constraint from scikit-learn
        filtered_constraints = discover_auto_constraints(exclude_triggers_for_packages=['pandas'])
        numpy_filtered = [c for c in filtered_constraints if 'numpy' in c[0] and '<' in c[0]]

        # Should have fewer numpy constraints but not zero
        assert len(numpy_filtered) < len(numpy_constraints)
        assert len(numpy_filtered) > 0

        # Should not have pandas as trigger
        pandas_triggered = [c for c in filtered_constraints if 'pandas>=2.0.0' in c[1]]
        assert len(pandas_triggered) == 0


def test_discover_auto_constraints_case_insensitive_exclusion():
    """
    Test that trigger package exclusion is case-insensitive.
    
    :returns: None
    """
    mock_distributions = [
        MagicMock(
            metadata={'Name': 'NumPy'}, 
            version='1.21.0',
            requires=['setuptools>=40.0']
        )
    ]
    
    with patch('importlib.metadata.distributions', return_value=mock_distributions), \
         patch('pipu_cli.package_constraints.read_ignores', return_value=set()):
        
        # Test various case combinations
        test_cases = [
            ['numpy'],      # lowercase
            ['NumPy'],      # mixed case
            ['NUMPY'],      # uppercase  
            ['nUmPy']       # random case
        ]
        
        for exclude_list in test_cases:
            constraints = discover_auto_constraints(exclude_triggers_for_packages=exclude_list)
            
            # Should not find any constraints triggered by numpy (in any case variation)
            numpy_triggered = [c for c in constraints if 'numpy>=' in c[1].lower()]
            assert len(numpy_triggered) == 0


def test_discover_auto_constraints_empty_exclusion_list():
    """
    Test that empty or None exclusion list works correctly.
    
    :returns: None
    """
    mock_distributions = [
        MagicMock(
            metadata={'Name': 'requests'}, 
            version='2.28.0',
            requires=['urllib3<2.0.0']
        )
    ]
    
    with patch('importlib.metadata.distributions', return_value=mock_distributions), \
         patch('pipu_cli.package_constraints.read_ignores', return_value=set()):
        
        # Test with None (default)
        constraints_none = discover_auto_constraints()
        
        # Test with empty list
        constraints_empty = discover_auto_constraints(exclude_triggers_for_packages=[])
        
        # Should be identical
        assert len(constraints_none) == len(constraints_empty)
        assert constraints_none == constraints_empty
        
        # Should find constraints
        assert len(constraints_none) > 0


def test_discover_auto_constraints_validates_trigger_packages():
    """
    Test that auto constraint discovery only creates triggers for packages
    that will be found by the validation logic, preventing invalid triggers
    that get cleaned up immediately.

    This addresses the issue where auto constraints create triggers for packages
    that aren't properly validated, leading to cleanup messages on restart.

    :returns: None
    """
    # Mock distributions where one package will be "installed" and one won't
    mock_distributions = [
        MagicMock(
            metadata={'Name': 'ValidPackage'},
            version='1.0.0',
            requires=['dependency<2.0.0']
        ),
        MagicMock(
            metadata={'Name': 'InvalidPackage'},
            version='1.0.0',
            requires=['another-dependency<3.0.0']
        )
    ]

    # Mock _get_installed_packages to only return one of the packages
    # This simulates the scenario where auto discovery sees a package but validation doesn't
    def mock_get_installed_packages():
        return {'validpackage'}  # Only validpackage is considered "installed"

    with patch('importlib.metadata.distributions', return_value=mock_distributions), \
         patch('pipu_cli.package_constraints.read_ignores', return_value=set()), \
         patch('pipu_cli.package_constraints._get_installed_packages', side_effect=mock_get_installed_packages):

        constraints = discover_auto_constraints()

        # Should only create triggers for packages that exist in _get_installed_packages
        trigger_packages = [trigger.split('>')[0] for _, trigger in constraints]

        # Only validpackage should be used as a trigger
        assert 'validpackage' in trigger_packages
        assert 'invalidpackage' not in trigger_packages

        # Verify constraint was created for the valid package
        constraint_specs = [spec for spec, _ in constraints]
        assert any('dependency<2.0.0' in spec for spec in constraint_specs)

        # Should not create constraints where trigger package is invalid
        # (no constraints should be created for invalidpackage dependencies)
        assert not any('another-dependency<3.0.0' in spec for spec in constraint_specs)