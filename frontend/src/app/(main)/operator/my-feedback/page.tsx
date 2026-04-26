"use client";

/**
 * /operator/my-feedback — 운영자 자기 의견 history (CEO review §1).
 *
 * 운영자는 자기가 보낸 의견이 어떻게 처리되고 있는지 확인할 수 있어야 신뢰
 * 곡선이 망가지지 않는다 (R3 — ⚠ 답 늦음 mitigation).
 */

import { useEffect, useState } from "react";

import { Header } from "@/shared/components/Header";
import { ApiError, apiFetch } from "@/shared/api/client";

interface FeedbackRow {
  id: number;
  created_at: string;
  run_label: string;
  batch_id: number;
  task_id: number | null;
  section: string;
  line_anchor: string;
  constraint_id_hint: string | null;
  free_text: string;
  operator_id: string;
  status: string;
}

const STATUS_LABEL: Record<string, string> = {
  open: "접수됨",
  investigating: "검토 중",
  fixed: "✅ 반영됨",
  wontfix: "검토 후 미반영",
};

const STATUS_BG: Record<string, string> = {
  open: "bg-pwc-status-default-bg text-pwc-status-default-text",
  investigating: "bg-pwc-status-info-bg text-pwc-status-info-text",
  fixed: "bg-pwc-status-success-bg text-pwc-status-success-text",
  wontfix: "bg-pwc-status-warning-bg text-pwc-status-warning-text",
};

function readOperatorId(): string {
  if (typeof window === "undefined") return "anonymous";
  return window.localStorage.getItem("kbi.operator_id") || "anonymous";
}

function formatKst(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd} ${hh}:${mi}`;
}

export default function MyFeedbackPage() {
  const [rows, setRows] = useState<FeedbackRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const operatorId = readOperatorId();
    apiFetch<FeedbackRow[]>(
      `/decision-feedback/me?operator_id=${encodeURIComponent(operatorId)}`,
    )
      .then((data) => {
        setRows(data);
        setLoading(false);
      })
      .catch((e) => {
        if (e instanceof ApiError) {
          setError(`로드 실패 (${e.status}). run-id: ${e.runId ?? "—"}`);
        } else {
          setError("로드 실패");
        }
        setLoading(false);
      });
  }, []);

  return (
    <>
      <Header />
      <main className="max-w-4xl mx-auto px-4 py-6">
        <h1 className="text-pwc-title4 mb-4">내 의견 history</h1>
        {loading ? (
          <p className="text-pwc-body text-pwc-gray-500">불러오는 중…</p>
        ) : error ? (
          <p className="text-pwc-body text-pwc-status-danger-text">{error}</p>
        ) : rows.length === 0 ? (
          <p className="text-pwc-body text-pwc-gray-500">
            아직 보낸 의견이 없습니다. 결정 카드의 ⚠️ 버튼을 눌러 보내주세요.
          </p>
        ) : (
          <ul className="space-y-3 list-none pl-0">
            {rows.map((r) => (
              <li
                key={r.id}
                className="border border-pwc-gray-200 rounded-md p-3"
              >
                <div className="flex items-center justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-pwc-badge rounded-full px-2 py-0.5 ${STATUS_BG[r.status] ?? STATUS_BG.open}`}
                    >
                      {STATUS_LABEL[r.status] ?? r.status}
                    </span>
                    <span className="text-pwc-caption text-pwc-gray-500 font-mono">
                      #{r.id}
                    </span>
                  </div>
                  <span className="text-pwc-caption text-pwc-gray-500">
                    {formatKst(r.created_at)}
                  </span>
                </div>
                <div className="text-pwc-subBody text-pwc-gray-600 mb-1">
                  run <span className="font-mono">{r.run_label}</span> · 배치{" "}
                  {r.batch_id} · 섹션 {r.section}
                  {r.constraint_id_hint ? (
                    <span className="font-mono ml-1">
                      #{r.constraint_id_hint}
                    </span>
                  ) : null}
                </div>
                <p className="text-pwc-body whitespace-pre-wrap">
                  {r.free_text}
                </p>
              </li>
            ))}
          </ul>
        )}
      </main>
    </>
  );
}
