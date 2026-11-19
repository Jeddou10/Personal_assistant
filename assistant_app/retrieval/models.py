"""Immutable retrieval values and explicit resource budgets (all in memory)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalLimits:
    max_roots: int = 16
    max_documents: int = 128
    max_file_bytes: int = 512 * 1024
    max_total_bytes: int = 8 * 1024 * 1024
    chunk_chars: int = 2000
    max_chunks_per_document: int = 512
    max_chunks: int = 4096
    max_context_chars: int = 12000
    max_query_chars: int = 2000
    max_hits: int = 20

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError("Retrieval limits must be positive integers")


@dataclass(frozen=True)
class RootIdentity:
    root_id: str
    path: str
    identity: tuple[int, int]


@dataclass(frozen=True)
class Citation:
    root_id: str
    relative_path: str
    start_line: int
    end_line: int
    sha256: str


@dataclass(frozen=True)
class Hit:
    citation: Citation
    text: str
    score: float


@dataclass(frozen=True)
class DocumentInfo:
    root_id: str
    relative_path: str
    sha256: str
    chunk_count: int


@dataclass(frozen=True)
class LoadedDocument:
    """A loader must verify scope and identity before/after reading these bytes."""

    relative_path: str
    text: str
    sha256: str
    byte_count: int
    fingerprint: tuple[int, ...]


@dataclass(frozen=True)
class Chunk:
    citation: Citation
    text: str


@dataclass(frozen=True)
class Context:
    """Only hits included in text; source text is untrusted, not instructions."""

    text: str
    hits: tuple[Hit, ...]
