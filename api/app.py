"""
app.py — REST API for the Unified Solar Flare Database.

FastAPI application exposing flare events, statistics, and solar
indices.  Auto-generated interactive docs at /docs.

Usage:
  uvicorn api.app:app --reload --host 0.0.0.0 --port 8000
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mysql.connector
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

from config import DB_CONFIG

app = FastAPI(
    title="Solar Flare Database API",
    description="Unified GOES XRS-B flare catalog: SWPC (1975–2017) + GOES-16 (2017–2024) + GOES-19 (2024–present)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------
def get_db():
    return mysql.connector.connect(**DB_CONFIG)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class FlareOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_start: str
    event_peak: str
    event_end: str
    magnitude: str | None
    xrsb_flux_peak: float | None
    integrated_flux: float | None
    instrument: str
    altitude: float | None
    azimuth: float | None
    source: str
    created_at: str | None


class FlarePage(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[FlareOut]


class StatsOut(BaseModel):
    total_flares: int
    by_class: dict
    by_year: dict
    by_instrument: dict
    date_range: dict


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/")
def root():
    return {"service": "Solar Flare Database API", "docs": "/docs"}


# -- Flares ----------------------------------------------------------------
@app.get("/flares", response_model=FlarePage)
def list_flares(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    date_from: str | None = Query(None, description="ISO datetime, e.g. 2024-01-01T00:00:00"),
    date_to: str | None = Query(None),
    class_: str | None = Query(None, alias="class", description="C, M, or X"),
    source: str | None = Query(None, description="SWPC, GOES-16, GOES-19, JSON"),
    instrument: str | None = Query(None, description="e.g. G18, GOES-16"),
    with_indices: bool = Query(False, description="Join solar indices on date"),
):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    where = []
    params = []

    if date_from:
        where.append("f.event_start >= %s")
        params.append(date_from)
    if date_to:
        where.append("f.event_start <= %s")
        params.append(date_to)
    if class_:
        where.append("f.magnitude LIKE %s")
        params.append(f"{class_}%")
    if source:
        where.append("f.source = %s")
        params.append(source)
    if instrument:
        where.append("f.instrument = %s")
        params.append(instrument)

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    # Count
    cursor.execute(f"SELECT COUNT(*) AS cnt FROM flares f {where_clause}", params)
    total = cursor.fetchone()["cnt"]

    # Paginated fetch
    offset = (page - 1) * page_size
    if with_indices:
        select_cols = (
            "f.*, i.sunspot_number, i.f10_7_flux, i.kp_index, i.ap_index"
        )
        join = "LEFT JOIN solar_indices i ON DATE(f.event_start) = i.date"
    else:
        select_cols = "f.*"
        join = ""

    sql = (
        f"SELECT {select_cols} FROM flares f {join} "
        f"{where_clause} "
        f"ORDER BY f.event_start DESC "
        f"LIMIT %s OFFSET %s"
    )
    cursor.execute(sql, params + [page_size, offset])
    rows = cursor.fetchall()

    cursor.close()
    db.close()

    results = []
    for r in rows:
        d = {}
        for k in (
            "id", "event_start", "event_peak", "event_end",
            "magnitude", "xrsb_flux_peak", "integrated_flux",
            "instrument", "altitude", "azimuth", "source", "created_at",
        ):
            val = r.get(k)
            d[k] = val.isoformat() if hasattr(val, "isoformat") else val
        # Attach indices as top-level fields if requested
        if with_indices:
            d["sunspot_number"] = r.get("sunspot_number")
            d["f10_7_flux"] = r.get("f10_7_flux")
            d["kp_index"] = r.get("kp_index")
            d["ap_index"] = r.get("ap_index")
        results.append(d)

    return FlarePage(
        total=total,
        page=page,
        page_size=page_size,
        results=results,
    )


@app.get("/flares/latest")
def latest_flare():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM flares ORDER BY event_start DESC LIMIT 1")
    row = cursor.fetchone()
    cursor.close()
    db.close()
    if not row:
        raise HTTPException(404, "No flares in database")
    return row


@app.get("/flares/{flare_id}")
def get_flare(flare_id: int):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "SELECT f.*, i.sunspot_number, i.f10_7_flux, i.kp_index, i.ap_index "
        "FROM flares f LEFT JOIN solar_indices i ON DATE(f.event_start) = i.date "
        "WHERE f.id = %s",
        (flare_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    db.close()
    if not row:
        raise HTTPException(404, f"Flare {flare_id} not found")
    return row


# -- Statistics ------------------------------------------------------------
@app.get("/stats")
def get_stats():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Total
    cursor.execute("SELECT COUNT(*) AS cnt FROM flares")
    total = cursor.fetchone()["cnt"]

    # By class (first letter of magnitude)
    cursor.execute(
        "SELECT LEFT(magnitude, 1) AS class, COUNT(*) AS cnt "
        "FROM flares WHERE magnitude IS NOT NULL "
        "GROUP BY class ORDER BY class"
    )
    by_class = {r["class"]: r["cnt"] for r in cursor.fetchall()}

    # By year
    cursor.execute(
        "SELECT YEAR(event_start) AS yr, COUNT(*) AS cnt "
        "FROM flares GROUP BY yr ORDER BY yr"
    )
    by_year = {str(r["yr"]): r["cnt"] for r in cursor.fetchall()}

    # By instrument
    cursor.execute(
        "SELECT instrument, COUNT(*) AS cnt "
        "FROM flares GROUP BY instrument ORDER BY cnt DESC"
    )
    by_instrument = {r["instrument"]: r["cnt"] for r in cursor.fetchall()}

    # Date range
    cursor.execute("SELECT MIN(event_start) AS mn, MAX(event_start) AS mx FROM flares")
    rng = cursor.fetchone()

    cursor.close()
    db.close()

    return StatsOut(
        total_flares=total,
        by_class=by_class,
        by_year=by_year,
        by_instrument=by_instrument,
        date_range={"from": rng["mn"].isoformat(), "to": rng["mx"].isoformat()},
    )


# -- Solar indices ---------------------------------------------------------
@app.get("/indices")
def list_indices(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    where = []
    params = []
    if date_from:
        where.append("date >= %s")
        params.append(date_from)
    if date_to:
        where.append("date <= %s")
        params.append(date_to)

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT * FROM solar_indices {where_clause} ORDER BY date DESC LIMIT 1000"
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return rows


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.app:app", host="0.0.0.0", port=8000, reload=True)
