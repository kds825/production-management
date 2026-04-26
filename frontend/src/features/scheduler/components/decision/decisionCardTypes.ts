/**
 * Phase 6 Step 4-MVP — DecisionCard v2 TypeScript types.
 *
 * 백엔드 `presentation/schemas/decision_card.py::DecisionCard` 와 1:1 mirror.
 * 변경 시 반드시 양쪽 동기화 — pydantic schema 우선.
 *
 * union 표현 (HandoffBlock | WipMatchBlock | OutsourceHandoffBlock):
 *   pydantic Optional 3개 — 정확히 1개만 채움. process_key 가 분기 키.
 *
 * role-based debug omit:
 *   백엔드가 운영자 role 일 때 debug 를 None 으로 응답. frontend 는 단순
 *   `card.debug ?? null` 분기 — 권한 게이트는 백엔드 1차.
 */

export type Severity = "ok" | "save" | "warn" | "fail";
export type ProcessKey =
  | "sheath"
  | "stranding"
  | "insulation"
  | "outsource"
  | "default";

export interface DecisionLine {
  anchor: string;
  natural: string;
  constraint_id: string | null;
  severity: Severity;
  detail: Record<string, unknown>;
}

export interface ImpactCell {
  label: string;
  value: string;
  severity: Severity;
  kind: string;
}

export interface ImpactBlock {
  cells: ImpactCell[];
  duration_breakdown: Record<string, unknown>[];
}

export interface HandoffBlock {
  predecessor_label: string;
  predecessor_end_at: string | null;
  gap_minutes: number;
  self_start_at: string | null;
  self_end_at: string | null;
  successor_label: string | null;
  successor_first_slot_at: string | null;
}

export interface WipMatchBlock {
  matched_wip_id: number | null;
  wip_total_m: number;
  wip_used_m: number;
  remainder_m: number;
  loss_pct: number;
  candidates: Record<string, unknown>[];
}

export interface OutsourceHandoffBlock {
  vendor_name: string;
  order_at: string | null;
  outsource_start_at: string | null;
  outsource_end_at: string | null;
  inbound_at: string | null;
  successor_label: string | null;
  successor_first_slot_at: string | null;
  lead_days: number;
}

export interface GanttRow {
  task_id: number;
  batch_id: number;
  batch_group: string;
  label: string;
  start_at: string;
  end_at: string;
  is_self: boolean;
}

export interface BundleAlternative {
  label: string;
  color_change_min: number;
  spec_change_min: number;
  duration_min: number;
  score: number;
  is_chosen: boolean;
  rationale: string;
}

export interface Alternative {
  candidate_label: string;
  rejected_reason: string;
  severity: Severity;
  code: string;
}

export interface ProvenanceInfo {
  feedback_ids: number[];
  last_fixed_at: string | null;
}

export interface DebugBlock {
  engine: string;
  solver_status: string;
  solve_time_ms: number;
  objective_breakdown: Record<string, number>;
  audit_anchors: Record<string, unknown>[];
  schedule_task_row: Record<string, unknown>;
  solver_decision_rows: Record<string, unknown>[];
  constraint_config_version: string;
}

export interface DecisionCardV2 {
  batch_id: number;
  task_id: number | null;
  run_label: string;
  process_key: ProcessKey;
  process_label: string;
  sub_chip: string | null;
  customer_name: string;
  customer_priority: number;
  due_date: string | null;

  placement_text: string;
  placement_calc: Record<string, unknown>;

  verdict_summary: string;

  why: DecisionLine[];
  impact: ImpactBlock;
  handoff: HandoffBlock | null;
  wip_match: WipMatchBlock | null;
  outsource_handoff: OutsourceHandoffBlock | null;
  equipment_day: GanttRow[];
  equipment_day_sort_label: string;
  bundle_compare: BundleAlternative[];
  alternatives: Alternative[];

  section_default_expanded: Record<string, boolean>;
  provenance: ProvenanceInfo;
  source: "llm" | "rule-based";
  debug: DebugBlock | null;
}
