export interface Equipment {
  id: string;
  name: string;
  process_type: string;
  capabilities: string[];
  capacity_tons_per_month: number;
  range_min?: number; // 최소 SQ (mm²)
  range_max?: number; // 최대 SQ (mm²)
  material_limit?: string; // "CU" | "AL" | "ALL"
  status: string;
}

export interface ScheduleTask {
  id: string;
  order_id: string;
  equipment_id: string;
  product: string;
  spec: string;
  core_count: number;
  color: string;
  start: Date;
  end: Date;
  volume_m: number;
  line_speed_m_per_min: number;
  // open string — 알려진 값: 'normal' | 'urgent' | 'critical'
  priority: string;
  // open string — 알려진 값: 'planned' | 'in_progress' | 'completed' | 'delayed'
  status: string;
  delivery_date?: Date;
  process_step?: number;
  /**
   * 공정명 (batch.process_name) — diff overlay 의 stable key 구성요소.
   * "(order_id, sales_order_line, process_name, process_step)" 튜플로
   * /api/pipeline/runs/compare 응답의 task_id 와 매칭된다.
   * 없으면 diff 매칭 실패 → overlay 미표시 (silent fail).
   */
  process_name?: string;
  /** 수주 라인 번호 (batch.sales_order_line) — 위 stable key 의 두번째 구성요소. */
  sales_order_line?: number;
  predecessors: string[];
  notes: string;
  changeover_min: number;
  /** 규격교체 시간 (분) — SpeedMaster 기준 */
  setup_time_min?: number;
  /** 색상교체 시간 (분) — 시스 공정에서 색상 변경 시 */
  color_change_min?: number;
  /** 거래처명 — production_batch.customer_name에서 가져옴 */
  customer?: string;
  /** 배치 그룹 식별자 — 같은 간트 블록에 묶인 수주 그룹 */
  batch_group?: string;
  /** 도체 재질 — CU, AL */
  material?: string;
  /** production_batch.batch_id — 상태 변경 API 호출에 필요 */
  batch_id?: number;
  /** production_batch.wip_matched_id — WIP 매칭된 배치는 unassign 불가 (Task 5.2 disabled 판정) */
  wip_matched_id?: number | null;
  /** schedule_task 생성 시각 — 증분 업데이트 후 신규 배치 강조 표시에 사용 */
  created_at?: Date;
  /** 도체 단면적 mm² — SQ별 색상 구분용 */
  sq_mm2?: number;
  /** 헤더 배치 drum_count — 틀 수 표시용 */
  lot_count?: number;
  /** 시스 배치 묶인 규격 목록 (백엔드에서 자동 채움, SH-* 설비 전용) */
  spec_list?: string[] | null;
}

export interface ConstraintViolation {
  // open string — 알려진 값: 'overlap' | 'equipment_capability' | 'precedence' | 'delivery' | 'process_route'
  type: string;
  severity: "error" | "warning";
  message: string;
  task_id: string;
  related_task_id?: string;
}

export type ZoomLevel = "week" | "day" | "hour";

/** 라인 속도 데이터 (API: GET /api/line-speeds) */
export interface LineSpeedEntry {
  spec: string;
  speeds: Record<string, number>;
}

export type ContextAction =
  | "create_task"
  | "edit_task"
  | "delete_task"
  | "copy_task"
  | "paste_task"
  | "move_tasks"
  | "zoom_selection";

export type ViewFilterType = "all" | "process" | "equipment" | "voltage";

/** 수주 (미배정 주문) */
export interface Order {
  id: string;
  order_number: string;
  product: string;
  spec: string;
  core_count: number;
  color: string;
  customer: string;
  delivery_date: string;
  total_length_m: number;
  // open string — 알려진 값: 'normal' | 'urgent' | 'critical'
  priority: string;
  /** 생산계획등록에서 배정 실패 시 원본 equipment_group 보존 */
  equipment_group?: string;
}

