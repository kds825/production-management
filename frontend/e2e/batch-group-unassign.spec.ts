import { test, expect, type Page } from "@playwright/test";

/**
 * Phase 6 — batch_group 미배정 & 복원 E2E (S1~S4)
 *
 * 시나리오:
 *   S1: 우클릭 → 사유(자재지연) → 미배정 → BatchGroupCard + 사유 배지 + success toast
 *   S2: in_progress 상태 batch_group → 메뉴 aria-disabled + tooltip 노출
 *   S3: "계획으로 복원" 클릭 → refreshTasks 즉시 반영 + success toast
 *   S4: 409 충돌 → warning toast + 카드 유지
 *
 * 주의:
 *   - GanttTaskBlock 의 testid 는 `gantt-block-<taskId>` 형식이다 (태스크 6.1 플랜의 "task-block" 제네릭 명이 아님).
 *   - ContextMenu 의 "미배정으로 이동" 버튼은 `aria-disabled` + `title` 로 비활성/툴팁을 표현한다 (React `disabled` prop 아님).
 *   - 모달은 role="dialog" + aria-modal="true" 로 표시되며 같은 라벨의 submit 버튼을 가진다 → dialog scoping 필요.
 *   - BatchGroupCard 는 role="group", aria-label 이 "배치 묶음 ..." 로 시작한다.
 *   - S2/S4 는 PoC 범위에서 fixture 의존 (in_progress 데이터, 충돌 시드) 이므로 skip 허용.
 */

const TASK_BLOCK = '[data-testid^="gantt-block-"]';
const BATCH_CARD = '[role="group"][aria-label^="배치 묶음"]';

/**
 * 백엔드 /api/schedules/tasks 응답의 status 필드를 "scheduled" → "planned" 로
 * 치환하고 unassign POST 는 200 OK 로 가장한다.
 *
 * 왜 필요: 현재 백엔드는 status="scheduled" 로 저장하지만 프론트 store/UI 는
 * "planned" 만을 unassign 가능 상태로 판정하고 (ContextMenu.tsx:243,
 * scheduleStore.ts:962), 백엔드 unassign 라우트 또한 planned 외 상태를 400 으로
 * 거부한다 (plan_pipeline.py). 이 시맨틱 mismatch 는 Phase 6 범위가 아니라
 * 별도 수정 대상이므로 E2E 에서는 route 레벨에서 맞춰주어 UI 플로우만 순수 검증한다.
 *
 * TODO(backend/data): status 를 planned 로 통일하거나 양쪽 판정 조건을
 * {planned, scheduled} 모두 허용하도록 확장.
 */
async function stubPlannedStatus(page: Page): Promise<void> {
  await page.route("**/api/schedules/tasks**", async (route) => {
    const res = await route.fetch();
    const body = await res.json().catch(() => null);
    if (!Array.isArray(body)) {
      await route.fulfill({ response: res });
      return;
    }
    const patched = body.map((t: Record<string, unknown>) =>
      t.status === "scheduled" ? { ...t, status: "planned" } : t,
    );
    await route.fulfill({
      status: res.status(),
      contentType: "application/json",
      body: JSON.stringify(patched),
    });
  });

  // unassign 성공 응답 — 실제 백엔드는 planned 외 400 이므로 프론트 E2E 에서는
  // 200 을 가장해 UI 낙관적 업데이트/토스트 경로만 검증한다.
  await page.route("**/api/pipeline/batch-group/**/unassign", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true }),
    });
  });
}

/** scheduler 페이지 진입 + 초기 렌더 대기. */
async function gotoScheduler(page: Page): Promise<void> {
  await stubPlannedStatus(page);
  await page.goto("/scheduler");
  await page.waitForLoadState("networkidle");
}

/**
 * 하단 "미배정 작업" CollapsiblePanel 을 확장. 초기 로드 시 unscheduled order 가
 * 없으면 defaultExpanded=false 로 카드가 숨겨지므로, batch_group 을 추가한 뒤
 * 패널 헤더를 클릭해 확장해야 BatchGroupCard 가 가시화된다.
 *
 * 이미 확장되어 있으면 BatchGroupCard 가 즉시 보이므로 건너뛴다.
 */
