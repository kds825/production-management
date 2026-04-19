/**
 * Task 26 — 드래그 이동 cross-equipment cascade E2E.
 *
 * 시나리오:
 *   블록 A 를 다른 설비의 블록 X 위치로 드래그 이동 → cascade-preview 가
 *   "후속 공정 설비의 다른 블록과 충돌로 밀림" (cross-equipment reason) 을 반환
 *   → 모달 노출 → 적용.
 *
 * 왜 drag E2E 가 필요한가:
 *   드래그 경로는 useScheduleChangeWithCascade 의 moveTask flow 를 타며,
 *   duration-edit 경로와는 진입점이 다르다. cross-equipment 로직이 실제 DnD
 *   이벤트 순서(pointerdown → move → drop)에서 끊기지 않고 preview → 모달까지
 *   이어지는지가 이 테스트의 핵심.
 *
 * 한계:
 *   Playwright 의 dragTo 는 내부적으로 mouse events 를 사용하는데, dnd-kit 은
 *   pointer events 를 쓴다. dnd-kit 이 mouse event 를 백업으로 받도록 설정되어
 *   있지 않으면 수동 pointer dispatch 가 필요할 수 있다. 현 시점에는 dragTo 로
 *   시작하고, 실패 시 page.mouse.down/move/up 으로 대체.
 */
import { test, expect } from "./fixtures/cascadeFixture";

test.describe("블록 드래그 cross-equipment cascade", () => {
  test.beforeEach(async ({ page, frozenTime, seededSchedule }) => {
    void frozenTime;
    void seededSchedule;
    await page.goto("/scheduler");
    await expect(page.getByTestId("gantt-block-A")).toBeVisible({
      timeout: 15_000,
    });
  });

  test("드래그 이동 → 후속 공정 설비 충돌 → 적용", async ({ page }) => {
    const source = page.getByTestId("gantt-block-A");
    // block-X 는 다른 설비 슬롯에 배치된 seed 블록. 없으면 seed 가 부족한 것.
    const target = page.getByTestId("gantt-block-X");
    await expect(target).toBeVisible();

    await source.dragTo(target);

    const modal = page.getByRole("dialog", { name: /배치 변경 확인/ });
    await expect(modal).toBeVisible();
    // cross-equipment reason 표기 확인 — 같은 설비 vs 후속 설비 구분이 핵심.
    await expect(
      modal.getByText(/후속 공정 설비의 다른 블록과 충돌로 밀림/),
    ).toBeVisible();

    await modal.getByRole("button", { name: "적용" }).click();
    await expect(page.getByText("적용 완료")).toBeVisible();
  });
});
