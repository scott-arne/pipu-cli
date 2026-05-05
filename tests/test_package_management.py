"""Comprehensive tests for package_management module."""

import json
import subprocess
import sys
import zipfile
from unittest.mock import Mock, MagicMock, patch
import pytest

from packaging.version import Version

from pipu_cli import package_management
from pipu_cli.package_management import (
    InstalledPackage,
    Package,
    UpgradePackageInfo,
    UpgradedPackage,
    inspect_installed_packages,
    get_latest_versions,
    parse_package_spec,
    resolve_upgradable_packages,
    resolve_upgradable_packages_with_reasons,
    install_packages,
    run_pip_install,
    run_pip_uninstall,
    _get_editable_packages,
    _extract_constrained_dependencies,
)


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def mock_distribution():
    """Create a mock distribution object with configurable metadata."""
    def _create_dist(name, version, requires=None, is_editable=False):
        dist = Mock()

        # Create a mock metadata object instead of a dict
        metadata_mock = Mock()
        metadata_mock.__getitem__ = Mock(return_value=name)
        metadata_mock.get = Mock(return_value=name)

        # Mock the get_all method for Requires-Dist
        if requires is not None:
            metadata_mock.get_all = Mock(return_value=requires)
        else:
            metadata_mock.get_all = Mock(return_value=None)

        dist.metadata = metadata_mock
        dist.version = version
        dist.canonical_name = name.lower().replace("_", "-")

        return dist

    return _create_dist


@pytest.fixture
def mock_pip_list_output():
    """Create mock pip list --editable output."""
    def _create_output(packages):
        """
        Create pip list output.

        :param packages: List of tuples (name, version, location)
        """
        lines = [
            "Package            Version  Location",
            "------------------ -------- ---------------------------------"
        ]
        for name, version, location in packages:
            lines.append(f"{name:<18} {version:<8} {location}")
        return "\n".join(lines)

    return _create_output


@pytest.fixture
def mock_popen():
    """Create a mock Popen object with proper pipe simulation."""
    def _create_popen(returncode=0, stdout_lines=None, stderr_lines=None):
        """
        Create a mock Popen object that simulates subprocess.Popen behavior.

        :param returncode: The return code for the process
        :param stdout_lines: List of strings to return from stdout (or None for empty)
        :param stderr_lines: List of strings to return from stderr (or None for empty)
        """
        # Create mock pipes
        mock_stdout = Mock()
        mock_stderr = Mock()

        # Setup readline to return lines one by one
        stdout_data = stdout_lines if stdout_lines else []
        stderr_data = stderr_lines if stderr_lines else []

        # readline returns one line at a time, then empty string
        mock_stdout.readline = Mock(side_effect=stdout_data + [''])
        mock_stderr.readline = Mock(side_effect=stderr_data + [''])

        # close() should do nothing
        mock_stdout.close = Mock()
        mock_stderr.close = Mock()

        # Create mock process
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = mock_stderr
        mock_process.returncode = returncode
        mock_process.wait = Mock(return_value=returncode)
        mock_process.kill = Mock()

        return mock_process

    return _create_popen


# ============================================================================
# Tests for _extract_constrained_dependencies
# ============================================================================

def test_extract_constrained_dependencies_with_various_constraints(mock_distribution):
    """Test extraction of different types of version constraints."""
    requires = [
        "requests>=2.28.0",
        "numpy>=1.20.0,<2.0.0",
        "pandas==1.5.0",
        "scipy~=1.9.0",
        "matplotlib>3.0.0",
    ]
    dist = mock_distribution("test-package", "1.0.0", requires)

    result = _extract_constrained_dependencies(dist)

    # Note: packaging may reorder specifiers, so we need to match what it actually produces
    assert result == {
        "requests": ">=2.28.0",
        "numpy": "<2.0.0,>=1.20.0",  # Reordered by packaging
        "pandas": "==1.5.0",
        "scipy": "~=1.9.0",
        "matplotlib": ">3.0.0",
    }


def test_extract_constrained_dependencies_with_extras(mock_distribution):
    """Test that extras are handled correctly."""
    requires = [
        "requests[security]>=2.28.0",
        "pandas[excel,sql]==1.5.0",
    ]
    dist = mock_distribution("test-package", "1.0.0", requires)

    result = _extract_constrained_dependencies(dist)

    # Extras should not affect the constraint extraction
    assert result == {
        "requests": ">=2.28.0",
        "pandas": "==1.5.0",
    }


def test_extract_constrained_dependencies_with_markers(mock_distribution):
    """Test that environment markers are evaluated correctly.

    - Markers that evaluate to True in current environment: constraint included
    - Markers that evaluate to False: constraint skipped
    - Extra markers: always skipped (can't know which extras were installed)
    """
    requires = [
        # This marker will be True on Python 3.x
        "typing-extensions>=4.0.0; python_version >= '3.0'",
        # This marker will be False on Python 3.8+
        "importlib-metadata>=1.0; python_version < '3.8'",
        # Extra markers should always be skipped
        "dask<2025.3.0; extra == 'dask'",
    ]
    dist = mock_distribution("test-package", "1.0.0", requires)

    result = _extract_constrained_dependencies(dist)

    # Only constraints with markers that evaluate to True should be included
    # Extra markers are always skipped
    assert result == {
        "typing-extensions": ">=4.0.0",
    }


def test_extract_constrained_dependencies_without_constraints(mock_distribution):
    """Test dependencies without version constraints are not included."""
    requires = [
        "requests",  # No constraint
        "numpy>=1.20.0",  # Has constraint
        "pandas",  # No constraint
    ]
    dist = mock_distribution("test-package", "1.0.0", requires)

    result = _extract_constrained_dependencies(dist)

    # Only constrained dependencies should be included
    assert result == {
        "numpy": ">=1.20.0",
    }


def test_extract_constrained_dependencies_no_dependencies(mock_distribution):
    """Test package with no dependencies."""
    dist = mock_distribution("test-package", "1.0.0", requires=[])

    result = _extract_constrained_dependencies(dist)

    assert result == {}


def test_extract_constrained_dependencies_none_metadata(mock_distribution):
    """Test package with None Requires-Dist metadata."""
    dist = mock_distribution("test-package", "1.0.0", requires=None)

    result = _extract_constrained_dependencies(dist)

    assert result == {}


def test_extract_constrained_dependencies_invalid_requirement(mock_distribution):
    """Test handling of invalid requirement specifications."""
    requires = [
        "valid-package>=1.0.0",
        "invalid package name!!!",  # Invalid
        "another-valid>=2.0.0",
    ]
    dist = mock_distribution("test-package", "1.0.0", requires)

    result = _extract_constrained_dependencies(dist)

    # Should skip invalid requirements and continue
    assert result == {
        "valid-package": ">=1.0.0",
        "another-valid": ">=2.0.0",
    }


def test_extract_constrained_dependencies_canonicalization(mock_distribution):
    """Test that package names are canonicalized (lowercased, hyphens)."""
    requires = [
        "Django>=4.0.0",
        "Pillow_PIL>=9.0.0",
        "PyYAML>=6.0",
    ]
    dist = mock_distribution("test-package", "1.0.0", requires)

    result = _extract_constrained_dependencies(dist)

    # All names should be canonicalized
    assert result == {
        "django": ">=4.0.0",
        "pillow-pil": ">=9.0.0",
        "pyyaml": ">=6.0",
    }


def test_extract_constrained_dependencies_exception_in_metadata(mock_distribution):
    """Test graceful handling when metadata access raises an exception."""
    dist = mock_distribution("test-package", "1.0.0")
    dist.metadata.get_all = Mock(side_effect=Exception("Metadata error"))

    result = _extract_constrained_dependencies(dist)

    # Should return empty dict on error
    assert result == {}


def test_extract_wheel_constraints_reads_metadata(tmp_path):
    """Wheel target metadata can be parsed without installing the package."""
    wheel_path = tmp_path / "package_a-2.0.0-py3-none-any.whl"
    metadata = (
        "Metadata-Version: 2.1\n"
        "Name: package-a\n"
        "Version: 2.0.0\n"
        "Requires-Dist: package-b <4.0\n"
        "Requires-Dist: unconstrained\n"
    )
    with zipfile.ZipFile(wheel_path, "w") as archive:
        archive.writestr("package_a-2.0.0.dist-info/METADATA", metadata)

    result = package_management._extract_wheel_constraints(wheel_path)

    assert result == {"package-b": "<4.0"}


# ============================================================================
# Tests for _get_editable_packages
# ============================================================================

def test_get_editable_packages_success(mock_pip_list_output):
    """Test successful retrieval of editable packages."""
    output = mock_pip_list_output([
        ("my-package", "1.0.0", "/home/user/projects/my-package"),
        ("another-pkg", "2.5.3", "/home/user/dev/another-pkg"),
    ])

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            stdout=output,
            returncode=0
        )

        result = _get_editable_packages(timeout=10)

    assert result == {
        "my-package": "/home/user/projects/my-package",
        "another-pkg": "/home/user/dev/another-pkg",
    }


def test_get_editable_packages_no_editable_packages():
    """Test when no editable packages are installed."""
    output = "Package  Version  Location\n------------------------------"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            stdout=output,
            returncode=0
        )

        result = _get_editable_packages(timeout=10)

    assert result == {}


def test_get_editable_packages_with_spaces_in_path(mock_pip_list_output):
    """Test handling of paths with spaces."""
    output = mock_pip_list_output([
        ("my-package", "1.0.0", "/home/user/My Projects/my-package"),
    ])

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            stdout=output,
            returncode=0
        )

        result = _get_editable_packages(timeout=10)

    assert result == {
        "my-package": "/home/user/My Projects/my-package",
    }


def test_get_editable_packages_subprocess_error():
    """Test handling of subprocess errors."""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(1, "pip")

        result = _get_editable_packages(timeout=10)

    # Should return empty dict on error
    assert result == {}


def test_get_editable_packages_timeout():
    """Test handling of subprocess timeout."""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired("pip", 10)

        result = _get_editable_packages(timeout=10)

    # Should return empty dict on timeout
    assert result == {}


def test_get_editable_packages_unexpected_error():
    """Test handling of unexpected errors."""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = OSError("Unexpected error")

        result = _get_editable_packages(timeout=10)

    # Should return empty dict on error
    assert result == {}


def test_get_editable_packages_canonicalization(mock_pip_list_output):
    """Test that package names are canonicalized."""
    output = mock_pip_list_output([
        ("My_Package", "1.0.0", "/home/user/projects/my-package"),
        ("Another.Pkg", "2.0.0", "/home/user/dev/another-pkg"),
    ])

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            stdout=output,
            returncode=0
        )

        result = _get_editable_packages(timeout=10)

    # Package names should be canonicalized
    assert result == {
        "my-package": "/home/user/projects/my-package",
        "another-pkg": "/home/user/dev/another-pkg",
    }


# ============================================================================
# Tests for inspect_installed_packages
# ============================================================================

def test_inspect_installed_packages_basic(mock_distribution, mock_pip_list_output):
    """Test basic package inspection with mixed regular and editable packages."""
    # Mock distributions
    dists = [
        mock_distribution("requests", "2.28.0", ["urllib3>=1.26.0", "charset-normalizer>=2.0.0"]),
        mock_distribution("numpy", "1.24.0", []),
        mock_distribution("my-package", "1.0.0", ["requests>=2.28.0"]),
    ]

    # Mock editable packages
    editable_output = mock_pip_list_output([
        ("my-package", "1.0.0", "/home/user/projects/my-package"),
    ])

    with patch("pipu_cli.package_management.get_default_environment") as mock_env, \
         patch("subprocess.run") as mock_run:

        mock_env.return_value.iter_all_distributions.return_value = dists
        mock_run.return_value = Mock(stdout=editable_output, returncode=0)

        result = inspect_installed_packages(timeout=10)

    assert len(result) == 3

    # Check first package (my-package) - editable
    my_pkg = next(p for p in result if p.name == "my-package")
    assert my_pkg.version == Version("1.0.0")
    assert my_pkg.is_editable is True
    assert my_pkg.constrained_dependencies == {"requests": ">=2.28.0"}

    # Check second package (numpy) - no dependencies
    numpy = next(p for p in result if p.name == "numpy")
    assert numpy.version == Version("1.24.0")
    assert numpy.is_editable is False
    assert numpy.constrained_dependencies == {}

    # Check third package (requests) - regular with dependencies
    requests = next(p for p in result if p.name == "requests")
    assert requests.version == Version("2.28.0")
    assert requests.is_editable is False
    assert requests.constrained_dependencies == {
        "urllib3": ">=1.26.0",
        "charset-normalizer": ">=2.0.0"
    }


def test_inspect_installed_packages_sorted_output(mock_distribution):
    """Test that packages are sorted alphabetically by name."""
    dists = [
        mock_distribution("zebra", "1.0.0", []),
        mock_distribution("alpha", "1.0.0", []),
        mock_distribution("middle", "1.0.0", []),
    ]

    with patch("pipu_cli.package_management.get_default_environment") as mock_env, \
         patch("pipu_cli.package_management._get_editable_packages") as mock_edit:

        mock_env.return_value.iter_all_distributions.return_value = dists
        mock_edit.return_value = {}

        result = inspect_installed_packages(timeout=10)

    names = [p.name for p in result]
    assert names == ["alpha", "middle", "zebra"]


def test_inspect_installed_packages_invalid_version(mock_distribution):
    """Test handling of packages with invalid versions."""
    dists = [
        mock_distribution("valid-pkg", "1.0.0", []),
        mock_distribution("invalid-pkg", "not-a-version", []),
        mock_distribution("another-valid", "2.0.0", []),
    ]

    with patch("pipu_cli.package_management.get_default_environment") as mock_env, \
         patch("pipu_cli.package_management._get_editable_packages") as mock_edit:

        mock_env.return_value.iter_all_distributions.return_value = dists
        mock_edit.return_value = {}

        result = inspect_installed_packages(timeout=10)

    # Invalid version package should be skipped
    assert len(result) == 2
    names = [p.name for p in result]
    assert "invalid-pkg" not in names
    assert "valid-pkg" in names
    assert "another-valid" in names


def test_inspect_installed_packages_package_processing_error(mock_distribution):
    """Test handling when a package raises an error during processing."""
    good_dist = mock_distribution("good-package", "1.0.0", [])

    # Create a distribution that will raise an error when accessing metadata["name"]
    bad_dist = Mock()
    metadata_mock = Mock()
    # Make __getitem__ raise an error to simulate metadata access failure
    metadata_mock.__getitem__ = Mock(side_effect=Exception("Metadata access error"))
    metadata_mock.get = Mock(return_value="bad-package")
    bad_dist.metadata = metadata_mock
    bad_dist.version = "1.0.0"
    bad_dist.canonical_name = "bad-package"

    dists = [good_dist, bad_dist]

    with patch("pipu_cli.package_management.get_default_environment") as mock_env, \
         patch("pipu_cli.package_management._get_editable_packages") as mock_edit:

        mock_env.return_value.iter_all_distributions.return_value = dists
        mock_edit.return_value = {}

        result = inspect_installed_packages(timeout=10)

    # Bad package should be skipped, good package should be included
    assert len(result) == 1
    assert result[0].name == "good-package"


