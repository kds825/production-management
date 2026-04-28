"use client";

import {
  ArrowsRightLeftIcon,
  PlusCircleIcon,
  MinusCircleIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
} from "@heroicons/react/24/outline";
import type {
  ScheduleDiffResponse,
  ScheduleDiffEntry,
} from "@/features/scheduler/types/diff";

/**
 * Diff 요약 패널 — 긴급수주 반영(/stage1/update) 직후 change_set_id 로 조회한
 * 변경 전/후 비교를 **최소 요약** 형태로 보여준다.
 *
 * 배치 위치: ErpUploadSection 의 Stage1 result 패널 맨 위 (사용자가 반영 직후 즉시 보는 위치).
 *
 * 디자인 원칙:
 *  - KBI 프로젝트 토큰 (var(--color-*)) 만 사용, raw hex 하드코딩 금지.
 *  - heroicons 라인 아이콘 사용 (이모지 금지).
 *  - 카드/뱃지/리스트는 samildevkit 패턴을 직접 구현 (패키지 import 없음).
 *  - moved(주황)=변경, added(초록)=신규, removed(빨강)=제거, unchanged(회색)=유지
 *
 * MVP 범위:
 *  - 4-카운트 요약 카드
 *  - start_delta_hours 절대값 기준 top 5 moved_tasks 리스트
 *  - snapshot_persisted=false 경고 배너
 */
