"""Tests for requirements file management."""

from packaging.version import Version

from pipu_cli.requirements import parse_requirements_file, update_requirements_file
from pipu_cli.package_management import UpgradedPackage


def test_parse_requirements_file(tmp_path):
    """Test parsing requirements.txt."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("""
# Comment
requests==2.28.0
numpy>=1.24.0
pandas
-r other-requirements.txt
""")

    packages = parse_requirements_file(req_file)

    assert "requests" in packages
    assert "numpy" in packages
    assert "pandas" in packages


def test_parse_requirements_file_empty(tmp_path):
    """Test parsing empty requirements.txt."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("")

    packages = parse_requirements_file(req_file)

    assert len(packages) == 0


def test_parse_requirements_file_not_exists(tmp_path):
    """Test parsing non-existent requirements.txt."""
    req_file = tmp_path / "nonexistent.txt"

    packages = parse_requirements_file(req_file)

    assert len(packages) == 0


def test_update_requirements_file(tmp_path):
    """Test updating requirements.txt with upgraded versions."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("""requests==2.28.0
numpy>=1.24.0
""")

    upgraded = [
        UpgradedPackage(
            name="requests",
            version=Version("2.31.0"),
            upgraded=True,
            previous_version=Version("2.28.0"),
            is_editable=False
        )
    ]

    count = update_requirements_file(req_file, upgraded)

    assert count == 1

    content = req_file.read_text()
    assert "requests==2.31.0" in content
    assert "numpy>=1.24.0" in content


def test_update_requirements_file_multiple_packages(tmp_path):
    """Test updating multiple packages in requirements.txt."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("""# Project dependencies
requests==2.28.0
numpy>=1.24.0
pandas==2.0.0

# Development dependencies
pytest==7.0.0
""")

    upgraded = [
        UpgradedPackage(
            name="requests",
            version=Version("2.31.0"),
            upgraded=True,
            previous_version=Version("2.28.0"),
            is_editable=False
        ),
        UpgradedPackage(
            name="pandas",
            version=Version("2.1.0"),
            upgraded=True,
            previous_version=Version("2.0.0"),
            is_editable=False
        ),
        UpgradedPackage(
            name="pytest",
            version=Version("7.4.0"),
            upgraded=True,
            previous_version=Version("7.0.0"),
            is_editable=False
        )
    ]

    count = update_requirements_file(req_file, upgraded)

    assert count == 3

    content = req_file.read_text()
    assert "requests==2.31.0" in content
    assert "pandas==2.1.0" in content
    assert "pytest==7.4.0" in content
    # Comments and unchanged packages should remain
    assert "# Project dependencies" in content
    assert "numpy>=1.24.0" in content


def test_update_requirements_file_preserves_comments(tmp_path):
    """Test that updating requirements.txt preserves comments."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("""# This is a comment
requests==2.28.0
# Another comment
numpy>=1.24.0
""")

    upgraded = [
        UpgradedPackage(
            name="requests",
            version=Version("2.31.0"),
            upgraded=True,
            previous_version=Version("2.28.0"),
            is_editable=False
        )
    ]

    count = update_requirements_file(req_file, upgraded)

    assert count == 1

    content = req_file.read_text()
    assert "# This is a comment" in content
    assert "# Another comment" in content
    assert "requests==2.31.0" in content


def test_update_requirements_file_not_exists(tmp_path):
    """Test updating non-existent requirements.txt returns 0."""
    req_file = tmp_path / "nonexistent.txt"

    upgraded = [
        UpgradedPackage(
            name="requests",
            version=Version("2.31.0"),
            upgraded=True,
            previous_version=Version("2.28.0"),
            is_editable=False
        )
    ]

    count = update_requirements_file(req_file, upgraded)

    assert count == 0


def test_update_requirements_file_only_upgraded_packages(tmp_path):
    """Test that only successfully upgraded packages are updated."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("""requests==2.28.0