def test_inspect_installed_packages_empty_environment():
    """Test handling of empty environment with no packages."""
    with patch("pipu_cli.package_management.get_default_environment") as mock_env, \
         patch("pipu_cli.package_management._get_editable_packages") as mock_edit:

        mock_env.return_value.iter_all_distributions.return_value = []
        mock_edit.return_value = {}

        result = inspect_installed_packages(timeout=10)

    assert result == []


def test_inspect_installed_packages_environment_error():
    """Test handling when get_default_environment raises an error."""
    with patch("pipu_cli.package_management.get_default_environment") as mock_env:
        mock_env.side_effect = Exception("Environment error")

        with pytest.raises(RuntimeError, match="Failed to inspect installed packages"):
            inspect_installed_packages(timeout=10)


def test_inspect_installed_packages_complex_dependencies(mock_distribution):
    """Test packages with complex dependency specifications."""
    dists = [
        mock_distribution("complex-pkg", "1.0.0", [
            "requests>=2.28.0,<3.0.0",
            "numpy>=1.20.0,!=1.21.0,<2.0.0",
            "pandas[excel]>=1.5.0",
            "scipy~=1.9.0",
            "matplotlib>3.0.0,<=3.6.0",
            "typing-extensions>=4.0.0; python_version >= '3.0'",  # Marker that's True
            "importlib-metadata>=1.0; python_version < '3.8'",  # Marker that's False - skipped
            "dask<2025.3.0; extra == 'dask'",  # Extra marker - always skipped
            "unconstrained-package",  # Should not appear in constrained_dependencies
        ]),
    ]

    with patch("pipu_cli.package_management.get_default_environment") as mock_env, \
         patch("pipu_cli.package_management._get_editable_packages") as mock_edit:

        mock_env.return_value.iter_all_distributions.return_value = dists
        mock_edit.return_value = {}

        result = inspect_installed_packages(timeout=10)

    assert len(result) == 1
    pkg = result[0]

    # Note: packaging may reorder specifiers, so we match what it actually produces
    # Markers that evaluate to False (python_version < '3.8') are skipped
    # Extra markers (extra == 'dask') are always skipped
    assert pkg.constrained_dependencies == {
        "requests": "<3.0.0,>=2.28.0",  # Reordered by packaging
        "numpy": "!=1.21.0,<2.0.0,>=1.20.0",
        "pandas": ">=1.5.0",
        "scipy": "~=1.9.0",
        "matplotlib": "<=3.6.0,>3.0.0",  # Reordered by packaging
        "typing-extensions": ">=4.0.0",  # Marker evaluates to True
    }
    # unconstrained-package, importlib-metadata, dask should not be in the dict
    assert "unconstrained-package" not in pkg.constrained_dependencies


def test_inspect_installed_packages_all_editable(mock_distribution, mock_pip_list_output):
    """Test when all packages are editable."""
    dists = [
        mock_distribution("pkg1", "1.0.0", []),
        mock_distribution("pkg2", "2.0.0", []),
    ]

    editable_output = mock_pip_list_output([
        ("pkg1", "1.0.0", "/home/user/pkg1"),
        ("pkg2", "2.0.0", "/home/user/pkg2"),
    ])

    with patch("pipu_cli.package_management.get_default_environment") as mock_env, \
         patch("subprocess.run") as mock_run:

        mock_env.return_value.iter_all_distributions.return_value = dists
        mock_run.return_value = Mock(stdout=editable_output, returncode=0)

        result = inspect_installed_packages(timeout=10)

    assert len(result) == 2
    assert all(p.is_editable for p in result)


def test_inspect_installed_packages_name_canonicalization(mock_distribution):
    """Test that package names with different formats are handled correctly."""
    dists = [
        mock_distribution("My_Package", "1.0.0", [
            "Some-Dep>=1.0.0",
            "another.dep>=2.0.0",
        ]),
    ]

    # Editable list uses a different format
    with patch("pipu_cli.package_management.get_default_environment") as mock_env, \
         patch("pipu_cli.package_management._get_editable_packages") as mock_edit:

        mock_env.return_value.iter_all_distributions.return_value = dists
        # Editable packages uses canonical name
        mock_edit.return_value = {"my-package": "/home/user/my-package"}

        result = inspect_installed_packages(timeout=10)

    assert len(result) == 1
    pkg = result[0]

    # Original name should be preserved in output
    assert pkg.name == "My_Package"
    # But editable check should work via canonicalization
    assert pkg.is_editable is True
    # Dependencies should be canonicalized
    assert pkg.constrained_dependencies == {
        "some-dep": ">=1.0.0",
        "another-dep": ">=2.0.0",
    }


def test_inspect_installed_packages_timeout_parameter(mock_distribution):
    """Test that timeout parameter is passed to editable packages function."""
    dists = [mock_distribution("pkg", "1.0.0", [])]

    with patch("pipu_cli.package_management.get_default_environment") as mock_env, \
         patch("pipu_cli.package_management._get_editable_packages") as mock_edit:

        mock_env.return_value.iter_all_distributions.return_value = dists
        mock_edit.return_value = {}

        inspect_installed_packages(timeout=30)

        # Verify timeout was passed
        mock_edit.assert_called_once_with(30)


# ============================================================================
# Tests for PackageInfo dataclass
# ============================================================================

def test_package_info_creation():
    """Test PackageInfo dataclass creation."""
    pkg = InstalledPackage(
        name="test-package",
        version=Version("1.2.3"),
        is_editable=True,
        constrained_dependencies={"dep1": ">=1.0.0"}
    )

    assert pkg.name == "test-package"
    assert pkg.version == Version("1.2.3")
    assert pkg.is_editable is True
    assert pkg.constrained_dependencies == {"dep1": ">=1.0.0"}


def test_package_info_default_dependencies():
    """Test that constrained_dependencies defaults to empty dict."""
    pkg = InstalledPackage(
        name="test-package",
        version=Version("1.0.0"),
        is_editable=False
    )

    assert pkg.constrained_dependencies == {}


def test_package_info_version_comparison():
    """Test that Version objects can be compared."""
    pkg1 = InstalledPackage(
        name="pkg",
        version=Version("1.0.0"),
        is_editable=False
    )
    pkg2 = InstalledPackage(
        name="pkg",
        version=Version("2.0.0"),
        is_editable=False
    )

    assert pkg1.version < pkg2.version
    assert pkg2.version > pkg1.version
    assert pkg1.version != pkg2.version


# ============================================================================
# Tests for get_latest_versions
# ============================================================================

@pytest.fixture
def mock_package_finder():
    """Create a mock PackageFinder with configurable candidates."""
    def _create_finder(packages_config):
        """
        Create a mock package finder.

        :param packages_config: Dict mapping package names to list of version strings
        """
        finder = Mock()

        def find_all_candidates(package_name):
            """Mock find_all_candidates method."""
            versions = packages_config.get(package_name, [])
            candidates = []
            for version_str in versions:
                candidate = Mock()
                candidate.version = Version(version_str)
                # Mock link for file type detection
                candidate.link = Mock()
                candidate.link.filename = f"{package_name}-{version_str}-py3-none-any.whl"
                candidates.append(candidate)
            return candidates

        finder.find_all_candidates = Mock(side_effect=find_all_candidates)
        return finder

    return _create_finder


@pytest.fixture
def mock_pip_config():
    """Create a mock pip Configuration object."""
    def _create_config(index_url=None, extra_index_urls=None, trusted_hosts=None):
        """
        Create a mock pip configuration.

        :param index_url: Primary index URL (default: None)
        :param extra_index_urls: List or string of extra index URLs (default: None)
        :param trusted_hosts: List or string of trusted hosts (default: None)
        """
        config = Mock()

        def get_value(key):
            if key == "global.index-url":
                return index_url
            elif key == "global.extra-index-url":
                return extra_index_urls
            elif key == "global.trusted-host":
                return trusted_hosts
            return None

        config.get_value = Mock(side_effect=get_value)
        config.load = Mock()
        return config

    return _create_config


def test_get_latest_versions_basic(mock_package_finder, mock_pip_config):
    """Test basic functionality with a few packages."""
    installed_packages = [
        InstalledPackage(name="requests", version=Version("2.28.0"), is_editable=False),
        InstalledPackage(name="numpy", version=Version("1.23.0"), is_editable=False),
    ]

    # Configure mock finder with latest versions
    finder = mock_package_finder({
        "requests": ["2.28.0", "2.28.1", "2.29.0"],
        "numpy": ["1.23.0", "1.23.5", "1.24.0", "1.24.1"],
    })

    # Configure mock config
    config = mock_pip_config(index_url="https://pypi.org/simple/")

    with patch("pipu_cli.package_management.Configuration") as mock_config_class, \
         patch("pipu_cli.package_management.PipSession") as mock_session_class, \
         patch("pipu_cli.package_management.PackageFinder") as mock_finder_class:

        mock_config_class.return_value = config
        mock_session_class.return_value = Mock()
        mock_finder_class.create.return_value = finder

        result = get_latest_versions(installed_packages, timeout=10)

    # Check results
    assert len(result) == 2

    # Find the installed packages in results (they're used as keys)
    requests_installed = next(pkg for pkg in installed_packages if pkg.name == "requests")
    numpy_installed = next(pkg for pkg in installed_packages if pkg.name == "numpy")

    assert requests_installed in result
    assert result[requests_installed].name == "requests"
    assert result[requests_installed].version == Version("2.29.0")

    assert numpy_installed in result
    assert result[numpy_installed].name == "numpy"
    assert result[numpy_installed].version == Version("1.24.1")


def test_get_latest_versions_with_extra_index_urls(mock_package_finder, mock_pip_config):
    """Test that extra index URLs are properly parsed and used."""
    installed_packages = [
        InstalledPackage(name="mypackage", version=Version("1.0.0"), is_editable=False),
    ]

    finder = mock_package_finder({
        "mypackage": ["1.0.0", "1.1.0"],
    })

    # Test with newline-separated extra index URLs
    extra_urls_str = "https://private.pypi.org/simple/\nhttps://internal.pypi.org/simple/"
    config = mock_pip_config(
        index_url="https://pypi.org/simple/",
        extra_index_urls=extra_urls_str
    )

    with patch("pipu_cli.package_management.Configuration") as mock_config_class, \
         patch("pipu_cli.package_management.PipSession") as mock_session_class, \
         patch("pipu_cli.package_management.SearchScope") as mock_search_scope, \
         patch("pipu_cli.package_management.PackageFinder") as mock_finder_class:

        mock_config_class.return_value = config
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_finder_class.create.return_value = finder

        result = get_latest_versions(installed_packages, timeout=10)

        # Verify SearchScope.create was called with all index URLs
        mock_search_scope.create.assert_called_once()
        call_kwargs = mock_search_scope.create.call_args[1]
        assert "https://pypi.org/simple/" in call_kwargs["index_urls"]
        assert "https://private.pypi.org/simple/" in call_kwargs["index_urls"]
        assert "https://internal.pypi.org/simple/" in call_kwargs["index_urls"]

    assert len(result) == 1


def test_get_latest_versions_with_trusted_hosts(mock_package_finder, mock_pip_config):
    """Test that trusted hosts are properly added to the session."""
    installed_packages = [
        InstalledPackage(name="mypackage", version=Version("1.0.0"), is_editable=False),
    ]

    finder = mock_package_finder({
        "mypackage": ["1.0.0", "1.1.0"],
    })

    # Test with newline-separated trusted hosts
    trusted_hosts_str = "private.pypi.org\ninternal.pypi.org"
    config = mock_pip_config(
        index_url="https://pypi.org/simple/",
        trusted_hosts=trusted_hosts_str
    )

    with patch("pipu_cli.package_management.Configuration") as mock_config_class, \
         patch("pipu_cli.package_management.PipSession") as mock_session_class, \
         patch("pipu_cli.package_management.PackageFinder") as mock_finder_class:

        mock_config_class.return_value = config
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_finder_class.create.return_value = finder

        result = get_latest_versions(installed_packages, timeout=10)

        # Verify add_trusted_host was called for each host
        assert mock_session.add_trusted_host.call_count == 2
        calls = [call[0][0] for call in mock_session.add_trusted_host.call_args_list]
        assert "private.pypi.org" in calls
        assert "internal.pypi.org" in calls

    assert len(result) == 1


def test_get_latest_versions_filters_prereleases(mock_package_finder, mock_pip_config):
    """Test that pre-release versions are filtered out by default."""
    installed_packages = [
        InstalledPackage(name="django", version=Version("4.0.0"), is_editable=False),
    ]

    finder = mock_package_finder({
        "django": ["4.0.0", "4.1.0", "4.2.0a1", "4.2.0b1", "4.2.0rc1"],
    })

    config = mock_pip_config(index_url="https://pypi.org/simple/")

    with patch("pipu_cli.package_management.Configuration") as mock_config_class, \
         patch("pipu_cli.package_management.PipSession") as mock_session_class, \
         patch("pipu_cli.package_management.PackageFinder") as mock_finder_class:

        mock_config_class.return_value = config
        mock_session_class.return_value = Mock()
        mock_finder_class.create.return_value = finder

        result = get_latest_versions(installed_packages, timeout=10, include_prereleases=False)

    # Should get 4.1.0, not any of the pre-releases
    django_installed = installed_packages[0]
    assert django_installed in result
    assert result[django_installed].version == Version("4.1.0")


def test_get_latest_versions_includes_prereleases_when_requested(mock_package_finder, mock_pip_config):
    """Test that pre-release versions are included when requested."""
    installed_packages = [
        InstalledPackage(name="django", version=Version("4.0.0"), is_editable=False),
    ]

    finder = mock_package_finder({
        "django": ["4.0.0", "4.1.0", "4.2.0a1", "4.2.0b1", "4.2.0rc1"],
    })

    config = mock_pip_config(index_url="https://pypi.org/simple/")

    with patch("pipu_cli.package_management.Configuration") as mock_config_class, \
         patch("pipu_cli.package_management.PipSession") as mock_session_class, \
         patch("pipu_cli.package_management.PackageFinder") as mock_finder_class:

        mock_config_class.return_value = config
        mock_session_class.return_value = Mock()
        mock_finder_class.create.return_value = finder

        result = get_latest_versions(installed_packages, timeout=10, include_prereleases=True)

    # Should get the latest pre-release
    django_installed = installed_packages[0]
    assert django_installed in result
    assert result[django_installed].version == Version("4.2.0rc1")


def test_get_latest_versions_no_candidates_found(mock_package_finder, mock_pip_config):
    """Test handling when no candidates are found for a package."""
    installed_packages = [
        InstalledPackage(name="nonexistent", version=Version("1.0.0"), is_editable=False),
        InstalledPackage(name="requests", version=Version("2.28.0"), is_editable=False),
    ]

    finder = mock_package_finder({
        "requests": ["2.28.0", "2.29.0"],
        # nonexistent has no candidates
    })

    config = mock_pip_config(index_url="https://pypi.org/simple/")

    with patch("pipu_cli.package_management.Configuration") as mock_config_class, \
         patch("pipu_cli.package_management.PipSession") as mock_session_class, \
         patch("pipu_cli.package_management.PackageFinder") as mock_finder_class:

        mock_config_class.return_value = config
        mock_session_class.return_value = Mock()
        mock_finder_class.create.return_value = finder

        result = get_latest_versions(installed_packages, timeout=10)

    # Should only have results for requests
    assert len(result) == 1
    requests_installed = next(pkg for pkg in installed_packages if pkg.name == "requests")
    assert requests_installed in result


