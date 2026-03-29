"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

interface WipRecord {
  wip_id: number;
  process_stage: string;
  voltage_class: string;
  material: string;
  product_name: string | null;
  spec: string;
  cross_section: number | null;
  length_m: number | null;
  count: number | null;
  total_length_m: number | null;
  core_colors: string | null;
  wire_diameter: number | null;
  wire_count: number | null;
  status: string;
}

const EMPTY_RECORD: Omit<WipRecord, "wip_id"> = {
  process_stage: "연선재고",
  voltage_class: "저압",
  material: "CU",
  product_name: null,
  spec: "",
  cross_section: null,
  length_m: null,
  count: 1,
  total_length_m: null,
  core_colors: null,
  wire_diameter: null,
  wire_count: null,
  status: "예상",
};

const PROCESS_OPTIONS = [
  "연선재고",
  "절연재고",
  "연합재고",
  "완제품",
  "연선 저압",
  "연선 고압",
  "절연 저압 TFR-CV(WB)",
  "절연 저압 TFR-8",
  "절연 고압 URD",
  "연합 저압 TFR-CV(WB)",
  "연합 저압 TFR-8",
];

export default function WipPage() {
  const [records, setRecords] = useState<WipRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(EMPTY_RECORD);
  const [saving, setSaving] = useState(false);

  const loadData = () => {
    fetch(`${API}/master/wip_inventory`)
      .then((r) => r.json())
      .then((data) => {
        setRecords(data.items || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(loadData, []);

  const handleSubmit = async () => {
    setSaving(true);
    const total = (form.length_m || 0) * (form.count || 1);
    const body = {
      ...form,
      total_length_m: total,
      expected_length_m: total,
    };

    await fetch(`${API}/master/wip_inventory`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).catch(() => {});

    setForm(EMPTY_RECORD);
    setShowForm(false);
    setSaving(false);
    loadData();
  };

  if (loading) return <div className="p-8 text-gray-400">로딩 중...</div>;

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">재공(SM) 재고 관리</h1>
          <p className="text-sm text-gray-500">
            연선재고, 절연재고 등 반제품 재고를 직접 입력/수정합니다.
          </p>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
        >
          + 재공 추가
        </button>
      </div>

      {/* Add form */}
      {showForm && (
        <div className="mb-4 rounded-lg border bg-blue-50 p-4">
          <h3 className="mb-3 text-sm font-bold">새 재공 재고 등록</h3>
          <div className="grid grid-cols-4 gap-3">
            <div>
              <label className="text-xs text-gray-500">공정</label>
              <select
                value={form.process_stage}
                onChange={(e) =>
                  setForm({ ...form, process_stage: e.target.value })
                }
                className="w-full rounded border px-2 py-1.5 text-sm"
              >
                {PROCESS_OPTIONS.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs text-gray-500">전압구분</label>
              <select
                value={form.voltage_class}
                onChange={(e) =>
                  setForm({ ...form, voltage_class: e.target.value })
                }
                className="w-full rounded border px-2 py-1.5 text-sm"
              >
                <option value="저압">저압</option>
                <option value="고압">고압</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-gray-500">재질</label>
              <select
                value={form.material}
                onChange={(e) => setForm({ ...form, material: e.target.value })}
                className="w-full rounded border px-2 py-1.5 text-sm"
              >
                <option value="CU">CU</option>
                <option value="AL">AL</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-gray-500">규격</label>
              <input
                type="text"
                value={form.spec}
                onChange={(e) => setForm({ ...form, spec: e.target.value })}
                className="w-full rounded border px-2 py-1.5 text-sm"
                placeholder="200SQ, 1250kcmil"
              />
            </div>
            <div>
              <label className="text-xs text-gray-500">길이(M)</label>
              <input
                type="number"
                value={form.length_m || ""}
                onChange={(e) =>
                  setForm({ ...form, length_m: Number(e.target.value) || null })
                }
                className="w-full rounded border px-2 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="text-xs text-gray-500">개수</label>
              <input
                type="number"
                value={form.count || ""}
                onChange={(e) =>
                  setForm({ ...form, count: Number(e.target.value) || 1 })
                }
                className="w-full rounded border px-2 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="text-xs text-gray-500">선심색상</label>
              <input
                type="text"
                value={form.core_colors || ""}
                onChange={(e) =>
                  setForm({ ...form, core_colors: e.target.value || null })
                }
                className="w-full rounded border px-2 py-1.5 text-sm"
                placeholder="흑/갈/회/청"
              />
            </div>
            <div>
              <label className="text-xs text-gray-500">상태</label>
              <select
                value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value })}
                className="w-full rounded border px-2 py-1.5 text-sm"
              >
                <option value="예상">예상 (생산 중)</option>
                <option value="실적">실적 (확정)</option>
              </select>
            </div>
          </div>
          <div className="mt-3 flex justify-end gap-2">
            <button
              onClick={() => setShowForm(false)}
              className="rounded border px-3 py-1.5 text-sm"
            >
              취소
            </button>
            <button
              onClick={handleSubmit}
              disabled={saving || !form.spec}
              className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
            >
              {saving ? "저장 중..." : "등록"}
            </button>
          </div>
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-3 py-2 text-left">공정</th>
              <th className="px-3 py-2 text-left">전압</th>
              <th className="px-3 py-2 text-left">재질</th>
              <th className="px-3 py-2 text-left">규격</th>
              <th className="px-3 py-2 text-right">길이(M)</th>
              <th className="px-3 py-2 text-right">개수</th>
              <th className="px-3 py-2 text-right">총량(M)</th>
              <th className="px-3 py-2 text-left">색상</th>
              <th className="px-3 py-2 text-center">상태</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {records.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-3 py-8 text-center text-gray-400">
                  등록된 재공 재고가 없습니다. 위 버튼으로 추가하거나 파일을
                  업로드하세요.
                </td>
              </tr>
            ) : (
              records.map((r) => (
                <tr key={r.wip_id} className="hover:bg-gray-50">
                  <td className="px-3 py-2">{r.process_stage}</td>
                  <td className="px-3 py-2">{r.voltage_class}</td>
                  <td className="px-3 py-2">{r.material}</td>
                  <td className="px-3 py-2 font-medium">{r.spec}</td>
                  <td className="px-3 py-2 text-right">
                    {r.length_m?.toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right">{r.count}</td>
                  <td className="px-3 py-2 text-right font-medium">
                    {r.total_length_m?.toLocaleString()}
                  </td>
                  <td className="px-3 py-2">{r.core_colors || "-"}</td>
                  <td className="px-3 py-2 text-center">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs ${
                        r.status === "실적"
                          ? "bg-green-100 text-green-700"
                          : r.status === "예상"
                            ? "bg-yellow-100 text-yellow-700"
                            : "bg-gray-100 text-gray-600"
                      }`}
                    >
                      {r.status}
                    </span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
