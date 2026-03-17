# Python Workers — Gotchas

Issues that are **specific to Python Workers**. General Cloudflare Workers issues (rate limits, billing, generic binding errors) are not included here.

---

## Quick Reference

| # | Issue | Error Signature | Fix |
|---|-------|-----------------|-----|
| 1 | Old handler pattern | `on_fetch is not defined` | Use `WorkerEntrypoint` class |
| 2 | Sync HTTP library | `blocking call in async context` | Use `httpx` or `aiohttp` |
| 3 | to_py is a method | `cannot import name 'to_py'` | Call `.to_py()` on the JsProxy |
| 4 | None ≠ null ≠ undefined | D1 NULL/undefined issues | Use `js.JSON.parse("null")` |
| 5 | js.eval() blocked | `Code generation disallowed` | Use `js.JSON.parse()` |
| 6 | Dict → Map | API rejects Python dict | Use `dict_converter=Object.fromEntries` |
| 7 | Slow cold start | First request 1-10s | Add `python_dedicated_snapshot` flag |
| 8 | PRNG at init | Deploy fails | Move `random`/`secrets` into handlers |
| 9 | D1 results are JsProxy | TypeError on iteration | Call `.to_py()` on `results.results` |
| 10 | Queue body is JsProxy | TypeError on field access | Call `msg.body.to_py()` |
| 11 | CPU exceeded | Worker killed | Increase `cpu_ms` (Python burns more CPU) |
| 12 | Stdlib limitations | ImportError | Check list below |
| 13 | Native packages fail | ModuleNotFoundError | Use Pyodide-compatible alternatives |
| 14 | Mocks pass, prod fails | JsProxy bugs in production | Add E2E tests against real infra |

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

## #2: Sync HTTP Libraries Don't Work

**Error**: `RuntimeError: cannot use blocking call in async context`

Pyodide blocks raw sockets (browser security model). Only async HTTP works.

```python
# FAILS: requests, urllib3, httplib
import requests
response = requests.get(url)

# WORKS: httpx (async), aiohttp, or js.fetch
import httpx
async with httpx.AsyncClient() as client:
    response = await client.get(url)
```

---

## #3: to_py Is a Method, Not a Function

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

## #4: None vs null vs undefined

Python `None` → JS `undefined` (NOT `null`). Three distinct values cross the boundary:

| Python | JavaScript | Notes |
|--------|------------|-------|
| `None` | `undefined` | Default mapping |
| `JS_NULL` | `null` | Must create explicitly |
| `None` (from `.to_py()`) | was `null` | JS null becomes Python None |

**Problem**: D1 expects `null` for SQL NULL, but gets `undefined` from Python `None`.

```python
# WRONG — sends undefined to D1
await env.DB.prepare("UPDATE feeds SET etag = ?").bind(None).run()

# CORRECT
JS_NULL = js.JSON.parse("null")
await env.DB.prepare("UPDATE feeds SET etag = ?").bind(JS_NULL).run()
```

JS `undefined` can also arrive as a JsProxy in Python — detect with:

```python
def _is_js_undefined(value):
    if value is None:
        return False
    return str(type(value)) == "<class 'pyodide.ffi.JsProxy'>" and str(value) == "undefined"
```

---

## #5: js.eval() Is Disallowed

**Error**: `EvalError: Code generation from strings disallowed for this context`

Workers security policy blocks `eval()`.

```python
# WRONG
JS_NULL = js.eval("null")

# CORRECT
JS_NULL = js.JSON.parse("null")
```

---

## #6: Dict Becomes Map Without dict_converter

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

## #7: Cold Start Performance

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
5. Module-level constants are captured in snapshot (but no PRNG — see #8)

---

## #8: PRNG Cannot Be Seeded During Initialization

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

## #9: D1 Results Are JsProxy

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

## #10: Queue Message Body Is JsProxy

**Symptom**: TypeError when accessing queue message fields.

```python
# WRONG
feed_id = msg.body["feed_id"]

# CORRECT
body = msg.body.to_py()
feed_id = body["feed_id"]
```

---

## #11: CPU Time Exceeded

**Symptom**: Worker killed mid-execution.

Python Workers consume more CPU than JS Workers. Pyodide interpretation overhead + Python library CPU usage (feedparser, bleach, JSON, XML parsing).

```jsonc
"limits": { "cpu_ms": 60000 }  // Increase from 30s default
```

---

## #12: Standard Library Limitations

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

## #13: Native/Compiled Packages Don't Work

**Error**: `ModuleNotFoundError` or build errors.

Only pure Python packages and Pyodide-compiled packages work. Native C extensions cannot be compiled to WebAssembly automatically.

Check: https://pyodide.org/en/stable/usage/packages-in-pyodide.html

**Common alternatives**:
| Doesn't Work | Use Instead |
|---------------|-------------|
| `lxml` | `xml.etree.ElementTree` |
| `psycopg2` | D1 (SQLite) |
| `cryptography` | `hashlib`/`hmac` from stdlib |
| `requests` | `httpx` (async) |

Request new packages: https://github.com/cloudflare/workerd/discussions/categories/python-packages

---

## #14: Mocks Pass, Production Fails

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
