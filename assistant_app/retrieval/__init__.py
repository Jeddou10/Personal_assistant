"""Opt-in local lexical retrieval, with no startup I/O or application/UI hooks.

Use RetrievalService.set_roots, ingest, search and build_context explicitly.
The default reader supports Windows only; other systems fail closed on file I/O.
No documents, roots or embeddings are persisted. Returned source text is untrusted.
"""

from .contracts import DocumentLoader, Embedder, Index
from .lexical import LexicalIndex
from .models import Chunk, Citation, Context, DocumentInfo, Hit, LoadedDocument, RetrievalLimits, RootIdentity
from .reader import WindowsDocumentLoader
from .service import RetrievalService

__all__ = [
    "Chunk",
    "Citation",
    "Context",
    "DocumentInfo",
    "DocumentLoader",
    "Embedder",
    "Hit",
    "Index",
    "LexicalIndex",
    "LoadedDocument",
    "RetrievalLimits",
    "RetrievalService",
    "RootIdentity",
    "WindowsDocumentLoader",
]
