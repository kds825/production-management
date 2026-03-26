"""KBI Production Scheduler — Final Playwright Verification"""

from playwright.sync_api import sync_playwright
import os

RESULTS = []
DIR = "/Users/jaewookim/Desktop/Project/KBI_PoC/frontend/e2e/screenshots/final"
os.makedirs(DIR, exist_ok=True)


def check(name, passed, detail=""):
    s = "PASS" if passed else "FAIL"
    RESULTS.append((name, passed, detail))
    print(
        f"  {'✅' if passed else '❌'} {s}: {name}" + (f" — {detail}" if detail else "")
    )


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1920, "height": 1080})

    print("\n=== 1. 페이지 로드 ===")
    page.goto(
        "http://localhost:3000/scheduler", wait_until="networkidle", timeout=30000
    )
    page.wait_for_timeout(3000)
    page.screenshot(path=f"{DIR}/01_full.png", full_page=True)
    check("페이지 로드", "생산계획" in page.title() or page.url.endswith("/scheduler"))

    print("\n=== 2. KBI 브랜딩 ===")
    body = page.content()
    check("KBI 로고 img 태그", page.locator('img[alt*="KBI"]').count() > 0)
    check("생산계획 스케줄러 텍스트", "생산계획" in page.locator("body").inner_text())
    check("KBI Red 색상", "C41230" in body.upper() or "c41230" in body)
    page.screenshot(
        path=f"{DIR}/02_header.png", clip={"x": 0, "y": 0, "width": 1920, "height": 60}
    )

    print("\n=== 3. 설비 Y축 (세로) ===")
    body_text = page.locator("body").inner_text()
    equip_found = [
        n
        for n in ["54B0", "CV#", "A100", "A150", "T/P", "12B0", "4B0"]
        if n in body_text
    ]
    check("설비명 Y축 표시", len(equip_found) >= 4, f"발견: {equip_found}")

    print("\n=== 4. SVG 아이콘 (이모지 아님) ===")
    svg_icons = page.locator("svg").count()
    emoji_check = any(e in body for e in ["🔧", "🔄", "⚡", "🔌", "📐", "🔗", "🛡️"])
    check("SVG 아이콘 사용", svg_icons > 5, f"SVG 수: {svg_icons}")
    check("이모지 미사용", not emoji_check)

    print("\n=== 5. 버튼 레이아웃 ===")
    add_btn = page.locator('button:has-text("작업 추가")').first
    edit_btn = page.locator(
        'button:has-text("수정하기"), button:has-text("저장하기")'
    ).first
    check("작업 추가 버튼", add_btn.count() > 0)
    check("수정하기/저장하기 버튼", edit_btn.count() > 0)
    page.screenshot(
        path=f"{DIR}/05_buttons.png",
        clip={"x": 1200, "y": 0, "width": 720, "height": 60},
    )

    print("\n=== 6. 줌 컨트롤 ===")
    zoom_btns = page.locator(
        'button:has-text("주"), button:has-text("일"), button:has-text("시간")'
    )
    check(
        "줌 프리셋 (주/일/시간)",
        zoom_btns.count() >= 3,
        f"버튼 수: {zoom_btns.count()}",
    )
    # +/- 버튼 확인 (title="확대", title="축소")
    zoom_in = page.locator('button[title="확대"]')
    zoom_out = page.locator('button[title="축소"]')
    check("확대(+) 버튼", zoom_in.count() > 0)
    check("축소(-) 버튼", zoom_out.count() > 0)
    page.screenshot(path=f"{DIR}/06_zoom.png")

    print("\n=== 7. ViewFilter ===")
    check("전체 필터", "전체" in body_text)
    check("공정별 필터", "공정별" in body_text)
    check("고압/저압 필터", "고압" in body_text or "저압" in body_text)
    page.screenshot(
        path=f"{DIR}/07_filter.png", clip={"x": 0, "y": 55, "width": 1920, "height": 50}
    )

    print("\n=== 8. OrderInbox ===")
    check("수주 패널", "수주" in body_text or "미배정" in body_text)
    page.screenshot(
        path=f"{DIR}/08_inbox.png", clip={"x": 0, "y": 100, "width": 300, "height": 600}
    )

    print("\n=== 9. 수정하기 클릭 → 편집 모드 ===")
    if edit_btn.count() > 0:
        edit_btn.click()
        page.wait_for_timeout(500)
        page.screenshot(path=f"{DIR}/09_edit_mode.png")
        # 편집 모드에서 "저장하기" 버튼이 보여야 함
        save_btn = page.locator('button:has-text("저장하기")')
        check("편집 모드 → 저장하기 표시", save_btn.count() > 0)
        # 다시 저장하기 클릭하여 읽기 모드로
        if save_btn.count() > 0:
            save_btn.click()
            page.wait_for_timeout(1000)
            check(
                "저장 → 읽기 모드 복귀",
                page.locator('button:has-text("수정하기")').count() > 0,
            )
    else:
        check("편집 모드 테스트", False, "수정하기 버튼 없음")

    print("\n=== 10. 작업 추가 모달 ===")
    # 먼저 수정 모드로 전환
    edit_btn2 = page.locator('button:has-text("수정하기")').first
    if edit_btn2.count() > 0:
        edit_btn2.click()
        page.wait_for_timeout(300)
    add_btn2 = page.locator('button:has-text("작업 추가")').first
    if add_btn2.count() > 0 and add_btn2.is_enabled():
        add_btn2.click()
        page.wait_for_timeout(1000)
        page.screenshot(path=f"{DIR}/10_modal.png")
        modal_text = page.locator("body").inner_text()
        check("모달 열림 (기본 정보 탭)", "기본" in modal_text or "설비" in modal_text)
        # 취소
        cancel = page.locator('button:has-text("취소")').first
        if cancel.count() > 0:
            cancel.click()
            page.wait_for_timeout(300)
    else:
        check("모달 테스트", False, "작업 추가 비활성")

    print("\n=== 11. API 연동 ===")
    api = page.evaluate("""async () => {
        try {
            const r1 = await fetch('http://localhost:8000/api/equipment');
            const d1 = await r1.json();
            const r2 = await fetch('http://localhost:8000/api/schedules/tasks');
            const d2 = await r2.json();
            return {ok: true, eq: d1.length, tasks: d2.length};
        } catch(e) { return {ok: false, err: e.message}; }
    }""")
    if api.get("ok"):
        check("API 설비", api["eq"] >= 10, f"{api['eq']}대")
        check("API 작업", api["tasks"] >= 0, f"{api['tasks']}개")
    else:
        check("API 연동", False, api.get("err"))

    print("\n=== 12. 최종 전체 스크린샷 ===")
    page.screenshot(path=f"{DIR}/12_final_full.png", full_page=True)

    browser.close()

    print("\n" + "=" * 60)
    total = len(RESULTS)
    passed = sum(1 for _, p, _ in RESULTS if p)
    failed = sum(1 for _, p, _ in RESULTS if not p)
    print(f"최종 결과: {total}개 테스트 — ✅ {passed} PASS / ❌ {failed} FAIL")
    if failed:
        print("\n실패 항목:")
        for n, p, d in RESULTS:
            if not p:
                print(f"  ❌ {n}" + (f" — {d}" if d else ""))
    print(f"\n스크린샷: {DIR}/")
    print("=" * 60)