async function expandUnassignedPanel(page: Page): Promise<void> {
  const anyCard = page.locator(BATCH_CARD).first();
  if (await anyCard.isVisible().catch(() => false)) return;
  const header = page.locator("text=미배정 작업").first();
  await header.click({ force: true });
}

/**
 * Gantt 에서 unassign 가능한 (planned + non-WIP) 블록을 찾아 우클릭 → 미배정 →
 * "자재지연" 사유로 확정. S1, S3, S4 공용.
 *
 * 왜 탐색이 필요: dev DB 의 첫 블록이 in_progress / completed / WIP-matched 면
 * ContextMenu 의 "미배정으로 이동" 항목이 aria-disabled="true" 가 되어 클릭해도
 * 모달이 뜨지 않는다. 최대 N개 블록을 순회해 aria-disabled="false" 인 첫 블록을
 * 사용한다. 모든 블록이 잠긴 경우 false 반환 → caller 가 skip 처리.
 */
async function unassignFirstEligibleBlockWithMaterialDelay(
  page: Page,
): Promise<boolean> {
  const blocks = page.locator(TASK_BLOCK);
  await expect(blocks.first()).toBeVisible({ timeout: 15_000 });
  const count = await blocks.count();
  const sample = Math.min(count, 20);

  for (let i = 0; i < sample; i++) {
    await blocks.nth(i).scrollIntoViewIfNeeded();
    // contextmenu 이벤트를 직접 dispatch 해서 hover 팝오버/DnD wrapper 간섭을 우회.
    // 왜: click({button:"right"}) 은 mouseenter 로 팝오버를 띄우고 그 상태에서
    // hit-test 가 팝오버로 빗나가는 회귀가 있었다. handleContextMenu 는 React
    // event 로부터 clientX/Y 만 읽으므로 dispatch 된 MouseEvent 로도 충분하다.
    await blocks.nth(i).evaluate((el: HTMLElement) => {
      const rect = el.getBoundingClientRect();
      const ev = new MouseEvent("contextmenu", {
        bubbles: true,
        cancelable: true,
        clientX: rect.left + 10,
        clientY: rect.top + 10,
        button: 2,
      });
      el.dispatchEvent(ev);
    });
    const menuItem = page
      .getByRole("button", { name: "미배정으로 이동" })
      .first();
    if (!(await menuItem.isVisible().catch(() => false))) {
      await page.keyboard.press("Escape");
      continue;
    }
    const disabled = await menuItem.getAttribute("aria-disabled");
    if (disabled === "true") {
      // 잠긴 블록 (in_progress/completed/WIP) — 다음 블록 시도
      await page.keyboard.press("Escape");
      continue;
    }
    // force 이유: GanttTaskBlock 의 hover 시간 구성 팝오버가 Portal 로 body 에
    // 띄워져 있을 때 메뉴 클릭 hit-test 를 가로챌 수 있다.
    await menuItem.click({ force: true });

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible({ timeout: 3_000 });
    await dialog.locator('input[type="radio"][value="자재지연"]').check();
    await dialog
      .getByRole("button", { name: "미배정으로 이동" })
      .click({ force: true });
    return true;
  }
  return false;
}

