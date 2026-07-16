-- Migration: Create jay_column_embeddings table for JAI semantic column search
--
-- This table stores per-column metadata profiles and their vector embeddings
-- for the JAI chatbot's intelligent column-matching pipeline. Each row
-- represents a single database column with its profiled statistics (data type,
-- sample values, null rate, distinct count, categorical flag) and a pgvector
-- embedding generated from a composite text representation of that metadata.
--
-- At query time, the user's natural-language question is embedded and compared
-- against these column embeddings via cosine similarity (HNSW index) to find
-- the most relevant columns, replacing the current static registry approach.
--
-- The profile_hash column enables incremental re-profiling: only columns whose
-- underlying data has changed need to be re-embedded.
--
-- Requires: PostgreSQL with pgvector extension installed.
-- Run on: wpo schema (myopsprod)

-- 1. Enable pgvector extension (idempotent)
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Create the column embeddings table
CREATE TABLE IF NOT EXISTS wpo.jay_column_embeddings (
    id                SERIAL PRIMARY KEY,
    schema_name       VARCHAR(100)  NOT NULL,
    table_name        VARCHAR(200)  NOT NULL,
    column_name       VARCHAR(200)  NOT NULL,
    data_type         VARCHAR(100),
    description       TEXT,
    sample_values     JSONB,            -- array of representative sample values
    distinct_count    INTEGER,
    null_rate         REAL,
    is_categorical    BOOLEAN       DEFAULT FALSE,
    business_aliases  JSONB,            -- array of alias strings (e.g. ["carrier", "company"])
    profile_hash      VARCHAR(64),      -- SHA-256 hash of profile payload for change detection
    embedding_text    TEXT          NOT NULL, -- the composite text string that was embedded
    embedding         vector(1536),     -- pgvector column for text-embedding-3-small output
    profiled_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    embedded_at       TIMESTAMP WITH TIME ZONE,

    -- Unique constraint: one row per schema.table.column
    CONSTRAINT uq_jay_col_emb_schema_table_col
        UNIQUE (schema_name, table_name, column_name)
);

-- 3. HNSW index for fast approximate nearest-neighbor cosine search
CREATE INDEX IF NOT EXISTS ix_jay_col_emb_hnsw
    ON wpo.jay_column_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- 4. B-tree index on profile_hash for incremental change-detection lookups
CREATE INDEX IF NOT EXISTS ix_jay_col_emb_profile_hash
    ON wpo.jay_column_embeddings (profile_hash);

-- 5. B-tree index on table_name for table-level queries
CREATE INDEX IF NOT EXISTS ix_jay_col_emb_table_name
    ON wpo.jay_column_embeddings (table_name);

-- 6. Descriptive comments
COMMENT ON TABLE wpo.jay_column_embeddings IS
    'Per-column metadata profiles and vector embeddings for JAI semantic column search';

COMMENT ON COLUMN wpo.jay_column_embeddings.embedding IS
    'text-embedding-3-small (1536-dim) vector of the embedding_text composite representation';

COMMENT ON COLUMN wpo.jay_column_embeddings.embedding_text IS
    'Composite text built from column name, description, aliases, sample values, and statistics -- the input to the embedding model';

COMMENT ON COLUMN wpo.jay_column_embeddings.profile_hash IS
    'SHA-256 hash of the column profile payload; used to detect when re-profiling and re-embedding is needed';

COMMENT ON COLUMN wpo.jay_column_embeddings.sample_values IS
    'JSONB array of representative sample values drawn from the column';

COMMENT ON COLUMN wpo.jay_column_embeddings.business_aliases IS
    'JSONB array of business-domain alias strings (synonyms, abbreviations) for this column';

COMMENT ON COLUMN wpo.jay_column_embeddings.is_categorical IS
    'TRUE when the column has a small, finite set of allowed values (used to include valid_values in embedding text)';
