"""Small authenticated HTTP transport for CloudQueue and an optional QuotaLedger.

No models, shell commands, computer control, or credential discovery run here.
Put non-loopback deployments behind an authenticated private TLS transport.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
import re
from urllib.parse import parse_qs, unquote, urlsplit

from .cloud_queue import CloudQueue, QueueConflict, QueueError, TaskNotFound, _identifier

try:
    from .quota_ledger import QuotaError
except ImportError:
    class QuotaError(ValueError):
        """Only used when optional ledger support is not installed."""



@dataclass(frozen=True)
class ClientIdentity:
    client_id: str
    target: str
    accounts: tuple[str, ...] = ()


def _credentials(tokens):
    if not isinstance(tokens, dict) or not tokens:
        raise ValueError("tokens must be a nonempty token-to-client mapping")
    identities = []
    for token, value in tokens.items():
        if not isinstance(token, str) or len(token) < 24 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError("each bearer token must contain at least 24 printable non-space characters")
        if isinstance(value, str):
            identity = ClientIdentity(_identifier(value, "client_id"), value)
        elif isinstance(value, dict) and set(value) <= {"client_id", "target", "accounts"}:
            client = _identifier(value.get("client_id"), "client_id")
            target = _identifier(value.get("target", client), "target")
            accounts = value.get("accounts", [])
            if not isinstance(accounts, list) or any(not isinstance(account, str) or not account for account in accounts):
                raise ValueError("accounts must be a list of permitted account names")
            identity = ClientIdentity(client, target, tuple(accounts))
        else:
            raise ValueError("invalid token identity")
        identities.append((token, identity))
    return identities


class CloudHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, queue, tokens, ledger=None):
        self.queue = queue
        self.identities = _credentials(tokens)
        self.ledger = ledger
        super().__init__(address, CloudHandler)


class CloudHandler(BaseHTTPRequestHandler):
    server_version = "OrchestratorQueue/1"
    MAX_BODY = 1024 * 1024

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format, *args):
        # Never log Authorization headers, request bodies, task text or tokens.
        pass

    def _send(self, code, data):
        encoded = json.dumps(data, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)
        self.close_connection = True

    def _identity(self):
        values = self.headers.get_all("Authorization", [])
        if len(values) != 1 or not values[0].startswith("Bearer "):
            return None
        candidate = values[0][7:]
        if len(candidate) > 4096:
            return None
        match = None
        for token, identity in self.server.identities:
            if hmac.compare_digest(token.encode("utf-8"), candidate.encode("utf-8")):
                match = identity
        return match

    def _body(self):
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
            raise QueueError("one Content-Length header is required; chunked bodies are unsupported")
        try:
            length = int(lengths[0])
        except ValueError as exc:
            raise QueueError("invalid Content-Length") from exc
        if not 0 <= length <= self.MAX_BODY:
            raise QueueError("body exceeds 1 MiB")
        if self.headers.get_content_type() != "application/json":
            raise QueueError("Content-Type must be application/json")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise QueueError("incomplete body")
        try:
            data = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise QueueError("body must contain finite JSON") from exc
        if not isinstance(data, dict):
            raise QueueError("body must be an object")
        return data

    @staticmethod
    def _fields(body, required, optional=()):
        if not set(required) <= body.keys() or body.keys() - set(required) - set(optional):
            raise QueueError("missing or unsupported request fields")

    def do_GET(self):
        self._dispatch(False)

    def do_POST(self):
        self._dispatch(True)

    def _dispatch(self, write):
        identity = self._identity()
        if identity is None:
            self._send(401, {"error": {"code": "unauthorized", "message": "valid bearer token required"}})
            return
        try:
            url = urlsplit(self.path)
            path = unquote(url.path)
            if write and url.query:
                raise QueueError("POST query parameters are unsupported")
            body = self._body() if write else {}
            if path.startswith("/v1/quota"):
                self._quota(identity, path, parse_qs(url.query, keep_blank_values=True), body, write)
                return
            if url.query:
                raise QueueError("query parameters are unsupported")
            queue = self.server.queue
            if write and path == "/v1/tasks":
                self._fields(body, ("task_id", "payload"), ("idempotency_key",))
                payload = body["payload"]
                if not isinstance(payload, dict) or payload.get("client_id") != identity.client_id or payload.get("target") != identity.target:
                    raise PermissionError("task identity or target is not authorized")
                result = queue.enqueue(body["task_id"], payload, body.get("idempotency_key"))
            elif write and path == "/v1/claim":
                self._fields(body, ("request_id",), ("target", "lease_seconds"))
                _identifier(body["request_id"], "request_id")
                if body.get("target", identity.target) != identity.target:
                    raise PermissionError("target is not authorized")
                result = queue.claim(identity.client_id, body.get("lease_seconds", 60), target=identity.target, request_id=body["request_id"])
            else:
                match = re.fullmatch(r"/v1/tasks/([^/]+)(?:/(heartbeat|complete|cancel))?", path)
                if match is None:
                    self._send(404, {"error": {"code": "not_found", "message": "route not found"}})
                    return
                task_id, operation = match.groups()
                result = queue.get(task_id, identity.client_id)
                if result["target"] != identity.target:
                    raise TaskNotFound("task not found")
                if not write and operation is None:
                    pass
                elif write and operation == "heartbeat":
                    self._fields(body, ("lease_token",), ("lease_seconds",))
                    result = queue.heartbeat(task_id, identity.client_id, body["lease_token"], body.get("lease_seconds", 60))
                elif write and operation == "complete":
                    self._fields(body, ("lease_token", "result"), ("status",))
                    result = queue.complete(task_id, identity.client_id, body["lease_token"], body["result"], body.get("status", "done"))
                elif write and operation == "cancel":
                    self._fields(body, ())
                    result = queue.cancel(task_id, identity.client_id)
                else:
                    self._send(405, {"error": {"code": "method_not_allowed", "message": "method not allowed"}})
                    return
            self._send(200, {"task": result})
        except TaskNotFound:
            self._send(404, {"error": {"code": "not_found", "message": "task not found"}})
        except PermissionError:
            self._send(403, {"error": {"code": "forbidden", "message": "identity, target or account is not authorized"}})
        except QueueConflict as exc:
            self._send(409, {"error": {"code": "conflict", "message": str(exc)}})
        except QuotaError:
            self._send(409, {"error": {"code": "quota_conflict", "message": "quota operation refused; inspect the account snapshot and reservation state"}})
        except (QueueError, TypeError, ValueError) as exc:
            # Validation messages contain field names, never the supplied payload.
            message = str(exc) if isinstance(exc, QueueError) else "invalid request"
            self._send(400, {"error": {"code": "invalid_request", "message": message}})
        except Exception:
            # Do not leak configuration, paths, credentials or exception payloads.
            self._send(503, {"error": {"code": "unavailable", "message": "service unavailable"}})

    def _quota(self, identity, path, query, body, write):
        ledger = self.server.ledger
        if ledger is None:
            self._send(503, {"error": {"code": "quota_unavailable", "message": "quota ledger is not configured"}})
            return
        if not write and path == "/v1/quota":
            if set(query) - {"account", "model"} or "account" not in query or any(len(values) != 1 for values in query.values()):
                raise QueueError("quota query requires one account and optional model")
            account = query["account"][0]
            if account not in identity.accounts:
                raise PermissionError()
            result = ledger.snapshot(account, model=query.get("model", ["*"])[0])
        elif write and path in {"/v1/quota/reserve", "/v1/quota/settle", "/v1/quota/release", "/v1/quota/unknown", "/v1/quota/mark_unknown"}:
            operation = path.rsplit("/", 1)[1]
            if operation == "reserve":
                self._fields(body, ("reservation_id", "account", "amount"), ("model", "unit", "minimum_remaining"))
                if body["account"] not in identity.accounts:
                    raise PermissionError()
            elif operation == "settle":
                self._fields(body, ("reservation_id", "actual_amount"))
            else:
                self._fields(body, ("reservation_id",))
            local_id = _identifier(body["reservation_id"], "reservation_id")
            # Length prefix prevents ambiguity when a client ID contains ':'.
            reservation_id = f"{len(identity.client_id)}:{identity.client_id}:{local_id}"
            if len(reservation_id) > 256:
                raise QueueError("client and reservation identifiers exceed ledger limit")
            if operation == "reserve":
                minimum = body.get("minimum_remaining", 0)
                if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
                    raise QueueError("minimum_remaining must be a finite nonnegative JSON number")
                try:
                    precise = Decimal(str(minimum))
                    if (not precise.is_finite() or precise < 0 or precise > Decimal("1e15")
                            or precise.as_tuple().exponent < -9):
                        raise InvalidOperation
                except InvalidOperation as exc:
                    raise QueueError("minimum_remaining must be finite, nonnegative, <= 1e15, with <= 9 decimal places") from exc
                result = ledger.reserve(reservation_id, body["account"], body["amount"], model=body.get("model", "*"), unit=body.get("unit"), minimum_remaining=minimum)
            else:
                reservation = ledger.get_reservation(reservation_id)
                if reservation["account"] not in identity.accounts:
                    raise PermissionError()
                if operation == "settle":
                    result = ledger.settle(reservation_id, body["actual_amount"])
                elif operation == "release":
                    result = ledger.release(reservation_id)
                else:
                    result = ledger.mark_unknown(reservation_id)
        else:
            self._send(404, {"error": {"code": "not_found", "message": "quota route not found"}})
            return
        self._send(200, {"quota": result})


def make_server(host="127.0.0.1", port=0, queue=None, tokens=None, ledger=None):
    """Build a server; callers control serve_forever/shutdown and database paths."""
    if queue is None:
        raise ValueError("queue is required")
    return CloudHTTPServer((host, port), queue, tokens, ledger)


def tokens_from_environment(name="ORCHESTRATOR_CLOUD_TOKENS"):
    """Read a caller-provisioned JSON mapping without logging its contents."""
    raw = os.environ.get(name)
    if not raw:
        raise ValueError(f"{name} must be configured")
    try:
        tokens = json.loads(raw)
        _credentials(tokens)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must contain a valid token identity mapping") from exc
    return tokens
