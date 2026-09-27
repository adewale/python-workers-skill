#!/usr/bin/env python3
"""Fail if SKILL.md claims to be verified but its content changed after `last_verified`.

SKILL.md frontmatter carries two fields:
  last_verified: YYYY-MM-DD      the day the skill content was last checked against the
                                 current Workers runtime / docs
  verification_status: verified  last_verified covers every content change, or
  verification_status: stale     content has changed since last_verified and has not been
                                 re-checked (an honest "unverified" marker)

The check finds the most recent commit that changed anything under skills/python-workers/
other than those two lines (plus uncommitted changes, when run locally). If the status is
`verified` and that change is newer than last_verified, it fails: bump last_verified only
after actually re-verifying, or set the status to `stale`.

Why content-based rather than "older than N days": the result depends only on the commit
being checked, so CI cannot turn red on an unrelated PR just because the calendar moved,
and there is no incentive to bump the date without re-verifying.

Needs full git history (actions/checkout `fetch-depth: 0`). Exit codes: 0 pass,
1 freshness/format failure, 2 cannot determine (no git, shallow clone).
"""
from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = "skills/python-workers"
SKILL_MD = ROOT / SKILL_DIR / "SKILL.md"
BOOKKEEPING_RE = re.compile(r"^[+-](?:last_verified|verification_status):")
STATUSES = {"verified", "stale"}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def frontmatter() -> dict[str, str]:
    lines = SKILL_MD.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md has no frontmatter")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if match:
            fields[match.group(1)] = match.group(2).strip().strip("'\"")
    raise ValueError("SKILL.md frontmatter is not closed")


def content_changed(diff: str) -> bool:
    changed = [
        line for line in diff.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    return any(not BOOKKEEPING_RE.match(line) for line in changed)


def last_content_change() -> tuple[dt.date, str]:
    if content_changed(git("diff", "HEAD", "-U0", "--", SKILL_DIR)):
        return dt.datetime.now(dt.timezone.utc).date(), "uncommitted changes"
    for row in git("log", "--no-merges", "--format=%H %cs", "--", SKILL_DIR).splitlines():
        sha, day = row.split()
        if content_changed(git("show", "--format=", "-U0", sha, "--", SKILL_DIR)):
            return dt.date.fromisoformat(day), f"commit {sha[:7]}"
    raise LookupError(f"no commit changes {SKILL_DIR}")


def main() -> int:
    try:
        if git("rev-parse", "--is-shallow-repository").strip() == "true":
            print("UNAVAILABLE: shallow clone; fetch full history (actions/checkout fetch-depth: 0)", file=sys.stderr)
            return 2
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"UNAVAILABLE: git history not readable: {exc}", file=sys.stderr)
        return 2
    try:
        fields = frontmatter()
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    errors: list[str] = []
    try:
        verified = dt.date.fromisoformat(fields.get("last_verified", ""))
    except ValueError:
        errors.append(f"last_verified must be YYYY-MM-DD, got {fields.get('last_verified')!r}")
    status = fields.get("verification_status", "")
    if status not in STATUSES:
        errors.append(f"verification_status must be one of {sorted(STATUSES)}, got {status!r}")
    if errors:
        for error in errors:
            print(f"FAIL: SKILL.md {error}", file=sys.stderr)
        return 1
    changed, source = last_content_change()
    if status == "verified" and changed > verified:
        print(
            f"FAIL: SKILL.md says verification_status: verified with last_verified: {verified}, "
            f"but skill content changed on {changed} ({source}). Re-verify and bump last_verified, "
            "or set verification_status: stale.",
            file=sys.stderr,
        )
        return 1
    if status == "stale":
        print(f"OK (stale, declared): content last changed {changed} ({source}); last verified {verified}")
    else:
        print(f"OK: last_verified {verified} covers the last content change {changed} ({source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
