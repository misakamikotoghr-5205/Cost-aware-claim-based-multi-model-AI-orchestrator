"""Profile-local, read-only quota telemetry. Never an execution entitlement.

Credentials are resolved by Hermes in the backend only. Views persist numeric
observations and provenance, never provider payloads, headers or tokens.
"""
from __future__ import annotations

from contextvars import copy_context
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .quota_sources import parse_normalized_quota, _object, _invalid_constant
from .quota_ledger import _amount, _number

TTL = 300
MAX_REFRESH_ACCOUNTS = 16
REFRESH_START_BUDGET_SECONDS = 30
_REFRESH_SLOTS = threading.BoundedSemaphore(4)
SECRET_KEYS = {"api_key", "apikey", "access_token", "refresh_token", "authorization",
               "password", "cookie", "cookies", "credentials", "auth", "token", "headers"}
OFFICIAL = {"openai-codex": ("chatgpt.com",), "anthropic": ("api.anthropic.com",),
            "openrouter": ("openrouter.ai",), "deepseek": ("api.deepseek.com",)}


def text(value, limit=100):
    value = str(value or "")
    if (re.search(r"(?i)(?:bearer\s|sk-[A-Za-z0-9]|eyJ[A-Za-z0-9_-]+\.)", value)
            or any(ord(c) < 32 for c in value)):
        return "[redacted]"
    return value[:limit]


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1e15 else None


def epoch(value):
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc).timestamp() if not parsed.tzinfo else parsed.timestamp()
        except (ValueError, OverflowError):
            return None
    return number(value)


def window(label, unit, *, capacity=None, used=None, remaining=None, reset_at=None, scope="account"):
    capacity, used, remaining = number(capacity), number(used), number(remaining)
    if remaining is None and capacity is not None and used is not None and used <= capacity:
        remaining = capacity - used
    if used is None and capacity is not None and remaining is not None and remaining <= capacity:
        used = capacity - remaining
    return {"id": hashlib.sha256((label + unit).encode()).hexdigest()[:12], "label": text(label),
            "unit": text(unit, 24), "capacity": capacity, "used": used, "remaining": remaining,
            "reset_at": epoch(reset_at), "scope": scope, "state": "ready" if remaining is not None else "unknown"}


def account(provider, identity, label="", *, source="native-hermes", observed_at=None, windows=(), reason=None):
    return {"id": hashlib.sha256((provider + ":" + identity).encode()).hexdigest()[:24],
            "provider": text(provider, 64), "label": text(label) or text(provider),
            "state": "ready" if windows and not reason else "unknown", "source": source,
            "observed_at": observed_at, "expires_at": observed_at + TTL if observed_at is not None else None,
            "unknown_reason": reason, "windows": list(windows)}


def freshen(row, now):
    row = json.loads(json.dumps(row, allow_nan=False))
    at = number(row.get("observed_at"))
    expires = number(row.get("expires_at"))
    if at is not None and (at > now + 5 or (expires is not None and now > expires) or (expires is None and now - at > TTL)):
        row.update(state="stale", unknown_reason="观测已过期，请刷新或导入更新的数据。")
        for item in row.get("windows", []):
            item["state"] = "stale"
    for item in row.get("windows", []):
        reset = number(item.get("reset_at"))
        if reset is not None and reset <= now:
            item["state"] = "expired"
            if item.get("scope") != "model":
                row.update(state="expired", unknown_reason="重置时间已到，需重新读取额度；当前数值仅供核对。")
    return row


def _no_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in SECRET_KEYS or str(key).lower().endswith(("_token", "_password", "_api_key")):
                raise ValueError("Import must contain quota observations, not credentials")
            _no_secrets(item)
    elif isinstance(value, list):
        for item in value:
            _no_secrets(item)


