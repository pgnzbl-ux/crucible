"""本地源码包：校验、安全解压、规范成 tar.gz，标识为 upload://local/{slug}。"""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.contexts.project.git_url import ParsedGitUrl, parse_git_url
from app.contexts.project.source_cache import pack_project_dir

UPLOAD_HOST = "upload"
UPLOAD_REF_TYPE = "upload"
UPLOAD_REF_NAME = "local"
UPLOAD_SCHEME = "upload://"

MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_FILE_COUNT = 50_000

_SKIP_NAMES = frozenset({"__macosx", ".ds_store", "thumbs.db"})
_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_SLUG_TOKEN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$")


@dataclass
class IngestedArchive:
    archive_path: str
    sha256: str
    slug: str
    repo_dirname: str
    display_name: str
    size_bytes: int
    file_count: int
    top_level: list[str]
    locator: str


def is_upload_locator(raw: str | None) -> bool:
    return (raw or "").strip().lower().startswith(UPLOAD_SCHEME)


def parse_upload_locator(raw: str) -> ParsedGitUrl:
    original = (raw or "").strip()
    if not original:
        raise ValueError("上传源码标识为空")
    if not original.lower().startswith(UPLOAD_SCHEME):
        raise ValueError(f"不是上传源码标识: {original}")
    rest = original[len(UPLOAD_SCHEME) :].lstrip("/")
    parts = [p for p in rest.split("/") if p]
    if len(parts) != 2 or parts[0] != "local":
        raise ValueError(f"上传源码标识格式应为 upload://local/{{slug}}: {original}")
    slug = parts[1]
    if not _SLUG_TOKEN_RE.fullmatch(slug):
        raise ValueError(f"非法上传源码标识: {original}")
    return ParsedGitUrl(
        original=original,
        normalized=f"upload://local/{slug}",
        host=UPLOAD_HOST,
        project_key=f"local/{slug}",
        repo_dirname=slug,
    )


def parse_source_locator(raw: str, source_type: str | None = None) -> ParsedGitUrl:
    text = (raw or "").strip()
    if source_type == "local_upload" or is_upload_locator(text):
        return parse_upload_locator(text)
    return parse_git_url(text)


def upload_locator(slug: str) -> str:
    if not _SLUG_TOKEN_RE.fullmatch(slug):
        raise ValueError(f"非法上传源码 slug: {slug}")
    return f"upload://local/{slug}"


def sanitize_slug(filename: str, fallback: str = "project") -> str:
    name = (filename or "").strip() or fallback
    lower = name.lower()
    stem = name
    for ext in (".tar.gz", ".tgz", ".tar", ".zip"):
        if lower.endswith(ext):
            stem = name[: -len(ext)]
            break
    else:
        stem = Path(name).stem
    slug = _SLUG_RE.sub("-", stem).strip("-._").lower()
    slug = re.sub(r"-{2,}", "-", slug)[:40]
    if not slug or slug[0] in ".-":
        slug = fallback
    if not _SLUG_TOKEN_RE.fullmatch(slug):
        slug = fallback
    return slug


def detect_archive_kind(data: bytes, filename: str) -> str:
    name = (filename or "").lower()
    if data.startswith(b"PK"):
        return "zip"
    if data.startswith(b"\x1f\x8b"):
        return "tar.gz"
    if len(data) > 262 and data[257:262] == b"ustar":
        return "tar"
    if name.endswith(".zip"):
        return "zip"
    if name.endswith((".tar.gz", ".tgz")):
        return "tar.gz"
    if name.endswith(".tar"):
        return "tar"
    raise ValueError("仅支持 zip / tar / tar.gz 源码包")


def _should_skip(path: str) -> bool:
    parts = Path(str(path).replace("\\", "/")).parts
    return any(p.lower() in _SKIP_NAMES or p.startswith("._") for p in parts if p not in (".",))


def _safe_dest(root: Path, member_name: str) -> Path:
    rel = str(member_name).replace("\\", "/").lstrip("/")
    if not rel or rel.endswith("/"):
        rel = rel.rstrip("/")
    if not rel:
        raise ValueError("压缩包含空路径")
    dest = (root / rel).resolve()
    root_resolved = root.resolve()
    try:
        dest.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"非法压缩路径: {member_name}") from exc
    return dest


def _iter_zip_names(zf: zipfile.ZipFile) -> list[str]:
    names: list[str] = []
    for info in zf.infolist():
        name = (info.filename or "").replace("\\", "/")
        if not name or name.endswith("/"):
            continue
        if _should_skip(name):
            continue
        names.append(name)
    return names


