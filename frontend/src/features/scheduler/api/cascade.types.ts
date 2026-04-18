/**
 * Cascade API 타입 — 백엔드 Pydantic 스키마의 TypeScript 미러.
 *
 * 업데이트 시 `backend/app/presentation/schemas/cascade.py` 와 동기화 필요.
 *
 * TZ guard: datetime 은 모두 naive KST ISO 문자열. 'Z' 접미사 / '+09:00' 은
 * 백엔드가 422로 reject 하므로 프론트에서 직접 `toISOString()` 대신 naive 포맷을
 * 사용한다 (`YYYY-MM-DDTHH:mm:ss`).
 */

// ── enum 미러 ───────────────────────────────────────────────────────────

export type PushReasonCode =
  | "same_equipment_conflict"
  | "cross_equipment_conflict"
  | "successor_chain";

export type PullReasonCode = "successor_slack_available";

export type UnresolvedReasonCode =
  | "due_date_violation"
  | "no_space_forward"
  | "cycle_detected"
  | "invalid_equipment";

// ── Cascade preview ─────────────────────────────────────────────────────

export interface PushEntry {
  task_id: string;
  equipment_code: string;
  batch_label: string;
  old_start: string; // ISO naive datetime (e.g. "2026-04-20T10:00:00")
  old_end: string;
  new_start: string;
  new_end: string;
  // push 와 pull 이 같은 구조를 공유 — 방향에 따라 의미만 다름.
  reason: PushReasonCode | PullReasonCode;
}

export interface UnresolvedEntry {
  task_id: string;
  equipment_code: string;
  batch_label: string;
  reason: UnresolvedReasonCode;
  detail: string;
}

export interface CascadePreviewRequest {
  task_id: string;
  new_start: string;
  new_end: string;
  new_equipment_code?: string | null;
}

export interface CascadePreviewResponse {
  request_id: string;
  summary: string;
  pushes: PushEntry[];
  pulls: PushEntry[];
  unresolved: UnresolvedEntry[];
  can_auto_resolve: boolean;
  iter_count: number;
  truncated: boolean;
}

// ── Bulk update ─────────────────────────────────────────────────────────

export interface TaskChange {
  task_id: string;
  new_start: string;
  new_end: string;
  new_equipment_code?: string | null;
}

export interface BulkUpdateRequest {
  changes: TaskChange[];
  // preview 응답의 request_id 를 감사 로그용으로 연계. 동시성 토큰은 아님.
  expected_cascade_request_id?: string | null;
}

export type BulkUpdateErrorCode =
  | "VALIDATION_OVERLAP_SAME_EQUIPMENT"
  | "VALIDATION_PREDECESSOR_VIOLATION"
  | "VALIDATION_DUE_DATE_VIOLATION"
  | "FEATURE_DISABLED"
  | "CONCURRENT_UPDATE";

export interface BulkUpdateSuccess {
  // Undo 앵커 — revert API 가 참조.
  change_set_id: string;
  updated_task_ids: string[];
}

export interface BulkUpdateError422 {
  error_code: BulkUpdateErrorCode;
  offending_task_id: string;
  detail: string;
  can_retry: boolean;
}

/**
 * 422 응답을 프론트 호출자에게 전달하기 위한 커스텀 Error.
 *
 * 왜 클래스: `error_code` 분기를 `instanceof` 기반으로 단순화하고
 * `offendingTaskId` / `canRetry` 같은 추가 필드를 타입 안전하게 노출하기 위함.
 */
export class BulkUpdateError extends Error {
  code: BulkUpdateErrorCode;
  offendingTaskId: string;
  canRetry: boolean;

  constructor(payload: BulkUpdateError422) {
    super(`bulk-update 422: ${payload.error_code}`);
    this.name = "BulkUpdateError";
    this.code = payload.error_code;
    this.offendingTaskId = payload.offending_task_id;
    this.canRetry = payload.can_retry;
  }
}
