"""tree-sitter 函数级索引与按需上下文提取。

索引：[{file, symbol, start_line, end_line}]，存 workdir .crucible-index/functions.json。
现行语言：Python / Java / JavaScript / TypeScript / Go / PHP。
profile.languages 决定建哪些；画像有语言但无一可索引时返回空（不回退全扫）。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

INDEX_DIR = ".crucible-index"
INDEX_FILE = "functions.json"
_CONTEXT_PAD = 5

# 扩展名 → 索引语言 id
_LANGUAGE_BY_EXT = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".php": "php",
}

# 画像 language id → 索引语言（nodejs 同时索引 js/ts）
_PROFILE_TO_INDEX_LANGS: dict[str, tuple[str, ...]] = {
    "python": ("python",),
    "java": ("java",),
    "nodejs": ("javascript", "typescript"),
    "go": ("go",),
    "php": ("php",),
}

_ALL_INDEX_LANGS = frozenset(_LANGUAGE_BY_EXT.values())

# 索引构建剪枝(与 profile_detector 的触发文件排除保持一致)
_SKIP_DIRS = {
    "node_modules", "vendor", ".venv", "venv", ".git", "dist", "build",
    "target", ".tox", "__pycache__", "site-packages",
}


def resolve_index_languages(profile_language_ids: Iterable[str] | None = None) -> list[str]:
    """由画像 languages[].id 决定索引语言。

    - None（无画像）：扫全部已支持语言。
    - 空可迭代（有画像但 languages=[]）：返回空，聚类降级。
    - 有语言但无一映射到索引语言（如仅 rust）：返回空，聚类降级 rule_id。
    """
    from app.contexts.agent.stacks.registry import index_langs_for_profile_ids

    if profile_language_ids is None:
        return index_langs_for_profile_ids(None)
    return index_langs_for_profile_ids([str(x) for x in profile_language_ids])


def _parse_functions(language: str, source: str) -> list[tuple[str, int, int]]:
    """返回 [(symbol, start_line, end_line)]；行号 1-based。tree-sitter 优先，正则兜底。"""
    try:
        return _parse_functions_treesitter(language, source)
    except Exception:  # noqa: BLE001 — grammar 缺失等场景退化为启发式
        return _parse_functions_heuristic(language, source)


def _parse_functions_treesitter(language: str, source: str) -> list[tuple[str, int, int]]:
    from tree_sitter import Language, Parser

    kinds: set[str]
    name_field = "name"
    if language == "python":
        import tree_sitter_python
        lang = Language(tree_sitter_python.language())
        kinds = {"function_definition"}
    elif language == "java":
        import tree_sitter_java
        lang = Language(tree_sitter_java.language())
        kinds = {"method_declaration"}
    elif language == "javascript":
        import tree_sitter_javascript
        lang = Language(tree_sitter_javascript.language())
        kinds = {"function_declaration", "method_definition", "generator_function_declaration"}
    elif language == "typescript":
        import tree_sitter_typescript
        # .tsx 用 tsx grammar；普通 .ts 用 typescript
        lang = Language(tree_sitter_typescript.language_typescript())
        kinds = {"function_declaration", "method_definition", "generator_function_declaration"}
    elif language == "go":
        import tree_sitter_go
        lang = Language(tree_sitter_go.language())
        kinds = {"function_declaration", "method_declaration"}
    elif language == "php":
        import tree_sitter_php
        lang = Language(tree_sitter_php.language_php())
        kinds = {"function_definition", "method_declaration"}
    else:
        return []

    parser = Parser(lang)
    raw = source.encode("utf-8")
    tree = parser.parse(raw)
    out: list[tuple[str, int, int]] = []

    def walk(node) -> None:
        if node.type in kinds:
            name_node = node.child_by_field_name(name_field)
            if name_node is not None:
                symbol = raw[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
                if symbol:
                    out.append((symbol, node.start_point[0] + 1, node.end_point[0] + 1))
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return out


def _parse_tsx_functions(source: str) -> list[tuple[str, int, int]]:
    """tsx 文件用 language_tsx。"""
    import tree_sitter_typescript
    from tree_sitter import Language, Parser

    lang = Language(tree_sitter_typescript.language_tsx())
    kinds = {"function_declaration", "method_definition", "generator_function_declaration"}
    parser = Parser(lang)
    tree = parser.parse(source.encode("utf-8"))
    out: list[tuple[str, int, int]] = []
    raw = source.encode("utf-8")

    def walk(node) -> None:
        if node.type in kinds:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                symbol = raw[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
                if symbol:
                    out.append((symbol, node.start_point[0] + 1, node.end_point[0] + 1))
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return out


def _parse_functions_heuristic(language: str, source: str) -> list[tuple[str, int, int]]:
    """正则兜底：只用于分组反查，容忍误差。"""
    import re

    out: list[tuple[str, int, int]] = []
    lines = source.splitlines()
    if language == "python":
        pat = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)")
        for i, line in enumerate(lines):
            m = pat.match(line)
            if m:
                out.append((m.group(1), i + 1, i + 1))
    elif language == "java":
        pat = re.compile(
            r"^\s*(?:public|protected|private|static|final|synchronized|\s)+"
            r"[\w<>\[\],\s]+?\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("
        )
        for i, line in enumerate(lines):
            m = pat.match(line)
            if m:
                out.append((m.group(1), i + 1, i + 1))
    elif language in ("javascript", "typescript"):
        pat = re.compile(
            r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_][A-Za-z0-9_]*)"
        )
        for i, line in enumerate(lines):
            m = pat.match(line)
            if m:
                out.append((m.group(1), i + 1, i + 1))
    elif language == "go":
        # func Name( / func (recv T) Name(
        pat = re.compile(
            r"^\s*func\s+(?:\([^)]+\)\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\("
        )
        for i, line in enumerate(lines):
            m = pat.match(line)
            if m:
                out.append((m.group(1), i + 1, i + 1))
    elif language == "php":
        pat = re.compile(
            r"^\s*(?:public|protected|private|static|final|\s)*function\s+&?([A-Za-z_][A-Za-z0-9_]*)\s*\("
        )
        for i, line in enumerate(lines):
            m = pat.match(line)
            if m:
                out.append((m.group(1), i + 1, i + 1))
    return out


def build_function_index(
    repo_root: str,
    *,
    languages: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """扫仓库建函数索引。languages 为索引语言 id 集合；None = 全部已支持。"""
    import os

    allowed = set(languages) if languages is not None else set(_ALL_INDEX_LANGS)
    root = Path(repo_root)
    index: list[dict[str, Any]] = []
    if not root.is_dir():
        return index
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            suffix = Path(fn).suffix
            language = _LANGUAGE_BY_EXT.get(suffix)
            if not language or language not in allowed:
                continue
            path = Path(dirpath) / fn
            try:
                source = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if suffix == ".tsx":
                try:
                    funcs = _parse_tsx_functions(source)
                except Exception:  # noqa: BLE001
                    funcs = _parse_functions_heuristic("typescript", source)
            else:
                funcs = _parse_functions(language, source)
            for symbol, start, end in funcs:
                index.append({
                    "file": path.relative_to(root).as_posix(),
                    "symbol": symbol,
                    "start_line": start,
                    "end_line": end,
                })
    return index


def save_index(host_workdir: str, index: list[dict[str, Any]]) -> str:
    """索引落 workdir；返回文件路径。"""
    index_dir = Path(host_workdir) / INDEX_DIR
    index_dir.mkdir(parents=True, exist_ok=True)
    path = index_dir / INDEX_FILE
    path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return str(path)


@lru_cache(maxsize=4)
def _load_index_cached(host_workdir: str) -> tuple[dict[str, Any], ...]:
    path = Path(host_workdir) / INDEX_DIR / INDEX_FILE
    if not path.exists():
        return ()
    try:
        return tuple(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return ()


def load_index(host_workdir: str) -> list[dict[str, Any]]:
    """索引只读共享：并发代表审议/快审 prepare 各自调用会重复读盘并解析
    （大仓库的符号索引是 MB 级 JSON），lru_cache 按 workdir 记忆化。
    返回可变 list 视图，调用方只读。"""
    return list(_load_index_cached(host_workdir))


# ── 查询 API ──

def enclosing(index: list[dict[str, Any]], file_path: str, line: int | None) -> dict[str, Any] | None:
    """命中行 → 所在函数。file_path 归一为相对仓库根的 posix 路径。"""
    rel = (file_path or "").replace("\\", "/").lstrip("/")
    if line is None:
        return None
    candidates = [e for e in index if e["file"].endswith(rel.split("/")[-1]) or e["file"] == rel]
    for entry in candidates:
        if entry["start_line"] <= line <= entry["end_line"]:
            return entry
    # 文件内最近的函数(容错：树解析与行号偶有偏差)
    same_file = [e for e in candidates if e["file"] == rel or e["file"].endswith(rel)]
    if same_file:
        return min(same_file, key=lambda e: abs((e["start_line"] + e["end_line"]) // 2 - line))
    return None


def symbol_lookup(index: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    return [e for e in index if e["symbol"] == symbol]


def read_function_source(repo_root: str, entry: dict[str, Any]) -> str | None:
    """切函数源码，带行号前缀(cat -n 风格)。"""
    path = Path(repo_root) / entry["file"]
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    start = max(entry["start_line"] - 1, 0)
    end = min(entry["end_line"], len(lines))
    return "\n".join(
        f"{i + 1}\t{lines[i]}" for i in range(start, end)
    )


def context_around(repo_root: str, file_path: str, line: int | None) -> str | None:
    """无索引时的退化上下文：命中行 ±_CONTEXT_PAD 行，带行号。"""
    if line is None:
        return None
    path = Path(repo_root) / (file_path or "").lstrip("/")
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    start = max(line - 1 - _CONTEXT_PAD, 0)
    end = min(line + _CONTEXT_PAD, len(lines))
    return "\n".join(f"{i + 1}\t{lines[i]}" for i in range(start, end))
