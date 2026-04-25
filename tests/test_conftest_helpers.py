"""Sanity tests for shared test fixtures."""

from packaging.version import Version

from pipu_cli.package_management import InstalledPackage


def test_make_installed_packages_builds_canonical_list(make_installed_packages):
    pkgs = make_installed_packages(
        ("Requests", "2.31.0", {"urllib3": "<3,>=1.21"}),
        ("urllib3", "2.2.2", {}),
    )
    assert len(pkgs) == 2
    assert all(isinstance(p, InstalledPackage) for p in pkgs)
    names = {p.name for p in pkgs}
    assert names == {"requests", "urllib3"}  # canonicalized
    requests = next(p for p in pkgs if p.name == "requests")
    assert requests.version == Version("2.31.0")
    assert requests.constrained_dependencies == {"urllib3": "<3,>=1.21"}
    assert requests.is_editable is False


def test_make_installed_packages_supports_editable(make_installed_packages):
    pkgs = make_installed_packages(
        ("mylib", "0.1.0", {}, {"is_editable": True, "editable_location": "/src/mylib"}),
    )
    assert pkgs[0].is_editable is True
    assert pkgs[0].editable_location == "/src/mylib"
