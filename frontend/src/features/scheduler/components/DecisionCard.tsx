"use client";

/**
 * Week 4 Task 4B.3 — Decision Card
 *
 * Gantt 배치 클릭 시 행 아래로 슬라이드해 펼쳐지는 인라인 카드.
 * 3-zone 구성:
 *   Zone 1: 한 줄 요약 + 이동 가능 여부 pill
 *   Zone 2: 가중치 상위 3개 bar-chart mini-viz
 *   Zone 3: LLM narrator summary (template-fallback 뱃지)
 *
 * 수동 조정(`is_manually_adjusted=true`) 시 노란 변형 + manual_override 디스클로저.
 *
 * 구조 분리:
 *   - DecisionCardView: 순수 props 기반 presenter — 테스트 친화적
 *   - DecisionCard: useDecisionCard 훅을 호출하는 thin container
 *
 * 왜 분리했는가:
 *   vitest 환경(node, jsdom 없음) + "use client" 컴포넌트는 hook 을 호출하는 순간
 *   renderToStaticMarkup 결과가 빈 문자열로 떨어진다(Toast.test.tsx 가 동일 한계 기록).
 *   View 를 분리하면 prop-drive 만으로 SSR HTML 이 생성되어 단위 테스트가 가능.
 */

import { useDecisionCard, type DecisionData } from "../hooks/useDecisionCard";

// ============================================================================
// Constants
// ============================================================================

/** Bar chart 에 노출할 contributions 최대 개수 — 스펙상 top-3. */
const TOP_CONTRIBUTIONS = 3;

/** weight_applied 표시 단위 임계값 — 1000 이상은 "K" 접미사로 압축. */
const WEIGHT_K_THRESHOLD = 1000;

// ============================================================================
// Helpers
// ============================================================================

/**
 * weight 표기 — 큰 숫자는 K 접미사로 압축 (예: 48000 → "48K", 1234 → "1.2K", 850 → "850").
 * 가독성 우선: bar-chart 라벨이 짧아야 좁은 카드 폭에서도 잘 읽힘.
 */
function formatWeight(w: number): string {
  if (w >= WEIGHT_K_THRESHOLD) {
    const k = w / 1000;
    // 10K 이상은 정수 표기, 미만은 소수점 1자리.
    return k >= 10 ? `${Math.round(k)}K` : `${k.toFixed(1)}K`;
  }
  return String(Math.round(w));
}

/**
 * ISO datetime → "MM/DD HH:mm" 한국식 짧은 표기.
 * 날짜 없을 시 "-".
 */
