"""声明式策略与权限引擎 (借鉴 OpenStack oslo.policy 模式)。

将权限校验与业务逻辑解耦，通过集中式规则集管理平台角色（admin / auditor / analyst / viewer）
对各类资源（task / lab / finding / settings）的访问权限。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from app.shared.context import CrucibleContext

logger = logging.getLogger(__name__)

PolicyCheck = Callable[[CrucibleContext, Any], bool]


def _is_admin(ctx: CrucibleContext, _target: Any = None) -> bool:
    return ctx.is_admin or ctx.role == "admin"


def _is_operator(ctx: CrucibleContext, _target: Any = None) -> bool:
    """具备审计/分析/操作权限（admin / auditor / analyst）。"""
    return _is_admin(ctx) or ctx.role in ("auditor", "analyst")


def _is_any_authenticated(_ctx: CrucibleContext, _target: Any = None) -> bool:
    return True


def _can_delete_task(ctx: CrucibleContext, target: Any = None) -> bool:
    if _is_admin(ctx):
        return True
    if target and hasattr(target, "owner_id") and target.owner_id:
        return str(target.owner_id) == str(ctx.user_id)
    return _is_operator(ctx)


DEFAULT_POLICIES: dict[str, PolicyCheck] = {
    # 系统与全局设置
    "system:admin": _is_admin,
    "settings:read": _is_any_authenticated,
    "settings:modify": _is_admin,
    "settings:reveal_secrets": _is_admin,

    # 任务相关
    "task:create": _is_operator,
    "task:read": _is_any_authenticated,
    "task:retry": _is_operator,
    "task:cancel": _is_operator,
    "task:delete": _can_delete_task,

    # 靶场 Lab 管理
    "lab:create": _is_operator,
    "lab:read": _is_any_authenticated,
    "lab:manage": _is_operator,
    "lab:delete": _is_operator,

    # 漏洞与研判
    "finding:read": _is_any_authenticated,
    "finding:adjudicate": _is_operator,

    # 报告
    "report:read": _is_any_authenticated,
    "report:export": _is_any_authenticated,
}


class PolicyEngine:
    """声明式权限决策引擎。"""

    def __init__(self, rules: dict[str, PolicyCheck] | None = None) -> None:
        self._rules = dict(rules or DEFAULT_POLICIES)

    def register(self, action: str, check: PolicyCheck) -> None:
        self._rules[action] = check

    def authorize(
        self, action: str, ctx: CrucibleContext, target: Any = None
    ) -> bool:
        check = self._rules.get(action)
        if check is None:
            logger.warning("未定义的 Policy 规则: %s（默认拒绝）", action)
            return False
        return bool(check(ctx, target))

    def enforce(
        self,
        action: str,
        ctx: CrucibleContext,
        target: Any = None,
        error_message: str | None = None,
    ) -> None:
        if not self.authorize(action, ctx, target):
            msg = error_message or f"无权执行操作: {action}"
            raise HTTPException(status_code=403, detail=msg)


policy_engine = PolicyEngine()


def check_policy(action: str, ctx: CrucibleContext, target: Any = None) -> bool:
    return policy_engine.authorize(action, ctx, target)


def enforce_policy(
    action: str,
    ctx: CrucibleContext,
    target: Any = None,
    error_message: str | None = None,
) -> None:
    policy_engine.enforce(action, ctx, target, error_message=error_message)
