"""Discovery contract tests use recorded protocol shapes and no model/network calls."""
from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.discovery import (DiscoveryCache, DiscoveryError, ModelCard,
                           parse_free_models_monitor, parse_ollama, parse_token_scout,
                           read_json_file, run_json_command)


def card(model="small", **kwargs):
    return ModelCard(model_id=model, provider="ollama", executor="ollama",
                     billing_mode="local", available=True, observed_at=100,
                     source="ollama", **kwargs)


def scout_match(**overrides):
    # Rust ScoutResult fields at upstream d1ddb98 (not the status-only response).
    return {"model": "org/zero:free", "model_name": "Zero", "provider": "OpenRouter",
            "endpoint": "https://openrouter.ai/api/v1", "api_style": "openai",
            "api_key_env": "OPENROUTER_API_KEY", "strengths": ["code", "reasoning"],
            "context_len": 32768, "prompt_cost": 0.0, "completion_cost": 0.0,
            "compat": {"tool_format": "OpenAIFunction", "tool_reliability": "Claimed",
                       "modality": "text+image"},
            "quota": {"requests_remaining": 50, "requests_limit": 50}, **overrides}


def monitor_report(**overrides):
    return {"changed": False, "changes": [], "provider_status": {"openrouter": "fresh"},
            "provider_counts": {"openrouter": 1}, "complete": True, "errors": [],
            "initialized": False, **overrides}


def test_token_scout_matches_jsonrpc_and_mcp_preserve_provenance():
    result = {"matches": [scout_match()]}
    for payload in (result, {"jsonrpc": "2.0", "id": 1, "result": result},
                    {"content": [{"type": "text", "text": json.dumps(result)}]}):
        [found] = parse_token_scout(payload, observed_at=100)
        assert found.model_id == "org/zero:free"
        assert found.billing_mode == "free"
        # Scout mixes live discovery and static fallback without labelling them.
        assert not found.available
        assert found.quota_remaining is None
        assert found.capabilities["code_generation"] == 0.5
        assert found.metadata["compat"]["tool_reliability"] == "Claimed"
        assert "api_key_env" not in found.metadata


def test_unknown_price_or_free_suffix_is_not_free():
    row = scout_match()
    row.pop("prompt_cost")
    row.pop("completion_cost")
    [found] = parse_token_scout({"matches": [row]}, observed_at=100)
    assert found.billing_mode == "paid_api"
    assert not found.is_eligible(now=100)
    with pytest.raises(DiscoveryError, match="status-only"):
        parse_token_scout({"providers": []}, observed_at=100)


@pytest.mark.parametrize("price", [-1, True, "NaN", "infinity", "unknown"])
def test_bad_prices_rejected(price):
    with pytest.raises(DiscoveryError):
        parse_token_scout({"matches": [scout_match(prompt_cost=price)]}, observed_at=100)


def test_scout_local_and_endpoint_secrets_are_not_persisted():
    row = scout_match(model="tiny:latest", provider="localhost", api_style="ollama",
                      endpoint="http://name:secret@localhost:11434/api?token=secret#secret")
    [found] = parse_token_scout({"matches": [row]}, observed_at=100)
    assert found.executor == "ollama" and found.is_eligible(now=100)
    assert "secret" not in json.dumps(found.to_dict())
    assert found.metadata["endpoint"] == "http://localhost:11434/api"


def test_ollama_tags_do_not_guess_model_capabilities_or_cloud_fees():
    cards = parse_ollama({"models": [{"name": "qwen:4b", "digest": "abc"},
                                     {"name": "big:cloud", "remote_host": "ollama.com"}]}, observed_at=100)
    assert cards[0].billing_mode == "local" and cards[0].available
    assert cards[0].capabilities == {}  # /api/show or local evaluation is separate.
    assert cards[1].billing_mode == "paid_api" and not cards[1].available
    with pytest.raises(DiscoveryError):
        parse_ollama({"models": [{"name": "duplicate"}, {"name": "duplicate"}]})
    with pytest.raises(DiscoveryError):
        parse_ollama({"error": "offline"})


def test_fmm_full_snapshot_requires_fresh_same_run_report():
    snapshot = {"openrouter/org/m:free": {"name": "m", "context_length": 8192}}
    [found] = parse_free_models_monitor(snapshot, monitor_report(), observed_at=100)
    assert found.provider == "openrouter" and found.model_id == "org/m:free"
    assert found.billing_mode == "free" and found.is_eligible(now=100)
    with pytest.raises(DiscoveryError, match="count mismatch"):
        parse_free_models_monitor(snapshot, monitor_report(provider_counts={"openrouter": 2}))
    with pytest.raises(DiscoveryError):
        parse_free_models_monitor(snapshot, {"changes": []})
    assert parse_free_models_monitor({}, monitor_report(provider_counts={"openrouter": 0})) == []


def test_fmm_failed_cached_and_manual_groq_are_unavailable():
    snapshot = {"openrouter/org/m:free": {"context_length": 8192},
                "groq/legacy": {"free_tier_verified": False}}
    report = monitor_report(provider_status={"openrouter": "cached", "groq": "static_unverified"},
                            provider_counts={"groq": 1}, complete=False, errors=["OpenRouter offline"])
    cards = parse_free_models_monitor(snapshot, report, observed_at=100)
    assert len(cards) == 2 and all(not candidate.available for candidate in cards)
    assert all(candidate.billing_mode == "paid_api" for candidate in cards)


