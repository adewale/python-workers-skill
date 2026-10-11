#!/usr/bin/env python3
"""Run the probe Worker in tests/runtime/ under real workerd and check the skill's runtime claims.

The skill's advice about the JS/Python boundary (JsProxy, None vs null, dict -> Map,
D1 results) is only true if the runtime behaves that way. This script starts the probe
Worker with `pywrangler dev` (workerd with a local D1; nothing is deployed and no
Cloudflare account is used), fetches its observations, and compares each one with the
value recorded in EXPECTED below.

EXPECTED comes from the real runtime (wrangler 4.147.0, workers-runtime-sdk 1.9.2,
compatibility_date 2025-12-01), not from the skill text. Each entry names the skill
section that makes the claim and says whether the runtime confirms or contradicts it.
A failure means the runtime changed: update the skill section first, then EXPECTED.

Needs uv >= 0.12.3, Node.js and network access (npx downloads wrangler).
Exit codes: 0 every observation matches, 1 a mismatch, 2 the probe did not start.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE_DIR = ROOT / "tests" / "runtime"
STARTUP_TIMEOUT_S = 300

# observation key -> (expected value, skill section, verdict on the skill's claim)
EXPECTED: dict[str, tuple[object, str, str]] = {
    "d1_results_type": ("builtins.list", "gotchas.md #8 says JsProxy", "contradicts"),
    "d1_results_is_jsproxy": (False, "gotchas.md #8", "contradicts"),
    "d1_row_subscript": (
        {"ok": True, "value": [{"id": 1, "name": "planet"}]},
        "eval 2 / gotchas.md #8 say row['id'] raises TypeError",
        "contradicts",
    ),
    "d1_results_to_py": (
        {"ok": False, "error": "AttributeError"},
        "gotchas.md #8, patterns.md D1 Row Conversion, api.md, SKILL.md say call .to_py()",
        "contradicts",
    ),
    "d1_bind_none_reads_back_none": (True, "gotchas.md #3 says bind(None) is WRONG for SQL NULL", "contradicts"),
    "d1_bind_jsnull_reads_back_none": (True, "gotchas.md #3 says bind(jsnull) for SQL NULL", "confirms"),
    "js_undefined_is_none": (True, "gotchas.md #3: JS undefined arrives as None", "confirms"),
    "jsnull_is_none": (False, "gotchas.md #3: jsnull is not None", "confirms"),
    "jsnull_is_falsy": (True, "gotchas.md #3: bool(jsnull) is False", "confirms"),
    "to_js_dict_constructor": ("LiteralMap", "gotchas.md #5: a dict becomes a Map", "confirms"),
    "to_js_dict_fromentries_constructor": ("Object", "gotchas.md #5: dict_converter=Object.fromEntries", "confirms"),
    "js_eval": ({"ok": False, "error": "JsException"}, "gotchas.md #4: js.eval() is disallowed", "confirms"),
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fetch_observations(port: int, server: subprocess.Popen) -> dict | None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if server.poll() is not None:
            return None
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # The Worker started and raised: report it as an observation, do not retry
            return {"http_error": exc.code}
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(2)
    return None


def main() -> int:
    if shutil.which("uv") is None:
        print("UNAVAILABLE: uv is not installed", file=sys.stderr)
        return 2
    port = free_port()
    log_path = PROBE_DIR / ".wrangler-dev.log"
    with log_path.open("w") as log:
        server = subprocess.Popen(
            ["uv", "run", "pywrangler", "dev", "--ip", "127.0.0.1", "--port", str(port)],
            cwd=PROBE_DIR,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            observed = fetch_observations(port, server)
        finally:
            os.killpg(server.pid, signal.SIGTERM)
            server.wait(timeout=30)
    if observed is None:
        print(f"UNAVAILABLE: probe Worker did not answer; see {log_path.relative_to(ROOT)}", file=sys.stderr)
        print(log_path.read_text()[-3000:], file=sys.stderr)
        return 2

    errors = []
    for key in sorted(EXPECTED.keys() | observed.keys()):
        if key not in EXPECTED:
            errors.append(f"{key}: observed {observed[key]!r} but nothing is expected; add it to EXPECTED")
            continue
        expected, section, verdict = EXPECTED[key]
        actual = observed.get(key, "<missing>")
        if actual != expected:
            errors.append(f"{key}: expected {expected!r}, observed {actual!r} ({section})")
        else:
            print(f"ok   {key} = {actual!r}  [{verdict}: {section}]")
    if errors:
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        return 1
    contradicted = sum(1 for _, _, verdict in EXPECTED.values() if verdict == "contradicts")
    print(f"OK: {len(EXPECTED)} runtime observations match ({contradicted} contradict the skill text)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
