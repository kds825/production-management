/**
 * Equipment.process_type 값 — backend/seed_db.py 기준 검증 완료.
 * 변경 시 backend equipment_master.process_name 도 동기되어야 함.
 */
export const PROCESS_ORDER: readonly string[] = [
  "연선",
  "저압절연",
  "고압절연",
  "연합",
  "T/P",
  "저압시스",
  "고압시스",
] as const;

const EXCLUDED_PROCESSES = new Set<string>(["신선"]);

export function isExcludedProcess(
  processName: string | null | undefined,
): boolean {
  return processName != null && EXCLUDED_PROCESSES.has(processName);
}

export function isTerminalProcess(
  processName: string | null | undefined,
): boolean {
  return processName != null && processName.endsWith("시스");
}
