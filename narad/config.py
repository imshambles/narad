from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Narad"
    database_url: str = f"sqlite+aiosqlite:///{Path(__file__).resolve().parent.parent / 'data' / 'narad.db'}"
    newsapi_key: str = ""
    gemini_api_key: str = ""
    fetch_interval_seconds: int = 300  # 5 minutes

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
