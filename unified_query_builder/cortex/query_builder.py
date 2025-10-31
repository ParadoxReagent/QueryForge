from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Sequence, Tuple

try:
    from .schema_loader import CortexSchemaCache, normalise_dataset
except ImportError:  # pragma: no cover - fallback when package layout unavailable
    from schema_loader import CortexSchemaCache, normalise_dataset  # type: ignore

DEFAULT_DATASET = "xdr_data"
DEFAULT_LIMIT = 100
MAX_LIMIT = 10000

_MD5_RE = re.compile(r"\b[a-fA-F0-9]{32}\b")
_SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_PROCESS_RE = re.compile(r"\b([A-Za-z0-9_\-]+\.exe)\b", re.IGNORECASE)
_FILE_PATH_RE = re.compile(
    r"((?:[A-Za-z]:\\\\?|\\\\)\\?(?:[^\\/:\n]+\\\\)*[^\\/:\n]+|/(?:[^/\s]+/)*[^/\s]+)"
)
_TIME_RANGE_RE = re.compile(
    r"(?:last|past)\s+(?:(\d+)\s+)?(minute|hour|day|week|month)s?",
    re.IGNORECASE,
)
_HOST_PHRASE_RE = re.compile(
    r"(?:host|hostname|server|agent)\s+(?:named\s+)?['\"]?([A-Za-z0-9_.-]{2,})['\"]?",
    re.IGNORECASE,
)

_KNOWN_PROCESS_ALIASES = {
    "powershell": "powershell.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "wmic": "wmic.exe",
    "mshta": "mshta.exe",
    "cscript": "cscript.exe",
    "wscript": "wscript.exe",
}

_HOST_KEYWORDS = {"host", "hostname", "agent", "endpoint", "machine", "server"}


class QueryBuildError(ValueError):
    """Raised when a Cortex XDR XQL query cannot be constructed."""


def _collect_fields(field_map: Dict[str, Dict[str, Any]]) -> Iterable[str]:
    return field_map.keys()


def _derive_default_fields(
    field_groups: Dict[str, Any],
    field_map: Dict[str, Dict[str, Any]],
) -> List[str]:
    """Derive a sensible default field selection from schema metadata."""

    if not field_groups or not field_map:
        return []

    preferred_groups = [
        "system_fields",
        "event_fields",
        "actor_fields",
        "action_fields",
        "agent_fields",
        "auth_fields",
        "dst_fields",
    ]

    seen: set[str] = set()
    results: List[str] = []

    def append_field(name: str) -> None:
        if name in field_map and name not in seen:
            seen.add(name)
            results.append(name)

    for group in preferred_groups:
        meta = field_groups.get(group)
        if not isinstance(meta, dict):
            continue
        raw_fields = meta.get("key_fields") or meta.get("fields")
        if isinstance(raw_fields, list):
            for entry in raw_fields:
                if isinstance(entry, str):
                    append_field(entry)

    if not results:
        for name, meta in field_map.items():
            if isinstance(meta, dict) and meta.get("default_field"):
                append_field(name)

    if not results:
        for candidate in ["_time", "agent_hostname", "actor_process_image_name", "action_process_image_name"]:
            append_field(candidate)

    return results[:6]


def _field_if_available(candidates: Sequence[str], available_fields: Iterable[str]) -> str | None:
    available = set(available_fields)
    for candidate in candidates:
        if candidate in available:
            return candidate
    return None


def _format_literal(value: str) -> str:
    if value.startswith("'") and value.endswith("'"):
        return value
    escaped = value.replace("'", "\\'")
    return f"'{escaped}'"


