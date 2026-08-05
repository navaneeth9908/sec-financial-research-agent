"""Deterministic lexical and section-aware retrieval for SEC filing evidence."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from sec_financial_research.domain.models import FilingChunk

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "does",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "what",
        "when",
        "where",
        "which",
        "with",
    }
)


@dataclass(frozen=True)
class FilingEvidence:
    """One ranked filing chunk with transparent deterministic scoring signals."""

    rank: int
    score: float
    lexical_score: float
    matched_terms: tuple[str, ...]
    chunk: FilingChunk


class HybridFilingRetriever:
    """Rank filing chunks with BM25 and optional canonical-section signals."""

    def __init__(self, chunks: tuple[FilingChunk, ...]) -> None:
        self._chunks = chunks
        self._body_tokens = tuple(_tokenize(chunk.text) for chunk in chunks)
        self._average_document_length = (
            sum(map(len, self._body_tokens)) / len(chunks) if chunks else 0.0
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        mode: Literal["lexical", "hybrid"] = "hybrid",
    ) -> tuple[FilingEvidence, ...]:
        """Return positive-scoring evidence in stable rank order."""

        if limit < 1:
            raise ValueError("limit must be at least 1")
        if mode not in {"lexical", "hybrid"}:
            raise ValueError("mode must be 'lexical' or 'hybrid'")
        query_terms = tuple(dict.fromkeys(_tokenize(query)))
        if not query_terms:
            raise ValueError("query must contain at least one searchable term")
        if not self._chunks:
            return ()

        document_frequency = {
            term: sum(term in tokens for tokens in self._body_tokens)
            for term in query_terms
        }
        candidates: list[
            tuple[float, float, tuple[str, ...], FilingChunk]
        ] = []
        for chunk, body_tokens in zip(
            self._chunks,
            self._body_tokens,
            strict=True,
        ):
            counts = Counter(body_tokens)
            lexical_score = sum(
                self._bm25_term_score(
                    term_frequency=counts[term],
                    document_frequency=document_frequency[term],
                    document_length=len(body_tokens),
                )
                for term in query_terms
            )
            section_tokens = _tokenize(chunk.section) if mode == "hybrid" else ()
            section_matches = set(query_terms).intersection(section_tokens)
            section_score = 0.0
            if section_matches:
                section_score = len(section_matches) / len(query_terms)
                if _contains_phrase(section_tokens, query_terms):
                    section_score += 0.5
            if not lexical_score and not section_score:
                continue
            matched_terms = tuple(
                sorted(
                    set(query_terms).intersection((*body_tokens, *section_tokens))
                )
            )
            candidates.append(
                (lexical_score, section_score, matched_terms, chunk)
            )

        maximum_lexical_score = max(
            (lexical for lexical, _, _, _ in candidates),
            default=0.0,
        )
        scored: list[tuple[float, float, tuple[str, ...], FilingChunk]] = []
        for lexical_score, section_score, matched_terms, chunk in candidates:
            score = lexical_score
            if mode == "hybrid":
                normalized_lexical = (
                    lexical_score / maximum_lexical_score
                    if maximum_lexical_score
                    else 0.0
                )
                score = normalized_lexical + section_score
            scored.append((score, lexical_score, matched_terms, chunk))

        scored.sort(
            key=lambda item: (-item[0], item[3].chunk_index, item[3].chunk_id)
        )
        return tuple(
            FilingEvidence(
                rank=rank,
                score=round(score, 6),
                lexical_score=round(lexical_score, 6),
                matched_terms=matched_terms,
                chunk=chunk,
            )
            for rank, (score, lexical_score, matched_terms, chunk) in enumerate(
                scored[:limit],
                start=1,
            )
        )

    def _bm25_term_score(
        self,
        *,
        term_frequency: int,
        document_frequency: int,
        document_length: int,
    ) -> float:
        if not term_frequency:
            return 0.0
        inverse_document_frequency = math.log(
            1
            + (len(self._chunks) - document_frequency + 0.5)
            / (document_frequency + 0.5)
        )
        k1 = 1.2
        b = 0.75
        length_normalization = k1 * (
            1 - b
            + b * document_length / max(self._average_document_length, 1)
        )
        return inverse_document_frequency * (
            term_frequency * (k1 + 1)
            / (term_frequency + length_normalization)
        )


def _tokenize(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in _TOKEN.findall(value.lower())
        if token not in _STOP_WORDS
    )


def _contains_phrase(document: tuple[str, ...], query: tuple[str, ...]) -> bool:
    if len(query) > len(document):
        return False
    return any(
        document[index : index + len(query)] == query
        for index in range(len(document) - len(query) + 1)
    )
