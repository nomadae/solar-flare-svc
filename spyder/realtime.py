"""
realtime.py — Poll NOAA's JSON endpoint for the latest GOES X-ray flare.

Derived from José Vidal Cardona Rosas' spyder.py (2021, GPL v3).
Refactored to feed the unified `flares` table.

The NOAA JSON endpoint provides data from the current operational
GOES satellite (GOES-19 since April 2025).  Only the single most
recent flare event is available; this script polls every 300 s to
catch flares within minutes of their occurrence.

Note: the JSON endpoint reports XRS-Long flux (0.05–0.4 nm), not
the XRS-B channel (0.1–0.8 nm) used by the netCDF catalog.  The
XRS-Long value is preserved in `metadata.xrslong_flux`; the
`xrsb_flux_peak` column is left NULL for JSON-sourced rows.

Usage:
  python spyder/realtime.py              # run once
  python spyder/realtime.py --loop 300   # poll every 300 s (5 min)
"""

import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import mysql.connector
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser
from pysolar import solar

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    DB_CONFIG,
    GOES_JSON_URL,
    LAT,
    LON,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def _fetch_latest_flare() -> dict:
    """
    Scrape the NOAA JSON directory for xray-flares-latest.json,
    download and parse it.  Returns the inner flare dict, or {}
    if no completed flare is available.
    """
    import json

    # Scrape directory for the JSON file link
    page = requests.get(GOES_JSON_URL, timeout=30)
    soup = BeautifulSoup(page.text, "html.parser")
    json_href = None
    for a in soup.find_all("a", href=True):
        if a["href"] == "xray-flares-latest.json":
            json_href = a["href"]
            break

    if not json_href:
        log.error("Could not find xray-flares-latest.json link on %s", GOES_JSON_URL)
        return {}

    # Fetch and parse the JSON array
    resp = requests.get(GOES_JSON_URL + json_href, timeout=30)
    raw_text = resp.text.strip()

    # NOAA occasionally serves malformed JSON (missing the closing ']').
    # Try a strict parse first; if it fails, attempt recovery.
    try:
        flares = json.loads(raw_text)
    except json.JSONDecodeError:
        if raw_text.startswith("[") and not raw_text.endswith("]"):
            raw_text += "]"
        flares = json.loads(raw_text)

    if not flares or not isinstance(flares, list):
        log.warning("JSON response is not an array; got: %s", type(flares))
        return {}
    return flares[0]


def _is_complete(flare: dict) -> bool:
    """A flare is complete only if max_time and end_time are known."""
    mt = flare.get("max_time", "")
    et = flare.get("end_time", "")
    if mt in ("Unk", None, "") or et in ("Unk", None, ""):
        return False
    return True


def _map_to_schema(data: dict) -> dict | None:
    """Map the raw JSON fields to the unified `flares` schema."""
    if not data or not _is_complete(data):
        return None

    import json

    instrument = data.get("satellite", None)
    classification = data.get("max_class") or data.get("current_class") or ""

    t_max = dateutil_parser.parse(data["max_time"])
    alt = solar.get_altitude(LAT, LON, t_max)
    az = solar.get_azimuth(LAT, LON, t_max)

    xrs_long = data.get("max_xrlong") or data.get("current_int_xrlong")

    return {
        "event_start": data.get("begin_time"),
        "event_peak": data.get("max_time"),
        "event_end": data.get("end_time"),
        "magnitude": classification,
        "xrsb_flux_peak": None,                # JSON source doesn't provide XRS-B
        "integrated_flux": None,                # not available from JSON
        "instrument": f"G{instrument}",
        "altitude": alt,
        "azimuth": az,
        "source": "JSON",
        "metadata": json.dumps(
            {
                "xrslong_flux": xrs_long,
                "total_energy": 0,
                "active_region": 0,
                "latitude": 0.0,
                "longitude": 0.0,
            }
        ),
    }


def insert_if_new(flare: dict) -> bool:
    """
    Insert a flare into the database using INSERT IGNORE.
    Returns True if inserted, False if it was a duplicate.
    """
    cnx = mysql.connector.connect(**DB_CONFIG)
    cursor = cnx.cursor()

    sql = """
        INSERT IGNORE INTO flares (
            event_start, event_peak, event_end,
            magnitude, xrsb_flux_peak, integrated_flux,
            instrument, altitude, azimuth, source, metadata
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
    """

    cursor.execute(
        sql,
        (
            flare["event_start"],
            flare["event_peak"],
            flare["event_end"],
            flare["magnitude"],
            flare["xrsb_flux_peak"],
            flare["integrated_flux"],
            flare["instrument"],
            flare["altitude"],
            flare["azimuth"],
            flare["source"],
            flare["metadata"],
        ),
    )
    cnx.commit()
    inserted = cursor.rowcount > 0
    cursor.close()
    cnx.close()
    return inserted


def run_once() -> bool:
    """Poll once.  Returns True if a new flare was inserted."""
    log.info("Polling NOAA JSON endpoint …")
    raw = _fetch_latest_flare()
    flare = _map_to_schema(raw)
    if not flare:
        log.warning("No flare data returned.")
        return False

    inserted = insert_if_new(flare)
    if inserted:
        log.info(
            "New flare inserted: %s %s from %s",
            flare["magnitude"],
            flare["event_start"],
            flare["instrument"],
        )
    else:
        log.info(
            "Already in DB: %s %s from %s",
            flare["magnitude"],
            flare["event_start"],
            flare["instrument"],
        )
    return inserted


def main(loop_seconds: int | None = None) -> None:
    log.info("=== Realtime spyder starting ===")
    if loop_seconds:
        log.info("Polling every %d seconds.", loop_seconds)
        while True:
            try:
                run_once()
            except Exception:
                log.exception("Error during poll; will retry.")
            time.sleep(loop_seconds)
    else:
        run_once()
        log.info("=== Done ===")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Poll NOAA JSON for the latest GOES flare."
    )
    parser.add_argument(
        "--loop",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Run in a loop, polling every SECONDS.",
    )
    args = parser.parse_args()
    main(loop_seconds=args.loop)
