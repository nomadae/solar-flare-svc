# Implementation Plan — Unified Solar Flare Database Service

## Target architecture

```
                     NOAA
                      │
          ┌───────────┼───────────┐
          │ JSON (latest)         │ netCDF (GOES-19, periodic refresh)
          ▼                       ▼
     realtime.py            netcdf_sync.py
   (runs every 300s)     (cron: daily @ 02:00)
          │                       │
          └───────────┬───────────┘
                      ▼
              MySQL (SolarFlare)
                      │
                      ▼
               api/app.py
            (FastAPI REST)
                      │
                      ▼
           External consumers
```

---

## Phase 1 — Unified database schema

**Goal:** One table that holds the historical catalog AND receives live updates from the spyder.

- [ ] Design `flares` table with all meaningful columns from both systems
- [ ] Write `db/schema.sql` (CREATE TABLE + indexes)
- [ ] Write `db/migrate.sql` (INSERT the single existing `observations` row into new table, then DROP `observations`)

### Proposed schema

| Column | Type | Source |
|---|---|---|
| `id` | BIGINT UNSIGNED AUTO_INCREMENT PK | — |
| `event_start` | DATETIME NOT NULL | both |
| `event_peak` | DATETIME NOT NULL | both |
| `event_end` | DATETIME NOT NULL | both |
| `magnitude` | VARCHAR(8) | Gilberto (e.g. "C5.7") |
| `xrsb_flux_peak` | DOUBLE | Gilberto (W/m², 0.1–0.8 nm band) |
| `integrated_flux` | DOUBLE | Gilberto (J/m² at event end) |
| `instrument` | VARCHAR(10) | Vidal ("G18") or netCDF ("GOES-16") |
| `altitude` | DOUBLE | both (solar altitude from Mexico center) |
| `azimuth` | DOUBLE | both (solar azimuth from Mexico center) |
| `source` | ENUM('SWPC','GOES-16','GOES-19','JSON') | new — data origin |
| `metadata` | JSON | new — extensible bag for future per-flare attributes |
| `created_at` | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | both |

Index: UNIQUE on `(event_start, instrument)` for dedup.

### Extensibility strategy

**Per-flare attributes** (e.g. active region magnetic class, GOES XRS-A channel flux if NOAA adds it): store in the `metadata` JSON column. No schema migration needed. The API can filter on JSON paths (`metadata->>"$.sunspot_group"`) with a generated column + index if a field becomes heavily queried.

**External time-series indices** (sunspot number, F10.7 solar radio flux, geomagnetic Kp/Ap): these are *daily* values, not per-flare. They belong in a separate table to avoid denormalization:

| Column | Type |
|---|---|
| `date` | DATE NOT NULL PK |
| `sunspot_number` | INT | (SILSO/ISN v2) |
| `f10_7_flux` | DOUBLE | (solar radio flux at 10.7 cm, sfu) |
| `kp_index` | DOUBLE | (planetary geomagnetic) |
| `ap_index` | INT |
| `source` | VARCHAR(50) | (SILSO, NOAA SWPC, etc.) |
| `created_at` | TIMESTAMP DEFAULT CURRENT_TIMESTAMP |

Join on `DATE(flares.event_start) = solar_indices.date`.
- [ ] Write `solar_indices` table into `db/schema.sql`

---

## Phase 2 — Historical data import

**Goal:** Load all 93K flares from the notebook pipeline into the new `flares` table.

- [ ] Extract the core logic from `netcdf_export.ipynb` into `db/historical_load.py`
  - Download GOES-16 + GOES-19 netCDFs (skip if already in `data/` and recent)
  - Load + correct SWPC catalog (/ 0.7)
  - `combine_flares()` for both GOES satellites
  - Merge, trim overlaps, insert into MySQL with dedup
- [ ] Run once to populate the database
- [ ] Verify: query total rows, date range, check no duplicates

---

## Phase 3 — Refactor the spyder (real-time updater)

**Goal:** Replace the current `spyder.py` + `runScript.sh` with a cleaner version that feeds the new `flares` table.

- [ ] Create `spyder/realtime.py`:
  - Parse `xray-flares-latest.json` (keep Vidal's BeautifulSoup logic)
  - Map JSON fields to the unified schema
  - Dedup by `(event_start, instrument)` → INSERT if new
  - Log activity
- [ ] Create `spyder/netcdf_sync.py`:
  - Download latest GOES-19 netCDF from NOAA (scrape directory)
  - Load with `nc_to_pandas()`, run `combine_flares()`
  - INSERT new flares only (WHERE NOT EXISTS by event_start)
  - This catches any flares missed by the JSON poller
- [ ] Systemd timer or crontab:
  - `realtime.py` every 300s (5 min)
  - `netcdf_sync.py` daily at 02:00

---

## Phase 4 — REST API

**Goal:** Expose the database via HTTP for external consumers.

- [ ] Create `api/app.py` using FastAPI:
  - `GET /flares` — paginated, filter by date range, class, source, instrument
  - `GET /flares/latest` — most recent flare
  - `GET /flares/{id}` — single flare by ID
  - `GET /stats` — counts by class, year, instrument
  - `GET /indices` — daily solar indices, filterable by date range
  - `GET /flares?with_indices=true` — join flares with their day's solar indices
- [ ] Add CORS middleware
- [ ] Add OpenAPI auto-docs at `/docs`
- [ ] Add `api/indices_importer.py` — script to fetch and import sunspot/F10.7/Kp data from public sources (SILSO, NOAA SWPC)

---

## Phase 5 — Containerization

**Goal:** Reproducible deployment with Docker.

- [ ] Create `Dockerfile` for the Python app
- [ ] Create `docker-compose.yml`:
  - MySQL 8 service (with persistent volume)
  - Python app service (API + spyder loop)
  - Optional: Adminer for DB inspection
- [ ] Environment variables via `.env` (already exists)

---

## Phase 6 — Cleanup & docs

- [ ] Update `README.md` with architecture diagram and usage
- [ ] Remove `main.py` (PyCharm template)
- [ ] Archive or remove old `spyder.py` after migration confirmed
- [ ] Add `data/` to `.gitignore` (netCDF files are large downloads)
