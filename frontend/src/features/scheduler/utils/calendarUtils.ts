/**
 * calendarUtils.ts
 *
 * 날짜/달력 관련 순수 유틸리티 함수 모음.
 * 주말·공휴일 판단 및 근무 시간 계산에 사용된다.
 */

/** 대한민국 법정 공휴일 (YYYY-MM-DD 문자열 set) */
const KR_HOLIDAYS_2025: ReadonlySet<string> = new Set([
  "2025-01-01", // 신정
  "2025-01-28", // 설날 연휴
  "2025-01-29", // 설날
  "2025-01-30", // 설날 연휴
  "2025-03-01", // 삼일절
  "2025-05-05", // 어린이날
  "2025-05-06", // 어린이날 대체
  "2025-06-06", // 현충일
  "2025-08-15", // 광복절
  "2025-10-03", // 개천절
  "2025-10-05", // 추석 연휴
  "2025-10-06", // 추석
  "2025-10-07", // 추석 연휴
  "2025-10-08", // 추석 대체
  "2025-10-09", // 한글날
  "2025-12-25", // 성탄절
]);

const KR_HOLIDAYS_2026: ReadonlySet<string> = new Set([
  "2026-01-01", // 신정
  "2026-02-16", // 설날 연휴
  "2026-02-17", // 설날
  "2026-02-18", // 설날 연휴
  "2026-03-01", // 삼일절
  "2026-05-05", // 어린이날
  "2026-05-25", // 부처님오신날
  "2026-06-06", // 현충일
  "2026-08-15", // 광복절
  "2026-09-24", // 추석 연휴
  "2026-09-25", // 추석
  "2026-09-26", // 추석 연휴
  "2026-10-03", // 개천절
  "2026-10-09", // 한글날
  "2026-12-25", // 성탄절
]);

function toYMD(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** 주말(토·일) 여부 반환 */
export function isWeekend(date: Date): boolean {
  const day = date.getDay();
  return day === 0 || day === 6;
}

/** 대한민국 법정 공휴일 여부 반환 */
export function isHoliday(date: Date): boolean {
  const ymd = toYMD(date);
  return KR_HOLIDAYS_2025.has(ymd) || KR_HOLIDAYS_2026.has(ymd);
}

/**
 * 해당 날짜의 표준 근무 시간 반환 (시간 단위).
 * 주말·공휴일이면 0, 평일이면 8.
 */
export function getWorkingHours(date: Date): number {
  if (isWeekend(date) || isHoliday(date)) return 0;
  return 8;
}
