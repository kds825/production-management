"use client";

import { useEffect, useMemo, useState, useCallback, useRef } from "react";
import Image from "next/image";
import { useSchedulingReviewStore } from "@/features/scheduling-review/store/schedulingReviewStore";
import type { SchedulingBatch } from "@/features/scheduling-review/types";
import { assignBatchNumbers } from "@/shared/utils/batchGrouping";
import { ProcessOptimizationSection } from "@/features/scheduling-review/components/ProcessOptimizationSection";
import { BatchCalculateButton } from "@/features/scheduling-review/components/BatchCalculateButton";
import { SchedulingResultTable } from "@/features/scheduling-review/components/SchedulingResultTable";
import { AiInsightCard } from "@/features/scheduling-review/components/AiInsightCard";
import { OutsourceTable } from "@/features/scheduling-review/components/OutsourceTable";

const API_BASE = "http://localhost:8000/api";

/** 파이프라인 실행 레코드 (GET /api/pipeline/runs) */
interface PipelineRun {
  run_label: string;
  created_at?: string;
  status?: string;
  warning_count?: number;
  batch_count?: number;
  outsource_count?: number;
  /** stage1/update 로 파생된 경우 이전 run_label — 두 버전 비교의 기본 before 값 */
  parent_run_label?: string | null;
}

/** GET /api/pipeline/runs/compare 응답 (ScheduleDiffResponse 호환) */
interface RunCompareResponse {
  run_label_before: string;
  run_label_after: string;
  kind: string;
  created_at: string;
  summary: {
    moved: number;
    added: number;
    removed: number;
    unchanged: number;
    total_before: number;
    total_after: number;
  };
  moved_tasks: Array<{
    task_id: string;
    old_start: string | null;
    old_end: string | null;
    old_equipment: string | null;
    new_start: string | null;
    new_end: string | null;
    new_equipment: string | null;
    start_delta_hours: number | null;
    end_delta_hours: number | null;
    equipment_changed: boolean;
    batch_group?: string | null;
    process_name?: string | null;
    sales_order_id?: string | null;
    customer_name?: string | null;
    sheath_color?: string | null;
    cross_section?: number | null;
  }>;
  added_tasks: Array<{
    task_id: string;
    start: string | null;
    end: string | null;
    equipment: string | null;
    batch_group?: string | null;
    process_name?: string | null;
    sales_order_id?: string | null;
    customer_name?: string | null;
    sheath_color?: string | null;
    cross_section?: number | null;
    due_date?: string | null;
  }>;
  removed_tasks: Array<{
    task_id: string;
    start: string | null;
    end: string | null;
    equipment: string | null;
    batch_group?: string | null;
    process_name?: string | null;
    sales_order_id?: string | null;
    customer_name?: string | null;
    sheath_color?: string | null;
    cross_section?: number | null;
    due_date?: string | null;
  }>;
  unchanged_task_ids: string[];
}

const PROCESS_TABS = [
  "저압연선",
  "저압절연(B100)",
  "저압시스(A100)",
  "저압시스(A120)",
  "연합",
  "T/P(고내화)",
  "고압연선",
  "고압절연(CV)",
  "고압시스(A150)",
  "외주",
] as const;
type ProcessTab = (typeof PROCESS_TABS)[number];

/**
 * 배치 → 시트명 결정 (백엔드 excel_exporter._resolve_sheet_name과 동일 로직)
 * 저압시스는 색상으로 A100/A120 분기, 연선은 전압으로 저압/고압 분기
 */
