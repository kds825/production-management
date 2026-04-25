from pathlib import Path

from pydantic_settings import BaseSettings

# Absolute path so Settings loads backend/.env regardless of CWD.
# The relative ".env" form silently falls back to the default
# DATABASE_URL (local Docker) whenever a script is run from repo root
# or a sibling worktree — violates Path D (Supabase-native) contract.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent  # backend/


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://kbi:kbi_poc_2026@localhost:5432/kbi_scheduler"
    # Both `localhost` and `127.0.0.1` are accepted out of the box because
    # browsers treat them as different origins. dev-mode pages opened by
    # one form would fail CORS preflight against an API server bound only
    # to the other form, even on a single laptop.
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    APP_TITLE: str = "KBI Production Scheduler API"

    class Config:
        env_file = str(_BACKEND_ROOT / ".env")
        extra = "ignore"


settings = Settings()
