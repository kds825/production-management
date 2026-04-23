# 2026-04-17 Design Gate — batch_group unassign/restore v3 Phase 0

본 문서는 뒤따르는 모든 UI Task(UnassignConfirmModal, BatchGroupCard, Toast, ContextMenu)의 디자인 기반(gate)을 정의한다. 이 문서에 정의되지 않은 색/프리셋/아이콘 경로는 UI 코드에 등장할 수 없다.

## 핵심 원칙

- **외부 디자인 시스템 패키지(samildevkit 등) import 금지 — 디자인 내용을 프로젝트 내부에 직접 구현한다.**
- Raw hex 하드코딩 금지. fallback hex도 금지. UI 파일에서는 `var(--color-*)` 단독 사용.
- 이모지 금지. 아이콘은 heroicons(outline) / lucide-react 중 하나만.
- 이 게이트에 없는 색을 도입하려면 먼저 이 문서에 추가 후 `globals.css`에 정의해야 한다.

## 프로젝트 CSS 변수 (globals.css `:root`)

`frontend/src/app/globals.css`에 정의된 v3 Phase 0 토큰:

### 브랜드/텍스트

| 변수명 | 값 | 용도 |
|--------|-----|------|
| `--color-brand-primary` | `#c41230` | 주요 CTA(btn-primary), 브랜드 강조 |
| `--color-text-primary` | `#111827` | 본문 기본 텍스트 |
| `--color-text-secondary` | `#6b7280` | 보조/캡션 텍스트 |
| `--color-text-tertiary` | `#9ca3af` | 비활성/힌트 텍스트 |
| `--color-text-inverse` | `#ffffff` | 어두운 배경 위 텍스트 |

### 배경/보더

| 변수명 | 값 | 용도 |
|--------|-----|------|
| `--color-bg-elevated` | `#ffffff` | 카드/모달 배경 |
| `--color-bg-muted` | `#f3f4f6` | 호버/칩 배경 |
| `--color-bg-disabled` | `#f9fafb` | 비활성 상태 배경 |
| `--color-border-default` | `#e5e7eb` | 기본 보더 |
| `--color-border-muted` | `#f3f4f6` | 약한 보더 |
| `--color-overlay` | `rgba(0, 0, 0, 0.3)` | 모달 backdrop |

### 상태

| 변수명 | 값 | 용도 |
|--------|-----|------|
| `--color-success` | `#059669` | 성공 토스트/상태 |
| `--color-danger` | `#dc2626` | 위험 액션(unassign) |
| `--color-warning` | `#d97706` | 경고/사유 칩 |

### 공정별 (pastel)

한글 키는 의도적 사용 — 업무 도메인(공정명)과 변수명 1:1 매핑으로 가독성 확보.

| 변수명 | 값 | 용도 |
|--------|-----|------|
| `--color-process-연선` | `#a5b4fc` | 연선 공정 배치 카드 |
| `--color-process-B100` | `#67e8f9` | B100 공정 |
| `--color-process-A100` | `#6ee7b7` | A100 공정 |
| `--color-process-A120` | `#fcd34d` | A120 공정 |

## 스타일 프리셋 (shared/ui/styles.ts)

`frontend/src/shared/ui/styles.ts`에서 export하는 className 프리셋. 모든 UI 컴포넌트는 이 프리셋을 조합해 사용해야 한다.

| 프리셋 | 용도 |
|--------|------|
| `btnPrimary` | 확정/제출 등 강조 액션 (위험 액션 제외). danger일 때는 background를 `--color-danger`로 덮어씀 |
| `btnSecondary` | 취소/부차 액션 |
| `btnGhost` | 낮은 강조도의 인라인 액션 |
| `chipMuted` | 중성 메타 정보(수량/배치ID 등) 칩 |
| `chipReason` | 분할 사유 등 경고성 메타 칩 |
| `menuItem` | ContextMenu 내 단일 액션 row |

## 아이콘

다음 두 패키지 중 하나만 import. 이모지 사용 금지.

- `@heroicons/react/24/outline` — 일반 UI 아이콘
- `lucide-react` — 보조 아이콘 풀

설치는 `frontend/package.json`의 dependencies에 포함되어 있어야 하며, 이 태스크에서 신규 설치됨.

## 검증 게이트 (Phase 7에서 확인)

- Grep: `samildevkit` 토큰이 UI 소스코드에 0건.
- Grep: UI 파일에서 raw hex(`#[0-9a-fA-F]{3,8}`)가 `globals.css` 외에 0건.
- Grep: `styles.ts`에 이모지/raw hex 0건.
- `npx tsc --noEmit` 에러 0건.
