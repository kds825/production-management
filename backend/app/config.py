from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./kbi_scheduler.db"
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    APP_TITLE: str = "KBI Production Scheduler API"

    class Config:
        env_file = ".env"


settings = Settings()
