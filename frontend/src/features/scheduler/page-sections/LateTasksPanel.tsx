/**
 * LateTasksPanel — 납기 초과 배치 목록 패널.
 *
 * 책임 (Week 7 Task 7B.1 분리):
 *   - 헤더 (총 건수 + 닫기) + lateTasks 테이블 렌더.
 *   - 행 클릭 시 store.selectTask(t.id) — 간트 블록 선택 + 패널 자동 닫힘.
 *
 * 입력은 useSchedulerData() 의 lateTasks 를 그대로 받는다 — 정렬/필터는 hook 책임.
 */
"use client";

import { useScheduleStore } from "../store/scheduleStore";
import type { LateTaskEntry } from "../hooks/useSchedulerData";

interface LateTasksPanelProps {
  lateTasks: LateTaskEntry[];
  onClose: () => void;
}

export function LateTasksPanel({ lateTasks, onClose }: LateTasksPanelProps) {
  return (
    <div
      className="shrink-0 border-b overflow-auto"
      style={{
        maxHeight: 220,
        backgroundColor: "var(--kbi-red-tint-5)",
        borderColor: "var(--kbi-red-tint-20)",
      }}
    >
      <div
        className="flex items-center justify-between px-4 py-2 sticky top-0 border-b"
        style={{
          backgroundColor: "var(--kbi-red-tint-5)",
          borderColor: "var(--kbi-red-tint-20)",
        }}
      >
        <div className="flex items-center gap-2">
          <span className="text-small font-bold" style={{ color: "var(--status-danger-text-strong)" }}>
            납기 초과 배치
          </span>
          <span
            className="px-1.5 py-0.5 rounded-full text-tiny font-bold text-white"
            style={{ backgroundColor: "var(--color-danger)" }}
          >
            {lateTasks.length}건
          </span>
          <span className="text-tiny text-gray-400">
            — 배치 종료 시각이 납기일을 초과한 수주 목록
          </span>
        </div>
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-gray-600 text-xs"
        >
          ✕
        </button>
      </div>
      {lateTasks.length === 0 ? (
        <div className="px-4 py-3 text-small text-gray-400">
          납기 초과 배치가 없습니다.
        </div>
      ) : (
        <table className="w-full text-small border-collapse">
          <thead>
            <tr style={{ backgroundColor: "var(--kbi-red-tint-12)" }}>
              {[
                "지연",
                "상태",
                "설비",
                "규격",
                "거래처",
                "납기일",
                "배치완료",
                "배치그룹",
              ].map((h) => (
                <th
                  key={h}
                  className="px-3 py-1.5 text-left font-semibold border-b"
                  style={{
                    color: "var(--status-danger-text-deep)",
                    borderColor: "var(--kbi-red-tint-20)",
                    whiteSpace: "nowrap",
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {lateTasks.map(({ task: t, lateDays }) => (
              <tr
                key={t.id}
                className="hover:bg-red-50 cursor-pointer border-b"
                style={{ borderColor: "var(--kbi-red-tint-12)" }}
                onClick={() => {
                  // 해당 배치 블록 선택 및 스크롤
                  const store = useScheduleStore.getState();
                  store.selectTask(t.id);
                  onClose();
                }}
              >
                <td
                  className="px-3 py-1.5 font-bold"
                  style={{ color: "var(--color-danger)", whiteSpace: "nowrap" }}
                >
                  +{lateDays}일
                </td>
                <td className="px-3 py-1.5" style={{ whiteSpace: "nowrap" }}>
                  {t.status === "unassigned" ? (
                    <span
                      className="px-1.5 py-0.5 rounded text-tiny font-bold text-white"
                      style={{ backgroundColor: "var(--color-text-tertiary)" }}
                    >
                      미배치
                    </span>
                  ) : (
                    <span className="text-gray-400 text-tiny">배치됨</span>
                  )}
                </td>
                <td
                  className="px-3 py-1.5 text-gray-600"
                  style={{ whiteSpace: "nowrap" }}
                >
                  {t.equipment_id}
                </td>
                <td className="px-3 py-1.5 text-gray-700 max-w-[160px] truncate">
                  {t.spec || t.product}
                </td>
                <td
                  className="px-3 py-1.5 text-gray-600"
                  style={{ whiteSpace: "nowrap" }}
                >
                  {t.customer ?? "-"}
                </td>
                <td
                  className="px-3 py-1.5 font-medium"
                  style={{ color: "var(--status-danger-text)", whiteSpace: "nowrap" }}
                >
                  {t.delivery_date instanceof Date
                    ? t.delivery_date.toLocaleDateString("ko-KR")
                    : new Date(t.delivery_date!).toLocaleDateString("ko-KR")}
                </td>
                <td
                  className="px-3 py-1.5 text-gray-500"
                  style={{ whiteSpace: "nowrap" }}
                >
                  {(t.end instanceof Date
                    ? t.end
                    : new Date(t.end)
                  ).toLocaleDateString("ko-KR")}
                </td>
                <td
                  className="px-3 py-1.5 font-mono text-gray-400 text-tiny"
                  style={{ whiteSpace: "nowrap" }}
                >
                  {t.batch_group ?? "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