export function DiffSummaryPanel({
  diff,
  snapshotPersisted,
}: {
  diff: ScheduleDiffResponse;
  snapshotPersisted?: boolean;
}) {
  // start_delta_hours 절대값 기준 top 5 추출.
  // null/undefined delta는 0으로 처리해 하단 배치.
  const topMoved: ScheduleDiffEntry[] = [...diff.moved_tasks]
    .sort((a, b) => {
      const aAbs = Math.abs(a.start_delta_hours ?? 0);
      const bAbs = Math.abs(b.start_delta_hours ?? 0);
      return bAbs - aAbs;
    })
    .slice(0, 5);

  const { moved, added, removed, unchanged, total_before, total_after } =
    diff.summary;

  // KBI 프로젝트 색상 토큰 (CSS 변수 우선, 접근성을 위한 bg/text 쌍)
  const counts: Array<{
    key: string;
    label: string;
    value: number;
    Icon: typeof ArrowsRightLeftIcon;
    bg: string;
    fg: string;
    border: string;
  }> = [
    {
      key: "moved",
      label: "이동",
      value: moved,
      Icon: ArrowsRightLeftIcon,
      bg: "var(--status-warning-bg)", // warning-subtle (주황/노랑 계열 — 일정 이동)
      fg: "var(--color-warning)",
      border: "var(--status-warning-border)",
    },
    {
      key: "added",
      label: "추가",
      value: added,
      Icon: PlusCircleIcon,
      bg: "var(--status-success-bg-soft)", // success-subtle
      fg: "var(--color-success)",
      border: "var(--status-success-border-soft)",
    },
    {
      key: "removed",
      label: "제거",
      value: removed,
      Icon: MinusCircleIcon,
      bg: "var(--kbi-red-tint-12)", // danger-subtle
      fg: "var(--color-danger)",
      border: "var(--kbi-red-tint-20)",
    },
    {
      key: "unchanged",
      label: "유지",
      value: unchanged,
      Icon: CheckCircleIcon,
      bg: "var(--color-bg-muted)",
      fg: "var(--color-text-secondary)",
      border: "var(--color-border-default)",
    },
  ];

  return (
    <div
      className="rounded-lg p-4"
      style={{
        border: "1px solid var(--color-border-default)",
        backgroundColor: "var(--color-bg-elevated)",
      }}
    >
      {/* 헤더 */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <p
            className="text-sm font-semibold"
            style={{ color: "var(--color-text-primary)" }}
          >
            긴급수주 반영 결과
          </p>
          <p
            className="text-small mt-0.5"
            style={{ color: "var(--color-text-secondary)" }}
          >
            스케줄 변경 요약 · 총 {total_before} → {total_after} 건
          </p>
        </div>
        <span
          className="font-mono text-tiny px-2 py-0.5 rounded"
          style={{
            backgroundColor: "var(--color-bg-muted)",
            color: "var(--color-text-tertiary)",
          }}
          title={diff.change_set_id}
        >
          {diff.change_set_id.slice(0, 8)}
        </span>
      </div>

      {/* 스냅샷 미저장 경고 배너 */}
      {snapshotPersisted === false && (
        <div
          className="flex items-start gap-2 rounded-md p-2.5 mb-3 text-xs"
          style={{
            backgroundColor: "var(--kbi-red-tint-5)",
            border: "1px solid var(--kbi-red-tint-20)",
            color: "var(--status-danger-text-strong)",
          }}
        >
          <ExclamationTriangleIcon
            className="shrink-0 mt-0.5"
            width={16}
            height={16}
          />
          <div>
            <span className="font-semibold">스냅샷 저장 실패 — </span>이 변경은
            자동 롤백이 불가능합니다. 결과 검토 후 수동 복구가 필요합니다.
          </div>
        </div>
      )}

      {/* 4-count summary grid */}
      <div className="grid grid-cols-4 gap-2 mb-3">
        {counts.map(({ key, label, value, Icon, bg, fg, border }) => (
          <div
            key={key}
            className="rounded-md px-3 py-2 flex flex-col gap-1"
            style={{ backgroundColor: bg, border: `1px solid ${border}` }}
          >
            <div className="flex items-center gap-1.5">
              <Icon width={12} height={12} style={{ color: fg }} />
              <span
                className="text-tiny font-medium uppercase"
                style={{ color: fg }}
              >
                {label}
              </span>
            </div>
            <span
              className="text-lg font-semibold tabular-nums leading-none"
              style={{ color: fg }}
            >
              {value}
            </span>
          </div>
        ))}
      </div>

      {/* top 5 moved tasks */}
      {topMoved.length > 0 && (
        <div>
          <p
            className="text-xs font-semibold mb-2"
            style={{ color: "var(--color-text-primary)" }}
          >
            시간 변경 Top {topMoved.length}
          </p>
          <div
            className="rounded-md overflow-hidden"
            style={{ border: "1px solid var(--color-border-default)" }}
          >
            {/* header row */}
            <div
              className="grid grid-cols-12 gap-2 px-3 py-1.5 text-tiny font-medium uppercase"
              style={{
                backgroundColor: "var(--color-bg-muted)",
                color: "var(--color-text-tertiary)",
              }}
            >
              <span className="col-span-3">Task ID</span>
              <span className="col-span-3">배치그룹</span>
              <span className="col-span-2">공정</span>
              <span className="col-span-2">색상</span>
              <span className="col-span-2 text-right">Δ 시간</span>
            </div>
            {/* data rows */}
            {topMoved.map((row) => {
              const delta = row.start_delta_hours ?? 0;
              const deltaDisplay =
                delta === 0
                  ? "0h"
                  : `${delta > 0 ? "+" : ""}${delta.toFixed(1)}h`;
              // 양수(뒤로 밀림)=warning, 음수(앞당겨짐)=success
              const deltaColor =
                delta > 0
                  ? "var(--color-warning)"
                  : delta < 0
                    ? "var(--color-success)"
                    : "var(--color-text-tertiary)";
              return (
                <div
                  key={row.task_id}
                  className="grid grid-cols-12 gap-2 px-3 py-1.5 text-xs border-t"
                  style={{
                    borderColor: "var(--color-border-muted)",
                    color: "var(--color-text-primary)",
                  }}
                >
                  <span
                    className="col-span-3 font-mono truncate"
                    title={row.task_id}
                  >
                    {row.task_id}
                  </span>
                  <span
                    className="col-span-3 truncate"
                    style={{ color: "var(--color-text-secondary)" }}
                    title={row.batch_group ?? ""}
                  >
                    {row.batch_group ?? "—"}
                  </span>
                  <span
                    className="col-span-2 truncate"
                    style={{ color: "var(--color-text-secondary)" }}
                  >
                    {row.process_name ?? "—"}
                  </span>
                  <span
                    className="col-span-2 truncate"
                    style={{ color: "var(--color-text-secondary)" }}
                  >
                    {row.sheath_color ?? "—"}
                  </span>
                  <span
                    className="col-span-2 text-right font-mono tabular-nums font-medium"
                    style={{ color: deltaColor }}
                  >
                    {deltaDisplay}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
