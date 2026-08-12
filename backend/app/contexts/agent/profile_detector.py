"""节点 1 项目画像规则引擎。

把 plugin run-project-env/references/project-detection.md(7 语言规则表)
+ web-detection.md(web 门禁关键字表)翻成 Python 确定性检测。
画像后回填 Project 表,后续任务复用省 AI。
"""
from __future__ import annotations

import re
from pathlib import Path

# 语言检测规则(触发文件 → 语言)(对齐 project-detection.md)
LANGUAGE_RULES: list[tuple[str, list[str]]] = [
    ("nodejs", ["package.json"]),
    ("python", ["requirements.txt", "pyproject.toml"]),
    ("java", ["pom.xml", "build.gradle"]),
    ("go", ["go.mod"]),
    ("php", ["composer.json", "index.php"]),
    ("rust", ["Cargo.toml"]),
    ("static", ["index.html"]),  # 仅静态 HTML(最低优先级)
]

# 框架关键字(语言 → {关键字 → 框架})
FRAMEWORK_KEYWORDS: dict[str, dict[str, str]] = {
    "nodejs": {
        "next": "next", "nuxt": "nuxt", "express": "express", "@nestjs/core": "nestjs",
        "koa": "koa", "fastify": "fastify",
    },
    "python": {
        "fastapi": "fastapi", "flask": "flask", "django": "django", "streamlit": "streamlit",
        "tornado": "tornado",
    },
    "java": {"spring-boot-starter": "spring-boot", "springframework": "spring-boot", "quarkus": "quarkus"},
    "go": {"gin": "gin", "echo": "echo"},
    "php": {"laravel": "laravel", "symfony": "symfony"},
    "rust": {"actix-web": "actix-web", "axum": "axum", "rocket": "rocket"},
}

# web 框架(web 门禁 is_web=True 的信号)
WEB_FRAMEWORKS = {
    "express", "nestjs", "koa", "fastify", "next", "nuxt",
    "fastapi", "flask", "django", "streamlit", "tornado",
    "spring-boot", "quarkus",
    "gin", "echo",
    "laravel", "symfony",
    "actix-web", "axum", "rocket",
}

# web 门禁额外信号(web-detection.md)
WEB_SIGNALS = [
    r"server\.port", r"PORT\s*=", r"listen\s*\(\s*\d", r"app\.listen",
    r"swagger", r"openapi", r"controller",
]
NON_WEB_SIGNALS = [r"CLI\s+tool", r"command.line", r"desktop\s+app", r"batch\s+process"]

DEPENDENCY_FILES = {
    "nodejs": ["package.json"],
    "python": ["requirements.txt", "pyproject.toml"],
    "java": ["pom.xml", "build.gradle"],
    "go": ["go.mod"],
    "php": ["composer.json"],
    "rust": ["Cargo.toml"],
}


def _read_file(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return ""


def _detect_language(root: Path) -> str | None:
    for lang, files in LANGUAGE_RULES:
        if any((root / f).exists() for f in files):
            return lang
    return None


def _detect_framework(root: Path, language: str | None) -> str | None:
    if not language:
        return None
    kws = FRAMEWORK_KEYWORDS.get(language, {})
    if not kws:
        return None
    dep_files = DEPENDENCY_FILES.get(language, [])
    blob = "\n".join(_read_file(root / f) for f in dep_files if (root / f).exists())
    for kw, fw in kws.items():
        if kw in blob:
            return fw
    return None


def _detect_port(root: Path) -> int | None:
    candidates = [
        root / ".env", root / "application.yml", root / "application.yaml",
        root / "config" / "default.json", root / ".env.example",
    ]
    for p in candidates:
        if not p.exists():
            continue
        text = _read_file(p)
        m = re.search(r"(?:^|\n)\s*(?:port|PORT)\s*[=:]\s*(\d{2,5})", text)
        if m:
            return int(m.group(1))
    return None


def _is_web(root: Path, language: str | None, framework: str | None) -> bool:
    if framework and framework in WEB_FRAMEWORKS:
        return True
    readme = ""
    for rname in ["README.md", "readme.md", "README.rst", "README"]:
        if (root / rname).exists():
            readme = _read_file(root / rname)
            break
    for signal in WEB_SIGNALS:
        if re.search(signal, readme, re.IGNORECASE):
            return True
    for signal in NON_WEB_SIGNALS:
        if re.search(signal, readme, re.IGNORECASE):
            return False
    if language == "static":
        return True
    return False


def detect_profile(source_path: str) -> dict:
    """对 clone 后的源码做画像,返回节点 1 output schema。

    source_path 指向 host_workdir,源码在其 `project/` 子目录(由 git_clone_to_workdir 产出)。
    """
    root = Path(source_path) / "project" if (Path(source_path) / "project").exists() else Path(source_path)
    language = _detect_language(root)
    framework = _detect_framework(root, language)
    port = _detect_port(root)
    is_web = _is_web(root, language, framework)
    has_dockerfile = (root / "Dockerfile").exists() or any(root.glob("Dockerfile*"))
    has_compose = any(root.glob("docker-compose*.yml")) or any(root.glob("docker-compose*.yaml"))

    return {
        "is_web": is_web,
        "language": language,
        "framework": framework,
        "port": port,
        "has_dockerfile": has_dockerfile,
        "has_compose": has_compose,
        "detected_services": [],
    }
