"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

interface EquipmentRow {
  equipment_code: string;
  equipment_name: string;
  process_name: string;
  material_limit?: string | null;
  range_min?: number | null;
  range_max?: number | null;
  range_unit?: string;
  color_group?: string | null;
  shift_type: string;
}

export default function EquipmentPage() {
  const [equipment, setEquipment] = useState<EquipmentRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/master/equipment_master`)
      .then((r) => r.json())
      .then((data) => {
        setEquipment(data.items || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-8 text-gray-400">로딩 중...</div>;

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">설비 관리</h1>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-3 py-2 text-left">설비코드</th>
              <th className="px-3 py-2 text-left">설비명</th>
              <th className="px-3 py-2 text-left">공정</th>
              <th className="px-3 py-2 text-left">재질</th>
              <th className="px-3 py-2 text-right">범위(min)</th>
              <th className="px-3 py-2 text-right">범위(max)</th>
              <th className="px-3 py-2 text-left">단위</th>
              <th className="px-3 py-2 text-left">색상그룹</th>
              <th className="px-3 py-2 text-left">교대</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {equipment.map((eq) => (
              <tr key={eq.equipment_code} className="hover:bg-gray-50">
                <td className="px-3 py-2 font-mono text-xs">
                  {eq.equipment_code}
                </td>
                <td className="px-3 py-2 font-medium">{eq.equipment_name}</td>
                <td className="px-3 py-2">{eq.process_name}</td>
                <td className="px-3 py-2">{eq.material_limit || "ALL"}</td>
                <td className="px-3 py-2 text-right">{eq.range_min || "-"}</td>
                <td className="px-3 py-2 text-right">{eq.range_max || "-"}</td>
                <td className="px-3 py-2">{eq.range_unit}</td>
                <td className="px-3 py-2">{eq.color_group || "-"}</td>
                <td className="px-3 py-2">{eq.shift_type}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
