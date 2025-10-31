# Architecture Documentation

## Overview

The MCP Security Query Builders project is a suite of Model Context Protocol (MCP) servers designed to transform natural-language security hunting intent into production-ready queries across four major security platforms: Microsoft Defender, Carbon Black Cloud, Cortex XDR, and SentinelOne.

## System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        MCP Client                           │
│            (Claude Desktop, Cline, Cursor, etc.)            │
└─────────────────────┬───────────────────────────────────────┘
                      │ MCP Protocol (stdio/SSE)
                      │
┌─────────────────────▼───────────────────────────────────────┐
│              Unified Query Builder Server                   │
│                    (FastMCP Entry Point)                    │
│                                                             │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌───────┐  │
│  │ KQL        │  │ CBC        │  │ Cortex     │  │ S1    │  │
│  │ Tools      │  │ Tools      │  │ Tools      │  │ Tools │  │
│  │ (8 tools)  │  │ (6 tools)  │  │ (7 tools)  │  │(3 tls)│  │
│  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └───┬───┘  │
│        │               │               │             │      │
│  ┌─────▼───────────────▼───────────────▼─────────────▼───┐  │
│  │            Unified RAG Service                        │  │
│  │         (FAISS/Rapidfuzz-based retrieval)             │  │
│  └────────────────────┬──────────────────────────────────┘  │
└───────────────────────┼─────────────────────────────────────┘
                        │
         ┌──────────────┴──────────────┐
         │                             │
    ┌────▼─────┐                  ┌────▼─────┐
    │  Schema  │                  │Embedding │
    │  Caches  │                  │  Cache   │
    │  (.json) │                  │ (.pkl)   │
    └──────────┘                  └──────────┘
```

### Component Architecture

#### 1. MCP Server Layer (FastMCP)

**Location**: `unified_query_builder/server.py`

The server layer is built on FastMCP and provides:
- 30+ MCP tools organized by platform namespace
- Pydantic-based parameter validation
- Automatic schema loading at startup
- SSE and stdio transport support
- Comprehensive logging

**Key Responsibilities**:
- Tool registration and routing
- Request validation
- Transport management
- Error handling and logging
- RAG service coordination

#### 2. Query Builder Layer

Each platform has a dedicated query builder module responsible for:

**KQL Builder** (`unified_query_builder/kql/query_builder.py`)
- Regex-based natural language parsing
- Table and column validation
- Time window extraction and normalization
- WHERE clause construction
- Query guardrails (SQL injection, limit enforcement)

**CBC Builder** (`unified_query_builder/cbc/query_builder.py`)
- Search-type aware query construction
- Boolean operator handling
- Field validation and suggestion
- Best practices enforcement

**Cortex Builder** (`unified_query_builder/cortex/query_builder.py`)
- XQL pipeline assembly
- Dataset validation
- Filter expression construction
- Field projection management
- Operator and enum validation

**S1 Builder** (`unified_query_builder/s1/query_builder.py`)
- Dataset inference from context
- S1QL query construction
- Boolean operator defaults
- Filter expression parsing

#### 3. Schema Management Layer

**Location**: `*/schema_loader.py` modules

Each platform maintains a schema cache that provides:

```python
class SchemaCache:
    def load() -> Dict[str, Any]
    def list_fields(dataset: str) -> List[Dict]
    def datasets() -> Dict[str, Any]
    # Platform-specific methods...
```

**Schema Sources**:
- **KQL**: JSON files in `defender_xdr_kql_schema_fuller/`
- **CBC**: Single JSON file `cbc_schema.json`
- **Cortex**: Single JSON file `cortex_xdr_schema.json`
- **S1**: Multiple JSON files in `s1_schemas/`

**Caching Strategy**:
- Schemas loaded once at startup
- Persistent cache in `.cache/` directory
- Force refresh capability for updates
- Version tracking for cache invalidation

#### 4. RAG (Retrieval-Augmented Generation) Layer

**Location**: `unified_query_builder/shared/rag.py`

The RAG service provides semantic search across all platform schemas.

**Architecture**:
```
Natural Language Query
        ↓
[Text Normalization]
        ↓
[Rapidfuzz Fuzzy Search]
        ↓
[Source Filtering (optional)]
        ↓
[Top-K Results with Scores]
```

**Components**:
- **Document Store**: In-memory collection of schema passages
- **Search Engine**: Rapidfuzz-based fuzzy string matching
- **Cache**: Persisted embeddings and document index
- **Source Filter**: Platform-specific filtering (cbc, kql, cortex, s1)

**Document Sources**:
- Table/dataset descriptions
- Field definitions and types
- Example queries
- Best practices
- Operator references

## Data Flow

### Query Building Flow

```
1. MCP Client sends tool request
        ↓
2. FastMCP validates parameters (Pydantic)
        ↓
3. Tool handler loads schema cache
        ↓
