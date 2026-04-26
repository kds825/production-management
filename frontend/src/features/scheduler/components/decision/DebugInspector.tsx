"use client";

/**
 * Phase 6 Step 5 — DebugInspector (admin only).
 *
 * 백엔드 응답에 debug 가 채워질 때만 노출 (role-based omit — 운영자 응답엔
 * debug=null). 토글 단축키: Alt+Shift+D. 메모리 simplicity 룰 — useState 1개
 * inline (hook 추상화 X).
 *
 * S2/S3 정정 (스키마 정합성 검증): 시각 source 는 ScheduleTask.start_at /
 * end_at, alternative scores 는 solver_decision.details_json. solver_decision
 * .start_minutes/end_minutes/alternative_objective 인용 금지.
 */

import { useEffect, useState } from "react";

import type { DebugBlock } from "./decisionCardTypes";

interface Props {
  debug: DebugBlock | null;
}

export function DebugInspector({ debug }: Props) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.altKey && e.shiftKey && e.key.toLowerCase() === "d") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  if (!debug) return null;

  return (
    <details
      className="border-t border-pwc-gray-200 px-4 py-3 bg-pwc-status-info-bg"
      open={open}
    >
      <summary
        className="text-pwc-subTitle2 cursor-pointer list-none flex items-center justify-between"
        onClick={(e) => {
          e.preventDefault();
          setOpen((v) => !v);
        }}
      >
        <span className="text-pwc-status-info-text">
          ⚙️ Debug Inspector (admin)
        </span>
        <span className="text-pwc-caption text-pwc-gray-500">Alt+Shift+D</span>
      </summary>
      <div className="mt-3 space-y-3 text-pwc-subBody">
        <Row label="Engine">{debug.engine}</Row>
        <Row label="Solver status">{debug.solver_status}</Row>
        <Row label="Solve time">{debug.solve_time_ms.toFixed(1)}ms</Row>
        <Row label="Constraint config version">
          {debug.constraint_config_version || "—"}
        </Row>
        <details>
          <summary className="text-pwc-caption text-pwc-gray-500 cursor-pointer">
            ScheduleTask snapshot (S2 — 시작/종료 source-of-truth)
          </summary>
          <pre className="mt-1 p-2 bg-pwc-bg-elevated text-pwc-caption font-mono overflow-x-auto rounded">
            {JSON.stringify(debug.schedule_task_row, null, 2)}
          </pre>
        </details>
        <details>
          <summary className="text-pwc-caption text-pwc-gray-500 cursor-pointer">
            solver_decision per-constraint trace (S3 — details_json raw)
          </summary>
          <pre className="mt-1 p-2 bg-pwc-bg-elevated text-pwc-caption font-mono overflow-x-auto rounded max-h-64">
            {JSON.stringify(debug.solver_decision_rows, null, 2)}
          </pre>
        </details>
        <details>
          <summary className="text-pwc-caption text-pwc-gray-500 cursor-pointer">
            objective_breakdown (constraint_id → penalty)
          </summary>
          <pre className="mt-1 p-2 bg-pwc-bg-elevated text-pwc-caption font-mono overflow-x-auto rounded">
            {JSON.stringify(debug.objective_breakdown, null, 2)}
          </pre>
        </details>
        {debug.audit_anchors.length > 0 ? (
          <details>
            <summary className="text-pwc-caption text-pwc-gray-500 cursor-pointer">
              audit_anchors ({debug.audit_anchors.length})
            </summary>
            <ul className="mt-1 space-y-0.5 list-none pl-0 font-mono text-pwc-caption">
              {debug.audit_anchors.map((a, i) => (
                <li key={i}>{JSON.stringify(a)}</li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
    </details>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-pwc-gray-500">{label}</span>
      <span className="font-mono text-pwc-gray-600">{children}</span>
    </div>
  );
}
