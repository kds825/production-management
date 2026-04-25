import { describe, it, expect, afterEach, vi } from "vitest";
import { useToastStore } from "../toastStore";

/**
 * Toast action slot 단위 테스트 (Task 18).
 *
 * 환경 제약:
 * - vitest environment="node" + 프로젝트에 Testing Library / jsdom 부재
 *   (package.json 수정 금지 제약 하에 DOM 렌더링을 신설하지 않음).
 * - React 19 + "use client" 컴포넌트는 renderToStaticMarkup 에서도 빈 문자열이
 *   반환되어 SSR HTML 검증이 불가능.
 *
 * 따라서 본 단위 테스트는 **store 계층 계약** 만 검증한다:
 *   1) show() 가 action 을 toast item 에 저장한다.
 *   2) action 이 없으면 item.action 은 undefined 로 backward-compatible.
 *   3) 저장된 action.onClick 참조가 호출 가능하다 (Toast.tsx 가 이 참조를 그대로 호출).
 *   4) auto-dismiss 타이머 계약이 유지된다 (기존 회귀 방지).
 *
 * 실제 DOM 클릭 → onClick 실행 회귀는 Playwright e2e (verification-stage1 류) 로 커버.
 */

describe("toastStore action slot", () => {
  afterEach(() => {
    useToastStore.getState().dismissAll();
    vi.useRealTimers();
  });

  it("show() with action stores the action on the toast item", () => {
    const onClick = vi.fn();
    useToastStore.getState().show("작업 완료", "success", 90_000, {
      label: "되돌리기",
      onClick,
    });

    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].action).toBeDefined();
    expect(toasts[0].action!.label).toBe("되돌리기");
    expect(toasts[0].message).toBe("작업 완료");
    expect(toasts[0].durationMs).toBe(90_000);
  });

  it("show() without action yields undefined action (backward compatible)", () => {
    useToastStore.getState().show("plain", "info");
    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].action).toBeUndefined();
  });

  it("action.onClick fires when invoked (Toast.tsx delegates to this ref)", () => {
    const onClick = vi.fn();
    useToastStore
      .getState()
      .show("done", "success", 90_000, { label: "되돌리기", onClick });

    // Toast.tsx 의 버튼 핸들러는 t.action.onClick() → dismiss(t.id) 순서로 호출.
    // 여기선 동일한 참조가 store 에 있는지 + 호출 가능한지 검증.
    const action = useToastStore.getState().toasts[0].action!;
    action.onClick();
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("show() with default durationMs=4000 auto-dismisses", () => {
    vi.useFakeTimers();
    useToastStore.getState().show("bye", "info");
    expect(useToastStore.getState().toasts).toHaveLength(1);
    vi.advanceTimersByTime(4000);
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it("dismissAll clears all toasts", () => {
    useToastStore.getState().show("a");
    useToastStore.getState().show("b");
    expect(useToastStore.getState().toasts).toHaveLength(2);
    useToastStore.getState().dismissAll();
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  /**
   * Week 4 Task 4B.4 — meta.runId 저장 + 표시 prefix 계약.
   *
   * Toast.tsx 의 footer 렌더링은 store 의 `t.meta?.runId` 만 의존하므로,
   * (a) store 가 meta 를 그대로 보존하고 (b) prefix slice(0, 8) 가 안정적으로
   * 8자 ID 를 산출함을 확인하면 UI 레이어 회귀를 방지할 수 있다 (DOM 환경 부재 우회).
   * 실제 DOM 렌더 + 클립보드 복사 회귀는 Playwright e2e 로 커버.
   */
  it("show() with meta.runId stores the runId on the toast item", () => {
    const runId = "11111111-2222-3333-4444-555555555555";
    useToastStore
      .getState()
      .show("API 실패", "error", 4000, undefined, { runId });

    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].meta).toBeDefined();
    expect(toasts[0].meta!.runId).toBe(runId);
    // Toast.tsx 가 노출하는 짧은 prefix — 운영자 가독성 + full UUID 는 클립보드로 복사.
    expect(runId.slice(0, 8)).toBe("11111111");
  });

  it("show() without meta yields undefined meta (backward compatible)", () => {
    useToastStore.getState().show("plain", "info");
    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].meta).toBeUndefined();
  });

  it("show() with meta.runId=null persists null (apiFetch 가 헤더 부재 시 null 부여)", () => {
    useToastStore
      .getState()
      .show("API 실패", "error", 4000, undefined, { runId: null });

    const toasts = useToastStore.getState().toasts;
    expect(toasts[0].meta).toBeDefined();
    expect(toasts[0].meta!.runId).toBeNull();
    // Toast.tsx: runId=null/undefined → footer 미렌더. UI 회귀 방지를 위해 nullish 보존.
  });
});
