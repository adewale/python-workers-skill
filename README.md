# Python Workers Skill

[![skills.sh](https://skills.sh/b/adewale/python-workers-skill)](https://skills.sh/adewale/python-workers-skill)

An Agent Skill for building [Cloudflare Workers in Python](https://developers.cloudflare.com/workers/languages/python/).

Python Workers run on [Pyodide](https://pyodide.org/) (CPython compiled to WebAssembly) inside V8 isolates. This creates a unique set of challenges — the JS/Python FFI boundary, JsProxy conversion, HTTP client compatibility, cold start management, and package compatibility — that this skill addresses.

## Install

```bash
npx skills add adewale/python-workers-skill
```

Skills appear on skills.sh automatically after users install the repo with the skills CLI. Install counts and leaderboard rankings come from anonymous CLI telemetry; opt out with `DISABLE_TELEMETRY=1`. The repo page customization in `skills.sh.json` is picked up after the repository is seen by telemetry and the cache refreshes.

## Agent compatibility

The installable skill directory is `skills/python-workers`. It uses the Agent Skills `SKILL.md` format and is configured for Codex, OpenCode, Pi, Gemini CLI, and Claude Code.

| Agent/client | Install or use |
|---|---|
| Codex | `cp -R skills/python-workers ~/.codex/skills/python-workers` |
| OpenCode | `cp -R skills/python-workers ~/.config/opencode/skills/python-workers` or use `.opencode/skills/python-workers` in a project |
| Pi | `pi install https://github.com/adewale/python-workers-skill` or `pi --skill skills/python-workers` |
| Gemini CLI | `gemini skills install https://github.com/adewale/python-workers-skill --path skills/python-workers` or copy to `.gemini/skills/python-workers` |
| Claude Code | `npx skills add adewale/python-workers-skill` or copy to `.claude/skills/python-workers` |

## What's Covered

- **FFI boundary** — `to_js()` / `.to_py()` conversion, `dict_converter`, `None` vs `null` vs `undefined`, type-compatibility matrix, binary data handling
- **All bindings** — D1, KV, R2, Queues, Vectorize, Workers AI, Durable Objects (storage, alarms, WebSockets), Workflows, Service Bindings
- **Static Assets** — routing, `run_worker_first`, why this matters more for Python (cold starts)
- **Testing** — `HAS_PYODIDE` guard, mock bindings, Pyodide fakes, three-tier strategy
- **Configuration** — `wrangler.jsonc`, compatibility flags, packages, `.dev.vars`, CPU limits
- **17 gotchas** — with error signatures and fixes
- **Anti-patterns** — raw JsProxy in business logic, dict without `dict_converter`, module-level PRNG, and more

## Files

```
skills/python-workers/                     # The installable skill
├── SKILL.md                               # Skill definition, decision trees, anti-patterns
└── references/python-workers/
    ├── README.md                          # Runtime overview, quick start, project structure
    ├── api.md                             # Handlers, Response, FFI, bindings, Workflows
    ├── configuration.md                   # wrangler.jsonc, packages, flags, testing setup
    ├── gotchas.md                         # 17 Python-specific issues
    └── patterns.md                        # FFI boundary, D1 conversion, Static Assets, DOs, testing
BEST_PRACTICES.md                          # Human-readable mirror (for review/feedback)
tests/                                     # Evals
└── evals.json
```

## Reading Order

| Task | Start With | Then Read |
|------|-----------|-----------|
| First Python Worker | README.md | configuration.md → api.md |
| Add FastAPI | api.md (ASGI Bridge) | patterns.md (request.js_object) |
| Debug JsProxy error | gotchas.md (#5, #8) | patterns.md (FFI Boundary) |
| Set up testing | patterns.md (Testing) | configuration.md (Test Setup) |
| Add Static Assets | patterns.md (Static Assets) | configuration.md (wrangler.jsonc) |
| Use Durable Objects | patterns.md (Durable Objects) | api.md (Handlers) |
| Check package compatibility | configuration.md (Packages) | gotchas.md (#12) |
| Something isn't working | gotchas.md (Quick Reference table) | patterns.md |

All files are in `skills/python-workers/references/python-workers/`.

## Contributing

[BEST_PRACTICES.md](BEST_PRACTICES.md) mirrors the skill's contents in a readable format. If you spot something wrong, outdated, or missing, [open an issue](https://github.com/adewale/python-workers-skill/issues) or submit a PR.

CI (the `install-boundary` job in `.github/workflows/install-boundary.yml`) runs two content checks you can run locally:

- `python3 scripts/check_code_blocks.py` (Python 3.12, to match Pyodide) compiles every `python` block and parses every `toml`, `json` and `jsonc` block in the tracked Markdown. `bash`, `makefile` and untagged blocks are skipped and counted.
- `python3 scripts/check_last_verified.py` compares `SKILL.md`'s `last_verified` date with the last commit that changed `skills/python-workers/`. With `verification_status: verified`, any later content change fails the check. Bump `last_verified` only after re-checking the content against the current runtime and docs; otherwise set `verification_status: stale`. The status is currently `stale`: the content has changed since 2026-02-08, including five corrections from a Pyodide maintainer, and has not been re-verified since.

One check runs by hand only, not in CI:

- `python3 scripts/check_runtime_claims.py` starts the probe Worker in `tests/runtime/` under `pywrangler dev` (workerd with a local D1; nothing is deployed and no Cloudflare account is used) and compares 12 runtime observations, such as how D1 results and `jsnull` behave, with the values recorded in the script. It needs uv >= 0.12.3, Node.js and network access, because `npx` downloads an unpinned wrangler, so a new wrangler or runtime release can change the result. Run it when you change the skill's FFI or D1 guidance, or anything in `tests/runtime/`. Exit 0 means every observation matches, 1 a mismatch, 2 the probe did not start.

## Sources

Built from production experience with [planet_cf](https://github.com/adewale/planet_cf) and [tasche](https://github.com/adewale/tasche), the [official Cloudflare Python Workers docs](https://developers.cloudflare.com/workers/languages/python/), and [cloudflare/python-workers-examples](https://github.com/cloudflare/python-workers-examples).
