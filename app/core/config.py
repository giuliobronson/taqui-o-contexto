from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database (direct Postgres connection used only by the backend)
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:54322/postgres"

    # Supabase Auth
    supabase_url: str = "http://localhost:54321"
    supabase_anon_key: str = ""

    # App
    app_base_url: str = "http://localhost:8000"
    admin_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)
    timezone: str = "America/Sao_Paulo"
    secure_cookies: bool = False

    @field_validator("admin_emails", mode="before")
    @classmethod
    def split_admin_emails(cls, value: object) -> object:
        if isinstance(value, str):
            return [email.strip().lower() for email in value.split(",") if email.strip()]
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
