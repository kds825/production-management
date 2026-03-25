"use client";

import { useEffect, useMemo } from "react";
import Image from "next/image";
import { useSchedulingReviewStore } from "@/features/scheduling-review/store/schedulingReviewStore";
import { assignBatchNumbers } from "@/shared/utils/batchGrouping";
import { ProcessOptimizationSection } from "@/features/scheduling-review/components/ProcessOptimizationSection";
import { BatchCalculateButton } from "@/features/scheduling-review/components/BatchCalculateButton";
import { SchedulingResultTable } from "@/features/scheduling-review/components/SchedulingResultTable";
import { AiInsightCard } from "@/features/scheduling-review/components/AiInsightCard";

const PRIMARY = "#C41230";

export default function SchedulingReviewPage() {
  const {
    yeonseoBatches,
    insulationBatches,
    sheatBatches,
    yeonaeoWip,
    insulationWip,
    isCalculating,
    isCalculated,
    aiInsights,
    aiSummary,
    activeTab,
    loadFromPlanRegister,
    calculateBatches,
    setActiveTab,
  } = useSchedulingReviewStore();

  useEffect(() => {
    loadFromPlanRegister();
  }, [loadFromPlanRegister]);

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
            style={{ color: "#4A2C2A", letterSpacing: "-0.02em" }}
          >
            생산스케줄링 검토
          </h1>
        </div>
      </header>

      {/* 서머리 바 */}
      <div
        className="sticky top-14 z-40 bg-white border-b border-gray-200 px-6 py-2.5 flex items-center gap-4"
        style={{ minHeight: 44 }}
      >
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
            {new Date().toISOString().slice(0, 10).replace(/-/g, ".")}
          </span>
        </span>
      </div>

      {/* 본문 */}
      <div className="flex-1 overflow-auto px-6 py-6 flex flex-col gap-0">
        {/* Section 1: 연선 생산 최적화 */}
        <ProcessOptimizationSection
          sectionNumber={1}
          title="연선 생산 최적화"
          processGroup="연선"
          batches={yeonseoBatches}
          wipItems={yeonaeoWip}
          wipTitle="연선 재공(WIP) 재고"
        />

        {/* Section 2: 절연 생산 최적화 */}
        <ProcessOptimizationSection
          sectionNumber={2}
          title="절연 생산 최적화"
          processGroup="절연"
          batches={insulationBatches}
          wipItems={insulationWip}
          wipTitle="절연 재공(WIP) 재고"
        />

        {/* Section 3: 시스 최적화 */}
        <ProcessOptimizationSection
          sectionNumber={3}
          title="시스 최적화"
          processGroup="시스"
          batches={sheatBatches}
        />

        {/* 배치 계산 버튼 */}
        <BatchCalculateButton
          isCalculating={isCalculating}
          isCalculated={isCalculated}
          onCalculate={calculateBatches}
          disabled={totalBatches === 0}
        />

        {/* AI 분석 결과 + Section 4 (계산 후에만 표시) */}
        {isCalculated && (
          <>
            {aiSummary && (
              <div className="mb-6">
                <AiInsightCard summary={aiSummary} />
              </div>
            )}

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
                aiInsights={aiInsights}
                activeTab={activeTab}
                onTabChange={setActiveTab}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
