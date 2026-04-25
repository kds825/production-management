"""Greedy 옵티마이저의 setup-phase 데이터 로더 모음.

``optimization_loop._run_optimization_once`` 의 전반부 (DB 조회 + 정렬 + WIP 필터
+ base_date 결정 + 마스터 데이터 로딩) 을 단일 책임 헬퍼로 분리. 사용자 요구
"#1 단일 책임 함수로 불러오기, 확장 가능한 형태로 db 등에서 가져오는 방식" 에
대응한다.

각 모듈 = 한 가지 입력 종류 = ConstraintParams / planned batches / WIP / 마스터.
새 데이터 소스 추가 시 새 파일 한 개 추가 + ``_run_optimization_once`` 의
호출 라인 하나만 변경하면 된다.
"""
