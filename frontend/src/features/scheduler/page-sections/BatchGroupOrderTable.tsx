/**
 * BatchGroupOrderTable — 선택된 배치 그룹의 수주 목록 테이블.
 *
 * 책임 (Week 7 Task 7B.1 분리):
 *   - batch_seq == -1 → 연선 그룹 헤더(틀단위 집계): 총 생산지시 틀 수 표시.
 *   - batch_seq == 0  → CORE 배치(61연선 코어): 수주 1건씩, 헤더 없음.
 *   - batch_seq == 1  → 개별 수주 배치: 수주 1건씩 행으로 표시.
 *   - batch_seq == null/undefined → 헤더 없는 그룹(절연·시스 등): 모두 표시.
 *
 * WIP 처리: net_length_m 가 있으면 작업지시량 = net (= total - wip 차감) 으로 합계 계산.
 */
"use client";

import { calcConvertedQty } from "@/shared/utils/batchGrouping";

export interface BatchGroupOrder {
  batch_id: number;
  /** -1: 그룹 헤더(틀단위 집계), 0: 61연선 코어, 1+: 개별 수주 배치 */
  batch_seq: number;
  sales_order_id: string;
  spec_raw: string;
  sheath_color: string;
  customer_name: string;
  due_date: string;
  drum_length_m: number;
  drum_count: number;
  total_length_m: number;
  /** WIP 재고 커버량 (m) — WIP 사용 수주에만 존재 */
  wip_length_m?: number;
  /** 실제 작업지시량 (m) = total_length_m - wip_length_m */
  net_length_m?: number;
  wip_matched_id: number | null;
  product_group: string;
  status: string;
  core_count: number;
}

