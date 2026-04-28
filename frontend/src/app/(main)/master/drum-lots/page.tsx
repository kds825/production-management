"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

interface DrumLotRow {
  drum_id: string | number;
  cross_section: number;
  wire_diameter?: number | null;
  wire_count?: number | null;
  lot_wire_drawing?: number;
  lot_stranding?: number;
  daily_production?: number;
  setup_time_min: number;
  drum_weight_ton: number;
}

export default function DrumLotsPage() {
  const [lots, setLots] = useState<DrumLotRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/master/drum_lot_master`)
      .then((r) => r.json())
      .then((data) => {
        setLots(data.items || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-8 text-gray-400">로딩 중...</div>;

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">틀단위 관리</h1>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-3 py-2 text-right">SQ (mm²)</th>
              <th className="px-3 py-2 text-right">소선경 (mm)</th>
              <th className="px-3 py-2 text-right">소선수</th>
              <th className="px-3 py-2 text-right">신선 틀단위 (m)</th>
              <th className="px-3 py-2 text-right">연선 틀단위 (m)</th>
              <th className="px-3 py-2 text-right">일생산량 (m)</th>
              <th className="px-3 py-2 text-right">셋업 (분)</th>
              <th className="px-3 py-2 text-right">중량 (ton)</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {lots.map((l) => (
              <tr
                key={l.drum_id}
                className={`hover:bg-gray-50 ${!l.wire_count ? "bg-yellow-50" : ""}`}
              >
                <td className="px-3 py-2 text-right font-medium">
                  {l.cross_section}
                </td>
                <td className="px-3 py-2 text-right">
                  {l.wire_diameter || (
                    <span className="text-yellow-500">미확정</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right">
                  {l.wire_count || (
                    <span className="text-yellow-500">미확정</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right">
                  {l.lot_wire_drawing?.toLocaleString()}
                </td>
                <td className="px-3 py-2 text-right">
                  {l.lot_stranding?.toLocaleString()}
                </td>
                <td className="px-3 py-2 text-right">
                  {l.daily_production?.toLocaleString()}
                </td>
                <td className="px-3 py-2 text-right">{l.setup_time_min}</td>
                <td className="px-3 py-2 text-right">{l.drum_weight_ton}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
