"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

interface ItemRow {
  item_code: string;
  product_group: string;
  voltage: string;
  conductor_material: string;
  cross_section: number;
  routing_code: string;
  is_outsourced: boolean;
}

export default function ItemsPage() {
  const [items, setItems] = useState<ItemRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/master/item_master`)
      .then((r) => r.json())
      .then((data) => {
        setItems(data.items || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-8 text-gray-400">로딩 중...</div>;

  if (items.length === 0) {
    return (
      <div>
        <h1 className="mb-4 text-xl font-bold">품목 관리</h1>
        <div className="rounded-lg border border-dashed p-8 text-center text-gray-400">
          등록된 품목이 없습니다. ERP 데이터 업로드 후 자동 생성됩니다.
        </div>
      </div>
    );
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">품목 관리</h1>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-3 py-2 text-left">품목코드</th>
              <th className="px-3 py-2 text-left">제품군</th>
              <th className="px-3 py-2 text-left">전압</th>
              <th className="px-3 py-2 text-left">재질</th>
              <th className="px-3 py-2 text-right">SQ</th>
              <th className="px-3 py-2 text-left">라우팅</th>
              <th className="px-3 py-2 text-center">외주</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {items.map((item) => (
              <tr
                key={item.item_code}
                className={`hover:bg-gray-50 ${item.is_outsourced ? "bg-gray-50 opacity-60" : ""}`}
              >
                <td className="px-3 py-2 font-mono text-xs">
                  {item.item_code}
                </td>
                <td className="px-3 py-2">{item.product_group}</td>
                <td className="px-3 py-2">{item.voltage}</td>
                <td className="px-3 py-2">{item.conductor_material}</td>
                <td className="px-3 py-2 text-right">{item.cross_section}</td>
                <td className="px-3 py-2 font-mono text-xs">
                  {item.routing_code}
                </td>
                <td className="px-3 py-2 text-center">
                  {item.is_outsourced ? "Y" : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
