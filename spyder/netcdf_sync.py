"""
netcdf_sync.py — Daily GOES-19 netCDF refresh.

Downloads the latest GOES-19 XRS flux-summary netCDF from NOAA,
extracts new flare events, and inserts them into the `flares` table.

Only downloads when the remote filename has changed (NOAA includes
the end-date in the filename).  INSERT IGNORE ensures idempotency
— previously imported flares are silently skipped.

Usage:
  python spyder/netcdf_sync.py            # run once
  python spyder/netcdf_sync.py --refresh  # force re-download
"""

import logging
import sys
import time
from pathlib import Path

import mysql.connector
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    DATA_DIR,
    DB_CONFIG,
    GOES_19_NETCDF_URL,
)
from db.historical_load import (
    _download_file,
    _scrape_first_nc,
    combine_flares,
    nc_to_pandas,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def _cached_filename() -> str | None:
    """Return the newest GOES-19 .nc file in DATA_DIR, or None."""
    import os

    best = None
    for f in os.listdir(DATA_DIR):
        if f.startswith("sci_xrsf-l2-flsum_g19") and f.endswith(".nc"):
            if best is None or f > best:
                best = f
    return best


def sync(refresh: bool = False) -> int:
    """
    Check for a new GOES-19 netCDF file, download it if needed,
    and insert any new flare events into the database.

    Returns the number of newly inserted rows.
    """
    log.info("=== GOES-19 netCDF sync ===")

    remote_name = _scrape_first_nc(GOES_19_NETCDF_URL)
    log.info("Remote file: %s", remote_name)

    cached = _cached_filename()
    log.info("Cached file: %s", cached or "(none)")

    dest = f"{DATA_DIR}/{remote_name}"

    import os

    if not refresh and cached == remote_name and os.path.exists(dest):
        log.info("Already up to date.")
    else:
        log.info("Downloading new file …")
        _download_file(GOES_19_NETCDF_URL + remote_name, DATA_DIR)

    # Process
    raw = nc_to_pandas(dest)
    d = combine_flares(raw)
    df = pd.DataFrame(d)
    df["event_start"] = pd.to_datetime(df["event_start"])
    df["event_peak"] = pd.to_datetime(df["event_peak"])
    df["event_end"] = pd.to_datetime(df["event_end"])
    log.info("Extracted %d flare events from netCDF.", len(df))

    # Insert only new flares
    cnx = mysql.connector.connect(**DB_CONFIG)
    cursor = cnx.cursor()

    sql = """
        INSERT IGNORE INTO flares (
            event_start, event_peak, event_end,
            magnitude, xrsb_flux_peak, integrated_flux,
            instrument, altitude, azimuth, source
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
    """

    BATCH_SIZE = 2000
    total_inserted = 0
    rows = []
    for _, row in df.iterrows():
        rows.append(
            (
                row["event_start"].to_pydatetime(),
                row["event_peak"].to_pydatetime(),
                row["event_end"].to_pydatetime(),
                row["magnitude"],
                float(row["xrsb_flux_peak"]) if not pd.isna(row["xrsb_flux_peak"]) else None,
                float(row["integrated_flux"]) if not pd.isna(row["integrated_flux"]) else None,
                "GOES-19",
                float(row["altitude"]) if not pd.isna(row["altitude"]) else None,
                float(row["azimuth"]) if not pd.isna(row["azimuth"]) else None,
                "GOES-19",
            )
        )

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        cursor.executemany(sql, batch)
        cnx.commit()
        n = cursor.rowcount
        total_inserted += n
        if n:
            log.info("  Batch %d/%d: %d new flares inserted",
                     i // BATCH_SIZE + 1,
                     (len(rows) + BATCH_SIZE - 1) // BATCH_SIZE,
                     n)

    cursor.close()
    cnx.close()

    log.info("Total new flares inserted: %d", total_inserted)
    log.info("=== Sync complete ===")
    return total_inserted


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Refresh GOES-19 flare data from the latest netCDF."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force re-download even if the cached file matches.",
    )
    args = parser.parse_args()
    sync(refresh=args.refresh)
