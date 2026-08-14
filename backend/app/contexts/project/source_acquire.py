"""准备任务工作区源码：表记录命中则拉 MinIO，否则 git clone 到 {workdir}/{repo_dirname}。"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable

from app.contexts.project.git_url import classify_ref, parse_git_url
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


def _project_has_files(project_dir: str) -> bool:
    if not os.path.isdir(project_dir):
        return False
    return any(e != ".git" for e in os.listdir(project_dir))


def _list_top_level(project_dir: str) -> list[str]:
    if not os.path.isdir(project_dir):
        return []
    return sorted(e for e in os.listdir(project_dir) if e != ".git")[:50]


def _local_head_sha(project_dir: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", project_dir, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha[:40] if len(sha) >= 40 else None


def _rmtree(path: str) -> None:
    if not os.path.isdir(path):
        return

    def _force_remove(func, p, _exc):  # noqa: ANN001
        try:
            os.chmod(p, 0o777)
            func(p)
        except Exception:
            pass

    shutil.rmtree(path, onerror=_force_remove)


def _restore_cached(cached: CachedSource, store, host_workdir: str) -> bool:
    dest = os.path.join(host_workdir, cached.repo_dirname)
    _rmtree(dest)
    fd, archive = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    try:
        store.download(cached.object_key, archive)
        extract_source_archive(archive, host_workdir)
        return _project_has_files(dest)
    finally:
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


def _ls_remote_sha(git_url: str, ref: str | None) -> str | None:
    _ref_type, ref_name = classify_ref(ref)
    specs = ["HEAD"] if ref_name == "HEAD" else [f"refs/heads/{ref_name}", ref_name]
    for spec in specs:
        try:
            result = subprocess.run(
                ["git", "ls-remote", git_url, spec],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            continue
        lines = [ln for ln in (result.stdout or "").splitlines() if ln.strip()]
        if not lines:
            continue
        sha = lines[0].split()[0]
        if len(sha) >= 7:
            return sha
    return None


def _branch_cache_usable(
    cached: CachedSource,
    ref_type: str,
    git_url: str,
    ref: str | None,
    remote_sha_fn: Callable[[str, str | None], str | None],
) -> bool:
    """tag/commit 可永久缓存；branch 必须对上远端 SHA，对不上就 clone。"""
    if ref_type in ("tag", "commit"):
        return True
    remote = remote_sha_fn(git_url, ref)
    if not remote:
        logger.warning("无法确认远端 SHA，忽略源码缓存并重新 clone")
        return False
    if _sha_matches(cached.commit_sha, remote):
        return True
    logger.info("远端 SHA 已变，忽略源码缓存")
    return False


def _upload_cache(
    store, project_key: str, sha: str, repo_dir: str, repo_dirname: str, git_host: str
) -> str:
    fd, archive = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    try:
        pack_project_dir(repo_dir, archive, arcname=repo_dirname)
        key = source_object_key(git_host, project_key, sha)
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
    cached: CachedSource | None = None,
    store=None,
    clone_fn: CloneFn | None = None,
    local_sha_fn: ShaFn | None = None,
    remote_sha_fn: RemoteShaFn | None = None,
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
    ref_type, ref_name = classify_ref(ref)
    store = store or MinioSourceStore()
    clone_fn = clone_fn or git_clone_to_workdir
    sha_fn = local_sha_fn or _local_head_sha
    remote_fn = remote_sha_fn or _ls_remote_sha
    dest = os.path.join(host_workdir, parsed.repo_dirname)
    stored_url = parsed.normalized

    use_cache = False
    if cached is not None:
        use_cache = _branch_cache_usable(cached, ref_type, git_url, ref, remote_fn)
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
    if sha:
        try:
            object_key = _upload_cache(
                store, parsed.project_key, sha, dest, parsed.repo_dirname, parsed.host
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
