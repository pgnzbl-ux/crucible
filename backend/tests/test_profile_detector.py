"""节点 1 项目画像规则引擎测试。"""
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


def test_detect_nodejs():
    from app.contexts.agent.profile_detector import detect_profile

    d = _make_project({
        "package.json": '{"name":"x","dependencies":{"express":"^4.0.0"}}',
        "README.md": "# x\nrun: npm start\nport 3000",
    })
    r = detect_profile(d)
    assert r["language"] == "nodejs"
    assert r["framework"] == "express"
    assert r["is_web"] is True
    assert r["has_dockerfile"] is False


def test_detect_python_fastapi():
    from app.contexts.agent.profile_detector import detect_profile

    d = _make_project({
        "requirements.txt": "fastapi\nuvicorn\n",
        "main.py": "from fastapi import FastAPI\napp = FastAPI()",
    })
    r = detect_profile(d)
    assert r["language"] == "python"
    assert r["framework"] == "fastapi"
    assert r["is_web"] is True


def test_detect_non_web_cli_tool():
    from app.contexts.agent.profile_detector import detect_profile

    d = _make_project({
        "requirements.txt": "click\nrich\n",
        "cli.py": "import click\n@click.command()",
        "README.md": "# x\nA CLI tool for data processing",
    })
    r = detect_profile(d)
    assert r["is_web"] is False


def test_detect_java_spring():
    from app.contexts.agent.profile_detector import detect_profile

    d = _make_project({
        "pom.xml": "<project><dependencies><dependency><groupId>org.springframework.boot</groupId></dependency></dependencies></project>",
    })
    r = detect_profile(d)
    assert r["language"] == "java"
    assert "spring" in (r["framework"] or "").lower()


def test_detect_existing_dockerfile():
    from app.contexts.agent.profile_detector import detect_profile

    d = _make_project({
        "package.json": '{"name":"x"}',
        "Dockerfile": "FROM node:18\nCMD [\"npm\",\"start\"]",
        "docker-compose.yml": "version: '3'\nservices:\n  web:\n    image: x",
    })
    r = detect_profile(d)
    assert r["has_dockerfile"] is True
    assert r["has_compose"] is True


def test_detect_port_from_env():
    from app.contexts.agent.profile_detector import detect_profile

    d = _make_project({
        "package.json": '{"name":"x","scripts":{"start":"node server.js"}}',
        ".env": "PORT=8080\n",
        "server.js": "const express = require('express')",
    })
    r = detect_profile(d)
    assert r["port"] == 8080


def test_profile_needs_ai_when_language_missing():
    from app.contexts.agent.profile_detector import detect_profile, profile_needs_ai

    d = _make_project({"README.md": "# notes\n"})
    hints = detect_profile(d)
    assert hints["language"] is None
    assert profile_needs_ai(d, hints) is True


def test_profile_needs_ai_when_root_is_polyglot():
    from app.contexts.agent.profile_detector import detect_profile, profile_needs_ai

    d = _make_project({
        "package.json": '{"name":"web"}',
        "pyproject.toml": "[project]\nname='api'\n",
    })
    hints = detect_profile(d)
    assert profile_needs_ai(d, hints) is True


def test_profile_skips_ai_for_clear_fastapi():
    from app.contexts.agent.profile_detector import detect_profile, profile_needs_ai

    d = _make_project({"requirements.txt": "fastapi\nuvicorn\n"})
    hints = detect_profile(d)
    assert hints["language"] == "python"
    assert hints["framework"] == "fastapi"
    assert profile_needs_ai(d, hints) is False


def test_php_index_is_strong_web_skips_ai():
    from app.contexts.agent.profile_detector import detect_profile, profile_needs_ai

    d = _make_project({"index.php": "<?php echo 1;"})
    hints = detect_profile(d)
    assert hints["language"] == "php"
    assert hints["is_web"] is True
    assert profile_needs_ai(d, hints) is False


def test_php_public_index_is_strong_web():
    from app.contexts.agent.profile_detector import detect_profile, profile_needs_ai

    d = _make_project({"public/index.php": "<?php echo 1;"})
    hints = detect_profile(d)
    assert hints["language"] == "php"
    assert hints["is_web"] is True
    assert profile_needs_ai(d, hints) is False


