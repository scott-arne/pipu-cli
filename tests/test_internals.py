import pytest
from unittest.mock import Mock, patch, MagicMock
from rich.console import Console
from pipu_cli.internals import list_outdated, _check_constraint_satisfaction, _format_constraint_for_display


@pytest.fixture
def mock_console():
    """
    Create a mock console fixture for tests.

    :returns: Mock Console object
    """
    return Mock(spec=Console)


@patch('pipu_cli.internals.get_default_environment')
@patch('pipu_cli.internals.Configuration')
@patch('pipu_cli.internals.PipSession')
@patch('pipu_cli.internals.SearchScope')
@patch('pipu_cli.internals.LinkCollector')
@patch('pipu_cli.internals.PackageFinder')
def test_list_outdated_with_outdated_packages(mock_package_finder_cls, mock_link_collector_cls,
                                             mock_search_scope_cls, _mock_pip_session_cls,
                                             mock_config_cls, mock_get_env, mock_console):
    """
    Test list_outdated when there are outdated packages.

    Mocks the pip infrastructure to simulate packages with different versions,
    ensuring the function correctly identifies outdated packages.

    :param mock_package_finder_cls: Mock PackageFinder class
    :param mock_link_collector_cls: Mock LinkCollector class
    :param mock_search_scope_cls: Mock SearchScope class
    :param mock_pip_session_cls: Mock PipSession class
    :param mock_config_cls: Mock Configuration class
    :param mock_get_env: Mock get_default_environment function
    :param mock_console: Mock console fixture
    :returns: None
    """
    # Test with empty constraints
    constraints = {}

    # Mock the environment and distributions
    mock_env = Mock()
    mock_get_env.return_value = mock_env

    # Create mock distributions
    mock_dist1 = Mock()
    mock_dist1.metadata = {"name": "test-package"}
    mock_dist1.canonical_name = "test-package"
    mock_dist1.version = "1.0.0"

    mock_dist2 = Mock()
    mock_dist2.metadata = {"name": "another-package"}
    mock_dist2.canonical_name = "another-package"
    mock_dist2.version = "2.0.0"

    mock_env.iter_all_distributions.return_value = [mock_dist1, mock_dist2]

    # Mock configuration
    mock_config = Mock()
    mock_config_cls.return_value = mock_config
    mock_config.get_value.side_effect = lambda key: {
        "global.index-url": None,
        "global.extra-index-url": [],
        "global.trusted-host": []
    }.get(key, [])

    # Mock search scope and related objects
    mock_search_scope = Mock()
    mock_search_scope_cls.create.return_value = mock_search_scope

    mock_link_collector = Mock()
    mock_link_collector_cls.return_value = mock_link_collector

    # Mock package finder
    mock_package_finder = Mock()
    mock_package_finder_cls.create.return_value = mock_package_finder

    # Mock candidates for outdated packages
    mock_candidate1 = Mock()
    mock_candidate1.version = "1.5.0"  # Newer than 1.0.0
    mock_link1 = Mock()
    mock_link1.filename = "test-package-1.5.0-py3-none-any.whl"
    mock_candidate1.link = mock_link1

    mock_candidate2 = Mock()
    mock_candidate2.version = "2.0.0"  # Same as current version
    mock_link2 = Mock()
    mock_link2.filename = "another-package-2.0.0-py3-none-any.whl"
    mock_candidate2.link = mock_link2

    mock_package_finder.find_all_candidates.side_effect = [
        [mock_candidate1],  # test-package has newer version
        [mock_candidate2]   # another-package is up to date
    ]

    # Mock console status context manager
    mock_status = MagicMock()
    mock_console.status.return_value.__enter__ = Mock(return_value=mock_status)
    mock_console.status.return_value.__exit__ = Mock(return_value=None)

    # Call the function
    result = list_outdated(console=mock_console, print_table=False, constraints=constraints)

    # Assertions
    assert len(result) == 1  # Only one package is outdated
    assert result[0]["name"] == "test-package"
    assert result[0]["version"] == "1.0.0"
    assert result[0]["latest_version"] == "1.5.0"
    assert result[0]["latest_filetype"] == "wheel"
    assert result[0]["constraint"] is None

    # Verify configuration was loaded
    mock_config.load.assert_called_once()

    # Verify console status was used
    mock_console.status.assert_called_once()


@patch('pipu_cli.internals.get_default_environment')
@patch('pipu_cli.internals.Configuration')
@patch('pipu_cli.internals.PipSession')
@patch('pipu_cli.internals.SearchScope')
@patch('pipu_cli.internals.LinkCollector')
@patch('pipu_cli.internals.PackageFinder')
def test_list_outdated_no_outdated_packages(mock_package_finder_cls, mock_link_collector_cls,
                                           mock_search_scope_cls, _mock_pip_session_cls,
                                           mock_config_cls, mock_get_env, mock_console):
    """
    Test list_outdated when all packages are up to date.

    Verifies that the function returns an empty list when no packages
    have newer versions available.

    :param mock_package_finder_cls: Mock PackageFinder class
    :param mock_link_collector_cls: Mock LinkCollector class
    :param mock_search_scope_cls: Mock SearchScope class
    :param mock_pip_session_cls: Mock PipSession class
    :param mock_config_cls: Mock Configuration class
    :param mock_get_env: Mock get_default_environment function
    :param mock_console: Mock console fixture
    :returns: None
    """
    # Test with empty constraints
    constraints = {}

    # Mock the environment and distributions
    mock_env = Mock()
    mock_get_env.return_value = mock_env

    mock_dist = Mock()
    mock_dist.metadata = {"name": "up-to-date-package"}
    mock_dist.canonical_name = "up-to-date-package"
    mock_dist.version = "1.0.0"

    mock_env.iter_all_distributions.return_value = [mock_dist]

    # Mock configuration
    mock_config = Mock()
    mock_config_cls.return_value = mock_config
    mock_config.get_value.side_effect = Exception("Key not found")

    # Mock other components
    mock_search_scope = Mock()
    mock_search_scope_cls.create.return_value = mock_search_scope

    mock_link_collector = Mock()
    mock_link_collector_cls.return_value = mock_link_collector

    mock_package_finder = Mock()
    mock_package_finder_cls.create.return_value = mock_package_finder

    # Mock candidate with same version (up to date)
    mock_candidate = Mock()
    mock_candidate.version = "1.0.0"  # Same as current version
    mock_link = Mock()
    mock_link.filename = "up-to-date-package-1.0.0-py3-none-any.whl"
    mock_candidate.link = mock_link

    mock_package_finder.find_all_candidates.return_value = [mock_candidate]

    # Mock console status context manager
    mock_status = MagicMock()
    mock_console.status.return_value.__enter__ = Mock(return_value=mock_status)
    mock_console.status.return_value.__exit__ = Mock(return_value=None)

    # Call the function
    result = list_outdated(console=mock_console, print_table=False, constraints=constraints)

    # Assertions
    assert len(result) == 0  # No outdated packages


