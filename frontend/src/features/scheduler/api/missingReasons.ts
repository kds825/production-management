/**
 * Missing-reasons API 클라이언트.
 *
 * 책임:
 * - fetchMissingReasonsList: GET /change-sets/missing-reasons/list. 배지 카운트와
 *   동일한 fixture 가드가 백엔드에서 적용되므로 모달은 별도 필터링 불필요.
 * - patchBulkReason: PATCH /change-sets/bulk-reason. 부분 실패 envelope
 *   (updated_change_set_ids + errors[]) 를 그대로 반환하여 모달이 행별 결과를
 *   표시할 수 있게 한다.
 *
 * apiFetch 사용 이유: 기존 missingReasonsStore 가 같은 헬퍼를 쓰고, X-Run-Id
 * 헤더 회수 / ApiError 타입을 통일하기 위함.
 */

import { apiFetch } from "@/shared/api/client";

/** snapshot_before/after 의 task-level payload — bulk_update v2 와 동일 스키마. */
export interface TaskSnapshotPayload {
  start: string;
  end: string;
  equipment_code: string;
}

/** missing-reasons/list 응답의 행별 task 정보. */
export interface MissingReasonTask {
  task_id: string;
  batch_group: string | null;
  equipment_code: string | null;
  before: TaskSnapshotPayload | null;
  after: TaskSnapshotPayload | null;
}

/** missing-reasons/list 응답의 change_set 단일 행. */
export interface MissingReasonItem {
  change_set_id: string;
  created_at: string | null;
  tasks: MissingReasonTask[];
}

/** GET /change-sets/missing-reasons/list 응답. */
export interface MissingReasonsListResponse {
  items: MissingReasonItem[];
  since: string;
  allowed_reasons: string[];
}

/** PATCH /change-sets/bulk-reason 행별 업데이트 입력. */
export interface BulkReasonUpdate {
  change_set_id: string;
  reason: string | null;
}

/** PATCH /change-sets/bulk-reason 행별 실패 보고. */
export interface BulkReasonError {
  change_set_id: string;
  error_code: "invalid_reason" | "not_found";
  detail: string;
}

/** PATCH /change-sets/bulk-reason 응답. */
export interface BulkReasonResponse {
  updated_change_set_ids: string[];
  decisions_updated: number;
  errors: BulkReasonError[];
}

/**
 * 미기록 change_set 목록을 가져온다.
 *
 * @param since - ISO date (YYYY-MM-DD). 생략 시 백엔드가 오늘(KST) 자정을 기본값으로 사용.
 */
export function fetchMissingReasonsList(
  since?: string,
): Promise<MissingReasonsListResponse> {
  const qs = since ? `?since=${encodeURIComponent(since)}` : "";
  return apiFetch<MissingReasonsListResponse>(
    `/change-sets/missing-reasons/list${qs}`,
  );
}

/**
 * 여러 change_set 의 override_reason 을 한 번에 갱신한다.
 *
 * 부분 실패 정책: HTTP 200 + 응답 envelope 의 `errors[]` 로 행별 실패 보고.
 * 호출자는 응답을 받아서 어떤 행이 landed / failed 인지 사용자에게 노출해야 한다.
 */
export function patchBulkReason(
  updates: BulkReasonUpdate[],
): Promise<BulkReasonResponse> {
  return apiFetch<BulkReasonResponse>("/change-sets/bulk-reason", {
    method: "PATCH",
    body: JSON.stringify({ updates }),
  });
}
