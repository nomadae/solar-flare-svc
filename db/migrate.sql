-- Migrate existing data from the old `observations` table into the
-- new unified `flares` table.  Run this AFTER schema.sql.
--
-- The old spyder.py ingested XRS-Long flux (0.05–0.4 nm), not the
-- XRS-B channel (0.1–0.8 nm) used throughout the unified catalog.
-- That value is preserved in the `metadata` JSON column.

USE SolarFlare;

-- Insert existing observation into the new schema
INSERT INTO flares (
    event_start, event_peak, event_end,
    magnitude,
    xrsb_flux_peak,               -- not available from JSON source
    integrated_flux,              -- not available from JSON source
    instrument,
    altitude, azimuth,
    source,
    metadata
)
SELECT
    event_start,
    max_peak_event,
    event_finish,
    CONCAT(classification, FORMAT(sub_classification, 1)),
    NULL,                         -- xrsb_flux_peak not in old schema
    NULL,                         -- integrated_flux not in old schema
    observation_instrument,
    altitude,
    azimuth,
    'JSON',
    JSON_OBJECT(
        'xrslong_flux',     max_energy,
        'total_energy',     total_energy,
        'active_region',    active_region,
        'latitude',         latitude,
        'longitude',        longitude
    )
FROM observations
WHERE NOT EXISTS (
    SELECT 1 FROM flares
    WHERE flares.event_start = observations.event_start
      AND flares.instrument  = observations.observation_instrument
);

-- Verify migration
SELECT COUNT(*) AS flares_count FROM flares;

-- Drop old table once the migration is confirmed
-- DROP TABLE observations;
