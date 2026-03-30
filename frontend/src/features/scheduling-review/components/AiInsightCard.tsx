"use client";

import type { AiSummary } from "../types";

interface AiInsightCardProps {
  summary: AiSummary;
}

export function AiInsightCard({ summary }: AiInsightCardProps) {
  return (
    <div
      className="rounded-lg p-4"
      style={{
        backgroundColor: "#F0F4FF",
        border: "1px solid #E2E8F0",
        borderLeftWidth: 3,
        borderLeftColor: "#C41230",
      }}
    >
      {/* Title */}
      <div className="flex items-center gap-2 mb-3">
        <svg
          style={{ width: 16, height: 16, color: "#C41230" }}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z" />
        </svg>
        <span className="text-xs font-semibold" style={{ color: "#C41230" }}>
          AI 배치 분석 결과
        </span>
      </div>

      {/* Stats row */}
      <div
        className="flex items-center gap-4 mb-3 text-[11px]"
        style={{ color: "#374151" }}
      >
        <span>
          총 배치{" "}
          <strong style={{ color: "#111827" }}>{summary.totalBatches}건</strong>
        </span>
        <span style={{ color: "#D1D5DB" }}>|</span>
        <span>
          총 생산량{" "}
          <strong style={{ color: "#111827" }}>
            {summary.totalProductionM.toLocaleString()} M
          </strong>
        </span>
        <span style={{ color: "#D1D5DB" }}>|</span>
        <span>
          리스크{" "}
          <strong
            style={{
              color: summary.riskCount > 0 ? "#DC2626" : "#111827",
            }}
          >
            {summary.riskCount}건
          </strong>
        </span>
      </div>

      {/* Highlights */}
      {summary.highlights.length > 0 && (
        <ul className="space-y-1">
          {summary.highlights.map((highlight, idx) => (
            <li
              key={idx}
              className="text-[11px] flex items-start gap-1.5"
              style={{ color: "#374151" }}
            >
              <span
                className="mt-1 shrink-0 inline-block rounded-full"
                style={{
                  width: 4,
                  height: 4,
                  backgroundColor: "#C41230",
                }}
              />
              {highlight}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
