import { test, expect } from "@playwright/test";

test.describe("Smoke Tests", () => {
  test("홈페이지 접근 가능", async ({ page }) => {
    const resp = await page.goto("/");
    expect(resp?.status()).toBeLessThan(400);
  });

  test("plan-register 페이지 로드", async ({ page }) => {
    const resp = await page.goto("/plan-register");
    expect(resp?.status()).toBeLessThan(400);
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("scheduler 페이지 로드", async ({ page }) => {
    const resp = await page.goto("/scheduler");
    expect(resp?.status()).toBeLessThan(400);
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("scheduling-review 페이지 로드", async ({ page }) => {
    const resp = await page.goto("/scheduling-review");
    expect(resp?.status()).toBeLessThan(400);
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("백엔드 API /docs 응답", async ({ page }) => {
    const resp = await page.goto("http://localhost:8000/docs");
    expect(resp?.status()).toBe(200);
  });

  test("프론트엔드 콘솔 에러 없음", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    await page.goto("/plan-register");
    await page.waitForTimeout(3000);
    // Next.js hydration warning 등 무시, 심각한 에러만 체크
    const critical = errors.filter(
      (e) => !e.includes("hydration") && !e.includes("Warning:"),
    );
    expect(critical).toEqual([]);
  });
});
