"use client";

import { useRef, useState, useCallback, useEffect } from "react";
import Image from "next/image";
import Link from "next/link";
import { BatchSplitReview } from "@/features/plan-register/components/BatchSplitReview";
import type {
  ScheduleDiffResponse,
  ScheduleDiffEntry,
} from "@/features/scheduler/types/diff";
import {
  ArrowsRightLeftIcon,
  PlusCircleIcon,
  MinusCircleIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
} from "@heroicons/react/24/outline";

const ACCEPTED_EXTENSIONS = [".xls", ".xlsx"];
const PRIMARY = "var(--color-brand-primary)";
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(d: Date): string {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function isValidExtension(filename: string): boolean {
  const lower = filename.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

interface WipFile {
  name: string;
  size: number;
  uploadedAt: Date;
  file: File; // 실제 File 객체 — Stage 1 API에 전송용
}

// Stage 1 API response types
interface ParsedOrder {
  order_id: string;
  product_code: string;
  quantity: number;
  due_date: string;
}

// /stage1 및 /stage1/update 응답의 batches 필드. 백엔드 create_batches() 는 개별
// 배치 행이 아니라 집계 요약만 반환한다. 과거 ParsedBatch[] 로 잘못 타이핑되어
// BatchGridWithFrozen 이 .map() 에서 런타임 TypeError 를 일으켰다.
interface BatchSummary {
  total_batches: number;
  by_process: Record<string, number>;
  warnings?: string[];
  outsource_count?: number;
}

// T2a-2: /stage1/update 가 응답에 별도로 내려주는 "이번 파일로 추가된 주문에서
// 나온 planned 배치" 집계. 전체 batches(재생성 총계)와 구분해 UI 표기.
interface NewFromFileSummary {
  total_batches: number;
  by_process: Record<string, number>;
}

// T2a: /stage1/update 응답의 frozen_batches 원소. 진행중/완료/wip_complete 상태의
// 기존 배치를 재생성된 ParsedBatch 리스트와 함께 렌더링하기 위해 사용한다.
interface FrozenBatch {
  batch_id: number;
  batch_group: string | null;
  process_name: string;
  status: "in_progress" | "completed" | "wip_complete";
  customer_name: string | null;
  due_date: string | null;
  item_code: string | null;
  product_group: string | null;
  voltage: string | null;
  sq_mm2: number | null;
  sheath_color: string | null;
  drum_count: number | null;
  total_length_m: number | null;
  sales_order_id: string | null;
  sales_order_line: number | null;
  equipment_code: string | null;
}

// T2b: Full 모드에서 새 파일과 기존 수주 리스트를 대사한 order_id 레벨 분류.
interface OrderDiffSummary {
  added: number;
  updated: number;
  deleted: number;
  preserved_frozen: number;
}

interface SplitChunk {
  lot_index: number;
  order_count: number;
  total_m: number;
  min_due: string;
  max_due: string;
  order_ids: string[];
  batch_ids: number[];
  has_urgent?: boolean;
  min_priority?: number;
  days_until_due?: number;
}

interface SplitCandidate {
  batch_group: string;
  equipment_code: string | null;
  sq_mm2: number;
  lot_count: number;
  total_length_m: number;
  proposed_splits: SplitChunk[];
  gaps_days: number[];
  equipment_load_hours: number;
  auto_split_recommended?: boolean;
  urgency_reason?: string;
}

interface Stage1Result {
  run_label: string;
  parsed_orders: ParsedOrder[];
  batches: BatchSummary;
  // T2a-2: /stage1/update 에서만 내려줌. 이 파일로 실제 추가된 주문 → planned 배치.
  new_from_file?: NewFromFileSummary | null;
  warnings: string[];
  split_candidates?: SplitCandidate[];
  // incremental update 결과 요약 (stage1/update 응답에만 포함될 수 있음)
  added_orders?: number;
  created_batch_groups?: number;
  preserved_batches?: number;
  auto_split_count?: number;
  // P6 긴급수주 diff — /stage1/update 응답에만 포함 (전체 교체 모드에선 null)
  // change_set_id가 있으면 /schedules/change-sets/{id}/diff 로 diff 조회 가능
  change_set_id?: string | null;
  // snapshot_persisted=false 이면 이 변경은 자동 롤백 불가 (사용자 경고 필수)
  snapshot_persisted?: boolean;
  snapshot_count_before?: number;
  snapshot_count_after?: number;
  // T2a: 진행중/완료/wip_complete 로 보존된 배치. 재생성된 batches 와 병합 렌더링.
  frozen_batches?: FrozenBatch[];
  // T2b: Full 모드에서만 세팅. order_id 레벨 대사 결과.
  diff_summary?: OrderDiffSummary | null;
  // 응답 기반 분기(Full diff 패널 조건부 렌더)를 위해 노출.
  upload_mode?: UploadMode;
}

type UploadMode = "full" | "incremental";

interface BatchStatusSummary {
  total_batches: number;
  completed: number;
  in_progress: number;
  scheduled: number;
  wip_complete: number;
  planned: number;
  frozen_count: number;
  frozen_wip_count: number;
  available_wip_count: number;
}

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
function DiffSummaryPanel({
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
            className="text-[11px] mt-0.5"
            style={{ color: "var(--color-text-secondary)" }}
          >
            스케줄 변경 요약 · 총 {total_before} → {total_after} 건
          </p>
        </div>
        <span
          className="font-mono text-[10px] px-2 py-0.5 rounded"
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
                className="text-[10px] font-medium uppercase"
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
              className="grid grid-cols-12 gap-2 px-3 py-1.5 text-[10px] font-medium uppercase"
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

/**
 * T2b — Full 모드 업로드 직후 표시되는 수주 대사 요약 패널.
 * 배치 단위가 아니라 order_id 레벨 분류이므로 별도 패널로 둔다.
 *   - added   : 새 파일에만 있음 (신규 수주)
 *   - updated : 양쪽에 있고 frozen 아님 (delete→reinsert 경유, 내용 갱신 가능)
 *   - deleted : 기존에만 있고 새 파일에 없음 (non-frozen — 실제 삭제)
 *   - preserved_frozen: frozen 으로 보존된 수주 (진행중/완료, 새 파일에 없어도 유지)
 */
function OrderDiffSummaryPanel({ diff }: { diff: OrderDiffSummary }) {
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

/**
 * T2a — 재생성된 배치(집계 요약)와 frozen 배치(FrozenBatch) 병합 렌더링.
 *
 * 백엔드 /stage1/update 는 신규 배치를 개별 행으로 돌려주지 않고 집계만
 * 반환하므로(BatchSummary), frozen 만 행으로 나열하고 신규는 헤더의
 * 공정별 소계 + 총건수로 표시한다. (과거에 ParsedBatch[] 로 가정해 .map 을
 * 호출했다가 NewBatches 가 dict 라서 TypeError 가 발생한 이슈를 해소.)
 */
function BatchGridWithFrozen({
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
        return { label: "진행중", bg: "var(--status-warning-bg)", fg: "var(--color-warning)" };
      case "completed":
        return { label: "완료", bg: "var(--status-success-bg-soft)", fg: "var(--color-success)" };
      case "wip_complete":
        return { label: "WIP완료", bg: "var(--status-info-bg)", fg: "var(--status-info-text)" };
      case "regenerated":
        return { label: "재생성", bg: "var(--neutral-100)", fg: "var(--neutral-text-primary)" };
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

function WipUploadSection({
  wipFile,
  setWipFile,
}: {
  wipFile: WipFile | null;
  setWipFile: (f: WipFile | null) => void;
}) {
  const [isDragOver, setIsDragOver] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [showDeleteHover, setShowDeleteHover] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback(
    (file: File) => {
      setValidationError(null);
      if (!isValidExtension(file.name)) {
        setValidationError(".xls 또는 .xlsx 파일만 업로드 가능합니다.");
        return;
      }
      setWipFile({
        name: file.name,
        size: file.size,
        uploadedAt: new Date(),
        file,
      });
    },
    [setWipFile],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleFile(file);
      e.target.value = "";
    },
    [handleFile],
  );

  const handleDelete = useCallback(() => {
    setWipFile(null);
    setValidationError(null);
  }, [setWipFile]);

  const uploadAreaBorderColor = isDragOver ? PRIMARY : "var(--neutral-300)";

  return (
    <section className="mb-6">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-sm font-semibold" style={{ color: "var(--color-text-primary)" }}>
          2. 재공수량 파일 업로드
        </h3>
        <a
          href={`${API}/pipeline/wip-template`}
          download="wip_template.xlsx"
          className="text-[11px] font-medium px-3 py-1 rounded-md transition-colors"
          style={{
            border: `1px solid ${PRIMARY}`,
            color: PRIMARY,
          }}
        >
          템플릿 다운로드
        </a>
      </div>
      <p className="text-xs text-gray-500 mb-3">
        재공(WIP) 수량 데이터를 업로드해주세요
      </p>

      {!wipFile ? (
        <div
          className="rounded-lg flex flex-col items-center justify-center gap-2 cursor-pointer transition-colors"
          style={{
            border: `1.5px dashed ${uploadAreaBorderColor}`,
            backgroundColor: isDragOver ? "var(--kbi-red-tint-5)" : "var(--bg-surface)",
            minHeight: 120,
            transition: "border-color 150ms ease, background-color 150ms ease",
          }}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => e.key === "Enter" && inputRef.current?.click()}
        >
          <svg
            width="28"
            height="28"
            viewBox="0 0 24 24"
            fill="none"
            stroke={isDragOver ? PRIMARY : "var(--color-text-tertiary)"}
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="17 8 12 3 7 8" />
            <line x1="12" y1="3" x2="12" y2="15" />
          </svg>
          <p className="text-xs text-gray-500">
            클릭하거나 파일을 끌어다 놓으세요
          </p>
          <p className="text-[10px] text-gray-400">.xls, .xlsx 파일 지원</p>
          <button
            onClick={(e) => {
              e.stopPropagation();
              inputRef.current?.click();
            }}
            className="mt-2 px-4 py-1.5 text-xs font-medium rounded-md text-white transition-colors"
            style={{ backgroundColor: PRIMARY }}
          >
            파일 선택
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".xls,.xlsx"
            className="hidden"
            onChange={handleInputChange}
          />
        </div>
      ) : (
        <div
          className="rounded-lg p-3 flex items-center justify-between gap-3"
          style={{ border: "1px solid var(--color-border-default)", backgroundColor: "var(--bg-surface)" }}
          onMouseEnter={() => setShowDeleteHover(true)}
          onMouseLeave={() => setShowDeleteHover(false)}
        >
          <div className="flex items-center gap-3 min-w-0">
            <div
              className="flex-shrink-0 rounded flex items-center justify-center"
              style={{ width: 36, height: 36, backgroundColor: "var(--kbi-red-tint-5)" }}
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke={PRIMARY}
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
            </div>
            <div className="min-w-0">
              <p
                className="text-xs font-medium truncate"
                style={{ color: "var(--color-text-primary)" }}
              >
                {wipFile.name}
              </p>
              <p className="text-[10px] text-gray-400 mt-0.5">
                {formatFileSize(wipFile.size)} &middot;{" "}
                {formatTime(wipFile.uploadedAt)} 업로드
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            {showDeleteHover && (
              <button
                onClick={handleDelete}
                className="text-[11px] font-medium px-2.5 py-1.5 rounded-md transition-colors"
                style={{
                  border: "1px solid var(--color-border-default)",
                  color: "var(--color-text-secondary)",
                  backgroundColor: "transparent",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = "var(--color-brand-primary)";
                  e.currentTarget.style.color = "var(--color-brand-primary)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = "var(--color-border-default)";
                  e.currentTarget.style.color = "var(--color-text-secondary)";
                }}
              >
                삭제하기
              </button>
            )}
            <span
              className="text-[11px] font-medium px-3 py-1.5 rounded-md"
              style={{ backgroundColor: "var(--status-success-bg)", color: "var(--status-success)" }}
            >
              업로드 완료
            </span>
          </div>
        </div>
      )}

      {validationError && (
        <p className="text-[11px] mt-1.5" style={{ color: "var(--color-brand-primary)" }}>
          {validationError}
        </p>
      )}
    </section>
  );
}

// ERP 업로드 + Stage 1 트리거 섹션
function ErpUploadSection({
  wipFile,
  onUploadModeChange,
}: {
  wipFile: WipFile | null;
  onUploadModeChange?: (mode: UploadMode) => void;
}) {
  const [erpFile, setErpFile] = useState<File | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [showDeleteHover, setShowDeleteHover] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<Stage1Result | null>(null);
  const [splitGapDays, setSplitGapDays] = useState(3);
  const [splitModalOpen, setSplitModalOpen] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  // 업로드 모드: "full" = 전체 교체, "incremental" = 긴급수주 추가
  const [uploadMode, setUploadMode] = useState<UploadMode>("full");
  // 긴급수주 추가 기준일자 (incremental 모드 전용)
  const [baseDate, setBaseDate] = useState<string>(() => {
    const today = new Date();
    const y = today.getFullYear();
    const m = String(today.getMonth() + 1).padStart(2, "0");
    const d = String(today.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  });
  // 확인 모달 (Stage 1 실행 전 현황 확인)
  const [confirmModalOpen, setConfirmModalOpen] = useState(false);
  const [batchSummary, setBatchSummary] = useState<BatchStatusSummary | null>(
    null,
  );
  // 성공 토스트 메시지
  const [successToast, setSuccessToast] = useState<string | null>(null);
  // P6 긴급수주 diff — stage1 응답의 change_set_id로 조회한 스케줄 변경 요약
  const [diffData, setDiffData] = useState<ScheduleDiffResponse | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleUploadModeChange = useCallback(
    (mode: UploadMode) => {
      setUploadMode(mode);
      onUploadModeChange?.(mode);
    },
    [onUploadModeChange],
  );

  const handleFile = useCallback((file: File) => {
    setValidationError(null);
    setResult(null);
    setApiError(null);
    // 새 파일 선택 시 기존 diff 상태도 초기화 (혼선 방지)
    setDiffData(null);
    setDiffError(null);
    if (!isValidExtension(file.name)) {
      setValidationError(".xls 또는 .xlsx 파일만 업로드 가능합니다.");
      return;
    }
    setErpFile(file);
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleFile(file);
      e.target.value = "";
    },
    [handleFile],
  );

  const handleDelete = useCallback(() => {
    setErpFile(null);
    setResult(null);
    setApiError(null);
    setValidationError(null);
    setDiffData(null);
    setDiffError(null);
  }, []);

  // 에러 텍스트를 사용자 친화적 메시지로 변환
  const parseApiError = useCallback(async (res: Response): Promise<string> => {
    const errText = await res.text();
    let userMsg = `서버 오류 (${res.status})`;
    try {
      const errJson = JSON.parse(errText);
      const detail = errJson.detail || "";
      // 첫 줄만 추출 (SQL 쿼리/파라미터 제거)
      userMsg =
        typeof detail === "string" ? detail.split("\n")[0] : String(detail);
      if (userMsg.length > 100) userMsg = userMsg.slice(0, 100) + "...";
    } catch {
      if (errText.length > 100) userMsg = errText.slice(0, 100) + "...";
    }
    return userMsg;
  }, []);

  // 실제 Stage 1 API 호출 (확인 모달 통과 후)
  // fromConfirmModal=true 이면 배치가 존재하는 상태에서 확인 모달을 통과한 경로임을 의미한다.
  const executeStage1 = useCallback(
    async (parentRunLabel?: string, fromConfirmModal = false) => {
      if (!erpFile || isRunning) return;
      setConfirmModalOpen(false);
      setIsRunning(true);
      setApiError(null);
      setResult(null);
      setSuccessToast(null);

      try {
        const formData = new FormData();
        formData.append("erp_file", erpFile);
        if (wipFile?.file) {
          formData.append("wip_file", wipFile.file);
        }
        formData.append("split_gap_days", String(splitGapDays));

        let endpoint = `${API}/pipeline/stage1`;

        // 증분 모드이거나 기존 run이 있으면 update 엔드포인트를 사용한다.
        // full 모드에서 확인 모달을 통과한 경우(fromConfirmModal=true)에도 반드시
        // update 엔드포인트를 사용해야 한다. 레거시 /stage1 은 frozen 배치를 포함한
        // 모든 데이터를 삭제하므로, /pipeline/runs 조회가 실패해 parentRunLabel이
        // undefined 인 상황에서도 /stage1/update 를 호출해야 데이터 손실을 막는다.
        // 백엔드는 parent_run_label 미전달 시 최신 run_label 을 자동으로 탐지한다.
        if (
          uploadMode === "incremental" ||
          fromConfirmModal ||
          parentRunLabel
        ) {
          endpoint = `${API}/pipeline/stage1/update`;
          formData.append("upload_mode", uploadMode);
          if (parentRunLabel) {
            formData.append("parent_run_label", parentRunLabel);
          }
          // 기준일자: incremental 모드에서만 전송
          if (uploadMode === "incremental" && baseDate) {
            formData.append("base_date", baseDate);
          }
        }

        const res = await fetch(endpoint, {
          method: "POST",
          body: formData,
        });

        if (!res.ok) {
          throw new Error(await parseApiError(res));
        }

        const data: Stage1Result = await res.json();
        setResult(data);

        // 업데이트 결과 요약 토스트 표시
        if (parentRunLabel && data.added_orders !== undefined) {
          const parts: string[] = [];
          if (data.added_orders) parts.push(`${data.added_orders}건 추가`);
          if (data.created_batch_groups)
            parts.push(`${data.created_batch_groups}개 배치그룹 생성`);
          if (data.preserved_batches)
            parts.push(`${data.preserved_batches}개 보존`);
          if (data.auto_split_count)
            parts.push(`⚡ ${data.auto_split_count}개 자동분할`);
          if (parts.length > 0) setSuccessToast(parts.join(", "));
        }

        // P6: change_set_id 가 있으면 diff 를 비동기 조회해 요약 패널에 표시.
        // diff 조회 실패는 Stage1 성공과 독립적이므로 setApiError 를 쓰지 않고
        // 전용 diffError 상태로 격리한다 (Stage1 결과 표시는 방해 금지).
        if (data.change_set_id) {
          setDiffLoading(true);
          setDiffError(null);
          try {
            const diffRes = await fetch(
              `${API}/schedules/change-sets/${data.change_set_id}/diff`,
            );
            if (diffRes.ok) {
              const diffJson: ScheduleDiffResponse = await diffRes.json();
              setDiffData(diffJson);
            } else {
              setDiffError(`Diff 조회 실패 (${diffRes.status})`);
            }
          } catch (diffErr) {
            setDiffError(
              diffErr instanceof Error
                ? diffErr.message
                : "Diff 조회 중 오류 발생",
            );
          } finally {
            setDiffLoading(false);
          }
        }
      } catch (err) {
        setApiError(
          err instanceof Error
            ? err.message
            : "알 수 없는 오류가 발생했습니다.",
        );
      } finally {
        setIsRunning(false);
      }
    },
    [
      erpFile,
      isRunning,
      splitGapDays,
      wipFile,
      uploadMode,
      baseDate,
      parseApiError,
    ],
  );

  // Stage 1 실행 버튼 클릭 핸들러 — 기존 배치가 있으면 확인 모달 선표시
  const handleRunStage1 = useCallback(async () => {
    if (!erpFile || isRunning) return;

    // 1. 현재 배치 상태 조회
    let summary: BatchStatusSummary | null = null;
    try {
      const res = await fetch(`${API}/pipeline/batch-status-summary`);
      if (res.ok) {
        summary = await res.json();
      }
    } catch {
      // 상태 조회 실패 시 조용히 무시하고 직접 실행
    }

    // 2. 기존 배치가 있거나 증분 모드이면 확인 모달 표시
    if (
      summary &&
      (summary.total_batches > 0 || uploadMode === "incremental")
    ) {
      setBatchSummary(summary);
      setConfirmModalOpen(true);
      return;
    }

    // 3. 기존 배치 없음 + 전체 모드 → 레거시 /stage1 직접 실행
    await executeStage1();
  }, [erpFile, isRunning, uploadMode, executeStage1]);

  // 확인 모달에서 "업로드 진행" 클릭 시 — 최신 run_label을 받아 executeStage1 호출
  // fromConfirmModal=true 를 항상 전달해 full 모드에서도 /stage1/update 를 보장한다.
  const handleConfirmUpload = useCallback(async () => {
    let parentRunLabel: string | undefined;
    try {
      const res = await fetch(`${API}/pipeline/runs`);
      if (res.ok) {
        const runs: Array<{ run_label: string }> = await res.json();
        if (runs.length > 0) parentRunLabel = runs[0].run_label;
      }
    } catch {
      // run_label 조회 실패해도 fromConfirmModal=true 덕분에 /stage1/update 가 사용된다
    }
    await executeStage1(parentRunLabel, true);
  }, [executeStage1]);

  const uploadAreaBorderColor = isDragOver ? PRIMARY : "var(--neutral-300)";

  // 업로드 모드별 설명 텍스트
  const modeDescription =
    uploadMode === "full"
      ? "ERP 전체 파일로 기존 계획을 교체합니다. 진행중/완료 배치는 보존됩니다."
      : "긴급수주 파일의 주문만 기존 계획에 추가합니다.";

  return (
    <section className="mb-6">
      <h3 className="text-sm font-semibold mb-1" style={{ color: "var(--color-text-primary)" }}>
        3. ERP 작업지시 파일 업로드 및 Stage 1 실행
      </h3>
      <p className="text-xs text-gray-500 mb-3">
        ERP에서 추출한 작업지시서 파일(.xls)을 업로드하고 작업지시서를
        생성하세요
      </p>

      {/* 업로드 모드 세그먼트 컨트롤 */}
      <div className="mb-3">
        <div
          className="inline-flex rounded-lg overflow-hidden"
          style={{ border: "1px solid var(--color-border-default)" }}
        >
          {(
            [
              { value: "full", label: "전체 교체" },
              { value: "incremental", label: "긴급수주 추가" },
            ] as const
          ).map(({ value, label }) => (
            <button
              key={value}
              onClick={() => handleUploadModeChange(value)}
              className="px-4 py-1.5 text-xs font-medium transition-colors"
              style={{
                backgroundColor: uploadMode === value ? PRIMARY : "var(--bg-surface)",
                color: uploadMode === value ? "var(--bg-surface)" : "var(--neutral-600)",
                borderRight: value === "full" ? "1px solid var(--color-border-default)" : undefined,
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <p className="text-[11px] text-gray-500 mt-1.5">{modeDescription}</p>
        {uploadMode === "incremental" && (
          <div className="mt-2 flex items-center gap-2 flex-wrap">
            <span className="text-xs text-gray-600 font-medium">기준일자</span>
            <input
              type="date"
              value={baseDate}
              onChange={(e) => setBaseDate(e.target.value)}
              className="text-xs px-2 py-1 rounded-md"
              style={{ border: "1px solid var(--neutral-300)", color: "var(--color-text-primary)" }}
            />
            <span className="text-[11px] text-gray-400">
              이전 배치 고정 · 이후 배치는 긴급수주와 합산 재생성
            </span>
          </div>
        )}
      </div>

      {/* 성공 토스트 */}
      {successToast && (
        <div
          className="mb-3 rounded-lg px-3 py-2 text-xs flex items-center justify-between"
          style={{
            backgroundColor: "var(--status-success-bg-soft)",
            border: "1px solid var(--status-success-border-soft)",
            color: "var(--status-success-text-deep)",
          }}
        >
          <span>
            <span className="font-semibold">업데이트 완료: </span>
            {successToast}
          </span>
          <button
            onClick={() => setSuccessToast(null)}
            className="ml-3 text-green-400 hover:text-green-600 text-base leading-none"
          >
            ✕
          </button>
        </div>
      )}

      {/* File upload area */}
      {!erpFile ? (
        <div
          className="rounded-lg flex flex-col items-center justify-center gap-2 cursor-pointer"
          style={{
            border: `1.5px dashed ${uploadAreaBorderColor}`,
            backgroundColor: isDragOver ? "var(--kbi-red-tint-5)" : "var(--bg-surface)",
            minHeight: 120,
            transition: "border-color 150ms ease, background-color 150ms ease",
          }}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => e.key === "Enter" && inputRef.current?.click()}
        >
          <svg
            width="28"
            height="28"
            viewBox="0 0 24 24"
            fill="none"
            stroke={isDragOver ? PRIMARY : "var(--color-text-tertiary)"}
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="17 8 12 3 7 8" />
            <line x1="12" y1="3" x2="12" y2="15" />
          </svg>
          <p className="text-xs text-gray-500">
            클릭하거나 파일을 끌어다 놓으세요
          </p>
          <p className="text-[10px] text-gray-400">.xls, .xlsx 파일 지원</p>
          <button
            onClick={(e) => {
              e.stopPropagation();
              inputRef.current?.click();
            }}
            className="mt-2 px-4 py-1.5 text-xs font-medium rounded-md text-white"
            style={{ backgroundColor: PRIMARY }}
          >
            파일 선택
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".xls,.xlsx"
            className="hidden"
            onChange={handleInputChange}
          />
        </div>
      ) : (
        <div
          className="rounded-lg p-3 flex items-center justify-between gap-3"
          style={{ border: "1px solid var(--color-border-default)", backgroundColor: "var(--bg-surface)" }}
          onMouseEnter={() => setShowDeleteHover(true)}
          onMouseLeave={() => setShowDeleteHover(false)}
        >
          <div className="flex items-center gap-3 min-w-0">
            <div
              className="flex-shrink-0 rounded flex items-center justify-center"
              style={{ width: 36, height: 36, backgroundColor: "var(--kbi-red-tint-5)" }}
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke={PRIMARY}
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
            </div>
            <div className="min-w-0">
              <p
                className="text-xs font-medium truncate"
                style={{ color: "var(--color-text-primary)" }}
              >
                {erpFile.name}
              </p>
              <p className="text-[10px] text-gray-400 mt-0.5">
                {formatFileSize(erpFile.size)}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            {showDeleteHover && !isRunning && !result && (
              <button
                onClick={handleDelete}
                className="text-[11px] font-medium px-2.5 py-1.5 rounded-md transition-colors"
                style={{
                  border: "1px solid var(--color-border-default)",
                  color: "var(--color-text-secondary)",
                  backgroundColor: "transparent",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = "var(--color-brand-primary)";
                  e.currentTarget.style.color = "var(--color-brand-primary)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = "var(--color-border-default)";
                  e.currentTarget.style.color = "var(--color-text-secondary)";
                }}
              >
                삭제하기
              </button>
            )}
            {/* "작업지시서 생성" trigger button */}
            <button
              onClick={handleRunStage1}
              disabled={isRunning || !!result}
              className="text-[11px] font-medium px-3 py-1.5 rounded-md flex items-center gap-1.5 transition-colors"
              style={{
                backgroundColor: isRunning || result ? "var(--color-border-default)" : PRIMARY,
                color: isRunning || result ? "var(--color-text-tertiary)" : "var(--bg-surface)",
                cursor: isRunning || result ? "not-allowed" : "pointer",
              }}
            >
              {isRunning ? (
                <>
                  <svg
                    className="animate-spin"
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                  >
                    <path
                      d="M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"
                      opacity="0.25"
                    />
                    <path d="M21 12a9 9 0 0 0-9-9" />
                  </svg>
                  처리 중...
                </>
              ) : result ? (
                "생성 완료"
              ) : (
                "작업지시서 생성"
              )}
            </button>
          </div>
        </div>
      )}

      {validationError && (
        <p className="text-[11px] mt-1.5" style={{ color: "var(--color-brand-primary)" }}>
          {validationError}
        </p>
      )}

      {/* API error */}
      {apiError && (
        <div
          className="mt-3 rounded-lg p-3 text-xs"
          style={{
            backgroundColor: "var(--kbi-red-tint-5)",
            border: "1px solid var(--kbi-red-tint-20)",
            color: "var(--status-danger-text-strong)",
          }}
        >
          <span className="font-semibold">오류: </span>
          {apiError}
        </div>
      )}

      {/* Stage 1 result */}
      {result && (
        <div className="mt-4 space-y-3">
          {/* P6 긴급수주 diff 요약 — change_set_id 가 있을 때만 표시.
              로딩/에러/완료 세 가지 상태를 인라인으로 처리. */}
          {result.change_set_id && diffLoading && (
            <div
              className="rounded-lg p-3 text-xs flex items-center gap-2"
              style={{
                border: "1px solid var(--color-border-default)",
                backgroundColor: "var(--color-bg-muted)",
                color: "var(--color-text-secondary)",
              }}
            >
              <svg
                className="animate-spin"
                width="12"
                height="12"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
              >
                <path
                  d="M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"
                  opacity="0.25"
                />
                <path d="M21 12a9 9 0 0 0-9-9" />
              </svg>
              스케줄 변경 비교 조회 중...
            </div>
          )}
          {result.change_set_id && diffError && (
            <div
              className="rounded-lg p-3 text-xs"
              style={{
                backgroundColor: "var(--kbi-red-tint-5)",
                border: "1px solid var(--kbi-red-tint-20)",
                color: "var(--status-danger-text-strong)",
              }}
            >
              <span className="font-semibold">Diff 조회 실패: </span>
              {diffError}
            </div>
          )}
          {diffData && (
            <DiffSummaryPanel
              diff={diffData}
              snapshotPersisted={result.snapshot_persisted}
            />
          )}

          {/* Warnings */}
          {result.warnings && result.warnings.length > 0 && (
            <div
              className="rounded-lg p-3 text-xs space-y-1"
              style={{
                backgroundColor: "var(--status-warning-bg)",
                border: "1px solid var(--status-warning-border)",
              }}
            >
              <p className="font-semibold text-yellow-800">경고</p>
              {result.warnings.map((w, i) => (
                <p key={i} className="text-yellow-700">
                  {w}
                </p>
              ))}
            </div>
          )}

          {/* Full 모드 diff 요약 (T2b) — 새 파일과 기존 수주를 order_id 레벨에서 대사 */}
          {result.upload_mode === "full" && result.diff_summary && (
            <OrderDiffSummaryPanel diff={result.diff_summary} />
          )}

          {/* Batch summary — frozen(진행중/완료 보존) + 재생성 + 이 파일로 추가 (T2a) */}
          {((result.batches?.total_batches ?? 0) > 0 ||
            (result.frozen_batches && result.frozen_batches.length > 0)) && (
            <BatchGridWithFrozen
              frozenBatches={result.frozen_batches ?? []}
              newSummary={
                result.batches ?? { total_batches: 0, by_process: {} }
              }
              newFromFile={result.new_from_file ?? null}
            />
          )}

          {/* Parsed orders summary */}
          {result.parsed_orders && result.parsed_orders.length > 0 && (
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
                파싱된 주문 ({result.parsed_orders.length}건)
              </p>
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {result.parsed_orders.map((order) => (
                  <div
                    key={order.order_id}
                    className="flex items-center gap-3 text-xs py-1 border-b last:border-b-0"
                    style={{ borderColor: "var(--neutral-100)" }}
                  >
                    <span className="font-mono text-gray-500 shrink-0">
                      {order.order_id}
                    </span>
                    <span className="font-medium text-gray-700 truncate">
                      {order.product_code}
                    </span>
                    <span className="text-gray-400 shrink-0">
                      {order.quantity.toLocaleString()} m
                    </span>
                    <span className="text-gray-400 shrink-0">
                      {order.due_date}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Batch split review modal trigger */}
          {result.split_candidates && result.split_candidates.length > 0 && (
            <>
              <button
                onClick={() => setSplitModalOpen(true)}
                className="flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-medium transition-colors"
                style={{
                  border: "1px solid var(--status-info-bg)",
                  backgroundColor: "var(--status-info-bg-soft)",
                  color: "var(--status-info-text)",
                }}
              >
                <span>✂️</span>
                배치 분할 검토
                <span
                  className="rounded-full px-1.5 py-0.5 text-[10px] font-bold"
                  style={{ backgroundColor: "var(--status-info-bg)", color: "var(--status-info-text)" }}
                >
                  {result.split_candidates.length}
                </span>
              </button>

              {/* Modal */}
              {splitModalOpen && (
                <div
                  className="fixed inset-0 z-50 flex items-center justify-center"
                  style={{ backgroundColor: "rgba(0,0,0,0.4)" }}
                  onClick={(e) => {
                    if (e.target === e.currentTarget) setSplitModalOpen(false);
                  }}
                >
                  <div
                    className="relative rounded-xl shadow-2xl max-w-4xl w-full max-h-[85vh] overflow-y-auto"
                    style={{ backgroundColor: "var(--bg-surface)" }}
                  >
                    <div
                      className="sticky top-0 z-10 flex items-center justify-between px-5 py-3 border-b"
                      style={{ backgroundColor: "var(--neutral-100)" }}
                    >
                      <span className="text-sm font-semibold text-gray-800">
                        ✂️ 배치 분할 검토
                      </span>
                      <button
                        onClick={() => setSplitModalOpen(false)}
                        className="text-gray-400 hover:text-gray-600 text-lg"
                      >
                        ✕
                      </button>
                    </div>
                    <div className="p-5">
                      <BatchSplitReview
                        candidates={result.split_candidates}
                        runLabel={result.run_label}
                        gapDays={splitGapDays}
                        onGapDaysChange={setSplitGapDays}
                        onApplied={() => {}}
                        onClose={() => setSplitModalOpen(false)}
                      />
                    </div>
                  </div>
                </div>
              )}
            </>
          )}

          {/* Link to scheduling-review */}
          <div className="flex justify-end">
            <Link
              href="/scheduling-review"
              className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md text-white transition-opacity hover:opacity-80"
              style={{ backgroundColor: PRIMARY }}
            >
              스케줄링 검토 페이지에서 결과 확인
              <svg
                width="12"
                height="12"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="5" y1="12" x2="19" y2="12" />
                <polyline points="12 5 19 12 12 19" />
              </svg>
            </Link>
          </div>
        </div>
      )}
      {/* 확인 모달 — Stage 1 실행 전 현재 배치 현황 표시 */}
      {confirmModalOpen && batchSummary && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ backgroundColor: "rgba(0,0,0,0.4)" }}
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              setConfirmModalOpen(false);
            }
          }}
        >
          <div
            className="rounded-xl shadow-2xl w-full max-w-md overflow-hidden"
            style={{ backgroundColor: "var(--bg-surface)" }}
          >
            <div
              className="px-5 py-3 border-b"
              style={{ backgroundColor: "var(--neutral-100)" }}
            >
              <span className="text-sm font-semibold text-gray-800">
                현재 생산 현황 확인
              </span>
            </div>
            <div className="p-5 space-y-3">
              {/* 상태별 배치 수 bar */}
              <div className="space-y-2">
                {[
                  {
                    key: "completed",
                    label: "완료",
                    color: "var(--status-success)",
                    frozen: true,
                  },
                  {
                    key: "in_progress",
                    label: "진행중",
                    color: "var(--status-info)",
                    frozen: true,
                  },
                  {
                    key: "scheduled",
                    label: "스케줄링 완료",
                    color: "var(--viz-violet)",
                    frozen: true,
                  },
                  {
                    key: "wip_complete",
                    label: "WIP 매칭 완료",
                    color: "var(--status-warning)",
                    frozen: true,
                  },
                  {
                    key: "planned",
                    label: "계획",
                    color: "var(--neutral-300)",
                    frozen: false,
                  },
                ].map(({ key, label, color, frozen }) => {
                  const count =
                    (batchSummary as unknown as Record<string, number>)[key] ??
                    0;
                  if (count === 0) return null;
                  return (
                    <div key={key} className="flex items-center gap-2 text-xs">
                      <div
                        className="h-3 rounded"
                        style={{
                          width: `${Math.max(20, (count / Math.max(batchSummary.total_batches, 1)) * 200)}px`,
                          backgroundColor: color,
                        }}
                      />
                      <span className="text-gray-700">
                        {label}: <b>{count}개</b>{" "}
                        <span className="text-gray-400">
                          ({frozen ? "동결" : "재계산 대상"})
                        </span>
                      </span>
                    </div>
                  );
                })}
              </div>

              {/* WIP 현황 */}
              {(batchSummary.frozen_wip_count > 0 ||
                batchSummary.available_wip_count > 0) && (
                <div className="text-xs text-gray-500 pt-1 border-t">
                  WIP: 사용중 {batchSummary.frozen_wip_count}건 (보존) / 가용{" "}
                  {batchSummary.available_wip_count}건
                </div>
              )}

              {/* 모드 설명 */}
              <p className="text-xs text-gray-500 pt-1">
                {uploadMode === "incremental"
                  ? "긴급수주 파일의 주문이 기존 계획에 추가됩니다."
                  : "기존 계획이 새 파일로 교체됩니다."}{" "}
                동결된 배치는 보존됩니다.
              </p>
            </div>
            <div className="flex justify-end gap-2 px-5 py-3 border-t bg-gray-50">
              <button
                onClick={() => setConfirmModalOpen(false)}
                className="px-4 py-1.5 text-xs font-medium text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-100"
              >
                취소
              </button>
              <button
                onClick={() => {
                  setConfirmModalOpen(false);
                  handleConfirmUpload();
                }}
                className="px-4 py-1.5 text-xs font-medium text-white rounded-lg hover:opacity-90"
                style={{ backgroundColor: PRIMARY }}
              >
                업로드 진행
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function getKstToday(): string {
  const kst = new Date(
    new Date().toLocaleString("en-US", { timeZone: "Asia/Seoul" }),
  );
  const y = kst.getFullYear();
  const m = String(kst.getMonth() + 1).padStart(2, "0");
  const d = String(kst.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export default function PlanRegisterPage() {
  // baseDate 초기화: SSR 은 오늘 날짜를 반환해 서버 렌더를 결정적으로 유지하고,
  // client lazy-init 은 localStorage 에 저장된 값을 읽어 사용자 선호를 복원한다.
  // 잠재적 hydration mismatch 는 input[value] 차원에서만 발생하며
  // suppressHydrationWarning 으로 허용(사용자 입력 컨트롤이므로 자연스러움).
  // 이 패턴은 "setState-in-effect" 안티패턴을 피하기 위한 공식 대안.
  const [baseDate, setBaseDate] = useState<string>(() => {
    if (typeof window === "undefined") return getKstToday();
    const stored = window.localStorage.getItem("plan_base_date");
    if (stored && stored.length === 8) {
      return `${stored.slice(0, 4)}-${stored.slice(4, 6)}-${stored.slice(6, 8)}`;
    }
    return getKstToday();
  });
  const [wipFile, setWipFile] = useState<WipFile | null>(null);
  // incremental 모드 선택 시 WIP 섹션을 흐리게 처리하기 위해 모드를 상위에서 관리
  const [erpUploadMode, setErpUploadMode] = useState<UploadMode>("full");

  // 최초 방문 시 localStorage 기본값을 오늘 날짜로 채워둔다(외부 시스템 동기화만,
  // setState 호출 없음 — cascading render 방지).
  useEffect(() => {
    if (!localStorage.getItem("plan_base_date")) {
      localStorage.setItem("plan_base_date", getKstToday().replace(/-/g, ""));
    }
  }, []);

  return (
    <div
      className="flex flex-col h-full overflow-hidden"
      style={{ backgroundColor: "var(--color-bg-muted)" }}
    >
      {/* 헤더 — KBI 로고 + 페이지 제목 */}
      <header className="h-14 bg-white border-b border-gray-200 flex items-center px-6 sticky top-0 z-50 shrink-0">
        <div className="flex items-center gap-3">
          <Image
            src="/kbi-group-logo.jpg"
            alt="KBI GROUP"
            width={72}
            height={36}
            className="object-contain"
          />
          <div className="h-6 w-px bg-gray-200" />
          <h1
            className="text-sm font-semibold"
            style={{ color: "var(--kbi-brown)", letterSpacing: "-0.02em" }}
          >
            생산계획등록
          </h1>
        </div>
      </header>

      {/* 본문 */}
      <div className="flex-1 overflow-auto px-6 py-6 flex flex-col gap-0">
        {/* 계획 기준일자 */}
        <section className="mb-6">
          <h3
            className="text-sm font-semibold mb-1"
            style={{ color: "var(--color-text-primary)" }}
          >
            1. 계획 기준일자
          </h3>
          <p className="text-xs text-gray-500 mb-3">
            생산계획의 시작 기준일을 선택하세요 (기본: 오늘)
          </p>
          <input
            type="date"
            value={baseDate}
            // lazy-init 에서 localStorage 값을 읽으므로 SSR(오늘) ↔ client(저장값)
            // 가 다를 수 있다. 사용자 선호 복원 용도라 첫 페인트에서 잠시 다른
            // 값이 보이는 것은 의도된 동작이며 hydration warning 만 가린다.
            suppressHydrationWarning
            onChange={(e) => {
              setBaseDate(e.target.value);
              if (typeof window !== "undefined") {
                localStorage.setItem(
                  "plan_base_date",
                  e.target.value.replace(/-/g, ""),
                );
              }
            }}
            className="rounded-md px-3 py-2 text-sm border"
            style={{
              borderColor: "var(--neutral-300)",
              color: "var(--color-text-primary)",
              outline: "none",
            }}
          />
        </section>

        {/* incremental 모드에서는 WIP 섹션을 반투명하게 처리해 비활성 상태를 시각화 */}
        <div
          style={{
            opacity: erpUploadMode === "incremental" ? 0.5 : 1,
            transition: "opacity 150ms ease",
            pointerEvents: erpUploadMode === "incremental" ? "none" : undefined,
          }}
        >
          <WipUploadSection wipFile={wipFile} setWipFile={setWipFile} />
        </div>
        <ErpUploadSection
          wipFile={wipFile}
          onUploadModeChange={setErpUploadMode}
        />
      </div>
    </div>
  );
}
