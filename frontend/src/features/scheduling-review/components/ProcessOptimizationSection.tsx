"use client";

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

  return (
    <section className="mb-6">
      <h3 className="text-sm font-semibold mb-3" style={{ color: "#111827" }}>
        {sectionNumber}. {title}
      </h3>

      <div
        className="flex gap-4"
        style={{
          flexWrap: "wrap",
        }}
      >
        {/* Left: batch table (flex-1, min-width forces wrap at narrow widths) */}
        <div style={{ flex: "1 1 0%", minWidth: hasWip ? 800 : 0 }}>
          <ProductionBatchTable
            title={title}
            batches={batches}
            processGroup={processGroup}
          />
        </div>

        {/* Right: WIP inventory (only if wipItems provided) */}
        {hasWip && (
          <div style={{ flex: "0 0 288px", minWidth: 0 }}>
            <WipInventoryTable title={wipTitle} items={wipItems} />
          </div>
        )}
      </div>
    </section>
  );
}
