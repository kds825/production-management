/**
 * BatchInspector — 우측 수주 상세 + 감사 트레일 패널.
 *
 * 책임 (Week 7 Task 7B.1 분리):
 *   - 선택된 task 의 메타데이터 헤더 (배치 그룹 라벨 + 공정 흐름 네비)
 *   - 작업 요약 (규격/설비/길이/기간/선속/배치 상태)
 *   - 배치 상태 토글 (planned ↔ in_progress ↔ completed) — DB 갱신 + 로컬 fallback
 *   - 배치 그룹 수주 목록 테이블 (BatchGroupOrderTable) 또는 단일 수주 정보 fallback
 *   - 작업 시간 구성 (computeTimeBreakdown)
 *   - AI 스케줄링 근거 (auditPanel.data.explanation/reasoning)
 *
 * 모든 fetch/캐시는 useBatchInspector hook 이 담당. 이 컴포넌트는 순수 표시 + 인라인 액션.
 */
"use client";

import { useScheduleStore } from "../store/scheduleStore";
import { computeTimeBreakdown } from "../utils/ganttUtils";
import { BatchGroupOrderTable } from "./BatchGroupOrderTable";
import type { BatchGroupOrder } from "./BatchGroupOrderTable";
import type {
  AuditPanelState,
  ProcessFlowEntry,
} from "../hooks/useBatchInspector";

const API_BASE = "http://localhost:8000/api";

// 배치 상태 순환 + 시각 설정
const STATUS_CYCLE: Record<string, string> = {
  planned: "in_progress",
  in_progress: "completed",
  completed: "planned",
};
// PR3 Task 2.4 — 토큰 참조로 재배선.
// completed 색은 PR3에서 #059669 → var(--status-success) (#16a34a) 미세 hue 변경 — 시각 회귀 spec 통과.
const STATUS_CONFIG: Record<
  string,
  { label: string; bg: string; text: string }
> = {
  planned: {
    label: "계획",
    bg: "var(--status-idle)",
    text: "var(--color-text-inverse)",
  },
  in_progress: {
    label: "진행",
    bg: "var(--status-info)",
    text: "var(--color-text-inverse)",
  },
  completed: {
    label: "완료",
    bg: "var(--status-success)",
    text: "var(--color-text-inverse)",
  },
};

interface BatchInspectorProps {
  auditPanel: AuditPanelState;
  setAuditPanel: React.Dispatch<React.SetStateAction<AuditPanelState>>;
  batchGroupOrders: BatchGroupOrder[];
  batchGroupLoading: boolean;
  processFlow: ProcessFlowEntry[];
  onNavigateToProcessBatch: (batchGroup: string) => void | Promise<void>;
}

