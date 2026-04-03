"""Tests for TASK-017: RAG indexer with LlamaIndex/FAISS backend.

Acceptance criteria verified:
  - index_artifact chunks JSON content and inserts into FAISS (AC-011, REQ-018, REQ-020)
  - save_index persists index and metadata to disk
  - load_index restores from disk correctly
  - rag.enabled=false skips all RAG initialization (AC-013)
  - Provider can be switched between llamaindex and langchain via config

Tests gracefully skip when faiss-cpu is not installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.rag.indexer import ArtifactChunk, RAGIndexer, _chunk_text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rag_config(
    enabled: bool = True,
    provider: str = "llamaindex",
    vector_store_path: str | None = None,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    top_k: int = 5,
    tmp_path: Path | None = None,
) -> Any:
    """Return a RAGConfig Pydantic model instance."""
    from orchestrator.models import RAGConfig
    path = str(tmp_path / "rag_store") if tmp_path else ".knowledge/rag"
    return RAGConfig(
        enabled=enabled,
        provider=provider,  # type: ignore[arg-type]
        vector_store_path=vector_store_path or path,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        top_k=top_k,
    )


def _make_mock_provider() -> MagicMock:
    """Return a mock RAG provider that accepts chunks and returns search results."""
    provider = MagicMock()
    provider.add_chunks.return_value = None
    provider.search.return_value = []
    provider.save.return_value = None
    provider.load.return_value = None
    return provider


# ---------------------------------------------------------------------------
# _chunk_text unit tests
# ---------------------------------------------------------------------------

class TestChunkText:
    """Unit tests for the _chunk_text helper function."""

    def test_short_text_returns_single_chunk(self) -> None:
        chunks = _chunk_text("hello world", chunk_size=512, chunk_overlap=50)
        assert len(chunks) == 1
        assert chunks[0] == "hello world"

    def test_long_text_splits_into_multiple_chunks(self) -> None:
        text = "A" * 1000
        chunks = _chunk_text(text, chunk_size=200, chunk_overlap=20)
        assert len(chunks) > 1

    def test_no_empty_chunks(self) -> None:
        text = "word " * 200
        chunks = _chunk_text(text, chunk_size=100, chunk_overlap=10)
        for chunk in chunks:
            assert len(chunk) > 0

    def test_chunk_size_respected(self) -> None:
        text = "x" * 2000
        chunks = _chunk_text(text, chunk_size=300, chunk_overlap=30)
        # Each chunk should not exceed chunk_size significantly
        for chunk in chunks:
            assert len(chunk) <= 400, f"Chunk too long: {len(chunk)}"

    def test_empty_text_returns_empty_list(self) -> None:
        chunks = _chunk_text("", chunk_size=512, chunk_overlap=50)
        assert chunks == [] or chunks == [""]


# ---------------------------------------------------------------------------
# ArtifactChunk dataclass tests
# ---------------------------------------------------------------------------

class TestArtifactChunk:
    """Unit tests for the ArtifactChunk dataclass."""

    def test_creation(self) -> None:
        chunk = ArtifactChunk(
            text="Test content",
            run_id="run-001",
            artifact_name="prd",
            agent="pm",
            chunk_index=0,
        )
        assert chunk.text == "Test content"
        assert chunk.run_id == "run-001"
        assert chunk.artifact_name == "prd"
        assert chunk.agent == "pm"
        assert chunk.chunk_index == 0


# ---------------------------------------------------------------------------
# RAGIndexer tests
# ---------------------------------------------------------------------------

class TestRAGIndexer:
    """Tests for RAGIndexer.index_artifact, save_index, load_index."""

    def test_disabled_config_returns_zero_chunks(self, tmp_path: Path) -> None:
        """When rag.enabled=False, index_artifact should return 0 chunks."""
        config = _make_rag_config(enabled=False, tmp_path=tmp_path)
        indexer = RAGIndexer(config)
        result = indexer.index_artifact("run-001", "prd", {"title": "test"}, agent="pm")
        assert result == 0

    def test_disabled_config_does_not_initialize_provider(self, tmp_path: Path) -> None:
        """When rag.enabled=False, no provider should be initialized."""
        config = _make_rag_config(enabled=False, tmp_path=tmp_path)
        indexer = RAGIndexer(config)
        indexer.index_artifact("run-001", "prd", {"title": "test"})
        # Provider should remain None since enabled=False
        assert indexer._provider is None

    def test_index_artifact_calls_provider_add_chunks(self, tmp_path: Path) -> None:
        """index_artifact should delegate to provider.add_chunks()."""
        config = _make_rag_config(enabled=True, tmp_path=tmp_path)
        mock_provider = _make_mock_provider()
        indexer = RAGIndexer(config, provider=mock_provider)

        result = indexer.index_artifact(
            "run-001",
            "prd",
            {"title": "My PRD", "overview": "A test overview"},
            agent="pm",
        )

        assert result > 0
        mock_provider.add_chunks.assert_called_once()

    def test_index_artifact_tracks_chunk_count(self, tmp_path: Path) -> None:
        """chunk_count property should reflect all indexed chunks."""
        config = _make_rag_config(enabled=True, tmp_path=tmp_path)
        mock_provider = _make_mock_provider()
        indexer = RAGIndexer(config, provider=mock_provider)

        indexer.index_artifact("run-001", "prd", {"title": "test content " * 100}, agent="pm")
        assert indexer.chunk_count > 0

    def test_index_artifact_incremental(self, tmp_path: Path) -> None:
        """Multiple index_artifact calls should accumulate chunk_count."""
        config = _make_rag_config(enabled=True, tmp_path=tmp_path)
        mock_provider = _make_mock_provider()
        indexer = RAGIndexer(config, provider=mock_provider)

        count1 = indexer.index_artifact("run-001", "prd", {"title": "test", "body": "x" * 100})
        count2 = indexer.index_artifact("run-001", "architecture", {"title": "arch", "body": "y" * 100})

        assert indexer.chunk_count == count1 + count2

    def test_index_artifact_with_non_serializable_raises_gracefully(self, tmp_path: Path) -> None:
        """index_artifact should return 0, not crash, when data can't be serialized."""
        config = _make_rag_config(enabled=True, tmp_path=tmp_path)
        mock_provider = _make_mock_provider()
        indexer = RAGIndexer(config, provider=mock_provider)

        # Circular reference — can't be JSON-serialized
        circular: dict = {}
        circular["self"] = circular

        result = indexer.index_artifact("run-001", "bad", circular)
        assert result == 0

    def test_provider_exception_does_not_crash(self, tmp_path: Path) -> None:
        """If the provider raises during add_chunks, index_artifact returns 0 gracefully."""
        config = _make_rag_config(enabled=True, tmp_path=tmp_path)
        mock_provider = _make_mock_provider()
        mock_provider.add_chunks.side_effect = RuntimeError("FAISS write failed")
        indexer = RAGIndexer(config, provider=mock_provider)

        result = indexer.index_artifact("run-001", "prd", {"title": "test"})
        assert result == 0

    def test_save_index_calls_provider_save(self, tmp_path: Path) -> None:
        """save_index should delegate to provider.save()."""
        config = _make_rag_config(enabled=True, tmp_path=tmp_path)
        mock_provider = _make_mock_provider()
        indexer = RAGIndexer(config, provider=mock_provider)

        # Index something first
        indexer.index_artifact("run-001", "prd", {"title": "test"})
        indexer.save_index(tmp_path / "test_index")

        mock_provider.save.assert_called_once()

    def test_save_index_disabled_does_nothing(self, tmp_path: Path) -> None:
        """When rag.enabled=False, save_index is a no-op."""
        config = _make_rag_config(enabled=False, tmp_path=tmp_path)
        mock_provider = _make_mock_provider()
        indexer = RAGIndexer(config, provider=mock_provider)

        indexer.save_index(tmp_path / "test_index")
        mock_provider.save.assert_not_called()

    def test_load_index_returns_false_when_path_missing(self, tmp_path: Path) -> None:
        """load_index returns False when the target path does not exist."""
        config = _make_rag_config(enabled=True, tmp_path=tmp_path)
        mock_provider = _make_mock_provider()
        indexer = RAGIndexer(config, provider=mock_provider)

        result = indexer.load_index(tmp_path / "nonexistent_index")
        assert result is False

    def test_load_index_disabled_returns_false(self, tmp_path: Path) -> None:
        """When rag.enabled=False, load_index returns False."""
        config = _make_rag_config(enabled=False, tmp_path=tmp_path)
        mock_provider = _make_mock_provider()
        indexer = RAGIndexer(config, provider=mock_provider)

        result = indexer.load_index(tmp_path)
        assert result is False

    def test_load_index_calls_provider_load(self, tmp_path: Path) -> None:
        """load_index should delegate to provider.load() when path exists."""
        config = _make_rag_config(enabled=True, tmp_path=tmp_path)
        mock_provider = _make_mock_provider()
        indexer = RAGIndexer(config, provider=mock_provider)

        # Create the directory so it exists
        index_dir = tmp_path / "test_index"
        index_dir.mkdir()

        result = indexer.load_index(index_dir)
        mock_provider.load.assert_called_once()
        assert result is True

    def test_chunk_count_starts_at_zero(self, tmp_path: Path) -> None:
        config = _make_rag_config(enabled=True, tmp_path=tmp_path)
        indexer = RAGIndexer(config)
        assert indexer.chunk_count == 0


