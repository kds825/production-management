"""KBI Production Scheduler E2E Test - Playwright"""

from playwright.sync_api import sync_playwright
import os

RESULTS = []
SCREENSHOT_DIR = "/Users/jaewookim/Desktop/Project/KBI_PoC/frontend/e2e/screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def check(name, passed, detail=""):
    status = "✅ PASS" if passed else "❌ FAIL"
    RESULTS.append({"name": name, "passed": passed, "detail": detail})
    print(f"  {status}: {name}" + (f" — {detail}" if detail else ""))


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1920, "height": 1080})

    # ========================================
    print("\n=== 1. 페이지 로드 ===")
    # ========================================
    page.goto(
        "http://localhost:3000/scheduler", wait_until="networkidle", timeout=30000
    )
    page.wait_for_timeout(3000)
    page.screenshot(path=f"{SCREENSHOT_DIR}/01_initial_load.png", full_page=True)
    check("페이지 로드", page.title() != "")

    # ========================================
    print("\n=== 2. KBI 브랜딩 검증 ===")
    # ========================================
    # KBI GROUP 로고
    kbi_logo = page.locator('img[alt*="KBI"]').first
    check(
        "KBI GROUP 로고 존재", kbi_logo.is_visible() if kbi_logo.count() > 0 else False
    )

    # KBI COSMOLINK 로고
    cosmo_logo = page.locator(
        'img[alt*="COSMOLINK"], img[alt*="Cosmolink"], img[alt*="cosmolink"]'
    ).first
    check(
        "KBI COSMOLINK 로고 존재",
        cosmo_logo.is_visible() if cosmo_logo.count() > 0 else False,
    )

    # 헤더 텍스트
    header_text = page.locator("text=생산계획 스케줄러").first
    check(
        "헤더 타이틀 '생산계획 스케줄러'",
        header_text.is_visible() if header_text.count() > 0 else False,
    )

    # KBI Red (#C41230) 사용 확인
    html_content = page.content()
    check(
        "KBI Red (#C41230) 사용",
        "#C41230" in html_content
        or "#c41230" in html_content
        or "C41230" in html_content.upper(),
    )

    # KBI Brown (#4A2C2A) 사용 확인
    check(
        "KBI Brown (#4A2C2A) 사용",
        "#4A2C2A" in html_content
        or "#4a2c2a" in html_content
        or "4A2C2A" in html_content.upper(),
    )

    page.screenshot(path=f"{SCREENSHOT_DIR}/02_branding.png")

    # ========================================
    print("\n=== 3. 설비 행(Y축) 검증 ===")
    # ========================================
    equipment_names = [
        "54B0",
        "54BO",
        "T8B0",
        "T8BO",
        "30B0",
        "30BO",
        "AL6B0",
        "AL6BO",
        "44B0",
        "44BO",
        "CV#1",
        "CV #1",
        "CV_1",
        "CV#2",
        "CV #2",
        "CV_2",
        "12B0",
        "12BO",
        "4B0",
        "4BO",
        "T/P",
        "TP",
        "A100",
        "B100",
        "A150",
        "A120",
    ]

    found_equipment = []
    for name in equipment_names:
        locator = page.locator(f'text="{name}"')
        if locator.count() > 0:
            found_equipment.append(name)

    # Also try broader search
    body_text = page.locator("body").inner_text()
    for name in ["54B", "CV", "A100", "A150", "T/P"]:
        if name in body_text and name not in found_equipment:
            found_equipment.append(name)

    check(
        "설비 행 표시 (최소 5개 이상)",
        len(found_equipment) >= 5,
        f"발견: {found_equipment[:10]}",
    )
    page.screenshot(path=f"{SCREENSHOT_DIR}/03_equipment_rows.png", full_page=True)

    # ========================================
    print("\n=== 4. 작업 추가 버튼 검증 ===")
    # ========================================
    add_btn = page.locator(
        'button:has-text("작업 추가"), button:has-text("작업"), button:has-text("추가")'
    ).first
    has_add_btn = add_btn.count() > 0 and add_btn.is_visible()
    check("'작업 추가' 버튼 존재", has_add_btn)

    if has_add_btn:
        add_btn.click()
        page.wait_for_timeout(1000)
        page.screenshot(path=f"{SCREENSHOT_DIR}/04_add_task_modal.png")

        # TaskFormModal 확인
        modal = page.locator('[role="dialog"], .modal, div:has-text("기본 정보")').first
        modal_visible = modal.count() > 0 and modal.is_visible()
        check("TaskFormModal 팝업 열림", modal_visible)

        if modal_visible:
            # 필드 확인
            has_equipment_field = (
                page.locator('text=설비, label:has-text("설비")').count() > 0
            )
            has_date_field = (
                page.locator('input[type="date"], input[type="datetime-local"]').count()
                > 0
            )
            check("TaskFormModal에 설비 필드 존재", has_equipment_field)
            check("TaskFormModal에 날짜 필드 존재", has_date_field)

            # 확인/취소 버튼
            confirm_btn = page.locator(
                'button:has-text("확인"), button:has-text("OK")'
            ).first
            cancel_btn = page.locator(
                'button:has-text("취소"), button:has-text("Cancel")'
            ).first
            check("확인 버튼 존재", confirm_btn.count() > 0)
            check("취소 버튼 존재", cancel_btn.count() > 0)

            # 닫기
            if cancel_btn.count() > 0:
                cancel_btn.click()
                page.wait_for_timeout(500)

        page.screenshot(path=f"{SCREENSHOT_DIR}/04b_after_modal_close.png")

    # ========================================
    print("\n=== 5. ViewFilter 검증 ===")
    # ========================================
    filter_area = page.locator('text=전체, button:has-text("전체")').first
    check("ViewFilter '전체' 버튼 존재", filter_area.count() > 0)

    process_filter = page.locator('text=공정별, button:has-text("공정별")').first
    check("ViewFilter '공정별' 필터 존재", process_filter.count() > 0)

    voltage_filter = page.locator(
        'text=고압, text=저압, button:has-text("고압"), button:has-text("저압")'
    ).first
    check("ViewFilter 고압/저압 필터 존재", voltage_filter.count() > 0)

    page.screenshot(path=f"{SCREENSHOT_DIR}/05_view_filter.png")

    # ========================================
    print("\n=== 6. OrderInbox 검증 ===")
    # ========================================
    inbox = page.locator("text=수주 Inbox, text=Inbox, text=미배정, text=수주").first
    check("OrderInbox 패널 존재", inbox.count() > 0)

    # 수주 카드 확인
    order_cards = page.locator(
        '[draggable="true"], .order-card, div:has-text("TFR-CV")'
    ).all()
    check(
        "OrderInbox에 수주 카드 표시",
        len(order_cards) > 0,
        f"카드 수: {len(order_cards)}",
    )

    page.screenshot(path=f"{SCREENSHOT_DIR}/06_order_inbox.png")

    # ========================================
    print("\n=== 7. ConstraintAlert 검증 ===")
    # ========================================
    constraint_panel = page.locator(
        "text=제약조건, text=제약, text=검증, text=Constraint"
    ).first
    check("ConstraintAlert 패널 존재", constraint_panel.count() > 0)

    page.screenshot(path=f"{SCREENSHOT_DIR}/07_constraint_alert.png")

    # ========================================
    print("\n=== 8. 우클릭 컨텍스트 메뉴 검증 ===")
    # ========================================
    # 스케줄러 영역에서 우클릭
    scheduler_area = page.locator(
        '[style*="position"], .scheduler, .timeline, div[class*="scheduler"]'
    ).first
    if scheduler_area.count() > 0:
        scheduler_area.click(button="right", position={"x": 300, "y": 100})
        page.wait_for_timeout(1000)
        page.screenshot(path=f"{SCREENSHOT_DIR}/08_context_menu.png")

        ctx_menu = page.locator("text=작업 추가, text=수정, text=삭제").first
        check("우클릭 컨텍스트 메뉴 표시", ctx_menu.count() > 0)

        # 메뉴 닫기
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    else:
        check("우클릭 컨텍스트 메뉴 표시", False, "스케줄러 영역을 찾을 수 없음")

    # ========================================
    print("\n=== 9. 전체 레이아웃 스크린샷 ===")
    # ========================================
    page.screenshot(path=f"{SCREENSHOT_DIR}/09_full_layout.png", full_page=True)

    # ========================================
    print("\n=== 10. API 연동 검증 ===")
    # ========================================
    # Backend API 직접 호출
    api_response = page.evaluate("""
        async () => {
            try {
                const eq = await fetch('http://localhost:8000/api/equipment');
                const eqData = await eq.json();
                const sched = await fetch('http://localhost:8000/api/schedules/tasks');
                const schedData = await sched.json();
                return { equipment: eqData.length, tasks: schedData.length, success: true };
            } catch(e) {
                return { error: e.message, success: false };
            }
        }
    """)

    if api_response.get("success"):
        check(
            "API 설비 데이터 로드",
            api_response["equipment"] >= 10,
            f"설비 수: {api_response['equipment']}",
        )
        check(
            "API 스케줄 데이터 로드",
            api_response["tasks"] >= 0,
            f"작업 수: {api_response['tasks']}",
        )
    else:
        check("API 연동", False, api_response.get("error", "unknown"))

    # ========================================
    print("\n=== 11. PoC 버전 표시 검증 ===")
    # ========================================
    poc_badge = page.locator("text=PoC, text=v0.1").first
    check("PoC 버전 배지 표시", poc_badge.count() > 0)

    # ========================================
    # 최종 결과 요약
    # ========================================
    browser.close()

    print("\n" + "=" * 60)
    print("=== 최종 결과 요약 ===")
    print("=" * 60)

    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["passed"])
    failed = sum(1 for r in RESULTS if not r["passed"])

    print(f"\n총 {total}개 테스트: ✅ {passed} PASS / ❌ {failed} FAIL\n")

    if failed > 0:
        print("--- 실패 항목 ---")
        for r in RESULTS:
            if not r["passed"]:
                print(
                    f"  ❌ {r['name']}" + (f" — {r['detail']}" if r["detail"] else "")
                )

    print(f"\n스크린샷 저장: {SCREENSHOT_DIR}/")
    print("=" * 60)
