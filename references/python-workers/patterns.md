# Python Workers — Patterns

Production patterns that are **specific to Python Workers**. For general patterns (routing, auth, error handling) that work identically in JS Workers, see the Cloudflare Workers docs.

---

## Table of Contents

- [FFI Boundary Layer](#ffi-boundary-layer) — the most important pattern
- [D1 Row Conversion](#d1-row-conversion)
- [Queue Message Handling](#queue-message-handling)
- [Static Assets Architecture](#static-assets-architecture) — critical for cold starts
- [Jinja2 SSR Without Filesystem](#jinja2-ssr-without-filesystem)
- [`request.js_object` — Unwrapping the Python Wrapper](#requestjs_object--unwrapping-the-python-wrapper)
- [Durable Object WebSockets From Python](#durable-object-websockets-from-python)
- [Per-Isolate Initialization](#per-isolate-initialization)
- [Cold Start Mitigation](#cold-start-mitigation)
- [Testing](#testing)

---

## FFI Boundary Layer

**The single most important pattern.** Every Cloudflare binding returns JsProxy objects. If you let JsProxy leak into business logic, you get subtle bugs — things that look like dicts but aren't, JSON serialization failures, and tests that pass with mocks but fail in production.

### The principle

Convert JsProxy to native Python at the **boundary** — the moment data enters or leaves your Worker. Business logic should never see JsProxy.

### The HAS_PYODIDE guard

Makes your code importable in both Workers and pytest:

```python
# src/wrappers.py
try:
    import js
    from js import fetch as js_fetch
    from pyodide.ffi import to_js
    HAS_PYODIDE = True
    JS_NULL = js.JSON.parse("null")  # js.eval() is disallowed
except ImportError:
    js = None
    js_fetch = None
    to_js = None
    JS_NULL = None
    HAS_PYODIDE = False
```

### Safe recursive conversion

```python
_MAX_CONVERSION_DEPTH = 50

def _to_py_safe(value, depth=0):
    """Recursively convert JsProxy to native Python types."""
    if depth > _MAX_CONVERSION_DEPTH:
        return value
    if value is None:
        return None
    if _is_js_undefined(value):
        return None
    if hasattr(value, 'to_py'):
        return value.to_py()
    return value

def _is_js_undefined(value):
    if value is None:
        return False
    return (str(type(value)) == "<class 'pyodide.ffi.JsProxy'>"
            and str(value) == "undefined")
```

### Python-to-JS helper

```python
def _to_js_value(value):
    """Convert Python → JS with correct dict handling."""
    if not HAS_PYODIDE or to_js is None:
        return value  # Test environment — pass through
    if isinstance(value, dict):
        return to_js(value, dict_converter=js.Object.fromEntries)
    return to_js(value)
```

### Safe environment access

`self.env` attributes can be JsProxy or undefined. Wrap for safety:

```python
class SafeEnv:
    def __init__(self, env):
        self._env = env

    def get(self, key: str, default: str = "") -> str:
        try:
            value = getattr(self._env, key, None)
            if value is None or _is_js_undefined(value):
                return default
            return str(value)
        except Exception:
            return default
```

---

## D1 Row Conversion

D1 results are JsProxy. The pattern is: query → `.to_py()` → typed dict.

```python
from typing import TypedDict

class FeedRow(TypedDict):
    id: int
    url: str
    title: str | None
    is_active: int
    consecutive_failures: int

def feed_rows_from_d1(results) -> list[FeedRow]:
    """Convert D1 JsProxy results to typed Python dicts."""
    if not results or not results.results:
        return []
    return [dict(row) for row in results.results.to_py()]
```

### Parameterized queries (SQL injection prevention)

```python
# Correct — use bind() with ?
result = await env.DB.prepare(
    "SELECT * FROM entries WHERE feed_id = ? AND published_at > ? LIMIT ?"
).bind(feed_id, cutoff_date, limit).all()
rows = feed_rows_from_d1(result)
```

### Handling NULL with JS null

```python
# Python None → JS undefined (wrong for D1 NULL)
# Use JS_NULL for SQL NULL values
await env.DB.prepare(
    "UPDATE feeds SET etag = ? WHERE id = ?"
).bind(JS_NULL, feed_id).run()
```

---

## Queue Message Handling

Queue message bodies are JsProxy — convert before processing:

```python
class Default(WorkerEntrypoint):
    async def queue(self, batch):
        for message in batch.messages:
            try:
                body = message.body.to_py()  # JsProxy → dict
                await self.process(body)
                message.ack()
            except PermanentError:
                # Don't retry — send to DLQ manually
                await self.env.DEAD_LETTER_QUEUE.send(
                    _to_js_value({"job": body, "error": str(e)})
                )
                message.ack()
            except Exception:
                message.retry()  # Transient error — retry
```

### Sending to queues (Python dict → JS)

```python
await self.env.FEED_QUEUE.send(
    _to_js_value({
        "feed_id": feed["id"],
        "url": feed["url"],
        "etag": feed.get("etag"),
    })
)
```

---

## Static Assets Architecture

**This is a critical performance pattern for Python Workers.**

Workers Static Assets serves files directly from Cloudflare's edge **without invoking your Python Worker at all**. For Python Workers with ~1s cold starts, this means the difference between 15-90ms TTFB for CSS/JS/images versus 1000ms+.

### Two-tier serving

```
Request for /static/style.css
  → Cloudflare Edge: matches assets directory
  → Served directly: ~15-90ms TTFB
  → Python Worker: NEVER INVOKED

Request for /
  → Cloudflare Edge: no static match
  → Python Worker: boots Pyodide, runs fetch()
  → Response: ~90ms warm, ~1000ms cold
```

### Configuration

```jsonc
// wrangler.jsonc
{
  "assets": {
    "directory": "./assets/"
    // That's it. No binding needed. Files served automatically.
  }
}
```

### The binding anti-pattern

Adding a `"binding": "ASSETS"` creates a programmatic binding that routes through your Worker:

```python
# WRONG — the Worker is already awake by the time this runs
return await self.env.ASSETS.fetch(request)
```

Only add a binding if you need programmatic access for dynamic routing. For pure static serving, omit the binding — let the edge serve directly.

### Asset organization

```
assets/
├── static/
│   ├── style.css
│   ├── app.js
│   └── favicon.ico
├── fonts/
│   └── inter.woff2
└── images/
    └── logo.svg
```

### Cache headers

Static Assets are cached at the edge automatically (24h default). For additional control:

```python
# Only needed for dynamic content — static assets handle caching automatically
Response(html, headers={
    "Cache-Control": "public, max-age=3600, stale-while-revalidate=3600"
})
```

### Why this matters for Python

| Serving Method | TTFB | Worker Boot |
|----------------|------|-------------|
| Workers Static Assets (no binding) | 15-90ms | None |
| ASSETS binding via Worker | 1000-1400ms cold / 90ms warm | Yes |
| Python-generated response | 1000-1400ms cold / 90ms warm | Yes |

---

## Jinja2 SSR Without Filesystem

Workers have no writable filesystem. Jinja2's `FileSystemLoader` works for **bundled** files, but for best cold-start performance, pre-compile templates into a Python module.

### Pre-compiled templates (recommended)

```python
# scripts/build_templates.py — run at build time, generates src/templates.py
import jinja2
env = jinja2.Environment(loader=jinja2.FileSystemLoader("templates/"))
# ... compile and embed as Python string constants

# src/templates.py — generated, imported at runtime (captured in Wasm snapshot)
_EMBEDDED_TEMPLATES = {
    "home": "<html>{{ planet_name }}...</html>",
    "feed_rss": "<?xml ...{{ entries }}...",
}

# Usage in handler
template = jinja2.Template(_EMBEDDED_TEMPLATES["home"])
html = template.render(planet_name="My Planet", entries=entries)
```

**Why**: Jinja2 template parsing happens at import time, which is captured in the Wasm memory snapshot. Runtime rendering is just string substitution — fast.

### FileSystemLoader (simpler, slower cold start)

```python
from pathlib import Path
import jinja2

templates_dir = Path(__file__).parent.parent / "templates"
env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(templates_dir)))

template = env.get_template("home.html")
html = template.render(planet_name="My Planet")
```

This works but adds parsing time to cold starts.

---

## `request.js_object` — Unwrapping the Python Wrapper

The `request` parameter in `fetch(self, request)` and the `workers.Response` you return are Python wrappers around JavaScript objects. Most of the time the wrappers are fine. But some JavaScript APIs need the **raw underlying JS object**, not the wrapper.

### When you need `.js_object`

| API | Why | Code |
|-----|-----|------|
| `asgi.fetch()` (FastAPI bridge) | ASGI bridge expects a raw JS Request | `asgi.fetch(app, request.js_object, self.env)` |
| `HTMLRewriter.transform()` | JS API, doesn't understand the Python wrapper | `rewriter.transform(response.js_object)` |

### When you DON'T need it

For normal handler code — reading `request.url`, `request.method`, `request.headers`, `await request.json()` — the Python wrapper works fine. Only reach for `.js_object` when passing to a JS API that rejects the wrapper.

### FastAPI ASGI Bridge

```python
from fastapi import FastAPI, Request
from workers import WorkerEntrypoint

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello from FastAPI"}

@app.get("/env")
async def env(req: Request):
    env = req.scope["env"]  # Access Cloudflare bindings from FastAPI
    return {"name": env.APP_NAME}

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        import asgi                                          # Built-in module, not PyPI
        return await asgi.fetch(app, request.js_object, self.env)
        #                            ^^^^^^^^^^^^^^^^
        #                            Raw JS Request — ASGI bridge needs this
```

- `import asgi` — built-in module provided by the Workers runtime, not from PyPI
- `req.scope["env"]` — how FastAPI handlers access Cloudflare bindings
- Add `python_dedicated_snapshot` flag — FastAPI cold starts are ~1s with it, ~10s without

### HTMLRewriter

```python
from js import HTMLRewriter
from pyodide.ffi import create_proxy

class MetaInjector:
    def element(self, element):
        element.prepend('<meta property="og:title" content="..." />', html=True)

handler = create_proxy(MetaInjector())  # Must use create_proxy!
rewriter = HTMLRewriter.new()           # .new() for JS constructors
rewriter.on("head", handler)
return rewriter.transform(response.js_object)
#                         ^^^^^^^^^^^^^^^^^^
#                         Raw JS Response — HTMLRewriter needs this
```

`create_proxy` is required for the handler. `HTMLRewriter.new()` (not `HTMLRewriter()`). Pass `.js_object`, not the wrapper.

---

## Durable Object WebSockets From Python

```python
from js import WebSocketPair
from workers import DurableObject, Response

class ChatRoom(DurableObject):
    async def fetch(self, request):
        client, server = WebSocketPair.new().object_values()
        self.state.acceptWebSocket(server)
        return Response(None, status=101, web_socket=client)

    async def webSocketMessage(self, ws, message):
        for c in self.state.getWebSockets():
            c.send(message)
```

**Python-specific**: `WebSocketPair.new().object_values()` to destructure. `self.state` (not `self.ctx.state`).

---

## Per-Isolate Initialization

A `WorkerEntrypoint` instance persists across requests within the same V8 isolate. Class attributes are the place for per-isolate state — things you want to compute once and reuse for subsequent requests without paying the cost again.

**Why this is Python-specific**: In JS Workers you'd use a module-level variable. In Python Workers, module-level code runs at snapshot time (deploy), but the `WorkerEntrypoint` instance is created per isolate at request time. So per-isolate state lives on `self`, not in module scope.

### Pattern: one-time flag

```python
class Default(WorkerEntrypoint):
    _db_initialized: bool = False          # Persists across requests in this isolate

    async def _ensure_database_initialized(self):
        if self._db_initialized:
            return                          # 2nd+ request — skip entirely

        # Expensive: query sqlite_master, maybe run CREATE TABLE DDL
        result = await self.env.DB.prepare(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='feeds'"
        ).first()

        if result is None:
            await self.env.DB.exec("CREATE TABLE IF NOT EXISTS feeds (...)")

        self._db_initialized = True         # Even on error — don't retry every request
```

### Pattern: cached computed object

```python
class Default(WorkerEntrypoint):
    _router: RouteDispatcher | None = None  # Built once per isolate

    def _create_router(self) -> RouteDispatcher:
        if self._router is not None:
            return self._router             # Already compiled for this isolate
        self._router = RouteDispatcher([
            Route(path="/", content_type="html", cacheable=True),
            Route(path="/search", content_type="html"),
            # ... 13 routes with regex patterns to compile
        ])
        return self._router
```

### Pattern: cached wrapper

```python
class Default(WorkerEntrypoint):
    _cached_safe_env: SafeEnv | None = None

    @property
    def env(self) -> SafeEnv:
        raw_env = super().__getattribute__("_env_from_runtime")
        if self._cached_safe_env is None:
            self._cached_safe_env = SafeEnv(raw_env)
        return self._cached_safe_env
```

### What you can and can't cache

| OK to cache per-isolate | NOT OK |
|-------------------------|--------|
| Route dispatcher (compiled regexes) | Request-specific data |
| SafeEnv wrapper | User sessions |
| "DB initialized" flag | Query results |
| Jinja2 Environment | Mutable shared state (no locking in Workers) |

---

## Cold Start Mitigation

Python Workers cold start at ~1s (with snapshots). Strategies:

1. **`python_dedicated_snapshot`** — worker-specific Wasm snapshot (biggest win)
2. **Static Assets without binding** — CSS/JS/images never wake the Worker
3. **Pre-compiled templates** — Jinja2 parsing in snapshot, not at request time
4. **Per-isolate initialization** — compute expensive objects once, reuse across requests
5. **Module-level constants** — captured in snapshot (but NO PRNG calls)
6. **`stale-while-revalidate` cache** — serve stale content instantly while refreshing
7. **Cron cache pre-warming** — scheduled handler fetches own routes to keep cache hot
8. **Minimize top-level imports** — lazy import heavy modules inside handlers

### Edge cache with stale-while-revalidate

```python
# Serve stale content instantly while background-refreshing
Response(html, headers={
    "Cache-Control": "public, max-age=3600, stale-while-revalidate=3600"
})
# Hour 0-1: Fresh cache — zero Worker invocations
# Hour 1-2: Stale response served instantly + background Worker refresh
```

### Cron cache pre-warming

```python
async def scheduled(self, controller):
    """Hourly: enqueue feeds AND pre-warm edge cache."""
    await self.enqueue_active_feeds()

    # Pre-warm cache so visitors never hit cold Worker
    from workers import fetch
    origin = self.env.PLANET_URL
    for path in ["/", "/titles", "/feed.atom", "/feed.rss"]:
        await fetch(f"{origin}{path}")
```

---

## Testing

### The core challenge

Python Worker code imports `js`, `pyodide.ffi`, etc. — modules that only exist inside the Workers runtime. Tests must run outside that runtime (in regular Python/pytest).

### Three-tier strategy

| Tier | Speed | What it catches | Bindings |
|------|-------|-----------------|----------|
| **Unit** | ~1s | Logic, models, conversion | Python mocks |
| **Integration** | ~2s | HTTP flows, handler chains | Mock bindings (MockD1, MockQueue) |
| **E2E** | ~30s | JsProxy bugs, real behavior | Live Cloudflare (`wrangler dev --remote`) |

**Critical insight**: Mock-based tests will NOT catch JsProxy conversion bugs. Your mocks return Python dicts, but production returns JsProxy objects. You need E2E tests against real infrastructure.

### The HAS_PYODIDE pattern

```python
# src/wrappers.py — makes code importable in pytest
try:
    import js
    from pyodide.ffi import to_js
    HAS_PYODIDE = True
except ImportError:
    js = None
    to_js = None
    HAS_PYODIDE = False

def _to_js_value(value):
    if not HAS_PYODIDE:
        return value  # In tests, pass through unchanged
    if isinstance(value, dict):
        return to_js(value, dict_converter=js.Object.fromEntries)
    return to_js(value)
```

### Mock D1

```python
class MockD1Statement:
    def __init__(self, sql, db):
        self.sql = sql
        self.db = db
        self.params = []

    def bind(self, *args):
        self.params = list(args)
        return self

    async def all(self):
        rows = self.db._execute(self.sql, self.params)
        return type("D1Result", (), {"results": rows, "success": True})()

    async def first(self):
        rows = self.db._execute(self.sql, self.params)
        return rows[0] if rows else None

    async def run(self):
        self.db._execute(self.sql, self.params)
        return type("D1Meta", (), {"changes": 1})()

class MockD1:
    def __init__(self):
        self._tables = {}

    def prepare(self, sql):
        return MockD1Statement(sql, self)

    def _execute(self, sql, params):
        return []  # Override per test
```

### Mock Queue

```python
class MockQueue:
    def __init__(self):
        self.messages = []

    async def send(self, body, **kwargs):
        self.messages.append(body)
```

### Mock Workers AI

```python
class MockAI:
    async def run(self, model, inputs):
        if "bge-small" in model:
            texts = inputs.get("text", [])
            return type("R", (), {"data": [[0.1] * 768 for _ in texts]})()
        return type("R", (), {"output": "Mock response"})()
```

### Mock Vectorize

```python
class MockVectorize:
    def __init__(self):
        self._vectors = {}

    async def query(self, vector, options=None):
        matches = [type("M", (), {"id": id_, "score": 0.9})()
                   for id_ in list(self._vectors.keys())[:50]]
        return type("R", (), {"matches": matches})()

    async def upsert(self, vectors):
        for v in vectors:
            self._vectors[v["id"]] = v.get("values", [])
```

### Mock environment

```python
class MockEnv:
    def __init__(self):
        self.DB = MockD1()
        self.SEARCH_INDEX = MockVectorize()
        self.AI = MockAI()
        self.FEED_QUEUE = MockQueue()
        self.DEAD_LETTER_QUEUE = MockQueue()
        self.PLANET_NAME = "Test Planet"
        self.SESSION_SECRET = "test-secret-key"
```

### Test factories

```python
class FeedFactory:
    _counter = 0

    @classmethod
    def create(cls, **overrides) -> dict:
        cls._counter += 1
        defaults = {
            "id": cls._counter,
            "url": f"https://example.com/feed{cls._counter}.xml",
            "title": f"Test Feed {cls._counter}",
            "is_active": 1,
            "consecutive_failures": 0,
        }
        defaults.update(overrides)
        return defaults
```

### Running tests

```bash
uv run pytest tests/unit -x -q              # Fast unit tests
uv run pytest tests/integration -x -q        # Integration tests
uv run pytest tests/ --cov=src               # With coverage
```