def test_list_outdated_creates_console_when_none_provided():
    """
    Test that list_outdated creates a console when none is provided.

    Ensures the function gracefully handles the case where no console
    object is passed as a parameter.

    :returns: None
    """

    with patch('pipu_cli.internals.get_default_environment') as mock_get_env:
            # Mock empty environment to avoid complex setup
            mock_env = Mock()
            mock_env.iter_all_distributions.return_value = []
            mock_get_env.return_value = mock_env

            with patch('pipu_cli.internals.Configuration') as mock_config_cls:
                mock_config = Mock()
                mock_config_cls.return_value = mock_config
                mock_config.get_value.side_effect = Exception("Key not found")

                # Call function without console
                result = list_outdated(console=None, print_table=False)

                # Should return empty list and not crash
                assert result == []


@patch('pipu_cli.internals.get_default_environment')
def test_list_outdated_handles_exceptions_gracefully(mock_get_env, mock_console):
    """
    Test that list_outdated handles exceptions in package checking gracefully.

    Simulates network errors and other exceptions during package discovery
    to ensure the function continues processing and doesn't crash.

    :param mock_get_env: Mock get_default_environment function
    :param mock_console: Mock console fixture
    :returns: None
    """
    # Test with empty constraints
    constraints = {}

    # Mock environment
    mock_env = Mock()
    mock_get_env.return_value = mock_env

    # Mock a distribution that will cause an exception
    mock_dist = Mock()
    mock_dist.metadata = {"name": "problematic-package"}
    mock_dist.canonical_name = "problematic-package"
    mock_dist.version = "1.0.0"

    mock_env.iter_all_distributions.return_value = [mock_dist]

    with patch('pipu_cli.internals.Configuration') as mock_config_cls:
        mock_config = Mock()
        mock_config_cls.return_value = mock_config
        mock_config.get_value.side_effect = Exception("Key not found")

        with patch('pipu_cli.internals.PackageFinder') as mock_package_finder_cls:
            mock_package_finder = Mock()
            mock_package_finder_cls.create.return_value = mock_package_finder

            # Make find_all_candidates raise an exception
            mock_package_finder.find_all_candidates.side_effect = Exception("Network error")

            # Mock console status
            mock_status = MagicMock()
            mock_console.status.return_value.__enter__ = Mock(return_value=mock_status)
            mock_console.status.return_value.__exit__ = Mock(return_value=None)

            # Function should raise ConnectionError on network error
            with pytest.raises(ConnectionError) as exc_info:
                list_outdated(console=mock_console, print_table=False, constraints=constraints)
            assert "Network connectivity issues" in str(exc_info.value)
            assert "proxy settings" in str(exc_info.value)


@patch('pipu_cli.internals.get_default_environment')
@patch('pipu_cli.internals.Configuration')
@patch('pipu_cli.internals.PipSession')
@patch('pipu_cli.internals.SearchScope')
@patch('pipu_cli.internals.LinkCollector')
@patch('pipu_cli.internals.PackageFinder')
def test_list_outdated_extra_index_urls_as_string(mock_package_finder_cls, mock_link_collector_cls,
                                                  mock_search_scope_cls, _mock_pip_session_cls,
                                                  mock_config_cls, mock_get_env, mock_console):
    """
    Test list_outdated when extra_index_urls is returned as a string.

    :returns: None
    """
    # Test with empty constraints
    constraints = {}

    # Mock the environment with empty distributions to keep test simple
    mock_env = Mock()
    mock_get_env.return_value = mock_env
    mock_env.iter_all_distributions.return_value = []

    # Mock configuration that returns extra-index-url as string
    mock_config = Mock()
    mock_config_cls.return_value = mock_config

    def get_value_side_effect(key):
        if key == "global.index-url":
            return "https://pypi.org/simple/"
        elif key == "global.extra-index-url":
            return "https://extra.example.com/simple/"  # Return as string, not list
        elif key == "global.trusted-host":
            raise Exception("Not found")
        raise Exception("Key not found")

    mock_config.get_value.side_effect = get_value_side_effect

    # Mock other components
    mock_search_scope = Mock()
    mock_search_scope_cls.create.return_value = mock_search_scope

    mock_link_collector = Mock()
    mock_link_collector_cls.return_value = mock_link_collector

    mock_package_finder = Mock()
    mock_package_finder_cls.create.return_value = mock_package_finder

    # Mock console status
    mock_status = MagicMock()
    mock_console.status.return_value.__enter__ = Mock(return_value=mock_status)
    mock_console.status.return_value.__exit__ = Mock(return_value=None)

    # Call the function
    result = list_outdated(console=mock_console, print_table=False, constraints=constraints)

    # Verify SearchScope.create was called with the right index URLs
    mock_search_scope_cls.create.assert_called_once()
    call_args = mock_search_scope_cls.create.call_args
    expected_index_urls = ["https://pypi.org/simple/", "https://extra.example.com/simple/"]
    assert call_args[1]['index_urls'] == expected_index_urls

    # Should return empty list since no distributions were provided
    assert result == []


