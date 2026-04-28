# README 진실 일치 + DESIGN.md 도입 — 수행계획

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` (recommended) 또는 `superpowers:executing-plans`. Steps use checkbox (`- [ ]`) syntax.

**Goal:** ① README 의 "코드 로직 — 토글해도 동작 변화 없음" ⚠️ 항목 중 토글 가능 영역(2-2 외주, 2-4 61연선, 5-5 TFR-GV)에 `is_enabled` 게이트를 실제 연결하고, ② Admin UI `priority` 슬라이더가 solver objective 를 실제로 움직이도록 마지막 갭을 메우고, ③ `/Users/jaewookim/Downloads/kbi-design-system` 자료를 **`DESIGN.md`** 컨벤션 (Google Stitch + Claude Code 표준, 2026.04 기준 423 design-system 채택) 으로 프로젝트 루트에 정착시켜 이후 작업의 단일 출처로 만든다.

**Architecture:**

- Track C → B → A 순서로 실행 (독립적·빠른 → 검증 위주 → 비즈니스 로직 변경).
- 각 트랙은 atomic commit 단위. parity-quick 11/11 + main-parity 27/27 게이트 회귀 0 후에만 다음 작업 진행 (CLAUDE.md 검증 게이트 준수).
- 추상화 도입 금지 — 함수 인자에 `enabled: bool` 추가하는 surgical edit 위주.

**Tech Stack:**

- Backend: Python 3.11, SQLAlchemy, FastAPI, OR-Tools CP-SAT
- Frontend: Next.js 16, React 19, Tailwind, Zustand
- DB: Supabase Postgres (`backend/.env::DATABASE_URL`)
- Test: pytest (backend), vitest + Playwright (frontend)

**Pre-flight (한 번만 실행):**

- [ ] `git status` 깨끗한지 확인 (refactoring 브랜치 clean 가정).
- [ ] `cd backend && source venv/bin/activate && pytest -q` — 기준선 green 확인.
- [ ] `make parity-quick` — 11/11 baseline 확인 (없으면 `cd backend && source venv/bin/activate && pytest tests/test_parity_harness.py -m parity --parity-quick -v`).
- [ ] 본 문서 끝까지 1회 통독.

---

# Track C — DESIGN.md 도입 (소요: ~45분, 의존 없음)

**왜 먼저:** 독립적, 검증 가벼움, 이후 트랙에서 UI 손댈 때 즉시 참고 가능. 사용자 요청 "design.md 이런 형태로 남기고 이후 작업할때 참고하도록" 직접 충족.

**컨벤션 근거 (웹 검색 결과, 2026.04):**

- `DESIGN.md` 는 Google Stitch 가 도입, Claude Code/Cursor/Copilot 같은 AI 에이전트가 **프로젝트 루트의 마크다운 파일을 자동으로 읽는다는 사실** 을 활용하는 컨벤션.
- 구조: **YAML frontmatter (machine-readable 토큰 요약) + Markdown prose (rationale + guardrails)** + 상세 자료는 `docs/design-system/` 등으로 링크.
- "Conventions over configuration" 패턴 — `.gitignore` / `package.json` 처럼 **위치 + 파일명 자체가 계약**.
- 핵심: **Guardrails 섹션** ("Never use Sunrise Red on body text", "Never inline hex" 등 강한 금칙어) 가 LLM 실수를 차단.

## Task C.1 — `DESIGN.md` 작성 (프로젝트 루트)

**Files:**

- Create: `DESIGN.md` (프로젝트 루트 — `/Users/jaewookim/Desktop/Project/KBI_PoC/DESIGN.md`)

- [ ] **Step 1:** `Read` 로 출처 확인:
  - `/Users/jaewookim/Downloads/kbi-design-system/project/README.md` (디자인 원칙 4개)
  - `/Users/jaewookim/Downloads/kbi-design-system/project/SKILL.md` (사용 규칙)
  - `/Users/jaewookim/Downloads/kbi-design-system/project/colors_and_type.css` (전체 토큰)

- [ ] **Step 2:** `frontend/src/lib/design-tokens/` 또는 `frontend/tailwind.config.ts` 검색해서 **현재 코드에 들어가 있는 토큰** 명단을 확인 (PR1~5 결과). 이 명단이 DESIGN.md 의 "Source of truth (code)" 섹션이 되어야 함.

- [ ] **Step 3:** `DESIGN.md` 작성. 전체 내용 (한 글자도 줄여 쓰지 말 것 — 에이전트가 그대로 읽음):

```markdown
---
project: KBI 생산계획 AI Agent
brand: PwC × KBI 공동 브랜드
audience: B2B 산업용 SaaS · 한국어 우선 · 데이터 밀도 높음 · 라이트 우선
density: high # 24/7 무인 라인 한 화면 표시 — 패딩보다 정보 밀도
status_palette: color+shape # 색맹/저조도 대응: 색만이 아닌 좌측 액센트 바 / 빗금 / 외곽선 병행
tokens_source: frontend/src/lib/design-tokens # 코드의 단일 출처
tokens_reference: docs/design-system/ # HTML 미리보기 카드 + UI kit
---

# KBI 디자인 시스템 — Single Source of Truth

이 문서는 **AI 에이전트와 사람이 모두 읽는 단일 출처**입니다.
UI 코드를 작성·수정하기 전에 반드시 이 파일을 읽고, frontmatter 의 토큰 / 아래 규칙 / 안티패턴을 따릅니다.

> 변경 우선순위: ① **이 파일 (DESIGN.md)** ② `frontend/src/lib/design-tokens/` (코드 토큰) ③ `docs/design-system/` (HTML 미리보기) ④ `frontend/CLAUDE.md` ·`AGENTS.md`.
> 코드와 본 문서가 충돌하면 **본 문서를 진실로 보고 코드를 갱신** — 그 반대 금지.

## 디자인 원칙

1. **PwC × KBI 공동 브랜드** — 베이스는 PwC 의 절제된 그레이/타이포, 액션·상태 색은 KBI 시그니처(Sunrise Red, Warm Gray, Champagne Gold).
2. **데이터 밀도 우선** — 24시간 무인 라인을 한 화면에 보여줘야 함. 패딩보다 정보 밀도, 채도보다 대비.
3. **Agent-first 인터랙션** — 자연어 채팅은 보조가 아닌 **주된 입력 수단**. 화면 우측 고정 패널 + 제안 칩.
4. **상태는 색이 아닌 색+형태로** — 색맹/저조도 모니터 대비를 위해 좌측 액센트 바, 패턴(빗금), 외곽선을 병행.

## 색 토큰 사용 규칙

| 용도                      | 토큰                           | 메모                       |
| ------------------------- | ------------------------------ | -------------------------- |
| 주요 액션·CTA·로고 강조   | `--kbi-sunrise-red`            | 한 화면에 1–3회. 남용 금지 |
| 헤더 텍스트·사이드바 배경 | `--kbi-warm-gray`              | KBI 차분한 진회색          |
| 진행 중 작업              | `--kbi-orange`                 | 액티브 프로세스            |
| 완료·달성 표시            | `--kbi-champagne-gold`         | 빗금 패턴과 병행           |
| 대기·중립                 | `--kbi-silver`                 | 작업 미배정                |
| 본문 텍스트               | `--fg-1` / `--fg-2` / `--fg-3` | PwC 그레이 스케일          |
| 캔버스/카드 배경          | `--bg-canvas` / `#fff`         | 카드는 무조건 흰색         |
| 보더                      | `--border-1`                   | `--pwc-gray-200` 별칭      |

**임계값 표시 규칙** — 부하율·납기율 80% 이상은 `--kbi-sunrise-red` 단독 사용. 그 이하는 `--kbi-warm-gray`.

