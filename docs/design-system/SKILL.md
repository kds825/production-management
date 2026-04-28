# SKILL — KBI 생산계획 Agent 디자인 시스템 사용법

## 언제 이 시스템을 쓰는가
PwC × KBI 공동 프로젝트의 **생산계획·자원관리·시뮬레이션** 화면을 만들 때.
B2B 산업용 SaaS · 한국어 우선 · 데이터 밀도 높음 · 다크/라이트 중 라이트 우선.

## 셋업
모든 새 화면은 `colors_and_type.css`만 import 하면 토큰을 다 쓸 수 있음.
```html
<link rel="stylesheet" href="../../colors_and_type.css">
```
React 데모를 만들려면 `ui_kits/production-agent/`의 `App.jsx` 패턴(컴포넌트별 babel 스크립트 + `window` export)을 따른다.

## 색 사용 규칙
| 용도 | 토큰 | 메모 |
|---|---|---|
| 주요 액션·CTA·로고 강조 | `--kbi-sunrise-red` | 한 화면에 1–3회. 남용 금지 |
| 헤더 텍스트·사이드바 배경 | `--kbi-warm-gray` | KBI의 차분한 진회색 |
| 진행 중 작업 | `--kbi-orange` | 액티브 프로세스 |
| 완료·달성 표시 | `--kbi-champagne-gold` | 빗금 패턴과 병행 |
| 대기·중립 | `--kbi-silver` | 작업 미배정 |
| 본문 텍스트 | `--fg-1` / `--fg-2` / `--fg-3` | PwC 그레이 스케일 |
| 캔버스/카드 배경 | `--bg-canvas` / `#fff` | 카드는 무조건 흰색 |
| 보더 | `--border-1` | `--pwc-gray-200` 별칭 |

**부하율·납기율 등 임계값** — 80% 이상은 `--kbi-sunrise-red`로만 표현. 그 이하는 `--kbi-warm-gray`.

## 타이포 규칙
- 한글: Pretendard → 시스템 한글 폴백.
- 숫자: tabular-nums 필수 (`font-feature-settings: "tnum"`). 가동률·시간·m·kg 모두.
- 코드(작업번호 KBI-2418 등): `--font-mono` (IBM Plex Mono).
- 디스플레이 H1=32/40, H2=24/32, H3=18/26, Body=14/22, Caption=12/18, Micro=11/16.

## 컴포넌트 가이드

### 사이드바 (`Sidebar`)
- Warm Gray 배경 고정. 흰 글자 78% 투명도, 활성 항목만 100%.
- 활성 항목 좌측에 Sunrise Red 3px 보더.
- 너비 240px 고정.

### 상단바 (`TopBar`)
- 좌측: PwC 로고 → 구분선 → 제품명 (KBI 굵게, 보조 설명 회색).
- 우측: 검색 → 알림 → 아바타.
- 절대 사이드바 위로 올라가지 않음 (브랜드 위계: PwC가 제품 헤더에서만 노출).

### KPI 카드 (`KpiCard`)
- 좌측 4px 액센트 바 + tone(`red|orange|gold|warm`).
- 큰 숫자 30/36 700, 단위는 14/20 500.
- 델타는 ▲▼─ 화살표만 사용. 색은 up=초록 / down=Sunrise Red / flat=회색.

### Job Block (배치 셀)
- 4상태: `progress` `idle` `done` `delay`.
- `delay`만 단색 배경(Sunrise Red), 나머지는 흰 배경 + 좌측 3px 액센트.
- 코드(KBI-XXXX)는 mono, 사양은 13px, 미터는 회색 11px.
- `delay` 셀은 항상 시선의 우선순위가 가장 높아야 함.

### 간트 바
- `done`: 금색 + 빗금 (완료감).
- `progress`: 오렌지 + 우측 그라디언트로 진척률 암시.
- `normal`: Warm Gray 85%.
- `delay`: 흰 배경 + Sunrise Red 2px 외곽선 (강한 attention).
- 선택 시 Sunrise Red 2px focus ring.

### Agent 패널
- 화면 우측 고정 380px.
- 어시스턴트 버블: 흰 배경 + 보더, 좌측 하단 코너만 직각.
- 사용자 버블: Warm Gray 8% 배경, 우측 하단 코너만 직각.
- 제안 칩(suggest-chip)으로 자연어 명령 예시를 항상 노출 — 사용자가 명령어를 학습할 수 있도록.
- 액션 버튼은 버블 안에 종속(컨텍스트 안에서만 의미 있는 액션).

### 상태 Pill
`progress · idle · done · delay · ok` — 점 + 라벨. 외곽선 없음. 텍스트 셀 안 인라인 표시 용도.

## 안티패턴
- ❌ Sunrise Red를 본문/보더/대형 영역에 사용 — 경고와 액션 시그널이 희석됨.
- ❌ 임의 hex 추가 — 반드시 토큰으로 추가하고 사용.
- ❌ 그라디언트 배경, 이모지, 둥근 모서리 + 좌측 보더 액센트 컨테이너(SaaS 클리셰).
- ❌ 작업 코드(KBI-XXXX)를 sans로 표기 — 항상 mono.

## 다음에 만들 가능성이 높은 화면
- **자원 관리** — 원자재 재고 × 호기 매트릭스, 적정/부족 임계값.
- **시뮬레이션** — Before/After 비교 카드 + 지표 다이프 + Agent 자연어 시나리오 입력.
- **알림 센터** — 지연·임계 초과·자원 부족을 시간순/우선순위순으로.
이 세 화면 모두 위의 KPI Card / Status Pill / Agent Panel 패턴을 그대로 재사용한다.