def test_get_latest_versions_package_query_error(mock_package_finder, mock_pip_config):
    """Test that errors querying individual packages are handled gracefully."""
    installed_packages = [
        InstalledPackage(name="error-package", version=Version("1.0.0"), is_editable=False),
        InstalledPackage(name="good-package", version=Version("1.0.0"), is_editable=False),
    ]

    finder = Mock()

    def find_all_candidates_with_error(package_name):
        if package_name == "error-package":
            raise Exception("Network error")
        return [Mock(version=Version("2.0.0"))]

    finder.find_all_candidates = Mock(side_effect=find_all_candidates_with_error)

    config = mock_pip_config(index_url="https://pypi.org/simple/")

    with patch("pipu_cli.package_management.Configuration") as mock_config_class, \
         patch("pipu_cli.package_management.PipSession") as mock_session_class, \
         patch("pipu_cli.package_management.PackageFinder") as mock_finder_class:

        mock_config_class.return_value = config
        mock_session_class.return_value = Mock()
        mock_finder_class.create.return_value = finder

        result = get_latest_versions(installed_packages, timeout=10)

    # Should only have results for good-package
    assert len(result) == 1
    good_pkg = next(pkg for pkg in installed_packages if pkg.name == "good-package")
    assert good_pkg in result


def test_get_latest_versions_config_load_failure(mock_package_finder):
    """Test handling when pip configuration cannot be loaded."""
    installed_packages = [
        InstalledPackage(name="requests", version=Version("2.28.0"), is_editable=False),
    ]

    finder = mock_package_finder({
        "requests": ["2.28.0", "2.29.0"],
    })

    with patch("pipu_cli.package_management.Configuration") as mock_config_class, \
         patch("pipu_cli.package_management.PipSession") as mock_session_class, \
         patch("pipu_cli.package_management.PackageFinder") as mock_finder_class:

        # Simulate config load failure
        mock_config_class.side_effect = Exception("Config error")
        mock_session_class.return_value = Mock()
        mock_finder_class.create.return_value = finder

        result = get_latest_versions(installed_packages, timeout=10)

    # Should still work with default PyPI
    assert len(result) == 1


def test_get_latest_versions_session_creation_failure():
    """Test that ConnectionError is raised if session creation fails."""
    installed_packages = [
        InstalledPackage(name="requests", version=Version("2.28.0"), is_editable=False),
    ]

    with patch("pipu_cli.package_management.Configuration") as mock_config_class, \
         patch("pipu_cli.package_management.PipSession") as mock_session_class:

        mock_config = Mock()
        mock_config.load = Mock()
        mock_config.get_value = Mock(return_value=None)
        mock_config_class.return_value = mock_config

        # Simulate session creation failure
        mock_session_class.side_effect = Exception("Network error")

        with pytest.raises(ConnectionError, match="Failed to create network session"):
            get_latest_versions(installed_packages, timeout=10)


def test_get_latest_versions_empty_input(mock_pip_config):
    """Test handling of empty installed packages list."""
    installed_packages = []

    config = mock_pip_config(index_url="https://pypi.org/simple/")

    with patch("pipu_cli.package_management.Configuration") as mock_config_class, \
         patch("pipu_cli.package_management.PipSession") as mock_session_class, \
         patch("pipu_cli.package_management.PackageFinder") as mock_finder_class:

        mock_config_class.return_value = config
        mock_session_class.return_value = Mock()
        mock_finder_class.create.return_value = Mock()

        result = get_latest_versions(installed_packages, timeout=10)

    assert result == {}


def test_get_latest_versions_preserves_package_name_format(mock_package_finder, mock_pip_config):
    """Test that the original package name format is preserved in results."""
    installed_packages = [
        InstalledPackage(name="Django", version=Version("4.0.0"), is_editable=False),
        InstalledPackage(name="PIL_Fork", version=Version("1.0.0"), is_editable=False),
    ]

    finder = mock_package_finder({
        "django": ["4.0.0", "4.1.0"],  # Canonical name used for lookup
        "pil-fork": ["1.0.0", "1.1.0"],
    })

    config = mock_pip_config(index_url="https://pypi.org/simple/")

    with patch("pipu_cli.package_management.Configuration") as mock_config_class, \
         patch("pipu_cli.package_management.PipSession") as mock_session_class, \
         patch("pipu_cli.package_management.PackageFinder") as mock_finder_class:

        mock_config_class.return_value = config
        mock_session_class.return_value = Mock()
        mock_finder_class.create.return_value = finder

        result = get_latest_versions(installed_packages, timeout=10)

    # Original names should be preserved in Package objects
    django_installed = next(pkg for pkg in installed_packages if pkg.name == "Django")
    assert result[django_installed].name == "Django"

    pil_installed = next(pkg for pkg in installed_packages if pkg.name == "PIL_Fork")
    assert result[pil_installed].name == "PIL_Fork"


def test_get_latest_versions_only_prereleases_available(mock_package_finder, mock_pip_config):
    """Test handling when only pre-release versions are available."""
    installed_packages = [
        InstalledPackage(name="newpkg", version=Version("0.1.0a1"), is_editable=False),
    ]

    finder = mock_package_finder({
        "newpkg": ["0.1.0a1", "0.1.0a2", "0.1.0b1"],
    })

    config = mock_pip_config(index_url="https://pypi.org/simple/")

    with patch("pipu_cli.package_management.Configuration") as mock_config_class, \
         patch("pipu_cli.package_management.PipSession") as mock_session_class, \
         patch("pipu_cli.package_management.PackageFinder") as mock_finder_class:

        mock_config_class.return_value = config
        mock_session_class.return_value = Mock()
        mock_finder_class.create.return_value = finder

        result = get_latest_versions(installed_packages, timeout=10, include_prereleases=False)

    # Should fall back to pre-releases if no stable versions available
    newpkg_installed = installed_packages[0]
    assert newpkg_installed in result
    assert result[newpkg_installed].version == Version("0.1.0b1")


def test_get_latest_versions_custom_timeout(mock_package_finder, mock_pip_config):
    """Test that custom timeout is applied to the session."""
    installed_packages = [
        InstalledPackage(name="requests", version=Version("2.28.0"), is_editable=False),
    ]

    finder = mock_package_finder({
        "requests": ["2.28.0", "2.29.0"],
    })

    config = mock_pip_config(index_url="https://pypi.org/simple/")

    with patch("pipu_cli.package_management.Configuration") as mock_config_class, \
         patch("pipu_cli.package_management.PipSession") as mock_session_class, \
         patch("pipu_cli.package_management.PackageFinder") as mock_finder_class:

        mock_config_class.return_value = config
        mock_session = Mock()
        mock_session_class.return_value = mock_session
        mock_finder_class.create.return_value = finder

        result = get_latest_versions(installed_packages, timeout=30)

        # Verify timeout was set
        assert mock_session.timeout == 30

    assert len(result) == 1


def test_get_latest_versions_config_with_comments(mock_package_finder, mock_pip_config):
    """Test that comments in config values are filtered out."""
    installed_packages = [
        InstalledPackage(name="requests", version=Version("2.28.0"), is_editable=False),
    ]

    finder = mock_package_finder({
        "requests": ["2.28.0", "2.29.0"],
    })

    # Config with comments and blank lines
    extra_urls_with_comments = """https://private.pypi.org/simple/
# This is a comment
https://internal.pypi.org/simple/

# Another comment
"""

    config = mock_pip_config(
        index_url="https://pypi.org/simple/",
        extra_index_urls=extra_urls_with_comments
    )

    with patch("pipu_cli.package_management.Configuration") as mock_config_class, \
         patch("pipu_cli.package_management.PipSession") as mock_session_class, \
         patch("pipu_cli.package_management.SearchScope") as mock_search_scope, \
         patch("pipu_cli.package_management.PackageFinder") as mock_finder_class:

        mock_config_class.return_value = config
        mock_session_class.return_value = Mock()
        mock_finder_class.create.return_value = finder

        result = get_latest_versions(installed_packages, timeout=10)

        # Verify SearchScope was called without comments or blank lines
        call_kwargs = mock_search_scope.create.call_args[1]
        index_urls = call_kwargs["index_urls"]

        # Should have 3 URLs, no comments
        assert len(index_urls) == 3
        assert all(not url.startswith('#') for url in index_urls)

    assert len(result) == 1


def test_get_latest_versions_parallel_closes_sessions(monkeypatch):
    installed_pkg = package_management.InstalledPackage(
        name="demo",
        version=Version("1.0.0"),
    )
    closed = []

    class FakeSession:
        def close(self):
            closed.append(True)

    class FakeFinder:
        def find_all_candidates(self, canonical_name):
            return []

    def fake_build_session(*, timeout, include_prereleases=False):
        return FakeSession(), FakeFinder()

    monkeypatch.setattr(package_management, "_build_pip_session", fake_build_session)

    package_management.get_latest_versions_parallel([installed_pkg])

    assert closed == [True]


def test_get_latest_versions_reports_progress_after_each_package(monkeypatch):
    pkgs = [
        package_management.InstalledPackage(name=f"pkg{i}", version=Version("1.0.0"))
        for i in range(3)
    ]
    callback_calls = []

    def fake_fetch(installed_pkg, *, include_prereleases, timeout):
        return installed_pkg, package_management.Package(installed_pkg.name, Version("1.2.0"))

    monkeypatch.setattr(package_management, "_fetch_latest_version", fake_fetch)

    package_management.get_latest_versions(
        pkgs,
        progress_callback=lambda current, total: callback_calls.append((current, total)),
    )

    assert callback_calls == [(1, 3), (2, 3), (3, 3), (3, 3)]


# ============================================================================
# Tests for resolve_upgradable_packages
# ============================================================================

def test_resolve_upgradable_packages_no_constraints():
    """Test packages with no constraints blocking them."""
    installed_a = InstalledPackage(name="package-a", version=Version("1.0.0"), is_editable=False)
    installed_b = InstalledPackage(name="package-b", version=Version("2.0.0"), is_editable=False)

    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("1.5.0")),
        installed_b: Package(name="package-b", version=Version("2.5.0")),
    }

    all_installed = [installed_a, installed_b]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 2

    # Both should be upgradable
    a_result = next(r for r in result if r.name == "package-a")
    assert a_result.upgradable is True
    assert a_result.latest_version == Version("1.5.0")
    assert a_result.version == Version("1.0.0")

    b_result = next(r for r in result if r.name == "package-b")
    assert b_result.upgradable is True
    assert b_result.latest_version == Version("2.5.0")


def test_resolve_upgradable_packages_constraint_satisfied():
    """Test package upgrade that satisfies existing constraints."""
    # Package A depends on B<3.0
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "<3.0"},
        is_editable=False
    )
    installed_b = InstalledPackage(name="package-b", version=Version("2.0.0"), is_editable=False)

    # B upgrades to 2.5.0, which still satisfies A's constraint
    upgrade_candidates = {
        installed_b: Package(name="package-b", version=Version("2.5.0")),
    }

    all_installed = [installed_a, installed_b]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 1
    b_result = result[0]
    assert b_result.name == "package-b"
    assert b_result.upgradable is True  # Constraint satisfied


def test_resolve_upgradable_packages_constraint_violated_non_upgrading():
    """Test package upgrade blocked by constraint from non-upgrading package."""
    # Package A depends on B<3.0
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "<3.0"},
        is_editable=False
    )
    installed_b = InstalledPackage(name="package-b", version=Version("2.0.0"), is_editable=False)

    # B wants to upgrade to 3.5.0, which violates A's constraint
    # A is NOT being upgraded
    upgrade_candidates = {
        installed_b: Package(name="package-b", version=Version("3.5.0")),
    }

    all_installed = [installed_a, installed_b]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 1
    b_result = result[0]
    assert b_result.name == "package-b"
    assert b_result.upgradable is False  # Blocked by A's constraint


def test_resolve_upgradable_packages_constraint_violated_both_upgrading():
    """Test package upgrade allowed when both packages are upgrading."""
    # Package A depends on B<3.0
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "<3.0"},
        is_editable=False
    )
    installed_b = InstalledPackage(name="package-b", version=Version("2.0.0"), is_editable=False)

    # Both A and B are upgrading, B to 3.5.0 which violates A's constraint
    # But since A is also upgrading, we allow B to upgrade
    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("2.0.0")),
        installed_b: Package(name="package-b", version=Version("3.5.0")),
    }

    all_installed = [installed_a, installed_b]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 2

    a_result = next(r for r in result if r.name == "package-a")
    assert a_result.upgradable is True

    b_result = next(r for r in result if r.name == "package-b")
    assert b_result.upgradable is True  # Allowed because A is also upgrading


def test_resolve_upgradable_packages_checks_target_constraint_when_both_upgrading():
    """A target package that still pins a dependency keeps blocking that dependency."""
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "<3.0"},
        is_editable=False,
    )
    installed_b = InstalledPackage(
        name="package-b", version=Version("2.0.0"), is_editable=False
    )
    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("2.0.0")),
        installed_b: Package(name="package-b", version=Version("3.5.0")),
    }

    upgradable, blocked = resolve_upgradable_packages_with_reasons(
        upgrade_candidates,
        [installed_a, installed_b],
        target_constraints={"package-a": {"package-b": "<3.0"}},
    )

    assert [pkg.name for pkg in upgradable] == ["package-a"]
    assert [(pkg.name, pkg.blocked_by) for pkg in blocked] == [
        ("package-b", ["package-a target requires <3.0"])
    ]


def test_resolve_upgradable_packages_allows_relaxed_target_constraint():
    """A target package that relaxes its dependency pin allows the dependency upgrade."""
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "<3.0"},
        is_editable=False,
    )
    installed_b = InstalledPackage(
        name="package-b", version=Version("2.0.0"), is_editable=False
    )
    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("2.0.0")),
        installed_b: Package(name="package-b", version=Version("3.5.0")),
    }

    upgradable, blocked = resolve_upgradable_packages_with_reasons(
        upgrade_candidates,
        [installed_a, installed_b],
        target_constraints={"package-a": {"package-b": "<4.0"}},
    )

    assert [pkg.name for pkg in upgradable] == ["package-a", "package-b"]
    assert blocked == []


