"""stacks.registry — plan_inventory / 语言框架选型。"""
from __future__ import annotations

from app.contexts.agent.stacks.registry import (
    index_langs_for_profile_ids,
    plan_inventory,
)


def test_plan_inventory_flask_only():
    plan = plan_inventory(languages=["python"], frameworks=["flask"])
    assert plan.parser_keys == ("openapi", "flask")
    assert "python:flask" in plan.stack_ids
    assert "flask" in plan.surface_frameworks


def test_plan_inventory_php_laravel():
    plan = plan_inventory(languages=["php"], frameworks=["laravel"])
    assert "laravel" in plan.parser_keys
    assert "php_script" not in plan.parser_keys
    assert "php:laravel" in plan.stack_ids


def test_plan_inventory_php_no_framework_uses_script():
    plan = plan_inventory(languages=["php"], frameworks=[])
    assert plan.parser_keys == ("openapi", "php_script")


def test_index_langs_none_vs_empty():
    assert index_langs_for_profile_ids(None) == [
        "go", "java", "javascript", "php", "python", "typescript",
    ]
    assert index_langs_for_profile_ids([]) == []
