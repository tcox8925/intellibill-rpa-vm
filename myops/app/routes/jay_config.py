"""
JAI Config Admin API — CRUD for business knowledge tables.

All authenticated users can access these endpoints.
Every write endpoint auto-invalidates the relevant cache key.
"""

from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.middleware.validator import get_current_user
from app.models.jay.JayConfig import (
    JaySynonym,
    JayGlossaryEntry,
    JayFilterValue,
    JayQueryExample,
)
from app.schemas.jay_config import (
    SynonymCreate, SynonymUpdate, SynonymPatch, SynonymResponse,
    GlossaryCreate, GlossaryUpdate, GlossaryPatch, GlossaryResponse,
    FilterValueCreate, FilterValueUpdate, FilterValuePatch, FilterValueResponse,
    QueryExampleCreate, QueryExampleUpdate, QueryExamplePatch, QueryExampleResponse,
    BulkSynonymRequest, BulkGlossaryRequest, BulkFilterValueRequest, BulkQueryExampleRequest,
    CacheInvalidateRequest,
)
from app.utils.jay.knowledge_cache import invalidate_cache

router = APIRouter(prefix="/jay/config", tags=["JAI Config"])


# ═══════════════════════════════════════════════════════════════════
# SYNONYMS
# ═══════════════════════════════════════════════════════════════════

