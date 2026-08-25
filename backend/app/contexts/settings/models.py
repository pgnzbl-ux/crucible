from sqlalchemy import Boolean, Float, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import BaseModel

# Provider 高级设置全局默认（与 schemas / 迁移 server_default / 前端初值同源）
DEFAULT_LLM_TEMPERATURE = 0.2
DEFAULT_LLM_MAX_CONTEXT_TOKENS = 200_000
DEFAULT_LLM_EFFORT = "high"


class LlmProvider(BaseModel):
    """LLM Provider 配置 — 后台可管理的模型接入点

    api_key Fernet 加密存储,列表接口仅回显掩码。存量明文由 reveal_secret 兼容读取。
    is_default 全局唯一（部分唯一索引 + service 保证），即当前启用项；Agent 任务只读默认 Provider。
    """

    __tablename__ = "llm_providers"

    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="显示名，如 DeepSeek 官方")
    provider_type: Mapped[str] = mapped_column(
        String(30),
        default="deepseek",
        comment="deepseek | anthropic | custom（均为 Anthropic Messages 兼容端点）",
    )
    auth_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="bearer",
        server_default="bearer",
        comment="认证方式 api_key(X-Api-Key) | bearer(Authorization)",
    )
    base_url: Mapped[str] = mapped_column(String(512), nullable=False, comment="Anthropic 兼容端点")
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="", comment="Fernet 密文 API Key(响应层掩码；存量明文可读)")
    model: Mapped[str] = mapped_column(String(100), nullable=False, comment="模型名，如 deepseek-v4-flash")
    timeout_ms: Mapped[int] = mapped_column(Integer, default=600000, comment="API_TIMEOUT_MS")
    temperature: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=DEFAULT_LLM_TEMPERATURE,
        server_default=str(DEFAULT_LLM_TEMPERATURE),
        comment="采样温度 0–2；Messages API 全局约束（Agent CLI 暂不透传）",
    )
    max_context_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_LLM_MAX_CONTEXT_TOKENS,
        server_default=str(DEFAULT_LLM_MAX_CONTEXT_TOKENS),
        comment="模型上下文窗口；注入 CLAUDE_CODE_MAX_CONTEXT_TOKENS 驱动 CLI 压缩",
    )
    effort: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DEFAULT_LLM_EFFORT,
        server_default=DEFAULT_LLM_EFFORT,
        comment="思考强度 low|medium|high|xhigh|max|auto",
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否为全局默认（当前启用）Provider")
    # 模型角色映射(discovery-spec §5.4)：screening(粗筛) | final(终审) | hunting(P2 占位)
    role: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="模型角色；空=不占角色")
    extra: Mapped[str] = mapped_column(Text, default="{}", comment="扩展配置 JSON")

    __table_args__ = (
        Index("idx_llm_providers_default", "is_default"),
        Index(
            "uq_llm_providers_one_default",
            "is_default",
            unique=True,
            sqlite_where=text("is_default = 1"),
            postgresql_where=text("is_default IS TRUE"),
        ),
        Index(
            "idx_llm_providers_role",
            "role",
            unique=True,
            sqlite_where=text("role IS NOT NULL"),
            postgresql_where=text("role IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<LlmProvider {self.name} [{self.model}]>"


class Credential(BaseModel):
    """任务级凭据 — Fernet 加密存储,任务运行时注入 agent-runner 容器(零落盘)。

    kind=env_var：注入为容器环境变量（target=变量名[大写下划线]，secret=值）
    kind=file：   写为容器内 /workspace/.secrets/<target>（权限 600），
                  任务结束随 host_workdir rmtree 销毁

    通过 task.credential_refs（JSON id 数组）关联到任务。
    """

    __tablename__ = "credentials"

    owner_id: Mapped[str] = mapped_column(String(36), index=True, comment="所有者 user_id")
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="显示名")
    kind: Mapped[str] = mapped_column(String(20), default="env_var", comment="env_var | file")
    target: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="env_var→环境变量名(大写下划线) / file→容器内文件名(.secrets/<target>)",
    )
    secret_encrypted: Mapped[str] = mapped_column(Text, default="", comment="Fernet 密文凭据值(响应层掩码；存量明文可读)")
    description: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (Index("idx_credentials_owner", "owner_id"),)

    def __repr__(self) -> str:
        return f"<Credential {self.name} [{self.kind}:{self.target}]>"


class PlatformSetting(BaseModel):
    """平台运行时单例配置（任务、AI 容器与终认工位资源预算）。"""

    __tablename__ = "platform_settings"

    singleton_key: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, default="default", comment="单例键"
    )
    max_concurrent_tasks: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="同时 running 的验证任务软上限"
    )
    max_concurrent_agent_runners: Mapped[int] = mapped_column(
        Integer, nullable=False, default=4, comment="全平台同时运行的 AI 容器软上限"
    )
    lead_verify_per_task: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, comment="单任务同时终认的线索数"
    )
    reproduce_per_lab: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="同一靶场同时执行的复现数"
    )
    task_token_budget: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="单任务 token 预算(prompt+completion)；0=不限。软停：耗尽后不开新 agent 会话",
    )

    def __repr__(self) -> str:
        return (
            f"<PlatformSetting {self.singleton_key} tasks={self.max_concurrent_tasks} "
            f"runners={self.max_concurrent_agent_runners}>"
        )
