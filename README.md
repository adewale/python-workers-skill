# Python Workers Skill

A [Claude Code skill](https://docs.anthropic.com/en/docs/claude-code) for building [Cloudflare Workers in Python](https://developers.cloudflare.com/workers/languages/python/).

Python Workers run on [Pyodide](https://pyodide.org/) (CPython compiled to WebAssembly) inside V8 isolates. This creates a unique set of challenges — the JS/Python FFI boundary, JsProxy conversion, async-only HTTP, cold start management, and package compatibility — that this skill addresses.

## Install

```bash
claude skill install adewale/python-workers-skill
```

## What's Covered

- **FFI boundary** — `to_js()` / `.to_py()` conversion, `dict_converter`, `None` vs `null` vs `undefined`, type-compatibility matrix, binary data handling
- **All bindings** — D1, KV, R2, Queues, Vectorize, Workers AI, Durable Objects (storage, alarms, WebSockets), Workflows, Service Bindings
- **Static Assets** — routing, `run_worker_first`, why this matters more for Python (cold starts)
- **Testing** — `HAS_PYODIDE` guard, mock bindings, Pyodide fakes, three-tier strategy
- **Configuration** — `wrangler.jsonc`, compatibility flags, packages, `.dev.vars`, CPU limits
- **18 gotchas** — with error signatures and fixes
- **Anti-patterns** — raw JsProxy in business logic, dict without `dict_converter`, module-level PRNG, and more

## Files

```
SKILL.md                                  # Skill definition, decision trees, anti-patterns
references/python-workers/
├── README.md                              # Runtime overview, quick start, project structure
├── api.md                                 # Handlers, Response, FFI, bindings, Workflows
├── configuration.md                       # wrangler.jsonc, packages, flags, testing setup
├── gotchas.md                             # 18 Python-specific issues
└── patterns.md                            # FFI boundary, D1 conversion, Static Assets, DOs, testing
BEST_PRACTICES.md                          # Human-readable mirror (for review/feedback)
```

## Contributing

[BEST_PRACTICES.md](BEST_PRACTICES.md) mirrors the skill's contents in a readable format. If you spot something wrong, outdated, or missing, [open an issue](https://github.com/adewale/python-workers-skill/issues) or submit a PR.

## Sources

Built from production experience with [planet_cf](https://github.com/adewale/planet_cf) and [tasche](https://github.com/adewale/tasche), the [official Cloudflare Python Workers docs](https://developers.cloudflare.com/workers/languages/python/), and [cloudflare/python-workers-examples](https://github.com/cloudflare/python-workers-examples).
