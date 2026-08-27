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
        from app.core.semgrep_rules import (
            SemgrepLangDirError,
            ensure_rules,
            local_config_names,
            overlay_config_paths,
            require_allowed_lang_dirs,
        )

        names = local_config_names(getattr(inp.profile, "semgrep_configs", None) or [])
        if not names:
            return []
        try:
            require_allowed_lang_dirs(names)
        except SemgrepLangDirError as exc:
            raise EngineScanError(str(exc)) from exc
        rules_explicit = getattr(settings, "scanner_semgrep_rules_dir", "") or ""
        if not isinstance(rules_explicit, str):
            rules_explicit = ""
        root = ensure_rules(
            explicit=rules_explicit,
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
                f"本地 semgrep 规则缺失: {missing} (root={root})；"
                f"目录名须与 semgrep_configs 完全一致"
            )
        overlay_explicit = getattr(settings, "scanner_semgrep_overlay_dir", "") or ""
        if not isinstance(overlay_explicit, str):
            overlay_explicit = ""
        # 社区语言目录 + {RULES_DIR}/crucible/<lang>（同语言常开）
        for overlay in overlay_config_paths(
            names,
            explicit=overlay_explicit,
            rules_dir=str(root),
        ):
            if overlay not in paths:
                paths.append(overlay)
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
        from app.core.semgrep_rules import local_config_names, overlay_config_paths

        names = local_config_names(
            getattr(inp.profile, "semgrep_configs", None) or []
        )
        rules_explicit = getattr(settings, "scanner_semgrep_rules_dir", "") or ""
        if not isinstance(rules_explicit, str):
            rules_explicit = ""
        overlay_explicit = getattr(settings, "scanner_semgrep_overlay_dir", "") or ""
        if not isinstance(overlay_explicit, str):
            overlay_explicit = ""
        return {
            "engine": "semgrep",
            "configs": names,
            "rules_dir": rules_explicit or None,
            "overlay_configs": overlay_config_paths(
                names,
                explicit=overlay_explicit,
                rules_dir=rules_explicit,
            ),
            "dataflow_traces": True,
            "oss_only": True,
        }
