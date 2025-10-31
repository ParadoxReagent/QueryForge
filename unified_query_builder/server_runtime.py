from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from unified_query_builder.cbc.schema_loader import CBCSchemaCache
from unified_query_builder.cortex.schema_loader import CortexSchemaCache
from unified_query_builder.kql.schema_loader import SchemaCache
from unified_query_builder.s1.schema_loader import S1SchemaCache
from unified_query_builder.shared.rag import (
    UnifiedRAGService,
    SchemaSource,
    build_cbc_documents,
    build_cortex_documents,
    build_kql_documents,
    build_s1_documents,
)


logger = logging.getLogger(__name__)


class ServerRuntime:
    """Encapsulates shared caches and background services for the MCP server."""

    def __init__(self, data_dir: Path | str = Path(".cache")) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        base_dir = Path(__file__).parent
        self.cbc_schema_file = base_dir / "cbc" / "cbc_schema.json"
        self.cortex_schema_file = base_dir / "cortex" / "cortex_xdr_schema.json"
        self.kql_schema_dir = base_dir / "kql" / "defender_xdr_kql_schema_fuller"
        self.kql_schema_cache_file = self.data_dir / "kql_schema_cache.json"
        self.s1_schema_dir = base_dir / "s1" / "s1_schemas"

        self.cbc_cache = CBCSchemaCache(self.cbc_schema_file, cache_dir=self.data_dir)
        self.cortex_cache = CortexSchemaCache(self.cortex_schema_file, cache_dir=self.data_dir)
        self.kql_cache = SchemaCache(schema_path=self.kql_schema_cache_file)
        self.s1_cache = S1SchemaCache(self.s1_schema_dir, cache_dir=self.data_dir)

        self.rag_service = UnifiedRAGService(
            sources=[
                SchemaSource(
                    name="cbc",
                    schema_cache=self.cbc_cache,
                    loader=lambda cache, force=False: cache.load(force_refresh=force),
                    document_builder=build_cbc_documents,
                    version_getter=self._cbc_version,
                ),
                SchemaSource(
                    name="kql",
                    schema_cache=self.kql_cache,
                    loader=self._load_kql_schema,
                    document_builder=build_kql_documents,
                    version_getter=self._kql_version,
                ),
                SchemaSource(
                    name="cortex",
                    schema_cache=self.cortex_cache,
                    loader=lambda cache, force=False: cache.load(force_refresh=force),
                    document_builder=build_cortex_documents,
                    version_getter=self._cortex_version,
                ),
                SchemaSource(
                    name="s1",
                    schema_cache=self.s1_cache,
                    loader=lambda cache, force=False: cache.load(force_refresh=force),
                    document_builder=build_s1_documents,
                ),
            ],
            cache_dir=self.data_dir,
        )

        self._rag_init_event = threading.Event()
        self._rag_init_failed = False
        self._rag_init_error: Optional[str] = None
        self._server_ready = False

    # ------------------------------------------------------------------
    # Properties exposing runtime state
    # ------------------------------------------------------------------
    @property
    def rag_init_failed(self) -> bool:
        return self._rag_init_failed

    @property
    def rag_init_error(self) -> Optional[str]:
        return self._rag_init_error

    @property
    def server_ready(self) -> bool:
        return self._server_ready

    # ------------------------------------------------------------------
    # Schema helpers
    # ------------------------------------------------------------------
    def _cbc_version(self, cache: CBCSchemaCache) -> Optional[str]:  # pragma: no cover - IO heavy
        try:
            data = cache.load()
        except Exception:  # pragma: no cover - defensive
            return None
        version = data.get("version") if isinstance(data, dict) else None
        return str(version) if version else None

    def _kql_version(self, cache: SchemaCache) -> Optional[int]:  # pragma: no cover - IO heavy
        try:
            return cache.version
        except Exception:  # pragma: no cover - defensive
            return None

    def _cortex_version(self, cache: CortexSchemaCache) -> Optional[str]:  # pragma: no cover - IO heavy
        try:
            data = cache.load()
        except Exception:  # pragma: no cover - defensive
            return None
        version = data.get("version") if isinstance(data, dict) else None
        return str(version) if version else None

    def _load_kql_schema(self, cache: SchemaCache, force: bool = False) -> Dict[str, Any]:
        if force:
            cache.refresh(force=True)
        return cache.load_or_refresh()

    # ------------------------------------------------------------------
    # Initialization routines
    # ------------------------------------------------------------------
    def initialize_critical_components(self) -> None:
        """Initialise schema caches and verify file system prerequisites."""

        try:
            logger.info("🔍 Initializing critical components...")

            schema_checks = {
                "CBC": self.cbc_schema_file,
                "Cortex": self.cortex_schema_file,
                "KQL": self.kql_schema_dir,
                "S1": self.s1_schema_dir,
            }

            for name, path in schema_checks.items():
                if not path.exists():
                    logger.warning("⚠️ %s schema not found at %s", name, path)
                else:
                    logger.info("✅ %s schema found", name)

            if not self.data_dir.exists():
                logger.info("🔄 Creating cache directory: %s", self.data_dir)
                self.data_dir.mkdir(parents=True, exist_ok=True)

            if not self.data_dir.is_dir() or not os.access(self.data_dir, os.W_OK):
                raise RuntimeError(f"Cache directory {self.data_dir} is not writable")

            logger.info("✅ Cache directory ready: %s", self.data_dir)

            logger.info("📚 Loading schemas...")
            try:
                self.cbc_cache.load()
                logger.info("✅ CBC schema loaded")
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("⚠️ Failed to load CBC schema: %s", exc)

            try:
                self.cortex_cache.load()
                logger.info("✅ Cortex schema loaded")
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("⚠️ Failed to load Cortex schema: %s", exc)

            try:
                self.kql_cache.load_or_refresh()
                logger.info("✅ KQL schema loaded")
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("⚠️ Failed to load KQL schema: %s", exc)

            try:
                self.s1_cache.load()
                logger.info("✅ S1 schema loaded")
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("⚠️ Failed to load S1 schema: %s", exc)

            self._server_ready = True
            logger.info("✅ Critical components initialized - server ready to accept requests")

        except Exception as exc:  # pragma: no cover - defensive
            logger.error("❌ Critical initialization failed: %s", exc, exc_info=True)
            self._server_ready = True
            logger.warning("⚠️ Starting server in degraded mode")

    def ensure_rag_initialized(self, timeout: float = 5.0) -> bool:
        """Ensure the RAG service is ready before use."""

        if self._rag_init_event.is_set():
            return not self._rag_init_failed

        logger.debug("RAG service not ready yet, waiting up to %.1fs...", timeout)
        if self._rag_init_event.wait(timeout=timeout):
            if self._rag_init_failed:
                logger.debug("RAG initialization failed, continuing without RAG context")
                return False
            logger.debug("RAG service is now ready")
            return True

        logger.debug("RAG service not ready after %.1fs, skipping context retrieval", timeout)
        return False

    def initialize_rag_background(self) -> None:
        """Perform RAG indexing in a background thread."""

        start_time = time.time()
        try:
            logger.info("🚀 Starting background RAG enhancement initialization...")
            time.sleep(0.5)
            self.rag_service.ensure_index(timeout=120.0)
            self._rag_init_event.set()

            duration = time.time() - start_time
            if getattr(self.rag_service, "_embedding_service", None):
                logger.info(
                    "✅ RAG enhancements ready with semantic embeddings (model=%s) in %.2fs",
                    getattr(self.rag_service, "_embedding_model", "unknown"),
                    duration,
                )
            else:
                logger.info("✅ RAG enhancements ready with RapidFuzz fallback in %.2fs", duration)

        except Exception as exc:  # pragma: no cover - defensive
            duration = time.time() - start_time
            self._rag_init_failed = True
            self._rag_init_error = str(exc)
            self._rag_init_event.set()
            logger.error(
                "❌ RAG enhancement initialization failed after %.2fs: %s",
                duration,
                exc,
                exc_info=True,
            )
            logger.warning("⚠️ Query builders will work without RAG context enhancements")


__all__ = ["ServerRuntime"]
