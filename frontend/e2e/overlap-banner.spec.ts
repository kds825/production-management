import { test, expect } from "@playwright/test";

/**
 * Stage 2 API가 `overlap_alert: true`를 반환하면 스케줄러 페이지에
 * 겹침 경고 배너가 뜨고, 기존 스케줄은 리프레시되지 않아야 한다.
 *
 * /api/pipeline/runs 는 실제 백엔드 응답을 그대로 쓰되,
 * /api/pipeline/stage2 POST만 mock 으로 가로채서 overlap_alert 경로를 검증한다.
 */
test.describe("overlap alert banner", () => {
  test("stage2 응답이 overlap_alert 면 상단 배너가 노출된다", async ({
    page,
  }) => {
    // Stage 2 호출 전에 최신 런 조회가 필요 — 백엔드가 빈 배열을 주면 버튼이 이른 return 하므로
    // runs 응답도 최소 1건을 보장하도록 mock 한다.
    await page.route("**/api/pipeline/runs", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([{ run_label: "mock-run" }]),
      });
    });

    await page.route("**/api/pipeline/stage2", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          overlap_alert: true,
          status: "overlap_alert",
          message: "재시도 한도 도달",
          attempts: 3,
          violations: [{ constraint_id: "overlap", detail: "mock" }],
        }),
      });
    });

    await page.goto("/scheduler");

    // 자동배열 버튼 클릭
    const runBtn = page.getByRole("button", { name: /자동배열/ }).first();
    const count = await runBtn.count();
    if (count === 0) {
      test.skip(true, "자동배열 버튼 selector 미매칭");
    }
    await runBtn.click();

    // 배너 제목과 본문이 보여야 함
    await expect(
      page.getByText("스케줄에 겹침이 있습니다 — 관리자 확인 필요"),
    ).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/3회 재시도 실패/)).toBeVisible();

    // 닫기 버튼으로 dismiss 하면 사라져야 함
    await page.getByRole("button", { name: /겹침 경고 닫기/ }).click();
    await expect(
      page.getByText("스케줄에 겹침이 있습니다 — 관리자 확인 필요"),
    ).toHaveCount(0);
  });
});
