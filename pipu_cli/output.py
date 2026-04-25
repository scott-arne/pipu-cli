"""Output formatting for pipu CLI."""

import json
from typing import List, Optional, Any, Dict
from pipu_cli.package_management import (
    UpgradePackageInfo,
    UpgradedPackage,
    BlockedPackageInfo,
    InstalledResult,
    UninstalledResult,
    DepEdge,
    DepNode,
    DepProblem,
    DepReport,
    EnvReport,
)


class OutputFormatter:
    """Base class for output formatting."""

    def format_upgradable(self, packages: List[UpgradePackageInfo]) -> str:
        """Format upgradable packages."""
        raise NotImplementedError

    def format_blocked(self, packages: List[BlockedPackageInfo]) -> str:
        """Format blocked packages."""
        raise NotImplementedError

    def format_results(self, results: List[UpgradedPackage]) -> str:
        """Format upgrade results."""
        raise NotImplementedError


class JsonOutputFormatter(OutputFormatter):
    """JSON output formatter."""

    def _package_to_dict(self, pkg: Any) -> Dict[str, Any]:
        """Convert a package dataclass to a JSON-serializable dict."""
        result = {}
        for field_name in pkg.__dataclass_fields__:
            value = getattr(pkg, field_name)
            if hasattr(value, '__str__'):
                result[field_name] = str(value)
            else:
                result[field_name] = value
        return result

    def format_upgradable(self, packages: List[UpgradePackageInfo]) -> str:
        """Format upgradable packages as JSON."""
        data = {
            "upgradable": [self._package_to_dict(pkg) for pkg in packages],
            "count": len(packages)
        }
        return json.dumps(data, indent=2)

    def format_blocked(self, packages: List[BlockedPackageInfo]) -> str:
        """Format blocked packages as JSON."""
        data = {
            "blocked": [self._package_to_dict(pkg) for pkg in packages],
            "count": len(packages)
        }
        return json.dumps(data, indent=2)

    def format_results(self, results: List[UpgradedPackage]) -> str:
        """Format upgrade results as JSON."""
        successful = [self._package_to_dict(pkg) for pkg in results if pkg.upgraded]
        failed = [self._package_to_dict(pkg) for pkg in results if not pkg.upgraded]

        data = {
            "successful": successful,
            "failed": failed,
            "total": len(results),
            "success_count": len(successful),
            "failure_count": len(failed)
        }
        return json.dumps(data, indent=2)

    def format_all(
        self,
        upgradable: List[UpgradePackageInfo],
        blocked: Optional[List[BlockedPackageInfo]] = None,
        results: Optional[List[UpgradedPackage]] = None
    ) -> str:
        """Format all data as a single JSON object with standardized schema."""
        data: Dict[str, Any] = {
            "upgradable": [self._package_to_dict(pkg) for pkg in upgradable],
            "blocked": [self._package_to_dict(pkg) for pkg in blocked] if blocked else [],
            "results": [],
            "summary": {
                "total": 0,
                "upgraded": 0,
                "failed": 0
            }
        }

        if results is not None:
            data["results"] = [self._package_to_dict(pkg) for pkg in results]
            successful = [pkg for pkg in results if pkg.upgraded]
            failed = [pkg for pkg in results if not pkg.upgraded]
            data["summary"] = {
                "total": len(results),
                "upgraded": len(successful),
                "failed": len(failed)
            }

        return json.dumps(data, indent=2)

    def format_install_results(self, results: List[InstalledResult]) -> str:
        """Format install results as a single JSON object.

        :param results: List of InstalledResult objects
        :returns: JSON string
        """
        result_dicts = [self._package_to_dict(pkg) for pkg in results]
        new_installs = [pkg for pkg in results if pkg.installed and pkg.previous_version is None]
        updated = [pkg for pkg in results if pkg.installed and pkg.previous_version is not None
                    and pkg.version > pkg.previous_version]
        failed = [pkg for pkg in results if not pkg.installed]

        data: Dict[str, Any] = {
            "results": result_dicts,
            "summary": {
                "total": len(results),
                "installed": len(new_installs),
                "updated": len(updated),
                "failed": len(failed),
            }
        }
        return json.dumps(data, indent=2)

    def format_group_install_results(self, env_results: List[Dict[str, Any]]) -> str:
        """Format group install results as a JSON array of per-environment results.

        :param env_results: List of dicts, each with 'environment' key plus install results
        :returns: JSON string
        """
        return json.dumps(env_results, indent=2)

    def format_group_results(self, env_results: List[Dict[str, Any]]) -> str:
        """Format group results as a JSON array of per-environment results.

        :param env_results: List of dicts, each with 'environment' key plus standard schema
        :returns: JSON string
        """
        return json.dumps(env_results, indent=2)

    def format_uninstall_results(self, results: List[UninstalledResult]) -> str:
        """Format uninstall results as a single JSON object.

        :param results: List of UninstalledResult objects.
        :returns: JSON string.
        """
        result_dicts = []
        for pkg in results:
            result_dicts.append({
                "name": pkg.name,
                "previous_version": str(pkg.previous_version) if pkg.previous_version else None,
                "uninstalled": pkg.uninstalled,
                "already_absent": pkg.already_absent,
                "failure_reason": pkg.failure_reason,
            })

        successful = [pkg for pkg in results if pkg.uninstalled]
        already_absent = [pkg for pkg in results if pkg.already_absent]
        failed = [pkg for pkg in results if not pkg.uninstalled]

        data: Dict[str, Any] = {
            "results": result_dicts,
            "summary": {
                "total": len(results),
                "uninstalled": len(successful),
                "already_absent": len(already_absent),
                "failed": len(failed),
            },
        }
        return json.dumps(data, indent=2)

    def format_group_uninstall_results(self, env_results: List[Dict[str, Any]]) -> str:
        """Format group uninstall results as a JSON array of per-environment results.

        :param env_results: List of dicts, each with 'environment' key plus uninstall results.
        :returns: JSON string.
        """
        return json.dumps(env_results, indent=2)


