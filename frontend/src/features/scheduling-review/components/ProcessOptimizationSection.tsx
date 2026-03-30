"use client";

import { useState, useCallback, useRef } from "react";
import type { SchedulingBatch, WipItem } from "../types";
import type { ProcessGroup } from "@/shared/constants/processGroups";
import { ProductionBatchTable } from "./ProductionBatchTable";
import { WipInventoryTable } from "./WipInventoryTable";

interface ProcessOptimizationSectionProps {
  sectionNumber: number;
  title: string;
  processGroup: ProcessGroup;
  batches: SchedulingBatch[];
  wipItems?: WipItem[];
  wipTitle?: string;
}

export function ProcessOptimizationSection({
  sectionNumber,
  title,
  processGroup,
  batches,
  wipItems,
  wipTitle,
}: ProcessOptimizationSectionProps) {
  const hasWip = wipItems !== undefined && wipTitle;
  const [highlightedBatchId, setHighlightedBatchId] = useState<string | null>(
    null,
  );
  const [activeWipId, setActiveWipId] = useState<string | null>(null);
  const batchTableRef = useRef<HTMLDivElement>(null);

  const handleWipClick = useCallback(
    (matchedBatchId: string) => {
      setHighlightedBatchId(matchedBatchId);

      // 매칭된 WIP의 id 찾기
      const wip = wipItems?.find((w) => w.matchedBatchId === matchedBatchId);
      setActiveWipId(wip?.id ?? null);

      // 배치 테이블에서 해당 행으로 스크롤
      setTimeout(() => {
        const row = batchTableRef.current?.querySelector(
          `[data-batch-id="${matchedBatchId}"]`,
        );
        if (row) {
          row.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      }, 50);

      // 3초 후 하이라이트 해제
      setTimeout(() => {
        setHighlightedBatchId(null);
        setActiveWipId(null);
      }, 3000);
    },
    [wipItems],
  );

  return (
    <section className="mb-6">
      <h3 className="text-sm font-semibold mb-3" style={{ color: "#111827" }}>
        {sectionNumber}. {title}
      </h3>

      <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
        <div
          ref={batchTableRef}
          style={{ flex: "1 1 0%", minWidth: hasWip ? 800 : 0 }}
        >
          <ProductionBatchTable
            title={title}
            batches={batches}
            processGroup={processGroup}
            highlightedBatchId={highlightedBatchId}
          />
        </div>

        {hasWip && (
          <div style={{ flex: "0 0 288px", minWidth: 0 }}>
            <WipInventoryTable
              title={wipTitle}
              items={wipItems}
              onWipClick={handleWipClick}
              activeWipId={activeWipId}
            />
          </div>
        )}
      </div>
    </section>
  );
}
