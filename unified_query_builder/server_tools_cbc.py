from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from unified_query_builder.cbc.query_builder import (
    DEFAULT_BOOLEAN_OPERATOR,
    QueryBuildError as CBCQueryBuildError,
    build_cbc_query,
)
from unified_query_builder.cbc.schema_loader import normalise_search_type
from unified_query_builder.server_runtime import ServerRuntime
from unified_query_builder.server_tools_shared import attach_rag_context

logger = logging.getLogger(__name__)


def register_cbc_tools(mcp: FastMCP, runtime: ServerRuntime) -> None:
    """Register Carbon Black Cloud tooling with the MCP runtime."""

    @mcp.tool
    def cbc_list_search_types() -> Dict[str, Any]:
        """List Carbon Black Cloud search types with their descriptions."""

        schema = runtime.cbc_cache.load()
        search_types = schema.get("search_types", {})
        logger.info("Listing %d CBC search types", len(search_types))
        return {"search_types": search_types}

    @mcp.tool
    def cbc_get_fields(search_type: str) -> Dict[str, Any]:
        """Return available fields for a given search type."""

        schema = runtime.cbc_cache.load()
        search_type_normalised, log_entries = normalise_search_type(
            search_type,
            schema.get("search_types", {}).keys(),
        )
        fields = runtime.cbc_cache.list_fields(search_type_normalised)
        logger.info(
            "Resolved CBC search type %s (%s) with %d fields",
            search_type,
            search_type_normalised,
            len(fields),
        )
        return {
            "search_type": search_type_normalised,
            "fields": fields,
            "normalisation": log_entries,
        }

    @mcp.tool
    def cbc_get_operator_reference() -> Dict[str, Any]:
        """Return the logical, wildcard, and field operator reference."""

        operators = runtime.cbc_cache.operator_reference()
        logger.info("Returning CBC operator reference with categories: %s", list(operators.keys()))
        return {"operators": operators}

    @mcp.tool
    def cbc_get_best_practices() -> Dict[str, Any]:
        """Return documented query-building best practices."""

        best = runtime.cbc_cache.best_practices()
        logger.info(
            "Returning %s best practice entries",
            len(best) if isinstance(best, list) else "structured",
        )
        return {"best_practices": best}

    @mcp.tool
    def cbc_get_example_queries(category: Optional[str] = None) -> Dict[str, Any]:
        """Return example queries, optionally filtered by category."""

        examples = runtime.cbc_cache.example_queries()
        if category:
            key = category
            if key not in examples:
                available = ", ".join(sorted(examples.keys()))
                logger.warning("Unknown CBC example category %s", key)
                return {"error": f"Unknown category '{key}'. Available: {available}"}
            return {"category": key, "examples": examples[key]}
        return {"examples": examples}

    @mcp.tool
    def cbc_build_query(
        search_type: Optional[str] = None,
        terms: Optional[List[str]] = None,
        natural_language_intent: Optional[str] = None,
        boolean_operator: str = DEFAULT_BOOLEAN_OPERATOR,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build a Carbon Black Cloud query from structured parameters or natural language."""

        schema = runtime.cbc_cache.load()
        payload = {
            "search_type": search_type,
            "terms": terms,
            "natural_language_intent": natural_language_intent,
            "boolean_operator": boolean_operator,
            "limit": limit,
        }
        try:
            query, metadata = build_cbc_query(schema, **payload)
            logger.info("Built CBC query for search_type=%s", metadata.get("search_type"))

            intent = payload.get("natural_language_intent")
            metadata = attach_rag_context(
                runtime=runtime,
                intent=intent,
                metadata=metadata,
                source_filter="cbc",
                provider_label="CBC",
                logger=logger,
            )

            return {"query": query, "metadata": metadata}
        except CBCQueryBuildError as exc:
            logger.warning("Failed to build CBC query: %s", exc)
            return {"error": str(exc)}
