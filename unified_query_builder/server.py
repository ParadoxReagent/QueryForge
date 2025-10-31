from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal, Union

if __package__ is None or __package__ == "":  # pragma: no cover - direct script execution
    import sys

    sys.path.append(str(Path(__file__).resolve().parent.parent))

from unified_query_builder.cbc.schema_loader import CBCSchemaCache, normalise_search_type
from unified_query_builder.cbc.query_builder import (
    build_cbc_query,
    QueryBuildError as CBCQueryBuildError,
    DEFAULT_BOOLEAN_OPERATOR,
    MAX_LIMIT as CBC_MAX_LIMIT,
)
from unified_query_builder.cortex.schema_loader import CortexSchemaCache, normalise_dataset
from unified_query_builder.cortex.query_builder import (
    build_cortex_query,
    QueryBuildError as CortexQueryBuildError,
    DEFAULT_DATASET as CORTEX_DEFAULT_DATASET,
    MAX_LIMIT as CORTEX_MAX_LIMIT,
)
from unified_query_builder.kql.schema_loader import SchemaCache
from unified_query_builder.kql.query_builder import (
    build_kql_query,
    suggest_columns,
    example_queries_for_table,
)
from unified_query_builder.s1.schema_loader import S1SchemaCache
from unified_query_builder.s1.query_builder import (
    build_s1_query,
    DEFAULT_DATASET as S1_DEFAULT_DATASET,
    DEFAULT_BOOLEAN_OPERATOR as S1_DEFAULT_BOOLEAN_OPERATOR,
    infer_dataset,
)
from unified_query_builder.shared.rag import (
    UnifiedRAGService,
    SchemaSource,
    build_cbc_documents,
    build_cortex_documents,
    build_kql_documents,
    build_s1_documents,
)
from fastmcp import FastMCP


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


mcp = FastMCP(name="unified-query-builder")
DATA_DIR = Path(".cache")
DATA_DIR.mkdir(parents=True, exist_ok=True)

CBC_SCHEMA_FILE = Path(__file__).parent / "cbc" / "cbc_schema.json"
cbc_cache = CBCSchemaCache(CBC_SCHEMA_FILE, cache_dir=DATA_DIR)

CORTEX_SCHEMA_FILE = Path(__file__).parent / "cortex" / "cortex_xdr_schema.json"
cortex_cache = CortexSchemaCache(CORTEX_SCHEMA_FILE, cache_dir=DATA_DIR)

KQL_SCHEMA_DIR = Path(__file__).parent / "kql" / "defender_xdr_kql_schema_fuller"
KQL_SCHEMA_CACHE_FILE = DATA_DIR / "kql_schema_cache.json"
kql_cache = SchemaCache(schema_path=KQL_SCHEMA_CACHE_FILE)

S1_SCHEMA_DIR = Path(__file__).parent / "s1" / "s1_schemas"
s1_cache = S1SchemaCache(S1_SCHEMA_DIR, cache_dir=DATA_DIR)


def _cbc_version(cache: CBCSchemaCache) -> Optional[str]:
    try:
        data = cache.load()
    except Exception:  # pragma: no cover - defensive
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return str(version) if version else None


def _kql_version(cache: SchemaCache) -> Optional[int]:
    try:
        return cache.version
    except Exception:  # pragma: no cover - defensive
        return None


def _cortex_version(cache: CortexSchemaCache) -> Optional[str]:
    try:
        data = cache.load()
    except Exception:  # pragma: no cover - defensive
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return str(version) if version else None


def _load_kql_schema(cache: SchemaCache, force: bool = False) -> Dict[str, Any]:
    if force:
        cache.refresh(force=True)
    return cache.load_or_refresh()


rag_service = UnifiedRAGService(
    sources=[
        SchemaSource(
            name="cbc",
            schema_cache=cbc_cache,
            loader=lambda cache, force=False: cache.load(force_refresh=force),
            document_builder=build_cbc_documents,
            version_getter=_cbc_version,
        ),
        SchemaSource(
            name="kql",
            schema_cache=kql_cache,
            loader=_load_kql_schema,
            document_builder=build_kql_documents,
            version_getter=_kql_version,
        ),
        SchemaSource(
            name="cortex",
            schema_cache=cortex_cache,
            loader=lambda cache, force=False: cache.load(force_refresh=force),
            document_builder=build_cortex_documents,
            version_getter=_cortex_version,
        ),
        SchemaSource(
            name="s1",
            schema_cache=s1_cache,
            loader=lambda cache, force=False: cache.load(force_refresh=force),
            document_builder=build_s1_documents,
        ),
    ],
    cache_dir=DATA_DIR,
)

