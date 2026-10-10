#!/usr/bin/env python3
"""Parse every fenced code block in the skill and its docs, so broken examples fail CI.

Covers every tracked Markdown file (`git ls-files '*.md'`): the installable skill
(SKILL.md and references/), the human-readable mirror (BEST_PRACTICES.md), README and
other docs.

Blocks are routed by their info string:
- python / py      -> compiled with compile() (the check py_compile runs), so syntax
                      errors fail. Top-level `await` is allowed. A snippet that is only
                      valid as a handler body (`return`/`yield` at top level) is compiled
                      again inside `async def`, and reported as a "body fragment".
- toml             -> tomllib.loads
- json / jsonc     -> json.loads (jsonc: comments and trailing commas stripped first). A
                      jsonc excerpt of top-level properties (`"limits": {...}`) is parsed
                      again inside `{...}`, and reported as a "property fragment".
- bash, sh, shell, console, makefile, text, and untagged blocks -> skipped and counted,
  because they are commands, output or prose, not parseable source.

An unknown info string fails, so a new language must be routed on purpose.
Run with the Pyodide runtime's Python minor version (3.12) to match the Workers grammar.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
FENCE_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})(?P<info>[^\n`]*)$")
PYTHON = {"python", "py", "python3"}
TOML = {"toml"}
JSON = {"json"}
JSONC = {"jsonc"}
SKIPPED = {"", "bash", "sh", "shell", "console", "makefile", "text", "txt"}


@dataclass
class Block:
    path: Path
    line: int
    lang: str
    source: str

    @property
    def where(self) -> str:
        return f"{self.path.relative_to(ROOT)}:{self.line}"


def blocks(path: Path) -> list[Block]:
    found: list[Block] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        match = FENCE_RE.match(lines[i])
        if not match:
            i += 1
            continue
        fence, indent = match.group("fence"), len(match.group("indent"))
        lang = match.group("info").strip().split()[0].lower() if match.group("info").strip() else ""
        body: list[str] = []
        j = i + 1
        while j < len(lines):
            close = FENCE_RE.match(lines[j])
            if close and close.group("fence")[0] == fence[0] and len(close.group("fence")) >= len(fence) and not close.group("info").strip():
                break
            body.append(lines[j][indent:] if lines[j][:indent].strip() == "" else lines[j])
            j += 1
        else:
            raise SystemExit(f"FAIL: {path.relative_to(ROOT)}:{i + 1}: unclosed code fence")
        found.append(Block(path, i + 2, lang, "\n".join(body) + "\n"))
        i = j + 1
    return found


def strip_jsonc(text: str) -> str:
    out: list[str] = []
    i, in_string = 0, False
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\":
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


BODY_ONLY = ("'return' outside function", "'yield' outside function")
FRAGMENTS: Counter[str] = Counter()


def compile_python(block: Block) -> None:
    try:
        compile(block.source, block.where, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT, dont_inherit=True)
    except SyntaxError as exc:
        if exc.msg not in BODY_ONLY:
            raise
        body = "async def _snippet():\n" + "".join(f"    {line}\n" for line in block.source.splitlines())
        try:
            compile(body, block.where, "exec", dont_inherit=True)
        except SyntaxError as inner:
            inner.lineno = (inner.lineno or 2) - 1
            raise inner from None
        FRAGMENTS["python body fragment"] += 1


def parse_jsonc(block: Block) -> None:
    text = strip_jsonc(block.source)
    try:
        json.loads(text)
    except json.JSONDecodeError:
        if text.lstrip().startswith(("{", "[")):
            raise
        json.loads("{" + text + "}")
        FRAGMENTS["jsonc property fragment"] += 1


def check(block: Block) -> str | None:
    try:
        if block.lang in PYTHON:
            compile_python(block)
        elif block.lang in TOML:
            tomllib.loads(block.source)
        elif block.lang in JSON:
            json.loads(block.source)
        elif block.lang in JSONC:
            parse_jsonc(block)
    except SyntaxError as exc:
        return f"{block.path.relative_to(ROOT)}:{block.line + (exc.lineno or 1) - 1}: python: {exc.msg}"
    except (tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
        return f"{block.where}: {block.lang}: {exc}"
    return None


def main() -> int:
    tracked = subprocess.run(["git", "ls-files", "-z", "*.md"], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    files = sorted(ROOT / name for name in tracked.split("\0") if name)
    all_blocks = [b for f in files for b in blocks(f)]
    unknown = sorted({b.lang for b in all_blocks} - PYTHON - TOML - JSON - JSONC - SKIPPED)
    errors = [f"{b.where}: unrouted code block language {b.lang!r}" for b in all_blocks if b.lang in unknown]
    errors += [e for b in all_blocks if (e := check(b))]
    checked = Counter(b.lang for b in all_blocks if b.lang not in SKIPPED)
    skipped = Counter(b.lang or "(untagged)" for b in all_blocks if b.lang in SKIPPED)
    print(f"{len(all_blocks)} fenced blocks in {len(files)} Markdown files")
    print("  parsed:  " + ", ".join(f"{k} {v}" for k, v in sorted(checked.items())))
    print("  skipped: " + ", ".join(f"{k} {v}" for k, v in sorted(skipped.items())) + " (commands/output/prose, not parsed)")
    if FRAGMENTS:
        print("  of which excerpts: " + ", ".join(f"{k} {v}" for k, v in sorted(FRAGMENTS.items())))
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"OK: {sum(checked.values())} code blocks parse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