## 타이포

- 한글: Pretendard → 시스템 한글 폴백.
- **숫자: tabular-nums 필수** (`font-feature-settings: "tnum"`) — 가동률·시간·m·kg 모두.
- 코드(작업번호 KBI-2418 등): `--font-mono` (IBM Plex Mono).
- 디스플레이 H1=32/40, H2=24/32, H3=18/26, Body=14/22, Caption=12/18, Micro=11/16.

## 컴포넌트 가이드

### Sidebar (`Sidebar`)

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
- 델타는 ▲▼─ 화살표만. 색은 up=초록 / down=Sunrise Red / flat=회색.

### Job Block (배치 셀)

- 4상태: `progress` `idle` `done` `delay`.
- `delay` 만 단색 배경(Sunrise Red), 나머지는 흰 배경 + 좌측 3px 액센트.
- 코드(KBI-XXXX) 는 mono, 사양 13px, 미터 회색 11px.
- `delay` 셀은 항상 시선의 우선순위가 가장 높아야 함.

### 간트 바

- `done`: 금색 + 빗금 (완료감).
- `progress`: 오렌지 + 우측 그라디언트로 진척률 암시.
- `normal`: Warm Gray 85%.
- `delay`: 흰 배경 + Sunrise Red 2px 외곽선.
- 선택 시 Sunrise Red 2px focus ring.

### Agent 패널

- 화면 우측 고정 380px.
- 어시스턴트 버블: 흰 배경 + 보더, 좌측 하단 코너만 직각.
- 사용자 버블: Warm Gray 8% 배경, 우측 하단 코너만 직각.
- 제안 칩(suggest-chip)으로 자연어 명령 예시를 항상 노출.
- 액션 버튼은 버블 안에 종속.

### Status Pill

`progress · idle · done · delay · ok` — 점 + 라벨. 외곽선 없음. 텍스트 셀 안 인라인 표시 용도.

## 안티패턴 (Guardrails — AI 에이전트 필독)

> 아래는 **절대 금지**. 본 항목 위반 시 verify-pwc-design 스킬 / 코드 리뷰에서 즉시 차단.

- ❌ **Sunrise Red 를 본문/보더/대형 영역에 사용** — 경고와 액션 시그널이 희석됨.
- ❌ **임의 hex 추가** — 반드시 `frontend/src/lib/design-tokens/` 에 토큰으로 추가하고 `var(--token-name)` 으로 사용.
- ❌ **그라디언트 배경, 이모지, 둥근 모서리 + 좌측 보더 액센트 컨테이너** (SaaS 클리셰).
- ❌ **작업 코드(KBI-XXXX) 를 sans 로 표기** — 항상 mono.
- ❌ **이탤릭** — PwC 브랜드 가이드 위반. verify-pwc-design 으로 자동 검증.
- ❌ **Tailwind arbitrary value 안에 hex** (`bg-[#FF0000]`) — 반드시 토큰 var(`bg-[var(--kbi-sunrise-red)]`).

## 다음에 만들 가능성 높은 화면

- **자원 관리** — 원자재 재고 × 호기 매트릭스, 적정/부족 임계값.
- **시뮬레이션** — Before/After 비교 카드 + 지표 다이프 + Agent 자연어 시나리오 입력.
- **알림 센터** — 지연·임계 초과·자원 부족을 시간순/우선순위순.

세 화면 모두 위의 KpiCard / Status Pill / Agent Panel 패턴을 그대로 재사용.

## 참고 자료

- 토큰 코드 출처: `frontend/src/lib/design-tokens/` (PR1~5 적용)
- HTML 미리보기 카드: `docs/design-system/preview/` (22개 토큰별 카드)
- UI kit 데모: `docs/design-system/ui_kits/production-agent/index.html`
- 풀 CSS 변수 정의: `docs/design-system/colors_and_type.css`
- 검증 스킬: `verify-pwc-design` (samildevkit 준수 자동 체크)

## 변경 이력

- 2026-04-28: 초기 작성 — `/Users/jaewookim/Downloads/kbi-design-system/` 에서 정착.
```

- [ ] **Step 4:** Verify — 다른 곳에 `DESIGN.md` 가 없는지:

```bash
find /Users/jaewookim/Desktop/Project/KBI_PoC -maxdepth 2 -iname 'DESIGN.md' 2>&1
```

Expected: 방금 만든 1개만.

## Task C.2 — 자료 이전 `Downloads/kbi-design-system/project/` → `docs/design-system/`

**Files:**

- Create: `docs/design-system/` (디렉토리)
- Move from: `/Users/jaewookim/Downloads/kbi-design-system/project/`

> 사용자 의도: "이후 작업할때 참고하도록" — 휘발성 Downloads 가 아닌 git 추적 가능한 위치로 이동. `docs/` 는 빌드 산출물에 안 섞임 (Next.js 도 `frontend/` 안만 빌드).

- [ ] **Step 1:** 디렉토리 생성 + 자료 복사 (원본은 보존):

```bash
mkdir -p /Users/jaewookim/Desktop/Project/KBI_PoC/docs/design-system
cp -R /Users/jaewookim/Downloads/kbi-design-system/project/README.md \
      /Users/jaewookim/Downloads/kbi-design-system/project/SKILL.md \
      /Users/jaewookim/Downloads/kbi-design-system/project/colors_and_type.css \
      /Users/jaewookim/Downloads/kbi-design-system/project/preview \
      /Users/jaewookim/Downloads/kbi-design-system/project/ui_kits \
      /Users/jaewookim/Downloads/kbi-design-system/project/assets \
      /Users/jaewookim/Downloads/kbi-design-system/project/fonts \
      /Users/jaewookim/Desktop/Project/KBI_PoC/docs/design-system/
```

> `_debug` / `scraps` / `uploads` / `.DS_Store` 는 의도적으로 제외 — 작업 부산물.

- [ ] **Step 2:** Verify 결과 구조:

```bash
ls /Users/jaewookim/Desktop/Project/KBI_PoC/docs/design-system/
# Expected: README.md SKILL.md assets colors_and_type.css fonts preview ui_kits
```

- [ ] **Step 3:** `docs/design-system/README.md` 의 첫 줄에 한 줄 추가 — 단일 출처는 `DESIGN.md` 임을 명시 (`Edit` 사용):

```markdown
> **단일 출처: 프로젝트 루트의 `DESIGN.md`.** 본 디렉토리는 시각 미리보기 카드 + UI kit 레퍼런스용. 토큰 정의/규칙 변경은 `DESIGN.md` 를 먼저 갱신.
```

- [ ] **Step 4:** `.DS_Store` 가 들어가지 않았는지 확인 + `.gitignore` 에 `**/.DS_Store` 가 있는지 확인:

```bash
find docs/design-system -name '.DS_Store' -delete 2>&1
grep -q 'DS_Store' .gitignore || echo "ADD to .gitignore: **/.DS_Store"
```

## Task C.3 — `CLAUDE.md` 에 DESIGN.md 참조 추가 + Frontend `AGENTS.md` 동기화

**Files:**

- Modify: `/Users/jaewookim/Desktop/Project/KBI_PoC/CLAUDE.md`
- Modify (있을 경우): `frontend/AGENTS.md`, `frontend/CLAUDE.md`

- [ ] **Step 1:** `CLAUDE.md` 의 `## Architecture` 섹션 끝(graphify 섹션 직전) 에 다음 블록을 `Edit` 로 삽입:

