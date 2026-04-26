"use client";

/**
 * /admin/decision-feedback — 개발자(admin) 의견 처리 큐.
 *
 * 두 view 토글:
 *   1. List view — 단건 status 변경 + dev_notes
 *   2. Cluster view — (process_name, line_anchor) 그룹 + impact_score (CEO §8)
 *
 * Bulk action: cluster 의 ID 모두 선택 → 일괄 status=fixed/wontfix.
 * wontfix 시 dev_notes 필수 (운영자 자동 전달).
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

interface Cluster {
  process_name: string;
  line_anchor: string;
  constraint_id_hint: string | null;
  frequency: number;
  distinct_operators: number;
  cards_affected: number;
  impact_score: number;
  sample_free_text: string;
  feedback_ids: number[];
}

type View = "list" | "cluster";

export default function AdminDecisionFeedbackPage() {
  const [view, setView] = useState<View>("cluster");
  const [rows, setRows] = useState<FeedbackRow[]>([]);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, clusterList] = await Promise.all([
        apiFetch<FeedbackRow[]>("/admin/decision-feedback?status=open"),
        apiFetch<Cluster[]>("/admin/decision-feedback/clusters?status=open"),
      ]);
      setRows(list);
      setClusters(clusterList);
    } catch (e) {
      if (e instanceof ApiError) {
        setError(`로드 실패 (${e.status}). run-id: ${e.runId ?? "—"}`);
      } else {
        setError("로드 실패");
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const bulkPatch = async (
    ids: number[],
    status: "fixed" | "wontfix",
    devNotes?: string,
  ) => {
    if (status === "wontfix" && !devNotes?.trim()) {
      window.alert("미반영 처리는 운영자에게 전달할 사유가 필요합니다.");
      return;
    }
    if (
      !window.confirm(
        `${ids.length}건을 '${status === "fixed" ? "반영됨" : "검토 후 미반영"}' 으로 변경합니다. 계속할까요?`,
      )
    ) {
      return;
    }
    try {
      await apiFetch("/admin/decision-feedback/bulk", {
        method: "PATCH",
        body: JSON.stringify({ ids, status, dev_notes: devNotes ?? null }),
      });
      await refresh();
    } catch (e) {
      if (e instanceof ApiError) {
        window.alert(`bulk 처리 실패 (${e.status}). run-id: ${e.runId ?? "—"}`);
      } else {
        window.alert("bulk 처리 실패");
      }
    }
  };

  return (
    <>
      <Header />
      <main className="max-w-5xl mx-auto px-4 py-6">
        <div className="flex items-center justify-between mb-4">
          <h1 className="text-pwc-title4 m-0">의견 처리 큐 (admin)</h1>
          <div className="flex gap-1 border border-pwc-gray-200 rounded">
            <button
              type="button"
              onClick={() => setView("cluster")}
              className={`text-pwc-buttonText-sm px-3 py-1 ${
                view === "cluster"
                  ? "bg-pwc-primary-400 text-pwc-text-inverse"
                  : "text-pwc-gray-600"
              }`}
            >
              Cluster
            </button>
            <button
              type="button"
              onClick={() => setView("list")}
              className={`text-pwc-buttonText-sm px-3 py-1 ${
                view === "list"
                  ? "bg-pwc-primary-400 text-pwc-text-inverse"
                  : "text-pwc-gray-600"
              }`}
            >
              List
            </button>
          </div>
        </div>

        {loading ? (
          <p className="text-pwc-body text-pwc-gray-500">불러오는 중…</p>
        ) : error ? (
          <p className="text-pwc-body text-pwc-status-danger-text">{error}</p>
        ) : view === "cluster" ? (
          <ClusterList clusters={clusters} onBulkPatch={bulkPatch} />
        ) : (
          <FeedbackList rows={rows} onRefresh={refresh} />
        )}
      </main>
    </>
  );
}

function ClusterList({
  clusters,
  onBulkPatch,
}: {
  clusters: Cluster[];
  onBulkPatch: (
    ids: number[],
    status: "fixed" | "wontfix",
    devNotes?: string,
  ) => void;
}) {
  if (clusters.length === 0) {
    return (
      <p className="text-pwc-body text-pwc-gray-500">처리할 의견이 없습니다.</p>
    );
  }
  return (
    <ul className="space-y-3 list-none pl-0">
      {clusters.map((c) => (
        <li
          key={`${c.process_name}-${c.line_anchor}`}
          className="border border-pwc-gray-200 rounded-md p-3"
        >
          <div className="flex items-center justify-between gap-2 mb-2">
            <div className="flex items-baseline gap-2">
              <span className="text-pwc-subTitle2">
                {c.process_name} · {c.line_anchor}
              </span>
              {c.constraint_id_hint ? (
                <span className="font-mono text-pwc-caption text-pwc-gray-500">
                  #{c.constraint_id_hint}
                </span>
              ) : null}
            </div>
            <span className="text-pwc-badge bg-pwc-status-warning-bg text-pwc-status-warning-text rounded-full px-2 py-0.5">
              impact {c.impact_score.toFixed(1)}
            </span>
          </div>
          <div className="text-pwc-subBody text-pwc-gray-600 mb-2">
            {c.frequency}건 · 운영자 {c.distinct_operators}명 · 영향 카드{" "}
            {c.cards_affected}건
          </div>
          <p className="text-pwc-body italic text-pwc-gray-600 mb-3 line-clamp-2">
            “{c.sample_free_text}”
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => onBulkPatch(c.feedback_ids, "fixed")}
              className="text-pwc-buttonText-sm px-3 py-1 rounded bg-pwc-status-success-text text-pwc-text-inverse hover:opacity-90"
            >
              ✅ 일괄 반영
            </button>
            <button
              type="button"
              onClick={() => {
                const reason = window.prompt(
                  "미반영 사유 (운영자에게 자동 전달):",
                );
                if (reason && reason.trim()) {
                  onBulkPatch(c.feedback_ids, "wontfix", reason.trim());
                }
              }}
              className="text-pwc-buttonText-sm px-3 py-1 rounded border border-pwc-gray-300 text-pwc-gray-600 hover:bg-pwc-gray-100"
            >
              검토 후 미반영
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}

function FeedbackList({
  rows,
  onRefresh,
}: {
  rows: FeedbackRow[];
  onRefresh: () => void;
}) {
  if (rows.length === 0) {
    return (
      <p className="text-pwc-body text-pwc-gray-500">처리할 의견이 없습니다.</p>
    );
  }
  return (
    <ul className="space-y-2 list-none pl-0">
      {rows.map((r) => (
        <FeedbackItem key={r.id} row={r} onChanged={onRefresh} />
      ))}
    </ul>
  );
}

function FeedbackItem({
  row,
  onChanged,
}: {
  row: FeedbackRow;
  onChanged: () => void;
}) {
  const patch = async (status: "fixed" | "wontfix") => {
    let devNotes: string | undefined;
    if (status === "wontfix") {
      const reason = window.prompt("미반영 사유 (운영자에게 전달):");
      if (!reason?.trim()) return;
      devNotes = reason.trim();
    }
    try {
      await apiFetch(`/admin/decision-feedback/${row.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status, dev_notes: devNotes ?? null }),
      });
      onChanged();
    } catch (e) {
      if (e instanceof ApiError) {
        window.alert(`처리 실패 (${e.status})`);
      }
    }
  };
  return (
    <li className="border border-pwc-gray-200 rounded-md p-3">
      <div className="flex justify-between gap-2 mb-1">
        <span className="text-pwc-subBody text-pwc-gray-600">
          run <span className="font-mono">{row.run_label}</span> · 배치{" "}
          {row.batch_id} · {row.section}/{row.line_anchor} · {row.operator_id}
        </span>
        <span className="font-mono text-pwc-caption text-pwc-gray-500">
          #{row.id}
        </span>
      </div>
      <p className="text-pwc-body whitespace-pre-wrap mb-2">{row.free_text}</p>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => patch("fixed")}
          className="text-pwc-buttonText-sm px-3 py-1 rounded bg-pwc-status-success-text text-pwc-text-inverse hover:opacity-90"
        >
          ✅ 반영
        </button>
        <button
          type="button"
          onClick={() => patch("wontfix")}
          className="text-pwc-buttonText-sm px-3 py-1 rounded border border-pwc-gray-300 text-pwc-gray-600 hover:bg-pwc-gray-100"
        >
          미반영
        </button>
      </div>
    </li>
  );
}
