import { test, expect } from "@playwright/test";
import * as path from "path";
import * as fs from "fs";

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const DOCS = path.join(REPO_ROOT, "Documents");
const ARTIFACTS = path.join(
  REPO_ROOT,
  "docs",
  "superpowers",
  "specs",
  "2026-04-17-artifacts",
);
const API = "http://localhost:8000/api";

// 사용자 지정 시나리오:
//  - 기준일자 2026-04-02
//  - WIP: SM재고리스트.xls, ERP: ERP생산계획_v1.xls (전체 교체)
//  - 분할 후보 중 300SQ만 분할, 나머지는 분할 거절
const BASE_DATE = "2026-04-02";
const WIP_FILE = path.join(DOCS, "SM재고리스트.xls");
const ERP_FILE = path.join(DOCS, "ERP생산계획_v1.xls");
const KEEP_SPLIT_SQ = 300;

fs.mkdirSync(ARTIFACTS, { recursive: true });

test.describe.configure({ mode: "serial" });

test("Stage1 수동 검증 라운드 — 업로드 + 기준일자 + 300SQ만 분할", async ({
  page,
}) => {
  test.setTimeout(180_000);

  // 1) 빈 페이지에서 localStorage 선-세팅 (최상단 "1. 계획 기준일자" 복원용)
  await page.goto("http://localhost:3000/");
  await page.evaluate((d) => {
    localStorage.setItem("plan_base_date", d.replace(/-/g, ""));
  }, BASE_DATE);

  // 2) /plan-register 진입
  await page.goto("http://localhost:3000/plan-register");
  await expect(page.getByText("생산계획등록")).toBeVisible();

  // 3) 기준일자 입력 (최상단 "1. 계획 기준일자")
  const baseDateInput = page
    .locator('section:has(h3:has-text("계획 기준일자")) input[type="date"]')
    .first();
  await baseDateInput.fill(BASE_DATE);
  await expect(baseDateInput).toHaveValue(BASE_DATE);

  // 4) WIP 파일 업로드 (섹션 2)
  const wipSection = page.locator(
    'section:has(h3:has-text("재공수량 파일 업로드"))',
  );
  const wipInput = wipSection.locator('input[type="file"]');
  await wipInput.setInputFiles(WIP_FILE);
  await expect(wipSection.getByText("업로드 완료")).toBeVisible({
    timeout: 10_000,
  });

  // 5) ERP 파일 업로드 (섹션 3) — 전체 교체 모드(default) 유지
  const erpSection = page.locator(
    'section:has(h3:has-text("ERP 작업지시 파일 업로드"))',
  );
  const erpInput = erpSection.locator('input[type="file"]');
  await erpInput.setInputFiles(ERP_FILE);
  await expect(erpSection.getByText("ERP생산계획_v1.xls")).toBeVisible();

  // 6) "작업지시서 생성" 클릭 — reset 직후라 confirm modal 뜨지 않고 바로 실행됨
  const runBtn = erpSection.getByRole("button", { name: "작업지시서 생성" });
  await expect(runBtn).toBeEnabled();

  // Network capture: /stage1 응답 수신
  const stage1ResponsePromise = page.waitForResponse(
    (res) =>
      res.url().includes("/pipeline/stage1") &&
      !res.url().includes("/stage1/update") &&
      res.request().method() === "POST",
    { timeout: 120_000 },
  );
  await runBtn.click();

  const stage1Res = await stage1ResponsePromise;
  expect(stage1Res.ok(), `stage1 status=${stage1Res.status()}`).toBeTruthy();
  interface SplitCandidate {
    sq_mm2: number;
    [key: string]: unknown;
  }
  const stage1Json = (await stage1Res.json()) as {
    run_label: string;
    split_candidates?: SplitCandidate[];
  };
  const runLabel = stage1Json.run_label;

  fs.writeFileSync(
    path.join(ARTIFACTS, "stage1-raw.json"),
    JSON.stringify(stage1Json, null, 2),
  );

  // 7) 분할 후보 확인
  const candidates: SplitCandidate[] = stage1Json.split_candidates || [];
  fs.writeFileSync(
    path.join(ARTIFACTS, "stage1-split-candidates.json"),
    JSON.stringify(candidates, null, 2),
  );

  // 8) 분할 검토 모달 열기 (candidates 존재 시)
  if (candidates.length > 0) {
    const openModalBtn = erpSection.getByRole("button", {
      name: /배치 분할 검토/,
    });
    await expect(openModalBtn).toBeVisible({ timeout: 10_000 });
    await openModalBtn.click();

    // 모달 내부에서 각 후보 카드 순회
    const modal = page
      .locator('div:has(> div:has-text("배치 분할 검토"))')
      .last();
    await expect(modal.getByText("배치 분할 검토")).toBeVisible();

    // gap threshold 기본 3일. 300SQ 후보가 이 기준으로 자동 분할 안 되는 경우도 대비.
    // 구현: (a) 300SQ 카드는 모든 gap 위치의 가위를 ON 상태로 유지 (이미 ON 이면 그대로, OFF면 토글)
    //       (b) 300 이외 카드는 모든 가위 OFF
    //
    // DOM 구조: 각 CandidateCard는 "연선 NSQ" 텍스트를 갖는다.
    // 가위 버튼은 각 chunk 사이에 위치 — data-testid 없음.
    // chunk 내부 텍스트에 m/수주/납기가 있고, 가위 버튼은 "클릭하여 여기서 분할" title 존재.
    //
    // 우리는 각 카드 내부의 title="클릭하여 여기서 분할" | "클릭하여 분할 취소" 버튼들을 찾아서
    // 300SQ 카드는 "클릭하여 여기서 분할" (OFF) 인 것만 클릭해서 ON 으로,
    // 다른 SQ 카드는 "클릭하여 분할 취소" (ON) 인 것만 클릭해서 OFF 로.

    for (const c of candidates) {
      const sq = c.sq_mm2;
      const card = modal
        .locator(`div:has(> div > div > span:has-text("연선 ${sq}SQ"))`)
        .first();
      await expect(card).toBeVisible();

      const cutButtons = card.locator(
        'button[title="클릭하여 여기서 분할"], button[title="클릭하여 분할 취소"]',
      );
      const n = await cutButtons.count();

      if (sq === KEEP_SPLIT_SQ) {
        // 300SQ: 모든 가위 ON
        for (let i = 0; i < n; i++) {
          const btn = cutButtons.nth(i);
          const title = await btn.getAttribute("title");
          if (title === "클릭하여 여기서 분할") {
            await btn.click();
          }
        }
      } else {
        // 기타 SQ: 모든 가위 OFF
        for (let i = 0; i < n; i++) {
          const btn = cutButtons.nth(i);
          const title = await btn.getAttribute("title");
          if (title === "클릭하여 분할 취소") {
            await btn.click();
          }
        }
      }
    }

    // 분할 요약 텍스트 캡처
    const summaryLocator = modal.locator("text=/분할 대상/");
    const summaryText = (await summaryLocator.textContent()) || "";
    fs.writeFileSync(
      path.join(ARTIFACTS, "stage1-split-summary.txt"),
      `runLabel=${runLabel}\n${summaryText.trim()}\n`,
    );

    // 적용 버튼 클릭 — "N건 분할 적용"
    const applyBtn = modal.getByRole("button", { name: /분할 적용|변경 없음/ });
    const applyText = await applyBtn.textContent();
    if (applyText && applyText.includes("분할 적용")) {
      await applyBtn.click();
      // split API 응답 대기 — 여러 번 호출될 수 있으니 잠시 대기
      await page.waitForTimeout(1500);
    } else {
      // 300SQ 후보가 없었을 경우 경고
      console.warn(`[WARN] 300SQ 분할 후보 없음. applyBtn=${applyText}`);
      await modal.getByRole("button", { name: "취소" }).click();
    }
  } else {
    fs.writeFileSync(
      path.join(ARTIFACTS, "stage1-split-summary.txt"),
      `runLabel=${runLabel}\n(split_candidates 없음)\n`,
    );
  }

  // 9) 최종 배치 스냅샷 — GET /pipeline/stage1/{run_label}/batches
  const batchesRes = await page.request.get(
    `${API}/pipeline/stage1/${encodeURIComponent(runLabel)}/batches`,
  );
  expect(batchesRes.ok()).toBeTruthy();
  const batchesJson = await batchesRes.json();
  fs.writeFileSync(
    path.join(ARTIFACTS, "stage1-final-batches.json"),
    JSON.stringify(batchesJson, null, 2),
  );

  // 10) 전체 production_batch 덤프 (스키마 확인용)
  const allBatchesRes = await page.request.get(
    `${API}/pipeline/batch-status-summary`,
  );
  if (allBatchesRes.ok()) {
    fs.writeFileSync(
      path.join(ARTIFACTS, "stage1-batch-status-summary.json"),
      JSON.stringify(await allBatchesRes.json(), null, 2),
    );
  }

  console.log(`\n[verification-stage1] runLabel=${runLabel}`);
  console.log(`[verification-stage1] artifacts: ${ARTIFACTS}`);
});