def parse_response(provider, payload, *, observed_at, label="导入的账号", identity="import"):
    """Normalize documented provider response fields without guessing token allowances."""
    if provider not in OFFICIAL or not isinstance(payload, dict):
        raise ValueError("Unsupported provider quota response")
    _no_secrets(payload)
    rows = []
    if provider == "openai-codex":
        rate = payload.get("rate_limit")
        if not isinstance(rate, dict):
            raise ValueError("Codex quota requires rate_limit")
        groups = [("Account", rate, "account")]
        additional = payload.get("additional_rate_limits") or []
        if not isinstance(additional, list):
            raise ValueError("Invalid Codex additional allowances")
        for extra in additional[:32]:
            if isinstance(extra, dict) and isinstance(extra.get("rate_limit"), dict):
                groups.append((text(extra.get("limit_name") or "模型额度"), extra["rate_limit"], "model"))
        for prefix, rate, scope in groups:
            for key, title in (("primary_window", "主要窗口"), ("secondary_window", "次要窗口")):
                item = rate.get(key)
                if isinstance(item, dict):
                    used = number(item.get("used_percent"))
                    if used is not None and used <= 100:
                        rows.append(window(prefix + " · " + title, "percent", capacity=100, used=used,
                                           reset_at=item.get("reset_at"), scope=scope))
    elif provider == "anthropic":
        if not any(key in payload for key in ("five_hour", "seven_day")):
            raise ValueError("Anthropic quota requires an allowance window")
        for key, title in (("five_hour", "Session"), ("seven_day", "Week"),
                           ("seven_day_opus", "Opus week"), ("seven_day_sonnet", "Sonnet week")):
            item = payload.get(key)
            if isinstance(item, dict):
                used = number(item.get("utilization"))
                if used is not None and used <= 100:
                    rows.append(window(title, "percent", capacity=100, used=used, reset_at=item.get("resets_at"),
                                       scope="model" if key in {"seven_day_opus", "seven_day_sonnet"} else "account"))
    elif provider == "openrouter":
        # Live reads provide {credits: official.data, key: official.data}; a
        # saved official response is also accepted as {data: ...}.
        credits, key = payload.get("credits"), payload.get("key")
        data = payload.get("data")
        if isinstance(data, dict):
            if "total_credits" in data or "total_usage" in data:
                credits = data
            elif "limit_remaining" in data or "usage" in data:
                key = data
        if not isinstance(credits, dict) and not isinstance(key, dict):
            raise ValueError("OpenRouter quota requires credits or API-key usage data")
        if isinstance(credits, dict):
            cap, used = number(credits.get("total_credits")), number(credits.get("total_usage"))
            if cap is not None and used is not None:
                rows.append(window("API 余额", "USD", capacity=cap, used=used))
        if isinstance(key, dict):
            # None limit is unlimited/unspecified, never an invented zero cap.
            rows.append(window("API 密钥限额", "USD", capacity=key.get("limit"),
                               used=key.get("usage"), remaining=key.get("limit_remaining"), scope="api_key"))
    elif provider == "deepseek":
        if payload.get("is_available") is not True or not isinstance(payload.get("balance_infos"), list):
            raise ValueError("DeepSeek balance is unavailable")
        for item in payload["balance_infos"][:16]:
            if not isinstance(item, dict) or item.get("currency") not in {"USD", "CNY"}:
                continue
            balance = item.get("total_balance")
            try:
                balance = float(balance) if isinstance(balance, str) else balance
            except ValueError:
                balance = None
            rows.append(window("API 余额", item["currency"], remaining=balance))
    return account(provider, identity, label, source="provider-response", observed_at=observed_at, windows=rows,
                   reason=None if rows and any(row["remaining"] is not None for row in rows) else
                   "服务商响应中没有可读取的额度数值。")


