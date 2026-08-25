"""
敏感凭据加密 — Fernet 对称加密。

用途：LLM API Key / 任务凭据加密后落库，列表接口仅回显掩码。
写入走 seal_secret；读取走 reveal_secret（兼容存量明文）。

Key 来源：
- 优先 settings.settings_encrypt_key（生产必须显式配置）
- 开发环境从 auth_secret 派生（SHA256 → urlsafe base64），保证稳定性且无需额外配置
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

settings = get_settings()


def _get_key() -> bytes:
    if settings.settings_encrypt_key:
        # 用户配置的是标准 Fernet key（32 bytes urlsafe base64）
        return settings.settings_encrypt_key.encode("utf-8")
    # 开发降级：从 auth_secret 派生 32 字节 key
    digest = hashlib.sha256(settings.auth_secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_get_key())
    return _fernet


def encrypt_secret(plaintext: str) -> str:
    """加密敏感值，返回字符串"""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """解密敏感值；密钥不匹配时返回空串（不把密文当明文）。"""
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""


def seal_secret(plaintext: str) -> str:
    """写入落库：明文 → Fernet 密文。空串保持空串。"""
    return encrypt_secret(plaintext)


def reveal_secret(stored: str) -> str:
    """读出：Fernet 则解密；InvalidToken 视为升级前明文原样返回。"""
    if not stored:
        return ""
    try:
        return _get_fernet().decrypt(stored.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return stored


def mask_secret(plaintext: str, visible: int = 4) -> str:
    """掩码展示：sk-abc12345 → sk-***2345"""
    if not plaintext:
        return ""
    if len(plaintext) <= visible + 3:
        return "*" * len(plaintext)
    return f"{plaintext[:3]}***{plaintext[-visible:]}"
