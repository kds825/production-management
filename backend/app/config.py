from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

# Absolute path so Settings loads backend/.env regardless of CWD.
# The relative ".env" form silently falls back to defaults whenever a script
# is run from repo root or a sibling worktree — Supabase-native deployments
# require the URL be provided explicitly.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent  # backend/


class Settings(BaseSettings):
    # 필수 — backend/.env 의 DATABASE_URL 을 통해 Supabase 인스턴스에 연결.
    # 누락 시 import time 에 ValidationError 로 즉시 실패 (silent fallback X).
    DATABASE_URL: str = Field(...)
    # Both `localhost` and `127.0.0.1` are accepted out of the box because
    # browsers treat them as different origins. dev-mode pages opened by
    # one form would fail CORS preflight against an API server bound only
    # to the other form, even on a single laptop.
    CORS_ORIGINS: list[str] = ["*"]
    APP_TITLE: str = "KBI Production Scheduler API"

    class Config:
        env_file = str(_BACKEND_ROOT / ".env")
        extra = "ignore"


settings = Settings()
