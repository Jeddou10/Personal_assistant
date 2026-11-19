"""Credential-free protocol, scope, resource-bound and real subprocess tests."""

import copy
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

from assistant_app.mcp import McpServer, create_server

DEFINITIONS = [
    {
        "name": "search_mail",
        "description": "Search mail",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {"name": "read_notion", "description": "Read selected page", "input_schema": {"type": "object"}},
]


def request(method, params=None, request_id=1):
    value = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        value["params"] = params
    return value


def handshake(version="2025-11-25"):
    return [
        request(
            "initialize",
            {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        ),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]


def call(args=None, name="search_mail", request_id=2):
    return request("tools/call", {"name": name, "arguments": {"query": "hello"} if args is None else args}, request_id)


def frames(messages):
    return b"".join(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n" for message in messages)


class McpTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.now = 0.0
        self.server = McpServer(self.dispatch, DEFINITIONS, clock=lambda: self.now)

    def dispatch(self, name, args):
        self.calls.append((name, args))
        return {"text": "Grüße\n世界"}

    def run_server(self, messages=None, raw=None, server=None):
        output = io.BytesIO()
        (server or self.server).serve(io.BytesIO(raw if raw is not None else frames(messages)), output)
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_handshake_negotiates_and_lists_only_tools(self):
        result = self.run_server(handshake("future") + [request("tools/list"), call()])
        self.assertEqual(result[0]["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(result[0]["result"]["capabilities"], {"tools": {}})
        self.assertEqual([tool["name"] for tool in result[1]["result"]["tools"]], ["search_mail", "read_notion"])
        self.assertIn("inputSchema", result[1]["result"]["tools"][0])
        self.assertEqual(json.loads(result[2]["result"]["content"][0]["text"]), {"text": "Grüße\n世界"})
        self.assertEqual(len(self.calls), 1)

    def test_lifecycle_requires_initialized_and_rejects_reinitialize(self):
        init, initialized = handshake()
        result = self.run_server([request("ping"), call(), init, call(), initialized, call(), init])
        self.assertEqual(result[0]["result"], {})
        self.assertEqual(result[1]["error"]["code"], -32600)
        self.assertEqual(result[3]["error"]["code"], -32600)
        self.assertFalse(result[4]["result"]["isError"])
        self.assertEqual(result[5]["error"]["code"], -32600)

    def test_invalid_initialize_does_not_advance_state(self):
        result = self.run_server([request("initialize", {})] + handshake() + [request("ping")])
        self.assertEqual(result[0]["error"]["code"], -32602)
        self.assertIn("result", result[1])

    def test_notifications_never_reply_or_dispatch(self):
        notifications = [
            {"jsonrpc": "2.0", "method": name, "params": params}
            for name in ("tools/call", "initialize", "unknown", "notifications/initialized")
            for params in ({"name": "search_mail", "arguments": {"query": "x"}}, [], None)
        ]
        result = self.run_server(handshake() + notifications + [request("ping")])
        self.assertEqual(len(result), 2)
        self.assertEqual(self.calls, [])

    def test_protocol_errors(self):
        invalid = [
            [],
            {},
            {"jsonrpc": "2.0", "id": True, "method": "ping"},
            request("ping", []),
            request("ping", request_id=None),
            {"jsonrpc": "2.0", "id": 1, "method": "ping", "result": {}},
            request("ping", request_id="x" * 129),
        ]
        invalid.append(request("ping", request_id="\ud800"))
        result = self.run_server(
            raw=frames(handshake())
            + b"".join(json.dumps(message).encode("utf-8") + b"\n" for message in invalid)
            + frames([request("roots/list"), call(name="approve")])
        )
        self.assertTrue(all(item["error"]["code"] == -32600 for item in result[1:-2]))
        self.assertEqual(result[-2]["error"]["code"], -32601)
        self.assertEqual(result[-1]["error"]["code"], -32602)

    def test_bad_tool_arguments_are_tool_errors(self):
        result = self.run_server(
            handshake() + [call(args=value) for value in ({}, {"query": 1}, {"query": "x", "root": "/"})]
        )
        self.assertTrue(all(item["result"]["isError"] for item in result[1:]))
        self.assertFalse(self.calls)

    def test_nonobject_arguments_are_protocol_errors(self):
        messages = [request("tools/call", {"name": "search_mail", "arguments": value}) for value in ([], "x", None)]
        result = self.run_server(handshake() + messages)
        self.assertTrue(all(item["error"]["code"] == -32602 for item in result[1:]))
        self.assertFalse(self.calls)

    def test_runtime_errors_and_non_json_results_are_redacted(self):
        def fail(name, args):
            raise RuntimeError("PRIVATE TOKEN")

        for dispatcher in (
            fail,
            lambda *_: {"x": float("nan")},
            lambda *_: object(),
            lambda *_: {"x": "PRIVATE" * McpServer.MAX_OUTPUT},
        ):
            with self.subTest(dispatcher=dispatcher):
                result = self.run_server(handshake() + [call()], server=McpServer(dispatcher, DEFINITIONS))
                self.assertTrue(result[-1]["result"]["isError"])
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_framing_parse_errors_and_recovery(self):
        raw = b'\xff\n{\n{"jsonrpc":"2.0","id":1,"id":2}\nNaN\n' + frames(handshake() + [request("ping")])
        result = self.run_server(raw=raw)
        self.assertTrue(all(item["error"]["code"] == -32700 for item in result[:4]))
        self.assertEqual(result[-1]["result"], {})

    def test_input_and_aggregate_bounds(self):
        self.assertEqual(self.run_server(raw=b"x" * (self.server.MAX_INPUT + 1) + b"\n"), [])
        server = McpServer(self.dispatch, DEFINITIONS)
        server.MAX_MESSAGES = 2
        self.assertEqual(len(self.run_server([request("ping")] * 5, server=server)), 2)
        server = McpServer(self.dispatch, DEFINITIONS)
        server.MAX_INPUT_TOTAL = 1
        self.assertEqual(self.run_server([request("ping")], server=server), [])
        server = McpServer(self.dispatch, DEFINITIONS)
        server.MAX_OUTPUT_TOTAL = 1
        self.assertEqual(self.run_server([request("ping")], server=server), [])

    def test_unterminated_frame_and_single_session(self):
        result = self.run_server(raw=b'{"jsonrpc":"2.0","id":1,"method":"ping"}')
        self.assertEqual(result[0]["error"]["code"], -32700)
        with self.assertRaises(RuntimeError):
            self.server.serve(io.BytesIO(), io.BytesIO())

    def test_rate_limit_and_rolling_window(self):
        for message in handshake():
            self.server._handle(message)
        for _ in range(self.server.CALLS_PER_MINUTE):
            self.assertFalse(self.server._handle(call())["result"]["isError"])
        self.assertTrue(self.server._handle(call())["result"]["isError"])
        self.now = 60
        self.assertFalse(self.server._handle(call())["result"]["isError"])
        self.server._calls = self.server.CALLS_PER_SESSION
        self.now = 120
        self.assertTrue(self.server._handle(call())["result"]["isError"])

    def test_allowlist_and_schema_fail_closed(self):
        for name in ("propose_calendar_create", "approve", "execute", "read_file", "roots/list"):
            definitions = copy.deepcopy(DEFINITIONS)
            definitions[0]["name"] = name
            with self.assertRaises(ValueError):
                McpServer(self.dispatch, definitions)
        definitions = copy.deepcopy(DEFINITIONS)
        definitions[0]["input_schema"]["properties"]["query"]["pattern"] = ".*"
        with self.assertRaises(ValueError):
            McpServer(self.dispatch, definitions)

    def test_factory_rechecks_service_each_call(self):
        class Service:
            enabled = True

            def tool_definitions(self):
                return DEFINITIONS

            def call(self, name, args):
                if not self.enabled:
                    raise RuntimeError("Disabled private scope")
                return {}

        service = Service()
        server = create_server(service)
        for message in handshake():
            server._handle(message)
        self.assertFalse(server._handle(call())["result"]["isError"])
        service.enabled = False
        self.assertTrue(server._handle(call())["result"]["isError"])

    def test_subprocess_cli_handshake_without_credentials(self):
        # Inject only the composition root; exercise the real module entrypoint,
        # binary stdio framing, EOF exit and stderr-only trust disclosure.
        program = """
import sys, types, runpy
bootstrap = types.ModuleType("assistant_app.bootstrap")
class Service:
    def tool_definitions(self):
        print("adapter diagnostic")
        return []
    def call(self, name, args): raise AssertionError("must not dispatch")
bootstrap.create_read_service = Service
sys.modules["assistant_app.bootstrap"] = bootstrap
runpy.run_module("assistant_app.mcp", run_name="__main__")
"""
        completed = subprocess.run(
            [sys.executable, "-c", program],
            input=frames(handshake() + [request("tools/list"), request("ping")]),
            capture_output=True,
            timeout=10,
            cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(b"trust this local host", completed.stderr)
        self.assertIn(b"adapter diagnostic", completed.stderr)
        result = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(len(result), 3)
        self.assertEqual(result[1]["result"], {"tools": []})
        self.assertEqual(result[2]["result"], {})

    def test_import_does_not_load_bootstrap_or_storage(self):
        program = """
import sys
import assistant_app.mcp
assert "assistant_app.bootstrap" not in sys.modules
assert "assistant_app.storage" not in sys.modules
assert "assistant_app.integrations" not in sys.modules
assert "assistant_app.infrastructure.storage" not in sys.modules
assert "assistant_app.infrastructure.integrations" not in sys.modules
"""
        completed = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, timeout=10, cwd=Path(__file__).resolve().parents[1]
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, b"")


if __name__ == "__main__":
    unittest.main()
