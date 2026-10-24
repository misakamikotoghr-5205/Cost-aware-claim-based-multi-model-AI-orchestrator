"""Owner-scoped node pairing and presence; never stores model credentials."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import re
import secrets
import sqlite3
import time

from .cloud_queue import QueueError, TaskNotFound, _identifier


class PairingError(QueueError):
    """The same public error covers invalid, expired, and consumed codes."""


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _label(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 80
            or any(ord(c) < 32 for c in value)):
        raise QueueError("label must contain 1..80 printable characters")
    return value


def _names(values, field, *, aliases=False):
    if not isinstance(values, list) or len(values) > 128:
        raise QueueError(f"invalid {field}")
    pattern = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}" if aliases else r"[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,159}"
    if (any(not isinstance(v, str) or not re.fullmatch(pattern, v) or v.startswith("/")
            or re.match(r"^[A-Za-z]:", v) or ".." in v.split("/") for v in values)
            or len(values) != len(set(values))):
        raise QueueError(f"invalid {field}; use names, never local paths")
    return sorted(values)


def validate_capabilities(value):
    required = {"platform", "tools", "skills", "executors", "models", "workspace_aliases", "max_actions"}
    if not isinstance(value, dict) or set(value) != required:
        raise QueueError("capabilities require platform, tools, skills, executors, models, workspace_aliases, max_actions only")
    if value["platform"] not in {"windows", "linux", "macos", "other"}:
        raise QueueError("platform must be windows, linux, macos or other")
    limit = value["max_actions"]
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise QueueError("max_actions must be an integer between 1 and 1000")
    result = {"platform": value["platform"], "max_actions": limit}
    for field in required - {"platform", "max_actions"}:
        result[field] = _names(value[field], field, aliases=field == "workspace_aliases")
    return result


def quota_account(owner_id, account):
    """Physical ledger key; HTTP surfaces only the logical account name."""
    _identifier(owner_id, "owner_id")
    _identifier(account, "account")
    key = f"owner:{len(owner_id)}:{owner_id}:{account}"
    if len(key) > 256:
        raise QueueError("owner/account identifiers exceed ledger limit")
    return key


def reservation_prefix(owner_id, node_id=None):
    # Digest gives a bounded, unambiguous namespace even with long actor IDs.
    actor = json.dumps([owner_id, node_id], separators=(",", ":"))
    return "fleet:" + _digest(actor) + ":"


class NodeRegistry:
    def __init__(self, path, *, clock=time.time, online_seconds=90):
        if str(path) == ":memory:":
            raise QueueError("node registry requires a durable database")
        if (isinstance(online_seconds, bool) or not isinstance(online_seconds, (int, float))
                or not math.isfinite(online_seconds) or not 1 <= online_seconds <= 3600):
            raise QueueError("online_seconds must be between 1 and 3600")
        self.path, self.clock, self.online_seconds = str(path), clock, online_seconds
        with self._transaction() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS fleet_nodes (
                    node_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, label TEXT NOT NULL,
                    capabilities TEXT NOT NULL, accounts TEXT NOT NULL,
                    token_hash TEXT UNIQUE NOT NULL, created_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL, revoked_at REAL
                );
                CREATE INDEX IF NOT EXISTS fleet_owner ON fleet_nodes(owner_id,node_id);
                CREATE TABLE IF NOT EXISTS fleet_pairings (
                    pairing_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, code_hash TEXT UNIQUE NOT NULL,
                    label TEXT NOT NULL, accounts TEXT NOT NULL, expires_at REAL NOT NULL,
                    consumed_at REAL
                );
            """)

    @contextmanager
    def _transaction(self):
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _view(self, row):
        result = dict(row)
        result.pop("token_hash", None)
        result["capabilities"] = json.loads(result["capabilities"])
        result["accounts"] = json.loads(result["accounts"])
        age = self.clock() - result["last_seen_at"]
        result["online"] = result["revoked_at"] is None and 0 <= age < self.online_seconds
        return result

    def create_pairing(self, owner_id, *, accounts=(), label="New node", ttl_seconds=600):
        _identifier(owner_id, "owner_id")
        _label(label)
        accounts = _names(list(accounts), "accounts")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 600:
            raise QueueError("pairing TTL must be an integer between 1 and 600 seconds")
        code, pairing_id = secrets.token_urlsafe(32), "pair-" + secrets.token_hex(12)
        expires = self.clock() + ttl_seconds
        with self._transaction() as conn:
            conn.execute("INSERT INTO fleet_pairings VALUES(?,?,?,?,?,?,NULL)",
                         (pairing_id, owner_id, _digest(code), label, json.dumps(accounts), expires))
        return {"pairing_id": pairing_id, "code": code, "expires_at": expires}

    def redeem(self, code, label, capabilities):
        # Validate malformed code with exactly the same public error as expired.
        if not isinstance(code, str) or not 32 <= len(code) <= 128:
            raise PairingError("pairing could not be redeemed")
        _label(label)
        capabilities = validate_capabilities(capabilities)
        now = self.clock()
        token, node_id = secrets.token_urlsafe(32), "node-" + secrets.token_hex(12)
        with self._transaction() as conn:
            pairing = conn.execute("SELECT * FROM fleet_pairings WHERE code_hash=?", (_digest(code),)).fetchone()
            if pairing is None or pairing["consumed_at"] is not None or now >= pairing["expires_at"]:
                raise PairingError("pairing could not be redeemed")
            conn.execute("UPDATE fleet_pairings SET consumed_at=? WHERE pairing_id=?", (now, pairing["pairing_id"]))
            conn.execute("INSERT INTO fleet_nodes VALUES(?,?,?,?,?,?,?,?,NULL)",
                         (node_id, pairing["owner_id"], label, json.dumps(capabilities), pairing["accounts"],
                          _digest(token), now, now))
            row = conn.execute("SELECT * FROM fleet_nodes WHERE node_id=?", (node_id,)).fetchone()
            return {"node": self._view(row), "token": token}

    def authenticate(self, token):
        if not isinstance(token, str) or not 24 <= len(token) <= 4096:
            return None
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM fleet_nodes WHERE token_hash=? AND revoked_at IS NULL", (_digest(token),)).fetchone()
            return self._view(row) if row else None

    def get(self, owner_id, node_id):
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM fleet_nodes WHERE owner_id=? AND node_id=?", (owner_id, node_id)).fetchone()
            if row is None:
                raise TaskNotFound("node not found")
            return self._view(row)

    def list(self, owner_id):
        with self._transaction() as conn:
            return [self._view(row) for row in conn.execute(
                "SELECT * FROM fleet_nodes WHERE owner_id=? ORDER BY node_id", (owner_id,))]

    def heartbeat(self, owner_id, node_id, capabilities=None):
        capabilities = None if capabilities is None else validate_capabilities(capabilities)
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM fleet_nodes WHERE owner_id=? AND node_id=? AND revoked_at IS NULL",
                               (owner_id, node_id)).fetchone()
            if row is None:
                raise PermissionError()
            conn.execute("UPDATE fleet_nodes SET last_seen_at=?,capabilities=? WHERE node_id=?",
                         (self.clock(), row["capabilities"] if capabilities is None else json.dumps(capabilities), node_id))
            row = conn.execute("SELECT * FROM fleet_nodes WHERE node_id=?", (node_id,)).fetchone()
            return self._view(row)

    def revoke(self, owner_id, node_id):
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM fleet_nodes WHERE owner_id=? AND node_id=?", (owner_id, node_id)).fetchone()
            if row is None:
                raise TaskNotFound("node not found")
            conn.execute("UPDATE fleet_nodes SET revoked_at=COALESCE(revoked_at,?) WHERE node_id=?", (self.clock(), node_id))
            row = conn.execute("SELECT * FROM fleet_nodes WHERE node_id=?", (node_id,)).fetchone()
            return self._view(row)

    @staticmethod
    def matches(node, requirements):
        allowed = {"tools", "skills", "executors", "models", "platform", "max_actions", "workspace_alias"}
        if not isinstance(requirements, dict) or set(requirements) - allowed:
            raise QueueError("unsupported node selection requirements")
        cap = node["capabilities"]
        matched = node["online"] and node["revoked_at"] is None
        for field in ("tools", "skills", "executors", "models"):
            needed = _names(requirements.get(field, []), field)
            matched = matched and set(needed) <= set(cap[field])
        if "platform" in requirements:
            if requirements["platform"] not in {"windows", "linux", "macos", "other"}:
                raise QueueError("invalid platform requirement")
            matched = matched and cap["platform"] == requirements["platform"]
        if "workspace_alias" in requirements:
            _names([requirements["workspace_alias"]], "workspace_alias", aliases=True)
            matched = matched and requirements["workspace_alias"] in cap["workspace_aliases"]
        if "max_actions" in requirements:
            limit = requirements["max_actions"]
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
                raise QueueError("invalid max_actions requirement")
            matched = matched and limit <= cap["max_actions"]
        return bool(matched)

    def select(self, owner_id, requirements):
        # Validate even an empty owner's query instead of accepting arbitrary fields.
        empty = {"online": False, "revoked_at": None, "capabilities": {
            "platform": "other", "tools": [], "skills": [], "executors": [], "models": [],
            "workspace_aliases": [], "max_actions": 1}}
        self.matches(empty, requirements)
        return next((node for node in self.list(owner_id) if self.matches(node, requirements)), None)
