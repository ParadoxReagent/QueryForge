from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

from fastmcp import FastMCP

from unified_query_builder.cortex.query_builder import (
    DEFAULT_DATASET as CORTEX_DEFAULT_DATASET,
    QueryBuildError as CortexQueryBuildError,
    build_cortex_query,
)
from unified_query_builder.cortex.schema_loader import normalise_dataset
from unified_query_builder.server_runtime import ServerRuntime
from unified_query_builder.server_tools_shared import attach_rag_context

logger = logging.getLogger(__name__)


def register_cortex_tools(mcp: FastMCP, runtime: ServerRuntime) -> None:
    """Register Cortex XDR tooling with the MCP runtime."""

    @mcp.tool
    def cortex_list_datasets() -> Dict[str, Any]:
        """List Cortex XDR datasets with their descriptions."""

        datasets = runtime.cortex_cache.datasets()
        logger.info("Listing %d Cortex datasets", len(datasets))
        return {"datasets": datasets}

    @mcp.tool
    def cortex_get_dataset_fields(dataset: str) -> Dict[str, Any]:
        """Return available fields for a given Cortex XDR dataset."""

        datasets = runtime.cortex_cache.datasets()
        dataset_normalised, log_entries = normalise_dataset(dataset, datasets.keys())
        fields = runtime.cortex_cache.list_fields(dataset_normalised)
        logger.info(
            "Resolved Cortex dataset %s (%s) with %d fields",
            dataset,
            dataset_normalised,
            len(fields),
        )
        return {
            "dataset": dataset_normalised,
            "fields": fields,
            "normalisation": log_entries,
        }

    @mcp.tool
    def cortex_get_xql_functions() -> Dict[str, Any]:
        """Return documented XQL functions."""

        functions = runtime.cortex_cache.function_reference()
        logger.info("Returning %d Cortex XQL functions", len(functions))
        return {"functions": functions}

    @mcp.tool
    def cortex_get_operator_reference() -> Dict[str, Any]:
        """Return XQL operator reference grouped by category."""

        operators = runtime.cortex_cache.operator_reference()
        logger.info("Returning Cortex operator reference with categories: %s", list(operators.keys()))
        return {"operators": operators}

    @mcp.tool
    def cortex_get_enum_reference() -> Dict[str, Any]:
        """Return enumerated value mappings from the Cortex schema."""

        enums = runtime.cortex_cache.enum_values()
        logger.info("Returning Cortex enum reference for %d fields", len(enums))
        return {"enum_values": enums}

    @mcp.tool
    def cortex_get_field_groups() -> Dict[str, Any]:
        """Return logical field groupings to assist with projection selection."""

        groups = runtime.cortex_cache.field_groups()
        logger.info("Returning %d Cortex field groups", len(groups))
        return {"field_groups": groups}

    @mcp.tool
    def cortex_build_query(
        dataset: Optional[str] = None,
        filters: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None,
        fields: Optional[List[str]] = None,
        natural_language_intent: Optional[str] = None,
        time_range: Optional[Union[str, Dict[str, Any]]] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build a Cortex XDR XQL query from structured params or natural language."""

        dataset_name = dataset or CORTEX_DEFAULT_DATASET
        builder_kwargs = {
            "filters": filters,
            "fields": fields,
            "natural_language_intent": natural_language_intent,
            "time_range": time_range,
            "limit": limit,
        }
        builder_kwargs = {k: v for k, v in builder_kwargs.items() if v is not None}
        try:
            query, metadata = build_cortex_query(
                runtime.cortex_cache,
                dataset=dataset_name,
                **builder_kwargs,
            )
            logger.info("Built Cortex query for dataset=%s", metadata.get("dataset"))

            intent = builder_kwargs.get("natural_language_intent")
            metadata = attach_rag_context(
                runtime=runtime,
                intent=intent,
                metadata=metadata,
                source_filter="cortex",
                provider_label="Cortex",
                logger=logger,
            )

            return {"query": query, "metadata": metadata}
        except (CortexQueryBuildError, ValueError) as exc:
            logger.warning("Failed to build Cortex query: %s", exc)
            return {"error": str(exc)}
