"""
indices_importer.py — Fetch and import daily solar indices.

Sources:
  SILSO (Royal Observatory of Belgium) — International Sunspot Number v2
    https://www.sidc.be/SILSO/DATA/SN_d_tot_V2.0.csv
    Semicolon-delimited.  Full history 1818–present, updated daily.

  NOAA SWPC — Observed F10.7 solar radio flux (last 30 days)
    https://services.swpc.noaa.gov/products/10cm-flux-30-day.json

  NOAA SWPC — Estimated planetary Kp index
    https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json
    3-hour intervals; averaged to daily values.

Usage:
  python api/indices_importer.py                 # import all
  python api/indices_importer.py --sunspot-only  # only SILSO sunspot numbers
"""

import logging
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import mysql.connector
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DB_CONFIG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SILSO — daily total sunspot number
# ---------------------------------------------------------------------------
SILSO_URL = "https://www.sidc.be/SILSO/DATA/SN_d_tot_V2.0.csv"


def import_silso() -> int:
    """
    Download the full SILSO sunspot catalog and upsert into solar_indices.
    CSV is semicolon-delimited: YYYY;MM;DD;<decimal>;  SN;  err;  nobs;provider

    Returns number of rows inserted/updated.
    """
    log.info("Downloading SILSO sunspot data …")
    resp = requests.get(SILSO_URL, timeout=120)
    lines = resp.text.strip().splitlines()

    cnx = mysql.connector.connect(**DB_CONFIG)
    cursor = cnx.cursor()

    sql = (
        "INSERT INTO solar_indices (date, sunspot_number, source) "
        "VALUES (%s, %s, 'SILSO') "
        "ON DUPLICATE KEY UPDATE sunspot_number = VALUES(sunspot_number)"
    )

    count = 0
    for line in lines:
        if not line or line.startswith("#"):
            continue
        parts = line.split(";")
        if len(parts) < 5:
            continue
        try:
            yr, mo, dy = int(parts[0]), int(parts[1]), int(parts[2])
            sn = float(parts[4].strip())
            if sn < 0:  # -1 = no data
                continue
            dt = date(yr, mo, dy)
            cursor.execute(sql, (dt, sn))
            count += 1
        except (ValueError, IndexError):
            continue

    cnx.commit()
    cursor.close()
    cnx.close()
    log.info("SILSO: %d rows imported/updated.", count)
    return count


# ---------------------------------------------------------------------------
# NOAA — observed F10.7 flux (last 30 days)
# ---------------------------------------------------------------------------
F10_7_URL = "https://services.swpc.noaa.gov/products/10cm-flux-30-day.json"


def import_f10_7() -> int:
    """
    Fetch the last 30 days of observed F10.7 flux and upsert into solar_indices.
    """
    log.info("Fetching F10.7 flux data …")
    resp = requests.get(F10_7_URL, timeout=30)
    records = resp.json()

    cnx = mysql.connector.connect(**DB_CONFIG)
    cursor = cnx.cursor()

    sql = (
        "INSERT INTO solar_indices (date, f10_7_flux) "
        "VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE f10_7_flux = VALUES(f10_7_flux)"
    )

    count = 0
    for rec in records:
        try:
            dt_str = rec.get("time_tag", "")
            flux = rec.get("flux")
            if not dt_str or flux is None:
                continue
            dt = datetime.fromisoformat(dt_str).date()
            cursor.execute(sql, (dt, float(flux)))
            count += 1
        except (ValueError, KeyError):
            continue

    cnx.commit()
    cursor.close()
    cnx.close()
    log.info("F10.7: %d rows imported/updated.", count)
    return count


# ---------------------------------------------------------------------------
# NOAA — estimated planetary Kp index
# ---------------------------------------------------------------------------
KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"


def import_kp() -> int:
    """
    Fetch the NOAA estimated planetary Kp index and upsert into solar_indices.
    Each record covers a 3-hour interval; we average to a daily value.
    """
    log.info("Fetching Kp index data …")
    resp = requests.get(KP_URL, timeout=30)
    records = resp.json()

    daily = defaultdict(list)

    for rec in records:
        try:
            dt_str = rec.get("time_tag", "")
            kp = rec.get("Kp")  # NOAA uses capital "Kp"
            if not dt_str or kp is None:
                continue
            dt = datetime.fromisoformat(dt_str).date()
            daily[dt].append(float(kp))
        except (ValueError, KeyError):
            continue

    cnx = mysql.connector.connect(**DB_CONFIG)
    cursor = cnx.cursor()

    count = 0
    for dt, values in daily.items():
        avg_kp = sum(values) / len(values)
        ap = _kp_to_ap(avg_kp)
        cursor.execute(
            "INSERT INTO solar_indices (date, kp_index, ap_index) "
            "VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE "
            "kp_index = VALUES(kp_index), ap_index = VALUES(ap_index)",
            (dt, round(avg_kp, 2), ap),
        )
        count += 1

    cnx.commit()
    cursor.close()
    cnx.close()
    log.info("Kp: %d daily averages imported/updated.", count)
    return count


def _kp_to_ap(kp: float) -> int:
    """Convert Kp index to equivalent Ap index."""
    table = [
        (0, 0), (0.3, 2), (0.7, 3), (1.0, 4), (1.3, 5),
        (1.7, 6), (2.0, 7), (2.3, 9), (2.7, 12), (3.0, 15),
        (3.3, 18), (3.7, 22), (4.0, 27), (4.3, 32), (4.7, 39),
        (5.0, 48), (5.3, 56), (5.7, 67), (6.0, 80), (6.3, 94),
        (6.7, 111), (7.0, 132), (7.3, 154), (7.7, 179),
        (8.0, 207), (8.3, 236), (8.7, 300), (9.0, 400),
    ]
    for threshold, ap in table:
        if kp <= threshold + 0.15:
            return ap
    return 400


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(sunspot_only: bool = False) -> None:
    log.info("=== Solar indices import ===")

    n_sunspot = import_silso()
    if not sunspot_only:
        n_f10 = import_f10_7()
        n_kp = import_kp()
        log.info(
            "Summary: %d sunspot, %d F10.7, %d Kp rows",
            n_sunspot, n_f10, n_kp,
        )
    else:
        log.info("Summary: %d sunspot rows (--sunspot-only)", n_sunspot)

    log.info("=== Done ===")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Import daily solar indices from SILSO and NOAA."
    )
    parser.add_argument(
        "--sunspot-only",
        action="store_true",
        help="Only import SILSO sunspot numbers.",
    )
    args = parser.parse_args()
    main(sunspot_only=args.sunspot_only)
