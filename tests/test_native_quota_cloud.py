"""Read-only cloud dashboard projection, including an actual HTTP ledger."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from threading import Thread
import time
from types import SimpleNamespace

import pytest

from src.cloud_queue import CloudQueue
from src.cloud_server import CloudHandler, make_server
from src.fleet_client import FleetClient
from src.node_registry import quota_account
from src.quota_ledger import QuotaLedger


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("native_quota_cloud_tests",
    ROOT / "integrations/hermes-client/plugin/quota_cloud.py")
cloud = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cloud)


def core(settings, client=None):
    return SimpleNamespace(settings=lambda: deepcopy(settings), _fleet=lambda: client)


def settings(**states):
    return {"server": {"url": "http://127.0.0.1:1"}, "quota_states": states}


def snapshot(account="subscription", model="*", **changes):
    value = {"account": account, "model": model, "unit": "requests", "capacity": 100,
             "remaining": 80, "held": 5, "available": 75, "observed_at": time.time(),
             "max_age_seconds": 300, "reset_at": None, "fresh": True, "blocked": False,
             "status": "ready", "source": "provider-observation"}
    return {**value, **changes}


def test_unconfigured_never_resolves_credentials_or_uses_local_sample():
    value = core({"server": {"url": ""}, "quota_states": {"sample": snapshot()}})
    value._fleet = lambda: pytest.fail("credentials must not be consulted")
    result = cloud.read_cloud_quota(value)
    assert result["state"] == "unknown" and result["configured"] is False
    assert result["accounts"] == []


def test_only_explicit_pool_identities_are_used_and_duplicate_queries_collapse():
    seen = []
    client = SimpleNamespace(snapshot=lambda account, model: seen.append((account, model)) or snapshot(account, model))
    value = settings(first={"account": "subscription", "model": "*", "remaining": 999999},
                     duplicate={"account": "subscription", "model": "*", "remaining": 888888})
    result = cloud.read_cloud_quota(core(value, client))
    assert seen == [("subscription", "*")]
    window = result["accounts"][0]["windows"][0]
    assert window["remaining"] == 80 and window["available"] == 75 and window["held"] == 5
    assert window["used"] == 20 and result["state"] == "ready"
    assert "999999" not in json.dumps(result)


def test_fraction_unit_is_preserved_and_not_converted_to_tokens():
    client = SimpleNamespace(snapshot=lambda account, model: snapshot(account, model,
        unit="fraction", capacity=1, remaining=.7, held=.2, available=.5))
    result = cloud.read_cloud_quota(core(settings(a={"account": "subscription", "model": "*"}), client))
    window = result["accounts"][0]["windows"][0]
    assert window["unit"] == "fraction" and window["remaining"] == .7
    assert window["used"] == pytest.approx(.3)


def test_transport_errors_and_untrusted_fields_are_not_returned():
    def fail(*args):
        raise ValueError("Bearer SECRET-TRANSPORT-CREDENTIAL from server")
    result = cloud.read_cloud_quota(core(settings(a={"account": "subscription", "model": "*", "remaining": 123}),
                                        SimpleNamespace(snapshot=fail)))
    assert result["state"] == "unknown"
    assert result["accounts"][0]["windows"][0]["remaining"] is None
    assert "SECRET" not in json.dumps(result) and "123" not in json.dumps(result)
    client = SimpleNamespace(snapshot=lambda *args: snapshot(api_key="SECRET-UNTRUSTED"))
    assert "SECRET" not in json.dumps(cloud.read_cloud_quota(core(settings(a={"account": "subscription", "model": "*"}),client)))


@pytest.mark.parametrize("change", [{"account": "another"}, {"model": "another"},
    {"remaining": float("nan")}, {"remaining": True}, {"available": 1000},
    {"unit": ""}, {"observed_at": time.time()+3600}, {"capacity": 0}])
def test_invalid_cloud_snapshot_is_unknown(change):
    client = SimpleNamespace(snapshot=lambda *args: snapshot(**change))
    result = cloud.read_cloud_quota(core(settings(a={"account": "subscription", "model": "*"}),client))
    assert result["state"] == "unknown"
    assert result["accounts"][0]["windows"][0]["remaining"] is None


@pytest.mark.parametrize("change,expected", [({"blocked": True}, "blocked"),
    ({"observed_at": time.time()-301}, "stale"), ({"reset_at": time.time()-1}, "expired")])
def test_freshness_and_uncertain_consumption_are_rechecked(change, expected):
    client = SimpleNamespace(snapshot=lambda *args: snapshot(**change))
    result = cloud.read_cloud_quota(core(settings(a={"account": "subscription", "model": "*"}),client))
    assert result["state"] == "unknown"
    assert result["accounts"][0]["windows"][0]["state"] == expected


def test_no_declared_identity_never_queries_or_uses_native_model_metadata():
    value = settings(local_sample={"remaining": 100})
    value["models"] = [{"metadata": {"account": "not-a-declaration"}}]
    instance = core(value)
    instance._fleet = lambda: pytest.fail("no declared identity")
    assert cloud.read_cloud_quota(instance)["accounts"] == []


def test_real_http_dashboard_reads_ledger_and_does_not_reserve_or_claim(tmp_path):
    token = "temporary-cloud-dashboard-owner-test-token"
    queue = CloudQueue(tmp_path / "queue.sqlite")
    ledger = QuotaLedger(tmp_path / "quota.sqlite")
    ledger.set_quota(quota_account("owner", "subscription"), 80, capacity=100)
    ledger.reserve("existing-node-hold", quota_account("owner", "subscription"), 5)
    ledger.set_quota(quota_account("owner", "fraction-plan"), .6, unit="fraction", capacity=1)
    server = make_server("127.0.0.1", 0, queue, {token: {"role":"owner", "owner_id":"owner", "accounts":["subscription","fraction-plan"]}}, ledger)
    seen=[]
    class Observe(CloudHandler):
        def do_GET(self):
            seen.append(("GET", self.path))
            super().do_GET()
        def do_POST(self):
            seen.append(("POST", self.path))
            super().do_POST()
    server.RequestHandlerClass = Observe
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        client = FleetClient(f"http://127.0.0.1:{server.server_port}", token)
        before = ledger.snapshot(quota_account("owner", "subscription"))
        result = cloud.read_cloud_quota(core(settings(
            subscription={"account":"subscription", "model":"*", "remaining":999},
            fraction={"account":"fraction-plan", "model":"*"},
            unavailable={"account":"not-granted", "model":"*"}),client))
        after = ledger.snapshot(quota_account("owner", "subscription"))
        assert before == after
        assert len(seen) == 3 and all(method == "GET" and path.startswith("/v1/quota?") for method,path in seen)
        assert result["state"] == "partial"
        by_id = {account["id"]:account for account in result["accounts"]}
        assert by_id["subscription"]["windows"][0]["available"] == 75
        assert by_id["fraction-plan"]["windows"][0]["unit"] == "fraction"
        assert by_id["not-granted"]["state"] == "unknown"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
