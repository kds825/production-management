// Base URL for all API requests; falls back to local dev server
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

/**
 * Week 4 Task 4B.4 — API 에러 표현 클래스.
 *
 * 기존 코드는 `throw new Error(...)` 만 사용해 호출자가 status / runId 에 접근할 길이
 * 없었다. 백엔드 Week 2 Task 2A.4 미들웨어가 모든 응답에 `X-Run-Id` 헤더를 부여하므로,
 * 에러 발생 시 이 값을 보존해 운영자가 토스트에서 복사 후 담당자에게 전달할 수 있게 한다.
 *
 * - `runId`: 백엔드가 발급한 요청 상관관계 UUID. 헤더가 없으면 `null`.
 *   (스펙상 항상 존재하지만, 네트워크 단절·CORS preflight 실패 등 극단 케이스에선 누락 가능.)
 * - `status`: HTTP status (기존 메시지에 포함되던 정보를 구조적으로 노출).
 * - `message` 포맷은 기존 호환을 위해 `API Error: {status} {statusText}` 유지.
 *   (call-site 들이 `e.message` 를 그대로 토스트에 표시하던 패턴 보존.)
 */
export class ApiError extends Error {
  readonly status: number;
  readonly statusText: string;
  readonly runId: string | null;

  constructor(status: number, statusText: string, runId: string | null) {
    super(`API Error: ${status} ${statusText}`);
    this.name = "ApiError";
    this.status = status;
    this.statusText = statusText;
    this.runId = runId;
  }
}

export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    // Headers.get 은 fetch 스펙상 case-insensitive — 백엔드 미들웨어가 "X-Run-Id" 로
    // 발급하지만, 일부 프록시가 lowercase 로 정규화해도 안전하게 회수된다.
    const runId = res.headers.get("X-Run-Id");
    throw new ApiError(res.status, res.statusText, runId);
  }
  return res.json();
}
