"""CLI glue for ``pipu check --fix``.

Owns streaming per-fix status lines, the summary panel, interactive
prompting, rollback invocation, and group-mode dispatch. Calls into
:mod:`pipu_cli.fixer` for the actual fix work so the fixer stays pure
and easy to unit-test.
"""

import shutil
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from rich.console import Console
from rich.panel import Panel

from pipu_cli.fixer import (
    FixResult,
    apply_stale_metadata_fix,
    apply_violates_fix,
    build_fix_plan,
    summarize_fix_results,
    _extract_orphan_path,
)
from pipu_cli.package_management import (
    DepProblem,
    EnvReport,
    get_orphan_metadata,
    inspect_installed_packages,
    run_pip_install,
)
from pipu_cli.rollback import save_state
from pipu_cli.ui import CHECKMARK, CROSS, STYLES


_SUCCESS = STYLES["success"]
_FAILURE = STYLES["failure"]
_DIM = "dim"

_CHECK_MARKUP = f"[{_SUCCESS}]{CHECKMARK}[/{_SUCCESS}]"
_CROSS_MARKUP = f"[{_FAILURE}]{CROSS}[/{_FAILURE}]"
_SKIP_MARKUP = f"[{_DIM}]-[/{_DIM}]"


_KIND_LABEL = {
    "stale-metadata": "stale-metadata",
    "violates": "violates",
    "missing": "missing",
    "broken-editable": "broken-editable",
    "duplicate-install": "duplicate-install",
}


def render_fix_line(console: Console, fix: FixResult) -> None:
    """Render one streaming status line for a completed fix.

    :param console: Rich console.
    :param fix: The fix result. ``"unfixable"`` entries are not
        streamed — they appear only in the summary.
    """
    if fix.status == "succeeded":
        if fix.action == "delete":
            console.print(f"  {_CHECK_MARKUP} deleted {fix.target}")
        else:
            previous = fix.problem.installed_version
            was = f" (was {previous})" if previous is not None else ""
            console.print(
                f"  {_CHECK_MARKUP} installed {fix.target}{was}"
            )
    elif fix.status == "failed":
        verb = "delete" if fix.action == "delete" else "install"
        console.print(
            f"  {_CROSS_MARKUP} {verb} {fix.target} failed: "
            f"[{_DIM}]{fix.detail}[/{_DIM}]"
        )
    elif fix.status == "skipped":
        console.print(
            f"  {_SKIP_MARKUP} skipped {fix.problem.package} "
            f"[{_DIM}]({fix.detail})[/{_DIM}]"
        )
    # "unfixable" intentionally not streamed.


def render_fix_summary(console: Console, fixes: List[FixResult]) -> None:
    """Render the end-of-run summary panel.

    :param console: Rich console.
    :param fixes: Every :class:`FixResult` emitted for the env,
        including ``"unfixable"`` entries.
    """
    summary: Dict[str, Any] = summarize_fix_results(fixes)
    applied = int(summary["applied"])
    failed = int(summary["failed"])
    skipped = int(summary["skipped"])
    unfixable = int(summary["unfixable"])

    title = (
        f"Fix summary — {applied} applied, {failed} failed, "
        f"{skipped} skipped, {unfixable} unfixable"
    )

    lines: List[str] = []
    by_kind: Dict[str, Dict[str, int]] = summary["by_kind"]  # type: ignore[assignment]
    for kind, counts in by_kind.items():
        applied_ct = counts.get("applied", 0)
        failed_ct = counts.get("failed", 0)
        skipped_ct = counts.get("skipped", 0)
        unfixable_ct = counts.get("unfixable", 0)
        if applied_ct:
            verb = "deleted" if kind == "stale-metadata" else "satisfied"
            lines.append(
                f"{_CHECK_MARKUP} {applied_ct} {_KIND_LABEL[kind]} {verb}"
            )
        if failed_ct:
            lines.append(
                f"{_CROSS_MARKUP} {failed_ct} {_KIND_LABEL[kind]} failed (see above)"
            )
        if skipped_ct:
            lines.append(
                f"{_SKIP_MARKUP} {skipped_ct} {_KIND_LABEL[kind]} skipped"
            )
        if unfixable_ct:
            lines.append(
                f"[{_DIM}]ⓘ[/{_DIM}] {unfixable_ct} unfixable ({kind}) — "
                f"re-run `pipu check` for details"
            )

    body = "\n".join(lines) if lines else "No fixes attempted."
    border_style = _FAILURE if failed else _SUCCESS
    console.print(
        Panel(body, title=title, border_style=border_style, expand=False)
    )


class Prompter:
    """Interactive ``[y/n/a/q]`` prompt with sticky-per-kind ``a`` and terminal ``q``.

    Designed to be injectable: tests supply a ``prompt_fn`` returning
    canned answers, production wires it to ``input`` (or Click's
    :func:`click.prompt`).

    :param interactive: When ``False``, :meth:`should_apply` always
        returns ``True`` and never invokes ``prompt_fn``.
    :param prompt_fn: Callable taking a prompt message and returning
        the raw user response as a string.
    """

    def __init__(
        self,
        *,
        interactive: bool,
        prompt_fn: Callable[[str], str],
    ) -> None:
        self._interactive = interactive
        self._prompt_fn = prompt_fn
        self._sticky_kinds: Set[str] = set()
        self._quit = False

    @property
    def should_quit(self) -> bool:
        """``True`` once the user has answered ``q``."""
        return self._quit

    def should_apply(self, *, kind: str, message: str) -> bool:
        """Ask whether to apply a fix.

        :param kind: The problem kind. Scopes the sticky ``a`` behavior
            so ``a`` on a stale-metadata prompt does not auto-answer
            subsequent violates prompts.
        :param message: Human-readable prompt (e.g.,
            ``"Fix: delete /x [y/n/a/q]: "``).
        :returns: ``True`` to apply, ``False`` to skip. Once
            :attr:`should_quit` is ``True`` all subsequent calls return
            ``False`` without prompting.
        """
        if self._quit:
            return False
        if not self._interactive:
            return True
        if kind in self._sticky_kinds:
            return True

        while True:
            raw = (self._prompt_fn(message) or "").strip().lower()
            if raw in ("", "n"):
                return False
            if raw == "y":
                return True
            if raw == "a":
                self._sticky_kinds.add(kind)
                return True
            if raw == "q":
                self._quit = True
                return False
            # Invalid — re-prompt.