def test_resolve_upgradable_packages_blocks_tightened_target_constraint():
    """A target package can tighten a pin that the installed package allowed."""
    installed_marimo = InstalledPackage(
        name="marimo",
        version=Version("0.23.4"),
        constrained_dependencies={"jedi": ">=0.18.0"},
        is_editable=False,
    )
    installed_jedi = InstalledPackage(
        name="jedi", version=Version("0.19.2"), is_editable=False
    )
    upgrade_candidates = {
        installed_marimo: Package(name="marimo", version=Version("0.23.5")),
        installed_jedi: Package(name="jedi", version=Version("0.20.0")),
    }

    upgradable, blocked = resolve_upgradable_packages_with_reasons(
        upgrade_candidates,
        [installed_marimo, installed_jedi],
        target_constraints={"marimo": {"jedi": ">=0.18.0,<0.20.0"}},
    )

    assert [pkg.name for pkg in upgradable] == ["marimo"]
    assert [(pkg.name, pkg.blocked_by) for pkg in blocked] == [
        ("jedi", ["marimo target requires >=0.18.0,<0.20.0"])
    ]


def test_resolve_upgradable_packages_blocks_when_target_metadata_unavailable():
    """Missing target metadata fails closed for the disputed dependency edge."""
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "<3.0"},
        is_editable=False,
    )
    installed_b = InstalledPackage(
        name="package-b", version=Version("2.0.0"), is_editable=False
    )
    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("2.0.0")),
        installed_b: Package(name="package-b", version=Version("3.5.0")),
    }

    upgradable, blocked = resolve_upgradable_packages_with_reasons(
        upgrade_candidates,
        [installed_a, installed_b],
        target_constraints={"package-a": None},
    )

    assert [pkg.name for pkg in upgradable] == ["package-a"]
    assert [(pkg.name, pkg.blocked_by) for pkg in blocked] == [
        ("package-b", ["package-a target metadata unavailable"])
    ]


def test_get_target_constraints_for_disputed_upgrades_fetches_ambiguous_edges(monkeypatch):
    """Target metadata is fetched only for upgrading packages with disputed pins."""
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "<3.0"},
        is_editable=False,
    )
    installed_b = InstalledPackage(
        name="package-b", version=Version("2.0.0"), is_editable=False
    )
    installed_c = InstalledPackage(
        name="package-c", version=Version("1.0.0"), is_editable=False
    )
    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("2.0.0")),
        installed_b: Package(name="package-b", version=Version("3.5.0")),
        installed_c: Package(name="package-c", version=Version("1.1.0")),
    }
    fetched = []

    def fake_download(package, **_kwargs):
        fetched.append(package.name)
        return {"package-b": "<4.0"}

    monkeypatch.setattr(
        package_management, "_download_target_package_constraints", fake_download
    )

    result = package_management.get_target_constraints_for_disputed_upgrades(
        upgrade_candidates,
        [installed_a, installed_b, installed_c],
    )

    assert result == {"package-a": {"package-b": "<4.0"}}
    assert fetched == ["package-a"]


def test_get_target_constraints_fetches_satisfied_co_upgrade_edges(monkeypatch):
    """Fetch target metadata when a co-upgraded target may tighten a satisfied pin."""
    installed_marimo = InstalledPackage(
        name="marimo",
        version=Version("0.23.4"),
        constrained_dependencies={"jedi": ">=0.18.0"},
        is_editable=False,
    )
    installed_jedi = InstalledPackage(
        name="jedi", version=Version("0.19.2"), is_editable=False
    )
    upgrade_candidates = {
        installed_marimo: Package(name="marimo", version=Version("0.23.5")),
        installed_jedi: Package(name="jedi", version=Version("0.20.0")),
    }
    fetched = []

    def fake_download(package, **_kwargs):
        fetched.append(package.name)
        return {"jedi": ">=0.18.0,<0.20.0"}

    monkeypatch.setattr(
        package_management, "_download_target_package_constraints", fake_download
    )

    result = package_management.get_target_constraints_for_disputed_upgrades(
        upgrade_candidates,
        [installed_marimo, installed_jedi],
    )

    assert result == {"marimo": {"jedi": ">=0.18.0,<0.20.0"}}
    assert fetched == ["marimo"]


def test_get_target_constraints_for_disputed_upgrades_fails_closed(monkeypatch):
    """Failed target metadata fetches are recorded as unavailable."""
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "<3.0"},
        is_editable=False,
    )
    installed_b = InstalledPackage(
        name="package-b", version=Version("2.0.0"), is_editable=False
    )
    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("2.0.0")),
        installed_b: Package(name="package-b", version=Version("3.5.0")),
    }

    monkeypatch.setattr(
        package_management,
        "_download_target_package_constraints",
        lambda package, **_kwargs: None,
    )

    result = package_management.get_target_constraints_for_disputed_upgrades(
        upgrade_candidates,
        [installed_a, installed_b],
    )

    assert result == {"package-a": None}


def test_fetch_latest_version_respects_requested_specifier(monkeypatch):
    """Latest-version probing can target the newest version satisfying a spec."""
    from packaging.specifiers import SpecifierSet

    installed_jedi = InstalledPackage(
        name="jedi", version=Version("0.20.0"), is_editable=False
    )

    class Candidate:
        def __init__(self, version):
            self.version = version

    class Session:
        def close(self):
            pass

    class Finder:
        def find_all_candidates(self, canonical_name):
            assert canonical_name == "jedi"
            return [
                Candidate("0.18.2"),
                Candidate("0.19.2"),
                Candidate("0.20.0"),
            ]

    monkeypatch.setattr(
        package_management,
        "_build_pip_session",
        lambda **_kwargs: (Session(), Finder()),
    )

    result = package_management._fetch_latest_version(
        installed_jedi,
        specifier=SpecifierSet("<0.20.0,>=0.18.0"),
    )

    assert result == (
        installed_jedi,
        Package(name="jedi", version=Version("0.19.2")),
    )


def test_resolve_upgradable_packages_multiple_constraints_all_satisfied():
    """Test package with multiple constraints, all satisfied."""
    # A depends on C<3.0, B depends on C>=2.0,<4.0
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-c": "<3.0"},
        is_editable=False
    )
    installed_b = InstalledPackage(
        name="package-b",
        version=Version("1.0.0"),
        constrained_dependencies={"package-c": ">=2.0,<4.0"},
        is_editable=False
    )
    installed_c = InstalledPackage(name="package-c", version=Version("2.0.0"), is_editable=False)

    # C upgrades to 2.5.0, which satisfies both A's and B's constraints
    upgrade_candidates = {
        installed_c: Package(name="package-c", version=Version("2.5.0")),
    }

    all_installed = [installed_a, installed_b, installed_c]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 1
    c_result = result[0]
    assert c_result.name == "package-c"
    assert c_result.upgradable is True


def test_resolve_upgradable_packages_multiple_constraints_one_violated():
    """Test package with multiple constraints, one violated by non-upgrading package."""
    # A depends on C<3.0, B depends on C>=2.0,<4.0
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-c": "<3.0"},
        is_editable=False
    )
    installed_b = InstalledPackage(
        name="package-b",
        version=Version("1.0.0"),
        constrained_dependencies={"package-c": ">=2.0,<4.0"},
        is_editable=False
    )
    installed_c = InstalledPackage(name="package-c", version=Version("2.0.0"), is_editable=False)

    # C wants to upgrade to 3.5.0
    # Violates A's constraint (<3.0) but satisfies B's (>=2.0,<4.0)
    # Neither A nor B is upgrading
    upgrade_candidates = {
        installed_c: Package(name="package-c", version=Version("3.5.0")),
    }

    all_installed = [installed_a, installed_b, installed_c]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 1
    c_result = result[0]
    assert c_result.name == "package-c"
    assert c_result.upgradable is False  # Blocked by A's constraint


def test_resolve_upgradable_packages_multiple_constraints_one_upgrading():
    """Test package with multiple violated constraints, only one constrainer upgrading."""
    # A depends on C<3.0, B depends on C>=2.0,<4.0
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-c": "<3.0"},
        is_editable=False
    )
    installed_b = InstalledPackage(
        name="package-b",
        version=Version("1.0.0"),
        constrained_dependencies={"package-c": ">=2.0,<4.0"},
        is_editable=False
    )
    installed_c = InstalledPackage(name="package-c", version=Version("2.0.0"), is_editable=False)

    # C wants to upgrade to 3.5.0
    # Violates A's constraint (<3.0) but satisfies B's (>=2.0,<4.0)
    # Only B is upgrading, not A
    upgrade_candidates = {
        installed_b: Package(name="package-b", version=Version("2.0.0")),
        installed_c: Package(name="package-c", version=Version("3.5.0")),
    }

    all_installed = [installed_a, installed_b, installed_c]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 2

    c_result = next(r for r in result if r.name == "package-c")
    # Still blocked because A (which has violated constraint) is not upgrading
    assert c_result.upgradable is False


def test_resolve_upgradable_packages_multiple_constraints_all_violated_all_upgrading():
    """Test package with multiple violated constraints, all constrainers upgrading."""
    # A depends on C<2.0, B depends on C<2.5
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-c": "<2.0"},
        is_editable=False
    )
    installed_b = InstalledPackage(
        name="package-b",
        version=Version("1.0.0"),
        constrained_dependencies={"package-c": "<2.5"},
        is_editable=False
    )
    installed_c = InstalledPackage(name="package-c", version=Version("1.5.0"), is_editable=False)

    # C wants to upgrade to 3.0.0, violating both constraints
    # But both A and B are also upgrading
    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("2.0.0")),
        installed_b: Package(name="package-b", version=Version("2.0.0")),
        installed_c: Package(name="package-c", version=Version("3.0.0")),
    }

    all_installed = [installed_a, installed_b, installed_c]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 3

    c_result = next(r for r in result if r.name == "package-c")
    # Allowed because both A and B are upgrading
    assert c_result.upgradable is True


def test_resolve_upgradable_packages_already_at_latest():
    """Test package that's already at the latest version."""
    installed_a = InstalledPackage(name="package-a", version=Version("2.0.0"), is_editable=False)

    # "Latest" version is same as or older than current
    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("2.0.0")),
    }

    all_installed = [installed_a]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 1
    a_result = result[0]
    assert a_result.name == "package-a"
    assert a_result.upgradable is False  # Not actually an upgrade
    assert a_result.latest_version == Version("2.0.0")


def test_resolve_upgradable_packages_empty_candidates():
    """Test with empty upgrade candidates."""
    installed_a = InstalledPackage(name="package-a", version=Version("1.0.0"), is_editable=False)

    upgrade_candidates = {}
    all_installed = [installed_a]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert result == []


def test_resolve_upgradable_packages_editable_preserved():
    """Test that editable status is preserved in results."""
    installed_a = InstalledPackage(name="package-a", version=Version("1.0.0"), is_editable=True)
    installed_b = InstalledPackage(name="package-b", version=Version("1.0.0"), is_editable=False)

    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("1.5.0")),
        installed_b: Package(name="package-b", version=Version("1.5.0")),
    }

    all_installed = [installed_a, installed_b]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 2

    a_result = next(r for r in result if r.name == "package-a")
    assert a_result.is_editable is True

    b_result = next(r for r in result if r.name == "package-b")
    assert b_result.is_editable is False


def test_resolve_upgradable_packages_complex_chain():
    """Test complex dependency chain with multiple packages."""
    # Setup: A -> B, B -> C, D -> C
    # A depends on B<3.0
    # B depends on C<2.0
    # D depends on C>=1.5,<2.5
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "<3.0"},
        is_editable=False
    )
    installed_b = InstalledPackage(
        name="package-b",
        version=Version("2.0.0"),
        constrained_dependencies={"package-c": "<2.0"},
        is_editable=False
    )
    installed_c = InstalledPackage(name="package-c", version=Version("1.5.0"), is_editable=False)
    installed_d = InstalledPackage(
        name="package-d",
        version=Version("1.0.0"),
        constrained_dependencies={"package-c": ">=1.5,<2.5"},
        is_editable=False
    )

    # Scenario: B and C want to upgrade
    # B: 2.0.0 -> 2.5.0 (satisfies A's constraint <3.0)
    # C: 1.5.0 -> 2.2.0 (violates B's constraint <2.0, satisfies D's >=1.5,<2.5)
    upgrade_candidates = {
        installed_b: Package(name="package-b", version=Version("2.5.0")),
        installed_c: Package(name="package-c", version=Version("2.2.0")),
    }

    all_installed = [installed_a, installed_b, installed_c, installed_d]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 2

    b_result = next(r for r in result if r.name == "package-b")
    assert b_result.upgradable is True  # Satisfies A's constraint

    c_result = next(r for r in result if r.name == "package-c")
    # C's upgrade to 2.2.0 violates B's constraint (<2.0)
    # But B is upgrading, so C can upgrade
    assert c_result.upgradable is True


def test_resolve_upgradable_packages_name_canonicalization():
    """Test that package name canonicalization works correctly."""
    # Package with different name formats
    installed_a = InstalledPackage(
        name="Package_A",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "<3.0"},  # Canonical name
        is_editable=False
    )
    installed_b = InstalledPackage(name="Package-B", version=Version("2.0.0"), is_editable=False)

    # B wants to upgrade beyond A's constraint
    upgrade_candidates = {
        installed_b: Package(name="Package-B", version=Version("3.5.0")),
    }

    all_installed = [installed_a, installed_b]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 1
    b_result = result[0]
    assert b_result.name == "Package-B"
    assert b_result.upgradable is False  # Blocked despite name format differences


def test_resolve_upgradable_packages_invalid_specifier():
    """Test handling of invalid version specifiers."""
    # Package A has an invalid constraint
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "not-a-valid-specifier!!!"},
        is_editable=False
    )
    installed_b = InstalledPackage(name="package-b", version=Version("2.0.0"), is_editable=False)

    upgrade_candidates = {
        installed_b: Package(name="package-b", version=Version("2.5.0")),
    }

    all_installed = [installed_a, installed_b]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 1
    b_result = result[0]
    # Should be conservative and block the upgrade due to invalid specifier
    assert b_result.upgradable is False


def test_resolve_upgradable_matches_with_reasons():
    """Plain resolver must agree with the with-reasons resolver on upgradable names.

    Permanent regression guard: when resolve_upgradable_packages becomes a wrapper
    around resolve_upgradable_packages_with_reasons, this keeps us from ever
    letting the two implementations drift again.
    """
    # Reuse a constraint-violated scenario that exercises the fixed-point loop.
    # Mirrors test_resolve_upgradable_packages_constraint_violated_non_upgrading
    # but verifies both resolvers agree.
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "<3.0"},
        is_editable=False,
    )
    installed_b = InstalledPackage(
        name="package-b", version=Version("2.0.0"), is_editable=False
    )
    upgrade_candidates = {
        installed_b: Package(name="package-b", version=Version("3.5.0")),
    }
    all_installed = [installed_a, installed_b]

    plain = resolve_upgradable_packages(upgrade_candidates, all_installed)
    upgradable, _blocked = resolve_upgradable_packages_with_reasons(
        upgrade_candidates, all_installed
    )

    names_plain = {p.name for p in plain if p.upgradable}
    names_with_reasons = {p.name for p in upgradable}
    assert names_plain == names_with_reasons


