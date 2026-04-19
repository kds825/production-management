// Next.js: NEXT_PUBLIC_* 만 클라이언트 번들에 노출됨.
// Backend `feature_flags.py::_TRUTHY` 와 일치해야 split-brain 방지.
const TRUTHY = new Set(["1", "on", "true", "yes"]);

export const FEATURE_FLAG_CASCADE_V2 = TRUTHY.has(
  (process.env.NEXT_PUBLIC_FEATURE_FLAG_CASCADE_V2 ?? "").trim().toLowerCase(),
);
