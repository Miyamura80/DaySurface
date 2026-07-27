#!/usr/bin/env python3
"""Scenario-driven OpenAI-compatible mock LLM for Goose e2e tests (DaySurface).

Ported from Edison-Watch's harness and simplified: this template's /mcp mount has
no session-token handshake, so the mock just follows a declared tool-call plan.

Reads $SCENARIO_FILE fresh on every request (so the persistent mock picks up the
current scenario without a restart) and drives Goose through the plan. Key
guarantees that keep tests honest:
  - follows the plan deterministically (position = number of tool results so far)
  - NEVER inspects/masks tool errors to fake success. The verdict is decided by
    the oracle (mcp_probe.py) from the rendered iframe + the tool-call log, not by
    anything this mock narrates.
  - appends every emitted call and every observed tool result to $TOOLCALL_LOG so
    the oracle has an independent wire record of the round-trip.

Scenario file shape:
  { "plan": [ {"match": "webhook_settings"} ], "final": "Opened your settings." }
`match` is a substring matched against the tool names Goose actually offers (Goose
may namespace extension tools, so substring - not exact - matching is used).
"""

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

E2E_HOME = os.environ.get("E2E_HOME", os.path.expanduser("~/goose-e2e"))
SCENARIO_FILE = os.environ.get("SCENARIO_FILE") or os.path.join(
    E2E_HOME, "current_scenario.json"
)
TOOLCALL_LOG = os.environ.get("TOOLCALL_LOG") or os.path.join(
    E2E_HOME, "toolcalls.jsonl"
)


def load_scenario():
    try:
        with open(SCENARIO_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"plan": [], "final": "No scenario loaded."}


def log_event(event: dict):
    try:
        with open(TOOLCALL_LOG, "a") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        pass


def offered(tools, needle):
    for t in tools:
        n = t.get("function", {}).get("name", "")
        if needle in n:
            return n
    return None


def tool_results(msgs):
    out = []
    for m in msgs:
        if m.get("role") == "tool":
            c = m.get("content", "")
            if isinstance(c, list):
                c = " ".join(
                    x.get("text", "") if isinstance(x, dict) else str(x) for x in c
                )
            out.append(c or "")
    return out


def sse(o):
    return f"data: {json.dumps(o)}\n\n".encode()


def chunk(cid, model, choice, usage=None):
    """One OpenAI streaming chunk envelope, SSE-encoded. usage rides only on the
    final chunk of a stream (it's omitted elsewhere), so it's an opt-in kwarg."""
    o = {
        "id": cid,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [choice],
    }
    if usage:
        o["usage"] = usage
    return sse(o)


class H(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass

    def _json(self, code, o):
        b = json.dumps(o).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._json(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": "gpt-4o-mini", "object": "model", "owned_by": "mock"}
                    ],
                },
            )
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        model = body.get("model", "gpt-4o-mini")
        stream = bool(body.get("stream"))
        msgs = body.get("messages", [])
        tools = body.get("tools", []) or []
        sc = load_scenario()
        plan = sc.get("plan", [])
        results = tool_results(msgs)
        pos = len(results)

        # An independent wire record: the results the server actually returned for
        # our prior calls. The oracle uses this to prove a real round-trip.
        if results:
            log_event({"event": "result", "count": len(results)})

        decision = None
        if tools and pos < len(plan):
            step = plan[pos]
            name = offered(tools, step["match"])
            if name:
                decision = (name, dict(step.get("args", {})))

        cid = "chatcmpl-mock"
        usage = {"prompt_tokens": 90, "completion_tokens": 70, "total_tokens": 160}
        if decision:
            nm, args = decision
            log_event({"event": "call", "tool": nm, "args": args})
            call_id = "call_" + re.sub(r"\W", "", nm)[-20:]
            argstr = json.dumps(args)
            if stream:
                self._sse()
                self.wfile.write(
                    chunk(
                        cid,
                        model,
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": call_id,
                                        "type": "function",
                                        "function": {"name": nm, "arguments": ""},
                                    }
                                ],
                            },
                        },
                    )
                )
                self.wfile.write(
                    chunk(
                        cid,
                        model,
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": argstr}}
                                ]
                            },
                        },
                    )
                )
                self.wfile.write(
                    chunk(
                        cid,
                        model,
                        {"index": 0, "delta": {}, "finish_reason": "tool_calls"},
                        usage,
                    )
                )
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            else:
                self._json(
                    200,
                    {
                        "id": cid,
                        "object": "chat.completion",
                        "model": model,
                        "usage": usage,
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "tool_calls",
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": call_id,
                                            "type": "function",
                                            "function": {
                                                "name": nm,
                                                "arguments": argstr,
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                )
            return

        reply = sc.get("final", "Done.") if tools else "Chat"
        if stream:
            self._sse()
            self.wfile.write(
                chunk(cid, model, {"index": 0, "delta": {"role": "assistant"}})
            )
            for w in reply.split(" "):
                self.wfile.write(
                    chunk(cid, model, {"index": 0, "delta": {"content": w + " "}})
                )
            self.wfile.write(
                chunk(
                    cid,
                    model,
                    {"index": 0, "delta": {}, "finish_reason": "stop"},
                    usage,
                )
            )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            self._json(
                200,
                {
                    "id": cid,
                    "object": "chat.completion",
                    "model": model,
                    "usage": usage,
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": reply},
                        }
                    ],
                },
            )


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_PORT", "8410"))
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
