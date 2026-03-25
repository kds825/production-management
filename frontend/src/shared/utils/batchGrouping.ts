import type { ProductionBatch } from "@/features/scheduler/types";

export function getBatchGroupKey(b: ProductionBatch): string {
  return `${b.product}||${b.spec}||${b.customer}||${b.delivery_date}`;
}

export function sortByDeliveryPriority(
  batches: ProductionBatch[],
): ProductionBatch[] {
  return [...batches].sort((a, b) => {
    // 1. delivery_date ascending
    const dateCompare = a.delivery_date.localeCompare(b.delivery_date);
    if (dateCompare !== 0) return dateCompare;
    // 2. product
    const productCompare = a.product.localeCompare(b.product);
    if (productCompare !== 0) return productCompare;
    // 3. spec
    return a.spec.localeCompare(b.spec);
  });
}

export function assignBatchNumbers(
  batches: ProductionBatch[],
): Map<string, number> {
  const sorted = sortByDeliveryPriority(batches);
  const map = new Map<string, number>();
  let counter = 1;
  for (const b of sorted) {
    const key = getBatchGroupKey(b);
    if (!map.has(key)) {
      map.set(key, counter++);
    }
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
