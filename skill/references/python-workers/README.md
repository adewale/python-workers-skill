# Cloudflare Python Workers

## Overview

Cloudflare Python Workers run Python 3.12+ via **Pyodide** (CPython compiled to WebAssembly) inside V8 isolates. The Cloudflare bindings API is identical to JavaScript Workers — the differences are in the **runtime**, the **FFI boundary**, and the **constraints** of running Python in WebAssembly.

**Status**: Open Beta (requires `python_workers` compatibility flag)

## What's Different From JavaScript Workers

| Aspect | JS Workers | Python Workers |
|--------|-----------|----------------|
| Cold start | ~50ms | ~1s (with snapshot) to ~10s (without) |
| Handler pattern | `export default { fetch() {} }` | `class Default(WorkerEntrypoint): async def fetch()` |
| Binding results | Native JS objects | JsProxy (must convert with `.to_py()`) |
| Dict→JS object | Automatic | Requires `to_js(dict, dict_converter=Object.fromEntries)` |
| `None` | N/A | Maps to JS `undefined`, NOT `null` |
| HTTP clients | `fetch()`, any npm package | Only async: `httpx`, `aiohttp` (no `requests`) |
| Packages | npm (anything) | Pure Python + Pyodide-compiled only |
| PRNG at init | Works | Fails (breaks Wasm snapshot) |
| Templates | File I/O | No writable FS; bundle at deploy time |
| Testing | Miniflare / Vitest | pytest with mock bindings + HAS_PYODIDE guard |

## Quick Start

```bash
mkdir my-worker && cd my-worker
uv init
uv tool install workers-py
uv run pywrangler init
```

```python
# src/entry.py
from workers import WorkerEntrypoint, Response

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response("Hello from Python Worker!")
```

```bash
uv run pywrangler dev        # http://localhost:8787
uv run pywrangler deploy     # deploy to Cloudflare
```

## How It Works

1. Python code runs inside **Pyodide** (CPython compiled to WebAssembly) in a V8 isolate
2. Pyodide's **FFI** bridges JavaScript and Python — all bindings work through JsProxy objects
3. At deploy time, Cloudflare takes a **WebAssembly memory snapshot** (imports pre-resolved)
4. At request time, the snapshot is restored (fast), then your handler runs

### The snapshot matters

Module-level code runs at deploy time and is captured in the snapshot. This means:
- **DO**: Define constants, import libraries, create Jinja2 environments
- **DON'T**: Call `random.seed()`, `secrets.token_hex()`, `uuid.uuid4()` — PRNG calls break snapshots

## Project Structure

```
project-root/
├── src/
│   ├── main.py              # WorkerEntrypoint class (fetch/scheduled/queue)
│   ├── wrappers.py          # JS/Python FFI boundary layer
│   └── ...                  # Feature modules
├── tests/
│   ├── unit/                # Mock-based (~1s)
│   ├── integration/         # Mock bindings (~2s)
│   └── e2e/                 # Live Cloudflare (~30s)
├── assets/                  # Static files (served without waking Worker)
├── migrations/              # D1 SQL files
├── wrangler.jsonc           # Workers config (main = "src/main.py")
├── pyproject.toml           # Python deps (only async HTTP, Pyodide-compat)
└── Makefile                 # test, lint, deploy commands
```

**Import root**: When `main = "src/main.py"`, import as `from models import ...` (not `from src.models`).

## The `workers` Module

| Export | Purpose |
|--------|---------|
| `WorkerEntrypoint` | Base class — has `fetch`, `scheduled`, `queue` methods |
| `DurableObject` | Base class for Durable Objects |
| `WorkflowEntrypoint` | Base class for Workflows |
| `Response` | Python wrapper around JS Response |
| `fetch` | Python wrapper around JS `fetch()` |

## Official Resources

- [Python Workers Docs](https://developers.cloudflare.com/workers/languages/python/)
- [How Python Workers Work](https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/)
- [Python FFI](https://developers.cloudflare.com/workers/languages/python/ffi/)
- [Python Packages](https://developers.cloudflare.com/workers/languages/python/packages/)
- [Python Workers Examples](https://github.com/cloudflare/python-workers-examples)
- [pywrangler CLI](https://github.com/cloudflare/workers-py)
- [Pyodide Package List](https://pyodide.org/en/stable/usage/packages-in-pyodide.html)
