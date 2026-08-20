"""节点失败语料：分类、本轮快照、打包。上传由 TaskService 完成。"""
from __future__ import annotations

import io
import json
import logging
import re
import shutil
import tarfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

JSONL_MAX_BYTES = 5 * 1024 * 1024
ERROR_CLASSES = (
    "recipe_validation",
    "port_conflict",
    "compose_up.copy",
    "compose_up.transfer",
    "compose_up.build",
    "compose_up.runtime",
    "compose_up.policy",
    "health_check",
    "runner.killed",
    "runner.llm_error",
    "runner.no_submit",
    "runner.timeout",
    "docker.unavailable",
    "unknown",
)

_REDACT_TEXT = re.compile(
    r"(ANTHROPIC_[A-Z0-9_]+\s*[=:]\s*\S+)|(sk-[A-Za-z0-9_-]{10,})",
    re.IGNORECASE,
)


def infer_failed_stage(error_text: str) -> str | None:
    text = error_text or ""
    if "failed_stage=" in text:
        line = text.split("failed_stage=", 1)[1].split("\n", 1)[0].strip()
        if line:
            return line.split()[0]
    if "健康检查" in text:
        return "health_check"
    if "端口" in text and "占用" in text:
        return "port_conflict"
    if "compose up" in text:
        return "compose_up"
    if "compose 未" in text or "initial_creds" in text:
        return "recipe_validation"
    return None


def classify_node_error(*, failed_stage: str | None, error_text: str) -> str:
    text = error_text or ""
    stage = (failed_stage or "").strip()
    low = text.lower()

    if stage == "recipe_validation":
        return "recipe_validation"
    if stage == "port_conflict":
        return "port_conflict"
    if stage == "health_check":
        return "health_check"
    if stage == "compose_up" or stage.startswith("compose_up"):
        if "Could not transfer" in text:
            return "compose_up.transfer"
        if "failed to build" in low or "build failed" in low:
            return "compose_up.build"
        if "policy" in low:
            return "compose_up.policy"
        if "COPY" in text or "unable to find file" in low:
            return "compose_up.copy"
        return "compose_up.runtime"

    # 137（SIGKILL）必须先于 no_submit：强杀时不会有 .node_output.json，
    # 但成因是平台超时/巡检/OOM，不是模型没调 submit_result
    if "sigkill" in low or ("exit=137" in low) or ("exit code 137" in low):
        return "runner.killed"
    if "llm 调用失败" in low or "余额不足" in text or "http 401" in low:
        return "runner.llm_error"
    if "未产出" in text or ".node_output.json" in text or "submit_result" in low:
        return "runner.no_submit"
    if "timeout" in low:
        return "runner.timeout"
    if "docker daemon" in low or "cannot connect to docker" in low:
        return "docker.unavailable"
    return "unknown"


def _attempt_dir(host_workdir: str, node_key: str, attempt: int) -> Path:
    return Path(host_workdir) / ".node-failure" / node_key / "attempts" / str(attempt)


def snapshot_attempt(
    host_workdir: str,
    node_key: str,
    attempt: int,
    *,
    previous_error: str | None = None,
    platform_error: str | None = None,
    submit: dict[str, Any] | None = None,
    copy_vuln_env: bool = False,
) -> None:
    dest = _attempt_dir(host_workdir, node_key, attempt)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "previous_error.txt").write_text(previous_error or "", encoding="utf-8")
    (dest / "platform_error.txt").write_text(platform_error or "", encoding="utf-8")
    if submit is not None:
        (dest / "submit.json").write_text(
            json.dumps(submit, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    truncated = _copy_session_jsonl(Path(host_workdir), dest)
    meta = {"session_truncated": truncated}
    (dest / "attempt_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    if copy_vuln_env:
        src_env = Path(host_workdir) / ".vuln-env"
        if not src_env.is_dir():
            repo_env = None
            try:
                for child in Path(host_workdir).iterdir():
                    candidate = child / ".vuln-env"
                    if child.is_dir() and candidate.is_dir():
                        repo_env = candidate
                        break
            except OSError:
                repo_env = None
            src_env = repo_env or src_env
        if src_env.is_dir():
            target = dest / ".vuln-env"
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(src_env, target, ignore=_ignore_secrets)


def _ignore_secrets(directory: str, names: list[str]) -> list[str]:
    ignored = []
    if Path(directory).name == ".secrets" or ".secrets" in names:
        ignored.extend(n for n in names if n == ".secrets")
    return ignored


def _copy_session_jsonl(host_workdir: Path, dest: Path) -> bool:
    claude = host_workdir / ".claude"
    if not claude.is_dir():
        return False
    chunks: list[bytes] = []
    for path in sorted(claude.rglob("*.jsonl")):
        try:
            chunks.append(path.read_bytes())
        except OSError:
            continue
    if not chunks:
        return False
    data = b"\n".join(chunks)
    truncated = False
    if len(data) > JSONL_MAX_BYTES:
        data = data[-JSONL_MAX_BYTES:]
        truncated = True
    (dest / "session.jsonl").write_bytes(data)
    return truncated


def pack_node_run_bundle(
    host_workdir: str,
    node_key: str,
    manifest: dict[str, Any],
) -> bytes:
    root = Path(host_workdir) / ".node-failure" / node_key
    attempts_dir = root / "attempts"
    truncated_any = False
    if attempts_dir.is_dir():
        for meta_path in attempts_dir.glob("*/attempt_meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if meta.get("session_truncated"):
                truncated_any = True
    payload = dict(manifest)
    payload.setdefault("schema_version", 1)
    payload["session_truncated"] = truncated_any

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
        if attempts_dir.is_dir():
            for path in attempts_dir.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                if ".secrets" in path.parts:
                    continue
                data = _redact_bytes(path.read_bytes())
                info = tarfile.TarInfo(name=rel)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _redact_bytes(data: bytes) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return _REDACT_TEXT.sub("[REDACTED]", text).encode("utf-8")
