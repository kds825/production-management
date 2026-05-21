"use client";

/**
 * 미기록 사유 일괄 입력 모달.
 *
 * 트리거:
 *   - SchedulerToolbar 의 `MissingReasonsBadge` 클릭.
 *
 * 흐름:
 *   1. open → GET /api/change-sets/missing-reasons/list 호출. 백엔드가
 *      fixture 가드 (preview_request_id IS NOT NULL + applied_by !=
 *      'test-user') 를 이미 적용하므로 응답이 곧 카운트 대상.
 *   2. 상단 4-chip ("납기 변경" / "현장 긴급" / "설비 고장" / "자재 부족")
 *      클릭 → 체크된 모든 행의 사유를 그 값으로 일괄 설정.
 *   3. 행별 dropdown 으로 사유 override 가능.
 *   4. "전체 저장" → 사유가 정해진 행만 PATCH /bulk-reason. 부분 실패는
 *      응답 envelope 의 errors[] 로 받아 토스트에 노출.
 *
 * 디자인:
 *   samildevkit 토큰 (--color-brand-primary / --color-text-primary 등) 만
 *   사용. raw hex / Tailwind arbitrary 색상 금지 (verify-pwc-design 통과).
 *   BatchSplitModal 의 레이아웃 패턴을 거의 동일하게 따른다.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { useToastStore } from "@/shared/ui/toastStore";

import {
  BulkReasonError,
  BulkReasonUpdate,
  MissingReasonItem,
  MissingReasonsListResponse,
  fetchMissingReasonsList,
  patchBulkReason,
} from "../api/missingReasons";
import { useMissingReasonsStore } from "../store/missingReasonsStore";

interface MissingReasonsBulkModalProps {
  open: boolean;
  onClose: () => void;
}

export interface RowState {
  selected: boolean;
  // null = 사유 미정. 저장 시 selected && reason !== null 인 행만 보낸다.
  reason: string | null;
}

/**
 * 체크된 행에만 reason 을 일괄 적용한 새 rowState 를 반환 (pure).
 *
 * 일괄 적용은 체크박스로 선택된 행에만 영향을 줘야 한다 — 미선택 행은
 * 운영자가 명시적으로 제외한 것이므로 사유를 덮어쓰면 안 된다.
 */
export function applyBulkReasonToRows(
  rows: Record<string, RowState>,
  reason: string,
): Record<string, RowState> {
  const next: Record<string, RowState> = {};
  for (const [id, state] of Object.entries(rows)) {
    next[id] = state.selected ? { ...state, reason } : state;
  }
  return next;
}

/**
 * rowState 에서 저장 대상 payload 를 추출 (pure).
 *
 * 저장 조건: `selected === true && reason !== null`. 둘 중 하나라도 빠지면
 * "운영자가 이번 저장 사이클에서 처리하지 않기로 한 행" 이므로 빠진다.
 */
export function computePendingUpdates(
  rows: Record<string, RowState>,
): { change_set_id: string; reason: string }[] {
  const result: { change_set_id: string; reason: string }[] = [];
  for (const [change_set_id, s] of Object.entries(rows)) {
    if (s.selected && s.reason !== null) {
      result.push({ change_set_id, reason: s.reason });
    }
  }
  return result;
}

/** uuid → 앞 8자 short form. 화면 영역이 좁아 식별 보조 용도. */
export function shortChangeSet(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

/**
 * ISO datetime → HH:MM 부분문자열.
 *
 * 백엔드/프론트가 모두 naive datetime (KST 기준) 으로 합의했으므로 timezone
 * 변환 없이 substring 으로 충분하다. 운영자는 분/시간 단위만 필요.
 */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = iso.slice(11, 16);
  return t || iso;
}

/**
 * change_set 한 건의 task 리스트를 한 줄 요약으로 압축.
 *
 * 운영자 관점 우선순위:
 *   1) batch_group (간트 블록의 식별자) — 있으면 최우선.
 *   2) `task {id} · {equipment_code}` — batch_group 미설정 시 fallback.
 *   3) `task {id}` — equipment 도 없는 fixture-like 행.
 *
 * task 가 여러 개면 "외 N건" 으로 줄여 모달 세로 길이를 통제.
 */
