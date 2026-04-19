# Changelog

변경사항 요약. 상세 설계는 `docs/specs/`, 구현 플랜은 `docs/plans/` 참조.

## 2026-04-18 — SpeedMaster 셋업 시간 인라인 편집

- `/master/speed` 페이지에서 `setup_spec_min` / `setup_color_min` / `setup_compound_min` / `setup_start_min` 4 컬럼 인라인 편집 (onBlur autosave)
- Pydantic 화이트리스트 PATCH `/master/speed_master/{id}/setup-params` — 구조 필드(`equipment_code` 등) 편집 차단, 음수 거부, null 덮어쓰기 차단
- `SpeedMaster.updated_at` 컬럼 추가 → `/constraints/drift-status` 에 포함 → SpeedMaster 편집 후에도 재실행 배너 ON
- `/master/constraints` 파라미터 탭에 "row 값 있으면 우선" 안내문 → 두 화면 역할 명확화
- **우선순위 규칙 명문화**: `SpeedMaster row 값 > ConstraintConfig 4-x 공정 기본값`
- 4-3 드럼 권취 편집 과제 흡수 — `setup_start_min` 편집으로 해결
- 범위 밖: row 신규 추가/삭제, 일괄 편집, 변경 이력 UI, 구조 필드 편집
- 스펙: `docs/specs/2026-04-18-speedmaster-setup-edit-design.md`
- 플랜: `docs/plans/2026-04-18-speedmaster-setup-edit.md`

## 2026-04-18 — ConstraintConfig 파라미터 UI 편집 (4-1/4-2/4-4)

- `/master/constraints` 페이지 **"파라미터" 탭** 추가 — 규격교체(4-1), 색상교체(4-2), 용접(4-4) 시간을 UI 에서 편집 가능
- 저장 모달: 영향 배치 N건 프리뷰 + "지금 기존 계획에도 반영" vs "다음 자동배열부터 적용" 2지선다
- **변경 이력** 탭 + Silent drift 배너 (저장 후 재실행 미완료 시)
- 하드코딩 `else 210.0` (연선 규격교체 3.5h) 제거 → `ConstraintConfig` 프리페치 조회
- `sm_color[0] == 0` 시맨틱 교정 — 0분을 "값 없음" 이 아닌 "0분 허용" 으로 처리
- 시드값 210/60/30/300/120/30 유지 → **No-op 불변식**: UI 변경 없이는 기존 스케줄과 동일
- **범위 밖** (별도 과제): 연선 메인 배치 `else 0.0` (batch_grouping.py:463-467), 드럼 권취(4-3), SpeedMaster 연선 row 보완
- 스펙: `docs/specs/2026-04-18-constraint-config-params-ui-design.md`
- 플랜: `docs/plans/2026-04-18-constraint-config-params-ui.md`
