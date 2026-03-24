/**
 * colorCoding.ts
 *
 * 제품 그룹, 작업 상태, 우선순위에 따른 색상 결정 함수 모음.
 * TaskItem 및 기타 컴포넌트에서 공통으로 사용된다.
 */

import type { CSSProperties } from "react";
import { KBI_BRAND, getProductColor } from "@/shared/constants/brand";

/** 제품명 → 색상 hex 반환. 미등록 제품 그룹도 해시 색상으로 안전하게 표시된다. */
export function getTaskColor(product: string): string {
  return getProductColor(product);
}

/** 우선순위 → CSSProperties 반환 */
export function getPriorityStyle(priority: string): CSSProperties {
  switch (priority) {
    case "critical":
      return {
        backgroundColor: KBI_BRAND.colors.error,
        animation: "pulse 1.5s ease-in-out infinite",
      };
    case "urgent":
      return {
        outline: `2px solid ${KBI_BRAND.colors.error}`,
        outlineOffset: "-2px",
      };
    default:
      return {};
  }
}

/** 작업 상태 → CSSProperties 반환. baseColor는 제품 그룹 색상. */
export function getStatusStyle(
  status: string,
  baseColor: string,
): CSSProperties {
  switch (status) {
    case "completed":
      return { backgroundColor: KBI_BRAND.colors.success };
    case "delayed":
      return {
        backgroundColor: baseColor,
        outline: `2px solid ${KBI_BRAND.colors.error}`,
        outlineOffset: "-2px",
      };
    case "in_progress":
      return { backgroundColor: baseColor, filter: "brightness(0.85)" };
    default:
      return { backgroundColor: baseColor };
  }
}

/** 가동률(0~1) → 색상 hex 반환 */
export function getUtilizationColor(ratio: number): string {
  if (ratio >= 0.8) return KBI_BRAND.colors.error; // ≥80% 적색
  if (ratio >= 0.5) return KBI_BRAND.colors.warning; // 50~80% 황색
  return KBI_BRAND.colors.success; // <50% 녹색
}