function formatDateShort(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}/${dd} ${hh}:${mi}`;
}

// ============================================================================
// View — Pure presenter (props only, no hooks)
// ============================================================================

export type DecisionCardViewState =
  | { kind: "loading" }
  | { kind: "no-trace" }
  | { kind: "timeout"; onRetry: () => void }
  | { kind: "error"; message: string; onRetry: () => void }
  | { kind: "ok"; data: DecisionData };

export interface DecisionCardViewProps {
  state: DecisionCardViewState;
}

/**
 * 카드 외곽 — manual 변형(노란) 만 분기, 그 외엔 elevated bg.
 * border-l-4 + token 색상으로 좌측 강조 — Toast 와 동일 패턴.
 */
function cardShellClass(variant: "default" | "manual"): string {
  const base =
    "mx-2 my-2 rounded-lg shadow-sm border-l-4 px-4 py-3 " +
    "bg-[color:var(--color-bg-elevated)] text-[color:var(--color-text-primary)]";
  // 수동 조정은 warning 색상 좌측 보더 + 살짝 옅은 배경으로 시각 분리.
  // var(--color-warning) 는 KBI 토큰 (orange-600 계열) — "주의" 의미와 일치.
  if (variant === "manual") {
    return `${base} border-[color:var(--color-warning)] bg-[color:var(--color-bg-muted)]`;
  }
  return `${base} border-[color:var(--color-border-default)]`;
}

export function DecisionCardView({ state }: DecisionCardViewProps) {
  // ----- Loading skeleton -----
  if (state.kind === "loading") {
    return (
      <div
        data-testid="decision-card-loading"
        className={cardShellClass("default")}
      >
        <div className="flex flex-col gap-2 animate-pulse">
          <div
            data-testid="decision-card-skeleton-zone1"
            className="h-4 w-2/3 rounded bg-[color:var(--color-bg-muted)]"
          />
          <div
            data-testid="decision-card-skeleton-zone2"
            className="h-12 w-full rounded bg-[color:var(--color-bg-muted)]"
          />
          <div
            data-testid="decision-card-skeleton-zone3"
            className="h-3 w-5/6 rounded bg-[color:var(--color-bg-muted)]"
          />
        </div>
      </div>
    );
  }

  // ----- No-trace (404) -----
  if (state.kind === "no-trace") {
    return (
      <div
        data-testid="decision-card-no-trace"
        className={cardShellClass("default")}
      >
        <p className="text-xs text-[color:var(--color-text-secondary)]">
          이 배치는 솔버 트레이스가 없습니다 (manual placement)
        </p>
      </div>
    );
  }

  // ----- Timeout -----
  if (state.kind === "timeout") {
    return (
      <div
        data-testid="decision-card-timeout"
        className={cardShellClass("default")}
      >
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs text-[color:var(--color-warning)]">
            네트워크 응답이 느립니다
          </p>
          <button
            type="button"
            onClick={state.onRetry}
            className="text-xs font-medium px-2 py-1 rounded border border-[color:var(--color-border-default)] hover:bg-[color:var(--color-bg-muted)] transition-colors"
          >
            재시도
          </button>
        </div>
      </div>
    );
  }

  // ----- Generic error -----
  if (state.kind === "error") {
    return (
      <div
        data-testid="decision-card-error"
        className={cardShellClass("default")}
      >
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs text-[color:var(--color-danger)]">
            트레이스 조회 실패: {state.message}
          </p>
          <button
            type="button"
            onClick={state.onRetry}
            className="text-xs font-medium px-2 py-1 rounded border border-[color:var(--color-border-default)] hover:bg-[color:var(--color-bg-muted)] transition-colors"
          >
            재시도
          </button>
        </div>
      </div>
    );
  }

  // ----- Happy path (ok) -----
  const { data } = state;
  const isManual = data.is_manually_adjusted;
  const variant: "default" | "manual" = isManual ? "manual" : "default";

  // Pill 색상 결정 — 우선순위: INFEASIBLE > binding > 자유.
  // INFEASIBLE 은 배치 자체가 불가능 → 빨강.
  // binding_hard_constraints 가 있으면 이동 시 제약 위반 → 주황.
  // 둘 다 없으면 자유로이 이동 가능 → 초록.
  let pillLabel: string;
  let pillColorVar: string;
  if (data.solver_status === "INFEASIBLE") {
    pillLabel = "불가능";
    pillColorVar = "var(--color-danger)";
  } else if (data.binding_hard_constraints.length > 0) {
    const names = data.binding_hard_constraints
      .map((c) => c.korean_name)
      .join(", ");
    pillLabel = `제약됨: ${names}`;
    pillColorVar = "var(--color-warning)";
  } else {
    pillLabel = "이동 가능";
    pillColorVar = "var(--color-success)";
  }

  // Top-N contributions 추출 + bar 폭 비율 계산.
  // weight_applied 가 음수일 가능성은 백엔드 스펙상 없지만(가중치 ≥ 0), 절대값 기준으로
  // 안전하게 정렬해 미래 계약 확장에 견고하게 둔다.
  const sortedContribs = [...data.contributions]
    .sort((a, b) => Math.abs(b.weight_applied) - Math.abs(a.weight_applied))
    .slice(0, TOP_CONTRIBUTIONS);

  const maxWeight =
    sortedContribs.length > 0
      ? Math.max(...sortedContribs.map((c) => Math.abs(c.weight_applied)))
      : 1;

  const hasNoContributions = data.contributions.length === 0;

  return (
    <div
      data-testid="decision-card"
      data-manual={isManual ? "true" : "false"}
      className={cardShellClass(variant)}
    >
      {/* ============ Zone 1: 한 줄 요약 + pill ============ */}
      <div
        data-testid="decision-card-zone1"
        className="flex items-center justify-between gap-3 mb-2"
      >
        <div className="flex items-center gap-2 text-xs text-[color:var(--color-text-primary)] min-w-0">
          <span className="font-semibold truncate">
            {data.assigned_equipment_id ?? "미배정"}
          </span>
          <span className="text-[color:var(--color-text-secondary)]">·</span>
          <span className="text-[color:var(--color-text-secondary)]">
            {formatDateShort(data.assigned_start)} ~{" "}
            {formatDateShort(data.assigned_end)}
          </span>
          <span className="text-[color:var(--color-text-secondary)]">·</span>
          <span className="text-[10px] text-[color:var(--color-text-tertiary)] font-mono">
            {data.run_label}
          </span>
        </div>
        <span
          data-testid="decision-card-pill"
          className="inline-flex items-center text-[10px] font-medium px-2 py-0.5 rounded-full whitespace-nowrap border"
          style={{
            color: pillColorVar,
            borderColor: pillColorVar,
          }}
        >
          {pillLabel}
        </span>
      </div>

      {/* ============ Zone 2: contributions bar chart ============ */}
      <div data-testid="decision-card-zone2" className="mb-2">
        {hasNoContributions ? (
          <p
            data-testid="decision-card-empty-contributions"
            className="text-xs text-[color:var(--color-text-secondary)] py-1"
          >
            활성 제약 없음 — 자유 배정
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {sortedContribs.map((c) => {
              const ratio =
                maxWeight > 0 ? Math.abs(c.weight_applied) / maxWeight : 0;
              // 최소 시각 폭 보장 — 0% 면 bar 가 안보여 라벨만 둥둥 떠보임.
              const widthPct = Math.max(ratio * 100, 4);
              return (
                <li
                  key={c.constraint_id}
                  className="flex items-center gap-2 text-[11px]"
                >
                  <span className="w-14 shrink-0 text-[color:var(--color-text-secondary)] truncate">
                    {c.korean_name}
                  </span>
                  <div
                    className="flex-1 h-2 rounded bg-[color:var(--color-bg-muted)] overflow-hidden"
                    role="img"
                    aria-label={`${c.korean_name} 가중치 ${formatWeight(c.weight_applied)}`}
                  >
                    <div
                      className="h-full rounded bg-[color:var(--color-brand-primary)]"
                      style={{ width: `${widthPct}%` }}
                    />
                  </div>
                  <span className="w-12 shrink-0 text-right font-mono text-[color:var(--color-text-primary)]">
                    {formatWeight(c.weight_applied)}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* ============ Zone 3: LLM narrator + template-fallback badge ============ */}
      <div
        data-testid="decision-card-zone3"
        className="flex items-start gap-2 pt-2 border-t border-[color:var(--color-border-muted)]"
      >
        <p className="text-xs text-[color:var(--color-text-secondary)] flex-1">
          {data.llm_summary}
        </p>
        {data.llm_was_template && (
          <span
            data-testid="decision-card-template-badge"
            className="shrink-0 text-[9px] font-medium px-1.5 py-0.5 rounded bg-[color:var(--color-bg-muted)] text-[color:var(--color-text-secondary)] border border-[color:var(--color-border-default)]"
            title="LLM 호출 실패 — 템플릿 기반 요약"
          >
            템플릿
          </span>
        )}
      </div>

      {/* ============ Manual override 디스클로저 ============ */}
      {isManual && data.manual_override && (
        <details
          data-testid="decision-card-manual-override"
          className="mt-2 pt-2 border-t border-[color:var(--color-border-muted)]"
        >
          <summary className="cursor-pointer text-[11px] font-medium text-[color:var(--color-warning)]">
            수동 조정 내역
          </summary>
          <div className="mt-2 flex flex-col gap-1 text-[11px] text-[color:var(--color-text-secondary)]">
            <div>
              <span className="font-semibold text-[color:var(--color-text-primary)]">
                사유:
              </span>{" "}
              {data.manual_override.reason}
            </div>
            <div>
              <span className="font-semibold text-[color:var(--color-text-primary)]">
                원래 솔버 결정:
              </span>{" "}
              {data.manual_override.snapshot_before.assigned_equipment_id ??
                "미배정"}{" "}
              ·{" "}
              {formatDateShort(
                data.manual_override.snapshot_before.assigned_start,
              )}{" "}
              ~{" "}
              {formatDateShort(
                data.manual_override.snapshot_before.assigned_end,
              )}
            </div>
          </div>
        </details>
      )}
    </div>
  );
}

// ============================================================================
// Container — calls hook, maps to View state
// ============================================================================

export interface DecisionCardProps {
  /**
   * 백엔드 production_batch.batch_id (정수형 PK).
   * `null` 이면 카드 자체를 렌더링하지 않음 — 호출부에서 조건부 마운트 권장이지만
   * 안전망으로 컨테이너에서도 null 가드한다.
   */
  batchId: string | null;
}

export function DecisionCard({ batchId }: DecisionCardProps) {
  const { data, status, error, refetch } = useDecisionCard(batchId);

  if (batchId === null || status === "idle") return null;

  let viewState: DecisionCardViewState;
  if (status === "loading") {
    viewState = { kind: "loading" };
  } else if (status === "no-trace") {
    viewState = { kind: "no-trace" };
  } else if (status === "timeout") {
    viewState = { kind: "timeout", onRetry: refetch };
  } else if (status === "error") {
    viewState = {
      kind: "error",
      message: error?.message ?? "알 수 없는 오류",
      onRetry: refetch,
    };
  } else if (status === "ok" && data) {
    viewState = { kind: "ok", data };
  } else {
    // status === "ok" 인데 data 가 null 인 비정상 분기 — 안전한 폴백.
    return null;
  }

  return <DecisionCardView state={viewState} />;
}