def native_accounts():
    """Read persisted native pools without selecting, rotating or refreshing them."""
    from hermes_cli.auth import read_credential_pool, resolve_api_key_provider_credentials, PROVIDER_REGISTRY
    from hermes_cli.config import load_config_readonly
    from agent.credential_pool import PooledCredential
    from agent.account_usage_cache import _identity_id_for
    from hermes_cli.inventory import _pool_entry_route_base_url
    config = load_config_readonly()
    model = config.get("model", {})
    current_provider = str(model.get("provider") or "") if isinstance(model, dict) else ""
    current_base = str(model.get("base_url") or "") if isinstance(model, dict) else ""
    results, seen = [], set()
    def add(provider, key, base_url, label="", identity="", auth_type="", dead=False):
        fingerprint = hashlib.sha256(str(key).encode()).hexdigest()
        identity = str(identity or (fingerprint if key else provider))
        dedupe = (provider, base_url, identity)
        if dedupe in seen:
            return
        seen.add(dedupe)
        results.append({"provider": provider, "api_key": key, "base_url": base_url, "label": label,
                        "identity": hashlib.sha256((base_url + ":" + identity).encode()).hexdigest(),
                        "auth_type": auth_type, "dead": dead})
    for provider, entries in read_credential_pool().items():
        if not isinstance(entries, list):
            continue
        for raw in entries[:64]:
            if not isinstance(raw, dict):
                continue
            try:
                entry = PooledCredential.from_dict(provider, raw)
                configured = _pool_entry_route_base_url(provider, entry)
                if not configured:
                    profile = PROVIDER_REGISTRY.get(provider)
                    configured = (current_base if provider == current_provider else "") or getattr(profile, "inference_base_url", "")
                add(provider, entry.runtime_api_key, configured or "", entry.label,
                    _identity_id_for(provider, entry), entry.auth_type, entry.last_status == "dead")
            except (ValueError, TypeError, KeyError):
                continue
    present = {item["provider"] for item in results}
    # Singletons and profile-scoped environment keys are resolved by the same
    # Hermes read-only entry points used by its native provider UI.
    for provider in ("openrouter", "deepseek", "anthropic"):
        if provider in present:
            continue
        try:
            if provider == "anthropic":
                from agent.anthropic_credentials import read_hermes_oauth_credentials
                oauth = read_hermes_oauth_credentials() or {}
                if oauth.get("accessToken"):
                    add(provider, oauth["accessToken"], "https://api.anthropic.com", auth_type="oauth")
                    continue
            creds = resolve_api_key_provider_credentials(provider)
            if creds.get("api_key"):
                base = current_base if provider == current_provider and current_base else creds.get("base_url", "")
                add(provider, creds["api_key"], base, auth_type="api_key")
        except Exception:
            pass
    if "openai-codex" not in present:
        try:
            from hermes_cli.auth import _read_codex_tokens
            from hermes_cli.auth_codex import _codex_base_url
            tokens = _read_codex_tokens()["tokens"]
            add("openai-codex", tokens["access_token"], _codex_base_url(), auth_type="oauth")
        except Exception:
            pass
    # A configured provider can have a local API-key binding without a pool
    # row (and without being the foreground model). Keep those accounts visible
    # as unknown when no official quota reader exists. The native status API
    # returns metadata only; no key is copied into this unsupported-provider row.
    present = {item["provider"] for item in results}
    from hermes_cli.auth import get_api_key_provider_status
    for provider, profile in PROVIDER_REGISTRY.items():
        if provider in present or getattr(profile, "auth_type", "") != "api_key":
            continue
        try:
            metadata = get_api_key_provider_status(provider)
            if metadata.get("configured") is True:
                add(provider, "", str(metadata.get("base_url") or ""),
                    label=str(metadata.get("name") or provider), identity="configured-provider", auth_type="api_key")
        except Exception:
            continue
    provider = current_provider
    if provider and provider not in {item["provider"] for item in results}:
        add(provider, "", "", identity="configured-provider")
    return results[:64]


def official_origin(provider, base_url):
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or parsed.hostname not in OFFICIAL.get(provider, ()) or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Configured provider origin is not the supported official usage origin")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError("Provider quota redirects are not followed")


def official_get(url, token, *, extra_headers=None):
    request = Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                                    **(extra_headers or {})})
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        payload = response.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError("Provider quota response exceeds the limit")
    return json.loads(payload)


