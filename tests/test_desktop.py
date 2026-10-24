"""Exercise the unified desktop services without requiring a display server."""
from pathlib import Path
import json

import pytest

from src.desktop_service import (DesktopService, configure_backend, default_config,
                                 load_config, make_payload, save_config)


def test_configure_single_client_without_optional_extensions(tmp_path):
    config = configure_backend(default_config(tmp_path), kind="ollama", model="local-model",
                               provider="ollama", billing_mode="local", tools=[])
    path = tmp_path / "settings" / "client.json"
    save_config(path, config)
    loaded = load_config(path)
    assert loaded["extensions"] == {"discovery": {"enabled": False}, "judge": {"enabled": False}}
    payload = make_payload(loaded, "Summarize text", workdir=str(tmp_path), tools=[])
    result = DesktopService(loaded).run("desktop-plan", payload)
    assert result["executed"] is False
    assert result["route"]["selected"]["model_id"] == "local-model"
    assert not list(tmp_path.rglob("*.sqlite3-journal"))


def test_subscription_form_wires_model_adapter_and_quota_account(tmp_path):
    config = configure_backend(default_config(tmp_path), kind="hermes", model="codex-model",
                               provider="openai-codex", executable="/path with spaces/hermes",
                               tools=["terminal"], account="subscription", reservation_amount=0.01)
    card = config["models"][0]
    assert config["adapters"][card["executor"]]["command"] == ["/path with spaces/hermes"]
    assert config["model_accounts"]["codex-model"] == {"account": "subscription", "reservation_amount": 0.01}
    assert config["allowed_tools"] == ["terminal"]
    payload = make_payload(config, "Code", workdir=str(tmp_path), tools=["terminal"])
    plan = DesktopService(config).run("no-balance", payload)
    assert plan["route"]["selected"] is None  # a declaration never fabricates live balance


def test_replace_backend_cleans_up_previous_adapter(tmp_path):
    config = configure_backend(default_config(tmp_path), kind="ollama", model="model", provider="ollama", billing_mode="local")
    old = config["models"][0]["executor"]
    replacement = configure_backend(config, kind="ollama", model="model", provider="ollama", billing_mode="local")
    assert len(replacement["models"]) == len(replacement["adapters"]) == 1
    assert old not in replacement["adapters"]
    assert old in config["adapters"]  # caller snapshot remains unchanged


def test_desktop_rejects_path_escape_and_ungranted_tools(tmp_path):
    config = default_config(tmp_path)
    with pytest.raises(ValueError, match="inside"):
        make_payload(config, "task", workdir=str(tmp_path.parent), tools=[])
    with pytest.raises(ValueError, match="not enabled"):
        make_payload(config, "task", workdir=str(tmp_path), tools=["shell"])


def test_disabled_discovery_never_reads_input(tmp_path):
    with pytest.raises(ValueError, match="Enable"):
        DesktopService(default_config(tmp_path)).import_discovery(tmp_path / "absent.json", "ollama")


def test_save_rejects_raw_cloud_token_and_preserves_prior_file(tmp_path):
    path = tmp_path / "client.json"
    save_config(path, default_config(tmp_path))
    before = path.read_bytes()
    bad = default_config(tmp_path)
    bad["server"] = {"token": "do-not-save"}
    with pytest.raises(ValueError, match="environment"):
        save_config(path, bad)
    assert path.read_bytes() == before


def test_ledger_import_from_desktop_is_explicit(tmp_path):
    import time
    source = tmp_path / "quota.json"
    source.write_text(json.dumps({"schema_version": 1, "source": "manual-observation", "observations": [
        {"account": "subscription", "model": "*", "remaining": 0.8,
         "unit": "fraction", "observed_at": time.time()}]}))
    result = DesktopService(default_config(tmp_path)).import_quota(source)
    assert result[0]["remaining"] == 0.8
    config = default_config(tmp_path)
    config["server"] = {"url": "http://127.0.0.1:1", "token_env": "ABSENT_TOKEN"}
    with pytest.raises(ValueError, match="cloud host"):
        DesktopService(config).import_quota(source)


def test_desktop_module_can_import_without_display():
    import src.desktop
    assert callable(src.desktop.main)


def test_basic_settings_preserve_advanced_parameters(tmp_path):
    from src.desktop import DesktopApp
    from types import SimpleNamespace
    class Variable:
        def __init__(self, value):
            self.value = value
        def get(self):
            return self.value
    app = object.__new__(DesktopApp)
    app.config = default_config(tmp_path)
    app.config["extensions"]["judge"]["config"] = {"timeout": 1.5}
    app.config["extensions"]["discovery"]["ttl_seconds"] = 42
    app.config["server"] = {"timeout": 3}
    app.config["verification"] = {"timeout": 17}
    values = {"workspace": str(tmp_path), "target": "desktop", "client_id": "desktop",
              "judge_enabled": False, "judge_url": "http://127.0.0.1:11434", "judge_model": "judge",
              "discovery_enabled": False, "cache": str(tmp_path / "cache.json"),
              "verifier": '["python", "-m", "pytest"]', "cloud_enabled": True,
              "cloud_url": "http://127.0.0.1:8787", "token_env": "TEST_TOKEN", "private_http": False}
    for name, value in values.items():
        setattr(app, name, Variable(value))
    updated = app._collect()
    assert updated["extensions"]["judge"]["config"]["timeout"] == 1.5
    assert updated["extensions"]["discovery"]["ttl_seconds"] == 42
    assert updated["server"]["timeout"] == 3
    assert updated["verification"]["timeout"] == 17
    assert app.config["verification"] == {"timeout": 17}
