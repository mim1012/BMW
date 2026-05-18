# -*- coding: utf-8 -*-
"""로그인 흐름 추적 디버그 v5"""
import sys, time, json
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from pathlib import Path
from playwright.sync_api import sync_playwright
import config

URL   = "https://shop.bmw.co.kr/online/oom/OMG4223020"
EMAIL = config.EMAIL
PW    = config.PASSWORD
OUT   = Path(__file__).parent / "debug_fields.json"
SS    = Path(__file__).parent / "screenshots"
SS.mkdir(exist_ok=True)

if not EMAIL or not PW:
    raise RuntimeError("BMW_EMAIL/BMW_PASSWORD 값을 .env 또는 환경변수에 설정하세요.")

SCAN_JS = """
(() => {
    function lbl(el) {
        if (el.id) {
            const l = document.querySelector('label[for="'+el.id+'"]');
            if (l && l.innerText.trim()) return l.innerText.trim();
        }
        let p = el.parentElement;
        for (let i=0; i<8 && p; i++, p=p.parentElement) {
            const t = p.querySelector('label,span.tit,span.label,span.name,dt,th,strong,em.tit,.form-label,.select-tit,p.tit');
            if (t && t!==el && t.innerText.trim()) return t.innerText.trim();
        }
        return el.getAttribute('aria-label')||el.placeholder||el.name||el.id||'(unnamed)';
    }
    const out=[];
    document.querySelectorAll('select').forEach(el=>{
        if(el.disabled) return;
        const opts=Array.from(el.options).map(o=>({text:o.text.trim(),value:o.value}))
            .filter(o=>o.text&&!['선택','-- 선택 --','선택하세요','딜러사 선택','전시장 선택','영업사원 선택'].includes(o.text));
        if(!opts.length) return;
        out.push({kind:'select',id:el.id||'',name:el.name||'',label:lbl(el),options:opts});
    });
    document.querySelectorAll('input[type=text],input[type=tel],input[type=number],input[type=email],textarea').forEach(el=>{
        if(el.disabled||el.readOnly) return;
        out.push({kind:'input',type:el.type||'textarea',id:el.id||'',name:el.name||'',label:lbl(el),placeholder:el.placeholder||''});
    });
    document.querySelectorAll('input[type=checkbox]').forEach(el=>{
        if(el.disabled) return;
        let lb='';
        if(el.id) lb=document.querySelector('label[for="'+el.id+'"]')?.innerText?.trim()||'';
        if(!lb) lb=el.closest('label')?.innerText?.trim()||'';
        if(!lb) lb=el.getAttribute('aria-label')||el.name||el.id||'';
        out.push({kind:'check',id:el.id||'',name:el.name||'',label:lb,checked:el.checked});
    });
    return out;
})()
"""

def p(msg): print(msg, flush=True)

def ss(page, name):
    try: page.screenshot(path=str(SS/f"{name}.png"), full_page=True)
    except: pass

def dump_selects(page):
    info = page.evaluate("""
        Array.from(document.querySelectorAll('select')).map(el => ({
            id: el.id, name: el.name, disabled: el.disabled, optCount: el.options.length,
            opts: Array.from(el.options).map(o => ({text:o.text.trim(), value:o.value}))
        }))
    """)
    for s in info:
        p(f"    SELECT id={s['id']!r} name={s['name']!r} disabled={s['disabled']} ({s['optCount']}개):")
        for o in s['opts']: p(f"      {o['text']!r} → {o['value']!r}")

