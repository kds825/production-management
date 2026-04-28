# Manual smoke — 2-2 / 2-4 / 5-5 토글 활성화 확인

> 2026-04-28 본 스킴 도입 후 토글 동작이 README 가 약속한 대로 작동하는지 수동 검증.
> Playwright 자동 E2E 가 정착되면 본 문서는 archive 가능.

## 사전 준비

```bash
# Backend
cd backend && source venv/bin/activate && uvicorn app.main:app --reload --port 8000

# Frontend (별도 터미널)
cd frontend && npm run dev
```

브라우저: http://localhost:3000

## 시나리오 1 — 2-2 외주 자동분류 토글

| 단계 | 액션                                                                                  | 기대 동작                                                          |
| ---- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| 1.1  | `/master/constraints` 접속 → 2-2 행 OFF 토글                                          | row 변경 즉시 저장됨 토스트                                        |
| 1.2  | `/plan-register` → demo_wip_data.xlsx + 3.25진행및대기.xls 업로드 → "작업지시서 생성" | Stage 1 실행. 응답에 `outsource_count: 0`                          |
| 1.3  | `/scheduling-review` 진입 → 외주 시트 확인                                            | sq<=10 / 아이마켓 TFR-GV / TFR-8(SQ16 주문 모두 사내 시트로 분류됨 |
| 1.4  | `/master/constraints` 2-2 ON 복원 → 다시 Stage 1 실행                                 | `outsource_count > 0` 으로 복귀                                    |

## 시나리오 2 — 5-5 TFR-GV 절연 생략 토글

| 단계 | 액션                          | 기대 동작                                                |
| ---- | ----------------------------- | -------------------------------------------------------- |
| 2.1  | `/master/constraints` 5-5 OFF | 저장 토스트                                              |
| 2.2  | Stage 1 재실행                | TFR-GV sq<=25 주문이 **연선 시트에 등장** (이전: 시스만) |
| 2.3  | 5-5 ON 복원 → Stage 1         | TFR-GV sq<=25 가 다시 연선 시트에서 사라짐               |

## 시나리오 3 — 2-4 61연선 분리 토글

| 단계 | 액션                                               | 기대 동작                                                               |
| ---- | -------------------------------------------------- | ----------------------------------------------------------------------- |
| 3.1  | `/master/constraints` 2-4 OFF                      | 저장 토스트                                                             |
| 3.2  | sq>=300 CU 주문이 포함된 ERP 데이터로 Stage 1 실행 | 61연선 2단계 (T6B0 → 54BO) 분할이 사라지고 단일 stranding batch 만 생성 |
| 3.3  | 2-4 ON 복원 → Stage 1                              | 다시 batch_seq=0 (T6B0) + batch_seq=1 (54BO) 두 배치로 분할             |

## 시나리오 4 — Track B priority 슬라이더 (보너스)

| 단계 | 액션                                                            | 기대 동작                                                       |
| ---- | --------------------------------------------------------------- | --------------------------------------------------------------- |
| 4.1  | `/master/constraints` W-TNORM priority=50 (기본) → Stage 2 실행 | 결과 schedule_task 저장. `objective_value` 기록                 |
| 4.2  | W-TNORM priority=100 → Stage 2 재실행                           | `objective_value` 가 baseline 대비 증가 (tardiness 비제로 시)   |
| 4.3  | W-TNORM priority=0 → Stage 2 재실행                             | tardiness term 사실상 무력화 — past-due 주문이 뒤로 밀릴 가능성 |

## 결과 기록

검증 일자 / 검증자 / 통과 여부 / 첨부 스크린샷:

- 2026-04-28 — **\_** — [ ] PASS / [ ] FAIL — `screenshots/manual-smoke-2026-04-28.png`

## 회귀 가드

수동 smoke 가 통과하지 않으면:

1. `git diff HEAD~1 backend/app/application/ingest/batch_grouper.py` 로 wrapper 호출 누락 확인
2. `cd backend && source venv/bin/activate && pytest tests/test_batch_grouper_toggle_wrappers.py -v` 단위 테스트 재확인
3. Supabase `constraint_config` 행에서 직접 `is_enabled` 값 조회 — 페이지가 잘못 갱신했을 가능성:
   ```sql
   SELECT constraint_id, is_enabled, updated_at FROM constraint_config WHERE constraint_id IN ('2-2','2-4','5-5');
   ```
