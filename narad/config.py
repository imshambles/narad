from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Narad"
    database_url: str = f"sqlite+aiosqlite:///{Path(__file__).resolve().parent.parent / 'data' / 'narad.db'}"
    newsapi_key: str = ""
    gemini_api_key: str = ""
    aisstream_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    fetch_interval_seconds: int = 300  # 5 minutes
    # Paper trading
    paper_trading_enabled: bool = False
    paper_trading_capital: float = 1000000.0  # 10 lakh INR
    paper_trading_max_exposure_pct: float = 60.0
    paper_trading_stop_loss_pct: float = 5.0
    paper_trading_take_profit_pct: float = 15.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
