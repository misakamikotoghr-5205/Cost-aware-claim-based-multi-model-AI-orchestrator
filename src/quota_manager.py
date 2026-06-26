"""Quota manager: quota health + the Quota Shadow Price.

The shadow price captures the *opportunity cost* of spending quota now: an
expensive model that is near its reserve should be discouraged from low-value
work so it stays available for the critical task it is uniquely good at.
"""
from __future__ import annotations

from .registry import Registry


class QuotaManager:
    def __init__(self, registry: Registry, quota_status: dict | None = None):
        self.reg = registry
        self.quota_status = quota_status

    def remaining(self, agent: str) -> float:
        return self.reg.remaining_quota(agent, self.quota_status)

    def reserve(self, agent: str) -> float:
        return self.reg.min_reserve(agent)

    def health(self, agent: str) -> float:
        """0..1 — how comfortable this agent's quota headroom is.

        Headroom above the reserve maps to ~1.0; at/below reserve maps to ~0.
        """
        remaining = self.remaining(agent)
        reserve = self.reserve(agent)
        if remaining <= reserve:
            return 0.0
        usable = 1.0 - reserve
        if usable <= 0:
            return 1.0
        return max(0.0, min(1.0, (remaining - reserve) / usable))

    def is_scarce(self, agent: str) -> bool:
        return self.remaining(agent) <= self.reserve(agent) + 0.05

    def shadow_price(self, agent: str) -> float:
        """Opportunity cost multiplier, 0..1.

        High when the agent is expensive AND scarce; low when cheap or plentiful.
        """
        scarcity = 1.0 - self.health(agent)        # 0 = plentiful, 1 = scarce
        price = self.reg.unit_cost(agent)          # 0..1 relative price
        return round(scarcity * price, 4)
