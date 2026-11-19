"""Trusted replaceable ports. Implementations must honor the loader security contract."""

from typing import Protocol, Sequence

from .models import Chunk, Hit, LoadedDocument, RootIdentity


class DocumentLoader(Protocol):
    def approve_root(self, path: str) -> RootIdentity:
        """Verify a local non-reparse directory and capture its handle identity."""
        ...

    def read(self, root: RootIdentity, path: str, max_bytes: int) -> LoadedDocument:
        """Bounded strict UTF-8 read; validate held handles, root and scope before bytes and after read."""
        ...


class Index(Protocol):
    def replace(self, chunks: Sequence[Chunk]) -> None: ...

    def search(self, query: str, limit: int) -> tuple[Hit, ...]: ...

    def clear(self) -> None: ...


class Embedder(Protocol):
    """Future integration only: the lexical baseline never invokes an embedder."""

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...
