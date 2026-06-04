# -*- coding: utf-8 -*-
import sys

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

import time, threading, queue, json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

APP_NAME = "BMW-AutoBuyer"

def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            app_data = Path.home() / "Library" / "Application Support" / APP_NAME
            app_data.mkdir(parents=True, exist_ok=True)
            return app_data
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def _resource_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return BASE_DIR

BASE_DIR    = _app_dir()
RESOURCE_DIR = _resource_dir()
SAVE_DIR    = BASE_DIR / "screenshots"
CONFIG_FILE = BASE_DIR / "user_config.json"
FIELDS_FILE = BASE_DIR / "form_fields.json"
MODELS_FILE = BASE_DIR / "scanned_models.json"
SAVE_DIR.mkdir(exist_ok=True)

PRODUCT_MODELS = [
    ("BMW M240i", "https://shop.bmw.co.kr/online/oom/OMG4223020"),
    ("BMW 뉴 M440i 쿠페 프로", "https://shop.bmw.co.kr/online/oom/OOM24090002"),
    ("BMW Online Exclusive 모델 목록", "https://shop.bmw.co.kr/online/oem/model"),
]
PRODUCT_URL = PRODUCT_MODELS[0][1]
DISCOVERED_PRODUCT_MODELS = {}
CHROME_PROFILE_DIR = BASE_DIR / "chrome_profile"
CHROME_DEBUG_PORT = 9222
BROWSER_CHANNEL = "chrome"

evt_q: queue.Queue = queue.Queue()
cmd_q: queue.Queue = queue.Queue()

def bootstrap_packaged_data():
    if RESOURCE_DIR == BASE_DIR:
        return
    patterns = ["scanned_models.json", "form_fields.json", "form_fields_*.json"]
    for pattern in patterns:
        for src in RESOURCE_DIR.glob(pattern):
            dst = BASE_DIR / src.name
            if not dst.exists():
                try:
                    dst.write_bytes(src.read_bytes())
                except Exception:
                    pass

bootstrap_packaged_data()

def _safe_slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_") or "default"

def _model_name_for_url(url: str) -> str:
    if url in DISCOVERED_PRODUCT_MODELS:
        return DISCOVERED_PRODUCT_MODELS[url]
    for name, model_url in PRODUCT_MODELS:
        if model_url == url:
            return name
    return url.rstrip("/").split("/")[-1] or "custom"

def is_product_url(url: str) -> bool:
    return "/online/oom/" in url or "/edition/oem/" in url

def _css_attr_value(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"')

def install_fast_scan_routes(ctx):
    blocked_hosts = (
        "google-analytics.com", "googletagmanager.com", "googleadservices.com",
        "doubleclick.net", "facebook.com", "datadoghq.com", "daum.net",
        "youtube.com", "ytimg.com", "fonts.googleapis.com", "fonts.gstatic.com",
    )

    def handle(route):
        req = route.request
        url = req.url.lower()
        if "nice.checkplus.co.kr" in url:
            return route.continue_()
        if req.resource_type in ("image", "media", "font"):
            return route.abort()
        if any(host in url for host in blocked_hosts):
            return route.abort()
        return route.continue_()

    ctx.route("**/*", handle)

def fields_file_for_url(url: str) -> Path:
    return BASE_DIR / f"form_fields_{_safe_slug(_model_name_for_url(url))}.json"

def config_file_for_url(url: str) -> Path:
    return BASE_DIR / f"user_config_{_safe_slug(_model_name_for_url(url))}.json"

def save_models_manifest(models):
    cleaned = []
    seen = set()
    for name, url in models:
        if not name or not url or url in seen:
            continue
        seen.add(url)
        cleaned.append({"name": str(name).strip(), "url": str(url).strip()})
    MODELS_FILE.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")

def load_models_manifest():
    if not MODELS_FILE.exists():
        models = []
        known_urls = {url for _, url in PRODUCT_MODELS}
        for path in sorted(BASE_DIR.glob("form_fields_*.json")):
            stem = path.stem.replace("form_fields_", "", 1)
            if not stem or stem == "json":
                continue
            name = stem.replace("_", " ").strip()
            matching = next(((n, u) for n, u in PRODUCT_MODELS if _safe_slug(n) == stem), None)
            if matching:
                models.append(matching)
                known_urls.add(matching[1])
        return models
    try:
        data = json.loads(MODELS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    models = []
    for item in data if isinstance(data, list) else []:
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if name and url:
            models.append((name, url))
    return models

def keep_browser_until_closed(page, log):
    while True:
        try:
            page.wait_for_timeout(1000)
        except Exception:
            log("  브라우저가 닫혀 세션 유지 루프 종료")
            return

def load_config_for_url(url: str):
    config_file = config_file_for_url(url)
    if config_file.exists():
        return json.loads(config_file.read_text(encoding="utf-8")), config_file
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if cfg.get("__url__", url) == url:
            return cfg, CONFIG_FILE
    return None, config_file

# ─── DOM 스캔 JS ──────────────────────────────────────────────────────────────

SCAN_JS = """
(() => {
    function esc(s) {
        if (window.CSS && CSS.escape) return CSS.escape(s);
        return String(s).replace(/([ #;?%&,.+*~':"!^$[\\]()=>|/@])/g, '\\\\$1');
    }
    function selector(el) {
        if (el.id) return '#' + esc(el.id);
        if (el.name) {
            const byName = el.tagName.toLowerCase() + '[name="' + String(el.name).replace(/"/g, '\\\\"') + '"]';
            if (document.querySelectorAll(byName).length === 1) return byName;
        }
        const parts = [];
        let cur = el;
        while (cur && cur.nodeType === 1 && cur !== document.body) {
            const tag = cur.tagName.toLowerCase();
            const parent = cur.parentElement;
            if (!parent) break;
            const siblings = Array.from(parent.children).filter(x => x.tagName === cur.tagName);
            const nth = siblings.length > 1 ? ':nth-of-type(' + (siblings.indexOf(cur) + 1) + ')' : '';
            parts.unshift(tag + nth);
            cur = parent;
        }
        return parts.length ? 'body > ' + parts.join(' > ') : '';
    }
    function lbl(el) {
        if (el.id) {
            const l = document.querySelector('label[for="'+el.id+'"]');
            if (l && l.innerText.trim()) return l.innerText.trim();
        }
        let p = el.parentElement;
        for (let i=0; i<6 && p; i++, p=p.parentElement) {
            const t = p.querySelector('label,span.tit,span.label,span.name,dt,th,strong,em.tit,.form-label');
            if (t && t!==el && t.innerText.trim()) return t.innerText.trim();
        }
        return el.getAttribute('aria-label')||el.placeholder||el.name||el.id||'';
    }
    function textOf(el) {
        return (el.innerText||el.textContent||el.getAttribute('aria-label')||
            el.getAttribute('title')||el.getAttribute('alt')||'').trim().replace(/\s+/g, ' ');
    }
    function optionText(el) {
        const own = textOf(el);
        if (own) return own;
        const img = el.querySelector?.('img[alt], img[title]');
        if (img) return textOf(img);
        return el.value || el.id || el.name || '';
    }
    function sectionLabel(el) {
        let p = el;
        for (let i=0; i<7 && p; i++, p=p.parentElement) {
            const title = p.querySelector?.('h1,h2,h3,h4,strong,.tit,.title,.label,.name,dt,legend');
            const t = title ? textOf(title) : '';
            if (t) return t;
        }
        return lbl(el);
    }
    function pushOption(label, optionEls) {
        const opts = [];
        const seen = new Set();
        optionEls.forEach((el, idx)=>{
            if (!el || el.disabled || el.getAttribute('aria-disabled') === 'true') return;
            const sel = selector(el);
            if (!sel) return;
            let text = optionText(el);
            if (!text) {
                const img = el.querySelector?.('img');
                const bg = getComputedStyle(el).backgroundColor;
                if (img?.src) text = img.src.split('/').pop()?.split('?')[0] || '';
                if (!text && bg && bg !== 'rgba(0, 0, 0, 0)') text = bg;
                if (!text) text = (label || '옵션') + ' ' + (idx + 1);
            }
            if (seen.has(text + sel)) return;
            seen.add(text + sel);
            opts.push({text:text, value:sel, selector:sel});
        });
        if (opts.length > 1) {
            const groupLabel = label || '옵션';
            const groupKey = groupLabel + ':' + opts.map(o=>o.text).join('|').slice(0, 120);
            out.push({kind:'option', id:'', name:groupKey, selector:'', label:groupLabel, options:opts});
        }
    }
    const out=[];
    document.querySelectorAll('.revervation_wrap2 .trim').forEach(trim=>{
        const label = textOf(trim.querySelector('h3')) || sectionLabel(trim);
        const opts = Array.from(trim.querySelectorAll('a.tooltip')).map((el, idx)=>{
            const img = el.querySelector('img[alt]');
            const tip = el.querySelector('.tooltiptext');
            const text = (img?.getAttribute('alt') || textOf(tip) || optionText(el) || `${label} ${idx + 1}`).trim();
            const sel = selector(el);
            return sel ? {text:text, value:sel, selector:sel, soldout:el.classList.contains('soldout'), active:el.classList.contains('active')} : null;
        }).filter(Boolean);
        if (opts.length) {
            out.push({
                kind:'option',
                id:'',
                name:'trim:' + label,
                selector:'',
                label:label || '트림 옵션',
                options:opts
            });
        }
    });

    document.querySelectorAll('select').forEach(el=>{
        if(el.disabled) return;
        const opts=Array.from(el.options).map(o=>({text:o.text.trim(),value:o.value}))
            .filter(o=>o.text&&!['선택','-- 선택 --','선택하세요'].includes(o.text));
        const fallback = Array.from(el.options).map(o=>({text:o.text.trim(),value:o.value})).filter(o=>o.text);
        const finalOpts = opts.length ? opts : fallback;
        if(!finalOpts.length) return;
        out.push({kind:'select',id:el.id||'',name:el.name||'',selector:selector(el),label:lbl(el),options:finalOpts});
    });
    document.querySelectorAll('input[type=text],input[type=tel],input[type=number],input[type=email],textarea').forEach(el=>{
        if(el.disabled||el.readOnly) return;
        out.push({kind:'input',type:el.type||'textarea',id:el.id||'',name:el.name||'',selector:selector(el),label:lbl(el),placeholder:el.placeholder||''});
    });
    document.querySelectorAll('input[type=checkbox]').forEach(el=>{
        if(el.disabled) return;
        let lb='';
        if(el.id) lb=document.querySelector('label[for="'+el.id+'"]')?.innerText?.trim()||'';
        if(!lb) lb=el.closest('label')?.innerText?.trim()||'';
        if(!lb) lb=el.getAttribute('aria-label')||el.name||el.id||'';
        out.push({kind:'check',id:el.id||'',name:el.name||'',selector:selector(el),label:lb,checked:el.checked});
    });
    const radioGroups = new Map();
    document.querySelectorAll('input[type=radio]').forEach(el=>{
        if(el.disabled) return;
        const key = sectionLabel(el) || el.name || 'radio';
        if(!radioGroups.has(key)) radioGroups.set(key, []);
        let target = el;
        if (el.id) {
            const label = document.querySelector('label[for="'+el.id+'"]');
            if (label) target = label;
        } else if (el.closest('label')) {
            target = el.closest('label');
        }
        radioGroups.get(key).push(target);
    });
    radioGroups.forEach((els, key)=>pushOption(key, els));

    const optionWords = /(익스테리어|인테리어|외장|내장|색상|컬러|휠|트림|옵션|Exterior|Interior|Color|Colour|Wheel|Trim|Option)/i;
    const clickableSel = [
        'button', 'a', 'label', 'li', '[role=radio]', '[role=option]', '[aria-checked]',
        '[class*=swatch]', '[class*=color]', '[class*=colour]', '[class*=option]',
        '[class*=exterior]', '[class*=interior]', '[class*=item]', '[class*=chip]',
        '[class*=box]', '[class*=tile]', '[style*=background]'
    ].join(',');
    document.querySelectorAll('.revervation_wrap2 section, .revervation_wrap2 article, .revervation_wrap2 fieldset, .revervation_wrap2 div, .revervation_wrap2 ul, .revervation_wrap2 ol').forEach(box=>{
        const boxText = textOf(box).slice(0, 300);
        const cls = box.className ? String(box.className) : '';
        if (!optionWords.test(boxText + ' ' + cls)) return;
        const els = Array.from(box.querySelectorAll(clickableSel)).filter(el=>{
            const t = optionText(el);
            if (t.length > 80) return false;
            const rect = el.getBoundingClientRect();
            if (rect.width < 8 || rect.height < 8) return false;
            if (rect.width > 260 || rect.height > 180) return false;
            return !['SCRIPT', 'STYLE', 'SELECT', 'OPTION'].includes(el.tagName);
        });
        pushOption(sectionLabel(box), els);
    });
    const uniq = [];
    const keys = new Set();
    out.forEach(f=>{
        const optKey = (f.options||[]).map(o=>o.text).join('|');
        const key = [f.kind, f.label, f.id, f.name, f.selector, optKey].join('::');
        if (keys.has(key)) return;
        keys.add(key);
        uniq.push(f);
    });
    return uniq;
})()
"""

# ─── 로그인 자동 입력 ────────────────────────────────────────────────────────

_LOGIN_EMAIL = ""
_LOGIN_PW    = ""

def _auto_login(page, log, status):
    """BMW 로그인 페이지에서 이메일/비밀번호 자동 입력 (hCaptcha는 사용자 수동).

    page는 customer.bmwgroup.com 이미 도달한 상태여야 함.
    반환: True=자동 성공, False=수동 대기 필요
    """
    log("  [로그인] 자동 입력 비활성화 — 수동 로그인 대기")
    status("브라우저에서 직접 로그인 완료 후 아래 버튼 클릭")
    return False

    log(f"  [로그인] URL: {page.url[:80]}")
    status("로그인 — 이메일 입력 중...")

    EMAIL_SELS = ["#username", "input[name='username']", "input[type='email']",
                  "input[name='email']"]
    filled_email = False
    for sel in EMAIL_SELS:
        try:
            page.wait_for_selector(sel, timeout=6000)
            page.fill(sel, _LOGIN_EMAIL)
            log(f"  이메일 입력 ({sel})")
            filled_email = True
            break
        except Exception:
            continue

    if not filled_email:
        log("  [!] 이메일 필드 없음 — 수동 로그인")
        return False

    time.sleep(0.5)

    # 계속 버튼
    for sel in ["button:has-text('계속')", "button:has-text('Continue')",
                "button:has-text('다음')", "button[type='submit']", "input[type='submit']"]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                log(f"  계속 클릭 ({sel})")
                break
        except Exception:
            continue
    time.sleep(2)

    # 비밀번호
    status("로그인 — 비밀번호 입력 중...")
    PW_SELS = ["#password", "input[name='password']", "input[type='password']"]
    filled_pw = False
    for sel in PW_SELS:
        try:
            page.wait_for_selector(sel, timeout=6000)
            page.fill(sel, _LOGIN_PW)
            log(f"  비밀번호 입력 ({sel})")
            filled_pw = True
            break
        except Exception:
            continue

    if not filled_pw:
        log("  [!] 비밀번호 필드 없음 — 수동 입력")
        return False

    time.sleep(0.5)

    # 로그인 버튼
    for sel in ["button:has-text('로그인')", "button:has-text('Login')",
                "button:has-text('Sign in')", "button[type='submit']", "input[type='submit']"]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                log(f"  로그인 클릭 ({sel})")
                break
        except Exception:
            continue

    status("hCaptcha가 보이면 직접 해결하세요 — 로그인 후 자동 진행")
    log("  자동 입력 완료 — hCaptcha 있으면 브라우저에서 직접 해결")

    # shop.bmw.co.kr 복귀 대기 (최대 120초 — hCaptcha 해결 시간 포함)
    try:
        page.wait_for_url("**/shop.bmw.co.kr/**", timeout=120_000)
        log("  로그인 성공 (자동 감지)")
        status("로그인 성공!")
        return True
    except Exception:
        log("  자동 감지 실패 — '로그인 완료' 버튼으로 계속")
        return False


# ─── Chrome 열기 + 스캔 스레드 ───────────────────────────────────────────────

def discover_product_models():
    from playwright.sync_api import sync_playwright

    models = []
    seen = set()

    def add(name, url):
        if not is_product_url(url) or url in seen:
            return
        name = name.replace("구매하기", "").replace("재고 없음", "").strip()
        seen.add(url)
        models.append((name.strip() or _model_name_for_url(url), url))

    for name, url in PRODUCT_MODELS:
        add(name, url)

    list_urls = [url for _, url in PRODUCT_MODELS if "/online/oem/" in url or url.endswith("/model")]
    if not list_urls:
        return models

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            channel=BROWSER_CHANNEL,
            slow_mo=50,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
            ],
        )
        ctx = browser.new_context(
            viewport=None,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
        )
        page = ctx.new_page()
        for list_url in list_urls:
            try:
                page.goto(list_url, wait_until="domcontentloaded", timeout=20000)
                try: page.wait_for_load_state("networkidle", timeout=8000)
                except Exception: pass
                stable_rounds = 0
                last_count = -1
                for _ in range(20):
                    page.mouse.wheel(0, 1000)
                    page.wait_for_timeout(500)
                    count = page.locator('a[href*="/edition/oem/"], a[href*="/online/oom/"]').count()
                    if count == last_count:
                        stable_rounds += 1
                    else:
                        stable_rounds = 0
                        last_count = count
                    if stable_rounds >= 3:
                        break
                links = page.evaluate("""
                (() => Array.from(document.querySelectorAll('a[href*="/online/oom/"], a[href*="/edition/oem/"]')).map(a => ({
                    text: (a.innerText || a.getAttribute('title') || a.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' '),
                    href: new URL(a.getAttribute('href'), location.href).href
                })))()
                """)
                for item in links:
                    add(item.get("text") or "", item.get("href") or "")
            except Exception:
                continue
        browser.close()
    DISCOVERED_PRODUCT_MODELS.update({url: name for name, url in models})
    return models


