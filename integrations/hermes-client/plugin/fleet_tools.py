"""One narrow agent bridge to paired nodes; native approval gates mutations."""
from __future__ import annotations
import json
import re

TOOL = "orchestrator_fleet"
PRIVATE_FIELDS = {"token", "ticket", "lease_token", "access_token", "refresh_token", "api_key", "apikey",
                  "authorization", "cookie", "cookies", "password", "credentials", "headers"}
SCHEMA = {"name": TOOL, "description": "查看或选择已配对节点，提交有界任务、查看状态或取消任务；不提供远程终端命令通道。",
          "parameters": {"type": "object", "additionalProperties": False,
            "properties": {
              "action": {"type": "string", "enum": ["list", "select", "submit", "status", "cancel"]},
              "task_id": {"type": "string", "description": "稳定的请求或任务标识；超时后沿用同一标识核对，不能重复提交结果未确认的工作。"},
              "target": {"type": "string"}, "task": {"type": "string"},
              "workspace_alias": {"type": "string"}, "relative_dir": {"type": "string", "default": "."},
              "tools": {"type": "array", "items": {"type": "string"}},
              "skills": {"type": "array", "items": {"type": "string"}},
              "executors": {"type": "array", "items": {"type": "string"}},
              "models": {"type": "array", "items": {"type": "string"}},
              "tool_calls": {"type": "array", "minItems": 1, "maxItems": 1000, "items": {
                "type": "object", "additionalProperties": False, "required": ["name", "arguments"],
                "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}}}},
              "task_card": {"type": "object", "additionalProperties": False, "properties": {
                "type": {"type": "string", "enum": ["general", "code_generation", "research", "writing", "multimodal"]},
                "requires": {"type": "array", "items": {"type": "string"}, "maxItems": 32},
                "value_level": {"type": "string", "enum": ["trivial", "normal", "important", "critical"]}}},
              "platform": {"type": "string"}, "max_actions": {"type": "integer", "minimum": 1, "maximum": 1000}},
            "required": ["action"]}}


def available(backend):
    config = backend.settings()
    return bool(config["fleet_tools"]["enabled"] and config["server"].get("url"))


def public_result(value):
    """Agent/UI receipts never need node-private lease or authentication fields."""
    if isinstance(value, dict):
        return {key: public_result(item) for key, item in value.items()
                if key.lower() not in PRIVATE_FIELDS and not key.lower().endswith(("_token", "_password", "_api_key"))}
    if isinstance(value, list):
        return [public_result(item) for item in value]
    return value


def perform(backend, args):
    mutating_request = False
    try:
        if not available(backend):
            raise ValueError("Enable the explicit fleet tool policy and configure an owner connection first")
        if not isinstance(args, dict) or set(args) - set(SCHEMA["parameters"]["properties"]):
            raise ValueError("Unknown fleet action argument")
        action = args.get("action")
        if action not in {"list", "select", "submit", "status", "cancel"}:
            raise ValueError("Unknown fleet action")
        client = backend._fleet()
        nodes = client.nodes()  # Owner-authorized read; a node-role token fails closed here.
        if action == "list":
            return json.dumps({"nodes": nodes})
        if action == "select":
            filters = {key: args[key] for key in ("tools", "skills", "executors", "models", "platform", "max_actions", "workspace_alias") if key in args}
            return json.dumps({"node": client.select_node(**filters)})
        task_id = args.get("task_id")
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", task_id):
            raise ValueError("Provide a stable task_id; reuse it when checking uncertain work")
        if action in {"status", "cancel"}:
            mutating_request = action == "cancel"
            record = client.task(task_id, "cancel", {}) if action == "cancel" else client.task(task_id)
            return json.dumps(public_result({"task": record}))
        target = next((node for node in nodes if node.get("node_id") == args.get("target") and not node.get("revoked_at")), None)
        if target is None:
            raise ValueError("Choose a live paired node owned by this connection")
        policy = backend.settings()["fleet_tools"]
        tools = args.get("tools", [])
        if (not isinstance(tools, list) or any(not isinstance(item, str) for item in tools)
                or not set(tools) <= set(policy["allowed_tools"])
                or not set(tools) <= set(target.get("capabilities", {}).get("tools", []))):
            raise ValueError("Requested tools exceed the owner or node policy")
        maximum = args.get("max_actions", policy["max_actions"])
        if type(maximum) is not int or not 1 <= maximum <= min(policy["max_actions"], target.get("capabilities", {}).get("max_actions", 20)):
            raise ValueError("Requested actions exceed the owner or node policy")
        alias, relative = args.get("workspace_alias"), args.get("relative_dir", ".")
        if alias not in target.get("capabilities", {}).get("workspace_aliases", []):
            raise ValueError("Select a workspace alias advertised by the paired node")
        if not isinstance(relative, str) or relative.startswith("/") or ".." in relative.split("/") or "\\" in relative or ":" in relative:
            raise ValueError("Task directory must stay relative to its local workspace alias")
        text = args.get("task")
        if not isinstance(text, str) or not text.strip() or len(text) > 131072:
            raise ValueError("Provide a bounded natural-language task")
        metadata = {"origin": "native-fleet-tool"}
        if "tool_calls" in args:
            plan = args["tool_calls"]
            if not isinstance(plan, list) or not 1 <= len(plan) <= maximum:
                raise ValueError("Explicit tool calls must fit the node action budget")
            for call in plan:
                if (not isinstance(call, dict) or set(call) != {"name", "arguments"}
                        or not isinstance(call["name"], str) or call["name"] not in tools
                        or not isinstance(call["arguments"], dict)):
                    raise ValueError("Each tool call must name a granted tool and JSON argument object")
                backend._safe(call["arguments"])
            if len(json.dumps(plan, allow_nan=False).encode()) > 65536:
                raise ValueError("Explicit tool plan exceeds the 64 KiB limit")
            metadata["tool_calls"] = plan
        if "task_card" in args:
            card = args["task_card"]
            if (not isinstance(card, dict) or set(card) - {"type", "requires", "value_level"}
                    or card.get("type", "general") not in {"general", "code_generation", "research", "writing", "multimodal"}
                    or card.get("value_level", "normal") not in {"trivial", "normal", "important", "critical"}
                    or not isinstance(card.get("requires", []), list) or len(card.get("requires", [])) > 32
                    or any(not isinstance(item, str) or len(item) > 100 for item in card.get("requires", []))):
                raise ValueError("Task card contains unsupported routing fields")
            metadata["task_card"] = card
        payload = {"client_id": target["owner_id"], "target": target["node_id"], "task": text,
                   "workspace_alias": alias, "relative_dir": relative, "tools": tools,
                   "max_actions": maximum, "metadata": metadata}
        mutating_request = True
        return json.dumps(public_result({"task": client.submit(task_id, payload)}))
    except (ValueError, TypeError, KeyError) as error:
        if mutating_request:
            return json.dumps({"error": "Fleet response unconfirmed; retain the task ID and query status", "submitted": "unknown"})
        return json.dumps({"error": str(error), "submitted": False})
    except Exception as error:
        return json.dumps({"error": f"Fleet operation unconfirmed ({type(error).__name__}); keep the task ID and query status", "submitted": "unknown"})



def register(ctx, backend):
    def approval(tool_name, args, **kwargs):
        if tool_name == TOOL and args.get("action") in {"submit", "cancel"}:
            action = "提交" if args["action"] == "submit" else "取消"
            return {"action": "approve", "message": "请确认在节点 " + str(args.get("target", "所选节点")) +
                    " 上" + action + "任务 " + str(args.get("task_id", "")),
                    "rule_key": "orchestrator-fleet-" + args["action"] + ":" + str(args.get("target", ""))}
        return None

    ctx.register_tool(name=TOOL, toolset=TOOL, schema=SCHEMA, handler=lambda args, **kwargs: perform(backend, args), check_fn=lambda: available(backend))
    ctx.register_hook("pre_tool_call", approval)
