"""
historical_load.py — One-shot bulk import of historical flare data.

Sources:
  1. SWPC unified catalog (1975–2017) — flares-goes-x-ray-unified.dat
  2. GOES-16 netCDF flux summary (2017–2024)
  3. GOES-19 netCDF flux summary (2024–present)

All fluxes are normalized to the GOES-16 XRS-B (0.1–0.8 nm) scale.
Legacy SWPC fluxes are divided by 0.7 for cross-calibration.

Usage:
  python historical_load.py               # use cached netCDF files
  python historical_load.py --refresh      # re-download netCDF files from NOAA
  python historical_load.py --dry-run      # process but don't insert
"""

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mysql.connector
import netCDF4
import numpy as np
import pandas as pd
import pytz
import requests
from bs4 import BeautifulSoup
from pysolar import solar

from config import (
    BASE_DATE,
    DATA_DIR,
    DB_CONFIG,
    GOES_16_NETCDF_URL,
    GOES_16_START,
    GOES_19_NETCDF_URL,
    GOES_19_START,
    LAT,
    LEGACY_CORRECTION_FACTOR,
    LON,
    SWPC_CATALOG_PATH,
    SWPC_END_INDEX,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Download helpers (from netcdf_export.ipynb cell 1)
# ---------------------------------------------------------------------------
def _scrape_first_nc(url: str) -> str:
    """Scrape an Apache-style directory listing for the first .nc file."""
    response = requests.get(url, timeout=30)
    soup = BeautifulSoup(response.text, "html.parser")
    for a in soup.find_all("a", href=True):
        if a["href"].endswith(".nc"):
            return a["href"]
    raise FileNotFoundError(f"No .nc file found at {url}")


def _download_file(url: str, dest_dir: str) -> str:
    local_filename = url.split("/")[-1]
    dest_path = f"{dest_dir}/{local_filename}"
    log.info("Downloading %s …", local_filename)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    log.info("Downloaded %s", local_filename)
    return local_filename


def _ensure_netcdf(netcdf_url: str, label: str, refresh: bool = False) -> str:
    """Return the path to a cached netCDF file, downloading if needed."""
    fname = _scrape_first_nc(netcdf_url)
    dest = f"{DATA_DIR}/{fname}"

    import os

    if os.path.exists(dest) and not refresh:
        log.info("%s: using cached %s", label, fname)
    else:
        log.info("%s: fetching %s", label, fname)
        _download_file(netcdf_url + fname, DATA_DIR)
    return dest


# ---------------------------------------------------------------------------
# 2. netCDF → DataFrame (from notebook cells 3 & 15)
# ---------------------------------------------------------------------------
def nc_to_pandas(filename: str) -> pd.DataFrame:
    """Read a GOES flux-summary netCDF file into a DataFrame."""
    log.info("Loading %s …", filename)
    with netCDF4.Dataset(filename) as ds:
        df = pd.DataFrame()
        for k in ds.variables.keys():
            df[k] = pd.Series(np.ma.filled(ds[k][:]), name=k)
    return df


def combine_flares(flares: pd.DataFrame) -> dict:
    """
    Collapse per-status rows (EVENT_START, EVENT_PEAK, EVENT_END) into
    one row per flare_id.  Computes solar altitude/azimuth at peak.
    """
    flare_ids = flares["flare_id"].unique()
    flare_count = len(flare_ids)

    data = {
        "event_start": [],
        "event_peak": [],
        "event_end": [],
        "magnitude": [],
        "altitude": [],
        "azimuth": [],
        "integrated_flux": [],
        "xrsb_flux_peak": [],
    }

    processed = 0
    for id_ in flare_ids:
        event = flares[flares["flare_id"] == id_]
        if event.shape[0] < 3:
            continue

        try:
            flare_type = "".join(
                event[event["status"] == "EVENT_PEAK"]["flare_class"].iloc[0]
            )
            integrated = float(
                event[event["status"] == "EVENT_END"]["integrated_flux"].iloc[0]
            )
            flux_peak = float(
                event[event["status"] == "EVENT_PEAK"]["xrsb_flux"].iloc[0]
            )

            t_start = int(event[event["status"] == "EVENT_START"]["time"].iloc[0])
            t_peak = int(event[event["status"] == "EVENT_PEAK"]["time"].iloc[0])
            t_end = int(event[event["status"] == "EVENT_END"]["time"].iloc[0])

            peak_dt = pytz.utc.localize(BASE_DATE + timedelta(seconds=t_peak))
            start_dt = pytz.utc.localize(BASE_DATE + timedelta(seconds=t_start))
            end_dt = pytz.utc.localize(BASE_DATE + timedelta(seconds=t_end))

            alt = solar.get_altitude(LAT, LON, peak_dt)
            az = solar.get_azimuth(LAT, LON, peak_dt)

            data["magnitude"].append(flare_type)
            data["event_start"].append(start_dt.isoformat())
            data["event_peak"].append(peak_dt.isoformat())
            data["event_end"].append(end_dt.isoformat())
            data["altitude"].append(alt)
            data["azimuth"].append(az)
            data["integrated_flux"].append(integrated)
            data["xrsb_flux_peak"].append(flux_peak)
            processed += 1
        except IndexError:
            continue

    log.info(
        "  Flare events detected: %d  |  Processed: %d  |  Discarded: %d",
        flare_count,
        processed,
        flare_count - processed,
    )
    return data


# ---------------------------------------------------------------------------
# 3. Flare classification helper (from notebook cell 20)
# ---------------------------------------------------------------------------
def estimate_flare_category(xrsb_flux_peak: float) -> tuple:
    """Return (letter_class, subcategory_number) for a peak XRS-B flux."""
    thresholds = [
        ("A", 1e-8, 1e8),
        ("B", 1e-7, 1e7),
        ("C", 1e-6, 1e6),
        ("M", 1e-5, 1e5),
        ("X", 1e-4, 1e4),
    ]
    for letter, limit, multiplier in thresholds:
        if xrsb_flux_peak < limit * 10:
            return letter, round(xrsb_flux_peak * multiplier, 2)
    return "X", round(xrsb_flux_peak * 1e4, 2)


# ---------------------------------------------------------------------------
# 4. Load SWPC historical catalog (from notebook cells 21, 27, 28)
# ---------------------------------------------------------------------------
def load_swpc_catalog() -> pd.DataFrame:
    """Load, correct, and normalize the SWPC flare catalog."""
    log.info("Loading SWPC catalog …")
    df = pd.read_csv(SWPC_CATALOG_PATH, sep="\t")

    # Keep the observation_instrument before renaming/dropping
    df["t-inicio"] = pd.to_datetime(df["t-inicio"])
    df["t-max"] = pd.to_datetime(df["t-max"])
    df["t-fin"] = pd.to_datetime(df["t-fin"])
    df = df.drop(["unknown"], axis=1)

    # Apply 0.7 cross-calibration correction
    df["total X-ray flux"] = df["total X-ray flux"] / LEGACY_CORRECTION_FACTOR

    # Rename to match unified schema
    df = df.rename(
        columns={
            "t-inicio": "event_start",
            "t-fin": "event_end",
            "t-max": "event_peak",
            "total X-ray flux": "xrsb_flux_peak",
            "altitud": "altitude",
            "Instrumento": "instrument",
        }
    )

    # Filter to GOES-era (post-1974)
    df = df[df["event_peak"] >= datetime(1974, 1, 1, 12, 0, 0).isoformat()]

    # Trim to pre-GOES-16 era
    df = df[df.index < SWPC_END_INDEX]

    # Recompute magnitude from corrected flux
    magnitudes = []
    for flux in df["xrsb_flux_peak"]:
        cat, sub = estimate_flare_category(flux)
        magnitudes.append(f"{cat}{sub}")
    df["magnitude"] = magnitudes

    df["instrument"] = df["instrument"].str.strip()

    df["source"] = "SWPC"
    df["integrated_flux"] = None  # not available for SWPC

    # Keep only columns in the unified schema
    df = df[
        [
            "event_start",
            "event_peak",
            "event_end",
            "magnitude",
            "xrsb_flux_peak",
            "integrated_flux",
            "instrument",
            "altitude",
            "azimuth",
            "source",
        ]
    ]

    log.info("SWPC catalog: %d rows ready", len(df))
    return df


# ---------------------------------------------------------------------------
# 5. Process a GOES-R netCDF file
# ---------------------------------------------------------------------------
def process_goes_nc(nc_path: str, source_label: str) -> pd.DataFrame:
    """Load a GOES netCDF file, combine flares, and return a DataFrame."""
    log.info("Processing %s (%s) …", nc_path, source_label)
    raw = nc_to_pandas(nc_path)
    d = combine_flares(raw)
    df = pd.DataFrame(d)

    df["event_start"] = pd.to_datetime(df["event_start"])
    df["event_peak"] = pd.to_datetime(df["event_peak"])
    df["event_end"] = pd.to_datetime(df["event_end"])

    df["source"] = source_label
    df["instrument"] = source_label  # "GOES-16" or "GOES-19"

    df = df[
        [
            "event_start",
            "event_peak",
            "event_end",
            "magnitude",
            "xrsb_flux_peak",
            "integrated_flux",
            "instrument",
            "altitude",
            "azimuth",
            "source",
        ]
    ]
    log.info("%s: %d flare events ready", source_label, len(df))
    return df


# ---------------------------------------------------------------------------
# 6. Merge and trim overlap
# ---------------------------------------------------------------------------
def merge_datasets(
    swpc: pd.DataFrame, g16: pd.DataFrame, g19: pd.DataFrame
) -> pd.DataFrame:
    """Merge SWPC, GOES-16, and GOES-19, trimming overlaps."""
    # SWPC ends Feb 2017 — no overlap trim needed with GOES-16
    #   because SWPC was already trimmed at index SWPC_END_INDEX.

    # Trim GOES-16 at GOES-19 start date
    g19_start_utc = pytz.utc.localize(GOES_19_START)
    g16_trimmed = g16[g16["event_start"] < g19_start_utc]
    log.info(
        "GOES-16 trimmed: %d before %s (%d removed for GOES-19 overlap)",
        len(g16_trimmed),
        GOES_19_START.date(),
        len(g16) - len(g16_trimmed),
    )

    merged = pd.concat([swpc, g16_trimmed, g19], ignore_index=True)
    log.info("Merged catalog: %d total flares", len(merged))
    log.info(
        "Date range: %s → %s",
        merged["event_start"].min(),
        merged["event_start"].max(),
    )
    return merged


# ---------------------------------------------------------------------------
# 7. MySQL bulk insert
# ---------------------------------------------------------------------------
def insert_flares(df: pd.DataFrame, dry_run: bool = False) -> int:
    """
    Insert flares into the `flares` table with dedup on
    (event_start, instrument).  Returns count of inserted rows.
    """
    if dry_run:
        log.info("DRY RUN — %d rows would be inserted", len(df))
        return 0

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
                row["instrument"],
                float(row["altitude"]) if not pd.isna(row["altitude"]) else None,
                float(row["azimuth"]) if not pd.isna(row["azimuth"]) else None,
                row["source"],
            )
        )

    BATCH_SIZE = 5000
    inserted = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        cursor.executemany(sql, batch)
        cnx.commit()
        inserted += cursor.rowcount
        log.info(
            "  Batch %d/%d: %d inserted",
            i // BATCH_SIZE + 1,
            (len(rows) + BATCH_SIZE - 1) // BATCH_SIZE,
            cursor.rowcount,
        )

    cursor.close()
    cnx.close()
    log.info("Total inserted: %d", inserted)
    return inserted


# ---------------------------------------------------------------------------
# 8. Main entry point
# ---------------------------------------------------------------------------
def main(refresh: bool = False, dry_run: bool = False) -> None:
    log.info("=== Historical flare data import ===")

    # -- SWPC --
    swpc = load_swpc_catalog()

    # -- GOES-16 --
    g16_path = _ensure_netcdf(GOES_16_NETCDF_URL, "GOES-16", refresh=refresh)
    g16 = process_goes_nc(g16_path, "GOES-16")

    # -- GOES-19 --
    g19_path = _ensure_netcdf(GOES_19_NETCDF_URL, "GOES-19", refresh=refresh)
    g19 = process_goes_nc(g19_path, "GOES-19")

    # -- Merge --
    all_df = merge_datasets(swpc, g16, g19)

    # -- Insert --
    count = insert_flares(all_df, dry_run=dry_run)

    log.info("=== Done. %d flares imported ===", count)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Bulk-import historical GOES X-ray flare data into MySQL."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-download netCDF files from NOAA even if cached.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Process data but do not insert into the database.",
    )
    args = parser.parse_args()
    main(refresh=args.refresh, dry_run=args.dry_run)