def _format_value(value: Any) -> str:
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return "''"
        if trimmed.startswith("ENUM."):
            return trimmed
        if trimmed.startswith("interval "):
            return trimmed
        if "(" in trimmed or trimmed.endswith("()") or "current_time()" in trimmed:
            return trimmed
        if trimmed[0] in {"'", '"'} and trimmed[-1] == trimmed[0]:
            if trimmed[0] == '"':
                trimmed = trimmed[1:-1].replace("'", "\\'")
                return f"'{trimmed}'"
            return trimmed
        return _format_literal(trimmed)
    return _format_literal(str(value))


def _format_filter(field: str, operator: str, value: Any) -> str:
    op = operator.strip()
    if op.lower() == "in" and isinstance(value, (list, tuple, set)):
        formatted = ", ".join(_format_value(v) for v in value)
        return f"{field} in ({formatted})"
    formatted_value = _format_value(value)
    return f"{field} {op} {formatted_value}"


def _extract_time_filters(intent: str) -> Tuple[List[str], List[Tuple[int, int]], List[Dict[str, Any]]]:
    filters: List[str] = []
    spans: List[Tuple[int, int]] = []
    metadata: List[Dict[str, Any]] = []
    for match in _TIME_RANGE_RE.finditer(intent):
        quantity = match.group(1) or "1"
        unit = match.group(2).lower()
        expression = f"_time > current_time() - interval '{quantity} {unit}'"
        filters.append(expression)
        spans.append(match.span())
        metadata.append({"type": "time_range", "value": f"last {quantity} {unit}"})
    return filters, spans, metadata


def _extract_natural_language_filters(
    intent: str,
    field_map: Dict[str, Dict[str, Any]],
) -> Tuple[List[str], List[Tuple[int, int]], List[Dict[str, Any]]]:
    expressions: List[str] = []
    spans: List[Tuple[int, int]] = []
    metadata: List[Dict[str, Any]] = []
    available_fields = list(_collect_fields(field_map))

    pattern_definitions = [
        ("md5", _MD5_RE, ["action_file_md5", "action_process_image_md5"]),
        ("sha256", _SHA256_RE, ["action_file_sha256", "action_process_image_sha256"]),
        (
            "ipv4",
            _IPV4_RE,
            ["action_local_ip", "action_remote_ip", "src_ip", "dst_ip"],
        ),
    ]

    for label, regex, candidates in pattern_definitions:
        for match in regex.finditer(intent):
            field = _field_if_available(candidates, available_fields)
            if not field:
                continue
            value = match.group(0)
            expression = _format_filter(field, "=", value)
            expressions.append(expression)
            spans.append(match.span())
            metadata.append({"type": label, "field": field, "value": value})

    # Process names
    for match in _PROCESS_RE.finditer(intent):
        field = _field_if_available(["actor_process_image_name", "action_file_name"], available_fields)
        if not field:
            continue
        value = match.group(1)
        expression = _format_filter(field, "=", value.lower())
        expressions.append(expression)
        spans.append(match.span())
        metadata.append({"type": "process_name", "field": field, "value": value.lower()})

    # File paths
    for match in _FILE_PATH_RE.finditer(intent):
        field = _field_if_available(["action_file_path", "actor_process_image_path"], available_fields)
        if not field:
            continue
        value = match.group(1)
        expression = _format_filter(field, "=", value)
        expressions.append(expression)
        spans.append(match.span())
        metadata.append({"type": "file_path", "field": field, "value": value})

    # Hostname phrases
    for match in _HOST_PHRASE_RE.finditer(intent):
        field = _field_if_available(
            ["agent_hostname", "dest_agent_hostname", "src_agent_hostname"],
            available_fields,
        )
        if not field:
            continue
        value = match.group(1)
        expression = _format_filter(field, "contains", value.lower())
        expressions.append(expression)
        spans.append(match.span())
        metadata.append({"type": "hostname", "field": field, "value": value})

    return expressions, spans, metadata


