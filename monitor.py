"""Gym+ grupinės treniruotės: laiškas, kai atsidaro registracija arba atsilaisvina vieta.

Paleidimas:
  python3 monitor.py            - patikrina ir, jei reikia, išsiunčia laišką
  python3 monitor.py --status   - tik parodo, ką skriptas mato (nieko nesiunčia ir neišsaugo)
"""
import json
import os
import re
import smtplib
import sys
from datetime import datetime, time, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("Europe/Vilnius")
API = "https://gymplius.lt/wp-json/api/async-render-block"
BASE_PARAMS = {
    "subdivision": "77",  # URBAN HUB Vilnius
    "pid": "MjQ3",
    "bid": "YWNmLWJsb2NrLTY3ZmNhOTg2NmQ4ZTc2LjMzNTY5NjI0",
    "rest_language": "lt",
}
WEEKS = int(os.getenv("WEEKS", "2"))
# Neprivaloma: ONLY="sculpt,booty" siųs pranešimus tik apie tokius pavadinimus
ONLY = [s.strip().lower() for s in os.getenv("ONLY", "").split(",") if s.strip()]
REG_HOURS = 52  # registracija atsidaro likus 52 val.
STATE_FILE = "state.json"
DAYS = ["Pr", "An", "Tr", "Kt", "Pn", "Št", "Sk"]


def fetch_week(monday):
    params = dict(BASE_PARAMS)
    params["from"] = monday.isoformat()
    params["till"] = (monday + timedelta(days=6)).isoformat()
    r = requests.get(API, params=params, timeout=30,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json()["data"]["content"]


def parse(html, monday):
    """Grąžina {raktas: treniruotės būsena}. Stulpelio numeris = savaitės diena."""
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    for row in soup.select("#schedulesBody .body__row"):
        for idx, cell in enumerate(row.select(".table__cell-entry")):
            day = monday + timedelta(days=idx)
            for w in cell.select(".workout"):
                def txt(sel):
                    el = w.select_one(sel)
                    return el.get_text(" ", strip=True) if el else ""

                classes = w.get("class", [])
                time_txt = txt(".workout__time")
                avail = txt(".workout__availability")

                # Registracija atidaryta = yra aktyvus mygtukas „Registruotis"
                can_register = any(
                    "registruot" in b.get_text(" ", strip=True).lower()
                    and not b.has_attr("disabled")
                    for b in w.select("button, a")
                )
                full = "no-place" in classes or "nėra laisvų" in avail.lower()
                m = re.search(r"(\d+)\s*iš\s*(\d+)", avail, re.I)

                start = None
                t = re.match(r"(\d{1,2}):(\d{2})", time_txt)
                if t:
                    start = datetime.combine(
                        day, time(int(t.group(1)), int(t.group(2))), TZ).isoformat()

                wid = w.get("data-workout", "")
                found[f"{day.isoformat()}|{time_txt}|{wid}"] = {
                    "date": day.isoformat(),
                    "weekday": DAYS[idx],
                    "time": time_txt,
                    "start": start,
                    "title": txt(".workout__title"),
                    "coach": txt(".workout__coach"),
                    "week_start": monday.isoformat(),
                    "open": can_register,
                    "full": full,
                    "free": int(m.group(1)) if m else None,
                    "total": int(m.group(2)) if m else None,
                    "bookable": can_register and not full,
                }
    return found


def is_future(v, now):
    return v["start"] is None or datetime.fromisoformat(v["start"]) > now


def compute_events(old, current, now):
    """opened = atsidarė registracija; freed = pilnoje treniruotėje atsirado vietų."""
    opened, freed = [], []
    for k, v in current.items():
        if not v["bookable"] or not is_future(v, now):
            continue
        if ONLY and not any(s in v["title"].lower() for s in ONLY):
            continue
        prev = old.get(k)
        if prev is not None and prev.get("full"):
            freed.append(v)
        elif prev is None or not prev.get("bookable"):
            opened.append(v)
    return opened, freed


def line(v):
    spots = f' (laisva {v["free"]}/{v["total"]})' if v["free"] is not None else ""
    return (f'{v["date"]} {v["weekday"]}  {v["time"]}  {v["title"]}'
            f'  –  {v["coach"]}{spots}')


def send_mail(opened, freed):
    parts, weeks = [], set()
    if opened:
        parts.append("ATSIDARĖ REGISTRACIJA:\n" + "\n".join(
            line(v) for v in sorted(opened, key=lambda x: x["start"] or "")))
    if freed:
        parts.append("ATSILAISVINO VIETŲ:\n" + "\n".join(
            line(v) for v in sorted(freed, key=lambda x: x["start"] or "")))
    for v in opened + freed:
        weeks.add(v["week_start"])
    links = []
    for ws in sorted(weeks):
        start = datetime.fromisoformat(ws).date()
        links.append(f'https://gymplius.lt/tvarkarastis/?subdivision={BASE_PARAMS["subdivision"]}'
                     f'&from={start}&till={start + timedelta(days=6)}')

    subject = []
    if opened:
        subject.append(f"atsidarė registracija ({len(opened)})")
    if freed:
        subject.append(f"atsilaisvino vietų ({len(freed)})")

    msg = EmailMessage()
    msg["Subject"] = "Gym+: " + ", ".join(subject)
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg.set_content("\n\n".join(parts) + "\n\nRegistracija:\n" + "\n".join(links) + "\n")

    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)


def show_status(current, now):
    for v in sorted(current.values(), key=lambda x: x["start"] or ""):
        if not is_future(v, now):
            continue
        if v["bookable"]:
            st = f'REGISTRACIJA ATIDARYTA, laisva {v["free"]}/{v["total"]}'
        elif v["full"]:
            st = "nėra laisvų vietų"
        elif v["start"]:
            opens = datetime.fromisoformat(v["start"]) - timedelta(hours=REG_HOURS)
            st = f"dar neatidaryta (atsidarys {opens:%m-%d %H:%M})"
        else:
            st = "neaišku"
        print(f'{v["date"]} {v["weekday"]} {v["time"]:<14} {v["title"]:<20} {st}')


def main():
    now = datetime.now(TZ)
    today = now.date()
    this_monday = today - timedelta(days=today.weekday())

    current = {}
    for i in range(WEEKS):
        monday = this_monday + timedelta(weeks=i)
        try:
            current.update(parse(fetch_week(monday), monday))
        except Exception as e:  # viena savaitė nepavyko – tęsiame su kitomis
            print(f"Savaitės {monday} nepavyko nuskaityti: {e}")

    if not current:
        print("Tvarkaraštis tuščias arba nepavyko jo perskaityti; būsena nekeičiama.")
        sys.exit(1)

    if "--status" in sys.argv:
        show_status(current, now)
        return

    old = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            old = json.load(f)

    # Pirmas paleidimas arba sena būsenos struktūra: tik išsaugome, nesiunčiame
    if not old or any("bookable" not in v for v in old.values()):
        save_state({}, current, today)
        print(f"Pradinė būsena išsaugota ({len(current)} treniruočių), laiškas nesiunčiamas.")
        return

    opened, freed = compute_events(old, current, now)
    if opened or freed:
        send_mail(opened, freed)  # jei nepavyks, būsena neišsaugoma ir bus bandoma vėl
        print(f"Išsiųsta: atsidarė {len(opened)}, atsilaisvino {len(freed)}")
    else:
        print("Pokyčių nėra.")
    save_state(old, current, today)


def save_state(old, current, today):
    cutoff = (today - timedelta(days=7)).isoformat()
    state = {k: v for k, v in old.items() if v.get("date", "") >= cutoff}
    state.update(current)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
