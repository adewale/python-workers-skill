---
name: python-workers
description: Skill for building Cloudflare Workers in Python. Focuses on what is unique to the Python/Pyodide runtime — the JS/Python FFI boundary, JsProxy conversion, async constraints, cold start management, package compatibility, and testing strategies. For general Cloudflare Workers concepts (bindings, caching, routing), see the Cloudflare Workers docs.
version: 0.2.0
runtime: Pyodide (CPython 3.12+ compiled to WebAssembly)
status: Open Beta (requires python_workers compatibility flag)
last_verified: 2026-02-08
references:
  - python-workers
---

# Cloudflare Python Workers Skill

Everything that is different, surprising, or error-prone when building Cloudflare Workers in **Python** instead of JavaScript/TypeScript. This skill does NOT duplicate general Cloudflare Workers documentation — it focuses on the Python-specific gaps.

## Quick Decision Trees

### "I need to set up a Python Worker"

```
New project?
├─ Quick start with pywrangler           -> README.md
├─ Python-specific config flags           -> configuration.md
├─ Add Python packages (Pyodide compat)   -> configuration.md (Packages)
├─ Set up pytest with mock bindings       -> patterns.md (Testing)
└─ Migrate from pre-Dec 2025 Worker       -> gotchas.md (#13)
```

### "I need to use a Cloudflare binding from Python"

```
Which binding?
├─ Any binding (D1, KV, Queues, etc.)   -> api.md (Binding Access)
│   The API is the same as JS, BUT:
│   ├─ Results come back as JsProxy      -> gotchas.md (#9) + patterns.md (FFI)
│   ├─ Dicts must use dict_converter     -> gotchas.md (#6)
│   ├─ None ≠ null ≠ undefined           -> gotchas.md (#4)
│   └─ Convert at boundary, not inline   -> patterns.md (FFI Boundary Layer)
├─ Static Assets (special — no Worker!)  -> patterns.md (Static Assets)
├─ Pass Python callable to JS API        -> api.md (create_proxy)
└─ JS API rejects wrapped request/resp   -> patterns.md (request.js_object)
```

### "I need to cross the JS/Python boundary"

```
FFI task?
├─ Convert JS → Python                  -> api.md (FFI) — .to_py() method
├─ Convert Python → JS                  -> api.md (FFI) — to_js + dict_converter
├─ None vs null vs undefined             -> gotchas.md (#4)
├─ Deep/recursive conversion             -> patterns.md (Safe JsProxy Conversion)
├─ D1 results are JsProxy               -> patterns.md (D1 Row Conversion)
└─ js.eval() is disallowed              -> gotchas.md (#5)
```

### "I need to test my Python Worker"

```
Testing task?
├─ Unit test with mock bindings          -> patterns.md (Testing — Mock Bindings)
├─ The HAS_PYODIDE testability pattern   -> patterns.md (Testing — HAS_PYODIDE)
├─ Mocks pass but prod fails (JsProxy)   -> patterns.md (Testing — E2E)
├─ Mock D1 / Vectorize / AI / Queue      -> patterns.md (Testing — Mock Bindings)
└─ Run tests (pytest)                    -> configuration.md (Test Setup)
```

### "Something isn't working"

```
Common issues?
├─ D1 returns JsProxy not dict           -> gotchas.md (#9) + patterns.md (FFI)
├─ Dict rejected by Cloudflare API       -> gotchas.md (#6) — dict_converter
├─ None vs null in D1                    -> gotchas.md (#4)
├─ Sync HTTP library fails               -> gotchas.md (#2)
├─ Cold start too slow                   -> gotchas.md (#7) + patterns.md (Per-Isolate Init)
├─ PRNG fails at module level            -> gotchas.md (#8)
├─ Package not found / won't install     -> gotchas.md (#13, #14)
├─ "python_workers flag required"        -> configuration.md
├─ js.eval() disallowed                  -> gotchas.md (#5)
├─ Tests pass but prod fails             -> patterns.md (Testing — E2E)
└─ No filesystem access for templates    -> patterns.md (Jinja2 SSR)
```

## Reference Files

All in `references/python-workers/`:

| File | Contents |
|------|----------|
| `README.md` | Runtime overview, quick start, how Pyodide works, project structure |
| `api.md` | Python-specific handler signatures, FFI functions (to_js/to_py/create_proxy), binding access patterns, Response |
| `configuration.md` | Python-specific config (compatibility flags, pyproject.toml, packages, CPU limits, test setup) |
| `patterns.md` | FFI boundary layer, D1 row conversion, queue message handling, Static Assets architecture, per-isolate initialization, `request.js_object` unwrapping, testing (3-tier with mocks), Jinja2 without filesystem, FastAPI ASGI bridge |
| `gotchas.md` | 14 Python-specific issues with error signatures and fixes |

## Anti-Patterns

### Raw JsProxy in Business Logic

```python
# NEVER — JsProxy leaks into app code, breaks serialization/iteration
results = await env.DB.prepare("SELECT * FROM feeds").all()
return results.results  # JsProxy!

# ALWAYS — convert at the boundary
results = await env.DB.prepare("SELECT * FROM feeds").all()
rows = [dict(r) for r in results.results.to_py()]  # Native Python
```

### Python Dict to JS Without dict_converter

```python
# NEVER — creates JS Map, most Cloudflare APIs reject it
js_obj = to_js({"topK": 50})

# ALWAYS — creates JS Object
js_obj = to_js({"topK": 50}, dict_converter=Object.fromEntries)
```

### Module-Level PRNG

```python
# NEVER — breaks Wasm snapshot, deployment FAILS
import random
random.seed(42)

# ALWAYS — inside handlers
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        random.seed(42)  # OK
```

### Routing Static Assets Through the Worker

```python
# NEVER — wakes up Python Worker for CSS/JS/images (1s+ cold start)
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if path.startswith("/static/"):
            return await self.env.ASSETS.fetch(request)  # Worker is already awake!

# ALWAYS — let Workers Static Assets serve directly from the edge
# Configure in wrangler.jsonc — requests to /static/ never reach your Python code
"assets": { "directory": "./assets/" }
# No binding needed. No Worker invocation. ~15-90ms TTFB.
```

### Sync HTTP Libraries

```python
# NEVER — raw sockets blocked in Pyodide
import requests
response = requests.get(url)

# ALWAYS — async only
import httpx
async with httpx.AsyncClient() as client:
    response = await client.get(url)
```