```markdown
## Design System

Single source of truth: **`DESIGN.md`** at project root (Google Stitch / Claude Code 컨벤션).

- UI 코드 작성·수정 전 반드시 통독.
- 토큰 코드 출처: `frontend/src/lib/design-tokens/` (PR1~5 적용 결과).
- HTML 미리보기·UI kit: `docs/design-system/`.
- 검증 스킬: `verify-pwc-design`.
```

- [ ] **Step 2:** `frontend/CLAUDE.md` 또는 `frontend/AGENTS.md` 가 있으면 동일 블록 추가:

```bash
ls -la /Users/jaewookim/Desktop/Project/KBI_PoC/frontend/{CLAUDE,AGENTS}.md 2>&1
```

(없으면 skip — Step 3 에서 생성하지 않음.)

- [ ] **Step 3:** Verify — `DESIGN.md` 가 git 에 추적되는지:

```bash
git status DESIGN.md docs/design-system/ CLAUDE.md
# Expected: DESIGN.md → new file, docs/design-system/ → new files, CLAUDE.md → modified
```

## Task C.4 — Track C 커밋

- [ ] **Step 1:** Commit:

```bash
git add DESIGN.md docs/design-system/ CLAUDE.md
git status  # 확인
git commit -m "$(cat <<'EOF'
docs(design-system): DESIGN.md 도입 + 자료 정착

- 프로젝트 루트 DESIGN.md (Google Stitch + Claude Code 컨벤션)
  YAML frontmatter (machine-readable) + 디자인 원칙 + 토큰 규칙 +
  컴포넌트 가이드 + 안티패턴 가드레일.
- docs/design-system/ 으로 HTML 미리보기 카드 / UI kit / 폰트 / colors_and_type.css 이전
  (Downloads 폴더 의존 제거, git 추적 가능).
- CLAUDE.md 에 단일 출처 참조 추가.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 2:** Verify — 다음 세션에서 Claude Code 가 자동 인식하는지 sanity check:

```bash
head -5 DESIGN.md
# Expected: --- 로 시작하는 frontmatter
```

---

# Track B — Admin UI `priority` 슬라이더 ↔ objective 실제 연결 (소요: ~90분)

**현 상태 (코드 + DB 직접 확인):**

- ✅ Supabase 에 `W-DHARD/W-CHAIN/W-IDLE/W-SLACK/W-PSEV/W-EDDP/W-EDDM/W-TCRIT/W-TURG/W-TNORM/W-TRANS` 11 row 존재, 각 `params_json={"weight": ...}` 시드 완료.
- ✅ `backend/app/application/scheduling/cp_sat/orchestrator.py:492-526` 의 `_spec_weight()` 가 DB 값을 읽어 `ModelWeights` 구성.
- ❌ **`_spec_weight()` 가 `params.weight` 만 읽고 `ConstraintConfig.priority` 컬럼 무시** → Admin UI 슬라이더 변경해도 objective 불변.
- 📝 `docs/hardcoded-weights.md:64-78` 가 이를 "Option (b) — UX-only compromise" 로 명시 → 본 트랙은 그 후속편.

**설계:** `_spec_weight()` 가 `weight × (priority / 50)` 로 스케일. `priority=50` 이면 factor=1.0 (parity 보존). `priority=0` 이면 factor=0 (effective off). `priority=100` 이면 factor=2.0.

**왜 50 기준점?** DB 스키마 default 가 `priority=50` 이고 모든 W-\* row 가 50 으로 시드됨 → factor=1.0 → **기존 11개 parity hash 와 100% 일치 보장**. 이게 결정적.

## Task B.1 — Failing test (priority change → objective change)

**Files:**

- Create: `backend/tests/test_priority_slider_objective.py`

- [ ] **Step 1:** 실패 테스트 작성:

```python
"""Track B Task 5A.priority — Admin UI priority slider 가 objective 에 영향 검증.

이전 상태: _spec_weight() 가 priority 컬럼 무시 → 슬라이더 = 장식.
변경 후: _spec_weight() 가 weight × (priority/50) 로 스케일.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.infrastructure.database import SessionLocal


@pytest.mark.parity
def test_priority_factor_at_50_preserves_baseline():
    """priority=50 = factor 1.0 = 기존 W-* row 의 weight 그대로 사용."""
    from app.application.scheduling.cp_sat.constraint_loader import (
        load_active_constraints,
    )

    db = SessionLocal()
    try:
        specs = load_active_constraints(db)
        spec_by_id = {s.constraint_id: s for s in specs}
        # 모든 W-* spec 이 priority=50 으로 시드되어 있어야 함
        for cid in [
            "W-DHARD",
            "W-IDLE",
            "W-SLACK",
            "W-PSEV",
            "W-EDDP",
            "W-EDDM",
            "W-TCRIT",
            "W-TURG",
            "W-TNORM",
            "W-TRANS",
            "W-CHAIN",
        ]:
            assert cid in spec_by_id, f"{cid} not seeded"
            assert spec_by_id[cid].priority == 50, (
                f"{cid} priority drift: {spec_by_id[cid].priority}"
            )
    finally:
        db.close()


def test_spec_weight_scales_by_priority(monkeypatch):
    """priority 변경 → _spec_weight 결과도 비례 변화."""
    from app.application.scheduling.cp_sat.orchestrator import _spec_weight_factory

    # priority=50 → factor 1.0
    fn50 = _spec_weight_factory({"X": _make_spec("X", weight=1000, priority=50)})
    assert fn50("X", fallback=999) == 1000

    # priority=100 → factor 2.0
    fn100 = _spec_weight_factory({"X": _make_spec("X", weight=1000, priority=100)})
    assert fn100("X", fallback=999) == 2000

    # priority=0 → factor 0 (effective off)
    fn0 = _spec_weight_factory({"X": _make_spec("X", weight=1000, priority=0)})
    assert fn0("X", fallback=999) == 0


def _make_spec(cid: str, *, weight: int, priority: int):
    """ConstraintSpec test fixture (frozen dataclass 가정)."""
    from app.application.scheduling.cp_sat.constraint_loader import ConstraintSpec

    return ConstraintSpec(
        constraint_id=cid,
        is_enabled=True,
        priority=priority,
        params={"weight": weight},
        # 실제 ConstraintSpec 시그니처 확인 후 보정 — 아래 Step 2 에서 점검
    )
```

- [ ] **Step 2:** `ConstraintSpec` 의 실제 시그니처 확인 후 위 fixture 를 보정:

```bash
grep -A 20 'class ConstraintSpec' backend/app/application/scheduling/cp_sat/constraint_loader.py
```

생성자 인자가 위 fixture 와 다르면 그에 맞춰 수정.

- [ ] **Step 3:** Run — 실패 확인:

```bash
cd backend && source venv/bin/activate
pytest tests/test_priority_slider_objective.py -v
```

Expected: `_spec_weight_factory` 가 아직 없으므로 ImportError 로 FAIL.

## Task B.2 — `_spec_weight_factory` 분리 + priority 스케일 적용

**Files:**

- Modify: `backend/app/application/scheduling/cp_sat/orchestrator.py:490-505`

- [ ] **Step 1:** 현재 inline `_spec_weight` 클로저를 모듈-수준 factory 로 추출 (testability 위해). `Edit` 로 다음 변경:

기존 (orchestrator.py:492-505):

```python
    def _spec_weight(cid: str, fallback: int) -> int:
        """ConstraintSpec.params['weight'] 조회 — 없으면 fallback 반환.

        Why fallback: 운영 DB 에 W-* 행이 아직 시드 안 된 환경(legacy / 테스트 DB)
        에서도 solver 가 죽지 않도록. Task 5A.2 seed 가 멱등 보장하므로 정상 환경
        에서는 항상 spec 값이 사용된다.
        """
        spec = _specs_by_id.get(cid)
        if spec is None:
            return fallback
        w = spec.params.get("weight")
        if not isinstance(w, (int, float)):
            return fallback
        return int(w)
```

변경 후 (같은 위치, 클로저는 factory 호출로 대체):

```python
    _spec_weight = _spec_weight_factory(_specs_by_id)
```

그리고 모듈 상단(`_spec_weight_factory` 가 처음 사용되기 전) 에 helper 추가 — 적절한 위치는 helpers import 직후 (`orchestrator.py:100` 근처):

```python
def _spec_weight_factory(specs_by_id: dict):
    """`_spec_weight(cid, fallback)` 클로저를 만들어 반환.

    Reads `ConstraintSpec.params["weight"]` and scales by
    `priority / 50.0` so the Admin-UI priority slider is causally wired
    to the objective. `priority=50` (DB default for all W-* rows) →
    factor 1.0 → **parity-preserving** (기존 11개 fixture hash 동일 보장).

    Falls back to the hardcoded constant if:
      - spec 자체가 없음 (W-* row 미시드 환경)
      - params["weight"] 가 숫자가 아님 (스키마 손상)
      - priority 가 None (이론상 불가 — column NOT NULL DEFAULT 50)
    """
    def _spec_weight(cid: str, fallback: int) -> int:
        spec = specs_by_id.get(cid)
        if spec is None:
            return fallback
        w = spec.params.get("weight")
        if not isinstance(w, (int, float)):
            return fallback
        priority = getattr(spec, "priority", 50) or 50
        # weight × (priority / 50) — int 캐스팅 (CP-SAT 는 정수 계수만 안전)
        return int(round(w * (priority / 50.0)))

    return _spec_weight
```

- [ ] **Step 2:** Run — Track B.1 의 fixture 테스트 통과 확인:

```bash
cd backend && source venv/bin/activate
pytest tests/test_priority_slider_objective.py::test_spec_weight_scales_by_priority -v
```

Expected: PASS.

- [ ] **Step 3:** 회귀 게이트 — parity 11/11 유지 확인:

```bash
make parity-quick
```

Expected: 11/11 PASS (모든 W-\* row 의 priority=50 이므로 factor=1.0 → 기존 hash 와 동일).

> **회귀 발생 시:** rollback. priority 컬럼이 50 이 아닌 row 가 있는지 확인:
>
> ```bash
> cd backend && source venv/bin/activate && python -c "
> from app.infrastructure.database import SessionLocal
> from sqlalchemy import text
> db = SessionLocal()
> for r in db.execute(text(\"SELECT constraint_id, priority FROM constraint_config WHERE constraint_id LIKE 'W-%' AND priority != 50\")).all():
>     print(r)"
> ```
>
> 출력 있으면 시드가 잘못된 것. `UPDATE constraint_config SET priority=50 WHERE constraint_id LIKE 'W-%';` 후 재시도.

## Task B.3 — End-to-end 검증 (priority 50 → 100 변경 → objective_value 증가 확인)

**Files:**

- Create: `backend/tests/test_priority_slider_e2e.py`

- [ ] **Step 1:** E2E 테스트 작성. `parity_db` fixture (Task 1.4 도입, `db.begin_nested()` rollback 보장) 사용:

```python
"""Track B 검증 — priority 변경이 실제 solver objective 에 영향 끼치는지.

