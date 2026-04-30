# Best Practices for Cloudflare Python Workers

> **About this document**
>
> This document mirrors the contents of the [python-workers skill](https://github.com/adewale/python-workers-skill). If you spot something wrong, outdated, or missing, [open an issue](https://github.com/adewale/python-workers-skill/issues) or submit a PR — changes here will be reflected in the skill, and vice versa.
>
> The skill is organized for an AI coding assistant to look things up quickly. This document covers the same ground, but is written for humans — developers who know Python but may be new to Cloudflare Workers, Pyodide, or the particular constraints of running Python inside a browser-style WebAssembly sandbox.

---

## Table of Contents

- [Getting Started](#getting-started)
  - [What Are Python Workers?](#what-are-python-workers)
  - [How Pyodide Works (and Why It Matters)](#how-pyodide-works-and-why-it-matters)
  - [Quick Start](#quick-start)
  - [Project Structure](#project-structure)
  - [The `workers` Module](#the-workers-module)
- [The FFI Boundary](#the-ffi-boundary)
  - [What the FFI Boundary Is, in Plain Terms](#what-the-ffi-boundary-is-in-plain-terms)
  - [Importing JavaScript Globals](#importing-javascript-globals)
  - [Converting Python to JavaScript (`to_js`)](#converting-python-to-javascript-to_js)
  - [Converting JavaScript to Python (`.to_py()`)](#converting-javascript-to-python-to_py)
  - [The FFI Boundary Layer Pattern](#the-ffi-boundary-layer-pattern)
  - [The `None` / `null` / `undefined` Problem](#the-none--null--undefined-problem)
  - [FFI Type-Compatibility Matrix](#ffi-type-compatibility-matrix)
  - [Passing Python Callables to JS APIs (`create_proxy`)](#passing-python-callables-to-js-apis-create_proxy)
  - [Binary Data Across the Boundary](#binary-data-across-the-boundary)
  - [`request.js_object` — Unwrapping the Python Wrapper](#requestjs_object--unwrapping-the-python-wrapper)
- [Working with Bindings](#working-with-bindings)
  - [General Binding Access](#general-binding-access)
  - [D1 (SQL Database)](#d1-sql-database)
  - [KV (Key-Value Store)](#kv-key-value-store)
  - [R2 (Object Storage)](#r2-object-storage)
  - [Queues](#queues)
  - [Vectorize](#vectorize)
  - [Workers AI](#workers-ai)
  - [Durable Objects](#durable-objects)
  - [Workflows](#workflows)
  - [Service Bindings and RPC](#service-bindings-and-rpc)
- [Static Assets](#static-assets)
  - [Why This Matters More for Python Than JS](#why-this-matters-more-for-python-than-js)
  - [Configuration](#static-assets-configuration)
  - [Routing: When Does the Worker Actually Run?](#routing-when-does-the-worker-actually-run)
  - [The Binding Anti-Pattern](#the-binding-anti-pattern)
  - [Asset Organization and Limits](#asset-organization-and-limits)
- [Testing](#testing)
  - [The Core Challenge](#the-core-challenge)
  - [Three-Tier Testing Strategy](#three-tier-testing-strategy)
  - [The HAS_PYODIDE Guard Pattern](#the-has_pyodide-guard-pattern)
  - [Mock Bindings](#mock-bindings)
  - [Testing Both Branches (Pyodide Fakes)](#testing-both-branches-pyodide-fakes)
  - [Running Tests](#running-tests)
- [Configuration](#configuration)
  - [wrangler.jsonc](#wrangerjsonc)
  - [Compatibility Flags](#compatibility-flags)
  - [pyproject.toml and Package Management](#pyprojecttoml-and-package-management)
  - [CPU Limits](#cpu-limits)
  - [Local Development](#local-development)
  - [Deployment and Import Roots](#deployment-and-import-roots)
- [Common Pitfalls](#common-pitfalls)
- [Anti-Patterns](#anti-patterns)

---

## Getting Started

### What Are Python Workers?

Cloudflare Workers are small programs that run on Cloudflare's global network — close to your users, at the "edge." Normally you write them in JavaScript or TypeScript. Python Workers let you write them in Python instead.

The catch is that Python does not run natively inside a browser engine. To make it work, Cloudflare uses **Pyodide**, which is CPython (the standard Python interpreter) compiled to WebAssembly. Your Python code runs inside a V8 JavaScript isolate — the same engine that runs Node.js and Chrome — but through a translation layer.

This means Python Workers are real Python (3.12+), with access to most of the standard library and many PyPI packages. But there are meaningful differences from both normal Python development and normal JavaScript Workers development. This document is about those differences.

**Status**: Python Workers are in Open Beta. You must explicitly opt in with the `python_workers` compatibility flag.

### How Pyodide Works (and Why It Matters)

Here is what happens when your Python Worker runs:

1. Your Python code runs inside **Pyodide** (CPython compiled to WebAssembly) in a V8 isolate.
2. Pyodide's **FFI** (Foreign Function Interface) bridges JavaScript and Python. All Cloudflare bindings — D1, KV, R2, Queues, everything — work through JavaScript proxy objects.
3. At deploy time, Cloudflare takes a **WebAssembly memory snapshot**. Your imports are pre-resolved, module-level code has already run, and the state of memory is frozen.
4. At request time, that snapshot is restored (fast), and your handler runs.

The snapshot is what makes cold starts tolerable. Without it, every cold start would need to boot the entire Python interpreter from scratch (~10 seconds). With a dedicated snapshot, cold starts drop to roughly 1 second — still much slower than the ~50ms cold start of a JavaScript Worker, but workable.

The snapshot also introduces a constraint: **module-level code runs at deploy time, not at request time.** This is mostly fine (define constants, import libraries), but it means you cannot call anything that generates random numbers at module level — `random.seed()`, `secrets.token_hex()`, `uuid.uuid4()` — because PRNG state cannot be meaningfully captured in a snapshot.

### Quick Start

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

### Project Structure

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
├── pyproject.toml           # Python deps (Pyodide-compatible)
└── Makefile                 # test, lint, deploy commands
```

One thing to know about the import root: when your `wrangler.jsonc` says `"main": "src/main.py"`, the `src/` directory becomes the import root. So if you have `src/models.py`, you import it as `from models import FeedRow`, not `from src.models import FeedRow`. The `src.` prefix will fail.

### The `workers` Module

The `workers` module is provided by the runtime. It gives you the base classes and helpers you will use constantly:

| Export | Purpose |
|--------|---------|
| `WorkerEntrypoint` | Base class for your Worker — has `fetch`, `scheduled`, `queue` methods |
| `DurableObject` | Base class for Durable Objects |
| `WorkflowEntrypoint` | Base class for Workflows |
| `Response` | Python wrapper around the JS Response object |
| `fetch` | Python wrapper around the JS `fetch()` function |

Python Workers use a **class-based pattern**. Instead of the JavaScript `export default { fetch() {} }`, you define a class that inherits from `WorkerEntrypoint` and implement async methods:

```python
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        # Handle HTTP requests
        return Response("OK")

    async def scheduled(self, controller):
        # Handle cron triggers
        print(f"Cron triggered: {controller.cron}")

    async def queue(self, batch):
        # Handle queue message batches
        for message in batch.messages:
            body = message.body.to_py()
            await self.process(body)
            message.ack()
```

No separate exports. One class, three methods. All optional — implement only what you need.

There is a legacy pattern using `@handler` decorators and `on_fetch`/`on_scheduled` functions. It was deprecated in August 2025. If you see `TypeError: on_fetch is not defined`, you are using the old pattern.

---

## The FFI Boundary

### What the FFI Boundary Is, in Plain Terms

"FFI" stands for Foreign Function Interface. In the context of Python Workers, it is the layer where Python and JavaScript talk to each other.

Here is the mental model: your Python code lives in one world, and Cloudflare's JavaScript APIs live in another. When you call `self.env.DB.prepare("SELECT * FROM feeds").all()`, you are reaching across into JavaScript land. The query runs in JavaScript. The result comes back to Python. But the result is not a Python dict or list — it is a **JsProxy**, a thin Python wrapper around the JavaScript object.

A JsProxy looks a bit like a Python object. You can access some attributes. But it is not a real Python dict, list, or string. If you try to serialize it with `json.dumps()`, it will fail. If you iterate over it expecting Python list behavior, you may get surprises.

The fundamental rule is: **convert at the boundary, not inline.** The moment data crosses from JavaScript to Python, convert it to a native Python type. The moment you need to send Python data to a JavaScript API, convert it to the right JavaScript type. Never let JsProxy objects leak into your business logic.

### Importing JavaScript Globals

Anything available as a JavaScript global can be imported from the `js` module:

```python
from js import fetch, console, Response, Object, JSON, URL, Headers
from js import HTMLRewriter, WebSocket, WebSocketPair
```

This is how you access JavaScript functionality directly. In most cases you will use the `workers` module wrappers instead, but sometimes you need the raw JavaScript APIs.

### Converting Python to JavaScript (`to_js`)

`to_js` is a standalone function you import from `pyodide.ffi`:

```python
from js import Object
from pyodide.ffi import to_js

# CRITICAL: Always use dict_converter for dicts
# Without it, Python dicts become JS Map (not Object) — most APIs break
python_dict = {"name": "test", "count": 42}
js_object = to_js(python_dict, dict_converter=Object.fromEntries)

# Lists convert directly
js_array = to_js([1, 2, 3])

# Primitives (str, int, float, bool) — auto-converted, no to_js needed
```

The `dict_converter` parameter is not optional in practice. Without it, `to_js()` converts a Python dict to a JavaScript `Map`, which is technically correct but practically wrong — almost every Cloudflare API expects a plain JavaScript `Object`, not a `Map`. The API will silently reject it or return unexpected results. Always use `dict_converter=Object.fromEntries`.

A good practice is to wrap this in a helper so you do not have to remember it every time:

```python
def _to_js_value(value):
    if isinstance(value, dict):
        return to_js(value, dict_converter=js.Object.fromEntries)
    return to_js(value)
```

### Converting JavaScript to Python (`.to_py()`)

Here is an asymmetry that trips people up: `to_js` is a standalone function, but `to_py` is a **method** on JsProxy objects. You cannot import it.

```python
# WRONG — ImportError!
from pyodide.ffi import to_py

# CORRECT — call .to_py() on the JsProxy object
data = await request.json()          # Returns a JsProxy
python_dict = data.to_py()           # Now a Python dict

results = await env.DB.prepare("...").all()
rows = results.results.to_py()       # Python list of dicts
```

This is easy to remember once you know: `to_js()` is a function you call to go *to* JavaScript. `.to_py()` is a method you call *on* a JavaScript object to bring it into Python.

For deeper or more defensive conversion, use a recursive helper:

```python
_MAX_CONVERSION_DEPTH = 50

def _to_py_safe(value, depth=0):
    """Recursively convert JsProxy to native Python types."""
    if depth > _MAX_CONVERSION_DEPTH:
        return value
    if value is None:
        return None
    if hasattr(value, 'to_py'):
        return value.to_py()
    return value
```

Note: JS `undefined` arrives in Python as `None`, so `value is None` already covers it. There is no separate `JsUndefined` type.

### The FFI Boundary Layer Pattern

This is the single most important pattern in Python Workers development. Every Cloudflare binding returns JsProxy objects. If you let JsProxy leak into your business logic, you get subtle bugs — things that look like dicts but are not, JSON serialization failures, and tests that pass with mocks but fail in production.

The principle: convert JsProxy to native Python at the **boundary** — the moment data enters or leaves your Worker. Business logic should never see JsProxy.

In practice, this means creating wrapper classes for your bindings:

```python
class SafeR2:
    def __init__(self, bucket):
        self._bucket = bucket

    async def get(self, key):
        obj = await self._bucket.get(key)
        if obj is jsnull:
            return None  # Guard reads: jsnull -> None
        return obj

    async def put(self, key, data):
        if isinstance(data, bytes):
            data = to_js(data)  # Guard writes: bytes -> Uint8Array
        await self._bucket.put(key, data)
```

This pattern applies to any binding wrapper — SafeD1, SafeKV, SafeQueue. Every method that reads from JavaScript must check for `jsnull`. Every method that writes to JavaScript must convert Python types appropriately.

For environment variables, which can also be JsProxy or missing:

```python
class SafeEnv:
    def __init__(self, env):
        self._env = env

    def get(self, key: str, default: str = "") -> str:
        return getattr(self._env, key, default)
```

### The `None` / `null` / `undefined` Problem

This is one of the trickiest parts of the FFI boundary. JavaScript has two "nothing" values: `null` and `undefined`. Python has one: `None`. When they cross the boundary, they do not map neatly onto each other.

Going from Python to JavaScript:
- Python `None` becomes JavaScript `undefined` (NOT `null`)

Going from JavaScript to Python:
- JavaScript `null` arrives as the `jsnull` sentinel from `pyodide.ffi` (NOT Python `None`)
- JavaScript `undefined` arrives as a Python `None`

This matters in practice because D1 expects `null` for SQL NULL values, but Python `None` gives it `undefined` instead, which D1 rejects.

| Value | `is None` | `bool()` | `type().__name__` |
|-------|:---------:|:--------:|:-----------------:|
| Python `None` | `True` | `False` | `NoneType` |
| JS `null` (`jsnull`) | **`False`** | `False` | `JsNull` |

The fix for sending `null` to JavaScript:

```python
from pyodide.ffi import jsnull
```

For checking whether a value from JavaScript is "missing," use a helper that covers both cases:

```python
from pyodide.ffi import jsnull

def _is_missing(value):
    """True for Python None (which includes JS undefined) or JS null."""
    return value is None or value is jsnull
```

**Rule**: Any boundary code that uses `if x is None` must also check for `jsnull`, or use `_is_missing()`.

### FFI Type-Compatibility Matrix

This table shows what happens when types cross the boundary. Read it once, bookmark it, and come back when something silently fails.

**Python to JavaScript (Outbound):**

| Python Type | Becomes in JS | D1 `.bind()` | R2 `.put()` | Queue `.send()` | Notes |
|-------------|---------------|:---:|:---:|:---:|-------|
| `str` | `string` | OK | OK | OK | |
| `int` | `number` | OK | OK | OK | |
| `float` | `number` | OK | OK | OK | |
| `bool` | `boolean` | OK | OK | OK | |
| `None` | `undefined` | **BREAKS** | OK | OK | D1 rejects `undefined`; use `jsnull` |
| `dict` | `Map` | N/A | N/A | **Fails silently** | Use `to_js(d, dict_converter=Object.fromEntries)` |
| `list` | `Array` | N/A | OK | OK | Via `to_js()` |
| `bytes` | `PyProxy` | **BREAKS** | **BREAKS** | **BREAKS** | Must use `to_js(data)` to get Uint8Array |
| `datetime` | `PyProxy` | **BREAKS** | **BREAKS** | **BREAKS** | Must convert to ISO string first |

**JavaScript to Python (Inbound):**

| JS Type | Arrives as in Python | Subscriptable? | `.to_py()` | Notes |
|---------|---------------------|:-:|:-:|-------|
| `Object` | `JsProxy` | No | `dict` | Must call `.to_py()` |
| `Array` | `JsProxy` | No | `list` | |
| `null` | `jsnull` | N/A | N/A | **NOT** Python `None`; `from pyodide.ffi import jsnull` |
| `undefined` | `None` | N/A | N/A |  |
| `string` | `str` | N/A | N/A | Auto-converted |
| `number` | `int`/`float` | N/A | N/A | Auto-converted |
| `boolean` | `bool` | N/A | N/A | Auto-converted |
| `ArrayBuffer` | `JsProxy` | N/A | `bytes` | Via `.to_bytes()` |

### Passing Python Callables to JS APIs (`create_proxy`)

Some JavaScript APIs need to hold a reference to a callback function — `addEventListener`, `HTMLRewriter.on`, and similar. When you pass a Python function to these APIs, you need to wrap it with `create_proxy` so JavaScript can hold onto it safely:

```python
from pyodide.ffi import create_proxy

class MetaInjector:
    def element(self, element):
        element.prepend('<meta property="og:title" content="..." />', html=True)

handler = create_proxy(MetaInjector())
rewriter = HTMLRewriter.new()
rewriter.on("head", handler)
result = rewriter.transform(response.js_object)
handler.destroy()  # Release when done (except in long-lived DOs)
```

Call `.destroy()` when you are done with the proxy to release the reference — unless you are in a long-lived context like a Durable Object, where the proxy should persist.

### Binary Data Across the Boundary

Python `bytes` cannot cross the FFI directly to binary APIs. When you pass `bytes` to R2's `.put()`, KV's `.put()`, or WebSocket's `.send()`, it arrives on the JavaScript side as a `PyProxy` — which those APIs do not understand.

```python
# WRONG — PyProxy, R2 rejects it
await env.BUCKET.put("key", my_bytes)

# CORRECT — Uint8Array
from pyodide.ffi import to_js
await env.BUCKET.put("key", to_js(my_bytes))
```

For returning binary data in a Response, you need an extra step — `to_js(bytes)` creates a `Uint8Array`, and the Response constructor wants an `ArrayBuffer`:

```python
from pyodide.ffi import to_js

image_bytes = generate_image()  # Python bytes
body = to_js(image_bytes).buffer  # .buffer extracts the ArrayBuffer

return Response(body, headers={
    "Content-Type": "image/png",
    "Cache-Control": "public, max-age=86400"
})
```

For large binary data (over ~10MB), avoid pulling the data through Python entirely. The round-trip — JavaScript ReadableStream to Python bytes to JavaScript Response body — doubles memory usage in WebAssembly linear memory and can crash the Worker. Instead, pass the ReadableStream directly to a JavaScript Response:

```python
from js import Response as JsResponse, Object
from pyodide.ffi import to_js

obj = await self.env.CONTENT.get(key)
if obj:
    headers = to_js({"Content-Type": content_type}, dict_converter=Object.fromEntries)
    return JsResponse.new(obj.body, to_js({"headers": headers}, dict_converter=Object.fromEntries))
```

### `request.js_object` — Unwrapping the Python Wrapper

The `request` you receive in `fetch(self, request)` is a Python wrapper around the underlying JavaScript Request object. Most of the time, the wrapper works fine — `request.url`, `request.method`, `request.headers.get()`, `await request.json()` all work as expected.

But some JavaScript APIs need the **raw** JavaScript object, not the wrapper. When you see an error from a JS API that does not understand the request or response you passed, try `.js_object`:

| API | Why | Code |
|-----|-----|------|
| `asgi.fetch()` (FastAPI bridge) | ASGI bridge expects a raw JS Request | `asgi.fetch(app, request.js_object, self.env)` |
| `HTMLRewriter.transform()` | JS API, doesn't understand the Python wrapper | `rewriter.transform(response.js_object)` |

The FastAPI integration is a common use case:

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

Note that `import asgi` is a built-in module provided by the Workers runtime. It is not on PyPI. And `req.scope["env"]` is how you access Cloudflare bindings from within FastAPI handlers.

Similarly, when using JavaScript constructors directly via FFI, you need `.new()`:

```python
from js import Response as JsResponse
return JsResponse.new("Hello!")  # .new() required for JS constructors via FFI
```

---

## Working with Bindings

### General Binding Access

All bindings are accessed via `self.env` — same names, same methods as JavaScript Workers. The difference is that **return values are JsProxy objects** that must be converted.

```python
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        # Environment variables — auto-converted (strings)
        name = self.env.PLANET_NAME

        # Secrets — same as env vars
        secret = self.env.SESSION_SECRET
```

What follows is how each specific binding type differs from its JavaScript counterpart.

### D1 (SQL Database)

D1 results are JsProxy. The pattern is: query, call `.to_py()`, then optionally convert to typed dicts.

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

Usage:

```python
results = await self.env.DB.prepare("SELECT * FROM feeds").all()
rows = feed_rows_from_d1(results)  # Native Python dicts
```

Parameterized queries work the same as in JavaScript — use `bind()` with `?` placeholders:

```python
result = await env.DB.prepare(
    "SELECT * FROM entries WHERE feed_id = ? AND published_at > ? LIMIT ?"
).bind(feed_id, cutoff_date, limit).all()
```

The NULL caveat: Python `None` becomes JavaScript `undefined`, but D1 needs `null` for SQL NULL values. Use the `jsnull` sentinel:

```python
from pyodide.ffi import jsnull

await env.DB.prepare(
    "UPDATE feeds SET etag = ? WHERE id = ?"
).bind(jsnull, feed_id).run()
```

### KV (Key-Value Store)

String values auto-convert. JSON values need `.to_py()`. Binary values need `to_js()` when writing.

```python
# Reading strings — auto-converted
value = await self.env.MY_KV.get("key")

# Writing binary — must convert
await self.env.MY_KV.put("key", to_js(my_bytes))
```

### R2 (Object Storage)

Same pattern as KV for binary data. Python `bytes` must be converted to `Uint8Array` via `to_js()`:

```python
# WRONG — PyProxy, R2 rejects it
await self.env.BUCKET.put("key", my_bytes)

# CORRECT — Uint8Array
await self.env.BUCKET.put("key", to_js(my_bytes))
```

For large objects (over ~10MB), do not read the data into Python at all. Pass the R2 object's `ReadableStream` body directly to a JavaScript Response to avoid doubling memory usage. See the [Binary Data Across the Boundary](#binary-data-across-the-boundary) section above.

### Queues

Queue message bodies arrive as JsProxy — convert before processing. When sending to a queue, dicts need `to_js()` with `dict_converter`:

```python
class Default(WorkerEntrypoint):
    async def queue(self, batch):
        for message in batch.messages:
            try:
                body = message.body.to_py()  # JsProxy -> dict
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

Sending to a queue:

```python
await self.env.FEED_QUEUE.send(
    _to_js_value({
        "feed_id": feed["id"],
        "url": feed["url"],
        "etag": feed.get("etag"),
    })
)
```

### Vectorize

Both input vectors and options need `to_js()` conversion. Results need `.to_py()`:

```python
js_vector = to_js(query_vector)
js_options = to_js({"topK": 50}, dict_converter=Object.fromEntries)
matches = await self.env.SEARCH_INDEX.query(js_vector, js_options)
```

Vectorize has no local simulation — during local development, you must set `"remote": true` on the Vectorize binding in your `wrangler.jsonc`:

```jsonc
"vectorize": [{ "binding": "SEARCH_INDEX", "index_name": "...", "remote": true }]
```

### Workers AI

Input dicts need `to_js()`, output needs `.to_py()`:

```python
response = await self.env.AI.run("@cf/baai/bge-small-en-v1.5",
    {"text": ["hello"]})
embeddings = [list(e) for e in response.data.to_py()]
```

Like Vectorize, you may want `"remote": true` for local development:

```jsonc
"ai": { "binding": "AI", "remote": true }
```

### Durable Objects

Durable Objects are stateful, single-instance objects that persist across requests. In Python, you subclass `DurableObject` from the `workers` module.

**Transactional Storage**

Durable Objects have persistent storage via `self.ctx.storage`. Primitives (strings, numbers, booleans) can be stored directly, but non-primitive values (lists, dicts) must be converted with `to_js()`:

```python
from workers import DurableObject, Response
from pyodide.ffi import to_js
from js import Object

class Counter(DurableObject):
    async def fetch(self, request):
        count = await self.ctx.storage.get("count")
        if count is None:
            count = 0

        count += 1

        # Primitives are OK as-is
        await self.ctx.storage.put("count", count)
        # Lists/dicts need to_js()
        await self.ctx.storage.put("history",
            to_js([1, 2, 3], dict_converter=Object.fromEntries))

        return Response.json({"count": count})
```

Note that in Python it is `self.ctx.storage`, not `self.state.storage`.

Wrangler config for DO migrations:

```jsonc
"migrations": [{ "tag": "v1", "new_sqlite_classes": ["Counter"] }]
```

**Alarms**

Alarms let a Durable Object schedule itself to wake up at a future time — useful for polling, retries, and recurring work:

```python
class Poller(DurableObject):
    async def fetch(self, request):
        import js
        now = js.Date.now()
        await self.ctx.storage.setAlarm(now + 60_000)  # 60 seconds from now
        return Response("Alarm set")

    async def alarm(self):
        """Called when the alarm fires."""
        await self.do_work()
        # Re-schedule for recurring work
        import js
        await self.ctx.storage.setAlarm(js.Date.now() + 60_000)
```

**WebSocket Hibernation**

The hibernation API allows Durable Objects to release memory between WebSocket messages. This is important for chat rooms, live dashboards, and any use case with many concurrent connections:

```python
from js import WebSocketPair
from workers import DurableObject, Response

class ChatRoom(DurableObject):
    async def fetch(self, request):
        client, server = WebSocketPair.new().object_values()
        self.state.acceptWebSocket(server)  # Register for hibernation
        return Response(None, status=101, web_socket=client)

    async def webSocketMessage(self, ws, message):
        """Called when a message is received on any accepted WebSocket."""
        for c in self.state.getWebSockets():
            c.send(message)

    async def webSocketClose(self, ws, code, reason, wasClean):
        """Called when an accepted WebSocket closes."""
        ws.close(code, reason)

    async def webSocketError(self, ws, error):
        """Called when an accepted WebSocket errors."""
        ws.close(1011, "WebSocket error")
```

Python-specific: `WebSocketPair.new().object_values()` to destructure the pair. `self.state.acceptWebSocket()` for hibernation is different from `self.ctx.storage` for transactional storage.

**Outbound WebSocket Client**

Durable Objects can also act as WebSocket clients, connecting to external services:

```python
from js import WebSocket
from pyodide.ffi import create_proxy

class StreamConsumer(DurableObject):
    async def fetch(self, request):
        ws = WebSocket.new("wss://external-service.example/stream")

        def on_message(event):
            data = event.data
            # Process incoming message

        def on_close(event):
            # Reconnect logic
            pass

        ws.addEventListener("message", create_proxy(on_message))
        ws.addEventListener("close", create_proxy(on_close))
        # create_proxy() instances persist for the DO's lifetime
        # — no need to .destroy() them, the DO restart handles cleanup

        return Response("Connected")
```

### Workflows

Python Workers can define multi-step Workflows using `WorkflowEntrypoint`. Steps declare dependencies and can run concurrently:

```python
from workers import WorkflowEntrypoint, WorkerEntrypoint, Response

class MyWorkflow(WorkflowEntrypoint):
    async def run(self, event, step):
        @step.do("fetch data")
        async def fetch_data():
            result = await self.env.DB.prepare("SELECT * FROM feeds").all()
            return [dict(r) for r in result.results.to_py()]

        @step.do("process", depends=[fetch_data])
        async def process():
            data = fetch_data.result
            return {"processed": len(data)}

        @step.do("notify", depends=[process])
        async def notify():
            await self.env.QUEUE.send(process.result)

        # Steps with concurrent=True run in parallel
        @step.do("task_a", concurrent=True)
        async def task_a():
            return "a"

        @step.do("task_b", concurrent=True)
        async def task_b():
            return "b"

# Trigger from another Worker
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        workflow = await self.env.MY_WORKFLOW.create()
        return Response.json({"id": workflow.id})
```

Wrangler config:

```jsonc
"workflows": [{ "name": "my-workflow", "binding": "MY_WORKFLOW", "class_name": "MyWorkflow" }]
```

Workflows require three compatibility flags: `"python_workers"`, `"python_workflows"`, and `"experimental"`.

### Service Bindings and RPC

Python Workers can expose custom methods beyond `fetch`, callable from other Workers via Service Bindings:

```python
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response("RPC server")

    async def highlight_code(self, code: str, language: str = None) -> dict:
        """Custom RPC method — callable via Service Binding."""
        return {"html": highlighted, "language": language}
```

There is an important caveat: Python RPC methods that return `dict` will arrive as a JavaScript `Map` on the caller side. This is the same `dict` without `dict_converter` issue that affects all FFI conversions. If the caller is a TypeScript Worker, they receive `Map<string, string>`, not a plain object.

---

## Static Assets

### Why This Matters More for Python Than JS

With JavaScript Workers, cold starts are roughly 50 milliseconds. Whether your CSS file is served by the Worker or directly from the edge, the user barely notices.

With Python Workers, cold starts are roughly 1 second (with a dedicated snapshot) to 10 seconds (without). If a request for `style.css` has to wake up your Python Worker, the user is waiting a full second for a stylesheet. Multiply that by every asset on the page, and initial page loads become painful.

Workers Static Assets solves this by serving files directly from Cloudflare's edge **without invoking your Worker at all**. Requests for static files never touch your Python code. They get the same 15-90ms TTFB as any CDN-served asset.

```
Request for /static/style.css
  -> Cloudflare Edge: matches assets directory
  -> Served directly: ~15-90ms TTFB
  -> Python Worker: NEVER INVOKED

Request for /
  -> Cloudflare Edge: no static match
  -> Python Worker: boots Pyodide, runs fetch()
  -> Response: ~90ms warm, ~1000ms cold
```

### Static Assets Configuration

```jsonc
// wrangler.jsonc
{
  "assets": {
    "directory": "./assets/"
    // That's it. No binding needed. Files served automatically.
  }
}
```

That is the minimal configuration. Requests to files in the `assets/` directory are served directly. No Worker invocation. No Python cold start.

### Routing: When Does the Worker Actually Run?

By default, if a request matches a file in the assets directory, it is served directly and the Worker is never invoked. Unmatched requests fall through to the Worker.

You can customize this with `run_worker_first`:

```jsonc
{
  "assets": {
    "directory": "./assets/",
    "run_worker_first": ["/api/*"],
    "not_found_handling": "single-page-application",
    "html_handling": "auto-trailing-slash"
  }
}
```

| `run_worker_first` | Behavior |
|---|---|
| `false` (default) | Assets served first; unmatched requests go to Worker |
| `true` | Worker runs for every request; use `env.ASSETS.fetch(request)` to serve assets |
| `["/api/*"]` | Worker runs first only for matching patterns; assets served directly for everything else |

| `not_found_handling` | Behavior |
|---|---|
| `"none"` (default) | No special handling; unmatched requests go to Worker or 404 |
| `"single-page-application"` | Returns `/index.html` with 200 for unmatched requests (for SPAs) |
| `"404-page"` | Returns nearest `404.html` with 404 status |

| `html_handling` | Behavior |
|---|---|
| `"auto-trailing-slash"` (default) | Smart: files without slash, directories with slash |
| `"force-trailing-slash"` | All URLs get trailing slash |
| `"drop-trailing-slash"` | All URLs lose trailing slash |
| `"none"` | No redirect/rewrite; must request exact file path |

### The Binding Anti-Pattern

Adding a `"binding": "ASSETS"` to your assets configuration creates a programmatic binding that routes requests through your Worker:

```python
# WRONG — the Worker is already awake by the time this runs
return await self.env.ASSETS.fetch(request)
```

This defeats the purpose. The Worker has already cold-started by the time it can call `ASSETS.fetch()`. Only add a binding if you need programmatic access for things like authentication checks, HTMLRewriter transformations, or A/B testing.

When you do need the binding:

```jsonc
{
  "assets": {
    "directory": "./assets/",
    "binding": "ASSETS",
    "run_worker_first": true
  }
}
```

```python
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        # Worker runs first — can add auth, transform, etc.
        if "/api/" in request.url:
            return await self.handle_api(request)
        return await self.env.ASSETS.fetch(request)
```

### Asset Organization and Limits

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

Limits: 20,000 assets (free plan) / 100,000 assets (paid plan), 25 MiB per file. Requests to static assets are **free and unlimited** — only Worker invocations are billed.

Use `.assetsignore` (same format as `.gitignore`) to exclude files from upload.

Static assets are cached at the edge automatically (24h default). For dynamic content you generate in Python, add cache headers explicitly:

```python
Response(html, headers={
    "Cache-Control": "public, max-age=3600, stale-while-revalidate=3600"
})
```

---

## Testing

### The Core Challenge

Python Worker code imports `js`, `pyodide.ffi`, and other modules that only exist inside the Workers runtime. Your tests run outside that runtime, in regular CPython with pytest. If your source code tries to `import js` during test collection, it will fail with `ImportError`.

This is a fundamentally different testing situation from JavaScript Workers (which have Miniflare/Vitest) or normal Python applications (which can import everything they test).

### Three-Tier Testing Strategy

| Tier | Speed | What it catches | Bindings |
|------|-------|-----------------|----------|
| **Unit** | ~1s | Logic, models, conversion | Python mocks |
| **Integration** | ~2s | HTTP flows, handler chains | Mock bindings (MockD1, MockQueue) |
| **E2E** | ~30s | JsProxy bugs, real behavior | Live Cloudflare (`wrangler dev --remote`) |

The critical insight: **mock-based tests will NOT catch JsProxy conversion bugs.** Your mocks return Python dicts, but production returns JsProxy objects. Your conversion code may have bugs that mocks never exercise. You need E2E tests against real Cloudflare infrastructure.

### The HAS_PYODIDE Guard Pattern

This pattern makes your code importable in both the Workers runtime and pytest:

```python
# src/wrappers.py
try:
    import js
    from js import fetch as js_fetch
    from pyodide.ffi import to_js, jsnull
    HAS_PYODIDE = True
except ImportError:
    js = None
    js_fetch = None
    to_js = None
    jsnull = None
    HAS_PYODIDE = False
```

Then in your conversion helpers:

```python
def _to_js_value(value):
    if not HAS_PYODIDE:
        return value  # In tests, pass through unchanged
    if isinstance(value, dict):
        return to_js(value, dict_converter=js.Object.fromEntries)
    return to_js(value)
```

When `HAS_PYODIDE` is `False` (in tests), the FFI functions are no-ops. Your business logic still runs. You just are not testing the actual FFI conversion — that is what E2E tests are for.

### Mock Bindings

Here are mock implementations for common bindings that you can use in unit and integration tests:

**Mock D1:**

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

**Mock Queue:**

```python
class MockQueue:
    def __init__(self):
        self.messages = []

    async def send(self, body, **kwargs):
        self.messages.append(body)
```

**Mock Workers AI:**

```python
class MockAI:
    async def run(self, model, inputs):
        if "bge-small" in model:
            texts = inputs.get("text", [])
            return type("R", (), {"data": [[0.1] * 768 for _ in texts]})()
        return type("R", (), {"output": "Mock response"})()
```

**Mock Vectorize:**

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

**Mock Environment (tying it all together):**

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

**Test Factories:**

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

### Testing Both Branches (Pyodide Fakes)

If your tests only run with `HAS_PYODIDE = False`, you are only testing the fallback path — not the production path. Every FFI production bug lives in the `if HAS_PYODIDE:` branches. To exercise those branches without the actual Pyodide runtime, create fake JS types:

```python
class FakeJsProxy:
    """Simulates pyodide.ffi.JsProxy in CPython tests."""
    def __init__(self, data):
        self._data = data
    def to_py(self):
        return self._data

class JsNull:
    """JS null sentinel — NOT Python None.

    Stand-in for `pyodide.ffi.jsnull` in CPython tests.
    """
    def __bool__(self):
        return False
JsNull.__name__ = "JsNull"
```

Then use a pytest fixture to activate the production path:

```python
@pytest.fixture
def pyodide_fakes(monkeypatch):
    monkeypatch.setattr(wrappers, "HAS_PYODIDE", True)
    monkeypatch.setattr(wrappers, "js", FakeJsModule())
    monkeypatch.setattr(wrappers, "to_js", fake_to_js)
    monkeypatch.setattr(wrappers, "jsnull", JsNull())
```

This lets you test that `_to_py_safe` correctly calls `.to_py()`, that `_is_missing` detects the right types, and that `_to_js_value` calls `to_js` with `dict_converter` — all without running inside Pyodide.

Maintain two test files per wrapper module:
- `test_wrappers.py` — CPython tests with `HAS_PYODIDE=False` (tests fallback/logic)
- `test_wrappers_ffi.py` — Monkeypatched `HAS_PYODIDE=True` with fakes (tests FFI conversion)

### Running Tests

```bash
uv run pytest tests/unit -x -q              # Fast unit tests
uv run pytest tests/integration -x -q        # Integration tests
uv run pytest tests/ --cov=src               # With coverage
```

For E2E tests against real infrastructure:

```bash
# Deploy to a test instance
npx wrangler deploy --config examples/test-planet/wrangler.jsonc

# Run E2E tests against it
uv run pytest tests/e2e/ -x -v
```

E2E tests catch things mocks cannot: JsProxy conversion bugs, real D1 SQL behavior (NULL vs undefined), Vectorize similarity scoring, Workers AI embedding dimensions, and queue message serialization round-trips.

pytest configuration (in `pyproject.toml`):

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

Makefile targets:

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

---

## Configuration

### wrangler.jsonc

Use `wrangler.jsonc` (JSON with comments) for all new projects. `wrangler.toml` is legacy — some newer Wrangler features are only available in JSON config files. Both `pywrangler init` and `npm create cloudflare` generate `wrangler.jsonc` by default.

Here is a complete Python-specific configuration:

```jsonc
{
  // IDE autocompletion for all wrangler config
  "$schema": "node_modules/wrangler/config-schema.json",

  "name": "my-python-worker",
  "main": "src/main.py",          // Points to .py file, not .js/.ts

  // Always use today's date when creating a new Worker
  "compatibility_date": "2026-03-17",
  "compatibility_flags": [
    "python_workers",              // REQUIRED — enables Python runtime
    "python_dedicated_snapshot"    // RECOMMENDED — faster cold starts
  ],

  // Python Workers burn more CPU than JS — increase from 30s default
  "limits": {
    "cpu_ms": 60000
  },

  // Static assets: served from edge WITHOUT waking the Python Worker
  "assets": {
    "directory": "./assets/"
  },

  // Cron triggers
  "triggers": {
    "crons": ["*/5 * * * *"]  // Every 5 minutes
  },

  // Some Pyodide packages include JS files that need bundling
  // Needed for packages like langchain_openai that bundle JS modules
  "rules": [
    { "type": "Data", "globs": ["python_modules/**/*.js"], "fallthrough": true }
  ]

  // ... all other bindings (D1, KV, Queues, etc.) are configured
  // identically to JS Workers — see Cloudflare docs
}
```

### Compatibility Flags

| Flag | Required | Purpose |
|------|----------|---------|
| `python_workers` | **Yes** | Enables the Pyodide runtime |
| `python_dedicated_snapshot` | Recommended | Worker-specific Wasm memory snapshot (~1s cold start vs ~10s) |
| `python_workflows` | For Workflows | Enables Python Workflow support |
| `experimental` | Sometimes | Required alongside `python_workflows` |
| `disable_python_no_global_handlers` | Legacy only | Re-enables deprecated `on_fetch`/`on_scheduled` function pattern |

### pyproject.toml and Package Management

```toml
[project]
name = "my-python-worker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    # Only packages deployed to Workers go here
    "feedparser>=6.0.0",
    "httpx>=0.27.0",         # HTTP client (AsyncClient or Client)
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

Generate environment types for IDE autocompletion:

```bash
uv run pywrangler types
```

**What packages work:**

- **Pure Python packages** from PyPI
- **Pyodide packages** (compiled to WebAssembly): numpy, pandas, pillow, etc.
- Full list: https://pyodide.org/en/stable/usage/packages-in-pyodide.html

**What does not work:**

- **Native C extensions** not in Pyodide: `psycopg2`, `lxml`, `cryptography`
- **OS-specific modules**: `multiprocessing`, `threading`, `socket` (raw)

**Alternatives:**

| Does Not Work | Use Instead |
|---------------|-------------|
| `lxml` | `xml.etree.ElementTree` (stdlib) |
| `psycopg2` | D1 binding (it's SQLite) |
| `cryptography` | `hashlib`, `hmac` (stdlib) |

A note on `lxml`: it is a C extension built on libxml2/libxslt, unavailable in Pyodide. This eliminates most Python HTML processing libraries that depend on it: Trafilatura, Newspaper4k, Goose3, ReadabiliPy, readability-lxml, jusText, and Inscriptis. Always check the **full dependency tree** for C extensions before choosing any library — a transitive dependency on lxml is enough to break your Worker.

**Confirmed working on Workers:** Pillow (PIL), LangChain (`langchain_core`, `langchain_openai` — needs `rules` config for JS bundling), Pygments, feedparser, httpx (`AsyncClient` and demonstrated sync `Client`), requests (demonstrated sync), urllib3 (demonstrated sync via `PoolManager`), Jinja2, bleach, beautifulsoup4 (without lxml backend).

Adding packages:

```bash
uv add httpx jinja2 bleach        # Runtime deps
uv add --group test pytest         # Test deps (not deployed)
```

### CPU Limits

Python Workers use significantly more CPU than JavaScript Workers for the same work. Pyodide interpretation overhead, plus Python libraries like feedparser, bleach, and JSON parsing are CPU-intensive.

| Plan | Default CPU | Max CPU |
|------|-------------|---------|
| Free | 10ms | 10ms |
| Paid | 30ms | 900,000ms (15 min) |

```jsonc
"limits": { "cpu_ms": 60000 }  // 60 seconds — often needed for Python
```

**CPU time** counts only computation. Awaiting I/O (fetch calls, D1 queries, KV reads) does NOT count. But parsing feeds, sanitizing HTML, and rendering templates does.

### Local Development

```bash
uv run pywrangler dev        # Local dev server at http://localhost:8787
```

Create a `.dev.vars` file in the project root for secrets during local development (NOT committed to git):

```
# .dev.vars — local development secrets
API_KEY=sk-your-key-here
SESSION_SECRET=local-dev-secret
```

These override `self.env` variables during local development. Add `.dev.vars` to `.gitignore`.

Vectorize has no local simulation — you must set `"remote": true` on Vectorize (and optionally AI) bindings during dev:

```jsonc
"vectorize": [{ "binding": "SEARCH_INDEX", "index_name": "...", "remote": true }],
"ai": { "binding": "AI", "remote": true }
```

### Deployment and Import Roots

```bash
uv run pywrangler deploy                                           # Deploy
npx wrangler deploy --config examples/my-instance/wrangler.jsonc   # Specific instance
```

When `main = "src/main.py"`, the `src/` directory is the import root:

```python
# src/models.py exists
from models import FeedRow    # CORRECT
from src.models import FeedRow  # WRONG — will fail
```

---

## Common Pitfalls

These are the 17 known Python-specific gotchas, presented as a narrative. Each one has caught real developers in real projects.

### 1. Using the legacy handler pattern

If you see `TypeError: on_fetch is not defined`, you are using the deprecated `@handler` + `on_fetch` pattern from before August 2025. Switch to the class-based `WorkerEntrypoint` pattern. The same applies to `on_scheduled` — use `async def scheduled(self, controller)` on the class instead.

### 2. Trying to import `to_py` as a function

`to_py` is a method on JsProxy objects, not a standalone function. `from pyodide.ffi import to_py` will fail with `ImportError`. Call `.to_py()` on the object instead. Note the asymmetry: `to_js` IS a standalone function.

### 3. Assuming `None` equals `null`

Python `None` maps to JavaScript `undefined`, not `null`. JavaScript `null` arrives in Python as the `jsnull` sentinel from `pyodide.ffi`, not `None`. JavaScript `undefined` arrives as `None`. This causes real bugs with D1, which needs `null` for SQL NULL. Use `from pyodide.ffi import jsnull` to pass a proper JavaScript null. And any code that checks `if x is None` at the FFI boundary must also check for `jsnull`. See the [None/null/undefined section](#the-none--null--undefined-problem) for the full details and helper functions.

### 4. Using `js.eval()`

Workers block `eval()` for security. You will see `EvalError: Code generation from strings disallowed for this context`. Use `js.JSON.parse()` instead. Also watch out for third-party libraries that call `js.eval()` under the hood — for example, `python-readability` loads Mozilla Readability via `js.eval()` and will fail. The workaround for JS-native functionality is a Service Binding to a separate JavaScript Worker.

### 5. Passing dicts to JavaScript without `dict_converter`

`to_js()` on a Python dict creates a JavaScript `Map`, not a plain `Object`. Most Cloudflare APIs only accept `Object` and will silently reject or mishandle `Map`. Always use `to_js(my_dict, dict_converter=Object.fromEntries)`. Write a helper function so you do not have to remember this every time.

### 6. Slow cold starts

First request taking 1-10+ seconds is expected behavior for Python Workers running Pyodide. The single biggest improvement is adding the `python_dedicated_snapshot` compatibility flag (drops ~10s to ~1s). Beyond that: use Workers Static Assets without a binding (so CSS/JS never wake the Worker), pre-compile Jinja2 templates, use `stale-while-revalidate` cache headers, and consider cron-based cache pre-warming.

### 7. Calling PRNG functions at module level

Any call to `random.seed()`, `secrets.token_hex()`, `uuid.uuid4()`, or `os.urandom()` at module level will cause deployment to fail. WebAssembly memory snapshots assert that PRNG state is unchanged after snapshotting. Move all entropy-generating calls inside handler methods.

### 8. Treating D1 results as Python dicts

D1 query results are JsProxy objects. If you iterate over `results.results` directly, individual rows may behave like dicts in some ways but fail in others. Always convert at the boundary: `[dict(r) for r in results.results.to_py()]`.

### 9. Accessing queue message bodies without conversion

`msg.body` is a JsProxy. `msg.body["feed_id"]` may fail with TypeError. Always call `msg.body.to_py()` first and work with the resulting Python dict.

### 10. Running out of CPU time

Python Workers consume significantly more CPU than JavaScript Workers for the same work. If your Worker is being killed mid-execution, increase `cpu_ms` in your `wrangler.jsonc`. The default 30ms is almost never enough for Python Workers doing real work. 60 seconds is a common starting point.

### 11. Hitting standard library limitations

Some stdlib modules import but do not work: `multiprocessing` (no process spawning in Wasm), `threading` (no threads in Wasm), `socket` (raw sockets blocked). Some cannot import at all: `pty`, `tty` (depend on removed `termios`). Some are limited: `decimal` (C implementation only). Many OS-specific modules are excluded entirely: `curses`, `dbm`, `ensurepip`, `fcntl`, `grp`, `idlelib`, `lib2to3`, `msvcrt`, `pwd`, `resource`, `syslog`, `termios`, `tkinter`, `venv`, `winreg`, `winsound`.

### 12. Using native/compiled packages

Only pure Python packages and packages pre-compiled for Pyodide work. Native C extensions cannot be compiled to WebAssembly automatically. Check https://pyodide.org/en/stable/usage/packages-in-pyodide.html before choosing any library. Pay special attention to transitive dependencies — a library that is itself pure Python may depend on `lxml` or `cryptography` deep in its dependency tree, which will break your Worker.

### 13. Tests passing but production failing

This is the most insidious gotcha. Your mocks return Python dicts, but production returns JsProxy objects. Your test suite is green, and your production Worker crashes with `TypeError` or `AttributeError`. The fix is E2E tests against real Cloudflare infrastructure. Deploy to a test instance and run your test suite against it. E2E tests catch JsProxy conversion bugs, real D1 NULL behavior, Vectorize scoring, AI embedding dimensions, and queue serialization round-trips.

### 14. Passing Python `bytes` to binary APIs

Python `bytes` crosses the FFI as a `PyProxy`, not a `Uint8Array`. R2's `.put()`, KV's `.put()`, and WebSocket's `.send()` with binary frames will reject it. Use `to_js(my_bytes)` to convert to a proper `Uint8Array`.

### 15. Cold start canceling the first queue message

When a queue message hits a cold Python Worker isolate, the Workers runtime cancels the first invocation — zero logs, zero exceptions. The automatic retry succeeds because the isolate is now warm. Queue messages may appear to take 30-60s longer than expected. This is inherent platform behavior due to Pyodide's cold start exceeding the queue consumer timeout. Do not chase it.

### 16. Miniflare queue consumer not working locally

Queue messages may never be delivered when running locally with `wrangler dev`. Miniflare's local queue consumer does not work reliably with Python Workers. The workaround is building a synchronous `POST /process-now` endpoint that runs your pipeline inline:

```python
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if request.method == "POST" and parsed.path == "/process-now":
            await self.run_pipeline()  # Same logic as queue handler
            return Response("OK")
```

Verify actual queue behavior on real Cloudflare infrastructure, not locally.

### 17. Large binary data crashing the Worker

The round-trip for large R2 objects (over ~10MB) — JavaScript ReadableStream to Python bytes to JavaScript Response body — doubles memory in WebAssembly linear memory and can crash the Worker. For large binary serving, bypass Python entirely by passing the ReadableStream directly to a JavaScript Response constructor. For small payloads (under ~2MB), reading into Python bytes is fine.

---

## Anti-Patterns

These are the patterns that will cause you the most pain if you get them wrong. Each one shows the wrong way and the right way.

### Letting JsProxy leak into business logic

```python
# NEVER — JsProxy leaks into app code, breaks serialization/iteration
results = await env.DB.prepare("SELECT * FROM feeds").all()
return results.results  # JsProxy!

# ALWAYS — convert at the boundary
results = await env.DB.prepare("SELECT * FROM feeds").all()
rows = [dict(r) for r in results.results.to_py()]  # Native Python
```

### Converting dicts without `dict_converter`

```python
# NEVER — creates JS Map, most Cloudflare APIs reject it
js_obj = to_js({"topK": 50})

# ALWAYS — creates JS Object
js_obj = to_js({"topK": 50}, dict_converter=Object.fromEntries)
```

### Seeding PRNG at module level

```python
# NEVER — breaks Wasm snapshot, deployment FAILS
import random
random.seed(42)

# ALWAYS — inside handlers
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        random.seed(42)  # OK
```

### Routing static assets through the Worker

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

### Passing Python bytes to binary APIs

```python
# NEVER — PyProxy, R2/KV will reject it
await env.BUCKET.put("key", my_bytes)

# ALWAYS — convert to Uint8Array
await env.BUCKET.put("key", to_js(my_bytes))
```

---

## Additional Patterns

### Per-Isolate Initialization

A `WorkerEntrypoint` instance persists across requests within the same V8 isolate. Class attributes are the place for per-isolate state — things you want to compute once and reuse for subsequent requests without paying the cost again.

In JavaScript Workers, you would use a module-level variable. In Python Workers, module-level code runs at snapshot time (deploy), but the `WorkerEntrypoint` instance is created per isolate at request time. So per-isolate state lives on `self`, not in module scope.

```python
class Default(WorkerEntrypoint):
    _db_initialized: bool = False          # Persists across requests in this isolate

    async def _ensure_database_initialized(self):
        if self._db_initialized:
            return                          # 2nd+ request — skip entirely

        result = await self.env.DB.prepare(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='feeds'"
        ).first()

        if result is None:
            await self.env.DB.exec("CREATE TABLE IF NOT EXISTS feeds (...)")

        self._db_initialized = True         # Even on error — don't retry every request
```

Other good uses: cached route dispatchers with compiled regexes, cached SafeEnv wrappers, and Jinja2 Environment objects. Not good for caching: request-specific data, user sessions, query results, or mutable shared state (there is no locking in Workers).

### Cold Start Mitigation

A summary of all the cold-start strategies in one place:

1. **`python_dedicated_snapshot`** — worker-specific Wasm snapshot (biggest win, ~10s down to ~1s)
2. **Static Assets without binding** — CSS/JS/images never wake the Worker
3. **Pre-compiled templates** — Jinja2 parsing in snapshot, not at request time
4. **Per-isolate initialization** — compute expensive objects once, reuse across requests
5. **Module-level constants** — captured in snapshot (but NO PRNG calls)
6. **`stale-while-revalidate` cache** — serve stale content instantly while refreshing in the background
7. **Cron cache pre-warming** — scheduled handler fetches your own routes to keep the cache hot
8. **Minimize top-level imports** — lazy import heavy modules inside handlers

The cron pre-warming pattern:

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

### Jinja2 Without a Filesystem

Workers have no writable filesystem. Jinja2's `FileSystemLoader` works for bundled files (using `Path(__file__)`), but for best cold-start performance, pre-compile templates into a Python module at build time:

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

The simpler approach (slower cold start):

```python
from pathlib import Path
import jinja2

templates_dir = Path(__file__).parent.parent / "templates"
env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(templates_dir)))

template = env.get_template("home.html")
html = template.render(planet_name="My Planet")
```

### Reading Bundled Files

Workers have no filesystem at runtime in the traditional sense, but `Path(__file__)` resolves to the bundled source directory:

```python
from pathlib import Path

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        html = Path(__file__).parent / "index.html"
        return Response(html.read_text(), headers={"Content-Type": "text/html"})
```

This reads files that were bundled at deploy time. You cannot write files or read files created at runtime.

### Logging

All three approaches work and appear in Workers logs:

```python
# print() — simplest, appears in wrangler tail and dashboard
print("Hello from Python Worker!")

# Python logging module — works normally
import logging
logger = logging.getLogger(__name__)
logger.info("Processing request")

# JS console via FFI
from js import console
console.log("From JS console")
```

### Outbound HTTP

`workers.fetch()` wraps the JavaScript `fetch()` API directly. It is always available without installing any package:

```python
from workers import fetch

response = await fetch("https://example.com/api")
data = await response.json()  # JsProxy — call .to_py()
```

For higher-level Python clients, both async and a subset of sync clients work in the current runtime. The Cloudflare examples include working synchronous calls with `requests`, `urllib3`, and `httpx.Client`.

```python
# Async: best for fan-out / overlapping multiple requests
import httpx
async with httpx.AsyncClient() as client:
    response = await client.get("https://example.com/api")

# Sync: works for simple one-off calls, but blocks this handler while in flight
import requests
response = requests.get("https://example.com/api", timeout=10)
```

Use `workers.fetch()` or async clients when you need concurrency, streaming-style integration with Workers APIs, or the lowest dependency overhead. Sync clients are convenient for porting existing code, but they execute sequentially and hold the Python handler until the response completes.

Known demonstrated sync clients: `requests`, `urllib3.PoolManager`, and `httpx.Client`. Do not assume every raw-socket or stdlib HTTP path works; `urllib.request`, `http.client`, and arbitrary `socket` usage are not covered by the examples.

---

## Official Resources

- [Python Workers Docs](https://developers.cloudflare.com/workers/languages/python/)
- [How Python Workers Work](https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/)
- [Python FFI](https://developers.cloudflare.com/workers/languages/python/ffi/)
- [Python Packages](https://developers.cloudflare.com/workers/languages/python/packages/)
- [Python Workers Examples](https://github.com/cloudflare/python-workers-examples)
- [pywrangler CLI](https://github.com/cloudflare/workers-py)
- [Pyodide Package List](https://pyodide.org/en/stable/usage/packages-in-pyodide.html)
- [Request new packages](https://github.com/cloudflare/workerd/discussions/categories/python-packages)
