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

// ─────────────────────────────────────────────────────────────
// Run compare (version diff) types
// ─────────────────────────────────────────────────────────────

/**
 * `/api/pipeline/runs/compare` 응답에서 moved 항목.
 *
 * 스케줄링 파이프라인 두 run(before/after) 사이에 **같은 stable key**
 * (`sales_order_id||batch_group|order_line|process_name|batch_seq`) 로 매칭된 작업 중
 * start/end/equipment 이 하나라도 바뀐 항목이 담긴다.
 *
 * old_ / new_ 필드는 ISO 8601 문자열 또는 null (백엔드가 schedule_task 미생성 상태를 null 로 반환).
 * null 인 경우 프론트에서는 해당 블록 렌더를 skip 하고 카운트만 유지한다 (스펙 §3 Partial 상태).
 */
export interface RunCompareMovedTask {
  task_id: string;
  old_start: string | null;
  old_end: string | null;
  old_equipment: string | null;
  new_start: string | null;
  new_end: string | null;
  new_equipment: string | null;
  start_delta_hours: number | null;
  end_delta_hours: number | null;
  equipment_changed: boolean;
  batch_group?: string | null;
  process_name?: string | null;
  sales_order_id?: string | null;
  customer_name?: string | null;
  sheath_color?: string | null;
  cross_section?: number | null;
}

/**
 * `/api/pipeline/runs/compare` 응답에서 added 항목 (after 에만 존재).
 * removed 와 필드 shape 이 동일해 하나의 타입으로 공유한다.
 */
export interface RunCompareAddedOrRemovedTask {
  task_id: string;
  start: string | null;
  end: string | null;
  equipment: string | null;
  batch_group?: string | null;
  process_name?: string | null;
  sales_order_id?: string | null;
  customer_name?: string | null;
  sheath_color?: string | null;
  cross_section?: number | null;
  due_date?: string | null;
}

/**
 * `/api/pipeline/runs/compare?before=X&after=Y` 전체 응답.
 *
 * 사용처:
 *  - scheduling-review 페이지: 집계/목록 모달 (기존)
 *  - scheduler 페이지: 간트차트 diff overlay (compareMode, 본 타입 확장의 목적)
 *
 * 두 페이지가 같은 서버 응답을 쓰므로 타입은 본 파일에서 SSOT 로 관리한다.
 * (스펙 Open Question Q3 — 중복 정의 통합).
 */
export interface RunCompareResponse {
  run_label_before: string;
  run_label_after: string;
  kind: string;
  created_at: string;
  summary: {
    moved: number;
    added: number;
    removed: number;
    unchanged: number;
    total_before: number;
    total_after: number;
  };
  moved_tasks: RunCompareMovedTask[];
  added_tasks: RunCompareAddedOrRemovedTask[];
  removed_tasks: RunCompareAddedOrRemovedTask[];
  unchanged_task_ids: string[];
}
