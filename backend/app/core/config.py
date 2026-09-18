from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://hfmg:hfmg_dev_local@localhost:5432/hfmg_helpdesk"

    cors_origins: str = "http://localhost:5173"

    enable_email_notifications: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_from_email: str = "helpdesk-noreply@hfmg.net"
    helpdesk_notification_email: str = "helpdesk@hfmg.net"

    enable_ai_summary: bool = False
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
