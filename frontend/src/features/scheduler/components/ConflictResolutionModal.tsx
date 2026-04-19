"use client";

/**
 * Task 16 cascade preview 응답을 섹션별 (충돌 해소 / 앞당김 제안 / 해소 불가) 로
 * 렌더링하는 모달.
 *
 * 설계 원칙:
 * - 색상은 전부 `--color-*` CSS 변수 경유 (raw hex 금지).  프로젝트 globals.css 정의 토큰:
 *   `--color-danger` (🔴/⛔), `--color-warning` (🟡), `--color-brand-primary`,
 *   `--color-text-primary/secondary`, `--color-bg-elevated/muted`, `--color-border-default`.
 * - Reason code → 한국어 라벨은 `reasonLabels.ts` 에 dictionary 로 분리 (테스트 가능).
 * - props 는 Task 17 의 `useScheduleChangeWithCascade` hook 출력과 1:1 호환.
 *
 * 렌더 조건:
 * - `preview.pushes/pulls/unresolved` 각 배열이 비어 있으면 해당 섹션 미표시.
 * - `preview.unresolved` 가 있으면 "적용" 버튼 disabled (auto-resolve 불가).
 * - `guidanceShown` true 면 422 재시도 상한 초과 배너 표시 + apply disabled.
 * - `onManualAdjust` 가 주어지면 unresolved 행에 "수동 조정 진입" 버튼 노출.
 */

import type {
  CascadePreviewResponse,
  PushEntry,
  UnresolvedEntry,
} from "../api/cascade.types";
import { UNRESOLVED_REASON_LABEL, labelForReason } from "./reasonLabels";

interface Props {
  preview: CascadePreviewResponse;
  pullToggle: boolean;
  onPullToggle: (v: boolean) => void;
  onApply: () => void;
  onClose: () => void;
  /** 해소불가 task 의 수동조정 딥링크 핸들러. undefined 면 버튼 미표시. */
  onManualAdjust?: (taskId: string) => void;
  /** 422 재시도 상한 도달 시 guidance 배너 노출. */
  guidanceShown?: boolean;
  /** row hover 시 간트 하이라이트 연동 (Task 22 — optional). */
  onRowHover?: (taskId: string | null) => void;
}

