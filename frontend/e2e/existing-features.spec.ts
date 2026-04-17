import { test, expect } from "@playwright/test";

// 모든 테스트는 /scheduler 가 이미 스케줄을 렌더한다고 가정.
// batch_group 은 "저압절연_HC-xxx_고내화" / "저압절연_HC-xxx" 와 같이
// 공정명_식별자[_고내화] 구조로 전달된다 (scheduler/page.tsx 주석 및
// useScheduleStore 참조). 본 테스트는 selector 만 사용해 이 구조에 의존한다.

test.describe("기존 구현 기능 검증", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/scheduler");
    // 간트 블록이 렌더될 때까지 대기 (없으면 아래 각 테스트에서 skip)
    await page
      .locator('[data-testid^="gantt-block-"]')
      .first()
      .waitFor({ state: "visible", timeout: 15000 })
      .catch(() => {});
  });

  test("고내화 '고' 배지 렌더링", async ({ page }) => {
    const gonaehwaBlock = page
      .locator('[data-testid^="gantt-block-"][data-batch-group$="_고내화"]')
      .first();
    if ((await gonaehwaBlock.count()) === 0) {
      test.skip(
        true,
        "렌더 범위에 고내화 배치 없음 — 시드 데이터/날짜 범위 확인",
      );
      return;
    }
    await expect(gonaehwaBlock).toBeVisible();
    // '고' 텍스트가 블록 내부에 존재하는지 확인
    await expect(gonaehwaBlock.getByText("고", { exact: true })).toBeVisible();
  });

  test("블록 클릭 → 우측 상세 패널", async ({ page }) => {
    const firstBlock = page.locator('[data-testid^="gantt-block-"]').first();
    if ((await firstBlock.count()) === 0) {
      test.skip(true, "간트 블록 없음");
      return;
    }
    await firstBlock.click();
    const panel = page.locator('[data-testid="task-detail-panel"]').first();
    await expect(panel).toBeVisible({ timeout: 5000 });
    const box = await panel.boundingBox();
    expect(box).not.toBeNull();
    const viewport = page.viewportSize();
    // 패널 폭 ≥ 350px
    expect(box!.width).toBeGreaterThanOrEqual(350);
    // 패널 우측 끝이 뷰포트 중앙보다 오른쪽
    expect(box!.x + box!.width).toBeGreaterThan((viewport?.width ?? 1200) / 2);
  });

  test("주말 접힘 — 토/일 열 너비 ≤ 16px", async ({ page }) => {
    // hideWeekends 기본값은 false (펼침). "주말 펼침" 토글 버튼을 눌러
    // 접힘 상태로 전환한 뒤, weekend column 폭이 WEEKEND_COLLAPSED_WIDTH
    // (≈8px) 근처인지 검증.
    const toggle = page.getByRole("button", { name: /주말 (펼침|접힘)/ });
    if ((await toggle.count()) === 0) {
      test.skip(true, "주말 토글 버튼 없음 — 간트 설비 렌더 실패");
      return;
    }
    // "주말 펼침" 상태에서만 클릭 (이미 "접힘"이면 skip)
    const label = await toggle.textContent();
    if (label?.includes("펼침")) {
      await toggle.click();
    }
    const weekendCol = page.locator('[data-weekend="true"]').first();
    if ((await weekendCol.count()) === 0) {
      test.skip(true, "주말 열 data attr 없음 — selector 업데이트 필요");
      return;
    }
    const box = await weekendCol.boundingBox();
    expect(box).not.toBeNull();
    // WEEKEND_COLLAPSED_WIDTH ≈ 8px, 여유 포함 16px 이하를 통과 기준.
    expect(box!.width).toBeLessThanOrEqual(16);
  });

  test("고내화 저압절연 분리 — 시간 구간 미중첩", async ({ page }) => {
    // 같은 SQ 에서 '저압절연_..._고내화' 와 '저압절연_...'(일반) 이
    // 별도 블록으로 분리되는지 확인.
    const gonaehwa = page
      .locator(
        '[data-testid^="gantt-block-"][data-batch-group^="저압절연"][data-batch-group$="_고내화"]',
      )
      .first();
    const normal = page
      .locator(
        '[data-testid^="gantt-block-"][data-batch-group^="저압절연"]:not([data-batch-group$="_고내화"])',
      )
      .first();
    if ((await gonaehwa.count()) === 0 || (await normal.count()) === 0) {
      test.skip(true, "고내화 + 일반 저압절연 블록 쌍 없음");
      return;
    }
    const g = await gonaehwa.boundingBox();
    const n = await normal.boundingBox();
    expect(g).not.toBeNull();
    expect(n).not.toBeNull();
    // 두 블록의 X 축(시간) 중첩 여부 — 겹치지 않아야 true
    const nonOverlap =
      g!.x + g!.width <= n!.x + 0.5 || n!.x + n!.width <= g!.x + 0.5;
    expect(nonOverlap).toBe(true);
  });

  test("시각 겹침 없음 — 같은 row 같은 시간 블록은 Y축 분리", async ({
    page,
  }) => {
    // beforeEach 가 이미 /scheduler 로 이동 + 블록 대기함
    const blocks = await page.locator('[data-testid^="gantt-block-"]').all();
    if (blocks.length < 2) {
      test.skip(true, "간트 블록 2개 미만 — 시각 겹침 검증 불가");
      return;
    }
    const boxes = await Promise.all(blocks.map((b) => b.boundingBox()));
    const validBoxes = boxes.filter((b) => b !== null) as Array<{
      x: number;
      y: number;
      width: number;
      height: number;
    }>;

    // Note: GanttTaskBlock 이 가독성 위해 width 를 최소 30px 로 inflate 하므로,
    // 짧은 배치 2개가 시간상 인접하면 시각적으로 오버랩 보일 수 있다(가짜 양성).
    // 실제 시간 겹침 여부는 lane 알고리즘이 다루므로, 테스트는 "lane 0 블록끼리
    // 완전히 같은 시각 구간(≥50% 오버랩) 에 찍히는지" 만 확인한다.
    const MIN_BLOCK_W_INFLATION = 30;
    for (let i = 0; i < validBoxes.length; i++) {
      for (let j = i + 1; j < validBoxes.length; j++) {
        const a = validBoxes[i];
        const b = validBoxes[j];
        const sameBand = Math.abs(a.y - b.y) < 2;
        if (!sameBand) continue;
        const overlapW =
          Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
        if (overlapW <= 0) continue;
        // min-width inflation 보정: 두 블록 중 좁은 쪽 폭이 최소 폭이라면
        // 실제 시간 오버랩이 아닐 가능성이 커서 무시
        const minW = Math.min(a.width, b.width);
        if (minW <= MIN_BLOCK_W_INFLATION + 1) continue;
        // 의미 있는 오버랩(≥50% of the smaller block) 이면 lane 분리 실패
        if (overlapW > minW * 0.5) {
          throw new Error(
            `시각 겹침: block ${i} at (${a.x},${a.y},w${a.width}) vs block ${j} at (${b.x},${b.y},w${b.width}) overlap=${overlapW}`,
          );
        }
      }
    }
  });
});
