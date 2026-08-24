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

    # Find and select the date
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
        print("No date select found!")
        browser.close()
        exit()

    print(f"Step 1: Selecting {TARGET_DATE}")
    date_select.select_option(TARGET_DATE)
    page.wait_for_timeout(1500)

    # Find and click Weiter
    weiter = None
    for label in ["Weiter", "Next", "Continuer", "Volgende"]:
        try:
            btn = page.locator(f"button:has-text('{label}')").first
            if btn.is_visible():
                weiter = btn
                print(f"Found button: {label}")
                break
        except Exception:
            pass

    if weiter:
        weiter.click()
        print("Clicked Weiter, waiting for step 2...")
        page.wait_for_timeout(3000)
    else:
        print("No Weiter button found!")

    print("\n=== SELECTS on step 2 ===")
    for sel in page.query_selector_all("select"):
        opts = [(o.get_attribute("value") or "", o.inner_text().strip()) for o in sel.query_selector_all("option")]
        print(f"  {opts[:10]}")

    print("\n=== RADIO BUTTONS ===")
    for inp in page.query_selector_all("input[type=radio]"):
        val = inp.get_attribute("value") or ""
        inp_id = inp.get_attribute("id") or ""
        lbl = page.query_selector(f"label[for='{inp_id}']")
        lbl_text = lbl.inner_text().strip() if lbl else ""
        print(f"  val={val!r} label={lbl_text!r}")

    print("\n=== ALL BUTTONS ===")
    for btn in page.query_selector_all("button"):
        t = btn.inner_text().strip()
        if t:
            print(f"  {t[:80]!r}")

    print("\n=== FULL PAGE TEXT (first 3000 chars) ===")
    body = page.inner_text("body")
    print(body[:3000])

    browser.close()
