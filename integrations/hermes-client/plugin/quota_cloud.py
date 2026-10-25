"""Read-only fleet-ledger observations for the quota dashboard.

Call this only from an explicit refresh worker. The dashboard GET endpoint must
serve its profile-scoped cache; this module does not start requests on page load.
Configured quota_states contribute pool identities only, never balances. Model
credentials, task claims and quota reservation operations are not consulted.
"""
from __future__ import annotations

import math
import re
import time
from copy import deepcopy
from decimal import Decimal

SOURCE = "fleet-ledger"
MAX_POOLS = 32
REFRESH_SECONDS = 12
REQUEST_SECONDS = 3
_ACCOUNT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,159}\Z")


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1e15:
        return None
    return value


def _identity(account, model):
    return (isinstance(account, str) and _ACCOUNT.fullmatch(account) is not None
            and isinstance(model, str) and 0 < len(model) <= 256
            and not any(ord(char) < 32 or ord(char) == 127 for char in model))


def _unknown_window(model, reason):
    return {"id": model, "label": "Shared account pool" if model == "*" else model,
            "unit": None, "capacity": None, "used": None, "remaining": None,
            "reset_at": None, "scope": "account" if model == "*" else "model",
            "state": "unknown", "held": None, "available": None,
            "observed_at": None, "expires_at": None, "unknown_reason": reason}


def _window(account, model, snapshot, now):
    if not isinstance(snapshot, dict) or snapshot.get("account") != account or snapshot.get("model") != model:
        raise ValueError("Cloud quota snapshot identity mismatch")
    unit = snapshot.get("unit")
    if (not isinstance(unit, str) or not unit or len(unit) > 80
            or any(ord(char) < 32 for char in unit)):
        raise ValueError("Cloud quota unit is invalid")
    remaining, held, available, capacity = (_number(snapshot.get(field))
                                             for field in ("remaining", "held", "available", "capacity"))
    observed, age, reset = (_number(snapshot.get(field))
                            for field in ("observed_at", "max_age_seconds", "reset_at"))
    if (remaining is None or held is None or available is None or observed is None
            or observed > now or age is None or age <= 0 or capacity == 0
            or snapshot.get("capacity") is not None and capacity is None
            or snapshot.get("reset_at") is not None and reset is None
            or type(snapshot.get("fresh")) is not bool or type(snapshot.get("blocked")) is not bool
            or capacity is not None and remaining > capacity
            or Decimal(str(available)) > max(Decimal(0), Decimal(str(remaining)) - Decimal(str(held)))):
        raise ValueError("Cloud quota snapshot measurements are invalid")
    expires = min(observed + age, reset) if reset is not None else observed + age
    state = snapshot.get("status")
    if snapshot.get("blocked") is True:
        state = "blocked"
    elif reset is not None and now >= reset:
        state = "expired"
    elif snapshot.get("fresh") is not True or now > expires:
        state = "stale"
    elif state != "ready":
        raise ValueError("Cloud quota snapshot state is invalid")
    reason = {"blocked": "Uncertain consumption blocks this cloud pool until reconciliation.",
              "expired": "The cloud observation has reached its reset time.",
              "stale": "The cloud observation is stale; it is not spendable quota."}.get(state)
    return {"id": model, "label": "Shared account pool" if model == "*" else model,
            "unit": unit, "capacity": capacity,
            "used": float(Decimal(str(capacity)) - Decimal(str(remaining))) if capacity is not None else None,
            "remaining": remaining, "reset_at": reset,
            "scope": "account" if model == "*" else "model", "state": state,
            "held": held, "available": available, "observed_at": observed,
            "expires_at": expires, "unknown_reason": reason}


def _aggregate(windows):
    ready = sum(window["state"] == "ready" for window in windows)
    return "ready" if ready == len(windows) and ready else "partial" if ready else "unknown"


