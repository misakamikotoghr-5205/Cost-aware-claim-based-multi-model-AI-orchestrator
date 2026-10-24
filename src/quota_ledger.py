"""Durable, atomic quota reservations, with explicit units and conservative recovery.

Only the server should own the ledger file. SQLite coordinates processes on one
host; a copy on each client is not a shared quota ledger.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
import math
import sqlite3
import time


class QuotaError(ValueError):
    """A quota operation cannot safely proceed."""


class InsufficientQuota(QuotaError):
    pass


class StaleQuota(QuotaError):
    pass


class ReservationConflict(QuotaError):
    pass


def _name(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise QuotaError(f"{field} must be a nonempty string of at most 256 characters")
    return value


def _amount(value, *, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
        # Bound precision as well as magnitude: arithmetic must remain exact in
        # Decimal's default context and cannot be an unbounded-input DoS.
        if (not result.is_finite() or result < 0 or (positive and result == 0)
                or result > Decimal("1e15") or result.as_tuple().exponent < -9):
            raise ValueError
    except (InvalidOperation, ValueError, TypeError):
        raise QuotaError("amount must be finite, nonnegative, <= 1e15, with <= 9 decimal places") from None
    return result


def _timestamp(value, field):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise QuotaError(f"{field} must be finite UTC epoch seconds")
    return float(value)


def _number(value):
    value = Decimal(value)
    return int(value) if value == value.to_integral_value() else float(value)


class QuotaLedger:
    def __init__(self, path, *, clock=time.time):
        self.path = str(Path(path))
        if self.path == ":memory:":
            raise QuotaError("use a durable SQLite file, not :memory:")
        self.clock = clock
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS quotas (
                    account TEXT NOT NULL, model TEXT NOT NULL, unit TEXT NOT NULL,
                    remaining TEXT NOT NULL, observed_remaining TEXT NOT NULL,
                    capacity TEXT, observed_at REAL NOT NULL, reset_at REAL,
                    max_age_seconds REAL NOT NULL, source TEXT NOT NULL,
                    PRIMARY KEY(account, model)
                );
                CREATE TABLE IF NOT EXISTS quota_reservations (
                    reservation_id TEXT PRIMARY KEY, account TEXT NOT NULL,
                    model TEXT NOT NULL, unit TEXT NOT NULL, amount TEXT NOT NULL,
                    state TEXT NOT NULL, actual_amount TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    minimum_remaining TEXT NOT NULL DEFAULT '0',
                    FOREIGN KEY(account, model) REFERENCES quotas(account, model)
                );
                CREATE INDEX IF NOT EXISTS quota_reservation_pool
                    ON quota_reservations(account, model, state);
            """)
        # Additive migration for databases created before atomic reserve floors.
        # Serialize the check+ALTER so concurrent process startup cannot race.
        with self._transaction() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(quota_reservations)")}
            if "minimum_remaining" not in columns:
                conn.execute("ALTER TABLE quota_reservations ADD COLUMN minimum_remaining TEXT NOT NULL DEFAULT '0'")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def _quota(self, conn, account, model):
        row = conn.execute("SELECT * FROM quotas WHERE account=? AND model=?", (account, model)).fetchone()
        if row is None:
            raise QuotaError("quota is unknown for this account/model")
        return row

    def _reservation(self, conn, reservation_id):
        row = conn.execute("SELECT * FROM quota_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if row is None:
            raise QuotaError("reservation does not exist")
        return row

    @staticmethod
    def _reservation_dict(row):
        result = dict(row)
        for key in ("amount", "actual_amount", "minimum_remaining"):
            if result[key] is not None:
                result[key] = _number(result[key])
        return result

    def _snapshot(self, conn, account, model):
        row = self._quota(conn, account, model)
        result = dict(row)
        result.pop("observed_remaining")
        pending = conn.execute(
            "SELECT amount,actual_amount,state FROM quota_reservations WHERE account=? AND model=? AND state IN ('RESERVED','UNKNOWN')",
            (account, model)).fetchall()
        held = sum((max(Decimal(r[0]), Decimal(r[1] or 0)) for r in pending), Decimal(0))
        # UNKNOWN has no trustworthy consumption bound. Hold accounting remains
        # useful for reconciliation, but no other request may spend this pool.
        blocked = any(r[2] == "UNKNOWN" for r in pending)
        remaining = Decimal(row["remaining"])
        now = self.clock()
        expired = row["reset_at"] is not None and now >= row["reset_at"]
        fresh = not blocked and not expired and 0 <= now - row["observed_at"] <= row["max_age_seconds"]
        result.update(remaining=_number(remaining), held=_number(held),
                      available=_number(max(Decimal(0), remaining - held)),
                      fresh=fresh, blocked=blocked,
                      status="blocked" if blocked else "expired" if expired else "ready" if fresh else "stale")
        result["capacity"] = None if row["capacity"] is None else _number(row["capacity"])
        return result

    def snapshot(self, account, model="*"):
        with self._transaction() as conn:
            return self._snapshot(conn, _name(account, "account"), _name(model, "model"))

    def set_quota(self, account, remaining, unit="requests", *, model="*", capacity=None,
                  reset_at=None, observed_at=None, max_age_seconds=300, source="manual"):
        """Import an authoritative observation; never infer a subscription's units.

        Open reservations survive imports. Equal timestamps are only accepted
        for identical observations and never undo a local settlement.
        """
        account, model, unit, source = (_name(v, k) for v, k in (
            (account, "account"), (model, "model"), (unit, "unit"), (source, "source")))
        remaining = _amount(remaining)
        capacity = None if capacity is None else _amount(capacity, positive=True)
        if capacity is not None and remaining > capacity:
            raise QuotaError("remaining exceeds capacity")
        observed_at = _timestamp(self.clock() if observed_at is None else observed_at, "observed_at")
        max_age_seconds = _timestamp(max_age_seconds, "max_age_seconds")
        if max_age_seconds <= 0 or observed_at > self.clock():
            raise QuotaError("observation cannot be in the future and max_age_seconds must be positive")
        reset_at = None if reset_at is None else _timestamp(reset_at, "reset_at")
        values = (unit, str(remaining), None if capacity is None else str(capacity),
                  observed_at, reset_at, max_age_seconds, source)
        with self._transaction() as conn:
            old = conn.execute("SELECT * FROM quotas WHERE account=? AND model=?", (account, model)).fetchone()
            if old is not None:
                if observed_at < old["observed_at"]:
                    raise StaleQuota("older observation cannot replace newer quota")
                if unit != old["unit"]:
                    raise QuotaError("pool unit is immutable; create a separate account/model pool")
                if observed_at == old["observed_at"]:
                    previous = tuple(old[k] for k in ("unit", "observed_remaining", "capacity", "observed_at", "reset_at", "max_age_seconds", "source"))
                    if values != previous:
                        raise ReservationConflict("different observations have the same timestamp")
                    return self._snapshot(conn, account, model)
            conn.execute("""INSERT INTO quotas(account,model,unit,remaining,observed_remaining,capacity,
                           observed_at,reset_at,max_age_seconds,source) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(account,model) DO UPDATE SET remaining=excluded.remaining,
                observed_remaining=excluded.observed_remaining, capacity=excluded.capacity,
                observed_at=excluded.observed_at,reset_at=excluded.reset_at,
                max_age_seconds=excluded.max_age_seconds,source=excluded.source""",
                (account, model, unit, str(remaining), str(remaining), values[2], observed_at,
                 reset_at, max_age_seconds, source))
            return self._snapshot(conn, account, model)

    def reserve(self, reservation_id, account, amount, *, model="*", unit=None, minimum_remaining=0):
        reservation_id = _name(reservation_id, "reservation_id")
        account, model = _name(account, "account"), _name(model, "model")
        amount = _amount(amount, positive=True)
        floor = _amount(minimum_remaining)
        with self._transaction() as conn:
            quota = self._quota(conn, account, model)
            if unit is not None and unit != quota["unit"]:
                raise QuotaError("reservation unit does not match pool")
            old = conn.execute("SELECT * FROM quota_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if old:
                if (old["account"] != account or old["model"] != model or Decimal(old["amount"]) != amount
                        or Decimal(old["minimum_remaining"]) != floor):
                    raise ReservationConflict("reservation id already has different parameters")
                # A replay returns its original state; it never authorizes execution again.
                return dict(self._reservation_dict(old), replayed=True)
            snap = self._snapshot(conn, account, model)
            if not snap["fresh"]:
                raise StaleQuota("quota is stale, reset, or blocked by UNKNOWN consumption; reconcile before execution")
            held = sum((Decimal(r[0]) for r in conn.execute(
                "SELECT amount FROM quota_reservations WHERE account=? AND model=? AND state IN ('RESERVED','UNKNOWN')",
                (account, model))), Decimal(0))
            if Decimal(quota["remaining"]) - held - amount < floor:
                raise InsufficientQuota("insufficient available quota above the required minimum")
            now = self.clock()
            conn.execute("""INSERT INTO quota_reservations(reservation_id,account,model,unit,amount,
                         state,actual_amount,created_at,updated_at,minimum_remaining)
                         VALUES(?,?,?,?,?,'RESERVED',NULL,?,?,?)""",
                         (reservation_id, account, model, quota["unit"], str(amount), now, now, str(floor)))
            return dict(self._reservation_dict(self._reservation(conn, reservation_id)), replayed=False)

    def get_reservation(self, reservation_id):
        with self._connect() as conn:
            return self._reservation_dict(self._reservation(conn, _name(reservation_id, "reservation_id")))

    def settle(self, reservation_id, actual_amount):
        """Settle known consumption once, up to the reserved maximum.

        An overrun is UNKNOWN and retains its hold; manual reconciliation is
        required. Clients must enforce the reserved maximum before calling a tool.
        """
        actual = _amount(actual_amount)
        overrun = False
        with self._transaction() as conn:
            row = self._reservation(conn, _name(reservation_id, "reservation_id"))
            if row["state"] == "SETTLED":
                if Decimal(row["actual_amount"]) != actual:
                    raise ReservationConflict("settlement amount differs from previous settlement")
                return self._reservation_dict(row)
            if row["state"] == "RELEASED":
                raise ReservationConflict("released reservation cannot be settled")
            quota = self._quota(conn, row["account"], row["model"])
            if actual > Decimal(row["amount"]) or actual > Decimal(quota["remaining"]):
                conn.execute("UPDATE quota_reservations SET state='UNKNOWN',actual_amount=?,updated_at=? WHERE reservation_id=?",
                             (str(actual), self.clock(), reservation_id))
                overrun = True
            else:
                conn.execute("UPDATE quotas SET remaining=? WHERE account=? AND model=?",
                             (str(Decimal(quota["remaining"]) - actual), row["account"], row["model"]))
                conn.execute("UPDATE quota_reservations SET state='SETTLED',actual_amount=?,updated_at=? WHERE reservation_id=?",
                             (str(actual), self.clock(), reservation_id))
            result = self._reservation_dict(self._reservation(conn, reservation_id))
        if overrun:
            raise ReservationConflict("actual consumption exceeds reserved/remaining quota; reservation is UNKNOWN")
        return result

    def release(self, reservation_id, *, confirmed_unused=False):
        """Cancel before execution; UNKNOWN requires verified evidence of no use."""
        with self._transaction() as conn:
            row = self._reservation(conn, _name(reservation_id, "reservation_id"))
            if row["state"] == "SETTLED":
                raise ReservationConflict("settled reservation cannot be released")
            if row["state"] == "UNKNOWN" and confirmed_unused is not True:
                raise ReservationConflict("UNKNOWN remains held until confirmed unused or settled")
            conn.execute("UPDATE quota_reservations SET state='RELEASED',updated_at=? WHERE reservation_id=?",
                         (self.clock(), reservation_id))
            return self._reservation_dict(self._reservation(conn, reservation_id))

    def mark_unknown(self, reservation_id):
        with self._transaction() as conn:
            row = self._reservation(conn, _name(reservation_id, "reservation_id"))
            if row["state"] not in ("RESERVED", "UNKNOWN"):
                raise ReservationConflict("terminal reservation cannot become UNKNOWN")
            conn.execute("UPDATE quota_reservations SET state='UNKNOWN',updated_at=? WHERE reservation_id=?",
                         (self.clock(), reservation_id))
            return self._reservation_dict(self._reservation(conn, reservation_id))

    def invalidate_reservations(self, prefix):
        """Freeze unfinished reservations for one authenticated actor namespace.

        The server constructs an unambiguous prefix; callers must never accept
        an arbitrary prefix from a node request. Terminal settlements stay intact.
        """
        prefix = _name(prefix, "prefix")
        with self._transaction() as conn:
            changed = conn.execute("""UPDATE quota_reservations SET state='UNKNOWN',updated_at=?
                WHERE substr(reservation_id,1,?)=? AND state='RESERVED'""",
                (self.clock(), len(prefix), prefix)).rowcount
            return {"invalidated": changed}
