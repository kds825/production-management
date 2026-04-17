import { expect, test } from "@playwright/test";

/**
 * 시스 배치 블록 라벨 overlay 렌더 E2E.
 *
 * 회귀 시나리오:
 *  - 주말/야간 건너뛴 첫 segment 가 7-8px 로 좁은 블록(예: SH-A150/B100)에서
 *    기존 flex:1 라벨이 0-width 로 축소되어 시각적으로 사라지던 버그.
 *  - 이제 라벨은 segments 위에 widest segment 기준 absolute overlay 로 그린다.
 *
 * 전제: dev server (포트 3000)가 떠 있고 /scheduler 에 최소 1건의 시스 블록이
 * 존재함. 없으면 test.skip.
 */
test.describe("시스 블록 라벨 가시성", () => {
  test("SH- 접두사 블록은 모두 시각적 라벨(innerText + 양의 너비)이 있다", async ({
    page,
  }) => {
    await page.goto("/scheduler", { waitUntil: "networkidle" });

    const blocks = page.locator('[data-equipment-id^="SH-"]');
    const total = await blocks.count();
    if (total === 0) {
      test.skip(true, "시스 블록이 없음 — 빈 DB");
    }

    // 충분히 넓은 블록만 검사 (좁은 30px 블록은 padding 만으로 내부가 소진될 수 있음)
    const WIDE_ENOUGH = 100;
    let checked = 0;
    for (let i = 0; i < total; i++) {
      const block = blocks.nth(i);
      const eq = await block.getAttribute("data-equipment-id");
      const box = await block.boundingBox();
      if (!box || box.width < WIDE_ENOUGH) continue;
      const text = (await block.innerText()).trim();
      expect(text, `${eq} 블록 innerText 가 비어있음`).not.toBe("");

      // 라벨 span(클래스 text-white)의 실제 width > 0 — overlay 가 widest segment
      // 위에 제대로 펼쳐졌는지 확인 (회귀 포인트: SH-A150/B100 고압시스)
      const labelWidths = await block.evaluate((root) =>
        Array.from(root.querySelectorAll("span.text-white")).map(
          (s) => s.getBoundingClientRect().width,
        ),
      );
      expect(
        labelWidths.length,
        `${eq}: text-white 라벨 span 없음`,
      ).toBeGreaterThan(0);
      const maxW = Math.max(...labelWidths);
      expect(
        maxW,
        `${eq} (block width ${box.width}): 라벨 width 0 (overlay 미작동)`,
      ).toBeGreaterThan(0);
      checked++;
    }
    if (checked === 0) {
      test.skip(true, "≥100px 시스 블록 없음 — 검증 불가");
    }
  });
});