parity_db fixture 로 SAVEPOINT 격리 → 테스트 종료 시 자동 rollback.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.parity


def test_doubling_tnorm_priority_increases_tardiness_term(parity_db, request):
    """W-TNORM priority 50→100 → tardiness 비용 2배 → objective 변화."""
    # fixture 01 nominal scenario seed
    from scripts.seed_parity_scenarios.seed_01_nominal import (
        seed as seed_01_nominal,
    )

    seed_01_nominal(parity_db)

    from app.application.scheduling.cp_sat.orchestrator import cp_sat_schedule

    # baseline
    result_baseline = cp_sat_schedule("priority-test-baseline", parity_db)
    obj_baseline = result_baseline.get("objective_value")

    # priority bump
    parity_db.execute(
        text("UPDATE constraint_config SET priority = 100 WHERE constraint_id = 'W-TNORM'")
    )

    result_bumped = cp_sat_schedule("priority-test-bumped", parity_db)
    obj_bumped = result_bumped.get("objective_value")

    # tardiness 가 zero 인 시나리오면 차이 없음 — fixture 가 past-due 를 포함한다고
    # 가정할 때 obj_bumped > obj_baseline (latency penalty 가중)
    if obj_baseline == 0:
        pytest.skip(
            "fixture 01 produces zero tardiness — switch to 02_past_due_skew"
        )

    assert obj_bumped > obj_baseline, (
        f"priority bump 후 objective_value 변화 없음 — wiring 미연결"
        f"\nbaseline={obj_baseline} bumped={obj_bumped}"
    )
```

- [ ] **Step 2:** `parity_db` fixture 시그니처 확인 후 import 보정:

```bash
grep -A 10 'def parity_db' backend/tests/conftest.py
```

(없으면 `tests/test_parity_harness.py` 의 fixture 참고하여 conftest 에 추가하거나, 본 테스트에 직접 SAVEPOINT 작성.)

- [ ] **Step 3:** Run:

```bash
pytest tests/test_priority_slider_e2e.py -v -m parity
```

Expected: PASS — past-due 가 있는 fixture 로 swap 필요시 fixture 02 또는 직접 seed 변형.

> 만약 어떤 fixture 도 tardiness 비제로가 안 나오면, 본 테스트를 **숫자 검증 대신 wiring 검증** 으로 단순화:
> `_spec_weight_factory({"W-TNORM": spec(priority=100, weight=100000)})("W-TNORM", 0) == 200000` — Track B.1 에서 이미 커버됨. 이 경우 본 테스트는 skip 처리.

## Task B.4 — README "DB 파라미터" 표 갱신 + `docs/hardcoded-weights.md` Status 갱신

**Files:**

- Modify: `README.md` (제어 수준 섹션, `priority` 정확한 동작 명시)
- Modify: `docs/hardcoded-weights.md` (Status 섹션)

- [ ] **Step 1:** `README.md:253-260` 의 "제약조건 동적 제어" 섹션 수정 — `priority` 가 가중치 스케일러로 작동함을 추가:

기존:

```markdown
### 제약조건 동적 제어 (`constraint_config` 테이블)

프론트 `/master/constraints` 페이지에서 ON/OFF 토글 가능. 제어 수준이 2단계:
```

변경 후 (블록 끝에 한 줄 추가):

```markdown
### 제약조건 동적 제어 (`constraint_config` 테이블)

프론트 `/master/constraints` 페이지에서 ON/OFF 토글 가능. 제어 수준이 2단계:

(기존 표 유지)

> **W-\* 가중치 행 (W-TNORM, W-IDLE, ...)**: `priority` 슬라이더가 `weight × (priority / 50)` 로 objective 에 반영됩니다 (priority=50 = baseline 1.0×, priority=100 = 2×, priority=0 = effective off). solver 재실행 시 즉시 반영.
```

- [ ] **Step 2:** `docs/hardcoded-weights.md` Status 섹션을 `Edit` 로 갱신:

```markdown
## Status

