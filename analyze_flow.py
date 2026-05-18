# -*- coding: utf-8 -*-
import sys
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

import time
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PWTimeout
import config

EMAIL       = config.EMAIL
PASSWORD    = config.PASSWORD
PRODUCT_URL = "https://shop.bmw.co.kr/online/oom/OMG4223020"
SAVE_DIR    = Path(__file__).parent / "screenshots"
SAVE_DIR.mkdir(exist_ok=True)

if not EMAIL or not PASSWORD:
    raise RuntimeError("BMW_EMAIL/BMW_PASSWORD 값을 .env 또는 환경변수에 설정하세요.")

# ─── 유틸 ───────────────────────────────────────────────────────────────────

def shot(page: Page, name: str):
    p = SAVE_DIR / f"{name}.png"
    try:
        page.screenshot(path=str(p), full_page=True)
        print(f"  [screenshot] {name}.png")
    except Exception:
        pass

def try_click(page: Page, selectors: list, label: str, timeout=5000) -> bool:
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=timeout)
            page.click(sel)
            print(f"  [OK] click: {label} ({sel})")
            return True
        except Exception:
            continue
    print(f"  [SKIP] not found: {label}")
    return False

def dump_click_targets(page: Page, name: str):
    try:
        items = page.evaluate("""
            Array.from(document.querySelectorAll('button,a,label,[role=button],li')).map((el, idx) => {
                const rect = el.getBoundingClientRect();
                return {
                    idx,
                    tag: el.tagName,
                    id: el.id || '',
                    cls: String(el.className || '').slice(0, 80),
                    text: (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 120),
                    visible: rect.width > 10 && rect.height > 10 && getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden'
                };
            }).filter(x => x.visible && x.text)
        """)
        out = SAVE_DIR / f"{name}.json"
        out.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [debug] clickable dump: {out.name} ({len(items)}개)")
    except Exception as e:
        print(f"  [debug] clickable dump failed: {e}")

def click_pass_qr_debug(page: Page) -> bool:
    try:
        clicked = page.evaluate("""
            () => {
                const visible = el => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 20 && rect.height > 20 &&
                        style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = el => (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
                const candidates = Array.from(document.querySelectorAll('button.mobileCertMethodCheck, button, a, label, [role=button], li'));
                const target = candidates.find(el => {
                    const text = textOf(el);
                    return visible(el) && /QR\\s*코드|QR/.test(text) &&
                        !text.includes('PASS 인증') &&
                        !text.includes('문자') && !text.includes('SMS') &&
                        !text.includes('다음') && !text.includes('확인');
                });
                if (!target) return '';
                target.scrollIntoView({block:'center', inline:'center'});
                target.click();
                return textOf(target) || target.id || target.className || 'QR target';
            }
        """)
        if clicked:
            print(f"  [OK] QR 코드 선택: {clicked}")
            return True
    except Exception as e:
        print(f"  [ERR] QR 코드 선택 JS 실패: {e}")
    print("  [SKIP] QR 코드 선택 대상 없음")
    return False

def wait_qr_screen(page: Page, timeout=8000) -> bool:
    try:
        page.wait_for_function(
            """() => {
                const text = document.body?.innerText || '';
                if (text.includes('인증방법을 선택')) return false;
                return text.includes('QR코드 인증') ||
                    text.includes('QR 코드 인증') ||
                    text.includes('QR코드를') ||
                    text.includes('QR 코드를') ||
                    text.includes('PASS 앱으로') ||
                    document.querySelector('canvas, img[src*="qr" i], [class*="qr" i], [id*="qr" i]');
            }""",
            timeout=timeout,
        )
        return True
    except Exception:
        return False

def try_fill(page: Page, selectors: list, value: str, label: str, timeout=5000) -> bool:
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=timeout)
            page.fill(sel, value)
            print(f"  [OK] fill: {label} = {value[:20]}")
            return True
        except Exception:
            continue
    print(f"  [SKIP] not found: {label}")
    return False

