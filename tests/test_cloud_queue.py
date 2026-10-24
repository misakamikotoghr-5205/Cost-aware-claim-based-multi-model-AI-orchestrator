"""Queue failures, fencing, concurrency and HTTP identity isolation (no models)."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.client import HTTPConnection
import json
import sys
from pathlib import Path
from threading import Thread

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.cloud_queue import CloudQueue, QueueConflict, QueueError, TaskNotFound
from src.cloud_server import make_server, tokens_from_environment


TOKEN_A = "token-for-node-a-0123456789abcdef"
TOKEN_B = "token-for-node-b-0123456789abcdef"


def payload(client="a", **changes):
    return dict(client_id=client, target=client, task="check repository",
                workdir="/workspace/project", tools=["git", "browser"],
                max_actions=20, **changes)


def verified():
    return {"verified": True, "evidence": ["local verifier exited 0"]}


@pytest.fixture
def queue(tmp_path):
    return CloudQueue(tmp_path / "queue.sqlite")


def test_persistent_idempotent_enqueue_and_conflicting_retry(queue):
    first = queue.enqueue("t1", payload(), "key1")
    assert queue.enqueue("t1", payload(), "key1") == first
    reopened = CloudQueue(queue.path)
    assert reopened.get("t1", "a")["status"] == "queued"
    changed = payload()
    changed["max_actions"] = 2
    with pytest.raises(QueueConflict):
        queue.enqueue("t1", changed, "key1")
    with pytest.raises(QueueConflict):
        queue.enqueue("t2", payload(), "key1")
    with pytest.raises(QueueConflict):
        queue.enqueue("t1", payload("b"), "other")


def test_claim_client_and_target_isolation(queue):
    queue.enqueue("a1", payload())
    other = payload("b")
    other["target"] = "windows"
    queue.enqueue("b1", other)
    assert queue.claim("c") is None
    assert queue.claim("b", target="a") is None
    assert queue.claim("a")["task_id"] == "a1"
    assert queue.claim("b", target="windows")["task_id"] == "b1"
    with pytest.raises(TaskNotFound):
        queue.get("a1", "b")


def test_atomic_claim_across_workers(queue):
    for index in range(16):
        queue.enqueue(f"t{index}", payload())
    def claim(index):
        return CloudQueue(queue.path).claim("a", request_id=f"worker{index}")
    with ThreadPoolExecutor(max_workers=8) as workers:
        claimed = list(workers.map(claim, range(24)))
    ids = [task["task_id"] for task in claimed if task]
    assert len(ids) == len(set(ids)) == 16


def test_claim_reconnect_and_completion_are_idempotent(queue):
    queue.enqueue("t1", payload())
    queue.enqueue("t2", payload())
    lease = queue.claim("a", request_id="request-1")
    assert queue.claim("a", request_id="request-1") == lease
    assert queue.get(lease["task_id"], "a")["lease_token"] == lease["lease_token"]
    args = (lease["task_id"], "a", lease["lease_token"], verified())
    complete = queue.complete(*args)
    assert complete["status"] == "done"
    assert queue.complete(*args) == complete
    assert queue.claim("a", request_id="request-1")["status"] == "done"
    with pytest.raises(QueueConflict):
        queue.complete(lease["task_id"], "a", lease["lease_token"], {"verified": True, "evidence": ["different"]})
    assert queue.claim("a", request_id="request-2")["task_id"] == "t2"


def test_lost_lease_becomes_unknown_and_old_worker_cannot_ack(tmp_path):
    now = [1000.0]
    queue = CloudQueue(tmp_path / "queue.sqlite", clock=lambda: now[0])
    queue.enqueue("t1", payload())
    lease = queue.claim("a", 2, request_id="first")
    now[0] = 1002.0
    with pytest.raises(QueueConflict):
        queue.complete("t1", "a", lease["lease_token"], verified())
    # The rejected acknowledgement must still persist expiration.
    assert queue.get("t1", "a")["status"] == "unknown"
    assert queue.claim("a", request_id="second") is None
    assert queue.claim("a", request_id="first")["status"] == "unknown"
    with pytest.raises(QueueConflict):
        queue.heartbeat("t1", "a", lease["lease_token"])


def test_heartbeat_fencing_and_cancellation(queue):
    queue.enqueue("t1", payload())
    lease = queue.claim("a")
    assert lease["fence"] == 1
    with pytest.raises(QueueConflict):
        queue.heartbeat("t1", "a", "wrong")
    assert queue.heartbeat("t1", "a", lease["lease_token"])["status"] == "claimed"
    assert queue.cancel("t1", "a")["status"] == "unknown"
    assert queue.claim("a") is None
    queue.enqueue("t2", payload())
    assert queue.cancel("t2", "a")["status"] == "cancelled"


def test_done_requires_verification_evidence(queue):
    queue.enqueue("t1", payload())
    lease = queue.claim("a")
    for result in ({}, {"verified": True}, {"verified": True, "evidence": []}, {"verified": "yes", "evidence": ["ok"]}):
        with pytest.raises(QueueError):
            queue.complete("t1", "a", lease["lease_token"], result)
    assert queue.complete("t1", "a", lease["lease_token"], {"error": "verification failed"}, "failed")["status"] == "failed"


@pytest.mark.parametrize("field,value", [
    ("workdir", "relative/path"), ("workdir", "/tmp/../etc"),
    ("workdir", "\\relative"), ("max_actions", True), ("max_actions", 0),
    ("max_actions", 1001), ("tools", ["shell; rm"]), ("tools", ["git", "git"]),
    ("metadata", {"number": float("nan")}), ("task", ""), ("client_id", "../node"),
])
def test_invalid_execution_constraints_rejected(queue, field, value):
    task = payload()
    task[field] = value
    with pytest.raises(QueueError):
        queue.enqueue("t1", task)


@contextmanager
def running_server(queue, ledger=None):
    server = make_server("127.0.0.1", 0, queue, {
        TOKEN_A: {"client_id": "a", "accounts": ["subscription"]},
        TOKEN_B: {"client_id": "b", "accounts": []},
    }, ledger)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(server, path, body=None, token=TOKEN_A, method=None):
    connection = HTTPConnection(*server.server_address, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    encoded = json.dumps(body) if body is not None else None
    connection.request(method or ("POST" if body is not None else "GET"), path, encoded, headers)
    response = connection.getresponse()
    result = response.status, json.loads(response.read())
    connection.close()
    return result


def test_http_auth_identity_bound_claim_and_done(queue):
    with running_server(queue) as server:
        assert request(server, "/v1/claim", {"request_id": "c1"}, token=None)[0] == 401
        assert request(server, "/v1/tasks", {"task_id": "t1", "payload": payload("b")})[0] == 403
        assert request(server, "/v1/tasks", {"task_id": "t1", "payload": payload()})[0] == 200
        assert request(server, "/v1/tasks/t1", token=TOKEN_B)[0] == 404
        assert request(server, "/v1/claim", {"request_id": "b1"}, token=TOKEN_B)[1]["task"] is None
        assert request(server, "/v1/claim", {"request_id": "b2", "target": "a"}, token=TOKEN_B)[0] == 403
        status, claimed = request(server, "/v1/claim", {"request_id": "a1"})
        assert status == 200
        lease = claimed["task"]["lease_token"]
        assert request(server, "/v1/tasks/t1/complete", {"lease_token": lease, "result": {}})[0] == 400
        assert request(server, "/v1/tasks/t1/complete", {"lease_token": lease, "result": verified()})[1]["task"]["status"] == "done"
        assert request(server, "/v1/claim", {"request_id": "a1"})[1]["task"]["status"] == "done"


def test_http_payload_error_and_unavailable_quota(queue):
    with running_server(queue) as server:
        assert request(server, "/v1/tasks", {"task_id": "t1", "payload": payload(), "command": "sh"})[0] == 400
        assert request(server, "/v1/quota?account=subscription")[0] == 503
        assert request(server, "/v1/claim", {})[0] == 400
        assert request(server, "/v1/claim", {"request_id": "a1", "lease_seconds": float("nan")})[0] == 400


def test_credentials_require_provisioned_secret_mapping(queue, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_CLOUD_TOKENS", raising=False)
    with pytest.raises(ValueError):
        tokens_from_environment()
    with pytest.raises(ValueError):
        make_server(queue=queue, tokens={"weak": "a"})
    monkeypatch.setenv("ORCHESTRATOR_CLOUD_TOKENS", json.dumps({TOKEN_A: "a"}))
    assert tokens_from_environment() == {TOKEN_A: "a"}


def test_quota_http_contract_namespaces_reservations_and_binds_accounts(queue):
    class Ledger:
        def __init__(self):
            self.reservations = {}

        def snapshot(self, account, model="*"):
            return {"account": account, "model": model, "available": 9, "fresh": True}

        def reserve(self, reservation_id, account, amount, model="*", unit=None, minimum_remaining=0):
            result = dict(reservation_id=reservation_id, account=account, amount=amount,
                          model=model, unit=unit, minimum_remaining=minimum_remaining, state="RESERVED")
            self.reservations[reservation_id] = result
            return result

        def get_reservation(self, reservation_id):
            return self.reservations[reservation_id]

        def settle(self, reservation_id, actual_amount):
            result = self.reservations[reservation_id]
            result.update(state="SETTLED", actual_amount=actual_amount)
            return result

        def release(self, reservation_id):
            self.reservations[reservation_id]["state"] = "RELEASED"
            return self.reservations[reservation_id]

        def mark_unknown(self, reservation_id):
            self.reservations[reservation_id]["state"] = "UNKNOWN"
            return self.reservations[reservation_id]

    ledger = Ledger()
    with running_server(queue, ledger) as server:
        assert request(server, "/v1/quota?account=subscription")[1]["quota"]["available"] == 9
        assert request(server, "/v1/quota?account=other")[0] == 403
        assert request(server, "/v1/quota?account=subscription", token=TOKEN_B)[0] == 403
        status, data = request(server, "/v1/quota/reserve", {"reservation_id": "r1", "account": "subscription", "amount": 1, "unit": "requests", "minimum_remaining": 3})
        assert status == 200
        assert data["quota"]["reservation_id"] == "1:a:r1"
        assert data["quota"]["minimum_remaining"] == 3
        for invalid_floor in (True, False, -1, "2", None, 1e16, 0.0000000001, float("nan"), float("inf")):
            assert request(server, "/v1/quota/reserve", {"reservation_id": "bad", "account": "subscription", "amount": 1, "minimum_remaining": invalid_floor})[0] == 400
        assert request(server, "/v1/quota/reserve", {"reservation_id": "r1", "account": "subscription", "amount": 1}, token=TOKEN_B)[0] == 403
        assert request(server, "/v1/quota/settle", {"reservation_id": "r1", "actual_amount": 1})[1]["quota"]["state"] == "SETTLED"
        # Supplied identifiers cannot escape the caller's namespace.
        assert request(server, "/v1/quota/unknown", {"reservation_id": "1:a:r1"}, token=TOKEN_B)[0] != 200
        assert request(server, "/v1/quota/release", {"reservation_id": "r1", "confirmed_unused": True})[0] == 400


def test_same_client_different_targets_still_isolated(queue):
    token_other = "token-same-client-other-target-0123456789"
    server = make_server("127.0.0.1", 0, queue, {
        TOKEN_A: {"client_id": "a", "target": "a"},
        token_other: {"client_id": "a", "target": "linux"},
    })
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        queue.enqueue("t1", payload())
        assert request(server, "/v1/tasks/t1", token=token_other)[0] == 404
        assert request(server, "/v1/claim", {"request_id": "linux-poll"}, token=token_other)[1]["task"] is None
        assert request(server, "/v1/tasks/t1/cancel", {}, token=token_other)[0] == 404
        assert queue.get("t1", "a")["status"] == "queued"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_real_ledger_atomic_floor_and_unknown_pool_block(queue, tmp_path):
    # This optional subsystem is brought in by the quota PR. After integration
    # this runs against the actual shared ledger, not an HTTP contract double.
    ledger_module = pytest.importorskip("src.quota_ledger")
    ledger = ledger_module.QuotaLedger(tmp_path / "floor-ledger.sqlite")
    ledger.set_quota("subscription", 10, unit="requests")
    with running_server(queue, ledger) as server:
        def reserve(reservation_id, amount, minimum_remaining):
            return request(server, "/v1/quota/reserve", {
                "reservation_id": reservation_id, "account": "subscription",
                "amount": amount, "unit": "requests", "minimum_remaining": minimum_remaining,
            })
        assert reserve("initial", 4, 6)[0] == 200
        assert reserve("would-spend-floor", 1, 6)[0] == 409
        assert reserve("initial", 4, 5)[0] == 409
        assert request(server, "/v1/quota/unknown", {"reservation_id": "initial"})[0] == 200
        assert reserve("unknown-must-block", 1, 0)[0] == 409
        assert ledger.snapshot("subscription")["status"] == "blocked"
        # Known usage reconciliation reopens the pool with six requests left.
        assert request(server, "/v1/quota/settle", {"reservation_id": "initial", "actual_amount": 4})[0] == 200
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(lambda index: reserve(f"parallel-{index}", 1, 2)[0], range(8)))
        assert results.count(200) == 4
        assert results.count(409) == 4
        assert ledger.snapshot("subscription")["available"] == 2
