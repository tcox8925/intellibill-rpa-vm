-- Create carrier lookup table for fast entity resolution
-- Replaces slow queries against vw_com_items_ai (311s -> <50ms)
-- Run: psql -f scripts/migrations/create_carrier_lookup.sql

CREATE TABLE IF NOT EXISTS wpo.jay_carrier_lookup (
    carrier_name VARCHAR(500) PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Populate from the commission items view (distinct carrier names)
INSERT INTO wpo.jay_carrier_lookup (carrier_name)
SELECT DISTINCT carrier_name
FROM wpo.vw_com_items_ai
WHERE carrier_name IS NOT NULL AND TRIM(carrier_name) <> ''
ON CONFLICT (carrier_name) DO NOTHING;

-- To refresh periodically (e.g., weekly via pg_cron):
-- SELECT cron.schedule('refresh-carrier-lookup', '0 2 * * 0',
--   $$INSERT INTO wpo.jay_carrier_lookup (carrier_name)
--     SELECT DISTINCT carrier_name FROM wpo.vw_com_items_ai
--     WHERE carrier_name IS NOT NULL AND TRIM(carrier_name) <> ''
--     ON CONFLICT (carrier_name) DO NOTHING$$);