def wait_url(page: Page, pattern: str, timeout=30000) -> bool:
    try:
        page.wait_for_url(f"**{pattern}**", timeout=timeout)
        print(f"  [URL] {page.url[:70]}")
        return True
    except PWTimeout:
        print(f"  [!] timeout waiting for {pattern}, current: {page.url[:70]}")
        return False

# ─── 구매 폼 드롭다운 수집 및 사용자 입력 ──────────────────────────────────

def handle_purchase_form(page: Page):
    print("\n" + "="*55)
    print("[구매 폼] 전체 필드 스캔")
    print("="*55)
    time.sleep(2)
    shot(page, "11_purchase_form")

    # select 드롭다운 추출
    selects = page.evaluate("""
        Array.from(document.querySelectorAll('select')).map(el => {
            const label = document.querySelector('label[for="'+el.id+'"]')?.innerText?.trim()
                       || el.closest('[class*="form"],[class*="field"],[class*="group"]')
                           ?.querySelector('label,span,dt')?.innerText?.trim()
                       || el.name || el.id || '(label없음)';
            return {
                id:      el.id,
                name:    el.name,
                label:   label,
                options: Array.from(el.options).map(o => ({text: o.text.trim(), value: o.value}))
                             .filter(o => o.value !== '')
            };
        })
    """)

    # text/number/tel 입력 필드 추출
    inputs = page.evaluate("""
        Array.from(document.querySelectorAll(
            'input[type=text], input[type=number], input[type=tel], input[type=email], textarea'
        )).filter(el => !el.readOnly && !el.disabled).map(el => {
            const label = document.querySelector('label[for="'+el.id+'"]')?.innerText?.trim()
                       || el.closest('[class*="form"],[class*="field"],[class*="group"]')
                           ?.querySelector('label,span,dt')?.innerText?.trim()
                       || el.placeholder || el.name || el.id || '(label없음)';
            return { id: el.id, name: el.name, type: el.type, label: label, placeholder: el.placeholder };
        })
    """)

    # 체크박스/라디오
    checks = page.evaluate("""
        Array.from(document.querySelectorAll('input[type=checkbox], input[type=radio]'))
            .filter(el => !el.disabled).map(el => {
                const label = document.querySelector('label[for="'+el.id+'"]')?.innerText?.trim()
                           || el.closest('label')?.innerText?.trim()
                           || el.name || el.id;
                return { id: el.id, name: el.name, type: el.type, label: label, checked: el.checked };
            })
    """)

    print(f"\n  [드롭다운 {len(selects)}개] [텍스트 {len(inputs)}개] [체크/라디오 {len(checks)}개]")

    form_data = {}  # {selector: value}

    # ── 드롭다운 처리 ──
    for s in selects:
        sel_attr = f"select[id='{s['id']}']" if s['id'] else f"select[name='{s['name']}']"
        print(f"\n  [SELECT] {s['label']}")
        for i, opt in enumerate(s['options']):
            print(f"    {i+1}. {opt['text']}")
        while True:
            raw = input(f"  번호 선택 (1~{len(s['options'])}): ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(s['options']):
                chosen = s['options'][int(raw)-1]
                form_data[sel_attr] = ("select", chosen['value'])
                print(f"  -> {chosen['text']}")
                break
            print("  올바른 번호를 입력하세요.")

    # ── 텍스트 입력 처리 ──
    for f in inputs:
        sel_attr = f"#{f['id']}" if f['id'] else f"[name='{f['name']}']"
        hint = f" (예: {f['placeholder']})" if f['placeholder'] else ""
        val = input(f"\n  [INPUT] {f['label']}{hint}: ").strip()
        if val:
            form_data[sel_attr] = ("fill", val)

    # ── 체크박스 처리 ──
    for c in checks:
        sel_attr = f"#{c['id']}" if c['id'] else f"[name='{c['name']}']"
        cur = "[현재 체크됨]" if c['checked'] else "[현재 해제됨]"
        yn = input(f"\n  [CHECK] {c['label']} {cur} -> 체크하시겠습니까? (y/n): ").strip().lower()
        form_data[sel_attr] = ("check", yn == 'y')

    # ── 입력 실행 ──
    print("\n[입력 적용 중...]")
    for selector, (action, value) in form_data.items():
        try:
            if action == "select":
                page.select_option(selector, value=value)
            elif action == "fill":
                page.fill(selector, value)
            elif action == "check":
                if value:
                    page.check(selector)
                else:
                    page.uncheck(selector)
            print(f"  [OK] {selector} = {str(value)[:30]}")
        except Exception as e:
            print(f"  [ERR] {selector}: {e}")

    shot(page, "12_form_filled")

    print("\n[폼 입력 완료]")
    confirm = input("  제출하시겠습니까? (y/n): ").strip().lower()
    if confirm == 'y':
        submitted = try_click(page, [
            "button[type='submit']", "input[type='submit']",
            ".btn-submit", ".submit-btn", ".order-btn",
            "button:has-text('구매')", "button:has-text('결제')", "button:has-text('신청')"
        ], "제출 버튼")
        if submitted:
            time.sleep(3)
            shot(page, "13_submitted")

# ─── 본인인증 처리 ──────────────────────────────────────────────────────────

def handle_verification(page: Page, ctx: BrowserContext):
    """본인인증 팝업 자동 처리 -> SKT -> QR 화면까지"""
    print("\n[본인인증 자동 처리 시작]")
    shot(page, "07_before_verify")

    # 본인인증 버튼 클릭 (인라인 or 팝업)
    popup_future = ctx.expect_page()

    clicked = try_click(page, [
        "button:has-text('본인인증')", "a:has-text('본인인증')",
        "button:has-text('인증하기')", "a:has-text('인증하기')",
        ".btn-cert", ".cert-btn", "#btnCert", "#btn_cert",
        "[class*='identity']", "[class*='verify']",
        "button:has-text('확인')",
    ], "본인인증 버튼", timeout=4000)

    # 팝업 대기 (최대 5초)
    popup = None
    try:
        popup = popup_future.value
        popup.wait_for_load_state("domcontentloaded", timeout=8000)
        print(f"  [팝업] {popup.url[:60]}")
        shot(popup, "08_popup_open")
    except Exception:
        # 팝업 없이 인라인으로 처리되는 경우
        print("  [팝업 없음] 인라인 처리")
        popup = page

    time.sleep(1.5)
    shot(popup, "09_carrier_select")

    # 통신사 선택 - SKT
    skt_clicked = try_click(popup, [
        "a:has-text('SKT')", "button:has-text('SKT')",
        "label:has-text('SKT')", "#btn_skt", ".btn_skt",
        "[id*='skt']", "[class*='skt']",
        "a:has-text('에스케이')", "a:has-text('SK텔레콤')",
        "li:has-text('SKT')", "td:has-text('SKT')",
    ], "SKT 선택", timeout=5000)

    time.sleep(1.5)
    shot(popup, "10_after_skt")
    dump_click_targets(popup, "10_after_skt_clickables")

    # QR 인증 선택
    qr_clicked = click_pass_qr_debug(popup)
    if not qr_clicked:
        qr_clicked = try_click(popup, [
            "button.mobileCertMethodCheck:has-text('QR')",
            "button:has-text('QR코드')", "a:has-text('QR코드')",
            "button:has-text('QR 코드')", "a:has-text('QR 코드')",
            "#btn_qr", ".btn_qr",
        ], "QR 코드 선택", timeout=5000)

    time.sleep(2)
    shot(popup, "11_qr_screen")
    if not wait_qr_screen(popup, timeout=5000):
        dump_click_targets(popup, "11_qr_not_ready_clickables")
        print("  [WARN] QR 화면 전환 확인 실패. screenshots/10_after_skt_clickables.json 확인 필요")
    print("\n  [QR 화면 스크린샷 저장 완료] -> screenshots/11_qr_screen.png")
    print("  [!] 스마트폰으로 QR 코드를 스캔하여 인증을 완료하세요.")

    # QR 인증 완료 대기 (팝업 닫힘 or URL 변경)
    print("  QR 인증 완료를 기다립니다...")
    for _ in range(60):  # 최대 60초
        time.sleep(1)
        try:
            if popup != page:
                # 팝업이 닫혔는지 확인
                popup.url  # 접근 가능하면 아직 열림
            break
        except Exception:
            print("  [OK] 팝업 닫힘 감지 - 인증 완료")
            break

    time.sleep(2)
    shot(page, "12_after_verification")

    # 인증 후 구매 폼으로 이동
    print("\n  인증 후 페이지 확인 중...")
    return popup != page  # 팝업이었으면 True

# ─── 메인 ───────────────────────────────────────────────────────────────────

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            slow_mo=150,
            args=["--start-maximized"],
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
        )
        page = ctx.new_page()

        # 1. 제품 페이지
        print("[1] 제품 페이지...")
        page.goto(PRODUCT_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=20000)
        shot(page, "01_product")

        # 2. 구매 버튼
        print("[2] 구매 버튼 클릭...")
        page.evaluate("document.querySelector('.revervation_btn')?.scrollIntoView({block:'center'})")
        time.sleep(0.3)
        page.evaluate(
            "document.querySelector('.revervation_btn')?.dispatchEvent("
            "new MouseEvent('click',{bubbles:true,cancelable:true,view:window}))"
        )
        time.sleep(1.5)

        # 로그인 모달
        if page.query_selector(".cBtn"):
            page.click(".cBtn")
            time.sleep(2)

        # 3. 로그인
        print("[3] 로그인...")
        try:
            page.wait_for_url("**customer.bmwgroup.com**", timeout=12000)
        except PWTimeout:
            pass

        try_fill(page, ["#username"], EMAIL, "이메일")
        try_click(page, [".custom-button.primary"], "계속")
        time.sleep(2)
        try_fill(page, ["#password"], PASSWORD, "비밀번호")
        try_click(page, [".custom-button.primary"], "로그인")
        time.sleep(2)
        shot(page, "04_after_pw")

        # 4. CAPTCHA (수동) - 유일한 수동 개입 지점
        print("\n" + "="*55)
        print("[!] CAPTCHA 가 보이면 브라우저에서 직접 해결하세요.")
        print("[!] 로그인까지 완료되면 Enter 를 누르세요.")
        print("="*55)
        input("  Enter > ")

        shot(page, "05_after_captcha")

        # BMW shop 복귀 대기
        if "shop.bmw.co.kr" not in page.url:
            print("[대기] shop.bmw.co.kr 복귀...")
            wait_url(page, "shop.bmw.co.kr", timeout=30000)

        shot(page, "06_after_login")
        print(f"  현재 URL: {page.url}")

        # 로그인 후 제품 페이지로 재진입 필요한 경우
        if "/oom/" not in page.url and "/oem/" not in page.url:
            print("[재진입] 구매 페이지로 이동...")
            page.goto(PRODUCT_URL, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=20000)
            page.evaluate("document.querySelector('.revervation_btn')?.scrollIntoView({block:'center'})")
            time.sleep(0.3)
            page.evaluate(
                "document.querySelector('.revervation_btn')?.dispatchEvent("
                "new MouseEvent('click',{bubbles:true,cancelable:true,view:window}))"
            )
            time.sleep(2)

        # 5. 본인인증 자동 처리
        page_text = page.evaluate("document.body.innerText || ''")
        needs_verify = any(k in page_text for k in ["본인인증", "인증하기", "휴대폰 인증"])

        if needs_verify:
            handle_verification(page, ctx)
        else:
            print("[본인인증 없음 - 바로 구매 폼 진행]")
            shot(page, "07_no_verify")

        # 6. 구매 폼 처리
        handle_purchase_form(page)

        print("\n[완료] 모든 단계가 끝났습니다.")
        input("브라우저 닫으려면 Enter...")
        browser.close()


if __name__ == "__main__":
    main()
