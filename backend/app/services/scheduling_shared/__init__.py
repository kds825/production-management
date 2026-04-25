"""스케줄링 공용 헬퍼 패키지.

cp_sat_optimizer / schedule_optimizer 양쪽에서 공유되던 함수·상수를
중립적인 위치로 분리해 모듈 간 순환 import 를 제거한다 (Week 3 Task 3A.1).

서브모듈:
    calendar_ops  — datetime ↔ working-min 변환, base-date 결정
    group_ops     — 그룹 분류·SQ 추출·multi-equipment 스케줄링
    slot_filters  — 설비/슬롯 후보 필터링
    db_ops        — DB 정리 등 cross-cutting 헬퍼

원래 dotted path (app.services.cp_sat_optimizer.*, app.services.schedule_optimizer.*)
는 Week 9 까지 re-export 로 유지된다 (D7-C invariant).
"""
