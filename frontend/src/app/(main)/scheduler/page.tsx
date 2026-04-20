"use client";

import { useCallback, useMemo, useRef, useState, useEffect } from "react";
import { createPortal } from "react-dom";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  type DragStartEvent,
  type DragEndEvent,
  type DragMoveEvent,
} from "@dnd-kit/core";

import { Header } from "@/shared/components/Header";
import { CollapsiblePanel } from "@/shared/components/CollapsiblePanel";
import {
  SchedulerView,
  equipmentMatchesGroup,
} from "@/features/scheduler/components/SchedulerView";
import { ViewFilter } from "@/features/scheduler/components/ViewFilter";
import { OrderInbox } from "@/features/scheduler/components/OrderInbox";
import { ConstraintAlert } from "@/features/scheduler/components/ConstraintAlert";
import { OverlapAlertBanner } from "@/features/scheduler/components/OverlapAlertBanner";
import { ContextMenu } from "@/features/scheduler/components/ContextMenu";
import { TaskFormModal } from "@/features/scheduler/components/TaskFormModal";
import { ZoomControl } from "@/features/scheduler/components/ZoomControl";
import { SyncButton } from "@/features/scheduler/components/SyncButton";
import { WipUpdateModal } from "@/features/scheduler/components/WipUpdateModal";
import { BatchSplitModal } from "@/features/scheduler/components/BatchSplitModal";
// Task 21 — cascade v2 연결 복원.
// Task 19 에서 Task 16/17 기반으로 교체된 ConflictResolutionModal 을 `useScheduleChangeWithCascade`
// 훅으로 재wiring. FEATURE_FLAG_CASCADE_V2 off 경로는 legacyMove 콜백이 기존 store.moveTask 로 fallback.
import { ConflictResolutionModal } from "@/features/scheduler/components/ConflictResolutionModal";
import { useScheduleChangeWithCascade } from "@/features/scheduler/hooks/useScheduleChangeWithCascade";
import { refreshTasks as refreshScheduleTasks } from "@/features/scheduler/hooks/useScheduleData";
import { useScheduleData } from "@/features/scheduler/hooks/useScheduleData";
import { useBatchGroupDrag } from "@/features/scheduler/hooks/useBatchGroupDrag";
import { useScheduleStore } from "@/features/scheduler/store/scheduleStore";
import type { Order, ScheduleTask } from "@/features/scheduler/types";
import { UnassignConfirmModal } from "@/features/scheduler/components/UnassignConfirmModal";
import { BatchGroupDragPreviewModal } from "@/features/scheduler/components/BatchGroupDragPreviewModal";
import { useToastStore } from "@/shared/ui/toastStore";
import {
  xToTime,
  computeTimeBreakdown,
} from "@/features/scheduler/utils/ganttUtils";
import {
  filterEquipmentByView,
  taskMatchesViewFilter,
} from "@/features/scheduler/utils/viewFilter";
import { calcConvertedQty } from "@/shared/utils/batchGrouping";

/** 드래그 중인 아이템 정보 */
interface ActiveDragItem {
  type: "order" | "task";
  order?: Order;
  task?: ScheduleTask;
}

const API_BASE = "http://localhost:8000/api";

/** 감사 설명 응답 (GET /api/audit/explain/{batch_id}) */
interface AuditExplanation {
  batch_id: string;
  explanation?: string;
  reasoning?: string;
  scheduled_at?: string;
  changed_by?: string;
  [key: string]: unknown;
}

/** 배치 그룹 내 개별 수주 (GET /api/pipeline/batch-group/{batch_group}/orders) */
interface BatchGroupOrder {
  batch_id: number;
  /** -1: 그룹 헤더(틀단위 집계), 0: 61연선 코어, 1+: 개별 수주 배치 */
  batch_seq: number;
  sales_order_id: string;
  spec_raw: string;
  sheath_color: string;
  customer_name: string;
  due_date: string;
  drum_length_m: number;
  drum_count: number;
  total_length_m: number;
  /** WIP 재고 커버량 (m) — WIP 사용 수주에만 존재 */
  wip_length_m?: number;
  /** 실제 작업지시량 (m) = total_length_m - wip_length_m */
  net_length_m?: number;
  wip_matched_id: number | null;
  product_group: string;
  status: string;
  core_count: number;
}

/** 배치 그룹 수주 목록 테이블
 *
 * batch_seq == -1 → 연선 그룹 헤더(틀단위 집계): 총 생산지시 틀 수 표시에 사용
 * batch_seq == 0  → CORE 배치(61연선 코어): 수주 1건씩, 헤더 없음
 * batch_seq == 1  → 개별 수주 배치: 수주 1건씩 행으로 표시
 * batch_seq == null/undefined → 헤더 없는 그룹(절연·시스 등): 모두 표시
 */
