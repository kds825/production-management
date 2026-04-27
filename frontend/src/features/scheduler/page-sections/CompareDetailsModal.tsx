/**
 * CompareDetailsModal — 버전 비교 상세 모달.
 *
 * 책임 (Week 7 Task 7B.1 분리):
 *   - store.compareMode.diffResponse 를 입력으로 받아 added/moved/removed/unchanged 카운트와
 *     상위 N 건의 task 목록을 텍스트로 표시.
 *   - "비교 모드" 토글로 활성화된 diff 데이터를 그대로 재사용 (추가 fetch 없음).
 *
 * 가시성 제어는 부모(SchedulerPage) 의 compareOpen 상태로만 결정 — useBatchCompareMode 가 보유.
 */
"use client";

import { useScheduleStore } from "../store/scheduleStore";

interface CompareDetailsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function CompareDetailsModal({
  isOpen,
  onClose,
}: CompareDetailsModalProps) {
  const compareMode = useScheduleStore((s) => s.compareMode);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-xl max-w-3xl w-full max-h-[80vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-3 border-b border-gray-200 flex items-center justify-between">
          <div>
            <h2
              className="text-sm font-semibold"
              style={{ color: "var(--color-text-primary)" }}
            >
              버전 비교
            </h2>
            {compareMode.diffResponse && (
              <div className="text-tiny text-gray-500 mt-0.5">
                {compareMode.diffResponse.run_label_before} →{" "}
                {compareMode.diffResponse.run_label_after}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
          >
            ×
          </button>
        </div>

        <div className="p-5 overflow-y-auto flex-1">
          {compareMode.loading && (
            <div className="text-xs text-gray-500">
              비교 데이터 로드 중...
            </div>
          )}
          {compareMode.error && (
            <div
              className="text-xs p-3 rounded"
              style={{
                backgroundColor: "var(--kbi-red-tint-12)",
                color: "var(--status-danger-text-strong)",
                border: "1px solid var(--kbi-red-tint-20)",
              }}
            >
              {compareMode.error}
            </div>
          )}
          {compareMode.diffResponse && (
            <>
              <div className="grid grid-cols-4 gap-2 mb-4">
                <div
                  className="rounded p-3 text-center"
                  style={{ backgroundColor: "var(--status-success-bg-soft)", color: "var(--status-success-text-deep)" }}
                >
                  <div className="text-tiny font-medium">추가됨</div>
                  <div className="text-2xl font-bold">
                    {compareMode.diffResponse.summary.added}
                  </div>
                </div>
                <div
                  className="rounded p-3 text-center"
                  style={{ backgroundColor: "var(--status-warning-bg)", color: "var(--status-warning-text)" }}
                >
                  <div className="text-tiny font-medium">이동됨</div>
                  <div className="text-2xl font-bold">
                    {compareMode.diffResponse.summary.moved}
                  </div>
                </div>
                <div
                  className="rounded p-3 text-center"
                  style={{
                    backgroundColor: "var(--kbi-red-tint-12)",
                    color: "var(--status-danger-text-strong)",
                  }}
                >
                  <div className="text-tiny font-medium">삭제됨</div>
                  <div className="text-2xl font-bold">
                    {compareMode.diffResponse.summary.removed}
                  </div>
                </div>
                <div
                  className="rounded p-3 text-center"
                  style={{
                    backgroundColor: "var(--neutral-100)",
                    color: "var(--neutral-text-primary)",
                  }}
                >
                  <div className="text-tiny font-medium">변경 없음</div>
                  <div className="text-2xl font-bold">
                    {compareMode.diffResponse.summary.unchanged}
                  </div>
                </div>
              </div>

              {compareMode.diffResponse.added_tasks.length > 0 && (
                <div className="mb-4">
                  <h3 className="text-small font-semibold text-gray-700 mb-2">
                    추가된 배치 ({compareMode.diffResponse.added_tasks.length})
                  </h3>
                  <div className="max-h-40 overflow-y-auto border border-gray-200 rounded">
                    {compareMode.diffResponse.added_tasks
                      .slice(0, 50)
                      .map((t) => (
                        <div
                          key={t.task_id}
                          className="px-2 py-1.5 border-b border-gray-100 text-small flex items-center gap-2"
                        >
                          <span
                            className="inline-block w-1.5 h-1.5 rounded-full"
                            style={{ backgroundColor: "var(--status-success-text)" }}
                          />
                          <span className="font-medium">
                            {t.sales_order_id || "-"}
                          </span>
                          <span className="text-gray-500">
                            {t.process_name}
                          </span>
                          {t.customer_name && (
                            <span className="text-gray-400">
                              · {t.customer_name}
                            </span>
                          )}
                          {t.cross_section != null && (
                            <span className="text-gray-400">
                              · {t.cross_section}SQ
                            </span>
                          )}
                        </div>
                      ))}
                  </div>
                </div>
              )}

              {compareMode.diffResponse.removed_tasks.length > 0 && (
                <div className="mb-4">
                  <h3 className="text-small font-semibold text-gray-700 mb-2">
                    삭제된 배치 ({compareMode.diffResponse.removed_tasks.length}
                    )
                  </h3>
                  <div className="max-h-40 overflow-y-auto border border-gray-200 rounded">
                    {compareMode.diffResponse.removed_tasks
                      .slice(0, 50)
                      .map((t) => (
                        <div
                          key={t.task_id}
                          className="px-2 py-1.5 border-b border-gray-100 text-small flex items-center gap-2"
                        >
                          <span
                            className="inline-block w-1.5 h-1.5 rounded-full"
                            style={{ backgroundColor: "var(--color-danger)" }}
                          />
                          <span className="font-medium">
                            {t.sales_order_id || "-"}
                          </span>
                          <span className="text-gray-500">
                            {t.process_name}
                          </span>
                          {t.customer_name && (
                            <span className="text-gray-400">
                              · {t.customer_name}
                            </span>
                          )}
                        </div>
                      ))}
                  </div>
                </div>
              )}

              {compareMode.diffResponse.moved_tasks.length > 0 && (
                <div className="mb-4">
                  <h3 className="text-small font-semibold text-gray-700 mb-2">
                    이동된 배치 (상위{" "}
                    {Math.min(5, compareMode.diffResponse.moved_tasks.length)}/
                    {compareMode.diffResponse.moved_tasks.length})
                  </h3>
                  <div className="max-h-56 overflow-y-auto border border-gray-200 rounded">
                    {[...compareMode.diffResponse.moved_tasks]
                      .sort(
                        (a, b) =>
                          Math.abs(b.start_delta_hours ?? 0) -
                          Math.abs(a.start_delta_hours ?? 0),
                      )
                      .slice(0, 5)
                      .map((t) => (
                        <div
                          key={t.task_id}
                          className="px-2 py-1.5 border-b border-gray-100 text-small flex items-center gap-2"
                        >
                          <span
                            className="inline-block w-1.5 h-1.5 rounded-full"
                            style={{ backgroundColor: "var(--status-warning)" }}
                          />
                          <span className="font-medium">
                            {t.sales_order_id || "-"}
                          </span>
                          <span className="text-gray-500">
                            {t.process_name}
                          </span>
                          {t.start_delta_hours != null && (
                            <span
                              className="font-mono"
                              style={{
                                color:
                                  t.start_delta_hours > 0
                                    ? "var(--color-danger)"
                                    : "var(--status-success-text)",
                              }}
                            >
                              {t.start_delta_hours > 0 ? "+" : ""}
                              {t.start_delta_hours.toFixed(1)}h
                            </span>
                          )}
                          {t.equipment_changed && (
                            <span className="text-gray-400">· 설비변경</span>
                          )}
                        </div>
                      ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
