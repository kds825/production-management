"use client";

import { useEffect, useMemo, useState, useCallback } from "react";
import Image from "next/image";
import { useSchedulingReviewStore } from "@/features/scheduling-review/store/schedulingReviewStore";
import type { SchedulingBatch } from "@/features/scheduling-review/types";
import { assignBatchNumbers } from "@/shared/utils/batchGrouping";
import { ProcessOptimizationSection } from "@/features/scheduling-review/components/ProcessOptimizationSection";
import { BatchCalculateButton } from "@/features/scheduling-review/components/BatchCalculateButton";
import { SchedulingResultTable } from "@/features/scheduling-review/components/SchedulingResultTable";
import { AiInsightCard } from "@/features/scheduling-review/components/AiInsightCard";

const API_BASE = "http://localhost:8000/api";

/** 파이프라인 실행 레코드 (GET /api/pipeline/runs) */
interface PipelineRun {
  run_label: string;
  created_at?: string;
  status?: string;
  warning_count?: number;
  batch_count?: number;
  outsource_count?: number;
}

const PROCESS_TABS = [
  "연선",
  "B100",
  "A100",
  "A120",
  "연합",
  "CV절연",
  "A150시스",
  "고압연선",
] as const;
type ProcessTab = (typeof PROCESS_TABS)[number];

/**
 * 배치 → 시트명 결정 (백엔드 excel_exporter._resolve_sheet_name과 동일 로직)
 * 저압시스는 색상으로 A100/A120 분기, 연선은 전압으로 저압/고압 분기
 */
function resolveSheetName(b: SchedulingBatch): ProcessTab {
  const bg = b.batch_group || "";

  // 연선: 전압으로 고압/저압 분기
  if (bg.startsWith("연선_")) {
    return b.voltage_type === "고압" ? "고압연선" : "연선";
  }
  if (bg.startsWith("저압절연_")) return "B100";
  if (bg.startsWith("A100_")) return "A100";
  if (bg.startsWith("A120_")) return "A120";
  if (bg.startsWith("연합_")) return "연합";
  if (bg.startsWith("고압절연_")) return "CV절연";
  if (bg.startsWith("고압시스_")) return "A150시스";

  return "연선"; // fallback
}

/** 탭별 배치 필터 — Excel 시트와 동일 분류 */
function filterBatchesByTab(
  allBatches: SchedulingBatch[],
  tab: ProcessTab,
): SchedulingBatch[] {
  return allBatches.filter((b) => resolveSheetName(b) === tab);
}

const PRIMARY = "#C41230";

export default function SchedulingReviewPage() {
  const {
    allBatches,
    yeonseoBatches,
    insulationBatches,
    sheatBatches,
    yeonaeoWip,
    insulationWip,
    isCalculating,
    isCalculated,
    isLoaded,
    calcError,
    aiInsights,
    aiSummary,
    activeTab,
    loadBatchesFromApi,
    isLoading: batchesLoading,
    loadError,
    calculateBatches,
    setActiveTab,
  } = useSchedulingReviewStore();

  // ── 파이프라인 런 목록 ──
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<string>("");
  const [runsLoading, setRunsLoading] = useState(false);
  const [activeProcessTab, setActiveProcessTab] = useState<ProcessTab>("연선");
  const [excelLoading, setExcelLoading] = useState(false);

  // 런 목록 로드
  const loadRuns = useCallback(async () => {
    setRunsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/pipeline/runs`);
      if (res.ok) {
        const data: PipelineRun[] = await res.json();
        setRuns(data);
        if (data.length > 0 && !selectedRun) {
          setSelectedRun(data[0].run_label);
        }
      }
    } catch {
      // 연결 실패 시 조용히 처리 — 기존 기능에 영향 없음
    } finally {
      setRunsLoading(false);
    }
  }, [selectedRun]);

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
      }
    } catch {
      // 에러 처리 생략 — 백엔드 미연결 환경 고려
    } finally {
      setExcelLoading(false);
    }
  }, [selectedRun]);

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
              </option>
            ))}
          </select>
        </div>

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

        {/* 외주 분류 건수 */}
        {selectedRunInfo?.outsource_count !== undefined &&
          selectedRunInfo.outsource_count > 0 && (
            <>
              <div className="h-4 w-px bg-gray-200" />
              <span
                className="text-[11px] font-medium px-2 py-0.5 rounded"
                style={{
                  backgroundColor: "#EFF6FF",
                  color: "#2563EB",
                  border: "1px solid #BFDBFE",
                }}
              >
                외주 분류 {selectedRunInfo.outsource_count}건
              </span>
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
          <span style={{ color: "#111827", fontWeight: 500 }}>
            {(() => {
              const stored =
                typeof window !== "undefined"
                  ? localStorage.getItem("plan_base_date")
                  : null;
              if (stored && stored.length === 8)
                return `${stored.slice(0, 4)}.${stored.slice(4, 6)}.${stored.slice(6, 8)}`;
              return new Date().toISOString().slice(0, 10).replace(/-/g, ".");
            })()}
          </span>
        </span>
      </div>

      {/* 공정별 탭 */}
      <div
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
        {/* 모든 탭 — filterBatchesByTab으로 Excel 시트와 동일 분류 */}
        <ProcessOptimizationSection
          sectionNumber={PROCESS_TABS.indexOf(activeProcessTab) + 1}
          title={`${activeProcessTab} 생산 최적화`}
          processGroup={
            activeProcessTab === "연선" ||
            activeProcessTab === "고압연선" ||
            activeProcessTab === "연합"
              ? "연선"
              : activeProcessTab === "B100" || activeProcessTab === "CV절연"
                ? "절연"
                : "시스"
          }
          batches={
            filterBatchesByTab(
              allBatches,
              activeProcessTab,
            ) as SchedulingBatch[]
          }
          wipItems={
            activeProcessTab === "연선"
              ? yeonaeoWip
              : activeProcessTab === "B100"
                ? insulationWip
                : undefined
          }
          wipTitle={
            activeProcessTab === "연선"
              ? "연선 재공(WIP) 재고"
              : activeProcessTab === "B100"
                ? "절연 재공(WIP) 재고"
                : undefined
          }
        />

        {/* 배치 계산 버튼 */}
        <BatchCalculateButton
          isCalculating={isCalculating}
          isCalculated={isCalculated}
          calcError={calcError}
          onCalculate={calculateBatches}
          disabled={totalBatches === 0}
        />

        {/* AI 분석 카드 — 로드 후 항상 표시 (대기/결과/미감지 상태 포함) */}
        {isLoaded && (
          <div className="mb-6">
            <AiInsightCard summary={aiSummary} />
          </div>
        )}

        {/* Section 4: 계산 완료 후에만 표시 */}
        {isCalculated && (
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
              onTabChange={setActiveTab}
            />
          </div>
        )}
      </div>
    </div>
  );
}
