#!/usr/bin/env python
"""Command-line entry for the cost-aware AI orchestrator (offline planner MVP).

Examples:
  python -m src.cli plan --task "Build a routing CLI" --type code_generation \
      --value important --requires repository_editing,cli_design

  python -m src.cli plan-file examples/code_task.yaml
  python -m src.cli plan-file examples/complex_project_task.yaml --json
  python -m src.cli capabilities
  python -m src.cli quota
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

# Allow running both as `python -m src.cli` and `python src/cli.py`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.orchestrator import Orchestrator      # type: ignore
    from src.quota_manager import QuotaManager     # type: ignore
    from src.registry import Registry              # type: ignore
    from src.task_card import TaskCard             # type: ignore
else:
    from .orchestrator import Orchestrator
    from .quota_manager import QuotaManager
    from .registry import Registry
    from .task_card import TaskCard


def _print_decision(dec, as_json: bool) -> None:
    if as_json:
        print(json.dumps(dec.to_dict(), ensure_ascii=False, indent=2))
        return
    print(f"\n=== Task {dec.task_id}: {dec.title} ===")
    print(f"value_level : {dec.value_level}")
    print(f"method      : {dec.method}")
    print("roles       :")
    for role, agent in dec.roles.items():
        print(f"   - {role:11s}: {agent}")
    if dec.peer_votes:
        votes = ", ".join(f"{k}={v}" for k, v in sorted(dec.peer_votes.items(), key=lambda x: -x[1]))
        print(f"peer_votes  : {votes}")
    print("ranking     :")
    for s in dec.ranking[:5]:
        print("   " + s.explain())
    for n in dec.notes:
        print(f"note        : {n}")


def cmd_plan(args, reg, orch) -> None:
    task = TaskCard(
        task_id=args.id or "T001",
        title=args.task,
        type=args.type,
        requires=[t.strip() for t in (args.requires or "").split(",") if t.strip()],
        value_level=args.value,
        priority=args.priority,
    )
    _print_decision(orch.decide(task), args.json)


def cmd_plan_file(args, reg, orch) -> None:
    data = yaml.safe_load(Path(args.path).read_text(encoding="utf-8")) or {}
    cards = data.get("tasks") or ([data] if "task_id" in data else [])
    if not cards:
        print("No tasks found in file (expected a top-level 'tasks:' list or a single card).")
        return
    for raw in cards:
        _print_decision(orch.decide(TaskCard.from_dict(raw)), args.json)


def cmd_capabilities(args, reg, orch) -> None:
    for agent in reg.agents:
        tier = reg.tier(agent)
        cost = reg.unit_cost(agent)
        print(f"{agent:12s} tier={tier:9s} unit_cost={cost:.2f}")


def cmd_quota(args, reg, orch) -> None:
    qm = QuotaManager(reg)
    for agent in reg.agents:
        print(f"{agent:12s} remaining={qm.remaining(agent):.2f} "
              f"reserve={qm.reserve(agent):.2f} health={qm.health(agent):.2f} "
              f"shadow_price={qm.shadow_price(agent):.3f}"
              + ("  [SCARCE]" if qm.is_scarce(agent) else ""))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Cost-aware claim-based multi-model AI orchestrator (planner MVP)")
    p.add_argument("--config-dir", default=None, help="Override configs/ directory")
    sub = p.add_subparsers(dest="command", required=True)

    pl = sub.add_parser("plan", help="Plan a single inline task")
    pl.add_argument("--task", required=True)
    pl.add_argument("--type", default="general")
    pl.add_argument("--value", default="normal")
    pl.add_argument("--priority", default="normal")
    pl.add_argument("--requires", default="")
    pl.add_argument("--id", default="")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_plan)

    pf = sub.add_parser("plan-file", help="Plan tasks from a YAML file")
    pf.add_argument("path")
    pf.add_argument("--json", action="store_true")
    pf.set_defaults(func=cmd_plan_file)

    cp = sub.add_parser("capabilities", help="List agents/tiers/prices")
    cp.set_defaults(func=cmd_capabilities)

    qz = sub.add_parser("quota", help="Show quota health + shadow prices")
    qz.set_defaults(func=cmd_quota)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    config_dir = Path(args.config_dir) if args.config_dir else None
    reg = Registry.load(config_dir)
    orch = Orchestrator(reg)
    args.func(args, reg, orch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
