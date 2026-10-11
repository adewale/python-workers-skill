"""Probe Worker: reports how the real runtime behaves for claims the skill makes.

Each key is one observation. scripts/check_runtime_claims.py starts this Worker
under `pywrangler dev` (workerd + local D1) and compares the observations with
the expected values it records next to the skill section that makes the claim.
"""
import json

import js
from pyodide.ffi import JsProxy, jsnull, to_js
from workers import Response, WorkerEntrypoint


def type_name(value):
    return f"{type(value).__module__}.{type(value).__name__}"


def attempt(fn):
    """Run fn and report its value, or the exception the runtime raised."""
    try:
        return {"ok": True, "value": fn()}
    except Exception as exc:  # noqa: BLE001 -- the exception type is the observation
        return {"ok": False, "error": type(exc).__name__}


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        db = self.env.DB
        await db.prepare("DROP TABLE IF EXISTS feeds").run()
        await db.prepare("CREATE TABLE feeds (id INTEGER PRIMARY KEY, name TEXT, etag TEXT)").run()
        await db.prepare("INSERT INTO feeds (id, name, etag) VALUES (1, 'planet', 'v1')").run()

        results = await db.prepare("SELECT id, name FROM feeds").all()
        rows = results.results

        # Python None bound as a D1 parameter (gotchas.md #3 calls this WRONG)
        await db.prepare("UPDATE feeds SET etag = ?").bind(None).run()
        etag_after_none = (await db.prepare("SELECT etag FROM feeds").first())["etag"]
        await db.prepare("UPDATE feeds SET etag = 'v2'").run()
        await db.prepare("UPDATE feeds SET etag = ?").bind(jsnull).run()
        etag_after_jsnull = (await db.prepare("SELECT etag FROM feeds").first())["etag"]

        observed = {
            # D1 (gotchas.md #8, patterns.md "D1 Row Conversion", api.md)
            "d1_results_type": type_name(rows),
            "d1_results_is_jsproxy": isinstance(rows, JsProxy),
            "d1_row_subscript": attempt(lambda: [{"id": r["id"], "name": r["name"]} for r in rows]),
            "d1_results_to_py": attempt(lambda: rows.to_py()),
            "d1_bind_none_reads_back_none": etag_after_none is None,
            "d1_bind_jsnull_reads_back_none": etag_after_jsnull is None,
            # FFI (gotchas.md #3, #5; api.md FFI)
            "js_undefined_is_none": js.undefined is None,
            "jsnull_is_none": jsnull is None,
            "jsnull_is_falsy": not jsnull,
            "to_js_dict_constructor": str(to_js({"topK": 50}).constructor.name),
            "to_js_dict_fromentries_constructor": str(
                to_js({"topK": 50}, dict_converter=js.Object.fromEntries).constructor.name
            ),
            # gotchas.md #4
            "js_eval": attempt(lambda: js.eval("1 + 1")),
        }
        return Response(json.dumps(observed), headers={"content-type": "application/json"})
