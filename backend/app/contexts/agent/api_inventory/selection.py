"""根据 ProfileHandoff 决定启用哪些清单 parser / surface。"""
from __future__ import annotations

from typing import Any

from app.contexts.agent.stacks.registry import InventoryPlan, plan_inventory

from .models import canonical_language_ids, profile_frameworks


def inventory_plan_for_profile(profile: Any = None) -> InventoryPlan:
    langs = canonical_language_ids(profile)
    fws = profile_frameworks(profile)
    return plan_inventory(languages=langs, frameworks=fws)
