"""Gym+ grupinių treniruočių stebėjimas: el. laiškas, kai atsiranda nauja treniruotė."""
import json
import os
import smtplib
import sys
from datetime import datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

API = "https://gymplius.lt/wp-json/api/async-render-block"
BASE_PARAMS = {
    "subdivision": "77",  # URBAN HUB Vilnius; kitą klubą rasite tvarkaraščio URL
    "pid": "MjQ3",
    "bid": "YWNmLWJsb2NrLTY3ZmNhOTg2NmQ4ZTc2LjMzNTY5NjI0",
    "rest_language": "lt",
}
WEEKS = int(os.getenv("WEEKS", "2"))  # kiek savaičių stebėti (dabartinė + kitos)
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
    """Grąžina {raktas: treniruotės info}. Stulpelio numeris = savaitės diena."""
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    for row in soup.select("#schedulesBody .body__row"):
        for idx, cell in enumerate(row.select(".table__cell-entry")):
            day = monday + timedelta(days=idx)
            for w in cell.select(".workout"):
                def txt(sel):
                    el = w.select_one(sel)
                    return el.get_text(" ", strip=True) if el else ""
                wid = w.get("data-workout", "")
                time = txt(".workout__time")
                key = f"{day.isoformat()}|{time}|{wid}"
                found[key] = {
                    "date": day.isoformat(),
                    "weekday": DAYS[idx],
                    "time": time,
                    "title": txt(".workout__title"),
                    "coach": txt(".workout__coach"),
                    "full": "no-place" in w.get("class", []),
                    "week_start": monday.isoformat(),
                }
    return found


def send_mail(new_items):
    lines, weeks = [], set()
    for it in sorted(new_items, key=lambda x: (x["date"], x["time"])):
        full = " (vietų nėra)" if it["full"] else ""
        lines.append(f'{it["date"]} {it["weekday"]}  {it["time"]}  {it["title"]}'
                     f'  –  {it["coach"]}{full}')
        weeks.add(it["week_start"])
    links = []
    for ws in sorted(weeks):
        start = datetime.fromisoformat(ws).date()
        end = start + timedelta(days=6)
        links.append(f'https://gymplius.lt/tvarkarastis/?subdivision={BASE_PARAMS["subdivision"]}'
                     f'&from={start}&till={end}')

    msg = EmailMessage()
    msg["Subject"] = f"Gym+: nauja treniruotė tvarkaraštyje ({len(new_items)})"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg.set_content("Atsirado naujų treniruočių:\n\n" + "\n".join(lines)
                    + "\n\nRegistracija:\n" + "\n".join(links) + "\n")

    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)


def main():
    today = datetime.now(ZoneInfo("Europe/Vilnius")).date()
    this_monday = today - timedelta(days=today.weekday())

    current = {}
    for i in range(WEEKS):
        monday = this_monday + timedelta(weeks=i)
        current.update(parse(fetch_week(monday), monday))

    if not current:
        print("Tvarkaraštis tuščias arba nepavyko jo perskaityti; būsena nekeičiama.")
        sys.exit(1)

    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=1)
        print(f"Pirmas paleidimas: išsaugota {len(current)} treniruočių, laiškas nesiunčiamas.")
        return

    with open(STATE_FILE, encoding="utf-8") as f:
        old = json.load(f)

    new_items = [v for k, v in current.items()
                 if k not in old and v["date"] >= today.isoformat()]
    if new_items:
        send_mail(new_items)  # jei siuntimas nepavyks, būsena neišsaugoma ir bus bandoma vėl
        print(f"Išsiųsta pranešimų: {len(new_items)}")
    else:
        print("Naujų treniruočių nėra.")

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
