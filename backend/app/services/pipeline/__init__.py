"""Pipeline orchestration package — Week 4 Task 4A.1.

Splits the formerly 2660-line ``presentation/routes/plan_pipeline.py`` into
single-responsibility modules:

* ``run_labeler``  — run_label generation + base_date / date-range parsing
* ``stage1``       — Stage 1 (ERP→batch) orchestration helpers
* ``stage2``       — Stage 2 (solver/greedy) orchestration helpers
* ``orchestrator`` — top-level coordinator + AI background cache

Why a package and not a single module: Week 4 spec §7 requires SoC between
HTTP wiring and pipeline business logic. The route file (``plan_pipeline.py``)
keeps only FastAPI handlers + thin glue that delegates here.
"""

from app.services.pipeline.run_labeler import (  # noqa: F401
    new_run_label,
    parse_base_date_yyyymmdd,
    parse_date_yyyymmdd,
)
