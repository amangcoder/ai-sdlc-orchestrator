"""LangChain + FAISS provider for RAG indexing and search.

Uses:
  - ``langchain-community`` for FAISS VectorStore wrapper
  - ``fastembed`` (via ``langchain-community`` FastEmbedEmbeddings) for ONNX embeddings
  - ``faiss-cpu`` for the underlying FAISS library

Install::

    pip install "ai-sdlc-orchestrator[rag-langchain]"
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from orchestrator.models import RAGConfig
    from orchestrator.rag.indexer import ArtifactChunk

_INDEX_FILE = "index.langchain.faiss"
_META_FILE = "metadata.langchain.pkl"


class LangChainProvider:
    """FAISS-backed vector store using LangChain + fastembed embeddings.

    Maintains a list of (embedding, metadata) pairs and uses a raw FAISS
    IndexFlatIP for retrieval.  This mirrors the LlamaIndex provider but
    uses LangChain-compatible structures for the embedding pipeline.
    """

    def __init__(self, config: "RAGConfig") -> None:
        self.config = config
        self._embed_model: Any = None
        self._faiss_index: Any = None
        self._metadata: list[dict[str, Any]] = []
        self._dim: int | None = None

    def _ensure_initialized(self) -> None:
        if self._embed_model is not None:
            return

        try:
            import faiss  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "faiss-cpu is required for the LangChain RAG provider. "
                "Install it: pip install 'ai-sdlc-orchestrator[rag-langchain]'"
            ) from exc

        try:
            from fastembed import TextEmbedding
            self._embed_model = TextEmbedding(model_name=self.config.embedding_model)
            dummy = list(self._embed_model.embed(["hello"]))
            self._dim = len(dummy[0])
        except ImportError as exc:
            raise ImportError(
                "fastembed is required for the LangChain RAG provider. "
                "Install it: pip install 'ai-sdlc-orchestrator[rag-langchain]'"
            ) from exc

        import faiss
        self._faiss_index = faiss.IndexFlatIP(self._dim)

    def add_chunks(self, chunks: "list[ArtifactChunk]") -> None:
        """Embed and insert *chunks* into the FAISS index."""
        self._ensure_initialized()

        import numpy as np

        texts = [c.text for c in chunks]
        metas = [
            {
                "run_id": c.run_id,
                "artifact_name": c.artifact_name,
                "agent": c.agent,
                "chunk_index": c.chunk_index,
                "text": c.text,
            }
            for c in chunks
        ]

        embeddings = list(self._embed_model.embed(texts))
        vectors = np.array(embeddings, dtype="float32")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        vectors = vectors / norms

        self._faiss_index.add(vectors)
        self._metadata.extend(metas)

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Return the *top_k* most similar chunks for *query*."""
        self._ensure_initialized()

        if self._faiss_index.ntotal == 0:
            return []

        import numpy as np

        query_vecs = list(self._embed_model.embed([query]))
        q = np.array(query_vecs, dtype="float32")
        norm = np.linalg.norm(q)
        if norm > 0:
            q = q / norm

        k = min(top_k, self._faiss_index.ntotal)
        scores, indices = self._faiss_index.search(q, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._metadata):
                continue
            meta = dict(self._metadata[idx])
            meta["score"] = float(score)
            results.append(meta)

        return results

    def save(self, path: Path) -> None:
        """Save FAISS index and metadata to *path*."""
        self._ensure_initialized()

        import faiss

        path.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._faiss_index, str(path / _INDEX_FILE))
        with open(path / _META_FILE, "wb") as f:
            pickle.dump({"metadata": self._metadata, "dim": self._dim}, f)

        logger.debug("LangChainProvider: saved %d vectors to %s", self._faiss_index.ntotal, path)

    def load(self, path: Path) -> None:
        """Load FAISS index and metadata from *path*."""
        try:
            from fastembed import TextEmbedding
            import faiss

            index_path = path / _INDEX_FILE
            meta_path = path / _META_FILE

            if not index_path.exists() or not meta_path.exists():
                logger.debug("LangChainProvider: index files not found at %s", path)
                return

            self._faiss_index = faiss.read_index(str(index_path))
            with open(meta_path, "rb") as f:
                saved = pickle.load(f)
            self._metadata = saved.get("metadata", [])
            self._dim = saved.get("dim")
            self._embed_model = TextEmbedding(model_name=self.config.embedding_model)

            logger.debug("LangChainProvider: loaded %d vectors from %s", len(self._metadata), path)

        except Exception as exc:
            logger.warning("LangChainProvider: load failed: %s", exc)
