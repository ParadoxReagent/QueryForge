from __future__ import annotations
import json, logging
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Dict, Optional
from pathlib import Path
import hashlib

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class SchemaCache:
    schema_path: Path
    source_dir: Path = field(
        default_factory=lambda: Path(__file__).parent / "defender_xdr_kql_schema_fuller"
    )
    _cache: Dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _cache_version: int = field(default=0, init=False, repr=False)
    _loaded: bool = field(default=False, init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _source_signature: Optional[str] = field(default=None, init=False, repr=False)

    def load_or_refresh(self) -> Dict[str, Any]:
        """Load schema data, preferring persisted cache when available."""

        with self._lock:
            signature = self._compute_source_signature()

            if self._loaded and signature == self._source_signature:
                logger.debug("Schema cache hit (version %s)", self._cache_version)
                return self._cache

            if signature is not None:
                disk = self._load_from_disk(signature)
                if disk is not None:
                    self._update_cache(
                        disk["schema"], disk.get("signature"), disk.get("version")
                    )
                    logger.info(
                        "Schema cache warmed from disk (%s tables)", len(self._cache)
                    )
                    return self._cache
            elif not self._loaded:
                disk = self._load_from_disk(None)
                if disk is not None:
                    self._update_cache(
                        disk["schema"], disk.get("signature"), disk.get("version")
                    )
                    logger.info(
                        "Schema cache restored from disk with unknown signature"
                    )
                    return self._cache

            schema = self._load_schema_from_json()
            self._update_cache(schema, signature)
            self._persist_to_disk()
            logger.info("Schema cache populated with %d tables", len(self._cache))
            return self._cache

    def refresh(self, force: bool = False) -> bool:
        """Reload the schema data when forced or when source files changed."""

        with self._lock:
            signature = self._compute_source_signature()

            if not force and self._loaded and signature == self._source_signature:
                logger.info(
                    "Schema refresh skipped; cache is current (version %s)",
                    self._cache_version,
                )
                return True

            if signature is None:
                raise FileNotFoundError(
                    f"Schema directory not found or empty: {self.source_dir}"
                )

            schema = self._load_schema_from_json()
            self._update_cache(schema, signature)
            self._persist_to_disk()
            logger.info("Schema cache refreshed with %d tables", len(self._cache))
            return True

    @property
    def version(self) -> int:
        """Monotonically increasing counter for the cached schema."""

        return self._cache_version

    def _update_cache(
        self, schema: Dict[str, Any], signature: Optional[str], version: Optional[int] = None
    ) -> None:
        self._cache = schema
        if version is None:
            self._cache_version += 1
        else:
            self._cache_version = version
        self._loaded = True
        self._source_signature = signature

    def _compute_source_signature(self) -> Optional[str]:
        """Return a hash of the schema source files to detect changes."""

        if not self.source_dir.exists():
            logger.warning("Schema directory not found: %s", self.source_dir)
            return None

        hasher = hashlib.blake2s(digest_size=16)
        found = False

        for path in sorted(self.source_dir.glob("*.json")):
            try:
                stats = path.stat()
            except OSError:
                continue

            hasher.update(path.name.encode("utf-8"))
            hasher.update(str(stats.st_mtime_ns).encode("utf-8"))
            hasher.update(str(stats.st_size).encode("utf-8"))
            found = True

        return hasher.hexdigest() if found else None

    def _load_from_disk(self, expected_signature: Optional[str]) -> Optional[Dict[str, Any]]:
        """Attempt to load the schema payload from the persisted cache file."""

        if not self.schema_path.exists():
            return None

        try:
            with self.schema_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to read schema cache %s: %s", self.schema_path, exc)
            return None

        signature = payload.get("signature")
        schema = payload.get("schema")
        version = payload.get("version")

        if expected_signature is not None and signature != expected_signature:
            logger.info("Schema cache on disk is stale; rebuilding from sources.")
            return None

        if not isinstance(schema, dict):
            logger.warning("Schema cache payload invalid; ignoring persisted cache.")
            return None

        if not isinstance(version, int):
            version = 0

        return {"schema": schema, "version": version, "signature": signature}

    def _persist_to_disk(self) -> None:
        """Persist the aggregated schema to disk for faster warm starts."""

        self.schema_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": self._cache,
            "signature": self._source_signature,
            "version": self._cache_version,
        }

        try:
            with self.schema_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to persist schema cache to %s: %s", self.schema_path, exc)

    def _load_schema_from_json(self) -> Dict[str, Any]:
        """Load schema from local JSON files in the Defender schema directory."""

        try:
            logger.info("Loading schema from local JSON files")

            if not self.source_dir.exists():
                raise FileNotFoundError(f"Schema directory not found: {self.source_dir}")

            index_path = self.source_dir / "schema_index.json"
            if not index_path.exists():
                raise FileNotFoundError(f"Schema index not found: {index_path}")

            with index_path.open("r", encoding="utf-8") as f:
                index_data = json.load(f)

            schema: Dict[str, Any] = {}

            for table_info in index_data["tables"]:
                table_name = table_info["name"]
                table_url = table_info["url"]
                has_columns_json = table_info.get("has_columns_json", False)

                if has_columns_json:
                    table_file = self.source_dir / f"{table_name}.json"
                    if table_file.exists():
                        try:
                            with table_file.open("r", encoding="utf-8") as f:
                                table_data = json.load(f)

                            schema[table_name] = {
                                "columns": table_data["columns"],
                                "url": table_data["source_url"],
                            }
                            logger.debug(
                                "Loaded schema for table '%s' with %d columns",
                                table_name,
                                len(table_data["columns"]),
                            )
                        except Exception as e:
                            logger.error(
                                "Failed to load table file for '%s': %s", table_name, e
                            )
                            continue
                    else:
                        logger.warning(
                            "Table file not found for '%s': %s", table_name, table_file
                        )
                else:
                    schema[table_name] = {"columns": [], "url": table_url}
                    logger.debug(
                        "Added table '%s' without columns (no JSON file)", table_name
                    )

            logger.info("Successfully loaded schema for %d tables", len(schema))
            return schema

        except Exception as e:
            logger.error(f"Schema loading failed: {e}")
            raise
