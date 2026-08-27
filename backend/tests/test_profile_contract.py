"""WP0 · 画像契约升级测试(discovery-spec §6.0)。

表驱动覆盖：多语言不被 package.json 盖住、semgrep_configs 纯函数派生、
AI 不得覆盖文件证据语言、旧缓存缺派生字段可重算。
"""
import sys
import os
import tempfile
import pathlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_project(files: dict[str, str]) -> str:
    d = tempfile.mkdtemp()
    for rel, content in files.items():
        p = pathlib.Path(d) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


# ---------- detect_profile：languages / semgrep_configs 派生 ----------

def test_pure_python_derives_taint_configs():
    from app.contexts.agent.profile_detector import detect_profile

    d = _make_project({
        "requirements.txt": "fastapi\nuvicorn\n",
        "main.py": "from fastapi import FastAPI",
    })
    r = detect_profile(d)
    assert [f["id"] for f in r["languages"]] == ["python"]
    assert all(f["source"] == "rules" for f in r["languages"])
    assert r["primary_language"] == "python"
    assert r["semgrep_configs"] == ["python"]


def test_pure_java_pom():
    from app.contexts.agent.profile_detector import detect_profile

    d = _make_project({"pom.xml": "<project/>"})
    r = detect_profile(d)
    assert [f["id"] for f in r["languages"]] == ["java"]
    assert r["semgrep_configs"] == ["java"]


def test_polyglot_backend_plus_frontend_not_shadowed():
    """Python 后端 + frontend/package.json：两种语言都在，配置取并集。"""
    from app.contexts.agent.profile_detector import detect_profile, profile_needs_ai

    d = _make_project({
        "requirements.txt": "flask\n",
        "app/main.py": "from flask import Flask",
        "frontend/package.json": '{"name":"web","dependencies":{"vue":"^3"}}',
    })
    r = detect_profile(d)
    ids = {f["id"] for f in r["languages"]}
    assert ids == {"python", "nodejs"}
    assert profile_needs_ai(d, r) is True  # 多语言 → 问 AI
    assert "python" in r["semgrep_configs"]
    assert "javascript" in r["semgrep_configs"]
    assert "p/" not in ",".join(r["semgrep_configs"])
    # osv 清单同时覆盖两生态
    assert "requirements.txt" in r["osv_manifests"]
    assert "frontend/package.json" in r["osv_manifests"]
    assert set(r["package_managers"]) >= {"pip", "npm"}


def test_node_modules_not_language_evidence():
    from app.contexts.agent.profile_detector import detect_profile

    d = _make_project({
        "requirements.txt": "flask\n",
        "frontend/node_modules/foo/package.json": '{"name":"foo"}',
    })
    r = detect_profile(d)
    assert [f["id"] for f in r["languages"]] == ["python"]


def test_no_trigger_files_empty_configs():
    from app.contexts.agent.profile_detector import detect_profile

    d = _make_project({"README.md": "# notes\n", "main.py": "print(1)"})
    r = detect_profile(d)
    assert r["languages"] == []
    assert r["primary_language"] is None
    assert r["semgrep_configs"] == []


def test_rust_has_no_semgrep_config_but_go_does():
    from app.contexts.agent.profile_detector import detect_profile

    d = _make_project({"go.mod": "module x\n", "Cargo.toml": "[package]\n"})
    r = detect_profile(d)
    ids = {f["id"] for f in r["languages"]}
    assert ids == {"nodejs"} or ids == {"go", "rust"} or {"go", "rust"} <= ids
    assert "go" in r["semgrep_configs"]
    assert not any("rust" in c for c in r["semgrep_configs"])


def test_subdirectory_pom_counts_as_java_evidence():
    from app.contexts.agent.profile_detector import detect_profile

    d = _make_project({
        "requirements.txt": "flask\n",
        "server/pom.xml": "<project/>",
    })
    r = detect_profile(d)
    ids = {f["id"] for f in r["languages"]}
    assert ids == {"python", "java"}


# ---------- merge_profile：AI 不得覆盖文件证据 ----------

def _hints_for(files: dict[str, str]) -> dict:
    from app.contexts.agent.profile_detector import detect_profile

    return detect_profile(_make_project(files))


