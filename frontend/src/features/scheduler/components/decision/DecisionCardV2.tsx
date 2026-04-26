"use client";

/**
 * Phase 6 Step 4-MVP — DecisionCard v2 (4 sections).
 *
 * Header / VerdictSummary (CEO R2 1줄) / Why / Impact / Handoff.
 * Step 4-lift 에서 EquipmentDay / BundleCompare / Alternatives 추가.
 *
 * default expanded:
 *   백엔드 `section_default_expanded` 시나리오별 차등 (UI review §3.2).
 *   예: 시나리오 B (색상교체) → impact 펼침. 시나리오 C (규격교체) → impact +
 *   handoff 펼침.
 *
 * onFeedback prop 은 Step 6-MVP 에서 wire-up. 본 MVP 는 prop 형태만 정의.
 */

import { useDecisionCardV2 } from "../../hooks/useDecisionCardV2";

import type { DecisionCardV2 as DecisionCardV2Data } from "./decisionCardTypes";
import { Handoff } from "./Handoff";
import { Header } from "./Header";
import { Impact } from "./Impact";
import { VerdictSummary } from "./VerdictSummary";
import { Why } from "./Why";

interface ContainerProps {
  runLabel: string;
  batchId: number;
  onFeedback?: (anchor: string) => void;
}

interface ViewProps {
  data: DecisionCardV2Data;
  onFeedback?: (anchor: string) => void;
}

/** Container — fetch + status 분기. */
export function DecisionCardV2({
  runLabel,
  batchId,
  onFeedback,
}: ContainerProps) {
  const { data, status, error, refetch } = useDecisionCardV2({
    runLabel,
    batchId,
  });

  if (status === "loading") {
    return (
      <div
        role="status"
        aria-live="polite"
        className="border border-pwc-gray-200 rounded-md p-4 text-pwc-gray-500 text-pwc-body"
      >
        결정 카드 불러오는 중…
      </div>
    );
  }
  if (status === "no-trace") {
    return (
      <div className="border border-pwc-gray-200 rounded-md p-4 text-pwc-gray-500 text-pwc-body">
        결정 카드를 찾을 수 없습니다 (배치 ID: {batchId}).
      </div>
    );
  }
  if (status === "timeout") {
    return (
      <ErrorPanel
        message="결정 카드 응답이 지연됩니다."
        onRetry={refetch}
        runId={null}
      />
    );
  }
  if (status === "error") {
    return (
      <ErrorPanel
        message={error?.message ?? "알 수 없는 오류"}
        onRetry={refetch}
        runId={error?.runId ?? null}
      />
    );
  }
  if (status === "idle" || data == null) return null;

  return <DecisionCardV2View data={data} onFeedback={onFeedback} />;
}

/** Pure presenter — 단위 테스트용 (props only, hook 미사용). */
export function DecisionCardV2View({ data, onFeedback }: ViewProps) {
  const expanded = data.section_default_expanded;
  return (
    <article
      className="border border-pwc-gray-200 rounded-md bg-pwc-bg-elevated overflow-hidden"
      aria-label={`결정 카드 ${data.process_label} 배치 ${data.batch_id}`}
    >
      <Header
        processKey={data.process_key}
        processLabel={data.process_label}
        subChip={data.sub_chip}
        customerName={data.customer_name}
        customerPriority={data.customer_priority}
        dueDate={data.due_date}
        placementText={data.placement_text}
      />
      <div className="px-4 pt-3">
        <VerdictSummary text={data.verdict_summary} />
      </div>
      <Why
        lines={data.why}
        defaultExpanded={expanded.section_1 !== false}
        onFeedback={onFeedback}
      />
      <Impact
        block={data.impact}
        defaultExpanded={expanded.section_2 === true}
      />
      <Handoff
        handoff={data.handoff}
        wipMatch={data.wip_match}
        outsourceHandoff={data.outsource_handoff}
        defaultExpanded={expanded.section_3 === true}
      />
    </article>
  );
}

function ErrorPanel({
  message,
  onRetry,
  runId,
}: {
  message: string;
  onRetry: () => void;
  runId: string | null;
}) {
  return (
    <div className="border border-pwc-status-danger-text rounded-md p-4 bg-pwc-status-danger-bg">
      <p className="text-pwc-body text-pwc-status-danger-text">{message}</p>
      {runId ? (
        <p className="text-pwc-caption text-pwc-gray-500 mt-1 font-mono">
          run-id: {runId}
        </p>
      ) : null}
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 text-pwc-buttonText-sm bg-pwc-primary-400 text-pwc-text-inverse rounded px-3 py-1 hover:bg-pwc-primary-500"
      >
        다시 시도
      </button>
    </div>
  );
}
