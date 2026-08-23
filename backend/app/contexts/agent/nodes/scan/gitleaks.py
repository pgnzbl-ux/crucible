"""gitleaks 适配器(discovery-spec §6.1)。

默认扫全 git 历史；无 .git(本地上传包)降级 files 模式。
`--redact` + 本地 redact_secrets 双保险(§8.2)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import EngineScanNode


class GitleaksNode(EngineScanNode):
    node_key = "scan_gitleaks"
    engine = "gitleaks"

    def enabled(self, settings) -> bool:
        return settings.scanner_gitleaks_enabled

    def _binary(self, settings) -> str:
        from app.core.scanners import resolve

        return resolve(
            "gitleaks",
            bin_dir=settings.scanner_bin_dir,
            auto_install=settings.scanner_auto_install,
        )

    def build_command(self, ctx, inp, settings) -> list[str]:
        argv = [
            self._binary(settings), "detect",
            "--source", self._repo_root(inp, ctx),
            "--report-format", "sarif", "--report-path", "-",
            "--redact", "--exit-code", "0",
        ]
        if not (Path(self._repo_root(inp, ctx)) / ".git").exists():
            argv.append("--no-git")  # 本地上传包：files 模式
        return argv

    def timeout_seconds(self, settings) -> int:
        return settings.scanner_gitleaks_timeout_seconds

    def parse_output(self, stdout: str) -> list[dict[str, Any]]:
        from app.contexts.finding.sarif import normalize

        return normalize("gitleaks", json.loads(stdout or "{}"))

    def config_summary(self, ctx, inp, settings) -> dict[str, Any]:
        root = self._repo_root(inp, ctx)
        return {"engine": "gitleaks", "mode": "git" if (Path(root) / ".git").exists() else "files"}
