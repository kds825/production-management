"""CP-SAT 스케줄러 입력 빌더 패키지.

Task 1.1 (Production Handoff Refactor, Week 1): `cp_sat_schedule` 의
DB-로드 블록을 pure 함수 `build_solver_input` 으로 추출한다.

왜 분리하나:
  - 현재 `cp_sat_schedule` 은 "DB 조회 → 전처리 → CP-SAT 모델 구성 → 솔버 호출
    → DB 쓰기" 를 한 함수에 담아 테스트/리플레이가 어렵다.
  - 파러티 하니스(Task 1.4) 가 Rev 3 리팩터 전후 bit-exact 동일성을 검증
    하려면 "같은 입력 → 같은 출력" 을 보장할 `SolverInput` 값 객체가 필요.
  - Week 3 스케줄링 shared 모듈 이전의 첫 scaffold — 아직은 `cp_sat_schedule`
    내부 동작을 바꾸지 않고, override 경로만 추가한다 (behavior-neutral).

외부 노출은 `build_solver_input` 과 `SolverInput` 두 심볼로 충분하다.
"""

from app.application.scheduling.cp_sat.constraint_loader import (
    ConstraintSpec,
    load_active_constraints,
)
from app.application.scheduling.cp_sat.input_builder import SolverInput, build_solver_input
from app.application.scheduling.cp_sat.trace_writer import (
    TraceMetadata,
    compute_input_hash,
    compute_output_hash,
    write_trace,
)

__all__ = [
    "ConstraintSpec",
    "SolverInput",
    "TraceMetadata",
    "build_solver_input",
    "compute_input_hash",
    "compute_output_hash",
    "load_active_constraints",
    "write_trace",
]
