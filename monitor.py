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

WIESN_DATES = {
    "2026-09-19": {"label": "Sa 19.09 Anstich", "day_type": "sa"},
    "2026-09-25": {"label": "Fr 25.09", "day_type": "fr"},
    "2026-09-26": {"label": "Sa 26.09", "day_type": "sa"},
    "2026-10-02": {"label": "Fr 02.10", "day_type": "fr"},
    "2026-10-03": {"label": "Sa 03.10", "day_type": "sa"},
}

ABEND_KEYWORDS = ["abend", "dinner", "evening", "abendsitzung", "abendtisch"]
NACHMITTAG_KEYWORDS = ["nachmittag", "afternoon", "nachmittagsschicht"]
MITTAG_ONLY_KEYWORDS = ["mittag", "lunch", "mittagstisch", "mittagsschicht"]


def slot_is_relevant(slot_text: str, day_type: str) -> bool:
    s = slot_text.lower()
    if any(k in s for k in ABEND_KEYWORDS):
        return True
    if day_type == "sa" and any(k in s for k in NACHMITTAG_KEYWORDS):
        return True
    return False


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


def fetch_portal_data(url: str) -> dict:
    """
    Returns:
      {
        "nav": str,               # nav/tab text for kontingent-tab detection
        "slots": {                # per date: list of session texts
          "2026-09-25": ["Abendschicht", ...],
          ...
        }
      }
    """
    result = {"nav": "", "slots": {}}
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

            # Nav text for kontingent-tab detection
            nav_parts = []
            for el in page.query_selector_all("a, button, li, nav"):
                t = el.inner_text().strip()
                if t:
                    nav_parts.append(t)
            result["nav"] = " ".join(nav_parts)[:800]

            # Find the date select
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
                browser.close()
                return result

            # Get available target dates
            available_dates = []
            for opt in date_select.query_selector_all("option"):
                val = (opt.get_attribute("value") or "").strip()
                if re.match(r"^\d{4}-\d{2}-\d{2}$", val) and val in WIESN_DATES:
                    available_dates.append(val)

            # For each target date: select it, wait for Livewire, read sessions
            for date_val in available_dates:
                date_select.select_option(date_val)
                page.wait_for_timeout(2500)

                slots = []

                # 1. Check secondary selects (not the date select)
                for sel in page.query_selector_all("select"):
                    has_iso = any(
                        re.match(r"^\d{4}-\d{2}-\d{2}$", (o.get_attribute("value") or "").strip())
                        for o in sel.query_selector_all("option")
                    )
                    if has_iso:
                        continue
                    for opt in sel.query_selector_all("option"):
                        t = opt.inner_text().strip()
                        if t and t.lower() not in ("", "bitte wählen", "please select", "-"):
                            slots.append(t)

                # 2. Radio buttons with labels
                for inp in page.query_selector_all("input[type='radio']"):
                    inp_id = inp.get_attribute("id")
                    if inp_id:
                        lbl = page.query_selector(f"label[for='{inp_id}']")
                        if lbl:
                            t = lbl.inner_text().strip()
                            if t:
                                slots.append(t)

                # 3. Fallback: scan visible text for session keywords
                if not slots:
                    try:
                        body_text = page.inner_text("body")
                        for line in body_text.split("\n"):
                            line = line.strip()
                            if any(k in line.lower() for k in
                                   ["abend", "nachmittag", "schicht", "session", "mittag", "dinner"]):
                                if 3 < len(line) < 120:
                                    slots.append(line)
                    except Exception:
                        pass

                result["slots"][date_val] = slots
                print(f"    {date_val}: {slots[:5]}")

            browser.close()
    except Exception as e:
        print(f"  Playwright Fehler {url}: {e}")
    return result


def find_new_relevant_slots(old_data: dict, new_data: dict) -> list:
    """Returns list of alert strings for newly available relevant slots."""
    alerts = []
    old_slots_map = old_data.get("slots", {})
    new_slots_map = new_data.get("slots", {})

    for date_key, info in WIESN_DATES.items():
        label = info["label"]
        day_type = info["day_type"]

        new_slots = set(new_slots_map.get(date_key, []))
        old_slots = set(old_slots_map.get(date_key, []))

        if not new_slots and date_key not in new_slots_map:
            continue  # date not in portal at all

        date_is_new = date_key not in old_slots_map

        if date_is_new:
            relevant = [s for s in new_slots if slot_is_relevant(s, day_type)]
            if relevant:
                alerts.append(f"{label}: {', '.join(relevant)}")
            elif not new_slots:
                # Date appeared but couldn't read sessions -- alert so Sara can check
                alerts.append(f"{label}: Datum verfuegbar (Schichten pruefen!)")
            # If only irrelevant slots (e.g. Mittag only): store but no alert
        else:
            # Date already known: alert only on NEW relevant slots
            added = new_slots - old_slots
            new_relevant = [s for s in added if slot_is_relevant(s, day_type)]
            if new_relevant:
                alerts.append(f"{label}: Neue Schicht: {', '.join(new_relevant)}")

    return alerts


