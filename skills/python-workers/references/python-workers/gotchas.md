# Python Workers — Gotchas

Issues that are **specific to Python Workers**. General Cloudflare Workers issues (rate limits, billing, generic binding errors) are not included here.

---

## Quick Reference

| # | Issue | Error Signature | Fix |
|---|-------|-----------------|-----|
| 1 | Old handler pattern | `on_fetch is not defined` | Use `WorkerEntrypoint` class |
| 2 | to_py is a method | `cannot import name 'to_py'` | Call `.to_py()` on the JsProxy |
| 3 | None = undefined ≠ null | D1 NULL/undefined issues | Import `jsnull` from `pyodide.ffi` |
| 4 | js.eval() blocked | `Code generation disallowed` | Use a non-eval alternative (e.g. `js.JSON.parse()`) |
| 5 | Dict → Map | API rejects Python dict | Use `dict_converter=Object.fromEntries` |
| 6 | Slow cold start | First request 1-10s | Add `python_dedicated_snapshot` flag |
| 7 | PRNG at init | Deploy fails | Move `random`/`secrets` into handlers |
| 8 | D1 results are JsProxy | TypeError on iteration | Call `.to_py()` on `results.results` |
| 9 | Queue body is JsProxy | TypeError on field access | Call `msg.body.to_py()` |
| 10 | CPU exceeded | Worker killed | Increase `cpu_ms` (Python burns more CPU) |
| 11 | Stdlib limitations | ImportError | Check list below |
| 12 | Native packages fail | ModuleNotFoundError | Use Pyodide-compatible alternatives |
| 13 | Mocks pass, prod fails | JsProxy bugs in production | Add E2E tests against real infra |
| 14 | bytes crosses as PyProxy | R2/KV rejects Python bytes | Use `to_js(data)` for Uint8Array |
| 15 | Cold start cancels queue | First queue msg canceled, retried | Inherent behavior — don't chase it |
| 16 | Miniflare queue unreliable | Queue msgs never delivered locally | Use `POST /process-now` for local dev |
| 17 | Large binary round-trip | Worker crash on >10MB R2 objects | Bypass Python — pass ReadableStream to JS Response |

---

## #1: Legacy Handler Pattern

**Error**: `TypeError: on_fetch is not defined`

The `@handler` + `on_fetch` pattern was deprecated August 2025.

```python
# WRONG (deprecated)
from workers import handler
@handler
async def on_fetch(request, env):
    return Response("Hello")

# CORRECT
from workers import WorkerEntrypoint, Response
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response("Hello")
```

Also applies to `on_scheduled` → `async def scheduled(self, controller)`.

---

## #2: to_py Is a Method, Not a Function

**Error**: `ImportError: cannot import name 'to_py' from 'pyodide.ffi'`

```python
# WRONG
from pyodide.ffi import to_py

# CORRECT — it's a method on JsProxy objects
data = await request.json()    # JsProxy
python_dict = data.to_py()     # Method call
```

Note: `to_js` IS a standalone function. Only `to_py` is asymmetric.

---

## #3: None = undefined ≠ null

The two distinct JS values `null` and `undefined` correspond to the two distinct Python values `jsnull` and `None`. Python `None` → JS `undefined` (NOT `null`):

| Python | JavaScript | Notes |
|--------|------------|-------|
| `None` | `undefined` | Default mapping |
| `jsnull` | `null` | Import `from pyodide.ffi import jsnull` |

**Problem**: D1 expects `null` for SQL NULL, but gets `undefined` from Python `None`.

```python
# WRONG — sends undefined to D1
await env.DB.prepare("UPDATE feeds SET etag = ?").bind(None).run()

# CORRECT
from pyodide.ffi import jsnull
await env.DB.prepare("UPDATE feeds SET etag = ?").bind(jsnull).run()
```

**`is None` is not enough to detect every missing value at the FFI boundary.** JS `undefined` arrives as Python `None`, but JS `null` arrives as `jsnull`:

| Value | `is None` | `bool()` | Notes |
|-------|:---------:|:--------:|-------|
| Python `None` | `True` | `False` | JS `undefined` also arrives here |
| JS `null` (`jsnull`) | **`False`** | `False` | Sentinel from `pyodide.ffi` |

**Rule**: Any boundary code with `if x is None` must also check for `jsnull`. Use a helper:

```python
from pyodide.ffi import jsnull

def _is_missing(value):
    """True for Python None (which includes JS undefined) or JS null."""
    return value is None or value is jsnull
```

---

## #4: js.eval() Is Disallowed

**Error**: `EvalError: Code generation from strings disallowed for this context`

Workers security policy blocks `eval()`. Anything that runs JS source from a string at runtime is rejected — use a non-eval alternative (e.g. `js.JSON.parse()` for parsing JSON, `from pyodide.ffi import jsnull` for a `null` sentinel).