4. Query builder processes input:
   - Natural language parsing (if provided)
   - Parameter extraction
   - Schema validation
   - Guardrail checks
        ↓
5. RAG service retrieves context (if NL intent provided)
        ↓
6. Query + Metadata returned to client
        ↓
7. Client executes query on target platform
```

### Schema Loading Flow

```
Server Startup
    ↓
For each platform:
    ↓
[Check cache exists?]
    ├─ Yes → Load from cache
    └─ No → Load from source files
        ↓
[Validate schema structure]
        ↓
[Initialize RAG documents]
        ↓
[Store in memory cache]
```

### RAG Initialization Flow

```
Server Startup
    ↓
UnifiedRAGService.__init__()
    ↓
For each SchemaSource:
    ↓
[Load schema via source loader]
    ↓
[Build documents via document_builder]
    ↓
[Add to unified document store]
    ↓
[Check embedding cache]
    ├─ Valid cache → Load embeddings
    └─ No/stale → Build new index
        ↓
[Rapidfuzz index ready for search]
```

## Design Patterns

### 1. Schema Cache Pattern

Each platform implements a consistent schema caching interface:

```python
class SchemaCache:
    def __init__(self, schema_path: Path):
        self.schema_path = schema_path
        self._cache = None

    def load(self, force_refresh: bool = False) -> Dict:
        if self._cache is None or force_refresh:
            self._cache = self._load_schema()
        return self._cache
```

**Benefits**:
- Consistent interface across platforms
- Lazy loading support
- Force refresh capability
- Memory efficiency

### 2. Builder Pattern

Query builders follow a consistent pattern:

```python
def build_query(
    schema: Dict,
    natural_language_intent: Optional[str] = None,
    # Platform-specific params...
) -> Tuple[str, Dict[str, Any]]:
    """
    Returns: (query_string, metadata_dict)
    """
```

**Benefits**:
- Predictable API across platforms
- Metadata for audit trails
- Separation of concerns
- Testability

### 3. Plugin Architecture

The unified server uses a plugin-like architecture:

```python
SchemaSource(
    name="platform_name",
    schema_cache=cache_instance,
    loader=lambda cache, force: cache.load(force),
    document_builder=build_documents_fn,
    version_getter=get_version_fn,
)
```

**Benefits**:
- Easy to add new platforms
- Consistent integration pattern
- Independent platform development
- Shared RAG infrastructure

### 4. Transport Abstraction

FastMCP handles transport abstraction:

```python
transport = os.getenv("MCP_TRANSPORT", "stdio")
if transport == "sse":
    app = mcp.http_app(path="/sse", transport="sse")
    uvicorn.run(app, host=host, port=port)
else:
    mcp.run()  # stdio
```

**Benefits**:
- Environment-driven configuration
- Support for multiple client types
- Docker-friendly defaults
- No code changes needed

## Security Considerations

### Input Validation

1. **Pydantic Models**: All tool parameters validated via Pydantic schemas
2. **Schema Validation**: Table/column names checked against cached schemas
3. **Limit Enforcement**: Maximum result limits enforced per platform
4. **Pattern Validation**: IP addresses, hashes, domains validated with regex

### Query Guardrails

**SQL Injection Prevention**:
```python
# Check for dangerous patterns
DANGEROUS_PATTERNS = [
    r";\s*drop\s+table",
    r";\s*delete\s+from",
    r";\s*update\s+.*\s+set",
]
```

**Limit Enforcement**:
```python
MAX_LIMIT = 10000  # Platform-specific
if limit and limit > MAX_LIMIT:
    raise QueryBuildError(f"Limit exceeds maximum of {MAX_LIMIT}")
```

**Table/Column Validation**:
```python
if table not in schema:
    suggestions = fuzzy_match(table, schema.keys())
    raise ValueError(f"Unknown table. Did you mean: {suggestions}?")
