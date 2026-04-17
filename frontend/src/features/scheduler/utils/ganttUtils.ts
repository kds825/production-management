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
export const DATE_HEADER_HEIGHT = 44; // px — date + weekday 2-line header
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

// --- 주말 너비 조정 매핑 ---

/**
 * 주말(토·일) 컬럼을 weekendWidth 너비로 처리하는 시간→픽셀 변환.
 * weekendWidth === dayWidth 이면 기존 timeToX와 동일 결과.
 */
export function timeToXAdj(
  timestamp: number,
  rangeStart: number,
  dayWidth: number,
  weekendWidth: number,
): number {
  if (weekendWidth === dayWidth)
    return timeToX(timestamp, rangeStart, dayWidth);
  let x = 0;
  const cursor = new Date(rangeStart);
  cursor.setHours(0, 0, 0, 0);
  const tsDay = new Date(timestamp);
  tsDay.setHours(0, 0, 0, 0);
  while (cursor.getTime() < tsDay.getTime()) {
    x += isWeekend(cursor) ? weekendWidth : dayWidth;
    cursor.setDate(cursor.getDate() + 1);
  }
  const wThis = isWeekend(tsDay) ? weekendWidth : dayWidth;
  x += ((timestamp - tsDay.getTime()) / MS_PER_DAY) * wThis;
  return x;
}

/**
 * 픽셀 X → 타임스탬프 역변환 (주말 너비 보정 적용).
 */
export function xToTimeAdj(
  x: number,
  rangeStart: number,
  dayWidth: number,
  weekendWidth: number,
): number {
  if (weekendWidth === dayWidth) return xToTime(x, rangeStart, dayWidth);
  let remaining = x;
  const cursor = new Date(rangeStart);
  cursor.setHours(0, 0, 0, 0);
  while (remaining > 0) {
    const w = isWeekend(cursor) ? weekendWidth : dayWidth;
    if (remaining < w) {
      return cursor.getTime() + (remaining / w) * MS_PER_DAY;
    }
    remaining -= w;
    cursor.setDate(cursor.getDate() + 1);
  }
  return cursor.getTime();
}

/**
 * 주말 너비 보정을 적용한 타임라인 전체 너비 계산.
 */