function resolveSheetName(b: SchedulingBatch): ProcessTab {
  const bg = b.batch_group || "";
  const pn = b.processGroup;
  const isHV = b.voltage_type === "고압";

  // T/P 공정 (TFR-8 고내화)
  if (pn === "T/P") return "T/P(고내화)";

  // 연선/연합: process_name + voltage 기준
  if (pn === "연선") {
    if (bg.startsWith("연합_")) return "연합";
    return isHV ? "고압연선" : "저압연선";
  }

  // 절연
  if (pn === "절연") {
    return isHV ? "고압절연(CV)" : "저압절연(B100)";
  }

  // 시스: batch_group 접두사로 분기
  if (pn === "시스") {
    if (bg.startsWith("고압시스_")) return "고압시스(A150)";
    if (bg.startsWith("A100_")) return "저압시스(A100)";
    if (bg.startsWith("A120_")) return "저압시스(A120)";
    return "저압시스(A120)"; // fallback
  }

  // batch_group 접두사 fallback
  if (bg.startsWith("연합_")) return "연합";
  if (bg.startsWith("고압절연_")) return "고압절연(CV)";
  if (bg.startsWith("고압시스_")) return "고압시스(A150)";
  if (bg.startsWith("A100_")) return "저압시스(A100)";
  if (bg.startsWith("A120_")) return "저압시스(A120)";

  return "저압연선";
}

/** CORE 배치 판별 — 61연선 내부 7연선코어(T6BO/AL6BO)는 배치 테이블에서 숨김 */
function isCoreGroup(bg: string): boolean {
  return bg.startsWith("CORE-") || bg.startsWith("AL-CORE-");
}

/** 탭별 배치 필터 — Excel 시트와 동일 분류, CORE 배치 제외 */
function filterBatchesByTab(
  allBatches: SchedulingBatch[],
  tab: ProcessTab,
): SchedulingBatch[] {
  return allBatches.filter(
    (b) => resolveSheetName(b) === tab && !isCoreGroup(b.batch_group || ""),
  );
}

const PRIMARY = "#C41230";

