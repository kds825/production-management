import { test, expect, type Page } from "@playwright/test";

const BLOCK = '[data-testid^="gantt-block-"]';
const OVERLAY = '[data-testid="chain-highlight-overlay"]';
const TIMELINE_BG = '[data-testid="timeline-bg"]';

async function goToScheduler(page: Page) {
  await page.goto("/scheduler");
  await page.waitForSelector(BLOCK, { timeout: 10_000 });
}

test("S1: 블록 클릭 → 오버레이 표시", async ({ page }) => {
  await goToScheduler(page);
  await expect(page.locator(OVERLAY)).toHaveCount(0);
  await page.locator(BLOCK).first().click();
  await expect(page.locator(OVERLAY)).toBeVisible();
});

test("S2: 다른 블록 클릭 → 체인 전환 (동시 해제-재선 버그 없음)", async ({
  page,
}) => {
  await goToScheduler(page);
  const blocks = page.locator(BLOCK);
  if ((await blocks.count()) < 2) test.skip();

  await blocks.nth(0).click();
  await expect(page.locator(OVERLAY)).toBeVisible();
  await blocks.nth(1).click();
  // Overlay 가 계속 visible 이어야 (중간에 null 로 깜빡이지 않아야)
  await expect(page.locator(OVERLAY)).toBeVisible();
});

test("S3: Esc → 해제", async ({ page }) => {
  await goToScheduler(page);
  await page.locator(BLOCK).first().click();
  await expect(page.locator(OVERLAY)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(OVERLAY)).toHaveCount(0);
});

test("S4: 빈 timeline 영역 click → 해제", async ({ page }) => {
  await goToScheduler(page);
  await page.locator(BLOCK).first().click();
  await expect(page.locator(OVERLAY)).toBeVisible();

  const bg = page.locator(TIMELINE_BG);
  const box = await bg.boundingBox();
  expect(box).not.toBeNull();
  if (box) {
    // timeline 안의 블록 없는 영역 — bg 우하단 근처 여백
    await page.mouse.click(box.x + box.width - 40, box.y + box.height - 40);
  }
  await expect(page.locator(OVERLAY)).toHaveCount(0);
});
