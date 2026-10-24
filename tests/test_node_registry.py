"""Durable, single-use pairing and capability-based owner isolation."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from src.cloud_queue import QueueError, TaskNotFound
from src.node_registry import (NodeRegistry, PairingError, quota_account,
                               reservation_prefix, validate_capabilities)


def capabilities(**updates):
    result = {"platform": "linux", "tools": ["git"], "skills": [], "models": ["qwen3:4b"],
              "executors": ["ollama"], "workspace_aliases": ["default"], "max_actions": 20}
    result.update(updates)
    return result


def test_pairing_tokens_are_hashed_persistent_and_one_use(tmp_path):
    path = tmp_path / "registry.sqlite"
    registry = NodeRegistry(path)
    code = registry.create_pairing("alice", accounts=["subscription"])
    redeemed = registry.redeem(code["code"], "Ubuntu", capabilities())
    token, node = redeemed["token"], redeemed["node"]
    assert NodeRegistry(path).authenticate(token)["node_id"] == node["node_id"]
    assert node["owner_id"] == "alice"
    with sqlite3.connect(path) as connection:
        stored = repr(connection.execute("SELECT * FROM fleet_pairings").fetchall())
        stored += repr(connection.execute("SELECT * FROM fleet_nodes").fetchall())
    assert token not in stored and code["code"] not in stored
    assert "token_hash" not in repr(registry.list("alice"))
    with pytest.raises(PairingError, match="pairing could not be redeemed"):
        registry.redeem(code["code"], "Duplicate", capabilities())


def test_pairing_redemption_is_atomic_across_connections(tmp_path):
    path = tmp_path / "registry.sqlite"
    registry = NodeRegistry(path)
    pairing = registry.create_pairing("a")
    def redeem(_):
        try:
            return NodeRegistry(path).redeem(pairing["code"], "node", capabilities())["node"]
        except PairingError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(redeem, range(20)))
    assert sum(node is not None for node in results) == 1
    assert len(registry.list("a")) == 1


def test_expired_unknown_and_consumed_codes_have_same_error(tmp_path):
    now = [1000]
    registry = NodeRegistry(tmp_path / "r.db", clock=lambda: now[0])
    pairing = registry.create_pairing("a", ttl_seconds=10)
    now[0] += 10
    for code in (pairing["code"], "unknown" * 8, ""):
        with pytest.raises(PairingError) as error:
            registry.redeem(code, "node", capabilities())
        assert str(error.value) == "pairing could not be redeemed"


def test_owner_presence_matching_and_revocation(tmp_path):
    now = [1000]
    registry = NodeRegistry(tmp_path / "r.db", clock=lambda: now[0])
    codes = [registry.create_pairing("a") for _ in range(2)]
    one = registry.redeem(codes[0]["code"], "Linux", capabilities())
    two = registry.redeem(codes[1]["code"], "Windows", capabilities(platform="windows"))
    assert registry.list("b") == []
    with pytest.raises(TaskNotFound):
        registry.get("b", one["node"]["node_id"])
    assert registry.select("a", {"platform": "windows"})["node_id"] == two["node"]["node_id"]
    assert registry.select("a", {})["node_id"] == min(one["node"]["node_id"], two["node"]["node_id"])
    assert registry.select("a", {"tools": ["browser"]}) is None
    now[0] += 90
    assert registry.select("a", {}) is None
    registry.heartbeat("a", one["node"]["node_id"])
    assert registry.select("a", {})["node_id"] == one["node"]["node_id"]
    registry.revoke("a", one["node"]["node_id"])
    assert registry.authenticate(one["token"]) is None
    with pytest.raises(PermissionError):
        registry.heartbeat("a", one["node"]["node_id"])


@pytest.mark.parametrize("updates", [
    {"workspace_aliases": ["/home/user"]}, {"workspace_aliases": ["C:\\Users\\me"]},
    {"tools": ["C:/Users/me"]}, {"tools": ["../secret"]}, {"models": ["/tmp/model"]},
    {"api_key": "do-not-send"}, {"max_actions": True}, {"max_actions": 0},
    {"tools": ["git", "git"]}, {"platform": "private-system-info"},
])
def test_capabilities_reject_credentials_paths_and_bad_limits(updates):
    with pytest.raises(QueueError):
        validate_capabilities(capabilities(**updates))


def test_namespaces_do_not_alias_owners_nodes_or_legacy():
    assert quota_account("a:b", "c") != quota_account("a", "b:c")
    assert reservation_prefix("a:b", "c") != reservation_prefix("a", "b:c")
    assert reservation_prefix("a", None) != reservation_prefix("a", "a")
    assert reservation_prefix("a", "b") != reservation_prefix("b", "a")
    assert len(reservation_prefix("a" * 160, "b" * 160) + "c" * 160) <= 256
