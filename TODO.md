# Implementation Plan — Unified Solar Flare Database Service

> **Status: All phases complete** (2026-05-30)

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

## Phase 1 — Unified database schema [x]

- [x] Design `flares` table with all meaningful columns from both systems
- [x] Write `db/schema.sql` (CREATE TABLE + indexes)
- [x] Write `db/migrate.sql` (INSERT the single existing `observations` row into new table, then DROP `observations`)

### Schema delivered

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

Unique key: `(event_start, instrument)` for dedup.

- [x] Write `solar_indices` table into `db/schema.sql`

---

## Phase 2 — Historical data import [x]

- [x] Extract the core logic from `netcdf_export.ipynb` into `db/historical_load.py`
- [x] Run once to populate the database — **92,670 flares loaded**
- [x] Verify: query total rows, date range, check no duplicates

---

## Phase 3 — Refactor the spyder (real-time updater) [x]

- [x] Create `spyder/realtime.py` — JSON poller, handles malformed NOAA JSON + in-progress flares
- [x] Create `spyder/netcdf_sync.py` — daily GOES-19 netCDF batch sync, skips if unchanged
- [x] Schedule: `crontab.example` — realtime every 5 min, netcdf sync daily at 02:00

---

## Phase 4 — REST API [x]

- [x] Create `api/app.py` using FastAPI with all endpoints
- [x] Add CORS middleware
- [x] Add OpenAPI auto-docs at `/docs`
- [x] Add `api/indices_importer.py` — SILSO sunspot (72K rows), NOAA F10.7 (30d), NOAA Kp/Ap

---

## Phase 5 — Containerization [x]

- [x] Create `Dockerfile` for the Python app
- [x] Create `docker-compose.yml` (MySQL 8 + API + spyder + optional Adminer)
- [x] `.env.docker` for Docker networking (HOST=db)

---

## Phase 6 — Cleanup & docs [x]

- [x] Update `README.md` with architecture diagram and usage
- [x] Remove `main.py` (PyCharm template)
- [x] Archive old `spyder.py`, `runScript.sh`, `createDB.txt` → `legacy/`
- [x] Add `data/` to `.gitignore`
- [x] Untrack `logs.log`
- [x] Remove `.ipynb_checkpoints`
- [x] Add `.env.example` template
