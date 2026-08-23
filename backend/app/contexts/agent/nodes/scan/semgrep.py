"""semgrep 适配器(discovery-spec §6.1)。

只跑 profile.semgrep_configs 映射后的**本地**规则树；禁止节点内启发式选语言，
禁止 p/ registry（需要 login / 出网 semgrep.dev）。
`--oss-only` 关掉 Pro 引擎；`--dataflow-traces` 让 taint 规则产 codeFlows。
"""
from __future__ import annotations

import json
from typing import Any

from .base import EngineScanError, EngineScanNode


class SemgrepNode(EngineScanNode):
    node_key = "scan_semgrep"
    engine = "semgrep"

    def enabled(self, settings) -> bool:
        return settings.scanner_semgrep_enabled

    def applicable(self, ctx, inp) -> bool:
        from app.core.semgrep_rules import local_config_names

        return bool(local_config_names(getattr(inp.profile, "semgrep_configs", None) or []))

    def _binary(self, settings) -> str:
        from app.core.scanners import resolve

        # pip 包，禁止走 Go 产物的 GitHub 自动安装
        return resolve(
            "semgrep",
            bin_dir=settings.scanner_bin_dir,
            auto_install=False,
        )

    def _config_paths(self, inp, settings) -> list[str]:
        from app.core.semgrep_rules import ensure_rules, local_config_names

        names = local_config_names(getattr(inp.profile, "semgrep_configs", None) or [])
        if not names:
            return []
        root = ensure_rules(
            explicit=getattr(settings, "scanner_semgrep_rules_dir", "") or "",
            auto_install=bool(getattr(settings, "scanner_auto_install", False)),
        )
        paths: list[str] = []
        missing: list[str] = []
        for name in names:
            path = root / name
            if path.is_dir():
                paths.append(str(path))
            else:
                missing.append(name)
        if missing:
            raise EngineScanError(
                f"本地 semgrep 规则缺失: {missing} (root={root})"
            )
        return paths

    def build_command(self, ctx, inp, settings) -> list[str]:
        argv = [
            self._binary(settings), "scan", "--sarif", "--metrics=off",
            "--dataflow-traces", "--oss-only",
        ]
        for path in self._config_paths(inp, settings):
            argv += ["--config", path]
        return argv

    def timeout_seconds(self, settings) -> int:
        return settings.scanner_semgrep_timeout_seconds

    def success_exit_codes(self) -> tuple[int, ...]:
        return (0, 1)  # 1 = 命中规则，stdout 仍是 SARIF

    def parse_output(self, stdout: str) -> list[dict[str, Any]]:
        from app.contexts.finding.sarif import normalize

        return normalize("semgrep", json.loads(stdout or "{}"))

    def config_summary(self, ctx, inp, settings) -> dict[str, Any]:
        from app.core.semgrep_rules import local_config_names

        return {
            "engine": "semgrep",
            "configs": local_config_names(
                getattr(inp.profile, "semgrep_configs", None) or []
            ),
            "dataflow_traces": True,
            "oss_only": True,
        }
