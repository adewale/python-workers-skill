# Python Workers — Configuration

Python-specific configuration only. For general wrangler.jsonc binding setup (D1, KV, R2, Queues, Vectorize, etc.), see the [Cloudflare Workers docs](https://developers.cloudflare.com/workers/configuration/).

---

## Table of Contents

- [Python-Specific wrangler.jsonc](#python-specific-wrangerjsonc)
- [Compatibility Flags](#compatibility-flags)
- [pyproject.toml](#pyprojecttoml)
- [Package Management](#package-management)
- [CPU Limits (Python Is Heavier)](#cpu-limits)
- [Local Development](#local-development)
- [Deployment](#deployment)
- [Test Setup](#test-setup)

---

## Python-Specific wrangler.jsonc

These are the fields that are **different or especially important** for Python Workers:

```jsonc
{
  // IDE autocompletion for all wrangler config
  "$schema": "node_modules/wrangler/config-schema.json",

  "name": "my-python-worker",
  "main": "src/main.py",          // Points to .py file, not .js/.ts

  "compatibility_date": "2026-01-01",
  "compatibility_flags": [
    "python_workers",              // REQUIRED — enables Python runtime
    "python_dedicated_snapshot"    // RECOMMENDED — faster cold starts
  ],

  // Python Workers burn more CPU than JS — increase from 30s default
  "limits": {
    "cpu_ms": 60000
  },

  // Static assets: served from edge WITHOUT waking the Python Worker
  // This is critical for Python where cold starts are ~1s
  "assets": {
    "directory": "./assets/"
    // No "binding" needed unless you also want programmatic access
    // Requests to files in this directory never invoke your Worker
  }

  // ... all other bindings (D1, KV, Queues, etc.) are configured
  // identically to JS Workers — see Cloudflare docs
}
```

---

## Compatibility Flags

| Flag | Required | Purpose |
|------|----------|---------|
| `python_workers` | **Yes** | Enables the Pyodide runtime |
| `python_dedicated_snapshot` | Recommended | Worker-specific Wasm memory snapshot (~1s cold start vs ~10s) |
| `python_workflows` | For Workflows | Enables Python Workflow support |
| `experimental` | Sometimes | Required alongside `python_workflows` |
| `disable_python_no_global_handlers` | Legacy only | Re-enables deprecated `on_fetch`/`on_scheduled` function pattern |

---

## pyproject.toml

```toml
[project]
name = "my-python-worker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    # Only packages deployed to Workers go here
    "feedparser>=6.0.0",
    "httpx>=0.27.0",         # Async HTTP — requests/urllib3 won't work
    "jinja2>=3.1.0",
    "bleach>=6.0.0",
]

[dependency-groups]
workers = [
    "workers-py",              # pywrangler CLI
    "workers-runtime-sdk",     # Type stubs + IDE autocompletion
]
test = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "respx>=0.22",             # httpx mocking
    "freezegun>=1.4",          # Time mocking
]
dev = [
    "ruff>=0.8",               # Lint + format
]
```

Generate Env types for IDE autocompletion:

```bash
uv run pywrangler types
```

---

## Package Management

### What works

- **Pure Python packages** from PyPI
- **Pyodide packages** (compiled to WebAssembly): numpy, pandas, pillow, etc.
- Full list: https://pyodide.org/en/stable/usage/packages-in-pyodide.html

### What doesn't work

- **Sync HTTP libraries**: `requests`, `urllib3` — blocked by Pyodide's socket restrictions
- **Native C extensions** not in Pyodide: `psycopg2`, `lxml`, `cryptography`
- **OS-specific modules**: See gotchas.md #12

### Alternatives

| Doesn't Work | Use Instead |
|---------------|-------------|
| `requests` | `httpx` (async) or `aiohttp` |
| `urllib3` | `httpx` (async) |
| `lxml` | `xml.etree.ElementTree` (stdlib) |
| `psycopg2` | D1 binding (it's SQLite) |
| `cryptography` | `hashlib`, `hmac` (stdlib) |

### Adding packages

```bash
uv add httpx jinja2 bleach        # Runtime deps
uv add --group test pytest         # Test deps (not deployed)
```

---

## CPU Limits

**Python Workers use significantly more CPU** than JS Workers for the same work — Pyodide interpretation overhead, plus Python libraries like feedparser, bleach, and JSON parsing are CPU-intensive.

| Plan | Default CPU | Max CPU |
|------|-------------|---------|
| Free | 10ms | 10ms |
| Paid | 30ms | 900,000ms (15 min) |

```jsonc
"limits": { "cpu_ms": 60000 }  // 60 seconds — often needed for Python
```

**CPU time** = computation only. Awaiting I/O (fetch, D1 queries) does NOT count. But parsing feeds, sanitizing HTML, and rendering templates does.

---

## Local Development

```bash
uv run pywrangler dev        # Local dev server at http://localhost:8787
```

**Important**: Vectorize has no local simulation — you must set `"remote": true` on Vectorize (and optionally AI) bindings during dev:

```jsonc
"vectorize": [{ "binding": "SEARCH_INDEX", "index_name": "...", "remote": true }],
"ai": { "binding": "AI", "remote": true }
```

---

## Deployment

```bash
uv run pywrangler deploy                                           # Deploy
npx wrangler deploy --config examples/my-instance/wrangler.jsonc   # Specific instance
```

### Import root

When `main = "src/main.py"`, the `src/` directory is the import root:

```python
# src/models.py exists
from models import FeedRow    # CORRECT
from src.models import FeedRow  # WRONG — will fail
```

---

## Test Setup

### pytest configuration (in pyproject.toml)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

### Makefile

```makefile
test:
	uv run pytest tests/unit tests/integration -x -q

test-unit:
	uv run pytest tests/unit -x -q

test-cov:
	uv run pytest tests/unit tests/integration --cov=src --cov-report=term-missing

check:
	uv run ruff check . && uv run ruff format --check . && $(MAKE) test
```

### Key requirement

Your source code must be importable outside the Workers runtime. This means FFI imports need the `HAS_PYODIDE` guard pattern — see patterns.md (Testing).