# Track initialization state with proper thread synchronization
import threading
_rag_init_event = threading.Event()
_rag_init_failed = False
_rag_init_error: Optional[str] = None


def _ensure_rag_initialized(timeout: float = 5.0) -> bool:
    """Ensure RAG service is initialized before use.
    
    Parameters
    ----------
    timeout:
        Maximum time in seconds to wait for initialization to complete.
    
    Returns
    -------
    bool
        True if RAG service is ready, False otherwise.
    """
    global _rag_init_failed
    
    # If already initialized, return immediately
    if _rag_init_event.is_set():
        if _rag_init_failed:
            return False
        return True
    
    # Wait briefly for initialization to complete
    logger.debug("RAG service not ready yet, waiting up to %.1fs...", timeout)
    if _rag_init_event.wait(timeout=timeout):
        # Initialization completed within timeout
        if _rag_init_failed:
            logger.debug("RAG initialization failed, continuing without RAG context")
            return False
        logger.debug("RAG service is now ready")
        return True
    
    # Timeout expired, RAG still initializing
    logger.debug("RAG service not ready after %.1fs, skipping context retrieval", timeout)
    return False


def _initialize_rag_background() -> None:
    """Initialize RAG service in background thread with timeout and error handling."""
    global _rag_init_failed, _rag_init_error
    
    import time
    start_time = time.time()
    
    try:
        logger.info("🚀 Starting background RAG initialization...")
        
        # Check schema files exist
        logger.info("🔍 Verifying schema files...")
        schema_checks = {
            "CBC": CBC_SCHEMA_FILE,
            "Cortex": CORTEX_SCHEMA_FILE,
            "KQL": KQL_SCHEMA_DIR,
            "S1": S1_SCHEMA_DIR,
        }
        
        for name, path in schema_checks.items():
            if not path.exists():
                logger.warning("⚠️ %s schema not found at %s", name, path)
            else:
                logger.info("✅ %s schema found", name)
        
        # Check cache directory
        if not DATA_DIR.exists():
            logger.info("🔄 Creating cache directory: %s", DATA_DIR)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
        
        if not DATA_DIR.is_dir() or not os.access(DATA_DIR, os.W_OK):
            raise RuntimeError(f"Cache directory {DATA_DIR} is not writable")
        
        logger.info("✅ Cache directory ready: %s", DATA_DIR)
        
        # Initialize RAG with timeout
        rag_service.ensure_index(timeout=120.0)
        
        # Signal successful initialization
        _rag_init_event.set()
        
        duration = time.time() - start_time
        # Log which retrieval method is being used
        if rag_service._embedding_service:
            logger.info(
                "✅ RAG service initialized with semantic embeddings (model=%s) in %.2fs",
                rag_service._embedding_model,
                duration,
            )
        else:
            logger.info("✅ RAG service initialized with RapidFuzz fallback in %.2fs", duration)
            
    except Exception as exc:
        duration = time.time() - start_time
        _rag_init_failed = True
        _rag_init_error = str(exc)
        # Signal completion even on failure so tools don't hang
        _rag_init_event.set()
        logger.error(
            "❌ RAG initialization failed after %.2fs: %s",
            duration,
            exc,
            exc_info=True,
        )
        logger.warning("⚠️ Tools will work without RAG context")


# ---------------------------------------------------------------------------
# CBC tools
# ---------------------------------------------------------------------------


@mcp.tool
def cbc_list_search_types() -> Dict[str, Any]:
    """List Carbon Black Cloud search types with their descriptions."""

    schema = cbc_cache.load()
    search_types = schema.get("search_types", {})
    logger.info("Listing %d CBC search types", len(search_types))
    return {"search_types": search_types}


@mcp.tool
def cbc_get_fields(search_type: str) -> Dict[str, Any]:
    """Return available fields for a given search type.
    
    Args:
        search_type: Carbon Black search type (process, binary, alert, threat)
    """

    schema = cbc_cache.load()
    search_type, log_entries = normalise_search_type(
        search_type, schema.get("search_types", {}).keys()
    )
    fields = cbc_cache.list_fields(search_type)
    logger.info(
        "Resolved CBC search type %s (%s) with %d fields",
        search_type,
        search_type,
        len(fields),
    )
    return {"search_type": search_type, "fields": fields, "normalisation": log_entries}


