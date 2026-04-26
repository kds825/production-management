/**
 * ❹ EquipmentDay — 본 설비의 하루 Gantt + sort_label.
 *
 * sort_label 은 백엔드 phrasing.gantt_sort_label() — memory 3차 iteration
 * 정렬 ('① 납기 가까운 순 → ② 전공정 ready 시각 → ③ 색상 인접 순').
 * 본 카드의 batch (is_self=true) 를 highlight.
 */

import type { GanttRow } from "./decisionCardTypes";

interface Props {
  rows: GanttRow[];
  sortLabel: string;
  defaultExpanded: boolean;
}

function formatHm(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function EquipmentDay({ rows, sortLabel, defaultExpanded }: Props) {
  if (rows.length === 0) return null;
  return (
    <details
      className="border-t border-pwc-gray-200 px-4 py-3"
      open={defaultExpanded}
    >
      <summary className="text-pwc-subTitle2 cursor-pointer list-none flex items-center justify-between">
        <span>❹ 이 설비의 하루</span>
        <span className="text-pwc-caption text-pwc-gray-500" aria-hidden>
          ▼
        </span>
      </summary>
      <p className="text-pwc-caption text-pwc-gray-500 mt-2">{sortLabel}</p>
      <ol className="mt-2 space-y-1 list-none pl-0">
        {rows.map((row) => (
          <li
            key={row.task_id}
            className={`flex items-center gap-2 px-2 py-1 rounded text-pwc-subBody ${
              row.is_self
                ? "bg-pwc-primary-100 border border-pwc-primary-300"
                : "bg-pwc-gray-100"
            }`}
          >
            <span className="font-mono text-pwc-gray-500 shrink-0">
              {formatHm(row.start_at)}–{formatHm(row.end_at)}
            </span>
            <span className="flex-1 truncate">{row.label}</span>
            {row.is_self ? (
              <span
                className="text-pwc-badge text-pwc-primary-500"
                aria-label="본 작업"
              >
                ◀ 본 작업
              </span>
            ) : null}
          </li>
        ))}
      </ol>
    </details>
  );
}
