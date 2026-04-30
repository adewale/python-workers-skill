---
name: python-workers
description: Skill for building Cloudflare Workers in Python. Focuses on what is unique to the Python/Pyodide runtime — the JS/Python FFI boundary, JsProxy conversion, HTTP client compatibility, cold start management, package compatibility, and testing strategies. For general Cloudflare Workers concepts (bindings, caching, routing), see the Cloudflare Workers docs.
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
├─ Local dev secrets (.dev.vars)          -> configuration.md (Local Development)
├─ Set up pytest with mock bindings       -> patterns.md (Testing)
└─ Migrate from pre-Dec 2025 Worker       -> gotchas.md (#1)
```

### "I need to use a Cloudflare binding from Python"

```
Which binding?
├─ Any binding (D1, KV, Queues, etc.)   -> api.md (Binding Access)
│   The API is the same as JS, BUT:
│   ├─ Results come back as JsProxy      -> gotchas.md (#8) + patterns.md (FFI)
│   ├─ Dicts must use dict_converter     -> gotchas.md (#5)
│   ├─ None = undefined ≠ null           -> gotchas.md (#3)
│   └─ Convert at boundary, not inline   -> patterns.md (FFI Boundary Layer)
├─ Durable Objects                       -> patterns.md (Durable Objects From Python)
│   ├─ Transactional storage             -> patterns.md (DO Transactional Storage)
│   ├─ Alarms (scheduled wake-ups)       -> patterns.md (DO Alarms)
│   ├─ WebSocket hibernation             -> patterns.md (DO WebSocket Hibernation API)
│   └─ Outbound WebSocket client         -> patterns.md (Outbound WebSocket Client)
├─ Workflows (multi-step)               -> api.md (Workflows)
├─ Service Bindings / RPC               -> api.md (Service Bindings)
│   └─ dict→Map serialization caveat     -> api.md (Service Bindings note)
├─ Static Assets (special — no Worker!)  -> patterns.md (Static Assets)
├─ Pass Python callable to JS API        -> api.md (create_proxy)
└─ JS API rejects wrapped request/resp   -> patterns.md (request.js_object)
```

### "I need to cross the JS/Python boundary"

```
FFI task?
├─ Convert JS → Python                  -> api.md (FFI) — .to_py() method
├─ Convert Python → JS                  -> api.md (FFI) — to_js + dict_converter
├─ Type compatibility matrix             -> api.md (FFI Type-Compatibility Matrix)
├─ None = undefined ≠ null               -> gotchas.md (#3)
├─ Python bytes to R2/KV                 -> gotchas.md (#14) — to_js(bytes) for Uint8Array
├─ Binary Response (images/files)        -> api.md (Binary Response)
├─ Deep/recursive conversion             -> patterns.md (Safe JsProxy Conversion)
├─ Bidirectional boundary wrappers       -> patterns.md (FFI — The boundary is bidirectional)
├─ D1 results are JsProxy               -> patterns.md (D1 Row Conversion)
├─ Large binary data (>10MB)             -> gotchas.md (#17) — bypass Python
└─ js.eval() is disallowed              -> gotchas.md (#4)
```

### "I need to test my Python Worker"

```
Testing task?
├─ Unit test with mock bindings          -> patterns.md (Testing — Mock Bindings)
├─ The HAS_PYODIDE testability pattern   -> patterns.md (Testing — HAS_PYODIDE)
├─ Test both HAS_PYODIDE branches        -> patterns.md (Testing — Test both branches)
├─ Pyodide fakes for FFI testing         -> patterns.md (Testing — Two-tier FFI testing)
├─ Mocks pass but prod fails (JsProxy)   -> patterns.md (Testing — E2E)
├─ Mock D1 / Vectorize / AI / Queue      -> patterns.md (Testing — Mock Bindings)
└─ Run tests (pytest)                    -> configuration.md (Test Setup)
```

### "Something isn't working"

```
Common issues?
├─ D1 returns JsProxy not dict           -> gotchas.md (#8) + patterns.md (FFI)
├─ Dict rejected by Cloudflare API       -> gotchas.md (#5) — dict_converter
├─ None = undefined ≠ null in D1         -> gotchas.md (#3)
├─ Cold start too slow                   -> gotchas.md (#6) + patterns.md (Per-Isolate Init)
├─ PRNG fails at module level            -> gotchas.md (#7)
├─ Package not found / won't install     -> gotchas.md (#12)
├─ "python_workers flag required"        -> configuration.md
├─ js.eval() disallowed                  -> gotchas.md (#4)
├─ R2/KV rejects Python bytes           -> gotchas.md (#14) — to_js(bytes)
├─ Queue msg canceled on cold start      -> gotchas.md (#15) — inherent behavior
├─ Queue msgs not delivered locally      -> gotchas.md (#16) — use /process-now
├─ Worker crashes on large R2 objects    -> gotchas.md (#17) — bypass Python
├─ Tests pass but prod fails             -> patterns.md (Testing — E2E)
└─ No filesystem access for templates    -> patterns.md (Jinja2 SSR)
```

## Reference Files

All in `references/python-workers/`:

| File | Contents |
|------|----------|
| `README.md` | Runtime overview, quick start, how Pyodide works, project structure |
| `api.md` | Python-specific handler signatures, FFI functions (to_js/to_py/create_proxy), binding access patterns, Response (including binary), Service Bindings (RPC + dict→Map note), Workflows, `workers.fetch()` |
| `configuration.md` | Python-specific config (compatibility flags, pyproject.toml, packages + confirmed working list, CPU limits, `.dev.vars`, `rules` for JS bundling, `triggers.crons`, test setup) |
| `patterns.md` | FFI boundary layer, D1 row conversion, queue message handling, Static Assets architecture, Durable Objects (storage, alarms, WebSocket hibernation, outbound WS), per-isolate initialization, `request.js_object` unwrapping, testing (3-tier with mocks), Jinja2 without filesystem, FastAPI ASGI bridge |
| `gotchas.md` | 17 Python-specific issues with error signatures and fixes |

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

### Python bytes to Binary APIs

```python
# NEVER — PyProxy, R2/KV will reject it
await env.BUCKET.put("key", my_bytes)

# ALWAYS — convert to Uint8Array
await env.BUCKET.put("key", to_js(my_bytes))
```