test.describe("batch_group 미배정 & 복원 (Phase 6)", () => {
  test.beforeEach(async ({ page }) => {
    await gotoScheduler(page);
  });

  test("S1: 우클릭 → 자재지연 → 미배정 → BatchGroupCard + 배지 + success toast", async ({
    page,
  }) => {
    const ok = await unassignFirstEligibleBlockWithMaterialDelay(page);
    if (!ok) {
      test.skip(true, "unassign 가능한 planned 블록 없음 (dev DB 상태 의존)");
      return;
    }

    // 미배정 작업 패널은 order 가 없으면 defaultExpanded=false 이므로 헤더 클릭해 확장.
    await expandUnassignedPanel(page);

    // BatchGroupCard 가 나타나야 함 (aria-label 기반 selector)
    const card = page.locator(BATCH_CARD).first();
    await expect(card).toBeVisible({ timeout: 5_000 });
    // 사유 배지 — 카드의 aria-label 에 "사유: 자재지연" 포함
    await expect(card).toHaveAttribute("aria-label", /자재지연/);
    // 카드 내 "자재지연" 배지 텍스트 노출
    await expect(card).toContainText("자재지연");

    // Success toast — `<role="status">` 컨테이너 안에 "미배정으로 이동 (사유: 자재지연)" 메시지
    await expect(
      page.getByRole("status").filter({ hasText: /미배정으로 이동.*자재지연/ }),
    ).toBeVisible({ timeout: 3_000 });
  });

  test("S2: in_progress batch_group → 메뉴 aria-disabled + tooltip", async ({
    page,
  }) => {
    // Gantt 블록에 data-status 속성이 없으므로 fixture 판별 어렵다.
    // PoC: dev DB 에 in_progress task 가 있을 때만 검증, 없으면 skip.
    //
    // 우클릭 후 메뉴 아이템의 aria-disabled / title 을 읽어 판정하는 방식.
    // block 전체를 돌면서 첫 disabled 케이스를 탐색한다.
    const blocks = page.locator(TASK_BLOCK);
    const count = await blocks.count();
    if (count === 0) {
      test.skip(true, "Gantt 에 블록이 없어 S2 검증 불가");
      return;
    }

    let found = false;
    // 최대 10개 샘플 (전체 스캔은 느리고 과잉)
    const sample = Math.min(count, 10);
    for (let i = 0; i < sample; i++) {
      await blocks.nth(i).evaluate((el: HTMLElement) => {
        const rect = el.getBoundingClientRect();
        const ev = new MouseEvent("contextmenu", {
          bubbles: true,
          cancelable: true,
          clientX: rect.left + 10,
          clientY: rect.top + 10,
          button: 2,
        });
        el.dispatchEvent(ev);
      });
      const item = page
        .getByRole("button", { name: "미배정으로 이동" })
        .first();
      if (await item.isVisible().catch(() => false)) {
        const disabled = await item.getAttribute("aria-disabled");
        if (disabled === "true") {
          // tooltip 에 "진행중" 또는 "WIP" 문구가 포함돼야 함
          await expect(item).toHaveAttribute("title", /진행중인 공정|WIP 매칭/);
          found = true;
          // 메뉴 닫기 (다음 테스트 영향 방지)
          await page.keyboard.press("Escape");
          break;
        }
      }
      await page.keyboard.press("Escape");
    }

    if (!found) {
      test.skip(
        true,
        "dev DB 에 in_progress/WIP batch_group 이 없어 S2 검증 skip (fixture TODO)",
      );
    }
  });

  test("S3: 계획으로 복원 → 간트 즉시 반영 (refreshTasks) + success toast", async ({
    page,
  }) => {
    // 왜 route stub: 백엔드는 DB 에 해당 batch_group unassign 기록이 없으면
    // restore 시 404/400 이 날 수 있다. 본 시나리오는 UI 플로우(성공 토스트,
    // unscheduledItems 제거, refreshTasks 호출)를 검증하는 것이므로 200 응답을
    // 주입해 프론트 로직만 검증한다.
    await page.route(
      "**/api/pipeline/batch-group/**/restore",
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ok: true }),
        });
      },
    );

    // S1 을 선행으로 수행하여 BatchGroupCard 를 먼저 만들어둔다.
    const ok = await unassignFirstEligibleBlockWithMaterialDelay(page);
    if (!ok) {
      test.skip(true, "unassign 가능한 planned 블록 없음 (dev DB 상태 의존)");
      return;
    }

    await expandUnassignedPanel(page);

    const card = page.locator(BATCH_CARD).first();
    await expect(card).toBeVisible({ timeout: 5_000 });

    // 복원 버튼 — aria-label 로 조회 (텍스트는 "계획으로 복원")
    const restoreBtn = card.getByRole("button", {
      name: "묶음을 원래 자리로 복원",
    });
    await expect(restoreBtn).toBeVisible();
    await expect(restoreBtn).toBeEnabled();
    // dispatch 로 직접 click — 패널 확장 애니메이션 중 hit-test 가 흔들리고
    // viewport 바깥 판정이 나는 케이스를 우회한다. React onClick 은
    // synthetic event 로 감지하므로 dispatchEvent 로도 핸들러가 실행된다.
    await restoreBtn.evaluate((el: HTMLElement) => el.click());

    // 카드가 인박스에서 사라져야 함
    await expect(card).toBeHidden({ timeout: 10_000 });

    // Gantt 에 블록이 다시 보여야 함 (refreshTasks 후 서버 응답 반영)
    await expect(page.locator(TASK_BLOCK).first()).toBeVisible({
      timeout: 10_000,
    });

    // Success toast — "원래 자리로 복원 완료"
    await expect(
      page.getByRole("status").filter({ hasText: /복원 완료/ }),
    ).toBeVisible({ timeout: 3_000 });
  });

  test("S4: 409 충돌 → warning toast + 카드 유지", async ({ page }) => {
    // 409 충돌 유발은 서버 시드가 필요하다 (unassign 된 묶음의 원래 자리에 다른 task 가 겹치는 상태).
    // PoC 범위 밖 — route 가로채기로 409 를 강제 주입하여 UI 처리만 검증한다.
    //
    // 흐름: S1 으로 카드 생성 → restore 호출 시 409 응답 주입 →
    //       warning toast + 카드 유지 검증.
    const ok = await unassignFirstEligibleBlockWithMaterialDelay(page);
    if (!ok) {
      test.skip(true, "unassign 가능한 planned 블록 없음 (dev DB 상태 의존)");
      return;
    }

    await expandUnassignedPanel(page);

    const card = page.locator(BATCH_CARD).first();
    await expect(card).toBeVisible({ timeout: 5_000 });

    // restore API 를 가로채서 409 응답 강제
    await page.route(
      "**/api/pipeline/batch-group/**/restore",
      async (route) => {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "원래 자리에 다른 작업이 있습니다",
          }),
        });
      },
    );

    const restoreBtn = card.getByRole("button", {
      name: "묶음을 원래 자리로 복원",
    });
    await expect(restoreBtn).toBeEnabled();
    await restoreBtn.evaluate((el: HTMLElement) => el.click());

    // Warning toast — 스토어가 "원래 자리에 다른 작업이 있습니다..." 메시지를 띄움
    await expect(
      page.getByRole("status").filter({ hasText: /원래 자리에 다른 작업/ }),
    ).toBeVisible({ timeout: 3_000 });

    // 카드는 여전히 유지되어야 함 (409 path 에서는 unscheduledItems 제거 안 함)
    await expect(card).toBeVisible();
  });

  // ────────────────────────────────────────────────────────────────────
  // S5~S8: batch_group 드래그 시나리오
  // ────────────────────────────────────────────────────────────────────

  test("S5: 간트 블록 드래그 → inbox dropzone → Modal 사유 선택 → 미배정", async ({
    page,
  }) => {
    // 수정 모드 진입 (간트 편집 활성화 버튼)
    await page.getByRole("button", { name: /수정/ }).first().click();

    const block = page.locator(TASK_BLOCK).first();
    await expect(block).toBeVisible({ timeout: 10_000 });

    // drag 시작: block 가운데서 mouse down
    const bb = await block.boundingBox();
    if (!bb) throw new Error("간트 블록 bounding box 없음");
    await page.mouse.move(bb.x + bb.width / 2, bb.y + bb.height / 2);
    await page.mouse.down();

    // 미배정 작업 패널 헤더를 기준으로 dropzone 방향 이동
    // 작은 이동으로 @dnd-kit activation constraint 를 먼저 충족시킨 뒤 대상으로 이동
    await page.mouse.move(bb.x + bb.width / 2 + 5, bb.y + bb.height / 2 + 5);

    const inboxHeader = page.getByText("미배정 작업").first();
    const inboxBB = await inboxHeader.boundingBox();
    if (inboxBB) {
      await page.mouse.move(inboxBB.x + 40, inboxBB.y + 100, { steps: 8 });
    }

    // dropzone 배너 표시 기대
    await expect(
      page.getByText("여기에 놓으면 미배정 작업으로 이동합니다"),
    ).toBeVisible({ timeout: 3000 });
    await page.mouse.up();

    // Modal 열림
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible({ timeout: 5000 });
    await dialog.locator('input[type="radio"][value="자재지연"]').check();
    await dialog
      .getByRole("button", { name: /미배정.*이동|이동/ })
      .click({ force: true });

    // 미배정 패널 확장 후 BatchGroupCard + 자재지연 배지 노출 확인
    await expandUnassignedPanel(page);
    await expect(page.locator(BATCH_CARD).first()).toBeVisible({
      timeout: 5000,
    });
    await expect(page.getByText(/자재지연/).first()).toBeVisible();
  });

  test("S6: BatchGroupCard 드래그 → 원위치 즉시 복원 — isOrigin 자동 판정 v2b 이전엔 skip", async ({
    page,
  }) => {
    test.skip(true, "isOrigin 자동 판정은 v2b milestone 전까지 false 고정");
  });

  test("S7: BatchGroupCard → 다른 설비 레인 → Preview Modal → 적용 → Undo toast", async ({
    page,
  }) => {
    const card = page.locator(BATCH_CARD).first();
    if (!(await card.isVisible().catch(() => false))) {
      test.skip(true, "미배정 카드 없음 — S5 선행 필요");
      return;
    }

    await page.getByRole("button", { name: /수정/ }).first().click();

    const cardBB = await card.boundingBox();
    if (!cardBB) throw new Error("card bounding box 없음");
    await page.mouse.move(
      cardBB.x + cardBB.width / 2,
      cardBB.y + cardBB.height / 2,
    );
    await page.mouse.down();

    const row = page.locator('[data-type="equipment-row"]').first();
    const rowBB = await row.boundingBox();
    if (!rowBB) throw new Error("equipment-row bounding box 없음");
    // activation constraint 충족을 위한 작은 이동 후 대상 레인으로 이동
    await page.mouse.move(
      cardBB.x + cardBB.width / 2 + 5,
      cardBB.y + cardBB.height / 2 + 5,
    );
    await page.mouse.move(rowBB.x + 200, rowBB.y + rowBB.height / 2, {
      steps: 10,
    });
    await page.mouse.up();

    // Preview Modal 표시
    await expect(
      page.getByRole("dialog").getByText("재배치 미리보기"),
    ).toBeVisible({ timeout: 5000 });

    // 적용 버튼 클릭
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "적용" })
      .click();

    // Undo toast
    await expect(page.getByText(/재배치 적용 완료/)).toBeVisible({
      timeout: 5000,
    });
  });

  test("S8: BatchGroupCard → 공정 불일치 설비 → warning toast + 카드 유지", async ({
    page,
  }) => {
    // invalid_equipment 스텁 — restore-at 가 unresolved 만 반환하도록 가로챔
    await page.route(
      "**/api/pipeline/batch-group/*/restore-at",
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            batch_group: "x",
            task_positions: [],
            pushes: [],
            pulls: [],
            unresolved: [
              {
                task_id: "1",
                equipment_code: "",
                batch_label: "",
                reason: "invalid_equipment",
                detail: "mismatch",
              },
            ],
            request_id: "req-s8",
            can_auto_resolve: false,
            iter_count: 0,
            truncated: false,
          }),
        });
      },
    );

    const card = page.locator(BATCH_CARD).first();
    if (!(await card.isVisible().catch(() => false))) {
      test.skip(true, "미배정 카드 없음");
      return;
    }

    await page.getByRole("button", { name: /수정/ }).first().click();

    const cardBB = await card.boundingBox();
    if (!cardBB) throw new Error("card bounding box 없음");
    await page.mouse.move(
      cardBB.x + cardBB.width / 2,
      cardBB.y + cardBB.height / 2,
    );
    await page.mouse.down();

    const row = page.locator('[data-type="equipment-row"]').first();
    const rowBB = await row.boundingBox();
    if (!rowBB) throw new Error("row bounding box 없음");
    // activation constraint 충족을 위한 작은 이동 후 대상 레인으로 이동
    await page.mouse.move(
      cardBB.x + cardBB.width / 2 + 5,
      cardBB.y + cardBB.height / 2 + 5,
    );
    await page.mouse.move(rowBB.x + 200, rowBB.y + rowBB.height / 2, {
      steps: 10,
    });
    await page.mouse.up();

    // 공정 불일치 warning toast
    await expect(
      page.getByText(/선택한 설비가 공정 경로와 맞지 않습니다/),
    ).toBeVisible({ timeout: 5000 });
    // 카드는 여전히 DOM 에 존재해야 함
    await expect(card).toBeVisible();
  });
});
