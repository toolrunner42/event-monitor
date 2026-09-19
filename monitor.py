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

WIESN_DATES: set = set()
DATE_LABELS: dict = {}

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def load_config() -> dict:
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def fetch_page(url: str) -> Optional[str]:
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


def check_portal_sessions(url: str) -> dict:
    """
    Hybrid: requests fuer Datum-Erkennung (SSR), Playwright fuer Session-Check (Seite 2).
    Gibt {date: True/False} zurueck. Nur Daten die im SSR-Dropdown vorhanden sind.
    """
    from playwright.sync_api import sync_playwright

    # SSR: welche Ziel-Daten sind im Dropdown?
    html = fetch_page(url)
    if not html:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    available = []
    for sel in soup.find_all("select"):
        for o in sel.find_all("option"):
            val = o.get("value", "").strip()
            if val in WIESN_DATES:
                available.append(val)

    if not available:
        return {}

    results = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_extra_http_headers({"Accept-Language": "de-DE,de;q=0.9"})
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1500)

            for date in available:
                try:
                    page.evaluate(f"""
                        () => {{
                            const selects = document.querySelectorAll('select');
                            for (const sel of selects) {{
                                for (const opt of sel.options) {{
                                    if (opt.value === '{date}') {{
                                        sel.value = '{date}';
                                        sel.dispatchEvent(new Event('input', {{bubbles: true}}));
                                        sel.dispatchEvent(new Event('change', {{bubbles: true}}));
                                        break;
                                    }}
                                }}
                            }}
                        }}
                    """)
                    page.wait_for_timeout(3000)
                    page.wait_for_load_state("networkidle", timeout=10000)

                    soup = BeautifulSoup(page.content(), "html.parser")
                    for tag in soup(["script", "style", "meta", "link", "noscript"]):
                        tag.decompose()
                    text = soup.get_text(separator=" ", strip=True)
                    has_abend = "abend" in text.lower()
                    results[date] = has_abend
                    print(f"    {date}: {'Abend verfuegbar' if has_abend else 'kein Abend (nur Morgen/Mittag)'}")

                    page.goto(url, wait_until="networkidle", timeout=30000)
                    page.wait_for_timeout(1500)

                except Exception as e:
                    print(f"    {date}: Fehler: {e}")

            browser.close()
    except Exception as e:
        print(f"  Playwright-Fehler: {e}")

    return results


def extract_text(html: str, site_type: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "meta", "link", "noscript"]):
        tag.decompose()

    if site_type == "wiesnkini":
        bold = [b.get_text(strip=True) for b in soup.find_all(["strong", "b"])]
        tables = [td.get_text(strip=True) for td in soup.find_all(["td", "th"])]
        return " | ".join(filter(None, bold + tables))

    elif site_type == "portal":
        # Nur die Ziel-Daten per option-value aus dem SSR-HTML extrahieren
        options = []
        for sel in soup.find_all("select"):
            for o in sel.find_all("option"):
                val = o.get("value", "").strip()
                if val in WIESN_DATES:
                    options.append(f"datum:{val}")
        return " | ".join(filter(None, options))

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
        r"(\d{1,2}\.\s*(?:januar|februar|märz|april|mai|juni|juli|august|september|oktober|november|dezember)(?:\s*202[6789])?"
        r"|\d{1,2}\.\d{1,2}\.202[6789])",
        text, re.IGNORECASE
    )
    time_pattern = re.search(r"\d{1,2}[:.]\d{2}\s*Uhr|\bab\s+\d{1,2}\s*Uhr", text, re.IGNORECASE)

    if date_pattern and time_pattern:
        return f"{date_pattern.group(0).strip()} um {time_pattern.group(0).strip()}"
    elif date_pattern:
        return date_pattern.group(0).strip()
    return None


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
    global WIESN_DATES, DATE_LABELS
    config = load_config()
    DATE_LABELS = config.get("target_dates", {})
    WIESN_DATES = set(DATE_LABELS.keys())
    state = load_state()
    state_changed = False

    print(f"Pruefe {len(config['sites'])} Seiten (Ziel-Daten: {sorted(WIESN_DATES)}) ...")

    for site in config["sites"]:
        key = site["key"]
        name = site["name"]
        url = site["url"]
        site_type = site.get("type", "generic")

        print(f"  {name} ...")

        if site_type == "portal":
            session_results = check_portal_sessions(url)
            if not session_results:
                print(f"    Keine Ziel-Daten im Dropdown")
                continue

            newly_available = []
            for date, has_abend in session_results.items():
                state_key = f"{key}_{date}"
                was_available = state.get(state_key)
                state[state_key] = has_abend
                state_changed = True
                if was_available is None:
                    print(f"    {date}: Baseline ({'Abend' if has_abend else 'kein Abend'})")
                elif not was_available and has_abend:
                    print(f"    {date}: NEU Abend verfuegbar!")
                    newly_available.append(date)
                elif was_available and not has_abend:
                    print(f"    {date}: Abend nicht mehr verfuegbar")
                else:
                    print(f"    {date}: Keine Aenderung ({'Abend' if has_abend else 'kein Abend'})")

            if newly_available:
                labels = ", ".join(DATE_LABELS.get(d, d) for d in sorted(newly_available))
                notify(
                    title=f"ABEND frei: {name}",
                    message=f"Abendschicht verfuegbar: {labels}\nJetzt buchen!",
                    url=url,
                    priority="urgent",
                )
            continue

        # Nicht-Portal: Hash-basiert
        html = fetch_page(url)
        if not html:
            continue

        text = extract_text(html, site_type)
        current_hash = hashlib.md5(text.encode()).hexdigest()
        previous_hash = state.get(key)

        if previous_hash is None:
            print(f"    Baseline gespeichert ({len(text)} Zeichen)")
            state[key] = current_hash
            state[f"{key}_text"] = text
            state_changed = True
            continue

        if current_hash == previous_hash:
            print(f"    Keine Aenderung")
            continue

        print(f"    AENDERUNG erkannt!")
        old_text = state.get(f"{key}_text", "")
        state[key] = current_hash
        state[f"{key}_text"] = text
        state_changed = True

        kontingent_info = detect_kontingent_announcement(text)
        if kontingent_info and site.get("kontingent"):
            notify(
                title=f"KONTINGENT: {name}",
                message=f"Datum + Uhrzeit angekuendigt: {kontingent_info}\nJetzt vormerken!",
                url=url,
                priority="urgent",
            )
        else:
            notify(
                title=f"Aenderung: {name}",
                message=f"Seite hat sich geaendert\nJetzt pruefen!",
                url=url,
                priority="high",
            )

    if state_changed:
        save_state(state)

    print("Fertig.")


if __name__ == "__main__":
    main()
