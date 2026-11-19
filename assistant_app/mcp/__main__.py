"""Explicit opt-in: python -m assistant_app.mcp trusts the local MCP host.

The host receives private content from selected Gmail, Calendar and Notion
reads. No network listener or external MCP client is started. The composition
root's create_read_service() must defer credentials/storage access until call().
"""

import sys
from contextlib import redirect_stdout

from . import create_server


def main() -> int:
    """Load the parent composition root only on explicit CLI startup."""
    print("MCP enabled: you trust this local host with selected private integration reads.", file=sys.stderr)
    try:
        with redirect_stdout(sys.stderr):
            from assistant_app.bootstrap import create_read_service

            server = create_server(create_read_service())
        server.serve(sys.stdin.buffer, sys.stdout.buffer)
    except Exception:
        print("Read-only MCP server unavailable.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