@mcp.tool
def cbc_get_operator_reference() -> Dict[str, Any]:
    """Return the logical, wildcard, and field operator reference."""

    operators = cbc_cache.operator_reference()
    logger.info("Returning CBC operator reference with categories: %s", list(operators.keys()))
    return {"operators": operators}


@mcp.tool
def cbc_get_best_practices() -> Dict[str, Any]:
    """Return documented query-building best practices."""

    best = cbc_cache.best_practices()
    logger.info(
        "Returning %s best practice entries", len(best) if isinstance(best, list) else "structured"
    )
    return {"best_practices": best}


@mcp.tool
def cbc_get_example_queries(category: Optional[str] = None) -> Dict[str, Any]:
    """Return example queries, optionally filtered by category.
    
    Args:
        category: Optional example category (process_search, binary_search, alert_search, etc.)
    """

    examples = cbc_cache.example_queries()
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
    limit: Optional[int] = None
) -> Dict[str, Any]:
    """Build a Carbon Black Cloud query from structured parameters or natural language.
    
    Args:
        search_type: Desired search type (defaults to process_search)
        terms: Pre-built expressions such as field:value pairs
        natural_language_intent: High-level description of what to search for
        boolean_operator: Boolean operator between expressions
        limit: Optional record limit hint (1-5000)
    """

    schema = cbc_cache.load()
    payload = {
        "search_type": search_type,
        "terms": terms,
        "natural_language_intent": natural_language_intent,
        "boolean_operator": boolean_operator,
        "limit": limit
    }
    try:
        query, metadata = build_cbc_query(schema, **payload)
        logger.info("Built CBC query for search_type=%s", metadata.get("search_type"))

        intent = payload.get("natural_language_intent")
        if intent and _ensure_rag_initialized():
            try:
                context = rag_service.search(intent, k=5, source_filter="cbc")
                if context:
                    metadata = {**metadata, "rag_context": context}
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("⚠️ Unable to attach CBC RAG context: %s", exc)
        elif intent:
            logger.debug("⏳ RAG not ready, skipping context retrieval for CBC query")

        return {"query": query, "metadata": metadata}
    except CBCQueryBuildError as exc:
        logger.warning("Failed to build CBC query: %s", exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Cortex tools
# ---------------------------------------------------------------------------


@mcp.tool
def cortex_list_datasets() -> Dict[str, Any]:
    """List Cortex XDR datasets with their descriptions."""

    datasets = cortex_cache.datasets()
    logger.info("Listing %d Cortex datasets", len(datasets))
    return {"datasets": datasets}


@mcp.tool
def cortex_get_dataset_fields(dataset: str) -> Dict[str, Any]:
    """Return available fields for a given Cortex XDR dataset.
    
    Args:
        dataset: Target XQL dataset name (e.g. xdr_data)
    """

    datasets = cortex_cache.datasets()
    dataset, log_entries = normalise_dataset(dataset, datasets.keys())
    fields = cortex_cache.list_fields(dataset)
    logger.info(
        "Resolved Cortex dataset %s (%s) with %d fields",
        dataset,
        dataset,
        len(fields),
    )
    return {"dataset": dataset, "fields": fields, "normalisation": log_entries}


@mcp.tool
def cortex_get_xql_functions() -> Dict[str, Any]:
    """Return documented XQL functions."""

    functions = cortex_cache.function_reference()
    logger.info("Returning %d Cortex XQL functions", len(functions))
    return {"functions": functions}


@mcp.tool
def cortex_get_operator_reference() -> Dict[str, Any]:
    """Return XQL operator reference grouped by category."""

    operators = cortex_cache.operator_reference()
    logger.info("Returning Cortex operator reference with categories: %s", list(operators.keys()))
    return {"operators": operators}


@mcp.tool
def cortex_get_enum_reference() -> Dict[str, Any]:
    """Return enumerated value mappings from the Cortex schema."""

    enums = cortex_cache.enum_values()
    logger.info("Returning Cortex enum reference for %d fields", len(enums))
    return {"enum_values": enums}


@mcp.tool
def cortex_get_field_groups() -> Dict[str, Any]:
    """Return logical field groupings to assist with projection selection."""

    groups = cortex_cache.field_groups()
    logger.info("Returning %d Cortex field groups", len(groups))
    return {"field_groups": groups}


@mcp.tool
def cortex_build_query(
    dataset: Optional[str] = None,
    filters: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None,
    fields: Optional[List[str]] = None,
    natural_language_intent: Optional[str] = None,
    time_range: Optional[Union[str, Dict[str, Any]]] = None,
    limit: Optional[int] = None
) -> Dict[str, Any]:
    """Build a Cortex XDR XQL query from structured params or natural language.
    
    Args:
        dataset: Dataset to query (defaults to xdr_data)
        filters: Structured filter definitions with field/operator/value
        fields: Optional list of fields for the fields stage
        natural_language_intent: Free-form description of the investigation goal
        time_range: Optional time range expression or structured definition
        limit: Optional limit override for the final stage (1-10000)
    """

    dataset_name = dataset or CORTEX_DEFAULT_DATASET
    builder_kwargs = {
        "filters": filters,
        "fields": fields,
        "natural_language_intent": natural_language_intent,
        "time_range": time_range,
        "limit": limit
    }
    builder_kwargs = {k: v for k, v in builder_kwargs.items() if v is not None}
    try:
        query, metadata = build_cortex_query(
            cortex_cache,
            dataset=dataset_name,
            **builder_kwargs,
        )
        logger.info("Built Cortex query for dataset=%s", metadata.get("dataset"))

        intent = builder_kwargs.get("natural_language_intent")
        if intent and _ensure_rag_initialized():
            try:
                context = rag_service.search(intent, k=5, source_filter="cortex")
                if context:
                    metadata = {**metadata, "rag_context": context}
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("⚠️ Unable to attach Cortex RAG context: %s", exc)
        elif intent:
            logger.debug("⏳ RAG not ready, skipping context retrieval for Cortex query")

        return {"query": query, "metadata": metadata}
    except (CortexQueryBuildError, ValueError) as exc:
        logger.warning("Failed to build Cortex query: %s", exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# KQL tools
# ---------------------------------------------------------------------------


@mcp.tool
def kql_list_tables(keyword: Optional[str] = None) -> Dict[str, Any]:
    """List available Advanced Hunting tables (optionally filter by keyword).
    
    Args:
        keyword: Substring filter
    """

    schema = kql_cache.load_or_refresh()
    names = list(schema.keys())
    if keyword:
        kw = keyword.lower()
        names = [n for n in names if kw in n.lower()]
    result = {"tables": sorted(names)}
    logger.info("Found %d KQL tables matching filter", len(names))
    return result


@mcp.tool
def kql_get_table_schema(table: str) -> Dict[str, Any]:
    """Return columns and docs URL for a given table.
    
    Args:
        table: Table name
    """

    schema = kql_cache.load_or_refresh()
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
    return {"table": table, "columns": schema[table]["columns"], "url": schema[table]["url"]}


@mcp.tool
def kql_suggest_columns(table: str, keyword: Optional[str] = None) -> Dict[str, Any]:
    """Suggest columns for a table, optionally filtered by keyword.
    
    Args:
        table: Table name
        keyword: Optional keyword filter
    """

    schema = kql_cache.load_or_refresh()
    suggestions = suggest_columns(schema, table, keyword)
    logger.info(
        "Found %d KQL column suggestions for table '%s'",
        len(suggestions),
        table,
    )
    return {"suggestions": suggestions}


@mcp.tool
def kql_examples(table: str) -> Dict[str, Any]:
    """Return example KQL for a given table.
    
    Args:
        table: Table name
    """

    schema = kql_cache.load_or_refresh()
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
    natural_language_intent: Optional[str] = None
) -> Dict[str, Any]:
    """Build a KQL query from structured params or natural-language intent.
    
    Args:
        table: Table name
        select: List of columns to select
        where: List of filter conditions
        time_window: Time window expression
        summarize: Summarize expression
        order_by: Order by expression
        limit: Result limit
        natural_language_intent: Natural language description
    """

    schema = kql_cache.load_or_refresh()
    payload = {
        "table": table,
        "select": select,
        "where": where,
        "time_window": time_window,
        "summarize": summarize,
        "order_by": order_by,
        "limit": limit,
        "natural_language_intent": natural_language_intent
    }
    try:
        kql, meta = build_kql_query(schema=schema, **payload)

        if payload.get("natural_language_intent"):
            if _ensure_rag_initialized():
                try:
                    context = rag_service.search(payload["natural_language_intent"], k=5, source_filter="kql")
                    if context:
                        meta = {**meta, "rag_context": context}
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("⚠️ Failed to retrieve KQL RAG context: %s", exc)
            else:
                logger.debug("⏳ RAG not ready, skipping context retrieval for KQL query")

        logger.info("Successfully built KQL query for table '%s'", meta.get("table", "unknown"))
        return {"kql": kql, "meta": meta}
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to build KQL query: %s", exc)
        raise


# ---------------------------------------------------------------------------
# SentinelOne tools
# ---------------------------------------------------------------------------


@mcp.tool
def s1_list_datasets() -> Dict[str, Any]:
    """List SentinelOne datasets with display names and descriptions."""

    schema = s1_cache.load()
    datasets = s1_cache.datasets()
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
    """Return available fields for a SentinelOne dataset.
    
    Args:
        dataset: Dataset name
    """

    schema = s1_cache.load()
    dataset_key = infer_dataset(dataset, None, schema)
    if not dataset_key:
        return {"error": f"Unknown dataset '{dataset}'"}

    fields = s1_cache.list_fields(dataset_key)
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
    boolean_operator: str = S1_DEFAULT_BOOLEAN_OPERATOR
) -> Dict[str, Any]:
    """Build a SentinelOne S1QL query from structured inputs or intent.
    
    Args:
        dataset: Dataset to target (defaults to processes)
        filters: Optional structured filters or raw expressions
        natural_language_intent: Free-form description of what to look for
        boolean_operator: Boolean operator used when combining expressions
    """

    schema = s1_cache.load()
    if isinstance(filters, dict):
        filters = [filters]
    payload = {
        "dataset": dataset,
        "filters": filters,
        "natural_language_intent": natural_language_intent,
        "boolean_operator": boolean_operator
    }
    try:
        query, metadata = build_s1_query(schema=schema, **payload)
        logger.info(
            "Built SentinelOne query for dataset=%s",
            metadata.get("dataset") or S1_DEFAULT_DATASET,
        )
        intent = payload.get("natural_language_intent")
        if intent and _ensure_rag_initialized():
            try:
                context = rag_service.search(intent, k=5, source_filter="s1")
                if context:
                    metadata = {**metadata, "rag_context": context}
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("⚠️ Unable to attach SentinelOne RAG context: %s", exc)
        elif intent:
            logger.debug("⏳ RAG not ready, skipping context retrieval for S1 query")
        return {"query": query, "metadata": metadata}
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to build SentinelOne query: %s", exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Shared tools
# ---------------------------------------------------------------------------


