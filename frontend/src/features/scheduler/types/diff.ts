/**
 * 스케줄 변경(ChangeSet) Diff 응답 타입.
 *
 * 백엔드 엔드포인트:
 *   GET /api/schedules/change-sets/{change_set_id}/diff
 *
 * 사용처:
 *  - plan-register 페이지: 긴급수주 반영 직후 diff 요약 패널
 *  - scheduling-review 페이지: 추후 Gantt diff 비교 뷰 (후속 작업)
 *
 * 백엔드 schemas 는 backend/app/schemas/change_set_diff.py 와 1:1 매칭되어야 한다.
 * 필드 추가 시 양쪽 모두 갱신 필요.
 */

/** Diff 요약 카운트 (moved/added/removed/unchanged + 전/후 총합) */
export interface ScheduleDiffSummary {
  moved: number;
  added: number;
  removed: number;
  unchanged: number;
  total_before: number;
  total_after: number;
}

/**
 * Diff 단일 항목.
 *
 * moved_tasks: old_*, new_* 모두 존재
 * added_tasks: new_*만 존재 (old_*는 null)
 * removed_tasks: old_*만 존재 (new_*는 null)
 *
 * UI 에서 `start_delta_hours`의 절대값 기준 정렬 시 음수/양수/null 혼재를 주의.
 */
export interface ScheduleDiffEntry {
  task_id: string;
  old_start?: string | null;
  old_end?: string | null;
  old_equipment?: string | null;
  new_start?: string | null;
  new_end?: string | null;
  new_equipment?: string | null;
  start_delta_hours?: number;
  end_delta_hours?: number;
  equipment_changed?: boolean;
  batch_group?: string | null;
  process_name?: string | null;
  sheath_color?: string | null;
  cross_section?: number | null;
  is_urgent?: boolean;
  customer_priority?: number | null;
}

/** `/schedules/change-sets/{id}/diff` 전체 응답 shape */
export interface ScheduleDiffResponse {
  change_set_id: string;
  kind: string;
  created_at: string;
  preview_request_id?: string | null;
  summary: ScheduleDiffSummary;
  moved_tasks: ScheduleDiffEntry[];
  added_tasks: ScheduleDiffEntry[];
  removed_tasks: ScheduleDiffEntry[];
  unchanged_task_ids: string[];
}