def _extract_keywords(intent: str, spans: List[Tuple[int, int]]) -> List[str]:
    if not intent:
        return []
    chars = list(intent)
    for start, end in spans:
        for idx in range(start, min(end, len(chars))):
            chars[idx] = " "
    residual = re.sub(r"\s+", " ", "".join(chars)).strip()
    if not residual:
        return []
    keywords: List[str] = []
    for token in re.split(r"[;,]", residual):
        token = token.strip()
        if not token:
            continue
        if token.lower() in _HOST_KEYWORDS:
            continue
        keywords.extend(re.findall(r"[A-Za-z0-9_.-]+", token))
    return keywords


def _resolve_process_aliases(keywords: Iterable[str]) -> List[str]:
    resolved: List[str] = []
    for keyword in keywords:
        lowered = keyword.lower()
        if lowered in _KNOWN_PROCESS_ALIASES:
            resolved.append(_KNOWN_PROCESS_ALIASES[lowered])
        elif lowered.endswith(".exe"):
            resolved.append(lowered)
    return resolved


def build_cortex_query(
    schema: Dict[str, Any] | CortexSchemaCache,
    dataset: str = DEFAULT_DATASET,
    filters: Sequence[Dict[str, Any]] | Dict[str, Any] | None = None,
    fields: Sequence[str] | None = None,
    natural_language_intent: str | None = None,
    time_range: str | Dict[str, Any] | None = None,
    limit: int | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """Construct an XQL query using the provided parameters and heuristics."""

    payload: Dict[str, Any]
    field_map: Dict[str, Dict[str, Any]]

    dataset_meta: Dict[str, Any] | None = None

    field_groups: Dict[str, Any] = {}

    if isinstance(schema, CortexSchemaCache):
        payload = schema.load()
        available_datasets = schema.datasets()
        field_map = schema.field_map_for(dataset)
        dataset_meta = available_datasets.get(dataset) if isinstance(available_datasets, dict) else None
        field_groups = schema.field_groups()
    else:
        payload = schema
        available_datasets = payload.get("datasets", {}) if isinstance(payload, dict) else {}
        field_map = {}
        mapping = CortexSchemaCache.DATASET_FIELD_MAP
        if isinstance(payload, dict) and dataset in mapping:
            raw_fields = payload.get(mapping[dataset], {})
            if isinstance(raw_fields, dict):
                field_map = raw_fields
        if isinstance(available_datasets, dict):
            dataset_meta = available_datasets.get(dataset)
        if isinstance(payload, dict):
            raw_groups = payload.get("field_groups", {})
            field_groups = raw_groups if isinstance(raw_groups, dict) else {}

    available_names: List[str] = []
    if isinstance(available_datasets, dict):
        available_names.extend(available_datasets.keys())

    dataset_mapping_keys: Iterable[str]
    if isinstance(schema, CortexSchemaCache):
        dataset_mapping_keys = schema.DATASET_FIELD_MAP.keys()
    else:
        dataset_mapping_keys = CortexSchemaCache.DATASET_FIELD_MAP.keys()

    for name in dataset_mapping_keys:
        if name not in available_names:
            available_names.append(name)

    chosen_dataset, normalisation_log = normalise_dataset(dataset, available_names)
    if isinstance(schema, CortexSchemaCache):
        field_map = schema.field_map_for(chosen_dataset)
        datasets_info = schema.datasets()
        if isinstance(datasets_info, dict):
            dataset_meta = datasets_info.get(chosen_dataset)
        field_groups = schema.field_groups()
    else:
        mapping = CortexSchemaCache.DATASET_FIELD_MAP
        field_map = {}
        if isinstance(payload, dict):
            mapped_key = mapping.get(chosen_dataset)
            raw_fields = payload.get(mapped_key, {}) if mapped_key else {}
            if isinstance(raw_fields, dict):
                field_map = raw_fields
        if isinstance(available_datasets, dict):
            dataset_meta = available_datasets.get(chosen_dataset)
        if isinstance(payload, dict):
            raw_groups = payload.get("field_groups", {})
            field_groups = raw_groups if isinstance(raw_groups, dict) else {}

    stages: List[str] = [f"dataset = {chosen_dataset}"]
    recognised: List[Dict[str, Any]] = []
    selected_fields: List[str] = []

    def add_filter_stage(expression: str, meta: Dict[str, Any]) -> None:
        stages.append(f"| filter {expression}")
        recognised.append(meta)

    structured_filters: Sequence[Dict[str, Any]] = []
    if filters:
        if isinstance(filters, dict):
            structured_filters = [filters]
        else:
            structured_filters = list(filters)

    for item in structured_filters:
        field = item.get("field") if isinstance(item, dict) else None
        operator = item.get("operator", "=") if isinstance(item, dict) else "="
        value = item.get("value") if isinstance(item, dict) else None
        if not field:
            continue
        expression = _format_filter(field, operator, value)
        add_filter_stage(expression, {"type": "structured", "field": field, "operator": operator, "value": value})

    if natural_language_intent:
        nl_filters, spans, meta = _extract_natural_language_filters(natural_language_intent, field_map)
        for expression, entry in zip(nl_filters, meta):
            add_filter_stage(expression, entry)

        keyword_candidates = _extract_keywords(natural_language_intent, spans)
        process_names = _resolve_process_aliases(keyword_candidates)
        if process_names:
            field = _field_if_available(["actor_process_image_name", "action_file_name"], field_map)
            if field:
                expression = _format_filter(field, "in", sorted(set(process_names)))
                add_filter_stage(
                    expression,
                    {"type": "process_keyword", "field": field, "value": process_names},
                )
        if re.search(r"process|execution", natural_language_intent, re.IGNORECASE):
            field = _field_if_available(["event_type"], field_map)
            if field:
                add_filter_stage(
                    _format_filter(field, "=", "ENUM.PROCESS"),
                    {"type": "event_type", "field": field, "value": "ENUM.PROCESS"},
                )
        time_filters, time_spans, time_meta = _extract_time_filters(natural_language_intent)
        for expression, meta_entry in zip(time_filters, time_meta):
            add_filter_stage(expression, meta_entry)
        spans.extend(time_spans)

    if time_range:
        if isinstance(time_range, str):
            add_filter_stage(time_range, {"type": "time_range", "value": time_range})
        elif isinstance(time_range, dict):
            field = time_range.get("field", "_time")
            operator = time_range.get("operator", ">")
            value = time_range.get("value", "current_time() - interval '1 hour'")
            expression = _format_filter(field, operator, value)
            add_filter_stage(
                expression,
                {"type": "time_range", "field": field, "operator": operator, "value": value},
            )

    if fields:
        cleaned_fields = [field.strip() for field in fields if field]
        if cleaned_fields:
            stages.append(f"| fields {', '.join(cleaned_fields)}")
            selected_fields = cleaned_fields
    else:
        default_fields: Sequence[str] | None = None
        if dataset_meta and isinstance(dataset_meta, dict):
            raw_default = dataset_meta.get("default_fields")
            if isinstance(raw_default, list) and raw_default:
                default_fields = [str(field).strip() for field in raw_default if field]
        if not default_fields and field_groups:
            default_fields = _derive_default_fields(field_groups, field_map)
        if default_fields:
            formatted_fields = [field for field in default_fields if field]
            if formatted_fields:
                stages.append(f"| fields {', '.join(formatted_fields)}")
                selected_fields = list(formatted_fields)

    effective_limit = DEFAULT_LIMIT if limit is None else min(max(int(limit), 1), MAX_LIMIT)
    stages.append(f"| limit {effective_limit}")

    metadata = {
        "dataset": chosen_dataset,
        "normalisation": normalisation_log,
        "recognised": recognised,
        "limit": effective_limit,
    }

    if selected_fields:
        metadata["fields"] = selected_fields

    return "\n".join(stages), metadata
