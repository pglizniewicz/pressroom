#!/usr/bin/env python3
"""Backfill the terratec.de pressemit/ files that exist in the full Wayback
directory listing but weren't linked from the two index pages already
scraped (backfill_terratec_de_and_net_gaps.py) - confirmed via a full
site:terratec.de/presse/pressemit/ timemap diff against pressroom.db.

Usage:
  python backfill_terratec_de_full_dir.py
"""

import time

import requests

from fetch import SLEEP
from db import already_stored
import db
from progress import Stats
from backfill_terratec_de_and_net_gaps import parse_de_snapshot
import wayback

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
    conn = db.connect()
    session = requests.Session()

    stats = Stats(SOURCE)

    for url in URLS:
        if already_stored(conn, url):
            stats.skipped()
            continue

        try:
            found = wayback.get_latest_working_snapshot(url)
        except Exception as e:
            print(f"\n  ERROR probing snapshots for {url}: {e}")
            time.sleep(SLEEP * 2)
            stats.uncertain()
            continue
        if not found:
            stats.dead()
            continue
        snapshot_url, timestamp = found

        try:
            content = wayback.fetch_snapshot(conn, session, snapshot_url, timeout=20)
            parsed = parse_de_snapshot(content)
        except Exception as e:
            print(f"\n  ERROR fetching {snapshot_url}: {e}")
            stats.uncertain()
            continue

        if db.store_release(conn, SOURCE, url, title=parsed["title"],
                            date=parsed["date"], body=parsed["body"], detail_id=timestamp):
            stats.added()

    stats.summary(conn)
    conn.close()


if __name__ == "__main__":
    backfill()