def run_scan_all_models():
    try:
        status = lambda m: evt_q.put(("status", m))
        log = lambda m: evt_q.put(("log", m))
        status("전체 차종 목록 수집 중...")
        models = discover_product_models()
        if not models:
            evt_q.put(("error", "스캔할 차종 URL을 찾지 못했습니다."))
            return
        DISCOVERED_PRODUCT_MODELS.update({url: name for name, url in models})
        evt_q.put(("models_discovered", models))
        log(f"  전체 스캔 대상: {len(models)}개")
        for idx, (name, url) in enumerate(models, 1):
            status(f"전체 스캔 {idx}/{len(models)} — {name}")
            log(f"[전체 스캔] {idx}/{len(models)} {name}: {url}")
            run_open_and_scan(url, emit_scanned=False)
        status(f"전체 차종 스캔 완료 ({len(models)}개)")
        evt_q.put(("scan_all_done", len(models)))
    except Exception:
        import traceback
        evt_q.put(("error", traceback.format_exc()))


def run_open_browser_for_login(url: str):
    from playwright.sync_api import sync_playwright

    def log(m): evt_q.put(("log", m))
    def status(m): evt_q.put(("status", m))

    try:
        with sync_playwright() as pw:
            status("로그인용 Chrome 실행 중...")
            ctx = pw.chromium.launch_persistent_context(
                str(CHROME_PROFILE_DIR),
                headless=False,
                channel=BROWSER_CHANNEL,
                slow_mo=0,
                viewport=None,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="ko-KR",
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    f"--remote-debugging-port={CHROME_DEBUG_PORT}",
                ],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            status("Chrome 열림 — 브라우저에서 로그인 완료 후 '전체 스캔' 클릭")
            log("  Chrome 열기: 스캔하지 않음, 로그인 세션만 유지")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                log(f"  초기 페이지 이동 실패: {e}")
            evt_q.put(("browser_opened", None))
            keep_browser_until_closed(page, log)
    except Exception:
        import traceback
        evt_q.put(("error", traceback.format_exc()))


def run_open_and_scan(url: str, emit_scanned=True):
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    def log(m):    evt_q.put(("log", m))
    def status(m): evt_q.put(("status", m))
    def wait_cmd(exp):
        while True:
            try:
                if cmd_q.get(timeout=0.5) == exp: return
            except queue.Empty: pass

    def click_entry_button():
        status("예약/구매 버튼 찾는 중...")
        selectors = [
            ".revervation_btn_alaram", ".revervation_btn", ".reservation_btn", ".reserve_btn",
            "button:has-text('재고 알림 신청하기')", "a:has-text('재고 알림 신청하기')",
            "button:has-text('알림 신청')", "a:has-text('알림 신청')",
            "button:has-text('예약')", "a:has-text('예약')",
            "button:has-text('구매')", "a:has-text('구매')",
            "button:has-text('신청')", "a:has-text('신청')",
            "button:has-text('온라인')", "a:has-text('온라인')",
        ]
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible(timeout=1000):
                    el.scroll_into_view_if_needed(timeout=2000)
                    el.click(force=True, timeout=3000)
                    log(f"  진입 버튼 클릭: {sel}")
                    return True
            except Exception:
                continue
        clicked = page.evaluate("""
        (() => {
            const words = ['재고 알림 신청하기', '알림 신청', '예약', '구매', '신청', '온라인'];
            const els = Array.from(document.querySelectorAll('button,a,[role=button],input[type=button],input[type=submit]'));
            for (const el of els) {
                const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
                const rect = el.getBoundingClientRect();
                if (rect.width < 8 || rect.height < 8) continue;
                if (words.some(w => text.includes(w))) {
                    el.scrollIntoView({block:'center'});
                    el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));
                    return text || el.className || el.id || true;
                }
            }
            return '';
        })()
        """)
        if clicked:
            log(f"  진입 버튼 클릭(JS): {clicked}")
            return True
        log("  진입 버튼을 못 찾음 — 현재 페이지에서 바로 스캔")
        return False

    def has_option_panel():
        try:
            return bool(page.evaluate("""
            (() => {
                const body = document.body?.innerText || '';
                const hasLabels = ['익스테리어', '인테리어', '딜러위치', '결제방법'].some(t => body.includes(t));
                const hasControls = document.querySelectorAll('select, input[type=radio], [role=radio], [class*=color], [class*=option]').length > 0;
                return hasLabels && hasControls;
            })()
            """))
        except Exception:
            return False

    def dealer_options_ready():
        try:
            return bool(page.evaluate("""
            (() => Array.from(document.querySelectorAll('.revervation_wrap2 .section02 select, select')).some(sel => {
                const opts = Array.from(sel.options || []).map(o => (o.textContent || '').trim()).filter(Boolean);
                return opts.length > 1 || opts.some(t => !['딜러사 선택', '전시장 선택', '영업사원 선택'].includes(t));
            }))()
            """))
        except Exception:
            return False

    def maybe_login_for_scan():
        if dealer_options_ready():
            log("  딜러위치 옵션 로드 확인")
            return
        if has_option_panel():
            status("딜러위치 로드를 위해 로그인 진입...")
            log("  딜러위치 옵션 미로드 — 로그인/알림 신청 버튼 클릭")
            click_entry_button()
            time.sleep(2)

    try:
        with sync_playwright() as pw:
            status("브라우저 실행 중...")
            ctx = pw.chromium.launch_persistent_context(
                str(CHROME_PROFILE_DIR),
                headless=False,
                channel=BROWSER_CHANNEL,
                slow_mo=0,
                viewport=None,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="ko-KR",
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                ]
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            install_fast_scan_routes(ctx)

            # 상품 페이지 이동
            status("상품 페이지 로딩...")
            page.goto(url, wait_until="domcontentloaded")
            try: page.wait_for_load_state("networkidle", timeout=15000)
            except Exception: pass
            log(f"  페이지: {page.url[:80]}")

            if has_option_panel():
                log("  옵션 선택 화면 감지")
                maybe_login_for_scan()
            else:
                click_entry_button()
                time.sleep(2)

            # 로그인 모달(.cBtn) 클릭
            try:
                page.wait_for_selector(".cBtn", timeout=3000)
                page.click(".cBtn")
                log("  모달 확인 클릭")
            except Exception:
                pass

            # redirect_gcdm → customer.bmwgroup.com JS 리다이렉트 대기
            try:
                page.wait_for_url("**/customer.bmwgroup.com/**", timeout=8000)
                log(f"  로그인 페이지: {page.url[:60]}")
            except Exception:
                pass
            time.sleep(1)

            # 로그인 필요 시 사용자가 브라우저에서 직접 처리
            if "customer.bmwgroup.com" in page.url:
                status("브라우저에서 직접 로그인 완료 후 아래 버튼 클릭")
                log("  로그인 페이지 감지 — 수동 로그인 대기")
                evt_q.put(("need_login", None))
                wait_cmd("login_done")

                # 로그인 완료 → 상품 페이지 재이동 → 예약 버튼
                status("폼 페이지 이동 중...")
                page.goto(url, wait_until="domcontentloaded")
                try: page.wait_for_load_state("networkidle", timeout=15000)
                except Exception: pass
                if has_option_panel():
                    log("  옵션 선택 화면 감지")
                    for _ in range(10):
                        if dealer_options_ready():
                            break
                        time.sleep(0.5)
                    maybe_login_for_scan()
                else:
                    click_entry_button()
                    time.sleep(2)
                try:
                    page.wait_for_selector(".cBtn", timeout=3000)
                    page.click(".cBtn")
                except Exception: pass
                time.sleep(2)

            # 폼 요소 대기
            status("폼 로딩 대기...")
            try:
                page.wait_for_selector("select, input[type=text], input[type=tel], input[type=radio], [role=radio], button, [class*=color], [class*=option]", timeout=10000)
            except Exception:
                pass
            try: page.wait_for_load_state("networkidle", timeout=5000)
            except Exception: pass
            for _ in range(12):
                if dealer_options_ready():
                    break
                time.sleep(0.5)

            log(f"  최종 URL: {page.url[:80]}")
            sel_n = page.evaluate("document.querySelectorAll('select').length")
            inp_n = page.evaluate("document.querySelectorAll('input[type=text],input[type=tel]').length")
            radio_n = page.evaluate("document.querySelectorAll('input[type=radio], [role=radio]').length")
            opt_n = page.evaluate("document.querySelectorAll('[class*=color], [class*=option], [class*=exterior], [class*=interior]').length")
            log(f"  select:{sel_n}개  input:{inp_n}개  radio:{radio_n}개  option 후보:{opt_n}개")

            # 스캔
            status("폼 필드 스캔 중...")
            fields = page.evaluate(SCAN_JS)
            log(f"  스캔 결과: {len(fields)}개")
            for f in fields:
                if f['kind'] == 'select':
                    opts = ", ".join(o['text'] for o in f['options'][:3])
                    log(f"    [SELECT] {f['label']!r} → {opts}")
                else:
                    log(f"    [{f['kind'].upper()}] {f['label']!r}")

            fields_json = json.dumps(fields, ensure_ascii=False, indent=2)
            fields_file_for_url(url).write_text(fields_json, encoding="utf-8")
            FIELDS_FILE.write_text(fields_json, encoding="utf-8")
            if emit_scanned:
                evt_q.put(("scanned", fields))

            ctx.close()

    except Exception:
        import traceback
        evt_q.put(("error", traceback.format_exc()))