export function BatchGroupOrderTable({
  orders,
}: {
  orders: BatchGroupOrder[];
}) {
  const headerBatch = orders.find((o) => o.batch_seq === -1);
  // 개별 수주 행: batch_seq >= 0(CORE 포함) 이거나 batch_seq 없는 경우(비연선 그룹)
  const orderRows = orders.filter(
    (o) => o.batch_seq == null || o.batch_seq >= 0,
  );
  // 실제 표시 행: 개별 수주 행이 있으면 그것만, 없으면 전체(폴백)
  const displayRows = orderRows.length > 0 ? orderRows : orders;

  const totalLots = headerBatch
    ? headerBatch.drum_count
    : displayRows.reduce((s, o) => s + o.drum_count, 0);
  // 작업지시량 합계: WIP 재고 사용분 제외 (net_length_m 우선, 없으면 total_length_m)
  const totalQty = displayRows.reduce(
    (s, o) => s + (o.net_length_m ?? o.total_length_m),
    0,
  );
  const wipCount = displayRows.filter((o) => o.wip_matched_id).length;

  return (
    <div className="overflow-x-auto">
      <table className="text-small whitespace-nowrap">
        <thead>
          <tr
            style={{
              backgroundColor: "var(--bg-surface-alt)",
              borderBottom: "1px solid var(--color-border-default)",
            }}
          >
            {(
              [
                ["수주번호", "left"],
                ["거래처", "left"],
                ["품명", "left"],
                ["규격", "left"],
                ["색상", "left"],
                ["납기", "left"],
                ["드럼", "right"],
                ["총 길이", "right"],
                ["환산수량", "right"],
                ["WIP", "center"],
              ] as [string, string][]
            ).map(([label, align]) => (
              <th
                key={label}
                className={`text-${align} py-1 px-2 font-semibold text-gray-500 text-tiny uppercase tracking-wider`}
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {displayRows.map((order) => (
            <tr
              key={order.batch_id}
              className="hover:bg-gray-50 transition-colors"
              style={{ borderBottom: "1px solid var(--neutral-100)" }}
            >
              <td className="py-1 px-2 font-mono text-gray-700">
                {order.sales_order_id || "-"}
              </td>
              <td className="py-1 px-2 text-gray-700">
                {order.customer_name || "-"}
              </td>
              <td className="py-1 px-2 text-gray-600">
                {order.product_group || "-"}
              </td>
              <td className="py-1 px-2 text-gray-600">{order.spec_raw}</td>
              <td className="py-1 px-2 text-gray-600">
                {order.sheath_color || "-"}
              </td>
              <td
                className="py-1 px-2"
                style={{
                  color:
                    order.due_date &&
                    new Date(order.due_date).getTime() < Date.now()
                      ? "var(--color-danger)"
                      : "var(--neutral-600)",
                }}
              >
                {order.due_date
                  ? new Date(order.due_date).toLocaleDateString("ko-KR")
                  : "-"}
              </td>
              <td className="py-1 px-2 text-right text-gray-600">
                {order.drum_count} x {order.drum_length_m.toLocaleString()}m
              </td>
              <td className="py-1 px-2 text-right font-medium text-gray-700">
                {order.wip_length_m != null && order.wip_length_m > 0 ? (
                  <span
                    title={`원본: ${order.total_length_m.toLocaleString()}m, WIP차감: -${order.wip_length_m.toLocaleString()}m`}
                  >
                    <span style={{ color: "var(--status-success)" }}>
                      {(
                        order.net_length_m ?? order.total_length_m
                      ).toLocaleString()}
                      m
                    </span>
                    <span className="ml-1 text-mini text-gray-400">
                      (-{order.wip_length_m.toLocaleString()})
                    </span>
                  </span>
                ) : (
                  (
                    order.net_length_m ?? order.total_length_m
                  ).toLocaleString() + "m"
                )}
              </td>
              <td className="py-1 px-2 text-right text-gray-600">
                {(() => {
                  const base = order.net_length_m ?? order.total_length_m;
                  const conv = calcConvertedQty(order.spec_raw || "", base);
                  return (
                    <span
                      title={`${order.core_count}C × ${base.toLocaleString()}m`}
                    >
                      {conv.toLocaleString()}m
                    </span>
                  );
                })()}
              </td>
              <td className="py-1 px-2 text-center">
                {order.wip_matched_id ? (
                  <span
                    className="inline-block px-1.5 py-0.5 rounded text-mini font-medium"
                    style={{
                      backgroundColor: "var(--status-success-bg)",
                      color: "var(--status-success)",
                    }}
                  >
                    재고
                  </span>
                ) : (
                  <span className="text-gray-300">-</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr
            style={{
              borderTop: "2px solid var(--color-border-default)",
              backgroundColor: "var(--kbi-red-tint-5)",
            }}
          >
            <td
              colSpan={6}
              className="py-1 px-2 font-semibold"
              style={{ color: "var(--color-brand-primary)" }}
            >
              합계 {displayRows.length}건
              {headerBatch && (
                <span className="ml-2 text-tiny font-normal text-gray-500">
                  (생산지시 {totalLots}틀 /{" "}
                  {headerBatch.drum_length_m.toLocaleString()}m×{totalLots})
                </span>
              )}
            </td>
            <td className="py-1 px-2 text-right font-medium text-gray-500">
              {totalLots}틀
            </td>
            <td
              className="py-1 px-2 text-right font-semibold"
              style={{ color: "var(--color-brand-primary)" }}
              title="WIP 재고 사용량 제외한 실제 작업지시량"
            >
              {totalQty.toLocaleString()}m
            </td>
            <td
              className="py-1 px-2 text-right font-semibold"
              style={{ color: "var(--color-brand-primary)" }}
              title="다심 케이블 환산수량 합계 (단심은 —)"
            >
              {displayRows
                .reduce((s, o) => {
                  const base = o.net_length_m ?? o.total_length_m;
                  return s + calcConvertedQty(o.spec_raw || "", base);
                }, 0)
                .toLocaleString()}
              m
            </td>
            <td className="py-1 px-2 text-center text-tiny text-gray-400">
              {wipCount > 0 ? `재고 ${wipCount}건` : "-"}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
