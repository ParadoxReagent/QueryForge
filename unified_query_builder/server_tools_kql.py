from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from unified_query_builder.kql.query_builder import (
    build_kql_query,
    example_queries_for_table,
    suggest_columns,
)
from unified_query_builder.server_runtime import ServerRuntime
from unified_query_builder.server_tools_shared import attach_rag_context

logger = logging.getLogger(__name__)


def register_kql_tools(mcp: FastMCP, runtime: ServerRuntime) -> None:
    """Register Microsoft 365 Defender KQL tooling."""

    @mcp.tool
    def kql_list_tables(keyword: Optional[str] = None) -> Dict[str, Any]:
        """List available Advanced Hunting tables (optionally filter by keyword)."""

        schema = runtime.kql_cache.load_or_refresh()
        names = list(schema.keys())
        if keyword:
            kw = keyword.lower()
            names = [name for name in names if kw in name.lower()]
        logger.info("Found %d KQL tables matching filter", len(names))
        return {"tables": sorted(names)}

    @mcp.tool
    def kql_get_table_schema(table: str) -> Dict[str, Any]:
        """Return columns and docs URL for a given table."""

        schema = runtime.kql_cache.load_or_refresh()
        if table not in schema:
            try:
                from rapidfuzz import process

                choice, score, _ = process.extractOne(table, schema.keys())
                logger.warning(
                    "KQL table '%s' not found, suggesting '%s' with score %s",
                    table,
                    choice,
                    score,
                )
                return {"error": f"Unknown table '{table}'. Did you mean '{choice}' (score {score})?"}
            except ImportError:
                logger.error("rapidfuzz not available for fuzzy matching")
                return {"error": f"Unknown table '{table}'"}

        logger.info(
            "Retrieved schema for KQL table '%s' with %d columns",
            table,
            len(schema[table]["columns"]),
        )
        return {
            "table": table,
            "columns": schema[table]["columns"],
            "url": schema[table]["url"],
        }

    @mcp.tool
    def kql_suggest_columns(table: str, keyword: Optional[str] = None) -> Dict[str, Any]:
        """Suggest columns for a table, optionally filtered by keyword."""

        schema = runtime.kql_cache.load_or_refresh()
        suggestions = suggest_columns(schema, table, keyword)
        logger.info(
            "Found %d KQL column suggestions for table '%s'",
            len(suggestions),
            table,
        )
        return {"suggestions": suggestions}

    @mcp.tool
    def kql_examples(table: str) -> Dict[str, Any]:
        """Return example KQL for a given table."""

        schema = runtime.kql_cache.load_or_refresh()
        examples = example_queries_for_table(schema, table)
        logger.info("Generated %d KQL examples for table '%s'", len(examples), table)
        return {"examples": examples}

    @mcp.tool
    def kql_build_query(
        table: Optional[str] = None,
        select: Optional[List[str]] = None,
        where: Optional[List[str]] = None,
        time_window: Optional[str] = None,
        summarize: Optional[str] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        natural_language_intent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a KQL query from structured params or natural-language intent."""

        schema = runtime.kql_cache.load_or_refresh()
        payload = {
            "table": table,
            "select": select,
            "where": where,
            "time_window": time_window,
            "summarize": summarize,
            "order_by": order_by,
            "limit": limit,
            "natural_language_intent": natural_language_intent,
        }
        try:
            kql, meta = build_kql_query(schema=schema, **payload)

            intent = payload.get("natural_language_intent")
            meta = attach_rag_context(
                runtime=runtime,
                intent=intent,
                metadata=meta,
                source_filter="kql",
                provider_label="KQL",
                logger=logger,
            )

            logger.info(
                "Successfully built KQL query for table '%s'",
                meta.get("table", "unknown"),
            )
            return {"kql": kql, "meta": meta}
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to build KQL query: %s", exc)
            raise