def age_cloud_quota(value, now):
    """Age a cached cloud projection without I/O or advancing observation times.

    An UNKNOWN or blocked window never becomes ready through time passage.
    Existing aggregate warnings (for example omitted configured pools) cannot
    disappear merely because every window that was returned is still fresh.
    """
    if _number(now) is None:
        raise ValueError("Cloud quota cache requires a finite epoch time")
    result = deepcopy(value)
    states = {"unknown": 0, "partial": 1, "ready": 2}

    def conservative(previous, computed):
        return min((previous, computed), key=lambda state: states.get(state, 0)) if previous in states else "unknown"

    windows = []
    for account in result.get("accounts", []):
        for window in account.get("windows", []):
            state = window.get("state", "unknown")
            if state not in {"unknown", "blocked"}:
                reset, expires, observed = (_number(window.get(field))
                                            for field in ("reset_at", "expires_at", "observed_at"))
                if reset is not None and now >= reset:
                    window["state"] = "expired"
                    window["unknown_reason"] = "The cloud observation has reached its reset time."
                elif state == "expired":
                    pass  # A backwards clock cannot renew an expired observation.
                elif observed is None or observed > now or expires is None:
                    window["state"] = "unknown"
                    window["unknown_reason"] = "The cached cloud observation has no valid freshness deadline."
                elif now >= expires or state == "stale":
                    window["state"] = "stale"
                    window["unknown_reason"] = "The cached cloud observation is stale; refresh to obtain a new observation."
                elif state != "ready":
                    window["state"] = "unknown"
                    window["unknown_reason"] = "The cached cloud observation state is invalid."
            windows.append(window)
        account["state"] = conservative(account.get("state"), _aggregate(account.get("windows", [])))
        if account["state"] != "ready":
            account["unknown_reason"] = "One or more cached cloud quota windows are unavailable, stale, expired or blocked."
    result["state"] = conservative(result.get("state"), _aggregate(windows))
    if result["state"] != "ready" and not result.get("unknown_reason"):
        result["unknown_reason"] = "One or more cached cloud quota pools do not have a fresh usable observation."
    return result


def read_cloud_quota(core):
    """Explicitly refresh declared cloud pools; caller owns the profile cache.

    The return value contains a projection of whitelisted ledger fields only.
    Failures do not echo URLs, credentials, server text, or configured balances.
    Every network request is FleetClient.snapshot (GET /v1/quota), bounded by a
    per-request timeout; no further requests start after the refresh deadline.
    No pool is inferred from
    bundled examples, native provider authentication, or local model metadata.
    """
    settings = core.settings()
    configured = bool(settings.get("server", {}).get("url"))
    result = {"configured": configured, "state": "unknown", "source": SOURCE,
              "accounts": [], "unknown_reason": None}
    if not configured:
        result["unknown_reason"] = "No cloud quota server is configured."
        return result
    identities, invalid = set(), False
    declarations = settings.get("quota_states", {})
    if not isinstance(declarations, dict):
        declarations = {}
        invalid = True
    for value in declarations.values():
        if not isinstance(value, dict) or not _identity(value.get("account"), value.get("model")):
            invalid = True
            continue
        identities.add((value["account"], value["model"]))
    if not identities:
        result["unknown_reason"] = "No valid cloud account/model pool identities are configured."
        return result
    ordered = sorted(identities)
    truncated = len(ordered) > MAX_POOLS
    ordered = ordered[:MAX_POOLS]
    try:
        client = core._fleet()
    except Exception:
        client = None
    deadline = time.monotonic() + REFRESH_SECONDS
    grouped = {}
    for account, model in ordered:
        reason = "Cloud quota could not be read; check the configured connection and account grant."
        window = None
        remaining = deadline - time.monotonic()
        if client is not None and remaining > 0:
            try:
                client.timeout = min(REQUEST_SECONDS, remaining)
                window = _window(account, model, client.snapshot(account, model), time.time())
            except Exception:
                pass
        elif client is not None:
            reason = "The cloud quota refresh deadline was reached."
        if window is None:
            window = _unknown_window(model, reason)
        grouped.setdefault(account, []).append(window)
    for account, windows in grouped.items():
        observed = [window["observed_at"] for window in windows if window["observed_at"] is not None]
        expiries = [window["expires_at"] for window in windows if window["expires_at"] is not None]
        state = _aggregate(windows)
        result["accounts"].append({"id": account, "provider": SOURCE, "label": account,
            "state": state, "source": SOURCE, "observed_at": min(observed) if observed else None,
            "expires_at": min(expiries) if expiries else None,
            "unknown_reason": None if state == "ready" else "One or more cloud quota windows are unavailable, stale, expired or blocked.",
            "windows": windows})
    result["state"] = _aggregate([window for windows in grouped.values() for window in windows])
    if invalid or truncated:
        result["state"] = "partial" if result["state"] in {"ready", "partial"} else "unknown"
        result["unknown_reason"] = "Some configured pool identities were invalid or exceeded the refresh limit."
    elif result["state"] != "ready":
        result["unknown_reason"] = "One or more cloud quota pools do not have a fresh usable observation."
    return result