def test_java_web_xml_is_strong_web():
    from app.contexts.agent.profile_detector import detect_profile, profile_needs_ai

    d = _make_project({"src/main/webapp/WEB-INF/web.xml": "<web-app/>"})
    hints = detect_profile(d)
    assert hints["language"] == "java"
    assert hints["is_web"] is True
    assert profile_needs_ai(d, hints) is False


def test_spa_vite_is_strong_web_skips_ai():
    from app.contexts.agent.profile_detector import detect_profile, profile_needs_ai

    d = _make_project({"package.json": '{"dependencies":{"vite":"^5.0.0"}}'})
    hints = detect_profile(d)
    assert hints["language"] == "nodejs"
    assert hints["is_web"] is True
    assert profile_needs_ai(d, hints) is False


def test_cli_readme_is_strong_non_web_skips_ai():
    from app.contexts.agent.profile_detector import detect_profile, profile_needs_ai

    d = _make_project({
        "requirements.txt": "click\nrich\n",
        "README.md": "# x\nA CLI tool for data processing",
    })
    hints = detect_profile(d)
    assert hints["is_web"] is False
    assert profile_needs_ai(d, hints) is False


def test_uncertain_go_module_needs_ai():
    """有语言无框架、无强 Web/CLI 证据 → 必须问 AI，不能判死非 Web。"""
    from app.contexts.agent.profile_detector import detect_profile, profile_needs_ai

    d = _make_project({"go.mod": "module example.com/app\ngo 1.22\n"})
    hints = detect_profile(d)
    assert hints["language"] == "go"
    assert hints["framework"] is None
    assert hints["is_web"] is False
    assert profile_needs_ai(d, hints) is True


def test_uncertain_java_pom_needs_ai():
    from app.contexts.agent.profile_detector import detect_profile, profile_needs_ai

    d = _make_project({"pom.xml": "<project><artifactId>lib</artifactId></project>"})
    hints = detect_profile(d)
    assert hints["language"] == "java"
    assert hints["is_web"] is False
    assert profile_needs_ai(d, hints) is True


def test_polyglot_java_backend_with_ui_package_json():
    """Java 后端(根 pom.xml/build.gradle) + 前端目录(scadalts-ui/package.json)：
    主语言必须判定为 java，而不是被前端 package.json 夺主为 nodejs。
    """
    from app.contexts.agent.profile_detector import detect_profile

    d = _make_project({
        "pom.xml": "<project><dependencies><dependency><groupId>org.springframework</groupId></dependency></dependencies></project>",
        "build.gradle": "apply plugin: 'java'",
        "scadalts-ui/package.json": '{"name":"ui","dependencies":{"vue":"^2.6.0"}}',
        "scadalts-ui/src/App.vue": "<template><div/></template>",
    })
    r = detect_profile(d)
    assert r["primary_language"] == "java"
    assert r["language"] == "java"
    assert "spring" in (r["framework"] or "").lower()
    # 两种语言都在 languages 列表中（保证 Semgrep/OSV 都能扫描）
    lang_ids = {f["id"] for f in r["languages"]}
    assert "java" in lang_ids
    assert "nodejs" in lang_ids


def test_framework_alignment_locks_language():
    """当已知明确框架（如 spring-mvc）时，必须匹配对应的主要语言（java）。"""
    from app.contexts.agent.nodes.profile import rebuild_derived_fields

    raw_profile = {
        "languages": [
            {"id": "nodejs", "evidence_files": ["package.json"], "source": "rules"},
            {"id": "java", "evidence_files": ["pom.xml"], "source": "rules"},
        ],
        "framework": "spring-mvc",
    }
    rebuilt = rebuild_derived_fields(raw_profile)
    assert rebuilt["primary_language"] == "java"
    assert rebuilt["language"] == "java"


def test_derive_semgrep_configs_adopts_ai_language_when_no_rules_manifests():
    """仓库无触发文件(无 pom.xml/requirements.txt)但 AI 识别出 Python 时，Semgrep 仍能获得规则配置。"""
    from app.contexts.agent.profile_detector import derive_semgrep_configs

    ai_languages = [{"id": "python", "evidence_files": [], "source": "ai", "confidence": 1.0}]
    configs = derive_semgrep_configs(ai_languages)
    assert configs == ["python"]


