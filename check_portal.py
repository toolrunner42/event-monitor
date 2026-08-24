#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import re

URL = "https://reservierung.armbrustschuetzenzelt.de/reservierung"
TARGET_DATE = "2026-09-25"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        locale="de-DE",
    ).new_page()

    page.goto(URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)

    # Find date select
    date_select = None
    for sel in page.query_selector_all("select"):
        for opt in sel.query_selector_all("option"):
            val = (opt.get_attribute("value") or "").strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}$", val):
                date_select = sel
                break
        if date_select:
            break

    if not date_select:
        print("No date select!")
        browser.close()
        exit()

    print(f"Step 1: Setting date via JS events")
    # Use JS to set value + dispatch proper events that Livewire listens to
    page.evaluate(f"""
        const sel = document.querySelector('select');
        sel.value = '{TARGET_DATE}';
        sel.dispatchEvent(new Event('input', {{bubbles: true}}));
        sel.dispatchEvent(new Event('change', {{bubbles: true}}));
    """)
    page.wait_for_timeout(2000)

    # Dismiss Livewire error overlay if present
    overlay = page.query_selector("#livewire-error-overlay")
    if overlay and overlay.is_visible():
        print("Livewire overlay detected, dismissing...")
        # Try clicking a button inside it
        btn = page.query_selector("#livewire-error-overlay button")
        if btn:
            btn.click()
        else:
            # Click the overlay itself to dismiss
            page.evaluate("document.getElementById('livewire-error-overlay').style.display='none';")
        page.wait_for_timeout(500)
        print("Overlay dismissed")
    else:
        print("No overlay - good!")

    print("\n=== PAGE TEXT after date select ===")
    print(page.inner_text("body")[:2000])

    print("\n=== BUTTONS ===")
    for btn in page.query_selector_all("button"):
        t = btn.inner_text().strip()
        if t:
            print(f"  {t[:80]!r} visible={btn.is_visible()}")

    # Try clicking Weiter
    weiter = None
    for label in ["Weiter", "Next"]:
        try:
            btn = page.locator(f"button:has-text('{label}')").first
            if btn.is_visible():
                weiter = btn
                print(f"\nFound Weiter button: {label}")
                break
        except:
            pass

    if weiter:
        weiter.click(timeout=10000)
        print("Clicked Weiter!")
        page.wait_for_timeout(3000)
        print("\n=== PAGE TEXT after Weiter ===")
        print(page.inner_text("body")[:3000])
    else:
        print("No Weiter button clickable")

    browser.close()
