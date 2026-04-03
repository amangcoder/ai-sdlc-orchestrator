#!/usr/bin/env python3
"""Minimal stdio MCP server exposing the artifact_rag_search tool.

This script is launched as a subprocess by the orchestrator when
``rag.enabled=true``.  It reads JSON-RPC messages from stdin and writes
responses to stdout, implementing the MCP stdio transport protocol.

Environment variables:
  RAG_VECTOR_STORE_PATH: Path to the FAISS index directory
  RAG_PROVIDER:          "llamaindex" or "langchain"
  RAG_EMBEDDING_MODEL:   fastembed model name
  RAG_TOP_K:             Default number of results
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load the RAG searcher from environment
# ---------------------------------------------------------------------------

def _build_searcher():
    """Initialise and return a RAGSearcher from environment variables."""
    try:
        from orchestrator.models import RAGConfig
        from orchestrator.rag.indexer import RAGIndexer
        from orchestrator.rag.search import RAGSearcher

        config = RAGConfig(
            enabled=True,
            provider=os.environ.get("RAG_PROVIDER", "llamaindex"),
            embedding_model=os.environ.get("RAG_EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
            vector_store_path=os.environ.get("RAG_VECTOR_STORE_PATH", ".knowledge/rag"),
            top_k=int(os.environ.get("RAG_TOP_K", "5")),
        )

        indexer = RAGIndexer(config)
        indexer.load_index()
        return RAGSearcher(indexer), config.top_k
    except Exception as exc:
        logger.error("Failed to initialise RAG searcher: %s", exc)
        return None, 5


# ---------------------------------------------------------------------------
# MCP protocol helpers
# ---------------------------------------------------------------------------

def _read_message() -> dict | None:
    """Read a single Content-Length framed JSON-RPC message from stdin."""
    headers = {}
    while True:
        line = sys.stdin.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()

    length = int(headers.get("content-length", 0))
    if length <= 0:
        return None
    body = sys.stdin.read(length)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _write_message(msg: dict) -> None:
    """Write a Content-Length framed JSON-RPC message to stdout."""
    body = json.dumps(msg)
    sys.stdout.write(f"Content-Length: {len(body)}\r\n\r\n{body}")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_TOOLS = [
    {
        "name": "artifact_rag_search",
        "description": (
            "Semantic search over previously indexed pipeline artifacts. "
            "Returns the most relevant artifact chunks ranked by similarity."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    }
]


# ---------------------------------------------------------------------------
# Request handlers
# ---------------------------------------------------------------------------

_searcher = None
_default_top_k = 5


def _handle_initialize(request: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "result": {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "artifact-rag-search", "version": "1.0.0"},
            "capabilities": {"tools": {}},
        },
    }


def _handle_tools_list(request: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "result": {"tools": _TOOLS},
    }


def _handle_tools_call(request: dict) -> dict:
    params = request.get("params", {})
    tool_name = params.get("name", "")
    args = params.get("arguments", {})
    req_id = request.get("id")

    if tool_name != "artifact_rag_search":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
        }

    query = args.get("query", "")
    top_k = int(args.get("top_k", _default_top_k))

    if not query:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32602, "message": "query parameter is required"},
        }

    if _searcher is None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"results": [], "error": "RAG index not available"}),
                    }
                ]
            },
        }

    try:
        results = _searcher.search(query, top_k=top_k)
        payload = {
            "results": [r.to_dict() for r in results],
            "total": len(results),
        }
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(payload, indent=2)}]
            },
        }
    except Exception as exc:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32000, "message": str(exc)},
        }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    global _searcher, _default_top_k

    _searcher, _default_top_k = _build_searcher()

    dispatch = {
        "initialize": _handle_initialize,
        "tools/list": _handle_tools_list,
        "tools/call": _handle_tools_call,
    }

    while True:
        msg = _read_message()
        if msg is None:
            break

        method = msg.get("method", "")
        handler = dispatch.get(method)

        if handler:
            response = handler(msg)
        elif "id" in msg:
            response = {
                "jsonrpc": "2.0",
                "id": msg["id"],
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        else:
            # Notification — no response needed
            continue

        _write_message(response)


if __name__ == "__main__":
    main()
