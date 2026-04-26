/**
 * 카드 펼치지 않고도 의사결정 가능한 1줄 (CEO review R2).
 *
 * 백엔드 `phrasing.verdict_summary(batch, audit, solver, schedule_task)` 결과.
 * '✓' / '⚠' / '·' prefix 로 severity 시각 미리 노출.
 */

interface Props {
  text: string;
}

function severityFromPrefix(text: string): "save" | "warn" | "ok" {
  const head = text.trim().charAt(0);
  if (head === "✓") return "save";
  if (head === "⚠") return "warn";
  return "ok";
}

const ACCENT: Record<"save" | "warn" | "ok", string> = {
  save: "border-l-pwc-status-success-text bg-pwc-status-success-bg",
  warn: "border-l-pwc-status-warning-text bg-pwc-status-warning-bg",
  ok: "border-l-pwc-gray-300 bg-pwc-gray-100",
};

export function VerdictSummary({ text }: Props) {
  if (!text) return null;
  const sev = severityFromPrefix(text);
  return (
    <div
      className={`text-pwc-subTitle2 border-l-4 px-3 py-2 ${ACCENT[sev]}`}
      role="status"
      aria-live="polite"
    >
      {text}
    </div>
  );
}
