"""Read-only import of explicitly normalized, credential-free quota observations.

This is an interchange format owned by this project, NOT an alleged CC Switch
CLI export format. No OAuth stores, provider settings, or usage scripts are read
or executed here. See docs/quota-ledger.md for the upstream interface audit.
"""
from __future__ import annotations

import json
from pathlib import Path

from .quota_ledger import QuotaError, _amount, _name, _timestamp


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise QuotaError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise QuotaError("nonfinite JSON number")


def load_normalized_quota(path):
    """Read <=1 MiB of schema v1 JSON, rejecting secrets and unknown fields.

    Only explicitly supplied files are opened. An upstream/local helper must
    obtain authorized quota observations and map them to this schema first.
    """
    with Path(path).open("rb") as handle:
        payload = handle.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise QuotaError("quota JSON exceeds 1 MiB")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_object,
                              parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        # Never echo payloads or decoder snippets: the wrong file may contain credentials.
        raise QuotaError("invalid quota JSON") from None
    if not isinstance(document, dict) or set(document) != {"schema_version", "source", "observations"}:
        raise QuotaError("expected schema_version, source, observations only")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise QuotaError("unsupported quota schema version")
    source = _name(document["source"], "source")
    records = document["observations"]
    if not isinstance(records, list) or not records or len(records) > 1000:
        raise QuotaError("observations must contain 1..1000 items")
    required = {"account", "model", "remaining", "unit", "observed_at"}
    optional = {"capacity", "reset_at", "max_age_seconds"}
    result, seen = [], set()
    for record in records:
        if not isinstance(record, dict) or not required <= set(record) or set(record) - required - optional:
            raise QuotaError("quota observation has missing or unknown fields")
        item = dict(record)
        for field in ("account", "model", "unit"):
            _name(item[field], field)
        key = (item["account"], item["model"])
        if key in seen:
            raise QuotaError("duplicate account/model observation")
        seen.add(key)
        remaining = _amount(item["remaining"])
        if item.get("capacity") is not None and remaining > _amount(item["capacity"], positive=True):
            raise QuotaError("remaining exceeds capacity")
        _timestamp(item["observed_at"], "observed_at")
        if item.get("reset_at") is not None:
            _timestamp(item["reset_at"], "reset_at")
        if _timestamp(item.get("max_age_seconds", 300), "max_age_seconds") <= 0:
            raise QuotaError("max_age_seconds must be positive")
        item["source"] = source
        result.append(item)
    return result


class CCSwitchQuotaSource:
    """Adapter for sanitized normalized JSON, not CC Switch's raw database.

    No external processes or networks are invoked. `read` validates every record;
    `sync` explicitly imports observations into the caller's ledger. Multiple
    records are applied individually, so callers should handle a later conflict
    by fixing its observation and safely replaying the complete file.
    """
    def __init__(self, normalized_json_path):
        self.path = Path(normalized_json_path)

    def read(self):
        return load_normalized_quota(self.path)

    def sync(self, ledger):
        return [ledger.set_quota(**record) for record in self.read()]
