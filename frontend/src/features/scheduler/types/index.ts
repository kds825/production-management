export interface Equipment {
  id: string;
  name: string;
  process_type: string;
  capabilities: string[];
  capacity_tons_per_month: number;
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
  priority: "normal" | "urgent" | "critical";
  status: "planned" | "in_progress" | "completed" | "delayed";
  delivery_date?: Date;
  process_step?: number;
  predecessors: string[];
  notes: string;
  changeover_min: number;
}

export interface ConstraintViolation {
  type:
    | "overlap"
    | "equipment_capability"
    | "precedence"
    | "delivery"
    | "process_route";
  severity: "error" | "warning";
  message: string;
  task_id: string;
  related_task_id?: string;
}

export interface TimelineRow {
  id: string;
  disabled?: boolean;
}

export interface TimelineItem {
  id: string;
  rowId: string;
  /** dnd-timeline은 span의 start/end를 타임스탬프(number)로 요구한다 */
  span: { start: number; end: number };
  data: ScheduleTask;
}

export type ZoomLevel = "week" | "day" | "hour";

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
  priority: "normal" | "urgent" | "critical";
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
