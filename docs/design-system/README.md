# KBI 생산계획 AI Agent — 디자인 시스템 / UI 키트

> **단일 출처: 프로젝트 루트의 [`DESIGN.md`](../../DESIGN.md).** 본 디렉토리는 시각 미리보기 카드 + UI kit 레퍼런스용 (samildevkit handoff 원본 보존). 토큰 정의/규칙 변경은 `DESIGN.md` 를 먼저 갱신.

PwC × KBI 공동 브랜드. 24/7 무인 생산현장의 의사결정을 보조하는 AI Agent용 디자인 시스템.

## 구조

```
colors_and_type.css        ← 모든 토큰의 단일 출처 (CSS 변수)
fonts/                     ← (선택) 로컬 폰트 — 없으면 시스템 폴백
assets/
  pwc-logo.svg
  kbi-symbol.png
  kbi-signature.png
preview/                   ← 디자인 시스템 카드 (개별 토큰/컴포넌트)
ui_kits/production-agent/  ← 동작하는 데모 (배치 뷰 + 간트차트)
```

## 디자인 원칙

1. **PwC × KBI 공동 브랜드** — 베이스는 PwC의 절제된 그레이/타이포, 액션·상태 색은 KBI 시그니처(Sunrise Red, Warm Gray, Champagne Gold).
2. **데이터 밀도 우선** — 24시간 무인 라인을 한 화면에 보여줘야 함. 패딩보다 정보 밀도, 채도보다 대비.
3. **Agent-first 인터랙션** — 자연어 채팅은 보조가 아닌 주된 입력 수단. 화면 우측 고정 패널 + 제안 칩.
4. **상태는 색이 아닌 색+형태로** — 색맹/저조도 모니터 대비를 위해 좌측 액센트 바, 패턴(빗금), 외곽선을 병행.

## 토큰 사용

- 색·타이포·간격·그림자는 모두 `colors_and_type.css`의 변수만 사용. 직접 hex 값 작성 금지.
- 액션 색 = `--kbi-sunrise-red`. 본문 텍스트 = `--fg-1`. 캔버스 = `--bg-canvas`.

## 데모 실행

`ui_kits/production-agent/index.html`를 열면 좌측 네비에서 **배치 뷰** ↔ **간트차트** 전환 가능.

자세한 사용 가이드는 [SKILL.md](./SKILL.md) 참고.
