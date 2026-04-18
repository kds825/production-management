"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import { useScheduleStore } from "../store/scheduleStore";
import type { Order, ScheduleTask } from "../types";
import { apiFetch } from "@/shared/api/client";

// ISO datetime-local 형식 (YYYY-MM-DDTHH:mm)
export function toDateTimeLocal(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

function parseDateTimeLocal(value: string): Date {
  return new Date(value);
}

/**
 * naive ISO 포맷 — 백엔드 TZ-guard 와 일관 (예: "2026-04-20T10:00:00").
 * datetime-local input value 는 이미 naive (TZ 없음) 이므로 ":00" 초만 부가.
 */
export function toIsoNaive(value: string): string {
  // "YYYY-MM-DDTHH:mm" → "YYYY-MM-DDTHH:mm:00"
  return value.length === 16 ? `${value}:00` : value;
}

/** 두 날짜 사이의 소요 시간(시간) 계산 */
export function calcHours(start: Date, end: Date): number {
  return Math.max(0, (end.getTime() - start.getTime()) / (1000 * 60 * 60));
}

/**
 * Task 20 — 3-field 편집 규칙의 검증 결과.
 * - null: 유효
 * - string: 한글 에러 메시지 (role="alert" 로 표시)
 */
export function validateStartEnd(start: string, end: string): string | null {
  if (!start || !end) return "시작/종료 시간 필수";
  const s = new Date(start).getTime();
  const e = new Date(end).getTime();
  if (!isFinite(s) || !isFinite(e)) return "잘못된 날짜 형식";
  if (e <= s) return "종료일시는 시작일시 이후여야 합니다";
  const h = (e - s) / 3_600_000;
  if (h <= 0) return "소요시간은 0보다 커야 합니다";
  return null;
}

/**
 * Task 20 — duration(h) 편집 핸들러의 순수 계산 부분.
 * start 가 유효한 datetime-local 문자열이면 start + h*3600s 의 새 end 를 반환.
 * start 가 무효이거나 h <= 0 / NaN 이면 null 반환 (caller 에서 무시).
 */
export function computeEndFromDuration(
  startLocal: string,
  hours: number,
): string | null {
  if (!isFinite(hours) || hours <= 0) return null;
  if (!startLocal) return null;
  const s = new Date(startLocal);
  if (!isFinite(s.getTime())) return null;
  return toDateTimeLocal(new Date(s.getTime() + hours * 3_600_000));
}

interface FormData {
  equipment_id: string;
  order_id: string;
  product: string;
  spec: string;
  core_count: number;
  color: string;
  // open string — 알려진 값: 'normal' | 'urgent' | 'critical'
  priority: string;
  start: string; // datetime-local 문자열
  end: string;
  notes: string;
}

const EMPTY_FORM: FormData = {
  equipment_id: "",
  order_id: "",
  product: "",
  spec: "",
  core_count: 0,
  color: "",
  priority: "normal",
  start: "",
  end: "",
  notes: "",
};

type Tab = "basic" | "detail";

/**
 * Task 20 — caller 측(Task 21)에서 cascade 훅의 `commit` 을 주입.
 * 부재 시 기존 legacy PATCH 경로로 fallback.
 */
export interface TaskFormModalProps {
  /**
   * FEATURE_FLAG_CASCADE_V2 on 경로에서 호출되는 orchestrator.
   * 생성 모드(create)에서는 기존 legacy POST 경로를 그대로 사용하며,
   * 편집 모드(edit)의 시간/설비 변경만 cascade 훅으로 전달.
   */
  onSubmitWithCascade?: (input: {
    task_id: string;
    new_start: string;
    new_end: string;
    new_equipment_code?: string | null;
  }) => Promise<void> | void;
}

export function TaskFormModal({
  onSubmitWithCascade,
}: TaskFormModalProps = {}) {
  const taskFormModal = useScheduleStore((s) => s.taskFormModal);
  const closeTaskFormModal = useScheduleStore((s) => s.closeTaskFormModal);
  const equipment = useScheduleStore((s) => s.equipment);
  const tasks = useScheduleStore((s) => s.tasks);
  const unscheduledItems = useScheduleStore((s) => s.unscheduledItems);
  const updateTask = useScheduleStore((s) => s.updateTask);
  const addTask = useScheduleStore((s) => s.addTask);

  // Task 4.2: 모달은 "order" kind만 소비. batch_group kind는 Task 5.4에서
  // 별도 UI로 처리되므로 이곳에서 필터링한 Order[] 만 사용한다.
  const orderItems = useMemo<Order[]>(
    () =>
      unscheduledItems
        .filter((i): i is { kind: "order"; order: Order } => i.kind === "order")
        .map((i) => i.order),
    [unscheduledItems],
  );

  const [tab, setTab] = useState<Tab>("basic");
  const [form, setForm] = useState<FormData>(EMPTY_FORM);
  const [errors, setErrors] = useState<Partial<Record<keyof FormData, string>>>(
    {},
  );
  const [isSubmitting, setIsSubmitting] = useState(false);

  // 모달이 열릴 때 초기값 설정
  useEffect(() => {
    if (!taskFormModal.isOpen) return;

    setTab("basic");
    setErrors({});

    if (taskFormModal.mode === "edit" && taskFormModal.taskId) {
      const task = tasks.find((t) => t.id === taskFormModal.taskId);
      if (task) {
        setForm({
          equipment_id: task.equipment_id,
          order_id: task.order_id,
          product: task.product,
          spec: task.spec,
          core_count: task.core_count,
          color: task.color,
          priority: task.priority,
          start: toDateTimeLocal(
            task.start instanceof Date ? task.start : new Date(task.start),
          ),
          end: toDateTimeLocal(
            task.end instanceof Date ? task.end : new Date(task.end),
          ),
          notes: task.notes,
        });
      }
    } else {
      // 생성 모드: prefill 값으로 초기화
      const prefill = taskFormModal.prefill;
      const now = new Date();
      const defaultStart = prefill?.start ?? now;
      const defaultEnd =
        prefill?.end ?? new Date(defaultStart.getTime() + 8 * 60 * 60 * 1000);
      setForm({
        ...EMPTY_FORM,
        equipment_id: prefill?.equipmentId ?? "",
        start: toDateTimeLocal(defaultStart),
        end: toDateTimeLocal(defaultEnd),
      });
    }
  }, [taskFormModal, tasks]);

  // ESC 키로 닫기
  useEffect(() => {
    if (!taskFormModal.isOpen) return;
    function handleEscape(e: KeyboardEvent) {
      if (e.key === "Escape") closeTaskFormModal();
    }
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [taskFormModal.isOpen, closeTaskFormModal]);

  // 수주 선택 시 자동 채우기
  const handleOrderSelect = useCallback(
    (orderId: string) => {
      const order = orderItems.find((o) => o.id === orderId);
      if (!order) {
        setForm((prev) => ({ ...prev, order_id: orderId }));
        return;
      }
      setForm((prev) => ({
        ...prev,
        order_id: orderId,
        product: order.product,
        spec: order.spec,
        core_count: order.core_count,
        color: order.color,
        priority: order.priority,
      }));
    },
    [orderItems],
  );

  function validate(): boolean {
    const newErrors: Partial<Record<keyof FormData, string>> = {};
    if (!form.equipment_id) newErrors.equipment_id = "설비를 선택하세요.";
    if (!form.product.trim()) newErrors.product = "제품명을 입력하세요.";
    if (!form.start) newErrors.start = "시작일을 입력하세요.";
    if (!form.end) newErrors.end = "종료일을 입력하세요.";
    if (form.start && form.end && form.start >= form.end) {
      newErrors.end = "종료일은 시작일 이후여야 합니다.";
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }

  async function handleSubmit() {
    if (!validate()) return;
    // Task 20 — 3-field 검증 (duration > 0, end > start, isNaN).
    // validate() 는 단순 문자열 비교로만 일부 커버 — 여기서 수치 검증 보강.
    if (validateStartEnd(form.start, form.end)) return;
    setIsSubmitting(true);

    try {
      const startDate = parseDateTimeLocal(form.start);
      const endDate = parseDateTimeLocal(form.end);

      if (taskFormModal.mode === "edit" && taskFormModal.taskId) {
        // Task 20 — cascade 훅 주입 경로 (Task 21 에서 caller 가 wire-up).
        // 부재 시 기존 legacy PATCH 경로로 fallback.
        if (onSubmitWithCascade) {
          await onSubmitWithCascade({
            task_id: taskFormModal.taskId,
            new_start: toIsoNaive(form.start),
            new_end: toIsoNaive(form.end),
            new_equipment_code: form.equipment_id || null,
          });
          closeTaskFormModal();
          return;
        }

        // 수정 모드: store 업데이트 후 API 호출 (낙관적 업데이트)
        updateTask(taskFormModal.taskId, {
          equipment_id: form.equipment_id,
          product: form.product,
          spec: form.spec,
          core_count: form.core_count,
          color: form.color,
          priority: form.priority,
          start: startDate,
          end: endDate,
          notes: form.notes,
        });

        try {
          await apiFetch(`/schedules/tasks/${taskFormModal.taskId}`, {
            method: "PATCH",
            body: JSON.stringify({
              equipment_id: form.equipment_id,
              product: form.product,
              spec: form.spec,
              core_count: form.core_count,
              color: form.color,
              priority: form.priority,
              start: startDate.toISOString(),
              end: endDate.toISOString(),
              notes: form.notes,
            }),
          });
        } catch {
          // API 실패 시 오류 무시 (낙관적 업데이트 유지)
          console.warn("[TaskFormModal] API 업데이트 실패 (로컬 상태 유지)");
        }
      } else {
        // 생성 모드
        const newTask: ScheduleTask = {
          id: `task-new-${Date.now()}`,
          order_id: form.order_id,
          equipment_id: form.equipment_id,
          product: form.product,
          spec: form.spec,
          core_count: form.core_count,
          color: form.color,
          start: startDate,
          end: endDate,
          volume_m: 0,
          line_speed_m_per_min: 0,
          priority: form.priority,
          status: "planned",
          predecessors: [],
          notes: form.notes,
          changeover_min: 0,
        };

        try {
          const created = await apiFetch<{ id: string }>("/schedules/tasks", {
            method: "POST",
            body: JSON.stringify({
              ...newTask,
              start: startDate.toISOString(),
              end: endDate.toISOString(),
            }),
          });
          newTask.id = created.id ?? newTask.id;
        } catch {
          console.warn("[TaskFormModal] API 생성 실패 (로컬 ID로 추가)");
        }

        addTask(newTask);
      }

      closeTaskFormModal();
    } finally {
      setIsSubmitting(false);
    }
  }

  // Task 20 — duration 편집 (start 고정). 순수 계산은 computeEndFromDuration 위임.
  const onDurationChange = useCallback(
    (h: number) => {
      const newEndLocal = computeEndFromDuration(form.start, h);
      if (!newEndLocal) return;
      setForm((p) => ({ ...p, end: newEndLocal }));
    },
    [form.start],
  );

  if (!taskFormModal.isOpen) return null;

  const startDate = form.start ? parseDateTimeLocal(form.start) : null;
  const endDate = form.end ? parseDateTimeLocal(form.end) : null;
  const durationHours =
    startDate && endDate ? calcHours(startDate, endDate) : 0;

  // 실시간 검증 — 저장 버튼 disabled + role="alert" 메시지.
  const validationError = validateStartEnd(form.start, form.end);

  const title = taskFormModal.mode === "create" ? "작업 추가" : "작업 수정";

  return (
    // 모달 오버레이
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: "rgba(0,0,0,0.4)" }}
      onMouseDown={(e) => {
        // 오버레이 클릭 시 닫기 (모달 카드 클릭은 버블 방지)
        if (e.target === e.currentTarget) closeTaskFormModal();
      }}
    >
      {/* 모달 카드 */}
      <div
        className="bg-white rounded-xl shadow-2xl w-full max-w-lg mx-4 flex flex-col"
        style={{ maxHeight: "90vh" }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* 헤더 */}
        <div
          className="flex items-center justify-between px-5 py-3.5 border-b border-gray-200 rounded-t-xl"
          style={{ backgroundColor: "var(--kbi-brown)" }}
        >
          <h2 className="text-sm font-semibold text-white">{title}</h2>
          <button
            onClick={closeTaskFormModal}
            className="text-white/70 hover:text-white transition-colors text-lg leading-none"
            aria-label="닫기"
          >
            ×
          </button>
        </div>

        {/* 탭 */}
        <div className="flex border-b border-gray-200 bg-gray-50">
          {(["basic", "detail"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-5 py-2.5 text-xs font-medium transition-colors border-b-2 ${
                tab === t
                  ? "border-red-600 text-red-700 bg-white"
                  : "border-transparent text-gray-500 hover:text-gray-700"
              }`}
            >
              {t === "basic" ? "기본 정보" : "상세"}
            </button>
          ))}
        </div>

        {/* 폼 본문 */}
        <div className="overflow-y-auto flex-1 px-5 py-4">
          {tab === "basic" ? (
            <div className="flex flex-col gap-3.5">
              {/* 설비 */}
              <Field label="설비" required error={errors.equipment_id}>
                <select
                  value={form.equipment_id}
                  onChange={(e) =>
                    setForm((p) => ({ ...p, equipment_id: e.target.value }))
                  }
                  className={selectClass(!!errors.equipment_id)}
                >
                  <option value="">설비를 선택하세요</option>
                  {equipment.map((eq) => (
                    <option key={eq.id} value={eq.id}>
                      {eq.name}
                    </option>
                  ))}
                </select>
              </Field>

              {/* 수주번호 / 제품명 */}
              <Field label="수주번호 / 제품명" required error={errors.product}>
                {orderItems.length > 0 ? (
                  <select
                    value={form.order_id}
                    onChange={(e) => handleOrderSelect(e.target.value)}
                    className={selectClass(false)}
                  >
                    <option value="">수주를 선택하세요</option>
                    {orderItems.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.order_number} — {o.product}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    type="text"
                    value={form.product}
                    onChange={(e) =>
                      setForm((p) => ({ ...p, product: e.target.value }))
                    }
                    placeholder="예: TFR-CV-WB 6/10kV"
                    className={inputClass(!!errors.product)}
                  />
                )}
              </Field>

              {/* 규격 */}
              <Field label="규격">
                <input
                  type="text"
                  value={form.spec}
                  onChange={(e) =>
                    setForm((p) => ({ ...p, spec: e.target.value }))
                  }
                  placeholder="예: 240mm²"
                  className={inputClass(false)}
                />
              </Field>

              {/* 코어수 + 색상 */}
              <div className="grid grid-cols-2 gap-3">
                <Field label="코어수">
                  <input
                    type="number"
                    min={0}
                    value={form.core_count || ""}
                    onChange={(e) =>
                      setForm((p) => ({
                        ...p,
                        core_count: Number(e.target.value),
                      }))
                    }
                    placeholder="0"
                    className={inputClass(false)}
                  />
                </Field>
                <Field label="색상">
                  <input
                    type="text"
                    value={form.color}
                    onChange={(e) =>
                      setForm((p) => ({ ...p, color: e.target.value }))
                    }
                    placeholder="예: 흑/황/적/청"
                    className={inputClass(false)}
                  />
                </Field>
              </div>

              {/* 작업유형 */}
              <Field label="작업유형">
                <div className="flex gap-3 mt-0.5">
                  {(
                    [
                      ["normal", "정상"],
                      ["urgent", "긴급"],
                      ["critical", "외주"],
                    ] as [string, string][]
                  ).map(([value, label]) => (
                    <label
                      key={value}
                      className="flex items-center gap-1.5 cursor-pointer"
                    >
                      <input
                        type="radio"
                        name="priority"
                        value={value}
                        checked={form.priority === value}
                        onChange={() =>
                          setForm((p) => ({
                            ...p,
                            priority: value as FormData["priority"],
                          }))
                        }
                        className="accent-red-600"
                      />
                      <span className="text-xs text-gray-700">{label}</span>
                    </label>
                  ))}
                </div>
              </Field>
            </div>
          ) : (
            <div className="flex flex-col gap-3.5">
              {/* 시작일시 */}
              <Field label="시작일시" required error={errors.start}>
                <input
                  type="datetime-local"
                  value={form.start}
                  onChange={(e) =>
                    setForm((p) => ({ ...p, start: e.target.value }))
                  }
                  className={inputClass(!!errors.start)}
                />
              </Field>

              {/* 종료일시 */}
              <Field label="종료일시" required error={errors.end}>
                <input
                  type="datetime-local"
                  value={form.end}
                  onChange={(e) =>
                    setForm((p) => ({ ...p, end: e.target.value }))
                  }
                  className={inputClass(!!errors.end)}
                />
              </Field>

              {/* Task 20 — 소요시간(h) editable (3-field rule: start 고정, end 파생) */}
              <Field label="소요시간(h)" required>
                <input
                  type="number"
                  step="0.1"
                  min="0.1"
                  value={durationHours > 0 ? durationHours.toFixed(1) : ""}
                  onChange={(e) => onDurationChange(parseFloat(e.target.value))}
                  placeholder="예: 3.0"
                  className={inputClass(false)}
                  aria-label="소요시간(h)"
                />
              </Field>

              {/* Task 20 — 실시간 fail-fast 검증 메시지 */}
              {validationError && (
                <p
                  role="alert"
                  className="text-[11px]"
                  style={{ color: "var(--color-danger)" }}
                >
                  {validationError}
                </p>
              )}

              {/* 메모 */}
              <Field label="메모">
                <textarea
                  value={form.notes}
                  onChange={(e) =>
                    setForm((p) => ({ ...p, notes: e.target.value }))
                  }
                  rows={3}
                  placeholder="작업 관련 메모를 입력하세요"
                  className={`${inputClass(false)} resize-none`}
                />
              </Field>
            </div>
          )}
        </div>

        {/* 하단 버튼 */}
        <div className="flex justify-end gap-2 px-5 py-3.5 border-t border-gray-200 bg-gray-50 rounded-b-xl">
          <button
            onClick={closeTaskFormModal}
            className="px-4 py-2 text-xs font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
          >
            취소
          </button>
          <button
            onClick={handleSubmit}
            disabled={isSubmitting || !!validationError}
            className="px-5 py-2 text-xs font-medium text-white rounded-lg transition-colors disabled:opacity-50"
            style={{
              backgroundColor:
                isSubmitting || validationError
                  ? "var(--color-text-tertiary)"
                  : "var(--color-brand-primary)",
            }}
            onMouseEnter={(e) => {
              if (!isSubmitting && !validationError)
                e.currentTarget.style.backgroundColor = "var(--kbi-red-dark)";
            }}
            onMouseLeave={(e) => {
              if (!isSubmitting && !validationError)
                e.currentTarget.style.backgroundColor =
                  "var(--color-brand-primary)";
            }}
          >
            {isSubmitting ? "저장 중..." : "확인"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── 헬퍼 컴포넌트 ────────────────────────────────────────────────

interface FieldProps {
  label: string;
  required?: boolean;
  error?: string;
  children: React.ReactNode;
}

function Field({ label, required, error, children }: FieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium text-gray-700">
        {label}
        {required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      {children}
      {error && <p className="text-[10px] text-red-500">{error}</p>}
    </div>
  );
}

function inputClass(hasError: boolean): string {
  return [
    "w-full px-3 py-2 text-xs border rounded-lg outline-none transition-colors",
    hasError
      ? "border-red-400 focus:ring-1 focus:ring-red-400"
      : "border-gray-300 focus:border-red-400 focus:ring-1 focus:ring-red-200",
  ].join(" ");
}

function selectClass(hasError: boolean): string {
  return inputClass(hasError) + " bg-white cursor-pointer";
}