def _extract_zip(data: bytes, dest: Path) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("zip 源码包损坏") from exc
    with zf:
        names = _iter_zip_names(zf)
        if not names:
            raise ValueError("源码包为空")
        if len(names) > MAX_FILE_COUNT:
            raise ValueError(f"源码包文件数超过 {MAX_FILE_COUNT} 限制")
        uncompressed = 0
        for info in zf.infolist():
            name = (info.filename or "").replace("\\", "/")
            if not name or name.endswith("/") or _should_skip(name):
                continue
            size = int(info.file_size or 0)
            uncompressed += size
            if uncompressed > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("源码包解压后超过 1GB 限制")
            target = _safe_dest(dest, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as out:
                copied = 0
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > size + 1024:
                        raise ValueError("zip 条目大小与声明不符")
                    out.write(chunk)


def _extract_tar(data: bytes, dest: Path, mode: str) -> None:
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode=mode)
    except tarfile.TarError as exc:
        raise ValueError("tar 源码包损坏") from exc
    with tar:
        members = [
            m
            for m in tar.getmembers()
            if m.isreg() and m.name and not _should_skip(m.name)
        ]
        if not members:
            raise ValueError("源码包为空")
        if len(members) > MAX_FILE_COUNT:
            raise ValueError(f"源码包文件数超过 {MAX_FILE_COUNT} 限制")
        uncompressed = 0
        for member in members:
            size = int(member.size or 0)
            uncompressed += size
            if uncompressed > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("源码包解压后超过 1GB 限制")
            target = _safe_dest(dest, member.name)
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with extracted, target.open("wb") as out:
                shutil.copyfileobj(extracted, out)


def _project_root(extracted: Path) -> Path:
    entries = [p for p in extracted.iterdir() if not _should_skip(p.name)]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return extracted


def _list_top_level(project_dir: Path) -> list[str]:
    if not project_dir.is_dir():
        return []
    return sorted(p.name for p in project_dir.iterdir() if p.name != ".git")[:50]


def ingest_source_archive(
    data: bytes,
    filename: str,
    *,
    display_name: str | None = None,
    workdir: str | None = None,
) -> IngestedArchive:
    """把用户上传的 zip/tar 规范成与 git 缓存相同的 tar.gz。

    workdir 为空时自建临时目录（调用方负责清理其父目录或传入已有目录）。
    """
    if not data:
        raise ValueError("源码包为空")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("源码包超过 200MB 限制")

    kind = detect_archive_kind(data, filename)
    root = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="crucible-upload-"))
    extracted = root / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)

    if kind == "zip":
        _extract_zip(data, extracted)
    elif kind == "tar.gz":
        _extract_tar(data, extracted, "r:gz")
    else:
        _extract_tar(data, extracted, "r:")

    project_root = _project_root(extracted)
    if not any(project_root.iterdir()):
        raise ValueError("源码包为空")

    inner_name = project_root.name if project_root != extracted else sanitize_slug(filename)
    repo_dirname = sanitize_slug(inner_name, fallback="project")
    base_slug = sanitize_slug(display_name or filename, fallback=repo_dirname)

    packed_dir = root / "packed" / repo_dirname
    packed_dir.parent.mkdir(parents=True, exist_ok=True)
    if packed_dir.exists():
        shutil.rmtree(packed_dir)
    shutil.copytree(project_root, packed_dir, ignore=shutil.ignore_patterns("__MACOSX", ".DS_Store"))

    archive_path = str(root / "source.tar.gz")
    pack_project_dir(str(packed_dir), archive_path, arcname=repo_dirname)
    sha256 = hashlib.sha256(Path(archive_path).read_bytes()).hexdigest()
    slug = f"{base_slug}-{sha256[:12]}"
    if not _SLUG_TOKEN_RE.fullmatch(slug):
        slug = f"project-{sha256[:12]}"

    top_level = _list_top_level(packed_dir)
    file_count = sum(1 for _root, _dirs, files in os.walk(packed_dir) for _ in files)
    name = (display_name or "").strip() or inner_name or base_slug
    return IngestedArchive(
        archive_path=archive_path,
        sha256=sha256,
        slug=slug,
        repo_dirname=repo_dirname,
        display_name=name[:255],
        size_bytes=os.path.getsize(archive_path),
        file_count=file_count,
        top_level=top_level,
        locator=upload_locator(slug),
    )
