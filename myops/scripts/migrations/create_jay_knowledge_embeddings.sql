-- Migration: Create jay_knowledge_embeddings table for JAI semantic knowledge search
--
-- This table stores vector embeddings of business knowledge entries (synonyms,
-- glossary definitions, query examples, and learned patterns) for the JAI
-- chatbot's RAG pipeline. At query time, the user's natural-language question
-- is embedded and compared against these knowledge embeddings via cosine
-- similarity (HNSW index) to retrieve the most relevant context, replacing
-- the current full-injection approach with targeted retrieval.
--
-- Categories:
--   'synonym'  - Business term -> database column mappings
--   'glossary' - Business concept -> SQL pattern definitions
--   'example'  - Curated SQL query examples
--   'learned'  - Feedback-derived patterns (weighted at 0.85)
--
-- Requires: PostgreSQL with pgvector extension installed.
-- Run on: wpo schema (myopsprod)

-- 1. Enable pgvector extension (idempotent)
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Create the knowledge embeddings table
CREATE TABLE IF NOT EXISTS wpo.jay_knowledge_embeddings (
    id              SERIAL PRIMARY KEY,
    category        VARCHAR(50)   NOT NULL,           -- 'synonym', 'glossary', 'example', 'learned'
    text            TEXT          NOT NULL,           -- the text content that was embedded
    metadata        JSONB,                            -- flexible metadata (module, sql_hint, domain_tags, etc.)
    embedding       vector(1536),                     -- pgvector column for text-embedding-3-small output
    score_weight    REAL          DEFAULT 1.0,        -- quality weighting (learned examples get 0.85)
    is_active       BOOLEAN       DEFAULT TRUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 3. HNSW index on embedding for fast approximate nearest-neighbor cosine search
CREATE INDEX IF NOT EXISTS ix_jay_knowledge_emb_hnsw
    ON wpo.jay_knowledge_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- 4. B-tree index on category for filtered queries
CREATE INDEX IF NOT EXISTS ix_jay_knowledge_emb_category
    ON wpo.jay_knowledge_embeddings (category);

-- 5. B-tree index on is_active for active-only queries
CREATE INDEX IF NOT EXISTS ix_jay_knowledge_emb_is_active
    ON wpo.jay_knowledge_embeddings (is_active);

-- 6. Descriptive comments
COMMENT ON TABLE wpo.jay_knowledge_embeddings IS
    'Vector embeddings of business knowledge entries (synonyms, glossary, examples, learned patterns) for JAI RAG retrieval';

COMMENT ON COLUMN wpo.jay_knowledge_embeddings.category IS
    'Knowledge entry type: synonym, glossary, example, or learned';

COMMENT ON COLUMN wpo.jay_knowledge_embeddings.text IS
    'The human-readable text content that was embedded into the vector';

COMMENT ON COLUMN wpo.jay_knowledge_embeddings.metadata IS
    'Flexible JSONB metadata: module, sql_hint, domain_tags, term, db_column, etc.';

COMMENT ON COLUMN wpo.jay_knowledge_embeddings.embedding IS
    'text-embedding-3-small (1536-dim) vector of the text column content';

COMMENT ON COLUMN wpo.jay_knowledge_embeddings.score_weight IS
    'Quality weighting multiplied into similarity score during retrieval (default 1.0, learned patterns get 0.85)';

COMMENT ON COLUMN wpo.jay_knowledge_embeddings.is_active IS
    'Soft-delete flag; inactive rows are excluded from retrieval';