- [x] Task 5A.1 — inventory complete (this file).
- [x] Task 5A.2 — seed completed (Supabase 에 W-\* 11 rows 시드, `priority=50`).
- [x] Task 5A.3 — `_spec_weight_factory` 가 `params.weight` 를 DB 에서 read.
- [x] Task 5A.4 — `_spec_weight_factory` 가 `weight × (priority/50)` 로 슬라이더 wiring (2026-04-28 본 plan).
```

또한 "Priority normalisation choice" 섹션의 "Option (b) — UX-only compromise" 단락 다음에 다음 한 줄 추가:

```markdown
> **2026-04-28 갱신:** Option (b) 는 더 이상 적용 안 됨. `_spec_weight_factory` 가 `weight × (priority/50)` 로 슬라이더를 wiring. priority=50 = baseline 1.0× 보존 (모든 fixture hash 무회귀).
```

## Task B.5 — Track B 커밋

- [ ] **Step 1:** Commit:

```bash
git add backend/app/application/scheduling/cp_sat/orchestrator.py \
        backend/tests/test_priority_slider_objective.py \
        backend/tests/test_priority_slider_e2e.py \
        README.md \
        docs/hardcoded-weights.md
git commit -m "$(cat <<'EOF'
feat(solver): priority slider → objective 실제 연결 (Week 5A.4 마무리)

_spec_weight_factory 로 weight × (priority/50) 스케일 적용.
priority=50 (DB default) = factor 1.0 → 기존 11개 fixture hash 보존.
priority=100 → 2× / priority=0 → effective off.

기존 Option (b) UX-only compromise 해소: docs/hardcoded-weights.md 갱신.
README "제약조건 동적 제어" 섹션에 W-* priority 동작 명시.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 2:** main-parity 27/27 회귀 게이트:

```bash
make parity 2>&1 | tail -20
```

Expected: 11/11 (parity-quick) + 16/16 추가 시나리오 = 27/27 PASS.

---

# Track A — Stage 1 하드코딩 룰 → `is_enabled` 게이트 연결 (소요: ~3시간)

**Scope 결정 (ultrathink — 모든 "코드 로직" 항목 검토 후 분류):**

| ID                       | 룰                                         | 토글 가능성                               | 본 트랙 포함?           |
| ------------------------ | ------------------------------------------ | ----------------------------------------- | ----------------------- |
| 1-1 거래처 우선순위      | `customer_master.priority` 테이블에서 읽음 | toggle 의미 모호 (priority=0 으로 가능)   | ❌ 별도                 |
| 1-2 납기 기준            | `customer_master.due_type` 컬럼            | toggle 의미 모호                          | ❌ 별도                 |
| 2-1 재공 활용            | `wip_matching.py` — toggle off = WIP 무시  | 가능, 위험도 높음 (재고 무시 → 중복 생산) | ❌ post-pilot           |
| **2-2 외주 자동분류**    | `_is_outsource_rule` 단일 helper           | **가능, 안전** (외주 → 사내 routing 폴백) | ✅ A.1                  |
| 2-3 틀단위               | batch_group 으로 대체됨                    | 토글 무관                                 | ❌                      |
| **2-4 61연선 분리**      | `is_61strand = sq>=300 and CU`             | **가능** (off 시 normal stranding)        | ✅ A.3                  |
| 3-2 색상 묶음            | sheath_cluster + sort_key                  | 가능, sort 변경만                         | ⚠️ Tier 2 (시간 남으면) |
| 3-3 설비별 색상그룹      | A100=갈회 / A120=흑청                      | 위험 (잘못된 color → equipment 매칭)      | ❌                      |
| 4-\* (Stage 2)           | 셋업/색상교체/드럼/용접/T/P                | routing-core                              | ❌ Stage 2 별도 트랙    |
| 5-1 SQ→설비              | T6B0=≤50, 54BO=70+                         | **routing 코어, 토글 시 solver 붕괴**     | ❌                      |
| 5-2 연선방식 격리        | sort_key                                   | 가능                                      | ⚠️ Tier 2               |
| 5-3 다심 우선            | sort_key                                   | 가능                                      | ⚠️ Tier 2               |
| **5-5 TFR-GV 절연 생략** | `skip_stranding = TFR-GV and sq<=25`       | **가능** (off 시 normal stranding 적용)   | ✅ A.2                  |
| 6-\* 가동시간            | calendar_engine                            | 위험 (calendar 의존)                      | ❌                      |
| 9-1 선행공정 완료 체크   | scheduling 코어                            | routing-core                              | ❌                      |
| 10-\* 재질/4심           | routing-core                               | ❌                                        |

**Tier 1 (본 트랙 포함, 3개):** 2-2 외주, 2-4 61연선, 5-5 TFR-GV
**Tier 2 (선택):** 3-2, 5-2, 5-3 — 시간 여유 있을 때 추가 commit
**Tier 3 (의도적 제외):** routing-core 룰 — README 갱신 시 "코드 (의도적 코어 — 토글 불가)" 로 명시.

**공통 패턴 (DRY):**

- `ConstraintParams` (기존 `application/_shared/constraint_params.py`) 가 이미 모든 룰 spec 을 prefetch — 거기에 `is_enabled` 도 같이 노출.
- batch_grouper 에서 helper 호출 시 `enabled=...` keyword 추가.
- 미시드 환경(legacy DB) 폴백 = `True` (기존 동작 유지).

## Task A.0 — `ConstraintParams.load` 가 `is_enabled` 도 노출

**Files:**

- Modify: `backend/app/application/_shared/constraint_params.py`

- [ ] **Step 1:** 현 구조 파악:

```bash
grep -nE 'class ConstraintParams|def load|is_enabled' backend/app/application/_shared/constraint_params.py | head -30
```

- [ ] **Step 2:** 결과 보고 결정:
  - Case A: 이미 모든 spec 의 `is_enabled` 가 dict 형태로 노출 → A.0 skip.
  - Case B: 일부만 노출 → 누락된 ID(`2-2`, `2-4`, `5-5`) 추가.
  - Case C: 노출 안 됨 → `enabled_by_id: dict[str, bool]` 필드 추가.

(여기서 정확한 코드 변경은 Step 1 결과를 보고 결정 — 본 plan 은 인터페이스만 명시: `params.is_rule_enabled(constraint_id: str, default: bool = True) -> bool` 메서드가 존재한다고 가정)

- [ ] **Step 3:** 만약 인터페이스 추가가 필요하면, helper 작성:

```python
def is_rule_enabled(self, constraint_id: str, default: bool = True) -> bool:
    """ConstraintConfig.is_enabled 조회 — row 미존재 시 default."""
    cfg = self._configs_by_id.get(constraint_id)
    if cfg is None:
        return default
    return bool(cfg.is_enabled)
```

- [ ] **Step 4:** 단위 테스트 추가 — `backend/tests/test_constraint_params_is_enabled.py`:

```python
"""ConstraintParams.is_rule_enabled — toggle 게이트 helper."""

from app.application._shared.constraint_params import ConstraintParams
from app.infrastructure.database import SessionLocal


def test_is_rule_enabled_returns_true_when_db_enabled():
    db = SessionLocal()
    try:
        params = ConstraintParams.load(db)
        # '2-2' 는 본 plan 시작 시점에 is_enabled=True (DB 초기 상태)
        assert params.is_rule_enabled("2-2") is True
    finally:
        db.close()


def test_is_rule_enabled_returns_default_for_missing_id():
    db = SessionLocal()
    try:
        params = ConstraintParams.load(db)
        assert params.is_rule_enabled("NONEXISTENT-999", default=True) is True
        assert params.is_rule_enabled("NONEXISTENT-999", default=False) is False
    finally:
        db.close()
```

- [ ] **Step 5:** Run + commit:

```bash
cd backend && source venv/bin/activate && pytest tests/test_constraint_params_is_enabled.py -v
make parity-quick
git add backend/app/application/_shared/constraint_params.py backend/tests/test_constraint_params_is_enabled.py
git commit -m "feat(constraint-params): is_rule_enabled helper for toggle gates"
```

