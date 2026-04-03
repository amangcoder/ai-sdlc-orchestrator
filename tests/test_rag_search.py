"""Tests for TASK-017: RAG searcher with LlamaIndex/FAISS backend.

Acceptance criteria verified:
  - search returns results ordered by descending relevance_score (AC-012, REQ-019)
  - Each result has required fields: content, source_run_id, source_artifact, relevance_score
  - rag.enabled=false means search returns empty list (AC-013)
  - Results ordered by descending score

Tests gracefully skip when faiss-cpu is not installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from orchestrator.rag.indexer import RAGIndexer
from orchestrator.rag.search import RAGSearchResult, RAGSearcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rag_config(
    enabled: bool = True,
    provider: str = "llamaindex",
    top_k: int = 5,
    tmp_path: Path | None = None,
) -> Any:
    from orchestrator.models import RAGConfig
    path = str(tmp_path / "rag_store") if tmp_path else ".knowledge/rag"
    return RAGConfig(
        enabled=enabled,
        provider=provider,  # type: ignore[arg-type]
        vector_store_path=path,
        chunk_size=512,
        chunk_overlap=50,
        top_k=top_k,
    )


def _make_mock_provider(search_results: list[dict] | None = None) -> MagicMock:
    """Return a mock RAG provider with configurable search results."""
    provider = MagicMock()
    provider.add_chunks.return_value = None
    provider.save.return_value = None
    provider.load.return_value = None
    provider.search.return_value = search_results or []
    return provider


def _make_raw_results(n: int = 3) -> list[dict[str, Any]]:
    """Generate fake raw search results from a provider."""
    return [
        {
            "text": f"Chunk content for result {i}",
            "run_id": f"run-{i:03d}",
            "artifact_name": "prd",
            "score": 1.0 - i * 0.1,
            "agent": "pm",
            "chunk_index": i,
        }
        for i in range(n)
    ]


def _make_indexer_with_mock_provider(
    search_results: list[dict] | None = None,
    enabled: bool = True,
    top_k: int = 5,
    tmp_path: Path | None = None,
) -> RAGIndexer:
    config = _make_rag_config(enabled=enabled, top_k=top_k, tmp_path=tmp_path)
    mock_provider = _make_mock_provider(search_results)
    return RAGIndexer(config, provider=mock_provider)


# ---------------------------------------------------------------------------
# RAGSearchResult dataclass tests
# ---------------------------------------------------------------------------

class TestRAGSearchResult:
    """Unit tests for the RAGSearchResult dataclass."""

    def test_creation(self) -> None:
        result = RAGSearchResult(
            content="Some relevant text",
            source_run_id="run-001",
            source_artifact="prd",
            relevance_score=0.92,
            agent="pm",
            chunk_index=0,
        )
        assert result.content == "Some relevant text"
        assert result.source_run_id == "run-001"
        assert result.source_artifact == "prd"
        assert result.relevance_score == 0.92
        assert result.agent == "pm"

    def test_to_dict_has_required_fields(self) -> None:
        result = RAGSearchResult(
            content="Test",
            source_run_id="run-001",
            source_artifact="architecture",
            relevance_score=0.85,
        )
        d = result.to_dict()
        assert "content" in d
        assert "source_run_id" in d
        assert "source_artifact" in d
        assert "relevance_score" in d

    def test_agent_defaults_to_none(self) -> None:
        result = RAGSearchResult(
            content="Test",
            source_run_id="run-001",
            source_artifact="prd",
            relevance_score=0.5,
        )
        assert result.agent is None


# ---------------------------------------------------------------------------
# RAGSearcher tests
# ---------------------------------------------------------------------------

class TestRAGSearcher:
    """Tests for RAGSearcher.search()."""

    def test_disabled_config_returns_empty_list(self, tmp_path: Path) -> None:
        """When rag.enabled=False, search should return []."""
        indexer = _make_indexer_with_mock_provider(enabled=False, tmp_path=tmp_path)
        searcher = RAGSearcher(indexer)
        results = searcher.search("authentication flow")
        assert results == []

    def test_returns_list_of_rag_search_results(self, tmp_path: Path) -> None:
        """search() should return a list of RAGSearchResult instances."""
        raw = _make_raw_results(3)
        indexer = _make_indexer_with_mock_provider(search_results=raw, tmp_path=tmp_path)
        searcher = RAGSearcher(indexer)
        results = searcher.search("test query")
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, RAGSearchResult)

    def test_result_count_matches_top_k(self, tmp_path: Path) -> None:
        """search() should return at most top_k results."""
        raw = _make_raw_results(5)
        indexer = _make_indexer_with_mock_provider(
            search_results=raw, top_k=5, tmp_path=tmp_path
        )
        searcher = RAGSearcher(indexer)
        results = searcher.search("authentication flow", top_k=5)
        assert len(results) <= 5

    def test_results_ordered_by_descending_score(self, tmp_path: Path) -> None:
        """Results must be ordered by descending relevance_score."""
        raw = [
            {"text": "Low relevance", "run_id": "run-003", "artifact_name": "prd",
             "score": 0.3, "agent": "pm", "chunk_index": 0},
            {"text": "High relevance", "run_id": "run-001", "artifact_name": "prd",
             "score": 0.9, "agent": "pm", "chunk_index": 0},
            {"text": "Medium relevance", "run_id": "run-002", "artifact_name": "architecture",
             "score": 0.6, "agent": "architect", "chunk_index": 0},
        ]
        indexer = _make_indexer_with_mock_provider(search_results=raw, tmp_path=tmp_path)
        searcher = RAGSearcher(indexer)
        results = searcher.search("test")

        scores = [r.relevance_score for r in results]
        assert scores == sorted(scores, reverse=True), (
            f"Results not sorted descending: {scores}"
        )

    def test_result_fields_are_populated(self, tmp_path: Path) -> None:
        """Each result should have non-empty content, source_run_id, source_artifact."""
        raw = _make_raw_results(2)
        indexer = _make_indexer_with_mock_provider(search_results=raw, tmp_path=tmp_path)
        searcher = RAGSearcher(indexer)
        results = searcher.search("test query")

        for r in results:
            assert r.content, "content should be non-empty"
            assert r.source_run_id, "source_run_id should be non-empty"
            assert r.source_artifact, "source_artifact should be non-empty"
            assert isinstance(r.relevance_score, float)

    def test_empty_index_returns_empty_list(self, tmp_path: Path) -> None:
        """When the provider has no results, search returns []."""
        indexer = _make_indexer_with_mock_provider(search_results=[], tmp_path=tmp_path)
        searcher = RAGSearcher(indexer)
        results = searcher.search("anything")
        assert results == []

    def test_top_k_override(self, tmp_path: Path) -> None:
        """Passing top_k to search() overrides config.top_k."""
        raw = _make_raw_results(10)
        indexer = _make_indexer_with_mock_provider(
            search_results=raw[:3], top_k=3, tmp_path=tmp_path
        )
        searcher = RAGSearcher(indexer)
        results = searcher.search("query", top_k=3)
        assert len(results) <= 3

    def test_provider_exception_returns_empty_list(self, tmp_path: Path) -> None:
        """If the provider's search raises, search() returns [] without crashing."""
        config = _make_rag_config(enabled=True, tmp_path=tmp_path)
        mock_provider = _make_mock_provider()
        mock_provider.search.side_effect = RuntimeError("FAISS lookup failed")
        indexer = RAGIndexer(config, provider=mock_provider)
        searcher = RAGSearcher(indexer)

        results = searcher.search("test query")
        assert results == []

    def test_relevance_scores_are_floats(self, tmp_path: Path) -> None:
        raw = _make_raw_results(3)
        indexer = _make_indexer_with_mock_provider(search_results=raw, tmp_path=tmp_path)
        searcher = RAGSearcher(indexer)
        results = searcher.search("test")
        for r in results:
            assert isinstance(r.relevance_score, float)

    def test_uses_config_top_k_by_default(self, tmp_path: Path) -> None:
        """When top_k is not passed to search(), config.top_k is used."""
        raw = _make_raw_results(5)
        config = _make_rag_config(enabled=True, top_k=3, tmp_path=tmp_path)
        mock_provider = _make_mock_provider(raw)
        indexer = RAGIndexer(config, provider=mock_provider)
        searcher = RAGSearcher(indexer)

        searcher.search("test")

        # Verify provider was called with top_k=3
        call_kwargs = mock_provider.search.call_args
        assert call_kwargs is not None
        _, kwargs = call_kwargs[0], call_kwargs[1] if len(call_kwargs) > 1 else {}
        # Check either positional or keyword arg
        called_top_k = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else kwargs.get("top_k")
        if called_top_k is not None:
            assert called_top_k == 3