def test_resolve_upgradable_packages_invalid_specifier_upgrading():
    """Test handling of invalid specifier when constraining package is upgrading."""
    # Package A has an invalid constraint but is upgrading
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "not-valid!!!"},
        is_editable=False
    )
    installed_b = InstalledPackage(name="package-b", version=Version("2.0.0"), is_editable=False)

    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("2.0.0")),
        installed_b: Package(name="package-b", version=Version("2.5.0")),
    }

    all_installed = [installed_a, installed_b]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 2

    b_result = next(r for r in result if r.name == "package-b")
    # Should be allowed since A is upgrading
    assert b_result.upgradable is True


def test_resolve_upgradable_packages_no_constraint_on_upgrading_package():
    """Test package with no constraints affecting it."""
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-c": "<2.0"},  # Constraint on different package
        is_editable=False
    )
    installed_b = InstalledPackage(name="package-b", version=Version("2.0.0"), is_editable=False)
    installed_c = InstalledPackage(name="package-c", version=Version("1.0.0"), is_editable=False)

    # Only B is upgrading, and it has no constraints on it
    upgrade_candidates = {
        installed_b: Package(name="package-b", version=Version("3.0.0")),
    }

    all_installed = [installed_a, installed_b, installed_c]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 1
    b_result = result[0]
    assert b_result.upgradable is True  # No constraints blocking it


# ============================================================================
# Tests for mutual constraints (packages constraining each other)
# ============================================================================

def test_resolve_upgradable_packages_mutual_constraints_both_upgrading():
    """Test mutual constraints where both packages are upgrading."""
    # A constrains B==2.0, B constrains A<3.0
    # Both want to upgrade: A: 1.0 -> 2.0, B: 2.0 -> 2.5
    # A's new version (2.0) satisfies B's constraint (<3.0)
    # B's new version (2.5) violates A's constraint (==2.0), but A is upgrading
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "==2.0"},
        is_editable=False
    )
    installed_b = InstalledPackage(
        name="package-b",
        version=Version("2.0.0"),
        constrained_dependencies={"package-a": "<3.0"},
        is_editable=False
    )

    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("2.0.0")),
        installed_b: Package(name="package-b", version=Version("2.5.0")),
    }

    all_installed = [installed_a, installed_b]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 2

    a_result = next(r for r in result if r.name == "package-a")
    # A can upgrade because B's constraint (<3.0) is satisfied by 2.0
    assert a_result.upgradable is True

    b_result = next(r for r in result if r.name == "package-b")
    # B can upgrade because A (which has violated constraint ==2.0) is also upgrading
    assert b_result.upgradable is True


def test_resolve_upgradable_packages_mutual_constraints_one_satisfies():
    """Test mutual constraints where only one package upgrades and satisfies constraint."""
    # A constrains B>=2.0,<3.0, B constrains A<4.0
    # Only A wants to upgrade: A: 1.0 -> 2.5
    # A's new version (2.5) satisfies B's constraint (<4.0)
    # B is not upgrading, A's constraint on B (>=2.0,<3.0) is already satisfied by current B (2.0)
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": ">=2.0,<3.0"},
        is_editable=False
    )
    installed_b = InstalledPackage(
        name="package-b",
        version=Version("2.0.0"),
        constrained_dependencies={"package-a": "<4.0"},
        is_editable=False
    )

    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("2.5.0")),
    }

    all_installed = [installed_a, installed_b]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 1

    a_result = result[0]
    assert a_result.name == "package-a"
    # A can upgrade because new version (2.5) satisfies B's constraint (<4.0)
    # and B is not changing (still satisfies A's constraint >=2.0,<3.0)
    assert a_result.upgradable is True


def test_resolve_upgradable_packages_mutual_constraints_one_violates():
    """Test mutual constraints where one upgrade violates the other's constraint."""
    # A constrains B>=2.0,<3.0, B constrains A<2.0
    # Only A wants to upgrade: A: 1.5 -> 2.5
    # A's new version (2.5) violates B's constraint (<2.0)
    # B is not upgrading, so A cannot upgrade
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.5.0"),
        constrained_dependencies={"package-b": ">=2.0,<3.0"},
        is_editable=False
    )
    installed_b = InstalledPackage(
        name="package-b",
        version=Version("2.0.0"),
        constrained_dependencies={"package-a": "<2.0"},
        is_editable=False
    )

    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("2.5.0")),
    }

    all_installed = [installed_a, installed_b]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 1

    a_result = result[0]
    assert a_result.name == "package-a"
    # A cannot upgrade because new version (2.5) violates B's constraint (<2.0)
    # and B is not upgrading
    assert a_result.upgradable is False


def test_resolve_upgradable_packages_mutual_constraints_both_violate_neither_upgrades():
    """Test mutual constraints where both would violate but only one tries to upgrade."""
    # A constrains B<2.0, B constrains A<2.0
    # Only B wants to upgrade: B: 1.5 -> 2.5
    # B's new version (2.5) violates A's constraint (<2.0)
    # A is not upgrading, so B cannot upgrade
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.5.0"),
        constrained_dependencies={"package-b": "<2.0"},
        is_editable=False
    )
    installed_b = InstalledPackage(
        name="package-b",
        version=Version("1.5.0"),
        constrained_dependencies={"package-a": "<2.0"},
        is_editable=False
    )

    upgrade_candidates = {
        installed_b: Package(name="package-b", version=Version("2.5.0")),
    }

    all_installed = [installed_a, installed_b]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 1

    b_result = result[0]
    assert b_result.name == "package-b"
    # B cannot upgrade because new version (2.5) violates A's constraint (<2.0)
    # and A is not upgrading
    assert b_result.upgradable is False


def test_resolve_upgradable_packages_mutual_constraints_strict_equality():
    """Test mutual constraints with strict equality requirements."""
    # A constrains B==2.0, B constrains A==1.5
    # Both at correct versions, both want to upgrade to new versions
    # Both violate each other's constraints but both are upgrading
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.5.0"),
        constrained_dependencies={"package-b": "==2.0"},
        is_editable=False
    )
    installed_b = InstalledPackage(
        name="package-b",
        version=Version("2.0.0"),
        constrained_dependencies={"package-a": "==1.5"},
        is_editable=False
    )

    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("2.0.0")),
        installed_b: Package(name="package-b", version=Version("3.0.0")),
    }

    all_installed = [installed_a, installed_b]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 2

    a_result = next(r for r in result if r.name == "package-a")
    # A can upgrade because B (which has constraint ==1.5 violated by A's 2.0) is upgrading
    assert a_result.upgradable is True

    b_result = next(r for r in result if r.name == "package-b")
    # B can upgrade because A (which has constraint ==2.0 violated by B's 3.0) is upgrading
    assert b_result.upgradable is True


def test_resolve_upgradable_packages_mutual_constraints_asymmetric_satisfaction():
    """Test mutual constraints where one satisfies and one violates."""
    # A constrains B>=2.0, B constrains A<2.0
    # A wants to upgrade: A: 1.0 -> 2.5
    # B wants to upgrade: B: 1.5 -> 2.5
    # A's new version (2.5) violates B's constraint (<2.0)
    # B's new version (2.5) satisfies A's constraint (>=2.0)
    # Both are upgrading
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": ">=2.0"},
        is_editable=False
    )
    installed_b = InstalledPackage(
        name="package-b",
        version=Version("1.5.0"),
        constrained_dependencies={"package-a": "<2.0"},
        is_editable=False
    )

    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("2.5.0")),
        installed_b: Package(name="package-b", version=Version("2.5.0")),
    }

    all_installed = [installed_a, installed_b]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 2

    a_result = next(r for r in result if r.name == "package-a")
    # A can upgrade because B (which has violated constraint) is also upgrading
    assert a_result.upgradable is True

    b_result = next(r for r in result if r.name == "package-b")
    # B can upgrade because its new version (2.5) satisfies A's constraint (>=2.0)
    # Even though A's constraint was violated before, A is also upgrading
    assert b_result.upgradable is True


def test_resolve_upgradable_packages_mutual_constraints_compatible_upgrades():
    """Test mutual constraints where both upgrades are compatible with constraints."""
    # A constrains B>=2.0,<4.0, B constrains A>=1.0,<3.0
    # Both want to upgrade to compatible versions
    # A: 1.0 -> 2.0 (satisfies B's constraint >=1.0,<3.0)
    # B: 2.0 -> 3.0 (satisfies A's constraint >=2.0,<4.0)
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": ">=2.0,<4.0"},
        is_editable=False
    )
    installed_b = InstalledPackage(
        name="package-b",
        version=Version("2.0.0"),
        constrained_dependencies={"package-a": ">=1.0,<3.0"},
        is_editable=False
    )

    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("2.0.0")),
        installed_b: Package(name="package-b", version=Version("3.0.0")),
    }

    all_installed = [installed_a, installed_b]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 2

    a_result = next(r for r in result if r.name == "package-a")
    # A can upgrade because new version (2.0) satisfies B's constraint (>=1.0,<3.0)
    assert a_result.upgradable is True

    b_result = next(r for r in result if r.name == "package-b")
    # B can upgrade because new version (3.0) satisfies A's constraint (>=2.0,<4.0)
    assert b_result.upgradable is True


def test_resolve_upgradable_packages_downgrade_scenario_blocks_dependent():
    """Test that when a package's 'latest' version is older than installed,
    it doesn't incorrectly allow its dependents to upgrade.

    Reproduces bug where:
    - ldclient 2024.1.4 is installed, but PyPI has 0.0.1 as "latest"
    - ldclient requires Deprecated==1.2.10
    - Deprecated wants to upgrade from 1.2.10 to 1.3.1

    Without fix: Deprecated would be marked upgradable because ldclient
    would be in upgrading_packages set (even though it's not really upgrading)

    With fix: Deprecated should NOT be upgradable because ldclient is not
    actually being upgraded (0.0.1 < 2024.1.4).
    """
    # ldclient: installed 2024.1.4, "latest" is 0.0.1 (actually older!)
    installed_ldclient = InstalledPackage(
        name="ldclient",
        version=Version("2024.1.4"),
        constrained_dependencies={"deprecated": "==1.2.10"},
        is_editable=False
    )

    # Deprecated: installed 1.2.10, latest is 1.3.1
    installed_deprecated = InstalledPackage(
        name="Deprecated",
        version=Version("1.2.10"),
        constrained_dependencies={},
        is_editable=False
    )

    upgrade_candidates = {
        installed_ldclient: Package(name="ldclient", version=Version("0.0.1")),  # Older!
        installed_deprecated: Package(name="Deprecated", version=Version("1.3.1")),
    }

    all_installed = [installed_ldclient, installed_deprecated]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 2

    # ldclient should NOT be upgradable (0.0.1 < 2024.1.4)
    ldclient_result = next(r for r in result if r.name == "ldclient")
    assert ldclient_result.upgradable is False

    # Deprecated should NOT be upgradable because:
    # - New version 1.3.1 violates ldclient's constraint ==1.2.10
    # - ldclient is NOT actually being upgraded
    deprecated_result = next(r for r in result if r.name == "Deprecated")
    assert deprecated_result.upgradable is False


def test_resolve_upgradable_packages_circular_constraint_blocks_dependent():
    """Test that packages blocked by constraints don't incorrectly allow their
    dependents to upgrade.

    Scenario:
    - Package A requires B==1.0 (exact version), A is NOT upgrading
    - Package B wants to upgrade 1.0 -> 2.0, but is blocked by A
    - Package B requires C<2.0
    - Package C wants to upgrade 1.5 -> 2.5

    Expected: C should NOT be upgradable because:
    - C 2.5 violates B's constraint (C<2.0)
    - B is NOT actually upgrading (blocked by A)

    Current bug: C is marked upgradable because B is in initial upgrading set.
    """
    # Package A: not upgrading, requires B==1.0
    installed_a = InstalledPackage(
        name="package-a",
        version=Version("1.0.0"),
        constrained_dependencies={"package-b": "==1.0.0"},
        is_editable=False
    )

    # Package B: wants to upgrade 1.0 -> 2.0, requires C<2.0
    installed_b = InstalledPackage(
        name="package-b",
        version=Version("1.0.0"),
        constrained_dependencies={"package-c": "<2.0"},
        is_editable=False
    )

    # Package C: wants to upgrade 1.5 -> 2.5
    installed_c = InstalledPackage(
        name="package-c",
        version=Version("1.5.0"),
        constrained_dependencies={},
        is_editable=False
    )

    upgrade_candidates = {
        installed_a: Package(name="package-a", version=Version("1.0.0")),  # Not upgrading
        installed_b: Package(name="package-b", version=Version("2.0.0")),
        installed_c: Package(name="package-c", version=Version("2.5.0")),
    }

    all_installed = [installed_a, installed_b, installed_c]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 3

    # A should not be upgradable (1.0.0 == 1.0.0)
    a_result = next(r for r in result if r.name == "package-a")
    assert a_result.upgradable is False

    # B should not be upgradable (violates A's constraint ==1.0.0)
    b_result = next(r for r in result if r.name == "package-b")
    assert b_result.upgradable is False

    # C should NOT be upgradable because:
    # - C 2.5 violates B's constraint (<2.0)
    # - B is NOT actually upgrading
    c_result = next(r for r in result if r.name == "package-c")
    assert c_result.upgradable is False


def test_resolve_upgradable_packages_wrapt_deprecated_ldclient_scenario():
    """Test the real-world scenario that uncovered the circular constraint bug.

    Real environment:
    - ldclient 2024.1.4 requires Deprecated==1.2.10, ldclient not upgrading
    - Deprecated 1.2.10 wants to upgrade to 1.3.1, requires wrapt<2
    - wrapt 1.17.3 wants to upgrade to 2.0.1

    Expected behavior:
    - ldclient: NOT upgradable (no newer version)
    - Deprecated: NOT upgradable (blocked by ldclient's exact version constraint)
    - wrapt: NOT upgradable (would violate Deprecated's constraint, and Deprecated not upgrading)

    Without fix: wrapt marked upgradable because Deprecated in initial upgrading set
    With fix: wrapt correctly blocked because Deprecated removed from upgrading set
    """
    # ldclient: not upgrading, requires Deprecated==1.2.10
    installed_ldclient = InstalledPackage(
        name="ldclient",
        version=Version("2024.1.4"),
        constrained_dependencies={"deprecated": "==1.2.10"},
        is_editable=False
    )

    # Deprecated: wants to upgrade, requires wrapt<2
    installed_deprecated = InstalledPackage(
        name="Deprecated",
        version=Version("1.2.10"),
        constrained_dependencies={"wrapt": "<2,>=1.10"},
        is_editable=False
    )

    # wrapt: wants to upgrade
    installed_wrapt = InstalledPackage(
        name="wrapt",
        version=Version("1.17.3"),
        constrained_dependencies={},
        is_editable=False
    )

    upgrade_candidates = {
        installed_ldclient: Package(name="ldclient", version=Version("2024.1.4")),  # No upgrade
        installed_deprecated: Package(name="Deprecated", version=Version("1.3.1")),
        installed_wrapt: Package(name="wrapt", version=Version("2.0.1")),
    }

    all_installed = [installed_ldclient, installed_deprecated, installed_wrapt]

    result = resolve_upgradable_packages(upgrade_candidates, all_installed)

    assert len(result) == 3

    # ldclient: not upgradable (no newer version)
    ldclient_result = next(r for r in result if r.name == "ldclient")
    assert ldclient_result.upgradable is False

    # Deprecated: not upgradable (blocked by ldclient's ==1.2.10 constraint)
    deprecated_result = next(r for r in result if r.name == "Deprecated")
    assert deprecated_result.upgradable is False

    # wrapt: NOT upgradable because:
    # - wrapt 2.0.1 violates Deprecated's constraint (<2)
    # - Deprecated is NOT actually upgrading (blocked by ldclient)
    # - Fixed-point iteration removes Deprecated first, then wrapt
    wrapt_result = next(r for r in result if r.name == "wrapt")
    assert wrapt_result.upgradable is False