@patch('pipu_cli.internals.get_default_environment')
@patch('pipu_cli.internals.Configuration')
@patch('pipu_cli.internals.PipSession')
@patch('pipu_cli.internals.SearchScope')
@patch('pipu_cli.internals.LinkCollector')
@patch('pipu_cli.internals.PackageFinder')
def test_list_outdated_trusted_hosts_as_string(mock_package_finder_cls, mock_link_collector_cls,
                                               mock_search_scope_cls, _mock_pip_session_cls,
                                               mock_config_cls, mock_get_env, mock_console):
    """
    Test list_outdated when trusted_hosts is returned as a string.

    :returns: None
    """
    # Test with empty constraints
    constraints = {}

    # Mock the environment with empty distributions to keep test simple
    mock_env = Mock()
    mock_get_env.return_value = mock_env
    mock_env.iter_all_distributions.return_value = []

    # Mock configuration that returns trusted-host as string
    mock_config = Mock()
    mock_config_cls.return_value = mock_config

    def get_value_side_effect(key):
        if key == "global.index-url":
            return "https://pypi.org/simple/"
        elif key == "global.extra-index-url":
            raise Exception("Not found")
        elif key == "global.trusted-host":
            return "trusted.example.com"  # Return as string, not list
        raise Exception("Key not found")

    mock_config.get_value.side_effect = get_value_side_effect

    # Mock other components
    mock_search_scope = Mock()
    mock_search_scope_cls.create.return_value = mock_search_scope

    mock_link_collector = Mock()
    mock_link_collector_cls.return_value = mock_link_collector

    mock_package_finder = Mock()
    mock_package_finder_cls.create.return_value = mock_package_finder

    # Mock console status
    mock_status = MagicMock()
    mock_console.status.return_value.__enter__ = Mock(return_value=mock_status)
    mock_console.status.return_value.__exit__ = Mock(return_value=None)

    # Call the function
    result = list_outdated(console=mock_console, print_table=False, constraints=constraints)

    # Should complete without error - trusted_hosts conversion is handled
    assert result == []


@patch('pipu_cli.internals.get_default_environment')
@patch('pipu_cli.internals.Configuration')
@patch('pipu_cli.internals.PipSession')
@patch('pipu_cli.internals.SearchScope')
@patch('pipu_cli.internals.LinkCollector')
@patch('pipu_cli.internals.PackageFinder')
def test_list_outdated_prints_all_up_to_date_message(mock_package_finder_cls, mock_link_collector_cls,
                                                     mock_search_scope_cls, _mock_pip_session_cls,
                                                     mock_config_cls, mock_get_env):
    """
    Test list_outdated prints 'all packages up to date' when no outdated packages.

    :returns: None
    """
    # Test with empty constraints
    constraints = {}

    # Mock the environment with one distribution
    mock_env = Mock()
    mock_get_env.return_value = mock_env

    mock_dist = Mock()
    mock_dist.metadata = {"name": "up-to-date-package"}
    mock_dist.canonical_name = "up-to-date-package"
    mock_dist.version = "1.0.0"

    mock_env.iter_all_distributions.return_value = [mock_dist]

    # Mock configuration
    mock_config = Mock()
    mock_config_cls.return_value = mock_config
    mock_config.get_value.side_effect = Exception("Key not found")

    # Mock other components
    mock_search_scope = Mock()
    mock_search_scope_cls.create.return_value = mock_search_scope

    mock_link_collector = Mock()
    mock_link_collector_cls.return_value = mock_link_collector

    mock_package_finder = Mock()
    mock_package_finder_cls.create.return_value = mock_package_finder

    # Mock candidate with same version (up to date)
    mock_candidate = Mock()
    mock_candidate.version = "1.0.0"  # Same as current version
    mock_link = Mock()
    mock_link.filename = "up-to-date-package-1.0.0-py3-none-any.whl"
    mock_candidate.link = mock_link
    mock_package_finder.find_all_candidates.return_value = [mock_candidate]

    # Mock console
    mock_console = Mock(spec=Console)
    mock_status = MagicMock()
    mock_console.status.return_value.__enter__ = Mock(return_value=mock_status)
    mock_console.status.return_value.__exit__ = Mock(return_value=None)

    # Call the function with print_table=True (default)
    result = list_outdated(console=mock_console, constraints=constraints)

    # Should print "All packages are up to date!" message
    mock_console.print.assert_called_with("[green]All packages are up to date![/green]")
    assert result == []


@patch('pipu_cli.internals.get_default_environment')
@patch('pipu_cli.internals.Configuration')
@patch('pipu_cli.internals.PipSession')
@patch('pipu_cli.internals.SearchScope')
@patch('pipu_cli.internals.LinkCollector')
@patch('pipu_cli.internals.PackageFinder')
def test_list_outdated_prints_table_with_outdated_packages(mock_package_finder_cls, mock_link_collector_cls,
                                                           mock_search_scope_cls, _mock_pip_session_cls,
                                                           mock_config_cls, mock_get_env):
    """
    Test list_outdated prints a table when there are outdated packages.

    :returns: None
    """
    # Test with empty constraints
    constraints = {}

    # Mock the environment with one distribution
    mock_env = Mock()
    mock_get_env.return_value = mock_env

    mock_dist = Mock()
    mock_dist.metadata = {"name": "outdated-package"}
    mock_dist.canonical_name = "outdated-package"
    mock_dist.version = "1.0.0"

    mock_env.iter_all_distributions.return_value = [mock_dist]

    # Mock configuration
    mock_config = Mock()
    mock_config_cls.return_value = mock_config
    mock_config.get_value.side_effect = Exception("Key not found")

    # Mock other components
    mock_search_scope = Mock()
    mock_search_scope_cls.create.return_value = mock_search_scope

    mock_link_collector = Mock()
    mock_link_collector_cls.return_value = mock_link_collector

    mock_package_finder = Mock()
    mock_package_finder_cls.create.return_value = mock_package_finder

    # Mock candidate with newer version
    mock_candidate = Mock()
    mock_candidate.version = "2.0.0"  # Newer version
    mock_link = Mock()
    mock_link.filename = "outdated-package-2.0.0-py3-none-any.whl"
    mock_candidate.link = mock_link
    mock_package_finder.find_all_candidates.return_value = [mock_candidate]

    # Mock console and table
    mock_console = Mock(spec=Console)
    mock_status = MagicMock()
    mock_console.status.return_value.__enter__ = Mock(return_value=mock_status)
    mock_console.status.return_value.__exit__ = Mock(return_value=None)

    # Call the function with print_table=True (default)
    with patch('pipu_cli.internals.Table') as mock_table_cls:
        mock_table = Mock()
        mock_table_cls.return_value = mock_table

        result = list_outdated(console=mock_console, constraints=constraints)

        # Should create and print table
        mock_table_cls.assert_called_with(title="Outdated Packages")
        mock_table.add_column.assert_any_call("", width=3)  # Indicator column
        mock_table.add_column.assert_any_call("Package", style="cyan", no_wrap=True)
        mock_table.add_column.assert_any_call("Version", style="magenta")
        mock_table.add_column.assert_any_call("Latest", no_wrap=True)  # No style - uses conditional coloring
        mock_table.add_column.assert_any_call("Type", style="yellow")
        mock_table.add_column.assert_any_call("Constraint", no_wrap=True)
        mock_table.add_column.assert_any_call("Constraint Invalid When", no_wrap=True)

        mock_table.add_row.assert_called_with(
            "[bold green]✓[/bold green]",  # Will update (no constraint)
            "outdated-package", "1.0.0", "[green]2.0.0[/green]", "wheel", "[dim]-[/dim]", "[dim]-[/dim]"
        )
        # Should print table and legend
        mock_console.print.assert_any_call(mock_table)
        mock_console.print.assert_any_call("\n[dim]Legend:[/dim]")
        mock_console.print.assert_any_call("  [bold green]✓[/bold green] = Will be updated  |  [dim]✗[/dim] = Blocked by constraint")

        assert len(result) == 1
        assert result[0]["name"] == "outdated-package"


