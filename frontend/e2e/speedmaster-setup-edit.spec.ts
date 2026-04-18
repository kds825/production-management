import { test, expect } from "@playwright/test";

/**
 * Why: 실제 DB 값을 변경하는 E2E — 편집 전 값을 캡처해 테스트 끝에 복원한다.
 * afterEach 에서 시드값 복원 불가 (원래 값을 모름).
 */

test.describe("SpeedMaster 셋업 시간 인라인 편집", () => {
  test("시나리오 1 — 셀 편집 성공 → drift 배너 ON", async ({ page }) => {
    await page.goto("/master/speed");
    await expect(page.locator("h1")).toContainText("선속 마스터");

    // 첫 번째 데이터 row 의 '규격교체' 컬럼 input (첫 번째 number input)
    const firstRow = page.locator("tbody tr").first();
    const specInput = firstRow.locator('input[type="number"]').nth(0);
    const originalValue = await specInput.inputValue();

    // 값 편집
    const newValue = (Number(originalValue) + 1).toString();
    await specInput.fill(newValue);
    await specInput.blur();

    // 성공 피드백 (✓ 아이콘)
    await expect(firstRow.locator("text=✓")).toBeVisible({ timeout: 5000 });

    // drift 배너 ON
    await expect(
      page.getByText("저장된 변경이 아직 스케줄에 반영되지 않았습니다"),
    ).toBeVisible({ timeout: 10000 });

    // cleanup — 원래 값 복원
    await specInput.fill(originalValue);
    await specInput.blur();
    await page.waitForTimeout(1000);
  });

  test("시나리오 2 — 음수 입력 거부 + 원래 값 복원", async ({ page }) => {
    await page.goto("/master/speed");
    const firstRow = page.locator("tbody tr").first();
    const specInput = firstRow.locator('input[type="number"]').nth(0);
    const originalValue = await specInput.inputValue();

    // 음수 입력
    await specInput.fill("-10");
    await specInput.blur();

    // 에러 메시지 노출
    await expect(firstRow.getByText("0 이상의 숫자를 입력하세요")).toBeVisible({
      timeout: 5000,
    });

    // input 값이 원래 값으로 복원되었는지
    await expect(specInput).toHaveValue(originalValue);
  });
});
