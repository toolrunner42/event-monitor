#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import re, json

URL = "https://reservierung.armbrustschuetzenzelt.de/reservierung"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        locale="de-DE",
    ).new_page()

    page.goto(URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)

    html = page.content()

    print("=== LIVEWIRE COMPONENT DATA ===")
    # Livewire v3 embeds state in wire:snapshot or wire:initial-data attributes
    for pattern in [
        r"wire:snapshot='([^']+)'",
        r'wire:snapshot="([^"]+)"'  ,
        r"wire:initial-data='([^']+)'",
        r'wire:initial-data="([^"]+)"'  ,
    ]:
        matches = re.findall(pattern, html)
        for m in matches[:2]:
            try:
                data = json.loads(m.replace("&quot;", '"'))
                print(json.dumps(data, indent=2, ensure_ascii=False)[:3000])
            except:
                print(m[:500])

    print("\n=== ALL OPTION ELEMENTS ===")
    for option in page.query_selector_all("option"):
        val = option.get_attribute("value") or ""
        txt = option.inner_text().strip()
        print(f"  value={val!r} text={txt!r}")

    print("\n=== NETWORK REQUESTS (XHR) ===")
    # Intercept any Livewire requests by listening
    print("(static snapshot only - no XHR captured here)")

    browser.close()
