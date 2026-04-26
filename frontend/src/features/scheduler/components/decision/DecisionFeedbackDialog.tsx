"use client";

/**
 * Phase 6 Step 6-MVP — DecisionFeedbackDialog.
 *
 * 운영자가 [⚠️ 이상한 것 같아요] 클릭 시 뜨는 modal. ⌘+Enter 보내기, Esc 취소.
 * 첨부 파일은 v2 deferred (CEO §4 cut).
 *
 * payload_snapshot:
 *   현재 보고 있던 DecisionCard 응답 전체를 함께 전송. admin 큐 재생용
 *   (2nd opinion blocker — batch 삭제 후에도 운영자가 본 그대로 재생).
 */

import { useEffect, useRef, useState } from "react";

import { ApiError, apiFetch } from "@/shared/api/client";

import type { DecisionCardV2 } from "./decisionCardTypes";

type Section =
  | "why"
  | "impact"
  | "handoff"
  | "equipment_day"
  | "bundle"
  | "alternatives"
  | "general";

interface Props {
  open: boolean;
  onClose: () => void;
  card: DecisionCardV2;
  /** ⚠ 트리거된 line.anchor — 'why_line_3' 등. null 이면 'general'. */
  initialAnchor: string | null;
  initialSection?: Section;
  /** 운영자 식별 — Step 5 JWT 도입 전 PoC 단계는 localStorage 기반. */
  operatorId: string;
  onSubmitted?: (id: number) => void;
}

function pickConstraintIdHint(
  card: DecisionCardV2,
  anchor: string | null,
): string | null {
  if (!anchor) return null;
  const line = card.why.find((l) => l.anchor === anchor);
  return line?.constraint_id ?? null;
}

export function DecisionFeedbackDialog({
  open,
  onClose,
  card,
  initialAnchor,
  initialSection = "why",
  operatorId,
  onSubmitted,
}: Props) {
  const [section, setSection] = useState<Section>(initialSection);
  const [anchor, setAnchor] = useState<string>(initialAnchor ?? "general");
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!open) return;
    setSection(initialSection);
    setAnchor(initialAnchor ?? "general");
    setText("");
    setError(null);
    queueMicrotask(() => textareaRef.current?.focus());
  }, [open, initialAnchor, initialSection]);

  const submit = async () => {
    if (!text.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const body = {
        run_label: card.run_label,
        batch_id: card.batch_id,
        task_id: card.task_id,
        section,
        line_anchor: anchor,
        constraint_id_hint: pickConstraintIdHint(card, initialAnchor),
        free_text: text.trim(),
        operator_id: operatorId,
        payload_snapshot: card,
      };
      const resp = await apiFetch<{ id: number }>("/decision-feedback", {
        method: "POST",
        body: JSON.stringify(body),
      });
      onSubmitted?.(resp.id);
      onClose();
    } catch (e) {
      if (e instanceof ApiError) {
        setError(`전송 실패 (${e.status}). run-id: ${e.runId ?? "—"}`);
      } else {
        setError("전송 실패 — 네트워크 또는 서버 오류");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      submit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="decision-feedback-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-pwc-status-default-bg/60"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-pwc-bg-elevated rounded-md w-full max-w-md mx-4 shadow-lg">
        <header className="px-4 py-3 border-b border-pwc-gray-200">
          <h3 id="decision-feedback-title" className="text-pwc-subTitle2 m-0">
            ⚠️ 이 결정에 대한 의견
          </h3>
          <p className="text-pwc-caption text-pwc-gray-500 mt-1">
            본 카드의 자연어가 어색하거나, 운영 현장과 다르면 알려주세요. 같은
            카드를 보던 그대로 admin 큐에 보존됩니다.
          </p>
        </header>
        <div className="px-4 py-3 space-y-3">
          <div className="text-pwc-subBody text-pwc-gray-600">
            대상: <strong>{card.process_label}</strong> · 배치 {card.batch_id} ·{" "}
            <span className="font-mono">{anchor}</span>
          </div>
          <label className="block">
            <span className="text-pwc-caption text-pwc-gray-500">섹션</span>
            <select
              value={section}
              onChange={(e) => setSection(e.target.value as Section)}
              className="mt-1 block w-full border border-pwc-gray-200 rounded px-2 py-1 text-pwc-body"
            >
              <option value="why">❶ 왜 이 결정</option>
              <option value="impact">❷ 임팩트</option>
              <option value="handoff">❸ 앞·뒤 작업</option>
              <option value="equipment_day">❹ 설비 하루</option>
              <option value="bundle">❺ 묶음 비교</option>
              <option value="alternatives">❻ 탈락 후보</option>
              <option value="general">기타</option>
            </select>
          </label>
          <label className="block">
            <span className="text-pwc-caption text-pwc-gray-500">
              의견 본문
            </span>
            <textarea
              ref={textareaRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKeyDown}
              maxLength={5000}
              rows={6}
              placeholder="예: 이 색상교체 추정이 어색합니다. 현장에선 같은 흑색 묶음 직후라 셋업이 거의 0인데 180분 잡힘."
              className="mt-1 block w-full border border-pwc-gray-200 rounded px-2 py-2 text-pwc-body"
            />
            <span className="text-pwc-caption text-pwc-gray-500">
              {text.length}/5000 · ⌘+Enter 보내기 · Esc 취소
            </span>
          </label>
          {error ? (
            <p className="text-pwc-caption text-pwc-status-danger-text">
              {error}
            </p>
          ) : null}
        </div>
        <footer className="px-4 py-3 border-t border-pwc-gray-200 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="text-pwc-buttonText-sm px-3 py-1 rounded border border-pwc-gray-300 text-pwc-gray-600 hover:bg-pwc-gray-100 disabled:opacity-50"
          >
            취소
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={submitting || !text.trim()}
            className="text-pwc-buttonText-sm px-3 py-1 rounded bg-pwc-primary-400 text-pwc-text-inverse hover:bg-pwc-primary-500 disabled:opacity-50"
          >
            {submitting ? "전송 중…" : "보내기"}
          </button>
        </footer>
      </div>
    </div>
  );
}