def fetch_native(entry, now):
    provider, key = entry["provider"], entry.get("api_key")
    unknown = account(provider, entry["identity"], entry.get("label", ""))
    if provider not in OFFICIAL:
        return {**unknown, "unknown_reason": "此服务商暂不支持自动读取额度，可导入已核实的数据。"}
    if not key:
        return {**unknown, "unknown_reason": "未找到本机凭证，请在原生账号设置中登录。"}
    if entry.get("dead"):
        return {**unknown, "unknown_reason": "原生账号当前不可用，请检查账号设置。"}
    try:
        official_origin(provider, entry.get("base_url", ""))
        if provider == "anthropic":
            from agent.anthropic_credentials import _is_oauth_token
            if not _is_oauth_token(key):
                return {**unknown, "unknown_reason": "Claude 订阅额度需原生 OAuth 账号；API 密钥不能代表订阅额度。"}
            payload = official_get("https://api.anthropic.com/api/oauth/usage", key,
                                   extra_headers={"anthropic-beta": "oauth-2025-04-20", "User-Agent": "claude-code/2.1.0"})
            result = parse_response(provider, payload, observed_at=now, identity=entry["identity"], label=entry.get("label", ""))
            result["source"] = "official-usage-api"
            return result
        if provider == "openai-codex":
            from agent.account_usage import fetch_account_usage
            snapshot = fetch_account_usage(provider, base_url=entry["base_url"], api_key=key, read_only=True)
            if snapshot is None or snapshot.unavailable_reason:
                return {**unknown, "unknown_reason": "无法读取官方用量，请检查本机登录状态和访问权限。"}
            if provider == "openai-codex" and isinstance(snapshot.raw, dict):
                result = parse_response(provider, snapshot.raw, observed_at=epoch(snapshot.fetched_at),
                                        identity=entry["identity"], label=entry.get("label", ""))
                result["source"] = "native-official-usage"
                return result
            windows = []
            for item in snapshot.windows:
                used = number(item.used_percent)
                if used is not None and used <= 100:
                    windows.append(window(text(item.label), "percent", capacity=100, used=used,
                                          reset_at=item.reset_at, scope="model" if item.scope == "model" else "account"))
            return account(provider, entry["identity"], entry.get("label", ""), source="native-official-usage",
                           observed_at=epoch(snapshot.fetched_at), windows=windows,
                           reason=None if windows else "官方响应未提供可读取的额度窗口。")
        if provider == "openrouter":
            payload = {}
            for kind in ("credits", "key"):
                try:
                    payload[kind] = official_get("https://openrouter.ai/api/v1/" + kind, key).get("data")
                except Exception:
                    pass
            result = parse_response(provider, payload, observed_at=now, identity=entry["identity"], label=entry.get("label", ""))
        else:
            result = parse_response(provider, official_get("https://api.deepseek.com/user/balance", key),
                                    observed_at=now, identity=entry["identity"], label=entry.get("label", ""))
        result["source"] = "official-usage-api"
        return result
    except Exception:
        return {**unknown, "unknown_reason": "官方额度请求失败，或地址、响应暂不支持；剩余额度未知。"}


