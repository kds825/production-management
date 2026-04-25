/**
 * Week 4 Task 4B.3 — Decision Card view contract tests.
 *
 * 환경 제약 (Toast.test.tsx 가 동일 한계 기록):
 *  vitest environment="node" + Testing Library 부재 → DOM/이벤트 검증 불가.
 *  "use client" 컴포넌트라도 hook/state 미사용이면 renderToStaticMarkup 으로
 *  SSR HTML 검증이 가능. DecisionCardView 는 의도적으로 props-only 로 분리됨.
 *
 * 본 스위트는 6개 분기에 대한 view contract (텍스트/구조/data-testid) 만 검증한다.
 *  - loading / no-trace / happy / empty contributions / manual / template-fallback
 *  - timeout/error 의 onRetry 콜백 동작은 e2e Playwright 로 커버.
 */

import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  DecisionCardView,
  type DecisionCardViewState,
} from "../components/DecisionCard";
import type { DecisionData } from "../hooks/useDecisionCard";

function mkData(over: Partial<DecisionData> = {}): DecisionData {
  return {
    batch_id: "B-1001",
    run_id: "run-uuid-1",
    run_label: "run-2026-04-25",
    solver_status: "OPTIMAL",
    objective_value: 12345,
    assigned_equipment_id: "EX-B100",
    assigned_start: "2026-04-25T08:00:00",
    assigned_end: "2026-04-25T16:00:00",
    contributions: [
      {
        constraint_id: "c_tardiness",
        korean_name: "납기",
        weight_applied: 48000,
        bound: null,
        delta_if_removed: null,
      },
      {
        constraint_id: "c_color",
        korean_name: "컬러",
        weight_applied: 8500,
        bound: null,
        delta_if_removed: null,
      },
      {
        constraint_id: "c_setup",
        korean_name: "셋업",
        weight_applied: 1200,
        bound: null,
        delta_if_removed: null,
      },
    ],
    binding_hard_constraints: [],
    is_manually_adjusted: false,
    manual_override: null,
    llm_summary: "이 배치는 납기 압박이 가장 큰 비중을 차지했습니다.",
    llm_was_template: false,
    ...over,
  };
}

describe("DecisionCardView", () => {
  it("renders skeleton when loading", () => {
    const html = renderToStaticMarkup(
      <DecisionCardView state={{ kind: "loading" }} />,
    );
    expect(html).toContain('data-testid="decision-card-loading"');
    expect(html).toContain('data-testid="decision-card-skeleton-zone1"');
    expect(html).toContain('data-testid="decision-card-skeleton-zone2"');
    expect(html).toContain('data-testid="decision-card-skeleton-zone3"');
    // 토큰 사용 검증 (raw hex 금지 가드).
    expect(html).toContain("var(--color-bg-muted)");
  });

  it("renders no-trace message when 404", () => {
    const html = renderToStaticMarkup(
      <DecisionCardView state={{ kind: "no-trace" }} />,
    );
    expect(html).toContain('data-testid="decision-card-no-trace"');
    expect(html).toContain("이 배치는 솔버 트레이스가 없습니다");
    expect(html).toContain("manual placement");
  });

  it("renders all 3 zones on happy path", () => {
    const data = mkData();
    const state: DecisionCardViewState = { kind: "ok", data };
    const html = renderToStaticMarkup(<DecisionCardView state={state} />);

    // Zone 1 — equipment + pill
    expect(html).toContain('data-testid="decision-card-zone1"');
    expect(html).toContain("EX-B100");
    expect(html).toContain('data-testid="decision-card-pill"');
    expect(html).toContain("이동 가능"); // green pill (no binding constraints)
    expect(html).toContain("var(--color-success)");

    // Zone 2 — bar chart with all 3 contributions in Korean
    expect(html).toContain('data-testid="decision-card-zone2"');
    expect(html).toContain("납기");
    expect(html).toContain("컬러");
    expect(html).toContain("셋업");
    // Weight formatting — 48000 → "48K", 8500 → "8.5K", 1200 → "1.2K"
    expect(html).toContain("48K");
    expect(html).toContain("8.5K");
    expect(html).toContain("1.2K");

    // Zone 3 — LLM summary
    expect(html).toContain('data-testid="decision-card-zone3"');
    expect(html).toContain("이 배치는 납기 압박이");

    // Default variant (not manual)
    expect(html).toContain('data-manual="false"');
    expect(html).not.toContain('data-testid="decision-card-manual-override"');
    expect(html).not.toContain('data-testid="decision-card-template-badge"');
  });

  it("renders empty-contributions message when contributions=[]", () => {
    const data = mkData({ contributions: [] });
    const html = renderToStaticMarkup(
      <DecisionCardView state={{ kind: "ok", data }} />,
    );
    expect(html).toContain('data-testid="decision-card-empty-contributions"');
    expect(html).toContain("활성 제약 없음");
    expect(html).toContain("자유 배정");
  });

  it("renders yellow variant + manual_override disclosure when is_manually_adjusted=true", () => {
    const data = mkData({
      is_manually_adjusted: true,
      manual_override: {
        change_set_id: "cs-77",
        reason: "고객 요청으로 우선 배치",
        snapshot_before: {
          assigned_equipment_id: "EX-A050",
          assigned_start: "2026-04-26T09:00:00",
          assigned_end: "2026-04-26T17:00:00",
        },
      },
    });
    const html = renderToStaticMarkup(
      <DecisionCardView state={{ kind: "ok", data }} />,
    );
    expect(html).toContain('data-manual="true"');
    // 노란 보더 — token 사용 확인 (raw hex 금지)
    expect(html).toContain("var(--color-warning)");
    expect(html).toContain('data-testid="decision-card-manual-override"');
    expect(html).toContain("수동 조정 내역");
    expect(html).toContain("고객 요청으로 우선 배치");
    expect(html).toContain("EX-A050");
  });

  it("renders template-fallback badge when llm_was_template=true", () => {
    const data = mkData({ llm_was_template: true });
    const html = renderToStaticMarkup(
      <DecisionCardView state={{ kind: "ok", data }} />,
    );
    expect(html).toContain('data-testid="decision-card-template-badge"');
    expect(html).toContain("템플릿");
  });

  it("pill = 제약됨 with binding constraint names when binding_hard_constraints non-empty", () => {
    const data = mkData({
      binding_hard_constraints: [
        { constraint_id: "h_capacity", korean_name: "설비용량" },
        { constraint_id: "h_route", korean_name: "공정경로" },
      ],
    });
    const html = renderToStaticMarkup(
      <DecisionCardView state={{ kind: "ok", data }} />,
    );
    expect(html).toContain("제약됨: 설비용량, 공정경로");
    expect(html).toContain("var(--color-warning)");
  });

  it("pill = 불가능 (red) when solver_status=INFEASIBLE", () => {
    const data = mkData({ solver_status: "INFEASIBLE" });
    const html = renderToStaticMarkup(
      <DecisionCardView state={{ kind: "ok", data }} />,
    );
    expect(html).toContain("불가능");
    expect(html).toContain("var(--color-danger)");
  });
});
