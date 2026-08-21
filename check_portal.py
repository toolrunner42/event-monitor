#!/usr/bin/env python3
"""One-time inspection script to understand portal HTML structure."""
import re
from playwright.sync_api import sync_playwright

URL = "https://reservierung.paulanerfestzelt.de/reservierung"

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="de-DE",
        )
        page = context.new_page()
        print(f"Loading {URL} ...")
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)

        html = page.content()
        print(f"\n--- PAGE TITLE ---")
        print(page.title())

        print(f"\n--- RELEVANT HTML (forms, inputs, selects, tabs, livewire) ---")
        for line in html.split("\n"):
            l = line.strip()
            if any(k in l.lower() for k in [
                "livewire", "wire:", "<select", "<input", "<option",
                "date", "session", "schicht", "abend", "freitag", "samstag",
                "<button", "<form", "nav", "tab", "kontingent", "slot",
                "reservier", "buchung"
            ]):
                if len(l) > 5 and not l.startswith("//"):
                    print(l[:300])

        print(f"\n--- FULL TEXT ---")
        # Strip tags and print text
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        print(text[:3000])

        browser.close()

if __name__ == "__main__":
    main()
