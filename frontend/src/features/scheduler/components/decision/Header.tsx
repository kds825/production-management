/**
 * DecisionCard v2 Header — process_label + sub_chip + 거래처 + 납기 + placement.
 *
 * sub_chip 은 시스 'A120 묶음 (작은 설비)' / 연선 '압축연선' 등 — sub-process
 * 가 시각 chip 으로 즉시 파악 가능 (UI review §3.1 — 헤더 sub-label).
 */

import type { ProcessKey } from "./decisionCardTypes";

interface Props {
  processKey: ProcessKey;
  processLabel: string;
  subChip: string | null;
  customerName: string;
  customerPriority: number;
  dueDate: string | null;
  placementText: string;
}

function formatDueDate(iso: string | null): string {
  if (!iso) return "납기 미지정";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "납기 미지정";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

const PROCESS_BORDER: Record<ProcessKey, string> = {
  sheath: "border-pwc-primary-400",
  stranding: "border-pwc-blue-400",
  insulation: "border-pwc-blue-300",
  outsource: "border-pwc-status-info-text",
  default: "border-pwc-gray-300",
};

export function Header({
  processKey,
  processLabel,
  subChip,
  customerName,
  customerPriority,
  dueDate,
  placementText,
}: Props) {
  return (
    <header
      className={`border-l-4 ${PROCESS_BORDER[processKey]} bg-pwc-gray-100 px-4 py-3 space-y-2`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-pwc-title4 m-0">{processLabel}</h2>
        {subChip ? (
          <span className="text-pwc-badge bg-pwc-gray-200 text-pwc-gray-600 rounded-full px-2 py-0.5">
            {subChip}
          </span>
        ) : null}
        {processKey === "outsource" ? (
          <span
            className="text-pwc-badge bg-pwc-status-info-bg text-pwc-status-info-text rounded-full px-2 py-0.5"
            aria-label="외주 작업"
          >
            📦 외주
          </span>
        ) : null}
      </div>
      <div className="text-pwc-subBody text-pwc-gray-600 flex flex-wrap gap-x-4 gap-y-1">
        <span>
          거래처: <strong>{customerName || "—"}</strong>
        </span>
        <span>
          우선순위: <strong>P{customerPriority}</strong>
        </span>
        <span>
          납기: <strong>{formatDueDate(dueDate)}</strong>
        </span>
      </div>
      <div className="text-pwc-body">
        <span className="text-pwc-gray-500">📍 </span>
        {placementText}
      </div>
    </header>
  );
}
