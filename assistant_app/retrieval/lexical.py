"""Deterministic token-overlap ranking, not vector or semantic search."""

import re
from collections import Counter
from typing import Sequence

from .models import Chunk, Hit


def terms(text: str) -> Counter[str]:
    return Counter(re.findall(r"\w+", text.casefold()))


class LexicalIndex:
    def __init__(self):
        self._entries: tuple[tuple[Chunk, Counter[str]], ...] = ()

    def replace(self, chunks: Sequence[Chunk]) -> None:
        self._entries = tuple((chunk, terms(chunk.text)) for chunk in chunks)

    def clear(self) -> None:
        self._entries = ()

    def search(self, query: str, limit: int = 5) -> tuple[Hit, ...]:
        wanted = terms(query)
        hits = []
        for chunk, counts in self._entries:
            score = float(sum(min(counts[word], count) for word, count in wanted.items()))
            if score:
                hits.append(Hit(chunk.citation, chunk.text, score))
        hits.sort(
            key=lambda hit: (-hit.score, hit.citation.root_id, hit.citation.relative_path, hit.citation.start_line)
        )
        return tuple(hits[: max(0, limit)])
