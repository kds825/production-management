"use client";

/**
 * DecisionConstraintsModal — DecisionCard 결정 상세 모달.
 *
 * 호출 경로:
 *   - DecisionCard zone1 pill 좌클릭
 *   - 간트 task block 또는 DecisionCard 우클릭 → ContextMenu "상세보기"
 *
 * 책임:
 *   - useDecisionCard 훅으로 동일 batch 의 DecisionData 페치 (DecisionCard 와
 *     별도 인스턴스 — modal 단독 사용 OK).
 *   - binding_hard_constraints 를 한 줄 join 이 아니라 줄바꿈 가능한 chip list
 *     로 표시 → 가독성 개선의 핵심.
 *   - contributions 가중치 chart 는 DecisionCard 와 동일 디자인 (TOP-N 제한
 *     없이 전부).
 *   - LLM 요약 + manual override 도 포함.
 *
 * Store 연동:
 *   scheduler/page.tsx 에 전역 마운트. store.decisionDetailModal.batchId 가
 *   null 이 아니면 표시. 닫기는 store.closeDecisionDetailModal.
 */

import { useScheduleStore } from "../store/scheduleStore";
import { useDecisionCard } from "../hooks/useDecisionCard";

/** weight_applied 표시 포맷 (DecisionCard 와 동일 규칙). */
function formatWeight(w: number): string {
  if (Number.isInteger(w)) return String(w);
  return w.toFixed(2);
}

