"""Explicit, bounded, memory-only retrieval for future trusted application callers.

No discovery, crawling, persistence, network, embedding or chat hooks. Loader and
index injections are trusted application dependencies. The service owns its index;
do not share or mutate it externally. Operations linearize under a scope lock;
I/O happens outside that lock and results are rejected if its generation changes.
Sources are validated at query time, not continuously: a file can change after a
query returns. This is not an atomic multi-file filesystem snapshot. Document text
and citation paths remain untrusted data if a future UI forwards them to a model.
"""

import json
import ntpath
import os
from dataclasses import dataclass
from threading import RLock
from typing import Iterable

from assistant_app.domain.errors import AppError

from .chunking import chunk_document
from .contracts import DocumentLoader, Index
from .lexical import LexicalIndex
from .models import Chunk, Context, DocumentInfo, Hit, LoadedDocument, RetrievalLimits, RootIdentity
from .reader import WindowsDocumentLoader, contained, safe_path


@dataclass(frozen=True)
class _Document:
    root: RootIdentity
    path: str
    source: LoadedDocument
    chunks: tuple[Chunk, ...]


class RetrievalService:
    def __init__(
        self, *, limits: RetrievalLimits | None = None, loader: DocumentLoader | None = None, index: Index | None = None
    ):
        self.limits = limits or RetrievalLimits()
        self._loader = loader if loader is not None else WindowsDocumentLoader()
        self._index = index if index is not None else LexicalIndex()
        self._index_ready = True
        self._lock = RLock()
        self._generation = 0
        self._roots: tuple[RootIdentity, ...] = ()
        self._documents: dict[str, _Document] = {}

    @property
    def roots(self) -> tuple[RootIdentity, ...]:
        with self._lock:
            return self._roots

    @staticmethod
    def _paths(paths: Iterable[str | os.PathLike[str]], maximum: int) -> tuple[str, ...]:
        if isinstance(paths, (str, bytes, os.PathLike)):
            raise AppError("Pass an iterable of explicitly selected paths")
        result = []
        for path in paths:
            if len(result) >= maximum:
                raise AppError("Too many selected paths")
            result.append(safe_path(os.fspath(path)))
        return tuple(result)

    def _check_generation(self, generation: int):
        if generation != self._generation:
            raise AppError("Retrieval scope or index changed; retry the operation")

    def _discard(self):
        self._documents.clear()
        self._generation += 1
        self._index_ready = False
        try:
            self._index.clear()
        except Exception:
            raise AppError("Retrieval index could not be cleared. Search is disabled until clear succeeds.") from None
        self._index_ready = True

    def _require_index(self):
        if not self._index_ready:
            raise AppError("Retrieval index is unavailable. Clear it successfully before using it again.")

    def set_roots(self, paths: Iterable[str | os.PathLike[str]]) -> tuple[RootIdentity, ...]:
        """Replace approved roots and discard all documents; no files are ingested.

        IDs last for this approval lifetime. Reapproving roots issues new IDs.
        Root validation failure leaves the old selection unchanged. Overlapping
        roots are refused. Index failure disables search but applies the new scope.
        An empty iterable revokes all roots, including on unsupported platforms.
        """
        with self._lock:
            generation = self._generation
        selected = self._paths(paths, self.limits.max_roots)
        roots = []
        for path in selected:
            if any(contained(root.path, path) or contained(path, root.path) for root in roots):
                raise AppError("Approved roots must not overlap")
            roots.append(self._loader.approve_root(path))
        with self._lock:
            self._check_generation(generation)
            self._roots = tuple(roots)
            self._discard()
            return self._roots

    def clear(self) -> None:
        """Discard all indexed documents and cancel in-flight work; retain roots."""
        with self._lock:
            self._discard()

    def revoke_roots(self, root_ids: Iterable[str]) -> tuple[RootIdentity, ...]:
        """Remove selected root IDs and clear the entire index, even for unknown IDs."""
        if isinstance(root_ids, str):
            raise AppError("Pass an iterable of root IDs")
        ids = set()
        for number, root_id in enumerate(root_ids):
            if number >= self.limits.max_roots:
                raise AppError("Too many root IDs")
            ids.add(root_id)
        with self._lock:
            self._roots = tuple(root for root in self._roots if root.root_id not in ids)
            self._discard()
            return self._roots

    def _replace_index(self, documents):
        # Injected indexes may mutate before raising. Never trust their previous
        # contents after failure, and invalidate work already in flight.
        self._generation += 1
        self._index_ready = False
        try:
            self._index.replace(tuple(chunk for doc in documents.values() for chunk in doc.chunks))
        except Exception:
            raise AppError("Retrieval index update failed. Clear it before using it again.") from None
        self._index_ready = True

    def ingest(self, paths: Iterable[str | os.PathLike[str]]) -> tuple[DocumentInfo, ...]:
        """Read only listed files; atomically add/replace them within all budgets.

        Directories are not crawled. A rejected batch commits no new documents.
        Previously indexed sources are still revalidated on every search.
        """
        with self._lock:
            self._require_index()
            generation, roots = self._generation, self._roots
            documents = self._documents.copy()
        selected = self._paths(paths, self.limits.max_documents)
        output = []
        unique = {ntpath.normcase(path): path for path in selected}
        for key, path in unique.items():
            root = next((root for root in roots if contained(root.path, path)), None)
            if root is None:
                raise AppError("Document is outside approved roots")
            try:
                source = self._loader.read(root, path, self.limits.max_file_bytes)
            except OSError:
                raise AppError("Cannot securely read retrieval source") from None
            chunks = chunk_document(root, source, self.limits)
            documents[key] = _Document(root, path, source, chunks)
            if (
                len(documents) > self.limits.max_documents
                or sum(doc.source.byte_count for doc in documents.values()) > self.limits.max_total_bytes
                or sum(len(doc.chunks) for doc in documents.values()) > self.limits.max_chunks
            ):
                raise AppError("Retrieval index budget exceeded")
            output.append(DocumentInfo(root.root_id, source.relative_path, source.sha256, len(chunks)))
        with self._lock:
            self._check_generation(generation)
            self._replace_index(documents)
            self._documents = documents
            return tuple(output)

    def _query(self, query: str, limit: int) -> tuple[tuple[Hit, ...], int]:
        if not isinstance(query, str) or len(query) > self.limits.max_query_chars:
            raise AppError("Invalid or oversized retrieval query")
        if type(limit) is not int or not 0 <= limit <= self.limits.max_hits:
            raise AppError("Invalid retrieval hit limit")
        with self._lock:
            self._require_index()
            generation = self._generation
            documents = tuple(self._documents.items())
        invalid = []
        for key, doc in documents:
            try:
                current = self._loader.read(doc.root, doc.path, self.limits.max_file_bytes)
                if (
                    current.sha256 != doc.source.sha256
                    or current.fingerprint != doc.source.fingerprint
                    or current.relative_path != doc.source.relative_path
                ):
                    invalid.append(key)
            except (AppError, OSError):
                invalid.append(key)
        with self._lock:
            self._check_generation(generation)
            if invalid:
                for key in invalid:
                    self._documents.pop(key, None)
                self._replace_index(self._documents)
            hits = self._index.search(query, limit)
            return hits, self._generation

    def search(self, query: str, limit: int = 5) -> tuple[Hit, ...]:
        """Revalidate every source using the secure reader, then rank lexically.

        Deleted, edited, replaced, inaccessible or out-of-scope sources are removed,
        never automatically refreshed. Explicit ingest is required to restore them.
        """
        hits, generation = self._query(query, limit)
        with self._lock:
            self._check_generation(generation)
            return hits

    def build_context(self, query: str, limit: int = 5, *, max_chars: int | None = None) -> Context:
        """Return bounded citation-labelled text and exactly its included hits.

        Includes only whole chunks; an oversized chunk is skipped, never cut into
        a misleading line citation. max_chars counts Unicode characters, not tokens.
        Citation headers are JSON to escape untrusted filenames. Text is untrusted.
        """
        budget = self.limits.max_context_chars if max_chars is None else max_chars
        if type(budget) is not int or not 0 <= budget <= self.limits.max_context_chars:
            raise AppError("Invalid retrieval context budget")
        hits, generation = self._query(query, limit)
        blocks, included = [], []
        size = 0
        for hit in hits:
            header = json.dumps(vars(hit.citation), ensure_ascii=True, sort_keys=True)
            block = f"[citation {header}]\n{hit.text}\n"
            if size + len(block) <= budget:
                blocks.append(block)
                included.append(hit)
                size += len(block)
        with self._lock:
            self._check_generation(generation)
            return Context("".join(blocks), tuple(included))