def test_ai_lying_about_language_cannot_poison_configs():
    """AI 谎称 python 但仓库只有 pom.xml：主语言仍 java，规则包不含 python。"""
    from app.contexts.agent.nodes.profile import merge_profile

    hints = _hints_for({"pom.xml": "<project/>"})
    merged = merge_profile(
        {"is_web": True, "language": "python", "framework": "spring-boot"}, hints
    )
    assert merged["primary_language"] == "java"
    assert "python" not in merged["semgrep_configs"]
    assert merged["semgrep_configs"] == ["java"]
    # AI 的说法只作为低置信追加项留档
    ai_facts = [f for f in merged["languages"] if f["source"] == "ai"]
    assert [f["id"] for f in ai_facts] == ["python"]
    assert ai_facts[0]["confidence"] <= 0.6


def test_ai_cannot_override_rules_language_or_write_derived():
    from app.contexts.agent.nodes.profile import merge_profile

    hints = _hints_for({"requirements.txt": "flask\n"})
    merged = merge_profile(
        {
            "is_web": True,
            "semgrep_configs": ["p/evil"],  # 禁写字段 → 丢弃重算
            "primary_language": "java",  # 同上
        },
        hints,
    )
    assert merged["primary_language"] == "python"
    assert merged["semgrep_configs"] == ["python"]
    # AI 没报语言 → 不追加任何 ai 事实
    assert not any(f.get("source") == "ai" for f in merged["languages"])


def test_ai_append_new_language_stays_low_confidence():
    from app.contexts.agent.nodes.profile import merge_profile

    hints = _hints_for({"requirements.txt": "flask\n"})
    merged = merge_profile({"is_web": True, "language": "php"}, hints)
    ids = [f["id"] for f in merged["languages"]]
    assert ids == ["python", "php"]
    php = next(f for f in merged["languages"] if f["id"] == "php")
    assert php["source"] == "ai" and php["confidence"] <= 0.6
    # AI 追加项不进 semgrep_configs
    assert "php" not in merged["semgrep_configs"]
    assert merged["primary_language"] == "python"


# ---------- sanitize / 旧缓存升级 ----------

def test_sanitize_upgrades_legacy_cache_profile():
    """旧缓存：单 language 无 languages/semgrep_configs → 重算派生，不整份作废。"""
    from app.contexts.agent.nodes.profile import sanitize_profile

    legacy = {
        "is_web": True,
        "language": "python",
        "framework": "flask",
        "port": 8000,
        "detected_services": [],
    }
    facts = sanitize_profile(legacy)
    assert [f["id"] for f in facts["languages"]] == ["python"]
    assert facts["language"] == facts["primary_language"] == "python"
    assert facts["semgrep_configs"] == ["python"]
    assert facts["frameworks"] == ["flask"]
    assert facts["framework"] == "flask"
    assert facts["profile_source"] == "cache"


def test_sanitize_rules_profile_keeps_source_and_rebuilds():
    from app.contexts.agent.nodes.profile import sanitize_profile
    from app.contexts.agent.profile_detector import detect_profile

    raw = detect_profile(_make_project({"requirements.txt": "flask\n"}))
    facts = sanitize_profile(raw)
    assert facts["profile_source"] == "rules"
    assert facts["semgrep_configs"] == ["python"]
    assert facts.get("non_web_reason") is None


def test_sanitize_non_web_gets_reason():
    from app.contexts.agent.nodes.profile import sanitize_profile
    from app.contexts.agent.profile_detector import detect_profile

    raw = detect_profile(_make_project({
        "requirements.txt": "click\n",
        "README.md": "# x\nA CLI tool for data processing",
    }))
    facts = sanitize_profile(raw)
    assert facts["is_web"] is False
    assert facts["non_web_reason"]


def test_fact_keys_are_canonical_only():
    """language/framework 等别名不进填补/落库名单，避免和 languages/frameworks 双写。"""
    from app.contexts.agent.nodes.profile import PROFILE_FACT_KEYS, _HINT_FILL_KEYS

    aliases = {"language", "framework", "primary_language", "semgrep_configs"}
    assert aliases.isdisjoint(PROFILE_FACT_KEYS)
    assert aliases.isdisjoint(_HINT_FILL_KEYS)


# ---------- Handoff 投影兼容 ----------

def test_profile_handoff_accepts_legacy_and_new_shapes():
    from app.contexts.agent.contracts.outputs import ProfileHandoff, project_handoff

    legacy = project_handoff("profile", {"is_web": True, "language": "python"})
    assert legacy.language == "python"
    assert legacy.languages == []

    new = ProfileHandoff.model_validate({
        "is_web": True,
        "languages": [{"id": "python", "evidence_files": ["requirements.txt"]}],
        "semgrep_configs": ["python"],
    })
    assert new.languages[0].id == "python"
    assert new.languages[0].source == "rules"
    assert new.semgrep_configs == ["python"]
