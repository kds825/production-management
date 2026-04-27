// PR3 Task 2.1 — KBI_BRAND.colors.* 토큰 참조로 재배선.
// 사용처는 inline style backgroundColor/color/border 등 CSS context (var() 해석됨).
// SVG attribute 직접 참조 (fill="..." stroke="...") 는 PR3 Task 3에서 별도 처리.
// 변경 시 globals.css 토큰 정의와 동기화.
export const KBI_BRAND = {
  colors: {
    primary: "var(--color-brand-primary)",
    primaryDark: "var(--accent-primary-hover)",
    secondary: "var(--kbi-brown)",
    accent: "var(--accent-warm)",
    background: "var(--color-bg-muted)",
    surface: "var(--bg-surface)",
    text: "var(--color-text-primary)",
    textMuted: "var(--color-text-secondary)",
    border: "var(--color-border-default)",
    success: "var(--status-success)",
    warning: "var(--status-warning)",
    error: "var(--color-danger)",
    // 간트 diff overlay 전용 팔레트 (compareMode ON 시 사용).
    // 의도: cascade(warning=#F59E0B) 와 diff 는 의미가 다르다 —
    //   cascade 는 "수락하면 발생할 미래 변화",
    //   diff 는 "이미 발생한 과거 변화".
    //   색을 분리해 시각 혼동을 방지하고, 두 overlay 는 store 상 상호배타로 활성화한다.
    //   참조: docs/specs/2026-04-20-gantt-version-diff-overlay-design.md
    diff: {
      added: "var(--viz-diff-added)", // 녹색 outline / "+" 뱃지
      moved: "var(--viz-diff-moved)", // 파란 outline + ghost dashed
      removed: "var(--viz-diff-removed)", // 회색 dashed (필터 ON 시)
    },
    taskColors: {
      "TFR-CV-WB": "var(--viz-product-tfr-cv-wb)",
      "TFR-CV": "var(--viz-product-tfr-cv)",
      HFCO: "var(--viz-product-hfco)",
      "TFR-8": "var(--viz-product-tfr-8)",
      "TFR-GV": "var(--viz-product-tfr-gv)",
      "CNCV-W": "var(--viz-product-cncv-w)",
      default: "var(--viz-product-default)",
    },
  },
  fonts: {
    heading: "'Pretendard', 'Apple SD Gothic Neo', sans-serif",
    body: "'Pretendard', 'Apple SD Gothic Neo', sans-serif",
  },
} as const;

export type ProductGroup = keyof typeof KBI_BRAND.colors.taskColors;

// PR3 Task 2.3 — 기존 KNOWN_TASK_COLORS 제거, KBI_BRAND.colors.taskColors 단일 source.
// getProductColor 가 KBI_BRAND.colors.taskColors 를 직접 조회하도록 변경.

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
// PR4 Task B.2 — 토큰 참조. globals.css --viz-sq-cluster-* 와 동기화.
const SQ_COLORS: Record<number, string> = {
  16: "var(--viz-sq-warm-teal-light)",
  25: "var(--viz-sq-warm-teal-mid)",
  70: "var(--viz-sq-warm-teal-dark)",
  35: "var(--viz-sq-slate-blue-light)",
  95: "var(--viz-sq-slate-blue-mid)",
  185: "var(--viz-sq-slate-blue-dark)",
  300: "var(--viz-sq-slate-blue-deep)",
  120: "var(--viz-sq-plum-light)",
  400: "var(--viz-sq-plum-deep)",
  50: "var(--viz-sq-forest-light)",
  240: "var(--viz-sq-forest-deep)",
  150: "var(--viz-sq-amber)",
  200: "var(--viz-sq-champagne)",
  633: "var(--viz-sq-crimson)",
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
  // PR4 Task B.3 — CSS context consumer 만 호환 (style.color 등). HTML attribute X.
  return 1.05 / (L + 0.05) >= 3.5
    ? "var(--color-text-inverse)"
    : "var(--color-text-primary)";
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
  const map = KBI_BRAND.colors.taskColors as Record<string, string>;
  if (map[productGroup] && productGroup !== "default") return map[productGroup];
  for (const [key, color] of Object.entries(map)) {
    if (key === "default") continue;
    if (productGroup.includes(key)) return color;
  }
  return hashColor(productGroup);
}
