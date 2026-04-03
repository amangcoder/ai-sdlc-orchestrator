"""RAG (Retrieval-Augmented Generation) indexing and search for orchestrator artifacts.

Provides semantic search over pipeline artifacts using FAISS vector store.
All imports are lazy-guarded so ``import orchestrator.rag`` works even when
the optional RAG dependencies are not installed.

Install extras:
  pip install "ai-sdlc-orchestrator[rag]"           # LlamaIndex + FAISS
  pip install "ai-sdlc-orchestrator[rag-langchain]"  # LangChain + FAISS
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.rag.indexer import RAGIndexer
    from orchestrator.rag.search import RAGSearchResult, RAGSearcher


def get_indexer(config):
    """Return a RAGIndexer for *config*.  Raises ImportError when deps are missing."""
    from orchestrator.rag.indexer import RAGIndexer as _RAGIndexer
    return _RAGIndexer(config)


def get_searcher(indexer):
    """Return a RAGSearcher backed by *indexer*."""
    from orchestrator.rag.search import RAGSearcher as _RAGSearcher
    return _RAGSearcher(indexer)


__all__ = ["get_indexer", "get_searcher"]
