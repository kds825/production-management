"""
KBI Production Scheduler — FastAPI 애플리케이션 진입점
"""

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.config import settings
from app.infrastructure.logging import RunIdMiddleware
from app.presentation.routes import audit  # noqa: F401
from app.presentation.routes import change_sets  # noqa: F401
from app.presentation.routes import constraints  # noqa: F401
from app.presentation.routes import decision_card  # noqa: F401
from app.presentation.routes import decision_feedback  # noqa: F401
from app.presentation.routes import decisions  # noqa: F401
from app.presentation.routes import equipment  # noqa: F401
from app.presentation.routes import master_data  # noqa: F401
from app.presentation.routes import orders  # noqa: F401
from app.presentation.routes import plan_pipeline  # noqa: F401
from app.presentation.routes import process_routes  # noqa: F401
from app.presentation.routes import schedules  # noqa: F401

# Task 23: observability 모듈 import — prometheus Histogram/Counter/Gauge 가
# import 시점에 default registry 에 등록되어 `/metrics` 가 바로 노출함.
from app.observability import metrics  # noqa: F401

app = FastAPI(
    title=settings.APP_TITLE,
    version="0.1.0",
    description="KBI Cosmolink 생산 스케줄 관리 API (PoC)",
)

# CORS 설정 — 프론트엔드 개발 서버 허용. Added FIRST so RunIdMiddleware
# becomes the OUTERMOST layer (Starlette prepends to user_middleware,
# so last-added = outermost). This ensures CORS preflight (OPTIONS)
# responses ALSO carry X-Run-Id, satisfying spec §10a "every response".
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Task 2A.4 (spec §10a): RunIdMiddleware — stamps X-Run-Id on every
# response (including CORS preflight) and sets the contextvar for log
# correlation. Added LAST so it wraps CORS on the outside: request-side
# runs first (contextvar set before route/logging + before CORS), and
# response-side runs last (stamps header after CORS produces its response,
# so preflight OPTIONS also get X-Run-Id).
app.add_middleware(RunIdMiddleware)

# 라우터 등록 (모두 /api 접두사)
app.include_router(equipment.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(schedules.router, prefix="/api")
app.include_router(constraints.router, prefix="/api")
app.include_router(process_routes.router, prefix="/api")
app.include_router(plan_pipeline.router, prefix="/api")
app.include_router(master_data.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(decisions.router, prefix="/api")
app.include_router(decision_card.router, prefix="/api")
app.include_router(decision_feedback.router, prefix="/api")
app.include_router(change_sets.router, prefix="/api")


@app.on_event("startup")
def _register_decision_card_providers() -> None:
    """Phase 6 — phrasing / section / gantt provider 명시적 register.

    plan §B.1 Engineer review note: lifespan startup event 안에서만 등록.
    모듈 import time 등록 X (테스트 부팅 부작용 차단).
    """
    from app.application.decisions.equipment_day_gantt import (
        DefaultGanttBuilder,
        InsulationGanttBuilder,
        OutsourceGanttBuilder,
        SheathGanttBuilder,
        StrandingGanttBuilder,
        register_gantt_builder,
    )
    from app.application.decisions.phrasing import register_phrasing_provider
    from app.application.decisions.phrasing_providers import (
        DefaultPhrasingProvider,
        InsulationPhrasingProvider,
        OutsourcePhrasingProvider,
        SheathPhrasingProvider,
        StrandingPhrasingProvider,
    )
    from app.application.decisions.section_builder import (
        DefaultSectionBuilder,
        InsulationSectionBuilder,
        OutsourceSectionBuilder,
        SheathSectionBuilder,
        StrandingSectionBuilder,
        register_section_builder,
    )

    for p in (
        DefaultPhrasingProvider(),
        SheathPhrasingProvider(),
        StrandingPhrasingProvider(),
        InsulationPhrasingProvider(),
        OutsourcePhrasingProvider(),
    ):
        register_phrasing_provider(p)
    for sb in (
        DefaultSectionBuilder(),
        SheathSectionBuilder(),
        StrandingSectionBuilder(),
        InsulationSectionBuilder(),
        OutsourceSectionBuilder(),
    ):
        register_section_builder(sb)
    for gb in (
        DefaultGanttBuilder(),
        SheathGanttBuilder(),
        StrandingGanttBuilder(),
        InsulationGanttBuilder(),
        OutsourceGanttBuilder(),
    ):
        register_gantt_builder(gb)


@app.get("/api/health", tags=["헬스체크"])
def health_check() -> dict[str, str]:
    """서버 상태 확인"""
    return {"status": "ok", "service": settings.APP_TITLE}


# ---------------------------------------------------------------------------
# Task 23: /metrics — Prometheus scrape endpoint.
#
# 관측/모니터링 파이프라인(Grafana/Loki 등)이 직접 수집할 수 있도록 기본
# global registry 의 모든 메트릭을 text exposition format 으로 제공한다.
# 왜 /api prefix 없이: 외부 모니터링 툴은 관례적으로 "/metrics" 를 기대하므로
# 표준 경로를 유지.
# ---------------------------------------------------------------------------
@app.get("/metrics", tags=["관측성"])
def prometheus_metrics() -> Response:
    """Prometheus text exposition format 으로 현재 메트릭 스냅샷을 반환."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
