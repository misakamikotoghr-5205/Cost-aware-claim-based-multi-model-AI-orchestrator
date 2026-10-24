"""Quota invariants, crash recovery, concurrency, and credential-free import."""
from concurrent.futures import ProcessPoolExecutor
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.quota_ledger import (QuotaLedger, QuotaError, InsufficientQuota,
                              ReservationConflict, StaleQuota)
from src.quota_sources import CCSwitchQuotaSource, load_normalized_quota


def _reserve_process(args):
    path, number, *extra = args
    try:
        return QuotaLedger(path).reserve(f"request-{number}", "shared", 1,
                                        minimum_remaining=extra[0] if extra else 0)["state"]
    except InsufficientQuota:
        return "DENIED"


def _settle_process(path):
    return QuotaLedger(path).settle("once", 4)["state"]


class QuotaLedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "ledger.sqlite"
        self.now = 1000.0
        self.ledger = QuotaLedger(self.path, clock=lambda: self.now)
        self.ledger.set_quota("a", 10, capacity=10)

    def test_atomic_reservations_between_processes(self):
        live = QuotaLedger(self.path)
        live.set_quota("shared", 10)
        with ProcessPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(_reserve_process, [(str(self.path), i) for i in range(36)]))
        self.assertEqual(results.count("RESERVED"), 10)
        self.assertEqual(results.count("DENIED"), 26)
        self.assertEqual(live.snapshot("shared")["available"], 0)

    def test_atomic_minimum_remaining_floor_between_processes(self):
        live = QuotaLedger(self.path)
        live.set_quota("shared", 10)
        with ProcessPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(_reserve_process, [(str(self.path), i, 2) for i in range(36)]))
        self.assertEqual(results.count("RESERVED"), 8)
        self.assertEqual(results.count("DENIED"), 28)
        self.assertEqual(live.snapshot("shared")["available"], 2)

    def test_minimum_floor_is_part_of_reservation_identity(self):
        self.ledger.reserve("r", "a", 8, minimum_remaining=2)
        self.assertTrue(self.ledger.reserve("r", "a", 8, minimum_remaining=2)["replayed"])
        with self.assertRaises(ReservationConflict):
            self.ledger.reserve("r", "a", 8, minimum_remaining=0)
        with self.assertRaises(InsufficientQuota):
            self.ledger.reserve("s", "a", 1, minimum_remaining=2)

    def test_migrates_existing_database_without_losing_reservations(self):
        # Simulate the old schema using the existing known reservation state.
        self.ledger.reserve("old", "a", 3)
        # sqlite's context manager commits a transaction but does not close the
        # connection. Close it explicitly before Windows removes the fixture.
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                conn.execute("ALTER TABLE quota_reservations DROP COLUMN minimum_remaining")
        finally:
            conn.close()
        migrated = QuotaLedger(self.path, clock=lambda: self.now)
        self.assertEqual(migrated.get_reservation("old")["minimum_remaining"], 0)
        self.assertEqual(migrated.snapshot("a")["held"], 3)
        migrated.reserve("new", "a", 5, minimum_remaining=2)
        self.assertEqual(migrated.snapshot("a")["available"], 2)

    def test_settlement_once_releases_only_unused_amount(self):
        self.assertFalse(self.ledger.reserve("r", "a", 8)["replayed"])
        self.assertTrue(self.ledger.reserve("r", "a", 8)["replayed"])
        self.ledger.settle("r", 3)
        self.ledger.settle("r", 3)
        self.assertEqual(self.ledger.snapshot("a")["remaining"], 7)
        self.assertEqual(self.ledger.snapshot("a")["held"], 0)
        with self.assertRaises(ReservationConflict):
            self.ledger.settle("r", 4)
        with self.assertRaises(ReservationConflict):
            self.ledger.release("r")

    def test_concurrent_settlements_cannot_double_debit(self):
        live = QuotaLedger(self.path)
        live.set_quota("shared", 10)
        live.reserve("once", "shared", 6)
        with ProcessPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(_settle_process, [str(self.path)] * 24))
        self.assertEqual(set(results), {"SETTLED"})
        self.assertEqual(live.snapshot("shared")["remaining"], 6)

    def test_unknown_survives_restart_reset_and_cannot_be_retried(self):
        self.ledger.reserve("r", "a", 8)
        self.ledger.mark_unknown("r")
        restarted = QuotaLedger(self.path, clock=lambda: self.now)
        self.assertEqual(restarted.reserve("r", "a", 8)["state"], "UNKNOWN")
        with self.assertRaises(StaleQuota):
            restarted.reserve("retry", "a", 3)
        with self.assertRaises(ReservationConflict):
            restarted.release("r")
        self.now += 1
        restarted.set_quota("a", 10)
        self.assertEqual(restarted.snapshot("a")["available"], 2)
        self.assertEqual(restarted.snapshot("a")["status"], "blocked")
        with self.assertRaises(StaleQuota):
            restarted.reserve("small-retry", "a", 1)
        restarted.settle("r", 5)
        self.assertEqual(restarted.snapshot("a")["remaining"], 5)
        self.assertTrue(restarted.snapshot("a")["fresh"])

    def test_release_is_idempotent_but_id_is_never_reusable(self):
        self.ledger.reserve("r", "a", 8)
        self.ledger.release("r")
        self.ledger.release("r")
        self.assertEqual(self.ledger.snapshot("a")["available"], 10)
        self.assertEqual(self.ledger.reserve("r", "a", 8)["state"], "RELEASED")
        with self.assertRaises(ReservationConflict):
            self.ledger.settle("r", 1)
        with self.assertRaises(ReservationConflict):
            self.ledger.reserve("r", "a", 1)

    def test_confirmed_unused_unknown_can_be_released(self):
        self.ledger.reserve("r", "a", 8)
        self.ledger.mark_unknown("r")
        self.ledger.release("r", confirmed_unused=True)
        self.assertEqual(self.ledger.snapshot("a")["available"], 10)

    def test_overrun_preserves_unknown_hold(self):
        self.ledger.reserve("r", "a", 2)
        with self.assertRaises(ReservationConflict):
            self.ledger.settle("r", 3)
        self.assertEqual(self.ledger.get_reservation("r")["state"], "UNKNOWN")
        self.assertEqual(self.ledger.snapshot("a")["held"], 3)
        self.assertEqual(self.ledger.snapshot("a")["status"], "blocked")

    def test_known_overrun_freezes_pool_even_after_observation_refresh(self):
        self.ledger.reserve("r", "a", 2)
        with self.assertRaises(ReservationConflict):
            self.ledger.settle("r", 9)
        with self.assertRaises(StaleQuota):
            self.ledger.reserve("s", "a", 8)
        self.now += 1
        self.ledger.set_quota("a", 10)
        self.assertEqual(self.ledger.snapshot("a")["available"], 1)
        self.assertFalse(self.ledger.snapshot("a")["fresh"])
        with self.assertRaises(StaleQuota):
            self.ledger.reserve("t", "a", 1)

    def test_actor_invalidation_freezes_only_unfinished_matching_reservations(self):
        self.ledger.set_quota("other", 10)
        self.ledger.reserve("actor-a:pending", "a", 2)
        self.ledger.reserve("actor-a:settled", "a", 1)
        self.ledger.settle("actor-a:settled", 1)
        self.ledger.reserve("actor-ab:pending", "other", 2)
        assert self.ledger.invalidate_reservations("actor-a:") == {"invalidated": 1}
        assert self.ledger.get_reservation("actor-a:pending")["state"] == "UNKNOWN"
        assert self.ledger.get_reservation("actor-a:settled")["state"] == "SETTLED"
        assert self.ledger.snapshot("a")["blocked"] is True
        assert self.ledger.snapshot("other")["blocked"] is False
        assert self.ledger.invalidate_reservations("actor-a:") == {"invalidated": 0}
        self.ledger.settle("actor-a:settled", 1)
        assert self.ledger.snapshot("a")["remaining"] == 9

    def test_stale_and_reset_observations_block_reservations(self):
        self.now += 301
        self.assertFalse(self.ledger.snapshot("a")["fresh"])
        with self.assertRaises(StaleQuota):
            self.ledger.reserve("r", "a", 1)
        self.ledger.set_quota("a", 10, reset_at=self.now + 5)
        self.now += 5
        self.assertEqual(self.ledger.snapshot("a")["status"], "expired")
        with self.assertRaises(StaleQuota):
            self.ledger.reserve("s", "a", 1)

    def test_observation_replay_does_not_restore_consumed_quota(self):
        self.ledger.reserve("r", "a", 5)
        self.ledger.settle("r", 5)
        self.ledger.set_quota("a", 10, capacity=10)
        self.assertEqual(self.ledger.snapshot("a")["remaining"], 5)
        with self.assertRaises(ReservationConflict):
            self.ledger.set_quota("a", 9, capacity=10)
        with self.assertRaises(StaleQuota):
            self.ledger.set_quota("a", 10, observed_at=999)

    def test_account_model_and_unit_are_isolated(self):
        self.ledger.set_quota("a", 4, model="m")
        self.ledger.set_quota("b", 3, model="m")
        self.ledger.reserve("r", "a", 4, model="m")
        self.assertEqual(self.ledger.snapshot("a")["available"], 10)
        self.assertEqual(self.ledger.snapshot("b", "m")["available"], 3)
        with self.assertRaises(QuotaError):
            self.ledger.reserve("s", "b", 1, model="unobserved")
        with self.assertRaises(QuotaError):
            self.ledger.reserve("t", "b", 1, model="m", unit="tokens")
        with self.assertRaises(ReservationConflict):
            self.ledger.reserve("r", "b", 4, model="m")
        self.now += 1
        with self.assertRaises(QuotaError):
            self.ledger.set_quota("a", 10, unit="tokens")

    def test_fractional_units_do_not_use_binary_float_arithmetic(self):
        self.ledger.set_quota("fraction", "0.3", unit="fraction", capacity=1)
        self.ledger.reserve("one", "fraction", "0.1")
        self.ledger.reserve("two", "fraction", "0.2")
        self.assertEqual(self.ledger.snapshot("fraction")["available"], 0)
        self.ledger.settle("one", "0.1")
        self.ledger.settle("two", "0.2")
        self.assertEqual(self.ledger.snapshot("fraction")["remaining"], 0)

    def test_bad_values_rejected(self):
        for value in [-1, float("inf"), float("nan"), True, "1e999", "0.0000000001"]:
            with self.subTest(value=value), self.assertRaises(QuotaError):
                self.ledger.reserve("r", "a", value)
        with self.assertRaises(QuotaError):
            self.ledger.set_quota("a", 1, observed_at=1001)

    def _import_file(self, document):
        path = Path(self.directory.name) / "quota.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_explicit_normalized_import_preserves_units_and_age(self):
        document = {"schema_version": 1, "source": "cc-switch-normalized",
                    "observations": [{"account": "codex-owner", "model": "codex",
                        "remaining": 62, "unit": "percent", "capacity": 100,
                        "observed_at": 900, "reset_at": 2000, "max_age_seconds": 50}]}
        source = CCSwitchQuotaSource(self._import_file(document))
        snapshots = source.sync(self.ledger)
        self.assertEqual(snapshots[0]["remaining"], 62)
        self.assertEqual(snapshots[0]["unit"], "percent")
        self.assertFalse(snapshots[0]["fresh"])

    def test_raw_config_and_credentials_are_rejected_without_echo(self):
        secret = "private-token-do-not-log"
        doc = {"schema_version": 1, "source": "cc-switch-normalized", "observations": [
            {"account": "a", "model": "*", "remaining": 2, "unit": "requests",
             "observed_at": 1000, "api_key": secret}]}
        with self.assertRaises(QuotaError) as error:
            load_normalized_quota(self._import_file(doc))
        self.assertNotIn(secret, str(error.exception))
        with self.assertRaises(QuotaError):
            load_normalized_quota(self._import_file({"providers": {}}))

    def test_json_duplicate_keys_and_nonfinite_values_rejected(self):
        path = Path(self.directory.name) / "bad.json"
        for payload in ['{"schema_version":1,"schema_version":1}', '{"remaining":NaN}']:
            path.write_text(payload)
            with self.assertRaises(QuotaError):
                load_normalized_quota(path)


if __name__ == "__main__":
    unittest.main()
