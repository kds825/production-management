import { test, expect } from "@playwright/test";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

/**
 * Why: 이 스펙은 실제 DB 값을 변경한다. 시드값(210…)을 하드코딩으로 복원하면
 * 사용자가 UI 로 편집해둔 값까지 덮어쓴다. 각 테스트 시작 시점의 params_json
 * 을 캡처해 종료 시 그대로 복원한다. 이렇게 하면 사용자가 30 으로 저장해둔
 * 값은 테스트 실행 후에도 30 으로 유지된다.
 */
let preSnapshot: Record<string, Record<string, number>> = {};

async function captureSnapshot(request: any) {
  const resp = await request.get(`${API}/constraints`);
  const data = await resp.json();
  const snap: Record<string, Record<string, number>> = {};
  for (const c of data.constraints || []) {
    snap[c.constraint_id] = { ...(c.params_json || {}) };
  }
  return snap;
}

async function restoreSnapshot(
  request: any,
  snap: Record<string, Record<string, number>>,
) {
  for (const [id, params] of Object.entries(snap)) {
    await request.patch(`${API}/constraints/${id}`, {
      data: { params_json: params },
    });
  }
}

test.describe("ConstraintConfig 파라미터 편집", () => {
  test.beforeEach(async ({ request }) => {
    preSnapshot = await captureSnapshot(request);
  });

  test.afterEach(async ({ request }) => {
    await restoreSnapshot(request, preSnapshot);
  });

  test("시나리오 1 — 다음 자동배열부터 적용 (drift 배지 + 이력)", async ({
    page,
  }) => {
    await page.goto("/master/constraints");
    await expect(page.locator("h1")).toContainText("제약 파라미터");

    // 4-1 카드의 연선 규격교체 input (첫 번째 number input).
    // 초기 값은 사용자가 편집해둔 값에 의존하므로 특정 숫자 검증은 하지 않는다.
    const strandingInput = page.locator('input[type="number"]').first();
    await expect(strandingInput).toBeVisible();
    await strandingInput.fill("0");

    // 시간 병기 UX — 0분 표기
    await expect(page.getByText("(0분)")).toBeVisible();

    // 저장 버튼 → 모달
    await page.getByRole("button", { name: "저장" }).click();
    await expect(page.getByText("변경 사항 확인")).toBeVisible();

    // preview-impact 결과 노출
    await expect(page.getByText(/영향 계획 배치: \d+건/)).toBeVisible({
      timeout: 10000,
    });

    // 두 번째 선택: 다음 자동배열부터 적용
    await page.getByRole("button", { name: "다음 자동배열부터 적용" }).click();

    // drift 배너 ON (모달 닫히고 상단에 표시)
    await expect(
      page.getByText("저장된 변경이 아직 스케줄에 반영되지 않았습니다"),
    ).toBeVisible({ timeout: 10000 });

    // 변경 이력 탭에 새 row 존재
    await page.getByRole("button", { name: "변경 이력" }).click();
    await expect(page.getByText(/stranding_min:\s?0/)).toBeVisible({
      timeout: 10000,
    });
  });

  test("시나리오 2 — 기본값으로 되돌리기", async ({ page }) => {
    await page.goto("/master/constraints");
    // 먼저 값을 0으로 변경
    const strandingInput = page.locator('input[type="number"]').first();
    await strandingInput.fill("0");
    await expect(page.getByText("(0분)")).toBeVisible();

    // 4-1 카드의 "기본값으로 되돌리기" 클릭
    // Why: 4-1 섹션 내의 버튼을 정확히 타겟팅하기 위해 카드 context 로 스코핑
    const card41 = page
      .locator("h3:has-text('4-1')")
      .locator("..")
      .locator("..");
    await card41.getByRole("button", { name: "기본값으로 되돌리기" }).click();

    // 복원된 210
    await expect(strandingInput).toHaveValue("210");
    await expect(page.getByText("(3시간 30분)")).toBeVisible();
  });

  test("시나리오 3 — 시간 단위 병기 (분 / 시간 분)", async ({ page }) => {
    await page.goto("/master/constraints");

    // 4-1 stranding_min 210분 = 3시간 30분
    await expect(page.getByText("(3시간 30분)")).toBeVisible({
      timeout: 10000,
    });
    // 4-2 sheath_color_min 120분 = 2시간
    await expect(page.getByText("(2시간)")).toBeVisible();
    // 4-4 welding_min 30분 = 30분
    await expect(page.getByText("(30분)").first()).toBeVisible();
    // 4-1 cv_min 300분 = 5시간
    await expect(page.getByText("(5시간)")).toBeVisible();
  });
});
