"""节点 1 项目画像规则引擎测试。"""
import sys
import os
import tempfile
import pathlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_project(files: dict[str, str]) -> str:
    d = tempfile.mkdtemp()
    for rel, content in files.items():
        p = pathlib.Path(d) / "project" / rel
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