@patch('pipu_cli.internals.get_default_environment')
@patch('pipu_cli.internals.Configuration')
@patch('pipu_cli.internals.PipSession')
@patch('pipu_cli.internals.SearchScope')
@patch('pipu_cli.internals.LinkCollector')
@patch('pipu_cli.internals.PackageFinder')
def test_list_outdated_respects_constraints(mock_package_finder_cls, mock_link_collector_cls,
                                           mock_search_scope_cls, _mock_pip_session_cls,
                                           mock_config_cls, mock_get_env, mock_console):
    """
    Test that list_outdated includes packages with violating constraints to show them in red.

    :returns: None
    """
    # Test with constraints that would prevent updating to 2.0.0
    constraints = {'test-package': '<2.0.0'}

    # Mock environment with outdated package
    mock_env = Mock()
    mock_get_env.return_value = mock_env

    mock_dist = Mock()
    mock_dist.metadata = {"name": "test-package"}
    mock_dist.canonical_name = "test-package"
    mock_dist.version = "1.0.0"

    mock_env.iter_all_distributions.return_value = [mock_dist]

    # Mock configuration
    mock_config = Mock()
    mock_config_cls.return_value = mock_config
    mock_config.get_value.side_effect = Exception("Key not found")

    # Mock other components
    mock_search_scope = Mock()
    mock_search_scope_cls.create.return_value = mock_search_scope

    mock_link_collector = Mock()
    mock_link_collector_cls.return_value = mock_link_collector

    mock_package_finder = Mock()
    mock_package_finder_cls.create.return_value = mock_package_finder

    # Mock candidate with version that violates constraint
    mock_candidate = Mock()
    mock_candidate.version = "2.0.0"  # This violates <2.0.0 constraint
    mock_package_finder.find_all_candidates.return_value = [mock_candidate]

    # Mock console status
    mock_status = MagicMock()
    mock_console.status.return_value.__enter__ = Mock(return_value=mock_status)
    mock_console.status.return_value.__exit__ = Mock(return_value=None)

    # Call the function
    result = list_outdated(console=mock_console, print_table=False, constraints=constraints)

    # Should include package even though latest version violates constraint (will show in red)
    assert len(result) == 1
    assert result[0]["name"] == "test-package"
    assert result[0]["constraint"] == "<2.0.0"


@patch('pipu_cli.internals.get_default_environment')
@patch('pipu_cli.internals.Configuration')
@patch('pipu_cli.internals.PipSession')
@patch('pipu_cli.internals.SearchScope')
@patch('pipu_cli.internals.LinkCollector')
@patch('pipu_cli.internals.PackageFinder')
def test_list_outdated_includes_packages_satisfying_constraints(mock_package_finder_cls, mock_link_collector_cls,
                                                              mock_search_scope_cls, _mock_pip_session_cls,
                                                              mock_config_cls, mock_get_env, mock_console):
    """
    Test that list_outdated includes packages when latest version satisfies constraints.

    :returns: None
    """
    # Test with constraints that allow updating to 1.5.0
    constraints = {'test-package': '>=1.0.0,<2.0.0'}

    # Mock environment with outdated package
    mock_env = Mock()
    mock_get_env.return_value = mock_env

    mock_dist = Mock()
    mock_dist.metadata = {"name": "test-package"}
    mock_dist.canonical_name = "test-package"
    mock_dist.version = "1.0.0"

    mock_env.iter_all_distributions.return_value = [mock_dist]

    # Mock configuration
    mock_config = Mock()
    mock_config_cls.return_value = mock_config
    mock_config.get_value.side_effect = Exception("Key not found")

    # Mock other components
    mock_search_scope = Mock()
    mock_search_scope_cls.create.return_value = mock_search_scope

    mock_link_collector = Mock()
    mock_link_collector_cls.return_value = mock_link_collector

    mock_package_finder = Mock()
    mock_package_finder_cls.create.return_value = mock_package_finder

    # Mock candidate with version that satisfies constraint
    mock_candidate = Mock()
    mock_candidate.version = "1.5.0"  # This satisfies >=1.0.0,<2.0.0 constraint
    mock_package_finder.find_all_candidates.return_value = [mock_candidate]

    # Mock console status
    mock_status = MagicMock()
    mock_console.status.return_value.__enter__ = Mock(return_value=mock_status)
    mock_console.status.return_value.__exit__ = Mock(return_value=None)

    # Call the function
    result = list_outdated(console=mock_console, print_table=False, constraints=constraints)

    # Should include package with constraint info
    assert len(result) == 1
    assert result[0]["name"] == "test-package"
    assert result[0]["constraint"] == ">=1.0.0,<2.0.0"


def test_check_constraint_satisfaction_valid_versions():
    """
    Test _check_constraint_satisfaction with valid version constraints.

    :returns: None
    """
    # Test exact version match
    assert _check_constraint_satisfaction("1.0.0", "==1.0.0")

    # Test version range
    assert _check_constraint_satisfaction("1.5.0", ">=1.0.0,<2.0.0")
    assert not _check_constraint_satisfaction("2.0.0", ">=1.0.0,<2.0.0")

    # Test minimum version
    assert _check_constraint_satisfaction("1.1.0", ">=1.0.0")
    assert not _check_constraint_satisfaction("0.9.0", ">=1.0.0")


