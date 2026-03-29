from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://kbi:kbi_poc_2026@localhost:5432/kbi_scheduler"
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    APP_TITLE: str = "KBI Production Scheduler API"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
