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


def check_portal_dates(url: str) -> list:
    """SSR: gibt Liste der Ziel-Daten zurueck die im Dropdown vorhanden sind."""
    html = fetch_page(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for sel in soup.find_all("select"):
        for o in sel.find_all("option"):
            val = o.get("value", "").strip()
            if val in WIESN_DATES:
                found.append(val)
    return found


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
            found_dates = check_portal_dates(url)
            if not found_dates:
                print(f"    Keine Ziel-Daten im Dropdown")
                continue

            newly_found = []
            for date in found_dates:
                state_key = f"{key}_{date}"
                was_known = state.get(state_key, False)
                state[state_key] = True
                state_changed = True
                if not was_known:
                    print(f"    {date}: NEU im Dropdown!")
                    newly_found.append(date)
                else:
                    print(f"    {date}: bereits bekannt")

            if newly_found:
                labels = ", ".join(DATE_LABELS.get(d, d) for d in sorted(newly_found))
                notify(
                    title=f"Datum frei: {name}",
                    message=f"{labels} verfuegbar -- Abend pruefen!\nJetzt buchen!",
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