# ============================================================================
# Tests for install_packages
# ============================================================================

def test_install_packages_successful_upgrade(mock_popen):
    """Test successful package upgrade."""
    packages_to_upgrade = [
        UpgradePackageInfo(
            name="requests",
            version=Version("2.28.0"),
            upgradable=True,
            latest_version=Version("2.29.0"),
            is_editable=False
        )
    ]

    # Mock the post-installation environment to show upgraded version
    mock_dist = Mock()
    mock_dist.metadata = {"name": "requests"}
    mock_dist.version = "2.29.0"

    # Create mock process with output
    mock_process = mock_popen(
        returncode=0,
        stdout_lines=["Successfully installed requests-2.29.0\n"],
        stderr_lines=[]
    )

    with patch("subprocess.Popen", return_value=mock_process), \
         patch("pipu_cli.package_management.get_default_environment") as mock_env:

        mock_env.return_value.iter_all_distributions.return_value = [mock_dist]

        result = install_packages(packages_to_upgrade, timeout=300)

    assert len(result) == 1
    assert result[0].name == "requests"
    assert result[0].upgraded is True
    assert result[0].version == Version("2.29.0")
    assert result[0].previous_version == Version("2.28.0")
    assert result[0].is_editable is False


def test_install_packages_multiple_packages(mock_popen):
    """Test upgrading multiple packages."""
    packages_to_upgrade = [
        UpgradePackageInfo(
            name="requests",
            version=Version("2.28.0"),
            upgradable=True,
            latest_version=Version("2.29.0"),
            is_editable=False
        ),
        UpgradePackageInfo(
            name="numpy",
            version=Version("1.23.0"),
            upgradable=True,
            latest_version=Version("1.24.0"),
            is_editable=False
        )
    ]

    # Mock post-installation environment showing both upgraded
    mock_dist1 = Mock()
    mock_dist1.metadata = {"name": "requests"}
    mock_dist1.version = "2.29.0"

    mock_dist2 = Mock()
    mock_dist2.metadata = {"name": "numpy"}
    mock_dist2.version = "1.24.0"

    # Create mock process with output
    mock_process = mock_popen(
        returncode=0,
        stdout_lines=["Successfully installed\n"],
        stderr_lines=[]
    )

    with patch("subprocess.Popen", return_value=mock_process), \
         patch("pipu_cli.package_management.get_default_environment") as mock_env:

        mock_env.return_value.iter_all_distributions.return_value = [mock_dist1, mock_dist2]

        result = install_packages(packages_to_upgrade, timeout=300)

    assert len(result) == 2
    assert all(pkg.upgraded for pkg in result)
    assert result[0].name == "requests"
    assert result[1].name == "numpy"


def test_install_packages_upgrade_failure(mock_popen):
    """Test handling of failed package upgrade."""
    packages_to_upgrade = [
        UpgradePackageInfo(
            name="problematic-pkg",
            version=Version("1.0.0"),
            upgradable=True,
            latest_version=Version("2.0.0"),
            is_editable=False
        )
    ]

    # Create mock process with failure
    mock_process = mock_popen(
        returncode=1,
        stdout_lines=[],
        stderr_lines=["ERROR: Could not find a version that satisfies the requirement\n"]
    )

    with patch("subprocess.Popen", return_value=mock_process):
        result = install_packages(packages_to_upgrade, timeout=300)

    assert len(result) == 1
    assert result[0].name == "problematic-pkg"
    assert result[0].upgraded is False
    assert result[0].version == Version("1.0.0")  # Still at old version
    assert result[0].previous_version == Version("1.0.0")


def test_install_packages_timeout(mock_popen):
    """Test handling of timeout during upgrade."""
    packages_to_upgrade = [
        UpgradePackageInfo(
            name="slow-pkg",
            version=Version("1.0.0"),
            upgradable=True,
            latest_version=Version("2.0.0"),
            is_editable=False
        )
    ]

    # Create mock process that times out
    mock_process = mock_popen(returncode=0, stdout_lines=[], stderr_lines=[])
    mock_process.poll.return_value = None
    mock_process.wait.side_effect = subprocess.TimeoutExpired("pip", 300)

    with patch("subprocess.Popen", return_value=mock_process):
        result = install_packages(packages_to_upgrade, timeout=300)

    assert len(result) == 1
    assert result[0].name == "slow-pkg"
    assert result[0].upgraded is False
    assert result[0].version == Version("1.0.0")
    assert result[0].failure_reason == "Installation timed out"


def test_install_packages_exception():
    """Test handling of unexpected exception during upgrade."""
    packages_to_upgrade = [
        UpgradePackageInfo(
            name="error-pkg",
            version=Version("1.0.0"),
            upgradable=True,
            latest_version=Version("2.0.0"),
            is_editable=False
        )
    ]

    with patch("subprocess.Popen") as mock_popen_class:
        mock_popen_class.side_effect = OSError("Disk full")

        result = install_packages(packages_to_upgrade, timeout=300)

    assert len(result) == 1
    assert result[0].name == "error-pkg"
    assert result[0].upgraded is False
    assert result[0].version == Version("1.0.0")


def test_install_packages_with_output_stream(mock_popen):
    """Test that output is streamed to provided stream."""
    from io import StringIO

    packages_to_upgrade = [
        UpgradePackageInfo(
            name="requests",
            version=Version("2.28.0"),
            upgradable=True,
            latest_version=Version("2.29.0"),
            is_editable=False
        )
    ]

    output_stream = StringIO()

    # Mock post-installation environment
    mock_dist = Mock()
    mock_dist.metadata = {"name": "requests"}
    mock_dist.version = "2.29.0"

    # Create mock process with output
    mock_process = mock_popen(
        returncode=0,
        stdout_lines=["Collecting requests\n", "Successfully installed requests-2.29.0\n"],
        stderr_lines=[]
    )

    with patch("subprocess.Popen", return_value=mock_process), \
         patch("pipu_cli.package_management.get_default_environment") as mock_env:

        mock_env.return_value.iter_all_distributions.return_value = [mock_dist]

        result = install_packages(packages_to_upgrade, output_stream=output_stream, timeout=300)

    assert len(result) == 1
    assert result[0].upgraded is True

    # Check that output was written to stream
    output = output_stream.getvalue()
    assert "Upgrading 1 package(s)" in output
    assert "Collecting requests" in output
    assert "Successfully installed requests-2.29.0" in output


def test_install_packages_with_output_stream_stderr(mock_popen):
    """Test that stderr is also streamed to output stream."""
    from io import StringIO

    packages_to_upgrade = [
        UpgradePackageInfo(
            name="requests",
            version=Version("2.28.0"),
            upgradable=True,
            latest_version=Version("2.29.0"),
            is_editable=False
        )
    ]

    output_stream = StringIO()

    # Mock post-installation environment
    mock_dist = Mock()
    mock_dist.metadata = {"name": "requests"}
    mock_dist.version = "2.29.0"

    # Create mock process with both stdout and stderr
    mock_process = mock_popen(
        returncode=0,
        stdout_lines=["Successfully installed requests-2.29.0\n"],
        stderr_lines=["WARNING: Some deprecation warning\n"]
    )

    with patch("subprocess.Popen", return_value=mock_process), \
         patch("pipu_cli.package_management.get_default_environment") as mock_env:

        mock_env.return_value.iter_all_distributions.return_value = [mock_dist]

        result = install_packages(packages_to_upgrade, output_stream=output_stream, timeout=300)

    assert len(result) == 1
    assert result[0].upgraded is True

    # Check that both stdout and stderr were written
    output = output_stream.getvalue()
    assert "Successfully installed requests-2.29.0" in output
    assert "WARNING: Some deprecation warning" in output


def test_install_packages_empty_list():
    """Test handling of empty package list."""
    result = install_packages([], timeout=300)
    assert result == []


def test_install_packages_editable_preserved(mock_popen):
    """Test that editable status is preserved in results."""
    packages_to_upgrade = [
        UpgradePackageInfo(
            name="my-editable-pkg",
            version=Version("1.0.0"),
            upgradable=True,
            latest_version=Version("1.5.0"),
            is_editable=True
        )
    ]

    # Mock post-installation environment
    mock_dist = Mock()
    mock_dist.metadata = {"name": "my-editable-pkg"}
    mock_dist.version = "1.5.0"

    # Create mock process
    mock_process = mock_popen(
        returncode=0,
        stdout_lines=["Successfully installed my-editable-pkg-1.5.0\n"],
        stderr_lines=[]
    )

    with patch("subprocess.Popen", return_value=mock_process), \
         patch("pipu_cli.package_management.get_default_environment") as mock_env:

        mock_env.return_value.iter_all_distributions.return_value = [mock_dist]

        result = install_packages(packages_to_upgrade, timeout=300)

    assert len(result) == 1
    assert result[0].is_editable is True
    assert result[0].upgraded is True


def test_install_packages_mixed_success_failure(mock_popen):
    """Test mixed success and failure scenarios.

    Simulates a case where pip succeeds overall, but some packages
    were not upgraded due to runtime constraints.
    """
    packages_to_upgrade = [
        UpgradePackageInfo(
            name="good-pkg",
            version=Version("1.0.0"),
            upgradable=True,
            latest_version=Version("1.5.0"),
            is_editable=False
        ),
        UpgradePackageInfo(
            name="bad-pkg",
            version=Version("2.0.0"),
            upgradable=True,
            latest_version=Version("2.5.0"),
            is_editable=False
        ),
        UpgradePackageInfo(
            name="another-good-pkg",
            version=Version("3.0.0"),
            upgradable=True,
            latest_version=Version("3.5.0"),
            is_editable=False
        )
    ]

    # Mock environment showing good-pkg and another-good-pkg upgraded, but bad-pkg stayed same
    mock_dist1 = Mock()
    mock_dist1.metadata = {"name": "good-pkg"}
    mock_dist1.version = "1.5.0"  # Upgraded

    mock_dist2 = Mock()
    mock_dist2.metadata = {"name": "bad-pkg"}
    mock_dist2.version = "2.0.0"  # NOT upgraded (still at old version)

    mock_dist3 = Mock()
    mock_dist3.metadata = {"name": "another-good-pkg"}
    mock_dist3.version = "3.5.0"  # Upgraded

    # Create mock process
    mock_process = mock_popen(
        returncode=0,
        stdout_lines=["Successfully installed good-pkg-1.5.0 another-good-pkg-3.5.0\n"],
        stderr_lines=[]
    )

    with patch("subprocess.Popen", return_value=mock_process), \
         patch("pipu_cli.package_management.get_default_environment") as mock_env:

        mock_env.return_value.iter_all_distributions.return_value = [mock_dist1, mock_dist2, mock_dist3]

        result = install_packages(packages_to_upgrade, timeout=300)

    assert len(result) == 3
    assert result[0].name == "good-pkg"
    assert result[0].upgraded is True
    assert result[1].name == "bad-pkg"
    assert result[1].upgraded is False
    assert result[2].name == "another-good-pkg"
    assert result[2].upgraded is True


def test_install_packages_correct_pip_command(mock_popen):
    """Test that correct pip command is constructed with all packages."""
    packages_to_upgrade = [
        UpgradePackageInfo(
            name="requests",
            version=Version("2.28.0"),
            upgradable=True,
            latest_version=Version("2.29.0"),
            is_editable=False
        ),
        UpgradePackageInfo(
            name="numpy",
            version=Version("1.23.0"),
            upgradable=True,
            latest_version=Version("1.24.0"),
            is_editable=False
        )
    ]

    # Mock post-installation environment
    mock_dist1 = Mock()
    mock_dist1.metadata = {"name": "requests"}
    mock_dist1.version = "2.29.0"

    mock_dist2 = Mock()
    mock_dist2.metadata = {"name": "numpy"}
    mock_dist2.version = "1.24.0"

    # Create mock process
    mock_process = mock_popen(returncode=0, stdout_lines=["Success\n"], stderr_lines=[])

    with patch("subprocess.Popen", return_value=mock_process) as mock_popen_class, \
         patch("pipu_cli.package_management.get_default_environment") as mock_env:

        mock_env.return_value.iter_all_distributions.return_value = [mock_dist1, mock_dist2]

        install_packages(packages_to_upgrade, timeout=300)

        # Verify the correct command was called
        mock_popen_class.assert_called_once()
        call_args = mock_popen_class.call_args
        cmd = call_args[0][0]

        # Check command structure - all packages in one command
        assert cmd[0] == sys.executable
        assert cmd[1:4] == ['-m', 'pip', 'install']
        assert '--upgrade' in cmd
        assert 'requests' in cmd
        assert 'numpy' in cmd


def test_install_packages_timeout_parameter(mock_popen):
    """Test that timeout parameter is passed to subprocess."""
    packages_to_upgrade = [
        UpgradePackageInfo(
            name="requests",
            version=Version("2.28.0"),
            upgradable=True,
            latest_version=Version("2.29.0"),
            is_editable=False
        )
    ]

    # Create mock process
    mock_process = mock_popen(returncode=0, stdout_lines=["Success\n"], stderr_lines=[])

    with patch("subprocess.Popen", return_value=mock_process):
        install_packages(packages_to_upgrade, timeout=500)

        # Verify timeout was passed to wait()
        mock_process.wait.assert_called_once_with(timeout=500)


def test_install_packages_output_stream_timeout_error(mock_popen):
    """Test that timeout error is written to output stream."""
    from io import StringIO

    packages_to_upgrade = [
        UpgradePackageInfo(
            name="pkg1",
            version=Version("1.0.0"),
            upgradable=True,
            latest_version=Version("2.0.0"),
            is_editable=False
        ),
        UpgradePackageInfo(
            name="pkg2",
            version=Version("1.0.0"),
            upgradable=True,
            latest_version=Version("2.0.0"),
            is_editable=False
        )
    ]

    output_stream = StringIO()

    # Create mock process that times out
    mock_process = mock_popen(returncode=0, stdout_lines=[], stderr_lines=[])
    mock_process.wait.side_effect = subprocess.TimeoutExpired("pip", 300)

    with patch("subprocess.Popen", return_value=mock_process):
        result = install_packages(packages_to_upgrade, output_stream=output_stream, timeout=300)

    assert len(result) == 2
    assert all(not pkg.upgraded for pkg in result)

    output = output_stream.getvalue()
    assert "ERROR: Timeout during package upgrade" in output