function formatDateShort(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${m}/${day} ${hh}:${mm}`;
}

export function DecisionConstraintsModal() {
  const batchId = useScheduleStore((s) => s.decisionDetailModal.batchId);
  const closeModal = useScheduleStore((s) => s.closeDecisionDetailModal);

  // batchId 가 number 라 string 변환 후 훅 전달 (useDecisionCard 시그니처 일치).
  const { data, status, error, refetch } = useDecisionCard(
    batchId === null ? null : String(batchId),
  );

  if (batchId === null) return null;

  return (
    <div
      data-testid="decision-detail-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={closeModal}
    >
      <div
        className="bg-white rounded-lg shadow-xl w-[720px] max-w-[92vw] max-h-[88vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between border-b px-6 py-4 shrink-0">
          <div>
            <h2 className="text-lg font-bold">결정 상세</h2>
            <p className="text-xs text-gray-500">
              배치 #{batchId} · 활성 제약 / 가중치 기여 / 자연어 요약
            </p>
          </div>
          <button
            onClick={closeModal}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
            aria-label="닫기"
          >
            &times;
          </button>
        </div>

        {/* 본문 */}
        <div className="flex-1 overflow-auto px-6 py-5 flex flex-col gap-5">
          {/* ── 상태별 분기 ─────────────────────────────────────────── */}
          {status === "loading" && (
            <div className="flex flex-col gap-3 animate-pulse">
              <div className="h-4 w-2/3 rounded bg-gray-100" />
              <div className="h-20 w-full rounded bg-gray-100" />
              <div className="h-3 w-5/6 rounded bg-gray-100" />
            </div>
          )}

          {status === "no-trace" && (
            <p className="text-sm text-gray-500">
              이 배치는 솔버 트레이스가 없습니다 (수동 배치).
            </p>
          )}

          {(status === "timeout" || status === "error") && (
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm text-red-600">
                {status === "timeout"
                  ? "네트워크 응답이 느립니다."
                  : `트레이스 조회 실패: ${error?.message ?? "알 수 없음"}`}
              </p>
              <button
                type="button"
                onClick={refetch}
                className="text-xs font-medium px-3 py-1.5 rounded border border-gray-300 hover:bg-gray-50"
              >
                재시도
              </button>
            </div>
          )}

          {status === "ok" && data && (
            <>
              {/* 1) 배정 요약 — 설비 · 시간 · solver status */}
              <section>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  배정 요약
                </h3>
                <div className="grid grid-cols-3 gap-3 text-sm">
                  <div>
                    <div className="text-tiny text-gray-400">설비</div>
                    <div className="font-medium">
                      {data.assigned_equipment_id ?? "미배정"}
                    </div>
                  </div>
                  <div>
                    <div className="text-tiny text-gray-400">시작 ~ 종료</div>
                    <div className="font-medium">
                      {formatDateShort(data.assigned_start)} ~{" "}
                      {formatDateShort(data.assigned_end)}
                    </div>
                  </div>
                  <div>
                    <div className="text-tiny text-gray-400">솔버 상태</div>
                    <div className="font-medium">{data.solver_status}</div>
                  </div>
                </div>
              </section>

              {/* 2) 활성 hard constraints — 가독성 핵심 영역.
                  한 줄 join 대신 chip 줄바꿈 (flex-wrap). 각 chip 에 constraint_id
                  badge 표기로 시각적 식별 보강. */}
              <section>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  활성 제약 ({data.binding_hard_constraints.length}개)
                </h3>
                {data.binding_hard_constraints.length === 0 ? (
                  <p className="text-sm text-gray-400">
                    활성 제약 없음 — 자유 배정 가능
                  </p>
                ) : (
                  <ul className="flex flex-wrap gap-1.5">
                    {data.binding_hard_constraints.map((c) => (
                      <li
                        key={c.constraint_id}
                        className="inline-flex items-center gap-1.5 px-2 py-1 rounded border bg-orange-50 border-orange-200 text-xs"
                      >
                        <span className="font-mono text-tiny text-gray-500">
                          #{c.constraint_id}
                        </span>
                        <span className="font-medium text-gray-800">
                          {c.korean_name}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {/* 3) 가중치 기여 — TOP-N 제한 없이 전체 표시 */}
              <section>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  가중치 기여 ({data.contributions.length}개)
                </h3>
                {data.contributions.length === 0 ? (
                  <p className="text-sm text-gray-400">기여 항목 없음</p>
                ) : (
                  (() => {
                    const sorted = [...data.contributions].sort(
                      (a, b) =>
                        Math.abs(b.weight_applied) - Math.abs(a.weight_applied),
                    );
                    const maxW =
                      sorted.length > 0
                        ? Math.max(
                            ...sorted.map((c) => Math.abs(c.weight_applied)),
                          )
                        : 1;
                    return (
                      <ul className="flex flex-col gap-1.5">
                        {sorted.map((c) => {
                          const ratio =
                            maxW > 0 ? Math.abs(c.weight_applied) / maxW : 0;
                          const widthPct = Math.max(ratio * 100, 4);
                          return (
                            <li
                              key={c.constraint_id}
                              className="flex items-center gap-2 text-xs"
                            >
                              <span className="w-32 shrink-0 text-gray-600 truncate">
                                {c.korean_name}
                              </span>
                              <div
                                className="flex-1 h-2 rounded bg-gray-100 overflow-hidden"
                                role="img"
                                aria-label={`${c.korean_name} 가중치 ${formatWeight(c.weight_applied)}`}
                              >
                                <div
                                  className="h-full rounded bg-[color:var(--color-brand-primary)]"
                                  style={{ width: `${widthPct}%` }}
                                />
                              </div>
                              <span className="w-16 shrink-0 text-right font-mono text-gray-700">
                                {formatWeight(c.weight_applied)}
                              </span>
                            </li>
                          );
                        })}
                      </ul>
                    );
                  })()
                )}
              </section>

              {/* 4) LLM 요약 */}
              <section>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  자연어 요약
                  {data.llm_was_template && (
                    <span
                      className="ml-2 text-tiny font-medium px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 border border-gray-200"
                      title="LLM 호출 실패 — 템플릿 기반 요약"
                    >
                      템플릿
                    </span>
                  )}
                </h3>
                <p className="text-sm text-gray-700 leading-relaxed">
                  {data.llm_summary || "(요약 없음)"}
                </p>
              </section>

              {/* 5) 수동 조정 내역 (manual_override 있을 때만) */}
              {data.is_manually_adjusted && data.manual_override && (
                <section className="border-t pt-4">
                  <h3 className="text-xs font-semibold text-orange-600 uppercase tracking-wide mb-2">
                    수동 조정 내역
                  </h3>
                  <div className="text-sm text-gray-700 space-y-1">
                    <div>
                      <span className="font-semibold">사유: </span>
                      {data.manual_override.reason}
                    </div>
                    <div className="text-xs text-gray-500">
                      이전 배정:{" "}
                      {data.manual_override.snapshot_before
                        .assigned_equipment_id ?? "-"}{" "}
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
                </section>
              )}
            </>
          )}
        </div>

        {/* 푸터 */}
        <div className="flex justify-end gap-2 border-t px-6 py-3 shrink-0 bg-gray-50">
          <button
            type="button"
            onClick={closeModal}
            className="text-xs font-medium px-4 py-1.5 rounded border border-gray-300 hover:bg-white"
          >
            닫기
          </button>
        </div>
      </div>
    </div>
  );
}