def _edge_to_dict(edge: DepEdge) -> Dict[str, Any]:
    return {
        "name": edge.name,
        "installed_version": str(edge.installed_version) if edge.installed_version is not None else None,
        "specifier": edge.specifier,
        "is_editable": edge.is_editable,
        "editable_location": edge.editable_location,
    }


def _node_to_dict(node: DepNode) -> Dict[str, Any]:
    data = _edge_to_dict(node.edge)
    data["children"] = [_node_to_dict(c) for c in node.children]
    if node.is_cycle:
        data["cycle"] = True
    return data


def _problem_to_dict(problem: DepProblem) -> Dict[str, Any]:
    return {
        "kind": problem.kind,
        "package": problem.package,
        "detail": problem.detail,
        "required_by": problem.required_by,
        "specifier": problem.specifier,
        "installed_version": (
            str(problem.installed_version) if problem.installed_version is not None else None
        ),
    }


def dep_report_to_json(report: DepReport, *, depth: int) -> Dict[str, Any]:
    """Serialize a :class:`DepReport` to a JSON-ready ``dict``.

    :param report: The report to serialize.
    :param depth: The ``--depth`` value the command was invoked with; it
        is not derivable from ``report``.
    :returns: A dict suitable for :func:`json.dumps`.
    """
    pkg = report.package
    return {
        "package": {
            "name": pkg.name,
            "version": str(pkg.version),
            "is_editable": pkg.is_editable,
            "editable_location": pkg.editable_location,
        },
        "depth": depth,
        "required_by": [_node_to_dict(n) for n in report.required_by],
        "requires": [_node_to_dict(n) for n in report.requires],
        "problems": [_problem_to_dict(p) for p in report.problems],
    }


def dep_report_group_to_json(
    *,
    group_name: str,
    per_env: List[tuple],
    depth: int,
) -> Dict[str, Any]:
    """Wrap per-env reports into the group-mode JSON shape.

    :param group_name: Group name for the ``"group"`` key.
    :param per_env: List of ``(env_name, DepReport)`` tuples. Order is
        preserved.
    :param depth: Forwarded into each per-env report.
    :returns: ``{"group": ..., "environments": [{"env": ..., "report": ...}]}``.
    """
    return {
        "group": group_name,
        "environments": [
            {"env": env, "report": dep_report_to_json(report, depth=depth)}
            for env, report in per_env
        ],
    }


def env_report_to_json(report: EnvReport) -> Dict[str, Any]:
    """Serialize an :class:`EnvReport` to a JSON-ready ``dict``.

    :param report: The report to serialize.
    :returns: A dict suitable for :func:`json.dumps`.
    """
    summary = {
        "missing": 0,
        "violates": 0,
        "broken-editable": 0,
        "duplicate-install": 0,
        "stale-metadata": 0,
        "total": len(report.problems),
    }
    for problem in report.problems:
        if problem.kind in summary:
            summary[problem.kind] += 1
    return {
        "environment": report.python_path,
        "package_count": report.package_count,
        "problems": [_problem_to_dict(p) for p in report.problems],
        "summary": summary,
    }


def env_report_group_to_json(
    *,
    group_name: str,
    per_env: List[tuple],
) -> Dict[str, Any]:
    """Wrap per-env :class:`EnvReport` values into the group schema.

    :param group_name: Group name for the ``"group"`` key.
    :param per_env: List of ``(env_name, EnvReport)`` tuples. Order
        is preserved.
    :returns: ``{"group": ..., "environments": [{"env": ..., "report": ...}]}``.
    """
    return {
        "group": group_name,
        "environments": [
            {"env": env, "report": env_report_to_json(report)}
            for env, report in per_env
        ],
    }
