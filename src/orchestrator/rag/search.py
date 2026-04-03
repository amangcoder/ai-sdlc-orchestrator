"""RAG searcher — semantic search over indexed artifacts.

Usage::

    from orchestrator.rag.search import RAGSearcher, RAGSearchResult
    from orchestrator.rag.indexer import RAGIndexer

    indexer = RAGIndexer(config)
    indexer.load_index()

    searcher = RAGSearcher(indexer)
    results = searcher.search("authentication flow", top_k=5)
    for r in results:
        print(r.source_artifact, r.relevance_score, r.content[:100])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from orchestrator.rag.indexer import RAGIndexer
    from orchestrator.models import RAGConfig


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class RAGSearchResult:
    """A single search result from the RAG index."""

    content: str
    source_run_id: str
    source_artifact: str
    relevance_score: float
    agent: str | None = None
    chunk_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "source_run_id": self.source_run_id,
            "source_artifact": self.source_artifact,
            "relevance_score": self.relevance_score,
            "agent": self.agent,
            "chunk_index": self.chunk_index,
        }


# ---------------------------------------------------------------------------
# RAGSearcher
# ---------------------------------------------------------------------------

class RAGSearcher:
    """Semantic search over an indexed FAISS vector store.

    Args:
        indexer: A :class:`RAGIndexer` instance (may be pre-populated or loaded
                 from disk).
    """

    def __init__(self, indexer: "RAGIndexer") -> None:
        self._indexer = indexer

    def search(
        self,
        query: str,
        top_k: int | None = None,
    ) -> list[RAGSearchResult]:
        """Return the top-*k* most relevant chunks for *query*.

        Results are ordered by descending :attr:`RAGSearchResult.relevance_score`.

        Args:
            query: Natural-language search query.
            top_k: Number of results to return. Defaults to
                   ``indexer.config.top_k``.

        Returns:
            List of :class:`RAGSearchResult`, sorted descending by score.
        """
        if not self._indexer.config.enabled:
            return []

        k = top_k if top_k is not None else self._indexer.config.top_k

        try:
            provider = self._indexer._get_provider()
            raw_results = provider.search(query, top_k=k)
        except Exception as exc:
            logger.warning("RAGSearcher: search failed: %s", exc)
            return []

        results = []
        for item in raw_results:
            results.append(
                RAGSearchResult(
                    content=item.get("text", ""),
                    source_run_id=item.get("run_id", ""),
                    source_artifact=item.get("artifact_name", ""),
                    relevance_score=float(item.get("score", 0.0)),
                    agent=item.get("agent"),
                    chunk_index=int(item.get("chunk_index", 0)),
                )
            )

        # Ensure descending order by relevance score
        results.sort(key=lambda r: r.relevance_score, reverse=True)
        return results
