"use client";

import type { AiSummary } from "../types";

interface AiInsightCardProps {
  /** null이면 "분석 대기 중" 상태 표시 */
  summary: AiSummary | null;
}

export function AiInsightCard({ summary }: AiInsightCardProps) {
  // 분석 미실행 상태
  if (!summary) {
    return (
      <div
        className="p-4"
        style={{
          backgroundColor: "#F9FAFB",
          border: "1px solid #E5E7EB",
          borderRadius: 0,
        }}
      >
        <div className="flex items-center gap-2">
          <svg
            aria-hidden="true"
            style={{ width: 16, height: 16, color: "#9CA3AF" }}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z" />
          </svg>
          <span className="text-xs" style={{ color: "#9CA3AF" }}>
            분석 대기 중 — 배치 계산을 실행하세요
          </span>
        </div>
      </div>
    );
  }

  // source 배지 텍스트
  const sourceBadge = summary.source === "llm" ? "LLM" : "규칙기반";
  const badgeColor = summary.source === "llm" ? "#C41230" : "#6B7280";

  // 리스크 없음 + 분석 완료
  const noRisk =
    summary.riskCount === 0 &&
    (summary.source === "rule-based" || summary.source === "llm");

  return (
    <div
      className="p-4"
      style={{
        backgroundColor: "#FFFFFF",
        border: "1px solid #E5E7EB",
        borderRadius: 0,
      }}
    >
      {/* Title + source badge */}
      <div className="flex items-center gap-2 mb-3">
        <svg
          aria-hidden="true"
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
        <span
          className="text-[10px] px-1.5 py-0.5 rounded"
          style={{
            backgroundColor: badgeColor === "#C41230" ? "#FEE2E2" : "#F3F4F6",
            color: badgeColor,
            fontWeight: 500,
          }}
        >
          {sourceBadge}
        </span>
      </div>

      {/* Stats row */}
      <div
        className="flex items-center gap-4 mb-3 text-[11px]"
        style={{ color: "#374151" }}
      >
        <span>
          총 배치{" "}
          <strong style={{ color: "#111827" }}>
            {summary.totalGroups > 0
              ? `${summary.totalGroups}배치 (${summary.totalBatches}수주)`
              : `${summary.totalBatches}건`}
          </strong>
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

      {/* 리스크 미감지 — 긍정 메시지 */}
      {noRisk && (
        <div
          className="flex items-center gap-1.5 mb-2 text-[11px]"
          style={{ color: "#16A34A" }}
        >
          <svg
            style={{ width: 14, height: 14 }}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M20 6L9 17l-5-5" />
          </svg>
          <span>리스크 미감지 — 모든 배치가 정상 범위 내입니다</span>
        </div>
      )}

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
