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
  /** schedule_task 생성 시각 — 증분 업데이트 후 신규 배치 강조 표시에 사용 */
  created_at?: Date;
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
