from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    database_url: str = Field(default="sqlite:///data/app.db", alias="DATABASE_URL")
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password: str = Field(default="change_me", alias="ADMIN_PASSWORD")
    secret_key: str = Field(default="change_me", alias="SECRET_KEY")
    service_name: str = Field(default="Городской справочник", alias="SERVICE_NAME")
    public_base_url: str = Field(default="http://localhost:8000", alias="PUBLIC_BASE_URL")
    ads_enabled: bool = Field(default=False, alias="ADS_ENABLED")
    auto_db_bootstrap: bool = Field(default=True, alias="AUTO_DB_BOOTSTRAP")
    official_telegram_source_id: str = Field(default="", alias="OFFICIAL_TELEGRAM_SOURCE_ID")
    official_telegram_source_name: str = Field(
        default="Официальный источник администрации",
        alias="OFFICIAL_TELEGRAM_SOURCE_NAME",
    )
    official_telegram_source_kind: str = Field(
        default="official_channel",
        alias="OFFICIAL_TELEGRAM_SOURCE_KIND",
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
