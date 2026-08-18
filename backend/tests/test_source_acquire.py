"""源码获取：MinIO 优先，落地目录用真实仓库名，clone 失败要分类。"""
from pathlib import Path

from app.contexts.project.git_url import parse_git_url
from app.contexts.project.source_cache import MemorySourceStore, pack_project_dir
from app.contexts.project.source_acquire import CachedSource, acquire_source


URL = "https://github.com/siteboon/claudecodeui.git"
SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _write_repo(root: Path, dirname: str, filename: str, content: str) -> Path:
    repo = root / dirname
    repo.mkdir(parents=True, exist_ok=True)
    (repo / filename).write_text(content, encoding="utf-8")
    return repo


def test_minio_hit_extracts_to_repo_dirname_not_project(tmp_path):
    parsed = parse_git_url(URL)
    cached_root = tmp_path / "cached"
    repo = _write_repo(cached_root, parsed.repo_dirname, "app.py", "from-cache")
    archive = tmp_path / "src.tar.gz"
    pack_project_dir(str(repo), str(archive), arcname=parsed.repo_dirname)

    store = MemorySourceStore()
    object_key = f"source/{parsed.project_key}/{SHA}.tar.gz"
    store.upload(object_key, SHA, str(archive))

    workdir = tmp_path / "audit-uuid"
    workdir.mkdir()
    cached = CachedSource(
        object_key=object_key,
        object_url=f"s3://crucible-durable/{object_key}",
        repo_dirname=parsed.repo_dirname,
        commit_sha=SHA,
        ref_type="branch",
        ref_name="main",
        git_url_normalized=parsed.normalized,
        project_key=parsed.project_key,
        git_host=parsed.host,
    )

    def boom_clone(*_a, **_k):
        raise RuntimeError("不应 clone")

    result = acquire_source(
        host_workdir=str(workdir),
        git_url=URL,
        ref="main",
        cached=cached,
        store=store,
        clone_fn=boom_clone,
        remote_sha_fn=lambda _url, _ref: SHA,
    )
    assert result.ok is True
    assert result.origin == "minio"
    assert result.repo_dirname == "claudecodeui"
    assert (workdir / "claudecodeui" / "app.py").read_text(encoding="utf-8") == "from-cache"
    assert not (workdir / "project").exists()


def test_cache_miss_clones_into_repo_dirname_and_uploads(tmp_path):
    parsed = parse_git_url(URL)
    workdir = tmp_path / "audit-uuid"
    workdir.mkdir()
    store = MemorySourceStore()

    def fake_clone(workdir_s: str, _url: str, _ref: str | None, dest_dirname: str) -> tuple[bool, str]:
        _write_repo(Path(workdir_s), dest_dirname, "app.py", "from-git")
        git = Path(workdir_s) / dest_dirname / ".git"
        git.mkdir()
        return True, ""

    result = acquire_source(
        host_workdir=str(workdir),
        git_url=URL,
        ref="main",
        cached=None,
        store=store,
        clone_fn=fake_clone,
        local_sha_fn=lambda _p: SHA,
        owner_id="u1",
    )
    assert result.ok is True
    assert result.origin == "git"
    assert result.repo_dirname == "claudecodeui"
    assert result.project_key == "siteboon/claudecodeui"
    assert result.commit_sha == SHA
    assert (workdir / "claudecodeui" / "app.py").exists()
    assert not (workdir / "project").exists()
    assert store.get_bytes(result.object_key) is not None


def test_clone_network_error_does_not_succeed(tmp_path):
    workdir = tmp_path / "audit-uuid"
    workdir.mkdir()

    def fail_clone(*_a, **_k) -> tuple[bool, str]:
        return False, "源码克隆失败: 网络错误（无法解析主机）: Could not resolve host: github.com"

    result = acquire_source(
        host_workdir=str(workdir),
        git_url=URL,
        ref="main",
        cached=None,
        store=MemorySourceStore(),
        clone_fn=fail_clone,
    )
    assert result.ok is False
    assert "网络错误" in result.error
    assert not (workdir / "claudecodeui").exists()
    assert not (workdir / "project").exists()


def test_invalid_git_url_does_not_succeed(tmp_path):
    workdir = tmp_path / "audit-uuid"
    workdir.mkdir()

    def boom_clone(*_a, **_k):
        raise RuntimeError("不应 clone")

    result = acquire_source(
        host_workdir=str(workdir),
        git_url="not-a-url",
        ref="main",
        cached=None,
        store=MemorySourceStore(),
        clone_fn=boom_clone,
    )
    assert result.ok is False
    assert "源码克隆失败" in result.error


