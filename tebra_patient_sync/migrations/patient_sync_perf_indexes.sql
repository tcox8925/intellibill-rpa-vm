-- Supports the batched lookups in tebra/load_patient_header.py and
-- tebra/load_patient_coverages.py, which match patient_header rows by
-- lower(trim(source)) / trim(source_id) rather than the raw columns
-- (source is free text and may carry stray casing/padding from other
-- loaders/legacy rows). A plain index on source/source_id can't be used by
-- a query wrapped in lower()/trim(), so on a populated table (unlike an
-- empty local dev DB) every such query pays a full sequential scan -
-- expression indexes let Postgres use an index scan instead.

CREATE INDEX IF NOT EXISTS idx_patient_header_source_lower_trim
    ON "EDI_Tebra".patient_header (lower(trim("source")));

CREATE INDEX IF NOT EXISTS idx_patient_header_source_id_trim
    ON "EDI_Tebra".patient_header (trim(source_id));

-- Supports build_active_coverage_map's single batched query
-- (patient_header_id IN (...) AND active = true) instead of one
-- get_active_coverage query per patient per coverage type.
CREATE INDEX IF NOT EXISTS idx_patient_coverages_header_active
    ON "EDI_Tebra".patient_coverages (patient_header_id, cov_type)
    WHERE active = true;