def test_check_constraint_satisfaction_invalid_inputs():
    """
    Test _check_constraint_satisfaction with invalid version/constraint inputs.

    :returns: None
    """
    # Invalid version string
    assert not _check_constraint_satisfaction("not-a-version", ">=1.0.0")

    # Invalid constraint string
    assert not _check_constraint_satisfaction("1.0.0", "invalid-constraint")

    # Both invalid
    assert not _check_constraint_satisfaction("not-version", "invalid-constraint")


def test_format_constraint_for_display_no_constraint():
    """
    Test _format_constraint_for_display with no constraint.

    :returns: None
    """
    result = _format_constraint_for_display(None, "1.0.0")
    assert result == "[dim]-[/dim]"

    result = _format_constraint_for_display("", "1.0.0")
    assert result == "[dim]-[/dim]"


def test_format_constraint_for_display_satisfying_constraint():
    """
    Test _format_constraint_for_display when latest version satisfies constraint.

    :returns: None
    """
    result = _format_constraint_for_display(">=1.0.0,<2.0.0", "1.5.0")
    assert result == "[green]>=1.0.0,<2.0.0[/green]"


def test_format_constraint_for_display_violating_constraint():
    """
    Test _format_constraint_for_display when latest version violates constraint.

    :returns: None
    """
    result = _format_constraint_for_display(">=1.0.0,<2.0.0", "2.1.0")
    assert result == "[red]>=1.0.0,<2.0.0[/red]"


def test_format_constraint_for_display_invalid_constraint():
    """
    Test _format_constraint_for_display with invalid constraint or version.

    :returns: None
    """
    result = _format_constraint_for_display("invalid-constraint", "1.0.0")
    # Invalid constraints are treated as unsatisfied (red), not error (yellow)
    assert result == "[red]invalid-constraint[/red]"


def test_format_constraint_for_display_exception_handling():
    """
    Test _format_constraint_for_display when _check_constraint_satisfaction raises an exception.

    :returns: None
    """
    with patch('pipu_cli.internals._check_constraint_satisfaction', side_effect=Exception("Test error")):
        result = _format_constraint_for_display(">=1.0.0", "1.5.0")
        # Should return yellow when an exception occurs
        assert result == "[yellow]>=1.0.0[/yellow]"


@patch('pipu_cli.internals.get_default_environment')
@patch('pipu_cli.internals.Configuration')
@patch('pipu_cli.internals.PipSession')
@patch('pipu_cli.internals.SearchScope')
@patch('pipu_cli.internals.LinkCollector')
@patch('pipu_cli.internals.PackageFinder')
def test_list_outdated_excludes_prereleases_by_default(mock_package_finder_cls, mock_link_collector_cls,
                                                       mock_search_scope_cls, _mock_pip_session_cls,
                                                       mock_config_cls, mock_get_env, mock_console):
    """
    Test that list_outdated excludes pre-release versions by default.

    :returns: None
    """
    constraints = {}

    # Mock environment with outdated package
    mock_env = Mock()
    mock_get_env.return_value = mock_env

    mock_dist = Mock()
    mock_dist.metadata = {"name": "test-package"}
    mock_dist.canonical_name = "test-package"
    mock_dist.version = "1.0.0"

    mock_env.iter_all_distributions.return_value = [mock_dist]

    # Mock configuration
    mock_config = Mock()
    mock_config_cls.return_value = mock_config
    mock_config.get_value.side_effect = Exception("Key not found")

    # Mock other components
    mock_search_scope = Mock()
    mock_search_scope_cls.create.return_value = mock_search_scope

    mock_link_collector = Mock()
    mock_link_collector_cls.return_value = mock_link_collector

    mock_package_finder = Mock()
    mock_package_finder_cls.create.return_value = mock_package_finder

    # Mock candidates with both stable and pre-release versions
    mock_candidate_stable = Mock()
    mock_candidate_stable.version = "1.5.0"  # Stable version

    mock_candidate_prerelease = Mock()
    mock_candidate_prerelease.version = "2.0.0a1"  # Pre-release version (higher)

    mock_package_finder.find_all_candidates.return_value = [mock_candidate_stable, mock_candidate_prerelease]

    # Mock console status
    mock_status = MagicMock()
    mock_console.status.return_value.__enter__ = Mock(return_value=mock_status)
    mock_console.status.return_value.__exit__ = Mock(return_value=None)

    # Call the function with default pre=False
    result = list_outdated(console=mock_console, print_table=False, constraints=constraints)

    # Should select stable version (1.5.0), not pre-release (2.0.0a1)
    assert len(result) == 1
    assert result[0]["name"] == "test-package"
    assert result[0]["latest_version"] == "1.5.0"


@patch('pipu_cli.internals.get_default_environment')
@patch('pipu_cli.internals.Configuration')
@patch('pipu_cli.internals.PipSession')
@patch('pipu_cli.internals.SearchScope')
@patch('pipu_cli.internals.LinkCollector')
@patch('pipu_cli.internals.PackageFinder')
def test_list_outdated_includes_prereleases_with_pre_flag(mock_package_finder_cls, mock_link_collector_cls,
                                                          mock_search_scope_cls, _mock_pip_session_cls,
                                                          mock_config_cls, mock_get_env, mock_console):
    """
    Test that list_outdated includes pre-release versions when pre=True.

    :returns: None
    """
    constraints = {}

    # Mock environment with outdated package
    mock_env = Mock()
    mock_get_env.return_value = mock_env

    mock_dist = Mock()
    mock_dist.metadata = {"name": "test-package"}
    mock_dist.canonical_name = "test-package"
    mock_dist.version = "1.0.0"

    mock_env.iter_all_distributions.return_value = [mock_dist]

    # Mock configuration
    mock_config = Mock()
    mock_config_cls.return_value = mock_config
    mock_config.get_value.side_effect = Exception("Key not found")

    # Mock other components
    mock_search_scope = Mock()
    mock_search_scope_cls.create.return_value = mock_search_scope

    mock_link_collector = Mock()
    mock_link_collector_cls.return_value = mock_link_collector

    mock_package_finder = Mock()
    mock_package_finder_cls.create.return_value = mock_package_finder

    # Mock candidates with both stable and pre-release versions
    mock_candidate_stable = Mock()
    mock_candidate_stable.version = "1.5.0"  # Stable version

    mock_candidate_prerelease = Mock()
    mock_candidate_prerelease.version = "2.0.0a1"  # Pre-release version (higher)

    mock_package_finder.find_all_candidates.return_value = [mock_candidate_stable, mock_candidate_prerelease]

    # Mock console status
    mock_status = MagicMock()
    mock_console.status.return_value.__enter__ = Mock(return_value=mock_status)
    mock_console.status.return_value.__exit__ = Mock(return_value=None)

    # Call the function with pre=True
    result = list_outdated(console=mock_console, print_table=False, constraints=constraints, pre=True)

    # Should select pre-release version (2.0.0a1) as it's the highest
    assert len(result) == 1
    assert result[0]["name"] == "test-package"
    assert result[0]["latest_version"] == "2.0.0a1"