with sync_playwright() as pw:
    browser = pw.chromium.launch(
        headless=False, slow_mo=120,
        args=["--start-maximized", "--disable-blink-features=AutomationControlled", "--no-first-run"]
    )
    ctx = browser.new_context(
        viewport=None,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        locale="ko-KR",
    )
    page = ctx.new_page()

    # ── 1. 상품 페이지 + 로그인 링크 href 확인
    p(f"\n[1] 상품 페이지...")
    page.goto(URL, wait_until="domcontentloaded")
    try: page.wait_for_load_state("networkidle", timeout=15000)
    except: pass
    ss(page, "01_product")

    login_href = page.evaluate("""
        document.querySelector('a.btn-login, .btn-login')?.href || ''
    """)
    p(f"  로그인 href: {login_href!r}")

    # ── 2. 로그인 리다이렉트 따라가기
    if login_href:
        p(f"\n[2] 로그인 리다이렉트 이동...")
        page.goto(login_href, wait_until="commit")  # commit = 첫 응답만 기다림
        p(f"  URL after commit: {page.url}")

        # JS 리다이렉트가 있으면 customer.bmwgroup.com 으로 이동 대기
        try:
            page.wait_for_url("**/customer.bmwgroup.com/**", timeout=15000)
            p(f"  ✓ customer.bmwgroup.com 도달: {page.url}")
        except:
            p(f"  타임아웃 → 현재 URL: {page.url}")
            ss(page, "02_redirect")
            # 현재 페이지 분석
            title = page.title()
            body_text = page.evaluate("document.body?.innerText?.substring(0,300)||''")
            p(f"  title: {title!r}")
            p(f"  body: {body_text[:200]!r}")

        p(f"  최종 URL: {page.url}")
        ss(page, "02_login_page")

        if "customer.bmwgroup.com" in page.url:
            p("  BMW 로그인 페이지!")
            # 입력 필드 목록
            inputs = page.evaluate("""
                Array.from(document.querySelectorAll('input')).map(el=>({
                    type:el.type, id:el.id, name:el.name,
                    placeholder:el.placeholder, visible:el.offsetParent!==null
                }))
            """)
            p(f"  inputs: {inputs}")

            # 이메일
            for sel in ["#username","input[name='username']","input[type='email']","input[name='email']"]:
                try:
                    page.wait_for_selector(sel, timeout=4000)
                    page.fill(sel, EMAIL)
                    p(f"  이메일 입력 → {sel}"); break
                except: continue

            time.sleep(0.5)

            # 계속 버튼
            btns = page.evaluate("""
                Array.from(document.querySelectorAll('button,input[type=submit]')).map(el=>({
                    type:el.type,id:el.id,text:el.innerText?.trim()||el.value||'',visible:el.offsetParent!==null
                }))
            """)
            p(f"  buttons: {btns}")
            for sel in ["button[type='submit']","button:has-text('계속')","button:has-text('Continue')",
                        "button:has-text('다음')","input[type='submit']"]:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible(): el.click(); p(f"  계속 → {sel}"); break
                except: continue
            time.sleep(2.5)
            p(f"  URL: {page.url}")
            ss(page, "03_after_email")

            # 비밀번호
            for sel in ["#password","input[name='password']","input[type='password']"]:
                try:
                    page.wait_for_selector(sel, timeout=6000)
                    page.fill(sel, PW)
                    p(f"  비밀번호 → {sel}"); break
                except: continue
            time.sleep(0.5)

            # 로그인 버튼
            for sel in ["button[type='submit']","button:has-text('로그인')","button:has-text('Login')",
                        "button:has-text('Sign in')","input[type='submit']"]:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible(): el.click(); p(f"  로그인 → {sel}"); break
                except: continue

            ss(page, "04_after_pw")
            p("  ⏳ shop.bmw.co.kr 복귀 대기 (hCaptcha 있으면 직접 해결)...")
            try:
                page.wait_for_url("**/shop.bmw.co.kr/**", timeout=120_000)
                p(f"  ✓ 로그인 성공: {page.url}")
            except Exception as e:
                p(f"  ✗ 타임아웃: {page.url}")
                ss(page, "04_captcha_fail")
        else:
            p(f"  customer.bmwgroup.com 아님, URL: {page.url}")

    # ── 3. 상품 페이지 재이동
    p(f"\n[3] 상품 페이지 재이동...")
    page.goto(URL, wait_until="domcontentloaded")
    try: page.wait_for_load_state("networkidle", timeout=15000)
    except: pass
    p(f"  URL: {page.url}")
    ss(page, "05_product_after_login")

    # 로그인 여부 재확인
    logged = page.evaluate("!document.querySelector('a.btn-login, .btn-login')")
    p(f"  로그인됨: {logged}")

    stock = page.evaluate("""({
        normal: !!document.querySelector('.revervation_btn'),
        gray:   !!document.querySelector('.revervation_gray_btn'),
        alarm:  !!document.querySelector('.revervation_btn_alaram'),
    })""")
    p(f"  재고상태: {stock}")

    # ── 4. 딜러 select 현황
    p(f"\n[4] select 현황...")
    dump_selects(page)

    # ── 5. 예약 버튼 (재고 있는 경우만)
    if stock['normal']:
        p(f"\n[5] .revervation_btn 클릭...")
        try:
            with ctx.expect_page(timeout=4000) as npi:
                page.locator('.revervation_btn').click(force=True)
            np = npi.value
            np.wait_for_load_state("domcontentloaded", timeout=10000)
            p(f"  새탭: {np.url}"); page = np
        except:
            page.locator('.revervation_btn').click(force=True)
            time.sleep(2.5)
            p(f"  URL: {page.url}")

        try:
            page.wait_for_selector(".cBtn", timeout=3000)
            page.click(".cBtn"); p("  .cBtn 클릭"); time.sleep(2)
        except: pass

        p(f"  최종 URL: {page.url}")
        try: page.wait_for_selector("select,input[type=text]", timeout=10000)
        except: pass
        time.sleep(3)
        dump_selects(page)
        ss(page, "06_form_page")

        fields = page.evaluate(SCAN_JS)
        p(f"  SCAN → {len(fields)}개")
        for f in fields:
            if f['kind']=='select':
                opts = ", ".join(o['text'] for o in f['options'][:4])
                p(f"  [SELECT] {f['label']!r:22} [{opts}]")
            else:
                p(f"  [{f['kind'].upper():6}] {f['label']!r:22} id={f['id']!r}")
        if fields:
            OUT.write_text(json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8")
            p(f"  ✓ {OUT} 저장")
    else:
        p(f"\n[5] 재고 없음 — 딜러 select는 로그인 후 옵션 로드되는지 확인")
        dump_selects(page)
        fields = page.evaluate(SCAN_JS)
        p(f"  SCAN → {len(fields)}개")
        for f in fields:
            p(f"  {f}")

    p("\n\n스크린샷 저장 완료. 30초 후 종료...")
    p(f"  screenshots: {SS}")
    time.sleep(30)
    browser.close()
