from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def read_app_version() -> str:
    """产品版本唯一入口：backend/pyproject.toml [project].version。"""
    import tomllib

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with pyproject.open("rb") as fh:
        return str(tomllib.load(fh)["project"]["version"])


class Settings(BaseSettings):
    """平台配置的类型入口，不是第二份默认值清单。

    分层（禁止同一事实抄三份）：
    - `.env`：基础设施连接与密钥（DATABASE_URL / REDIS_* / S3_* / AUTH_SECRET）。
      这些字段在本类里**没有代码默认值**。pytest 用环境变量覆盖 DATABASE_URL 为 sqlite。
    - 本类默认值：行为开关与限额（environment / debug / SDK / runner 资源）。
    - 产品版本：`backend/pyproject.toml`，经 `read_app_version()` 读取，禁止在本文件抄号。
    - 代码常量：MinIO 桶与 kind（`shared/object_store.py`）；LLM 凭据禁止进 Settings。
    - 后台 DB：LLM Provider。
    - `alembic.ini` 不保存真实库地址，由 `alembic/env.py` 从本类注入。
    """

    app_name: str = "Crucible API"
    environment: str = "development"
    debug: bool = False
    # SQLAlchemy SQL echo 独立开关（默认关）：echo 会把含明文密钥的
    # INSERT 打进日志，不能跟随 debug 联动
    database_echo: bool = False

    database_url: str
    redis_url: str
    celery_broker_url: str
    celery_result_backend: str
    redis_clue_url: str

    auth_secret: str = ""
    auth_algorithm: str = "HS256"
    # 访问令牌默认 60 分钟；SSE 另发短命 ticket，勿再把长 JWT 塞进 query
    auth_token_expire_minutes: int = 60
    # SSE ticket 有效期（秒）；前端重连前可再取票
    sse_ticket_expire_seconds: int = 120

    settings_encrypt_key: str = ""
    # Prometheus /metrics：非空则要求 Authorization: Bearer <token>；生产必须非空
    metrics_token: str = ""

    claude_agent_sdk_enabled: bool = False
    claude_sdk_max_turns: int = 480

    agent_runner_image: str = "crucible-agent-runner:base"
    agent_runner_cpu_limit: float = 1.0
    agent_runner_memory_limit: str = "1g"
    agent_runner_network: str = "crucible-sandbox-net"
    agent_runner_workdir_base: str = "/tmp/crucible/audit"
    agent_runner_concurrency_limit: int = Field(4, ge=1, le=8)

    # ── 扫描引擎(discovery-spec §6.1)：宿主 subprocess，不占 agent-runner 槽 ──
    # 空 = 当前 Python 前缀的 bin（.venv/bin）。生产可指 /opt/crucible/bin
    scanner_bin_dir: str = ""
    scanner_auto_install: bool = True  # worker 缺二进制时按锁定版本下载；测试关
    scanner_semgrep_enabled: bool = True
    scanner_gitleaks_enabled: bool = True
    scanner_osv_enabled: bool = True  # 需出网访问 api.osv.dev；离线部署置 false
    scanner_semgrep_timeout_seconds: int = 1200
    scanner_gitleaks_timeout_seconds: int = 600
    scanner_osv_timeout_seconds: int = 300
    scanner_output_max_bytes: int = 64 * 1024 * 1024  # 子进程输出上限(防超大仓库)
    # 规则包根：社区语言目录 + crucible/ 叠加。推荐 backend/semgrep_rules
    # 空 = 优先用 backend/semgrep_rules，否则 worker git clone 到 share/crucible-semgrep-rules
    scanner_semgrep_rules_dir: str = ""
    # 叠加根。空 = {scanner_semgrep_rules_dir}/crucible
    scanner_semgrep_overlay_dir: str = ""

    # ── 轻量 LLM 网关 / triage(discovery-spec §7 / §2.4) ──
    llm_gateway_enabled: bool = True  # False = mock 固定判决(链路联调)
    # Provider Base URL：生产必须 false。本地开发可 true（.env）；false=仅 HTTPS 公网域名
    llm_base_url_relaxed: bool = True
    triage_hide_sast_conclusion: bool = True  # 结论信号默认不注入(§2.2 反锚定)
    triage_high_confidence: float = 0.8  # 历史 HIGH 水位；dispatch 入队不再用硬门槛（漏报优先）
    triage_medium_confidence: float = 0.5
    # 仅用于首次创建 PlatformSetting / 旧调用方兜底；运行时以设置页 DB 值为准。
    lead_verify_per_task: int = 2

    # ── triage 分级收敛管线：逐层过滤，只有少数不确定项走到全价 agent 二审 ──
    triage_cascade_enabled: bool = True  # 总开关；False = 逐组全价 agent（旧路径）
    triage_carryover_enabled: bool = True  # T0 同项目同指纹历史判决携带
    triage_carryover_min_confidence: float = 0.7
    triage_rule_enabled: bool = True  # T1 规则历史 FP 率前置判决
    triage_rule_fp_rate_min: float = 0.95
    triage_rule_min_samples: int = 20
    triage_fast_model_enabled: bool = True  # T2 快模型首审（llm_gateway screening 角色）
    triage_fast_confidence: float = 0.75  # 达到即定案，否则升级 agent
    triage_family_enabled: bool = True  # T3 同根因族代表审议 + 判决传播
    triage_propagate_min_confidence: float = 0.6  # 代表判决低于此值时成员转人工
    triage_propagate_confidence_factor: float = 0.85  # 传播判决置信度折扣(无验证数据时的默认)
    triage_concurrency: int = 4  # agent 审议并发（受 runner 并发上限约束）
    # 瞬时 LLM 错误（5xx/断连/限流）每组退避重试次数；耗尽后该组转人工不中止节点
    triage_llm_transient_retries: int = 1
    # 连续多组瞬时降级达此数视为平台级网关故障，升级中止二审（防静默全转人工）
    triage_llm_transient_fatal_streak: int = 3
    # ── 验证结果回流：lead 终态(resolution)是真值，反哺级联先验 ──
    # 一条已验证判决的证据权重（相对 agent 亲审的 1.0）
    triage_feedback_resolved_weight: float = 3.0
    # 验证样本少于此数时不覆盖默认传播折扣（避免小样本过拟合）
    triage_feedback_min_verified: int = 10
    # ── 流式派单：triage 判完达门槛的组立即入终认队列并后台排空，
    # 不再等整个 triage 跑完（首批确认漏洞的到达时间大幅提前）──
    triage_stream_dispatch_enabled: bool = True

    # API 清单 + 猎洞（discovery-spec §6.2.1）
    api_inventory_enabled: bool = True
    api_hunt_enabled: bool = True
    api_hunt_top_k: int = 20
    api_hunt_max_batches: int = 8

    # ── 任务级 wall-clock（Celery soft/hard；子步骤另有局部超时）──
    # soft 触发 SoftTimeLimitExceeded 走失败收尾；hard 为 SIGKILL 兜底
    celery_task_soft_time_limit_seconds: int = Field(3 * 60 * 60 + 30 * 60, ge=60)  # 3.5h
    celery_task_time_limit_seconds: int = Field(4 * 60 * 60, ge=120)  # 4h

    @property
    def celery_task_time_limit(self) -> int:
        return self.celery_task_time_limit_seconds

    @property
    def celery_task_soft_time_limit(self) -> int:
        soft = self.celery_task_soft_time_limit_seconds
        hard = self.celery_task_time_limit_seconds
        return soft if soft < hard else max(60, hard - 60)

    cors_origins: str = "http://localhost:5173,http://localhost:4173,http://localhost:3000"

    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_secure: bool = False

    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1

    # 固定读取 backend/.env，避免从仓库根或服务管理器启动时相对 cwd 失效。
    model_config = SettingsConfigDict(env_file=BACKEND_ENV_FILE, extra="ignore")

    @model_validator(mode="after")
    def _enforce_production_security(self) -> "Settings":
        if self.environment != "production":
            return self
        weak_secrets = {"", "dev-secret-change-in-production", "secret", "changeme"}
        if not self.auth_secret or self.auth_secret.strip() in weak_secrets:
            raise ValueError("生产环境必须设置强随机 AUTH_SECRET")
        if "sqlite" in self.database_url:
            raise ValueError("生产环境禁止使用 SQLite，必须使用 PostgreSQL")
        if self.llm_base_url_relaxed:
            raise ValueError("生产环境必须 LLM_BASE_URL_RELAXED=false")
        if not self.claude_agent_sdk_enabled:
            raise ValueError("生产环境必须 CLAUDE_AGENT_SDK_ENABLED=true")
        if not self.llm_gateway_enabled:
            raise ValueError("生产环境必须 LLM_GATEWAY_ENABLED=true")
        if not (self.metrics_token or "").strip():
            raise ValueError("生产环境必须设置 METRICS_TOKEN（保护 /metrics）")
        origins = self.cors_origin_list
        if not origins or any(o == "*" for o in origins):
            raise ValueError("生产环境 CORS 必须为精确域名白名单（禁止 *）")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def app_version(self) -> str:
        return read_app_version()


@lru_cache
def get_settings() -> Settings:
    return Settings()