SHA_NEW = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _cached_and_store(tmp_path, sha: str, ref_name: str = "main", ref_type: str = "branch"):
    parsed = parse_git_url(URL)
    cached_root = tmp_path / "cached"
    repo = _write_repo(cached_root, parsed.repo_dirname, "app.py", f"from-cache-{sha[:8]}")
    archive = tmp_path / f"{sha[:8]}.tar.gz"
    pack_project_dir(str(repo), str(archive), arcname=parsed.repo_dirname)
    store = MemorySourceStore()
    object_key = f"source/{parsed.host}/{parsed.project_key}/{sha}.tar.gz"
    store.upload(object_key, sha, str(archive))
    cached = CachedSource(
        object_key=object_key,
        object_url=f"s3://crucible-durable/{object_key}",
        repo_dirname=parsed.repo_dirname,
        commit_sha=sha,
        ref_type=ref_type,
        ref_name=ref_name,
        git_url_normalized=parsed.normalized,
        project_key=parsed.project_key,
        git_host=parsed.host,
    )
    return cached, store, parsed


def test_branch_cache_reused_when_remote_sha_matches(tmp_path):
    cached, store, _ = _cached_and_store(tmp_path, SHA)
    workdir = tmp_path / "audit-uuid"
    workdir.mkdir()

    def boom_clone(*_a, **_k):
        raise RuntimeError("不应 clone")

    result = acquire_source(
        host_workdir=str(workdir),
        git_url=URL,
        ref="main",
        cached=cached,
        store=store,
        clone_fn=boom_clone,
        remote_sha_fn=lambda _url, _ref: SHA,
    )
    assert result.ok is True
    assert result.origin == "minio"
    assert result.commit_sha == SHA


def test_branch_cache_reclones_when_remote_sha_changed(tmp_path):
    cached, store, parsed = _cached_and_store(tmp_path, SHA)
    workdir = tmp_path / "audit-uuid"
    workdir.mkdir()
    cloned = {"n": 0}

    def fake_clone(workdir_s: str, _url: str, _ref: str | None, dest_dirname: str) -> tuple[bool, str]:
        cloned["n"] += 1
        _write_repo(Path(workdir_s), dest_dirname, "app.py", "from-git-new")
        return True, ""

    result = acquire_source(
        host_workdir=str(workdir),
        git_url=URL,
        ref="main",
        cached=cached,
        store=store,
        clone_fn=fake_clone,
        local_sha_fn=lambda _p: SHA_NEW,
        remote_sha_fn=lambda _url, _ref: SHA_NEW,
    )
    assert cloned["n"] == 1
    assert result.ok is True
    assert result.origin == "git"
    assert result.commit_sha == SHA_NEW
    assert (workdir / parsed.repo_dirname / "app.py").read_text(encoding="utf-8") == "from-git-new"


def test_tag_cache_skips_remote_sha_lookup(tmp_path):
    cached, store, _ = _cached_and_store(tmp_path, SHA, ref_name="v1.2.3", ref_type="tag")
    workdir = tmp_path / "audit-uuid"
    workdir.mkdir()

    def boom_clone(*_a, **_k):
        raise RuntimeError("不应 clone")

    def boom_remote(*_a, **_k):
        raise RuntimeError("tag 不应 ls-remote")

    result = acquire_source(
        host_workdir=str(workdir),
        git_url=URL,
        ref="v1.2.3",
        cached=cached,
        store=store,
        clone_fn=boom_clone,
        remote_sha_fn=boom_remote,
    )
    assert result.ok is True
    assert result.origin == "minio"


def test_branch_cache_reclones_when_remote_sha_unknown(tmp_path):
    """main/master 名字不变；对不上远端 SHA 就不能用 MinIO。"""
    cached, store, parsed = _cached_and_store(tmp_path, SHA)
    workdir = tmp_path / "audit-uuid"
    workdir.mkdir()
    cloned = {"n": 0}

    def fake_clone(workdir_s: str, _url: str, _ref: str | None, dest_dirname: str) -> tuple[bool, str]:
        cloned["n"] += 1
        _write_repo(Path(workdir_s), dest_dirname, "app.py", "from-git-fresh")
        return True, ""

    result = acquire_source(
        host_workdir=str(workdir),
        git_url=URL,
        ref="main",
        cached=cached,
        store=store,
        clone_fn=fake_clone,
        local_sha_fn=lambda _p: SHA_NEW,
        remote_sha_fn=lambda _url, _ref: None,
    )
    assert cloned["n"] == 1
    assert result.ok is True
    assert result.origin == "git"
    assert (workdir / parsed.repo_dirname / "app.py").read_text(encoding="utf-8") == "from-git-fresh"
