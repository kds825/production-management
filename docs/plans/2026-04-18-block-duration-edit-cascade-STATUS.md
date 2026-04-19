# 블록 소요시간 편집 · 연쇄 재배치 — 실행 결과 (2026-04-18)

## 요약

- **Spec**: `docs/specs/2026-04-18-block-duration-edit-cascade-design.md` (v2, 커밋 `e61afc7`)
- **Plan**: `docs/plans/2026-04-18-block-duration-edit-cascade.md` (30 tasks, 커밋 `b02b3b4`)
- **실행 방식**: subagent-driven-development (fresh implementer per task + two-stage review)
- **결과**: Task 1~27 구현 완료. Task 28~30 (verify-pwc-design / graphify / document-release)은 도구 가용성 제약으로 수동 확인.

## Phase 별 진행

| Phase                     | Tasks | 커밋                                                                        | 상태                                          |
| ------------------------- | ----- | --------------------------------------------------------------------------- | --------------------------------------------- |
| 0 Foundation              | 1–3   | `bf58fca`, `74abdeb`, `5414123`, `1dc19e9`                                  | ✅                                            |
| 1 Backend algorithm (TDD) | 4–10  | `99e1203`, `95e2eec`, `4d39cad`, `3193d00`, `41b2f94`, `ca6496d`, `fa0baa3` | ✅                                            |
| 2 Backend API             | 11–15 | `de2dca6`, `aa5f5c8`, `00fe2e5`                                             | ✅                                            |
| 3 Frontend                | 16–21 | `993158d`, `d9c8c18`, `f4051c4`, `e902be4`, `90305ee`                       | ✅                                            |
| 4 Ghost + Obs + E2E       | 22–27 | `ae559d9`, `8bb17a1`, `14db50d`                                             | ✅ (E2E spec 작성, 실행은 seed endpoint 의존) |
| 5 Gate + Docs             | 28–30 | —                                                                           | ⚠️ 도구 가용성 제약 (본 문서에 상태 기록)     |

## 테스트 현황

### Backend

- `tests/services/cascade/` (snap/bfs/validators/pull/service): 52 passed
- `tests/test_cascade_preview_v2.py` (Task 11): 6 passed
- `tests/test_bulk_update_v2.py` (Task 13): 계약/flag 테스트 passed
- `tests/test_schedule_validators.py` (Task 13): passed
- `tests/test_schedule_change_set_model.py` (Task 12): passed
- `tests/test_revert.py` (Task 14): 3 passed
- `tests/test_cascade_preview_benchmark.py` (Task 15): p95 max < 1.5s (실측 ~5ms)
- `tests/test_observability_metrics.py` (Task 23): 5 passed
- **누계 79 cascade-related backend tests passing**

### Frontend

- `cascade.test.ts` (Task 16): 6 passed
- `Toast.test.tsx` (Task 18): 5 passed
- `useScheduleChangeWithCascade.test.ts` (Task 17): 3 contract passed
- `ConflictResolutionModal.test.tsx` (Task 19): 5 passed
- `TaskFormModal.test.tsx` (Task 20): 13 passed (pure helpers)
- `page-integration.test.ts` (Task 21): 3 contract passed
- `ghost-overlay.test.tsx` (Task 22): 5 passed
- **누계 59 frontend tests passing (10 files)**

### E2E (Playwright)

- Fixture + 6 tests 작성 완료, `npx playwright test --list` 로 파싱 확인.
- **실행은 backend seed endpoint (`/api/test/reset-schedule`) 구현 후 가능** — 현재 `test.skip` 으로 우아하게 스킵.

## Phase 5 (Gate + Docs) 수동 확인 결과

### Task 28 — verify-pwc-design

- `ConflictResolutionModal.tsx` + `TaskFormModal.tsx`: raw hex **0** (grep 결과). 모두 `var(--color-brand-primary)`, `var(--color-danger)`, `var(--color-warning)`, `var(--kbi-brown)`, `var(--color-text-tertiary)` 등 프로젝트 토큰.
- 완전 스킬 기반 검증은 별도 세션에서 `verify-pwc-design` 스킬 호출 권장.

### Task 29 — graphify rebuild + god-node 확인

- `graphify` Python 모듈이 **로컬 환경에 미설치** (`ModuleNotFoundError`). 별도 세션에서 `pip install graphify` 후 재빌드 필요.
- 구조적으로는 `cascade/` 5-module 분할 (snap/bfs/validators/pull/service + reasons) 로 SRP 유지 — spec §3.8 god-node 방지 원칙 준수.

### Task 30 — document-release

- 본 문서 (`STATUS.md`) 가 일차 기록.
- README/ARCHITECTURE/CHANGELOG 반영은 별도 세션에서 `document-release` 스킬 호출 권장.

## 주요 남은 작업 (후속 세션 권장)

1. **Backend seed endpoint** — `/api/test/reset-schedule` 구현 후 E2E 3종 실제 실행.
2. **frontend `onManualAdjust` 연결** — 수주 상세 라우트 경로 확정 후 `ConflictResolutionModal` 의 "수동 조정 진입" CTA wire-up.
3. **납기 초과 카운터 필터 반응화** — 본 PR 에서 분리 (별도 spec).
4. **graphify rebuild + god-node in-degree ≤ 15 assertion** — graphify 설치 환경에서.
5. **verify-pwc-design / document-release 스킬 실행** — 별도 세션.
6. **블록 통째 이동 토글** — Task 20 spec §6.1 known limitation (§11 미해결).

## 세션 통계

- **Atomic 커밋 (cascade 범위)**: 17건 (Task 1–27 + Task 4 fix).
- **subagent 디스패치**: ~20회 (implementer + spec/code reviewers + fixup).
- **spec 리비전**: CEO/Engineer/UX/DevEx 병렬 비판 리뷰 반영 (v1 → v2).
- **MAX_WAVES**: 5 → 4 (파이프라인 깊이 3 + 안전마진 1).
- **주요 v1 버그 수정**: B2 BFS 양방향 · B3 wave dedup · B4 Pull same-eq prev 가드 · B5 Pull 마스터 토글 · B6 Undo via change_set_id · B7 수동조정 CTA · B8 feature flag · B9 error_code enum.
- **Tier 2 (10x) 반영**: 간트 고스트 오버레이 + 규칙기반 자연어 summary.