/** 컨텍스트 메뉴 상태 */
export interface ContextMenuState {
  x: number;
  y: number;
  type: "empty" | "task";
  taskId?: string;
  /** 빈 영역 우클릭 시 해당 설비 row / 날짜 prefill 용 */
  equipmentId?: string;
  clickTime?: Date;
}

/** 작업 폼 모달 상태 */
export interface TaskFormModalState {
  isOpen: boolean;
  mode: "create" | "edit";
  taskId?: string;
  prefill?: {
    equipmentId?: string;
    start?: Date;
    end?: Date;
  };
}

/** 스케줄 버전 스냅샷 */
export interface ScheduleVersion {
  id: string;
  label: string;
  created_at: Date;
  tasks: ScheduleTask[];
}

/** 생산 배치 (생산계획등록에서 확정된 배치 항목) */
/** Cross-process cascade preview: 선행 공정 이동 시 후행 공정에 미치는 영향 */
export interface CascadeAffectedTask {
  task_id: string;
  old_start: string;
  old_end: string;
  new_start: string;
  new_end: string;
  process: string;
  equipment: string;
  reason: string;
}

export interface CascadeConflict {
  task_id: string;
  conflict_with: string;
  equipment: string;
  overlap_min: number;
  resolution: string;
}

export interface CascadePreview {
  affected_tasks: CascadeAffectedTask[];
  conflicts: CascadeConflict[];
  can_auto_resolve: boolean;
}

export interface ProductionBatch {
  id: string;
  product: string;
  spec: string;
  color: string;
  core_colors?: string;
  customer: string;
  delivery_date: string;
  length_per_unit_m: number;
  unit_count: number;
  total_length_m: number;
  equipment_group: "연선" | "B100" | "A100" | "A120" | "T/P";
  voltage_type: "저압" | "고압";
  notes: string;
  /** AI 분류 근거 */
  classification_reason: string;
  /** 원본 수주 데이터 */
  rawData?: {
    order_number?: string;
    voltage?: string;
    neutral_wire?: string;
    core_color?: string;
    product_group?: string;
    unit_price_krw?: number;
    total_price_krw?: number;
  };
  /** 배치 그룹 — 같은 (공정, SQ) 묶음 식별자 */
  batch_group?: string;
}

// ─────────────────────────────────────────────────────────────
// Batch group unassign/restore feature (Phase 4)
// ─────────────────────────────────────────────────────────────

/**
 * ScheduleTask.status 리터럴 집합.
 * 'scheduled'는 DB/ORM의 legacy default 값이므로 backward compat 위해 포함.
 */
export type TaskStatus =
  | "planned"
  | "scheduled"
  | "in_progress"
  | "completed"
  | "unassigned";

/** unassign 사유 (모달 radio 선택지) */
export type UnassignReason = "자재지연" | "설비고장" | "납기재협상" | "기타";

/** UnassignReason 리스트 — 모달에서 radio 렌더링 시 순회용 */
export const UNASSIGN_REASONS: readonly UnassignReason[] = [
  "자재지연",
  "설비고장",
  "납기재협상",
  "기타",
] as const;

/** 배치 그룹의 공정-설비 체인 항목 (BatchGroupSnapshot.processes 원소) */
export interface ProcessChainItem {
  process: string;
  equipment_group: string;
}

/**
 * 미배정 Inbox에 배치 그룹 단위로 표시되는 스냅샷.
 * 백엔드 `GET /batch-group-snapshots` 응답의 groups[] 원소 shape.
 */
export interface BatchGroupSnapshot {
  batch_group: string;
  customer: string;
  spec: string;
  color: string;
  total_length_m: number;
  delivery_date: string;
  processes: ProcessChainItem[];
  order_count: number;
  unassign_reason: UnassignReason;
}

/**
 * OrderInbox 항목의 union type.
 * 'order'는 기존 OrderCard, 'batch_group'은 신규 BatchGroupCard로 렌더링.
 */
export type InboxItem =
  | { kind: "order"; order: Order }
  | { kind: "batch_group"; group: BatchGroupSnapshot };
