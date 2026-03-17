# Python Workers — API Reference

Python-specific APIs: handler signatures, FFI functions, Response wrapper, and how binding access differs from JavaScript.

For the bindings themselves (D1 SQL syntax, KV methods, Queue config), see the [Cloudflare Workers docs](https://developers.cloudflare.com/workers/runtime-apis/bindings/). This file covers what changes when calling them from Python.

---

## Table of Contents

- [Handlers (Python Class Pattern)](#handlers)
- [Response (Python Wrapper)](#response)
- [FFI — JavaScript/Python Boundary](#ffi)
- [Binding Access From Python](#binding-access-from-python)
- [Logging](#logging)
- [Reading Bundled Files](#reading-bundled-files)

---

## Handlers

Python Workers use a **class-based pattern** (not the `export default` of JS Workers):

### fetch — HTTP Requests

```python
from workers import WorkerEntrypoint, Response

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        # request is a JavaScript Request object via FFI (not a Python object)
        url = request.url           # str (auto-converted immutable)
        method = request.method     # str

        # Headers — JS Headers object, use .get()
        auth = request.headers.get("Authorization")

        # JSON body — returns JsProxy, must convert
        if method == "POST":
            body = await request.json()    # JsProxy!
            python_dict = body.to_py()     # Now a Python dict

        # URL parsing — use Python stdlib, not JS URL
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(request.url)
        path = parsed.path
        params = parse_qs(parsed.query)

        return Response("OK")
```

**Key difference from JS**: `request.json()` returns a `JsProxy`, not a Python dict. You must call `.to_py()`.

### scheduled — Cron Triggers

```python
class Default(WorkerEntrypoint):
    async def scheduled(self, controller):
        # controller.scheduledTime — when the cron fired
        # controller.cron — the cron pattern that matched
        print(f"Cron triggered: {controller.cron}")
        await self.env.FEED_QUEUE.send({"action": "refresh"})
```

**Key difference from JS**: This is a method on the class, not a separate `export`. The old `@handler` + `on_scheduled` function pattern was deprecated August 2025 (see gotchas.md #1).

### queue — Queue Message Batches

```python
class Default(WorkerEntrypoint):
    async def queue(self, batch):
        for message in batch.messages:
            body = message.body.to_py()  # JsProxy → Python dict
            await self.process(body)
            message.ack()
```

**Key difference from JS**: `message.body` is a `JsProxy`. Always `.to_py()` before accessing fields.

### All Three in One Class

```python
class Default(WorkerEntrypoint):
    async def fetch(self, request): ...
    async def scheduled(self, controller): ...
    async def queue(self, batch): ...
```

No separate exports. One class, three methods.

---

## Response

The `workers.Response` is a Python-friendly wrapper around JS Response. Behaves like the JS version but accepts Python types:

```python
from workers import Response

# Text
Response("Hello World!")
Response("Not Found", status=404)
Response("OK", headers={"Content-Type": "text/plain"})

# JSON (serializes Python dict automatically)
Response.json({"key": "value"})

# Binary
Response(image_bytes, headers={"Content-Type": "image/png"})

# Redirect
Response(None, status=302, headers={"Location": "/new-path"})

# WebSocket upgrade
Response(None, status=101, web_socket=client_ws)
```

**When using `js.Response` directly** (not the `workers` wrapper), you must call `.new()`:

```python
from js import Response as JsResponse
return JsResponse.new("Hello!")  # .new() required for JS constructors via FFI
```

---

## FFI

The core Python-specific API. This is what makes Python Workers fundamentally different from JS Workers.

### import js — Access JavaScript Globals

```python
from js import fetch, console, Response, Object, JSON, URL, Headers
from js import HTMLRewriter, WebSocket, WebSocketPair
```

Anything available as a JS global can be imported from `js`.

### to_js — Python to JavaScript

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

### .to_py() — JavaScript to Python

**`to_py()` is a METHOD on JsProxy objects, NOT a standalone function.**

```python
# WRONG — ImportError!
from pyodide.ffi import to_py

# CORRECT — call on the JsProxy object
data = await request.json()          # JsProxy
python_dict = data.to_py()           # Python dict

results = await env.DB.prepare("...").all()
rows = results.results.to_py()       # Python list of dicts
```

### create_proxy — Python Callables for JS APIs

Required when passing Python functions to JS APIs that **retain references** (addEventListener, HTMLRewriter.on):

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

### JS null Creation

Python `None` maps to JS `undefined`, NOT `null`. For APIs that need `null` (D1 SQL NULL):

```python
import js
JS_NULL = js.JSON.parse("null")  # js.eval("null") is disallowed in Workers
```

### Checking for JS undefined

```python
def _is_js_undefined(value):
    """JS undefined wrapped as JsProxy looks like a value but isn't."""
    if value is None:
        return False
    return (str(type(value)) == "<class 'pyodide.ffi.JsProxy'>"
            and str(value) == "undefined")
```

---

## Binding Access From Python

All bindings are accessed via `self.env` — same names, same methods as JS. The difference is that **return values are JsProxy objects** that must be converted.

```python
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        # Environment variables — auto-converted (strings)
        name = self.env.PLANET_NAME

        # Secrets — same as env vars
        secret = self.env.SESSION_SECRET

        # D1 — results are JsProxy
        results = await self.env.DB.prepare("SELECT * FROM feeds").all()
        rows = results.results.to_py()  # Convert!

        # KV — string values auto-convert, JSON needs .to_py()
        value = await self.env.MY_KV.get("key")

        # Queues — must to_js() the payload dict
        await self.env.FEED_QUEUE.send(
            to_js({"feed_id": 1}, dict_converter=Object.fromEntries)
        )

        # Vectorize — both input and output need conversion
        js_vector = to_js(query_vector)
        js_options = to_js({"topK": 50}, dict_converter=Object.fromEntries)
        matches = await self.env.SEARCH_INDEX.query(js_vector, js_options)

        # Workers AI — input dict needs to_js, output needs .to_py()
        response = await self.env.AI.run("@cf/baai/bge-small-en-v1.5",
            {"text": ["hello"]})
        embeddings = [list(e) for e in response.data.to_py()]
```

### Service Bindings — Python RPC Methods

Python Workers can expose custom methods beyond `fetch`, callable from other Workers:

```python
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response("RPC server")

    async def highlight_code(self, code: str, language: str = None) -> dict:
        """Custom RPC method — callable via Service Binding."""
        return {"html": highlighted, "language": language}
```

---

## Logging

```python
# print() — appears in wrangler tail and dashboard
print("Hello from Python Worker!")

# Python logging module — works normally
import logging
logger = logging.getLogger(__name__)
logger.info("Processing request")

# JS console via FFI
from js import console
console.log("From JS console")
```

All three appear in Workers logs. `print()` is simplest.

---

## Reading Bundled Files

Workers have **no filesystem at runtime** in the traditional sense, but `Path(__file__)` resolves to the bundled source directory:

```python
from pathlib import Path

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        html = Path(__file__).parent / "index.html"
        return Response(html.read_text(), headers={"Content-Type": "text/html"})
```

**Caveat**: This reads files bundled at deploy time. You cannot write files or read files created at runtime. For templates, see patterns.md (Jinja2 SSR).