def test_install_packages_output_stream_exception_error():
    """Test that exception error is written to output stream."""
    from io import StringIO

    packages_to_upgrade = [
        UpgradePackageInfo(
            name="pkg1",
            version=Version("1.0.0"),
            upgradable=True,
            latest_version=Version("2.0.0"),
            is_editable=False
        )
    ]

    output_stream = StringIO()

    with patch("subprocess.Popen") as mock_popen_class:
        mock_popen_class.side_effect = OSError("Unexpected error")

        result = install_packages(packages_to_upgrade, output_stream=output_stream, timeout=300)

    assert len(result) == 1
    assert not result[0].upgraded

    output = output_stream.getvalue()
    assert "ERROR: Failed to upgrade packages" in output


# ============================================================================
# Tests for real-time streaming functionality
# ============================================================================


def test_install_packages_streams_output_progressively(mock_popen):
    """Test that output is written progressively, not all at once."""
    from io import StringIO

    packages_to_upgrade = [
        UpgradePackageInfo(
            name="requests",
            version=Version("2.28.0"),
            upgradable=True,
            latest_version=Version("2.29.0"),
            is_editable=False
        )
    ]

    # Track when lines are written
    write_times = []
    original_write = StringIO.write

    class TimedStringIO(StringIO):
        def write(self, s):
            write_times.append((s, len(write_times)))
            return original_write(self, s)

    output_stream = TimedStringIO()

    # Mock post-installation environment
    mock_dist = Mock()
    mock_dist.metadata = {"name": "requests"}
    mock_dist.version = "2.29.0"

    # Create mock process with multiple output lines
    mock_process = mock_popen(
        returncode=0,
        stdout_lines=["Collecting requests\n", "Downloading...\n", "Installing...\n", "Successfully installed\n"],
        stderr_lines=[]
    )

    with patch("subprocess.Popen", return_value=mock_process), \
         patch("pipu_cli.package_management.get_default_environment") as mock_env:

        mock_env.return_value.iter_all_distributions.return_value = [mock_dist]
        result = install_packages(packages_to_upgrade, output_stream=output_stream, timeout=300)

    # Verify package was upgraded
    assert len(result) == 1
    assert result[0].upgraded is True

    # Verify multiple writes occurred (not just one big write)
    assert len(write_times) > 2  # Initial message + at least 2 output lines

    # Verify the lines were written
    output = output_stream.getvalue()
    assert "Collecting requests\n" in output
    assert "Downloading...\n" in output
    assert "Installing...\n" in output


def test_install_packages_interleaves_stdout_stderr(mock_popen):
    """Test that stdout and stderr are properly interleaved."""
    from io import StringIO

    packages_to_upgrade = [
        UpgradePackageInfo(
            name="requests",
            version=Version("2.28.0"),
            upgradable=True,
            latest_version=Version("2.29.0"),
            is_editable=False
        )
    ]

    output_stream = StringIO()

    # Mock post-installation environment
    mock_dist = Mock()
    mock_dist.metadata = {"name": "requests"}
    mock_dist.version = "2.29.0"

    # Create mock process with interleaved stdout and stderr
    mock_process = mock_popen(
        returncode=0,
        stdout_lines=["STDOUT: Collecting\n", "STDOUT: Installing\n"],
        stderr_lines=["STDERR: Warning 1\n", "STDERR: Warning 2\n"]
    )

    with patch("subprocess.Popen", return_value=mock_process), \
         patch("pipu_cli.package_management.get_default_environment") as mock_env:

        mock_env.return_value.iter_all_distributions.return_value = [mock_dist]
        result = install_packages(packages_to_upgrade, output_stream=output_stream, timeout=300)

    # Verify package was upgraded
    assert len(result) == 1
    assert result[0].upgraded is True

    # Verify both stdout and stderr were written
    output = output_stream.getvalue()
    assert "STDOUT: Collecting\n" in output
    assert "STDOUT: Installing\n" in output
    assert "STDERR: Warning 1\n" in output
    assert "STDERR: Warning 2\n" in output


# ============================================================================
# Tests for UpgradedPackage failure_reason field
# ============================================================================

def test_upgraded_package_has_failure_reason_field():
    """Test that UpgradedPackage has an optional failure_reason field."""
    pkg = UpgradedPackage(
        name="requests",
        version=Version("2.28.0"),
        upgraded=False,
        previous_version=Version("2.28.0"),
        failure_reason="Version unchanged — may be constrained by dependency resolver"
    )
    assert pkg.failure_reason == "Version unchanged — may be constrained by dependency resolver"

    pkg_ok = UpgradedPackage(
        name="requests",
        version=Version("2.31.0"),
        upgraded=True,
        previous_version=Version("2.28.0"),
    )
    assert pkg_ok.failure_reason is None


# ============================================================================
# Tests for python_path parameter
# ============================================================================

class TestPythonPathInspection:
    """Tests for inspect_installed_packages with python_path."""

    def test_inspect_with_python_path_uses_subprocess(self):
        """When python_path is provided, use subprocess instead of pip internals."""
        pip_list_output = json.dumps([
            {"name": "requests", "version": "2.31.0"},
            {"name": "numpy", "version": "1.24.0"},
        ])

        editable_output = "Package    Version    Editable project location\n---------- ---------- -------------------------\n"

        with patch("pipu_cli.package_management.subprocess.run") as mock_run:
            # First call: pip list --editable
            # Second call: pip list --format=json
            mock_run.side_effect = [
                MagicMock(stdout=editable_output, returncode=0),
                MagicMock(stdout=pip_list_output, returncode=0),
            ]
            packages = inspect_installed_packages(
                timeout=10, python_path="/other/python"
            )

        assert len(packages) == 2
        assert packages[0].name == "numpy"  # Sorted alphabetically
        assert packages[1].name == "requests"
        # Should call subprocess with the provided python_path
        calls = mock_run.call_args_list
        assert calls[0][0][0][0] == "/other/python"

    def test_inspect_without_python_path_uses_pip_internals(self):
        """When python_path is None, use get_default_environment (existing behavior).

        The orphan-metadata scan also calls ``get_default_environment`` once
        to enumerate locations pip knows about. Both call sites together
        account for ``call_count == 2``.
        """
        with patch("pipu_cli.package_management.get_default_environment") as mock_env, \
             patch("pipu_cli.package_management._get_editable_packages", return_value={}), \
             patch(
                 "pipu_cli.package_management._detect_local_orphan_metadata",
                 return_value={},
             ):
            mock_dist = MagicMock()
            mock_dist.metadata = {"name": "requests"}
            mock_dist.version = "2.31.0"
            mock_env.return_value.iter_all_distributions.return_value = [mock_dist]

            packages = inspect_installed_packages(timeout=10)
        assert len(packages) == 1
        mock_env.assert_called_once()

    def test_get_editable_packages_with_python_path(self):
        """_get_editable_packages uses python_path in subprocess."""
        editable_output = (
            "Package    Version    Editable project location\n"
            "---------- ---------- -------------------------\n"
            "my-pkg     0.1.0      /home/user/my-pkg\n"
        )
        with patch("pipu_cli.package_management.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=editable_output, returncode=0
            )
            result = _get_editable_packages(10, python_path="/other/python")

        assert "my-pkg" in result
        assert mock_run.call_args[0][0][0] == "/other/python"

    def test_inspect_remote_extracts_constraints(self):
        """Remote inspection parses Requires-Dist constraints from the probe script.

        The remote probe returns ``{"packages": [...], "orphans": {...}}``
        where each package entry carries ``constraints`` gathered in the
        target env (so ``extra``/marker handling runs against the right
        interpreter). Those constraints must land on the resulting
        ``InstalledPackage`` so :func:`build_dep_report` can walk them.
        """
        probe_output = json.dumps({
            "packages": [{
                "name": "requests",
                "version": "2.31.0",
                "constraints": {
                    "urllib3": "<3,>=1.21",
                    "idna": "<4,>=2.5",
                },
            }],
            "orphans": {},
        })
        editable_output = ""

        with patch("pipu_cli.package_management.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout=editable_output, returncode=0),
                MagicMock(stdout=probe_output, returncode=0),
            ]
            packages = inspect_installed_packages(
                timeout=10, python_path="/other/python"
            )

        assert packages[0].constrained_dependencies == {
            "urllib3": "<3,>=1.21",
            "idna": "<4,>=2.5",
        }

    def test_inspect_remote_captures_orphan_metadata(self):
        """The remote probe's ``orphans`` payload must populate the cache.

        After ``inspect_installed_packages`` returns for a given
        ``python_path``, :func:`get_orphan_metadata` for that path should
        reflect exactly what the probe reported.
        """
        from pipu_cli.package_management import get_orphan_metadata

        probe_output = json.dumps({
            "packages": [
                {"name": "requests", "version": "2.31.0", "constraints": {}},
            ],
            "orphans": {
                "requests": [{"version": "2.28.0", "path": "/old/requests.egg-info"}],
            },
        })
        editable_output = ""

        with patch("pipu_cli.package_management.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout=editable_output, returncode=0),
                MagicMock(stdout=probe_output, returncode=0),
            ]
            inspect_installed_packages(timeout=10, python_path="/other/python")

        assert get_orphan_metadata("/other/python") == {
            "requests": [{"version": "2.28.0", "path": "/old/requests.egg-info"}],
        }


class TestOrphanMetadataDetection:
    """Tests for local orphan metadata classification."""

    def test_compat_editable_source_egg_info_is_not_orphan(self, monkeypatch):
        """Editable ``.egg-info`` in the source tree is valid metadata."""
        from pipu_cli.package_management import _detect_local_orphan_metadata

        class PipDist:
            metadata = {"Name": "my-pkg"}
            version = "1.0.0"
            location = "/venv/lib/python3.13/site-packages"
            editable_project_location = "/src/my-pkg"

        class PipEnv:
            def iter_all_distributions(self):
                return [PipDist()]

        class ImportlibDist:
            metadata = {"Name": "my-pkg"}
            version = "1.0.0"
            _path = "/src/my-pkg/my_pkg.egg-info"

        monkeypatch.setattr(
            "pipu_cli.package_management.get_default_environment",
            lambda: PipEnv(),
        )
        monkeypatch.setattr(
            "importlib.metadata.distributions",
            lambda: [ImportlibDist()],
        )

        assert _detect_local_orphan_metadata() == {}


