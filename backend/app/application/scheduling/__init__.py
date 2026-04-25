"""스케줄링 use-case 패키지.

cp_sat/ : OR-Tools CP-SAT 기반 모델 빌더 + 솔버 + lex-min-time 어댑터.
greedy/ : 납기역산 그리디 + retry harness + reschedule + JIT.

각 sub-package 의 orchestrator (cp_sat/orchestrator.py — Phase 1 step 4b
에서 cp_sat_optimizer.py 가 rename, greedy/auto_schedule.py) 가 use-case
entry-point. presentation/routes 또는 application/ingest/pipeline 에서
호출된다.
"""
