import type { ProductionBatch } from "@/features/scheduler/types";

export function getBatchGroupKey(b: ProductionBatch): string {
  // batch_group이 있으면 그것을 사용 (같은 공정+SQ 묶음)
  if (b.batch_group) return b.batch_group;
  return `${b.product}||${b.spec}||${b.customer}||${b.delivery_date}`;
}

export function sortByDeliveryPriority(
  batches: ProductionBatch[],
): ProductionBatch[] {
  return [...batches].sort((a, b) => {
    // 1. delivery_date ascending (납기 빠른 순 = 먼저 투입)
    const dateCompare = a.delivery_date.localeCompare(b.delivery_date);
    if (dateCompare !== 0) return dateCompare;
    // 2. SQ descending (같은 납기면 큰 SQ 먼저)
    const sqA = parseInt(a.spec.match(/(\d+)SQ/)?.[1] || "0", 10);
    const sqB = parseInt(b.spec.match(/(\d+)SQ/)?.[1] || "0", 10);
    if (sqA !== sqB) return sqB - sqA;
    // 3. color
    return (a.color || "").localeCompare(b.color || "");
  });
}

/**
 * 배치 번호 부여 — batch_group 기준, 납기 빠른 순 오름차순.
 * batch_group 1개 = 배치 번호 1개.
 * 같은 batch_group의 개별 수주는 모두 같은 번호.
 */
export function assignBatchNumbers(
  batches: ProductionBatch[],
): Map<string, number> {
  // batch_group별 가장 빠른 납기를 구해서 그룹 순서 결정
  const groupEarliestDue = new Map<string, string>();
  for (const b of batches) {
    const key = getBatchGroupKey(b);
    const existing = groupEarliestDue.get(key);
    if (!existing || b.delivery_date < existing) {
      groupEarliestDue.set(key, b.delivery_date);
    }
  }

  // 납기 빠른 순으로 그룹 정렬
  const sortedGroups = [...groupEarliestDue.entries()].sort((a, b) =>
    a[1].localeCompare(b[1]),
  );

  // 번호 부여
  const map = new Map<string, number>();
  let counter = 1;
  for (const [groupKey] of sortedGroups) {
    map.set(groupKey, counter++);
  }
  return map;
}

/** 색상 정렬 우선순위: 흑→갈→회→청→녹→황 (명시되지 않은 색상은 뒤로) */
const COLOR_PRIORITY: Record<string, number> = {
  흑: 0,
  흑색: 0,
  BLACK: 0,
  갈: 1,
  갈색: 1,
  BROWN: 1,
  회: 2,
  회색: 2,
  GRAY: 2,
  청: 3,
  청색: 3,
  BLUE: 3,
  녹: 4,
  녹색: 4,
  GREEN: 4,
  황: 5,
  황색: 5,
  YELLOW: 5,
};

function getColorPriority(color: string): number {
  if (!color) return 99;
  const upper = color.trim().toUpperCase();
  // 정확히 매칭되는 것 먼저
  if (COLOR_PRIORITY[color.trim()] !== undefined)
    return COLOR_PRIORITY[color.trim()];
  // 접두사 매칭 (e.g., "흑색" matches "흑")
  for (const [key, val] of Object.entries(COLOR_PRIORITY)) {
    if (upper.startsWith(key.toUpperCase())) return val;
  }
  return 99;
}

/**
 * 배치를 batch_group 번호(납기 빠른 순) → 색상 순으로 정렬.
 * assignBatchNumbers 결과를 기반으로 정렬하므로 배치 번호와 표시 순서가 일치.
 */
export function sortBatchesByBatchNumber<
  T extends { delivery_date: string; color: string },
>(batches: T[], batchNumbers: Map<string, number>): T[] {
  return [...batches].sort((a, b) => {
    const numA =
      batchNumbers.get(getBatchGroupKey(a as unknown as ProductionBatch)) ??
      999;
    const numB =
      batchNumbers.get(getBatchGroupKey(b as unknown as ProductionBatch)) ??
      999;
    if (numA !== numB) return numA - numB;
    // 같은 batch_group 내: 색상 순서
    const colorA = getColorPriority(a.color);
    const colorB = getColorPriority(b.color);
    if (colorA !== colorB) return colorA - colorB;
    return 0;
  });
}

export function calcConvertedQty(spec: string, totalLengthM: number): number {
  const match = spec.match(/(\d+)C/);
  const coreCount = match ? parseInt(match[1], 10) : 1;
  if (isNaN(coreCount) || coreCount < 1) return totalLengthM;
  return totalLengthM * coreCount;
}

export function formatDeliveryDate(raw: string): string {
  if (raw.length === 8) {
    return `${raw.slice(0, 4)}.${raw.slice(4, 6)}.${raw.slice(6, 8)}`;
  }
  return raw;
}