class TestPythonPathInstallation:
    """Tests for install/reinstall with python_path."""

    def test_install_packages_uses_python_path_in_command(self):
        """install_packages uses python_path in the pip command."""
        packages = [
            UpgradePackageInfo(
                name="requests",
                version=Version("2.28.0"),
                upgradable=True,
                latest_version=Version("2.31.0"),
            )
        ]

        with patch("pipu_cli._subprocess.subprocess.Popen") as mock_popen, \
             patch("pipu_cli.package_management._get_remote_package_versions") as mock_versions:
            mock_process = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stdout.readline = MagicMock(return_value="")
            mock_process.stderr = MagicMock()
            mock_process.stderr.readline = MagicMock(return_value="")
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            mock_popen.return_value = mock_process

            mock_versions.return_value = {
                "requests": Version("2.31.0")
            }

            results = install_packages(
                packages, python_path="/other/python"
            )

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "/other/python"
        assert results[0].upgraded is True

    def test_reinstall_editable_uses_python_path(self):
        """reinstall_editable_packages uses python_path."""
        from pipu_cli.package_management import reinstall_editable_packages

        packages = [
            UpgradePackageInfo(
                name="my-pkg",
                version=Version("0.1.0"),
                upgradable=True,
                latest_version=Version("0.2.0"),
                is_editable=True,
                editable_location="/home/user/my-pkg",
            )
        ]

        with patch("pipu_cli._subprocess.subprocess.Popen") as mock_popen, \
             patch("pipu_cli.package_management._get_remote_package_versions") as mock_versions:
            mock_process = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stdout.readline = MagicMock(return_value="")
            mock_process.stderr = MagicMock()
            mock_process.stderr.readline = MagicMock(return_value="")
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            mock_popen.return_value = mock_process

            mock_versions.return_value = {
                "my-pkg": Version("0.2.0")
            }

            results = reinstall_editable_packages(
                packages, python_path="/other/python"
            )

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "/other/python"
        assert results[0].upgraded is True

    def test_install_packages_interrupt_token_propagates(self):
        """install_packages routes interrupt_token through run_pip's early-return branch."""
        from pipu_cli._subprocess import InterruptToken
        from pipu_cli.package_management import install_packages

        token = InterruptToken()
        token.set()

        upgrade_packages = [
            UpgradePackageInfo(
                name="requests",
                version=Version("2.28.0"),
                upgradable=True,
                latest_version=Version("2.31.0"),
            )
        ]

        with patch("pipu_cli._subprocess.subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stdout.readline = MagicMock(return_value="")
            mock_process.stderr = MagicMock()
            mock_process.stderr.readline = MagicMock(return_value="")
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            mock_process.poll.return_value = 0
            mock_popen.return_value = mock_process

            results = install_packages(upgrade_packages, interrupt_token=token)

        assert len(results) == 1
        assert results[0].upgraded is False
        assert results[0].failure_reason == "Installation interrupted"

    def test_reinstall_editable_interrupt_token_propagates(self):
        """reinstall_editable_packages routes interrupt_token through run_pip's early-return branch."""
        from pipu_cli._subprocess import InterruptToken
        from pipu_cli.package_management import reinstall_editable_packages

        token = InterruptToken()
        token.set()

        editable_packages = [
            UpgradePackageInfo(
                name="my-pkg",
                version=Version("0.1.0"),
                upgradable=True,
                latest_version=Version("0.2.0"),
                is_editable=True,
                editable_location="/home/user/my-pkg",
            )
        ]

        with patch("pipu_cli._subprocess.subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stdout.readline = MagicMock(return_value="")
            mock_process.stderr = MagicMock()
            mock_process.stderr.readline = MagicMock(return_value="")
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            mock_process.poll.return_value = 0
            mock_popen.return_value = mock_process

            results = reinstall_editable_packages(editable_packages, interrupt_token=token)

        assert len(results) == 1
        assert results[0].upgraded is False
        assert results[0].failure_reason == "Installation interrupted"


# ============================================================================
# Tests for run_pip_install
# ============================================================================


class TestRunPipInstall:
    """Tests for the run_pip_install function."""

    def test_new_package_install(self):
        """Installing a new package returns InstalledResult with previous_version=None."""
        mock_process = Mock()
        mock_process.stdout = Mock()
        mock_process.stdout.readline = Mock(side_effect=["", ""])
        mock_process.stdout.close = Mock()
        mock_process.stderr = Mock()
        mock_process.stderr.readline = Mock(side_effect=["", ""])
        mock_process.stderr.close = Mock()
        mock_process.wait.return_value = 0
        mock_process.returncode = 0

        with patch("pipu_cli.package_management.subprocess.Popen", return_value=mock_process) as mock_popen, \
             patch("pipu_cli.package_management._get_local_package_versions") as mock_versions:

            # Pre-install: package not found; Post-install: package found
            mock_versions.side_effect = [
                {},
                {"requests": Version("2.31.0")},
            ]

            results = run_pip_install(["requests"])

        assert len(results) == 1
        assert results[0].installed is True
        assert results[0].previous_version is None
        assert results[0].version == Version("2.31.0")
        assert results[0].name == "requests"

        # Verify -U flag is present
        cmd = mock_popen.call_args[0][0]
        assert "-U" in cmd

    def test_no_update_flag(self):
        """upgrade=False omits -U from pip command."""
        mock_process = Mock()
        mock_process.stdout = Mock()
        mock_process.stdout.readline = Mock(side_effect=["", ""])
        mock_process.stdout.close = Mock()
        mock_process.stderr = Mock()
        mock_process.stderr.readline = Mock(side_effect=["", ""])
        mock_process.stderr.close = Mock()
        mock_process.wait.return_value = 0

        with patch("pipu_cli.package_management.subprocess.Popen", return_value=mock_process) as mock_popen, \
             patch("pipu_cli.package_management._get_local_package_versions") as mock_versions:

            mock_versions.side_effect = [
                {},
                {"requests": Version("2.31.0")},
            ]

            run_pip_install(["requests"], upgrade=False)

        cmd = mock_popen.call_args[0][0]
        assert "-U" not in cmd
        assert "install" in cmd

    def test_pre_flag(self):
        """pre=True adds --pre to pip command."""
        mock_process = Mock()
        mock_process.stdout = Mock()
        mock_process.stdout.readline = Mock(side_effect=["", ""])
        mock_process.stdout.close = Mock()
        mock_process.stderr = Mock()
        mock_process.stderr.readline = Mock(side_effect=["", ""])
        mock_process.stderr.close = Mock()
        mock_process.wait.return_value = 0

        with patch("pipu_cli.package_management.subprocess.Popen", return_value=mock_process) as mock_popen, \
             patch("pipu_cli.package_management._get_local_package_versions") as mock_versions:

            mock_versions.side_effect = [
                {},
                {"requests": Version("3.0.0a1")},
            ]

            run_pip_install(["requests"], pre=True)

        cmd = mock_popen.call_args[0][0]
        assert "--pre" in cmd

    def test_existing_package_updated(self):
        """Updating an existing package reports previous and new versions."""
        mock_process = Mock()
        mock_process.stdout = Mock()
        mock_process.stdout.readline = Mock(side_effect=["", ""])
        mock_process.stdout.close = Mock()
        mock_process.stderr = Mock()
        mock_process.stderr.readline = Mock(side_effect=["", ""])
        mock_process.stderr.close = Mock()
        mock_process.wait.return_value = 0
        mock_process.returncode = 0

        with patch("pipu_cli.package_management.subprocess.Popen", return_value=mock_process), \
             patch("pipu_cli.package_management._get_local_package_versions") as mock_versions:

            mock_versions.side_effect = [
                {"requests": Version("2.28.0")},
                {"requests": Version("2.31.0")},
            ]

            results = run_pip_install(["requests"])

        assert len(results) == 1
        assert results[0].installed is True
        assert results[0].previous_version == Version("2.28.0")
        assert results[0].version == Version("2.31.0")

    def test_install_failure(self):
        """Non-zero exit code marks all packages as not installed."""
        mock_process = Mock()
        mock_process.stdout = Mock()
        mock_process.stdout.readline = Mock(side_effect=["", ""])
        mock_process.stdout.close = Mock()
        mock_process.stderr = Mock()
        mock_process.stderr.readline = Mock(side_effect=["", ""])
        mock_process.stderr.close = Mock()
        mock_process.wait.return_value = 1
        mock_process.returncode = 1

        with patch("pipu_cli.package_management.subprocess.Popen", return_value=mock_process), \
             patch("pipu_cli.package_management._get_local_package_versions", return_value={}):

            results = run_pip_install(["badpkg"])

        assert len(results) == 1
        assert results[0].installed is False
        assert "exit code 1" in results[0].failure_reason  # pyright: ignore[reportOperatorIssue]

    def test_remote_environment(self):
        """python_path uses remote version lookup and correct executable."""
        mock_process = Mock()
        mock_process.stdout = Mock()
        mock_process.stdout.readline = Mock(side_effect=["", ""])
        mock_process.stdout.close = Mock()
        mock_process.stderr = Mock()
        mock_process.stderr.readline = Mock(side_effect=["", ""])
        mock_process.stderr.close = Mock()
        mock_process.wait.return_value = 0
        mock_process.returncode = 0

        with patch("pipu_cli.package_management.subprocess.Popen", return_value=mock_process) as mock_popen, \
             patch("pipu_cli.package_management._get_remote_package_versions") as mock_versions:

            mock_versions.side_effect = [
                {},
                {"requests": Version("2.31.0")},
            ]

            results = run_pip_install(["requests"], python_path="/other/python")

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "/other/python"
        assert results[0].installed is True

    def test_empty_specs(self):
        """Empty package list returns empty results."""
        results = run_pip_install([])
        assert results == []

    def test_vcs_url_spec_does_not_raise(self):
        """VCS / URL specs (not PEP 508) must not abort the batch.

        ``parse_package_spec`` raises :class:`ValueError` for inputs like
        ``git+https://...`` or ``https://example.com/pkg.whl`` because they
        aren't valid :class:`packaging.requirements.Requirement` inputs.
        ``run_pip_install`` must catch that, fall back to a canonical key
        derived from the raw spec, and still hand the spec to pip.
        """
        mock_process = Mock()
        mock_process.stdout = Mock()
        mock_process.stdout.readline = Mock(side_effect=["", ""])
        mock_process.stdout.close = Mock()
        mock_process.stderr = Mock()
        mock_process.stderr.readline = Mock(side_effect=["", ""])
        mock_process.stderr.close = Mock()
        mock_process.wait.return_value = 0
        mock_process.returncode = 0

        vcs_spec = "git+https://github.com/foo/bar.git"

        with patch("pipu_cli.package_management.subprocess.Popen", return_value=mock_process) as mock_popen, \
             patch("pipu_cli.package_management._get_local_package_versions") as mock_versions:

            # Pre- and post-install snapshots: the VCS-derived key isn't a real
            # installed package, so both snapshots are empty.
            mock_versions.side_effect = [{}, {}]

            # The key assertion: no ValueError propagates.
            results = run_pip_install([vcs_spec])

        # pip was still invoked with the raw VCS spec.
        cmd = mock_popen.call_args[0][0]
        assert vcs_spec in cmd
        # One result row, keyed by the original spec, marked as failed-to-find
        # (since post_ver is None) — this matches the old _parse_package_name
        # behavior where the name lookup returned a meaningless key.
        assert len(results) == 1
        assert results[0].name == vcs_spec


# ============================================================================
# Tests for run_pip_install and run_pip_uninstall via shared run_pip helper
# ============================================================================


class TestRunPipInstallAndUninstallViaRunPip:
    """Verify that run_pip_install and run_pip_uninstall delegate to run_pip.

    These tests monkeypatch ``pipu_cli.package_management.run_pip`` so we can
    craft :class:`PipResult` return values without spinning up real subprocesses,
    and then assert that each function translates those results into the
    documented ``InstalledResult`` / ``UninstalledResult`` shape.
    """

    # ---- run_pip_install ---------------------------------------------------

    def test_run_pip_install_success(self):
        """A zero-exit PipResult yields installed=True and no failure_reason."""
        from pipu_cli._subprocess import PipResult

        fake = PipResult(returncode=0, stdout="", stderr="")
        with patch("pipu_cli.package_management.run_pip", return_value=fake) as mock_rp, \
             patch("pipu_cli.package_management._get_local_package_versions") as mock_versions:
            mock_versions.side_effect = [
                {},
                {"requests": Version("2.31.0")},
            ]

            results = run_pip_install(["requests"])

        mock_rp.assert_called_once()
        assert len(results) == 1
        assert results[0].installed is True
        assert results[0].failure_reason is None
        assert results[0].version == Version("2.31.0")

    def test_run_pip_install_nonzero(self):
        """Non-zero returncode yields failure_reason including the captured stderr."""
        from pipu_cli._subprocess import PipResult

        fake = PipResult(returncode=1, stdout="", stderr="ERROR: No matching distribution")
        with patch("pipu_cli.package_management.run_pip", return_value=fake), \
             patch("pipu_cli.package_management._get_local_package_versions", return_value={}):
            results = run_pip_install(["badpkg"])

        assert len(results) == 1
        assert results[0].installed is False
        assert results[0].failure_reason == "pip exit code 1: ERROR: No matching distribution"

    def test_run_pip_install_timeout(self):
        """timed_out=True PipResult yields 'Installation timed out' failure_reason."""
        from pipu_cli._subprocess import PipResult

        fake = PipResult(returncode=-1, stdout="", stderr="", timed_out=True)
        with patch("pipu_cli.package_management.run_pip", return_value=fake), \
             patch("pipu_cli.package_management._get_local_package_versions", return_value={}):
            results = run_pip_install(["requests"])

        assert len(results) == 1
        assert results[0].installed is False
        assert results[0].failure_reason == "Installation timed out"

    def test_run_pip_install_interrupted(self):
        """interrupted=True PipResult yields 'Installation interrupted' failure_reason."""
        from pipu_cli._subprocess import PipResult

        fake = PipResult(returncode=-1, stdout="", stderr="", interrupted=True)
        with patch("pipu_cli.package_management.run_pip", return_value=fake), \
             patch("pipu_cli.package_management._get_local_package_versions", return_value={}):
            results = run_pip_install(["requests"])

        assert len(results) == 1
        assert results[0].installed is False
        assert results[0].failure_reason == "Installation interrupted"

    # ---- run_pip_uninstall -------------------------------------------------

    def test_run_pip_uninstall_success(self):
        """A zero-exit PipResult yields uninstalled=True and no failure_reason."""
        from pipu_cli._subprocess import PipResult

        fake = PipResult(returncode=0, stdout="", stderr="")
        with patch("pipu_cli.package_management.run_pip", return_value=fake) as mock_rp, \
             patch("pipu_cli.package_management._get_local_package_versions") as mock_versions:
            # Pre: installed; Post: absent
            mock_versions.side_effect = [
                {"requests": Version("2.31.0")},
                {},
            ]

            results = run_pip_uninstall(["requests"])

        mock_rp.assert_called_once()
        assert len(results) == 1
        assert results[0].uninstalled is True
        assert results[0].failure_reason is None
        assert results[0].previous_version == Version("2.31.0")

    def test_run_pip_uninstall_nonzero(self):
        """Non-zero returncode yields failure_reason including the captured stderr."""
        from pipu_cli._subprocess import PipResult

        fake = PipResult(returncode=1, stdout="", stderr="ERROR: Cannot uninstall")
        with patch("pipu_cli.package_management.run_pip", return_value=fake), \
             patch(
                "pipu_cli.package_management._get_local_package_versions",
                return_value={"requests": Version("2.31.0")},
             ):
            results = run_pip_uninstall(["requests"])

        assert len(results) == 1
        assert results[0].uninstalled is False
        assert results[0].failure_reason == "pip exit code 1: ERROR: Cannot uninstall"

    def test_run_pip_uninstall_timeout(self):
        """timed_out=True PipResult yields 'Uninstall timed out' failure_reason."""
        from pipu_cli._subprocess import PipResult

        fake = PipResult(returncode=-1, stdout="", stderr="", timed_out=True)
        with patch("pipu_cli.package_management.run_pip", return_value=fake), \
             patch(
                "pipu_cli.package_management._get_local_package_versions",
                return_value={"requests": Version("2.31.0")},
             ):
            results = run_pip_uninstall(["requests"])

        assert len(results) == 1
        assert results[0].uninstalled is False
        assert results[0].failure_reason == "Uninstall timed out"

    def test_run_pip_uninstall_interrupted(self):
        """interrupted=True PipResult yields 'Uninstall interrupted' failure_reason."""
        from pipu_cli._subprocess import PipResult

        fake = PipResult(returncode=-1, stdout="", stderr="", interrupted=True)
        with patch("pipu_cli.package_management.run_pip", return_value=fake), \
             patch(
                "pipu_cli.package_management._get_local_package_versions",
                return_value={"requests": Version("2.31.0")},
             ):
            results = run_pip_uninstall(["requests"])

        assert len(results) == 1
        assert results[0].uninstalled is False
        assert results[0].failure_reason == "Uninstall interrupted"

    def test_run_pip_uninstall_not_installed_precheck(self):
        """If every requested package is absent pre-call, skip the pip invocation entirely.

        The precheck uses _get_local_package_versions (or the remote equivalent).
        When the package is missing, run_pip_uninstall must short-circuit to
        UninstalledResult(uninstalled=True, already_absent=True) WITHOUT invoking run_pip.
        """
        with patch("pipu_cli.package_management.run_pip") as mock_rp, \
             patch(
                "pipu_cli.package_management._get_local_package_versions",
                return_value={},
             ):
            results = run_pip_uninstall(["requests"])

        mock_rp.assert_not_called()
        assert len(results) == 1
        assert results[0].uninstalled is True
        assert results[0].already_absent is True
        assert results[0].previous_version is None


class TestParsePackageSpec:
    """Tests for the unified parse_package_spec / ParsedSpec helper."""

    @pytest.mark.parametrize("spec,expected_name,expected_specifier", [
        ("requests", "requests", ""),
        ("Requests", "requests", ""),
        ("my.namespaced-pkg", "my-namespaced-pkg", ""),
        ("requests==2.31.0", "requests", "==2.31.0"),
        ("requests>=2.30,<3.0", "requests", ">=2.30,<3.0"),
        ("requests[security]>=2.30", "requests", ">=2.30"),
    ])
    def test_parse_package_spec(self, spec, expected_name, expected_specifier):
        from packaging.specifiers import SpecifierSet
        parsed = parse_package_spec(spec)
        assert parsed.name == expected_name
        # Compare as SpecifierSet to be order-insensitive (str() sorts).
        assert parsed.specifier == SpecifierSet(expected_specifier)

    def test_parse_package_spec_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_package_spec("not a valid spec!!!")

    @pytest.mark.parametrize("spec,expected_constraint_str", [
        ("requests", None),
        ("requests==2.31.0", "==2.31.0"),
        # Multi-clause SpecifierSet stringifies in sorted order.
        ("requests>=2.30,<3.0", "<3.0,>=2.30"),
    ])
    def test_parsed_spec_constraint_str_matches_legacy_cli_tuple(
        self, spec, expected_constraint_str
    ):
        parsed = parse_package_spec(spec)
        assert parsed.constraint_str == expected_constraint_str

    def test_parse_package_spec_accepts_local_wheel(self, tmp_path):
        """File-path specs were accepted by the old ``_parse_package_name``.

        ``pipu install ./pkg-1.0-py3-none-any.whl`` is a documented feature;
        the unified parser must keep handling it.
        """
        wheel = tmp_path / "requests-2.31.0-py3-none-any.whl"
        wheel.write_bytes(b"")
        parsed = parse_package_spec(str(wheel))
        assert parsed.name == "requests"
        assert str(parsed.specifier) == ""

    def test_parse_package_spec_accepts_local_sdist(self, tmp_path):
        sdist = tmp_path / "my-pkg-1.2.3.tar.gz"
        sdist.write_bytes(b"")
        parsed = parse_package_spec(str(sdist))
        assert parsed.name == "my-pkg"