@patch('pipu_cli.internals.get_default_environment')
@patch('pipu_cli.internals.Configuration')
@patch('pipu_cli.internals.PipSession')
@patch('pipu_cli.internals.SearchScope')
@patch('pipu_cli.internals.LinkCollector')
@patch('pipu_cli.internals.PackageFinder')
def test_list_outdated_fallback_to_prerelease_if_no_stable(mock_package_finder_cls, mock_link_collector_cls,
                                                           mock_search_scope_cls, _mock_pip_session_cls,
                                                           mock_config_cls, mock_get_env, mock_console):
    """
    Test that list_outdated falls back to pre-release if no stable versions available.

    :returns: None
    """
    constraints = {}

    # Mock environment with outdated package
    mock_env = Mock()
    mock_get_env.return_value = mock_env

    mock_dist = Mock()
    mock_dist.metadata = {"name": "test-package"}
    mock_dist.canonical_name = "test-package"
    mock_dist.version = "1.0.0"

    mock_env.iter_all_distributions.return_value = [mock_dist]

    # Mock configuration
    mock_config = Mock()
    mock_config_cls.return_value = mock_config
    mock_config.get_value.side_effect = Exception("Key not found")

    # Mock other components
    mock_search_scope = Mock()
    mock_search_scope_cls.create.return_value = mock_search_scope

    mock_link_collector = Mock()
    mock_link_collector_cls.return_value = mock_link_collector

    mock_package_finder = Mock()
    mock_package_finder_cls.create.return_value = mock_package_finder

    # Mock only pre-release candidates (no stable versions available)
    mock_candidate_prerelease = Mock()
    mock_candidate_prerelease.version = "2.0.0a1"  # Only pre-release available

    mock_package_finder.find_all_candidates.return_value = [mock_candidate_prerelease]

    # Mock console status
    mock_status = MagicMock()
    mock_console.status.return_value.__enter__ = Mock(return_value=mock_status)
    mock_console.status.return_value.__exit__ = Mock(return_value=None)

    # Call the function with default pre=False
    result = list_outdated(console=mock_console, print_table=False, constraints=constraints)

    # Should fall back to pre-release version since no stable version is available
    assert len(result) == 1
    assert result[0]["name"] == "test-package"
    assert result[0]["latest_version"] == "2.0.0a1"


@patch('pipu_cli.internals.get_default_environment')
@patch('pipu_cli.internals.Configuration')
@patch('pipu_cli.internals.PipSession')
@patch('pipu_cli.internals.SearchScope')
@patch('pipu_cli.internals.LinkCollector')
@patch('pipu_cli.internals.PackageFinder')
def test_list_outdated_handles_invalid_version_during_prerelease_filtering(
    mock_package_finder_cls, mock_link_collector_cls,
    mock_search_scope_cls, _mock_pip_session_cls,
    mock_config_cls, mock_get_env, mock_console
):
    """
    Test that list_outdated handles InvalidVersion exceptions during pre-release filtering.

    :returns: None
    """
    constraints = {}

    # Mock environment with outdated package
    mock_env = Mock()
    mock_get_env.return_value = mock_env

    mock_dist = Mock()
    mock_dist.metadata = {"name": "test-package"}
    mock_dist.canonical_name = "test-package"
    mock_dist.version = "1.0.0"

    mock_env.iter_all_distributions.return_value = [mock_dist]

    # Mock configuration
    mock_config = Mock()
    mock_config_cls.return_value = mock_config
    mock_config.get_value.side_effect = Exception("Key not found")

    # Mock other components
    mock_search_scope = Mock()
    mock_search_scope_cls.create.return_value = mock_search_scope

    mock_link_collector = Mock()
    mock_link_collector_cls.return_value = mock_link_collector

    mock_package_finder = Mock()
    mock_package_finder_cls.create.return_value = mock_package_finder

    # Mock candidates - one with invalid version string, one valid
    mock_candidate_invalid = Mock()
    mock_candidate_invalid.version = "invalid-version-string"  # Will cause InvalidVersion

    mock_candidate_valid = Mock()
    mock_candidate_valid.version = "1.5.0"  # Valid version

    mock_package_finder.find_all_candidates.return_value = [mock_candidate_invalid, mock_candidate_valid]

    # Mock console status
    mock_status = MagicMock()
    mock_console.status.return_value.__enter__ = Mock(return_value=mock_status)
    mock_console.status.return_value.__exit__ = Mock(return_value=None)

    # Call the function with default pre=False - should handle InvalidVersion gracefully
    result = list_outdated(console=mock_console, print_table=False, constraints=constraints)

    # Should successfully process the valid candidate and ignore the invalid one
    assert len(result) == 1
    assert result[0]["name"] == "test-package"
    assert result[0]["latest_version"] == "1.5.0"