def run_scan_all_models_one_session():
    from playwright.sync_api import sync_playwright

    def log(m): evt_q.put(("log", m))
    def status(m): evt_q.put(("status", m))
    api_headers = {}

    def capture_api_headers(resp):
        if "/shop/api/" not in resp.url:
            return
        headers = resp.request.headers
        if headers.get("token"):
            api_headers.clear()
            for key in ("accept", "user-agent", "token", "x-m-x-token"):
                if headers.get(key):
                    api_headers[key] = headers[key]
            api_headers["referer"] = "https://shop.bmw.co.kr/online/oem/model"

    def wait_cmd(exp):
        while True:
            try:
                if cmd_q.get(timeout=0.5) == exp: return
            except queue.Empty: pass

    def collect_models(page):
        models, seen = [], set()

        def add(name, href):
            if not href:
                return
            url = page.evaluate("(href) => new URL(href, location.href).href", href)
            if not is_product_url(url) or url in seen:
                return
            name = (name or _model_name_for_url(url)).replace("구매하기", "").replace("재고 없음", "").strip()
            seen.add(url)
            models.append((name or _model_name_for_url(url), url))

        for name, url in PRODUCT_MODELS:
            add(name, url)

        page.goto("https://shop.bmw.co.kr/online/oem/model", wait_until="domcontentloaded", timeout=20000)
        try: page.wait_for_load_state("networkidle", timeout=5000)
        except Exception: pass
        last_count = -1
        stable = 0
        for _ in range(24):
            count = page.locator('a[href*="/edition/oem/"], a[href*="/online/oom/"]').count()
            if count == last_count:
                stable += 1
            else:
                stable = 0
                last_count = count
            page.mouse.wheel(0, 1000)
            page.wait_for_timeout(200)
            if stable >= 4:
                break
        links = page.evaluate("""
        (() => Array.from(document.querySelectorAll('a[href*="/online/oom/"], a[href*="/edition/oem/"]')).map(a => ({
            text: (a.innerText || a.getAttribute('title') || a.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' '),
            href: a.getAttribute('href')
        })))()
        """)
        for item in links:
            add(item.get("text"), item.get("href"))
        return models

    def click_entry_button(page):
        selectors = [
            ".revervation_btn_alaram", ".revervation_btn", ".reservation_btn", ".reserve_btn",
            "button:has-text('재고 알림 신청하기')", "a:has-text('재고 알림 신청하기')",
            "button:has-text('알림 신청')", "a:has-text('알림 신청')",
            "button:has-text('예약')", "a:has-text('예약')",
            "button:has-text('구매')", "a:has-text('구매')",
            "button:has-text('신청')", "a:has-text('신청')",
        ]
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible(timeout=500):
                    el.scroll_into_view_if_needed(timeout=1000)
                    el.click(force=True, timeout=1500)
                    log(f"  진입 버튼 클릭: {sel}")
                    return True
            except Exception:
                continue
        return False

    def has_option_panel(page):
        try:
            return bool(page.evaluate("""
            (() => {
                const body = document.body?.innerText || '';
                return ['익스테리어', '인테리어', '딜러위치', '결제방법'].some(t => body.includes(t));
            })()
            """))
        except Exception:
            return False

    def dealer_options_ready(page):
        try:
            return bool(page.evaluate("""
            (() => Array.from(document.querySelectorAll('.revervation_wrap2 .section02 select, select')).some(sel => {
                const opts = Array.from(sel.options || []).map(o => (o.textContent || '').trim()).filter(Boolean);
                return opts.length > 1 || opts.some(t => !['딜러사 선택', '전시장 선택', '영업사원 선택'].includes(t));
            }))()
            """))
        except Exception:
            return False

    def select_default_trims(page):
        try:
            return page.evaluate("""
            async () => {
                const sleep = ms => new Promise(r => setTimeout(r, ms));
                const clicked = [];
                for (const trim of Array.from(document.querySelectorAll('.revervation_wrap2 .trim'))) {
                    const title = (trim.querySelector('h3')?.innerText || '').trim();
                    if (!/익스테리어|인테리어/.test(title)) continue;
                    const opts = Array.from(trim.querySelectorAll('a.tooltip'));
                    if (!opts.length) continue;
                    const target = trim.querySelector('a.tooltip.active') || opts[0];
                    target.scrollIntoView({block:'center', inline:'center'});
                    target.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window}));
                    target.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, view:window}));
                    target.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
                    clicked.push(title + ':' + ((target.querySelector('img')?.alt || target.innerText || '').trim()));
                    await sleep(500);
                }
                return clicked;
            }
            """)
        except Exception as e:
            log(f"  외장/내장 기본 선택 실패: {e}")
            return []

    def wait_for_dealer_selects(page, timeout_ms=8000):
        deadline = time.time() + (timeout_ms / 1000)
        while time.time() < deadline:
            if dealer_options_ready(page):
                return True
            page.wait_for_timeout(250)
        return dealer_options_ready(page)

    def read_dealer_select_fields(page):
        try:
            return page.evaluate("""
            () => {
                const labels = ['딜러사', '전시장', '영업사원'];
                return Array.from(document.querySelectorAll('.revervation_wrap2 .section02 select')).slice(0, 3).map((sel, idx) => ({
                    kind: 'select',
                    id: sel.id || '',
                    name: sel.name || ('dealer:' + labels[idx]),
                    selector: `.revervation_wrap2 .section02 select:nth-of-type(${idx + 1})`,
                    label: labels[idx],
                    options: Array.from(sel.options || []).map(o => ({
                        text: (o.textContent || '').trim(),
                        value: o.value
                    })).filter(o => o.text)
                }));
            }
            """)
        except Exception as e:
            log(f"  딜러 select 읽기 실패: {e}")
            return []

    def select_dealer_value(page, index, value):
        selector = f".revervation_wrap2 .section02 select:nth-of-type({index + 1})"
        try:
            page.select_option(selector, value=str(value))
            page.eval_on_selector(selector, "el => el.dispatchEvent(new Event('change', {bubbles:true}))")
            page.wait_for_timeout(700)
            return True
        except Exception:
            return False

    def collect_dealer_fields_from_dom(page, url):
        status("딜러위치 수집 — 외장/내장 선택 중...")
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        try: page.wait_for_load_state("networkidle", timeout=5000)
        except Exception: pass
        if not has_option_panel(page):
            click_entry_button(page)
            page.wait_for_timeout(1000)
        clicked = select_default_trims(page)
        if clicked:
            log("  외장/내장 선택: " + ", ".join(clicked))
        wait_for_dealer_selects(page, timeout_ms=10000)

        unions = [[], [], []]
        seen = [set(), set(), set()]

        def merge(fields):
            for idx, field in enumerate(fields[:3]):
                for opt in field.get("options", []):
                    text = str(opt.get("text", "")).strip()
                    value = str(opt.get("value", "")).strip()
                    if text in ("딜러사 선택", "전시장 선택", "영업사원 선택") and not value:
                        continue
                    if not text or text in seen[idx]:
                        continue
                    seen[idx].add(text)
                    unions[idx].append({"text": text, "value": opt.get("value", "")})

        fields = read_dealer_select_fields(page)
        merge(fields)
        dealers = [o for o in (fields[0].get("options", []) if fields else []) if str(o.get("value", "")).strip()]
        for dealer in dealers:
            if not select_dealer_value(page, 0, dealer.get("value", "")):
                continue
            page.wait_for_timeout(600)
            fields = read_dealer_select_fields(page)
            merge(fields)
            showrooms = [o for o in (fields[1].get("options", []) if len(fields) > 1 else []) if str(o.get("value", "")).strip()]
            for showroom in showrooms:
                if not select_dealer_value(page, 1, showroom.get("value", "")):
                    continue
                page.wait_for_timeout(500)
                merge(read_dealer_select_fields(page))

        labels = ["딜러사", "전시장", "영업사원"]
        result = []
        for idx, opts in enumerate(unions):
            if not opts:
                opts = [{"text": labels[idx] + " 선택", "value": ""}]
            result.append({
                "kind": "select",
                "id": "",
                "name": "dealer:" + labels[idx],
                "selector": f".revervation_wrap2 .section02 select:nth-of-type({idx + 1})",
                "label": labels[idx],
                "options": opts,
            })
        log(f"  딜러위치 수집: 딜러사 {len(unions[0])}개, 전시장 {len(unions[1])}개, 영업사원 {len(unions[2])}개")
        return result

    def api_get_json(ctx, path, referer="https://shop.bmw.co.kr/online/oem/model"):
        if not api_headers.get("token"):
            raise RuntimeError("API token header is not captured yet")
        headers = dict(api_headers)
        headers["referer"] = referer
        url = "https://shop.bmw.co.kr" + path
        resp = ctx.request.get(url, headers=headers, timeout=15000)
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"API failed: {path} {data.get('apiError')}")
        return data["response"]

    def collect_models_api(ctx):
        models, seen = [], set()
        page_no = 0
        while True:
            response = api_get_json(ctx, f"/shop/api/oem/model/list/3/{page_no}")
            for item in response.get("modelListDTOS", []):
                url = "https://shop.bmw.co.kr" + item.get("templatePath", "")
                if not is_product_url(url) or url in seen:
                    continue
                seen.add(url)
                models.append((item.get("editionName", "").strip() or _model_name_for_url(url), url))
            next_id = response.get("nextId")
            if next_id in (None, "", str(page_no)):
                break
            page_no = int(next_id)
        return models

    def fields_from_api(model_response, init_response=None):
        fields = []
        for group in model_response.get("trimMain", []) or []:
            title = group.get("title") or "옵션"
            options = []
            for opt in group.get("trimSubArrayList", []) or []:
                name = str(opt.get("trimNm", "")).strip()
                if not name:
                    continue
                selector = f'a.tooltip:has(img[alt*="{_css_attr_value(name)}"])'
                options.append({
                    "text": name,
                    "value": selector,
                    "selector": selector,
                    "pcode": opt.get("pcode", ""),
                    "soldout": opt.get("soldYn") == "Y",
                    "active": bool(opt.get("basicCh")),
                })
            if options:
                fields.append({
                    "kind": "option",
                    "id": "",
                    "name": "trim:" + title,
                    "selector": "",
                    "label": title,
                    "options": options,
                })

        fields.append(
            {
                "kind": "option",
                "id": "",
                "name": "결제방법 선택:현금|BMW금융",
                "selector": "",
                "label": "결제방법 선택",
                "options": [
                    {"text": "현금", "value": "label.section03_list:has-text('현금')", "selector": "label.section03_list:has-text('현금')"},
                    {"text": "BMW금융", "value": "label.section03_list:has-text('BMW금융')", "selector": "label.section03_list:has-text('BMW금융')"},
                ],
            }
        )
        return fields

    def scan_all_by_api(ctx, models):
        done = 0
        for idx, (name, url) in enumerate(models, 1):
            edition_id = url.rstrip("/").split("/")[-1]
            status(f"API 스캔 {idx}/{len(models)} — {name}")
            model_response = api_get_json(
                ctx,
                f"/shop/api/regular/model/init/{edition_id}",
                referer=url,
            )
            fields = fields_from_api(model_response)
            fields_json = json.dumps(fields, ensure_ascii=False, indent=2)
            fields_file_for_url(url).write_text(fields_json, encoding="utf-8")
            FIELDS_FILE.write_text(fields_json, encoding="utf-8")
            log(f"  [API] {name}: {len(fields)}개 항목 저장")
            done += 1
        return done

    def ensure_logged_scan_page(page, url):
        def load_product():
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try: page.wait_for_load_state("networkidle", timeout=4000)
            except Exception: pass
            log(f"  페이지: {page.url[:80]}")

        def click_login_entry():
            if has_option_panel(page) and not dealer_options_ready(page):
                status("딜러위치 로드를 위해 로그인 진입...")
                log("  딜러위치 미로드 — 로그인 진입")
                click_entry_button(page)
                page.wait_for_timeout(800)
            elif not has_option_panel(page):
                log("  옵션 패널 미감지 — 진입 버튼 클릭")
                click_entry_button(page)
                page.wait_for_timeout(800)

            try:
                page.wait_for_selector(".cBtn", timeout=1500)
                page.click(".cBtn")
                log("  모달 확인 클릭")
            except Exception:
                pass

            try:
                page.wait_for_url("**/customer.bmwgroup.com/**", timeout=5000)
            except Exception:
                pass

        def perform_login_if_needed():
            if "customer.bmwgroup.com" not in page.url:
                return False
            status("브라우저에서 직접 로그인 완료 후 아래 버튼 클릭")
            log("  로그인 페이지 감지 — 수동 로그인 대기")
            evt_q.put(("need_login", None))
            wait_cmd("login_done")
            return True

        for attempt in range(1, 4):
            if attempt > 1:
                log(f"  재시도 {attempt}/3 — 로그인 세션 확인")
            load_product()
            for _ in range(8):
                if dealer_options_ready(page):
                    break
                page.wait_for_timeout(250)
            if dealer_options_ready(page):
                break

            click_login_entry()
            performed_login = perform_login_if_needed()
            if performed_login:
                status("로그인 세션으로 상품 페이지 복귀...")
                load_product()

            for _ in range(16):
                if dealer_options_ready(page):
                    break
                page.wait_for_timeout(250)
            if dealer_options_ready(page):
                break
        else:
            raise RuntimeError("로그인 후에도 딜러위치 옵션이 로드되지 않아 이 차종 스캔을 중단했습니다.")

        fields = page.evaluate(SCAN_JS)
        has_dealer_data = any(
            f.get("kind") == "select" and any(
                str(o.get("text", "")).strip() not in ("딜러사 선택", "전시장 선택", "영업사원 선택")
                for o in f.get("options", [])
            )
            for f in fields
        )
        if not has_dealer_data:
            raise RuntimeError("딜러위치 옵션 없는 스캔 결과라 저장하지 않았습니다.")
        fields_json = json.dumps(fields, ensure_ascii=False, indent=2)
        fields_file_for_url(url).write_text(fields_json, encoding="utf-8")
        FIELDS_FILE.write_text(fields_json, encoding="utf-8")
        log(f"  스캔 결과: {len(fields)}개")
        return fields

    def ensure_logged_before_scan(page, models):
        if not models:
            return
        first_name, first_url = models[0]
        status("전체 스캔 전 로그인 상태 확인...")
        log(f"  선로그인 확인: {first_name}")
        page.goto(first_url, wait_until="domcontentloaded", timeout=20000)
        try: page.wait_for_load_state("networkidle", timeout=5000)
        except Exception: pass
        if api_headers.get("token"):
            log("  API 토큰 확인 완료 — 같은 브라우저 세션으로 전체 스캔 시작")
            return

        click_entry_button(page)
        page.wait_for_timeout(800)
        try:
            page.wait_for_selector(".cBtn", timeout=1500)
            page.click(".cBtn")
            log("  모달 확인 클릭")
        except Exception:
            pass
        try:
            page.wait_for_url("**/customer.bmwgroup.com/**", timeout=5000)
        except Exception:
            pass
        if "customer.bmwgroup.com" in page.url:
            status("브라우저에서 직접 로그인 완료 후 아래 버튼 클릭")
            log("  수동 로그인 대기 — 자동 로그인은 실행하지 않음")
            evt_q.put(("need_login", None))
            wait_cmd("login_done")

        page.goto(first_url, wait_until="domcontentloaded", timeout=20000)
        try: page.wait_for_load_state("networkidle", timeout=5000)
        except Exception: pass
        for _ in range(20):
            if api_headers.get("token"):
                break
            page.wait_for_timeout(250)
        if not api_headers.get("token"):
            raise RuntimeError("로그인 후 API 토큰을 캡처하지 못했습니다.")
        log("  API 토큰 캡처 완료 — 같은 브라우저 세션으로 전체 스캔 시작")

    try:
        with sync_playwright() as pw:
            status("로그인 Chrome 세션 연결 중...")
            ctx = None
            try:
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CHROME_DEBUG_PORT}")
                ctx = browser.contexts[0] if browser.contexts else None
                log("  열린 로그인 Chrome 세션에 연결")
            except Exception as e:
                log(f"  열린 Chrome 연결 실패 — 새 로그인 세션 실행: {e}")
            if ctx is None:
                status("전체 스캔 브라우저 실행 중...")
                ctx = pw.chromium.launch_persistent_context(
                    str(CHROME_PROFILE_DIR),
                    headless=False,
                    channel=BROWSER_CHANNEL,
                    slow_mo=0,
                    viewport=None,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    locale="ko-KR",
                    args=[
                        "--start-maximized",
                        "--disable-blink-features=AutomationControlled",
                        "--no-first-run",
                        f"--remote-debugging-port={CHROME_DEBUG_PORT}",
                    ],
                )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            install_fast_scan_routes(ctx)
            page.on("response", capture_api_headers)
            keep_browser = False
            try:
                status("전체 차종 목록 수집 중...")
                models = collect_models(page)
                if not models:
                    evt_q.put(("error", "스캔할 차종 URL을 찾지 못했습니다."))
                    return
                DISCOVERED_PRODUCT_MODELS.update({url: name for name, url in models})
                save_models_manifest(models)
                evt_q.put(("models_discovered", models))
                log(f"  전체 스캔 대상: {len(models)}개")
                ensure_logged_before_scan(page, models)
                try:
                    api_models = collect_models_api(ctx)
                    if api_models:
                        models = api_models
                        DISCOVERED_PRODUCT_MODELS.update({url: name for name, url in models})
                        save_models_manifest(models)
                        evt_q.put(("models_discovered", models))
                    done = scan_all_by_api(ctx, models)
                    status(f"전체 차종 API 스캔 완료 ({done}개)")
                    evt_q.put(("scan_all_done", done))
                    keep_browser = True
                except Exception as api_error:
                    log(f"  API 스캔 실패 — DOM 스캔으로 전환: {api_error}")
                    for idx, (name, url) in enumerate(models, 1):
                        status(f"전체 스캔 {idx}/{len(models)} — {name}")
                        log(f"[전체 스캔] {idx}/{len(models)} {name}: {url}")
                        ensure_logged_scan_page(page, url)
                    save_models_manifest(models)
                    status(f"전체 차종 스캔 완료 ({len(models)}개)")
                    evt_q.put(("scan_all_done", len(models)))
                    keep_browser = True
            finally:
                if keep_browser:
                    try:
                        ctx.unroute("**/*")
                        log("  스캔 완료: 이미지 차단 해제")
                    except Exception:
                        pass
                    status("전체 스캔 완료 — 로그인 브라우저 유지 중")
                    log("  로그인 세션 유지: 이 브라우저를 닫지 않고 구매하기에서 재사용합니다.")
                    keep_browser_until_closed(page, log)
                ctx.close()
    except Exception:
        import traceback
        evt_q.put(("error", traceback.format_exc()))


