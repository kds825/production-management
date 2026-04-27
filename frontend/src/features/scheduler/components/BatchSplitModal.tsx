"use client";

import { useState, useEffect, useCallback } from "react";
import { useScheduleStore } from "../store/scheduleStore";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

/** batch_group 내 개별 수주 항목 (백엔드 GET /pipeline/batch-group/{bg}/orders 응답) */
interface BatchOrderItem {
  batch_id: number;
  sales_order_id: string;
  spec_raw: string;
  sheath_color: string;
  customer_name: string;
  due_date: string;
  drum_length_m: number;
  drum_count: number;
  total_length_m: number;
  wip_matched_id: number | null;
  product_group: string;
  status: string;
}

/**
 * 배치 분할 모달 -- 간트 블록(= 1 batch_group)을 2개로 나누는 UI.
 *
 * 1. batch_group의 모든 수주를 조회하여 체크박스 테이블로 표시
 * 2. 분리할 수주를 선택하고 접미사를 입력
 * 3. "분할" 클릭 시 POST /pipeline/batch-group/{bg}/split 호출
 * 4. 성공 시 태스크 목록 재로드
 */
export function BatchSplitModal() {
  const splitModal = useScheduleStore((s) => s.splitModal);
  const closeSplitModal = useScheduleStore((s) => s.closeSplitModal);
  const deleteTask = useScheduleStore((s) => s.deleteTask);

  const { isOpen, batchGroup, taskId } = splitModal;

  const [orders, setOrders] = useState<BatchOrderItem[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [suffix, setSuffix] = useState("B");
  const [loading, setLoading] = useState(false);
  const [splitting, setSplitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  // 모달 열릴 때 수주 목록 조회
  useEffect(() => {
    if (!isOpen || !batchGroup) return;

    setLoading(true);
    setError(null);
    setResult(null);
    setSelectedIds(new Set());
    setSuffix("B");

    fetch(
      `${API}/pipeline/batch-group/${encodeURIComponent(batchGroup)}/orders`,
    )
      .then(async (res) => {
        if (!res.ok) {
          const text = await res.text();
          throw new Error(`${res.status}: ${text.slice(0, 100)}`);
        }
        return res.json();
      })
      .then((data: BatchOrderItem[]) => {
        setOrders(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "수주 목록 조회 실패");
        setLoading(false);
      });
  }, [isOpen, batchGroup]);

  const toggleSelect = useCallback((batchId: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(batchId)) {
        next.delete(batchId);
      } else {
        next.add(batchId);
      }
      return next;
    });
  }, []);

  const toggleAll = useCallback(() => {
    if (selectedIds.size === orders.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(orders.map((o) => o.batch_id)));
    }
  }, [selectedIds.size, orders]);

  const handleSplit = useCallback(async () => {
    if (selectedIds.size === 0) return;
    // 전체 선택은 분할 불가 -- 최소 1개는 원래 그룹에 남아야 함
    if (selectedIds.size === orders.length) {
      setError("전체를 이동하면 분할이 되지 않습니다. 일부만 선택하세요.");
      return;
    }

    setSplitting(true);
    setError(null);
    setResult(null);

    try {
      const res = await fetch(
        `${API}/pipeline/batch-group/${encodeURIComponent(batchGroup)}/split`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            batch_ids: Array.from(selectedIds),
            new_group_suffix: suffix || "B",
          }),
        },
      );

      if (!res.ok) {
        const text = await res.text();
        throw new Error(`${res.status}: ${text.slice(0, 120)}`);
      }

      const data = await res.json();
      const remainingPlanned: number = data.original_remaining_planned ?? -1;

      // 분할 후 자동으로 Stage 2 재실행 — schedule_task가 삭제되었으므로 재생성 필요
      let stage2Ok = false;
      try {
        // 최신 run_label 조회
        const runsRes = await fetch(`${API}/pipeline/runs`);
        if (runsRes.ok) {
          const runs: Array<{ run_label: string }> = await runsRes.json();
          if (runs.length > 0) {
            const runLabel = runs[0].run_label;
            const s2Res = await fetch(`${API}/pipeline/stage2`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ run_label: runLabel }),
            });
            stage2Ok = s2Res.ok;
          }
        }
      } catch {
        // Stage 2 실패해도 새로고침은 진행
      }

      // 결과 메시지: 원본 그룹의 잔여 작업량에 따라 분기
      const newGroupMsg = `"${data.new_group}" 그룹 생성`;
      let resultMsg: string;
      if (remainingPlanned === 0) {
        resultMsg = `분할 완료: ${newGroupMsg}. 원본 그룹의 남은 수주는 WIP 재고로 전량 충당되어 간트에서 표시되지 않습니다.`;
      } else {
        resultMsg = `분할 완료: ${newGroupMsg}. ${stage2Ok ? "자동배열 완료." : "자동배열 재실행 필요."}`;
      }
      setResult(resultMsg);

      // 프론트 간트에서 해당 태스크 제거
      if (taskId) {
        deleteTask(taskId);
      }

      // 모달 닫기 + 페이지 새로고침 (WIP 충당 안내 메시지는 더 오래 표시)
      setTimeout(
        () => {
          closeSplitModal();
          window.location.reload();
        },
        remainingPlanned === 0 ? 3000 : 1500,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "분할 실패");
    } finally {
      setSplitting(false);
    }
  }, [
    selectedIds,
    orders.length,
    batchGroup,
    suffix,
    taskId,
    deleteTask,
    closeSplitModal,
  ]);

  if (!isOpen) return null;

  const selectedTotal = orders
    .filter((o) => selectedIds.has(o.batch_id))
    .reduce((sum, o) => sum + o.total_length_m, 0);

  const remainTotal = orders
    .filter((o) => !selectedIds.has(o.batch_id))
    .reduce((sum, o) => sum + o.total_length_m, 0);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={closeSplitModal}
    >
      <div
        className="bg-white rounded-lg shadow-xl w-[720px] max-h-[80vh] overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 헤더 */}
        <div
          className="flex items-center justify-between border-b px-6 py-4"
          style={{ borderColor: "var(--color-border-default)" }}
        >
          <div>
            <h2
              className="text-lg font-bold"
              style={{ color: "var(--color-text-primary)" }}
            >
              배치 분할
            </h2>
            <p className="text-sm text-gray-500 mt-0.5">
              <span
                className="font-mono px-1.5 py-0.5 rounded text-xs"
                style={{
                  backgroundColor: "var(--kbi-red-tint-5)",
                  color: "var(--color-brand-primary)",
                }}
              >
                {batchGroup}
              </span>{" "}
              그룹을 2개로 나눕니다. 이동할 수주를 선택하세요.
            </p>
          </div>
          <button
            onClick={closeSplitModal}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
            aria-label="닫기"
          >
            &times;
          </button>
        </div>

        {/* 테이블 */}
        <div className="overflow-auto max-h-[45vh] px-6 py-4">
          {loading ? (
            <div className="flex items-center gap-2 text-gray-400 text-sm py-8 justify-center">
              <span
                className="inline-block w-4 h-4 border-2 border-gray-300 border-t-transparent rounded-full"
                style={{ animation: "spin 1s linear infinite" }}
              />
              수주 목록 로딩 중...
            </div>
          ) : orders.length === 0 ? (
            <p className="text-gray-400 text-sm text-center py-8">
              해당 그룹에 수주 항목이 없습니다.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-gray-50 sticky top-0">
                <tr>
                  <th className="px-3 py-2 text-left w-8">
                    <input
                      type="checkbox"
                      checked={
                        selectedIds.size === orders.length && orders.length > 0
                      }
                      onChange={toggleAll}
                      className="rounded border-gray-300"
                      style={{ accentColor: "var(--color-brand-primary)" }}
                    />
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-gray-600">
                    수주번호
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-gray-600">
                    규격
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-gray-600">
                    색상
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-gray-600">
                    거래처
                  </th>
                  <th className="px-3 py-2 text-right font-medium text-gray-600">
                    길이(m)
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-gray-600">
                    납기
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {orders.map((order) => {
                  const isChecked = selectedIds.has(order.batch_id);
                  return (
                    <tr
                      key={order.batch_id}
                      className={`cursor-pointer transition-colors ${
                        isChecked ? "bg-red-50" : "hover:bg-gray-50"
                      }`}
                      onClick={() => toggleSelect(order.batch_id)}
                    >
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => toggleSelect(order.batch_id)}
                          className="rounded border-gray-300"
                          style={{ accentColor: "var(--color-brand-primary)" }}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-700">
                        {order.sales_order_id || "-"}
                      </td>
                      <td className="px-3 py-2 text-gray-700">
                        {order.spec_raw}
                      </td>
                      <td className="px-3 py-2 text-gray-700">
                        {order.sheath_color || "-"}
                      </td>
                      <td className="px-3 py-2 text-gray-700">
                        {order.customer_name || "-"}
                      </td>
                      <td className="px-3 py-2 text-right text-gray-700">
                        {order.total_length_m.toLocaleString()}
                      </td>
                      <td className="px-3 py-2 text-gray-500 text-xs">
                        {order.due_date || "-"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* 분할 요약 + 접미사 입력 */}
        {orders.length > 0 && (
          <div
            className="px-6 py-3 border-t flex items-center gap-6"
            style={{
              borderColor: "var(--color-border-default)",
              backgroundColor: "var(--color-bg-muted)",
            }}
          >
            <div className="flex items-center gap-4 text-xs">
              <div>
                <span className="text-gray-500">원래 그룹:</span>{" "}
                <span className="font-semibold text-gray-700">
                  {remainTotal.toLocaleString()}m
                </span>
                <span className="text-gray-400 ml-1">
                  ({orders.length - selectedIds.size}건)
                </span>
              </div>
              <span className="text-gray-300">|</span>
              <div>
                <span style={{ color: "var(--color-brand-primary)" }}>
                  새 그룹:
                </span>{" "}
                <span
                  className="font-semibold"
                  style={{ color: "var(--color-brand-primary)" }}
                >
                  {selectedTotal.toLocaleString()}m
                </span>
                <span className="text-gray-400 ml-1">
                  ({selectedIds.size}건)
                </span>
              </div>
            </div>
            <div className="flex items-center gap-2 ml-auto">
              <label className="text-xs text-gray-500">접미사:</label>
              <input
                type="text"
                value={suffix}
                onChange={(e) => setSuffix(e.target.value)}
                className="w-16 rounded border border-gray-300 px-2 py-1 text-xs text-center focus:border-red-400 focus:outline-none"
                placeholder="B"
                maxLength={5}
              />
            </div>
          </div>
        )}

        {/* 에러/결과 메시지 */}
        {(error || result) && (
          <div
            className="px-6 py-2 text-xs border-t"
            style={{
              borderColor: "var(--color-border-default)",
              backgroundColor: error ? "var(--kbi-red-tint-5)" : "var(--status-success-bg-soft)",
              color: error ? "var(--status-danger-text)" : "var(--status-success-text)",
            }}
          >
            {error || result}
          </div>
        )}

        {/* 푸터 */}
        <div
          className="border-t px-6 py-4 flex items-center justify-end gap-2"
          style={{ borderColor: "var(--color-border-default)" }}
        >
          <button
            onClick={closeSplitModal}
            className="rounded border border-gray-300 px-4 py-1.5 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
          >
            취소
          </button>
          <button
            onClick={handleSplit}
            disabled={splitting || selectedIds.size === 0 || !!result}
            className="rounded px-4 py-1.5 text-sm text-white font-medium transition-colors disabled:opacity-50"
            style={{ backgroundColor: "var(--color-brand-primary)" }}
          >
            {splitting ? "분할 중..." : `분할 (${selectedIds.size}건 이동)`}
          </button>
        </div>
      </div>
    </div>
  );
}
