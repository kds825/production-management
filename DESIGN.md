---
project: KBI 생산계획 AI Agent
brand: PwC × KBI 공동 브랜드
audience: B2B 산업용 SaaS · 한국어 우선 · 데이터 밀도 높음 · 라이트 우선
density: high # 24/7 무인 라인 한 화면 표시 — 패딩보다 정보 밀도
status_palette: color+shape # 색맹/저조도 대응: 색만이 아닌 좌측 액센트 바 / 빗금 / 외곽선 병행
tokens_source: frontend/src/app/globals.css # 코드의 단일 출처 (Tailwind v4 @theme + :root CSS vars)
tokens_reference: docs/design-system/ # HTML 미리보기 카드 + UI kit (samildevkit handoff)
verify_skill: verify-pwc-design # samildevkit 준수 자동 검증
---

# KBI 디자인 시스템 — Single Source of Truth

이 문서는 **AI 에이전트와 사람이 모두 읽는 단일 출처** 입니다.
UI 코드를 작성·수정하기 전에 반드시 이 파일을 통독하고, frontmatter 의 토큰 출처 / 아래 규칙 / 안티패턴을 따릅니다.

> **변경 우선순위:**
> ① 본 파일 (DESIGN.md, project root)
> ② `frontend/src/app/globals.css` (`@theme` 블록 + `:root` CSS 변수 — 코드 토큰 정의)
> ③ `docs/design-system/` (HTML 미리보기 카드 + UI kit)
> ④ `frontend/CLAUDE.md` · `AGENTS.md`
>
> **충돌 해결**: 코드와 본 문서가 충돌하면 **본 문서가 진실** — 코드를 갱신. 그 반대 금지.

---

## 디자인 원칙

1. **PwC × KBI 공동 브랜드** — 베이스는 PwC 의 절제된 그레이/타이포, 액션·상태 색은 KBI 시그니처(Sunrise Red, Warm Gray, Champagne Gold).
2. **데이터 밀도 우선** — 24시간 무인 라인을 한 화면에 보여줘야 함. 패딩보다 정보 밀도, 채도보다 대비.
3. **Agent-first 인터랙션** — 자연어 채팅은 보조가 아닌 **주된 입력 수단**. 화면 우측 고정 패널 + 제안 칩.
4. **상태는 색이 아닌 색+형태로** — 색맹/저조도 모니터 대비를 위해 좌측 액센트 바, 패턴(빗금), 외곽선을 병행.

---

## 색 토큰 사용 규칙

> 모든 토큰은 `frontend/src/app/globals.css` 에 정의됨. 컴포넌트에서는 `var(--token-name)` 또는 Tailwind v4 자동 생성 클래스 (`bg-kbi-red`, `text-pwc-gray-500` 등) 사용.

| 용도                       | 토큰                                          | 메모                                      |
| -------------------------- | --------------------------------------------- | ----------------------------------------- |
| 주요 액션·CTA·로고 강조    | `--kbi-sunrise-red` (= `--color-kbi-red`)     | 한 화면에 1–3회. 남용 금지                |
| 헤더 텍스트·사이드바 배경  | `--kbi-warm-gray` (= `--color-kbi-brown`)     | KBI 차분한 진회색                         |
| 진행 중 작업               | `--kbi-orange`                                | 액티브 프로세스                           |
| 완료·달성 표시             | `--kbi-champagne-gold` (= `--color-kbi-gold`) | 빗금 패턴과 병행                          |
| 대기·중립                  | `--kbi-silver`                                | 작업 미배정                               |
| 본문 텍스트                | `--fg-1` / `--fg-2` / `--fg-3`                | PwC 그레이 스케일                         |
| 캔버스/카드 배경           | `--bg-canvas` / `--bg-surface` (`#fff`)       | 카드는 무조건 흰색                        |
| 보더                       | `--border-1` (`--pwc-border` 별칭)            |                                           |
| Decision Card v2 (Phase 6) | `--color-pwc-*` 시리즈                        | samildevkit handoff 보존 — 임의 변경 금지 |

**임계값 표시 규칙** — 부하율·납기율 80% 이상은 `--kbi-sunrise-red` 단독. 그 이하는 `--kbi-warm-gray`.

---

## 타이포

- 한글: Pretendard Variable (CDN) → 시스템 한글 폴백.
- **숫자: tabular-nums 필수** (`font-feature-settings: "tnum"` 또는 `.t-num-tabular` 클래스) — 가동률·시간·m·kg 모두.
- 코드(작업번호 KBI-2418 등): `--font-mono` (IBM Plex Mono).
- 타이포 스케일 (`globals.css` `@theme`):
  - Display/Title: `--text-pwc-title4` (24/32/700)
  - Subtitle: `--text-pwc-subTitle2` (16/24/600)
  - Body: `--text-pwc-body` (14/22/400)
  - SubBody: `--text-pwc-subBody` (13/20/400)
  - Caption: `--text-pwc-caption` (12/18/400)
  - Badge: `--text-pwc-badge` (12/16/600)
  - **Compact (PR5)**: `--text-micro` (8) / `--text-mini` (9) / `--text-tiny` (10) / `--text-small` (11) — 컴팩트 UI 전용

---

## 컴포넌트 가이드

### Sidebar

