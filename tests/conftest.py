"""Shared pytest fixtures for pipu-cli tests."""

from typing import Any, Dict, List, Tuple, Union

import pytest
from packaging.utils import canonicalize_name
from packaging.version import Version

from pipu_cli.package_management import InstalledPackage


PackageSpec = Union[
    Tuple[str, str, Dict[str, str]],
    Tuple[str, str, Dict[str, str], Dict[str, Any]],
]


@pytest.fixture
def make_installed_packages():
    """Build a list of :class:`InstalledPackage` from compact specs.

    Each spec is a tuple ``(name, version, constrained_dependencies)`` or
    ``(name, version, constrained_dependencies, extra)`` where ``extra``
    may set ``is_editable`` / ``editable_location``.

    :returns: Factory callable.
    """

    def _factory(*specs: PackageSpec) -> List[InstalledPackage]:
        packages: List[InstalledPackage] = []
        for spec in specs:
            extra: Dict[str, Any] = {}
            if len(spec) == 3:
                name, version, deps = spec  # type: ignore[misc]
            elif len(spec) == 4:
                name, version, deps, extra = spec  # type: ignore[misc]
            else:
                raise ValueError(f"Invalid spec: {spec!r}")
            packages.append(
                InstalledPackage(
                    name=canonicalize_name(name),
                    version=Version(version),
                    constrained_dependencies=dict(deps),
                    is_editable=bool(extra.get("is_editable", False)),
                    editable_location=extra.get("editable_location"),
                )
            )
        return packages

    return _factory
