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
