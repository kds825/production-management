"use client";

import { useEffect, useState } from "react";
import { EDITABLE_CONSTRAINTS } from "./ParamEditor";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

interface HistoryRow {
  history_id: number;
  changed_at: string;
  changed_by: string | null;
  old_params_json: Record<string, number> | null;
  new_params_json: Record<string, number>;
}

type MergedRow = HistoryRow & { constraint_id: string };

function formatKV(obj: Record<string, number> | null | undefined): string {
  if (!obj) return "(없음)";
  const entries = Object.entries(obj);
  if (entries.length === 0) return "{}";
  return entries.map(([k, v]) => `${k}: ${v}`).join(", ");
}

function formatChangedAt(iso: string): string {
  try {
    return new Date(iso).toLocaleString("ko-KR");
  } catch {
    return iso;
  }
}

export function HistoryTab() {
  const [rows, setRows] = useState<MergedRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const ids = EDITABLE_CONSTRAINTS.map((c) => c.constraint_id);

    const fetchAll = async () => {
      try {
        const all: MergedRow[] = [];
        for (const id of ids) {
          const resp = await fetch(`${API}/constraints/${id}/history`);
          if (!resp.ok) continue;
          const data = await resp.json();
          for (const r of (data.history || []) as HistoryRow[]) {
            all.push({ ...r, constraint_id: id });
          }
        }
        all.sort((a, b) => b.changed_at.localeCompare(a.changed_at));
        if (!cancelled) {
          setRows(all);
          setLoading(false);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "이력 조회 실패");
          setLoading(false);
        }
      }
    };

    fetchAll();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return <div className="p-4 text-sm text-gray-400">로딩 중...</div>;
  }
  if (error) {
    return (
      <div className="rounded border border-red-300 bg-red-50 p-4 text-sm text-red-700">
        {error}
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <div className="rounded border border-dashed p-8 text-center text-sm text-gray-400">
        변경 이력이 없습니다.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {rows.map((r) => (
        <div
          key={`${r.constraint_id}-${r.history_id}`}
          className="rounded border border-gray-200 bg-white px-4 py-3 text-sm"
        >
          <div className="mb-2 flex items-center justify-between">
            <span className="font-mono text-xs text-gray-500">
              {r.constraint_id}
            </span>
            <span className="text-xs text-gray-400">
              {formatChangedAt(r.changed_at)}
              {r.changed_by && ` · ${r.changed_by}`}
            </span>
          </div>
          <div className="space-y-1 text-xs">
            <div className="flex gap-2">
              <span className="w-6 text-red-500">-</span>
              <span className="flex-1 text-gray-600">
                {formatKV(r.old_params_json)}
              </span>
            </div>
            <div className="flex gap-2">
              <span className="w-6 text-green-600">+</span>
              <span className="flex-1 text-gray-800">
                {formatKV(r.new_params_json)}
              </span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
