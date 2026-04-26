/**
 * ProvenanceBadge — 본 카드의 phrasing/룰을 변경한 운영자 의견 #ID link.
 *
 * CEO review R1: "이 카드 룰은 김선임 의견 #237 (4/12) 반영"
 * 운영자가 자기 의견이 실제로 반영됐다는 신호를 카드 헤더에서 즉시 본다.
 */

import type { ProvenanceInfo } from "./decisionCardTypes";

interface Props {
  info: ProvenanceInfo;
}

function formatShort(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export function ProvenanceBadge({ info }: Props) {
  if (info.feedback_ids.length === 0) return null;
  const ids = info.feedback_ids
    .slice(0, 3)
    .map((n) => `#${n}`)
    .join(", ");
  const more =
    info.feedback_ids.length > 3 ? ` 외 ${info.feedback_ids.length - 3}건` : "";
  const date = formatShort(info.last_fixed_at);
  return (
    <a
      href={`/admin/decision-feedback?ids=${info.feedback_ids.join(",")}`}
      className="text-pwc-caption text-pwc-status-info-text bg-pwc-status-info-bg rounded-full px-2 py-0.5 hover:underline"
      aria-label={`반영된 운영자 의견 ${ids}${more}${date ? ` (${date})` : ""}`}
    >
      🛠 의견 {ids}
      {more}
      {date ? ` (${date})` : ""} 반영
    </a>
  );
}