@patch('pipu_cli.internals.get_default_environment')
@patch('pipu_cli.internals.Configuration')
@patch('pipu_cli.internals.PipSession')
@patch('pipu_cli.internals.SearchScope')
@patch('pipu_cli.internals.LinkCollector')
@patch('pipu_cli.internals.PackageFinder')
def test_list_outdated_detects_different_file_types(mock_package_finder_cls, mock_link_collector_cls,
                                                     mock_search_scope_cls, _mock_pip_session_cls,
                                                     mock_config_cls, mock_get_env, mock_console):
    """
    Test that list_outdated correctly detects different package file types.

    :returns: None
    """
    constraints = {}

    # Mock environment with multiple outdated packages
    mock_env = Mock()
    mock_get_env.return_value = mock_env

    mock_dist1 = Mock()
    mock_dist1.metadata = {"name": "wheel-package"}
    mock_dist1.canonical_name = "wheel-package"
    mock_dist1.version = "1.0.0"

    mock_dist2 = Mock()
    mock_dist2.metadata = {"name": "sdist-package"}
    mock_dist2.canonical_name = "sdist-package"
    mock_dist2.version = "1.0.0"

    mock_env.iter_all_distributions.return_value = [mock_dist1, mock_dist2]

    # Mock configuration
    mock_config = Mock()
    mock_config_cls.return_value = mock_config
    mock_config.get_value.side_effect = Exception("Key not found")

    # Mock other components
    mock_search_scope = Mock()
    mock_search_scope_cls.create.return_value = mock_search_scope

    mock_link_collector = Mock()
    mock_link_collector_cls.return_value = mock_link_collector

    mock_package_finder = Mock()
    mock_package_finder_cls.create.return_value = mock_package_finder

    # Mock candidates with different file types
    mock_candidate_wheel = Mock()
    mock_candidate_wheel.version = "1.5.0"
    mock_link_wheel = Mock()
    mock_link_wheel.filename = "wheel-package-1.5.0-py3-none-any.whl"
    mock_candidate_wheel.link = mock_link_wheel

    mock_candidate_sdist = Mock()
    mock_candidate_sdist.version = "1.5.0"
    mock_link_sdist = Mock()
    mock_link_sdist.filename = "sdist-package-1.5.0.tar.gz"
    mock_candidate_sdist.link = mock_link_sdist

    mock_package_finder.find_all_candidates.side_effect = [
        [mock_candidate_wheel],  # wheel-package
        [mock_candidate_sdist]   # sdist-package
    ]

    # Mock console status
    mock_status = MagicMock()
    mock_console.status.return_value.__enter__ = Mock(return_value=mock_status)
    mock_console.status.return_value.__exit__ = Mock(return_value=None)

    # Call the function
    result = list_outdated(console=mock_console, print_table=False, constraints=constraints)

    # Verify different file types are detected correctly
    assert len(result) == 2
    
    wheel_pkg = next(pkg for pkg in result if pkg["name"] == "wheel-package")
    sdist_pkg = next(pkg for pkg in result if pkg["name"] == "sdist-package")
    
    assert wheel_pkg["latest_filetype"] == "wheel"
    assert sdist_pkg["latest_filetype"] == "sdist"


@patch('pipu_cli.internals.get_default_environment')
@patch('pipu_cli.internals.Configuration')
@patch('pipu_cli.internals.PipSession')
@patch('pipu_cli.internals.SearchScope')
@patch('pipu_cli.internals.LinkCollector')
@patch('pipu_cli.internals.PackageFinder')
def test_list_outdated_handles_edge_case_file_types(mock_package_finder_cls, mock_link_collector_cls,
                                                     mock_search_scope_cls, _mock_pip_session_cls,
                                                     mock_config_cls, mock_get_env, mock_console):
    """
    Test that list_outdated handles edge cases in file type detection.

    :returns: None
    """
    constraints = {}

    # Mock environment with multiple outdated packages
    mock_env = Mock()
    mock_get_env.return_value = mock_env

    mock_dist1 = Mock()
    mock_dist1.metadata = {"name": "egg-package"}
    mock_dist1.canonical_name = "egg-package"
    mock_dist1.version = "1.0.0"

    mock_dist2 = Mock()
    mock_dist2.metadata = {"name": "custom-package"}
    mock_dist2.canonical_name = "custom-package"
    mock_dist2.version = "1.0.0"

    mock_dist3 = Mock()
    mock_dist3.metadata = {"name": "no-extension"}
    mock_dist3.canonical_name = "no-extension"
    mock_dist3.version = "1.0.0"

    mock_dist4 = Mock()
    mock_dist4.metadata = {"name": "no-link"}
    mock_dist4.canonical_name = "no-link"
    mock_dist4.version = "1.0.0"

    mock_env.iter_all_distributions.return_value = [mock_dist1, mock_dist2, mock_dist3, mock_dist4]

    # Mock configuration
    mock_config = Mock()
    mock_config_cls.return_value = mock_config
    mock_config.get_value.side_effect = Exception("Key not found")

    # Mock other components
    mock_search_scope = Mock()
    mock_search_scope_cls.create.return_value = mock_search_scope

    mock_link_collector = Mock()
    mock_link_collector_cls.return_value = mock_link_collector

    mock_package_finder = Mock()
    mock_package_finder_cls.create.return_value = mock_package_finder

    # Mock candidates with edge case file types
    mock_candidate_egg = Mock()
    mock_candidate_egg.version = "1.5.0"
    mock_link_egg = Mock()
    mock_link_egg.filename = "egg-package-1.5.0.egg"
    mock_candidate_egg.link = mock_link_egg

    mock_candidate_custom = Mock()
    mock_candidate_custom.version = "1.5.0"
    mock_link_custom = Mock()
    mock_link_custom.filename = "custom-package-1.5.0.dmg"  # Custom extension
    mock_candidate_custom.link = mock_link_custom

    mock_candidate_no_ext = Mock()
    mock_candidate_no_ext.version = "1.5.0"
    mock_link_no_ext = Mock()
    mock_link_no_ext.filename = "no-extension-1"  # No extension
    mock_candidate_no_ext.link = mock_link_no_ext

    mock_candidate_no_link = Mock()
    mock_candidate_no_link.version = "1.5.0"
    mock_candidate_no_link.link = None  # No link information

    mock_package_finder.find_all_candidates.side_effect = [
        [mock_candidate_egg],     # egg-package
        [mock_candidate_custom],  # custom-package
        [mock_candidate_no_ext],  # no-extension
        [mock_candidate_no_link]  # no-link
    ]

    # Mock console status
    mock_status = MagicMock()
    mock_console.status.return_value.__enter__ = Mock(return_value=mock_status)
    mock_console.status.return_value.__exit__ = Mock(return_value=None)

    # Call the function
    result = list_outdated(console=mock_console, print_table=False, constraints=constraints)

    # Verify edge case file types are detected correctly
    assert len(result) == 4
    
    egg_pkg = next(pkg for pkg in result if pkg["name"] == "egg-package")
    custom_pkg = next(pkg for pkg in result if pkg["name"] == "custom-package")
    no_ext_pkg = next(pkg for pkg in result if pkg["name"] == "no-extension")
    no_link_pkg = next(pkg for pkg in result if pkg["name"] == "no-link")
    
    assert egg_pkg["latest_filetype"] == "egg"
    assert custom_pkg["latest_filetype"] == "dmg"  # Custom extension
    assert no_ext_pkg["latest_filetype"] == "unknown"  # No extension
    assert no_link_pkg["latest_filetype"] == "wheel"  # Fallback