@router.get("/synonyms", response_model=List[SynonymResponse])
async def list_synonyms(
    request: Request,
    module: Optional[str] = None,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    query = db.query(JaySynonym)
    # No module filter on synonyms (they don't have a module column),
    # but kept for API consistency; just return all.
    return query.order_by(JaySynonym.term).all()


@router.post("/synonyms", response_model=SynonymResponse)
async def create_synonym(
    request: Request,
    body: SynonymCreate,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = JaySynonym(
        term=body.term,
        db_column=body.db_column,
        notes=body.notes,
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    invalidate_cache("synonyms")
    return row


@router.put("/synonyms/{item_id}", response_model=SynonymResponse)
async def update_synonym(
    item_id: int,
    request: Request,
    body: SynonymUpdate,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = db.query(JaySynonym).filter(JaySynonym.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Synonym not found")
    row.term = body.term
    row.db_column = body.db_column
    row.notes = body.notes
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    invalidate_cache("synonyms")
    return row


@router.patch("/synonyms/{item_id}", response_model=SynonymResponse)
async def patch_synonym(
    item_id: int,
    request: Request,
    body: SynonymPatch,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = db.query(JaySynonym).filter(JaySynonym.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Synonym not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    invalidate_cache("synonyms")
    return row


@router.delete("/synonyms/{item_id}")
async def delete_synonym(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = db.query(JaySynonym).filter(JaySynonym.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Synonym not found")
    row.is_active = False
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    invalidate_cache("synonyms")
    return {"status": "deactivated", "id": item_id}


@router.post("/synonyms/bulk", response_model=List[SynonymResponse])
async def bulk_upsert_synonyms(
    request: Request,
    body: BulkSynonymRequest,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    results = []
    for item in body.items:
        existing = db.query(JaySynonym).filter(JaySynonym.term == item.term).first()
        if existing:
            existing.db_column = item.db_column
            existing.notes = item.notes
            existing.is_active = True
            existing.updated_at = datetime.now(timezone.utc)
            results.append(existing)
        else:
            row = JaySynonym(
                term=item.term,
                db_column=item.db_column,
                notes=item.notes,
                is_active=True,
            )
            db.add(row)
            results.append(row)
    db.commit()
    for r in results:
        db.refresh(r)
    invalidate_cache("synonyms")
    return results


# ═══════════════════════════════════════════════════════════════════
# GLOSSARY
# ═══════════════════════════════════════════════════════════════════

@router.get("/glossary", response_model=List[GlossaryResponse])
async def list_glossary(
    request: Request,
    module: Optional[str] = None,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    query = db.query(JayGlossaryEntry)
    if module:
        query = query.filter(JayGlossaryEntry.module == module)
    return query.order_by(JayGlossaryEntry.term).all()


@router.post("/glossary", response_model=GlossaryResponse)
async def create_glossary(
    request: Request,
    body: GlossaryCreate,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = JayGlossaryEntry(
        term=body.term,
        description=body.description,
        sql_hint=body.sql_hint,
        module=body.module,
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    invalidate_cache("glossary")
    return row


@router.put("/glossary/{item_id}", response_model=GlossaryResponse)
async def update_glossary(
    item_id: int,
    request: Request,
    body: GlossaryUpdate,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = db.query(JayGlossaryEntry).filter(JayGlossaryEntry.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Glossary entry not found")
    row.term = body.term
    row.description = body.description
    row.sql_hint = body.sql_hint
    row.module = body.module
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    invalidate_cache("glossary")
    return row


@router.patch("/glossary/{item_id}", response_model=GlossaryResponse)
async def patch_glossary(
    item_id: int,
    request: Request,
    body: GlossaryPatch,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = db.query(JayGlossaryEntry).filter(JayGlossaryEntry.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Glossary entry not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    invalidate_cache("glossary")
    return row


@router.delete("/glossary/{item_id}")
async def delete_glossary(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = db.query(JayGlossaryEntry).filter(JayGlossaryEntry.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Glossary entry not found")
    row.is_active = False
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    invalidate_cache("glossary")
    return {"status": "deactivated", "id": item_id}


@router.post("/glossary/bulk", response_model=List[GlossaryResponse])
async def bulk_upsert_glossary(
    request: Request,
    body: BulkGlossaryRequest,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    results = []
    for item in body.items:
        existing = db.query(JayGlossaryEntry).filter(
            JayGlossaryEntry.term == item.term
        ).first()
        if existing:
            existing.description = item.description
            existing.sql_hint = item.sql_hint
            existing.module = item.module
            existing.is_active = True
            existing.updated_at = datetime.now(timezone.utc)
            results.append(existing)
        else:
            row = JayGlossaryEntry(
                term=item.term,
                description=item.description,
                sql_hint=item.sql_hint,
                module=item.module,
                is_active=True,
            )
            db.add(row)
            results.append(row)
    db.commit()
    for r in results:
        db.refresh(r)
    invalidate_cache("glossary")
    return results


# ═══════════════════════════════════════════════════════════════════
# FILTER VALUES
# ═══════════════════════════════════════════════════════════════════

@router.get("/filter-values", response_model=List[FilterValueResponse])
async def list_filter_values(
    request: Request,
    module: Optional[str] = None,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    query = db.query(JayFilterValue)
    if module:
        query = query.filter(JayFilterValue.module == module)
    return query.order_by(
        JayFilterValue.module,
        JayFilterValue.filter_name,
        JayFilterValue.sort_order,
    ).all()


@router.post("/filter-values", response_model=FilterValueResponse)
async def create_filter_value(
    request: Request,
    body: FilterValueCreate,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = JayFilterValue(
        module=body.module,
        filter_name=body.filter_name,
        column_name=body.column_name,
        filter_type=body.filter_type,
        valid_value=body.valid_value,
        sort_order=body.sort_order,
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    invalidate_cache("filter_values")
    return row


@router.put("/filter-values/{item_id}", response_model=FilterValueResponse)
async def update_filter_value(
    item_id: int,
    request: Request,
    body: FilterValueUpdate,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = db.query(JayFilterValue).filter(JayFilterValue.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Filter value not found")
    row.module = body.module
    row.filter_name = body.filter_name
    row.column_name = body.column_name
    row.filter_type = body.filter_type
    row.valid_value = body.valid_value
    row.sort_order = body.sort_order
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    invalidate_cache("filter_values")
    return row


@router.patch("/filter-values/{item_id}", response_model=FilterValueResponse)
async def patch_filter_value(
    item_id: int,
    request: Request,
    body: FilterValuePatch,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = db.query(JayFilterValue).filter(JayFilterValue.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Filter value not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    invalidate_cache("filter_values")
    return row


@router.delete("/filter-values/{item_id}")
async def delete_filter_value(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = db.query(JayFilterValue).filter(JayFilterValue.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Filter value not found")
    row.is_active = False
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    invalidate_cache("filter_values")
    return {"status": "deactivated", "id": item_id}


@router.post("/filter-values/bulk", response_model=List[FilterValueResponse])
async def bulk_upsert_filter_values(
    request: Request,
    body: BulkFilterValueRequest,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    results = []
    for item in body.items:
        existing = db.query(JayFilterValue).filter(
            JayFilterValue.module == item.module,
            JayFilterValue.filter_name == item.filter_name,
            JayFilterValue.valid_value == item.valid_value,
        ).first()
        if existing:
            existing.column_name = item.column_name
            existing.filter_type = item.filter_type
            existing.sort_order = item.sort_order
            existing.is_active = True
            existing.updated_at = datetime.now(timezone.utc)
            results.append(existing)
        else:
            row = JayFilterValue(
                module=item.module,
                filter_name=item.filter_name,
                column_name=item.column_name,
                filter_type=item.filter_type,
                valid_value=item.valid_value,
                sort_order=item.sort_order,
                is_active=True,
            )
            db.add(row)
            results.append(row)
    db.commit()
    for r in results:
        db.refresh(r)
    invalidate_cache("filter_values")
    return results


# ═══════════════════════════════════════════════════════════════════
# QUERY EXAMPLES
# ═══════════════════════════════════════════════════════════════════

@router.get("/query-examples", response_model=List[QueryExampleResponse])
async def list_query_examples(
    request: Request,
    example_type: Optional[str] = None,
    module_tag: Optional[str] = None,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    query = db.query(JayQueryExample)
    if example_type:
        query = query.filter(JayQueryExample.example_type == example_type)
    if module_tag:
        query = query.filter(JayQueryExample.module_tag == module_tag)
    return query.order_by(JayQueryExample.name).all()


@router.post("/query-examples", response_model=QueryExampleResponse)
async def create_query_example(
    request: Request,
    body: QueryExampleCreate,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = JayQueryExample(
        name=body.name,
        example_type=body.example_type,
        sql_text=body.sql_text,
        natural_query=body.natural_query,
        domain_tags=body.domain_tags,
        module_tag=body.module_tag,
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    invalidate_cache("query_examples")
    return row


@router.put("/query-examples/{item_id}", response_model=QueryExampleResponse)
async def update_query_example(
    item_id: int,
    request: Request,
    body: QueryExampleUpdate,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = db.query(JayQueryExample).filter(JayQueryExample.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Query example not found")
    row.name = body.name
    row.example_type = body.example_type
    row.sql_text = body.sql_text
    row.natural_query = body.natural_query
    row.domain_tags = body.domain_tags
    row.module_tag = body.module_tag
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    invalidate_cache("query_examples")
    return row


@router.patch("/query-examples/{item_id}", response_model=QueryExampleResponse)
async def patch_query_example(
    item_id: int,
    request: Request,
    body: QueryExamplePatch,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = db.query(JayQueryExample).filter(JayQueryExample.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Query example not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    invalidate_cache("query_examples")
    return row


@router.delete("/query-examples/{item_id}")
async def delete_query_example(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    row = db.query(JayQueryExample).filter(JayQueryExample.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Query example not found")
    row.is_active = False
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    invalidate_cache("query_examples")
    return {"status": "deactivated", "id": item_id}


@router.post("/query-examples/bulk", response_model=List[QueryExampleResponse])
async def bulk_upsert_query_examples(
    request: Request,
    body: BulkQueryExampleRequest,
    db: Session = Depends(get_db),
):
    get_current_user(request)
    results = []
    for item in body.items:
        existing = db.query(JayQueryExample).filter(
            JayQueryExample.name == item.name,
            JayQueryExample.example_type == item.example_type,
        ).first()
        if existing:
            existing.sql_text = item.sql_text
            existing.natural_query = item.natural_query
            existing.domain_tags = item.domain_tags
            existing.module_tag = item.module_tag
            existing.is_active = True
            existing.updated_at = datetime.now(timezone.utc)
            results.append(existing)
        else:
            row = JayQueryExample(
                name=item.name,
                example_type=item.example_type,
                sql_text=item.sql_text,
                natural_query=item.natural_query,
                domain_tags=item.domain_tags,
                module_tag=item.module_tag,
                is_active=True,
            )
            db.add(row)
            results.append(row)
    db.commit()
    for r in results:
        db.refresh(r)
    invalidate_cache("query_examples")
    return results


# ═══════════════════════════════════════════════════════════════════
# CACHE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

@router.post("/cache/invalidate")
async def invalidate_knowledge_cache(
    request: Request,
    body: CacheInvalidateRequest = CacheInvalidateRequest(),
):
    get_current_user(request)
    invalidate_cache(body.key)
    return {"status": "invalidated", "key": body.key or "all"}


# ═══════════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS  (prefix: /jay/admin)
# ═══════════════════════════════════════════════════════════════════

admin_router = APIRouter(prefix="/jay/admin", tags=["JAI Admin"])


@admin_router.post("/embeddings/rebuild")
async def rebuild_embeddings(
    request: Request,
    db: Session = Depends(get_db),
):
    """Force rebuild all column embeddings."""
    get_current_user(request)
    from app.utils.jay.column_embedding_sync import sync_column_embeddings

    stats = sync_column_embeddings(db, force=True)
    return {"status": "ok", "stats": stats}


@admin_router.get("/embeddings/stats")
async def get_embedding_stats(
    request: Request,
    db: Session = Depends(get_db),
):
    """Get column embedding index statistics."""
    get_current_user(request)
    result = db.execute(text("""
        SELECT
            COUNT(*) AS total_columns,
            COUNT(CASE WHEN embedding IS NOT NULL THEN 1 END) AS embedded_columns,
            COUNT(CASE WHEN is_categorical THEN 1 END) AS categorical_columns,
            COUNT(DISTINCT table_name) AS table_count,
            MIN(embedded_at) AS oldest_embedding,
            MAX(embedded_at) AS newest_embedding
        FROM wpo.jay_column_embeddings
    """))
    row = result.fetchone()

    # FK graph stats
    try:
        from app.utils.jay.fk_inference import discover_join_graph

        graph = discover_join_graph(db)
        fk_stats = {
            "total_edges": len(graph.edges),
            "explicit_fks": len([e for e in graph.edges if e.is_explicit]),
            "implicit_fks": len([e for e in graph.edges if not e.is_explicit]),
            "table_count": len(graph.tables),
        }
    except Exception:
        fk_stats = {"error": "Failed to load FK graph"}

    return {
        "columns": {
            "total": row[0],
            "embedded": row[1],
            "categorical": row[2],
            "tables": row[3],
            "oldest_embedding": str(row[4]) if row[4] else None,
            "newest_embedding": str(row[5]) if row[5] else None,
        },
        "fk_graph": fk_stats,
    }


@admin_router.get("/rag/stats")
async def get_rag_stats(
    request: Request,
    days: int = 7,
    db: Session = Depends(get_db),
):
    """Get RAG retrieval metrics from recent conversations."""
    get_current_user(request)
    result = db.execute(text("""
        SELECT
            COUNT(*) AS total_knowledge_entries,
            COUNT(CASE WHEN category = 'synonym' THEN 1 END) AS synonyms,
            COUNT(CASE WHEN category = 'glossary' THEN 1 END) AS glossary,
            COUNT(CASE WHEN category = 'example' THEN 1 END) AS examples,
            COUNT(CASE WHEN category = 'learned' THEN 1 END) AS learned,
            COUNT(CASE WHEN embedding IS NOT NULL THEN 1 END) AS embedded
        FROM wpo.jay_knowledge_embeddings
        WHERE is_active = true
    """))
    row = result.fetchone()

    return {
        "knowledge_entries": {
            "total": row[0],
            "synonyms": row[1],
            "glossary": row[2],
            "examples": row[3],
            "learned": row[4],
            "embedded": row[5],
        },
    }


# ═══════════════════════════════════════════════════════════════════
# LEARNING DATA ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@admin_router.get("/learning/stats")
async def get_learning_stats(
    request: Request,
    db: Session = Depends(get_db),
):
    """Overview of all learning systems."""
    get_current_user(request)
    try:
        # Entity aliases by type
        alias_rows = db.execute(text(
            "SELECT entity_type, COUNT(*) "
            "FROM wpo.jay_entity_aliases "
            "WHERE is_active = true "
            "GROUP BY entity_type"
        )).fetchall()
        alias_by_type = {r[0]: r[1] for r in alias_rows}
        alias_total = sum(alias_by_type.values())

        # Query cache stats
        cache_row = db.execute(text(
            "SELECT COUNT(*), "
            "AVG(CASE WHEN success_count > 0 "
            "    THEN success_count::float / NULLIF(execution_count, 0) END), "
            "SUM(execution_count) "
            "FROM wpo.jay_query_cache"
        )).fetchone()

        # Learned examples (learned + negative_example)
        learned_rows = db.execute(text(
            "SELECT category, COUNT(*) "
            "FROM wpo.jay_knowledge_embeddings "
            "WHERE category IN ('learned', 'negative_example') "
            "GROUP BY category"
        )).fetchall()
        learned_by_cat = {r[0]: r[1] for r in learned_rows}

        # Full knowledge base breakdown
        kb_rows = db.execute(text(
            "SELECT category, COUNT(*) "
            "FROM wpo.jay_knowledge_embeddings "
            "GROUP BY category"
        )).fetchall()
        kb_by_cat = {r[0]: r[1] for r in kb_rows}

        return {
            "entity_aliases": {
                "total": alias_total,
                "by_type": alias_by_type,
            },
            "query_cache": {
                "total": cache_row[0] or 0,
                "avg_success_rate": round(float(cache_row[1]), 4) if cache_row[1] is not None else 0,
                "total_executions": cache_row[2] or 0,
            },
            "learned_examples": {
                "total": sum(learned_by_cat.values()),
                "positive": learned_by_cat.get("learned", 0),
                "negative": learned_by_cat.get("negative_example", 0),
            },
            "knowledge_base": kb_by_cat,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch learning stats: {e}")


@admin_router.get("/learning/entity-aliases")
async def list_entity_aliases(
    request: Request,
    entity_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    """Paginated list of entity aliases."""
    get_current_user(request)
    try:
        offset = (page - 1) * page_size
        params = {"page_size": page_size, "offset": offset}

        where_clause = "WHERE is_active = true"
        if entity_type:
            where_clause += " AND entity_type = :entity_type"
            params["entity_type"] = entity_type

        # Total count
        total = db.execute(
            text(f"SELECT COUNT(*) FROM wpo.jay_entity_aliases {where_clause}"),
            params,
        ).scalar()

        # Items
        rows = db.execute(text(
            f"SELECT id, entity_type, alias_value, canonical_value, source, "
            f"usage_count, created_at, is_active "
            f"FROM wpo.jay_entity_aliases {where_clause} "
            f"ORDER BY usage_count DESC, created_at DESC "
            f"LIMIT :page_size OFFSET :offset"
        ), params).fetchall()

        items = [
            {
                "id": str(r[0]),
                "entity_type": r[1],
                "alias_value": r[2],
                "canonical_value": r[3],
                "source": r[4],
                "use_count": r[5],
                "created_at": str(r[6]) if r[6] else None,
                "is_active": r[7],
            }
            for r in rows
        ]

        return {"items": items, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch entity aliases: {e}")


@admin_router.get("/learning/query-cache")
async def list_query_cache(
    request: Request,
    module: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    """Paginated list of cached queries."""
    get_current_user(request)
    try:
        offset = (page - 1) * page_size
        params = {"page_size": page_size, "offset": offset}

        where_clause = "WHERE 1=1"
        if module:
            where_clause += " AND module = :module"
            params["module"] = module

        # Total count
        total = db.execute(
            text(f"SELECT COUNT(*) FROM wpo.jay_query_cache {where_clause}"),
            params,
        ).scalar()

        # Items
        rows = db.execute(text(
            f"SELECT id, query_text, module, sql_text, execution_count, success_count, "
            f"failure_count, avg_exec_ms, created_at, last_used_at "
            f"FROM wpo.jay_query_cache {where_clause} "
            f"ORDER BY execution_count DESC, last_used_at DESC "
            f"LIMIT :page_size OFFSET :offset"
        ), params).fetchall()

        items = [
            {
                "id": str(r[0]),
                "query_text": r[1],
                "module": r[2],
                "sql": r[3],
                "execution_count": r[4],
                "success_count": r[5],
                "failure_count": r[6],
                "avg_exec_ms": float(r[7]) if r[7] is not None else None,
                "created_at": str(r[8]) if r[8] else None,
                "last_used_at": str(r[9]) if r[9] else None,
            }
            for r in rows
        ]

        return {"items": items, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch query cache: {e}")


@admin_router.get("/learning/learned-examples")
async def list_learned_examples(
    request: Request,
    category: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    """Paginated list of learned knowledge embeddings."""
    get_current_user(request)
    try:
        offset = (page - 1) * page_size
        params = {"page_size": page_size, "offset": offset}

        where_clause = "WHERE category IN ('learned', 'negative_example')"
        if category:
            where_clause += " AND category = :category"
            params["category"] = category

        # Total count
        total = db.execute(
            text(f"SELECT COUNT(*) FROM wpo.jay_knowledge_embeddings {where_clause}"),
            params,
        ).scalar()

        # Items
        rows = db.execute(text(
            f"SELECT id, category, text, metadata, score_weight, created_at "
            f"FROM wpo.jay_knowledge_embeddings {where_clause} "
            f"ORDER BY created_at DESC "
            f"LIMIT :page_size OFFSET :offset"
        ), params).fetchall()

        items = [
            {
                "id": str(r[0]),
                "category": r[1],
                "text": r[2],
                "metadata": r[3],
                "score_weight": float(r[4]) if r[4] is not None else None,
                "created_at": str(r[5]) if r[5] else None,
            }
            for r in rows
        ]

        return {"items": items, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch learned examples: {e}")