numpy>=1.24.0
""")

    upgraded = [
        UpgradedPackage(
            name="requests",
            version=Version("2.31.0"),
            upgraded=True,
            previous_version=Version("2.28.0"),
            is_editable=False
        ),
        UpgradedPackage(
            name="numpy",
            version=Version("1.24.0"),
            upgraded=False,  # Failed upgrade
            previous_version=Version("1.24.0"),
            is_editable=False
        )
    ]

    count = update_requirements_file(req_file, upgraded)

    assert count == 1

    content = req_file.read_text()
    assert "requests==2.31.0" in content
    # numpy should remain unchanged
    assert "numpy>=1.24.0" in content


def test_update_requirements_file_with_pin_versions_false(tmp_path):
    """Test updating requirements.txt with pin_versions=False uses >= operator."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("""requests==2.28.0
""")

    upgraded = [
        UpgradedPackage(
            name="requests",
            version=Version("2.31.0"),
            upgraded=True,
            previous_version=Version("2.28.0"),
            is_editable=False
        )
    ]

    count = update_requirements_file(req_file, upgraded, pin_versions=False)

    assert count == 1

    content = req_file.read_text()
    assert "requests>=2.31.0" in content


def test_update_requirements_handles_dotted_names(tmp_path):
    """update_requirements_file matches zope.interface despite the dot."""
    path = tmp_path / "requirements.txt"
    path.write_text("zope.interface==5.4.0\n")
    upgraded = [UpgradedPackage(
        name="zope.interface",
        version=Version("6.0"),
        upgraded=True,
        previous_version=Version("5.4.0"),
        is_editable=False,
    )]
    count = update_requirements_file(path, upgraded)
    assert count == 1
    assert "zope.interface==6.0" in path.read_text()


def test_update_requirements_is_case_insensitive(tmp_path):
    """Matching uses canonicalize_name, so Requests in file matches requests in upgrades."""
    path = tmp_path / "requirements.txt"
    path.write_text("Requests==2.30.0\n")
    upgraded = [UpgradedPackage(
        name="requests",
        version=Version("2.31.0"),
        upgraded=True,
        previous_version=Version("2.30.0"),
        is_editable=False,
    )]
    count = update_requirements_file(path, upgraded)
    assert count == 1
    # The display name from the file ("Requests") is preserved; only the pin changes.
    assert "Requests==2.31.0" in path.read_text()


def test_update_requirements_preserves_extras_and_markers(tmp_path):
    """Extras and PEP 508 environment markers round-trip through an upgrade."""
    path = tmp_path / "requirements.txt"
    path.write_text('requests[security,socks]==2.30.0; python_version >= "3.10"\n')
    upgraded = [UpgradedPackage(
        name="requests",
        version=Version("2.31.0"),
        upgraded=True,
        previous_version=Version("2.30.0"),
        is_editable=False,
    )]
    count = update_requirements_file(path, upgraded)
    assert count == 1
    out = path.read_text()
    assert "requests[security,socks]==2.31.0" in out
    assert 'python_version >= "3.10"' in out


def test_update_requirements_preserves_comments_and_options(tmp_path):
    """Comment lines and pip option lines (-r, -e, --extra-index-url) survive verbatim."""
    path = tmp_path / "requirements.txt"
    original = (
        "# top comment\n"
        "-r other.txt\n"
        "-e git+https://example.com/pkg.git#egg=pkg\n"
        "--extra-index-url https://internal.example.com/simple\n"
        "requests==2.30.0  # inline comment\n"
        "\n"
    )
    path.write_text(original)
    upgraded = [UpgradedPackage(
        name="requests",
        version=Version("2.31.0"),
        upgraded=True,
        previous_version=Version("2.30.0"),
        is_editable=False,
    )]
    count = update_requirements_file(path, upgraded)
    assert count == 1
    out = path.read_text()
    assert "# top comment" in out
    assert "-r other.txt" in out
    assert "-e git+https://example.com/pkg.git#egg=pkg" in out
    assert "--extra-index-url https://internal.example.com/simple" in out
    assert "requests==2.31.0" in out


def test_parse_requirements_handles_dotted_names(tmp_path):
    """parse_requirements_file keys dotted names via PEP 503 canonicalization."""
    path = tmp_path / "requirements.txt"
    path.write_text("zope.interface==5.4.0\n")
    packages = parse_requirements_file(path)
    # PEP 503 canonical form of "zope.interface" is "zope-interface"
    assert "zope-interface" in packages
