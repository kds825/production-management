"use client";

/**
 * DecisionConstraintsModal — DecisionCard 결정 상세 모달.
 *
 * 호출 경로:
 *   - DecisionCard zone1 pill 좌클릭
 *   - 간트 task block 또는 DecisionCard 우클릭 → ContextMenu "상세보기"
 *
 * 정보 위계 (P0 reorganize — 의사결정 지원 뷰):
 *   1. LLM 자연어 요약 — 헤더 직후, 큰 글씨. 운영자가 가장 먼저 읽는 한 문단.
 *   2. 메타 summary — 가중치 분포에서 도출한 한 줄. "지배 여부" 즉시 판별.
 *   3. 배정 요약 — 설비/시간/솔버 상태.
 *   4. 활성 제약 — `constraint_id` prefix(`split("-")[0]`) 로 카테고리 grouping.
 *   5. 가중치 기여 — 절대값 대신 % 변환, weight=0 항목은 expandable 로 숨김.
 *   6. 수동 조정 내역 — manual_override 있을 때만.
 *
 * Store 연동:
 *   scheduler/page.tsx 에 전역 마운트. store.decisionDetailModal.batchId 가
 *   null 이 아니면 표시. 닫기는 store.closeDecisionDetailModal.
 */

import { useScheduleStore } from "../store/scheduleStore";
import {
  useDecisionCard,
  type DecisionContribution,
  type DecisionBindingHardConstraint,
} from "../hooks/useDecisionCard";

/** constraint_id prefix → 카테고리 라벨. backend `constraint_rules` 분류와 일치. */
const CATEGORY_LABELS: Record<string, string> = {
  "1": "고객/수주",
  "2": "공정/외주",
  "3": "색상",
  "4": "시간",
  "5": "설비",
  "6": "일정",
  "7": "품질",
  "9": "라우팅",
  "10": "통합",
};

function categoryOf(constraintId: string): string {
  const idx = constraintId.indexOf("-");
  return idx === -1 ? constraintId : constraintId.slice(0, idx);
}

function categoryLabel(prefix: string): string {
  return CATEGORY_LABELS[prefix] ?? "기타";
}

/** weight_applied 표시 포맷 (DecisionCard 와 동일 규칙) — 절대값 raw. */
function formatWeight(w: number): string {
  if (Number.isInteger(w)) return String(w);
  return w.toFixed(2);
}

/**
 * 가중치 비율을 % 로 표시.
 *  - 99.95% 이상은 "100%" (보기 깔끔하게)
 *  - 10% 이상은 소수점 1자리
 *  - 미만은 소수점 2자리 — 0.01% 차이도 의사결정에 의미
 */
