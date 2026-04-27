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
          backgroundColor: "var(--bg-surface-alt)",
          border: "1px solid var(--color-border-default)",
          borderRadius: 0,
        }}
      >
        <div className="flex items-center gap-2">
          <svg
            aria-hidden="true"
            style={{
              width: 16,
              height: 16,
              color: "var(--color-text-tertiary)",
            }}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z" />
          </svg>
          <span
            className="text-xs"
            style={{ color: "var(--color-text-tertiary)" }}
          >
            분석 대기 중 — 배치 계산을 실행하세요
          </span>
        </div>
      </div>
    );
  }

  // source 배지 텍스트
  const sourceBadge = summary.source === "llm" ? "LLM" : "규칙기반";
  const badgeColor =
    summary.source === "llm"
      ? "var(--color-brand-primary)"
      : "var(--color-text-secondary)";

  // 리스크 없음 + 분석 완료
  const noRisk =
    summary.riskCount === 0 &&
    (summary.source === "rule-based" || summary.source === "llm");

  return (
    <div
      className="p-4"
      style={{
        backgroundColor: "var(--bg-surface)",
        border: "1px solid var(--color-border-default)",
        borderRadius: 0,
      }}
    >
      {/* Title + source badge */}
      <div className="flex items-center gap-2 mb-3">
        <svg
          aria-hidden="true"
          style={{ width: 16, height: 16, color: "var(--color-brand-primary)" }}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z" />
        </svg>
        <span
          className="text-xs font-semibold"
          style={{ color: "var(--color-brand-primary)" }}
        >
          AI 배치 분석 결과
        </span>
        <span
          className="text-tiny px-1.5 py-0.5 rounded"
          style={{
            backgroundColor:
              badgeColor === "var(--color-brand-primary)"
                ? "var(--kbi-red-tint-12)"
                : "var(--neutral-100)",
            color: badgeColor,
            fontWeight: 500,
          }}
        >
          {sourceBadge}
        </span>
      </div>

      {/* Stats row */}
      <div
        className="flex items-center gap-4 mb-3 text-small"
        style={{ color: "var(--neutral-text-primary)" }}
      >
        <span>
          총 배치{" "}
          <strong style={{ color: "var(--color-text-primary)" }}>
            {summary.totalGroups > 0
              ? `${summary.totalGroups}배치 (${summary.totalBatches}수주)`
              : `${summary.totalBatches}건`}
          </strong>
        </span>
        <span style={{ color: "var(--neutral-300)" }}>|</span>
        <span>
          총 생산량{" "}
          <strong style={{ color: "var(--color-text-primary)" }}>
            {summary.totalProductionM.toLocaleString()} M
          </strong>
        </span>
        <span style={{ color: "var(--neutral-300)" }}>|</span>
        <span>
          리스크{" "}
          <strong
            style={{
              color:
                summary.riskCount > 0
                  ? "var(--color-danger)"
                  : "var(--color-text-primary)",
            }}
          >
            {summary.riskCount}건
          </strong>
        </span>
      </div>

      {/* 리스크 미감지 — 긍정 메시지 */}
      {noRisk && (
        <div
          className="flex items-center gap-1.5 mb-2 text-small"
          style={{ color: "var(--status-success)" }}
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
              className="text-small flex items-start gap-1.5"
              style={{ color: "var(--neutral-text-primary)" }}
            >
              <span
                className="mt-1 shrink-0 inline-block rounded-full"
                style={{
                  width: 4,
                  height: 4,
                  backgroundColor: "var(--color-brand-primary)",
                }}
              />
              {highlight.replace(/\*\*/g, "")}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
