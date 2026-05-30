# Spyder-SolarData-GICC

Unified GOES X-ray flare catalog with a live spyder for real-time updates.

- 92,670 solar flare events spanning 1975–present
- REST API with pagination, filtering, and Swagger docs
- Real-time ingestion from NOAA (JSON endpoint + netCDF files)
- Daily solar indices: sunspot number, F10.7 flux, Kp/Ap geomagnetic

## Architecture

```
                                NOAA
                                 │
                 ┌───────────────┼───────────────┐
                 │ JSON (latest)                 │ netCDF (GOES-19)
                 ▼                               ▼
          spyder/realtime.py            spyder/netcdf_sync.py
         (every 5 minutes)              (daily at 02:00)
                 │                               │
                 └───────────────┬───────────────┘
                                 ▼
                         MySQL (SolarFlare)
                                 │
                                 ▼
                          api/app.py
                       (FastAPI on :8000)
                                 │
                                 ▼
                        /docs  /flares  /stats  /indices
```

## Data sources

| Source | Period | Satellite | Channel |
|---|---|---|---|
| SWPC Historical Catalog | 1975 – 2017-02 | GOES-1 through GOES-15 | XRS-B (0.7-corrected) |
| GOES-16 netCDF | 2017-02 – 2024-09 | GOES-16 | XRS-B (0.1–0.8 nm) |
| GOES-19 netCDF | 2024-09 – present | GOES-19 | XRS-B (0.1–0.8 nm) |
| NOAA JSON (realtime) | now | GOES-18/19 | XRS-Long (0.05–0.4 nm) |

Legacy SWPC fluxes are divided by 0.7 to correct for the spectral
response difference between older and newer GOES XRS-B instruments.

## Quick start

### 1. Set up environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-prod.txt
```

### 2. Configure database

```bash
cp .env.example .env      # or use your existing .env
# Edit .env with your MySQL credentials
mysql -u root -p < db/schema.sql
```

### 3. Load historical data (one-time)

```bash
python db/historical_load.py           # dry-run first
python db/historical_load.py --refresh  # force re-download netCDFs
```

### 4. Start the API

```bash
uvicorn api.app:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000/docs for the interactive API browser.

### 5. Start the spyder (continuous updates)

```bash
python spyder/realtime.py --loop 300    # poll NOAA JSON every 5 min
python api/indices_importer.py          # import solar indices
```

Or install the crontab:

```bash
crontab crontab.example
```

## API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Service info |
| `GET` | `/flares` | Paginated flare list. Filters: `date_from`, `date_to`, `class` (C/M/X), `source`, `instrument`, `with_indices=true` |
| `GET` | `/flares/latest` | Most recent flare |
| `GET` | `/flares/{id}` | Single flare with solar indices |
| `GET` | `/stats` | Counts by class, year, instrument |
| `GET` | `/indices` | Solar indices. Filters: `date_from`, `date_to` |
| `GET` | `/docs` | Swagger UI |

### Example queries

```bash
curl "localhost:8000/flares?class=X&page_size=5"           # last 5 X-class flares
curl "localhost:8000/flares?date_from=2024-01-01&date_to=2024-12-31"  # 2024 flares
curl "localhost:8000/flares/92670"                         # flare with sunspot context
curl "localhost:8000/stats"                                # database summary
```

## Docker

```bash
cp .env.docker .env          # HOST=db for Docker networking
docker compose up -d          # API + spyder + MySQL
docker compose --profile tools up -d adminer  # DB browser on :8080
```

First-time historical load inside Docker:

```bash
docker compose exec spyder python db/historical_load.py
```

## Project structure

```
├── api/
│   ├── app.py                  # FastAPI application
│   └── indices_importer.py     # SILSO sunspot + NOAA F10.7/Kp import
├── db/
│   ├── schema.sql              # flares + solar_indices tables
│   ├── migrate.sql             # legacy observations → flares migration
│   └── historical_load.py      # one-shot bulk import (93K flares)
├── spyder/
│   ├── realtime.py             # NOAA JSON poller (every 5 min)
│   ├── netcdf_sync.py          # GOES-19 netCDF daily sync
│   └── entrypoint.sh           # Docker spyder service entrypoint
├── legacy/                     # original spyder.py and helpers
├── data/                       # cached netCDF files (gitignored)
├── netcdf_export.ipynb         # reference notebook (data pipeline)
├── config.py                   # shared settings
├── Dockerfile
├── docker-compose.yml
├── crontab.example
├── TODO.md                     # implementation plan
└── requirements-prod.txt
```

## Authors

- José Vidal Cardona Rosas — ladivcr@comunidad.unam.mx (original spyder)
- Gilberto Domínguez — gilberto.carlos@comunidad.unam.mx (unified catalog, API)

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).

## Reference coordinates

The solar altitude and azimuth are computed from the geometric center
of Mexico (derived from triangulation and centroid operations on the
continental outline):

```
LAT = 24.05754867
LON = -104.0226393
```
