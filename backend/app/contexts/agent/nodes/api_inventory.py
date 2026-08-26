"""api_inventory 节点 — 确定性后端入口清单(discovery-spec §6.2.1)。

按画像语言表驱动 parser；禁止 AI 列端点。与 scan_* 同波；失败隔离。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import NodeContext, emit_phase


def _empty(*, ok: bool = True, skipped: bool = False, error: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": ok,
        "parser": "none",
        "parsers": [],
        "acquisition_kinds": [],
        "endpoint_count": 0,
        "pve_count": 0,
        "bom_path": None,
        "unsupported_languages": [],
        "stack_ids": [],
    }
    if skipped:
        out["skipped"] = True
    if error:
        out["error"] = error
    return out


class ApiInventoryNode:
    node_key = "api_inventory"

    @property
    def is_ai(self) -> bool:
        return False

    def _resolve_input(self, ctx: NodeContext, node_input):
        from app.contexts.agent.contracts import InputAssembler

        if node_input is not None:
            return node_input
        return InputAssembler.from_previous_outputs(
            "api_inventory",
            ctx.previous_outputs,
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
        )

    async def execute(self, ctx: NodeContext, node_input=None) -> dict[str, Any]:
        from app.contexts.agent.api_inventory.registry import (
            build_inventory_bom,
            parser_keys_for_profile,
            phase_message_for_profile,
            unsupported_for_profile,
        )
        from app.core.config import get_settings

        inp = self._resolve_input(ctx, node_input)
        ctx.node_input = inp
        settings = get_settings()

        if not getattr(settings, "api_inventory_enabled", True):
            emit_phase(ctx, "API 清单未启用，已跳过", phase=self.node_key)
            return _empty(skipped=True)

        profile = getattr(inp, "profile", None)
        repo_root = getattr(getattr(inp, "source", None), "project_path", None) or ctx.source_path
        unsupported = unsupported_for_profile(profile)
        parser_keys = parser_keys_for_profile(profile)

        emit_phase(ctx, phase_message_for_profile(profile), phase=self.node_key)
        try:
            bom = build_inventory_bom(repo_root, profile)
        except Exception as e:  # noqa: BLE001 — 失败隔离
            emit_phase(ctx, f"清单解析失败：{str(e)[:200]}", phase=self.node_key)
            out = _empty(ok=False, error=str(e))
            out["parsers"] = parser_keys
            out["parser"] = ",".join(parser_keys) if parser_keys else "none"
            out["unsupported_languages"] = unsupported
            return out

        rel = f".api-inventory/{ctx.run_id}/bom.json"
        out_path = Path(ctx.host_workdir) / rel
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(bom, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            emit_phase(ctx, f"BOM 落盘失败：{e}", phase=self.node_key)
            return {
                "ok": False,
                "parser": bom.get("parser") or "none",
                "parsers": list(bom.get("parsers") or parser_keys),
                "acquisition_kinds": list(bom.get("acquisition_kinds") or []),
                "endpoint_count": int(bom.get("endpoint_count") or 0),
                "pve_count": int(bom.get("pve_count") or 0),
                "bom_path": None,
                "unsupported_languages": unsupported,
                "stack_ids": list(bom.get("stack_ids") or []),
                "error": f"bom write failed: {e}",
            }

        n = int(bom.get("endpoint_count") or 0)
        pve = int(bom.get("pve_count") or 0)
        ran = list(bom.get("parsers") or parser_keys)
        kinds = list(bom.get("acquisition_kinds") or [])
        stack_ids = list(bom.get("stack_ids") or [])
        parser_label = "/".join(ran) if ran else "none"
        extra = ""
        if unsupported:
            extra = f"；{'、'.join(unsupported)} 暂无语言 parser"
        zero_note = "（0 端点≠无 API）" if n == 0 else ""
        emit_phase(
            ctx,
            f"清单完成：{n} 端点 / PVE {pve} · {parser_label}{zero_note}{extra}",
            phase=self.node_key,
        )
        return {
            "ok": True,
            "parser": bom.get("parser") or parser_label,
            "parsers": ran,
            "acquisition_kinds": kinds,
            "endpoint_count": n,
            "pve_count": pve,
            "bom_path": rel,
            "unsupported_languages": unsupported,
            "stack_ids": stack_ids,
        }
