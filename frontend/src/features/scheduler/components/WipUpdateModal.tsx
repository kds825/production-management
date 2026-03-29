"use client";
import { useState, useEffect } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

interface WipItem {
  wip_id: number;
  process: string;
  spec: string;
  sq: number | null;
  expected_m: number | null;
  actual_m: number | null;
  total_m: number | null;
  variance_m: number | null;
  status: string;
  matched_order: string | null;
  colors: string | null;
}

interface Props {
  runLabel: string;
  onClose: () => void;
}

export function WipUpdateModal({ runLabel, onClose }: Props) {
  const [items, setItems] = useState<WipItem[]>([]);
  const [edits, setEdits] = useState<Record<number, number>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/audit/wip/summary/${runLabel}`)
      .then((r) => r.json())
      .then((data) => {
        setItems(data.items || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [runLabel]);

  const handleSave = async () => {
    setSaving(true);
    const results = [];
    for (const [wipId, actual] of Object.entries(edits)) {
      const res = await fetch(`${API}/audit/wip/${wipId}/actual`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actual_length_m: actual }),
      });
      const data = await res.json();
      results.push(data);
    }

    const shortages = results.filter((r) => r.shortage > 0);
    if (shortages.length > 0) {
      setResult(
        `${shortages.length}건 부족 감지. 총 부족량: ${shortages.reduce((sum, s) => sum + s.shortage, 0).toFixed(0)}m`,
      );
    } else {
      setResult("모든 SM재고가 업데이트되었습니다.");
    }

    // 저장 후 최신 데이터 재조회
    const refreshRes = await fetch(`${API}/audit/wip/summary/${runLabel}`);
    const refreshData = await refreshRes.json();
    setItems(refreshData.items || []);
    setEdits({});
    setSaving(false);
  };

  const handleCreateShortage = async () => {
    const res = await fetch(`${API}/audit/wip/shortage-batches`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_label: runLabel }),
    });
    const data = await res.json();
    setResult(`부족분 추가 배치 ${data.shortage_batches_created}건 생성됨`);
  };

  // 저장된 실적 기준으로 부족 항목 집계 (edits 반영 포함)
  const shortageItems = items.filter((item) => {
    const editValue = edits[item.wip_id];
    const actual = editValue !== undefined ? editValue : item.actual_m;
    const expected = item.expected_m ?? item.total_m;
    if (actual === null || expected === null)
      return item.variance_m != null && item.variance_m < 0;
    return actual - expected < 0;
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-xl w-[800px] max-h-[80vh] overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between border-b px-6 py-4">
          <div>
            <h2 className="text-lg font-bold">SM재고 실적 업데이트</h2>
            <p className="text-sm text-gray-500">
              예상 수량을 실제 생산 수량으로 업데이트합니다.
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
            aria-label="닫기"
          >
            &times;
          </button>
        </div>

        {/* 테이블 */}
        <div className="overflow-auto max-h-[50vh] p-6">
          {loading ? (
            <p className="text-gray-400 text-sm">로딩 중...</p>
          ) : items.length === 0 ? (
            <p className="text-gray-400 text-sm">
              조회된 SM재고 항목이 없습니다.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-3 py-2 text-left font-medium text-gray-600">
                    공정
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-gray-600">
                    규격
                  </th>
                  <th className="px-3 py-2 text-right font-medium text-gray-600">
                    예상(m)
                  </th>
                  <th className="px-3 py-2 text-right font-medium text-gray-600">
                    실적(m)
                  </th>
                  <th className="px-3 py-2 text-right font-medium text-gray-600">
                    차이(m)
                  </th>
                  <th className="px-3 py-2 text-center font-medium text-gray-600">
                    상태
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-gray-600">
                    매칭 수주
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {items.map((item) => {
                  const editValue = edits[item.wip_id];
                  const displayActual =
                    editValue !== undefined ? editValue : item.actual_m;
                  const expected = item.expected_m ?? item.total_m;
                  const variance =
                    displayActual !== null &&
                    displayActual !== undefined &&
                    expected !== null
                      ? (displayActual as number) - (expected as number)
                      : item.variance_m;

                  const isShortage = variance !== null && variance < 0;

                  return (
                    <tr
                      key={item.wip_id}
                      className={isShortage ? "bg-red-50" : ""}
                    >
                      <td className="px-3 py-2 text-gray-700">
                        {item.process}
                      </td>
                      <td className="px-3 py-2 text-gray-700">
                        {item.spec}
                        {item.colors ? ` (${item.colors})` : ""}
                      </td>
                      <td className="px-3 py-2 text-right text-gray-700">
                        {(item.expected_m ?? item.total_m)?.toLocaleString() ||
                          "-"}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {item.status === "예상" ? (
                          <input
                            type="number"
                            value={editValue ?? item.actual_m ?? ""}
                            onChange={(e) =>
                              setEdits({
                                ...edits,
                                [item.wip_id]: Number(e.target.value),
                              })
                            }
                            className="w-24 rounded border border-gray-300 px-2 py-1 text-right text-sm focus:border-blue-500 focus:outline-none"
                            placeholder="실적 입력"
                          />
                        ) : (
                          <span className="text-gray-700">
                            {item.actual_m?.toLocaleString() || "-"}
                          </span>
                        )}
                      </td>
                      <td
                        className={`px-3 py-2 text-right font-medium ${
                          isShortage
                            ? "text-red-600"
                            : variance && variance > 0
                              ? "text-green-600"
                              : "text-gray-500"
                        }`}
                      >
                        {variance !== null
                          ? `${variance > 0 ? "+" : ""}${variance.toLocaleString()}`
                          : "-"}
                      </td>
                      <td className="px-3 py-2 text-center">
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                            item.status === "실적"
                              ? "bg-green-100 text-green-700"
                              : item.status === "예상"
                                ? "bg-yellow-100 text-yellow-700"
                                : "bg-gray-100 text-gray-600"
                          }`}
                        >
                          {item.status}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-500">
                        {item.matched_order || "-"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* 푸터 */}
        <div className="border-t px-6 py-4 flex items-center justify-between gap-4">
          <div className="flex flex-col gap-1.5">
            {result && <p className="text-sm text-blue-600">{result}</p>}
            {shortageItems.length > 0 && (
              <button
                onClick={handleCreateShortage}
                className="rounded bg-red-600 px-4 py-1.5 text-sm text-white hover:bg-red-700 transition-colors"
              >
                부족분 추가 배치 생성 ({shortageItems.length}건)
              </button>
            )}
          </div>
          <div className="flex gap-2 shrink-0">
            <button
              onClick={onClose}
              className="rounded border border-gray-300 px-4 py-1.5 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
            >
              닫기
            </button>
            <button
              onClick={handleSave}
              disabled={saving || Object.keys(edits).length === 0}
              className="rounded bg-blue-600 px-4 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {saving ? "저장 중..." : "실적 업데이트"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