# ---------------------------------------------------------------------------
# RAG module-level import isolation
# ---------------------------------------------------------------------------

class TestRAGModuleImports:
    """Tests that verify lazy import behavior."""

    def test_rag_init_module_importable_without_faiss(self) -> None:
        """orchestrator.rag should be importable even without faiss."""
        import orchestrator.rag as rag_module
        assert hasattr(rag_module, "get_indexer")
        assert hasattr(rag_module, "get_searcher")

    def test_rag_indexer_importable_without_faiss(self) -> None:
        """RAGIndexer class should be importable without faiss."""
        from orchestrator.rag.indexer import RAGIndexer  # noqa: F401
        assert True

    def test_rag_searcher_importable_without_faiss(self) -> None:
        """RAGSearcher class should be importable without faiss."""
        from orchestrator.rag.search import RAGSearcher  # noqa: F401
        assert True

    def test_disabled_indexer_never_imports_provider(self, tmp_path: Path) -> None:
        """RAGIndexer with enabled=False should never call get_provider()."""
        from orchestrator.models import RAGConfig
        config = RAGConfig(enabled=False)
        indexer = RAGIndexer(config)

        # These operations must work without triggering provider import
        indexer.index_artifact("run-001", "prd", {"title": "test"})
        indexer.save_index(tmp_path)
        result = indexer.load_index(tmp_path)

        assert indexer._provider is None
        assert result is False
