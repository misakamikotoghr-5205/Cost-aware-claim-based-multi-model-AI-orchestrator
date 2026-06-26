"""Config loading + a tiny capability/price/quota registry."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"


def _load(name: str, override_dir: Path | None = None) -> dict:
    base = override_dir or CONFIG_DIR
    path = base / name
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass
class Registry:
    """All static knowledge the engines need, loaded from configs/."""

    capabilities: dict = field(default_factory=dict)
    scoring: dict = field(default_factory=dict)
    quota: dict = field(default_factory=dict)
    prices: dict = field(default_factory=dict)
    routing: dict = field(default_factory=dict)

    @classmethod
    def load(cls, config_dir: Path | None = None) -> "Registry":
        cap = _load("capability_cards.yaml", config_dir)
        return cls(
            capabilities=cap,
            scoring=_load("scoring_weights.yaml", config_dir),
            quota=_load("quota_policy.yaml", config_dir),
            prices=_load("model_prices.yaml", config_dir),
            routing=_load("routing_rules.yaml", config_dir),
        )

    # --- convenience accessors -------------------------------------------
    @property
    def agents(self) -> List[str]:
        return list(self.capabilities.get("agents", {}).keys())

    def capability(self, agent: str, dimension: str) -> float:
        a = self.capabilities.get("agents", {}).get(agent, {})
        return float(a.get("capabilities", {}).get(dimension, 0.0))

    def unit_cost(self, agent: str) -> float:
        return float(self.prices.get("prices", {}).get(agent, {}).get("unit_cost", 0.5))

    def tier(self, agent: str) -> str:
        return self.capabilities.get("agents", {}).get(agent, {}).get("tier", "mid")

    def min_reserve(self, agent: str) -> float:
        return float(self.quota.get("policy", {}).get(agent, {}).get("minimum_reserve", 0.0))

    def remaining_quota(self, agent: str, quota_status: dict | None = None) -> float:
        status = quota_status or self.quota.get("quota_status", {})
        return float(status.get(agent, {}).get("remaining", 1.0))
