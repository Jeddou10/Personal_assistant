"""Bounded, sequential JSON-RPC subset for one trusted local stdio host.

Supports initialize, notifications/initialized, ping, tools/list and tools/call.
No batches, pagination, tasks, roots, resources, writes or outbound MCP requests.
Unknown notifications are ignored, including calls disguised as notifications.
EOF ends the session; oversized input ends it without draining an unbounded line.
Limits count rejected attempts too and persist for the lifetime of the instance.
"""

import json
import sys
import time
from collections import deque
from collections.abc import Callable
from contextlib import redirect_stdout
from copy import deepcopy
from typing import BinaryIO, Protocol

VERSION = "2025-11-25"
READ_TOOLS = frozenset({"search_mail", "read_mail", "list_events", "read_notion"})


class ReadService(Protocol):
    """Parent bootstrap contract; construction/definitions must not read credentials."""

    def tool_definitions(self) -> list[dict]:
        """Return Anthropic definitions for the selected integration read tools."""
        ...

    def call(self, name: str, args: dict) -> dict:
        """Recheck current scope, perform a bounded read, or raise an exception."""
        ...


def create_server(read_service: ReadService, *, clock: Callable[[], float] = time.monotonic) -> "McpServer":
    """Adapt the parent's lazy read service; never construct Store or Integrations."""
    return McpServer(read_service.call, read_service.tool_definitions(), clock=clock)


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _tool_error() -> dict:
    return {"content": [{"type": "text", "text": "Read failed or arguments are invalid."}], "isError": True}


def _encode(value, limit: int) -> bytes:
    """Serialize strict JSON while limiting accumulated UTF-8 output bytes."""
    output = bytearray()
    for part in json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(value):
        encoded = part.encode("utf-8")
        if len(output) + len(encoded) + 1 > limit:
            raise ValueError("Output limit")
        output.extend(encoded)
    return bytes(output) + b"\n"


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Non-JSON constant")


