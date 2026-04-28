"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

interface CustomerRow {
  customer_code: string;
  customer_name: string;
  priority: number;
  due_type: string;
  due_strictness: string;
  urgency_frequency: string;
  require_sample: boolean;
}

export default function CustomersPage() {
  const [customers, setCustomers] = useState<CustomerRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/master/customer_master`)
      .then((r) => r.json())
      .then((data) => {
        setCustomers(data.items || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-8 text-gray-400">로딩 중...</div>;

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">거래처 관리</h1>
      <div className="grid gap-4">
        {customers.map((c) => (
          <div
            key={c.customer_code}
            className="flex items-center gap-4 rounded-lg border p-4"
          >
            <div
              className={`flex h-10 w-10 items-center justify-center rounded-full text-white font-bold ${
                c.priority === 1
                  ? "bg-red-500"
                  : c.priority <= 3
                    ? "bg-yellow-500"
                    : "bg-gray-400"
              }`}
            >
              P{c.priority}
            </div>
            <div className="flex-1">
              <div className="font-medium">{c.customer_name}</div>
              <div className="text-sm text-gray-500">
                {c.due_type} · {c.due_strictness} · 독촉: {c.urgency_frequency}
              </div>
            </div>
            <div className="flex gap-2">
              {c.require_sample && (
                <span className="rounded bg-purple-100 px-2 py-0.5 text-xs text-purple-700">
                  샘플부착
                </span>
              )}
              <span className="font-mono text-xs text-gray-400">
                {c.customer_code}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
