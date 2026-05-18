# -*- coding: utf-8 -*-
"""
BMW 구매 폼 필드 1회 수집 스크립트
- Chrome 에서 BMW 구매 폼 화면(드롭다운/입력칸 보이는 화면)까지 이동 후 실행
- form_fields.json 저장 → 이후 gui_app.py 가 자동 로드
"""
import sys, json
sys.stdout.reconfigure(encoding="utf-8")

from pathlib import Path
from playwright.sync_api import sync_playwright

CDP_PORT  = 9222
OUT_FILE  = Path(__file__).parent / "form_fields.json"

COLLECT_JS = """
(() => {
    function getLabel(el) {
        if (el.id) {
            const l = document.querySelector('label[for="' + el.id + '"]');
            if (l && l.innerText.trim()) return l.innerText.trim();
        }
        let p = el.parentElement;
        for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
            const t = p.querySelector(
                'label,span.tit,span.label,span.name,span.title,dt,th,p.tit,strong,em.tit,.form-label,.input-label'
            );
            if (t && t !== el && t.innerText.trim()) return t.innerText.trim();
        }
        return el.getAttribute('aria-label') || el.placeholder || el.name || el.id || '';
    }
    const out = [];
    document.querySelectorAll('select').forEach(el => {
        if (el.disabled) return;
        const opts = Array.from(el.options)
            .map(o => ({text: o.text.trim(), value: o.value}))
            .filter(o => o.value && o.text && !['선택','-- 선택 --','선택하세요'].includes(o.text));
        if (!opts.length) return;
        out.push({kind:'select', id:el.id||'', name:el.name||'', label:getLabel(el), options:opts});
    });
    document.querySelectorAll(
        'input[type=text],input[type=tel],input[type=number],input[type=email],textarea'
    ).forEach(el => {
        if (el.disabled || el.readOnly) return;
        out.push({kind:'input', type:el.type||'textarea', id:el.id||'', name:el.name||'',
                  label:getLabel(el), placeholder:el.placeholder||''});
    });
    document.querySelectorAll('input[type=checkbox]').forEach(el => {
        if (el.disabled) return;
        let lbl = '';
        if (el.id) lbl = document.querySelector('label[for="'+el.id+'"]')?.innerText?.trim()||'';
        if (!lbl) lbl = el.closest('label')?.innerText?.trim()||'';
        if (!lbl) lbl = el.getAttribute('aria-label')||el.name||el.id||'';
        out.push({kind:'check', id:el.id||'', name:el.name||'', label:lbl, checked:el.checked});
    });
    return out;
})()
"""

def main():
    print(f"Chrome CDP({CDP_PORT}) 연결 중...")
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        except Exception as e:
            print(f"\n[오류] Chrome에 연결할 수 없습니다: {e}")
            print("\nChrome을 다음과 같이 실행하세요:")
            print('  chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\\Temp\\bmw_chrome')
            input("\nEnter 종료...")
            return

        page = browser.contexts[0].pages[0]
        print(f"  현재 URL: {page.url}")

        if "shop.bmw.co.kr" not in page.url:
            print("\n[!] BMW 구매 폼 화면이 아닙니다.")
            print("    Chrome 에서 BMW 구매 폼 드롭다운이 보이는 화면으로 이동 후")
            print("    이 스크립트를 다시 실행하세요.")
            input("\nEnter 종료...")
            browser.close()
            return

        print("  폼 필드 수집 중...")
        fields = page.evaluate(COLLECT_JS)
        print(f"  {len(fields)}개 필드 발견:\n")

        for f in fields:
            if f['kind'] == 'select':
                opts = ", ".join(o['text'] for o in f['options'][:4])
                print(f"  [SELECT] {f['label']!r}  (id={f['id']!r})  [{opts}...]")
            else:
                print(f"  [{f['kind'].upper()}] {f['label']!r}  (id={f['id']!r})")

        OUT_FILE.write_text(json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  -> {OUT_FILE} 저장 완료")
        browser.close()

    print("\n이제 gui.bat 을 실행하면 필드가 자동으로 로드됩니다.")
    input("Enter 종료...")

if __name__ == "__main__":
    main()
