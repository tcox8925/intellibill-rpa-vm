-- Upgrade HNSW indexes for better recall quality.
-- ef_construction: 64 -> 200 (recommended minimum for production)
-- m: 16 -> 24 (more connections per node = better recall)

-- Column embeddings
DROP INDEX IF EXISTS wpo.ix_jay_col_emb_hnsw;
CREATE INDEX ix_jay_col_emb_hnsw
    ON wpo.jay_column_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 24, ef_construction = 200);

-- Knowledge embeddings
DROP INDEX IF EXISTS wpo.ix_jay_knowledge_emb_hnsw;
CREATE INDEX ix_jay_knowledge_emb_hnsw
    ON wpo.jay_knowledge_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 24, ef_construction = 200);