export default function SchedulingReviewPage() {
  const {
    allBatches,
    yeonseoBatches,
    insulationBatches,
    sheatBatches,
    outsourcedOrders,
    yeonaeoWip,
    insulationWip,
    isCalculating,
    isCalculated,
    isLoaded,
    calcError,
    aiAnalysisStatus,
    aiInsights,
    aiSummary,
    activeTab,
    loadBatchesFromApi,
    isLoading: batchesLoading,
    loadError,
    calculateBatches,
  } = useSchedulingReviewStore();

  // ── 파이프라인 런 목록 ──
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<string>("");
  const [runsLoading, setRunsLoading] = useState(false);

  // ── 버전 비교 모달 ── selected run 과 그 parent_run_label 을 비교
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareData, setCompareData] = useState<RunCompareResponse | null>(
    null,
  );
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);
  const [activeProcessTab, setActiveProcessTab] =
    useState<ProcessTab>("저압연선");
  const [excelLoading, setExcelLoading] = useState(false);
  const outsourceRef = useRef<HTMLDivElement>(null);
  const batchTabRef = useRef<HTMLDivElement>(null);
  const [planDate, setPlanDate] = useState<string>("");

  useEffect(() => {
    const stored = localStorage.getItem("plan_base_date");
    if (stored && stored.length === 8) {
      setPlanDate(
        `${stored.slice(0, 4)}.${stored.slice(4, 6)}.${stored.slice(6, 8)}`,
      );
    } else {
      setPlanDate(new Date().toISOString().slice(0, 10).replace(/-/g, "."));
    }
  }, []);

  // 런 목록 로드
  const loadRuns = useCallback(async () => {
    setRunsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/pipeline/runs`);
      if (res.ok) {
        const data: PipelineRun[] = await res.json();
        setRuns(data);
        if (data.length > 0) {
          // 현재 선택된 run_label이 목록에 없으면 최신(첫 번째) run_label로 교체
          setSelectedRun((prev) =>
            data.some((r) => r.run_label === prev) ? prev : data[0].run_label,
          );
        }
      }
    } catch {
      // 연결 실패 시 조용히 처리 — 기존 기능에 영향 없음
    } finally {
      setRunsLoading(false);
    }
  }, []);

  // Excel 다운로드
  const handleExcelDownload = useCallback(async () => {
    if (!selectedRun) return;
    setExcelLoading(true);
    try {
      const res = await fetch(
        `${API_BASE}/pipeline/stage1/${encodeURIComponent(selectedRun)}/export`,
      );
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        // Content-Disposition 헤더에서 파일명을 추출하거나 기본값 사용
        const disposition = res.headers.get("content-disposition");
        const match = disposition?.match(
          /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/,
        );
        a.download =
          match?.[1]?.replace(/['"]/g, "") ?? `schedule_${selectedRun}.xlsx`;
        a.click();
        URL.revokeObjectURL(url);
      } else {
        const body = await res
          .json()
          .catch(() => ({ detail: `HTTP ${res.status}` }));
        alert(`Excel 다운로드 실패: ${body.detail ?? res.statusText}`);
      }
    } catch (err) {
      alert(
        `Excel 다운로드 오류: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setExcelLoading(false);
    }
  }, [selectedRun]);

  // 이전 버전과 비교 — 선택된 run 과 그 parent_run_label 을 /runs/compare 로 조회
  const handleCompareWithParent = useCallback(async () => {
    if (!selectedRun) return;
    const current = runs.find((r) => r.run_label === selectedRun);
    const parent = current?.parent_run_label;
    if (!parent) {
      setCompareError(
        "비교 대상(parent)이 없습니다. 최초 계획 실행은 비교할 이전 버전이 없습니다.",
      );
      setCompareOpen(true);
      setCompareData(null);
      return;
    }
    setCompareLoading(true);
    setCompareError(null);
    setCompareData(null);
    setCompareOpen(true);
    try {
      const url = `${API_BASE}/pipeline/runs/compare?before=${encodeURIComponent(
        parent,
      )}&after=${encodeURIComponent(selectedRun)}`;
      const res = await fetch(url);
      if (!res.ok) {
        const body = await res
          .json()
          .catch(() => ({ detail: `HTTP ${res.status}` }));
        setCompareError(body.detail ?? `비교 실패 (${res.status})`);
        return;
      }
      const data: RunCompareResponse = await res.json();
      setCompareData(data);
    } catch (err) {
      setCompareError(err instanceof Error ? err.message : String(err));
    } finally {
      setCompareLoading(false);
    }
  }, [selectedRun, runs]);

  // 런 목록 초기 로드
  useEffect(() => {
    loadRuns();
  }, [loadRuns]);

  // 선택된 런이 바뀌면 배치 API 호출
  useEffect(() => {
    if (selectedRun) {
      loadBatchesFromApi(selectedRun);
    }
  }, [selectedRun, loadBatchesFromApi]);

  // 선택된 런의 요약 정보
  const selectedRunInfo = useMemo(
    () => runs.find((r) => r.run_label === selectedRun),
    [runs, selectedRun],
  );

  const yeonseoGroupCount = useMemo(
    () => assignBatchNumbers(yeonseoBatches).size,
    [yeonseoBatches],
  );
  const insulationGroupCount = useMemo(
    () => assignBatchNumbers(insulationBatches).size,
    [insulationBatches],
  );
  const sheatGroupCount = useMemo(
    () => assignBatchNumbers(sheatBatches).size,
    [sheatBatches],
  );
  const totalBatches =
    yeonseoBatches.length + insulationBatches.length + sheatBatches.length;
  const hasWip = yeonaeoWip.length > 0 || insulationWip.length > 0;

  return (
    <div
      className="flex flex-col h-full overflow-hidden"
      style={{ backgroundColor: "#FAFAFA" }}
    >
      {/* 헤더 */}
      <header className="h-14 bg-white border-b border-gray-200 flex items-center px-6 sticky top-0 z-50 shrink-0 gap-4">
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
            style={{ color: "#4A2C2A", letterSpacing: "-0.02em" }}
          >
            생산스케줄링 검토
          </h1>
        </div>

        {/* 런 선택 드롭다운 */}
        <div className="flex items-center gap-2 ml-4">
          <label className="text-[11px] text-gray-500 whitespace-nowrap">
            실행 버전
          </label>
          <select
            value={selectedRun}
            onChange={(e) => setSelectedRun(e.target.value)}
            disabled={runsLoading || runs.length === 0}
            className="text-[11px] border border-gray-200 rounded px-2 py-1 bg-white text-gray-700 focus:outline-none focus:ring-1"
            style={{ minWidth: 160, fontSize: 11 }}
          >
            {runs.length === 0 && (
              <option value="">{runsLoading ? "로드 중..." : "런 없음"}</option>
            )}
            {runs.map((r) => (
              <option key={r.run_label} value={r.run_label}>
                {r.run_label}
                {r.created_at ? ` (${r.created_at.slice(0, 10)})` : ""}
                {r.parent_run_label ? " ←" : ""}
              </option>
            ))}
          </select>
        </div>

        {/* 이전 버전과 비교 — parent_run_label 이 있을 때만 활성 */}
        <button
          onClick={handleCompareWithParent}
          disabled={
            !selectedRun ||
            !runs.find((r) => r.run_label === selectedRun)?.parent_run_label
          }
          className="flex items-center gap-1 px-3 py-1.5 rounded text-[11px] font-medium transition-opacity disabled:opacity-40"
          style={{
            backgroundColor: "#EFF6FF",
            color: "#1E40AF",
            border: "1px solid #BFDBFE",
          }}
          title={
            runs.find((r) => r.run_label === selectedRun)?.parent_run_label
              ? `이전 버전(${
                  runs.find((r) => r.run_label === selectedRun)
                    ?.parent_run_label
                })과 비교`
              : "최초 실행 — 비교할 이전 버전 없음"
          }
        >
          <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
            <path d="M8 1a7 7 0 100 14A7 7 0 008 1zm0 13V2a6 6 0 010 12z" />
          </svg>
          이전 버전과 비교
        </button>

        {/* 계획 삭제 버튼 */}
        <button
          onClick={async () => {
            if (!selectedRun) return;
            if (!confirm(`"${selectedRun}" 계획을 삭제하시겠습니까?`)) return;
            const res = await fetch(
              `${API_BASE}/pipeline/runs/${encodeURIComponent(selectedRun)}`,
              { method: "DELETE" },
            );
            if (res.ok) {
              // store 초기화 + 페이지 새로고침
              window.location.reload();
            }
          }}
          disabled={!selectedRun}
          className="flex items-center gap-1 px-3 py-1.5 rounded text-[11px] font-medium transition-opacity disabled:opacity-40"
          style={{
            backgroundColor: "#FEF2F2",
            color: "#C41230",
            border: "1px solid #FECACA",
          }}
          title="선택한 계획 실행 삭제"
        >
          <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
            <path d="M5.5 5.5A.5.5 0 016 6v6a.5.5 0 01-1 0V6a.5.5 0 01.5-.5zm2.5 0a.5.5 0 01.5.5v6a.5.5 0 01-1 0V6a.5.5 0 01.5-.5zm3 .5a.5.5 0 00-1 0v6a.5.5 0 001 0V6z" />
            <path
              fillRule="evenodd"
              d="M14.5 3a1 1 0 01-1 1H13v9a2 2 0 01-2 2H5a2 2 0 01-2-2V4h-.5a1 1 0 010-2H6a1 1 0 011-1h2a1 1 0 011 1h3.5a1 1 0 011 1zM4.118 4L4 4.059V13a1 1 0 001 1h6a1 1 0 001-1V4.059L11.882 4H4.118z"
            />
          </svg>
          삭제
        </button>

        {/* Excel 다운로드 버튼 — 런이 선택된 경우에만 활성화 */}
        <button
          onClick={handleExcelDownload}
          disabled={!selectedRun || excelLoading}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-medium text-white transition-opacity disabled:opacity-40"
          style={{ backgroundColor: "#16A34A" }}
          title={
            selectedRun
              ? `${selectedRun} Excel 다운로드`
              : "런을 먼저 선택하세요"
          }
        >
          {excelLoading ? (
            <span
              className="inline-block w-3 h-3 border-2 border-white border-t-transparent rounded-full"
              style={{ animation: "spin 1s linear infinite" }}
            />
          ) : (
            <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
              <path d="M8 12l-4-4h2.5V4h3v4H12L8 12z" />
              <path d="M2 14h12v-2H2v2z" />
            </svg>
          )}
          Excel 다운로드
        </button>
      </header>

      {/* 서머리 배너 */}
      <div
        className="sticky top-14 z-40 bg-white border-b border-gray-200 px-6 py-2.5 flex items-center gap-4 flex-wrap"
        style={{ minHeight: 44 }}
      >
        {/* 배치 카운트 */}
        <div className="flex items-center gap-2">
          <span
            className="text-[11px] font-medium px-2 py-1 rounded"
            style={{ backgroundColor: "#FEF2F2", color: PRIMARY }}
          >
            연선 {yeonseoGroupCount}배치
          </span>
          <span
            className="text-[11px] font-medium px-2 py-1 rounded"
            style={{ backgroundColor: "#FEF2F2", color: PRIMARY }}
          >
            절연 {insulationGroupCount}배치
          </span>
          <span
            className="text-[11px] font-medium px-2 py-1 rounded"
            style={{ backgroundColor: "#FEF2F2", color: PRIMARY }}
          >
            시스 {sheatGroupCount}배치
          </span>
        </div>

        <div className="h-4 w-px bg-gray-200" />

        {/* 배치 그룹 수 + 수주 행 수 */}
        <span className="text-[11px] text-gray-500">
          총{" "}
          <span style={{ color: "#111827", fontWeight: 600 }}>
            {yeonseoGroupCount + insulationGroupCount + sheatGroupCount}
          </span>
          배치 ({selectedRunInfo?.batch_count ?? totalBatches}수주)
        </span>

        {/* 외주 분류 건수 — 클릭 시 외주 섹션으로 스크롤 */}
        {selectedRunInfo?.outsource_count !== undefined &&
          selectedRunInfo.outsource_count > 0 && (
            <>
              <div className="h-4 w-px bg-gray-200" />
              <button
                onClick={() => {
                  setActiveProcessTab("외주");
                  setTimeout(
                    () =>
                      batchTabRef.current?.scrollIntoView({
                        behavior: "smooth",
                      }),
                    100,
                  );
                }}
                className="text-[11px] font-medium px-2 py-0.5 rounded cursor-pointer transition-colors hover:bg-blue-100"
                style={{
                  backgroundColor: "#EFF6FF",
                  color: "#2563EB",
                  border: "1px solid #BFDBFE",
                }}
              >
                외주 분류 {selectedRunInfo.outsource_count}건
              </button>
            </>
          )}

        {/* 경고 수 (런 정보에 있을 때만 표시) */}
        {selectedRunInfo?.warning_count !== undefined &&
          selectedRunInfo.warning_count > 0 && (
            <>
              <div className="h-4 w-px bg-gray-200" />
              <span
                className="text-[11px] font-medium flex items-center gap-1"
                style={{ color: "#D97706" }}
              >
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 16 16"
                  fill="currentColor"
                >
                  <path d="M8 1L1 14h14L8 1zm0 2.5l5.5 9.5h-11L8 3.5zM7.25 7v3.5h1.5V7h-1.5zm0 4.5v1.5h1.5v-1.5h-1.5z" />
                </svg>
                경고 {selectedRunInfo.warning_count}건
              </span>
            </>
          )}

        <div className="h-4 w-px bg-gray-200" />

        <span
          className="text-[11px] flex items-center gap-1.5"
          style={{ color: hasWip ? "#16A34A" : "#9CA3AF" }}
        >
          <span
            className="inline-block rounded-full"
            style={{
              width: 6,
              height: 6,
              backgroundColor: hasWip ? "#16A34A" : "#D1D5DB",
            }}
          />
          WIP {hasWip ? "반영됨" : "없음"}
        </span>

        <div className="h-4 w-px bg-gray-200" />

        <span className="text-[11px] text-gray-500">
          계획일{" "}
          <span style={{ color: "#111827", fontWeight: 500 }}>{planDate}</span>
        </span>
      </div>

      {/* 공정별 탭 */}
      <div
        ref={batchTabRef}
        className="sticky bg-white border-b border-gray-200 px-6 flex items-center gap-0 shrink-0 overflow-x-auto"
        style={{ top: "calc(3.5rem + 44px)", zIndex: 39 }}
      >
        {PROCESS_TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveProcessTab(tab)}
            className="px-4 py-2.5 text-[11px] font-medium whitespace-nowrap transition-colors border-b-2"
            style={{
              borderBottomColor:
                activeProcessTab === tab ? PRIMARY : "transparent",
              color: activeProcessTab === tab ? PRIMARY : "#6B7280",
            }}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* 본문 */}
      <div className="flex-1 overflow-auto px-6 py-6 flex flex-col gap-0">
        {/* 외주 탭 선택 시 외주 테이블 표시 */}
        {activeProcessTab === "외주" ? (
          <div ref={outsourceRef} className="mb-6">
            <OutsourceTable orders={outsourcedOrders} />
          </div>
        ) : (
          <>
            {/* 모든 탭 — filterBatchesByTab으로 Excel 시트와 동일 분류 */}
            <ProcessOptimizationSection
              sectionNumber={PROCESS_TABS.indexOf(activeProcessTab) + 1}
              title={`${activeProcessTab} 생산 최적화`}
              processGroup={
                activeProcessTab === "저압연선" ||
                activeProcessTab === "고압연선" ||
                activeProcessTab === "연합"
                  ? "연선"
                  : activeProcessTab === "저압절연(B100)" ||
                      activeProcessTab === "고압절연(CV)"
                    ? "절연"
                    : activeProcessTab === "T/P(고내화)"
                      ? "T/P"
                      : "시스"
              }
              batches={
                filterBatchesByTab(
                  allBatches,
                  activeProcessTab,
                ) as SchedulingBatch[]
              }
              wipItems={
                // 가용 재고 = 예상 제외 (예상은 별도 탭으로 분리)
                activeProcessTab === "저압연선"
                  ? [
                      ...yeonaeoWip.filter(
                        (w) =>
                          !w.voltage_class.includes("고압") &&
                          w.status !== "예상",
                      ),
                      ...insulationWip.filter(
                        (w) =>
                          !w.voltage_class.includes("고압") &&
                          w.status !== "예상",
                      ),
                    ]
                  : activeProcessTab === "고압연선"
                    ? [
                        ...yeonaeoWip.filter(
                          (w) =>
                            w.voltage_class.includes("고압") &&
                            w.status !== "예상",
                        ),
                        ...insulationWip.filter(
                          (w) =>
                            w.voltage_class.includes("고압") &&
                            w.status !== "예상",
                        ),
                      ]
                    : activeProcessTab === "저압절연(B100)"
                      ? insulationWip.filter(
                          (w) =>
                            !w.voltage_class.includes("고압") &&
                            w.status !== "예상",
                        )
                      : activeProcessTab === "고압절연(CV)"
                        ? insulationWip.filter(
                            (w) =>
                              w.voltage_class.includes("고압") &&
                              w.status !== "예상",
                          )
                        : undefined
              }
              expectedWipItems={
                // 예상 재고 = 현재 run 의 헤더 배치에서 listener 가 만든 예정 출고분
                activeProcessTab === "저압연선"
                  ? [
                      ...yeonaeoWip.filter(
                        (w) =>
                          !w.voltage_class.includes("고압") &&
                          w.status === "예상",
                      ),
                      ...insulationWip.filter(
                        (w) =>
                          !w.voltage_class.includes("고압") &&
                          w.status === "예상",
                      ),
                    ]
                  : activeProcessTab === "고압연선"
                    ? [
                        ...yeonaeoWip.filter(
                          (w) =>
                            w.voltage_class.includes("고압") &&
                            w.status === "예상",
                        ),
                        ...insulationWip.filter(
                          (w) =>
                            w.voltage_class.includes("고압") &&
                            w.status === "예상",
                        ),
                      ]
                    : activeProcessTab === "저압절연(B100)"
                      ? insulationWip.filter(
                          (w) =>
                            !w.voltage_class.includes("고압") &&
                            w.status === "예상",
                        )
                      : activeProcessTab === "고압절연(CV)"
                        ? insulationWip.filter(
                            (w) =>
                              w.voltage_class.includes("고압") &&
                              w.status === "예상",
                          )
                        : undefined
              }
              wipTitle={
                activeProcessTab === "저압연선"
                  ? "저압 연선/절연 재공(WIP) 재고"
                  : activeProcessTab === "고압연선"
                    ? "고압 연선/절연 재공(WIP) 재고"
                    : activeProcessTab === "저압절연(B100)"
                      ? "저압 절연 재공(WIP) 재고"
                      : activeProcessTab === "고압절연(CV)"
                        ? "고압 절연 재공(WIP) 재고"
                        : undefined
              }
            />

            {/* 배치 계산 버튼 */}
            <BatchCalculateButton
              isCalculating={isCalculating}
              isCalculated={isCalculated}
              calcError={calcError}
              aiAnalysisStatus={aiAnalysisStatus}
              onCalculate={calculateBatches}
              disabled={totalBatches === 0}
            />

            {/* AI 분석 카드 — 로드 후 항상 표시 (대기/결과/미감지 상태 포함) */}
            {isLoaded && (
              <div className="mb-6">
                <AiInsightCard summary={aiSummary} />
              </div>
            )}

            {/* Section 4: AI 분석 완료 후에만 표시 */}
            {isLoaded && totalBatches > 0 && isCalculated && (
              <div className="mb-6">
                <h3
                  className="text-sm font-semibold mb-3"
                  style={{ color: "#111827" }}
                >
                  4. 생산 스케줄링 검토
                </h3>
                <SchedulingResultTable
                  yeonseoBatches={yeonseoBatches}
                  insulationBatches={insulationBatches}
                  sheatBatches={sheatBatches}
                  activeTab={activeTab}
                />
              </div>
            )}
          </>
        )}
        {/* 외주 탭이 아닌 경우에도 하단에 외주 참조 가능 */}
      </div>

      {/* ── 버전 비교 모달 ───────────────────────────────────────────────── */}
      {compareOpen && (
        <div
          className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/40"
          onClick={() => setCompareOpen(false)}
        >
          <div
            className="bg-white rounded-lg shadow-xl max-w-3xl w-full max-h-[80vh] overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="px-5 py-3 border-b border-gray-200 flex items-center justify-between">
              <div>
                <h2
                  className="text-sm font-semibold"
                  style={{ color: "#111827" }}
                >
                  버전 비교
                </h2>
                {compareData && (
                  <div className="text-[10px] text-gray-500 mt-0.5">
                    {compareData.run_label_before} →{" "}
                    {compareData.run_label_after}
                  </div>
                )}
              </div>
              <button
                onClick={() => setCompareOpen(false)}
                className="text-gray-400 hover:text-gray-600 text-xl leading-none"
              >
                ×
              </button>
            </div>

            {/* Body */}
            <div className="p-5 overflow-y-auto flex-1">
              {compareLoading && (
                <div className="text-[12px] text-gray-500">
                  비교 데이터 로드 중...
                </div>
              )}
              {compareError && (
                <div
                  className="text-[12px] p-3 rounded"
                  style={{
                    backgroundColor: "#FEE2E2",
                    color: "#991B1B",
                    border: "1px solid #FECACA",
                  }}
                >
                  {compareError}
                </div>
              )}
              {compareData && (
                <>
                  {/* 4개 요약 카드 */}
                  <div className="grid grid-cols-4 gap-2 mb-4">
                    <div
                      className="rounded p-3 text-center"
                      style={{ backgroundColor: "#ECFDF5", color: "#065F46" }}
                    >
                      <div className="text-[10px] font-medium">추가됨</div>
                      <div className="text-2xl font-bold">
                        {compareData.summary.added}
                      </div>
                    </div>
                    <div
                      className="rounded p-3 text-center"
                      style={{ backgroundColor: "#FEF3C7", color: "#92400E" }}
                    >
                      <div className="text-[10px] font-medium">이동됨</div>
                      <div className="text-2xl font-bold">
                        {compareData.summary.moved}
                      </div>
                    </div>
                    <div
                      className="rounded p-3 text-center"
                      style={{ backgroundColor: "#FEE2E2", color: "#991B1B" }}
                    >
                      <div className="text-[10px] font-medium">삭제됨</div>
                      <div className="text-2xl font-bold">
                        {compareData.summary.removed}
                      </div>
                    </div>
                    <div
                      className="rounded p-3 text-center"
                      style={{ backgroundColor: "#F3F4F6", color: "#374151" }}
                    >
                      <div className="text-[10px] font-medium">변경 없음</div>
                      <div className="text-2xl font-bold">
                        {compareData.summary.unchanged}
                      </div>
                    </div>
                  </div>

                  {/* 추가된 수주 */}
                  {compareData.added_tasks.length > 0 && (
                    <div className="mb-4">
                      <h3 className="text-[11px] font-semibold text-gray-700 mb-2">
                        추가된 배치 ({compareData.added_tasks.length})
                      </h3>
                      <div className="max-h-40 overflow-y-auto border border-gray-200 rounded">
                        {compareData.added_tasks.slice(0, 50).map((t) => (
                          <div
                            key={t.task_id}
                            className="px-2 py-1.5 border-b border-gray-100 text-[11px] flex items-center gap-2"
                          >
                            <span
                              className="inline-block w-1.5 h-1.5 rounded-full"
                              style={{ backgroundColor: "#059669" }}
                            />
                            <span className="font-medium">
                              {t.sales_order_id || "-"}
                            </span>
                            <span className="text-gray-500">
                              {t.process_name}
                            </span>
                            {t.customer_name && (
                              <span className="text-gray-400">
                                · {t.customer_name}
                              </span>
                            )}
                            {t.cross_section != null && (
                              <span className="text-gray-400">
                                · {t.cross_section}SQ
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* 삭제된 수주 */}
                  {compareData.removed_tasks.length > 0 && (
                    <div className="mb-4">
                      <h3 className="text-[11px] font-semibold text-gray-700 mb-2">
                        삭제된 배치 ({compareData.removed_tasks.length})
                      </h3>
                      <div className="max-h-40 overflow-y-auto border border-gray-200 rounded">
                        {compareData.removed_tasks.slice(0, 50).map((t) => (
                          <div
                            key={t.task_id}
                            className="px-2 py-1.5 border-b border-gray-100 text-[11px] flex items-center gap-2"
                          >
                            <span
                              className="inline-block w-1.5 h-1.5 rounded-full"
                              style={{ backgroundColor: "#DC2626" }}
                            />
                            <span className="font-medium">
                              {t.sales_order_id || "-"}
                            </span>
                            <span className="text-gray-500">
                              {t.process_name}
                            </span>
                            {t.customer_name && (
                              <span className="text-gray-400">
                                · {t.customer_name}
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* 이동된 배치 (상위 5건) */}
                  {compareData.moved_tasks.length > 0 && (
                    <div className="mb-4">
                      <h3 className="text-[11px] font-semibold text-gray-700 mb-2">
                        이동된 배치 (상위{" "}
                        {Math.min(5, compareData.moved_tasks.length)}/
                        {compareData.moved_tasks.length})
                      </h3>
                      <div className="max-h-56 overflow-y-auto border border-gray-200 rounded">
                        {[...compareData.moved_tasks]
                          .sort(
                            (a, b) =>
                              Math.abs(b.start_delta_hours ?? 0) -
                              Math.abs(a.start_delta_hours ?? 0),
                          )
                          .slice(0, 5)
                          .map((t) => (
                            <div
                              key={t.task_id}
                              className="px-2 py-1.5 border-b border-gray-100 text-[11px] flex items-center gap-2"
                            >
                              <span
                                className="inline-block w-1.5 h-1.5 rounded-full"
                                style={{ backgroundColor: "#D97706" }}
                              />
                              <span className="font-medium">
                                {t.sales_order_id || "-"}
                              </span>
                              <span className="text-gray-500">
                                {t.process_name}
                              </span>
                              {t.start_delta_hours != null && (
                                <span
                                  className="font-mono"
                                  style={{
                                    color:
                                      t.start_delta_hours > 0
                                        ? "#DC2626"
                                        : "#059669",
                                  }}
                                >
                                  {t.start_delta_hours > 0 ? "+" : ""}
                                  {t.start_delta_hours.toFixed(1)}h
                                </span>
                              )}
                              {t.equipment_changed && (
                                <span className="text-gray-400">
                                  · 설비변경
                                </span>
                              )}
                            </div>
                          ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