def _prompt_user(message: str) -> str:
    """Read one line from the user. Thin indirection so tests can mock.

    :param message: Prompt to display.
    :returns: Raw user response, or ``"q"`` on EOF (non-interactive TTY).
    """
    try:
        return input(message)
    except EOFError:
        return "q"


def _collect_rollback_snapshot(python_path: Optional[str]) -> List[Dict[str, str]]:
    """Build a :func:`save_state`-ready package list for ``python_path``.

    :param python_path: Interpreter path, or ``None`` for local.
    :returns: List of ``{"name", "version"}`` dicts.
    """
    pkgs = inspect_installed_packages(python_path=python_path)
    return [{"name": p.name, "version": str(p.version)} for p in pkgs]


def run_fix(
    *,
    report: EnvReport,
    console: Console,
    output: str,
    interactive: bool,
) -> Tuple[List[FixResult], int]:
    """Execute the full fix flow for one env.

    :param report: Previously-rendered :class:`EnvReport`. Re-partitioned
        via :func:`build_fix_plan`.
    :param console: Rich console for human-mode output.
    :param output: ``"human"`` or ``"json"``. Only affects whether
        streaming status lines and the summary panel are rendered.
    :param interactive: When ``True``, prompt before each action.
    :returns: ``(fixes, exit_code)``. ``exit_code`` is ``0`` when every
        attempted fix succeeded, ``1`` when at least one attempted fix
        failed. Unfixable kinds do not affect the exit code.
    """
    plan = build_fix_plan(report)
    prompter = Prompter(interactive=interactive, prompt_fn=_prompt_user)

    fixes: List[FixResult] = []

    fixable_count = len(plan.stale_metadata) + len(plan.violates)
    if output == "human":
        console.print(
            f"Fixing ({fixable_count} fixable, {len(plan.unfixable)} unfixable)..."
        )

    # Rollback snapshot only when a violates fix is in the plan.
    if plan.violates:
        try:
            pkgs = _collect_rollback_snapshot(plan.python_path)
            save_state(
                pkgs,
                f"Pre-fix state (check --fix) — {plan.python_path or 'local'}",
            )
        except Exception as exc:
            if output == "human":
                console.print(
                    f"  [dim](rollback snapshot failed: {exc} — proceeding)[/dim]"
                )

    # Phase 2a: stale-metadata, one problem at a time.
    for problem in plan.stale_metadata:
        path = _extract_orphan_path(problem.detail) or ""
        message = f"Fix: delete orphan metadata {path} [y/n/a/q]: "
        if not prompter.should_apply(kind="stale-metadata", message=message):
            reason = "user quit" if prompter.should_quit else "user declined"
            result = FixResult(
                problem=problem, action="delete", target=path,
                status="skipped", detail=reason,
            )
            fixes.append(result)
            if output == "human":
                render_fix_line(console, result)
            continue

        result = apply_stale_metadata_fix(
            problem,
            python_path=plan.python_path,
            verifier=get_orphan_metadata,
            remover=shutil.rmtree,
        )
        fixes.append(result)
        if output == "human":
            render_fix_line(console, result)

    # Phase 2b: violates, grouped by package for single-flight install.
    groups: Dict[str, List[DepProblem]] = defaultdict(list)
    group_order: List[str] = []
    for problem in plan.violates:
        if problem.package not in groups:
            group_order.append(problem.package)
        groups[problem.package].append(problem)

    for package in group_order:
        group = groups[package]
        spec = group[0].specifier or ""
        installed = group[0].installed_version
        message = (
            f'Fix: install "{package}{spec}" (currently {installed}) [y/n/a/q]: '
        )
        if not prompter.should_apply(kind="violates", message=message):
            reason = "user quit" if prompter.should_quit else "user declined"
            for p in group:
                skipped = FixResult(
                    problem=p, action="install",
                    target=f"{package}{spec}",
                    status="skipped", detail=reason,
                )
                fixes.append(skipped)
                if output == "human":
                    render_fix_line(console, skipped)
            continue

        group_results = apply_violates_fix(
            group, python_path=plan.python_path, installer=run_pip_install,
        )
        fixes.extend(group_results)
        if output == "human" and group_results:
            # All group_results share status/target; show one streaming line.
            render_fix_line(console, group_results[0])

    # Unfixable problems: surface in the flat results list (summary only).
    for problem in plan.unfixable:
        fixes.append(FixResult(
            problem=problem, action="install", target="",
            status="unfixable", detail=None,
        ))

    if output == "human":
        render_fix_summary(console, fixes)

    any_failure = any(f.status == "failed" for f in fixes)
    return fixes, 1 if any_failure else 0
