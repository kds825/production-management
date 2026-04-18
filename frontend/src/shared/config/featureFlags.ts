// Next.js: NEXT_PUBLIC_* 만 클라이언트 번들에 노출됨.
export const FEATURE_FLAG_CASCADE_V2 =
  (process.env.NEXT_PUBLIC_FEATURE_FLAG_CASCADE_V2 ?? "").toLowerCase() ===
  "on";
