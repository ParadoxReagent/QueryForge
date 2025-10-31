from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CBCSchemaCache:
    """Load and cache the Carbon Black Cloud EDR schema file."""

    def __init__(self, schema_path: Path, cache_dir: Optional[Path] = None) -> None:
        self.schema_path = Path(schema_path)
        self._lock = threading.Lock()
        self._cache: Dict[str, Any] | None = None
        self._cache_version: int = 0
        self._source_signature: Optional[str] = None
        
        if cache_dir is None:
            cache_dir = Path(".cache")
        self.cache_file = cache_dir / "cbc_schema_cache.json"
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)

    def load(self, force_refresh: bool = False) -> Dict[str, Any]:
        with self._lock:
            if force_refresh or self._cache is None:
                signature = self._compute_signature()
                
                # Try to load from disk cache first
                if not force_refresh and signature:
                    cached = self._load_from_disk(signature)
                    if cached is not None:
                        self._cache = cached["schema"]
                        self._cache_version = cached.get("version", 0)
                        self._source_signature = signature
                        search_types = self._cache.get("search_types", {})
                        logger.info("CBC schema cache warmed from disk (%d search types)", len(search_types))
                        return self._cache
                
                # Load from source - try multi-file pattern first, fall back to monolithic
                payload = self._load_split_schema()
                if payload is None:
                    payload = self._load_monolithic_schema()
                
                self._cache = payload
                self._cache_version += 1
                self._source_signature = signature
                
                # Persist to disk
                self._persist_to_disk()
                
                search_types = payload.get("search_types", {})
                logger.info("Loaded CBC schema with %d search types", len(search_types))
            return self._cache
    
    def _compute_signature(self) -> Optional[str]:
        """Compute a signature based on the source file's modification time and size."""
        try:
            stats = self.schema_path.stat()
            hasher = hashlib.blake2s(digest_size=16)
            hasher.update(self.schema_path.name.encode("utf-8"))
            hasher.update(str(stats.st_mtime_ns).encode("utf-8"))
            hasher.update(str(stats.st_size).encode("utf-8"))
            return hasher.hexdigest()
        except OSError:
            return None
    
    def _load_from_disk(self, expected_signature: str) -> Optional[Dict[str, Any]]:
        """Load cached schema from disk if signature matches."""
        if not self.cache_file.exists():
            return None
        
        try:
            with self.cache_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            
            if data.get("signature") != expected_signature:
                logger.debug("CBC cache signature mismatch, will reload from source")
                return None
            
            if not isinstance(data.get("schema"), dict):
                return None
            
            return data
        except Exception as exc:
            logger.warning("Failed to load CBC cache from disk: %s", exc)
            return None
    
    def _persist_to_disk(self) -> None:
        """Save the current schema cache to disk."""
        try:
            data = {
                "schema": self._cache,
                "signature": self._source_signature,
                "version": self._cache_version,
            }
            with self.cache_file.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.warning("Failed to persist CBC cache to disk: %s", exc)
    
    def _load_split_schema(self) -> Optional[Dict[str, Any]]:
        """Load schema from multiple cbc_*.json files and merge them."""
        schema_dir = self.schema_path.parent
        cbc_files = sorted(schema_dir.glob("cbc_*.json"))
        
        # Exclude the monolithic cbc_schema.json file if it exists
        cbc_files = [f for f in cbc_files if f.name != "cbc_schema.json"]
        
        if not cbc_files:
            return None
        
        logger.info("Loading CBC schema from %d split files", len(cbc_files))
        merged: Dict[str, Any] = {}
        
        for file_path in cbc_files:
            try:
                with file_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                
                if not isinstance(data, dict):
                    continue
                
                payload = data.get("carbonblack_edr_query_schema")
                if not isinstance(payload, dict):
                    continue
                
                # Deep merge the payload into merged
                for key, value in payload.items():
                    if key not in merged:
                        merged[key] = value
                    elif isinstance(merged[key], dict) and isinstance(value, dict):
                        merged[key].update(value)
                    else:
                        merged[key] = value
                        
            except Exception as exc:
                logger.warning("Failed to load %s: %s", file_path.name, exc)
                continue
        
        if not merged:
            return None
        
        logger.info("Merged CBC schema from split files (keeping split field sets)")
        return merged
    
    def _load_monolithic_schema(self) -> Dict[str, Any]:
        """Load schema from single cbc_schema.json file."""
        logger.info("Loading Carbon Black Cloud schema from %s", self.schema_path.name)
        raw = self.schema_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Schema root must be a JSON object")
        payload = data.get("carbonblack_edr_query_schema")
        if not isinstance(payload, dict):
            raise ValueError("Missing 'carbonblack_edr_query_schema' root key")
        return payload

    # Convenience helpers -------------------------------------------------

    def search_types(self) -> Dict[str, Dict[str, Any]]:
        return dict(self.load().get("search_types", {}))

    def field_map_for(self, search_type: str) -> Dict[str, Dict[str, Any]]:
        payload = self.load()
        
        # For process_search, merge all process_*_fields into one dict
        if search_type == "process_search":
            merged_fields: Dict[str, Dict[str, Any]] = {}
            for key in payload.keys():
                if key.startswith("process_") and key.endswith("_fields"):
                    fields = payload.get(key, {})
                    if isinstance(fields, dict):
                        merged_fields.update(fields)
            return merged_fields
        
        # For other search types, use direct mapping
        mapping_key = {
            "binary_search": "binary_search_fields",
            "alert_search": "alert_search_fields",
            "threat_report_search": "threat_report_search_fields",
        }.get(search_type)

        if not mapping_key:
            return {}

        fields = payload.get(mapping_key, {})
        return dict(fields) if isinstance(fields, dict) else {}

    def list_fields(self, search_type: str) -> List[Dict[str, Any]]:
        fields = self.field_map_for(search_type)
        output: List[Dict[str, Any]] = []
        for name, meta in sorted(fields.items()):
            if isinstance(meta, dict):
                entry = {"name": name}
                entry.update(meta)
                output.append(entry)
        return output

    def operator_reference(self) -> Dict[str, Any]:
        payload = self.load()
        return payload.get("operators", {})

    def best_practices(self) -> List[str] | Dict[str, Any]:
        payload = self.load()
        best = payload.get("best_practices")
        return best if isinstance(best, (list, dict)) else []

    def example_queries(self) -> Dict[str, Any]:
        payload = self.load()
        examples = payload.get("example_queries", {})
        return examples if isinstance(examples, dict) else {}