# ─── 구매 자동화 스레드 ───────────────────────────────────────────────────────

def run_buy(url: str, fill_data: list, dealer_defaults: dict | None = None):
    from playwright.sync_api import sync_playwright

    def log(m):    evt_q.put(("log", m))
    def status(m): evt_q.put(("status", m))
    def wait_cmd(exp):
        while True:
            try:
                if cmd_q.get(timeout=0.5) == exp: return
            except queue.Empty: pass

    def sel_of(f):
        if f.get("selector"): return f["selector"]
        if f.get("id"):   return f"#{f['id']}"
        if f.get("name"): return f"[name='{f['name']}']"
        return None

    def try_click(pg, sels, label, timeout=5000):
        for s in sels:
            try:
                pg.wait_for_selector(s, timeout=timeout)
                pg.click(s); log(f"  [OK] {label}"); return True
            except Exception: continue
        log(f"  [SKIP] {label}"); return False

    def select_value(pg, sel, value):
        try:
            pg.select_option(sel, value=str(value))
            return True
        except Exception:
            pass
        try:
            pg.select_option(sel, label=str(value))
            return True
        except Exception:
            return False

    def select_dealer_default(pg, index, desired, label):
        desired = str(desired or "").strip()
        if not desired:
            log(f"  [SKIP] {label} 고정값 없음")
            return False
        sel = f".revervation_wrap2 .section02 select:nth-of-type({index + 1})"
        try:
            pg.wait_for_selector(sel, timeout=10000)
        except Exception:
            log(f"  [ERR] {label} 드롭다운 없음")
            return False
        for _ in range(24):
            try:
                matched = pg.evaluate(
                    """
                    ([selector, desired]) => {
                        const norm = s => String(s || '').replace(/\\s+/g, '').toLowerCase();
                        const score = (text) => {
                            const a = norm(text), b = norm(desired);
                            if (!a || !b) return -1;
                            if (a === b) return 1000;
                            if (a.includes(b) || b.includes(a)) return 700 + Math.min(a.length, b.length);
                            let hit = 0;
                            for (const ch of b) if (a.includes(ch)) hit++;
                            const ratio = hit / Math.max(b.length, 1);
                            return ratio >= 0.65 ? Math.floor(ratio * 100) : -1;
                        };
                        const sel = document.querySelector(selector);
                        if (!sel) return null;
                        const options = Array.from(sel.options || []).filter(o => (o.textContent || '').trim());
                        let best = null;
                        for (const opt of options) {
                            const text = (opt.textContent || '').trim();
                            if (/^(딜러사|전시장|영업사원) 선택$/.test(text) && !opt.value) continue;
                            const s = score(text);
                            if (!best || s > best.score) best = {text, value: opt.value, score: s};
                        }
                        if (!best || best.score < 65) return null;
                        sel.value = best.value;
                        sel.dispatchEvent(new Event('input', {bubbles:true}));
                        sel.dispatchEvent(new Event('change', {bubbles:true}));
                        return best;
                    }
                    """,
                    [sel, desired],
                )
                if matched:
                    log(f"  [OK] {label} 매칭: {desired} -> {matched.get('text')}")
                    pg.wait_for_timeout(900)
                    return True
            except Exception:
                pass
            pg.wait_for_timeout(250)
        log(f"  [ERR] {label} 매칭 실패: {desired}")
        return False

    def select_fixed_dealer(pg):
        defaults = dealer_defaults or {}
        if not any(str(v or "").strip() for v in defaults.values()):
            return
        status("고정 딜러위치 자동 선택 중...")
        select_dealer_default(pg, 0, defaults.get("dealer"), "딜러사")
        select_dealer_default(pg, 1, defaults.get("showroom"), "전시장")
        select_dealer_default(pg, 2, defaults.get("salesperson"), "영업사원")

    def click_purchase_button(pg):
        status("사이트 구매하기 버튼 클릭 중...")
        selectors = [
            ".revervation_btn:not([disabled])",
            ".revervation_btn_alaram:not([disabled])",
            "button:has-text('구매하기'):not([disabled])",
            "a:has-text('구매하기')",
            "button:has-text('재고 알림 신청하기'):not([disabled])",
            "a:has-text('재고 알림 신청하기')",
            "button:has-text('신청하기'):not([disabled])",
            "button:has-text('예약하기'):not([disabled])",
        ]
        for _ in range(20):
            for sel in selectors:
                try:
                    el = pg.locator(sel).first
                    if el.count() and el.is_visible(timeout=200):
                        try:
                            disabled = el.get_attribute("disabled", timeout=200)
                        except Exception:
                            disabled = None
                        if disabled is not None:
                            continue
                        el.scroll_into_view_if_needed(timeout=1000)
                        el.click(force=True, timeout=1500)
                        log(f"  [OK] 사이트 구매 버튼 클릭: {sel}")
                        return True
                except Exception:
                    continue
            pg.wait_for_timeout(150)
        clicked = pg.evaluate("""
        () => {
            const words = ['구매하기', '재고 알림 신청하기', '신청하기', '예약하기'];
            const els = Array.from(document.querySelectorAll('button,a,[role=button]'));
            for (const el of els) {
                const text = (el.innerText || el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                if (!words.some(w => text.includes(w))) continue;
                if (rect.width < 8 || rect.height < 8) continue;
                if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
                el.scrollIntoView({block:'center'});
                el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window}));
                el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, view:window}));
                el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
                return text;
            }
            return '';
        }
        """)
        if clicked:
            log(f"  [OK] 사이트 구매 버튼 클릭(JS): {clicked}")
            return True
        log("  [ERR] 사이트 구매 버튼 클릭 실패")
        return False

    def click_visible_text_card(pg, label, include_words, exclude_words=None, timeout_ms=8000):
        exclude_words = exclude_words or []
        deadline = time.time() + (timeout_ms / 1000)
        while time.time() < deadline:
            try:
                clicked = pg.evaluate(
                    """
                    ([includeWords, excludeWords]) => {
                        const visible = el => {
                            const rect = el.getBoundingClientRect();
                            const style = getComputedStyle(el);
                            return rect.width > 20 && rect.height > 20 &&
                                style.visibility !== 'hidden' && style.display !== 'none';
                        };
                        const score = el => {
                            const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
                            if (!text) return -1;
                            if (excludeWords.some(w => text.includes(w))) return -1;
                            if (!includeWords.some(w => text.includes(w))) return -1;
                            let s = 0;
                            if (includeWords.some(w => text === w)) s += 1000;
                            if (/button|a|label/i.test(el.tagName) || el.getAttribute('role') === 'button') s += 100;
                            const rect = el.getBoundingClientRect();
                            s += Math.max(0, 500 - Math.abs(rect.top));
                            s -= text.length;
                            return s;
                        };
                        const candidates = Array.from(document.querySelectorAll('button,a,label,li,div,span,[role=button]'))
                            .filter(visible)
                            .map(el => ({el, s: score(el)}))
                            .filter(x => x.s >= 0)
                            .sort((a, b) => b.s - a.s);
                        const target = candidates[0]?.el;
                        if (!target) return '';
                        target.scrollIntoView({block:'center', inline:'center'});
                        target.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window}));
                        target.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, view:window}));
                        target.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
                        return (target.innerText || target.textContent || '').trim().replace(/\\s+/g, ' ');
                    }
                    """,
                    [include_words, exclude_words],
                )
                if clicked:
                    log(f"  [OK] {label} 클릭: {clicked}")
                    return True
            except Exception:
                pass
            pg.wait_for_timeout(200)
        log(f"  [ERR] {label} 클릭 실패")
        return False

    def click_pass_skt(pg):
        for sel in ["#telcomSK", "button.mobileCoCheck#telcomSK", "button.mobileCoCheck:has-text('SKT')"]:
            try:
                el = pg.locator(sel).first
                if el.count() and el.is_visible(timeout=700):
                    el.click(force=True, timeout=1500)
                    log(f"  [OK] PASS SKT 클릭: {sel}")
                    return True
            except Exception:
                continue
        return click_visible_text_card(pg, "SKT", ["SKT", "SK텔레콤", "에스케이"], ["알뜰폰"], timeout_ms=5000)

    def wait_pass_method_screen(pg, timeout_ms=12000):
        try:
            pg.wait_for_function(
                """() => {
                    const text = document.body?.innerText || '';
                    return text.includes('인증방법을') ||
                        text.includes('QR코드 인증') ||
                        Array.from(document.querySelectorAll('button.mobileCertMethodCheck')).some(el =>
                            (el.innerText || el.textContent || '').includes('QR코드')
                        );
                }""",
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False

    def pass_debug(pg, name):
        try:
            path = SAVE_DIR / f"pass_debug_{name}.png"
            pg.screenshot(path=str(path), full_page=True)
            log(f"  [DEBUG] PASS 화면 저장: {path.name}")
        except Exception as e:
            log(f"  [DEBUG] PASS 화면 저장 실패: {e}")

    def dismiss_pass_alert(pg):
        try:
            clicked = pg.evaluate("""
            () => {
                const body = document.body?.innerText || '';
                if (!body.includes('인증방법을 선택')) return '';
                const buttons = Array.from(document.querySelectorAll('button,a,[role=button]'));
                const target = buttons.find(el => {
                    const text = (el.innerText || el.textContent || '').trim();
                    const rect = el.getBoundingClientRect();
                    return text === '확인' && rect.width > 30 && rect.height > 20 &&
                        getComputedStyle(el).display !== 'none' &&
                        getComputedStyle(el).visibility !== 'hidden';
                });
                if (!target) return '';
                target.click();
                return '인증방법 선택 경고 확인';
            }
            """)
            if clicked:
                log(f"  [WARN] {clicked} — QR 선택 후 재시도")
                pg.wait_for_timeout(500)
                return True
        except Exception:
            pass
        return False

    def wait_for_pass_qr_screen(pg, timeout_ms=8000):
        try:
            pg.wait_for_function(
                """() => {
                    const text = document.body?.innerText || '';
                    if (text.includes('인증방법을 선택')) return false;
                    if (text.includes('본인확인 이용 동의')) return false;
                    const media = Array.from(document.querySelectorAll('canvas, img')).find(el => {
                        const rect = el.getBoundingClientRect();
                        const src = (el.getAttribute('src') || '').toLowerCase();
                        return rect.width >= 120 && rect.height >= 120 &&
                            (el.tagName.toLowerCase() === 'canvas' || src.includes('qr') || src.startsWith('data:image'));
                    });
                    return !!media || text.includes('QR코드를 스캔') || text.includes('QR 코드를 스캔');
                }""",
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False

    def click_pass_qr(pg):
        for sel in [
            "button.mobileCertMethodCheck:has-text('QR코드 인증')",
            "button.mobileCertMethodCheck:has-text('QR코드')",
            "li.cert_item:has-text('QR코드 인증') button",
        ]:
            try:
                el = pg.locator(sel).first
                if el.count() and el.is_visible(timeout=700):
                    el.scroll_into_view_if_needed(timeout=1000)
                    el.click(force=True, timeout=1500)
                    log(f"  [OK] PASS QR 버튼 클릭: {sel}")
                    pg.wait_for_timeout(500)
                    return True
            except Exception:
                continue
        for _ in range(40):
            try:
                clicked = pg.evaluate("""
                () => {
                    const visible = el => {
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 20 && rect.height > 20 &&
                            style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const textOf = el => (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
                    const candidates = Array.from(document.querySelectorAll(
                        'button.mobileCertMethodCheck, button[id*="qr" i], button[class*="qr" i], [role=button][id*="qr" i], [role=button][class*="qr" i]'
                    ));
                    let target = candidates.find(el => {
                        const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
                        return visible(el) && text.includes('QR') &&
                            !text.includes('PASS 인증') &&
                            !text.includes('문자') && !text.includes('SMS');
                    });
                    if (!target) {
                        const all = Array.from(document.querySelectorAll('button.mobileCertMethodCheck, button, a, label, [role=button]'));
                        target = all.find(el => {
                            const text = textOf(el);
                            return visible(el) && /QR\\s*코드|QR/.test(text) &&
                                !text.includes('PASS 인증') &&
                                !text.includes('문자') && !text.includes('SMS') &&
                                !text.includes('다음') && !text.includes('확인');
                        });
                    }
                    if (!target) return '';
                    target.scrollIntoView({block:'center', inline:'center'});
                    target.click();
                    return textOf(target) || target.id || target.className || 'QR target';
                }
                """)
                if clicked:
                    log(f"  [OK] PASS QR 클릭: {clicked}")
                    return True
            except Exception:
                pass
            pg.wait_for_timeout(250)
        log("  [ERR] PASS QR 클릭 실패")
        return False

    def pass_agree_and_next(pg):
        status("PASS 본인확인 동의 및 다음 클릭...")
        try:
            pg.wait_for_function(
                """() => {
                    const text = document.body?.innerText || '';
                    return text.includes('본인확인 이용 동의') || text.includes('다음');
                }""",
                timeout=8000,
            )
        except Exception:
            log("  [SKIP] PASS 동의 화면 미감지")
            return False

        checked = pg.evaluate("""
        () => {
            const clickLike = el => {
                el.scrollIntoView({block:'center', inline:'center'});
                el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window}));
                el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, view:window}));
                el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
            };
            const inputs = Array.from(document.querySelectorAll('input[type=checkbox]'));
            let count = 0;
            for (const input of inputs) {
                if (!input.checked) {
                    clickLike(input);
                    if (!input.checked) input.checked = true;
                    input.dispatchEvent(new Event('input', {bubbles:true}));
                    input.dispatchEvent(new Event('change', {bubbles:true}));
                }
                count++;
            }
            if (!count) {
                const candidates = Array.from(document.querySelectorAll('button,label,span,div,a,[role=checkbox],[class*=check]'));
                const target = candidates.find(el => {
                    const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
                    const rect = el.getBoundingClientRect();
                    return text.includes('본인확인 이용 동의') &&
                        rect.width > 20 && rect.height > 20 &&
                        getComputedStyle(el).display !== 'none' &&
                        getComputedStyle(el).visibility !== 'hidden';
                });
                if (target) {
                    const row = target.closest('li, label, button, div') || target;
                    const rect = row.getBoundingClientRect();
                    const leftCheck = document.elementFromPoint(rect.left + 24, rect.top + rect.height / 2);
                    clickLike(leftCheck || target);
                    count = 1;
                }
            }
            return count;
        }
        """)
        log(f"  PASS 동의 체크: {checked}개")
        pg.wait_for_timeout(300)

        for _ in range(20):
            clicked = pg.evaluate("""
            () => {
                const clickLike = el => {
                    el.scrollIntoView({block:'center', inline:'center'});
                    el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window}));
                    el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, view:window}));
                    el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
                };
                const candidates = Array.from(document.querySelectorAll('button,a,[role=button],input[type=button],input[type=submit]'));
                const target = candidates.find(el => {
                    const text = (el.innerText || el.textContent || el.value || '').trim().replace(/\\s+/g, ' ');
                    const rect = el.getBoundingClientRect();
                    return text.includes('다음') &&
                        rect.width > 20 && rect.height > 20 &&
                        !el.disabled &&
                        el.getAttribute('aria-disabled') !== 'true' &&
                        getComputedStyle(el).display !== 'none' &&
                        getComputedStyle(el).visibility !== 'hidden';
                });
                if (!target) return '';
                clickLike(target);
                return (target.innerText || target.textContent || target.value || '').trim();
            }
            """)
            if clicked:
                log(f"  [OK] PASS 다음 클릭: {clicked}")
                pg.wait_for_timeout(700)
                if dismiss_pass_alert(pg):
                    return False
                return True
            pg.wait_for_timeout(250)
        log("  [ERR] PASS 다음 버튼 클릭 실패")
        return False

    def pass_screen_state(pg):
        try:
            return pg.evaluate("""
            () => {
                const text = document.body?.innerText || '';
                const url = location.href;
                const visible = el => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 15 && rect.height > 15 &&
                        style.display !== 'none' && style.visibility !== 'hidden';
                };
                const hasTelco = text.includes('이용중인 통신사') ||
                    !!document.querySelector('#telcomSK, button.mobileCoCheck');
                const hasMethod = text.includes('인증방법을') ||
                    Array.from(document.querySelectorAll('button.mobileCertMethodCheck'))
                        .some(el => visible(el) && (el.innerText || el.textContent || '').includes('QR'));
                const hasAgree = text.includes('본인확인 이용 동의') ||
                    Array.from(document.querySelectorAll('button,a,[role=button],input[type=button],input[type=submit]'))
                        .some(el => visible(el) && ((el.innerText || el.textContent || el.value || '').trim()).includes('다음'));
                const hasQrMedia = Array.from(document.querySelectorAll('canvas, img')).some(el => {
                    const rect = el.getBoundingClientRect();
                    const src = (el.getAttribute('src') || '').toLowerCase();
                    return rect.width >= 120 && rect.height >= 120 &&
                        (el.tagName.toLowerCase() === 'canvas' || src.includes('qr') || src.startsWith('data:image'));
                });
                if (hasQrMedia || text.includes('QR코드를 스캔') || text.includes('QR 코드를 스캔')) return 'qr';
                if (hasAgree && !hasMethod) return 'agree';
                if (hasAgree && text.includes('본인확인 이용 동의')) return 'agree';
                if (hasMethod) return 'method';
                if (hasTelco) return 'telco';
                return url;
            }
            """)
        except Exception as e:
            return f"error:{e}"

    def advance_pass_to_qr(pg, max_seconds=35):
        deadline = time.time() + max_seconds
        last_state = None
        step = 0
        while time.time() < deadline:
            step += 1
            dismiss_pass_alert(pg)
            state = pass_screen_state(pg)
            if state != last_state:
                log(f"  PASS 상태: {state}")
                last_state = state

            if state == "qr":
                return True

            if state == "telco":
                status("PASS 통신사 SKT 선택...")
                if not click_pass_skt(pg):
                    pass_debug(pg, f"pass_state_{step:02d}_telco_click_failed")
                    return False
                wait_pass_method_screen(pg, timeout_ms=12000)
                pg.wait_for_timeout(500)
                continue

            if state == "method":
                status("PASS QR 인증 선택...")
                if not click_pass_qr(pg):
                    pass_debug(pg, f"pass_state_{step:02d}_qr_click_failed")
                    return False
                pg.wait_for_timeout(700)
                continue

            if state == "agree":
                if not pass_agree_and_next(pg):
                    pass_debug(pg, f"pass_state_{step:02d}_agree_failed")
                    return False
                if wait_for_pass_qr_screen(pg, timeout_ms=10000):
                    return True
                pg.wait_for_timeout(700)
                continue

            pg.wait_for_timeout(500)

        pass_debug(pg, "pass_state_timeout")
        return wait_for_pass_qr_screen(pg, timeout_ms=1500)

    def all_context_pages(browser, fallback_ctx):
        pages = []
        contexts = list(browser.contexts) if browser else []
        if fallback_ctx and fallback_ctx not in contexts:
            contexts.append(fallback_ctx)
        for c in contexts:
            try:
                pages.extend(c.pages)
            except Exception:
                pass
        return pages

    def close_stale_pass_pages(browser, fallback_ctx):
        closed = 0
        for p in all_context_pages(browser, fallback_ctx):
            try:
                if "nice.checkplus.co.kr" in p.url:
                    p.close()
                    closed += 1
            except Exception:
                pass
        if closed:
            log(f"  이전 PASS 인증창 정리: {closed}개")

    def find_pass_popup(browser, fallback_ctx, timeout_ms=15000, exclude_pages=None):
        exclude_pages = set(exclude_pages or [])
        deadline = time.time() + (timeout_ms / 1000)
        while time.time() < deadline:
            pages = all_context_pages(browser, fallback_ctx)
            for pass_no in (0, 1):
                for p in reversed(pages):
                    if pass_no == 0 and p in exclude_pages:
                        continue
                    try:
                        if "nice.checkplus.co.kr" in p.url:
                            try: p.wait_for_load_state("domcontentloaded", timeout=3000)
                            except Exception: pass
                            try: p.bring_to_front()
                            except Exception: pass
                            log(f"  [PASS 팝업 감지] {p.url[:90]}")
                            return p
                    except Exception:
                        continue
                    try:
                        body = p.locator("body").inner_text(timeout=300)
                        if "이용중인 통신사" in body or "인증방법을" in body:
                            try: p.bring_to_front()
                            except Exception: pass
                            log(f"  [PASS 팝업 텍스트 감지] {p.url[:90]}")
                            return p
                    except Exception:
                        pass
            time.sleep(0.25)
        return None

    def wait_option_panel(pg):
        try:
            pg.wait_for_function(
                """() => {
                    const body = document.body?.innerText || '';
                    return body.includes('익스테리어') && body.includes('인테리어') &&
                        document.querySelectorAll('.revervation_wrap2, a.tooltip, select, input[type=radio]').length > 0;
                }""",
                timeout=10000,
            )
            return True
        except Exception:
            return False

    def confirm_purchase_modal(pg):
        status("구매 확인 모달 처리 중...")
        try:
            pg.wait_for_function(
                """() => {
                    const text = document.body?.innerText || '';
                    return text.includes('개인정보 수집') || text.includes('결제 후 옵션') || text.includes('선택하신 옵션');
                }""",
                timeout=5000,
            )
        except Exception:
            log("  [SKIP] 구매 확인 모달 미감지")
            return False
        checked = pg.evaluate("""
        () => {
            const modal = Array.from(document.querySelectorAll('body *')).reverse().find(el => {
                const text = (el.innerText || '').trim();
                const rect = el.getBoundingClientRect();
                return rect.width > 250 && rect.height > 180 &&
                    (text.includes('개인정보 수집') || text.includes('결제 후 옵션') || text.includes('선택하신 옵션'));
            }) || document.body;
            const boxes = Array.from(modal.querySelectorAll('input[type=checkbox]'));
            for (const box of boxes) {
                if (!box.checked) {
                    box.click();
                    if (!box.checked) box.checked = true;
                    box.dispatchEvent(new Event('input', {bubbles:true}));
                    box.dispatchEvent(new Event('change', {bubbles:true}));
                }
            }
            return boxes.length;
        }
        """)
        log(f"  동의 체크: {checked}개")
        pg.wait_for_timeout(300)
        for sel in [
            ".cBtn:not([disabled])",
            "button:has-text('확인'):not([disabled])",
            "a:has-text('확인')",
            "button:has-text('다음'):not([disabled])",
        ]:
            try:
                el = pg.locator(sel).first
                if el.count() and el.is_visible(timeout=500):
                    el.click(force=True, timeout=1500)
                    log(f"  [OK] 모달 확인 클릭: {sel}")
                    return True
            except Exception:
                continue
        clicked = pg.evaluate("""
        () => {
            const els = Array.from(document.querySelectorAll('button,a,[role=button]'));
            const target = els.find(el => {
                const text = (el.innerText || el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                return text.includes('확인') && !text.includes('취소') &&
                    rect.width > 20 && rect.height > 15 &&
                    !el.disabled && el.getAttribute('aria-disabled') !== 'true';
            });
            if (!target) return '';
            target.scrollIntoView({block:'center'});
            target.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window}));
            target.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, view:window}));
            target.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
            return (target.innerText || target.textContent || '').trim();
        }
        """)
        if clicked:
            log(f"  [OK] 모달 확인 클릭(JS): {clicked}")
            return True
        log("  [ERR] 모달 확인 클릭 실패")
        return False

    try:
        with sync_playwright() as pw:
            status("로그인 브라우저 세션 연결 중...")
            browser = None
            ctx = None
            try:
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CHROME_DEBUG_PORT}")
                ctx = browser.contexts[0] if browser.contexts else None
                log("  열린 로그인 브라우저에 연결")
            except Exception as e:
                log(f"  열린 브라우저 연결 실패 — 저장된 프로필로 재사용: {e}")

            if ctx is None:
                ctx = pw.chromium.launch_persistent_context(
                    str(CHROME_PROFILE_DIR),
                    headless=False,
                    channel=BROWSER_CHANNEL,
                    slow_mo=0,
                    viewport=None,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    locale="ko-KR",
                    args=[
                        "--start-maximized",
                        "--disable-blink-features=AutomationControlled",
                        "--no-first-run",
                        f"--remote-debugging-port={CHROME_DEBUG_PORT}",
                    ],
                )
            try:
                ctx.unroute("**/*")
                log("  구매 흐름: 스캔용 이미지 차단 해제")
            except Exception:
                pass
            page = ctx.pages[-1] if ctx.pages else ctx.new_page()

            # 상품 페이지
            status("상품 페이지 이동...")
            page.goto(url, wait_until="domcontentloaded")
            try: page.wait_for_load_state("networkidle", timeout=15000)
            except Exception: pass

            # 로그인 필요
            if "customer.bmwgroup.com" in page.url:
                status("브라우저에서 직접 로그인 완료 후 아래 버튼 클릭")
                log("  로그인 페이지 감지 — 수동 로그인 대기")
                evt_q.put(("need_login", None))
                wait_cmd("login_done")
                page.goto(url, wait_until="domcontentloaded")
                try: page.wait_for_load_state("networkidle", timeout=15000)
                except Exception: pass

            # 옵션 패널 대기: 익스테리어/인테리어를 먼저 선택해야 함
            status("옵션 패널 대기 중...")
            if not wait_option_panel(page):
                evt_q.put(("error", "상품 옵션 패널을 찾지 못했습니다. 로그인 상태와 상품 페이지를 확인하세요."))
                return

            # 폼 입력
            status("폼 자동 입력 중...")
            for field, value in fill_data:
                if value == "" and field["kind"] != "check": continue
                if str(field.get("name", "")).startswith("dealer:"):
                    continue
                sel = sel_of(field)
                if not sel and field["kind"] != "option": continue
                try:
                    k = field["kind"]
                    if k == "select":
                        if not select_value(page, sel, value):
                            raise RuntimeError(f"선택값을 찾지 못했습니다: {value}")
                        page.eval_on_selector(sel, "el => el.dispatchEvent(new Event('change', {bubbles:true}))")
                        page.wait_for_timeout(150)
                    elif k == "option":
                        page.click(str(value), force=True)
                        page.wait_for_timeout(250)
                    elif k == "input": page.fill(sel, str(value))
                    elif k == "check": page.check(sel) if value else page.uncheck(sel)
                    log(f"  [OK] {field.get('label', sel)} = {str(value)[:40]}")
                except Exception as e:
                    log(f"  [ERR] {field.get('label', sel)}: {e}")
                page.wait_for_timeout(50)
            select_fixed_dealer(page)

            try: page.screenshot(path=str(SAVE_DIR/"filled.png"), full_page=True)
            except Exception: pass
            log("  폼 입력 완료")
            close_stale_pass_pages(browser, ctx)
            pages_before_pass = set(all_context_pages(browser, ctx))
            if not click_purchase_button(page):
                evt_q.put(("error", "폼 선택은 완료했지만 사이트 구매하기 버튼을 찾거나 클릭하지 못했습니다."))
                return
            page.wait_for_timeout(500)
            confirm_purchase_modal(page)
            page.wait_for_timeout(500)

            # 본인인증
            status("본인인증 진행...")
            popup = find_pass_popup(browser, ctx, timeout_ms=4000, exclude_pages=pages_before_pass)
            if popup is None:
                pages_before_pass = set(all_context_pages(browser, ctx))
                try_click(page, [
                    "button:has-text('본인인증')", "a:has-text('본인인증')",
                    "button:has-text('인증하기')", "a:has-text('인증하기')",
                    "button:has-text('휴대폰 인증')", "a:has-text('휴대폰 인증')",
                    ".btn-cert", ".cert-btn", "#btnCert", "#btn_cert",
                    "[class*='identity']", "[class*='verify']",
                ], "본인인증 버튼", timeout=3000)
                popup = find_pass_popup(browser, ctx, timeout_ms=15000, exclude_pages=pages_before_pass)
            if popup is None:
                evt_q.put(("error", "휴대폰 본인확인 PASS 창을 감지하지 못했습니다."))
                return

            status("PASS QR 화면까지 진행 중...")
            if not advance_pass_to_qr(popup):
                pass_debug(popup, "99_qr_not_ready")
                evt_q.put(("error", "PASS QR 화면으로 전환하지 못했습니다. screenshots/pass_debug_*.png를 확인하세요."))
                keep_browser_until_closed(page, log)
                return

            time.sleep(2)
            qr_path = str(SAVE_DIR / "qr_screen.png")
            try: (popup if popup != page else page).screenshot(path=qr_path, full_page=True)
            except Exception: pass

            status("QR 화면 표시 완료 — 이후 단계는 브라우저에서 수동 진행")
            evt_q.put(("qr", qr_path))
            evt_q.put(("qr_ready", qr_path))
            log("  QR 화면까지 자동 진행 완료 — 후속 작업은 사용자가 브라우저에서 수동 처리")
            keep_browser_until_closed(page, log)

    except Exception:
        import traceback
        evt_q.put(("error", traceback.format_exc()))


# ═══════════════════════════════════════════════════════════════════════════
#  GUI
# ═══════════════════════════════════════════════════════════════════════════

class BMWApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("BMW Online Exclusive — 구매 자동화")
        self.geometry("660x760")
        self.resizable(True, True)
        self.configure(bg="#f0f0f0")

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TLabel",    background="#f0f0f0", font=("맑은 고딕", 9))
        style.configure("TCombobox", font=("맑은 고딕", 9))
        style.configure("TEntry",    font=("맑은 고딕", 9))

        self._fields: list = []
        self._field_widgets: list = []
        self._models = {name: url for name, url in PRODUCT_MODELS}
        self._load_models_manifest()
        first_name = next(iter(self._models), PRODUCT_MODELS[0][0])
        self._model_var = tk.StringVar(value=first_name)
        self._url_var = tk.StringVar(value=self._models.get(first_name, PRODUCT_URL))
        self._dealer_var = tk.StringVar()
        self._showroom_var = tk.StringVar()
        self._salesperson_var = tk.StringVar()

        self._build()
        self._load_saved()
        self._poll()

    def _load_models_manifest(self):
        models = load_models_manifest()
        if not models:
            return
        DISCOVERED_PRODUCT_MODELS.update({url: name for name, url in models})
        for name, url in models:
            self._models[name] = url

    def _build(self):
        # 헤더
        hdr = tk.Frame(self, bg="#1a1a2e", height=50)
        hdr.pack(fill=tk.X); hdr.pack_propagate(False)
        tk.Label(hdr, text="BMW Online Exclusive  구매 자동화",
                 font=("맑은 고딕", 12, "bold"), bg="#1a1a2e", fg="white"
                 ).pack(side=tk.LEFT, padx=16, pady=14)

        # 차종 / URL
        uf = tk.Frame(self, bg="#f0f0f0")
        uf.pack(fill=tk.X, padx=16, pady=(10, 0))
        tk.Label(uf, text="차종", font=("맑은 고딕", 8, "bold"),
                 bg="#f0f0f0", fg="#555").pack(anchor=tk.W)
        model_box = ttk.Combobox(
            uf, textvariable=self._model_var, values=list(self._models.keys()),
            state="readonly", font=("맑은 고딕", 9)
        )
        self._model_box = model_box
        model_box.pack(fill=tk.X, pady=(2, 6))
        model_box.bind("<<ComboboxSelected>>", self._on_model_change)
        tk.Label(uf, text="상품 URL", font=("맑은 고딕", 8, "bold"),
                 bg="#f0f0f0", fg="#555").pack(anchor=tk.W)
        uc = tk.Frame(uf, bg="white", highlightthickness=1, highlightbackground="#ccc")
        uc.pack(fill=tk.X, pady=(2, 0))
        ttk.Entry(uc, textvariable=self._url_var,
                  font=("맑은 고딕", 9)).pack(fill=tk.X, padx=8, pady=5)

        dealer_box = tk.LabelFrame(
            self, text="고정 딜러위치", bg="#f0f0f0", fg="#444",
            font=("맑은 고딕", 8, "bold"), padx=8, pady=6
        )
        dealer_box.pack(fill=tk.X, padx=16, pady=(8, 0))
        dealer_grid = tk.Frame(dealer_box, bg="#f0f0f0")
        dealer_grid.pack(fill=tk.X)
        dealer_inputs = [
            ("딜러사", self._dealer_var),
            ("전시장", self._showroom_var),
            ("영업사원", self._salesperson_var),
        ]
        for col, (label, var) in enumerate(dealer_inputs):
            tk.Label(dealer_grid, text=label, bg="#f0f0f0", fg="#555",
                     font=("맑은 고딕", 8)).grid(row=0, column=col, sticky="w", padx=(0, 6))
            ttk.Entry(dealer_grid, textvariable=var, font=("맑은 고딕", 9)).grid(
                row=1, column=col, sticky="ew", padx=(0, 8)
            )
            dealer_grid.columnconfigure(col, weight=1)

        # 버튼
        bar = tk.Frame(self, bg="#f0f0f0", pady=10)
        bar.pack(fill=tk.X, padx=16)
        self._btn_open = self._btn(bar, "Chrome 열기", "#1a5a8a", self._on_open,  width=14)
        self._btn_scan_all = self._btn(bar, "전체 스캔", "#2f6f4e", self._on_scan_all, width=10)
        self._btn_save = self._btn(bar, "설정 저장",   "#555",    self._on_save,  width=10)
        self._btn_buy  = self._btn(bar, "구매하기",    "#c8000a", self._on_buy,   width=10, state=tk.DISABLED)

        # 진행 중 버튼 (숨김)
        self._btn_login = tk.Button(self, text="로그인 완료 — 계속 진행",
                                    font=("맑은 고딕", 10, "bold"),
                                    bg="#e07000", fg="white", relief=tk.FLAT,
                                    padx=14, pady=8, cursor="hand2",
                                    command=self._on_login_done)
        self._btn_qr = tk.Button(self, text="QR 스캔 완료",
                                  font=("맑은 고딕", 10, "bold"),
                                  bg="#444466", fg="white", relief=tk.FLAT,
                                  padx=14, pady=8, cursor="hand2",
                                  command=self._on_qr)
        self._btn_submit = tk.Button(self, text="최종 제출",
                                      font=("맑은 고딕", 10, "bold"),
                                      bg="#882222", fg="white", relief=tk.FLAT,
                                      padx=14, pady=8, cursor="hand2",
                                      command=self._on_submit)

        # 상태
        self._status_var = tk.StringVar(value="'Chrome 열기'로 로그인한 뒤 '전체 스캔' 클릭")
        tk.Label(self, textvariable=self._status_var,
                 font=("맑은 고딕", 8), bg="#dde", fg="#333",
                 anchor=tk.W, padx=10, pady=4).pack(fill=tk.X)

        # 폼 영역
        wrap = tk.Frame(self, bg="#f0f0f0")
        wrap.pack(fill=tk.BOTH, expand=True, padx=16, pady=(10, 0))
        tk.Label(wrap, text="구매 정보 입력",
                 font=("맑은 고딕", 9, "bold"), bg="#f0f0f0", fg="#444").pack(anchor=tk.W)

        card = tk.Frame(wrap, bg="white", highlightthickness=1, highlightbackground="#ccc")
        card.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        self._canvas = tk.Canvas(card, bg="white", highlightthickness=0)
        sb = ttk.Scrollbar(card, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._inner = tk.Frame(self._canvas, bg="white")
        self._cwin  = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>", lambda e: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfig(
            self._cwin, width=e.width))
        self._canvas.bind_all("<MouseWheel>", lambda e: self._canvas.yview_scroll(
            int(-1*(e.delta/120)), "units"))

        tk.Label(self._inner, bg="white", fg="#aaa", font=("맑은 고딕", 9),
                 text="'Chrome 열기'는 로그인용 브라우저만 엽니다.\n로그인 완료 후 '전체 스캔'을 클릭하세요."
                 ).pack(pady=40)

        # 로그
        self._log_box = scrolledtext.ScrolledText(
            self, height=5, font=("Consolas", 8),
            bg="#1e1e1e", fg="#ccffcc", state=tk.DISABLED, relief=tk.FLAT)
        self._log_box.pack(fill=tk.X, pady=(4, 0))

    def _btn(self, parent, text, color, cmd, width=10, state=tk.NORMAL):
        b = tk.Button(parent, text=text, font=("맑은 고딕", 9, "bold"),
                      bg=color, fg="white", relief=tk.FLAT, width=width,
                      padx=6, pady=7, cursor="hand2", state=state, command=cmd)
        b.pack(side=tk.LEFT, padx=(0, 6))
        return b

    def _field_key(self, f): return f.get("id") or f.get("name") or f.get("selector") or f.get("label") or ""

    def _normalize_fields(self, fields):
        normalized = []
        used = set()

        def add(field):
            opts = []
            seen_opts = set()
            for opt in field.get("options", []):
                text = str(opt.get("text", "")).strip()
                value = opt.get("value", text)
                if not text or text in seen_opts:
                    continue
                seen_opts.add(text)
                new_opt = dict(opt)
                new_opt["text"] = text
                new_opt["value"] = value
                opts.append(new_opt)
            if field.get("kind") in ("select", "option") and not opts:
                return
            new_field = dict(field)
            if opts:
                new_field["options"] = opts
            key = new_field.get("name") or new_field.get("selector") or new_field.get("label")
            if key in used:
                return
            used.add(key)
            normalized.append(new_field)

        label_map = {
            "딜러사 선택": "딜러사",
            "전시장 선택": "전시장",
            "영업사원 선택": "영업사원",
        }
        for label in ("익스테리어", "인테리어"):
            for f in fields:
                if f.get("kind") == "option" and f.get("label") == label:
                    add(f)
                    break

        for f in fields:
            if f.get("kind") == "option" and f.get("label") == "결제방법 선택":
                add(f)
                break

        return normalized

    def _on_model_change(self, _event=None):
        url = self._models.get(self._model_var.get())
        if url:
            self._url_var.set(url)
            self._load_current_model_data(show_empty=True)

    def _load_saved(self):
        self._load_current_model_data(show_empty=False)

    def _load_current_model_data(self, show_empty: bool):
        url = self._url_var.get().strip() or PRODUCT_URL
        fields_file = fields_file_for_url(url)
        config_file = config_file_for_url(url)
        if not fields_file.exists():
            if FIELDS_FILE.exists() and not show_empty:
                fields_file = FIELDS_FILE
            else:
                self._render_empty(
                    "이 차종은 아직 스캔된 구매 정보가 없습니다.\n"
                    "'Chrome 열기'로 로그인한 뒤 '전체 스캔'을 누르세요."
                )
                self._set_status("차종 URL 적용 — 로그인 후 '전체 스캔'으로 옵션을 스캔하세요")
                return
        try:
            fields = json.loads(fields_file.read_text(encoding="utf-8"))
            self._render_fields(fields)
        except Exception as e:
            self._log(f"{fields_file.name} 로드 오류: {e}"); return
        try:
            cfg, config_file = load_config_for_url(url)
        except Exception as e:
            self._log(f"{config_file.name} 로드 오류: {e}")
            return
        if not cfg:
            self._set_status(f"{len(fields)}개 항목 로드 — 값 선택 후 '설정 저장' → '구매하기'")
            return
        try:
            model_name = cfg.get("__model__")
            if model_name in self._models:
                self._model_var.set(model_name)
            self._url_var.set(cfg.get("__url__", PRODUCT_URL))
            dealer_defaults = cfg.get("__dealer_defaults__", {})
            self._dealer_var.set(dealer_defaults.get("dealer", ""))
            self._showroom_var.set(dealer_defaults.get("showroom", ""))
            self._salesperson_var.set(dealer_defaults.get("salesperson", ""))
            saved = cfg.get("__fields__", {})
            cnt = 0
            for f, var in self._field_widgets:
                k = self._field_key(f)
                if k in saved:
                    try: var.set(saved[k]); cnt += 1
                    except Exception: pass
            self._log(f"저장값 복원: {cnt}개")
            self._set_status(f"{len(fields)}개 항목 로드 — 값 확인 후 '구매하기' 클릭")
        except Exception as e:
            self._log(f"{config_file.name} 로드 오류: {e}")

    def _restore_saved_values_for_url(self, url: str):
        config_file = config_file_for_url(url)
        try:
            cfg, config_file = load_config_for_url(url)
            if not cfg:
                return
            saved = cfg.get("__fields__", {})
            for f, var in self._field_widgets:
                k = self._field_key(f)
                if k in saved:
                    try:
                        var.set(saved[k])
                    except Exception:
                        pass
        except Exception as e:
            self._log(f"{config_file.name} 로드 오류: {e}")

    def _reload_scanned_model_data(self):
        model_name = self._model_var.get()
        url = self._url_var.get().strip() or self._models.get(model_name) or PRODUCT_URL
        if model_name in self._models:
            url = self._models[model_name]
            self._url_var.set(url)

        fields_file = fields_file_for_url(url)
        if not fields_file.exists():
            self._load_current_model_data(show_empty=False)
            return

        try:
            fields = json.loads(fields_file.read_text(encoding="utf-8"))
            self._render_fields(fields)
            self._restore_saved_values_for_url(url)
            self._set_status(f"스캔 갱신 완료 ({len(fields)}개) — 최신 데이터가 화면에 반영되었습니다.")
        except Exception as e:
            self._log(f"{fields_file.name} 로드 오류: {e}")
            self._load_current_model_data(show_empty=False)

    def _on_save(self):
        url = self._url_var.get().strip()
        cfg = {
            "__model__": self._model_var.get(),
            "__url__": url,
            "__dealer_defaults__": {
                "dealer": self._dealer_var.get().strip(),
                "showroom": self._showroom_var.get().strip(),
                "salesperson": self._salesperson_var.get().strip(),
            },
            "__fields__": {},
        }
        for f, var in self._field_widgets:
            k = self._field_key(f)
            if k: cfg["__fields__"][k] = var.get()
        cfg_json = json.dumps(cfg, ensure_ascii=False, indent=2)
        config_file_for_url(url).write_text(cfg_json, encoding="utf-8")
        CONFIG_FILE.write_text(cfg_json, encoding="utf-8")
        self._set_status(f"설정 저장 완료 ({len(cfg['__fields__'])}개)")
        messagebox.showinfo("저장", "구매 정보가 저장되었습니다.")

    def _on_open(self):
        url = self._url_var.get().strip()
        if not url:
            messagebox.showwarning("URL 없음", "상품 URL을 입력하세요."); return
        self._btn_open.config(state=tk.DISABLED)
        self._btn_scan_all.config(state=tk.NORMAL)
        threading.Thread(target=run_open_browser_for_login, args=(url,), daemon=True).start()

    def _on_scan_all(self):
        self._btn_open.config(state=tk.DISABLED)
        self._btn_scan_all.config(state=tk.DISABLED)
        self._btn_buy.config(state=tk.DISABLED)
        threading.Thread(target=run_scan_all_models_one_session, daemon=True).start()

    def _on_login_done(self):
        self._btn_login.pack_forget()
        cmd_q.put("login_done")

    def _on_buy(self):
        url = self._url_var.get().strip()
        if not url:
            messagebox.showwarning("URL 없음", "상품 URL을 입력하세요."); return
        data = self._collect()
        if not data:
            messagebox.showwarning("입력 없음", "구매 정보를 입력하세요."); return
        dealer_defaults = {
            "dealer": self._dealer_var.get().strip(),
            "showroom": self._showroom_var.get().strip(),
            "salesperson": self._salesperson_var.get().strip(),
        }
        self._btn_buy.config(state=tk.DISABLED)
        threading.Thread(target=run_buy, args=(url, data, dealer_defaults), daemon=True).start()

    def _on_qr(self):
        self._btn_qr.pack_forget()
        cmd_q.put("qr_done")

    def _on_submit(self):
        if messagebox.askyesno("최종 제출", "BMW 구매 신청서를 제출합니까?\n되돌릴 수 없습니다."):
            self._btn_submit.pack_forget()
            cmd_q.put("submit")

    def _collect(self):
        result = []
        for f, var in self._field_widgets:
            kind = f["kind"]; val = var.get()
            if kind in ("select", "option"):
                real = f.get("_omap", {}).get(str(val), str(val))
                if real: result.append((f, real))
            elif kind == "input":
                if str(val).strip(): result.append((f, str(val).strip()))
            elif kind == "check":
                result.append((f, val))
        return result

    def _render_empty(self, message):
        self._fields = []
        for w in self._inner.winfo_children(): w.destroy()
        self._field_widgets.clear()
        self._btn_buy.config(state=tk.DISABLED)
        tk.Label(
            self._inner,
            text=message,
            bg="white",
            fg="#777",
            font=("맑은 고딕", 9),
            justify=tk.CENTER,
            wraplength=360,
        ).pack(pady=40, padx=16)

    def _render_fields(self, fields):
        fields = self._normalize_fields(fields)
        self._fields = fields
        for w in self._inner.winfo_children(): w.destroy()
        self._field_widgets.clear()
        if not fields:
            tk.Label(self._inner, text="발견된 항목 없음",
                     bg="white", fg="#c00", font=("맑은 고딕", 9)).pack(pady=20)
            return

        def section(title):
            frame = tk.Frame(self._inner, bg="white")
            frame.pack(fill=tk.X, padx=18, pady=(16, 0))
            tk.Label(frame, text=title, bg="white", fg="#111",
                     font=("맑은 고딕", 10, "bold")).pack(anchor=tk.W)
            return frame

        current_section = None
        dealer_section = None
        for f in fields:
            kind = f["kind"]
            label = f.get("label") or f.get("name") or f.get("id") or "?"
            if label in ("익스테리어", "인테리어", "결제방법 선택"):
                current_section = section(label)
            elif label in ("딜러사", "전시장", "영업사원"):
                if dealer_section is None:
                    dealer_section = section("딜러위치")
                current_section = dealer_section
            elif current_section is None:
                current_section = section("구매 정보")

            row = tk.Frame(current_section, bg="white")
            row.pack(fill=tk.X, pady=(8, 0))
            if label not in ("익스테리어", "인테리어", "결제방법 선택"):
                tk.Label(row, text=label, width=10, anchor=tk.W,
                         font=("맑은 고딕", 9), bg="white", fg="#333").pack(side=tk.LEFT)
            if kind in ("select", "option"):
                texts = [o["text"] for o in f.get("options", [])]
                omap  = {o["text"]: o["value"] for o in f.get("options", [])}
                f["_omap"] = omap
                active = next((o["text"] for o in f.get("options", []) if o.get("active")), None)
                var = tk.StringVar(value=active or (texts[0] if texts else ""))
                if label in ("익스테리어", "인테리어"):
                    grid = tk.Frame(row, bg="white")
                    grid.pack(anchor=tk.W)
                    for idx, text in enumerate(texts):
                        rb = tk.Radiobutton(
                            grid, text=text, variable=var, value=text,
                            indicatoron=False, bg="#f5f5f5", selectcolor="#dfeaf8",
                            activebackground="#e9eef5", relief=tk.RIDGE, bd=1,
                            font=("맑은 고딕", 8), padx=10, pady=6,
                            width=18, anchor=tk.W
                        )
                        rb.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=(0, 8), pady=(0, 6))
                elif label == "결제방법 선택":
                    for text in texts:
                        ttk.Radiobutton(row, text=text, variable=var, value=text).pack(side=tk.LEFT, padx=(0, 18))
                else:
                    ttk.Combobox(row, textvariable=var, values=texts,
                                 state="readonly", width=38, font=("맑은 고딕", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True)
                self._field_widgets.append((f, var))
            elif kind == "check":
                var = tk.BooleanVar(value=f.get("checked", False))
                ttk.Checkbutton(row, variable=var).pack(side=tk.LEFT)
                self._field_widgets.append((f, var))
            else:
                var = tk.StringVar()
                ent = ttk.Entry(row, textvariable=var, width=36, font=("맑은 고딕", 9))
                ent.pack(side=tk.LEFT)
                ph = f.get("placeholder", "")
                if ph:
                    tk.Label(row, text=ph, font=("맑은 고딕", 8),
                             bg="white", fg="#bbb").pack(side=tk.LEFT, padx=4)
                self._field_widgets.append((f, var))
        self._btn_buy.config(state=tk.NORMAL)

    def _poll(self):
        try:
            while True:
                etype, data = evt_q.get_nowait()
                if etype == "log":     self._log(data)
                elif etype == "status": self._set_status(data)
                elif etype == "scanned":
                    self._render_fields(data)
                    self._btn_open.config(state=tk.NORMAL)
                    self._btn_scan_all.config(state=tk.NORMAL)
                    url = self._url_var.get().strip() or PRODUCT_URL
                    self._restore_saved_values_for_url(url)
                    self._set_status(f"스캔 완료 ({len(data)}개) — 값 선택 후 '설정 저장' → '구매하기'")
                elif etype == "models_discovered":
                    DISCOVERED_PRODUCT_MODELS.update({url: name for name, url in data})
                    save_models_manifest(data)
                    for name, url in data:
                        self._models[name] = url
                    self._model_box.configure(values=list(self._models.keys()))
                    self._log(f"차종 목록 갱신: {len(data)}개")
                elif etype == "browser_opened":
                    self._btn_scan_all.config(state=tk.NORMAL)
                    self._set_status("브라우저에서 로그인 완료 후 '전체 스캔' 클릭")
                elif etype == "scan_all_done":
                    self._btn_open.config(state=tk.DISABLED)
                    self._btn_scan_all.config(state=tk.NORMAL)
                    self._reload_scanned_model_data()
                    messagebox.showinfo("전체 스캔", f"{data}개 차종 스캔이 완료되었습니다.")
                elif etype == "need_login":
                    self._set_status("브라우저에서 로그인 완료 후 아래 버튼 클릭")
                    self._btn_login.pack(fill=tk.X, padx=16, pady=4)
                elif etype == "qr":
                    self._set_status("QR 화면 표시 완료 — 이후 단계는 브라우저에서 수동 진행")
                    self._show_qr(data)
                elif etype == "qr_ready":
                    self._btn_buy.config(state=tk.NORMAL)
                    self._btn_scan_all.config(state=tk.NORMAL)
                    self._btn_login.pack_forget()
                    self._btn_qr.pack_forget()
                    self._btn_submit.pack_forget()
                    self._set_status("QR 화면 표시 완료 — 브라우저 유지 중")
                elif etype == "fill_done":
                    self._set_status("QR 완료 — '최종 제출' 클릭")
                    self._btn_submit.pack(fill=tk.X, padx=16, pady=4)
                elif etype == "done":
                    messagebox.showinfo("완료", "구매 신청이 완료되었습니다.")
                elif etype == "error":
                    self._btn_open.config(state=tk.NORMAL)
                    self._btn_scan_all.config(state=tk.NORMAL)
                    self._btn_buy.config(state=tk.NORMAL)
                    self._btn_login.pack_forget()
                    self._btn_qr.pack_forget()
                    self._btn_submit.pack_forget()
                    messagebox.showerror("오류", str(data))
        except queue.Empty: pass
        self.after(200, self._poll)

    def _set_status(self, msg):
        self._status_var.set(msg)
        self._log(f"[상태] {msg}")

    def _log(self, msg):
        self._log_box.config(state=tk.NORMAL)
        self._log_box.insert(tk.END, msg.rstrip() + "\n")
        self._log_box.see(tk.END)
        self._log_box.config(state=tk.DISABLED)

    def _show_qr(self, img_path):
        try:
            from PIL import Image, ImageTk
            win = tk.Toplevel(self); win.title("QR 스캔"); win.configure(bg="white")
            img = Image.open(img_path).resize((400, 400))
            photo = ImageTk.PhotoImage(img)
            tk.Label(win, image=photo, bg="white").pack(padx=20, pady=16)
            win._photo = photo
            tk.Label(win, text="스마트폰으로 QR 스캔 후\n'QR 스캔 완료' 버튼 클릭",
                     font=("맑은 고딕", 11), bg="white").pack(pady=8)
        except Exception:
            messagebox.showinfo("QR", f"스크린샷: {img_path}\n\n스캔 후 'QR 스캔 완료' 클릭")


if __name__ == "__main__":
    app = BMWApp()
    app.mainloop()