class McpServer:
    """Single-use stdio session with an injected read-only dispatcher.

    Fixed bounds: 64 KiB/input frame, 256 KiB/output frame, 2,000 input
    frames, 8 MiB total input, 16 MiB total output, 60 tool attempts per
    rolling minute and 500 per instance. The injected clock is monotonic.
    Tool schemas accept only object properties of type string, required,
    additionalProperties=false, and descriptive metadata; omitted
    additionalProperties is tightened to false. No general schema engine
    or schema references are accepted. Dispatchers must raise on failures.
    """

    MAX_INPUT = 64 * 1024
    MAX_OUTPUT = 256 * 1024
    MAX_MESSAGES = 2000
    MAX_INPUT_TOTAL = 8 * 1024 * 1024
    MAX_OUTPUT_TOTAL = 16 * 1024 * 1024
    CALLS_PER_MINUTE = 60
    CALLS_PER_SESSION = 500

    def __init__(
        self, read_dispatcher: Callable[[str, dict], dict], tool_definitions: list[dict], *, clock=time.monotonic
    ):
        if not callable(read_dispatcher) or not callable(clock):
            raise ValueError("Invalid dispatcher or clock")
        self._dispatch = read_dispatcher
        self._clock = clock
        self._tools = {}
        if not isinstance(tool_definitions, list) or len(tool_definitions) > len(READ_TOOLS):
            raise ValueError("Invalid read tool definitions")
        for definition in deepcopy(tool_definitions):
            name = definition.get("name")
            schema = definition.get("input_schema")
            if not isinstance(name, str) or name not in READ_TOOLS or name in self._tools:
                raise ValueError("Only unique allowlisted read tools are permitted")
            if not isinstance(definition.get("description"), str) or not isinstance(schema, dict):
                raise ValueError("Invalid read tool definition")
            if schema.get("type") != "object" or set(schema) - {
                "type",
                "properties",
                "required",
                "additionalProperties",
                "description",
            }:
                raise ValueError("Unsupported read tool schema")
            props, required = schema.get("properties", {}), schema.get("required", [])
            if not isinstance(props, dict) or not isinstance(required, list):
                raise ValueError("Invalid read tool schema")
            if any(not isinstance(key, str) or key not in props for key in required):
                raise ValueError("Invalid required properties")
            for prop in props.values():
                if not isinstance(prop, dict) or prop.get("type") != "string" or set(prop) - {"type", "description"}:
                    raise ValueError("Unsupported property schema")
            if schema.get("additionalProperties", False) is not False:
                raise ValueError("Additional properties are not supported")
            schema["additionalProperties"] = False
            self._tools[name] = {"name": name, "description": definition["description"], "inputSchema": schema}
        _encode({"tools": list(self._tools.values())}, self.MAX_OUTPUT // 2)
        self._state = "new"
        self._calls = 0
        self._recent = deque()
        self._served = False

    def _handle(self, message):
        notification = isinstance(message, dict) and "method" in message and "id" not in message
        request_id = message.get("id") if isinstance(message, dict) else None
        valid_id = type(request_id) in (str, int) and (
            not isinstance(request_id, str)
            or (len(request_id) <= 128 and not any(0xD800 <= ord(char) <= 0xDFFF for char in request_id))
        )
        if (
            not isinstance(message, dict)
            or message.get("jsonrpc") != "2.0"
            or not isinstance(message.get("method"), str)
            or (not notification and not valid_id)
            or set(message) - {"jsonrpc", "id", "method", "params"}
            or ("params" in message and not isinstance(message["params"], dict))
        ):
            return None if notification else _error(request_id if valid_id else None, -32600, "Invalid Request")
        method, params = message["method"], message.get("params", {})
        if notification:
            if (
                method == "notifications/initialized"
                and self._state == "initializing"
                and not (set(params) - {"_meta"})
            ):
                self._state = "ready"
            return None
        if method == "ping":
            return (
                _result(request_id, {})
                if not (set(params) - {"_meta"})
                else _error(request_id, -32602, "Invalid params")
            )
        if method == "initialize":
            info = params.get("clientInfo")
            if self._state != "new":
                return _error(request_id, -32600, "Already initialized")
            if (
                not isinstance(params.get("protocolVersion"), str)
                or not params["protocolVersion"]
                or not isinstance(params.get("capabilities"), dict)
                or not isinstance(info, dict)
                or not all(isinstance(info.get(key), str) and info[key] for key in ("name", "version"))
            ):
                return _error(request_id, -32602, "Invalid params")
            self._state = "initializing"
            return _result(
                request_id,
                {
                    "protocolVersion": VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "personal-assistant-readonly", "version": "1.0"},
                },
            )
        if method not in {"tools/list", "tools/call"}:
            return _error(request_id, -32601, "Method not found")
        if self._state != "ready":
            return _error(request_id, -32600, "Initialization required")
        if method == "tools/list":
            if set(params) - {"_meta"}:
                return _error(request_id, -32602, "Invalid params")
            return _result(request_id, {"tools": list(self._tools.values())})
        name = params.get("name")
        if not isinstance(name, str) or name not in self._tools or set(params) - {"name", "arguments", "_meta"}:
            return _error(request_id, -32602, "Unknown tool or invalid params")
        args = params.get("arguments", {})
        if not isinstance(args, dict):
            return _error(request_id, -32602, "Tool arguments must be an object")
        self._calls += 1
        now = self._clock()
        while self._recent and now - self._recent[0] >= 60:
            self._recent.popleft()
        if self._calls > self.CALLS_PER_SESSION or len(self._recent) >= self.CALLS_PER_MINUTE:
            return _result(request_id, _tool_error())
        self._recent.append(now)
        schema = self._tools[name]["inputSchema"]
        if (
            set(args) - set(schema.get("properties", {}))
            or any(key not in args for key in schema.get("required", []))
            or any(not isinstance(value, str) for value in args.values())
        ):
            return _result(request_id, _tool_error())
        try:
            # A synchronous CLI owns stdout; adapter diagnostics belong on stderr.
            with redirect_stdout(sys.stderr):
                data = self._dispatch(name, args)
            if not isinstance(data, dict):
                raise ValueError("Invalid tool result")
            content = _encode(data, self.MAX_OUTPUT // 2).decode("utf-8").rstrip("\n")
            response = _result(request_id, {"content": [{"type": "text", "text": content}], "isError": False})
            _encode(response, self.MAX_OUTPUT)
            return response
        except Exception:
            return _result(request_id, _tool_error())

    def serve(self, binaryin: BinaryIO, binaryout: BinaryIO) -> None:
        """Serve one newline-delimited UTF-8 binary stream; never close caller streams.

        Reject duplicate keys, batches, nonfinite JSON and unterminated frames.
        Notifications never produce replies. Oversize or aggregate exhaustion
        closes the session by returning. Transport failures are silently terminal.
        """
        if self._served:
            raise RuntimeError("A server instance serves only one session")
        self._served = True
        input_bytes = output_bytes = 0
        try:
            for _ in range(self.MAX_MESSAGES):
                line = binaryin.readline(self.MAX_INPUT + 1)
                if not line:
                    return
                input_bytes += len(line)
                if len(line) > self.MAX_INPUT or input_bytes > self.MAX_INPUT_TOTAL:
                    return
                try:
                    if not line.endswith(b"\n"):
                        raise ValueError("Unterminated frame")
                    message = json.loads(
                        line.decode("utf-8"), object_pairs_hook=_object, parse_constant=_invalid_constant
                    )
                except (ValueError, UnicodeError, RecursionError):
                    response = _error(None, -32700, "Parse error")
                else:
                    try:
                        response = self._handle(message)
                    except Exception:
                        # Never echo untrusted input, exception text or traceback.
                        if isinstance(message, dict) and "id" not in message and "method" in message:
                            response = None
                        else:
                            response = _error(None, -32603, "Internal error")
                if response is not None:
                    encoded = _encode(response, self.MAX_OUTPUT)
                    output_bytes += len(encoded)
                    if output_bytes > self.MAX_OUTPUT_TOTAL:
                        return
                    binaryout.write(encoded)
                    binaryout.flush()
        except (OSError, ValueError, TypeError, RecursionError):
            return
