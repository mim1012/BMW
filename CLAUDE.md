# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BMW AutoBuyer — a Playwright-based automation tool for purchasing BMW Online Exclusive vehicles from `shop.bmw.co.kr`. The tool handles the full purchase flow: product selection → login (manual CAPTCHA) → identity verification (PASS/QR) → form filling → submission.

## Environment Setup

This project uses a shared venv at `D:\Project\.venv`. All batch scripts reference this path.

```bat
:: Install dependencies and Playwright browser
setup.bat
```

```powershell
# Or manually:
D:\Project\.venv\Scripts\python.exe -m pip install -r requirements.txt
D:\Project\.venv\Scripts\python.exe -m playwright install chromium
```

## Running the App

```bat
gui.bat          # Launch the Tkinter GUI (main entry point)
run.bat          # CLI-only mode (main.py)
collect.bat      # One-shot form field collector (collect_fields.py)
```

## Running Tests

```powershell
D:\Project\.venv\Scripts\python.exe -m pytest tests/ -v
# Single test:
D:\Project\.venv\Scripts\python.exe -m pytest tests/test_gui_app_reload.py -v
```

## Architecture

### Two execution modes

**CLI mode (`main.py`)** — sequential, step-based flow using `sync_playwright`. Steps: navigate → click buy button → login → identity verification → collect form fields → fill form → submit. Uses `config.py` for credentials and model URLs.

**GUI mode (`gui_app.py`)** — Tkinter app (~2700 lines) with a background-thread architecture:
- Main thread: Tkinter UI
- Worker threads: Playwright browser operations
- Communication: `evt_q` (events from worker → UI) and `cmd_q` (commands from UI → worker)
- The GUI is the primary user-facing tool; CLI mode is legacy

### Key data files (runtime-generated)

| File | Purpose |
|------|---------|
| `form_fields.json` | Last-scanned form fields (default) |
| `form_fields_{model_slug}.json` | Per-model scanned fields |
| `user_config_{model_slug}.json` | Saved user selections per model |
| `scanned_models.json` | Manifest of discovered BMW models |
| `chrome_profile/` | Persistent Chrome profile (preserves login session) |
| `screenshots/` | Debug screenshots from automation runs |

### Scan flow (GUI)

`run_scan_all_models_one_session()` in `gui_app.py` is the primary scan path:
1. Connects to an existing Chrome CDP session on port `9222`, or launches a new persistent context
2. Collects model list via DOM scraping (`collect_models`) then tries the BMW shop API (`collect_models_api` at `/shop/api/oem/model/list/3/{page}`)
3. For each model: tries API-based field extraction (`scan_all_by_api`) first; falls back to DOM scan using `SCAN_JS` (large inline JS string at line ~165)
4. Dealer/showroom dropdowns require login; the scan thread emits `("need_login", None)` to the UI queue and blocks on `cmd_q` waiting for `"login_done"`

### `SCAN_JS` (embedded JavaScript)

The `SCAN_JS` constant in `gui_app.py` (around line 165) is a self-executing JS function injected into the page. It scans for BMW-specific option patterns (`.revervation_wrap2`, `a.tooltip`, color swatches), selects, inputs, checkboxes, and radio groups. Returns field descriptors with `kind` values: `"option"`, `"select"`, `"input"`, `"check"`.

### Identity verification (PASS)

`advance_pass_to_qr()` navigates a state machine (`telco` → `method` → `agree` → `qr`) through the PASS/NICE authentication popup at `nice.checkplus.co.kr`. The popup is tracked via `find_pass_popup()` which polls all browser contexts.

## Configuration

Credentials are loaded from `.env` via `python-dotenv`:

```
BMW_EMAIL=your@email.com
BMW_PASSWORD=yourpassword
```

`config.py` also defines `MODELS` (product URL map), `HEADLESS=False` (required for CAPTCHA), `SLOW_MO=300ms`, and `TIMEOUT=30000ms`.

## Important Constraints

- **`HEADLESS` must stay `False`**: CAPTCHA and PASS identity verification require a visible browser
- **`--disable-blink-features=AutomationControlled`**: Required to avoid bot detection on the BMW shop
- The Chrome persistent profile (`chrome_profile/`) carries the login session; deleting it forces re-login
- `collect_fields.py` requires Chrome already running with `--remote-debugging-port=9222`