export function BatchInspector({
  auditPanel,
  setAuditPanel,
  batchGroupOrders,
  batchGroupLoading,
  processFlow,
  onNavigateToProcessBatch,
}: BatchInspectorProps) {
  const tasks = useScheduleStore((s) => s.tasks);
  const equipment = useScheduleStore((s) => s.equipment);
  const selectedTaskId = useScheduleStore((s) => s.selectedTaskId);
  const selectTask = useScheduleStore((s) => s.selectTask);
  const updateTask = useScheduleStore((s) => s.updateTask);

  if (!auditPanel.open) return null;

  const selectedTask = selectedTaskId
    ? tasks.find((t) => t.id === selectedTaskId)
    : null;
  const selectedEquipment = selectedTask
    ? equipment.find((e) => e.id === selectedTask.equipment_id)
    : null;

  // 배치 상태 순환 클릭 핸들러 — 낙관적 업데이트 + 실패 시 롤백.
  const handleStatusClick = async () => {
    if (!selectedTask?.batch_id) return;
    const nextStatus = STATUS_CYCLE[selectedTask.status] ?? "planned";
    const prevStatus = selectedTask.status;
    updateTask(selectedTask.id, { status: nextStatus });
    try {
      const res = await fetch(
        `${API_BASE}/pipeline/batch/${selectedTask.batch_id}/status`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: nextStatus }),
        },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch {
      updateTask(selectedTask.id, { status: prevStatus });
    }
  };

  return (
    <div
      data-testid="task-detail-panel"
      className="shrink-0 border-l bg-white overflow-y-auto"
      style={{
        // 좁은 뷰포트(<~933px)에서 간트가 가려지지 않도록 비율 축소
        width: "min(420px, 45vw)",
        borderColor: "var(--color-border-default)",
      }}
    >
      {/* 패널 헤더 — sticky: 스크롤해도 항상 상단에 고정 */}
      <div
        className="flex items-center justify-between px-4 py-2 border-b sticky top-0 z-10"
        style={{
          backgroundColor: "var(--kbi-red-tint-5)",
          borderColor: "var(--kbi-red-tint-12)",
        }}
      >
        <div className="flex items-center gap-2">
          <div
            className="w-1 h-4 rounded-sm"
            style={{ backgroundColor: "var(--color-brand-primary)" }}
          />
          <span
            className="text-small font-semibold"
            style={{ color: "var(--kbi-brown)" }}
          >
            {batchGroupOrders.filter(
              (o) => o.batch_seq == null || o.batch_seq >= 0,
            ).length > 1
              ? "배치 그룹 수주 목록"
              : "수주 상세 정보"}
          </span>
          {selectedTask?.batch_group && (
            <span
              className="text-tiny font-mono px-1.5 py-0.5 rounded"
              style={{
                backgroundColor: "var(--kbi-red-tint-5)",
                color: "var(--color-brand-primary)",
              }}
            >
              {selectedTask.batch_group}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/* 공정 흐름 네비게이션 */}
          {processFlow.length > 1 &&
            (() => {
              const currentBg =
                selectedTask?.batch_group ?? processFlow[0]?.batch_group;
              const currentIdx = processFlow.findIndex(
                (p) => p.batch_group === currentBg,
              );
              const prev = currentIdx > 0 ? processFlow[currentIdx - 1] : null;
              const next =
                currentIdx >= 0 && currentIdx < processFlow.length - 1
                  ? processFlow[currentIdx + 1]
                  : null;
              return (
                <div className="flex items-center gap-1">
                  <button
                    disabled={!prev}
                    onClick={() =>
                      prev && onNavigateToProcessBatch(prev.batch_group)
                    }
                    className="px-2 py-0.5 text-tiny rounded border disabled:opacity-30 hover:bg-gray-100 transition-colors"
                    style={{
                      borderColor: "var(--neutral-300)",
                      color: "var(--kbi-brown)",
                    }}
                    title={
                      prev
                        ? `${prev.process_name} (${prev.batch_group})`
                        : "이전 공정 없음"
                    }
                  >
                    &larr; 이전공정
                  </button>
                  <span
                    className="text-mini px-1 font-mono"
                    style={{ color: "var(--color-text-tertiary)" }}
                  >
                    {currentIdx + 1}/{processFlow.length}
                  </span>
                  <button
                    disabled={!next}
                    onClick={() =>
                      next && onNavigateToProcessBatch(next.batch_group)
                    }
                    className="px-2 py-0.5 text-tiny rounded border disabled:opacity-30 hover:bg-gray-100 transition-colors"
                    style={{
                      borderColor: "var(--neutral-300)",
                      color: "var(--kbi-brown)",
                    }}
                    title={
                      next
                        ? `${next.process_name} (${next.batch_group})`
                        : "다음 공정 없음"
                    }
                  >
                    다음공정 &rarr;
                  </button>
                </div>
              );
            })()}
          <button
            onClick={() => {
              setAuditPanel((prev) => ({ ...prev, open: false }));
              selectTask(null);
            }}
            className="text-gray-400 hover:text-gray-600 text-xs leading-none px-1"
          >
            ✕
          </button>
        </div>
      </div>

      {/* 작업 요약 + 배치 그룹 수주 테이블 */}
      {selectedTask && (
        <div
          className="px-4 py-3 border-b"
          style={{ borderColor: "var(--color-border-muted)" }}
        >
          {/* 작업 요약 — 4 컬럼 그리드 */}
          <div className="grid grid-cols-4 gap-x-3 gap-y-2 mb-2">
            <div className="flex flex-col gap-0.5">
              <span className="text-mini font-medium text-gray-400 uppercase tracking-wider">
                규격
              </span>
              <span
                className="text-small font-semibold"
                style={{ color: "var(--color-text-primary)" }}
              >
                {selectedTask.spec || "-"}
              </span>
            </div>
            <div className="flex flex-col gap-0.5">
              <span className="text-mini font-medium text-gray-400 uppercase tracking-wider">
                배정 설비
              </span>
              <span
                className="text-small font-semibold"
                style={{ color: "var(--color-text-primary)" }}
              >
                {selectedEquipment?.name || selectedTask.equipment_id || "-"}
              </span>
            </div>
            <div className="flex flex-col gap-0.5">
              <span className="text-mini font-medium text-gray-400 uppercase tracking-wider">
                총 길이
              </span>
              <span
                className="text-small font-semibold"
                style={{ color: "var(--color-text-primary)" }}
              >
                {selectedTask.volume_m
                  ? `${selectedTask.volume_m.toLocaleString()}m`
                  : "-"}
              </span>
            </div>
            <div className="flex flex-col gap-0.5">
              <span className="text-mini font-medium text-gray-400 uppercase tracking-wider">
                작업 기간
              </span>
              <span className="text-small text-gray-600">
                {new Date(selectedTask.start).toLocaleString("ko-KR", {
                  month: "numeric",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
                {" ~ "}
                {new Date(selectedTask.end).toLocaleString("ko-KR", {
                  month: "numeric",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            </div>
            <div className="flex flex-col gap-0.5">
              <span className="text-mini font-medium text-gray-400 uppercase tracking-wider">
                선속
              </span>
              <span className="text-small text-gray-600">
                {selectedTask.line_speed_m_per_min
                  ? `${selectedTask.line_speed_m_per_min}m/min`
                  : "-"}
              </span>
            </div>
            <div className="flex flex-col gap-0.5"></div>
            {/* 배치 상태 — 클릭으로 순환 변경 */}
            {(() => {
              const cfg =
                STATUS_CONFIG[selectedTask.status] ?? STATUS_CONFIG.planned;
              return (
                <div className="flex flex-col gap-0.5">
                  <span className="text-mini font-medium text-gray-400 uppercase tracking-wider">
                    배치 상태
                  </span>
                  <button
                    onClick={handleStatusClick}
                    disabled={!selectedTask.batch_id}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 3,
                      padding: "2px 7px",
                      borderRadius: 4,
                      fontSize: "var(--text-small)",
                      fontWeight: 600,
                      backgroundColor: cfg.bg,
                      color: cfg.text,
                      border: "none",
                      cursor: selectedTask.batch_id ? "pointer" : "default",
                      lineHeight: 1.5,
                      width: "fit-content",
                    }}
                    title="클릭하여 상태 변경"
                  >
                    {cfg.label}
                    {selectedTask.batch_id && (
                      <span
                        style={{ fontSize: "var(--text-micro)", opacity: 0.75 }}
                      >
                        ▾
                      </span>
                    )}
                  </button>
                </div>
              );
            })()}

            <div className="flex flex-col gap-0.5">
              <span className="text-mini font-medium text-gray-400 uppercase tracking-wider">
                수주 건수
              </span>
              <span
                className="text-small font-semibold"
                style={{ color: "var(--color-brand-primary)" }}
              >
                {batchGroupOrders.filter(
                  (o) => o.batch_seq == null || o.batch_seq >= 0,
                ).length > 0
                  ? `${batchGroupOrders.filter((o) => o.batch_seq == null || o.batch_seq >= 0).length}건`
                  : "-"}
              </span>
            </div>
          </div>

          {/* 배치 그룹 수주 목록 테이블 */}
          {batchGroupLoading ? (
            <div className="flex items-center gap-2 py-2 text-small text-gray-400">
              <span
                className="inline-block w-3 h-3 border-2 border-gray-300 border-t-transparent rounded-full"
                style={{ animation: "spin 1s linear infinite" }}
              />
              수주 목록 로드 중...
            </div>
          ) : batchGroupOrders.length > 0 ? (
            <BatchGroupOrderTable orders={batchGroupOrders} />
          ) : (
            /* batch_group 가 없거나 API 실패 시 기존 단일 수주 정보 표시 */
            <div className="grid grid-cols-6 gap-x-4 gap-y-1">
              <div className="flex flex-col gap-0.5">
                <span className="text-mini font-medium text-gray-400 uppercase tracking-wider">
                  수주번호
                </span>
                <span className="text-small text-gray-700">
                  {selectedTask.order_id || "-"}
                </span>
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="text-mini font-medium text-gray-400 uppercase tracking-wider">
                  거래처
                </span>
                <span className="text-small text-gray-700">
                  {selectedTask.customer || "-"}
                </span>
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="text-mini font-medium text-gray-400 uppercase tracking-wider">
                  제품군
                </span>
                <span className="text-small text-gray-700">
                  {selectedTask.product || "-"}
                </span>
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="text-mini font-medium text-gray-400 uppercase tracking-wider">
                  색상
                </span>
                <span className="text-small text-gray-700">
                  {selectedTask.color || "-"}
                </span>
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="text-mini font-medium text-gray-400 uppercase tracking-wider">
                  납기
                </span>
                <span
                  className="text-small"
                  style={{
                    color: selectedTask.delivery_date
                      ? new Date(selectedTask.delivery_date).getTime() <
                        Date.now()
                        ? "var(--color-danger)"
                        : "var(--neutral-600)"
                      : "var(--color-text-tertiary)",
                  }}
                >
                  {selectedTask.delivery_date
                    ? new Date(selectedTask.delivery_date).toLocaleDateString(
                        "ko-KR",
                      )
                    : "-"}
                </span>
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="text-mini font-medium text-gray-400 uppercase tracking-wider">
                  길이
                </span>
                <span className="text-small text-gray-700">
                  {selectedTask.volume_m
                    ? `${selectedTask.volume_m.toLocaleString()}m`
                    : "-"}
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 작업 시간 구성 */}
      {selectedTask &&
        (() => {
          const startTs = new Date(selectedTask.start).getTime();
          const endTs = new Date(selectedTask.end).getTime();
          const setupMin =
            (selectedTask as { setup_time_min?: number }).setup_time_min ??
            selectedTask.changeover_min ??
            0;
          const colorChangeMin =
            (selectedTask as { color_change_min?: number }).color_change_min ??
            0;
          const tb = computeTimeBreakdown(
            startTs,
            endTs,
            setupMin + colorChangeMin,
            (selectedTask as { equipment_id?: string }).equipment_id,
          );
          const totalHrs = ((endTs - startTs) / (60 * 60 * 1000)).toFixed(1);
          return (
            <div
              className="px-4 py-2 border-t"
              style={{ borderColor: "var(--color-border-muted)" }}
            >
              <div className="flex items-center gap-1.5 mb-1.5">
                <svg
                  width="10"
                  height="10"
                  viewBox="0 0 16 16"
                  style={{ fill: "var(--color-text-tertiary)" }}
                >
                  <path d="M8 1a7 7 0 100 14A7 7 0 008 1zm-.75 4v4.25l3 1.75.75-1.3-2.5-1.45V5h-1.25z" />
                </svg>
                <span className="text-tiny font-medium text-gray-400">
                  작업 시간 구성
                </span>
              </div>
              <div className="flex flex-wrap gap-x-5 gap-y-1 text-small">
                <span>
                  <span className="text-gray-400">총 기간</span>{" "}
                  <span className="font-medium text-gray-700">{totalHrs}h</span>
                </span>
                <span>
                  <span className="text-gray-400">실제 작업</span>{" "}
                  <span
                    className="font-medium"
                    style={{ color: "var(--color-brand-primary)" }}
                  >
                    {tb.actualWork.toFixed(1)}h
                  </span>
                </span>
                {tb.gapHrs > 0 && (
                  <span>
                    <span className="text-gray-400">주말·야간 gap</span>{" "}
                    <span className="text-gray-600">{tb.gapHrs}h</span>
                  </span>
                )}
                {tb.breakHrs > 0 && (
                  <span>
                    <span className="text-gray-400">평일 break</span>{" "}
                    <span className="text-gray-600">{tb.breakHrs}h</span>
                  </span>
                )}
                {setupMin > 0 && (
                  <span>
                    <span className="text-gray-400">규격교체</span>{" "}
                    <span className="text-gray-600">{setupMin}분</span>
                  </span>
                )}
                {colorChangeMin > 0 && (
                  <span>
                    <span className="text-gray-400">색상교체</span>{" "}
                    <span className="text-gray-600">{colorChangeMin}분</span>
                  </span>
                )}
              </div>
            </div>
          );
        })()}

      {/* AI 스케줄링 근거 */}
      <div className="px-4 py-2">
        <div className="flex items-center gap-1.5 mb-1.5">
          <svg
            width="10"
            height="10"
            viewBox="0 0 16 16"
            style={{ fill: "var(--color-text-tertiary)" }}
          >
            <path d="M8 1a7 7 0 100 14A7 7 0 008 1zm0 2a1 1 0 110 2 1 1 0 010-2zm-1 4h2v5H7V7z" />
          </svg>
          <span className="text-tiny font-medium text-gray-400">
            AI 스케줄링 근거
          </span>
        </div>
        {auditPanel.loading && (
          <div className="flex items-center gap-2 text-small text-gray-400">
            <span
              className="inline-block w-3 h-3 border-2 border-gray-300 border-t-transparent rounded-full"
              style={{ animation: "spin 1s linear infinite" }}
            />
            AI 설명 로드 중...
          </div>
        )}
        {auditPanel.error && !auditPanel.loading && (
          <p className="text-small text-gray-400 italic">
            스케줄링 근거를 불러올 수 없습니다.
          </p>
        )}
        {auditPanel.data && !auditPanel.loading && (
          <div className="flex flex-col gap-1">
            {(auditPanel.data.explanation || auditPanel.data.reasoning) && (
              <p className="text-small text-gray-600 leading-relaxed whitespace-pre-wrap">
                {auditPanel.data.explanation ?? auditPanel.data.reasoning}
              </p>
            )}
            {auditPanel.data.scheduled_at && (
              <p className="text-tiny text-gray-400">
                배정 시각:{" "}
                {new Date(auditPanel.data.scheduled_at).toLocaleString("ko-KR")}
              </p>
            )}
            {auditPanel.data.changed_by && (
              <p className="text-tiny text-gray-400">
                변경자: {auditPanel.data.changed_by}
              </p>
            )}
            {Object.entries(auditPanel.data)
              .filter(
                ([k]) =>
                  ![
                    "batch_id",
                    "explanation",
                    "reasoning",
                    "scheduled_at",
                    "changed_by",
                  ].includes(k),
              )
              .map(([k, v]) => (
                <p key={k} className="text-tiny text-gray-500">
                  <span className="font-medium">{k}</span>: {String(v)}
                </p>
              ))}
          </div>
        )}
      </div>
    </div>
  );
}
