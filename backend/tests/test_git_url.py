"""Git 地址规范化：去 .git、抽出 space/project 与落地目录名。"""
import pytest

from app.contexts.project.git_url import ParsedGitUrl, classify_ref, parse_git_url, resolve_ref_type


@pytest.mark.parametrize(
    "raw,key,dirname,host,normalized",
    [
        (
            "https://github.com/siteboon/claudecodeui.git",
            "siteboon/claudecodeui",
            "claudecodeui",
            "github.com",
            "https://github.com/siteboon/claudecodeui",
        ),
        (
            "https://github.com/siteboon/claudecodeui",
            "siteboon/claudecodeui",
            "claudecodeui",
            "github.com",
            "https://github.com/siteboon/claudecodeui",
        ),
        (
            "https://github.com/siteboon/claudecodeui.git/",
            "siteboon/claudecodeui",
            "claudecodeui",
            "github.com",
            "https://github.com/siteboon/claudecodeui",
        ),
        (
            "git@github.com:siteboon/claudecodeui.git",
            "siteboon/claudecodeui",
            "claudecodeui",
            "github.com",
            "git@github.com:siteboon/claudecodeui",
        ),
        (
            "ssh://git@github.com/siteboon/claudecodeui.git",
            "siteboon/claudecodeui",
            "claudecodeui",
            "github.com",
            "ssh://git@github.com/siteboon/claudecodeui",
        ),
    ],
)
def test_parse_git_url_github(raw, key, dirname, host, normalized):
    p = parse_git_url(raw)
    assert isinstance(p, ParsedGitUrl)
    assert p.project_key == key
    assert p.repo_dirname == dirname
    assert p.host == host
    assert p.normalized == normalized
    assert p.original == raw


def test_git_url_lookup_candidates_include_with_and_without_git():
    from app.contexts.project.git_url import git_url_lookup_candidates

    c = git_url_lookup_candidates("https://github.com/a/b.git")
    assert "https://github.com/a/b.git" in c
    assert "https://github.com/a/b" in c


def test_parse_git_url_rejects_empty():
    with pytest.raises(ValueError, match="Git"):
        parse_git_url("   ")


@pytest.mark.parametrize(
    "raw",
    [
        "file:///etc/passwd",
        "file://C:/tmp/repo",
        "git://github.com/a/b",
        "javascript:alert(1)",
    ],
)
def test_parse_git_url_rejects_unsafe_schemes(raw):
    with pytest.raises(ValueError, match="协议|Git"):
        parse_git_url(raw)


def test_parse_git_url_strips_https_userinfo():
    p = parse_git_url("https://user:ghp_secret@github.com/siteboon/claudecodeui.git")
    assert p.normalized == "https://github.com/siteboon/claudecodeui"
    assert "ghp_secret" not in p.normalized
    assert "user:" not in p.normalized
    assert p.host == "github.com"
    assert p.project_key == "siteboon/claudecodeui"


def test_parse_git_url_keeps_ssh_git_user():
    p = parse_git_url("ssh://git@github.com/siteboon/claudecodeui.git")
    assert p.normalized == "ssh://git@github.com/siteboon/claudecodeui"
    assert p.host == "github.com"


@pytest.mark.parametrize(
    "ref,expect_type,expect_name",
    [
        (None, "branch", "HEAD"),
        ("", "branch", "HEAD"),
        ("main", "branch", "main"),
        ("v1.2.3", "tag", "v1.2.3"),
        ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "commit", "a" * 40),
        ("abc1234", "commit", "abc1234"),
    ],
)
def test_classify_ref(ref, expect_type, expect_name):
    got_type, got_name = classify_ref(ref)
    assert got_type == expect_type
    assert got_name == expect_name


@pytest.mark.parametrize(
    "explicit,ref,expect_type,expect_name",
    [
        (None, "main", "branch", "main"),
        ("tag", "zentaopms_22.4_20260730", "tag", "zentaopms_22.4_20260730"),
        ("branch", "zentaopms_22.4_20260730", "branch", "zentaopms_22.4_20260730"),
        ("commit", "abc1234", "commit", "abc1234"),
        ("tag", "release-candidate", "tag", "release-candidate"),
    ],
)
def test_resolve_ref_type(explicit, ref, expect_type, expect_name):
    got_type, got_name = resolve_ref_type(explicit, ref)
    assert got_type == expect_type
    assert got_name == expect_name