```

### Schema Isolation

- Each platform's schema is isolated in memory
- No cross-platform schema pollution
- Independent version tracking
- Separate cache files

## Performance Characteristics

### Startup Performance

- **Cold Start**: 2-5 seconds (schema loading + RAG indexing)
- **Warm Start**: <1 second (cached schemas + embeddings)

### Query Performance

- **Simple Query**: <50ms (parameter validation + building)
- **NL Query with RAG**: 100-200ms (includes fuzzy search)
- **Schema Introspection**: <10ms (memory cache lookup)

### Memory Usage

- **Base Server**: ~100MB
- **Per Platform Schema**: 5-20MB
- **RAG Index**: 10-50MB (depends on document count)
- **Total**: ~150-300MB typical

### Caching Strategy

1. **Schema Caches**: Persistent JSON files
2. **RAG Embeddings**: Persistent pickle files
3. **Memory Caches**: In-process Python dicts
4. **Cache Invalidation**: Version-based + force refresh

## Extensibility

### Adding a New Platform

1. **Create schema loader**:
   ```python
   # platform/schema_loader.py
   class PlatformSchemaCache:
       def load(self) -> Dict: ...
       def list_fields(self, dataset: str) -> List: ...
   ```

2. **Create query builder**:
   ```python
   # platform/query_builder.py
   def build_query(schema: Dict, **kwargs) -> Tuple[str, Dict]: ...
   ```

3. **Create RAG document builder**:
   ```python
   # platform/schema_loader.py
   def build_platform_documents(schema: Dict) -> List[str]: ...
   ```

4. **Register with unified server**:
   ```python
   # server.py
   platform_cache = PlatformSchemaCache(...)

   rag_service.add_source(SchemaSource(
       name="platform",
       schema_cache=platform_cache,
       loader=lambda c, f: c.load(force_refresh=f),
       document_builder=build_platform_documents,
   ))

   @mcp.tool
   def platform_build_query(params: PlatformBuildQueryParams):
       # Implementation...
   ```

### Adding New Tools

```python
@mcp.tool
def platform_new_tool(params: NewToolParams) -> Dict[str, Any]:
    """Tool description for MCP clients."""
    # Implementation
    return {"result": ...}
```

Tools are automatically:
- Registered with MCP protocol
- Documented in tool list
- Validated via Pydantic
- Logged for audit trails

## Error Handling

### Error Categories

1. **Validation Errors**: Invalid parameters, unknown tables/fields
2. **Build Errors**: Query construction failures
3. **RAG Errors**: Search failures, cache issues
4. **Transport Errors**: Connection issues, timeout

### Error Response Pattern

```python
try:
    query, metadata = build_query(...)
    return {"query": query, "metadata": metadata}
except QueryBuildError as exc:
    logger.warning("Failed to build query: %s", exc)
    return {"error": str(exc)}
```

### Logging Strategy

- **INFO**: Successful operations, tool invocations
- **WARNING**: Recoverable errors, fallback actions
- **ERROR**: Unrecoverable errors, exceptions

## Testing Architecture

### Test Structure

```
tests/
├── test_unified_query_builder.py  # Integration tests
├── test_kql_builder.py            # KQL unit tests
├── test_cbc_query_builder.py      # CBC unit tests
├── test_cortex_query_builder.py   # Cortex unit tests
└── test_s1_query_builder.py       # S1 unit tests
```

### Test Coverage

- **Query Building**: Various input combinations
- **Natural Language Parsing**: Intent extraction
- **Schema Validation**: Table/column checking
- **Guardrails**: Limit enforcement, SQL injection
- **RAG Integration**: Context retrieval
- **Error Handling**: Invalid inputs, edge cases

### Testing Strategy

1. **Unit Tests**: Individual builder functions
2. **Integration Tests**: Full tool invocation flow
3. **Mock Data**: Test schemas and queries
4. **Regression Tests**: Known issues and fixes

## Deployment Architecture

### Docker Architecture

```
┌─────────────────────────────────────────┐
│         Docker Container                │
│                                         │
│  ┌────────────────────────────────────┐ │
│  │   Uvicorn ASGI Server              │ │
│  │   (SSE transport on port 8080)     │ │
│  └──────────────┬─────────────────────┘ │
│                 │                       │
│  ┌──────────────▼─────────────────────┐ │
│  │   FastMCP Application              │ │
│  │   (Unified Query Builder)          │ │
│  └──────────────┬─────────────────────┘ │
│                 │                       │
│  ┌──────────────▼─────────────────────┐ │
│  │   Named Volume: cache/             │ │
│  │   - Schema caches                  │ │
│  │   - RAG embeddings                 │ │
│  └────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

### Transport Options

1. **SSE (Server-Sent Events)**:
   - HTTP-based transport
   - Web-friendly
   - Docker default
   - Port: 8080 (configurable)

2. **stdio (Standard I/O)**:
   - Direct process communication
   - Terminal-friendly
   - Lower latency
   - Used for local development

### Environment Configuration

```bash
MCP_TRANSPORT=sse|stdio  # Transport type
MCP_HOST=0.0.0.0         # SSE bind address
MCP_PORT=8080            # SSE port
```

## Future Architecture Considerations

### Planned Enhancements

1. **CrowdStrike Integration**: Complete Humio builder
2. **Multi-Tenant Support**: Isolated caches per tenant
3. **Query History**: Persistent query audit log
4. **Advanced RAG**: Semantic embeddings with transformers
5. **Query Optimization**: Platform-specific optimizations
6. **Distributed Caching**: Redis/Memcached support

### Scalability Considerations

- **Horizontal Scaling**: Stateless server design supports load balancing
- **Cache Distribution**: Move from local to distributed cache
- **RAG Optimization**: GPU-accelerated embeddings for large schemas
- **Async Processing**: Background schema updates
