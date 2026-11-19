# Local retrieval foundation

`assistant_app.retrieval` is an executable developer API for explicit local text ingestion, lexical search and citation-labelled context. It is a foundation for future retrieval-augmented generation (RAG), not an end-to-end AI answering feature. It is not connected to the desktop UI, conversation engine, model tools or MCP tools.

Nothing is scanned at import, installation or desktop startup. Callers explicitly select Windows roots and individual files; selecting a root does not ingest its contents. There is no directory crawler, Google Drive adapter, embedding download, semantic search or persistent index. The built-in implementation uses only the standard library and performs no network calls.

## Developer example: synthetic files only

Run this Python example on Windows from the checkout or an installed package. It creates and indexes only a temporary synthetic document, sends nothing to a provider, and removes its temporary directory when finished. The temporary location must satisfy the reader's local, non-reparse path restrictions; a redirected or excluded temporary directory can be rejected.

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from assistant_app.retrieval import RetrievalService

with TemporaryDirectory(prefix="retrieval-demo-") as directory:
    root = Path(directory)
    document = root / "notes.txt"
    document.write_text(
        "Synthetic project notes.\nThe demo garden has blue flowers.\n",
        encoding="utf-8",
    )

    retrieval = RetrievalService()
    retrieval.set_roots([root])
    documents = retrieval.ingest([document])
    hits = retrieval.search("blue flowers", limit=3)
    context = retrieval.build_context("blue flowers", limit=3, max_chars=2000)

    assert documents and hits and context.hits
    assert hits[0].citation.relative_path == "notes.txt"
    print(context.text)  # Synthetic source text and relative-path citations only.
    retrieval.set_roots([])  # Revoke roots and discard indexed documents.
```

No personal directory or private source file is selected by this example. It exercises actual Windows file reads on synthetic data, not a live account integration.

## Lifecycle and citations

`set_roots(paths)` replaces the approved selection and clears the index. Roots must not overlap; root IDs last only for that approval lifetime. `ingest(paths)` explicitly adds or replaces listed documents within those roots. A rejected batch commits no new documents.

`search(query, limit=5)` revalidates every indexed source before lexical ranking. Deleted, edited, replaced, inaccessible or out-of-scope sources are removed, not silently refreshed; restoring them requires explicit ingestion. `clear()` discards documents while retaining roots. `revoke_roots(ids)` removes selected roots and clears the whole index. Scope/index changes invalidate concurrent work.

Hits include text, a lexical overlap score and a citation containing root ID, relative path, inclusive one-based start/end lines and the SHA-256 hash of the original bytes. Scores measure token overlap, not semantic confidence. Long physical lines can span chunks citing the same line. Line-ending normalization in chunk text does not change the source hash.

`build_context()` returns bounded citation-labelled text and exactly the hits included in it. Citation headers use JSON escaping for untrusted filenames. It includes whole chunks only, skipping chunks that do not fit; its budget counts characters, not model tokens. Citations establish provenance, not factual accuracy. Snippets and filenames remain untrusted data and must never grant permissions or override instructions if later forwarded to a model.

## Safe reader and resource limits

The default `WindowsDocumentLoader` fails closed on non-Windows file I/O. It accepts strict UTF-8 `.txt`, `.md`, `.csv` and `.json` files and rejects NUL-containing binary content. It applies conservative secret/generated-name filters and excludes dot paths and common dependency/build/cache directories. These filters cannot guarantee ordinary text is free of secrets.

The reader holds ancestor and file handles without write/delete sharing, rejects reparse points (including symlinks, junctions and cloud placeholders), and checks final paths, selected root identity and containment before reading bytes and again afterward. It rejects UNC/device paths, remote drives, alternate streams and ambiguous paths; files open for writing can also be rejected. Size and metadata checks reject files changed during the read. This protects against ordinary filesystem races, not privileged attackers or malicious injected dependencies.

Default `RetrievalLimits` allow 16 roots, 128 documents, 512 KiB per file, 8 MiB total source bytes, 2,000 characters per chunk, 512 chunks per document and 4,096 chunks overall. Queries are capped at 2,000 characters, requested hits at 20, and context at 12,000 characters. Callers can supply explicit limits.

Source validation occurs at query time, not continuously. A file may change after results return, and multiple files are not read as one atomic snapshot. Text, root selections, hashes and index entries remain in process memory; callers holding returned results are responsible for their lifetime and onward transmission. OS swap and crash dumps remain outside the application's control.

## Extension contracts and alpha limits

`DocumentLoader` defines root approval and bounded, scope-validated reads. `Index` defines `replace`, `search` and `clear`; `LexicalIndex` is the current in-memory implementation. `RetrievalService(loader=..., index=..., limits=...)` accepts trusted implementations and owns its index; do not share or mutate that index externally.

An index update or clear failure disables search, context building and ingestion. Call `clear()` successfully before using the service again, then explicitly ingest the desired files. Root revocations still take effect when clearing fails; old snippets cannot be queried through the service. Index implementations must actually discard their contents when `clear()` succeeds.

`Embedder.embed(texts)` is a future contract only. The service does not invoke an embedder, download weights, create vectors or use a vector database. A future embedder/index implementation, Drive connector or UI/model integration needs its own data-flow review and explicit selection controls. Their contracts do not make those capabilities available today.

The project remains an unreleased alpha. Synthetic examples and fake-service tests do not establish live provider verification or a security audit. See [architecture](ARCHITECTURE.md), [privacy](../PRIVACY.md) and [security](../SECURITY.md).
