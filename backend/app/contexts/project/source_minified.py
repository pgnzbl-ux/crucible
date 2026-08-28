"""压缩/打包产物检测 — 「文件很大但行数很少」的源码文件识别。

打包上线的前端资源（如 www/js/zui3/zui.zentao.js）常被压成几行、每行数百 KB：
- AI 节点用 Read 按行读会超过 CLI 工具大小上限（exceeds maximum allowed size）；
- host 侧 triage 切片（±5 行）会产出超预算的巨型切片，被静默丢弃。

本模块只做只读判定与统计：source 节点用于可见性输出，
finding/context_extractor 用于切片保护。容器侧 agent-runner 有一份同阈值的
副本（backend/agent-runner/runner/policies.py::is_minified_file 默认值），两边必须保持一致。
"""
from __future__ import annotations

import os
from pathlib import Path

# 判定阈值：大小 ≥300KB 且 行数 ≤500 视为压缩产物。
# 未压缩的第三方库（如 jquery ~300KB / 上万行）不会误伤；正常源码单行远小于 300KB。
MINIFIED_MIN_BYTES = 300_000
MINIFIED_MAX_LINES = 500
# 行数只需在前 4MB 内数即可判定：更大的文件行数只会更多，不影响结论
_LINE_COUNT_SCAN_BYTES = 4 * 1024 * 1024

# 扫描剪枝：与 profile_detector 的遍历剪枝对齐
_PRUNE_DIRS = {".git", "node_modules"}


def _count_lines(path: Path) -> int:
    """流式数换行（只读前 _LINE_COUNT_SCAN_BYTES）；末行无换行也计一行。"""
    lines = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1 << 16)
            if not chunk:
                break
            lines += chunk.count(b"\n")
            if fh.tell() >= _LINE_COUNT_SCAN_BYTES:
                break
    return lines + 1


def _minified_size(path: Path) -> int | None:
    """命中压缩判定时返回文件大小，否则 None；stat/读失败按未命中处理。"""
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size < MINIFIED_MIN_BYTES:
            return None
        if _count_lines(path) > MINIFIED_MAX_LINES:
            return None
    except OSError:
        return None
    return size


def is_minified(path: str | os.PathLike[str]) -> bool:
    """单文件判定：大而少行 → 压缩/打包产物。"""
    return _minified_size(Path(path)) is not None


def scan_minified_files(root: str | os.PathLike[str]) -> dict:
    """遍历 root 统计压缩产物：{count, total_bytes, top:[按大小前 10 的相对路径]}。"""
    root_path = Path(root)
    hits: list[tuple[int, str]] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
        for name in filenames:
            fp = Path(dirpath) / name
            size = _minified_size(fp)
            if size is not None:
                hits.append((size, fp.relative_to(root_path).as_posix()))
    hits.sort(reverse=True)
    return {
        "count": len(hits),
        "total_bytes": sum(size for size, _ in hits),
        "top": [rel for _, rel in hits[:10]],
    }
