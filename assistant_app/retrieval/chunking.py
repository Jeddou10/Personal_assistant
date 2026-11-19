"""Bounded chunks with inclusive, one-based original line citations.

Long lines are split without inventing line numbers: multiple chunks can cite
the same line. CRLF and CR are normalized to LF only in chunk text, not hashes.
"""

from assistant_app.domain.errors import AppError

from .models import Chunk, Citation, LoadedDocument, RetrievalLimits, RootIdentity


def chunk_document(root: RootIdentity, doc: LoadedDocument, limits: RetrievalLimits) -> tuple[Chunk, ...]:
    chunks = []
    parts: list[str] = []
    size = 0
    first = last = 1

    def flush():
        nonlocal size, parts
        if not parts:
            return
        if len(chunks) >= limits.max_chunks_per_document:
            raise AppError("Document exceeds chunk budget")
        chunks.append(Chunk(Citation(root.root_id, doc.relative_path, first, last, doc.sha256), "".join(parts)))
        parts = []
        size = 0

    # Split only physical text lines; Unicode separators do not create phantom lines.
    lines = doc.text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for number, line in enumerate(lines, 1):
        text = line + ("\n" if number < len(lines) else "")
        offset = 0
        while offset < len(text):
            if not parts:
                first = number
            last = number
            take = min(limits.chunk_chars - size, len(text) - offset)
            parts.append(text[offset : offset + take])
            size += take
            offset += take
            if size == limits.chunk_chars:
                flush()
    flush()
    return tuple(chunks)
