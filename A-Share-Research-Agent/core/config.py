from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """项目级配置。

    加载顺序（优先级从高到低）：
    1. 显式传入 / 进程内 setattr；
    2. 系统环境变量；
    3. .env 文件（容器内、本机开发统一入口）。

    容器内由 docker compose 的 env_file 注入，宿主机开发时直接读 .env，
    因此两端的解析逻辑都必须走 Pydantic Settings 的 env_file 配置。
    """

    # 注意：model_config 只能定义一次；为避免重复覆盖，所有配置集中在底部。
    PROJECT_NAME: str = "A-Share Research Agent"
    VERSION: str = "0.1.0"
    ENV: str = "dev"
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/ashare_agent"
    )
    DATABASE_ECHO: bool = False

    # LLM Settings
    LLM_PROVIDER: str = "deepseek"
    LLM_API_KEY: str = ""
    LLM_API_BASE: str = ""
    LLM_MODEL_NAME: str = ""
    # 默认留空：dev 环境下没真 key，应由 Agent 走 deterministic 兜底，
    # 而不是去发一次注定失败的 HTTP 请求。
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_API_BASE: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL_NAME: str = "deepseek-chat"

    # 备用 provider 凭据：保留接口，本项目当前主要走 DEEPSEEK_* / LLM_API_KEY。
    OPENAI_API_KEY: str = ""
    DASHSCOPE_API_KEY: str = ""
    ZHIPU_API_KEY: str = ""
    MINIMAX_API_KEY: str = ""
    MINIMAX_CN_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    XAI_API_KEY: str = ""

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # AkShare
    AKSHARE_CONCURRENCY: int = 4
    AKSHARE_RETRY_COUNT: int = 3
    AKSHARE_RETRY_DELAY_SECONDS: float = 1.5

    # DataRouter 路由层（data_pipeline/data_router.py）
    ROUTE_DEFAULT_TIMEOUT: float = 10.0  # 单端点超时（秒）
    ROUTE_DEFAULT_STRICT: bool = False   # strict 模式：任一 provider 失败即抛
    ROUTE_CACHE_TTL_SEC: float = 30.0    # TTL 缓存（同 method + 同参数复用）
    # 生产默认韧性策略（DataRouter() 未显式传 default_resilience 时启用）
    # 启用后会注入：重试 3 次 + 限流 5 rps + 熔断阈值 5 + bulkhead 10
    # 关闭（默认）= 行为不变；仅在生产环境显式打开
    ROUTE_DEFAULT_RESILIENCE: bool = False

    # 东财 HTTP 协议层（data_pipeline/providers/_shared/http_client.py）
    EM_MIN_INTERVAL_SEC: float = 1.0     # 东财最小请求间隔（秒）
    EM_USER_AGENT: str = ""              # 空 = 用默认 UA
    EM_RETRY_TOTAL: int = 3              # 总重试次数
    EM_RETRY_CONNECT: int = 3            # 连接重试次数
    EM_RETRY_BACKOFF: float = 0.6        # 重试 backoff 因子

    # mootdx 通达信服务器列表（data_pipeline/providers/_shared/tdx_client.py）
    # 留空时使用 _shared/tdx_client.py 内的国内 10 个 IP 默认值
    TDX_SERVERS: list[tuple[str, int]] = Field(default_factory=list)

    # Feature / Rule / Output 版本
    DEFAULT_FEATURE_VERSION: int = 1
    DEFAULT_RULE_VERSION: int = 1
    DEFAULT_OUTPUT_VERSION: int = 1

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    # 显式日志：方便在 docker logs / dev 中确认 LLM 是否带上了真 key
    import logging

    logging.basicConfig(level=logging.INFO)
    print(
        f"[settings] project={s.PROJECT_NAME} llm_provider={s.LLM_PROVIDER} "
        f"api_key_present={bool(s.LLM_API_KEY)} deepseek_key_present={bool(s.DEEPSEEK_API_KEY)}",
        flush=True,
    )
    return s


settings = get_settings()
