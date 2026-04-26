## Architecture (post Phase 0~5 refactoring, 2026-04)

Backend follows 4-layer Clean Architecture under `backend/app/`:

- `domain/` — pure logic, no DB/IO (constants, constraint_rules, sheath_cluster, tardiness, batch_sheath_keys)
- `application/` — use-case orchestration with DB/IO allowed
  - `_shared/` cross-cutting (audit_logger, slot_filters, calendar_ops, group_ops, db_ops, constraint_params)
  - `ingest/` Stage 1 (batch_grouper, wip_matching, stage1, pipeline_orchestrator)
  - `scheduling/cp_sat/` CP-SAT engine (orchestrator + lex_min_time, default)
  - `scheduling/greedy/` 그리디 (auto_schedule + optimization_loop + scheduler_state, fallback/retry)
  - `decisions/` LLM explanation (narrator + explain_batch + summarize_run + risk_detector)
  - `validation/`, `cascade/`, `stage2_job_queue.py`, `sm_inventory.py`
- `infrastructure/` — I/O adapters (database, calendar_engine, parsers/, exporters/, llm/, models/, logging/)
- `presentation/` — FastAPI routes + schemas

**Source of truth**: [docs/architecture-target.md](docs/architecture-target.md) (§3 layout map, §11 phase markers).

**Rules**:

- `services/` 디렉토리는 삭제됨 (Phase 5 §9.4-d). 새 모듈 추가 시 절대 부활 금지.
- 추상화 도입 금지 (Protocol / Registry / ABC / Service base class) — 사용자 simplicity 원칙. dataclass + 함수.
- 디렉토리 깊이 ≤ 3 (`domain/X.py`, `application/X/Y.py`, `infrastructure/X/Y.py`).
- 변경 후 검증 게이트: `pytest backend/tests/ -q` + main-parity 27/27 + parity-quick 11/11. 회귀 0 확인 후 atomic commit.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:

- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` to keep the graph current