@mcp.tool
def retrieve_context(
    query: str,
    k: int = 5,
    query_type: Optional[Literal["cbc", "kql", "cortex"]] = None
) -> Dict[str, Any]:
    """Return relevant schema passages for a natural language query.
    
    Args:
        query: Query string
        k: Number of results to return (1-20)
        query_type: Optionally restrict to CBC, KQL, or Cortex schema entries
    """

    if not _ensure_rag_initialized():
        msg = "RAG service is not ready yet. Please try again in a moment."
        if _rag_init_failed:
            msg = f"RAG service initialization failed: {_rag_init_error or 'unknown error'}"
        logger.warning("⚠️ %s", msg)
        return {"error": msg, "matches": []}
    
    try:
        results = rag_service.search(query, k=k, source_filter=query_type)
        logger.info(
            "RAG returned %d matches for query with filter=%s",
            len(results),
            query_type,
        )
        return {"matches": results}
    except Exception as exc:
        logger.warning("⚠️ Failed to retrieve RAG context: %s", exc)
        return {"error": str(exc), "matches": []}


if __name__ == "__main__":
    import os
    import threading

    logger.info("🚀 Starting unified query builder MCP server")

    # Start RAG initialization in background thread
    init_thread = threading.Thread(target=_initialize_rag_background, daemon=True, name="RAG-Init")
    init_thread.start()
    logger.info("🔄 RAG initialization started in background thread")

    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    if transport == "sse":
        import uvicorn

        host = os.getenv("MCP_HOST", "0.0.0.0")
        port = int(os.getenv("MCP_PORT", "8080"))

        app = mcp.http_app(path="/sse", transport="sse")

        logger.info("🌐 Running MCP server on http://%s:%s/sse", host, port)
        uvicorn.run(app, host=host, port=port)
    else:
        logger.info("📡 Running MCP server in STDIO mode")
        mcp.run()
