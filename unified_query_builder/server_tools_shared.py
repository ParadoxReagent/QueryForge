from __future__ import annotations

import logging
from typing import Dict, Literal, Optional

from fastmcp import FastMCP

from unified_query_builder.server_runtime import ServerRuntime

logger = logging.getLogger(__name__)


def register_shared_tools(mcp: FastMCP, runtime: ServerRuntime) -> None:
    """Register shared helper tooling for schema retrieval."""

    @mcp.tool
    def retrieve_context(
        query: str,
        k: int = 5,
        query_type: Optional[Literal["cbc", "kql", "cortex", "s1"]] = None,
    ) -> Dict[str, object]:
        """Return relevant schema passages for a natural language query."""

        if not runtime.ensure_rag_initialized():
            msg = "RAG service is not ready yet. Please try again in a moment."
            if runtime.rag_init_failed:
                msg = f"RAG service initialization failed: {runtime.rag_init_error or 'unknown error'}"
            logger.warning("⚠️ %s", msg)
            return {"error": msg, "matches": []}

        try:
            results = runtime.rag_service.search(query, k=k, source_filter=query_type)
            logger.info(
                "RAG returned %d matches for query with filter=%s",
                len(results),
                query_type,
            )
            return {"matches": results}
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("⚠️ Failed to retrieve RAG context: %s", exc)
            return {"error": str(exc), "matches": []}