export function timelineWidthAdj(
  rangeStart: number,
  rangeEnd: number,
  dayWidth: number,
  weekendWidth: number,
): number {
  if (weekendWidth === dayWidth)
    return getTimelineWidth(rangeStart, rangeEnd, dayWidth);
  const days = generateDays(rangeStart, rangeEnd);
  return days.reduce(
    (sum, d) => sum + (isWeekend(d.date) ? weekendWidth : dayWidth),
    0,
  );
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
 * 작업 시간 구성 계산 — backend calendar_engine.py 와 동일 규칙을 JS 로 재현.
 *
 * 공정 카테고리별 shift 창 + 휴식을 task 의 실제 점유 구간 [startTs, endTs) 에
 * 교집합으로 적용하여 break / gap 을 정확히 산출한다. 공유 일(同 요일을 두
 * task 가 나눠 점유) 에서 full-day bucket 을 양쪽에 더해 생기던 이중 표시는
 * 이 구현에서 구조적으로 발생하지 않는다 (각 break 구간은 [startTs, endTs)
 * 에 1 회만 교차).
 */
export interface TimeBreakdown {
  gapHrs: number; // shift 창 밖 시간 (주말·공휴·금 22시 이후·월 00~08 등)
  breakHrs: number; // 평일 점심/저녁/간식 등 shift 내부 break
  totalIdleHrs: number; // gap + break
  workingDays: number; // task 가 실제로 점유한 "가동일" 수
  actualWork: number; // 순수 작업시간(setup 제외, h)
  details: string[];
  // ── 호환성: 기존 필드 보존 (page.tsx / GanttTaskBlock 에서 UI 라벨만 재매핑)
  weekendHrs: number; // = gapHrs (alias)
  dailyIdleHrs: number; // = breakHrs (alias)
}

// ── 공정 카테고리 매핑 (calendar_engine.py 와 동일) ───────────────────────────
const _EQUIP_PREFIX_CATEGORY: [string, string][] = [
  ["ST-", "연선연합"],
  ["CA-", "연선연합"],
  ["EX-B", "저압절연"],
  ["EX-CV", "고압절연"],
  ["SH-", "시스"],
];

// 요일: 0=Mon, 1=Tue, ..., 4=Fri, 5=Sat, 6=Sun (Python 기준)
const _PROCESS_HOURS: Record<string, Record<number, number>> = {
  연선연합: { 0: 22, 1: 22, 2: 22, 3: 22, 4: 14, 5: 0, 6: 0 },
  저압절연: { 0: 24, 1: 24, 2: 24, 3: 24, 4: 12, 5: 0, 6: 0 },
  고압절연: { 0: 18, 1: 24, 2: 24, 3: 24, 4: 12, 5: 0, 6: 0 },
  시스: { 0: 24, 1: 24, 2: 24, 3: 24, 4: 12, 5: 0, 6: 0 },
  default: { 0: 22, 1: 22, 2: 22, 3: 22, 4: 14, 5: 0, 6: 0 },
};

const _FRI_END_HOUR: Record<string, number> = {
  연선연합: 22,
  저압절연: 20,
  고압절연: 20,
  시스: 20,
  default: 22,
};

// 월~목 shift 내부 break (연선연합·default 한정)
const _DAILY_BREAKS: Record<string, [number, number, number, number][]> = {
  연선연합: [
    [12, 0, 13, 0],
    [18, 0, 18, 30],
    [22, 0, 22, 30],
  ],
  default: [
    [12, 0, 13, 0],
    [18, 0, 18, 30],
    [22, 0, 22, 30],
  ],
  저압절연: [],
  고압절연: [],
  시스: [],
};

function _getCategory(equipmentCode?: string | null): string {
  if (!equipmentCode) return "default";
  for (const [prefix, cat] of _EQUIP_PREFIX_CATEGORY) {
    if (equipmentCode.startsWith(prefix)) return cat;
  }
  return "default";
}

type Shift = {
  start: number;
  end: number;
  breaks: { start: number; end: number }[];
};

/** 특정 날짜(d) 의 shift(가동 창) 생성. hours=0 이면 null. */
function _getShift(d: Date, cat: string): Shift | null {
  const jsDay = d.getDay(); // 0=Sun, 1=Mon, ..., 6=Sat
  const pyDay = (jsDay + 6) % 7; // 0=Mon, ..., 6=Sun
  const hours = _PROCESS_HOURS[cat][pyDay];
  if (hours <= 0) return null;

  const MS_PER_HOUR_LOCAL = 60 * 60 * 1000;
  const start = new Date(d);
  start.setHours(8, 0, 0, 0);
  let end: Date;

  if (pyDay === 4) {
    const friEndH = _FRI_END_HOUR[cat] ?? _FRI_END_HOUR.default;
    end = new Date(d);
    end.setHours(friEndH, 0, 0, 0);
  } else if (cat === "연선연합" || cat === "default") {
    // Mon~Thu 연선연합: 08:00 → 익일 08:00 (24h 창, 내부 break 로 22h 유효)
    end = new Date(start.getTime() + 24 * MS_PER_HOUR_LOCAL);
  } else if (hours === 24) {
    end = new Date(start.getTime() + 24 * MS_PER_HOUR_LOCAL);
  } else {
    // 고압절연 월: 18h 창
    end = new Date(start.getTime() + hours * MS_PER_HOUR_LOCAL);
  }

  const breaks: { start: number; end: number }[] = [];
  if (pyDay < 4) {
    for (const [sh, sm, eh, em] of _DAILY_BREAKS[cat] ?? []) {
      const bs = new Date(d);
      bs.setHours(sh, sm, 0, 0);
      const be = new Date(d);
      be.setHours(eh, em, 0, 0);
      breaks.push({ start: bs.getTime(), end: be.getTime() });
    }
  }

  return { start: start.getTime(), end: end.getTime(), breaks };
}

export function computeTimeBreakdown(
  startTs: number,
  endTs: number,
  changeoverTotalMin: number, // setupMin + colorChangeMin
  equipmentCode?: string | null,
): TimeBreakdown {
  const MS_PER_MIN = 60 * 1000;
  const totalMin = (endTs - startTs) / MS_PER_MIN;
  const cat = _getCategory(equipmentCode);

  // [startTs, endTs) 와 겹칠 가능성 있는 shift 수집.
  // shift 는 시작일이 기준 (pyDay=0~3 일 경우 익일까지 연장 → 경계 ±2일 여유).
  const shifts: Shift[] = [];
  const scan = new Date(startTs);
  scan.setHours(0, 0, 0, 0);
  scan.setDate(scan.getDate() - 2);
  const stopTs = endTs + 2 * 24 * 60 * 60 * 1000;
  while (scan.getTime() < stopTs) {
    const s = _getShift(scan, cat);
    if (s && s.start < endTs && s.end > startTs) shifts.push(s);
    scan.setDate(scan.getDate() + 1);
  }

  // 교집합으로 work / break 시간 집계
  let workMin = 0;
  let breakMin = 0;
  const workingDateSet = new Set<string>();
  for (const s of shifts) {
    const a = Math.max(s.start, startTs);
    const b = Math.min(s.end, endTs);
    if (b <= a) continue;
    const shiftCoveredMin = (b - a) / MS_PER_MIN;
    let brk = 0;
    for (const br of s.breaks) {
      const ba = Math.max(br.start, a);
      const bb = Math.min(br.end, b);
      if (bb > ba) brk += (bb - ba) / MS_PER_MIN;
    }
    workMin += shiftCoveredMin - brk;
    breakMin += brk;
    workingDateSet.add(new Date(s.start).toDateString());
  }
  const gapMin = Math.max(0, totalMin - workMin - breakMin);

  // 상세 — 관측된 휴무일 (weekend/holiday) 나열
  const details: string[] = [];
  const cur = new Date(startTs);
  cur.setHours(0, 0, 0, 0);
  while (cur.getTime() < endTs) {
    const jsDay = cur.getDay();
    const pyDay = (jsDay + 6) % 7;
    const h = _PROCESS_HOURS[cat][pyDay] ?? 0;
    if (h <= 0) {
      const name = jsDay === 6 ? "토" : jsDay === 0 ? "일" : "휴";
      details.push(`${cur.getMonth() + 1}/${cur.getDate()}(${name}) 휴무`);
    }
    cur.setDate(cur.getDate() + 1);
  }

  const actualWork = Math.max(0, workMin / 60 - changeoverTotalMin / 60);

  return {
    gapHrs: Math.round((gapMin / 60) * 10) / 10,
    breakHrs: Math.round((breakMin / 60) * 10) / 10,
    totalIdleHrs: Math.round(((gapMin + breakMin) / 60) * 10) / 10,
    workingDays: workingDateSet.size,
    actualWork,
    details,
    // alias (legacy field names)
    weekendHrs: Math.round((gapMin / 60) * 10) / 10,
    dailyIdleHrs: Math.round((breakMin / 60) * 10) / 10,
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
