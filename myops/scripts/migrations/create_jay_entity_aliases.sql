-- JAI Entity Alias Cache: Self-learning alias resolution
-- Stores mappings from abbreviations/aliases to canonical DB values
-- Auto-populated by LLM expansion, manually editable by admins

CREATE TABLE IF NOT EXISTS wpo.jay_entity_aliases (
    id              SERIAL PRIMARY KEY,
    entity_type     VARCHAR(50)  NOT NULL,   -- 'carrier_name', 'agent', 'pch_provider', etc.
    alias_value     VARCHAR(255) NOT NULL,   -- normalized input: 'bcbs', 'uhc'
    canonical_value VARCHAR(255) NOT NULL,   -- DB-matched value: 'Blue Cross Blue Shield'
    confidence      FLOAT        NOT NULL DEFAULT 0.5,
    usage_count     INTEGER      NOT NULL DEFAULT 1,
    hit_count       INTEGER      NOT NULL DEFAULT 0,
    source          VARCHAR(20)  NOT NULL DEFAULT 'llm',  -- 'llm', 'admin', 'seed'
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,

    CONSTRAINT uq_entity_alias UNIQUE (entity_type, alias_value)
);

CREATE INDEX IF NOT EXISTS idx_alias_lookup
    ON wpo.jay_entity_aliases (entity_type, alias_value)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_alias_canonical
    ON wpo.jay_entity_aliases (entity_type, canonical_value);
