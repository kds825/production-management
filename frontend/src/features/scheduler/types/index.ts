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
  span: { start: Date; end: Date };
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
