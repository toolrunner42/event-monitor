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

ABEND_KEYWORDS = ["abend", "abendschicht", "abendsitzung", "17:00", "18:00", "19:00", "20:00", "21:00", "22:00"]


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
    Playwright: laedt Portal, prueft fuer jedes Ziel-Datum ob Abendschicht verfuegbar.
    Gibt {date: True/False/None} zurueck (None = Datum nicht im Dropdown).
    """
    from playwright.sync_api import sync_playwright

    results = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_extra_http_headers({"Accept-Language": "de-DE,de;q=0.9"})
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1500)

            # Welche Ziel-Daten sind im Dropdown?
            available = page.evaluate("""
                () => {
                    const dates = """ + json.dumps(list(WIESN_DATES)) + """;
                    const found = [];
                    document.querySelectorAll('select option').forEach(o => {
                        if (dates.includes(o.value)) found.push(o.value);
                    });
                    return found;
                }
            """)

            for date in available:
                try:
                    # Datum per JS setzen und Livewire-Event ausloesen
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

                    content = page.content()
                    text = content.lower()
                    has_abend = any(k in text for k in ABEND_KEYWORDS)
                    results[date] = has_abend
                    print(f"    {date}: {'Abend verfuegbar' if has_abend else 'kein Abend'}")

                    # Seite neu laden fuer naechstes Datum
                    page.goto(url, wait_until="networkidle", timeout=30000)
                    page.wait_for_timeout(1500)

                except Exception as e:
                    print(f"    {date}: Fehler beim Session-Check: {e}")
                    results[date] = None

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
            # Playwright: pruefe Abend-Sessions pro Datum
            sessions = check_portal_sessions(url)
            if not sessions:
                print(f"    Keine Ziel-Daten im Dropdown")
                continue

            session_state_key = f"{key}_sessions"
            old_sessions = state.get(session_state_key, {})
            new_sessions = {d: v for d, v in sessions.items() if v is not None}

            if new_sessions != old_sessions:
                state[session_state_key] = {**old_sessions, **new_sessions}
                state_changed = True

                # Welche Daten haben jetzt Abend, hatten es vorher nicht?
                newly_abend = [
                    d for d, has_abend in new_sessions.items()
                    if has_abend and not old_sessions.get(d, False)
                ]
                if newly_abend:
                    labels = ", ".join(DATE_LABELS.get(d, d) for d in sorted(newly_abend))
                    notify(
                        title=f"ABEND: {name}",
                        message=f"Abendschicht neu verfuegbar: {labels}\nJetzt buchen!",
                        url=url,
                        priority="urgent",
                    )
                else:
                    print(f"    Session-Status geaendert (kein neues Abend)")
            else:
                print(f"    Keine Aenderung")
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
