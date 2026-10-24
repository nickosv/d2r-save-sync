"""Text formatting for the CLI."""

from __future__ import annotations

from datetime import datetime

from d2rsync.model import Entry, Outcome, Resolved


def format_time(mtime: float) -> str:
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / 1024 / 1024:.1f} MiB"


def format_entry(label: str, entry: Entry | None) -> str:
    if entry is None:
        return f"    {label:<8} (missing)"
    return (
        f"    {label:<8} {format_time(entry.mtime)}  "
        f"{entry.sha256[:12]}  {format_size(entry.size)}"
    )


def format_plan(items: list[Resolved], *, dry_run: bool) -> str:
    lines: list[str] = []
    interesting = [item for item in items if item.op != "none"]
    if not interesting:
        lines.append("Already in sync.")
    for item in interesting:
        verb = _verb(item.op, dry_run)
        lines.append(f"{verb:<18} {item.rel}")
        lines.append(f"                   {item.reason}")
        if item.conflict or item.op in ("push", "pull", "delete_local", "delete_remote", "unresolved", "skip"):
            lines.append(format_entry("local", item.local))
            lines.append(format_entry("remote", item.remote))
    unchanged = sum(1 for item in items if item.op == "none")
    lines.append("")
    lines.append(f"Unchanged: {unchanged}. Actions: {len(interesting)}.")
    if dry_run:
        lines.append("Dry run: no files were written.")
    return "\n".join(lines)


def format_outcomes(outcomes: list[Outcome]) -> str:
    errors = [item for item in outcomes if item.op == "error"]
    if not errors:
        return ""
    lines = ["Stopped because a file failed:"]
    for item in errors:
        lines.append(f"  {item.rel}: {item.reason}")
    return "\n".join(lines)


def summary_counts(outcomes: list[Outcome]) -> str:
    counts: dict[str, int] = {}
    for item in outcomes:
        counts[item.op] = counts.get(item.op, 0) + 1
    parts = [f"{count} {name}" for name, count in sorted(counts.items())]
    return "Result: " + ", ".join(parts) if parts else "Result: nothing to do"


def exit_code(outcomes: list[Outcome]) -> int:
    if any(item.op == "error" for item in outcomes):
        return 1
    if any(item.op in ("skipped", "unresolved") and item.conflict for item in outcomes):
        return 2
    return 0


def _verb(op: str, dry_run: bool) -> str:
    prefix = "would " if dry_run else ""
    labels = {
        "push": "push",
        "pull": "pull",
        "delete_local": "delete local",
        "delete_remote": "delete remote",
        "skip": "skip",
        "unresolved": "conflict",
        "none": "unchanged",
    }
    return prefix + labels.get(op, op)