class QuotaDashboard:
    def __init__(self, home, *, reader=native_accounts, fetcher=fetch_native, clock=time.time, monotonic=time.monotonic):
        self.home, self.reader, self.fetcher, self.clock = Path(home), reader, fetcher, clock
        self.monotonic = monotonic
        self.path = self.home / "plugin-data/orchestrator-core/quota-dashboard.json"
        self.lock = threading.RLock()
        self.refreshing = False
        self.cloud_reader = None

    def _read(self):
        try:
            raw = self.path.read_bytes()
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError
            value = json.loads(raw)
            _no_secrets(value)
            return value
        except (OSError, ValueError, RecursionError):
            return {"accounts": [], "imports": [], "refreshed_at": None}

    def _write(self, value):
        _no_secrets(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(".quota-dashboard-" + os.urandom(8).hex() + ".tmp")
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def view(self):
        with self.lock:
            state = self._read()
            accounts = list(state.get("accounts", []))
            try:
                current = {}
                for entry in self.reader():
                    row = account(entry["provider"], entry["identity"], entry.get("label", ""),
                                  reason="尚未刷新。可读取官方用量；如需登录，请前往原生账号设置。")
                    current[row["id"]] = row
                accounts = [row for row in accounts if row["id"] in current]
                existing = {row["id"] for row in accounts}
                accounts.extend(row for identity, row in current.items() if identity not in existing)
            except Exception:
                for row in accounts:
                    row.update(state="unknown", unknown_reason="无法读取本机账号列表。")
            cloud = state.get("cloud", {"configured": False, "state": "unknown", "source": "fleet-ledger", "accounts": [],
                                        "unknown_reason": "尚未读取云端台账。"})
            accounts.extend(state.get("imports", []))
            return {"accounts": [freshen(row, self.clock()) for row in accounts], "refreshing": self.refreshing,
                    "refreshed_at": state.get("refreshed_at"), "cloud": cloud,
                    "notes": ["服务商额度与云端台账分别显示；查看不会预留额度。",
                              "公开模型标价为零不代表账号有可用额度；缺失数据保持未知。"] +
                             ([state["refresh_error"]] if state.get("refresh_error") else [])}

    def refresh(self, *, cloud_reader=None):
        with self.lock:
            if self.refreshing:
                return self.view()
            if not _REFRESH_SLOTS.acquire(blocking=False):
                value = self._read()
                value["refresh_error"] = "同时刷新的配置过多，请稍后重试。"
                self._write(value)
                return self.view()
            self.refreshing = True
        context = copy_context()
        def run():
            try:
                entries = self.reader()
                started = self.monotonic()
                rows = []
                for index, entry in enumerate(entries):
                    if index >= MAX_REFRESH_ACCOUNTS or self.monotonic() - started >= REFRESH_START_BUDGET_SECONDS:
                        rows.append(account(entry["provider"], entry["identity"], entry.get("label", ""),
                                            reason="本次刷新已达到账号数量或时间上限；该账号额度未知。"))
                    else:
                        rows.append(self.fetcher(entry, self.clock()))
                with self.lock:
                    value = self._read()
                    value.pop("refresh_error", None)
                    value.update(accounts=rows, refreshed_at=self.clock())
                    if cloud_reader:
                        value["cloud"] = cloud_reader()
                    self._write(value)
            except Exception:
                # No response bodies, exception text or credential-bearing URLs
                # are returned or logged by this telemetry worker.
                with self.lock:
                    value = self._read()
                    value["refresh_error"] = "额度刷新失败；旧数据保留原始采集时间。"
                    self._write(value)
            finally:
                with self.lock:
                    self.refreshing = False
                _REFRESH_SLOTS.release()
        try:
            threading.Thread(target=context.run, args=(run,), daemon=True, name="misaka-quota-refresh").start()
        except BaseException:
            self.refreshing = False
            _REFRESH_SLOTS.release()
            raise
        return self.view()

    def import_file(self, body):
        if not isinstance(body, dict) or set(body) - {"format", "path", "observed_at", "account_label"}:
            raise ValueError("Invalid quota import request")
        path = Path(body["path"]).expanduser()
        if path.is_symlink() or path.suffix.lower() != ".json" or any(part.lower() in {
                "auth.json", ".credentials.json", "credentials", ".env", ".ssh", ".aws", ".gnupg"} for part in path.parts):
            raise ValueError("Choose a sanitized quota JSON response, not a credential file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("Quota import requires a regular JSON file")
            raw = handle.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("Quota import exceeds 1 MiB")
        identity = hashlib.sha256(raw).hexdigest()
        kind = body["format"]
        if kind == "normalized-v1":
            rows = []
            for record in parse_normalized_quota(raw):
                row = account("imported", identity + record["account"] + record["model"], record["account"],
                                    source="file:" + text(record["source"], 48), observed_at=record["observed_at"],
                                    windows=[window(record["model"], record["unit"],
                                                    capacity=None if record.get("capacity") is None else _number(_amount(record["capacity"])),
                                                    remaining=_number(_amount(record["remaining"])), reset_at=record.get("reset_at"))])
                row["expires_at"] = record["observed_at"] + record.get("max_age_seconds", TTL)
                rows.append(row)
        else:
            observed = number(body.get("observed_at"))
            if observed is None or observed > self.clock() + 5:
                raise ValueError("Saved provider responses require their actual observation time")
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_invalid_constant)
            provider = {"codex": "openai-codex", "anthropic": "anthropic", "openrouter": "openrouter", "deepseek": "deepseek"}.get(kind)
            row = parse_response(provider, payload, observed_at=observed, label=text(body.get("account_label") or "导入的账号"), identity=identity)
            row["source"] = "file:" + provider
            rows = [row]
        with self.lock:
            value = self._read()
            imported = {row["id"]: row for row in value.get("imports", [])}
            imported.update({row["id"]: row for row in rows})
            value["imports"] = list(imported.values())[-1000:]
            self._write(value)
        return self.view()
