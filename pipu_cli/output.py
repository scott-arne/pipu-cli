"""JSON payload builders for pipu CLI output.

All builders return plain ``dict`` / ``list`` structures; callers are
responsible for the final :func:`json.dumps` call so they can merge
additional keys (e.g., ``post_check`` from auto-check) before
serialization.
"""

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
    is_failed_upgrade_result,
    is_resolver_constrained_upgrade,
)


def package_to_dict(pkg: Any) -> Dict[str, Any]:
    """Convert any pipu dataclass to a JSON-serializable ``dict``.

    Walks ``__dataclass_fields__`` and stringifies values via ``str()``
    so :class:`~packaging.version.Version` and similar wrapper types
    round-trip cleanly through :func:`json.dumps`.

    :param pkg: A dataclass instance.
    :returns: Dict with the dataclass's fields as keys.
    """
    result: Dict[str, Any] = {}
    for field_name in pkg.__dataclass_fields__:
        value = getattr(pkg, field_name)
        if hasattr(value, '__str__'):
            result[field_name] = str(value)
        else:
            result[field_name] = value
    return result


def build_upgrade_payload(
    *,
    upgradable: List[UpgradePackageInfo],
    blocked: Optional[List[BlockedPackageInfo]] = None,
    results: Optional[List[UpgradedPackage]] = None,
) -> Dict[str, Any]:
    """Build the standard ``pipu upgrade`` JSON payload.

    :param upgradable: Packages eligible for upgrade.
    :param blocked: Packages blocked by constraints, if ``--show-blocked``.
    :param results: Install results, if the upgrade has executed.
    :returns: Dict with ``upgradable``, ``blocked``, ``results``, and
        ``summary`` keys.
    """
    payload: Dict[str, Any] = {
        "upgradable": [package_to_dict(pkg) for pkg in upgradable],
        "blocked": [package_to_dict(pkg) for pkg in blocked] if blocked else [],
        "results": [],
        "summary": {"total": 0, "upgraded": 0, "constrained": 0, "failed": 0},
    }

    if results is not None:
        payload["results"] = [package_to_dict(pkg) for pkg in results]
        upgraded = sum(1 for pkg in results if pkg.upgraded)
        constrained = sum(1 for pkg in results if is_resolver_constrained_upgrade(pkg))
        failed = sum(1 for pkg in results if is_failed_upgrade_result(pkg))
        payload["summary"] = {
            "total": len(results),
            "upgraded": upgraded,
            "constrained": constrained,
            "failed": failed,
        }

    return payload


def build_install_payload(results: List[InstalledResult]) -> Dict[str, Any]:
    """Build the standard ``pipu install`` JSON payload.

    :param results: Install results.
    :returns: Dict with ``results`` and ``summary`` keys.
    """
    new_installs = sum(
        1 for pkg in results if pkg.installed and pkg.previous_version is None
    )
    updated = sum(
        1 for pkg in results if pkg.installed and pkg.previous_version is not None
        and pkg.version > pkg.previous_version
    )
    failed = sum(1 for pkg in results if not pkg.installed)
    return {
        "results": [package_to_dict(pkg) for pkg in results],
        "summary": {
            "total": len(results),
            "installed": new_installs,
            "updated": updated,
            "failed": failed,
        },
    }


def build_uninstall_payload(results: List[UninstalledResult]) -> Dict[str, Any]:
    """Build the standard ``pipu uninstall`` JSON payload.

    :param results: Uninstall results.
    :returns: Dict with ``results`` and ``summary`` keys.
    """
    result_dicts = [
        {
            "name": pkg.name,
            "previous_version": (
                str(pkg.previous_version) if pkg.previous_version else None
            ),
            "uninstalled": pkg.uninstalled,
            "already_absent": pkg.already_absent,
            "failure_reason": pkg.failure_reason,
        }
        for pkg in results
    ]
    uninstalled = sum(1 for pkg in results if pkg.uninstalled)
    already_absent = sum(1 for pkg in results if pkg.already_absent)
    failed = sum(1 for pkg in results if not pkg.uninstalled)
    return {
        "results": result_dicts,
        "summary": {
            "total": len(results),
            "uninstalled": uninstalled,
            "already_absent": already_absent,
            "failed": failed,
        },
    }


def _edge_to_dict(edge: DepEdge) -> Dict[str, Any]:
    return {
        "name": edge.name,
        "installed_version": (
            str(edge.installed_version) if edge.installed_version is not None else None
        ),
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
            str(problem.installed_version)
            if problem.installed_version is not None else None
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


def env_report_to_json(
    report: EnvReport,
    *,
    fixes: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Serialize an :class:`EnvReport` to a JSON-ready ``dict``.

    :param report: The report to serialize.
    :param fixes: Optional list of :class:`pipu_cli.fixer.FixResult`
        entries. When provided, the payload gains ``fixes`` and
        ``fix_summary`` keys.
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
    payload: Dict[str, Any] = {
        "environment": report.python_path,
        "package_count": report.package_count,
        "problems": [_problem_to_dict(p) for p in report.problems],
        "summary": summary,
    }
    if fixes is not None:
        from pipu_cli.fixer import summarize_fix_results
        payload["fixes"] = [
            {
                "problem": _problem_to_dict(r.problem),
                "action": r.action,
                "target": r.target,
                "status": r.status,
                "detail": r.detail,
            }
            for r in fixes
        ]
        payload["fix_summary"] = summarize_fix_results(fixes)
    return payload


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