## Task A.1 — 2-2 외주 (`_is_outsource_rule`) 게이트

**Files:**

- Modify: `backend/app/application/ingest/batch_grouper.py:52-71` (helper signature) + `:248-249, 634-636` (call sites)
- Create: `backend/tests/test_outsource_toggle.py`

- [ ] **Step 1:** Failing test 먼저:

```python
"""2-2 외주 자동분류 토글 — is_enabled=False 시 외주 분류 미적용."""

import pytest
from sqlalchemy import text
from app.infrastructure.database import SessionLocal


@pytest.mark.parity
def test_outsource_toggle_off_skips_classification(parity_db):
    """is_enabled=False → sq<=10 주문도 사내 routing 으로 처리."""
    from app.application.ingest.batch_grouper import (
        _is_outsource_rule_with_toggle,
    )
    # toggle off
    parity_db.execute(
        text("UPDATE constraint_config SET is_enabled = FALSE WHERE constraint_id = '2-2'")
    )
    parity_db.flush()

    from app.application._shared.constraint_params import ConstraintParams
    params = ConstraintParams.load(parity_db)

    # SQ=5 (sq<=10 범위) — toggle on 이면 True, off 면 False 여야 함
    assert _is_outsource_rule_with_toggle(
        sq=5,
        product_group="일반전선",
        customer_name="일반시판",
        params=params,
    ) is False


def test_outsource_toggle_on_default_behavior(parity_db):
    """is_enabled=True (default) → 기존 룰 그대로."""
    from app.application.ingest.batch_grouper import (
        _is_outsource_rule_with_toggle,
    )
    from app.application._shared.constraint_params import ConstraintParams
    params = ConstraintParams.load(parity_db)
    assert _is_outsource_rule_with_toggle(
        sq=5, product_group="일반", customer_name="일반시판", params=params
    ) is True
    assert _is_outsource_rule_with_toggle(
        sq=100, product_group="일반", customer_name="일반시판", params=params
    ) is False
```

- [ ] **Step 2:** Run — 실패 확인 (`_is_outsource_rule_with_toggle` 미정의):

```bash
pytest backend/tests/test_outsource_toggle.py -v
```

Expected: ImportError FAIL.

- [ ] **Step 3:** `batch_grouper.py:71` 직후에 wrapper 함수 추가:

```python
def _is_outsource_rule_with_toggle(
    sq: float,
    product_group: str | None,
    customer_name: str | None,
    *,
    params,  # ConstraintParams
) -> bool:
    """`_is_outsource_rule` + `is_enabled` 토글.

    constraint 2-2 의 `is_enabled=False` 시 항상 False (외주 분류 미적용).
    누락 환경(legacy DB) 폴백 = True (기존 동작).
    """
    if not params.is_rule_enabled("2-2", default=True):
        return False
    return _is_outsource_rule(sq, product_group, customer_name)
```

- [ ] **Step 4:** Call site 갱신 — `:248-249` 와 `:634-636` 에서 `_is_outsource_rule(...)` 호출을 `_is_outsource_rule_with_toggle(..., params=constraint_params)` 로 교체. `constraint_params` 는 `:124` 에서 이미 load 됨.

`Edit` 로 정확히 두 곳:

`:248-249`:

기존:

```python
        # 외주 분류 조건 — Phase 2와 동일 (룰은 _is_outsource_rule helper)
        if _is_outsource_rule(sq, order.product_group, order.customer_name):
```

변경 후:

```python
        # 외주 분류 조건 — Phase 2와 동일 (룰은 _is_outsource_rule_with_toggle helper, 2-2 is_enabled 게이트)
        if _is_outsource_rule_with_toggle(sq, order.product_group, order.customer_name, params=constraint_params):
```

`:634-636`:

기존:

```python
        # ── 외주 자동분류 (2-2) — 룰은 _is_outsource_rule helper ───────────
        if _is_outsource_rule(sq, order.product_group, order.customer_name):
            result["outsource_count"] += 1
```

변경 후:

```python
        # ── 외주 자동분류 (2-2) — 룰은 _is_outsource_rule_with_toggle helper, is_enabled 게이트 ──
        if _is_outsource_rule_with_toggle(sq, order.product_group, order.customer_name, params=constraint_params):
            result["outsource_count"] += 1
```

`is_outsource_batch` (`:74-85`) 는 ProductionBatch 단계에서 호출되는데, **이 시점에는 db session 이 없을 수도 있음**. decision_card phrasing 에서만 호출되므로 토글 영향 없음 (이미 분류된 batch 의 is-outsource flag 조회). → 변경 불필요.

- [ ] **Step 5:** Test 통과 확인:

```bash
pytest backend/tests/test_outsource_toggle.py -v
```

Expected: 2/2 PASS.

- [ ] **Step 6:** parity-quick 회귀 0:

```bash
make parity-quick
```

(2-2 의 default `is_enabled=True` → 기존 동작 유지 → fixture hash 변동 없음)

- [ ] **Step 7:** Commit:

```bash
git add backend/app/application/ingest/batch_grouper.py backend/tests/test_outsource_toggle.py
git commit -m "feat(constraint 2-2): is_enabled 게이트 → 외주 자동분류 토글"
```

## Task A.2 — 5-5 TFR-GV 절연 생략 (`skip_stranding`) 게이트

**Files:**

- Modify: `backend/app/application/ingest/batch_grouper.py:268-269, 711-713, 737`
- Create: `backend/tests/test_tfr_gv_toggle.py`

- [ ] **Step 1:** Failing test:

```python
"""5-5 TFR-GV 절연 생략 토글 — is_enabled=False 시 normal stranding 적용."""

import pytest
from sqlalchemy import text


@pytest.mark.parity
def test_tfr_gv_toggle_off_runs_stranding(parity_db):
    """is_enabled=False → TFR-GV sq<=25 도 stranding 공정 포함."""
    parity_db.execute(
        text("UPDATE constraint_config SET is_enabled = FALSE WHERE constraint_id = '5-5'")
    )
    parity_db.flush()

    from app.application._shared.constraint_params import ConstraintParams
    params = ConstraintParams.load(parity_db)

    from app.application.ingest.batch_grouper import _should_skip_stranding
    assert _should_skip_stranding(
        product_group="TFR-GV(7C)", sq=16, params=params
    ) is False  # toggle off → skip 안 함


def test_tfr_gv_toggle_on_default_behavior(parity_db):
    from app.application._shared.constraint_params import ConstraintParams
    params = ConstraintParams.load(parity_db)
    from app.application.ingest.batch_grouper import _should_skip_stranding
    assert _should_skip_stranding(
        product_group="TFR-GV(7C)", sq=16, params=params
    ) is True
    # SQ > 25 → 토글 무관, skip 안 함
    assert _should_skip_stranding(
        product_group="TFR-GV(7C)", sq=70, params=params
    ) is False
```

- [ ] **Step 2:** Run — 실패 확인.

- [ ] **Step 3:** `batch_grouper.py` 에 helper 추가 (helper 섹션, `_is_outsource_rule_with_toggle` 직후):

```python
def _should_skip_stranding(
    product_group: str | None,
    sq: float,
    *,
    params,
) -> bool:
    """5-5 TFR-GV 절연 생략 토글 (constraint_id "5-5") + 룰.

    is_enabled=False 시 항상 False (모든 TFR-GV 가 normal stranding).
    """
    if not params.is_rule_enabled("5-5", default=True):
        return False
    return "TFR-GV" in (product_group or "").upper() and sq <= 25
```

- [ ] **Step 4:** Call site 갱신:

`:268-269` 인근:

기존:

