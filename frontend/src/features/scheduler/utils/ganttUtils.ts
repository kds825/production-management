/**
 * ganttUtils.ts
 *
 * Custom Gantt 차트 유틸리티: 시간↔픽셀 변환, 작업 소요시간 계산 등.
 * dnd-timeline 제거 후 직접 구현한 순수 함수 모음.
 */

import type { ZoomLevel } from "../types";

// --- 상수 ---

export const SIDEBAR_WIDTH = 160; // px
export const ROW_HEIGHT = 44; // px
export const DATE_HEADER_HEIGHT = 32; // px
export const MS_PER_DAY = 24 * 60 * 60 * 1000;
export const MS_PER_MINUTE = 60 * 1000;

/** 줌 레벨별 하루를 차지하는 픽셀 너비 */
export const DAY_WIDTH_MAP: Record<ZoomLevel, number> = {
  week: 40,
  day: 80,
  hour: 200,
};

// --- 시간 ↔ 픽셀 변환 ---

/**
 * 타임스탬프(ms)를 Gantt 영역 내 X 좌표(px)로 변환한다.
 * 사이드바 영역은 포함하지 않는다 (타임라인 영역 내 상대 좌표).
 */
export function timeToX(
  timestamp: number,
  rangeStart: number,
  dayWidth: number,
): number {
  return ((timestamp - rangeStart) / MS_PER_DAY) * dayWidth;
}

/**
 * Gantt 영역 내 X 좌표(px)를 타임스탬프(ms)로 역변환한다.
 */
export function xToTime(
  x: number,
  rangeStart: number,
  dayWidth: number,
): number {
  return rangeStart + (x / dayWidth) * MS_PER_DAY;
}

/**
 * Gantt 타임라인 영역의 전체 너비(px)를 계산한다.
 */
export function getTimelineWidth(
  rangeStart: number,
  rangeEnd: number,
  dayWidth: number,
): number {
  return ((rangeEnd - rangeStart) / MS_PER_DAY) * dayWidth;
}

// --- 날짜 유틸 ---

/** 토요일(6) / 일요일(0) 여부 */
export function isWeekend(date: Date): boolean {
  const d = date.getDay();
  return d === 0 || d === 6;
}

/** 현재 달의 시작/끝 Date 반환 (하위 호환용 — 신규 코드에서는 getDefaultRange 사용) */
export function getCurrentMonthRange(): { start: Date; end: Date } {
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), 1, 0, 0, 0, 0);
  const end = new Date(
    now.getFullYear(),
    now.getMonth() + 1,
    0,
    23,
    59,
    59,
    999,
  );
  return { start, end };
}

/** 오늘 기준 ±days 범위 반환 (기본: ±14일) */
export function getDefaultRange(daysOffset = 14): {
  start: number;
  end: number;
} {
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  const start = new Date(now);
  start.setDate(start.getDate() - daysOffset);
  const end = new Date(now);
  end.setDate(end.getDate() + daysOffset);
  end.setHours(23, 59, 59, 999);
  return { start: start.getTime(), end: end.getTime() };
}

/**
 * rangeStart ~ rangeEnd 사이의 날짜 목록을 생성한다.
 * 각 날짜는 00:00:00 기준.
 */
export function generateDays(
  rangeStart: number,
  rangeEnd: number,
): { date: Date; timestamp: number }[] {
  const result: { date: Date; timestamp: number }[] = [];
  const cursor = new Date(rangeStart);
  cursor.setHours(0, 0, 0, 0);

  while (cursor.getTime() <= rangeEnd) {
    result.push({
      date: new Date(cursor),
      timestamp: cursor.getTime(),
    });
    cursor.setDate(cursor.getDate() + 1);
  }

  return result;
}

// --- 라인 속도 & 소요시간 계산 ---

import type { LineSpeedEntry } from "../types";

/**
 * 주어진 주문의 라인 속도(m/min)를 조회한다.
 *
 * 탐색 우선순위:
 * 1. product_group 키 (예: "TFR-GV", "HFCO")
 * 2. core_count 기반 키 (예: "1C", "2C", "3C", "4C")
 * 3. "insulation" 폴백
 * 4. 최종 기본값 10 m/min
 */