- Warm Gray 배경 고정. 흰 글자 78% 투명도, 활성 항목만 100%.
- 활성 항목 좌측에 Sunrise Red 3px 보더.
- 너비 240px 고정.

### TopBar

- 좌측: PwC 로고 → 구분선 → 제품명 (KBI 굵게, 보조 설명 회색).
- 우측: 검색 → 알림 → 아바타.
- 절대 사이드바 위로 올라가지 않음 (브랜드 위계: PwC 가 제품 헤더에서만 노출).

### KpiCard

- 좌측 4px 액센트 바 + tone(`red|orange|gold|warm`).
- 큰 숫자 30/36 700, 단위 14/20 500.
- 델타는 ▲▼─ 화살표만. 색은 up=초록(`--status-success`) / down=Sunrise Red / flat=회색.

### Job Block (배치 셀)

- 4상태: `progress` `idle` `done` `delay`.
- `delay` 만 단색 배경 (Sunrise Red), 나머지는 흰 배경 + 좌측 3px 액센트.
- 코드(KBI-XXXX) 는 mono, 사양 13px, 미터 회색 11px.
- `delay` 셀은 항상 시선의 우선순위가 가장 높아야 함.

### 간트 바

- `done`: 금색 + 빗금 (완료감).
- `progress`: 오렌지 + 우측 그라디언트로 진척률 암시.
- `normal`: Warm Gray 85%.
- `delay`: 흰 배경 + Sunrise Red 2px 외곽선.
- 선택 시 Sunrise Red 2px focus ring (`--shadow-focus`).

### Decision Card v2 (Phase 6)

- samildevkit `--color-pwc-*` 토큰 사용 — **운영자 50건 카드 봐도 같은 색 = 같은 종류 신호** 학습 일관성 유지.
- 임의 변경 금지. 토큰 정리는 별도 PR 에서 사용처와 함께 일괄 처리.

### Agent 패널

- 화면 우측 고정 380px.
- 어시스턴트 버블: 흰 배경 + 보더, 좌측 하단 코너만 직각.
- 사용자 버블: Warm Gray 8% 배경 (`--kbi-warm-gray-tint-8`), 우측 하단 코너만 직각.
- 제안 칩(suggest-chip)으로 자연어 명령 예시를 항상 노출.
- 액션 버튼은 버블 안에 종속.

### Status Pill

`progress · idle · done · delay · ok` — 점 + 라벨. 외곽선 없음. 텍스트 셀 안 인라인 표시 용도.

---

## 안티패턴 (Guardrails — AI 에이전트 필독)

> 아래는 **절대 금지**. 본 항목 위반 시 `verify-pwc-design` 스킬 / 코드 리뷰에서 즉시 차단.

- ❌ **Sunrise Red 를 본문/보더/대형 영역에 사용** — 경고와 액션 시그널이 희석됨.
- ❌ **임의 hex 추가** — `frontend/src/app/globals.css` 에 토큰으로 추가하고 `var(--token-name)` 또는 Tailwind 자동 클래스로 사용.
- ❌ **Tailwind arbitrary value 안에 hex** (`bg-[#FF0000]`) — 반드시 토큰 var (`bg-[var(--kbi-sunrise-red)]` 또는 `bg-kbi-red`).
- ❌ **그라디언트 배경, 이모지, 둥근 모서리 + 좌측 보더 액센트 컨테이너** (SaaS 클리셰).
- ❌ **작업 코드(KBI-XXXX) 를 sans 로 표기** — 항상 mono.
- ❌ **이탤릭** — PwC 브랜드 가이드 위반. `verify-pwc-design` 으로 자동 검증.
- ❌ **숫자에 tabular-nums 누락** — 정렬이 깨져 데이터 밀도 원칙 훼손.
- ❌ **`--color-pwc-*` 토큰 임의 수정** — Phase 6 Decision Card 수십 곳이 의존. 정리는 PR 에서 사용처 일괄 처리.

---

## 다음에 만들 가능성 높은 화면

- **자원 관리** — 원자재 재고 × 호기 매트릭스, 적정/부족 임계값.
- **시뮬레이션** — Before/After 비교 카드 + 지표 다이프 + Agent 자연어 시나리오 입력.
- **알림 센터** — 지연·임계 초과·자원 부족을 시간순/우선순위순.

세 화면 모두 위의 KpiCard / Status Pill / Agent Panel 패턴을 그대로 재사용.

---

## 참고 자료

- **토큰 코드 출처**: `frontend/src/app/globals.css` (`@theme` 블록 + `:root` CSS 변수). PR1~5 적용 결과.
- **HTML 미리보기 카드**: `docs/design-system/preview/` (22개 토큰별 카드 — colors / spacing / typography / shadows / components).
- **UI kit 데모**: `docs/design-system/ui_kits/production-agent/index.html`.
- **풀 CSS 변수 정의 (samildevkit handoff 원본)**: `docs/design-system/colors_and_type.css`.
- **검증 스킬**: `verify-pwc-design` (samildevkit 준수 자동 체크).

---

## 변경 이력

- 2026-04-28: 초기 작성 — `/Users/jaewookim/Downloads/kbi-design-system/` 자료를 `docs/design-system/` 으로 이전 + 본 파일 (프로젝트 루트) 정착. PR1~5 디자인 토큰 작업 결과를 단일 출처로 통합.