```python
        # TFR-GV 소단면 연선 스킵
        if "TFR-GV" in (order.product_group or "").upper() and sq <= 25:
```

변경 후:

```python
        # TFR-GV 소단면 연선 스킵 (5-5 is_enabled 게이트)
        if _should_skip_stranding(order.product_group, sq, params=constraint_params):
```

`:711-713` 인근:

기존:

```python
        # TFR-GV 소단면(SQ ≤ 25): 단선 접지선 → 연선(stranding) 불필요
        # 원본 계획서에서 16SQ/25SQ TFR-GV는 연선 시트에 미표시, 시스만 표시
        skip_stranding = "TFR-GV" in (order.product_group or "").upper() and sq <= 25
```

변경 후:

```python
        # TFR-GV 소단면(SQ ≤ 25): 단선 접지선 → 연선(stranding) 불필요 (5-5 is_enabled 게이트)
        skip_stranding = _should_skip_stranding(order.product_group, sq, params=constraint_params)
```

`:737` 인근의 `if skip_stranding and process_name == "연선":` 는 변경 불필요 (이미 위에서 결정된 `skip_stranding` 값을 사용).

- [ ] **Step 5:** Test + parity-quick + commit:

```bash
pytest backend/tests/test_tfr_gv_toggle.py -v
make parity-quick
git add backend/app/application/ingest/batch_grouper.py backend/tests/test_tfr_gv_toggle.py
git commit -m "feat(constraint 5-5): is_enabled 게이트 → TFR-GV 연선 생략 토글"
```

## Task A.3 — 2-4 61연선 분리 (`is_61strand`) 게이트

**Files:**

- Modify: `backend/app/application/ingest/batch_grouper.py:342, 503, 564, 719, 723`
- Create: `backend/tests/test_61strand_toggle.py`

- [ ] **Step 1:** Failing test:

```python
"""2-4 61연선 분리 토글 — is_enabled=False 시 sq>=300 CU 도 normal stranding."""

import pytest
from sqlalchemy import text


@pytest.mark.parity
def test_61strand_toggle_off_uses_normal_stranding(parity_db):
    parity_db.execute(
        text("UPDATE constraint_config SET is_enabled = FALSE WHERE constraint_id = '2-4'")
    )
    parity_db.flush()

    from app.application._shared.constraint_params import ConstraintParams
    params = ConstraintParams.load(parity_db)

    from app.application.ingest.batch_grouper import _is_61strand_rule
    assert _is_61strand_rule(
        sq=400, conductor_material="CU", params=params
    ) is False


def test_61strand_toggle_on_default_behavior(parity_db):
    from app.application._shared.constraint_params import ConstraintParams
    params = ConstraintParams.load(parity_db)

    from app.application.ingest.batch_grouper import _is_61strand_rule
    assert _is_61strand_rule(sq=400, conductor_material="CU", params=params) is True
    assert _is_61strand_rule(sq=400, conductor_material="AL", params=params) is False
    assert _is_61strand_rule(sq=70, conductor_material="CU", params=params) is False
```

- [ ] **Step 2:** Run — 실패 확인.

- [ ] **Step 3:** `batch_grouper.py` helper 추가:

```python
def _is_61strand_rule(
    sq: float,
    conductor_material: str | None,
    *,
    params,
) -> bool:
    """2-4 61연선 분리 (constraint_id '2-4') + is_enabled 게이트.

    Default rule: sq >= 300 and conductor_material == "CU".
    is_enabled=False → 항상 False (sq>=300 CU 도 normal stranding).
    """
    if not params.is_rule_enabled("2-4", default=True):
        return False
    return sq >= 300 and conductor_material == "CU"
```

- [ ] **Step 4:** Call site 갱신 — 5 곳. 정확한 매칭은 Edit 의 `replace_all` 가 위험하므로 **각 라인별로 정확히 grep 후 Edit**.

```bash
grep -n 'is_61strand' backend/app/application/ingest/batch_grouper.py
```

각 라인에 대해:

`:342` (정의):

기존:

```python
        is_61strand_g = sq >= 300
```

변경 후:

```python
        # 2-4 is_enabled 게이트 (CU 한정은 helper 내부)
        is_61strand_g = _is_61strand_rule(sq, conductor_material_o, params=constraint_params)
```

> 단, `conductor_material_o` 가 그 시점에 정의돼있어야 함. `:342` 인근을 읽어보고 변수명 확인 후 보정. 만약 `conductor_material_o` 가 더 아래에서 정의된다면, **선언 위치를 위로 이동** 또는 라인을 약간 지연시켜야 함.

`:503` 와 `:564`:

기존:

```python
            if is_61strand_g and conductor_material_o == "CU" and not skip_strand_work:
            ...
            if is_61strand_g and conductor_material_o == "AL" and not skip_strand_work:
```

`is_61strand_g` 가 이미 helper 내부에서 CU 체크함 → AL 분기는 자연스럽게 항상 False 됨 (helper 가 CU 만 True 반환). 의도와 일치 — 변경 불필요.

> ⚠️ 단, AL 분기가 다른 의미를 가진다면 설계 잘못. 코드를 한 번 더 정독:

```bash
grep -B 2 -A 5 ':\s*\(503\|564\)' backend/app/application/ingest/batch_grouper.py 2>&1 || \
sed -n '500,570p' backend/app/application/ingest/batch_grouper.py
```

(`is_61strand` 라는 변수 이름이 CU/AL 양쪽에서 쓰인다면, 변수 의미가 "300+ 대단면" 일 수 있음 → 함수명 분리 필요할 수 있음. **필독 후 결정**.)

`:719`, `:723`:

```bash
sed -n '715,730p' backend/app/application/ingest/batch_grouper.py
```

기존 두 라인 (line 723 의 `# noqa: F841` 가 단서 — 변수 미사용 가능성):

```python
        is_61strand = sq >= 300 and conductor_material == "CU"
        ...
        is_61strand = sq >= 300 and conductor_material == "CU"  # noqa: F841
```

`is_61strand_rule` 로 교체. 단 두 줄이 사실상 중복이라면 한 줄 삭제.

- [ ] **Step 5:** Test + parity-quick:

```bash
pytest backend/tests/test_61strand_toggle.py -v
make parity-quick  # 회귀 0 확인 (default is_enabled=True → 기존 동작)
```

- [ ] **Step 6:** main-parity 27/27 게이트 (61연선은 더 무거운 케이스 — full parity 권장):

```bash
make parity 2>&1 | tail -10
```

Expected: 27/27 PASS.

- [ ] **Step 7:** Commit:

```bash
git add backend/app/application/ingest/batch_grouper.py backend/tests/test_61strand_toggle.py
git commit -m "feat(constraint 2-4): is_enabled 게이트 → 61연선 분리 토글"
```

## Task A.4 — README "코드 로직" 표 갱신

**Files:**

- Modify: `README.md:255-260` 와 `:285-330` 의 제어 컬럼

- [ ] **Step 1:** `README.md:255-260` 의 표 — "코드 로직" 행 메모 갱신:

기존:

```markdown
| **코드 로직** | 로직은 코드에 하드코딩, `is_enabled`는 미참조 | 2-2 외주, 2-4 61연선, 5-5 TFR-GV | ⚠️ 토글해도 동작 변화 없음 (코드 수정 필요) |
```

변경 후:

```markdown
| **코드 로직 (toggle 가능)** | 로직은 코드, `is_enabled` 게이트 통과 후 적용 | 2-2 외주, 2-4 61연선, 5-5 TFR-GV | ✅ OFF → 룰 비활성, 코드 정의된 본문은 코드 변경 필요 |
| **코드 로직 (코어, 토글 불가)** | routing/scheduling 코어 로직 — 토글 시 solver 붕괴 | 5-1 SQ→설비, 4-_ 시간/속도, 6-_ 가동시간, 9-1 선행공정, 10-2/3/4 재질·라우팅 | ⚠️ 변경 시 반드시 코드 수정 |
```

