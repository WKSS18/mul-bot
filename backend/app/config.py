from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")
    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./tongpin-dev.db"
    jwt_secret: str = ""
    jwt_issuer: str = "tongpin-ai"
    access_minutes: int = 30
    anthropic_auth_token: str = ""
    anthropic_base_url: str = "https://api.deepseek.com/anthropic"
    anthropic_model: str = "deepseek-v4-flash"
    api_timeout_ms: int = 600000
    oss_bucket: str = "wh-syyoss"
    oss_region: str = "cn-beijing"
    oss_endpoint: str = "https://oss-cn-beijing.aliyuncs.com"
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    max_upload_mb: int = 20


settings = Settings()


def validate_settings() -> None:
    if len(settings.jwt_secret) < 40:
        raise RuntimeError("JWT_SECRET must contain at least 40 characters")
