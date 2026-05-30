-- Unified Solar Flare Database Schema
-- Replaces the original `observations` table with a comprehensive
-- schema that supports historical data, real-time ingestion, and
-- extensible per-flare metadata.

CREATE DATABASE IF NOT EXISTS SolarFlare;
USE SolarFlare;

-- -------------------------------------------------------------------
-- Core table: individual flare events
-- -------------------------------------------------------------------
CREATE TABLE flares (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    event_start     DATETIME NOT NULL,
    event_peak      DATETIME NOT NULL,
    event_end       DATETIME NOT NULL,
    magnitude       VARCHAR(8),            -- e.g. "C5.7", "X12.9"
    xrsb_flux_peak  DOUBLE,                -- W/m², 0.1-0.8 nm band (XRS-B)
    integrated_flux DOUBLE,                -- J/m² at event end
    instrument      VARCHAR(10),           -- e.g. "G16", "GOES-16", "G19"
    altitude        DOUBLE,                -- solar altitude from Mexico center
    azimuth         DOUBLE,                -- solar azimuth from Mexico center
    source          ENUM('SWPC','GOES-16','GOES-19','JSON'),
    metadata        JSON,                  -- extensible bag for future attributes
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uq_event_instrument (event_start, instrument)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------------------------------
-- Lookup table: daily solar indices (sunspot number, radio flux, etc.)
-- Join on DATE(flares.event_start) = solar_indices.date
-- -------------------------------------------------------------------
CREATE TABLE solar_indices (
    date            DATE NOT NULL PRIMARY KEY,
    sunspot_number  INT,                   -- SILSO International Sunspot Number v2
    f10_7_flux      DOUBLE,                -- Solar radio flux at 10.7 cm (sfu)
    kp_index        DOUBLE,                -- Planetary Kp geomagnetic index
    ap_index        INT,                   -- Planetary Ap geomagnetic index
    source          VARCHAR(50),           -- e.g. "SILSO", "NOAA_SWPC"
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
