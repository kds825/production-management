"""
KBI Production Scheduler — FastAPI 애플리케이션 진입점
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.presentation.routes import (
    constraints,
    equipment,
    orders,
    process_routes,
    schedules,
)

app = FastAPI(
    title=settings.APP_TITLE,
    version="0.1.0",
    description="KBI Cosmolink 생산 스케줄 관리 API (PoC)",
)

# CORS 설정 — 프론트엔드 개발 서버 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록 (모두 /api 접두사)
app.include_router(equipment.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(schedules.router, prefix="/api")
app.include_router(constraints.router, prefix="/api")
app.include_router(process_routes.router, prefix="/api")


@app.get("/api/health", tags=["헬스체크"])
def health_check() -> dict[str, str]:
    """서버 상태 확인"""
    return {"status": "ok", "service": settings.APP_TITLE}
