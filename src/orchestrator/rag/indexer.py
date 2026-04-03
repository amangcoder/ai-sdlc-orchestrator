"""RAG indexer — chunks and embeds artifacts into a FAISS vector store.

Usage::

    from orchestrator.rag.indexer import RAGIndexer
    from orchestrator.models import RAGConfig

    indexer = RAGIndexer(RAGConfig(enabled=True))
    indexer.index_artifact("run-abc", "prd", {"title": "...", ...}, agent="pm")
    indexer.save_index()

    # Later:
    indexer2 = RAGIndexer(config)
    indexer2.load_index()
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from orchestrator.models import RAGConfig


# ---------------------------------------------------------------------------
# Chunk dataclass
# ---------------------------------------------------------------------------

@dataclass
class ArtifactChunk:
    """A single text chunk from an artifact, with metadata."""

    text: str
    run_id: str
    artifact_name: str
    agent: str | None = None
    chunk_index: int = 0


# ---------------------------------------------------------------------------
# Text splitter (no external deps)
# ---------------------------------------------------------------------------

def _chunk_text(text: str, chunk_size: int = 512, chunk_overlap: int = 50) -> list[str]:
    """Split *text* into overlapping character-level chunks.

    This is a simple character-level splitter that first tries to split on
    paragraph boundaries (``\\n\\n``) then line boundaries (``\\n``), and
    finally falls back to hard character slicing.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    step = max(1, chunk_size - chunk_overlap)
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end]

        # Try to find a clean sentence/paragraph boundary within the chunk
        for sep in ("\n\n", "\n", ". ", " "):
            last_sep = chunk.rfind(sep, chunk_size // 2)
            if last_sep > 0:
                end = start + last_sep + len(sep)
                chunk = text[start:end]
                break

        chunks.append(chunk.strip())
        start += max(1, len(chunk) - chunk_overlap)

    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# RAGIndexer
# ---------------------------------------------------------------------------

class RAGIndexer:
    """Indexes artifact content into a FAISS vector store for semantic search.

    Supports two backends via the provider pattern:
      - ``llamaindex``: LlamaIndex + FAISS + fastembed (default)
      - ``langchain``:  LangChain + FAISS + fastembed

    The provider is selected from :attr:`RAGConfig.provider` and lazy-loaded
    on first use so that importing this class does not require RAG deps.

    Args:
        config: RAGConfig instance with enabled=True.
        provider: Optional pre-built provider (used for testing / injection).
    """

    def __init__(self, config: "RAGConfig", provider: Any | None = None) -> None:
        self.config = config
        self._provider = provider
        self._chunks: list[ArtifactChunk] = []

    # ── Provider access (lazy) ──────────────────────────────────────────────

    def _get_provider(self) -> Any:
        if self._provider is None:
            from orchestrator.rag.providers import get_provider
            self._provider = get_provider(self.config)
        return self._provider

    # ── Public API ──────────────────────────────────────────────────────────

    def index_artifact(
        self,
        run_id: str,
        name: str,
        data: dict[str, Any],
        agent: str | None = None,
    ) -> int:
        """Chunk *data* and insert into the FAISS index.

        Args:
            run_id:  Run identifier that produced this artifact.
            name:    Artifact name (e.g. ``"prd"``).
            data:    JSON-serialisable artifact content.
            agent:   Agent role that produced the artifact.

        Returns:
            Number of chunks added to the index.
        """
        if not self.config.enabled:
            return 0

        try:
            text = json.dumps(data, indent=2, default=str)
        except (TypeError, ValueError) as exc:
            logger.warning("RAGIndexer: could not serialise artifact %s/%s: %s", run_id, name, exc)
            return 0

        raw_chunks = _chunk_text(text, self.config.chunk_size, self.config.chunk_overlap)
        chunks = [
            ArtifactChunk(
                text=c,
                run_id=run_id,
                artifact_name=name,
                agent=agent,
                chunk_index=i,
            )
            for i, c in enumerate(raw_chunks)
        ]

        if not chunks:
            return 0

        try:
            provider = self._get_provider()
            provider.add_chunks(chunks)
            self._chunks.extend(chunks)
            logger.debug(
                "RAGIndexer: indexed %d chunks for %s/%s",
                len(chunks), run_id, name,
            )
            return len(chunks)
        except Exception as exc:
            logger.warning(
                "RAGIndexer: failed to index %s/%s: %s", run_id, name, exc
            )
            return 0

    def save_index(self, path: str | Path | None = None) -> None:
        """Persist the FAISS index and metadata to *path* (or config.vector_store_path)."""
        if not self.config.enabled:
            return

        target = Path(path or self.config.vector_store_path).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)

        try:
            provider = self._get_provider()
            provider.save(target)
            logger.info("RAGIndexer: index saved to %s", target)
        except Exception as exc:
            logger.warning("RAGIndexer: save_index failed: %s", exc)

    def load_index(self, path: str | Path | None = None) -> bool:
        """Load the FAISS index from *path* (or config.vector_store_path).

        Returns True on success, False if the index directory does not exist.
        """
        if not self.config.enabled:
            return False

        target = Path(path or self.config.vector_store_path).expanduser().resolve()
        if not target.exists():
            return False

        try:
            provider = self._get_provider()
            provider.load(target)
            logger.info("RAGIndexer: index loaded from %s", target)
            return True
        except Exception as exc:
            logger.warning("RAGIndexer: load_index failed: %s", exc)
            return False

    @property
    def chunk_count(self) -> int:
        """Return number of chunks indexed in the current session."""
        return len(self._chunks)
