# Migration Guide

Guide for migrating from standalone builders to the unified query builder.

## Table of Contents

- [Overview](#overview)
- [Why Migrate](#why-migrate)
- [Migration Checklist](#migration-checklist)
- [KQL Builder Migration](#kql-builder-migration)
- [Configuration Changes](#configuration-changes)
- [Tool Name Changes](#tool-name-changes)
- [Code Examples](#code-examples)
- [Breaking Changes](#breaking-changes)
- [Rollback Plan](#rollback-plan)

## Overview

The unified query builder consolidates all platform-specific builders (KQL, CBC, Cortex, S1) into a single MCP server with shared infrastructure.

**Timeline**: The standalone builders will continue to be maintained through 2024, with the unified builder becoming the recommended deployment in 2025.

## Why Migrate

### Benefits of Unified Builder

1. **Single Server Management**:
   - One Docker container vs. multiple
   - One configuration file vs. several
   - Simplified deployment and updates

2. **Shared RAG Service**:
   - Unified semantic search across all platforms
   - Better context retrieval
   - Single cache for all embeddings

3. **Consistent Tool Interface**:
   - Standardized parameter names
   - Uniform error handling
   - Predictable responses

4. **Better Performance**:
   - Shared schema caching
   - Optimized startup time
   - Reduced memory footprint

5. **Easier Multi-Platform Queries**:
   - Single connection for all platforms
   - Cross-platform investigations simplified
   - Unified logging and monitoring

### What Stays the Same

- All existing tool functionality
- Query building logic
- Schema definitions
- Response formats
- Docker deployment option
- SSE and stdio transports

## Migration Checklist

- [ ] Review current deployment (standalone vs Docker)
- [ ] Backup current configuration and cache
- [ ] Update MCP client configuration
- [ ] Update tool names in code (if automated)
- [ ] Test query building for all platforms used
- [ ] Verify RAG context retrieval
- [ ] Update monitoring/alerting if applicable
- [ ] Update documentation references
- [ ] Deploy unified builder
- [ ] Validate in production
- [ ] Decommission standalone builders

**Estimated Migration Time**: 1-2 hours for most deployments

## KQL Builder Migration

### Configuration Changes

**Before** (Standalone KQL Builder):
```json
{
  "cline.mcpServers": [
    {
      "name": "KQL Builder",
      "type": "sse",
      "url": "http://localhost:8083/sse"
    }
  ]
}
```

**After** (Unified Builder):
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

### Docker Deployment

**Before**:
```bash
cd kql_builder
docker compose up -d
# Server on port 8083
```

**After**:
```bash
cd unified_query_builder
docker compose up -d
# Server on port 8080
```

### Tool Names

Tool names remain the same, prefixed with platform:

| Standalone Tool | Unified Tool | Notes |
|----------------|--------------|-------|
| `list_tables` | `kql_list_tables` | Added `kql_` prefix |
| `get_table_schema` | `kql_get_table_schema` | Added `kql_` prefix |
| `suggest_columns` | `kql_suggest_columns` | Added `kql_` prefix |
| `build_query` | `kql_build_query` | Added `kql_` prefix |
| `examples` | `kql_examples` | Added `kql_` prefix |

### Example Code Migration

**Before** (Standalone):
```python
# List tables
result = mcp_client.call_tool("list_tables")

# Build query
result = mcp_client.call_tool("build_query", {
    "table": "DeviceProcessEvents",
    "where": ["FileName =~ \"powershell.exe\""],
    "time_window": "24h"
})
```

**After** (Unified):
```python
# List tables (note the kql_ prefix)
result = mcp_client.call_tool("kql_list_tables")

# Build query (note the kql_ prefix)
result = mcp_client.call_tool("kql_build_query", {
    "table": "DeviceProcessEvents",
    "where": ["FileName =~ \"powershell.exe\""],
    "time_window": "24h"
})
```

## Configuration Changes

### Environment Variables

**Before** (Standalone KQL):
```bash
MCP_TRANSPORT=sse
MCP_PORT=8083
```

**After** (Unified):
```bash
MCP_TRANSPORT=sse
MCP_PORT=8080
```

### Cache Directory

**Before**:
```
kql_builder/.cache/
├── kql_schema_cache.json
└── rag_embeddings.pkl
```

**After**:
```
unified_query_builder/.cache/
├── kql_schema_cache.json
├── unified_rag_documents.pkl
├── kql_version.txt
├── cbc_version.txt
├── cortex_version.txt
└── s1_version.txt
```

### Volume Mounts (Docker)

**Before**:
```yaml
volumes:
  - kql_mcp_cache:/app/.cache
```

**After**:
```yaml
volumes:
  - unified_query_builder_cache:/app/.cache
```

## Tool Name Changes

### Complete Mapping

#### KQL Tools

| Old Name | New Name |
|----------|----------|
| `list_tables` | `kql_list_tables` |
| `get_table_schema` | `kql_get_table_schema` |
| `suggest_columns` | `kql_suggest_columns` |
| `build_query` | `kql_build_query` |
| `examples` | `kql_examples` |
| `refresh_schema` | `kql_refresh_schema` |

#### New Tools (Available in Unified)

| Tool Name | Purpose |
|-----------|---------|
| `cbc_*` | Carbon Black Cloud tools |
| `cortex_*` | Cortex XDR tools |
| `s1_*` | SentinelOne tools |
| `retrieve_context` | Cross-platform RAG search |

### Finding Tools

List all available tools:

```python
# Via MCP protocol
tools = mcp_client.list_tools()

# Filter for specific platform
kql_tools = [t for t in tools if t['name'].startswith('kql_')]
```

## Code Examples

### Python SDK Migration

**Before**:
```python
from mcp import Client

client = Client("http://localhost:8083/sse")

# Build KQL query
result = client.call_tool("build_query", {
    "natural_language_intent": "Find PowerShell with encoded commands"
})

query = result["kql"]
```

**After**:
```python
from mcp import Client

client = Client("http://localhost:8080/sse")

# Build KQL query (with kql_ prefix)
result = client.call_tool("kql_build_query", {
    "natural_language_intent": "Find PowerShell with encoded commands"
})

query = result["kql"]

# Bonus: Now you can use other platforms too!
cbc_result = client.call_tool("cbc_build_query", {
    "natural_language_intent": "Find PowerShell with encoded commands"
})
```

### Automated Migration Script

```python
#!/usr/bin/env python3
"""Migrate tool calls from standalone to unified."""

import re
from pathlib import Path

TOOL_MAPPINGS = {
    "list_tables": "kql_list_tables",
    "get_table_schema": "kql_get_table_schema",
    "suggest_columns": "kql_suggest_columns",
    "build_query": "kql_build_query",
    "examples": "kql_examples",
    "refresh_schema": "kql_refresh_schema",
}

def migrate_file(file_path: Path):
    """Migrate tool names in a Python file."""
    content = file_path.read_text()
    modified = False

    for old_name, new_name in TOOL_MAPPINGS.items():
        # Match call_tool("old_name"
        pattern = rf'call_tool\s*\(\s*["\']({old_name})["\']\s*'
        if re.search(pattern, content):
            content = re.sub(
                pattern,
                f'call_tool("{new_name}"',
                content
            )
            modified = True
            print(f"  {file_path}: {old_name} -> {new_name}")

    if modified:
        file_path.write_text(content)
        return True
    return False

def main():
    """Migrate all Python files in current directory."""
    files_modified = 0

    for py_file in Path(".").rglob("*.py"):
        if migrate_file(py_file):
            files_modified += 1

    print(f"\nMigrated {files_modified} file(s)")

if __name__ == "__main__":
    main()
```

Usage:
```bash
python migrate_tool_names.py
```

### VS Code Cline Configuration

**Before**:
```json
{
  "cline.mcpServers": [
    {
      "name": "KQL Builder",
      "type": "sse",
      "url": "http://localhost:8083/sse"
    },
    {
      "name": "CBC Builder",
      "type": "sse",
      "url": "http://localhost:8082/sse"
    }
  ]
}
```

**After**:
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

### Claude Desktop Configuration

**Before** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "kql-builder": {
      "command": "python",
      "args": ["/path/to/kql_builder/server.py"]
    }
  }
}
```

**After**:
```json
{
  "mcpServers": {
    "unified-builder": {
      "command": "python",
      "args": ["/path/to/unified_query_builder/server.py"]
    }
  }
}
```

## Breaking Changes

### None Expected

The migration is designed to be non-breaking:

- ✅ All tool functionality preserved
- ✅ Parameter names unchanged
- ✅ Response formats identical
- ✅ Only tool names changed (prefixed)

### Potential Issues

1. **Hardcoded Tool Names**:
   - **Issue**: Code with hardcoded tool names needs updates
   - **Solution**: Use the migration script above

2. **Port Changes**:
   - **Issue**: Port changed from 8083 to 8080
   - **Solution**: Update connection URLs

3. **Cache Location**:
   - **Issue**: Cache in different directory
   - **Solution**: Let server rebuild cache (automatic)

4. **Docker Volume Names**:
   - **Issue**: Different volume name
   - **Solution**: Migrate data or start fresh

## Rollback Plan

If issues arise, you can easily rollback:

### Option 1: Keep Both Running

Run unified and standalone in parallel during transition:

```bash
# Standalone on 8083
cd kql_builder
docker compose up -d

# Unified on 8080
cd unified_query_builder
docker compose up -d
```

Point clients to old port (8083) if needed.

### Option 2: Restore Standalone

```bash
# Stop unified
cd unified_query_builder
docker compose down

# Start standalone
cd ../kql_builder
docker compose up -d
```

### Option 3: Restore From Backup

```bash
# Restore configuration
cp backup/config.json ~/.config/app/config.json

# Restore cache (if needed)
cp -r backup/.cache/ kql_builder/.cache/
```

## Migration Timeline

### Phase 1: Preparation (Week 1)

- [ ] Review current setup
- [ ] Backup configurations and caches
- [ ] Read migration guide
- [ ] Test unified builder in development

### Phase 2: Development/Staging (Week 2)

- [ ] Deploy unified builder to dev/staging
- [ ] Update tool names in code
- [ ] Run integration tests
- [ ] Validate query building
- [ ] Benchmark performance

### Phase 3: Production Migration (Week 3)

- [ ] Schedule maintenance window (if needed)
- [ ] Deploy unified builder to production
- [ ] Update client configurations
- [ ] Monitor for issues
- [ ] Keep standalone as fallback (1 week)

### Phase 4: Cleanup (Week 4)

- [ ] Verify no issues in production
- [ ] Decommission standalone builders
- [ ] Update documentation
- [ ] Remove old configurations

## Validation Checklist

After migration, verify:

- [ ] Server starts successfully
- [ ] All tools listed correctly
- [ ] Query building works for each platform
- [ ] RAG context retrieval works
- [ ] Cache is being used (check logs)
- [ ] Performance is acceptable
- [ ] Error handling works
- [ ] Monitoring/logging configured

### Validation Script

```python
#!/usr/bin/env python3
"""Validate unified builder deployment."""

from mcp import Client

def validate_deployment(url: str):
    """Validate unified builder is working."""
    client = Client(url)

    # Test 1: List tools
    print("Test 1: Listing tools...")
    tools = client.list_tools()
    kql_tools = [t for t in tools if t['name'].startswith('kql_')]
    print(f"  Found {len(kql_tools)} KQL tools ✓")

    # Test 2: List tables
    print("Test 2: Listing KQL tables...")
    result = client.call_tool("kql_list_tables")
    assert "tables" in result
    print(f"  Found {len(result['tables'])} tables ✓")

    # Test 3: Build query
    print("Test 3: Building query...")
    result = client.call_tool("kql_build_query", {
        "table": "DeviceProcessEvents",
        "select": ["Timestamp", "FileName"],
        "limit": 10
    })
    assert "kql" in result
    assert "error" not in result
    print(f"  Query built successfully ✓")

    # Test 4: RAG retrieval
    print("Test 4: Testing RAG...")
    result = client.call_tool("retrieve_context", {
        "query": "process events",
        "k": 3
    })
    assert "matches" in result
    print(f"  Found {len(result['matches'])} RAG matches ✓")

    print("\n✓ All validation tests passed!")

if __name__ == "__main__":
    validate_deployment("http://localhost:8080/sse")
```

## Getting Help

If you encounter issues during migration:

1. **Check Logs**:
   ```bash
   # Docker
   docker compose logs -f

   # Local
   python server.py
   ```

2. **Review Troubleshooting Guide**:
   - See [TROUBLESHOOTING.md](../TROUBLESHOOTING.md)

3. **Test Individual Components**:
   ```python
   # Test schema loading
   from unified_query_builder.kql.schema_loader import SchemaCache
   cache = SchemaCache(".cache/kql_schema_cache.json")
   schema = cache.load_or_refresh()
   print(f"Loaded {len(schema)} tables")
   ```

4. **Open an Issue**:
   - Include error messages
   - Describe steps to reproduce
   - Mention if migration or fresh install

## FAQ

**Q: Do I need to migrate all platforms at once?**

A: No. If you only use KQL, you only need to update KQL tool names. Other platforms won't affect you.

**Q: Will my existing queries break?**

A: No. The query building logic is identical. Only tool names changed.

**Q: Can I run both standalone and unified simultaneously?**

A: Yes, on different ports. Useful for testing.

**Q: Will cache be rebuilt?**

A: Schema cache will be reused if compatible. RAG cache will be rebuilt once (1-2 seconds).

**Q: What about my custom schemas?**

A: Custom schemas work the same way. Just copy them to the unified builder directory.

**Q: Can I migrate back to standalone?**

A: Yes, easily. See [Rollback Plan](#rollback-plan).

**Q: Are there performance differences?**

A: Unified builder may be slightly faster due to shared caching.

**Q: What about updates?**

A: Updates are simpler with unified builder - only one server to update.

**Q: Do I need to update my monitoring?**

A: Only the port number and possibly container name.

**Q: What about authentication?**

A: Authentication (if you've added it) works the same way.

---

For questions not covered here, see the [main README](../README.md) or open an issue on GitHub.