function formatPercent(value: number): string {
  if (value >= 99.95) return "100%";
  if (value >= 10) return `${value.toFixed(1)}%`;
  return `${value.toFixed(2)}%`;
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

/**
 * 활성 제약을 prefix 기준으로 묶고 prefix 의 숫자 순서로 정렬.
 * 알 수 없는 prefix(예: 8, 11)는 맨 뒤로.
 */
interface ConstraintGroup {
  prefix: string;
  label: string;
  items: DecisionBindingHardConstraint[];
}

function groupByCategory(
  items: DecisionBindingHardConstraint[],
): ConstraintGroup[] {
  const map = new Map<string, DecisionBindingHardConstraint[]>();
  for (const item of items) {
    const prefix = categoryOf(item.constraint_id);
    const bucket = map.get(prefix);
    if (bucket) {
      bucket.push(item);
    } else {
      map.set(prefix, [item]);
    }
  }
  return [...map.entries()]
    .sort(([a], [b]) => {
      const na = Number(a);
      const nb = Number(b);
      if (Number.isNaN(na) && Number.isNaN(nb)) return a.localeCompare(b);
      if (Number.isNaN(na)) return 1;
      if (Number.isNaN(nb)) return -1;
      return na - nb;
    })
    .map(([prefix, items]) => ({
      prefix,
      label: categoryLabel(prefix),
      items,
    }));
}

/**
 * 가중치 분포에서 한 줄 메타 요약을 도출.
 *  - top 1 ≥ 80% : 단일 제약 지배 → 이동 어려움
 *  - top 3 합 ≥ 80% : 소수 제약 지배
 *  - 그 외 : 분산
 *
 * 왜 임계값 80%인가: 사용자 피드백 "이 배치는 33개 제약 중 거래처 우선순위 1개가
 * 99.9% 결정" 사례를 즉시 잡아내기 위함. 0.99 이상에서만 발화하면 90% 사례를
 * 놓치므로 0.8 로 두 단계 분기.
 */
function computeMetaSummary(
  contributions: DecisionContribution[],
  bindingCount: number,
): string {
  if (contributions.length === 0 && bindingCount === 0) {
    return "활성 제약·가중치 기여 없음 — 자유 배정.";
  }
  const total = contributions.reduce(
    (acc, c) => acc + Math.abs(c.weight_applied),
    0,
  );
  if (total === 0) {
    return `활성 제약 ${bindingCount}개, 가중치 기여 모두 0 — 하드 제약만 작동.`;
  }
  const sorted = [...contributions].sort(
    (a, b) => Math.abs(b.weight_applied) - Math.abs(a.weight_applied),
  );
  const top = sorted[0];
  const topRatio = Math.abs(top.weight_applied) / total;

  if (topRatio >= 0.8) {
    return `활성 제약 ${bindingCount}개 중 "${top.korean_name}" 1개가 ${formatPercent(topRatio * 100)} 결정 — 다른 설비/시간으로 이동 어려움.`;
  }

  const top3Sum = sorted
    .slice(0, 3)
    .reduce((acc, c) => acc + Math.abs(c.weight_applied), 0);
  const top3Ratio = top3Sum / total;
  if (top3Ratio >= 0.8) {
    return `활성 제약 ${bindingCount}개 — 상위 3개 제약이 ${formatPercent(top3Ratio * 100)} 차지, 소수 제약 지배.`;
  }

  const nonZero = sorted.filter((c) => c.weight_applied !== 0).length;
  return `활성 제약 ${bindingCount}개, 가중치가 ${nonZero}개 제약에 분산 — 균형 배정.`;
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
            <h2 className="text-lg font-semibold">결정 상세</h2>
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
              {/* 1) LLM 자연어 요약 — 최상단, 운영자가 가장 먼저 보는 한 문단. */}
              <section>
                <div className="flex items-center gap-2 mb-2">
                  <h3 className="text-xs font-semibold text-gray-500 uppercase">
                    자연어 요약
                  </h3>
                  {data.llm_was_template && (
                    <span
                      className="text-tiny font-medium px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 border border-gray-200"
                      title="LLM 호출 실패 — 템플릿 기반 요약"
                    >
                      템플릿
                    </span>
                  )}
                </div>
                <p className="text-base text-gray-800 leading-relaxed">
                  {data.llm_summary || "(요약 없음)"}
                </p>
              </section>

              {/* 2) 메타 summary — 가중치 분포 기반 한 줄 판단. 좌측 액센트 바
                  + 회색 배경으로 본문과 분리. (이탤릭 사용 금지 — DESIGN.md) */}
              <section
                className="border-l-2 border-gray-300 bg-gray-50 px-3 py-2"
                aria-label="결정 요약"
              >
                <p className="text-sm text-gray-700">
                  {computeMetaSummary(
                    data.contributions,
                    data.binding_hard_constraints.length,
                  )}
                </p>
              </section>

              {/* 3) 배정 요약 — 설비 · 시간 · solver status */}
              <section>
                <h3 className="text-xs font-semibold text-gray-500 uppercase mb-2">
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
                    <div className="font-medium tabular-nums">
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

              {/* 4) 활성 hard constraints — constraint_id prefix 로 카테고리 grouping.
                  33개를 균등 grid 로 쏟아내던 dump 를 카테고리별 그룹 chip 으로 정리. */}
              <section>
                <h3 className="text-xs font-semibold text-gray-500 uppercase mb-2">
                  활성 제약 ({data.binding_hard_constraints.length}개)
                </h3>
                {data.binding_hard_constraints.length === 0 ? (
                  <p className="text-sm text-gray-400">
                    활성 제약 없음 — 자유 배정 가능
                  </p>
                ) : (
                  <div className="flex flex-col gap-3">
                    {groupByCategory(data.binding_hard_constraints).map(
                      (group) => (
                        <div key={group.prefix}>
                          <div className="text-tiny font-semibold text-gray-500 mb-1.5">
                            #{group.prefix} · {group.label} (
                            {group.items.length})
                          </div>
                          <ul className="flex flex-wrap gap-1.5">
                            {group.items.map((c) => (
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
                        </div>
                      ),
                    )}
                  </div>
                )}
              </section>

              {/* 5) 가중치 기여 — % 변환 표시 + weight=0 항목 expandable 숨김.
                  raw 절대값 (111992) 만 봐서는 비중을 모르므로 sum 대비 % 가 1차,
                  raw 는 보조. */}
              <section>
                <h3 className="text-xs font-semibold text-gray-500 uppercase mb-2">
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
                    const total = sorted.reduce(
                      (acc, c) => acc + Math.abs(c.weight_applied),
                      0,
                    );
                    const nonZero = sorted.filter(
                      (c) => c.weight_applied !== 0,
                    );
                    const zero = sorted.filter((c) => c.weight_applied === 0);

                    if (nonZero.length === 0) {
                      // 전부 0 — meta summary 가 이미 안내하므로 zero list 만 보임.
                      return (
                        <p className="text-sm text-gray-400">
                          모든 항목 가중치 0 — 하드 제약만 작동.
                        </p>
                      );
                    }

                    return (
                      <>
                        <ul className="flex flex-col gap-1.5">
                          {nonZero.map((c) => {
                            const pct =
                              total > 0
                                ? (Math.abs(c.weight_applied) / total) * 100
                                : 0;
                            const widthPct = Math.max(pct, 4);
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
                                  aria-label={`${c.korean_name} ${formatPercent(pct)} (${formatWeight(c.weight_applied)})`}
                                >
                                  <div
                                    className="h-full rounded bg-[color:var(--color-brand-primary)]"
                                    style={{ width: `${widthPct}%` }}
                                  />
                                </div>
                                <span className="w-14 shrink-0 text-right font-mono text-gray-700 tabular-nums">
                                  {formatPercent(pct)}
                                </span>
                                <span className="w-16 shrink-0 text-right font-mono text-gray-400 tabular-nums">
                                  {formatWeight(c.weight_applied)}
                                </span>
                              </li>
                            );
                          })}
                        </ul>
                        {zero.length > 0 && (
                          <details className="mt-3">
                            <summary className="text-tiny text-gray-400 cursor-pointer hover:text-gray-600 select-none">
                              기여 없음 {zero.length}개 보기
                            </summary>
                            <ul className="flex flex-col gap-1 mt-2 pl-2 border-l border-gray-200">
                              {zero.map((c) => (
                                <li
                                  key={c.constraint_id}
                                  className="flex items-center justify-between text-tiny text-gray-400"
                                >
                                  <span className="truncate">
                                    {c.korean_name}
                                  </span>
                                  <span className="font-mono tabular-nums">
                                    0
                                  </span>
                                </li>
                              ))}
                            </ul>
                          </details>
                        )}
                      </>
                    );
                  })()
                )}
              </section>

              {/* 6) 수동 조정 내역 (manual_override 있을 때만) */}
              {data.is_manually_adjusted && data.manual_override && (
                <section className="border-t pt-4">
                  <h3 className="text-xs font-semibold text-orange-600 uppercase mb-2">
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
