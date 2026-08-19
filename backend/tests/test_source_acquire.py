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
ZENTAO_MAIN = "abbef96f08ce934ec461e6139fbef58a18182586"
ZENTAO_REMOTE_MAIN = "7c85dfa163a99493a01e13376bc058be2c30c6b6"


def test_parse_ls_remote_skips_remotes_and_non_hex():
    """branch 探活必须拿 refs/heads 的 commit，不能拿 refs/remotes 或进度行。"""
    from app.contexts.project.source_acquire import _parse_ls_remote_stdout

    stdout = "\n".join(
        [
            "warning: redirecting to github",
            f"{ZENTAO_MAIN}\trefs/heads/main",
            f"{ZENTAO_REMOTE_MAIN}\trefs/remotes/origin/main",
        ]
    )
    assert _parse_ls_remote_stdout(stdout, "main") == ZENTAO_MAIN
    assert _parse_ls_remote_stdout(stdout, "refs/heads/main") == ZENTAO_MAIN


def test_parse_ls_remote_prefers_peeled_tag_commit():
    from app.contexts.project.source_acquire import _parse_ls_remote_stdout

    tag_obj = "d0f2e143264a2635587eef489800b26bba8c7621"
    commit = "aabbccddeeff00112233445566778899aabbccdd"
    stdout = f"{tag_obj}\trefs/tags/v1.2.3\n{commit}\trefs/tags/v1.2.3^{{}}\n"
    assert _parse_ls_remote_stdout(stdout, "v1.2.3") == commit


def test_local_head_sha_ignores_git_dir_of_another_repo(tmp_path, monkeypatch):
    """Celery 若从 Crucible 仓库目录拉起，GIT_DIR 会让 rev-parse 读到平台自己的 SHA。"""
    import subprocess

    from app.contexts.project.source_acquire import _local_head_sha

    def _init_commit(path, content: str) -> str:
        path.mkdir()
        subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.t"], cwd=path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "t"], cwd=path, check=True, capture_output=True
        )
        (path / "f.txt").write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "t"], cwd=path, check=True, capture_output=True)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
        ).stdout.strip()
        return sha[:40]

    zentao = tmp_path / "zentaopms"
    crucible = tmp_path / "Crucible"
    want = _init_commit(zentao, "zentao")
    other = _init_commit(crucible, "platform")
    monkeypatch.setenv("GIT_DIR", str(crucible / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(crucible))
    got = _local_head_sha(str(zentao))
    assert got == want
    assert got != other


ZENTAO_TAG = "zentaopms_22.4_20260730"
ZENTAO_TAG_SHA = "c93f7feec2ce2899473af41a5d020a3677f38a9c"


def test_classify_zentao_release_tag():
    from app.contexts.project.git_url import classify_ref

    assert classify_ref(ZENTAO_TAG) == ("tag", ZENTAO_TAG)


def test_branch_ls_remote_does_not_pick_tag_sha():
    from app.contexts.project.source_acquire import _ls_remote_branch_sha

    sha = _ls_remote_branch_sha("https://github.com/easysoft/zentaopms.git", ZENTAO_TAG)
    assert sha is None


def test_resolve_remote_ref_finds_zentao_tag():
    from app.contexts.project.source_acquire import resolve_remote_ref

    ref_type, ref_name, sha = resolve_remote_ref(
        "https://github.com/easysoft/zentaopms.git", ZENTAO_TAG
    )
    assert ref_type == "tag"
    assert ref_name == ZENTAO_TAG
    assert sha.startswith(ZENTAO_TAG_SHA[:12])


def test_tag_ref_uses_minio_when_commit_matches(tmp_path):
    cached, store, parsed = _cached_and_store(
        tmp_path, ZENTAO_TAG_SHA, ref_name=ZENTAO_TAG, ref_type="tag"
    )
    workdir = tmp_path / "audit-uuid"
    workdir.mkdir()

    def boom_clone(*_a, **_k):
        raise RuntimeError("不应 clone")

    result = acquire_source(
        host_workdir=str(workdir),
        git_url="https://github.com/easysoft/zentaopms.git",
        ref=ZENTAO_TAG,
        cached=cached,
        store=store,
        clone_fn=boom_clone,
    )
    assert result.ok is True
    assert result.origin == "minio"
    assert result.commit_sha.startswith(ZENTAO_TAG_SHA[:12])


def test_branch_cache_reuses_minio_object_for_remote_commit(tmp_path):
    """MinIO 已按 commit 存包：ls-remote 对上该 SHA 就复用，不因 branch 行陈旧而重 clone。"""
    cached_old, store, parsed = _cached_and_store(tmp_path, SHA)
    fresh_root = tmp_path / "fresh"
    repo = _write_repo(fresh_root, parsed.repo_dirname, "app.py", "from-sha-cache")
    archive = tmp_path / "fresh.tar.gz"
    pack_project_dir(str(repo), str(archive), arcname=parsed.repo_dirname)
    fresh_key = f"source/{parsed.host}/{parsed.project_key}/{SHA_NEW}.tar.gz"
    store.upload(fresh_key, SHA_NEW, str(archive))
    fresh = CachedSource(
        object_key=fresh_key,
        object_url=f"s3://crucible-durable/{fresh_key}",
        repo_dirname=parsed.repo_dirname,
        commit_sha=SHA_NEW,
        ref_type="branch",
        ref_name="main",
        git_url_normalized=parsed.normalized,
        project_key=parsed.project_key,
        git_host=parsed.host,
    )
    workdir = tmp_path / "audit-uuid"
    workdir.mkdir()

    def boom_clone(*_a, **_k):
        raise RuntimeError("不应 clone")

    result = acquire_source(
        host_workdir=str(workdir),
        git_url=URL,
        ref="main",
        cached=cached_old,
        store=store,
        clone_fn=boom_clone,
        remote_sha_fn=lambda _url, _ref: SHA_NEW,
        cached_by_sha_fn=lambda sha: fresh if sha.lower().startswith(SHA_NEW.lower()) else None,
    )
    assert result.ok is True
    assert result.origin == "minio"
    assert result.commit_sha == SHA_NEW
    assert (workdir / parsed.repo_dirname / "app.py").read_text(encoding="utf-8") == "from-sha-cache"



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
