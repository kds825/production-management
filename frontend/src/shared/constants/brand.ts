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
