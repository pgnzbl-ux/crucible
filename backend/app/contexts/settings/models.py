from sqlalchemy import Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import BaseModel


class LlmProvider(BaseModel):
    """LLM Provider 配置 — 后台可管理的模型接入点

    api_key 加密存储（Fernet），列表接口仅回显掩码。
    is_default 全局唯一（通过 service 保证），Agent 任务运行时取默认 Provider。
    """
    __tablename__ = "llm_providers"

    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="显示名，如 DeepSeek 官方")
    provider_type: Mapped[str] = mapped_column(
        String(30), default="custom",
        comment="deepseek | tencent | openai_compat | anthropic | custom"
    )
    base_url: Mapped[str] = mapped_column(String(512), nullable=False, comment="Anthropic 兼容端点")
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="", comment="Fernet 加密的 API Key")
    model: Mapped[str] = mapped_column(String(100), nullable=False, comment="模型名，如 deepseek-v4-flash")
    timeout_ms: Mapped[int] = mapped_column(Integer, default=600000, comment="API_TIMEOUT_MS")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否为全局默认 Provider")
    extra: Mapped[str] = mapped_column(Text, default="{}", comment="扩展配置 JSON")

    __table_args__ = (
        Index("idx_llm_providers_default", "is_default"),
        Index("idx_llm_providers_enabled", "enabled"),
    )

    def __repr__(self) -> str:
        return f"<LlmProvider {self.name} [{self.model}]>"