export function summarizeTaskChange(item: MissingReasonItem): {
  label: string;
  before: string;
  after: string;
} {
  // 한 change_set 이 여러 task 를 건드릴 수 있지만 운영자 관점에선 첫
  // task 의 batch_group 이 사실상 식별자. 추가 task 가 있으면 "외 N건"
  // 으로 압축 — 모달이 세로로 길어지는 걸 막는다.
  if (item.tasks.length === 0) {
    return { label: "(영향 task 없음)", before: "", after: "" };
  }
  const head = item.tasks[0];
  const extras = item.tasks.length - 1;
  const labelBase =
    head.batch_group ||
    (head.equipment_code
      ? `task ${head.task_id} · ${head.equipment_code}`
      : `task ${head.task_id}`);
  const label = extras > 0 ? `${labelBase} 외 ${extras}건` : labelBase;
  const before = head.before
    ? `${formatTime(head.before.start)}–${formatTime(head.before.end)}`
    : "";
  const after = head.after
    ? `${formatTime(head.after.start)}–${formatTime(head.after.end)}`
    : "";
  return { label, before, after };
}

export function MissingReasonsBulkModal({
  open,
  onClose,
}: MissingReasonsBulkModalProps) {
  const refresh = useMissingReasonsStore((s) => s.refresh);
  const showToast = useToastStore((s) => s.show);

  const [data, setData] = useState<MissingReasonsListResponse | null>(null);
  // 초기 loading=true — 첫 렌더에서 fetch 가 떨어지기 전 잠깐 "미기록 없음"
  // 빈 상태가 깜빡이는 걸 방지. useEffect 가 동기적으로 setLoading(true) 를
  // 또 호출해도 무해 (state 동일).
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // change_set_id → row state. uncontrolled-by-default 와 동일한 효과를
  // 위해 응답 도착 시 모든 행을 selected=true 로 초기화 (운영자가 보통
  // 모두 처리하고 싶어한다는 가정).
  const [rowState, setRowState] = useState<Record<string, RowState>>({});
  const [bulkReason, setBulkReason] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    setRowState({});
    setBulkReason(null);
    fetchMissingReasonsList()
      .then((resp) => {
        if (cancelled) return;
        setData(resp);
        const init: Record<string, RowState> = {};
        for (const it of resp.items) {
          init[it.change_set_id] = { selected: true, reason: null };
        }
        setRowState(init);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "목록 조회 실패");
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const toggleRow = useCallback((id: string) => {
    setRowState((prev) => ({
      ...prev,
      [id]: { ...prev[id], selected: !prev[id]?.selected },
    }));
  }, []);

  const setRowReason = useCallback((id: string, reason: string | null) => {
    setRowState((prev) => ({
      ...prev,
      [id]: { ...prev[id], reason },
    }));
  }, []);

  const applyBulkReason = useCallback((reason: string) => {
    setBulkReason(reason);
    setRowState((prev) => applyBulkReasonToRows(prev, reason));
  }, []);

  const toggleAll = useCallback(() => {
    setRowState((prev) => {
      const allSelected = Object.values(prev).every((s) => s.selected);
      const next: Record<string, RowState> = {};
      for (const [id, s] of Object.entries(prev)) {
        next[id] = { ...s, selected: !allSelected };
      }
      return next;
    });
  }, []);

  const pendingUpdates: BulkReasonUpdate[] = useMemo(
    () => computePendingUpdates(rowState),
    [rowState],
  );

  const handleSave = useCallback(async () => {
    if (pendingUpdates.length === 0) return;
    setSaving(true);
    try {
      const result = await patchBulkReason(pendingUpdates);
      const ok = result.updated_change_set_ids.length;
      const fail = result.errors.length;
      if (fail === 0) {
        showToast(`사유 ${ok}건 저장 완료.`, "success");
        await refresh();
        onClose();
      } else {
        const detail = result.errors
          .slice(0, 3)
          .map(
            (e: BulkReasonError) =>
              `${shortChangeSet(e.change_set_id)}: ${e.error_code}`,
          )
          .join(", ");
        showToast(
          `${ok}건 저장, ${fail}건 실패 (${detail}${fail > 3 ? " …" : ""})`,
          "warning",
        );
        // 실패 행은 모달에 남겨두기 위해 close 하지 않음. store 는 부분 성공
        // 만큼 갱신.
        await refresh();
        // 성공한 행만 rowState 에서 제거 → 사용자가 실패 행을 다시 확인.
        setRowState((prev) => {
          const next = { ...prev };
          for (const id of result.updated_change_set_ids) {
            delete next[id];
          }
          return next;
        });
        setData((prev) =>
          prev
            ? {
                ...prev,
                items: prev.items.filter(
                  (it) =>
                    !result.updated_change_set_ids.includes(it.change_set_id),
                ),
              }
            : prev,
        );
      }
    } catch (e) {
      showToast(e instanceof Error ? e.message : "저장 실패", "error");
    } finally {
      setSaving(false);
    }
  }, [pendingUpdates, refresh, showToast, onClose]);

  if (!open) return null;

  const items = data?.items ?? [];
  const allowedReasons = data?.allowed_reasons ?? [
    "납기 변경",
    "현장 긴급",
    "설비 고장",
    "자재 부족",
  ];
  const selectedCount = Object.values(rowState).filter(
    (s) => s.selected,
  ).length;
  const allSelected = items.length > 0 && selectedCount === items.length;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="사유 미기록 일괄 입력"
    >
      <div
        className="rounded-lg shadow-xl w-[760px] max-h-[80vh] overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        style={{ backgroundColor: "var(--color-bg-elevated)" }}
      >
        {/* 헤더 */}
        <div
          className="flex items-center justify-between border-b px-6 py-4"
          style={{ borderColor: "var(--color-border-default)" }}
        >
          <div>
            <h2
              className="text-lg font-semibold"
              style={{ color: "var(--color-text-primary)" }}
            >
              사유 미기록 {items.length}건
            </h2>
            <p
              className="text-sm mt-0.5"
              style={{ color: "var(--color-text-secondary)" }}
            >
              드래그-드롭으로 변경했지만 사유가 입력되지 않은 항목입니다. 상단
              사유 칩으로 일괄 적용하거나 행별로 지정하세요.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-xl leading-none"
            style={{ color: "var(--color-text-secondary)" }}
            aria-label="닫기"
          >
            &times;
          </button>
        </div>

        {/* 일괄 적용 chip */}
        <div
          className="px-6 py-3 border-b flex items-center gap-2 flex-wrap"
          style={{ borderColor: "var(--color-border-default)" }}
        >
          <span
            className="text-sm font-medium mr-1"
            style={{ color: "var(--color-text-secondary)" }}
          >
            선택된 {selectedCount}건에 일괄 적용:
          </span>
          {allowedReasons.map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => applyBulkReason(r)}
              disabled={selectedCount === 0 || loading || saving}
              className="px-3 py-1 rounded-full border text-sm disabled:opacity-50"
              style={{
                borderColor:
                  bulkReason === r
                    ? "var(--color-brand-primary)"
                    : "var(--color-border-default)",
                color:
                  bulkReason === r
                    ? "var(--color-brand-primary)"
                    : "var(--color-text-primary)",
                backgroundColor:
                  bulkReason === r ? "var(--kbi-red-tint-5)" : "transparent",
              }}
            >
              {r}
            </button>
          ))}
        </div>

        {/* 본문 — row 리스트 */}
        <div className="overflow-auto" style={{ maxHeight: "48vh" }}>
          {loading ? (
            <p
              className="text-sm text-center py-10"
              style={{ color: "var(--color-text-secondary)" }}
            >
              불러오는 중...
            </p>
          ) : error ? (
            <p
              className="text-sm text-center py-10"
              style={{
                color: "var(--color-status-error, var(--color-warning))",
              }}
            >
              {error}
            </p>
          ) : items.length === 0 ? (
            <p
              className="text-sm text-center py-10"
              style={{ color: "var(--color-text-secondary)" }}
            >
              현재 미기록 변경 세트가 없습니다.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead
                className="sticky top-0"
                style={{ backgroundColor: "var(--color-bg-subtle)" }}
              >
                <tr>
                  <th className="px-3 py-2 text-left w-8">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleAll}
                      aria-label="전체 선택"
                      style={{ accentColor: "var(--color-brand-primary)" }}
                    />
                  </th>
                  <th
                    className="px-3 py-2 text-left font-medium"
                    style={{ color: "var(--color-text-secondary)" }}
                  >
                    변경 ID
                  </th>
                  <th
                    className="px-3 py-2 text-left font-medium"
                    style={{ color: "var(--color-text-secondary)" }}
                  >
                    대상
                  </th>
                  <th
                    className="px-3 py-2 text-left font-medium"
                    style={{ color: "var(--color-text-secondary)" }}
                  >
                    Before → After
                  </th>
                  <th
                    className="px-3 py-2 text-left font-medium"
                    style={{ color: "var(--color-text-secondary)" }}
                  >
                    사유
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => {
                  const state = rowState[it.change_set_id] ?? {
                    selected: false,
                    reason: null,
                  };
                  const summary = summarizeTaskChange(it);
                  return (
                    <tr
                      key={it.change_set_id}
                      className="border-t"
                      style={{ borderColor: "var(--color-border-default)" }}
                    >
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          checked={state.selected}
                          onChange={() => toggleRow(it.change_set_id)}
                          aria-label={`${shortChangeSet(it.change_set_id)} 선택`}
                          style={{ accentColor: "var(--color-brand-primary)" }}
                        />
                      </td>
                      <td
                        className="px-3 py-2 font-mono text-xs"
                        style={{ color: "var(--color-text-secondary)" }}
                        title={it.change_set_id}
                      >
                        {shortChangeSet(it.change_set_id)}
                      </td>
                      <td
                        className="px-3 py-2"
                        style={{ color: "var(--color-text-primary)" }}
                      >
                        {summary.label}
                      </td>
                      <td
                        className="px-3 py-2 text-xs"
                        style={{ color: "var(--color-text-secondary)" }}
                      >
                        {summary.before && summary.after
                          ? `${summary.before} → ${summary.after}`
                          : "—"}
                      </td>
                      <td className="px-3 py-2">
                        <select
                          value={state.reason ?? ""}
                          onChange={(e) =>
                            setRowReason(
                              it.change_set_id,
                              e.target.value === "" ? null : e.target.value,
                            )
                          }
                          disabled={!state.selected || saving}
                          aria-label={`${shortChangeSet(it.change_set_id)} 사유`}
                          className="border rounded px-2 py-1 text-sm"
                          style={{
                            borderColor: "var(--color-border-default)",
                            backgroundColor: "var(--color-bg-elevated)",
                            color: "var(--color-text-primary)",
                          }}
                        >
                          <option value="">(미정)</option>
                          {allowedReasons.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* 푸터 */}
        <div
          className="flex items-center justify-between px-6 py-3 border-t"
          style={{ borderColor: "var(--color-border-default)" }}
        >
          <span
            className="text-sm"
            style={{ color: "var(--color-text-secondary)" }}
          >
            저장 대상: {pendingUpdates.length}건
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              disabled={saving}
              className="px-4 py-2 rounded border text-sm disabled:opacity-50"
              style={{
                borderColor: "var(--color-border-default)",
                color: "var(--color-text-primary)",
                backgroundColor: "transparent",
              }}
            >
              취소
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={pendingUpdates.length === 0 || saving}
              className="px-4 py-2 rounded text-sm font-medium disabled:opacity-50"
              style={{
                backgroundColor: "var(--color-brand-primary)",
                color: "var(--color-text-on-brand, white)",
              }}
            >
              {saving ? "저장 중..." : `전체 저장 (${pendingUpdates.length}건)`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
