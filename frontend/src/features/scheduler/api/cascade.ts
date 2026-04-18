/**
 * Cascade API 클라이언트 (Task 16).
 *
 * 책임:
 * - cascadePreview: POST /schedules/cascade-preview (X-Cascade-API-Version: 2 필수).
 *   v1 엔드포인트는 별도 경로(cascade-preview-legacy)이며 본 함수는 v2 전용.
 * - bulkUpdate: POST /schedules/tasks/bulk-update. 422 → BulkUpdateError throw
 *   하여 호출자가 error_code 기반 분기(toast/dialog)를 할 수 있게 한다.
 * - revertChangeSet: POST /schedules/revert/{change_set_id}. 404/409 는
 *   일반 Error throw — Undo 버튼이 비활성 표시를 위해 catch 만 하면 됨.
 *
 * API base:
 * - 프로젝트 관례: `NEXT_PUBLIC_API_URL` 은 `/api` 를 이미 포함 (예: http://localhost:8000/api).
 *   따라서 fetch URL 은 `${API_BASE}/schedules/...` 형태. `/api` 를 중복 붙이지 않음.
 */

import {
  BulkUpdateError,
  BulkUpdateError422,
  BulkUpdateRequest,
  BulkUpdateSuccess,
  CascadePreviewRequest,
  CascadePreviewResponse,
} from "./cascade.types";

// 프로젝트 전역 관례: `NEXT_PUBLIC_API_URL` 은 이미 `/api` 접미사를 포함.
// 이 환경변수가 비어있으면 dev 기본값 (backend uvicorn 8000) 으로 폴백.
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

/**
 * Cascade preview v2 호출.
 *
 * 주의: `X-Cascade-API-Version: 2` 헤더가 없으면 백엔드가 v1 로 라우팅할 수 있으므로
 * 명시적으로 지정 (라우팅 로직이 변경되어도 계약을 단일화 유지).
 */
export async function cascadePreview(
  payload: CascadePreviewRequest,
): Promise<CascadePreviewResponse> {
  const res = await fetch(`${API_BASE}/schedules/cascade-preview`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Cascade-API-Version": "2",
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(`cascade-preview ${res.status}`);
  }
  return (await res.json()) as CascadePreviewResponse;
}

/**
 * Bulk update v2 호출.
 *
 * 422 응답 처리:
 * - FastAPI HTTPException(detail=dict) 은 `{ detail: {...} }` 로 래핑되지만
 *   일부 미들웨어/핸들러는 root-level payload 로 직접 직렬화할 수 있다.
 *   두 케이스 모두 호환되도록 fallback 처리.
 */
export async function bulkUpdate(
  body: BulkUpdateRequest,
): Promise<BulkUpdateSuccess> {
  const res = await fetch(`${API_BASE}/schedules/tasks/bulk-update`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 422) {
    const raw = (await res.json()) as
      | { detail: BulkUpdateError422 }
      | BulkUpdateError422;
    // FastAPI HTTPException 의 detail 래핑 vs root-level 모두 허용.
    const payload =
      "detail" in raw && typeof raw.detail === "object"
        ? raw.detail
        : (raw as BulkUpdateError422);
    throw new BulkUpdateError(payload);
  }
  if (!res.ok) {
    throw new Error(`bulk-update ${res.status}`);
  }
  return (await res.json()) as BulkUpdateSuccess;
}

/**
 * ChangeSet revert — Undo 버튼의 백엔드 엔드포인트.
 *
 * 404 (존재하지 않는 change_set_id) / 409 (이미 revert 됨) 은 일반 Error 로 throw.
 * Undo UI 는 단순히 토스트로 사용자에게 알리고 버튼을 사라지게 만들면 되므로
 * 세분화된 타입은 불필요 — 필요시 후속 task 에서 확장.
 */
export async function revertChangeSet(
  changeSetId: string,
): Promise<{ reverted: boolean }> {
  const res = await fetch(
    `${API_BASE}/schedules/revert/${encodeURIComponent(changeSetId)}`,
    { method: "POST" },
  );
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`revert ${res.status}: ${body}`);
  }
  return (await res.json()) as { reverted: boolean };
}