function BatchGroupOrderTable({ orders }: { orders: BatchGroupOrder[] }) {
  const headerBatch = orders.find((o) => o.batch_seq === -1);
  // 개별 수주 행: batch_seq >= 0(CORE 포함) 이거나 batch_seq 없는 경우(비연선 그룹)
  const orderRows = orders.filter(
    (o) => o.batch_seq == null || o.batch_seq >= 0,
  );
  // 실제 표시 행: 개별 수주 행이 있으면 그것만, 없으면 전체(폴백)
  const displayRows = orderRows.length > 0 ? orderRows : orders;

  const totalLots = headerBatch
    ? headerBatch.drum_count
    : displayRows.reduce((s, o) => s + o.drum_count, 0);
  // 작업지시량 합계: WIP 재고 사용분 제외 (net_length_m 우선, 없으면 total_length_m)
  const totalQty = displayRows.reduce(
    (s, o) => s + (o.net_length_m ?? o.total_length_m),
    0,
  );
  const wipCount = displayRows.filter((o) => o.wip_matched_id).length;

  return (
    <div className="overflow-x-auto">
      <table className="text-[11px] whitespace-nowrap">
        <thead>
          <tr
            style={{
              backgroundColor: "#F9FAFB",
              borderBottom: "1px solid #E5E7EB",
            }}
          >
            {(
              [
                ["수주번호", "left"],
                ["거래처", "left"],
                ["품명", "left"],
                ["규격", "left"],
                ["색상", "left"],
                ["납기", "left"],
                ["드럼", "right"],
                ["총 길이", "right"],
                ["환산수량", "right"],
                ["WIP", "center"],
              ] as [string, string][]
            ).map(([label, align]) => (
              <th
                key={label}
                className={`text-${align} py-1 px-2 font-semibold text-gray-500 text-[10px] uppercase tracking-wider`}
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {displayRows.map((order) => (
            <tr
              key={order.batch_id}
              className="hover:bg-gray-50 transition-colors"
              style={{ borderBottom: "1px solid #F3F4F6" }}
            >
              <td className="py-1 px-2 font-mono text-gray-700">
                {order.sales_order_id || "-"}
              </td>
              <td className="py-1 px-2 text-gray-700">
                {order.customer_name || "-"}
              </td>
              <td className="py-1 px-2 text-gray-600">
                {order.product_group || "-"}
              </td>
              <td className="py-1 px-2 text-gray-600">{order.spec_raw}</td>
              <td className="py-1 px-2 text-gray-600">
                {order.sheath_color || "-"}
              </td>
              <td
                className="py-1 px-2"
                style={{
                  color:
                    order.due_date &&
                    new Date(order.due_date).getTime() < Date.now()
                      ? "#DC2626"
                      : "#4B5563",
                }}
              >
                {order.due_date
                  ? new Date(order.due_date).toLocaleDateString("ko-KR")
                  : "-"}
              </td>
              <td className="py-1 px-2 text-right text-gray-600">
                {order.drum_count} x {order.drum_length_m.toLocaleString()}m
              </td>
              <td className="py-1 px-2 text-right font-medium text-gray-700">
                {order.wip_length_m != null && order.wip_length_m > 0 ? (
                  <span
                    title={`원본: ${order.total_length_m.toLocaleString()}m, WIP차감: -${order.wip_length_m.toLocaleString()}m`}
                  >
                    <span style={{ color: "#16A34A" }}>
                      {(
                        order.net_length_m ?? order.total_length_m
                      ).toLocaleString()}
                      m
                    </span>
                    <span className="ml-1 text-[9px] text-gray-400">
                      (-{order.wip_length_m.toLocaleString()})
                    </span>
                  </span>
                ) : (
                  (
                    order.net_length_m ?? order.total_length_m
                  ).toLocaleString() + "m"
                )}
              </td>
              <td className="py-1 px-2 text-right text-gray-600">
                {(() => {
                  const base = order.net_length_m ?? order.total_length_m;
                  const conv = calcConvertedQty(order.spec_raw || "", base);
                  return (
                    <span
                      title={`${order.core_count}C × ${base.toLocaleString()}m`}
                    >
                      {conv.toLocaleString()}m
                    </span>
                  );
                })()}
              </td>
              <td className="py-1 px-2 text-center">
                {order.wip_matched_id ? (
                  <span
                    className="inline-block px-1.5 py-0.5 rounded text-[9px] font-medium"
                    style={{ backgroundColor: "#DCFCE7", color: "#16A34A" }}
                  >
                    재고
                  </span>
                ) : (
                  <span className="text-gray-300">-</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr
            style={{
              borderTop: "2px solid #E5E7EB",
              backgroundColor: "#FDF2F2",
            }}
          >
            <td
              colSpan={6}
              className="py-1 px-2 font-semibold"
              style={{ color: "#C41230" }}
            >
              합계 {displayRows.length}건
              {headerBatch && (
                <span className="ml-2 text-[10px] font-normal text-gray-500">
                  (생산지시 {totalLots}틀 /{" "}
                  {headerBatch.drum_length_m.toLocaleString()}m×{totalLots})
                </span>
              )}
            </td>
            <td className="py-1 px-2 text-right font-medium text-gray-500">
              {totalLots}틀
            </td>
            <td
              className="py-1 px-2 text-right font-semibold"
              style={{ color: "#C41230" }}
              title="WIP 재고 사용량 제외한 실제 작업지시량"
            >
              {totalQty.toLocaleString()}m
            </td>
            <td
              className="py-1 px-2 text-right font-semibold"
              style={{ color: "#C41230" }}
              title="다심 케이블 환산수량 합계 (단심은 —)"
            >
              {displayRows
                .reduce((s, o) => {
                  const base = o.net_length_m ?? o.total_length_m;
                  return s + calcConvertedQty(o.spec_raw || "", base);
                }, 0)
                .toLocaleString()}
              m
            </td>
            <td className="py-1 px-2 text-center text-[10px] text-gray-400">
              {wipCount > 0 ? `재고 ${wipCount}건` : "-"}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

export default function SchedulerPage() {
  const { isLoading, error } = useScheduleData();
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);
  const updateTask = useScheduleStore((s) => s.updateTask);
  const isEditMode = useScheduleStore((s) => s.isEditMode);
  const toggleEditMode = useScheduleStore((s) => s.toggleEditMode);
  const discardEdits = useScheduleStore((s) => s.discardEdits);
  const saveVersion = useScheduleStore((s) => s.saveVersion);
  const showSavedToast = useScheduleStore((s) => s.showSavedToast);
  const assignOrder = useScheduleStore((s) => s.assignOrder);
  const moveTask = useScheduleStore((s) => s.moveTask);
  const setPreviewOffsets = useScheduleStore((s) => s.setPreviewOffsets);
  const clearPreviewOffsets = useScheduleStore((s) => s.clearPreviewOffsets);
  const tasks = useScheduleStore((s) => s.tasks);
  const equipment = useScheduleStore((s) => s.equipment);
  const viewFilter = useScheduleStore((s) => s.viewFilter);
  const zoomLevel = useScheduleStore((s) => s.zoomLevel);
  const range = useScheduleStore((s) => s.range);
  const unscheduledItems = useScheduleStore((s) => s.unscheduledItems);
  // Task 4.2: 페이지는 "order" kind 개수만 활용 (CollapsiblePanel count).
  // batch_group kind 카운트는 Task 5.4에서 별도 집계.
  const unscheduledOrderItems = useMemo<Order[]>(
    () =>
      unscheduledItems
        .filter((i): i is { kind: "order"; order: Order } => i.kind === "order")
        .map((i) => i.order),
    [unscheduledItems],
  );
  // Task 21 — legacy cascadePreview/conflictModalOpen/applyCascade/cancelCascade/cascadeOriginalTask
  // 는 더 이상 이 페이지에서 사용하지 않음. store 자체는 flag off 경로를 위해 유지 (scheduleStore).
  const setRunLabel = useScheduleStore((s) => s.setRunLabel);

  /**
   * Task 21 — cascade v2 orchestrator 훅.
   *
   * - legacyMove: FEATURE_FLAG off 시 기존 store.moveTask 로 fallback.
   *   ChangeInput (ISO 문자열) → moveTask(taskId, eqId, Date, Date) 시그니처 어댑터.
   *   new_equipment_code 가 없으면 현재 task 의 equipment_id 를 유지한다.
   * - onCommitted: bulk-update/revert 성공 후 간트 refetch.
   */
  const legacyMoveAdapter = useCallback(
    (input: {
      task_id: string;
      new_start: string;
      new_end: string;
      new_equipment_code?: string | null;
    }) => {
      const currentTask = tasks.find((t) => t.id === input.task_id);
      const targetEquipmentId =
        input.new_equipment_code ?? currentTask?.equipment_id ?? "";
      moveTask(
        input.task_id,
        targetEquipmentId,
        new Date(input.new_start),
        new Date(input.new_end),
      );
    },
    [moveTask, tasks],
  );

  const cascade = useScheduleChangeWithCascade({
    legacyMove: legacyMoveAdapter,
    onCommitted: async () => {
      await refreshScheduleTasks();
    },
  });

  // Task 4.3 — batch_group 드래그 orchestration 훅.
  const bgDrag = useBatchGroupDrag({
    onCommitted: async () => {
      await refreshScheduleTasks();
    },
  });

  // Task 4.3 — inbox 드롭 UnassignConfirmModal 상태.
  // skipConfirmFromDrag: "이 세션에서 다시 묻지 않기" 체크 시 즉시 처리.
  const [unassignFromDragState, setUnassignFromDragState] = useState<{
    batchGroup: string;
  } | null>(null);
  const [skipConfirmFromDrag, setSkipConfirmFromDrag] = useState(false);

  // Task 22 — ConflictResolutionModal row hover → 간트 블록 focus-ring 하이라이트.
  // 모달이 열렸을 때만 의미가 있으므로 단순 state 로 관리 (store 에 올릴 필요 없음).
  const [hoveredTaskId, setHoveredTaskId] = useState<string | null>(null);

  // ── SM재고 실적 모달 상태 ──
  const [showWipModal, setShowWipModal] = useState(false);
  const [wipRunLabel, setWipRunLabel] = useState<string | null>(null);

  // 최신 runLabel 조회 (모달 열기 시 사용) + scheduleStore에도 저장 (AI 재분석 트리거용)
  useEffect(() => {
    fetch(`${API_BASE}/pipeline/runs`)
      .then((r) => (r.ok ? r.json() : []))
      .then((runs: Array<{ run_label: string }>) => {
        if (runs.length > 0) {
          setWipRunLabel(runs[0].run_label);
          setRunLabel(runs[0].run_label);
        }
      })
      .catch(() => {});
  }, [setRunLabel]);

  // ── 선행 공정 이동 경고 토스트 ──
  const [predecessorToast, setPredecessorToast] = useState<string | null>(null);

  // ── 자동배열 상태 ──
  const [autoScheduleLoading, setAutoScheduleLoading] = useState(false);
  const [autoScheduleResult, setAutoScheduleResult] = useState<string | null>(
    null,
  );
  // 겹침 경고 — Stage 2 API가 `overlap_alert: true`를 반환했을 때 상단 배너에 표시할 메시지
  // 의도: 자동배열이 재시도 한도 내에 non-overlapping 스케줄을 만들지 못한 경우
  //       기존 스케줄을 유지(refresh 안 함)하고 사용자에게 명시적으로 경고한다.
  const [overlapAlert, setOverlapAlert] = useState<string | null>(null);

  // ── 배치 그룹 수주 목록 상태 ──
  const [batchGroupOrders, setBatchGroupOrders] = useState<BatchGroupOrder[]>(
    [],
  );
  const [batchGroupLoading, setBatchGroupLoading] = useState(false);

  // ── 공정 흐름 네비게이션 상태 ──
  const [processFlow, setProcessFlow] = useState<
    Array<{
      batch_group: string;
      process_name: string;
      equipment_code?: string;
      order: number;
    }>
  >([]);

  // ── 감사 트레일 패널 상태 ──
  const [auditPanel, setAuditPanel] = useState<{
    open: boolean;
    batchId: string | null;
    data: AuditExplanation | null;
    loading: boolean;
    error: string | null;
  }>({ open: false, batchId: null, data: null, loading: false, error: null });

  // ── 납기 초과 패널 상태 ──
  const [showLatePanel, setShowLatePanel] = useState(false);

  // 납기 초과 태스크: 배치 종료 시각 > 납기일 자정.
  // 뷰 필터(전체/저압만/고압만/공정별) 에 반응 — 필터에서 제외된 설비의 task 는 카운트·목록에서 빠짐.
  const visibleEquipmentIds = useMemo(
    () =>
      new Set(
        filterEquipmentByView(
          equipment,
          viewFilter.filterType,
          viewFilter.filterValue,
        ).map((eq) => eq.id),
      ),
    [equipment, viewFilter.filterType, viewFilter.filterValue],
  );

  const lateTasks = useMemo(() => {
    return tasks
      .filter((t) => taskMatchesViewFilter(t, visibleEquipmentIds))
      .filter((t) => {
        if (!t.delivery_date) return false;
        const dd =
          t.delivery_date instanceof Date
            ? t.delivery_date
            : new Date(t.delivery_date);
        const due = new Date(dd);
        due.setHours(23, 59, 59, 999);
        const endTs =
          t.end instanceof Date ? t.end.getTime() : new Date(t.end).getTime();
        return endTs > due.getTime();
      })
      .map((t) => {
        const dd =
          t.delivery_date instanceof Date
            ? t.delivery_date
            : new Date(t.delivery_date!);
        const due = new Date(dd);
        due.setHours(23, 59, 59, 999);
        const endTs =
          t.end instanceof Date ? t.end.getTime() : new Date(t.end).getTime();
        const lateDays = Math.ceil(
          (endTs - due.getTime()) / (24 * 60 * 60 * 1000),
        );
        return { task: t, lateDays };
      })
      .sort((a, b) => b.lateDays - a.lateDays);
  }, [tasks, visibleEquipmentIds]);

  // 자동배열 실행 — 최신 런 라벨을 먼저 조회한 뒤 stage2 호출
  const handleAutoSchedule = useCallback(async () => {
    setAutoScheduleLoading(true);
    setAutoScheduleResult(null);
    // 새 실행 시작 시 이전 겹침 배너는 제거 (성공/실패는 아래 분기에서 재설정)
    setOverlapAlert(null);
    try {
      // 최신 런 라벨 조회
      const runsRes = await fetch(`${API_BASE}/pipeline/runs`);
      let runLabel: string | null = null;
      if (runsRes.ok) {
        const runs: Array<{ run_label: string }> = await runsRes.json();
        if (runs.length > 0) runLabel = runs[0].run_label;
      }
      if (!runLabel) {
        setAutoScheduleResult(
          "실행 가능한 런이 없습니다. 먼저 Stage 1을 실행하세요.",
        );
        return;
      }
      // scheduleStore에 runLabel 저장 (AI 재분석 트리거용)
      setRunLabel(runLabel);

      // ── Phase 3 개선: 비동기 job 경로로 전환 ──────────────────────────
      // 왜 async 경로:
      //   동기 /pipeline/stage2 는 10분 급 솔빙 동안 HTTP 연결을 유지하며
      //   uvicorn 워커를 점유. 프론트는 응답이 올 때까지 아무 피드백을 줄
      //   수 없었고, 같은 시간대의 다른 API 요청은 워커 대기열에 쌓였음.
      //   /stage2/async 는 job_id 를 즉시 반환 → 1초 간격 status polling
      //   으로 완료/오류/오버랩을 감지. 기존 응답 구조(overlap_alert,
      //   schedule 등)는 job.result 에 그대로 실려 오므로 기존 UX 분기가
      //   그대로 동작.
      const submitRes = await fetch(`${API_BASE}/pipeline/stage2/async`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_label: runLabel,
          base_date:
            typeof window !== "undefined"
              ? localStorage.getItem("plan_base_date") || undefined
              : undefined,
        }),
      });
      if (!submitRes.ok) {
        const text = await submitRes.text();
        setAutoScheduleResult(
          `오류: ${submitRes.status} — ${text.slice(0, 120)}`,
        );
        return;
      }
      const { job_id: jobId } = (await submitRes.json()) as { job_id: string };

      // ── 폴링 루프 ─────────────────────────────────────────────────────
      // 1초 간격, 최대 20분 (대형 런 대비 여유). 완료/오버랩/오류 시 탈출.
      type JobStatus = {
        status: "running" | "done" | "overlap_alert" | "error";
        result?: {
          run_label?: string;
          overlap_alert?: boolean;
          attempts?: number;
          message?: string;
        };
        error?: string;
        started_at?: string;
      };
      const startedAt = Date.now();
      const deadlineMs = 20 * 60 * 1000;
      let finalStatus: JobStatus | null = null;
      while (Date.now() - startedAt < deadlineMs) {
        await new Promise((r) => setTimeout(r, 1000));
        const statusRes = await fetch(
          `${API_BASE}/pipeline/stage2/status/${jobId}`,
        );
        if (!statusRes.ok) {
          setAutoScheduleResult(`상태 조회 실패: ${statusRes.status}`);
          return;
        }
        const s = (await statusRes.json()) as JobStatus;
        if (s.status === "running") {
          const elapsedS = Math.floor((Date.now() - startedAt) / 1000);
          setAutoScheduleResult(`자동배열 진행 중... ${elapsedS}초 경과`);
          continue;
        }
        finalStatus = s;
        break;
      }

      if (!finalStatus) {
        setAutoScheduleResult("자동배열 타임아웃 (20분). 서버 로그 확인 필요.");
        return;
      }

      if (finalStatus.status === "error") {
        setAutoScheduleResult(
          `자동배열 오류: ${finalStatus.error ?? "알 수 없음"}`,
        );
        return;
      }

      if (
        finalStatus.status === "overlap_alert" ||
        finalStatus.result?.overlap_alert
      ) {
        const attempts = finalStatus.result?.attempts ?? 3;
        setOverlapAlert(`${attempts}회 재시도 실패. 기존 스케줄을 유지합니다.`);
        setAutoScheduleResult(null);
        return;
      }

      setAutoScheduleResult(`자동배열 완료 (런: ${runLabel}).`);
      // 왜 reload 대신 부분 갱신:
      //   window.location.reload() 는 모든 useEffect 를 재실행시켜 /pipeline/runs,
      //   /schedules/tasks, /equipment, /line-speeds, /audit 등 10+ 엔드포인트가
      //   동시에 재호출되어 Supabase 커넥션 풀에 폭주 트래픽을 만든다. 사용자 UX
      //   체감 시간의 상당 부분이 "reload 후 전체 페이지 재로드 대기" 였음.
      //   대신 scheduler 페이지가 실제로 관심 있는 태스크만 다시 가져와 store 에
      //   반영하면, Gantt 가 동일 effect 체인 없이 즉시 재렌더링된다.
      await refreshScheduleTasks();
    } catch {
      setAutoScheduleResult(
        `연결 실패: 백엔드 서버(localhost:8000)를 확인하세요.`,
      );
    } finally {
      setAutoScheduleLoading(false);
    }
  }, [setRunLabel]);

  // AI explain 캐시 — 같은 batch_id 재클릭 시 LLM 재호출 방지
  const explainCache = useRef<Record<string, AuditExplanation>>({});

  // 태스크 블록 클릭 → 감사 패널 열기 (캐시 우선)
  const handleTaskClick = useCallback((taskId: string) => {
    const numericId = taskId.replace(/\D/g, "");

    // 캐시 히트: LLM 호출 없이 즉시 표시
    if (explainCache.current[numericId]) {
      setAuditPanel({
        open: true,
        batchId: taskId,
        data: explainCache.current[numericId],
        loading: false,
        error: null,
      });
      return;
    }

    setAuditPanel({
      open: true,
      batchId: taskId,
      data: null,
      loading: true,
      error: null,
    });
    fetch(`${API_BASE}/audit/explain/${numericId}`)
      .then(async (res) => {
        if (res.ok) {
          const data: AuditExplanation = await res.json();
          // 캐시에 저장
          explainCache.current[numericId] = data;
          setAuditPanel((prev) => ({
            ...prev,
            loading: false,
            data,
          }));
        } else {
          setAuditPanel((prev) => ({
            ...prev,
            loading: false,
            error: `응답 오류: ${res.status}`,
          }));
        }
      })
      .catch(() => {
        setAuditPanel((prev) => ({
          ...prev,
          loading: false,
          error: "감사 정보를 가져올 수 없습니다.",
        }));
      });
  }, []);

  // selectedTaskId 변경 시 감사 패널 자동 열기
  const selectedTaskId = useScheduleStore((s) => s.selectedTaskId);
  const selectTask = useScheduleStore((s) => s.selectTask);
  useEffect(() => {
    if (!selectedTaskId) {
      setAuditPanel((prev) => ({ ...prev, open: false }));
      return;
    }
    handleTaskClick(selectedTaskId);
  }, [selectedTaskId, handleTaskClick]);

  // selectedTaskId 변경 시 batch_group 수주 목록 + 공정 흐름 조회
  useEffect(() => {
    if (!selectedTaskId) {
      setBatchGroupOrders([]);
      setProcessFlow([]);
      return;
    }
    const selectedTask = tasks.find((t) => t.id === selectedTaskId);
    const bg = selectedTask?.batch_group;
    if (!bg) {
      setBatchGroupOrders([]);
      setProcessFlow([]);
      return;
    }
    setBatchGroupLoading(true);
    const encodedBg = encodeURIComponent(bg);

    // 수주 목록과 공정 흐름을 병렬 조회
    Promise.all([
      fetch(`${API_BASE}/pipeline/batch-group/${encodedBg}/orders`)
        .then(async (res) =>
          res.ok ? ((await res.json()) as BatchGroupOrder[]) : [],
        )
        .catch(() => [] as BatchGroupOrder[]),
      fetch(`${API_BASE}/pipeline/batch-group/${encodedBg}/process-flow`)
        .then(async (res) => (res.ok ? await res.json() : []))
        .catch(() => []),
    ]).then(([orders, flow]) => {
      setBatchGroupOrders(orders);
      setProcessFlow(flow);
      setBatchGroupLoading(false);
    });
  }, [selectedTaskId, tasks]);

  // 공정 흐름 네비게이션 — 이전/다음 공정의 batch_group으로 이동
  const navigateToProcessBatch = useCallback(
    async (targetBatchGroup: string) => {
      // 간트에 이미 로드된 task 중 해당 batch_group을 찾아 선택
      const targetTask = tasks.find((t) => t.batch_group === targetBatchGroup);
      if (targetTask) {
        selectTask(targetTask.id);
        // 간트 뷰를 해당 task의 시작 시간 근처로 가로 스크롤
        const taskStart = new Date(targetTask.start).getTime();
        const range = useScheduleStore.getState().range;
        const span = range.end - range.start;
        const newStart = taskStart - span * 0.2;
        useScheduleStore.getState().setRange({
          start: newStart,
          end: newStart + span,
        });
        // 해당 task 블록으로 세로 스크롤 (설비 행 이동)
        requestAnimationFrame(() => {
          const el = document.querySelector(
            `[data-task-id="${targetTask.id}"]`,
          );
          el?.scrollIntoView({ behavior: "smooth", block: "center" });
        });
      } else {
        // 간트에 없는 경우(다른 공정 필터 등): 수주 목록만 갱신
        setBatchGroupLoading(true);
        const encodedBg = encodeURIComponent(targetBatchGroup);
        Promise.all([
          fetch(`${API_BASE}/pipeline/batch-group/${encodedBg}/orders`)
            .then(async (res) =>
              res.ok ? ((await res.json()) as BatchGroupOrder[]) : [],
            )
            .catch(() => [] as BatchGroupOrder[]),
          fetch(`${API_BASE}/pipeline/batch-group/${encodedBg}/process-flow`)
            .then(async (res) => (res.ok ? await res.json() : []))
            .catch(() => []),
        ]).then(([orders, flow]) => {
          setBatchGroupOrders(orders);
          setProcessFlow(flow);
          setBatchGroupLoading(false);
        });
      }
    },
    [tasks, selectTask],
  );

  const [activeDrag, setActiveDrag] = useState<ActiveDragItem | null>(null);

  // 드래그 중인 아이템의 equipment_group 추론 — SchedulerView로 전달하여 비호환 행을 비활성화
  const activeDragGroup = useMemo<string | null>(() => {
    if (!activeDrag) return null;
    if (activeDrag.type === "order" && activeDrag.order) {
      return activeDrag.order.equipment_group ?? null;
    }
    if (activeDrag.type === "task" && activeDrag.task) {
      // 기존 task는 equipment_id로 설비를 찾아 그룹 추론
      const eq = equipment.find((e) => e.id === activeDrag.task!.equipment_id);
      if (!eq) return null;
      // equipment_group 키를 순서대로 시도
      for (const g of ["연선", "B100", "A100", "A120"]) {
        if (equipmentMatchesGroup(eq, g)) return g;
      }
    }
    return null;
  }, [activeDrag, equipment]);

  // 드래그 중인 아이템의 SQ (mm²) 추출 — spec 문자열에서 "400SQ" → 400 파싱
  const activeDragSq = useMemo<number | null>(() => {
    if (!activeDrag) return null;
    const spec =
      activeDrag.type === "order"
        ? activeDrag.order?.spec
        : activeDrag.task?.spec;
    if (!spec) return null;
    const match = spec.match(/(\d+)\s*SQ/i);
    return match ? parseInt(match[1], 10) : null;
  }, [activeDrag]);

  // 드래그 중인 아이템의 도체 재질 (CU | AL) 추출
  const activeDragMaterial = useMemo<string | null>(() => {
    if (!activeDrag) return null;
    // task에 material 필드가 있으면 우선 사용
    if (activeDrag.type === "task" && activeDrag.task?.material) {
      return activeDrag.task.material;
    }
    // order의 경우 product_group 등으로 추론 (rawData 존재 시)
    // 현재 Order 타입에 material이 없으므로 null 반환 — 배치 기반 task만 재질 검증
    return null;
  }, [activeDrag]);

  const [panelAnimating, setPanelAnimating] = useState(false);
  const [showEditWarning, setShowEditWarning] = useState(false);

  // 드래그 감도 설정 — 5px 이동 후 드래그 시작
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 5 },
    }),
  );

  const handleAddTask = useCallback(() => {
    openTaskFormModal({ mode: "create" });
  }, [openTaskFormModal]);

  // 수정하기/저장하기 토글
  const handleToggleEditMode = useCallback(async () => {
    if (isEditMode) {
      await saveVersion();
    } else {
      toggleEditMode();
    }
  }, [isEditMode, saveVersion, toggleEditMode]);

  // 드래그 시작
  const handleDragStart = useCallback((event: DragStartEvent) => {
    const data = event.active.data.current;
    if (!data) return;

    if (data.type === "order") {
      // equipment_group이 drag data에 있으면 order 객체에 merge하여 activeDragGroup 계산에 활용
      const order = data.order as Order;
      const enrichedOrder: Order = data.equipment_group
        ? { ...order, equipment_group: data.equipment_group as string }
        : order;
      setActiveDrag({ type: "order", order: enrichedOrder });
    } else if (data.type === "task") {
      setActiveDrag({ type: "task", task: data.task as ScheduleTask });
    }
  }, []);

  // 드래그 중 — cascade preview 계산
  const handleDragMove = useCallback(
    (event: DragMoveEvent) => {
      if (!isEditMode) return;
      const activeData = event.active.data.current;
      const over = event.over;
      if (!activeData || !over?.data?.current) {
        clearPreviewOffsets();
        return;
      }
      if (over.data.current.type !== "equipment-row") {
        clearPreviewOffsets();
        return;
      }
      if (activeData.type !== "task") {
        clearPreviewOffsets();
        return;
      }

      const task = activeData.task as ScheduleTask;
      const targetEqId = over.data.current.equipmentId as string;

      // 현재 드래그 위치에서 task의 예상 start/end 계산
      const MS_PER_DAY = 24 * 60 * 60 * 1000;
      const totalDays = Math.max((range.end - range.start) / MS_PER_DAY, 1);
      const overWidth = over.rect?.width || 800;
      const dynamicDayWidth = overWidth / totalDays;

      const taskStartTs =
        task.start instanceof Date
          ? task.start.getTime()
          : new Date(task.start).getTime();
      const taskEndTs =
        task.end instanceof Date
          ? task.end.getTime()
          : new Date(task.end).getTime();
      const durationMs = taskEndTs - taskStartTs;
      const deltaMs = (event.delta.x / dynamicDayWidth) * MS_PER_DAY;
      const previewStart = taskStartTs + deltaMs;
      const previewEnd = previewStart + durationMs;

      // 같은 설비의 다른 task들에 대해 cascade offset 계산
      // 대상: 현재 드래그 대상 설비에 있는 task + 드래그 전 설비에 있던 task 중 targetEqId와 같은 것
      const sameMachine = tasks
        .filter((t) => {
          if (t.id === task.id) return false;
          // 이미 targetEqId에 있거나, 드래그로 targetEqId에 올 예정인 것
          return t.equipment_id === targetEqId;
        })
        .map((t) => ({
          id: t.id,
          start:
            t.start instanceof Date
              ? t.start.getTime()
              : new Date(t.start).getTime(),
          end:
            t.end instanceof Date ? t.end.getTime() : new Date(t.end).getTime(),
        }))
        .sort((a, b) => a.start - b.start);

      const offsets: Record<string, number> = {};

      // Forward push: 드래그 블록의 end 이후에 겹치는 블록을 오른쪽으로
      let fwdBoundary = previewEnd;
      for (const other of sameMachine) {
        if (other.start >= previewStart && fwdBoundary > other.start) {
          const pushMs = fwdBoundary - other.start;
          offsets[other.id] = pushMs; // 양수 = 오른쪽
          fwdBoundary = fwdBoundary + (other.end - other.start);
        }
      }

      // Backward push: 드래그 블록의 start 이전에 겹치는 블록을 왼쪽으로
      const reverseSorted = [...sameMachine].reverse();
      let bwdBoundary = previewStart;
      for (const other of reverseSorted) {
        if (other.end <= previewEnd && other.end > bwdBoundary) {
          const pushMs = other.end - bwdBoundary;
          offsets[other.id] = -pushMs; // 음수 = 왼쪽
          bwdBoundary = bwdBoundary - (other.end - other.start);
        }
      }

      if (Object.keys(offsets).length > 0) {
        setPreviewOffsets(offsets);
      } else {
        clearPreviewOffsets();
      }
    },
    [isEditMode, tasks, range, setPreviewOffsets, clearPreviewOffsets],
  );

  // 드래그 종료
  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveDrag(null);
      clearPreviewOffsets();

      const { active, over } = event;
      if (!over) return;

      // 수정 모드가 아니면 경고 모달 표시
      if (!isEditMode) {
        setShowEditWarning(true);
        return;
      }

      const activeData = active.data.current;
      const overData = over.data.current;
      if (!activeData) return;

      // --- 분기 1: inbox dropzone 드롭 ---
      // Task 4.3 — batch_group_task / task 를 미배정 영역으로 드롭 시
      // UnassignConfirmModal 표시 (skipConfirmFromDrag 시 즉시 처리).
      if (over.id === "inbox-dropzone") {
        if (
          activeData.type === "task" ||
          activeData.type === "batch_group_task"
        ) {
          const task = activeData.task as ScheduleTask | undefined;
          const bg = task?.batch_group;
          if (!bg) {
            useToastStore
              .getState()
              .show(
                "batch_group 에 속하지 않은 task 는 미배정 이동 불가",
                "warning",
              );
            return;
          }
          if (skipConfirmFromDrag) {
            void useScheduleStore.getState().unassignBatchGroup(bg, "기타");
            return;
          }
          setUnassignFromDragState({ batchGroup: bg });
        }
        return;
      }

      if (!overData) return;

      // 드롭 대상이 설비 행인지 확인
      if (overData.type !== "equipment-row") return;

      const targetEquipmentId = overData.equipmentId as string;

      // 동적 dayWidth 계산 (컨테이너 너비 기반)
      const MS_PER_DAY = 24 * 60 * 60 * 1000;
      const totalDays = Math.max((range.end - range.start) / MS_PER_DAY, 1);

      // --- 분기 2: BatchGroupCard → equipment-row 드롭 ---
      // Task 4.3 — 미배정 BatchGroupCard 를 간트 설비 행에 드롭 → useBatchGroupDrag.onDropToEquipment.
      // anchorStart: 드롭 위치 x → xToTime 변환 (order 분기와 동일 공식).
      // isOrigin: false 고정 (v2b 에서 원위치 감지 구현 예정).
      if (activeData.type === "batch_group") {
        const bgLabel = (
          activeData.group as { batch_group?: string } | undefined
        )?.batch_group;
        if (!bgLabel) return;
        let anchorStart: Date;
        const overRect = over.rect;
        if (event.delta && overRect) {
          const activatorEvent = event.activatorEvent as MouseEvent;
          if (activatorEvent) {
            const dropClientX = activatorEvent.clientX + event.delta.x;
            const relativeX = dropClientX - overRect.left;
            const dynamicDayWidth = overRect.width / totalDays;
            const timestamp = xToTime(relativeX, range.start, dynamicDayWidth);
            anchorStart = new Date(timestamp);
          } else {
            anchorStart = new Date();
          }
        } else {
          anchorStart = new Date();
        }
        void bgDrag.onDropToEquipment(bgLabel, {
          equipmentCode: targetEquipmentId,
          anchorStart,
          isOrigin: false,
        });
        return;
      }

      // --- 수주를 스케줄러에 드롭 ---
      if (activeData.type === "order") {
        const order = activeData.order as Order;
        const rangeStart = range.start;

        // 드롭 위치에서 시간을 계산
        // delta.x가 드래그 시작점으로부터의 이동 거리이므로,
        // over 요소의 위치를 기반으로 시작 시각을 결정
        // 기본 동작: 현재 시각으로 배정
        let startTime: Date;

        if (event.delta) {
          // 드롭 위치의 x를 기반으로 시간 계산
          // over 요소(설비 행)의 rect를 사용하여 상대 위치 결정
          const overRect = over.rect;
          if (overRect) {
            const activatorEvent = event.activatorEvent as MouseEvent;
            if (activatorEvent) {
              const dropClientX = activatorEvent.clientX + event.delta.x;
              const relativeX = dropClientX - overRect.left;
              // overRect.width가 타임라인 영역 너비 — dayWidth를 동적 계산
              const dynamicDayWidth = overRect.width / totalDays;
              const timestamp = xToTime(relativeX, rangeStart, dynamicDayWidth);
              startTime = new Date(timestamp);
            } else {
              startTime = new Date();
            }
          } else {
            startTime = new Date();
          }
        } else {
          startTime = new Date();
        }

        assignOrder(order.id, targetEquipmentId, startTime);
        return;
      }

      // --- 분기 3: 기존 task / batch_group_task → 단일 task 재배정 (cascade 경유) ---
      // Task 4.3 — batch_group_task 도 동일 경로 처리. 그룹 전체 이동은 분기 2(batch_group)에서 처리.
      if (
        activeData.type === "task" ||
        activeData.type === "batch_group_task"
      ) {
        const task = activeData.task as ScheduleTask;

        // 현재 작업의 duration을 유지한 채 새 위치로 이동
        const taskStartTs =
          task.start instanceof Date
            ? task.start.getTime()
            : new Date(task.start).getTime();
        const taskEndTs =
          task.end instanceof Date
            ? task.end.getTime()
            : new Date(task.end).getTime();
        const durationMs = taskEndTs - taskStartTs;

        // delta.x로 시간 이동량 계산 (동적 dayWidth)
        const overWidth = over.rect?.width || 800;
        const dynamicDayWidth = overWidth / totalDays;
        const deltaMs = (event.delta.x / dynamicDayWidth) * MS_PER_DAY;
        const newStartTs = taskStartTs + deltaMs;

        const newStart = new Date(newStartTs);
        const newEnd = new Date(newStartTs + durationMs);

        // Task 21 — cascade v2 훅 경유.
        // flag on → preview → 모달 승인 → bulk-update.
        // flag off → legacyMoveAdapter 로 기존 store.moveTask 위임.
        //
        // Date → naive ISO ("YYYY-MM-DDTHH:mm:ss"): backend TZ-guard 가 'Z'/'+HH:MM' 을 422 로 reject.
        // 로컬 타임존을 KST 로 간주하는 프로젝트 전제 아래, UTC 오프셋을 제거한 wall-clock 값을 전송.
        const toNaiveIso = (d: Date): string => {
          const tzOffsetMin = d.getTimezoneOffset();
          const local = new Date(d.getTime() - tzOffsetMin * 60_000);
          return local.toISOString().slice(0, 19);
        };
        cascade.commit({
          task_id: task.id,
          new_start: toNaiveIso(newStart),
          new_end: toNaiveIso(newEnd),
          new_equipment_code: targetEquipmentId,
        });

        // 블록 변경 → AI explain 캐시 무효화 (영향받는 배치 재분석 필요)
        explainCache.current = {};

        // 선행 공정 이동 경고: 연선→절연→시스 체인에서 후행 공정이 있으면 경고 표시
        const bg = (task.batch_group || "").toLowerCase();
        const eq = equipment.find((e) => e.id === task.equipment_id);
        const processType = eq?.process_type?.toLowerCase() ?? "";
        const isStranding =
          bg.startsWith("연선") || processType === "stranding";
        const isInsulation =
          bg.startsWith("저압절연") ||
          bg.startsWith("고압절연") ||
          bg.startsWith("b100");
        if (isStranding || isInsulation) {
          const successorLabel = isStranding ? "절연/시스" : "시스";
          setPredecessorToast(
            `선행 공정 이동 시 후행 공정(${successorLabel})에 영향을 줄 수 있습니다`,
          );
          setTimeout(() => setPredecessorToast(null), 4000);
        }
      }
    },
    [
      assignOrder,
      cascade,
      zoomLevel,
      range,
      isEditMode,
      equipment,
      bgDrag,
      skipConfirmFromDrag,
    ],
  );

  return (
    <div
      className="flex flex-col h-full overflow-hidden"
      style={{ backgroundColor: "#FAFAFA" }}
    >
      {/* 상단 헤더 */}
      <Header
        onAddTask={handleAddTask}
        isEditMode={isEditMode}
        onToggleEditMode={handleToggleEditMode}
        onDiscardEdits={discardEdits}
      />

      {/* 겹침 경고 배너 — Stage 2 overlap_alert 응답 시에만 노출, 헤더 바로 아래 최상단 */}
      {overlapAlert && (
        <OverlapAlertBanner
          message={overlapAlert}
          onDismiss={() => setOverlapAlert(null)}
        />
      )}

      {/* 뷰 필터 + 동기화 + 줌 컨트롤 + 자동배열 */}
      <div className="flex items-center bg-white border-b border-gray-200">
        <div className="flex-1">
          <ViewFilter />
        </div>
        <div className="px-4 py-2 shrink-0 border-l border-gray-200">
          <SyncButton />
        </div>
        <div className="px-4 py-2 shrink-0 border-l border-gray-200">
          <ZoomControl />
        </div>
        {/* 자동배열 버튼 */}
        <div className="px-3 py-2 shrink-0 border-l border-gray-200">
          <button
            onClick={handleAutoSchedule}
            disabled={autoScheduleLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-medium text-white transition-opacity disabled:opacity-50"
            style={{ backgroundColor: "#C41230" }}
            title="최신 런에 대해 Stage 2 자동배열 실행"
          >
            {autoScheduleLoading ? (
              <span
                className="inline-block w-3 h-3 border-2 border-white border-t-transparent rounded-full"
                style={{ animation: "spin 1s linear infinite" }}
              />
            ) : (
              <svg
                width="12"
                height="12"
                viewBox="0 0 16 16"
                fill="currentColor"
              >
                <path d="M8 2a6 6 0 100 12A6 6 0 008 2zm0 1.5a4.5 4.5 0 110 9 4.5 4.5 0 010-9zm-.75 2v3.19l2.47 1.43.75-1.3L8.75 7.5V5.5h-1.5z" />
              </svg>
            )}
            자동배열
          </button>
        </div>
        {/* 납기 초과 현황 버튼 */}
        <div className="px-3 py-2 shrink-0 border-l border-gray-200">
          <button
            onClick={() => setShowLatePanel((v) => !v)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-medium transition-colors"
            style={{
              backgroundColor:
                lateTasks.length > 0
                  ? showLatePanel
                    ? "#7F1D1D"
                    : "#FEE2E2"
                  : "#F3F4F6",
              color:
                lateTasks.length > 0
                  ? showLatePanel
                    ? "#FCA5A5"
                    : "#B91C1C"
                  : "#6B7280",
              border: `1px solid ${lateTasks.length > 0 ? "#FECACA" : "#E5E7EB"}`,
            }}
            title="납기를 초과한 배치 목록 보기"
          >
            <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
              <path d="M8 1a7 7 0 100 14A7 7 0 008 1zm.75 3.5v4.25l3 1.73-.75 1.3L7.25 9.5V4.5h1.5z" />
            </svg>
            납기 초과
            {lateTasks.length > 0 && (
              <span
                className="ml-0.5 px-1.5 py-0.5 rounded-full text-[10px] font-bold text-white"
                style={{ backgroundColor: "#DC2626" }}
              >
                {lateTasks.length}
              </span>
            )}
          </button>
        </div>

        {/* SM재고 실적 버튼 — 편집모드일 때만 표시 */}
        {isEditMode && (
          <div className="px-3 py-2 shrink-0 border-l border-gray-200">
            <button
              onClick={() => setShowWipModal(true)}
              disabled={!wipRunLabel}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-medium text-white transition-opacity disabled:opacity-50"
              style={{ backgroundColor: "#C41230" }}
              title="SM재고 실적 업데이트"
            >
              <svg
                width="12"
                height="12"
                viewBox="0 0 16 16"
                fill="currentColor"
              >
                <path d="M2 3h12v2H2V3zm0 4h12v2H2V7zm0 4h8v2H2v-2z" />
              </svg>
              SM재고 실적
            </button>
          </div>
        )}
      </div>

      {/* 자동배열 결과 알림 */}
      {autoScheduleResult && (
        <div
          className="flex items-start justify-between gap-2 px-4 py-2 border-b text-[11px]"
          style={{
            backgroundColor:
              autoScheduleResult.startsWith("오류") ||
              autoScheduleResult.startsWith("연결")
                ? "#FEF2F2"
                : "#F0FDF4",
            borderColor:
              autoScheduleResult.startsWith("오류") ||
              autoScheduleResult.startsWith("연결")
                ? "#FECACA"
                : "#BBF7D0",
            color:
              autoScheduleResult.startsWith("오류") ||
              autoScheduleResult.startsWith("연결")
                ? "#B91C1C"
                : "#15803D",
          }}
        >
          <span>{autoScheduleResult}</span>
          <button
            onClick={() => setAutoScheduleResult(null)}
            className="shrink-0 text-gray-400 hover:text-gray-600"
          >
            ✕
          </button>
        </div>
      )}

      {/* 납기 초과 패널 */}
      {showLatePanel && (
        <div
          className="shrink-0 border-b overflow-auto"
          style={{
            maxHeight: 220,
            backgroundColor: "#FFF7F7",
            borderColor: "#FECACA",
          }}
        >
          <div
            className="flex items-center justify-between px-4 py-2 sticky top-0 border-b"
            style={{ backgroundColor: "#FEF2F2", borderColor: "#FECACA" }}
          >
            <div className="flex items-center gap-2">
              <span
                className="text-[11px] font-bold"
                style={{ color: "#991B1B" }}
              >
                납기 초과 배치
              </span>
              <span
                className="px-1.5 py-0.5 rounded-full text-[10px] font-bold text-white"
                style={{ backgroundColor: "#DC2626" }}
              >
                {lateTasks.length}건
              </span>
              <span className="text-[10px] text-gray-400">
                — 배치 종료 시각이 납기일을 초과한 수주 목록
              </span>
            </div>
            <button
              onClick={() => setShowLatePanel(false)}
              className="text-gray-400 hover:text-gray-600 text-xs"
            >
              ✕
            </button>
          </div>
          {lateTasks.length === 0 ? (
            <div className="px-4 py-3 text-[11px] text-gray-400">
              납기 초과 배치가 없습니다.
            </div>
          ) : (
            <table className="w-full text-[11px] border-collapse">
              <thead>
                <tr style={{ backgroundColor: "#FEE2E2" }}>
                  {[
                    "지연",
                    "상태",
                    "설비",
                    "규격",
                    "거래처",
                    "납기일",
                    "배치완료",
                    "배치그룹",
                  ].map((h) => (
                    <th
                      key={h}
                      className="px-3 py-1.5 text-left font-semibold border-b"
                      style={{
                        color: "#7F1D1D",
                        borderColor: "#FECACA",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {lateTasks.map(({ task: t, lateDays }) => (
                  <tr
                    key={t.id}
                    className="hover:bg-red-50 cursor-pointer border-b"
                    style={{ borderColor: "#FEE2E2" }}
                    onClick={() => {
                      // 해당 배치 블록 선택 및 스크롤
                      const store = useScheduleStore.getState();
                      store.selectTask(t.id);
                      setShowLatePanel(false);
                    }}
                  >
                    <td
                      className="px-3 py-1.5 font-bold"
                      style={{ color: "#DC2626", whiteSpace: "nowrap" }}
                    >
                      +{lateDays}일
                    </td>
                    <td
                      className="px-3 py-1.5"
                      style={{ whiteSpace: "nowrap" }}
                    >
                      {t.status === "unassigned" ? (
                        <span
                          className="px-1.5 py-0.5 rounded text-[10px] font-bold text-white"
                          style={{ backgroundColor: "#9CA3AF" }}
                        >
                          미배치
                        </span>
                      ) : (
                        <span className="text-gray-400 text-[10px]">
                          배치됨
                        </span>
                      )}
                    </td>
                    <td
                      className="px-3 py-1.5 text-gray-600"
                      style={{ whiteSpace: "nowrap" }}
                    >
                      {t.equipment_id}
                    </td>
                    <td className="px-3 py-1.5 text-gray-700 max-w-[160px] truncate">
                      {t.spec || t.product}
                    </td>
                    <td
                      className="px-3 py-1.5 text-gray-600"
                      style={{ whiteSpace: "nowrap" }}
                    >
                      {t.customer ?? "-"}
                    </td>
                    <td
                      className="px-3 py-1.5 font-medium"
                      style={{ color: "#B91C1C", whiteSpace: "nowrap" }}
                    >
                      {t.delivery_date instanceof Date
                        ? t.delivery_date.toLocaleDateString("ko-KR")
                        : new Date(t.delivery_date!).toLocaleDateString(
                            "ko-KR",
                          )}
                    </td>
                    <td
                      className="px-3 py-1.5 text-gray-500"
                      style={{ whiteSpace: "nowrap" }}
                    >
                      {(t.end instanceof Date
                        ? t.end
                        : new Date(t.end)
                      ).toLocaleDateString("ko-KR")}
                    </td>
                    <td
                      className="px-3 py-1.5 font-mono text-gray-400 text-[10px]"
                      style={{ whiteSpace: "nowrap" }}
                    >
                      {t.batch_group ?? "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* 로딩 / 에러 배너 */}
      {isLoading && (
        <div className="flex items-center justify-center gap-2 py-1.5 bg-blue-50 border-b border-blue-200">
          <div
            className="w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full"
            style={{ animation: "spin 1s linear infinite" }}
          />
          <span className="text-xs text-blue-600">
            스케줄 데이터 로드 중...
          </span>
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 px-4 py-1.5 bg-red-50 border-b border-red-200">
          <span className="text-xs text-red-600">{error}</span>
          <span className="text-xs text-gray-400">
            -- 백엔드 서버 연결을 확인하세요 (localhost:8000)
          </span>
        </div>
      )}

      {/* 편집 모드 표시 배너 */}
      {isEditMode && (
        <div className="flex items-center justify-center gap-2 py-1 bg-amber-50 border-b border-amber-200">
          <div
            className="w-2 h-2 rounded-full animate-pulse"
            style={{ backgroundColor: "#C41230" }}
          />
          <span className="text-xs font-medium" style={{ color: "#4A2C2A" }}>
            수정 모드 -- 작업 바를 드래그하여 이동하거나 하단 패널의 수주를
            드래그하여 배정하세요. 완료 후 저장하기를 클릭하세요.
          </span>
        </div>
      )}

      {/* 저장 완료 토스트 */}
      {showSavedToast && (
        <div
          className="fixed bottom-6 right-6 z-50 flex items-center gap-2 px-4 py-3 rounded-lg shadow-lg text-white text-sm font-medium"
          style={{ backgroundColor: "#16A34A" }}
        >
          <span>저장 완료 -- 새 버전이 생성되었습니다.</span>
        </div>
      )}

      {/* 선행 공정 이동 경고 토스트 */}
      {predecessorToast && (
        <div
          className="fixed bottom-6 left-1/2 z-50 flex items-center gap-2 px-4 py-3 rounded-lg shadow-lg text-sm font-medium"
          style={{
            backgroundColor: "#FFFBEB",
            color: "#92400E",
            border: "1px solid #FCD34D",
            transform: "translateX(-50%)",
          }}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 16 16"
            fill="currentColor"
            style={{ color: "#D97706", flexShrink: 0 }}
          >
            <path d="M8 1L1 14h14L8 1zm0 2.5l5.5 9.5h-11L8 3.5zM7.25 7v3.5h1.5V7h-1.5zm0 4.5v1.5h1.5v-1.5h-1.5z" />
          </svg>
          <span>{predecessorToast}</span>
          <button
            onClick={() => setPredecessorToast(null)}
            className="ml-2 text-amber-400 hover:text-amber-600"
          >
            ✕
          </button>
        </div>
      )}

      {/* 메인 콘텐츠 영역 — DndContext가 OrderInbox와 SchedulerView를 모두 감싼다 */}
      <DndContext
        sensors={sensors}
        onDragStart={handleDragStart}
        onDragMove={handleDragMove}
        onDragEnd={handleDragEnd}
      >
        <main className="flex-1 overflow-hidden flex flex-col">
          {/* 간트 차트 + 우측 상세 패널 */}
          <div className="flex-1 overflow-hidden flex flex-row min-h-0">
            {/* 간트 차트 + 제약 조건 */}
            <div
              className="flex-1 overflow-hidden flex flex-col p-3 gap-2 min-w-0"
              style={{ minHeight: 300 }}
            >
              <SchedulerView
                activeDragGroup={activeDragGroup}
                activeDragSq={activeDragSq}
                activeDragMaterial={activeDragMaterial}
                previewOverlay={cascade.modalState?.preview ?? null}
                focusedTaskId={hoveredTaskId}
              />
              <ConstraintAlert />
            </div>

            {/* 수주 상세 + 감사 트레일 패널 — 간트 차트 우측 */}
            {auditPanel.open &&
              (() => {
                const selectedTask = selectedTaskId
                  ? tasks.find((t) => t.id === selectedTaskId)
                  : null;
                const selectedEquipment = selectedTask
                  ? equipment.find((e) => e.id === selectedTask.equipment_id)
                  : null;

                return (
                  <div
                    data-testid="task-detail-panel"
                    className="shrink-0 border-l bg-white overflow-y-auto"
                    style={{
                      // 좁은 뷰포트(<~933px)에서 간트가 가려지지 않도록 비율 축소
                      // min(420px, 45vw): 기본 420px, 뷰포트가 좁을 땐 45% 폭으로 줄임
                      width: "min(420px, 45vw)",
                      borderColor: "#E5E7EB",
                    }}
                  >
                    {/* 패널 헤더 — sticky: 스크롤해도 항상 상단에 고정 */}
                    <div
                      className="flex items-center justify-between px-4 py-2 border-b sticky top-0 z-10"
                      style={{
                        backgroundColor: "#FDF2F2",
                        borderColor: "#F3D5D5",
                      }}
                    >
                      <div className="flex items-center gap-2">
                        <div
                          className="w-1 h-4 rounded-sm"
                          style={{ backgroundColor: "#C41230" }}
                        />
                        <span
                          className="text-[11px] font-semibold"
                          style={{ color: "#4A2C2A" }}
                        >
                          {batchGroupOrders.filter(
                            (o) => o.batch_seq == null || o.batch_seq >= 0,
                          ).length > 1
                            ? "배치 그룹 수주 목록"
                            : "수주 상세 정보"}
                        </span>
                        {selectedTask?.batch_group && (
                          <span
                            className="text-[10px] font-mono px-1.5 py-0.5 rounded"
                            style={{
                              backgroundColor: "#F3E8E8",
                              color: "#C41230",
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
                              selectedTask?.batch_group ??
                              processFlow[0]?.batch_group;
                            const currentIdx = processFlow.findIndex(
                              (p) => p.batch_group === currentBg,
                            );
                            const prev =
                              currentIdx > 0
                                ? processFlow[currentIdx - 1]
                                : null;
                            const next =
                              currentIdx >= 0 &&
                              currentIdx < processFlow.length - 1
                                ? processFlow[currentIdx + 1]
                                : null;
                            return (
                              <div className="flex items-center gap-1">
                                <button
                                  disabled={!prev}
                                  onClick={() =>
                                    prev &&
                                    navigateToProcessBatch(prev.batch_group)
                                  }
                                  className="px-2 py-0.5 text-[10px] rounded border disabled:opacity-30 hover:bg-gray-100 transition-colors"
                                  style={{
                                    borderColor: "#D1D5DB",
                                    color: "#4A2C2A",
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
                                  className="text-[9px] px-1 font-mono"
                                  style={{ color: "#9CA3AF" }}
                                >
                                  {currentIdx + 1}/{processFlow.length}
                                </span>
                                <button
                                  disabled={!next}
                                  onClick={() =>
                                    next &&
                                    navigateToProcessBatch(next.batch_group)
                                  }
                                  className="px-2 py-0.5 text-[10px] rounded border disabled:opacity-30 hover:bg-gray-100 transition-colors"
                                  style={{
                                    borderColor: "#D1D5DB",
                                    color: "#4A2C2A",
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
                        style={{ borderColor: "#F3F4F6" }}
                      >
                        {/* 작업 요약 1행 */}
                        <div className="grid grid-cols-4 gap-x-3 gap-y-2 mb-2">
                          <div className="flex flex-col gap-0.5">
                            <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                              규격
                            </span>
                            <span
                              className="text-[11px] font-semibold"
                              style={{ color: "#1F2937" }}
                            >
                              {selectedTask.spec || "-"}
                            </span>
                          </div>
                          <div className="flex flex-col gap-0.5">
                            <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                              배정 설비
                            </span>
                            <span
                              className="text-[11px] font-semibold"
                              style={{ color: "#1F2937" }}
                            >
                              {selectedEquipment?.name ||
                                selectedTask.equipment_id ||
                                "-"}
                            </span>
                          </div>
                          <div className="flex flex-col gap-0.5">
                            <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                              총 길이
                            </span>
                            <span
                              className="text-[11px] font-semibold"
                              style={{ color: "#1F2937" }}
                            >
                              {selectedTask.volume_m
                                ? `${selectedTask.volume_m.toLocaleString()}m`
                                : "-"}
                            </span>
                          </div>
                          <div className="flex flex-col gap-0.5">
                            <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                              작업 기간
                            </span>
                            <span className="text-[11px] text-gray-600">
                              {new Date(selectedTask.start).toLocaleString(
                                "ko-KR",
                                {
                                  month: "numeric",
                                  day: "numeric",
                                  hour: "2-digit",
                                  minute: "2-digit",
                                },
                              )}
                              {" ~ "}
                              {new Date(selectedTask.end).toLocaleString(
                                "ko-KR",
                                {
                                  month: "numeric",
                                  day: "numeric",
                                  hour: "2-digit",
                                  minute: "2-digit",
                                },
                              )}
                            </span>
                          </div>
                          <div className="flex flex-col gap-0.5">
                            <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                              선속
                            </span>
                            <span className="text-[11px] text-gray-600">
                              {selectedTask.line_speed_m_per_min
                                ? `${selectedTask.line_speed_m_per_min}m/min`
                                : "-"}
                            </span>
                          </div>
                          <div className="flex flex-col gap-0.5"></div>
                          {/* 배치 상태 — 클릭으로 순환 변경 */}
                          {(() => {
                            const STATUS_CYCLE: Record<string, string> = {
                              planned: "in_progress",
                              in_progress: "completed",
                              completed: "planned",
                            };
                            const STATUS_CONFIG: Record<
                              string,
                              { label: string; bg: string; text: string }
                            > = {
                              planned: {
                                label: "계획",
                                bg: "#6B7280",
                                text: "#fff",
                              },
                              in_progress: {
                                label: "진행",
                                bg: "#2563EB",
                                text: "#fff",
                              },
                              completed: {
                                label: "완료",
                                bg: "#059669",
                                text: "#fff",
                              },
                            };
                            const cfg =
                              STATUS_CONFIG[selectedTask.status] ??
                              STATUS_CONFIG.planned;
                            const handleStatusClick = async () => {
                              if (!selectedTask.batch_id) return;
                              const nextStatus =
                                STATUS_CYCLE[selectedTask.status] ?? "planned";
                              const prevStatus = selectedTask.status;
                              updateTask(selectedTask.id, {
                                status: nextStatus,
                              });
                              try {
                                const res = await fetch(
                                  `${API_BASE}/pipeline/batch/${selectedTask.batch_id}/status`,
                                  {
                                    method: "PATCH",
                                    headers: {
                                      "Content-Type": "application/json",
                                    },
                                    body: JSON.stringify({
                                      status: nextStatus,
                                    }),
                                  },
                                );
                                if (!res.ok)
                                  throw new Error(`HTTP ${res.status}`);
                              } catch {
                                updateTask(selectedTask.id, {
                                  status: prevStatus,
                                });
                              }
                            };
                            return (
                              <div className="flex flex-col gap-0.5">
                                <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
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
                                    fontSize: 11,
                                    fontWeight: 600,
                                    backgroundColor: cfg.bg,
                                    color: cfg.text,
                                    border: "none",
                                    cursor: selectedTask.batch_id
                                      ? "pointer"
                                      : "default",
                                    lineHeight: 1.5,
                                    width: "fit-content",
                                  }}
                                  title="클릭하여 상태 변경"
                                >
                                  {cfg.label}
                                  {selectedTask.batch_id && (
                                    <span
                                      style={{ fontSize: 8, opacity: 0.75 }}
                                    >
                                      ▾
                                    </span>
                                  )}
                                </button>
                              </div>
                            );
                          })()}

                          <div className="flex flex-col gap-0.5">
                            <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                              수주 건수
                            </span>
                            <span
                              className="text-[11px] font-semibold"
                              style={{ color: "#C41230" }}
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
                          <div className="flex items-center gap-2 py-2 text-[11px] text-gray-400">
                            <span
                              className="inline-block w-3 h-3 border-2 border-gray-300 border-t-transparent rounded-full"
                              style={{ animation: "spin 1s linear infinite" }}
                            />
                            수주 목록 로드 중...
                          </div>
                        ) : batchGroupOrders.length > 0 ? (
                          <BatchGroupOrderTable orders={batchGroupOrders} />
                        ) : (
                          /* batch_group가 없거나 API 실패 시 기존 단일 수주 정보 표시 */
                          <div className="grid grid-cols-6 gap-x-4 gap-y-1">
                            <div className="flex flex-col gap-0.5">
                              <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                                수주번호
                              </span>
                              <span className="text-[11px] text-gray-700">
                                {selectedTask.order_id || "-"}
                              </span>
                            </div>
                            <div className="flex flex-col gap-0.5">
                              <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                                거래처
                              </span>
                              <span className="text-[11px] text-gray-700">
                                {selectedTask.customer || "-"}
                              </span>
                            </div>
                            <div className="flex flex-col gap-0.5">
                              <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                                제품군
                              </span>
                              <span className="text-[11px] text-gray-700">
                                {selectedTask.product || "-"}
                              </span>
                            </div>
                            <div className="flex flex-col gap-0.5">
                              <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                                색상
                              </span>
                              <span className="text-[11px] text-gray-700">
                                {selectedTask.color || "-"}
                              </span>
                            </div>
                            <div className="flex flex-col gap-0.5">
                              <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                                납기
                              </span>
                              <span
                                className="text-[11px]"
                                style={{
                                  color: selectedTask.delivery_date
                                    ? new Date(
                                        selectedTask.delivery_date,
                                      ).getTime() < Date.now()
                                      ? "#DC2626"
                                      : "#4B5563"
                                    : "#9CA3AF",
                                }}
                              >
                                {selectedTask.delivery_date
                                  ? new Date(
                                      selectedTask.delivery_date,
                                    ).toLocaleDateString("ko-KR")
                                  : "-"}
                              </span>
                            </div>
                            <div className="flex flex-col gap-0.5">
                              <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                                길이
                              </span>
                              <span className="text-[11px] text-gray-700">
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
                          (selectedTask as { setup_time_min?: number })
                            .setup_time_min ??
                          selectedTask.changeover_min ??
                          0;
                        const colorChangeMin =
                          (selectedTask as { color_change_min?: number })
                            .color_change_min ?? 0;
                        const tb = computeTimeBreakdown(
                          startTs,
                          endTs,
                          setupMin + colorChangeMin,
                          (selectedTask as { equipment_id?: string })
                            .equipment_id,
                        );
                        const totalHrs = (
                          (endTs - startTs) /
                          (60 * 60 * 1000)
                        ).toFixed(1);
                        return (
                          <div
                            className="px-4 py-2 border-t"
                            style={{ borderColor: "#F3F4F6" }}
                          >
                            <div className="flex items-center gap-1.5 mb-1.5">
                              <svg
                                width="10"
                                height="10"
                                viewBox="0 0 16 16"
                                fill="#9CA3AF"
                              >
                                <path d="M8 1a7 7 0 100 14A7 7 0 008 1zm-.75 4v4.25l3 1.75.75-1.3-2.5-1.45V5h-1.25z" />
                              </svg>
                              <span className="text-[10px] font-medium text-gray-400">
                                작업 시간 구성
                              </span>
                            </div>
                            <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px]">
                              <span>
                                <span className="text-gray-400">총 기간</span>{" "}
                                <span className="font-medium text-gray-700">
                                  {totalHrs}h
                                </span>
                              </span>
                              <span>
                                <span className="text-gray-400">실제 작업</span>{" "}
                                <span
                                  className="font-medium"
                                  style={{ color: "#C41230" }}
                                >
                                  {tb.actualWork.toFixed(1)}h
                                </span>
                              </span>
                              {tb.gapHrs > 0 && (
                                <span>
                                  <span className="text-gray-400">
                                    주말·야간 gap
                                  </span>{" "}
                                  <span className="text-gray-600">
                                    {tb.gapHrs}h
                                  </span>
                                </span>
                              )}
                              {tb.breakHrs > 0 && (
                                <span>
                                  <span className="text-gray-400">
                                    평일 break
                                  </span>{" "}
                                  <span className="text-gray-600">
                                    {tb.breakHrs}h
                                  </span>
                                </span>
                              )}
                              {setupMin > 0 && (
                                <span>
                                  <span className="text-gray-400">
                                    규격교체
                                  </span>{" "}
                                  <span className="text-gray-600">
                                    {setupMin}분
                                  </span>
                                </span>
                              )}
                              {colorChangeMin > 0 && (
                                <span>
                                  <span className="text-gray-400">
                                    색상교체
                                  </span>{" "}
                                  <span className="text-gray-600">
                                    {colorChangeMin}분
                                  </span>
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
                          fill="#9CA3AF"
                        >
                          <path d="M8 1a7 7 0 100 14A7 7 0 008 1zm0 2a1 1 0 110 2 1 1 0 010-2zm-1 4h2v5H7V7z" />
                        </svg>
                        <span className="text-[10px] font-medium text-gray-400">
                          AI 스케줄링 근거
                        </span>
                      </div>
                      {auditPanel.loading && (
                        <div className="flex items-center gap-2 text-[11px] text-gray-400">
                          <span
                            className="inline-block w-3 h-3 border-2 border-gray-300 border-t-transparent rounded-full"
                            style={{ animation: "spin 1s linear infinite" }}
                          />
                          AI 설명 로드 중...
                        </div>
                      )}
                      {auditPanel.error && !auditPanel.loading && (
                        <p className="text-[11px] text-gray-400 italic">
                          스케줄링 근거를 불러올 수 없습니다.
                        </p>
                      )}
                      {auditPanel.data && !auditPanel.loading && (
                        <div className="flex flex-col gap-1">
                          {(auditPanel.data.explanation ||
                            auditPanel.data.reasoning) && (
                            <p className="text-[11px] text-gray-600 leading-relaxed whitespace-pre-wrap">
                              {auditPanel.data.explanation ??
                                auditPanel.data.reasoning}
                            </p>
                          )}
                          {auditPanel.data.scheduled_at && (
                            <p className="text-[10px] text-gray-400">
                              배정 시각:{" "}
                              {new Date(
                                auditPanel.data.scheduled_at,
                              ).toLocaleString("ko-KR")}
                            </p>
                          )}
                          {auditPanel.data.changed_by && (
                            <p className="text-[10px] text-gray-400">
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
                              <p key={k} className="text-[10px] text-gray-500">
                                <span className="font-medium">{k}</span>:{" "}
                                {String(v)}
                              </p>
                            ))}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })()}
          </div>
          {/* END 간트+상세 패널 row */}

          {/* 하단 미배정 수주 패널 */}
          <CollapsiblePanel
            title="미배정 작업"
            count={unscheduledOrderItems.length}
            defaultExpanded={unscheduledOrderItems.length > 0}
            onAnimatingChange={setPanelAnimating}
          >
            <OrderInbox isAnimating={panelAnimating} />
          </CollapsiblePanel>
        </main>

        {/* 드래그 오버레이 — 드래그 중인 아이템 미리보기 */}
        <DragOverlay dropAnimation={null}>
          {activeDrag?.type === "order" && activeDrag.order && (
            <div
              className="px-3 py-2 rounded-lg shadow-xl text-white text-xs font-semibold"
              style={{
                backgroundColor: "#C41230",
                minWidth: 120,
                pointerEvents: "none",
              }}
            >
              {activeDrag.order.product} {activeDrag.order.spec}
              <br />
              <span className="text-white/80 text-[9px]">
                {activeDrag.order.total_length_m.toLocaleString()}m
              </span>
            </div>
          )}
          {activeDrag?.type === "task" && activeDrag.task && (
            <div
              className="px-3 py-2 rounded-lg shadow-xl text-white text-xs font-semibold"
              style={{
                backgroundColor: "#4A2C2A",
                minWidth: 120,
                pointerEvents: "none",
              }}
            >
              {activeDrag.task.product} {activeDrag.task.spec}
              <br />
              <span className="text-white/80 text-[9px]">
                {activeDrag.task.volume_m.toLocaleString()}m
              </span>
            </div>
          )}
        </DragOverlay>
      </DndContext>

      {/* 수정 모드 경고 모달 — Portal로 body 레벨에 렌더링 */}
      {showEditWarning &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            style={{
              position: "fixed",
              inset: 0,
              zIndex: 99999,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              backgroundColor: "rgba(0,0,0,0.3)",
            }}
            onClick={() => setShowEditWarning(false)}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              style={{
                backgroundColor: "#FFFFFF",
                borderRadius: 8,
                padding: "28px 32px",
                boxShadow: "0 8px 32px rgba(0,0,0,0.18)",
                border: "1px solid #E5E7EB",
                minWidth: 420,
                maxWidth: 500,
              }}
            >
              <p
                className="text-sm font-semibold mb-2"
                style={{ color: "#1F2937" }}
              >
                수정 모드를 활성화해주세요
              </p>
              <p className="text-xs text-gray-500 mb-5 leading-relaxed">
                작업을 배정하거나 이동하려면 우측 상단의 "수정하기" 버튼을 먼저
                눌러주세요.
              </p>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setShowEditWarning(false)}
                  className="px-4 py-2 text-xs font-medium rounded-md transition-colors"
                  style={{
                    border: "1px solid #E5E7EB",
                    color: "#6B7280",
                  }}
                >
                  닫기
                </button>
                <button
                  onClick={() => {
                    setShowEditWarning(false);
                    toggleEditMode();
                  }}
                  className="px-4 py-2 text-xs font-medium rounded-md text-white transition-colors"
                  style={{ backgroundColor: "#C41230" }}
                >
                  수정하기
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}

      {/* 전역 오버레이 UI */}
      <ContextMenu />
      {/* Task 21 — TaskFormModal edit 경로에서 cascade 훅 commit 주입. */}
      <TaskFormModal onSubmitWithCascade={cascade.commit} />

      {/* SM재고 실적 모달 */}
      {showWipModal && wipRunLabel && (
        <WipUpdateModal
          runLabel={wipRunLabel}
          onClose={() => setShowWipModal(false)}
        />
      )}

      {/* 배치 분할 모달 */}
      <BatchSplitModal />

      {/* Task 21 — cascade v2 충돌 해소 모달.
          flag on + preview 에 pushes/pulls/unresolved 존재 시에만 open.
          onManualAdjust 는 수주 상세 라우팅 경로 미확정이므로 undefined (CTA 비표시). */}
      {cascade.modalState?.open && (
        <ConflictResolutionModal
          preview={cascade.modalState.preview}
          pullToggle={cascade.pullToggle}
          onPullToggle={cascade.setPullToggle}
          onApply={cascade.applyModal}
          onClose={cascade.closeModal}
          guidanceShown={cascade.modalState.guidanceShown}
          onRowHover={setHoveredTaskId}
        />
      )}

      {/* Task 4.3 — inbox 드롭 미배정 확인 모달 */}
      <UnassignConfirmModal
        isOpen={!!unassignFromDragState}
        batchGroup={unassignFromDragState?.batchGroup ?? ""}
        tasks={
          unassignFromDragState
            ? tasks.filter(
                (t) => t.batch_group === unassignFromDragState.batchGroup,
              )
            : []
        }
        onConfirm={(reason, dontAskAgain) => {
          if (unassignFromDragState) {
            void useScheduleStore
              .getState()
              .unassignBatchGroup(unassignFromDragState.batchGroup, reason);
            if (dontAskAgain) setSkipConfirmFromDrag(true);
            setUnassignFromDragState(null);
          }
        }}
        onCancel={() => setUnassignFromDragState(null)}
      />

      {/* Task 4.3 — BatchGroupCard → equipment-row 드롭 preview 확정 모달 */}
      <BatchGroupDragPreviewModal
        state={bgDrag.modalState}
        onApply={() => void bgDrag.applyModal()}
        onCancel={() => bgDrag.cancelModal()}
      />
    </div>
  );
}