**Watch out for libraries that use js.eval() internally.** Some Python libraries call `js.eval()` under the hood to load JS dependencies. Example: `python-readability` loads Mozilla Readability via `js.eval()` and will fail with the same error. The workaround for JS-native functionality is a Service Binding to a JS Worker:

```python
# Instead of a Python library that uses js.eval():
# Deploy a JS Worker with the npm package, then call via Service Binding
result = await self.env.READABILITY_SERVICE.extract(url, html)
```

---

## #5: Dict Becomes Map Without dict_converter

**Symptom**: Cloudflare API silently rejects your dict, or returns unexpected results.

`to_js()` on a Python dict creates a JavaScript `Map`, not a plain `Object`. Most Cloudflare APIs only accept `Object`.

```python
# WRONG — creates JS Map
js_obj = to_js({"topK": 50})

# CORRECT — creates JS Object
from js import Object
js_obj = to_js({"topK": 50}, dict_converter=Object.fromEntries)
```

Wrap it once:

```python
def _to_js_value(value):
    if isinstance(value, dict):
        return to_js(value, dict_converter=js.Object.fromEntries)
    return to_js(value)
```

---

## #6: Cold Start Performance

**Symptom**: First request takes 1-10+ seconds.

Python Workers run Pyodide (CPython → WebAssembly). Inherently slower cold start than JS Workers.

| Configuration | Cold Start |
|---------------|-----------|
| No snapshots | ~10s |
| With `python_dedicated_snapshot` | ~1s |
| JS Workers (for comparison) | ~50ms |

