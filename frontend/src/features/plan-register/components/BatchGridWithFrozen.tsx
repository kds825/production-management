"use client";

import type {
  BatchSummary,
  FrozenBatch,
  NewFromFileSummary,
} from "@/features/plan-register/types";

const PRIMARY = "var(--color-brand-primary)";

/**
 * T2a — 재생성된 배치(집계 요약)와 frozen 배치(FrozenBatch) 병합 렌더링.
 *
 * 백엔드 /stage1/update 는 신규 배치를 개별 행으로 돌려주지 않고 집계만
 * 반환하므로(BatchSummary), frozen 만 행으로 나열하고 신규는 헤더의
 * 공정별 소계 + 총건수로 표시한다. (과거에 ParsedBatch[] 로 가정해 .map 을
 * 호출했다가 NewBatches 가 dict 라서 TypeError 가 발생한 이슈를 해소.)
 */
export function BatchGridWithFrozen({
  frozenBatches,
  newSummary,
  newFromFile,
}: {
  frozenBatches: FrozenBatch[];
  newSummary: BatchSummary;
  newFromFile?: NewFromFileSummary | null;
}) {
  // "재생성" = non-frozen 수주 전체에서 다시 만들어진 planned 배치 총계
  const regeneratedCount = newSummary.total_batches ?? 0;
  const fromFileCount = newFromFile?.total_batches ?? 0;
  const total = frozenBatches.length + regeneratedCount;
  // 상태별 카운트 (헤더 요약용)
  const counts = {
    in_progress: frozenBatches.filter((b) => b.status === "in_progress").length,
    completed: frozenBatches.filter((b) => b.status === "completed").length,
    wip_complete: frozenBatches.filter((b) => b.status === "wip_complete")
      .length,
    regenerated: regeneratedCount,
    from_file: fromFileCount,
  };

  const statusBadge = (
    status: FrozenBatch["status"] | "regenerated" | "from_file",
  ): { label: string; bg: string; fg: string } => {
    switch (status) {
      case "in_progress":
        return {
          label: "진행중",
          bg: "var(--status-warning-bg)",
          fg: "var(--color-warning)",
        };
      case "completed":
        return {
          label: "완료",
          bg: "var(--status-success-bg-soft)",
          fg: "var(--color-success)",
        };
      case "wip_complete":
        return {
          label: "WIP완료",
          bg: "var(--status-info-bg)",
          fg: "var(--status-info-text)",
        };
      case "regenerated":
        return {
          label: "재생성",
          bg: "var(--neutral-100)",
          fg: "var(--neutral-text-primary)",
        };
      case "from_file":
        return { label: "신규 파일", bg: "var(--kbi-red-tint-5)", fg: PRIMARY };
    }
  };

  return (
    <div
      className="rounded-lg p-3"
      style={{
        border: "1px solid var(--color-border-default)",
        backgroundColor: "var(--bg-surface)",
      }}
    >
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <p
          className="text-xs font-semibold"
          style={{ color: "var(--color-text-primary)" }}
        >
          배치 ({total}개)
        </p>
        <div className="flex items-center gap-1.5 text-xs">
          {counts.in_progress > 0 && (
            <span style={{ color: "var(--color-warning)" }}>
              진행중 {counts.in_progress}
            </span>
          )}
          {counts.completed > 0 && (
            <span style={{ color: "var(--color-success)" }}>
              완료 {counts.completed}
            </span>
          )}
          {counts.wip_complete > 0 && (
            <span style={{ color: "var(--status-info-text)" }}>
              WIP완료 {counts.wip_complete}
            </span>
          )}
          {counts.regenerated > 0 && (
            <span style={{ color: "var(--neutral-text-primary)" }}>
              재생성 {counts.regenerated}
            </span>
          )}
          {counts.from_file > 0 && (
            <span style={{ color: PRIMARY }}>신규 파일 {counts.from_file}</span>
          )}
        </div>
      </div>
      <div className="space-y-1">
        {frozenBatches.map((b) => {
          const badge = statusBadge(b.status);
          return (
            <div
              key={`frozen-${b.batch_id}`}
              className="flex items-center gap-3 text-xs py-1 border-b last:border-b-0"
              style={{ borderColor: "var(--neutral-100)" }}
            >
              <span
                className="rounded px-1.5 py-0.5 font-medium"
                style={{ backgroundColor: badge.bg, color: badge.fg }}
              >
                {badge.label}
              </span>
              <span
                className="rounded px-1.5 py-0.5 font-medium"
                style={{
                  backgroundColor: "var(--color-bg-muted)",
                  color: "var(--color-text-secondary)",
                }}
              >
                배치#{b.batch_id}
              </span>
              <span className="font-medium text-gray-700">
                {b.process_name}
              </span>
              {b.customer_name && (
                <span className="text-gray-400 truncate">
                  {b.customer_name}
                </span>
              )}
              {b.total_length_m !== null && (
                <span className="text-gray-400 ml-auto tabular-nums">
                  {b.total_length_m.toLocaleString()} m
                </span>
              )}
            </div>
          );
        })}
        {regeneratedCount > 0 && (
          <div
            className="flex items-center gap-3 text-xs py-1 border-b last:border-b-0 flex-wrap"
            style={{ borderColor: "var(--neutral-100)" }}
          >
            <span
              className="rounded px-1.5 py-0.5 font-medium"
              style={{
                backgroundColor: statusBadge("regenerated").bg,
                color: statusBadge("regenerated").fg,
              }}
            >
              {statusBadge("regenerated").label}
            </span>
            <span className="font-medium text-gray-700">
              재생성(비동결 전체) {regeneratedCount}개
            </span>
            {Object.entries(newSummary.by_process ?? {}).map(
              ([process, count]) => (
                <span key={process} className="text-gray-500 tabular-nums">
                  {process} {count}
                </span>
              ),
            )}
          </div>
        )}
        {fromFileCount > 0 && (
          <div
            className="flex items-center gap-3 text-xs py-1 border-b last:border-b-0 flex-wrap"
            style={{ borderColor: "var(--neutral-100)" }}
          >
            <span
              className="rounded px-1.5 py-0.5 font-medium"
              style={{
                backgroundColor: statusBadge("from_file").bg,
                color: statusBadge("from_file").fg,
              }}
            >
              {statusBadge("from_file").label}
            </span>
            <span className="font-medium text-gray-700">
              이 파일로 추가 {fromFileCount}개
            </span>
            {Object.entries(newFromFile?.by_process ?? {}).map(
              ([process, count]) => (
                <span key={process} className="text-gray-500 tabular-nums">
                  {process} {count}
                </span>
              ),
            )}
          </div>
        )}
      </div>
    </div>
  );
}
