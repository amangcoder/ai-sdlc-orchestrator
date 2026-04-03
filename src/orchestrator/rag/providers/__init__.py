"""Provider factory for RAG backends.

Selects between LlamaIndex and LangChain backends based on config.provider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestrator.models import RAGConfig


def get_provider(config: "RAGConfig") -> Any:
    """Return the appropriate backend provider for *config*.

    Args:
        config: RAGConfig with ``provider`` field set to ``"llamaindex"``
                or ``"langchain"``.

    Returns:
        A provider instance with ``add_chunks``, ``search``, ``save``,
        and ``load`` methods.

    Raises:
        ImportError: When the required optional dependencies are not installed.
        ValueError:  When ``config.provider`` is not a known value.
    """
    provider_name = getattr(config, "provider", "llamaindex")

    if provider_name == "llamaindex":
        from orchestrator.rag.providers.llamaindex_provider import LlamaIndexProvider
        return LlamaIndexProvider(config)
    elif provider_name == "langchain":
        from orchestrator.rag.providers.langchain_provider import LangChainProvider
        return LangChainProvider(config)
    else:
        raise ValueError(
            f"Unknown RAG provider {provider_name!r}. "
            "Valid values: 'llamaindex', 'langchain'."
        )
