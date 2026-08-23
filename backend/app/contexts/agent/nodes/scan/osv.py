"""osv-scanner 适配器(SCA, discovery-spec §6.1)。

需出网访问 api.osv.dev，配置可禁用；依赖 CVE 直报(bypass)不进 triage。
退出码 0/1 均视为成功(1 = 有漏洞)，>1 才是执行错误。
"""
from __future__ import annotations

import json
from typing import Any

from .base import EngineScanNode


class OsvScanNode(EngineScanNode):
    node_key = "scan_osv"
    engine = "osv"

    def enabled(self, settings) -> bool:
        return settings.scanner_osv_enabled

    def _binary(self, settings) -> str:
        from app.core.scanners import resolve

        return resolve(
            "osv-scanner",
            bin_dir=settings.scanner_bin_dir,
            auto_install=settings.scanner_auto_install,
        )

    def build_command(self, ctx, inp, settings) -> list[str]:
        # v2 已删除 --json（未知 flag 退出 127）；scan --format=json 是现行契约
        return [
            self._binary(settings), "scan", "--format=json", "-r",
            self._repo_root(inp, ctx),
        ]

    def timeout_seconds(self, settings) -> int:
        return settings.scanner_osv_timeout_seconds

    def success_exit_codes(self) -> tuple[int, ...]:
        return (0, 1)  # 1 = 发现漏洞(成功)

    def parse_output(self, stdout: str) -> list[dict[str, Any]]:
        from app.contexts.finding.sarif import normalize

        return normalize("osv", json.loads(stdout or "{}"))

    def config_summary(self, ctx, inp, settings) -> dict[str, Any]:
        return {"engine": "osv", "network": True}
