"""CLI glue for ``pipu check --fix``.

Owns streaming per-fix status lines, the summary panel, interactive
prompting, rollback invocation, and group-mode dispatch. Calls into
:mod:`pipu_cli.fixer` for the actual fix work so the fixer stays pure
and easy to unit-test.
"""

from typing import Any, Dict, List

from rich.console import Console
from rich.panel import Panel

from pipu_cli.fixer import FixResult, summarize_fix_results
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
