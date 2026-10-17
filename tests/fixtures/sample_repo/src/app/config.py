"""Application configuration loaded from environment variables."""

import os


class Settings:
    """Runtime settings for the sample service."""

    def __init__(self) -> None:
        self.database_url: str = os.environ.get("SAMPLE_DB_URL", "sqlite:///./sample.db")
        self.debug: bool = os.environ.get("SAMPLE_DEBUG", "0") == "1"
        self.max_page_size: int = int(os.environ.get("SAMPLE_MAX_PAGE", "100"))


settings = Settings()
