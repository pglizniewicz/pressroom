#!/usr/bin/env python3
"""Backfill the terratec.de pressemit/ files that exist in the full Wayback
directory listing but weren't linked from the two index pages already
scraped (backfill_terratec_de_and_net_gaps.py) - confirmed via a full
site:terratec.de/presse/pressemit/ timemap diff against pressroom.db.

Usage:
  python backfill_terratec_de_full_dir.py
"""

import sqlite3
import time
from pathlib import Path

import requests

from common import HEADERS, SLEEP, already_stored, init_db
from backfill_terratec_de_and_net_gaps import parse_de_snapshot
import wayback

DB_PATH = Path(__file__).parent / "pressroom.db"
SOURCE = "terratec_de"

URLS = [
    "http://www.terratec.de:80/presse/pressemit/5800wasser.htm",
    "http://www.terratec.de:80/presse/pressemit/ad2netag.htm",
    "http://www.terratec.de:80/presse/pressemit/Cameo_200_DV.htm",
    "http://www.terratec.de:80/presse/pressemit/cameo_grabster.htm",
    "http://www.terratec.de:80/presse/pressemit/car4000.htm",
    "http://www.terratec.de:80/presse/pressemit/car_4000.htm",
    "http://www.terratec.de:80/presse/pressemit/Cinergy_400_TV.htm",
    "http://www.terratec.de:80/presse/pressemit/DR_Box_1.htm",
    "http://www.terratec.de:80/presse/pressemit/drbox1.htm",
    "http://www.terratec.de:80/presse/pressemit/DRBox1_2.htm",
    "http://www.terratec.de:80/presse/pressemit/ews96m.htm",
    "http://www.terratec.de:80/presse/pressemit/ews96m2.htm",
    "http://www.terratec.de:80/presse/pressemit/homearena_2_1.htm",
    "http://www.terratec.de:80/presse/pressemit/homearenastereo.htm",
    "http://www.terratec.de:80/presse/pressemit/ifa.htm",
    "http://www.terratec.de:80/presse/pressemit/MidiMaster_usb.htm",
    "http://www.terratec.de:80/presse/pressemit/mp3_cd_player.htm",
    "http://www.terratec.de:80/presse/pressemit/ppa-studio.htm",
    "http://www.terratec.de:80/presse/pressemit/sixpack.htm",
    "http://www.terratec.de:80/presse/pressemit/tt_besonic.htm",
    "http://www.terratec.de:80/presse/pressemit/tvalue-radio.htm",
    "http://www.terratec.de:80/presse/pressemit/TXR_335.htm",
    "http://www.terratec.de:80/presse/pressemit/TXR_665.htm",
]


def backfill() -> None:
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    session = requests.Session()

    new_count = 0
    dead_count = 0

    for url in URLS:
        if already_stored(conn, url):
            print(".", end="", flush=True)
            continue

        try:
            found = wayback.get_latest_working_snapshot(url)
        except Exception as e:
            print(f"\n  ERROR probing snapshots for {url}: {e}")
            time.sleep(SLEEP * 2)
            continue
        if not found:
            dead_count += 1
            print(f"\n  no working snapshot for {url}")
            continue
        snapshot_url, timestamp = found

        try:
            r = session.get(snapshot_url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            parsed = parse_de_snapshot(r.text)
        except Exception as e:
            print(f"\n  ERROR fetching {snapshot_url}: {e}")
            continue
        time.sleep(SLEEP)

        conn.execute(
            "INSERT OR IGNORE INTO releases (source, detail_id, title, date, url, body) VALUES (?,?,?,?,?,?)",
            (SOURCE, timestamp, parsed["title"], parsed["date"], url, parsed["body"]),
        )
        conn.commit()
        new_count += 1
        print("+", end="", flush=True)

    total = conn.execute("SELECT count(*) FROM releases WHERE source = ?", (SOURCE,)).fetchone()[0]
    print(f"\nAdded {new_count} new, {dead_count} never archived successfully. Total [{SOURCE}] in DB: {total}")
    conn.close()


if __name__ == "__main__":
    backfill()