**Mitigation**:
1. `"python_dedicated_snapshot"` compatibility flag (biggest win)
2. Workers Static Assets without binding (CSS/JS never wake Worker)
3. Pre-compile Jinja2 templates at build time
4. `stale-while-revalidate` cache headers + cron pre-warming
5. Module-level constants are captured in snapshot (but no PRNG — see #7)

---

## #7: PRNG Cannot Be Seeded During Initialization

**Error**: Deployment fails with user error.

Wasm memory snapshots assert PRNG state unchanged after snapshotting.

```python
# FAILS at deploy time — any module-level entropy call
import random, secrets, uuid
random.seed(42)
token = secrets.token_hex(16)
id = uuid.uuid4()

# WORKS — inside handlers
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        token = secrets.token_hex(16)  # OK here
```

Applies to: `random.*`, `secrets.*`, `uuid.uuid4()`, `os.urandom()`.

---

## #8: D1 Results Are JsProxy

**Symptom**: TypeError when iterating, JSON serializing, or accessing fields on D1 results.

```python
# WRONG — results.results is JsProxy
results = await env.DB.prepare("SELECT * FROM feeds").all()
for row in results.results:
    print(row["title"])  # May work but row values may be JsProxy too

# CORRECT — convert at the boundary
rows = [dict(r) for r in results.results.to_py()]
for row in rows:
    print(row["title"])  # Native Python string
```

---

## #9: Queue Message Body Is JsProxy

**Symptom**: TypeError when accessing queue message fields.

```python
# WRONG
feed_id = msg.body["feed_id"]

# CORRECT
body = msg.body.to_py()
feed_id = body["feed_id"]
```

---

## #10: CPU Time Exceeded

**Symptom**: Worker killed mid-execution.

Python Workers consume more CPU than JS Workers. Pyodide interpretation overhead + Python library CPU usage (feedparser, bleach, JSON, XML parsing).

```jsonc
"limits": { "cpu_ms": 60000 }  // Increase from 30s default
```

---

## #11: Standard Library Limitations

**Not functional** (imports but doesn't work):
- `multiprocessing` — no process spawning in Wasm
- `threading` — no threads in Wasm
- `socket` — raw sockets blocked

**Cannot import** (depends on removed `termios`):
- `pty`, `tty`

**Limited**:
- `decimal` — C implementation only, no `_pydecimal`
- `webbrowser` — not available

**Excluded**: `curses`, `dbm`, `ensurepip`, `fcntl`, `grp`, `idlelib`, `lib2to3`, `msvcrt`, `pwd`, `resource`, `syslog`, `termios`, `tkinter`, `venv`, `winreg`, `winsound`

---

## #12: Native/Compiled Packages Don't Work

**Error**: `ModuleNotFoundError` or build errors.

Only pure Python packages and packages built for Pyodide work.

Check: https://pyodide.org/en/stable/usage/packages-in-pyodide.html

**Many native packages work in Pyodide**, including `lxml`, `cryptography`, `numpy`, `pandas`, `Pillow`, `pydantic`, `pyarrow`, `scipy`, and many more. Check the [Pyodide package list](https://pyodide.org/en/stable/usage/packages-in-pyodide.html) before assuming a C extension won't work.

**Common alternatives for packages that genuinely don't work**:
| Doesn't Work | Use Instead |
|---------------|-------------|
| `psycopg2` / `psycopg2-binary` | D1 binding (SQLite), or Hyperdrive + `pg8000` (pure Python) |
| `mysqlclient` | Hyperdrive + `PyMySQL` (pure Python) |
| `pycurl` | `httpx` or `aiohttp` |
| `gevent` / `greenlet` | `asyncio` (built into the runtime) |

Always check the **full dependency tree** before choosing any library — a transitive dependency on a native extension that isn't in Pyodide is enough to break your Worker.

Request new packages: https://github.com/cloudflare/workerd/discussions/categories/python-packages

---

## #13: Mocks Pass, Production Fails

**Symptom**: All tests green, but production has TypeError/AttributeError from JsProxy.

**Cause**: Mock bindings return native Python dicts. Real bindings return JsProxy objects. Your conversion code may have bugs that mocks never exercise.

**Fix**: Add E2E tests against real Cloudflare infrastructure:

```bash
# Deploy to a test instance
npx wrangler deploy --config examples/test-planet/wrangler.jsonc

# Run E2E tests against it
uv run pytest tests/e2e/ -x -v
```

E2E tests catch:
- JsProxy conversion bugs
- Real D1 SQL behavior (NULL vs undefined)
- Vectorize similarity scoring (mocks return fixed scores)
- Workers AI embedding dimensions
- Queue message serialization round-trips

---

## #14: Python `bytes` Cannot Cross FFI to R2/KV

**Symptom**: R2 `.put()`, KV `.put()`, or other binary APIs silently fail or reject the value.

Python `bytes` crosses the FFI as a `PyProxy`, not a `Uint8Array`. APIs that expect binary data will reject it.

```python
# WRONG — PyProxy, R2 rejects it
await env.BUCKET.put("key", my_bytes)

# CORRECT — Uint8Array
from pyodide.ffi import to_js
await env.BUCKET.put("key", to_js(my_bytes))
```

This applies to any API expecting binary: R2, KV (binary values), WebSocket `.send()` with binary frames.

---

## #15: Pyodide Cold Start Cancels First Queue Invocation

**Symptom**: Queue messages take 30-60s longer than expected. `process-now` endpoint works instantly. No logs, no exceptions.

When a queue message hits a **cold** Python Worker isolate, the Workers runtime cancels the first invocation (outcome: "canceled", zero logs, zero exceptions). The automatic retry succeeds because the isolate is now warm.

This is **not a bug** — it is inherent platform behavior due to Pyodide's cold start exceeding the queue consumer timeout. Do not chase it.

---

## #16: Miniflare Queue Consumer Unreliable for Python Workers

**Symptom**: Queue messages are never delivered when running locally with `wrangler dev`.

Miniflare's local queue consumer does not work reliably with Python Workers. Queue messages may never be delivered locally.

**Workaround**: Build a synchronous `POST /process-now` endpoint that runs the pipeline inline for local dev and testing:

```python
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if request.method == "POST" and parsed.path == "/process-now":
            await self.run_pipeline()  # Same logic as queue handler
            return Response("OK")
```

Verify actual queue behavior on real Cloudflare infrastructure, not locally.

---

## #17: Large Binary Data Must Not Round-Trip Through Python

**Symptom**: Worker crashes or exceeds memory limits when serving large R2 objects (>10MB).

The round-trip — JS ReadableStream to Python `bytes` to JS Response body — doubles memory in Wasm linear memory. For large objects this can crash the Worker.

**Fix**: Bypass Python entirely for large binary serving. Pass the ReadableStream directly to a JS Response:

```python
# In entry.py fetch(), before delegating to FastAPI:
from js import Response as JsResponse, Object
from pyodide.ffi import to_js

obj = await self.env.CONTENT.get(key)
if obj:
    headers = to_js({"Content-Type": content_type}, dict_converter=Object.fromEntries)
    return JsResponse.new(obj.body, to_js({"headers": headers}, dict_converter=Object.fromEntries))
```

For small payloads (<2MB), reading into Python `bytes` is fine. For large binary, pass the ReadableStream directly to the JS Response constructor.

---

## See Also

- [README.md](README.md) — Runtime overview, quick start, project structure
- [api.md](api.md) — Handler signatures, FFI functions, bindings, Workflows
- [configuration.md](configuration.md) — wrangler.jsonc, packages, flags, test setup
- [patterns.md](patterns.md) — FFI boundary, D1 conversion, Static Assets, DOs, testing
- [Cloudflare Python Workers Docs](https://developers.cloudflare.com/workers/languages/python/)
- [Python Workers Examples](https://github.com/cloudflare/python-workers-examples)
- [Pyodide Package List](https://pyodide.org/en/stable/usage/packages-in-pyodide.html)
