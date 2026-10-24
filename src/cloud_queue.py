"""Durable task transport. All routing and tool execution remain on the node.

A lost execution lease is UNKNOWN, never automatically retried: the original
worker might already have performed a side effect. SQLite serializes claims.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import ntpath
import posixpath
import re
import secrets
import sqlite3
import time
from typing import Any, Callable


class QueueError(ValueError):
    """Invalid task or state transition."""


class QueueConflict(QueueError):
    """An idempotency key, lease, or terminal result conflicts."""


class TaskNotFound(QueueError):
    """A task is absent or belongs to another client."""


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,159}$")
_TOOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise QueueError(f"invalid {field}")
    return value


def _json(value: Any) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise QueueError("expected finite JSON value") from exc
    if len(encoded.encode("utf-8")) > 512 * 1024:
        raise QueueError("JSON value exceeds 512 KiB")
    return encoded


def validate_payload(payload: Any) -> dict:
    """Check transport constraints; the node must enforce its local allowlists."""
    if not isinstance(payload, dict):
        raise QueueError("payload must be an object")
    required = {"client_id", "target", "task", "workdir", "tools", "max_actions"}
    optional = {"executor", "skills", "escalate_to", "require_approval_for", "metadata"}
    if not required <= payload.keys() or payload.keys() - required - optional:
        raise QueueError("missing or unsupported payload fields")
    for field in ("client_id", "target"):
        _identifier(payload[field], field)
    task = payload["task"]
    if not isinstance(task, str) or not task.strip() or len(task) > 100_000:
        raise QueueError("task must be nonempty text (at most 100000 characters)")
    workdir = payload["workdir"]
    if (not isinstance(workdir, str) or not workdir or len(workdir) > 4096
            or any(ord(char) < 32 for char in workdir)
            or not (posixpath.isabs(workdir) or (ntpath.isabs(workdir) and bool(ntpath.splitdrive(workdir)[0])))
            or ".." in workdir.replace("\\", "/").split("/")):
        raise QueueError("workdir must be an absolute path without parent traversal")
    for field in ("tools", "skills", "require_approval_for"):
        values = payload.get(field, [])
        if (not isinstance(values, list) or len(values) > 128
                or any(not isinstance(item, str) or not _TOOL.fullmatch(item) for item in values)
                or len(values) != len(set(values))):
            raise QueueError(f"invalid {field} allowlist")
    limit = payload["max_actions"]
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise QueueError("max_actions must be an integer between 1 and 1000")
    for field in ("executor", "escalate_to"):
        if field in payload:
            _identifier(payload[field], field)
    if "metadata" in payload and not isinstance(payload["metadata"], dict):
        raise QueueError("metadata must be an object")
    return json.loads(_json(payload))


class CloudQueue:
    def __init__(self, path: str, *, clock: Callable[[], float] = time.time):
        self.path = str(path)
        if self.path == ":memory:":
            raise QueueError("use a file-backed database for durable task transport")
        self.clock = clock
        with self._transaction() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS cloud_tasks (
                task_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, target TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                lease_token TEXT, fence INTEGER NOT NULL DEFAULT 0, lease_expires_at REAL,
                claim_request_id TEXT, result TEXT,
                UNIQUE(client_id, idempotency_key), UNIQUE(client_id, claim_request_id)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS cloud_pending ON cloud_tasks(client_id,target,status,created_at)")

    @contextmanager
    def _transaction(self):
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _expire(self, conn, now):
        conn.execute("""UPDATE cloud_tasks SET status='unknown',updated_at=?
                        WHERE status='claimed' AND lease_expires_at<=?""", (now, now))

    @staticmethod
    def _task(row):
        data = dict(row)
        data["payload"] = json.loads(data["payload"])
        data["result"] = json.loads(data["result"]) if data["result"] is not None else None
        return data

    @staticmethod
    def _find(conn, task_id, client_id):
        row = conn.execute("SELECT * FROM cloud_tasks WHERE task_id=? AND client_id=?", (task_id, client_id)).fetchone()
        if row is None:
            raise TaskNotFound("task not found")
        return row

    @staticmethod
    def _duration(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 1 <= value <= 3600:
            raise QueueError("lease_seconds must be between 1 and 3600")
        return float(value)

    def enqueue(self, task_id: str, payload: dict, idempotency_key: str | None = None) -> dict:
        task_id = _identifier(task_id, "task_id")
        payload = validate_payload(payload)
        key = _identifier(task_id if idempotency_key is None else idempotency_key, "idempotency_key")
        body = _json(payload)
        now = self.clock()
        with self._transaction() as conn:
            prior = conn.execute("SELECT * FROM cloud_tasks WHERE task_id=? OR (client_id=? AND idempotency_key=?)", (task_id, payload["client_id"], key)).fetchall()
            if prior:
                if len(prior) != 1 or prior[0]["task_id"] != task_id or prior[0]["payload"] != body or prior[0]["idempotency_key"] != key:
                    raise QueueConflict("task or idempotency key already used with different content")
                return self._task(prior[0])
            conn.execute("""INSERT INTO cloud_tasks(task_id,client_id,target,idempotency_key,payload,status,created_at,updated_at)
                            VALUES(?,?,?,?,?,'queued',?,?)""", (task_id, payload["client_id"], payload["target"], key, body, now, now))
            return self._task(self._find(conn, task_id, payload["client_id"]))

    def get(self, task_id: str, client_id: str) -> dict:
        with self._transaction() as conn:
            self._expire(conn, self.clock())
            return self._task(self._find(conn, task_id, client_id))

    def claim(self, client_id: str, lease_seconds: float = 60, *, target: str | None = None, request_id: str | None = None) -> dict | None:
        client_id = _identifier(client_id, "client_id")
        target = _identifier(client_id if target is None else target, "target")
        duration = self._duration(lease_seconds)
        if request_id is not None:
            _identifier(request_id, "request_id")
        now = self.clock()
        with self._transaction() as conn:
            self._expire(conn, now)
            if request_id is not None:
                prior = conn.execute("SELECT * FROM cloud_tasks WHERE client_id=? AND claim_request_id=?", (client_id, request_id)).fetchone()
                if prior is not None:
                    if prior["target"] != target:
                        raise QueueConflict("claim request target changed")
                    return self._task(prior)
            row = conn.execute("""SELECT * FROM cloud_tasks WHERE client_id=? AND target=? AND status='queued'
                                  ORDER BY created_at,task_id LIMIT 1""", (client_id, target)).fetchone()
            if row is None:
                return None
            conn.execute("""UPDATE cloud_tasks SET status='claimed',lease_token=?,fence=fence+1,
                            lease_expires_at=?,claim_request_id=?,updated_at=? WHERE task_id=?""",
                         (secrets.token_urlsafe(32), now + duration, request_id, now, row["task_id"]))
            return self._task(self._find(conn, row["task_id"], client_id))

    def heartbeat(self, task_id: str, client_id: str, lease_token: str, lease_seconds: float = 60) -> dict:
        duration = self._duration(lease_seconds)
        now = self.clock()
        expired = False
        with self._transaction() as conn:
            self._expire(conn, now)
            row = self._find(conn, task_id, client_id)
            if row["status"] != "claimed" or not lease_token or row["lease_token"] != lease_token:
                expired = True
            else:
                conn.execute("UPDATE cloud_tasks SET lease_expires_at=?,updated_at=? WHERE task_id=?", (now + duration, now, task_id))
                result = self._task(self._find(conn, task_id, client_id))
        if expired:
            raise QueueConflict("lease is not active")
        return result

    def complete(self, task_id: str, client_id: str, lease_token: str, result: dict, status: str = "done") -> dict:
        if not isinstance(status, str) or status not in {"done", "failed", "unknown"} or not isinstance(result, dict):
            raise QueueError("completion requires a result object and done/failed/unknown status")
        if status == "done" and (result.get("verified") is not True or not isinstance(result.get("evidence"), list)
                or not result["evidence"] or any(not isinstance(item, str) or not item.strip() for item in result["evidence"])):
            raise QueueError("done requires verified=true and nonempty verification evidence")
        body = _json(result)
        now = self.clock()
        conflict = False
        with self._transaction() as conn:
            self._expire(conn, now)
            row = self._find(conn, task_id, client_id)
            if not lease_token or row["lease_token"] != lease_token:
                conflict = True
            elif row["status"] in {"done", "failed", "unknown"} and row["result"] == body and row["status"] == status:
                return self._task(row)
            elif row["status"] != "claimed":
                conflict = True
            else:
                conn.execute("UPDATE cloud_tasks SET status=?,result=?,updated_at=? WHERE task_id=?", (status, body, now, task_id))
                completed = self._task(self._find(conn, task_id, client_id))
        if conflict:
            raise QueueConflict("lease is not active or terminal result differs")
        return completed

    def cancel(self, task_id: str, client_id: str) -> dict:
        """A running cancellation is uncertain; it cannot undo node side effects."""
        now = self.clock()
        with self._transaction() as conn:
            self._expire(conn, now)
            row = self._find(conn, task_id, client_id)
            if row["status"] == "queued":
                conn.execute("UPDATE cloud_tasks SET status='cancelled',updated_at=? WHERE task_id=?", (now, task_id))
            elif row["status"] == "claimed":
                conn.execute("UPDATE cloud_tasks SET status='unknown',updated_at=? WHERE task_id=?", (now, task_id))
            return self._task(self._find(conn, task_id, client_id))
