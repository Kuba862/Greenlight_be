from functools import lru_cache
from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr

BACKEND_DIR = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "Mój dojazd API"
    app_env: Literal["development", "test", "production"] = "development"

    db_host: str = Field(min_length=1)
    db_port: int = Field(default=6543, ge=1, le=65535)
    db_name: str = "postgres"
    db_user: str = Field(min_length=1)
    db_password: SecretStr
    db_migration_port: int = Field(default=5432, ge=1, le=65535)


@lru_cache
def get_settings() -> Settings:
    return Settings()