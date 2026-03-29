"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

interface AuditLog {
  log_id: number;
  stage: string;
  batch_id: number | null;
  task_id: number | null;
  action_type: string;
  constraints_applied: Array<{
    id: string;
    name: string;
    result: string;
    detail: string;
  }>;
  decision_reason: string;
  created_at: string;
}

interface RunInfo {
  run_label: string;
  batch_count: number;
  created_at: string;
}

export default function AuditPage() {
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [selectedRun, setSelectedRun] = useState<string>("");
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(false);
  const [expandedLog, setExpandedLog] = useState<number | null>(null);

  useEffect(() => {
    fetch(`${API}/pipeline/runs`)
      .then((r) => r.json())
      .then((data) => setRuns(Array.isArray(data) ? data : []))
      .catch(() => {});
  }, []);

  const loadAudit = async (runLabel: string) => {
    setSelectedRun(runLabel);
    setLoading(true);
    try {
      const res = await fetch(`${API}/audit/${runLabel}`);
      const data = await res.json();
      setLogs(data.logs || []);
    } catch {
      setLogs([]);
    }
    setLoading(false);
  };

  return (
    <div className="p-6">
      <h1 className="text-xl font-bold mb-4">감사 추적 (Audit Trail)</h1>

      {/* Run selector */}
      <div className="mb-4 flex items-center gap-3">
        <label className="text-sm font-medium text-gray-600">계획 실행:</label>
        <select
          value={selectedRun}
          onChange={(e) => loadAudit(e.target.value)}
          className="rounded border px-3 py-1.5 text-sm"
        >
          <option value="">선택하세요</option>
          {runs.map((r) => (
            <option key={r.run_label} value={r.run_label}>
              {r.run_label} ({r.batch_count}배치)
            </option>
          ))}
        </select>
        {loading && <span className="text-sm text-gray-400">로딩 중...</span>}
      </div>

      {/* Summary */}
      {logs.length > 0 && (
        <div className="mb-4 rounded-lg bg-blue-50 p-3 text-sm">
          총 {logs.length}건의 결정 기록 | Stage 1:{" "}
          {logs.filter((l) => l.stage === "stage1").length}건 | Stage 2:{" "}
          {logs.filter((l) => l.stage === "stage2").length}건
        </div>
      )}

      {/* Timeline */}
      <div className="space-y-2">
        {logs.map((log) => (
          <div
            key={log.log_id}
            className={`rounded-lg border p-3 cursor-pointer transition-colors ${
              expandedLog === log.log_id
                ? "border-blue-400 bg-blue-50"
                : "hover:bg-gray-50"
            }`}
            onClick={() =>
              setExpandedLog(expandedLog === log.log_id ? null : log.log_id)
            }
          >
            <div className="flex items-center gap-3">
              <span
                className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                  log.stage === "stage1"
                    ? "bg-green-100 text-green-700"
                    : "bg-purple-100 text-purple-700"
                }`}
              >
                {log.stage}
              </span>
              <span className="text-sm font-medium">{log.action_type}</span>
              {log.batch_id && (
                <span className="text-xs text-gray-400">
                  배치#{log.batch_id}
                </span>
              )}
              {log.task_id && (
                <span className="text-xs text-gray-400">
                  작업#{log.task_id}
                </span>
              )}
              <span className="ml-auto text-xs text-gray-400">
                {log.created_at
                  ? new Date(log.created_at).toLocaleString("ko-KR")
                  : ""}
              </span>
            </div>

            {/* Decision reason */}
            {log.decision_reason && (
              <p className="mt-1 text-sm text-gray-600">
                {log.decision_reason}
              </p>
            )}

            {/* Expanded: constraints applied */}
            {expandedLog === log.log_id &&
              log.constraints_applied?.length > 0 && (
                <div className="mt-3 space-y-1 border-t pt-2">
                  <p className="text-xs font-medium text-gray-500">
                    적용된 제약조건:
                  </p>
                  {log.constraints_applied.map((c, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs">
                      <span
                        className={`rounded px-1.5 py-0.5 ${
                          c.result === "pass"
                            ? "bg-green-100 text-green-700"
                            : "bg-red-100 text-red-700"
                        }`}
                      >
                        {c.result}
                      </span>
                      <span className="font-mono text-gray-400">{c.id}</span>
                      <span>{c.name}</span>
                      {c.detail && (
                        <span className="text-gray-400">— {c.detail}</span>
                      )}
                    </div>
                  ))}
                </div>
              )}
          </div>
        ))}
      </div>

      {selectedRun && logs.length === 0 && !loading && (
        <div className="text-center py-12 text-gray-400">
          해당 실행에 대한 감사 기록이 없습니다.
        </div>
      )}

      {!selectedRun && (
        <div className="text-center py-12 text-gray-400">
          계획 실행을 선택하면 결정 과정을 확인할 수 있습니다.
        </div>
      )}
    </div>
  );
}
