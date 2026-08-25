"""Crucible overlay：禅道蒸馏夹具 + 各语言关键夹具 JSON 扫描回归。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.semgrep_rules import overlay_rules_dir

REPO = Path(__file__).resolve().parents[2]
OVERLAY = overlay_rules_dir()
FIXTURE_ZENTAO = OVERLAY / "php" / "regression" / "zentao-chart-bi"
OVERLAY_PHP = OVERLAY / "php"
# 与 .env SCANNER_SEMGREP_RULES_DIR 对齐：backend/semgrep_rules/php
COMMUNITY_PHP = Path(__file__).resolve().parents[1] / "semgrep_rules" / "php"

# 各语言关键夹具目录（规则旁正例文件，轻量 JSON scan）
_CRITICAL_TARGETS: list[tuple[str, Path, Path, tuple[str, ...]]] = [
    (
        "python",
        OVERLAY / "python",
        OVERLAY / "python" / "fastapi",
        ("fastapi-tainted-sql", "sqlalchemy2-tainted-text", "flask-sql-fragment"),
    ),
    (
        "go",
        OVERLAY / "go",
        OVERLAY / "go" / "gin-echo",
        ("gin-echo-sqli", "chi-fiber-sqli", "gorm-where-receiver"),
    ),
    (
        "java",
        OVERLAY / "java",
        OVERLAY / "java" / "jdbc",
        (
            "jdbc-sql-fragment-concat",
            "mybatis-xml-dollar-interp",
            "mybatis-java-select-concat",
            "jsp-scriptlet-taint",
        ),
    ),
]


def _semgrep_env() -> dict[str, str]:
    """把 semgrep 用户数据落到仓库内可写目录。

    Semgrep 1.x 不认 SEMGREP_USER_DATA_DIR；实际读 XDG_CONFIG_HOME/.semgrep
    （见 semgrep.env.Env.user_data_folder）。沙箱/CI 若无法写 ~/.semgrep，
    pysemgrep 会在打开 semgrep.log 时 PermissionError，rc=1 且 stdout 为空——
    与「命中规则」的 exit 1 撞车，回归会误报 0 results。
    """
    env = os.environ.copy()
    xdg = REPO / ".semgrep-xdg"
    xdg.mkdir(exist_ok=True)
    data = xdg / ".semgrep"
    data.mkdir(exist_ok=True)
    env["XDG_CONFIG_HOME"] = str(xdg)
    env["SEMGREP_SETTINGS_FILE"] = str(data / "settings.yml")
    env["SEMGREP_LOG_FILE"] = str(data / "semgrep.log")
    # 版本检查默认写 ~/.cache/semgrep_version；沙箱不可写会导致扫描已成功后仍 rc=2
    env["SEMGREP_VERSION_CACHE_PATH"] = str(data / "semgrep_version")
    env["SEMGREP_ENABLE_VERSION_CHECK"] = "0"
    return env


def _semgrep_bin() -> str:
    cand = REPO / ".venv" / "bin" / "semgrep"
    if cand.is_file():
        return str(cand)
    found = shutil.which("semgrep")
    if found:
        return found
    pytest.skip("semgrep 未安装")


def _run_overlay_scan(config_dir: Path, target: Path) -> list[dict]:
    argv = [
        _semgrep_bin(),
        "scan",
        "--json",
        "--metrics=off",
        "--oss-only",
        "--disable-version-check",
        "--config",
        str(config_dir),
        str(target),
    ]
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=180,
        env=_semgrep_env(),
        cwd=str(REPO),
    )
    assert proc.returncode in (0, 1), (
        f"semgrep failed rc={proc.returncode}\nstderr={proc.stderr[:2000]}"
    )
    assert (proc.stdout or "").strip(), (
        "semgrep stdout 为空（常见于无法写用户数据目录导致启动崩溃；"
        f"rc={proc.returncode}）\nstderr={proc.stderr[:2000]}"
    )
    data = json.loads(proc.stdout)
    return data.get("results") or []


def _rule_tail(check_id: str) -> str:
    return (check_id or "").rsplit(".", 1)[-1]


@pytest.mark.skipif(not FIXTURE_ZENTAO.is_dir(), reason="zentao 蒸馏夹具缺失")
@pytest.mark.skipif(not OVERLAY_PHP.is_dir(), reason="php overlay 缺失")
def test_zentao_fixture_yields_cwe89_on_filter_sql_paths():
    """chart/model getFilterFormat 或 bi/model getMultiData 路径须有 CWE-89。"""
    argv = [
        _semgrep_bin(),
        "scan",
        "--json",
        "--metrics=off",
        "--oss-only",
        "--disable-version-check",
        "--config",
        str(OVERLAY_PHP),
        str(FIXTURE_ZENTAO),
    ]
    if COMMUNITY_PHP.is_dir():
        # 与 scan_semgrep 一致：社区 + overlay
        argv = [
            _semgrep_bin(),
            "scan",
            "--json",
            "--metrics=off",
            "--oss-only",
            "--disable-version-check",
            "--config",
            str(COMMUNITY_PHP),
            "--config",
            str(OVERLAY_PHP),
            str(FIXTURE_ZENTAO),
        ]

    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=180,
        env=_semgrep_env(),
        cwd=str(REPO),
    )
    assert proc.returncode in (0, 1), (
        f"semgrep failed rc={proc.returncode}\nstderr={proc.stderr[:2000]}"
    )
    assert (proc.stdout or "").strip(), (
        "semgrep stdout 为空（常见于无法写用户数据目录导致启动崩溃；"
        f"rc={proc.returncode}）\nstderr={proc.stderr[:2000]}"
    )
    data = json.loads(proc.stdout)
    results = data.get("results") or []

    def _is_cwe89(r: dict) -> bool:
        md = (r.get("extra") or {}).get("metadata") or {}
        cwe = md.get("cwe") or []
        if isinstance(cwe, str):
            cwe = [cwe]
        blob = " ".join(str(x) for x in cwe) + " " + str(r.get("check_id") or "")
        return "89" in blob or "sql-fragment" in blob or "pdo-mysqli" in blob

    hits = [
        r
        for r in results
        if _is_cwe89(r)
        and (
            "chart/model.php" in (r.get("path") or "").replace("\\", "/")
            or "bi/model.php" in (r.get("path") or "").replace("\\", "/")
        )
    ]
    assert hits, (
        "期望 chart/model.php 或 bi/model.php 上至少 1 条 CWE-89；"
        f"got {len(results)} results: "
        + ", ".join(
            f"{r.get('check_id')}@{r.get('path')}:{r.get('start', {}).get('line')}"
            for r in results[:20]
        )
    )


@pytest.mark.parametrize(
    "lang,config_dir,target,expected_ids",
    _CRITICAL_TARGETS,
    ids=[t[0] for t in _CRITICAL_TARGETS],
)
def test_critical_overlay_fixture_json_scan(
    lang: str,
    config_dir: Path,
    target: Path,
    expected_ids: tuple[str, ...],
):
    """python/go/java 关键夹具目录：overlay JSON scan 至少命中一条期望规则。"""
    if not config_dir.is_dir():
        pytest.skip(f"{lang} overlay 缺失")
    if not target.is_dir():
        pytest.skip(f"{lang} 关键夹具缺失: {target}")

    results = _run_overlay_scan(config_dir, target)
    wanted = set(expected_ids)
    hits = [r for r in results if _rule_tail(r.get("check_id") or "") in wanted]
    assert hits, (
        f"{lang}: 期望在 {target.relative_to(OVERLAY)} 命中 "
        f"{sorted(wanted)} 之一；got {len(results)} results: "
        + ", ".join(
            f"{_rule_tail(r.get('check_id') or '')}"
            f"@{Path(r.get('path') or '').name}:{r.get('start', {}).get('line')}"
            for r in results[:20]
        )
    )
