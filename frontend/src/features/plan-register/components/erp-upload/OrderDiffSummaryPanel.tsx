"use client";

import type { OrderDiffSummary } from "@/features/plan-register/types";

/**
 * T2b — Full 모드 업로드 직후 표시되는 수주 대사 요약 패널.
 * 배치 단위가 아니라 order_id 레벨 분류이므로 별도 패널로 둔다.
 *   - added   : 새 파일에만 있음 (신규 수주)
 *   - updated : 양쪽에 있고 frozen 아님 (delete→reinsert 경유, 내용 갱신 가능)
 *   - deleted : 기존에만 있고 새 파일에 없음 (non-frozen — 실제 삭제)
 *   - preserved_frozen: frozen 으로 보존된 수주 (진행중/완료, 새 파일에 없어도 유지)
 */
export function OrderDiffSummaryPanel({ diff }: { diff: OrderDiffSummary }) {
  const items: Array<{
    key: keyof OrderDiffSummary;
    label: string;
    value: number;
    bg: string;
    fg: string;
  }> = [
    {
      key: "added",
      label: "신규",
      value: diff.added,
      bg: "var(--status-success-bg-soft)",
      fg: "var(--color-success)",
    },
    {
      key: "updated",
      label: "수정",
      value: diff.updated,
      bg: "var(--status-warning-bg)",
      fg: "var(--color-warning)",
    },
    {
      key: "deleted",
      label: "삭제",
      value: diff.deleted,
      bg: "var(--kbi-red-tint-12)",
      fg: "var(--color-danger)",
    },
    {
      key: "preserved_frozen",
      label: "보존",
      value: diff.preserved_frozen,
      bg: "var(--color-bg-muted)",
      fg: "var(--color-text-secondary)",
    },
  ];
  return (
    <div
      className="rounded-lg p-3"
      style={{
        border: "1px solid var(--color-border-default)",
        backgroundColor: "var(--bg-surface)",
      }}
    >
      <p
        className="text-xs font-semibold mb-2"
        style={{ color: "var(--color-text-primary)" }}
      >
        수주 대사 결과 (전체 교체)
      </p>
      <div className="flex gap-2 flex-wrap">
        {items.map((it) => (
          <div
            key={it.key}
            className="flex items-center gap-1.5 rounded px-2 py-1"
            style={{ backgroundColor: it.bg }}
          >
            <span className="text-xs font-medium" style={{ color: it.fg }}>
              {it.label}
            </span>
            <span
              className="text-xs font-semibold tabular-nums"
              style={{ color: it.fg }}
            >
              {it.value.toLocaleString()}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
