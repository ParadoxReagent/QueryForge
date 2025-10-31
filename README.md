# Model Context Protocol Security Query Builders

A suite of Model Context Protocol (MCP) servers and utilities that help security analysts transform natural-language intent into production-ready hunting queries across Microsoft Defender, VMware Carbon Black Cloud, Palo Alto Cortex XDR, and SentinelOne. The repo also ships a developer-friendly RAG layer, Docker-first deployment assets, and example clients that demonstrate both stdio and SSE transports.

## Table of Contents
- [Model Context Protocol Security Query Builders](#model-context-protocol-security-query-builders)
  - [Table of Contents](#table-of-contents)
  - [Highlights](#highlights)
  - [Documentation](#documentation)
    - [Core Documentation](#core-documentation)
    - [Advanced Topics](#advanced-topics)
    - [Contributing](#contributing)
  - [Repository Layout](#repository-layout)
  - [Service Capabilities](#service-capabilities)
    - [Unified Query Builder](#unified-query-builder)
    - [Microsoft Defender KQL Builder](#microsoft-defender-kql-builder)
    - [Carbon Black Cloud Builder](#carbon-black-cloud-builder)
    - [Cortex XDR Builder](#cortex-xdr-builder)
    - [SentinelOne Builder](#sentinelone-builder)
  - [Getting Started (Local Python)](#getting-started-local-python)
  - [Running with Docker](#running-with-docker)
    - [Unified Query Builder](#unified-query-builder-1)
    - [Running Multiple Builders Together](#running-multiple-builders-together)
  - [Connecting from VS Code Cline](#connecting-from-vs-code-cline)
  - [Testing](#testing)
  - [Additional Resources](#additional-resources)
    - [Quick Links](#quick-links)
    - [For Developers](#for-developers)
    - [For Users](#for-users)
  - [Support](#support)

## Highlights
- **Unified multi-platform service** that exposes Defender KQL, Carbon Black, Cortex XDR, and SentinelOne tooling from a single MCP endpoint with shared caching and retrieval-augmented generation (RAG).
- **Rapidfuzz-powered RAG index** that bootstraps at startup for low-latency context retrieval across all schemas.
- **Expanded SentinelOne dataset coverage** with dataset inference helpers, boolean operator defaults, and schema-aware query validation.
- **First-class SSE transport** across Docker images and example clients, enabling easy integration with web apps and MCP extensions.
- **Comprehensive regression tests** for parsers, schema caches, tool defaults, and guardrails across every builder.

## Documentation

Comprehensive documentation is available to help you get started and understand the system:

### Core Documentation
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** - System design, data flows, and component architecture
- **[API_REFERENCE.md](docs/API_REFERENCE.md)** - Complete API documentation for all 30+ MCP tools
- **[DEPLOYMENT.md](docs/DEPLOYMENT.md)** - Deployment guides for local, Docker, Kubernetes, and cloud platforms
- **[TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** - Common issues and solutions

### Advanced Topics
- **[docs/RAG_INTERNALS.md](docs/RAG_INTERNALS.md)** - Deep dive into the RAG system and semantic search
- **[docs/SCHEMA_MANAGEMENT.md](docs/SCHEMA_MANAGEMENT.md)** - Schema versioning, updates, and cache management
- **[docs/MIGRATION_GUIDE.md](docs/MIGRATION_GUIDE.md)** - Migrating from standalone to unified builder

### Contributing
- **[CONTRIBUTING.md](docs/CONTRIBUTING.md)** - Guidelines for contributing code, adding platforms, and development workflow

## Repository Layout
| Path | Description |
| --- | --- |
| `unified_query_builder/` | **Main unified server** - Modular MCP server bundling all platform builders with shared RAG enhancement. |
| `unified_query_builder/server.py` | FastMCP entry point with minimal orchestration logic. |
| `unified_query_builder/server_runtime.py` | Runtime coordination, schema management, and two-phase initialization. |
| `unified_query_builder/server_tools_*.py` | Modular tool registration files, one per platform (kql, cbc, cortex, s1, shared). |
| `unified_query_builder/cbc/` | Carbon Black Cloud schema loaders, query builders, and RAG document builders. |
| `unified_query_builder/cortex/` | Cortex XDR dataset loaders, pipeline builders, function/operator references, and metadata exporters. |
| `unified_query_builder/kql/` | Shared Defender KQL schema cache, query builder, and example query catalog. |
| `unified_query_builder/s1/` | SentinelOne schema loader, dataset inference helpers, and query builder utilities. |
| `unified_query_builder/shared/` | Shared components including unified RAG service and configuration. |
| `tests/` | Pytest suite covering builders, schema caching, RAG behavior, and transport guards. |
| `docs/` | Comprehensive documentation including architecture, API reference, deployment guides, and RAG internals. |

## Service Capabilities

### Unified Query Builder
The recommended entry point for production workflows. Key capabilities include:
- Single MCP registration that surfaces `kql_*`, `cbc_*`, `cortex_*`, and `s1_*` tool namespaces.
- Automatic schema hydration with persisted caches under `.cache/` and refresh toggles per platform.
- Unified RAG layer that merges documentation snippets from all platforms and supports forced re-indexing.
- Configurable SSE or stdio transport (Docker images default to SSE on port `8080`).
- SentinelOne dataset inference and boolean operator defaults for fast hunting query construction.

### Microsoft Defender KQL Builder
- `schema_scraper.py` keeps a cached Defender table/column inventory.
- `build_kql_query` safeguards table, column, and where-clause selection from natural-language prompts.
- Retrieval utilities (`rag.py`, `retrieve_context`) embed Microsoft Learn documentation.
- `query_logging.py` captures metadata for downstream audit trails.
- Docker Compose profile exposes SSE transport on port `8083` with persistent caches.

### Carbon Black Cloud Builder
- Search-type aware schema cache (`CBCSchemaCache`) with normalization helpers.
- Query builder that translates intent into Carbon Black search syntax with guardrails and auto-applied defaults.
- RAG document builder seeded with Carbon Black schema guides.
- Ships inside the unified server and can also be targeted directly over SSE when running all services via Docker.

### Cortex XDR Builder
- Dataset introspection helpers to list fields, operators, and enums from the Cortex schema cache.
- Query builder that assembles XQL pipelines, enforces dataset compatibility, and auto-tunes time ranges.
- RAG integration that surfaces Cortex documentation snippets for natural-language prompts.

### SentinelOne Builder
- Schema loader backed by the curated SentinelOne exports in `s1_builder/`.
- Query builder with dataset inference (`infer_dataset`) and boolean defaults to streamline query authoring.
- RAG document builder so SentinelOne fields and examples are searchable alongside other platforms.

## Getting Started (Local Python)
1. **Create a virtual environment** (Python 3.10+ recommended):
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
2. **Install dependencies** for the builder you need:
   ```bash
   # Unified multi-platform server
   cd unified_query_builder
   pip install -r requirements.txt

   # OR standalone Defender KQL server
   cd kql_builder
   pip install -r requirements.txt
   ```
3. **Run the MCP server**:
   ```bash
   python server.py
   ```
   - Both servers auto-create a `.cache/` directory to persist schema metadata and embeddings.
   - Set `MCP_TRANSPORT=stdio` to prefer stdio transport when integrating with terminal-first clients.

## Running with Docker
Every builder ships a ready-to-use Dockerfile and Compose configuration.

### Unified Query Builder
```bash
cd unified_query_builder
docker compose up --build -d
```
- Exposes SSE transport on `http://localhost:8080/sse`.
- Persists schema and embedding caches in the `unified_query_builder_cache` named volume.
- Health checks ensure the server is reachable before clients attach.

### Running Multiple Builders Together
If you want each builder available individually over SSE, start each Compose project in its directory. Ports default to:
- Unified Query Builder — `8080`
- Cortex XDR Builder — `8081`
- Carbon Black Builder — `8082`
- Defender KQL Builder — `8083`

Update the `MCP_PORT` or `ports` mapping in each `docker-compose.yml` if you need alternative ports.

## Connecting from VS Code Cline
1. **Install the Cline extension** from the VS Code marketplace (requires VS Code ≥ 1.87).
2. **Start the desired MCP server** locally or with Docker. For SSE, ensure it is reachable at `http://localhost:<port>/sse`.
3. **Open VS Code settings (JSON)** and add an entry to `cline.mcpServers`:
   ```json
   {
     "cline.mcpServers": [
       {
         "name": "Unified Query Builder",
         "type": "sse",
         "url": "http://localhost:8080/sse"
       }
     ]
   }
   ```
   - Use `type: "stdio"` with a `command` array instead if you prefer running the Python script directly (`"command": ["python", "server.py"]`).
   - You can add multiple entries for each builder if you are running the per-service containers.
4. **Reload VS Code** (or run “Cline: Reload MCP Servers”) so the extension discovers the new endpoints.
5. **Connect and explore tools** from the Cline side panel; the extension lists the MCP tools exposed by the server.

## Testing
From the repository root run:
```bash
pytest
```
Or target a specific module:
```bash
pytest tests/test_kql_builder.py
pytest tests/test_cbc_builder.py
pytest tests/test_cortex_builder.py
pytest tests/test_schema_cache.py
```

## Additional Resources

### Quick Links
- [System Architecture](docs/ARCHITECTURE.md#high-level-architecture) - Understand how components work together
- [API Quick Start](docs/API_REFERENCE.md#overview) - Jump into using the tools
- [Deployment Options](docs/DEPLOYMENT.md#deployment-options) - Choose the right deployment method
- [Common Issues](docs/TROUBLESHOOTING.md#common-issues) - Quick solutions to frequent problems

### For Developers
- [Adding a New Platform](docs/CONTRIBUTING.md#adding-a-new-platform) - Step-by-step guide
- [Testing Guidelines](docs/CONTRIBUTING.md#testing) - How to write and run tests
- [Code Style Guide](docs/CONTRIBUTING.md#code-style) - Formatting and conventions

### For Users
- [Tool Reference](docs/API_REFERENCE.md#table-of-contents) - Find the right tool for your task
- [Example Queries](docs/API_REFERENCE.md#examples) - Real-world usage examples
- [Migration Guide](docs/MIGRATION_GUIDE.md) - Upgrade to unified builder

## Support

- **Issues**: Report bugs or request features on [GitHub Issues](https://github.com/ParadoxReagent/MCPs/issues)
- **Documentation**: All docs are in this repository and kept up-to-date
- **Contributing**: See [CONTRIBUTING.md](docs/CONTRIBUTING.md) for guidelines
