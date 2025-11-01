from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

from fastmcp import FastMCP

from unified_query_builder.s1.query_builder import (
    DEFAULT_BOOLEAN_OPERATOR as S1_DEFAULT_BOOLEAN_OPERATOR,
    DEFAULT_DATASET as S1_DEFAULT_DATASET,
    build_s1_query,
    infer_dataset,
)
from unified_query_builder.server_runtime import ServerRuntime
from unified_query_builder.server_tools_shared import attach_rag_context

logger = logging.getLogger(__name__)


def register_s1_tools(mcp: FastMCP, runtime: ServerRuntime) -> None:
    """Register SentinelOne S1QL tooling."""

    @mcp.tool
    def s1_list_datasets() -> Dict[str, Any]:
        """List SentinelOne datasets with display names and descriptions."""

        schema = runtime.s1_cache.load()
        datasets = runtime.s1_cache.datasets()
        items: List[Dict[str, Any]] = []
        for key in sorted(datasets.keys()):
            meta = datasets.get(key, {})
            if not isinstance(meta, dict):
                continue
            metadata = meta.get("metadata", {})
            description = metadata.get("description") if isinstance(metadata, dict) else None
            items.append(
                {
                    "key": key,
                    "name": meta.get("name", key),
                    "description": description,
                }
            )
        logger.info("Listing %d SentinelOne datasets", len(items))
        return {"datasets": items}

    @mcp.tool
    def s1_get_dataset_fields(dataset: str) -> Dict[str, Any]:
        """Return available fields for a SentinelOne dataset."""

        schema = runtime.s1_cache.load()
        dataset_key = infer_dataset(dataset, None, schema)
        if not dataset_key:
            return {"error": f"Unknown dataset '{dataset}'"}

        fields = runtime.s1_cache.list_fields(dataset_key)
        logger.info(
            "Resolved SentinelOne dataset %s (%s) with %d fields",
            dataset,
            dataset_key,
            len(fields),
        )
        return {
            "dataset": dataset_key,
            "name": schema.get("datasets", {}).get(dataset_key, {}).get("name", dataset_key),
            "fields": fields,
        }

    @mcp.tool
    def s1_build_query(
        dataset: Optional[str] = None,
        filters: Optional[List[Union[str, Dict[str, Any]]]] = None,
        natural_language_intent: Optional[str] = None,
        boolean_operator: str = S1_DEFAULT_BOOLEAN_OPERATOR,
    ) -> Dict[str, Any]:
        """Build a SentinelOne S1QL query from structured inputs or intent."""

        schema = runtime.s1_cache.load()
        if isinstance(filters, dict):
            filters = [filters]
        payload = {
            "dataset": dataset,
            "filters": filters,
            "natural_language_intent": natural_language_intent,
            "boolean_operator": boolean_operator,
        }
        try:
            query, metadata = build_s1_query(schema=schema, **payload)
            logger.info(
                "Built SentinelOne query for dataset=%s",
                metadata.get("dataset") or S1_DEFAULT_DATASET,
            )
            intent = payload.get("natural_language_intent")
            metadata = attach_rag_context(
                runtime=runtime,
                intent=intent,
                metadata=metadata,
                source_filter="s1",
                provider_label="SentinelOne",
                logger=logger,
            )
            return {"query": query, "metadata": metadata}
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to build SentinelOne query: %s", exc)
            return {"error": str(exc)}
