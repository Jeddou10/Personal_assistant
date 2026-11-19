"""Verify the installed wheel without launching the UI or reading user data."""

import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    import assistant_app
    from assistant_app.storage import Store

    package_path = Path(assistant_app.__file__).resolve()
    if not package_path.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError(
            "Smoke check must import a wheel installed in this virtual environment, not source/editable code."
        )
    metadata = importlib.metadata.distribution("personal-assistant-desktop")
    launcher = next(entry for entry in metadata.entry_points if entry.name == "personal-assistant")
    if not callable(launcher.load()):
        raise RuntimeError("GUI entry point did not resolve.")
    if not (Path(sys.prefix) / "Scripts" / "personal-assistant.exe").is_file():
        raise RuntimeError("Installed Windows launcher is missing.")
    with tempfile.TemporaryDirectory(dir=os.environ["LOCALAPPDATA"]) as directory:
        store = Store(directory)
        store.set_secret("smoke-test", "synthetic-test-value")
        store.save_preferences(["Synthetic test preference"])
        reloaded = Store(directory)
        if reloaded.get_secret("smoke-test") != "synthetic-test-value":
            raise RuntimeError("Installed credential storage round trip failed.")
        if reloaded.load_preferences() != ["Synthetic test preference"]:
            raise RuntimeError("Installed preference storage round trip failed.")
        for file in Path(directory).iterdir():
            if b"synthetic-test-value" in file.read_bytes() or b"Synthetic test preference" in file.read_bytes():
                raise RuntimeError("Installed private storage was not encrypted.")
        reloaded.set_secret("smoke-test", "")
        reloaded.save_preferences([])
        if reloaded.get_secret("smoke-test") or reloaded.load_preferences():
            raise RuntimeError("Installed private data removal failed.")
    from assistant_app.retrieval.service import RetrievalService

    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "notes.txt"
        source.write_text("Synthetic retrieval verification: violet telescope.", encoding="utf-8")
        retrieval = RetrievalService()
        retrieval.set_roots([directory])
        retrieval.ingest([source])
        context = retrieval.build_context("telescope")
        if not context.hits or context.hits[0].citation.relative_path != "notes.txt":
            raise RuntimeError("Installed retrieval/citation smoke failed.")
        source.write_text("Changed source.", encoding="utf-8")
        if retrieval.search("telescope"):
            raise RuntimeError("Installed retrieval returned stale content.")
        retrieval.set_roots([])

    protocol = "2025-11-25"
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": protocol,
                "capabilities": {},
                "clientInfo": {"name": "installed-smoke", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    with tempfile.TemporaryDirectory() as directory:
        environment = dict(os.environ, LOCALAPPDATA=directory)
        result = subprocess.run(
            [sys.executable, "-m", "assistant_app.mcp"],
            input="".join(json.dumps(item) + "\n" for item in requests).encode(),
            capture_output=True,
            timeout=10,
            cwd=directory,
            env=environment,
        )
        if result.returncode:
            raise RuntimeError("Installed MCP subprocess failed.")
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        if len(replies) != 2 or replies[0]["result"]["protocolVersion"] != protocol:
            raise RuntimeError("Installed MCP handshake failed.")
        names = {tool["name"] for tool in replies[1]["result"]["tools"]}
        if names != {"search_mail", "read_mail", "list_events", "read_notion"}:
            raise RuntimeError("Installed MCP exposed unexpected capabilities.")
        if list(Path(directory).iterdir()):
            raise RuntimeError("MCP discovery unexpectedly created runtime data.")
    print(f"Installed wheel {metadata.version}: launcher, protected storage, retrieval and MCP passed.")


if __name__ == "__main__":
    main()
