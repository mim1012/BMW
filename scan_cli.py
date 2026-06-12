# -*- coding: utf-8 -*-
"""
BMW AutoBuyer — CLI 전체 스캔
GUI 없이 Chrome CDP(9222)에 붙어 전 모델 form_fields_*.json 수집.
Usage:
    D:\\Project\\.venv\\Scripts\\python.exe scan_cli.py
    D:\\Project\\.venv\\Scripts\\python.exe scan_cli.py --force   # 기존 파일 무시 재스캔
"""
import sys, json, re, argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).parent
CDP_PORT = 9222
MODELS_FILE = BASE_DIR / "scanned_models.json"
FILTER_URL = "https://shop.bmw.co.kr/filter/oem"


def _safe_slug(name: str) -> str:
    slug = re.sub(r'[\\/:*?"<>|]+', '_', (name or "").strip())
    slug = re.sub(r'\s+', '_', slug)
    return slug.strip("_")[:80]


def _model_name_for_url(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    return re.sub(r"[_-]", " ", slug).strip()


def fields_file(name: str) -> Path:
    return BASE_DIR / f"form_fields_{_safe_slug(name)}.json"


def is_product_url(url: str) -> bool:
    return bool(
        url and "shop.bmw.co.kr" in url and
        ("edition/oem/" in url or "online/oom/" in url or "online/oem/" in url)
    )


def log(msg: str):
    print(msg, flush=True)


SCAN_JS = """
(() => {
    function esc(s) {
        if (window.CSS && CSS.escape) return CSS.escape(s);
        return String(s).replace(/([ #;?%&,.+*~':"!^$[\\]()=>|/@])/g, '\\\\$1');
    }
    function selector(el) {
        if (el.id) return '#' + esc(el.id);
        if (el.name) {
            const byName = el.tagName.toLowerCase() + '[name="' + String(el.name).replace(/"/g, '\\"') + '"]';
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
            el.getAttribute('title')||el.getAttribute('alt')||'').trim().replace(/\\s+/g, ' ');
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
    const trimContainers = [
        ...Array.from(document.querySelectorAll('.revervation_wrap2 .trim')),
        ...Array.from(document.querySelectorAll('.rsv-section')).filter(s =>
            /(익스테리어|인테리어|Exterior|Interior)/i.test(s.querySelector('h3,h4')?.innerText || '')
        )
    ];
    trimContainers.forEach(trim=>{
        const label = textOf(trim.querySelector('h3,h4')) || sectionLabel(trim);
        const opts = Array.from(trim.querySelectorAll('a.tooltip, a.activable')).map((el, idx)=>{
            const img = el.querySelector('img[alt]');
            const tip = el.querySelector('.tooltiptext');
            const text = (img?.getAttribute('alt') || textOf(tip) || optionText(el) || `${label} ${idx + 1}`).trim();
            const sel = selector(el);
            return sel ? {text:text, value:sel, selector:sel,
                          soldout:el.classList.contains('soldout'),
                          active:el.classList.contains('active')} : null;
        }).filter(Boolean);
        if (opts.length) {
            out.push({kind:'option', id:'', name:'trim:'+label, selector:'',
                      label:label||'트림 옵션', options:opts});
        }
    });
    const DEALER_LABELS = {dealer:'딜러사', showRoom:'전시장'};
    document.querySelectorAll('select').forEach(el=>{
        if(el.disabled) return;
        const opts=Array.from(el.options).map(o=>({text:o.text.trim(),value:o.value}))
            .filter(o=>o.text&&!['선택','-- 선택 --','선택하세요'].includes(o.text));
        const fallback=Array.from(el.options).map(o=>({text:o.text.trim(),value:o.value})).filter(o=>o.text);
        const finalOpts=opts.length?opts:fallback;
        if(!finalOpts.length) return;
        const resolvedLabel=DEALER_LABELS[el.name]||lbl(el)||el.name||el.id||'';
        out.push({kind:'select',id:el.id||'',name:el.name||'',selector:selector(el),
                  label:resolvedLabel,options:finalOpts});
    });
    document.querySelectorAll('input[type=text],input[type=tel],input[type=number],input[type=email],textarea').forEach(el=>{
        if(el.disabled||el.readOnly) return;
        out.push({kind:'input',type:el.type||'textarea',id:el.id||'',name:el.name||'',
                  selector:selector(el),label:lbl(el),placeholder:el.placeholder||''});
    });
    document.querySelectorAll('input[type=checkbox]').forEach(el=>{
        if(el.disabled) return;
        let lb='';
        if(el.id) lb=document.querySelector('label[for="'+el.id+'"]')?.innerText?.trim()||'';
        if(!lb) lb=el.closest('label')?.innerText?.trim()||'';
        if(!lb) lb=el.getAttribute('aria-label')||el.name||el.id||'';
        out.push({kind:'check',id:el.id||'',name:el.name||'',selector:selector(el),label:lb,checked:el.checked});
    });
    const radioGroups=new Map();
    document.querySelectorAll('input[type=radio]').forEach(el=>{
        if(el.disabled) return;
        const key=sectionLabel(el)||el.name||'radio';
        if(!radioGroups.has(key)) radioGroups.set(key,[]);
        let target=el;
        if(el.id){const lbl=document.querySelector('label[for="'+el.id+'"]');if(lbl)target=lbl;}
        else if(el.closest('label')) target=el.closest('label');
        radioGroups.get(key).push(target);
    });
    radioGroups.forEach((els,key)=>pushOption(key,els));
    const optionWords=/(익스테리어|인테리어|외장|내장|색상|컬러|휠|트림|옵션|Exterior|Interior|Color|Colour|Wheel|Trim|Option)/i;
    const clickableSel=['button','a','label','li','[role=radio]','[role=option]','[aria-checked]',
        '[class*=swatch]','[class*=color]','[class*=colour]','[class*=option]',
        '[class*=exterior]','[class*=interior]','[class*=item]','[class*=chip]',
        '[class*=box]','[class*=tile]','[style*=background]'].join(',');
    document.querySelectorAll('.revervation_wrap2 section,.revervation_wrap2 article,.revervation_wrap2 fieldset,.revervation_wrap2 div,.revervation_wrap2 ul,.revervation_wrap2 ol').forEach(box=>{
        const boxText=textOf(box).slice(0,300);
        const cls=box.className?String(box.className):'';
        if(!optionWords.test(boxText+' '+cls)) return;
        const els=Array.from(box.querySelectorAll(clickableSel)).filter(el=>{
            const t=optionText(el);
            if(t.length>80) return false;
            const rect=el.getBoundingClientRect();
            if(rect.width<8||rect.height<8) return false;
            if(rect.width>260||rect.height>180) return false;
            return !['SCRIPT','STYLE','SELECT','OPTION'].includes(el.tagName);
        });
        pushOption(sectionLabel(box),els);
    });
    const uniq=[],keys=new Set();
    out.forEach(f=>{
        const optKey=(f.options||[]).map(o=>o.text).join('|');
        const key=[f.kind,f.label,f.id,f.name,f.selector,optKey].join('::');
        if(keys.has(key)) return;
        keys.add(key);
        uniq.push(f);
    });
    return uniq;
})()
"""


def collect_models(page) -> list:
    models, seen = [], set()

    def add(name, url):
        if not url or not is_product_url(url) or url in seen:
            return
        name = (name or "").strip() or _model_name_for_url(url)
        seen.add(url)
        models.append((name, url))
        log(f"  등록: {name}")

    def load_filter():
        page.goto(FILTER_URL, wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        for _ in range(15):
            page.mouse.wheel(0, 1000)
            page.wait_for_timeout(250)

    load_filter()
    card_infos = page.evaluate("""
    () => {
        const cards = Array.from(document.querySelectorAll('div.filter-item'));
        return cards.map((card, idx) => {
            const h = card.querySelector('h1,h2,h3,h4,strong,.name,.model-name,.car-name,.title');
            let name = h ? h.innerText.trim().replace(/\\s+/g,' ') : '';
            if (!name) {
                const spans = Array.from(card.querySelectorAll('p,span,div'))
                    .map(el => (el.childNodes.length <= 2 ? el.innerText.trim() : ''))
                    .filter(t => t.length > 5 && !t.includes('만원') && !t.includes('구매'));
                name = spans.sort((a,b) => b.length-a.length)[0] || '';
            }
            return {name, idx};
        });
    }
    """)
    log(f"  카드 {len(card_infos)}개 발견")

    for info in card_infos:
        try:
            load_filter()
            clicked = page.evaluate("""
            (idx) => {
                const card = document.querySelectorAll('div.filter-item')[idx];
                if (!card) return null;
                const btn = card.querySelector('div.on,.btn-buy,.buy-btn');
                if (btn) { btn.scrollIntoView({block:'center'}); btn.click(); return 'btn'; }
                card.scrollIntoView({block:'center'}); card.click(); return 'card';
            }
            """, info["idx"])
            if not clicked:
                continue
            try:
                page.wait_for_function(
                    "() => location.href.includes('/edition/oem/') || location.href.includes('/online/oom/')",
                    timeout=6000,
                )
            except Exception:
                pass
            if is_product_url(page.url):
                add(info["name"], page.url)
        except Exception as e:
            log(f"  카드 {info['idx']} 실패: {e}")

    if not models:
        log("  DOM 수집 실패 — API 폴백")
        try:
            data = page.evaluate("""
            async () => {
                const r = await fetch('/shop/api/oem/model/list/3/1', {credentials:'include'});
                return await r.json();
            }
            """)
            for item in (data.get("response") or {}).get("list") or []:
                eid = item.get("editionId") or item.get("id") or ""
                if eid:
                    add(item.get("modelName") or "",
                        f"https://shop.bmw.co.kr/online/oem/edition/oem/{eid}")
        except Exception as e:
            log(f"  API 폴백 실패: {e}")

    return models


def has_option_panel(page) -> bool:
    try:
        return bool(page.evaluate("""
        (() => {
            const b = document.body?.innerText || '';
            return ['익스테리어','인테리어','딜러위치','결제방법'].some(t => b.includes(t));
        })()"""))
    except Exception:
        return False


def dealer_ready(page) -> bool:
    try:
        return bool(page.evaluate("""
        (() => {
            const SKIP = ['딜러사 선택','전시장 선택','영업사원 선택','선택','-- 선택 --'];
            return Array.from(document.querySelectorAll('select[name="dealer"],select[name="showRoom"],select'))
                .some(s => Array.from(s.options||[]).some(o => {
                    const t = (o.textContent||'').trim();
                    return t && !SKIP.includes(t);
                }));
        })()"""))
    except Exception:
        return False


def click_entry(page) -> bool:
    for sel in [
        "button:has-text('구매하기')", "a:has-text('구매하기')",
        "button:has-text('예약하기')", "a:has-text('예약하기')",
        ".revervation_btn", ".reservation_btn", ".reserve_btn",
        "button:has-text('구매')", "a:has-text('구매')",
        "button:has-text('신청')", "a:has-text('신청')",
        ".revervation_btn_alaram",
        "button:has-text('재고 알림 신청하기')", "a:has-text('재고 알림 신청하기')",
    ]:
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible(timeout=500):
                el.click(force=True, timeout=1500)
                return True
        except Exception:
            continue
    return False


def close_modal(page):
    for sel in [".cBtn", "button:has-text('확인')", "button:has-text('닫기')", ".btn-close"]:
        try:
            page.wait_for_selector(sel, timeout=1200)
            page.click(sel)
            page.wait_for_timeout(400)
            return
        except Exception:
            pass


def trigger_dealer_api(page, url: str) -> bool:
    edition_id = url.rstrip("/").split("/")[-1]

    page.evaluate("""
    () => {
        window._lastTrimReq = null;
        window._lastTrimResp = null;
        const orig = window.fetch;
        window.fetch = function(url, opts) {
            const p = orig.apply(this, arguments);
            if (typeof url === 'string' && url.includes('choice/vehicle/trim')) {
                if (opts && opts.body) {
                    try { window._lastTrimReq = JSON.parse(opts.body); } catch(e) {}
                }
                p.then(r => r.clone().json().then(d => { window._lastTrimResp = d; })).catch(()=>{});
            }
            return p;
        };
    }
    """)

    # 외장 클릭
    try:
        sec = page.locator('.rsv-section, .revervation_wrap2 .trim').filter(has_text="익스테리어").first
        opt = sec.locator('a.tooltip:not(.soldout), a.activable:not(.soldout)').first
        if not opt.count():
            opt = sec.locator('a.tooltip, a.activable').first
        opt.click(timeout=3000, force=True)
    except Exception as e:
        log(f"    외장 클릭 실패: {e}")

    ext_pcode = int_pcode = ""
    for _ in range(20):
        r = page.evaluate("() => ({req:window._lastTrimReq||null, resp:window._lastTrimResp||null})")
        cc = (r.get("req") or {}).get("chainingCode", "")
        if cc and "-" not in cc:
            ext_pcode = cc
        items = (r.get("resp") or {}).get("response", [])
        if isinstance(items, list) and items:
            int_pcode = items[0].get("pcode", "")
        if ext_pcode and int_pcode:
            break
        page.wait_for_timeout(200)

    if not (ext_pcode and int_pcode):
        log("    trim API 미응답 — 인테리어 폴백")
        try:
            sec = page.locator('.rsv-section, .revervation_wrap2 .trim').filter(has_text="인테리어").first
            opt = sec.locator('a.tooltip, a.activable').first
            opt.click(timeout=3000, force=True)
        except Exception:
            pass
        return False

    chaining = f"{ext_pcode}-{int_pcode}"
    log(f"    딜러 API: {edition_id} / {chaining}")
    dealer_data = page.evaluate("""
    async ([eid, cc]) => {
        try {
            const r = await fetch('/shop/api/choice/vehicle/dealer', {
                method:'POST', headers:{'Content-Type':'application/json'},
                credentials:'include',
                body: JSON.stringify({editionId:eid, chainingCode:cc})
            });
            return await r.json();
        } catch(e) { return {success:false, error:String(e)}; }
    }
    """, [edition_id, chaining])

    if not dealer_data.get("success"):
        log(f"    딜러 API 실패: {dealer_data.get('error','')}")
        return False

    dealers = dealer_data.get("response", {}).get("dealerInfo", [])
    if dealers:
        log(f"    딜러 첫 항목 키: {list(dealers[0].keys())}")
    log(f"    딜러 {len(dealers)}개 주입")
    page.evaluate("""
    ([dealers]) => {
        const sel = document.querySelector('select[name="dealer"]');
        if (!sel) return;
        const existing = new Set(Array.from(sel.options).map(o => o.value));
        dealers.forEach(d => {
            const v = String(d.dealerCd || d.dealerId || '');
            if (!v || existing.has(v)) return;
            const opt = document.createElement('option');
            opt.value = v;
            opt.textContent = d.dealerName || d.name || d.dlrNm || d.companyName || v;
            sel.appendChild(opt);
        });
    }
    """, [dealers])
    return True


def scan_model(page, name: str, url: str) -> list | None:
    log(f"\n  URL: {url}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            pass
        page.wait_for_timeout(1000)

        if not has_option_panel(page):
            log("  진입 버튼 클릭")
            click_entry(page)
            page.wait_for_timeout(1000)
            close_modal(page)
            for _ in range(16):
                if has_option_panel(page):
                    break
                page.wait_for_timeout(500)

        close_modal(page)

        if not dealer_ready(page):
            trigger_dealer_api(page, url)
            for _ in range(12):
                if dealer_ready(page):
                    break
                page.wait_for_timeout(250)

        dealer_status = "딜러O" if dealer_ready(page) else "딜러X(재고소진?)"
        fields = page.evaluate(SCAN_JS)
        if not fields:
            log(f"  [FAIL] 스캔 비어있음")
            return None

        log(f"  {dealer_status} / 필드 {len(fields)}개")
        return fields
    except Exception as e:
        log(f"  [ERROR] {e}")
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="기존 파일 무시 재스캔")
    args = parser.parse_args()

    log(f"Chrome CDP({CDP_PORT}) 연결 중...")
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        except Exception as e:
            log(f"[오류] Chrome 연결 실패: {e}")
            return

        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log(f"연결됨: {page.url}")

        log("\n=== 모델 목록 수집 ===")
        models = collect_models(page)
        if not models:
            log("[오류] 모델 없음")
            browser.close()
            return

        MODELS_FILE.write_text(
            json.dumps([{"name": n, "url": u} for n, u in models], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        log(f"\n총 {len(models)}개 모델")

        log("\n=== 스캔 시작 ===")
        ok = fail = skip = 0
        for i, (name, url) in enumerate(models, 1):
            out_file = fields_file(name)
            if not args.force and out_file.exists():
                log(f"[{i}/{len(models)}] SKIP: {name}")
                skip += 1
                continue

            log(f"\n[{i}/{len(models)}] {name}")
            fields = scan_model(page, name, url)
            if fields:
                out_file.write_text(json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8")
                log(f"  → {out_file.name}")
                ok += 1
            else:
                fail += 1

        log(f"\n=== 완료: 성공 {ok} / 실패 {fail} / 스킵 {skip} / 전체 {len(models)} ===")
        browser.close()


if __name__ == "__main__":
    main()
