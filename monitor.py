#!/usr/bin/env python3
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path(__file__).parent / "state.json"
CONFIG_FILE = Path(__file__).parent / "config.json"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

# Wiesn 2026: alle Freitage und Samstage
WIESN_DATES = [
    ("19.09", "Sa 19.09 Anstich"),
    ("19. September", "Sa 19.09 Anstich"),
    ("25.09", "Fr 25.09"),
    ("25. September", "Fr 25.09"),
    ("26.09", "Sa 26.09"),
    ("26. September", "Sa 26.09"),
    ("02.10", "Fr 02.10"),
    ("2. Oktober", "Fr 02.10"),
    ("03.10", "Sa 03.10"),
    ("3. Oktober", "Sa 03.10"),
]


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def load_config() -> dict:
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def fetch_with_requests(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "de-DE,de;q=0.9",
        }, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  Fehler {url}: {e}")
        return None


def fetch_with_playwright(url: str) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="de-DE",
            )
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        print(f"  Playwright Fehler {url}: {e}")
        return None


def extract_text(html: str, site_type: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "meta", "link", "noscript"]):
        tag.decompose()

    if site_type == "wiesnkini":
        bold = [b.get_text(strip=True) for b in soup.find_all(["strong", "b"])]
        tables = [td.get_text(strip=True) for td in soup.find_all(["td", "th"])]
        return " | ".join(filter(None, bold + tables))

    else:
        return soup.get_text(separator=" ", strip=True)[:8000]


def detect_kontingent_announcement(text: str) -> Optional[str]:
    kontingent_keywords = [
        "kontingent", "muenchner", "münchen", "einheimische",
        "reservierung ab", "ab sofort", "freigabe", "ab dem"
    ]
    has_kontingent = any(k in text.lower() for k in kontingent_keywords)
    if not has_kontingent:
        return None

    date_pattern = re.search(
        r"(\d{1,2}\.\s*(?:januar|februar|märz|april|mai|juni|juli|august|september|oktober)"
        r"|\d{1,2}\.\d{1,2}\.202[6789])",
        text, re.IGNORECASE
    )
    time_pattern = re.search(r"\d{1,2}[:.]\d{2}\s*Uhr|\bab\s+\d{1,2}\s*Uhr", text, re.IGNORECASE)

    if date_pattern and time_pattern:
        return f"{date_pattern.group(0).strip()} um {time_pattern.group(0).strip()}"
    elif date_pattern:
        return date_pattern.group(0).strip()
    return None


def detect_wiesn_slot(text: str) -> str:
    """Erkennt ob ein Wiesn Fr/Sa Termin im Text vorkommt."""
    found = []
    for pattern, label in WIESN_DATES:
        if pattern in text:
            if label not in found:
                found.append(label)
    if found:
        return ", ".join(found[:3])
    # Fallback: generisches Fr/Sa
    if any(d in text for d in ["Freitag", "Samstag"]):
        return "Fr/Sa erkannt"
    return ""


def detect_muenchen_kontingent_tab(text: str) -> bool:
    """Erkennt ob ein Muenchner Kontingent Tab/Bereich auf einem Portal aufgetaucht ist."""
    keywords = ["münchner kontingent", "muenchner kontingent", "münchen kontingent",
                "muenchen kontingent", "münchner reservierung"]
    return any(k in text.lower() for k in keywords)


def notify(title: str, message: str, url: str = "", priority: str = "high"):
    if not NTFY_TOPIC:
        print(f"  [Notification] {title}: {message}")
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": "beer,oktoberfest",
                **({"Click": url} if url else {}),
            },
            timeout=10,
        )
        print(f"  Notification: {title}")
    except Exception as e:
        print(f"  Notification-Fehler: {e}")


def main():
    config = load_config()
    state = load_state()
    state_changed = False

    print(f"Pruefe {len(config['sites'])} Seiten ...")

    for site in config["sites"]:
        key = site["key"]
        name = site.get("name", key)
        url = site["url"]
        site_type = site.get("type", "generic")

        print(f"  {name} ...")

        if site_type == "portal":
            html = fetch_with_playwright(url)
        else:
            html = fetch_with_requests(url)

        if not html:
            continue

        text = extract_text(html, site_type)
        current_hash = hashlib.md5(text.encode()).hexdigest()
        previous_hash = state.get(key)

        if previous_hash is None:
            print(f"    Baseline gespeichert")
            state[key] = current_hash
            state_changed = True
            continue

        if current_hash == previous_hash:
            print(f"    Keine Aenderung")
            continue

        print(f"    AENDERUNG erkannt!")
        state[key] = current_hash
        state_changed = True

        # Kontingent-Seiten: Datum + Uhrzeit Ankuendigung?
        kontingent_info = detect_kontingent_announcement(text)
        if kontingent_info and site.get("kontingent"):
            notify(
                title=f"KONTINGENT: {name}",
                message=f"Ankuendigung: {kontingent_info}\nJetzt vormerken!",
                url=url,
                priority="urgent",
            )
        # Portal: Muenchner Kontingent Tab aufgetaucht?
        elif site_type == "portal" and detect_muenchen_kontingent_tab(text):
            notify(
                title=f"KONTINGENT TAB: {name}",
                message=f"Muenchner Kontingent jetzt buchbar!\nSofort pruefen!",
                url=url,
                priority="urgent",
            )
        # Portal: Wiesn Fr/Sa Slot erkannt?
        elif site_type == "portal":
            slot = detect_wiesn_slot(text)
            if slot:
                notify(
                    title=f"Wiesn Slot: {name}",
                    message=f"Neuer Slot: {slot}\nJetzt pruefen!",
                    url=url,
                    priority="urgent",
                )
            else:
                print(f"    Kein Wiesn Fr/Sa erkannt, kein Alert")
        # Kontingent-Seiten: jede Aenderung melden
        elif site.get("kontingent"):
            slot = detect_wiesn_slot(text)
            notify(
                title=f"Aenderung: {name}",
                message=f"Seite hat sich geaendert{(' -- ' + slot) if slot else ''}\nJetzt pruefen!",
                url=url,
                priority="high",
            )

    if state_changed:
        save_state(state)

    print("Fertig.")


if __name__ == "__main__":
    main()
