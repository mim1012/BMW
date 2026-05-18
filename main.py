# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

"""
BMW Online Exclusive 구매 자동화
플로우: 제품 페이지 → 구매하기 → 로그인(이메일/PW 자동) → CAPTCHA(수동)
        → 본인인증/SKT/QR(수동) → 구매 폼(CLI 입력) → 제출
"""

import sys
import time
import os
from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout
import config


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def ask(prompt: str, default: str = "") -> str:
    val = input(f"  {prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    return val if val else default


def safe_fill(page: Page, selector: str, value: str, label: str = ""):
    try:
        page.wait_for_selector(selector, timeout=5_000)
        page.fill(selector, value)
        print(f"    ✓ {label or selector} 입력 완료")
    except PWTimeout:
        print(f"    ✗ {label or selector} 필드를 찾지 못했습니다 (스킵)")


def safe_click(page: Page, selector: str, label: str = ""):
    try:
        page.wait_for_selector(selector, timeout=5_000)
        page.click(selector)
        print(f"    ✓ {label or selector} 클릭")
    except PWTimeout:
        print(f"    ✗ {label or selector} 를 찾지 못했습니다 (스킵)")


def wait_for_url_change(page: Page, expected_substr: str, timeout: int = 30_000):
    print(f"    ⏳ URL에 '{expected_substr}' 포함될 때까지 대기 중...")
    page.wait_for_url(f"**{expected_substr}**", timeout=timeout)
    print(f"    ✓ 이동 완료: {page.url[:80]}")


# ── 단계 함수 ─────────────────────────────────────────────────────────────────

def select_model() -> dict:
    print("\n[모델 선택]")
    for k, v in config.MODELS.items():
        print(f"  {k}. {v['name']}")
    while True:
        choice = input("  번호 입력: ").strip()
        if choice in config.MODELS:
            return config.MODELS[choice]
        print("  ❌ 올바른 번호를 입력하세요.")


def step_navigate_to_product(page: Page, model: dict):
    print(f"\n[1단계] 제품 페이지 이동: {model['name']}")
    page.goto(model["url"], wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle", timeout=config.TIMEOUT)
    print(f"    ✓ 페이지 로드: {page.url}")


def step_click_buy_button(page: Page):
    print("\n[2단계] 온라인 구매하기 클릭")
    page.wait_for_selector(".revervation_btn", timeout=config.TIMEOUT)
    page.evaluate("document.querySelector('.revervation_btn').scrollIntoView({block:'center'})")
    time.sleep(0.5)
    page.evaluate(
        "document.querySelector('.revervation_btn').dispatchEvent("
        "new MouseEvent('click', {bubbles:true, cancelable:true, view:window}))"
    )
    time.sleep(1)
    modal_text = page.evaluate(
        "document.querySelector('[class*=\"modal\"], [id*=\"modal\"]')?.innerText || ''"
    )
    if "로그인" in modal_text:
        print("    → 로그인 모달 감지")
        safe_click(page, ".cBtn", "모달 확인")
        time.sleep(2)


def step_login(page: Page):
    print("\n[3단계] BMW ID 로그인")
    # customer.bmwgroup.com 으로 리다이렉트 대기
    try:
        page.wait_for_url("**customer.bmwgroup.com**", timeout=config.TIMEOUT)
    except PWTimeout:
        print("    ⚠ 로그인 페이지로 이동되지 않았습니다. 직접 이동합니다.")
        page.goto(config.LOGIN_URL)
        page.wait_for_url("**customer.bmwgroup.com**", timeout=config.TIMEOUT)

    # 이메일 입력
    email = config.EMAIL or ask("BMW 이메일 아이디")
    safe_fill(page, "#username", email, "이메일")
    safe_click(page, ".custom-button.primary", "계속")
    time.sleep(2)

    # 비밀번호 입력
    password = config.PASSWORD or ask("BMW 비밀번호")
    safe_fill(page, "#password", password, "비밀번호")
    safe_click(page, ".custom-button.primary", "로그인")
    time.sleep(2)

    # CAPTCHA 감지 (hCaptcha / reCAPTCHA)
    captcha_present = page.query_selector(
        "[class*='captcha'], [id*='captcha'], "
        "iframe[src*='captcha'], iframe[src*='hcaptcha'], "
        ".h-captcha, .g-recaptcha"
    )
    if captcha_present:
        print("\n    ⚠ CAPTCHA가 감지되었습니다!")
        print("    👉 브라우저에서 이미지 CAPTCHA를 직접 해결하고")
        print("       '다음' 또는 '로그인' 버튼을 눌러주세요.")
        input("    CAPTCHA 완료 후 Enter를 누르세요...")

    # 로그인 완료 후 shop.bmw.co.kr 복귀 대기 (최대 120초)
    print("    ⏳ 로그인 완료 대기 중 (최대 2분)...")
    try:
        page.wait_for_url("**shop.bmw.co.kr**", timeout=120_000)
        print("    ✓ 로그인 완료 - BMW 샵으로 복귀")
    except PWTimeout:
        print("    ⚠ 자동 복귀 대기 시간 초과. 현재 URL:", page.url)
        input("    로그인/본인인증을 완전히 마친 후 Enter를 누르세요...")


def step_identity_verification(page: Page):
    """
    본인인증 단계 처리.
    로그인 후 shop.bmw.co.kr 내에서 본인인증 페이지가 나타나는 경우 처리.
    SKT PASS / KT / LGU+ / QR 코드 방식 지원 (수동 완료 후 Enter 대기).
    """
    time.sleep(2)
    current_url = page.url

    # 본인인증 페이지 감지 키워드
    verification_keywords = ["본인인증", "인증", "identity", "verification", "pass.skt", "nice.co.kr"]
    page_text = page.evaluate("document.body.innerText") or ""
    is_verification_page = any(kw in page_text for kw in ["본인 인증", "본인인증", "휴대폰 인증", "통신사"])

    if not is_verification_page:
        return  # 본인인증 페이지가 아니면 스킵

    print("\n[3.5단계] 본인인증")
    print("    본인인증 페이지 감지됨.")

    # QR 코드 감지 → 스크린샷 저장
    qr_el = page.query_selector("img[src*='qr'], canvas[id*='qr'], [class*='qr']")
    if qr_el:
        qr_path = os.path.join(os.path.dirname(__file__), "bmw_qr_code.png")
        page.screenshot(path=qr_path)
        print(f"    📱 QR 코드 스크린샷 저장: {qr_path}")
        print("    👉 저장된 QR 이미지를 스마트폰으로 스캔하여 인증을 완료하세요.")
    else:
        page.screenshot(path="bmw_verification_page.png")
        print("    스크린샷 저장: bmw_verification_page.png")
        print("    👉 브라우저에서 SKT / KT / LGU+ 중 선택 후 인증을 완료하세요.")

    input("    본인인증 완료 후 Enter를 누르세요...")

    # 완료 후 구매 페이지 복귀 대기
    try:
        page.wait_for_url("**shop.bmw.co.kr**/online/**", timeout=30_000)
        print("    ✓ 본인인증 완료 - 구매 페이지 복귀")
    except PWTimeout:
        print("    현재 URL:", page.url)


def step_collect_purchase_form_data(page: Page) -> dict:
    """
    로그인 후 구매 폼의 모든 입력 필드를 스캔하고
    사용자에게 각 항목 값을 CLI로 입력받는다.
    """
    print("\n[4단계] 구매 폼 필드 수집 및 입력")
    time.sleep(2)

    # 현재 폼 필드 스캔
    fields = page.evaluate("""
        Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]), select, textarea'))
            .map(el => ({
                tag:         el.tagName,
                type:        el.type || '',
                id:          el.id || '',
                name:        el.name || '',
                placeholder: el.placeholder || '',
                label:       document.querySelector('label[for="' + el.id + '"]')?.innerText?.trim() || '',
                required:    el.required,
                value:       el.value || ''
            }))
    """)

    if not fields:
        print("    ℹ 자동 감지된 폼 필드가 없습니다.")
        print("    현재 URL:", page.url)
        page.screenshot(path="bmw_current_page.png")
        print("    스크린샷 저장: bmw_current_page.png")
        return {}

    print(f"\n    감지된 필드 {len(fields)}개:\n")
    data = {}

    for f in fields:
        label = f["label"] or f["placeholder"] or f["name"] or f["id"] or f["type"]
        req_mark = " *" if f["required"] else ""
        current = f["value"]

        if f["tag"] == "INPUT" and f["type"] in ("checkbox", "radio"):
            val = ask(f"{label}{req_mark} (y/n)", "n")
            data[f] = val.lower() == "y"
        elif f["tag"] == "SELECT":
            # 셀렉트 옵션 목록 출력
            options = page.evaluate(
                f"Array.from(document.querySelector('select[name=\"{f['name']}\"], select[id=\"{f['id']}\"]')?.options || []).map(o => o.text + '=' + o.value)"
            )
            print(f"    [{label}{req_mark}] 선택지: {', '.join(options)}")
            val = ask(f"{label}{req_mark}", current)
            data[f["name"] or f["id"]] = val
        else:
            val = ask(f"{label}{req_mark}", current)
            data[f["name"] or f["id"]] = val

    return data


def step_fill_purchase_form(page: Page, data: dict):
    print("\n[5단계] 구매 폼 자동 입력")
    for key, value in data.items():
        if not value:
            continue
        for selector in [f'[name="{key}"]', f'[id="{key}"]']:
            el = page.query_selector(selector)
            if not el:
                continue
            tag = el.evaluate("el => el.tagName")
            input_type = el.get_attribute("type") or ""
            if tag == "SELECT":
                el.select_option(value=value)
            elif input_type == "checkbox":
                if value:
                    el.check()
                else:
                    el.uncheck()
            else:
                el.fill(str(value))
            print(f"    ✓ {key} = {str(value)[:40]}")
            break


def step_review_and_submit(page: Page):
    print("\n[6단계] 최종 확인")
    page.screenshot(path="bmw_before_submit.png")
    print("    스크린샷 저장: bmw_before_submit.png")
    confirm = input("\n  입력 내용을 제출하시겠습니까? (y/n): ").strip().lower()
    if confirm != "y":
        print("  ❌ 제출 취소")
        return

    # 제출 버튼 탐색
    submit_btn = page.query_selector(
        "button[type='submit'], input[type='submit'], "
        ".submit-btn, .btn-submit, .order-btn, .pay-btn"
    )
    if submit_btn:
        submit_btn.click()
        print("    ✓ 제출 완료")
        time.sleep(3)
        page.screenshot(path="bmw_after_submit.png")
        print("    스크린샷 저장: bmw_after_submit.png")
    else:
        print("    ⚠ 제출 버튼을 찾지 못했습니다. 수동으로 처리해주세요.")
        input("    완료 후 Enter 누르세요...")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def run():
    print("=" * 55)
    print("  BMW Online Exclusive 구매 자동화")
    print("=" * 55)

    model = select_model()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=config.HEADLESS,
            slow_mo=config.SLOW_MO,
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
        page.set_default_timeout(config.TIMEOUT)

        try:
            step_navigate_to_product(page, model)
            step_click_buy_button(page)
            step_login(page)

            # 본인인증 처리 (로그인 직후 나타날 수 있음)
            step_identity_verification(page)

            # 로그인/인증 후 구매 페이지 복귀 (필요 시)
            if "shop.bmw.co.kr" in page.url and "/oom/" not in page.url and "/oem/" not in page.url:
                print(f"\n    구매 페이지 재진입: {model['url']}")
                page.goto(model["url"], wait_until="domcontentloaded")
                step_click_buy_button(page)
                time.sleep(2)

            # 구매 페이지 로드 후 본인인증이 다시 나타나는 경우 재처리
            step_identity_verification(page)

            data = step_collect_purchase_form_data(page)
            if data:
                step_fill_purchase_form(page, data)
                step_review_and_submit(page)
            else:
                print("\n  ℹ 폼 데이터 없음. 브라우저를 직접 확인하세요.")
                input("  종료하려면 Enter...")

        except KeyboardInterrupt:
            print("\n  사용자 중단")
        except Exception as e:
            print(f"\n  ❌ 오류 발생: {e}")
            page.screenshot(path="bmw_error.png")
            print("  스크린샷 저장: bmw_error.png")
            raise
        finally:
            input("\n  브라우저를 닫으려면 Enter 누르세요...")
            browser.close()

    print("\n✅ 완료")


if __name__ == "__main__":
    run()