/** 날짜를 "M/D HH:mm" 형식으로 포맷 — locale ko-KR 은 24h 시간 사용. */
const fmt = (iso: string) =>
  new Date(iso).toLocaleString("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

/**
 * 이동 전후 시각 차이를 "+Nh" / "-Nh" 로 표현.
 * 3600_000 == ms in hour. 소수 1자리 — 블록 분할 최소 단위가 0.5h.
 */
const deltaH = (oldIso: string, newIso: string) => {
  const diff =
    (new Date(newIso).getTime() - new Date(oldIso).getTime()) / 3_600_000;
  const sign = diff >= 0 ? "+" : "";
  return `${sign}${diff.toFixed(1)}h`;
};

function Row({
  entry,
  onHover,
}: {
  entry: PushEntry;
  onHover?: (id: string | null) => void;
}) {
  return (
    <tr
      onMouseEnter={() => onHover?.(entry.task_id)}
      onMouseLeave={() => onHover?.(null)}
      className="kbi-modal-row border-b border-[color:var(--color-border-muted)]"
    >
      <td className="px-2 py-1.5 text-[11px] text-[color:var(--color-text-primary)]">
        {entry.batch_label}
      </td>
      <td className="px-2 py-1.5 text-[11px] text-[color:var(--color-text-secondary)]">
        {entry.equipment_code}
      </td>
      <td className="px-2 py-1.5 text-[11px]">
        <span className="text-[color:var(--color-text-tertiary)]">
          {fmt(entry.old_start)}
        </span>
        <span className="text-[color:var(--color-text-tertiary)]"> → </span>
        <span className="font-medium text-[color:var(--color-brand-primary)]">
          {fmt(entry.new_start)}
        </span>
      </td>
      <td className="px-2 py-1.5 text-[11px] font-mono text-[color:var(--color-text-primary)]">
        {deltaH(entry.old_start, entry.new_start)}
      </td>
      <td className="px-2 py-1.5 text-[11px] text-[color:var(--color-text-secondary)]">
        {labelForReason(entry.reason)}
      </td>
    </tr>
  );
}

function UnresolvedRow({
  entry,
  onManualAdjust,
}: {
  entry: UnresolvedEntry;
  onManualAdjust?: (id: string) => void;
}) {
  return (
    <li className="kbi-modal-unresolved-row flex flex-col gap-0.5 px-3 py-2 border-b border-[color:var(--color-border-muted)]">
      <div className="flex items-center gap-2">
        <strong className="text-[11px] text-[color:var(--color-text-primary)]">
          {entry.batch_label || entry.task_id}
        </strong>
        <span className="kbi-modal-unresolved-reason text-[10px] text-[color:var(--color-danger)]">
          {UNRESOLVED_REASON_LABEL[entry.reason] ?? entry.reason}
        </span>
      </div>
      {entry.detail && (
        <span className="kbi-modal-unresolved-detail text-[10px] text-[color:var(--color-text-tertiary)]">
          {entry.detail}
        </span>
      )}
      {onManualAdjust && (
        <button
          type="button"
          onClick={() => onManualAdjust(entry.task_id)}
          className="kbi-modal-cta self-start text-[11px] font-medium px-2 py-1 rounded border border-[color:var(--color-danger)] text-[color:var(--color-danger)] hover:bg-[color:var(--color-bg-muted)] transition-colors"
        >
          수동 조정 진입
        </button>
      )}
    </li>
  );
}

export function ConflictResolutionModal({
  preview,
  pullToggle,
  onPullToggle,
  onApply,
  onClose,
  onManualAdjust,
  guidanceShown,
  onRowHover,
}: Props) {
  // 적용 가능 조건: unresolved 없음 + guidance 배너 미노출.
  const canApply = preview.unresolved.length === 0 && !guidanceShown;
  const hasPushes = preview.pushes.length > 0;
  const hasPulls = preview.pulls.length > 0;
  const hasUnresolved = preview.unresolved.length > 0;

  // "적용" 버튼 서브텍스트 — 몇 건 재배치/앞당김/해소불가인지 요약.
  const subtext = [
    hasPushes && `+${preview.pushes.length}건 재배치`,
    hasPulls && pullToggle && `-${preview.pulls.length}건 앞당김`,
    hasUnresolved && `${preview.unresolved.length}건 해소불가`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      role="dialog"
      aria-labelledby="kbi-conflict-title"
      className="kbi-modal fixed inset-0 z-[99999] flex items-center justify-center bg-[color:var(--color-overlay)]"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-[color:var(--color-bg-elevated)] rounded-lg shadow-xl flex flex-col border border-[color:var(--color-border-default)]"
        style={{ minWidth: 560, maxWidth: 720, maxHeight: "85vh" }}
      >
        <header className="kbi-modal-header px-5 py-3 border-b border-[color:var(--color-border-default)]">
          <h2
            id="kbi-conflict-title"
            className="text-sm font-semibold text-[color:var(--color-text-primary)]"
          >
            배치 변경 확인
          </h2>
        </header>

        <div
          className="px-5 py-4 flex-1 overflow-y-auto flex flex-col gap-3"
          style={{ maxHeight: "65vh" }}
        >
          {preview.summary && (
            <p className="kbi-modal-summary text-xs text-[color:var(--color-text-secondary)]">
              {preview.summary}
            </p>
          )}

          {guidanceShown && (
            <div
              role="alert"
              className="kbi-modal-guidance px-3 py-2 rounded border border-[color:var(--color-warning)] text-[11px] text-[color:var(--color-warning)] bg-[color:var(--color-bg-muted)]"
            >
              자동 해소에 실패했습니다. 수동 조정이 필요합니다.
            </div>
          )}

          {hasPushes && (
            <section
              aria-labelledby="sec-push"
              className="kbi-modal-section kbi-modal-section--push"
            >
              <h3
                id="sec-push"
                className="flex items-center gap-1.5 text-xs font-semibold mb-1.5 text-[color:var(--color-text-primary)]"
              >
                <span
                  className="kbi-modal-icon-negative inline-block w-2 h-2 rounded-full bg-[color:var(--color-danger)]"
                  aria-hidden="true"
                />
                충돌 해소 ({preview.pushes.length})
              </h3>
              <table className="w-full text-left border border-[color:var(--color-border-default)] rounded">
                <thead className="bg-[color:var(--color-bg-muted)]">
                  <tr className="text-[10px] text-[color:var(--color-text-secondary)]">
                    <th className="px-2 py-1 font-medium">배치</th>
                    <th className="px-2 py-1 font-medium">설비</th>
                    <th className="px-2 py-1 font-medium">시간 변경</th>
                    <th className="px-2 py-1 font-medium">Δ</th>
                    <th className="px-2 py-1 font-medium">사유</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.pushes.map((p) => (
                    <Row
                      key={`push-${p.task_id}`}
                      entry={p}
                      onHover={onRowHover}
                    />
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {hasPulls && (
            <section
              aria-labelledby="sec-pull"
              className="kbi-modal-section kbi-modal-section--pull"
            >
              <div className="flex items-center justify-between mb-1.5">
                <h3
                  id="sec-pull"
                  className="flex items-center gap-1.5 text-xs font-semibold text-[color:var(--color-text-primary)]"
                >
                  <span
                    className="kbi-modal-icon-warning inline-block w-0 h-0 border-l-[5px] border-r-[5px] border-b-[8px] border-l-transparent border-r-transparent border-b-[color:var(--color-warning)]"
                    aria-hidden="true"
                  />
                  앞당김 제안 ({preview.pulls.length})
                </h3>
                <label className="kbi-modal-pull-toggle flex items-center gap-1.5 text-[11px] text-[color:var(--color-text-secondary)] cursor-pointer">
                  <input
                    type="checkbox"
                    checked={pullToggle}
                    onChange={(e) => onPullToggle(e.target.checked)}
                    aria-label="Pull 제안을 적용에 포함"
                  />
                  <span>Pull 포함</span>
                </label>
              </div>
              <table className="w-full text-left border border-[color:var(--color-border-default)] rounded">
                <thead className="bg-[color:var(--color-bg-muted)]">
                  <tr className="text-[10px] text-[color:var(--color-text-secondary)]">
                    <th className="px-2 py-1 font-medium">배치</th>
                    <th className="px-2 py-1 font-medium">설비</th>
                    <th className="px-2 py-1 font-medium">시간 변경</th>
                    <th className="px-2 py-1 font-medium">Δ</th>
                    <th className="px-2 py-1 font-medium">사유</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.pulls.map((p) => (
                    <Row
                      key={`pull-${p.task_id}`}
                      entry={p}
                      onHover={onRowHover}
                    />
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {hasUnresolved && (
            <section
              aria-labelledby="sec-unres"
              className="kbi-modal-section kbi-modal-section--unresolved"
            >
              <h3
                id="sec-unres"
                className="flex items-center gap-1.5 text-xs font-semibold mb-1.5 text-[color:var(--color-danger)]"
              >
                <span
                  className="kbi-modal-icon-negative-strong inline-block text-[color:var(--color-danger)] font-bold"
                  aria-hidden="true"
                >
                  ✕
                </span>
                해소 불가 ({preview.unresolved.length})
              </h3>
              <ul className="border border-[color:var(--color-border-default)] rounded bg-[color:var(--color-bg-muted)]">
                {preview.unresolved.map((u) => (
                  <UnresolvedRow
                    key={u.task_id}
                    entry={u}
                    onManualAdjust={onManualAdjust}
                  />
                ))}
              </ul>
            </section>
          )}
        </div>

        <footer className="kbi-modal-footer flex justify-end items-center gap-2 px-5 py-3 border-t border-[color:var(--color-border-default)]">
          <button
            type="button"
            onClick={onClose}
            className="kbi-button-secondary px-4 py-2 text-xs font-medium rounded-md border border-[color:var(--color-border-default)] text-[color:var(--color-text-secondary)] bg-[color:var(--color-bg-elevated)] hover:bg-[color:var(--color-bg-muted)] transition-colors"
          >
            닫기
          </button>
          <button
            type="button"
            onClick={onApply}
            disabled={!canApply}
            className="kbi-button-primary flex flex-col items-center px-4 py-2 text-xs font-medium rounded-md text-[color:var(--color-text-inverse)] bg-[color:var(--color-brand-primary)] hover:brightness-110 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <span>적용</span>
            {subtext && (
              <span className="kbi-button-subtext text-[10px] opacity-80 mt-0.5">
                {subtext}
              </span>
            )}
          </button>
        </footer>
      </div>
    </div>
  );
}

// Default export 호환
export default ConflictResolutionModal;