@patch('pipu_cli.internals.Configuration')
@patch('pipu_cli.internals.PipSession')
@patch('pipu_cli.internals.get_default_environment')
def test_list_outdated_progress_callback(mock_env, mock_session, mock_config_cls):
    """Test that progress callback is called for each package being checked."""
    # Mock console
    mock_console = MagicMock()

    # Mock Configuration
    mock_config = MagicMock()
    mock_config.get_value.side_effect = lambda key: {
        "global.index-url": "https://pypi.org/simple/",
        "global.extra-index-url": [],
        "global.trusted-host": []
    }.get(key, None)
    mock_config_cls.return_value = mock_config

    # Mock environment with one package
    mock_dist = MagicMock()
    mock_dist.metadata = {"name": "test-package"}
    mock_dist.version = "1.0.0"
    mock_dist.canonical_name = "test-package"
    mock_env.return_value.iter_all_distributions.return_value = [mock_dist]

    # Mock session and package finder
    mock_session_instance = MagicMock()
    mock_session.return_value = mock_session_instance

    # Mock no candidates to avoid complex mocking
    with patch('pipu_cli.internals.PackageFinder') as mock_finder_class:
        mock_finder = MagicMock()
        mock_finder.find_all_candidates.return_value = []
        mock_finder_class.create.return_value = mock_finder

        # Track progress callback calls
        progress_calls = []

        def progress_callback(package_name):
            progress_calls.append(package_name)

        # Call list_outdated with progress callback
        list_outdated(
            console=mock_console,
            print_table=False,
            progress_callback=progress_callback
        )

        # Verify progress callback was called with package name
        assert len(progress_calls) == 1
        assert progress_calls[0] == "test-package"


class TestCallWithTimeoutCompletion:
    """Test the _call_with_timeout completion Event mechanism."""

    def test_timeout_uses_completion_event(self):
        """Test that timeout detection uses completion Event instead of thread.is_alive()."""
        import time
        from pipu_cli.internals import _call_with_timeout

        def slow_function():
            time.sleep(0.5)
            return "result"

        # Should timeout before function completes
        with pytest.raises(TimeoutError) as exc_info:
            _call_with_timeout(slow_function, 0.1)

        assert "timed out after 0.1 seconds" in str(exc_info.value)

    def test_completion_event_set_on_success(self):
        """Test that completion Event is set when function completes successfully."""
        from pipu_cli.internals import _call_with_timeout

        def fast_function():
            return "success"

        result = _call_with_timeout(fast_function, 1)
        assert result == "success"

    def test_completion_event_set_on_exception(self):
        """Test that completion Event is set even when function raises exception."""
        from pipu_cli.internals import _call_with_timeout

        def failing_function():
            raise ValueError("test error")

        with pytest.raises(ValueError) as exc_info:
            _call_with_timeout(failing_function, 1)

        assert "test error" in str(exc_info.value)

    def test_timeout_with_args_and_kwargs(self):
        """Test that _call_with_timeout properly passes args and kwargs."""
        from pipu_cli.internals import _call_with_timeout

        def function_with_params(a, b, c=None):
            return f"{a}-{b}-{c}"

        result = _call_with_timeout(function_with_params, 1, "arg1", "arg2", c="kwarg1")
        assert result == "arg1-arg2-kwarg1"


class TestGetConstraintColorUtility:
    """Test the get_constraint_color() utility function."""

    def test_color_with_no_constraint(self):
        """Test that no constraint returns green."""
        from pipu_cli.internals import get_constraint_color

        result = get_constraint_color("2.0.0", None)
        assert result == "green"

    def test_color_with_satisfied_constraint(self):
        """Test that satisfied constraint returns green."""
        from pipu_cli.internals import get_constraint_color

        result = get_constraint_color("2.0.0", ">=2.0.0")
        assert result == "green"

    def test_color_with_violated_constraint(self):
        """Test that violated constraint returns red."""
        from pipu_cli.internals import get_constraint_color

        result = get_constraint_color("3.0.0", "<3.0.0")
        assert result == "red"

    def test_color_with_complex_constraint_satisfied(self):
        """Test complex satisfied constraint returns green."""
        from pipu_cli.internals import get_constraint_color

        result = get_constraint_color("2.5.0", ">=2.0.0,<3.0.0")
        assert result == "green"

    def test_color_with_complex_constraint_violated(self):
        """Test complex violated constraint returns red."""
        from pipu_cli.internals import get_constraint_color

        result = get_constraint_color("3.5.0", ">=2.0.0,<3.0.0")
        assert result == "red"

    def test_color_with_empty_string_constraint(self):
        """Test that empty string constraint is treated as no constraint."""
        from pipu_cli.internals import get_constraint_color

        result = get_constraint_color("2.0.0", "")
        assert result == "green"


class TestFormatInvalidWhenDisplay:
    """Test the format_invalid_when_display() utility function."""

    def test_format_with_triggers(self):
        """Test formatting with trigger packages returns yellow text."""
        from pipu_cli.internals import format_invalid_when_display

        result = format_invalid_when_display("package-a, package-b")
        assert result == "[yellow]package-a, package-b[/yellow]"

    def test_format_with_none(self):
        """Test formatting with None returns dim dash."""
        from pipu_cli.internals import format_invalid_when_display

        result = format_invalid_when_display(None)
        assert result == "[dim]-[/dim]"

    def test_format_with_empty_string(self):
        """Test formatting with empty string returns dim dash."""
        from pipu_cli.internals import format_invalid_when_display

        result = format_invalid_when_display("")
        assert result == "[dim]-[/dim]"

    def test_format_with_single_trigger(self):
        """Test formatting with single trigger package."""
        from pipu_cli.internals import format_invalid_when_display

        result = format_invalid_when_display("django")
        assert result == "[yellow]django[/yellow]"