export function getLineSpeed(
  lineSpeedData: LineSpeedEntry[],
  spec: string,
  productGroup: string,
  coreCount: number,
): number {
  const entry = lineSpeedData.find((e) => e.spec === spec);
  if (!entry) return 10; // 기본 폴백

  // 1. 제품 그룹 직접 매칭
  if (entry.speeds[productGroup] != null) {
    return entry.speeds[productGroup];
  }

  // 2. 코어 수 기반
  const coreKey = `${coreCount}C`;
  if (entry.speeds[coreKey] != null) {
    return entry.speeds[coreKey];
  }

  // 3. 절연 폴백
  if (entry.speeds["insulation"] != null) {
    return entry.speeds["insulation"];
  }

  return 10;
}

/**
 * 주문의 생산 소요시간(ms)을 계산한다.
 */
export function calculateDurationMs(
  totalLengthM: number,
  lineSpeedMPerMin: number,
): number {
  const durationMinutes = totalLengthM / lineSpeedMPerMin;
  return durationMinutes * MS_PER_MINUTE;
}

/**
 * 작업 시간 구성 계산 — GanttTaskBlock 호버 팝오버 및 상세 패널 공용.
 * 가동시간: 월~목 08~익일06(22h), 금 08~22(14h), 토일 0h.
 */
export interface TimeBreakdown {
  weekendHrs: number;   // 주말 + 월요일 00~08시
  dailyIdleHrs: number; // 일일 부동시간
  totalIdleHrs: number;
  workingDays: number;
  actualWork: number;   // 순수 작업 시간(h)
  details: string[];
}

export function computeTimeBreakdown(
  startTs: number,
  endTs: number,
  changeoverTotalMin: number, // setupMin + colorChangeMin
): TimeBreakdown {
  const MS_PER_DAY_LOCAL = 24 * 60 * 60 * 1000;
  const MS_PER_HOUR_LOCAL = 60 * 60 * 1000;
  const totalDurationHrs = (endTs - startTs) / MS_PER_HOUR_LOCAL;

  let weekendHrs = 0;
  let dailyIdleHrs = 0;
  let overnightHrs = 0;
  let workingDays = 0;
  const details: string[] = [];
  const cur = new Date(startTs);
  cur.setHours(0, 0, 0, 0);

  while (cur.getTime() < endTs) {
    const day = cur.getDay();
    if (day === 0 || day === 6) {
      weekendHrs += 24;
      details.push(
        `${cur.getMonth() + 1}/${cur.getDate()}(${day === 6 ? "토" : "일"}) 휴무`,
      );
    } else {
      workingDays++;
      dailyIdleHrs += day === 5 ? 10 : 2;
    }
    cur.setDate(cur.getDate() + 1);
  }

  // 주말 후 월요일 00~08시 추가
  if (endTs - startTs > MS_PER_DAY_LOCAL) {
    const c2 = new Date(startTs);
    c2.setHours(0, 0, 0, 0);
    while (c2.getTime() < endTs) {
      if (c2.getDay() === 1 && c2.getTime() > startTs) {
        overnightHrs += 8;
        details.push(
          `${c2.getMonth() + 1}/${c2.getDate()}(월) 08시 업무시작`,
        );
      }
      c2.setDate(c2.getDate() + 1);
    }
  }

  const totalIdleHrs = weekendHrs + dailyIdleHrs + overnightHrs;
  const actualWork = Math.max(
    0,
    totalDurationHrs - totalIdleHrs - changeoverTotalMin / 60,
  );

  return {
    weekendHrs: weekendHrs + overnightHrs,
    dailyIdleHrs,
    totalIdleHrs,
    workingDays,
    actualWork,
    details,
  };
}

/**
 * 주문의 종료 시각을 계산한다.
 */
export function calculateTaskEnd(
  startTime: Date,
  totalLengthM: number,
  lineSpeedMPerMin: number,
): Date {
  const durationMs = calculateDurationMs(totalLengthM, lineSpeedMPerMin);
  return new Date(startTime.getTime() + durationMs);
}