def extract_kontingent_text(html: str, site_type: str) -> str:
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
    if not any(k in text.lower() for k in kontingent_keywords):
        return None

    date_pattern = re.search(
        r"(\d{1,2}\.\s*(?:januar|februar|märz|april|mai|juni|juli|august|september|oktober)"
        r"|\d{1,2}\.\d{1,2}\.202[6789])",
        text, re.IGNORECASE
    )
    time_pattern = re.search(
        r"\d{1,2}[:.]\d{2}\s*Uhr|\bab\s+\d{1,2}\s*Uhr",
        text, re.IGNORECASE
    )

    if date_pattern and time_pattern:
        return f"{date_pattern.group(0).strip()} um {time_pattern.group(0).strip()}"
    elif date_pattern:
        return date_pattern.group(0).strip()
    return None


def detect_kontingent_tab(nav_text: str) -> bool:
    keywords = [
        "münchner kontingent", "muenchner kontingent",
        "münchen kontingent", "muenchen kontingent",
        "münchner reservierung", "muenchner reservierung",
        "echte münchener", "echte münchner",
        "echte muenchener", "echte muenchner",
        "einheimische", "einheimischer",
        "stadtticket", "münchner ticket",
        "locals only", "muc kontingent",
        "münchner buchung", "muc reservierung",
    ]
    return any(k in nav_text.lower() for k in keywords)


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
        print(f"  Notification gesendet: {title}")
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

        print(f"\n  {name} ...")

        if site_type == "portal":
            portal_data = fetch_portal_data(url)
            data_json = json.dumps(portal_data, sort_keys=True, ensure_ascii=False)
            current_hash = hashlib.md5(data_json.encode()).hexdigest()
            previous_hash = state.get(key)
            previous_data = state.get(f"{key}_data", {})

            if previous_hash is None:
                print(f"    Baseline gespeichert")
                # Log what's already available
                for date_key, slots in portal_data.get("slots", {}).items():
                    info = WIESN_DATES[date_key]
                    relevant = [s for s in slots if slot_is_relevant(s, info["day_type"])]
                    print(f"    {info['label']}: {slots} (relevant: {relevant})")
                state[key] = current_hash
                state[f"{key}_data"] = portal_data
                state_changed = True
                continue

            if current_hash == previous_hash:
                print(f"    Keine Aenderung")
                continue

            print(f"    AENDERUNG erkannt!")
            alerts = find_new_relevant_slots(previous_data, portal_data)
            if alerts:
                notify(
                    title=f"WIESN SLOT: {name}",
                    message="\n".join(alerts) + "\n\nSofort buchen!",
                    url=url,
                    priority="urgent",
                )
            elif detect_kontingent_tab(portal_data.get("nav", "")):
                notify(
                    title=f"KONTINGENT TAB: {name}",
                    message="Muenchner Kontingent Tab ist jetzt sichtbar!\nSofort pruefen!",
                    url=url,
                    priority="urgent",
                )
            else:
                print(f"    Kein relevanter Slot, kein Alert")

            state[key] = current_hash
            state[f"{key}_data"] = portal_data
            state_changed = True

        else:
            # Kontingent info pages: alert on any change
            html = fetch_with_requests(url)
            if not html:
                continue

            text = extract_kontingent_text(html, site_type)
            current_hash = hashlib.md5(text.encode()).hexdigest()
            previous_hash = state.get(key)

            if previous_hash is None:
                print(f"    Baseline gespeichert")
                state[key] = current_hash
                state[f"{key}_text"] = text
                state_changed = True
                continue

            if current_hash == previous_hash:
                print(f"    Keine Aenderung")
                continue

            print(f"    AENDERUNG erkannt!")
            kontingent_info = detect_kontingent_announcement(text)
            if site.get("kontingent"):
                if kontingent_info:
                    notify(
                        title=f"KONTINGENT: {name}",
                        message=f"Ankuendigung: {kontingent_info}\nJetzt vormerken!",
                        url=url,
                        priority="urgent",
                    )
                else:
                    notify(
                        title=f"Aenderung: {name}",
                        message="Seite hat sich geaendert\nJetzt pruefen!",
                        url=url,
                        priority="high",
                    )

            state[key] = current_hash
            state[f"{key}_text"] = text
            state_changed = True

    if state_changed:
        save_state(state)

    print("\nFertig.")


if __name__ == "__main__":
    main()
