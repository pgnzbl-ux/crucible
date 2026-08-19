"""Git 远程地址规范化。

用户可能带或不带 .git 后缀；space/project（如 siteboon/claudecodeui）用来识别
同一仓库的不同 clone URL。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_TAG_RE = re.compile(r"^v?\d+\.\d+", re.IGNORECASE)
_ALLOWED_SCHEMES = {"http", "https", "ssh"}


@dataclass(frozen=True)
class ParsedGitUrl:
    original: str
    normalized: str
    host: str
    project_key: str
    repo_dirname: str


def _strip_git_suffix(path: str) -> str:
    p = path.strip().rstrip("/")
    if p.lower().endswith(".git"):
        p = p[: -len(".git")]
    return p.rstrip("/")


def parse_git_url(raw: str) -> ParsedGitUrl:
    original = (raw or "").strip()
    if not original:
        raise ValueError("Git 地址为空")

    host = ""
    path = ""
    normalized = original

    if original.startswith("git@"):
        # git@github.com:owner/repo.git
        rest = original[4:]
        if ":" not in rest:
            raise ValueError(f"无法解析 Git 地址: {original}")
        host, path = rest.split(":", 1)
        path = _strip_git_suffix(path)
        normalized = f"git@{host}:{path}"
    else:
        parsed = urlparse(original)
        scheme = (parsed.scheme or "").lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise ValueError(f"不支持的 Git 协议: {scheme or '无'}")
        if not parsed.netloc:
            raise ValueError(f"无法解析 Git 地址: {original}")
        host = parsed.hostname or parsed.netloc.split("@")[-1]
        path = _strip_git_suffix(parsed.path.lstrip("/"))
        if scheme in {"http", "https"}:
            port = f":{parsed.port}" if parsed.port else ""
            normalized = f"{scheme}://{host}{port}/{path}"
        else:
            user = parsed.username or "git"
            port = f":{parsed.port}" if parsed.port else ""
            normalized = f"{scheme}://{user}@{host}{port}/{path}"

    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"Git 地址缺少 space/project: {original}")
    project_key = "/".join(parts[:2]) if len(parts) == 2 else "/".join(parts)
    # github.com/a/b → a/b；gitlab 嵌套组保留 path
    if len(parts) >= 2:
        project_key = "/".join(parts)
    repo_dirname = parts[-1]
    if not repo_dirname:
        raise ValueError(f"无法得到仓库目录名: {original}")
    if not host:
        raise ValueError(f"无法得到 Git 主机: {original}")
    return ParsedGitUrl(
        original=original,
        normalized=normalized,
        host=host,
        project_key=project_key,
        repo_dirname=repo_dirname,
    )


def git_url_lookup_candidates(raw: str) -> list[str]:
    """同一仓库可能带或不带 .git，查找时都试一遍。"""
    original = (raw or "").strip()
    if not original:
        return []
    out = [original]
    try:
        parsed = parse_git_url(original)
    except ValueError:
        return out
    out.extend([parsed.normalized, f"{parsed.normalized}.git"])
    seen: list[str] = []
    for item in out:
        if item not in seen:
            seen.append(item)
    return seen


def classify_ref(ref: str | None) -> tuple[str, str]:
    """把用户填写的 ref 分成 branch | tag | commit。空 ref 记为 branch/HEAD。"""
    name = (ref or "").strip()
    if not name or name.upper() == "HEAD":
        return "branch", "HEAD"
    if _COMMIT_RE.fullmatch(name):
        return "commit", name.lower()
    if name.startswith("refs/tags/"):
        return "tag", name[len("refs/tags/") :]
    if name.startswith("tags/"):
        return "tag", name[len("tags/") :]
    if _TAG_RE.match(name):
        return "tag", name
    # 禅道等发行 tag：zentaopms_22.4_20260730（不是合法 branch 名，但旧逻辑会当 branch）
    if name.startswith("zentaopms_"):
        return "tag", name
    return "branch", name


def resolve_ref_type(
    explicit_type: str | None,
    ref: str | None,
) -> tuple[str, str]:
    """用户显式 branch|tag|commit 优先；否则走 classify_ref 自动推断。"""
    ref_type, ref_name = classify_ref(ref)
    if explicit_type in ("branch", "tag", "commit"):
        return explicit_type, ref_name
    return ref_type, ref_name
