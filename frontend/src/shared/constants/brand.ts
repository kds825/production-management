export const KBI_BRAND = {
  colors: {
    primary: "#C41230",
    primaryDark: "#9E0E27",
    secondary: "#4A2C2A",
    accent: "#D4A574",
    background: "#FAFAFA",
    surface: "#FFFFFF",
    text: "#1A1A1A",
    textMuted: "#6B7280",
    border: "#E5E7EB",
    success: "#16A34A",
    warning: "#F59E0B",
    error: "#DC2626",
    taskColors: {
      "TFR-CV-WB": "#C41230",
      "TFR-CV": "#E65100",
      HFCO: "#1565C0",
      "TFR-8": "#6A1B9A",
      "TFR-GV": "#2E7D32",
      "CNCV-W": "#00695C",
      default: "#78909C",
    },
  },
  fonts: {
    heading: "'Pretendard', 'Apple SD Gothic Neo', sans-serif",
    body: "'Pretendard', 'Apple SD Gothic Neo', sans-serif",
  },
} as const;

export type ProductGroup = keyof typeof KBI_BRAND.colors.taskColors;

// 알려진 제품 그룹 색상 (best-effort 매핑)
const KNOWN_TASK_COLORS: Record<string, string> = {
  "TFR-CV-WB": "#C41230",
  "TFR-CV": "#E65100",
  HFCO: "#1565C0",
  "TFR-8": "#6A1B9A",
  "TFR-GV": "#2E7D32",
  "CNCV-W": "#00695C",
};

/**
 * SQ(mm²) → 배경색 매핑
 * 소선경(wire_diameter) 클러스터별로 같은 계열 색상 사용:
 *   2.21mm: 16SQ·25SQ·70SQ  → 청록 계열
 *   2.64mm: 35SQ·95SQ·185SQ·300SQ → 파랑 계열
 *   2.92mm: 120SQ·400SQ     → 보라 계열
 *   3.02mm: 50SQ·240SQ      → 초록 계열
 *   기타: 150SQ·200SQ·633SQ → 개별 색상
 */
const SQ_COLORS: Record<number, string> = {
  // wire_diameter 2.21mm 클러스터 (청록)
  16:  "#0E7490",
  25:  "#0891B2",
  70:  "#06B6D4",
  // wire_diameter 2.64mm 클러스터 (파랑)
  35:  "#1D4ED8",
  95:  "#2563EB",
  185: "#3B82F6",
  300: "#60A5FA",
  // wire_diameter 2.92mm 클러스터 (보라)
  120: "#7C3AED",
  400: "#8B5CF6",
  // wire_diameter 3.02mm 클러스터 (초록)
  50:  "#15803D",
  240: "#16A34A",
  // 기타
  150: "#D97706",  // 황갈
  200: "#B45309",  // 갈
  633: "#C41230",  // 고압 대단면 → KBI 레드
};

/** SQ 값으로 색상 반환. 미등록 SQ는 해시 폴백. */
export function getSqColor(sq: number | undefined): string | null {
  if (!sq) return null;
  // 정확한 매칭 우선
  if (SQ_COLORS[sq]) return SQ_COLORS[sq];
  // 가장 가까운 SQ로 매핑
  const keys = Object.keys(SQ_COLORS).map(Number);
  const closest = keys.reduce((a, b) =>
    Math.abs(b - sq) < Math.abs(a - sq) ? b : a,
  );
  return SQ_COLORS[closest];
}

/**
 * 문자열 해시를 이용해 미등록 제품 그룹에 일관된 색상을 생성한다.
 * 동일한 문자열은 항상 동일한 색상을 반환하므로 렌더링이 안정적이다.
 */
function hashColor(str: string): string {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  const hue = Math.abs(hash % 360);
  return `hsl(${hue}, 55%, 45%)`;
}

/**
 * 제품 그룹 문자열로부터 표시 색상을 반환한다.
 * 1. 정확히 일치하는 키를 먼저 확인
 * 2. 부분 문자열로 매칭 (예: "TFR-CV-WB-XXX" → "TFR-CV-WB")
 * 3. 알 수 없는 그룹은 해시 기반 색상으로 폴백 — 크래시 없음
 */
export function getProductColor(productGroup: string): string {
  if (KNOWN_TASK_COLORS[productGroup]) return KNOWN_TASK_COLORS[productGroup];
  for (const [key, color] of Object.entries(KNOWN_TASK_COLORS)) {
    if (productGroup.includes(key)) return color;
  }
  return hashColor(productGroup);
}