def normalise_search_type(name: str | None, available: Iterable[str]) -> Tuple[str, List[str]]:
    """Return a valid search type and a record of the normalisation steps."""

    available_list = [st for st in available]
    log: List[str] = []

    if not name:
        if available_list:
            default = available_list[0]
            log.append(f"defaulted_to:{default}")
            return default, log
        raise ValueError("No search types available in schema")

    cleaned = name.strip().lower().replace(" ", "_")
    candidates = {
        "process": "process_search",
        "process_search": "process_search",
        "binary": "binary_search",
        "binary_search": "binary_search",
        "alert": "alert_search",
        "alert_search": "alert_search",
        "alerts": "alert_search",
        "threat": "threat_report_search",
        "threat_report": "threat_report_search",
        "threat_report_search": "threat_report_search",
        "report": "threat_report_search",
    }

    resolved = candidates.get(cleaned, cleaned)
    if resolved in available_list:
        if resolved != name:
            log.append(f"normalised_from:{name}->{resolved}")
        return resolved, log

    # Attempt fuzzy fallback by prefix
    for candidate in available_list:
        if candidate.startswith(resolved):
            log.append(f"prefix_matched:{candidate}")
            return candidate, log

    raise ValueError(f"Unknown search type '{name}'. Valid options: {', '.join(available_list)}")
