/**
 * Task 25 — 소요시간 편집 4 시나리오 E2E.
 *
 * 왜 4 시나리오인가:
 *   cascade-preview 계약의 4 가지 terminal state 를 직접 UI 에서 확인한다.
 *     1) 충돌 → 밀림 → 적용 → Undo (행복 경로 + Undo toast)
 *     2) 축소 → Pull 제안 → master toggle off → 적용 (Pull 분기)
 *     3) due_date 위반 → unresolved + 적용 disabled (실패 터미널)
 *     4) 변경 없음 → no-op (edge)
 *
 * 전제:
 *   - 개발 서버 (next + uvicorn) 구동 중 (playwright.config webServer 가 자동 기동).
 *   - `seededSchedule` fixture 가 deterministic seed 를 로드 — 없으면 자동 skip.
 *   - GanttTaskBlock 의 data-testid 는 `gantt-block-{task_id}` (Task 18 참고).
 *     seed 가 배치하는 고정 task_id "A", "B", "X" 를 사용한다.
 *
 * 실행 불가 케이스:
 *   - seed 엔드포인트가 없으면 전체 describe 가 skip — 이건 의도된 동작.
 *   - 블록 A/B/X 가 viewport 안에 렌더되지 않는 경우 scroll 로직이 필요할 수 있음.
 *     현재는 seed 가 "첫 화면에 보이는 타임윈도우" 에 블록을 배치했다고 가정.
 */
import { test, expect } from "./fixtures/cascadeFixture";

test.describe("블록 소요시간 편집 cascade", () => {
  test.beforeEach(async ({ page, frozenTime, seededSchedule }) => {
    // fixtures 의 사이드이펙트(시간 고정 + seed reset)를 먼저 트리거.
    void frozenTime;
    void seededSchedule;
    await page.goto("/scheduler");
    // 첫 블록 렌더 대기 — hydration + 초기 fetch 완료 시점.
    await expect(page.getByTestId("gantt-block-A")).toBeVisible({
      timeout: 15_000,
    });
  });

  test("소요시간 연장 → 충돌 모달 → 적용 → Undo Toast 복구", async ({
    page,
  }) => {
    // 1) 블록 우클릭 → 컨텍스트 메뉴 → 수정하기
    await page.getByTestId("gantt-block-A").click({ button: "right" });
    await page.getByRole("menuitem", { name: "수정하기" }).click();

    // 2) 소요시간 편집 (seed 기본 3h → 5h). 같은 설비 뒷 블록과 충돌 유도.
    const durationInput = page.getByLabel("소요시간(h)");
    await expect(durationInput).toBeVisible();
    await durationInput.fill("5");
    await page.getByRole("button", { name: "확인" }).click();

    // 3) cascade-preview 결과로 충돌 모달 노출
    const modal = page.getByRole("dialog", { name: /배치 변경 확인/ });
    await expect(modal).toBeVisible();
    await expect(
      modal.getByText(/같은 설비의 다음 블록과 충돌로 밀림/),
    ).toBeVisible();

    // 4) 전체 적용
    await modal.getByRole("button", { name: "적용" }).click();
    await expect(page.getByText("적용 완료")).toBeVisible();

    // 5) Undo toast → 되돌리기
    await page.getByRole("button", { name: "되돌리기" }).click();
    await expect(page.getByText("되돌렸습니다.")).toBeVisible();
  });

  test("소요시간 축소 → Pull 제안 → 마스터 토글 off → 적용", async ({
    page,
  }) => {
    await page.getByTestId("gantt-block-A").click({ button: "right" });
    await page.getByRole("menuitem", { name: "수정하기" }).click();

    // 3h → 1h: 뒷 블록을 앞당길 수 있는 여유 발생 → Pull 제안.
    await page.getByLabel("소요시간(h)").fill("1");
    await page.getByRole("button", { name: "확인" }).click();

    const modal = page.getByRole("dialog", { name: /배치 변경 확인/ });
    await expect(modal).toBeVisible();

    // Pull 제안 섹션 + 마스터 토글 off
    await expect(modal.getByText(/앞당김 제안/)).toBeVisible();
    await modal.getByLabel("Pull 제안을 적용에 포함").uncheck();

    await modal.getByRole("button", { name: "적용" }).click();
    await expect(page.getByText("적용 완료")).toBeVisible();
  });

  test("due date 위반 → unresolved + 적용 disabled", async ({ page }) => {
    // seed 가 task A 에 "4/20 due" 를 설정했다고 가정. 200h 연장은 반드시 초과.
    await page.getByTestId("gantt-block-A").click({ button: "right" });
    await page.getByRole("menuitem", { name: "수정하기" }).click();
    await page.getByLabel("소요시간(h)").fill("200");
    await page.getByRole("button", { name: "확인" }).click();

    const modal = page.getByRole("dialog", { name: /배치 변경 확인/ });
    await expect(modal).toBeVisible();
    // unresolved 메시지 + 적용 버튼 disabled
    await expect(modal.getByText(/해소 불가/)).toBeVisible();
    await expect(modal.getByRole("button", { name: "적용" })).toBeDisabled();
    // 수동조정 CTA 는 page.tsx 가 onManualAdjust prop 을 넘길 때만 노출되는데
    // 현 구현은 undefined 라 assertion 은 skip — 버튼 존재 자체가 선택적.
  });

  test("변경 없음 (duration 그대로) → 저장 no-op", async ({ page }) => {
    await page.getByTestId("gantt-block-A").click({ button: "right" });
    await page.getByRole("menuitem", { name: "수정하기" }).click();
    // 필드 변경 없이 바로 확인 클릭.
    await page.getByRole("button", { name: "확인" }).click();

    // diff 가 없으면 cascade-preview 를 호출하지 않고 모달 없이 종료되어야 한다.
    await expect(
      page.getByRole("dialog", { name: /배치 변경 확인/ }),
    ).not.toBeVisible();
  });
});
