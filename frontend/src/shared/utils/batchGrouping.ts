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
