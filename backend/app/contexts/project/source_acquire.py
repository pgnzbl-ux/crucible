"""准备任务工作区源码：表记录命中则拉 MinIO，否则 git clone 到 {workdir}/{repo_dirname}。"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from typing import Callable

from app.contexts.project.git_url import parse_git_url, resolve_ref_type
from app.contexts.project.source_cache import (
    MinioSourceStore,
    extract_source_archive,
    object_access_url,
    pack_project_dir,
    source_object_key,
)
from app.core.agent_runner import git_clone_to_workdir

logger = logging.getLogger(__name__)

CloneFn = Callable[[str, str, str | None, str], tuple[bool, str]]
ShaFn = Callable[[str], str | None]
RemoteShaFn = Callable[[str, str | None], str | None]
CachedByShaFn = Callable[[str], "CachedSource | None"]

_SHA_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_GIT_ENV_BLOCKLIST = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


@dataclass
class CachedSource:
    object_key: str
    object_url: str
    repo_dirname: str
    commit_sha: str
    ref_type: str
    ref_name: str
    git_url_normalized: str
    project_key: str
    git_host: str


@dataclass
class SourceAcquireResult:
    ok: bool
    error: str = ""
    origin: str = "git"
    git_url_original: str = ""
    git_url_normalized: str = ""
    project_key: str = ""
    git_host: str = ""
    repo_dirname: str = ""
    ref_type: str = "branch"
    ref_name: str = "HEAD"
    commit_sha: str = ""
    project_path: str = ""
    object_key: str | None = None
    object_url: str | None = None
    top_level: list[str] = field(default_factory=list)
    file_count: int = 0
    size_bytes: int | None = None


def _project_has_files(project_dir: str) -> bool:
    if not os.path.isdir(project_dir):
        return False
    return any(e != ".git" for e in os.listdir(project_dir))


def _list_top_level(project_dir: str) -> list[str]:
    if not os.path.isdir(project_dir):
        return []
    return sorted(e for e in os.listdir(project_dir) if e != ".git")[:50]


def git_subprocess_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """清掉 GIT_DIR 等，避免 worker 在 Crucible 仓库目录里跑时 rev-parse 读到平台自己的 SHA。"""
    env = dict(base if base is not None else os.environ)
    for key in _GIT_ENV_BLOCKLIST:
        env.pop(key, None)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def _local_head_sha(project_dir: str) -> str | None:
    git_dir = os.path.join(project_dir, ".git")
    try:
        result = subprocess.run(
            ["git", "--git-dir", git_dir, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            env=git_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha[:40] if len(sha) >= 40 else None


def _rmtree(path: str) -> bool:
    if not os.path.lexists(path):
        return True
    if os.path.islink(path) or not os.path.isdir(path):
        try:
            os.unlink(path)
        except OSError:
            return False
        return not os.path.lexists(path)

    def _force_remove(func, p, _exc):  # noqa: ANN001
        try:
            os.chmod(p, 0o777)
            func(p)
        except Exception:
            pass

    try:
        shutil.rmtree(path, onerror=_force_remove)
    except OSError:
        pass
    return not os.path.lexists(path)


def _clear_destination(path: str) -> str | None:
    """确保正式源码目录可重新使用；删不掉时原子隔离旧目录。

    rename 只需要任务工作区父目录的写权限，不依赖旧树内部 uid/gid，因此能
    处理被靶场容器改成 nobody/root 的源码。返回未能立即删除的隔离目录。
    """
    if _rmtree(path):
        return None
    parent = os.path.dirname(path)
    name = os.path.basename(path)
    stale = os.path.join(parent, f".crucible-stale-{name}-{uuid.uuid4().hex[:8]}")
    try:
        os.replace(path, stale)
    except OSError as exc:
        raise PermissionError(
            f"源码工作区准备失败: 无法清理或隔离 {path}: {exc}"
        ) from exc
    logger.warning("源码目录权限异常，已隔离旧目录: %s -> %s", path, stale)
    if not _rmtree(stale):
        logger.warning("隔离源码目录仍无法删除，留待工作区巡检清理: %s", stale)
        return stale
    return None


def _restore_cached(cached: CachedSource, store, host_workdir: str) -> bool:
    dest = os.path.join(host_workdir, cached.repo_dirname)
    staging = tempfile.mkdtemp(prefix=".source-restore-", dir=host_workdir)
    fd, archive = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    try:
        store.download(cached.object_key, archive)
        extract_source_archive(archive, staging)
        staged_dest = os.path.join(staging, cached.repo_dirname)
        if not _project_has_files(staged_dest):
            return False
        _clear_destination(dest)
        os.replace(staged_dest, dest)
        return True
    finally:
        _rmtree(staging)
        try:
            os.remove(archive)
        except OSError:
            pass


def _sha_matches(cached_sha: str, remote_sha: str) -> bool:
    a = (cached_sha or "").lower()
    b = (remote_sha or "").lower()
    if not a or not b:
        return False
    return a.startswith(b) or b.startswith(a)


def _parse_ls_remote_branch_stdout(stdout: str, spec: str) -> str | None:
    """只认 HEAD / refs/heads，不把同名 tag 当成 branch tip。"""
    want_head = spec == "HEAD"
    want_branch = spec[11:] if spec.startswith("refs/heads/") else spec
    for raw in (stdout or "").splitlines():
        parts = raw.split()
        if len(parts) < 2:
            continue
        sha, refname = parts[0], parts[1]
        if not _SHA_TOKEN_RE.fullmatch(sha):
            continue
        if refname.startswith("refs/remotes/") or refname.startswith("refs/tags/"):
            continue
        if want_head and refname == "HEAD":
            return sha
        if refname == f"refs/heads/{want_branch}":
            return sha
    return None


def _parse_ls_remote_tag_stdout(stdout: str) -> str | None:
    peeled: str | None = None
    fallback: str | None = None
    for raw in (stdout or "").splitlines():
        parts = raw.split()
        if len(parts) < 2:
            continue
        sha, refname = parts[0], parts[1]
        if not _SHA_TOKEN_RE.fullmatch(sha) or not refname.startswith("refs/tags/"):
            continue
        if refname.endswith("^{}"):
            peeled = sha
        else:
            fallback = sha
    return peeled or fallback


def _ls_remote_branch_sha(git_url: str, ref_name: str) -> str | None:
    specs = ["HEAD"] if ref_name == "HEAD" else [f"refs/heads/{ref_name}"]
    for spec in specs:
        try:
            result = subprocess.run(
                ["git", "ls-remote", git_url, spec],
                capture_output=True,
                text=True,
                timeout=30,
                env=git_subprocess_env(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            continue
        sha = _parse_ls_remote_branch_stdout(result.stdout or "", spec)
        if sha:
            return sha
    return None


def _ls_remote_tag_sha(git_url: str, ref_name: str) -> str | None:
    # annotated tag：只查 refs/tags/name 时 GitHub 只回 tag object SHA；
    # 缓存键是 clone 后 HEAD commit，必须先要 peeled。
    specs = (f"refs/tags/{ref_name}^{{}}", f"refs/tags/{ref_name}", ref_name)
    for spec in specs:
        try:
            result = subprocess.run(
                ["git", "ls-remote", git_url, spec],
                capture_output=True,
                text=True,
                timeout=30,
                env=git_subprocess_env(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            continue
        sha = _parse_ls_remote_tag_stdout(result.stdout or "")
        if sha:
            return sha
    return None


def resolve_remote_ref(
    git_url: str,
    ref: str | None,
    *,
    ref_type_hint: str | None = None,
    remote_sha_fn: RemoteShaFn | None = None,
) -> tuple[str, str, str | None]:
    """解析远端 ref 真实类型与 commit SHA（branch 查不到时再试同名 tag）。

    ref_type_hint 为 tag/commit 时直接查对应渠道，不调 remote_sha_fn（tag 不变性）。
    remote_sha_fn 仅在自动推断（无 hint）的 branch 流程中用于测试注入。
    """
    ref_type, ref_name = resolve_ref_type(ref_type_hint, ref)
    if ref_type == "commit":
        return ref_type, ref_name, ref_name.lower()
    if ref_type == "tag":
        return ref_type, ref_name, _ls_remote_tag_sha(git_url, ref_name)
    # branch（含显式 hint 和自动推断）
    if remote_sha_fn is not None:
        return ref_type, ref_name, remote_sha_fn(git_url, ref)
    if ref_type == "branch":
        return ref_type, ref_name, _ls_remote_branch_sha(git_url, ref_name)
    # 自动推断：branch 查不到则再试 tag
    branch_sha = _ls_remote_branch_sha(git_url, ref_name)
    if branch_sha:
        return "branch", ref_name, branch_sha
    tag_sha = _ls_remote_tag_sha(git_url, ref_name)
    if tag_sha:
        return "tag", ref_name, tag_sha
    return "branch", ref_name, None


def _parse_ls_remote_stdout(stdout: str, spec: str) -> str | None:
    """从 ls-remote 正文取出 commit SHA：跳过非 hex / refs/remotes；tag 优先 peeled。"""
    peeled: str | None = None
    heads: str | None = None
    first_hex: str | None = None
    want_head = spec == "HEAD"
    want_branch = spec[11:] if spec.startswith("refs/heads/") else spec
    for raw in (stdout or "").splitlines():
        parts = raw.split()
        if len(parts) < 2:
            continue
        sha, refname = parts[0], parts[1]
        if not _SHA_TOKEN_RE.fullmatch(sha):
            continue
        if refname.startswith("refs/remotes/"):
            continue
        if refname.endswith("^{}"):
            peeled = sha
            continue
        if first_hex is None:
            first_hex = sha
        if want_head and refname == "HEAD":
            return sha
        if refname == f"refs/heads/{want_branch}" or refname == want_branch:
            heads = sha
    if peeled:
        return peeled
    if heads:
        return heads
    return first_hex


def _ls_remote_sha(git_url: str, ref: str | None) -> str | None:
    _ref_type, _ref_name, sha = resolve_remote_ref(git_url, ref)
    return sha


def _upload_cache(
    store,
    owner_id: str,
    project_key: str,
    sha: str,
    repo_dir: str,
    repo_dirname: str,
    git_host: str,
) -> str:
    fd, archive = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    try:
        pack_project_dir(repo_dir, archive, arcname=repo_dirname)
        key = source_object_key(owner_id, git_host, project_key, sha)
        store.upload(key, sha, archive)
        return key
    finally:
        try:
            os.remove(archive)
        except OSError:
            pass


def acquire_source(
    host_workdir: str,
    git_url: str,
    ref: str | None,
    *,
    ref_type_hint: str | None = None,
    clone_depth: int | None = 1,
    cached: CachedSource | None = None,
    store=None,
    clone_fn: CloneFn | None = None,
    local_sha_fn: ShaFn | None = None,
    remote_sha_fn: RemoteShaFn | None = None,
    cached_by_sha_fn: CachedByShaFn | None = None,
    owner_id: str | None = None,
) -> SourceAcquireResult:
    """返回源码落地结果。失败时 ok=False 且 error 含网络/权限/空仓等原因。"""
    try:
        parsed = parse_git_url(git_url)
    except ValueError as e:
        return SourceAcquireResult(
            ok=False,
            error=f"源码克隆失败: {e}",
            git_url_original=git_url or "",
        )
    ref_type, ref_name = resolve_ref_type(ref_type_hint, ref)
    store = store or MinioSourceStore()
    sha_fn = local_sha_fn or _local_head_sha
    dest = os.path.join(host_workdir, parsed.repo_dirname)
    stored_url = parsed.normalized

    if clone_fn is None:
        depth = clone_depth
        hint = ref_type_hint

        def clone_fn(  # noqa: ANN001
            wd: str, url: str, r: str | None, dirname: str,
        ) -> tuple[bool, str]:
            return git_clone_to_workdir(
                wd, url, r, dirname, ref_type=hint, clone_depth=depth,
            )

    resolved_type, resolved_name, remote_sha = resolve_remote_ref(
        git_url, ref, ref_type_hint=ref_type_hint, remote_sha_fn=remote_sha_fn
    )
    if ref_type_hint is None and (
        resolved_type != ref_type or resolved_name != ref_name
    ):
        logger.info(
            "引用 %r 解析为 %s/%s（原分类 %s/%s）",
            ref,
            resolved_type,
            resolved_name,
            ref_type,
            ref_name,
        )
        ref_type, ref_name = resolved_type, resolved_name

    use_cache = False
    if cached is not None or cached_by_sha_fn is not None:
        if ref_type == "tag":
            # 人指定 tag：按名字命中即复用（不变性）。annotated tag 的 ls-remote
            # 常给出 tag object SHA，与落库的 commit SHA 不同，不能因此重 clone。
            if cached is not None:
                use_cache = True
            elif cached_by_sha_fn is not None and remote_sha:
                by_sha = cached_by_sha_fn(remote_sha)
                if by_sha is not None:
                    cached = by_sha
                    use_cache = True
        elif ref_type == "commit":
            if cached is not None and (
                not remote_sha or _sha_matches(cached.commit_sha, remote_sha)
            ):
                use_cache = True
        elif not remote_sha:
            logger.warning("无法确认远端 SHA，忽略源码缓存并重新 clone")
        else:
            by_sha = cached_by_sha_fn(remote_sha) if cached_by_sha_fn else None
            if by_sha is not None:
                cached = by_sha
                use_cache = True
            elif cached is not None and _sha_matches(cached.commit_sha, remote_sha):
                use_cache = True
            elif cached is not None:
                logger.info(
                    "远端 SHA 已变 cached=%s remote=%s ref=%s(%s)，忽略源码缓存",
                    cached.commit_sha[:12],
                    remote_sha[:12],
                    ref_name,
                    ref_type,
                )
    if cached is not None and use_cache:
        cached_dest = os.path.join(host_workdir, cached.repo_dirname)
        try:
            if _restore_cached(cached, store, host_workdir):
                entries = _list_top_level(cached_dest)
                return SourceAcquireResult(
                    ok=True,
                    origin="minio",
                    git_url_original=stored_url,
                    git_url_normalized=cached.git_url_normalized or stored_url,
                    project_key=cached.project_key,
                    git_host=cached.git_host,
                    repo_dirname=cached.repo_dirname,
                    ref_type=cached.ref_type,
                    ref_name=cached.ref_name,
                    commit_sha=cached.commit_sha,
                    project_path=cached_dest,
                    object_key=cached.object_key,
                    object_url=cached.object_url,
                    top_level=entries,
                    file_count=len(entries),
                )
            logger.warning("MinIO 源码解开后目录为空，回退 clone: %s", dest)
        except Exception as e:  # noqa: BLE001
            logger.warning("MinIO 源码拉取失败，回退 clone: %s", e)

    try:
        _clear_destination(dest)
    except PermissionError as e:
        return SourceAcquireResult(
            ok=False,
            error=str(e),
            git_url_original=stored_url,
            git_url_normalized=stored_url,
            project_key=parsed.project_key,
            git_host=parsed.host,
            repo_dirname=parsed.repo_dirname,
            ref_type=ref_type,
            ref_name=ref_name,
        )

    ok, err = clone_fn(host_workdir, git_url, ref, parsed.repo_dirname)
    if not ok:
        return SourceAcquireResult(
            ok=False,
            error=err,
            git_url_original=stored_url,
            git_url_normalized=stored_url,
            project_key=parsed.project_key,
            git_host=parsed.host,
            repo_dirname=parsed.repo_dirname,
            ref_type=ref_type,
            ref_name=ref_name,
        )

    entries = _list_top_level(dest)
    sha = sha_fn(dest) or ""
    object_key = None
    object_url = None
    if sha and owner_id:
        try:
            object_key = _upload_cache(
                store,
                owner_id,
                parsed.project_key,
                sha,
                dest,
                parsed.repo_dirname,
                parsed.host,
            )
            object_url = object_access_url(object_key)
        except Exception as e:  # noqa: BLE001
            logger.warning("源码缓存上传失败（任务继续）: %s", e)

    return SourceAcquireResult(
        ok=True,
        origin="git",
        git_url_original=stored_url,
        git_url_normalized=stored_url,
        project_key=parsed.project_key,
        git_host=parsed.host,
        repo_dirname=parsed.repo_dirname,
        ref_type=ref_type,
        ref_name=ref_name,
        commit_sha=sha,
        project_path=dest,
        object_key=object_key,
        object_url=object_url,
        top_level=entries,
        file_count=len(entries),
    )


def acquire_uploaded_source(
    host_workdir: str,
    *,
    cached: CachedSource | None,
    store=None,
) -> SourceAcquireResult:
    """从 MinIO 解开已上传的源码包。缓存缺失不得回退 git clone。"""
    if cached is None:
        return SourceAcquireResult(
            ok=False,
            error="源码解包失败: 未找到已上传的源码包",
            origin="upload",
        )
    store = store or MinioSourceStore()
    dest = os.path.join(host_workdir, cached.repo_dirname)
    try:
        if _restore_cached(cached, store, host_workdir):
            entries = _list_top_level(dest)
            return SourceAcquireResult(
                ok=True,
                origin="upload",
                git_url_original=cached.git_url_normalized,
                git_url_normalized=cached.git_url_normalized,
                project_key=cached.project_key,
                git_host=cached.git_host,
                repo_dirname=cached.repo_dirname,
                ref_type=cached.ref_type or "upload",
                ref_name=cached.ref_name or "local",
                commit_sha=cached.commit_sha,
                project_path=dest,
                object_key=cached.object_key,
                object_url=cached.object_url,
                top_level=entries,
                file_count=len(entries),
            )
        return SourceAcquireResult(
            ok=False,
            error="源码解包失败: 解开后目录为空",
            origin="upload",
            git_url_normalized=cached.git_url_normalized,
            project_key=cached.project_key,
            git_host=cached.git_host,
            repo_dirname=cached.repo_dirname,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("上传源码拉取失败: %s", e)
        return SourceAcquireResult(
            ok=False,
            error=f"源码解包失败: {e}",
            origin="upload",
            git_url_normalized=cached.git_url_normalized,
            project_key=cached.project_key,
            git_host=cached.git_host,
            repo_dirname=cached.repo_dirname,
        )
