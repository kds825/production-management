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
    // 간트 diff overlay 전용 팔레트 (compareMode ON 시 사용).
    // 의도: cascade(warning=#F59E0B) 와 diff 는 의미가 다르다 —
    //   cascade 는 "수락하면 발생할 미래 변화",
    //   diff 는 "이미 발생한 과거 변화".
    //   색을 분리해 시각 혼동을 방지하고, 두 overlay 는 store 상 상호배타로 활성화한다.
    //   참조: docs/specs/2026-04-20-gantt-version-diff-overlay-design.md
    diff: {
      added: "#10B981", // emerald-500 — 녹색 outline / "+" 뱃지
      moved: "#3B82F6", // blue-500 — 파란 outline + ghost dashed
      removed: "#9CA3AF", // gray-400 — 회색 dashed (필터 ON 시)
    },
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
 *
 * 색조 방침: KBI 브랜드 팔레트(Sunrise Red·Orange·Champagne Gold·Warm Gray) 톤 참조.
 *   전체적으로 earthy·warm-shifted, 명도 L≈28~46% (흰 텍스트 가독 확보).
 * 클러스터 내 SQ 오름차순 = 명도 내림차순 (작은 SQ → 연한 톤)
 */
const SQ_COLORS: Record<number, string> = {
  // wire_diameter 2.21mm 클러스터 (웜 틸 — 청록에서 따뜻하게 조정)
  16: "#3D909E", // 밝은 웜 틸        L≈42%
  25: "#2A7282", // 중간 웜 틸        L≈34%
  70: "#165666", // 짙은 웜 틸        L≈26%
  // wire_diameter 2.64mm 클러스터 (슬레이트 블루 — KBI 차분한 계열)
  35: "#4D7EB0", // 밝은 슬레이트     L≈45%
  95: "#355E94", // 중간 슬레이트     L≈36%
  185: "#1E4478", // 짙은 네이비       L≈28%
  300: "#122E60", // 딥 네이비         L≈22%
  // wire_diameter 2.92mm 클러스터 (플럼 — 보라에서 KBI 레드 언더톤으로)
  120: "#7A4898", // 밝은 플럼         L≈40%
  400: "#562878", // 짙은 플럼         L≈28%
  // wire_diameter 3.02mm 클러스터 (포레스트 — 초록에서 earthy하게)
  50: "#3A8C5C", // 밝은 포레스트     L≈38%
  240: "#1E6840", // 짙은 포레스트     L≈28%
  // 기타 — KBI 브랜드 컬러에서 직접 파생
  150: "#C07820", // KBI Orange 파생 (앰버)      L≈40%
  200: "#937A61", // KBI Champagne Gold           L≈38%
  633: "#B01E38", // KBI Sunrise Red 파생 (딥 크림슨) L≈30%
};

/**
 * hex 배경색에 대해 가독성 좋은 텍스트 색상(흰 or 짙은 회색)을 반환한다.
 * W3C 상대 휘도 기준 — 대비비 3.5:1 이상이면 흰색, 아니면 어두운 색.
 */
export function getContrastTextColor(hex: string): string {
  const c = hex.replace("#", "");
  const r = parseInt(c.substring(0, 2), 16) / 255;
  const g = parseInt(c.substring(2, 4), 16) / 255;
  const b = parseInt(c.substring(4, 6), 16) / 255;
  const toLinear = (v: number) =>
    v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  const L = 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
  // 흰색(L=1) 대비비: (1.05) / (L + 0.05)
  return 1.05 / (L + 0.05) >= 3.5 ? "#FFFFFF" : "#1A1A1A";
}

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
