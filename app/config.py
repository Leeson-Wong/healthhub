from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_path: str = "./data/healthhub.db"
    seed_dir: str = "./seed"

    llm_base_url: str = "https://open.bigmodel.cn/api/anthropic"
    llm_model_main: str = "GLM-5.3"
    llm_model_light: str = "GLM-4.5-air"
    llm_api_key: str = Field(default="", validation_alias=AliasChoices("LLM_API_KEY", "ANTHROPIC_AUTH_TOKEN"))

    app_tz: str = "Asia/Shanghai"
    daily_report_time: str = "07:30"
    scheduler_enabled: bool = True

    ingest_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
