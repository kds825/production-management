/**
 * Phase 6 Step 4-MVP — DecisionCardV2View contract tests (props-only).
 *
 * vitest env=node — renderToStaticMarkup 만 사용. 도메인 정확성 invariant
 * (시스 카드에 압축연선 부재 / 외주 카드 outsource_handoff 노출 / role-based
 * debug omit) 을 SSR HTML 텍스트 assertion 으로 검증.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { DecisionCardV2View } from "../components/decision/DecisionCardV2";
import type { DecisionCardV2 as Data } from "../components/decision/decisionCardTypes";

function mkData(over: Partial<Data> = {}): Data {
  return {
    batch_id: 1001,
    task_id: 5001,
    run_label: "run-2026-04-26",
    process_key: "sheath",
    process_label: "저압시스 (A120)",
    sub_chip: "A120 묶음 (작은 설비)",
    customer_name: "한국전력",
    customer_priority: 2,
    due_date: "2026-05-01T18:00:00",
    placement_text: "시스 3호기에 04-29(수) 14:00 — 22:30 배치",
    placement_calc: {},
    verdict_summary: "✓ 정상 — 색상교체 0분, 납기 +1.2일",
    why: [
      {
        anchor: "why_5-1",
        natural: "시스 3호기는 100~300SQ 시스 작업이 가능합니다 → 150SQ 적합",
        constraint_id: "5-1",
        severity: "ok",
        detail: {},
      },
    ],
    impact: {
      cells: [
        {
          label: "🎨 색상교체",
          value: "0분",
          severity: "save",
          kind: "color_change",
        },
        {
          label: "📅 납기 여유",
          value: "+1.2일",
          severity: "save",
          kind: "due_slack_days",
        },
      ],
      duration_breakdown: [],
    },
    handoff: {
      predecessor_label: "저압절연",
      predecessor_end_at: "2026-04-29T13:00:00",
      gap_minutes: 60,
      self_start_at: "2026-04-29T14:00:00",
      self_end_at: "2026-04-29T22:30:00",
      successor_label: null,
      successor_first_slot_at: null,
    },
    wip_match: null,
    outsource_handoff: null,
    equipment_day: [],
    equipment_day_sort_label:
      "① 납기 가까운 순 → ② 전공정 ready 시각 → ③ 색상 인접 순",
    bundle_compare: [],
    alternatives: [],
    section_default_expanded: {
      section_1: true,
      section_2: false,
      section_3: false,
    },
    provenance: { feedback_ids: [], last_fixed_at: null },
    source: "rule-based",
    debug: null,
    ...over,
  };
}

describe("DecisionCardV2View — sheath 카드", () => {
  it("Header / VerdictSummary / Why 자연어 노출", () => {
    const html = renderToStaticMarkup(<DecisionCardV2View data={mkData()} />);
    expect(html).toContain("저압시스 (A120)");
    expect(html).toContain("A120 묶음");
    expect(html).toContain("✓ 정상 — 색상교체 0분");
    expect(html).toContain("150SQ 적합");
  });

  it("도메인 정확성 — 시스 카드에 '압축연선' / '7연선' 부재", () => {
    const html = renderToStaticMarkup(<DecisionCardV2View data={mkData()} />);
    expect(html).not.toContain("압축연선");
    expect(html).not.toContain("7연선");
  });

  it("Impact severity → token mapping (save = success bg)", () => {
    const html = renderToStaticMarkup(<DecisionCardV2View data={mkData()} />);
    // section_2 default closed → details summary visible 만 확인
    expect(html).toContain("어떤 영향이 있나요");
  });
});

describe("DecisionCardV2View — 외주 카드", () => {
  it("OutsourceHandoffBlock 렌더 + 헤더 ribbon '외주' 노출", () => {
    const data = mkData({
      process_key: "outsource",
      process_label: "외주",
      sub_chip: null,
      handoff: null,
      outsource_handoff: {
        vendor_name: "협력사 H공장",
        order_at: "2026-04-29T08:00:00",
        outsource_start_at: "2026-04-30T08:00:00",
        outsource_end_at: "2026-05-02T18:00:00",
        inbound_at: "2026-05-03T08:00:00",
        successor_label: "저압시스",
        successor_first_slot_at: "2026-05-03T10:00:00",
        lead_days: 3,
      },
      section_default_expanded: { section_3: true },
    });
    const html = renderToStaticMarkup(<DecisionCardV2View data={data} />);
    expect(html).toContain("📦 외주");
    expect(html).toContain("협력사 H공장");
    expect(html).toContain("3일");
  });
});

describe("DecisionCardV2View — TFR-GV 변형", () => {
  it("predecessor_label 이 '연선 (절연 스킵 · TFR-GV)' 으로 노출", () => {
    const data = mkData({
      handoff: {
        predecessor_label: "연선 (절연 스킵 · TFR-GV)",
        predecessor_end_at: "2026-04-29T13:00:00",
        gap_minutes: 30,
        self_start_at: "2026-04-29T14:00:00",
        self_end_at: "2026-04-29T22:30:00",
        successor_label: null,
        successor_first_slot_at: null,
      },
      section_default_expanded: { section_3: true },
    });
    const html = renderToStaticMarkup(<DecisionCardV2View data={data} />);
    expect(html).toContain("연선 (절연 스킵 · TFR-GV)");
  });
});

describe("DecisionCardV2View — role-based debug omit", () => {
  it("debug == null → DOM 에 debug section 부재 (Step 4-MVP 는 미렌더)", () => {
    const html = renderToStaticMarkup(<DecisionCardV2View data={mkData()} />);
    expect(html).not.toContain("solver_status");
    expect(html).not.toContain("schedule_task_row");
  });
});