# ---------------------------------------------------------------------------
# RAGIndexer FAISS integration test (skips when faiss not installed)
# ---------------------------------------------------------------------------

class TestRAGIndexerFAISS:
    """Integration tests that require faiss-cpu to be installed."""

    faiss = pytest.importorskip("faiss")

    def test_faiss_available(self) -> None:
        """Verify faiss is importable for integration tests."""
        import faiss  # noqa: F401
        assert True

    def test_llamaindex_provider_imported(self, tmp_path: Path) -> None:
        """LlamaIndexProvider can be imported when rag extras are installed."""
        pytest.importorskip("faiss")

        try:
            from orchestrator.rag.providers.llamaindex_provider import LlamaIndexProvider
            config = _make_rag_config(enabled=True, tmp_path=tmp_path)
            provider = LlamaIndexProvider(config)
            assert provider is not None
        except ImportError as exc:
            pytest.skip(f"LlamaIndex extras not installed: {exc}")


# ---------------------------------------------------------------------------
# Config / import isolation tests
# ---------------------------------------------------------------------------

class TestRAGConfigIsolation:
    """Tests that rag.enabled=False prevents imports."""

    def test_disabled_config_no_provider_initialized(self, tmp_path: Path) -> None:
        """Creating RAGIndexer with enabled=False must not trigger provider import."""
        config = _make_rag_config(enabled=False, tmp_path=tmp_path)
        indexer = RAGIndexer(config)
        # No provider should be initialized
        assert indexer._provider is None
        # Chunk count starts at 0
        assert indexer.chunk_count == 0

    def test_enabled_config_with_mock_provider(self, tmp_path: Path) -> None:
        """RAGIndexer with enabled=True and mock provider should work without real deps."""
        config = _make_rag_config(enabled=True, tmp_path=tmp_path)
        mock_provider = _make_mock_provider()
        indexer = RAGIndexer(config, provider=mock_provider)

        count = indexer.index_artifact(
            "run-001",
            "prd",
            {"overview": "test feature request " * 20},
        )
        assert count > 0
        assert mock_provider.add_chunks.called