- [ ] **Step 2:** `:285-330` 의 큰 표 — `제어` 컬럼이 "코드" 인 행 중 본 트랙 대상(2-2, 2-4, 5-5) 을 **"DB-toggle"** 로 변경:

(정확한 텍스트 매치 후 Edit — 각 행마다 search/replace)

| ID  | 변경 전 | 변경 후 |
| --- | ------- | ------- | --- | --- | ------------- | --- |
| 2-2 | `       | 코드    | `   | `   | **DB-toggle** | `   |
| 2-4 | `       | 코드    | `   | `   | **DB-toggle** | `   |
| 5-5 | `       | 코드    | `   | `   | **DB-toggle** | `   |

- [ ] **Step 3:** Verify diff:

```bash
git diff README.md | head -60
```

- [ ] **Step 4:** Commit:

```bash
git add README.md
git commit -m "docs(readme): 2-2/2-4/5-5 토글 활성화 반영"
```

## Task A.5 — UI E2E smoke (Playwright) — `/master/constraints` 토글 → Stage 1 재실행 → 동작 변화 확인

**Files:**

- Create: `frontend/tests/e2e/constraint-toggle-smoke.spec.ts` (Playwright)

- [ ] **Step 1:** 현재 frontend Playwright 셋업 확인:

```bash
ls frontend/tests/e2e/ 2>/dev/null && cat frontend/playwright.config.* 2>/dev/null | head -30
```

(Playwright 설치 안 됐으면 본 task 는 manual smoke 로 대체.)

- [ ] **Step 2:** Manual smoke (자동화 미설치 시):

수동 절차 — `docs/qa-manual-smoke-2-2-5-5-2-4.md` 에 기록:

```
1. backend + frontend 띄움 (uvicorn + npm run dev).
2. /master/constraints 진입.
3. 2-2 외주 자동분류 토글 → OFF.
4. /plan-register 에서 demo_wip_data.xlsx + 3.25진행및대기.xls 업로드, "작업지시서 생성".
5. /scheduling-review 에서 외주 시트가 비어있는지 확인 (또는 outsource_count=0).
6. /master/constraints 에서 2-2 토글 → ON 복원.
7. 같은 절차 반복 → 외주 시트에 sq<=10 주문 다시 등장 확인.
8. 2-4 / 5-5 도 동일 패턴 반복.
```

- [ ] **Step 3:** 결과 사진/로그를 `docs/qa-manual-smoke-2-2-5-5-2-4.md` 에 첨부 (PoC 단계 — 스크린샷 1~2장이면 충분).

- [ ] **Step 4:** Commit (smoke 결과 문서):

```bash
git add docs/qa-manual-smoke-2-2-5-5-2-4.md
git commit -m "docs(qa): 2-2/2-4/5-5 토글 manual smoke 결과"
```

---

# Final — 전체 검증 + 머지

## Task F.1 — 전체 검증 게이트

- [ ] **Step 1:** 전 backend test:

```bash
cd backend && source venv/bin/activate && pytest -q 2>&1 | tail -10
```

Expected: 전체 PASS.

- [ ] **Step 2:** Full parity:

```bash
make parity 2>&1 | tail -10
```

Expected: 27/27.

- [ ] **Step 3:** Frontend lint + typecheck (touched 파일이 있다면):

```bash
cd frontend && npm run lint && npm run typecheck
```

- [ ] **Step 4:** `git log --oneline -15` 로 커밋 시퀀스 확인:

```
docs(qa): 2-2/2-4/5-5 토글 manual smoke 결과
docs(readme): 2-2/2-4/5-5 토글 활성화 반영
feat(constraint 2-4): is_enabled 게이트 → 61연선 분리 토글
feat(constraint 5-5): is_enabled 게이트 → TFR-GV 연선 생략 토글
feat(constraint 2-2): is_enabled 게이트 → 외주 자동분류 토글
feat(constraint-params): is_rule_enabled helper for toggle gates
feat(solver): priority slider → objective 실제 연결
docs(design-system): DESIGN.md 도입 + 자료 정착
```

## Task F.2 — Memory 갱신 (Claude 자동 메모리)

- [ ] **Step 1:** 다음 메모리 갱신 (`MEMORY.md` 인덱스 + 개별 파일):
  - `feedback_design_md_truth.md` — DESIGN.md 가 단일 출처. UI 작업 시 본 파일 참조 필수.
  - `project_constraint_toggle_2026_04_28.md` — 2-2/2-4/5-5 가 DB-toggle 로 승격, priority 슬라이더 wiring 완료.

(메모리 작성은 본 plan 의 마지막 atomic action — 변경 무회귀 확인 후.)

## Task F.3 — Plan 자체 archive

- [ ] **Step 1:** 본 plan 의 `Status` 섹션 추가:

```markdown
## Status

- [x] Track C (DESIGN.md) — 2026-04-28 commit <sha>
- [x] Track B (priority slider wiring) — 2026-04-28 commit <sha>
- [x] Track A (2-2/2-4/5-5 toggle) — 2026-04-28 commit <sha>
```

- [ ] **Step 2:** Commit:

```bash
git add docs/plans/2026-04-28-readme-truth-and-design-md.md
git commit -m "docs(plans): mark 2026-04-28 plan complete"
```

---

## Self-Review

**1. Spec coverage:**

- "전부다" → A+B+C 모두 포함. ✓
- "C는 doc에 포함하는게 아니라 design.md 이런 형태로 남기고" → DESIGN.md 가 프로젝트 루트, `docs/design-system/` 은 보조 자료로 분리. ✓
- "웹검색해서 사용법 말해봐" → Task C 도입부에 컨벤션 근거 명시 (Google Stitch + Claude Code, 423 채택, 2026.04). ✓
- "README의 ⚠️ 항목" → Track A 가 정확히 그 3 항목 (2-2/2-4/5-5) 을 다룸. ✓

**2. Placeholder scan:** 없음. 모든 코드 블록이 실제 변경할 텍스트.

- A.0 Step 2 Case A/B/C 는 "결정 후 진행" 분기지만 인터페이스는 `is_rule_enabled(...)` 로 못박음 → 수행자가 즉시 진행 가능.
- A.3 Step 4 의 `:503/:564` 분석은 "필독 후 결정" — 코드 의미가 작업자에게 의존하는 진짜 분기점이라 placeholder 가 아닌 합법적 결정 포인트.

**3. Type consistency:**

- `params: ConstraintParams` 인터페이스: A.0 (정의) → A.1, A.2, A.3 (consume) ✓
- `_spec_weight_factory(specs_by_id: dict)` 인터페이스: B.2 (정의) → B.1 (test consume) ✓
- `is_rule_enabled(constraint_id: str, default: bool = True)` 시그니처: A.0 (정의) → 모든 helper (consume) ✓

---

## Execution Handoff

**Plan complete and saved to `docs/plans/2026-04-28-readme-truth-and-design-md.md`. 두 가지 실행 옵션:**

**1. Subagent-Driven (recommended)** — 트랙별 (C → B → A) subagent 디스패치, 검토 사이 atomic commit. 본 plan 의 트랙이 독립적이라 병렬도 가능 (단 git index 충돌 방지 위해 worktree 분리 필요).

**2. Inline Execution** — 본 세션에서 `executing-plans` 으로 task-by-task 진행, parity-quick 게이트마다 체크포인트.

**어느 방식으로 진행할까요?**
