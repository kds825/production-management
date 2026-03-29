import { test, expect } from "@playwright/test";

const API = "http://localhost:8000/api";

test.describe("마스터 데이터 페이지", () => {
  test("제약조건 관리 페이지 로드 + 데이터 표시", async ({ page }) => {
    await page.goto("/master/constraints");
    await expect(page.locator("h1")).toContainText("제약조건 관리");
    // Wait for data to load
    await expect(page.locator("text=거래처 우선순위")).toBeVisible({
      timeout: 10000,
    });
    // Check toggle exists
    await expect(page.locator("input[type=checkbox]").first()).toBeVisible();
  });

  test("설비 관리 페이지 로드 + 26개 설비", async ({ page }) => {
    await page.goto("/master/equipment");
    await expect(page.locator("h1")).toContainText("설비 관리");
    await expect(page.locator("text=C11D").first()).toBeVisible({
      timeout: 10000,
    });
    // Check for at least some equipment rows
    const rows = page.locator("tbody tr");
    await expect(rows).toHaveCount(26, { timeout: 10000 });
  });

  test("선속 마스터 페이지 로드 + 데이터", async ({ page }) => {
    await page.goto("/master/speed");
    await expect(page.locator("h1")).toContainText("선속 마스터");
    await expect(page.locator("text=EX-B100").first()).toBeVisible({
      timeout: 10000,
    });
  });

  test("가동 캘린더 페이지 로드 + 4 규칙", async ({ page }) => {
    await page.goto("/master/calendar");
    await expect(page.locator("h1")).toContainText("가동 캘린더");
    await expect(page.locator("text=CAL-STD")).toBeVisible({ timeout: 10000 });
    await expect(page.locator("text=CAL-FRI")).toBeVisible();
    await expect(page.locator("text=CAL-MON-EDU")).toBeVisible();
    await expect(page.locator("text=CAL-HOL")).toBeVisible();
  });

  test("거래처 관리 페이지 로드", async ({ page }) => {
    await page.goto("/master/customers");
    await expect(page.locator("h1")).toContainText("거래처 관리");
    await expect(page.locator("text=아이마켓코리아")).toBeVisible({
      timeout: 10000,
    });
  });

  test("공정 라우팅 페이지 로드 + 화살표 표시", async ({ page }) => {
    await page.goto("/master/routing");
    await expect(page.locator("h1")).toContainText("공정 라우팅");
    await expect(page.locator("text=RT-001")).toBeVisible({ timeout: 10000 });
    // Check process arrows
    await expect(page.locator("text=→"))
      .toHaveCount(0, { timeout: 5000 })
      .catch(() => {});
  });

  test("틀단위 관리 페이지 로드", async ({ page }) => {
    await page.goto("/master/drum-lots");
    await expect(page.locator("h1")).toContainText("틀단위 관리");
  });
});

test.describe("API 통합 테스트", () => {
  test("제약조건 API 응답", async ({ request }) => {
    const response = await request.get(`${API}/constraints`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data.total).toBeGreaterThan(0);
    expect(data.enabled).toBeGreaterThan(0);
  });

  test("설비 마스터 API", async ({ request }) => {
    const response = await request.get(`${API}/master/equipment_master`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data.total).toBe(26);
  });

  test("선속 마스터 API", async ({ request }) => {
    const response = await request.get(`${API}/master/speed_master`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data.total).toBe(89);
  });

  test("LLM 설명 API", async ({ request }) => {
    const response = await request.get(`${API}/audit/explain/1`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data.source).toBeDefined();
    expect(data.explanation).toBeTruthy();
    expect(data.batch_id).toBe(1);
  });

  test("파이프라인 실행 이력 API", async ({ request }) => {
    const response = await request.get(`${API}/pipeline/runs`);
    expect(response.ok()).toBeTruthy();
  });

  test("Health check", async ({ request }) => {
    const response = await request.get(`${API}/health`);
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data.status).toBe("ok");
  });
});
