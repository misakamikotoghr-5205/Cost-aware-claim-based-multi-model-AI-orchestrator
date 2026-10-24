"""Real HTTP owner/node isolation, multi-node dispatch and disconnect fencing."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
import json
from threading import Thread

import pytest

from src.cloud_queue import CloudQueue
from src.cloud_server import make_server
from src.node_registry import NodeRegistry, quota_account, reservation_prefix
from src.quota_ledger import QuotaLedger


OWNER_A = "owner-alice-token-0123456789abcdef"
OWNER_B = "owner-bob-token-0123456789abcdef"


def caps(**changes):
    result = dict(platform="linux", tools=["git"], skills=[], models=["qwen3:4b"],
                  executors=["ollama"], workspace_aliases=["default"], max_actions=20)
    result.update(changes)
    return result


def req(server, path, body=None, token=OWNER_A):
    conn = HTTPConnection(*server.server_address, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    conn.request("GET" if body is None else "POST", path,
                 None if body is None else json.dumps(body), headers)
    response = conn.getresponse()
    result = response.status, json.loads(response.read())
    conn.close()
    return result


@pytest.fixture
def fleet(tmp_path):
    now = [1000.0]
    queue = CloudQueue(tmp_path / "queue.sqlite", clock=lambda: now[0])
    ledger = QuotaLedger(tmp_path / "quota.sqlite", clock=lambda: now[0])
    registry = NodeRegistry(queue.path, clock=lambda: now[0])
    server = make_server("127.0.0.1", 0, queue, {
        OWNER_A: {"role": "owner", "owner_id": "alice", "accounts": ["subscription"]},
        OWNER_B: {"role": "owner", "owner_id": "bob", "accounts": ["subscription"]},
    }, ledger, registry)
    server.test_clock = now
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def pair(server, *, token=OWNER_A, capabilities=None, accounts=None):
    status, result = req(server, "/v1/pairings", {"accounts": accounts or []}, token)
    assert status == 200
    status, node = req(server, "/v1/pairings/redeem", {
        "code": result["pairing"]["code"], "label": "Local node",
        "capabilities": capabilities or caps()}, token=None)
    assert status == 200
    return node


def task(node, task_id, **changes):
    payload = dict(client_id=node["node"]["owner_id"], target=node["node"]["node_id"],
                   task="check project", workspace_alias="default", relative_dir=".",
                   tools=["git"], max_actions=10)
    payload.update(changes)
    return {"task_id": task_id, "payload": payload}


def test_owner_dispatches_two_nodes_and_workers_are_target_bound(fleet):
    one, two = pair(fleet), pair(fleet, capabilities=caps(platform="windows"))
    assert len(req(fleet, "/v1/nodes")[1]["nodes"]) == 2
    assert req(fleet, "/v1/nodes", token=OWNER_B)[1]["nodes"] == []
    assert req(fleet, "/v1/nodes", token=one["token"])[0] == 403
    for node, tid in ((one, "t1"), (two, "t2")):
        assert req(fleet, "/v1/tasks", task(node, tid))[0] == 200
    def claim(node):
        return req(fleet, "/v1/claim", {"request_id": "same-id"}, node["token"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, (one, two)))
    assert [response[1]["task"]["task_id"] for response in claimed] == ["t1", "t2"]
    assert req(fleet, "/v1/tasks/t2", token=one["token"])[0] == 404
    assert req(fleet, "/v1/tasks/t1", token=OWNER_B)[0] == 404
    assert req(fleet, "/v1/tasks/t2")[0] == 200
    assert req(fleet, "/v1/claim", {"request_id": "owner-cannot-claim"})[0] == 403
    assert req(fleet, "/v1/claim", {"request_id": "steal", "target": two["node"]["node_id"]}, one["token"])[0] == 403
    assert req(fleet, "/v1/tasks", task(one, "node-cannot-submit"), one["token"])[0] == 403
    first = claimed[0][1]["task"]
    completion = {"lease_token": first["lease_token"], "result": {"verified": True, "evidence": ["tested"]}}
    assert req(fleet, "/v1/tasks/t1/complete", completion)[0] == 403
    assert req(fleet, "/v1/tasks/t1/complete", completion, two["token"])[0] == 404
    assert req(fleet, "/v1/tasks/t1/complete", completion, one["token"])[1]["task"]["status"] == "done"
    assert req(fleet, "/v1/claim", {"request_id": "same-id"}, one["token"])[1]["task"]["status"] == "done"


def test_selection_capability_admission_and_alias_validation(fleet):
    node = pair(fleet)
    assert req(fleet, "/v1/nodes/select", {"tools": ["git"], "workspace_alias": "default"})[1]["node"]["node_id"] == node["node"]["node_id"]
    assert req(fleet, "/v1/nodes/select", {"platform": "windows"})[1]["node"] is None
    assert req(fleet, "/v1/tasks", task(node, "tools", tools=["computer"]))[0] == 409
    assert req(fleet, "/v1/tasks", task(node, "missing", workspace_alias="private"))[0] == 409
    assert req(fleet, "/v1/tasks", task(node, "traversal", relative_dir="../private"))[0] == 400
    assert req(fleet, "/v1/tasks", task(node, "absolute", relative_dir="/etc"))[0] == 400
    assert req(fleet, "/v1/tasks", task(node, "windows", relative_dir="C:/Users/private"))[0] == 400
    assert req(fleet, "/v1/tasks", task(node, "mixed", workdir="/workspace"))[0] == 400
    assert req(fleet, "/v1/tasks", task(node, "wrong-owner"), OWNER_B)[0] == 403


def test_quota_owner_and_node_namespaces_and_account_grants(fleet):
    one, two = pair(fleet, accounts=["subscription"]), pair(fleet, accounts=["subscription"])
    no_grant = pair(fleet)
    fleet.ledger.set_quota(quota_account("alice", "subscription"), 10, capacity=10)
    fleet.ledger.set_quota(quota_account("bob", "subscription"), 99, capacity=99)
    assert req(fleet, "/v1/quota?account=subscription", token=one["token"])[1]["quota"]["remaining"] == 10
    assert req(fleet, "/v1/quota?account=subscription", token=OWNER_B)[1]["quota"]["remaining"] == 99
    assert req(fleet, "/v1/quota?account=subscription", token=no_grant["token"])[0] == 403
    assert req(fleet, "/v1/pairings", {"accounts": ["someone-else"]})[0] == 403
    for node in (one, two):
        status, result = req(fleet, "/v1/quota/reserve", {"reservation_id": "r1", "account": "subscription", "amount": 3}, node["token"])
        assert status == 200 and result["quota"]["replayed"] is False
        assert result["quota"]["account"] == "subscription"
    assert req(fleet, "/v1/quota?account=subscription")[1]["quota"]["held"] == 6
    other_key = reservation_prefix("alice", two["node"]["node_id"]) + "r1"
    assert req(fleet, "/v1/quota/settle", {"reservation_id": other_key, "actual_amount": 3}, one["token"])[0] != 200
    assert req(fleet, "/v1/quota/settle", {"reservation_id": "r1", "actual_amount": 2}, one["token"])[1]["quota"]["state"] == "SETTLED"
    assert req(fleet, "/v1/quota?account=subscription", token=OWNER_B)[1]["quota"]["remaining"] == 99


def test_revocation_invalidates_auth_tasks_and_unsettled_reservations(fleet):
    node = pair(fleet, accounts=["subscription"])
    fleet.ledger.set_quota(quota_account("alice", "subscription"), 10, capacity=10)
    for tid in ("running", "queued"):
        assert req(fleet, "/v1/tasks", task(node, tid))[0] == 200
    claimed = req(fleet, "/v1/claim", {"request_id": "start"}, node["token"])[1]["task"]
    assert req(fleet, "/v1/quota/reserve", {"reservation_id": "r1", "account": "subscription", "amount": 2}, node["token"])[0] == 200
    url = "/v1/nodes/" + node["node"]["node_id"] + "/revoke"
    assert req(fleet, url, {}, OWNER_B)[0] == 404
    assert req(fleet, url, {})[1]["node"]["revoked_at"] == 1000
    assert req(fleet, "/v1/nodes/self/heartbeat", {}, node["token"])[0] == 401
    assert req(fleet, "/v1/claim", {"request_id": "again"}, node["token"])[0] == 401
    statuses = {req(fleet, "/v1/tasks/" + tid)[1]["task"]["status"] for tid in ("running", "queued")}
    assert statuses == {"unknown", "cancelled"}
    assert req(fleet, "/v1/tasks/" + claimed["task_id"] + "/complete", {"lease_token": claimed["lease_token"], "result": {}}, node["token"])[0] == 401
    assert req(fleet, "/v1/quota?account=subscription")[1]["quota"]["blocked"] is True


def test_disconnect_reconnect_never_replays_a_claim_and_freezes_pool(fleet):
    node = pair(fleet, accounts=["subscription"])
    fleet.ledger.set_quota(quota_account("alice", "subscription"), 10, capacity=10)
    assert req(fleet, "/v1/tasks", task(node, "lost"))[0] == 200
    body = {"request_id": "stable", "lease_seconds": 600}
    req(fleet, "/v1/claim", body, node["token"])
    req(fleet, "/v1/quota/reserve", {"reservation_id": "r1", "account": "subscription", "amount": 1}, node["token"])
    fleet.test_clock[0] += 90
    assert req(fleet, "/v1/nodes")[1]["nodes"][0]["online"] is False
    assert req(fleet, "/v1/tasks/lost")[1]["task"]["status"] == "unknown"
    assert req(fleet, "/v1/claim", body, node["token"])[0] == 403
    assert req(fleet, "/v1/nodes/self/heartbeat", {}, node["token"])[1]["node"]["online"] is True
    assert req(fleet, "/v1/claim", body, node["token"])[1]["task"]["status"] == "unknown"
    assert req(fleet, "/v1/claim", {"request_id": "new"}, node["token"])[1]["task"] is None
    assert req(fleet, "/v1/quota?account=subscription")[1]["quota"]["blocked"] is True


def test_pairing_public_errors_do_not_disclose_code_validity(fleet):
    pairing = req(fleet, "/v1/pairings", {"ttl_seconds": 1})[1]["pairing"]
    fleet.test_clock[0] += 1
    errors = []
    for code in (pairing["code"], "unknown-but-long-enough-0123456789"):
        status, body = req(fleet, "/v1/pairings/redeem", {"code": code, "label": "x", "capabilities": caps()}, token=None)
        assert status == 403
        errors.append(body)
    assert errors[0] == errors[1]


def test_task_lease_loss_freezes_quota_even_with_online_node_presence(fleet):
    node = pair(fleet, accounts=["subscription"])
    fleet.ledger.set_quota(quota_account("alice", "subscription"), 10, capacity=10)
    req(fleet, "/v1/tasks", task(node, "short-lease"))
    req(fleet, "/v1/claim", {"request_id": "short", "lease_seconds": 1}, node["token"])
    req(fleet, "/v1/quota/reserve", {"reservation_id": "r", "account": "subscription", "amount": 1}, node["token"])
    fleet.test_clock[0] += 1
    assert req(fleet, "/v1/nodes")[1]["nodes"][0]["online"] is True
    assert req(fleet, "/v1/tasks/short-lease")[1]["task"]["status"] == "unknown"
    assert req(fleet, "/v1/quota?account=subscription")[1]["quota"]["blocked"] is True
