import { test, expect } from "@playwright/test";

// PR3 visual regression — 색상 토큰 변경이 의도된 페이지/컴포넌트에서 시각 회귀 감지.
// PR3 base commit 시점에 baseline PNG 캡쳐 (--update-snapshots), 이후 commit별 diff.
test.describe("Visual regression — PR3 design system", () => {
  test("/scheduler default state", async ({ page }) => {
    await page.goto("/scheduler");
    await page.waitForLoadState("networkidle");
    await expect(page).toHaveScreenshot("scheduler.png", {
      maxDiffPixelRatio: 0.01,
      fullPage: false,
    });
  });

  test("/scheduling-review default state", async ({ page }) => {
    await page.goto("/scheduling-review");
    await page.waitForLoadState("networkidle");
    await expect(page).toHaveScreenshot("scheduling-review.png", {
      maxDiffPixelRatio: 0.01,
      fullPage: false,
    });
  });

  test("/plan-register default state", async ({ page }) => {
    await page.goto("/plan-register");
    await page.waitForLoadState("networkidle");
    await expect(page).toHaveScreenshot("plan-register.png", {
      maxDiffPixelRatio: 0.01,
      fullPage: false,
    });
  });
});
