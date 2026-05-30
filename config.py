import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

# --- Database ---
DB_CONFIG = {
    "host": os.getenv("HOST", "localhost"),
    "port": int(os.getenv("PORT", 3306)),
    "user": os.getenv("USERDB", "solar"),
    "password": os.getenv("PASSWORD", ""),
    "database": os.getenv("DATABASE", "SolarFlare"),
}

# --- Reference coordinates: geometric center of Mexico ---
LAT = 24.05754867
LON = -104.0226393

# --- NOAA data sources ---
GOES_16_NETCDF_URL = (
    "https://data.ngdc.noaa.gov/platforms/"
    "solar-space-observing-satellites/goes/goes16/l2/data/xrsf-l2-flsum_science/"
)
GOES_19_NETCDF_URL = (
    "https://data.ngdc.noaa.gov/platforms/"
    "solar-space-observing-satellites/goes/goes19/l2/data/xrsf-l2-flsum_science/"
)
GOES_JSON_URL = "https://services.swpc.noaa.gov/json/goes/primary/"

# SWPC historical flare catalog
SWPC_CATALOG_PATH = "data/flares-goes-x-ray-unified.dat"

# --- Cross-calibration ---
# Legacy GOES (pre-GOES-16) reports XRS-B fluxes ~30% higher than GOES-R series.
# Divide by 0.7 to bring them onto the GOES-16 XRS-B scale.
LEGACY_CORRECTION_FACTOR = 0.7

# --- netCDF time base ---
# netCDF time is stored as "seconds since 2000-01-01 12:00:00 UTC"
BASE_DATE = datetime(2000, 1, 1, 12, 0, 0)

# --- Satellite era boundaries ---
GOES_16_START = datetime(2017, 2, 9)   # First GOES-16 netCDF data
GOES_19_START = datetime(2024, 9, 20)  # First GOES-19 netCDF data
SWPC_END_INDEX = 76421                 # SWPC catalog index ≈ Feb 2017

# --- Data directory ---
DATA_DIR = "data"