def test_cache_ttl_persists_across_restart_and_does_not_refresh_on_read(tmp_path):
    path = tmp_path / "models.json"
    cache = DiscoveryCache(path, ttl_seconds=10)
    assert [event.kind for event in cache.apply("ollama", [card()], now=100)] == ["added"]
    assert DiscoveryCache(path, ttl_seconds=900).load(now=109)
    assert DiscoveryCache(path, ttl_seconds=900).load(now=110) == []
    [expired] = cache.load(now=110, include_stale=True)
    assert not expired.available and expired.observed_at == 100
    assert expired.metadata["discovery_status"] == "stale"


def test_promotion_expiry_and_zero_quota_exclude_candidates(tmp_path):
    promotion = replace(card(), billing_mode="free", expires_at=105)
    cache = DiscoveryCache(tmp_path / "models.json", ttl_seconds=900)
    cache.apply("ollama", [promotion], now=100)
    assert cache.load(now=104)
    assert cache.load(now=105) == []
    assert cache.load(now=106, include_stale=True)[0].metadata["discovery_status"] == "expired"
    assert not replace(card(), quota_remaining=0).is_eligible(now=100)
    assert not replace(card(), billing_mode="paid_api").is_eligible(now=100)
    assert replace(card(), billing_mode="paid_api").is_eligible(now=100, allow_paid=True)


def test_cache_events_refresh_update_remove_and_source_isolation(tmp_path):
    cache = DiscoveryCache(tmp_path / "models.json")
    cache.apply("ollama", [card()], now=100)
    assert cache.apply("ollama", [replace(card(), observed_at=101)], now=101) == []
    events = cache.apply("ollama", [replace(card(), capabilities={"reasoning": 0.7}, observed_at=102)], now=102)
    assert [event.kind for event in events] == ["updated"]
    other = replace(card("other"), source="manual")
    cache.apply("manual", [other], now=102)
    assert [e.kind for e in cache.apply("ollama", [], now=103)] == ["removed"]
    assert [c.model_id for c in cache.load(now=103)] == ["other"]


def test_partial_refresh_does_not_remove_or_extend_unobserved_deadline(tmp_path):
    path = tmp_path / "models.json"
    cache = DiscoveryCache(path, ttl_seconds=10)
    cache.apply("ollama", [card("old"), card("seen")], now=100)
    # Reopening with a longer TTL must not extend old evidence during a partial refresh.
    cache = DiscoveryCache(path, ttl_seconds=900)
    events = cache.apply("ollama", [replace(card("seen"), observed_at=105)], now=105, complete=False)
    assert not events
    assert {c.model_id for c in cache.load(now=111)} == {"seen"}


def test_invalid_refresh_is_atomic_and_keeps_previous_cache(tmp_path):
    path = tmp_path / "models.json"
    cache = DiscoveryCache(path)
    cache.apply("ollama", [card()], now=100)
    before = path.read_bytes()
    with pytest.raises(DiscoveryError):
        cache.apply("ollama", [replace(card(), source="wrong")], now=100)
    with pytest.raises(DiscoveryError):
        cache.apply("ollama", [replace(card(), observed_at=101)], now=100)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".models.json.*"))


@pytest.mark.parametrize("values", [{"available": "yes"}, {"billing_mode": []},
    {"observed_at": float("nan")}, {"quota_remaining": 2},
    {"capabilities": {"code": True}}, {"capabilities": {"code": -1}},
    {"metadata": {"cost": float("inf")}}])
def test_model_card_schema_rejects_ambiguous_data(values):
    payload = card().to_dict()
    payload.update(values)
    with pytest.raises(DiscoveryError):
        ModelCard.from_dict(payload)


def test_json_input_rejects_duplicates_constants_and_oversize(tmp_path):
    path = tmp_path / "input.json"
    for text in ('{"models":[],"models":[]}', '{"x":NaN}', 'null trailing'):
        path.write_text(text)
        with pytest.raises(DiscoveryError):
            read_json_file(path)
    path.write_text('{"models":[]}')
    with pytest.raises(DiscoveryError, match="size limit"):
        read_json_file(path, max_bytes=4)


def test_command_adapter_uses_argv_stdin_and_explicit_exit_codes(tmp_path):
    marker = tmp_path / "must-not-exist"
    literal = "$(touch " + str(marker) + "); echo no"
    result = run_json_command([sys.executable, "-c", "import sys,json;print(json.dumps(sys.argv[1:]))", literal])
    assert result == [literal] and not marker.exists()
    request = {"jsonrpc": "2.0", "method": "scout", "params": {"query": "code"}}
    assert run_json_command([sys.executable, "-c", "import sys;print(sys.stdin.read())"], input_payload=request) == request
    command = [sys.executable, "-c", 'print("{}");raise SystemExit(2)']
    assert run_json_command(command, accepted_returncodes=(0, 2)) == {}
    with pytest.raises(DiscoveryError, match="returned 2"):
        run_json_command(command)
    with pytest.raises(DiscoveryError, match="argv"):
        run_json_command("echo '{}'")
    with pytest.raises(DiscoveryError, match="timed out"):
        run_json_command([sys.executable, "-c", "import time;time.sleep(10)"], timeout_seconds=0.05)


def test_errors_do_not_echo_child_stderr_or_parse_mcp_error():
    with pytest.raises(DiscoveryError) as caught:
        run_json_command([sys.executable, "-c", 'import sys;print("secret-key",file=sys.stderr);raise SystemExit(1)'])
    assert "secret-key" not in str(caught.value)
    with pytest.raises(DiscoveryError):
        parse_token_scout({"isError": True, "content": [{"type": "text", "text": '{"matches":[]}'}]})
