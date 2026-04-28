/**
 * useBatchInspector — 우측 BatchInspector 패널의 상태/페치 라이프사이클 응집.
 *
 * 책임 (Week 7 Task 7B.1 분리):
 *   - selectedTaskId 변경 시:
 *     · AI explain (audit) 캐싱 fetch — 같은 batch_id 재클릭 시 LLM 재호출 방지
 *     · 배치 그룹 수주 목록 + 공정 흐름 병렬 fetch
 *   - navigateToProcessBatch — 이전/다음 공정 batch_group 으로 이동.
 *     간트에 이미 로드된 task 가 있으면 selectTask + range 조정 + 스크롤,
 *     없으면 수주 목록만 갱신.
 *
 * 비고:
 *   - explainCache 는 ref 기반 in-memory 캐시. tasks 변경(드래그 후) 시 페이지가
 *     resetExplainCache() 호출해 무효화.
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useScheduleStore } from "../store/scheduleStore";
import type { BatchGroupOrder } from "../page-sections/BatchGroupOrderTable";

const API_BASE = "http://localhost:8000/api";

/** 감사 설명 응답 (GET /api/audit/explain/{batch_id}) */
export interface AuditExplanation {
  batch_id: string;
  explanation?: string;
  reasoning?: string;
  scheduled_at?: string;
  changed_by?: string;
  [key: string]: unknown;
}

export interface AuditPanelState {
  open: boolean;
  batchId: string | null;
  data: AuditExplanation | null;
  loading: boolean;
  error: string | null;
}

export interface ProcessFlowEntry {
  batch_group: string;
  process_name: string;
  equipment_code?: string;
  order: number;
}

export interface UseBatchInspectorResult {
  auditPanel: AuditPanelState;
  setAuditPanel: React.Dispatch<React.SetStateAction<AuditPanelState>>;
  batchGroupOrders: BatchGroupOrder[];
  batchGroupLoading: boolean;
  processFlow: ProcessFlowEntry[];
  /** 이전/다음 공정 batch_group 으로 이동 */
  navigateToProcessBatch: (targetBatchGroup: string) => Promise<void>;
  /** 블록 변경 시 AI explain 캐시 무효화 */
  resetExplainCache: () => void;
}

export function useBatchInspector(): UseBatchInspectorResult {
  const tasks = useScheduleStore((s) => s.tasks);
  const selectedTaskId = useScheduleStore((s) => s.selectedTaskId);
  const selectTask = useScheduleStore((s) => s.selectTask);

  // AI explain 캐시 — 같은 batch_id 재클릭 시 LLM 재호출 방지
  const explainCache = useRef<Record<string, AuditExplanation>>({});

  const [auditPanel, setAuditPanel] = useState<AuditPanelState>({
    open: false,
    batchId: null,
    data: null,
    loading: false,
    error: null,
  });

  const [batchGroupOrders, setBatchGroupOrders] = useState<BatchGroupOrder[]>(
    [],
  );
  const [batchGroupLoading, setBatchGroupLoading] = useState(false);
  const [processFlow, setProcessFlow] = useState<ProcessFlowEntry[]>([]);

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
          explainCache.current[numericId] = data;
          setAuditPanel((prev) => ({ ...prev, loading: false, data }));
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
  useEffect(() => {
    if (!selectedTaskId) {
      // TODO(react19-migration): selectedTaskId=null 시 reset effect.
      // panel 을 selectedTaskId 기반 derived state 로 만들면 룰 통과.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setAuditPanel((prev) => ({ ...prev, open: false }));
      return;
    }
    handleTaskClick(selectedTaskId);
  }, [selectedTaskId, handleTaskClick]);

  // selectedTaskId 변경 시 batch_group 수주 목록 + 공정 흐름 조회
  useEffect(() => {
    if (!selectedTaskId) {
      // TODO(react19-migration): reset effect (위와 동일 패턴).
      // eslint-disable-next-line react-hooks/set-state-in-effect
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

  // 공정 흐름 네비게이션 — 이전/다음 공정의 batch_group 으로 이동
  const navigateToProcessBatch = useCallback(
    async (targetBatchGroup: string) => {
      // 간트에 이미 로드된 task 중 해당 batch_group 을 찾아 선택
      const targetTask = tasks.find((t) => t.batch_group === targetBatchGroup);
      if (targetTask) {
        selectTask(targetTask.id);
        // 간트 뷰를 해당 task 의 시작 시간 근처로 가로 스크롤
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

  const resetExplainCache = useCallback(() => {
    explainCache.current = {};
  }, []);

  return {
    auditPanel,
    setAuditPanel,
    batchGroupOrders,
    batchGroupLoading,
    processFlow,
    navigateToProcessBatch,
    resetExplainCache,
  };
}
