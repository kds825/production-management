"""application 레이어 cross-cutting 공용 헬퍼 패키지.

solver/greedy/ingest 등 use-case 가 공유하는 헬퍼·dataclass 를 모은다.
parent layer (application) 안에 격리해 별도 shared/ 레이어 신설을 회피한다.

서브모듈:
    audit_logger        — 의사결정 감사 로그 기록/조회
    constraint_params   — ConstraintConfig 파라미터 프리페치 캐시 (load + dataclass)
    calendar_ops        — datetime ↔ working-min 변환, base-date 결정
    group_ops           — 그룹 분류·SQ 추출·multi-equipment 스케줄링
    slot_filters        — 설비/슬롯 후보 필터링
    db_ops              — DB 정리 등 cross-cutting 헬퍼
"""
