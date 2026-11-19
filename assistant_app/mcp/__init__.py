"""Optional read-only MCP 2025-11-25 server; importing it opens no resources.

Construct ``McpServer(service.call, service.tool_definitions(), clock=...)`` or
``create_server(service)`` and call ``server.serve(binary_input, binary_output)``.
The trusted service must recheck configured integration scope on every call and
raise on failure. It returns a JSON-compatible dict on success. Definitions use
Anthropic's name/description/input_schema shape. Only flat string object schemas
used by the four existing read tools are supported; other schemas fail closed.
The service must impose its own upstream timeout and response acquisition bounds.
"""

from .server import McpServer, create_server

__all__ = ["McpServer", "create_server"]
