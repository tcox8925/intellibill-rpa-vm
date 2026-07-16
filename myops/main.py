from fastapi import FastAPI, WebSocket
from app.core.config import settings
from app.routes.Commissions import commissionDashboard
from app.routes import (
    assistant,
    entityManagement,
    pchCrm,
    pchCrmNew,
    power_bi,
    call_ai,
    agent_contract,
    contract_filters,
    call_recording,
    commission,
    get_apis_synapse,
    email,
    tickets,
    audits,
    location,
    attachments,
    agility_call_assessment,
    agility_text_bot,
    producer_support,
    crm_tickets_users,
    kvp_extract_pdf,
    dashboard,
    membercare_dashboard,
)
from app.routes.ServicePerformance import (
    service_interruption,
    automationOps,
    project_status,
)
from app.routes.User import navigation, user
from app.routes.reports import agentsReport
from app.routes.Agent import agent_calls, agents, affiliations
from app.routes.Agent import agent_emails
from app.routes.Agent import agent_notes
from app.routes.Agent import agent_attachments
from app.middleware.validator import AuthMiddleware
from fastapi.middleware.cors import CORSMiddleware
from app.utils.callAI.ws_handler import WebsocketConnectionHandler
from app.routes.Pch import pch_member, pch_care_manegement, pch_population
from app.routes.membercare import agents_view as membercare_agents_view
from app.routes.membercare import agents_dashboards as membercare_agents_dashboards
from app.routes.membercare import customer_view as membercare_customers_view
from app.routes.membercare import customer_dashboards as membercare_customer_dashboards
from app.routes.Superset import superset_dashboard
from app.routes import jay as jay_routes
from app.routes import jay_config as jay_config_routes
from app.routes.jay_config import admin_router as jay_admin_router
from app.routes import membercare_dashboard
from app.utils.callAI.sud_file_scheduler import start_scheduler
import logging as _logging

# File logging for Jay pipeline debugging
_jay_fh = _logging.FileHandler("/tmp/jay-pipeline.log")
_jay_fh.setLevel(_logging.DEBUG)
_jay_fh.setFormatter(_logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
_logging.getLogger("app.utils.jay").addHandler(_jay_fh)
_logging.getLogger("app.routes.jay").addHandler(_jay_fh)

app = FastAPI(title=settings.PROJECT_NAME)

# origins = [
#     "http://localhost:3000",
#     "https://myopsportal-chgygnctd9h3c3gw.eastus-01.azurewebsites.net",
#     "https://myops360.834labs.com",
# ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # allow_origin_regex=r"https?://.*\.azurestaticapps\.net",
    allow_credentials=True,
    allow_methods=[
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ],  # Allows GET, POST, PUT, PATCH, DELETE etc.
    allow_headers=["*"],  # Allows Authorization and other headers
)

app.add_middleware(
    AuthMiddleware,
    exclude_paths=[
        "/login",
        "/docs",
        "/forgot-password",
        "/openapi.json",
        "/faq-chat",
        "/contracting-chat",
        "/producer-support-calls/send-onboarding-email",
        "/producer-support-calls/send-email-otp",
        "/producer-support-calls/send-email",
        "/agility-text-bot",
        "/pdf-kvp-extraction"
    ],
)


# WebSocket for Call AI
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    handler = WebsocketConnectionHandler(websocket)
    await handler.handle()


@app.on_event("startup")
async def startup_bootstrap_jai():
    """Initialize JAI column retrieval components on startup (lazy)."""
    import os
    import logging
    import threading

    logger = logging.getLogger("jay.startup")

    if os.environ.get("JAI_COLUMN_RETRIEVAL_ENABLED", "true").lower() not in ("true", "1", "yes"):
        logger.info("JAI column retrieval disabled, skipping bootstrap")
        return

    def _bootstrap():
        try:
            # 0. Pre-warm LLM + embedding clients (Azure Key Vault fetch)
            try:
                from app.utils.jay.llm_client import get_llm_client, get_embedding_client
                get_llm_client()
                get_embedding_client()
                logger.info("JAI: LLM + embedding clients pre-warmed")
            except Exception as e:
                logger.warning(f"JAI: LLM/embedding client pre-warm failed (non-fatal): {e}")

            from app.db.session import SessionLocal
            from app.utils.jay.column_embedding_sync import sync_column_embeddings
            from app.utils.jay.fk_inference import discover_join_graph
            from app.utils.jay.column_retriever import get_lsh_index

            session = SessionLocal()
            try:
                # ── PHASE A: Fast warmups (~60s) ─────────────────────────
                # Load existing data into memory FIRST so the first user
                # query doesn't pay cold-start penalties. Column sync
                # (the slow step) runs LAST.

                # 1. Pre-warm column embedding numpy matrix (loads existing from DB)
                try:
                    from app.utils.jay.column_retriever import warmup_column_cache
                    col_stats = warmup_column_cache(session)
                    logger.info(f"JAI: Column embedding matrix pre-warmed: {col_stats}")
                except Exception as e:
                    logger.warning(f"JAI: Column matrix warmup failed (non-fatal): {e}")

                # 2. Build FK join graph (and pre-warm pipeline_v2 cache)
                logger.info("JAI: Building FK join graph...")
                graph = discover_join_graph(session)
                logger.info(f"JAI: FK graph: {len(graph.tables)} tables, {len(graph.edges)} edges")

                try:
                    import app.utils.jay.pipeline_v2 as _p2
                    with _p2._join_graph_lock:
                        if _p2._join_graph_cache is None:
                            _p2._join_graph_cache = graph
                            logger.info("JAI: Pre-warmed pipeline_v2 join graph cache")
                except Exception as e:
                    logger.debug(f"JAI: Could not pre-warm pipeline_v2 cache: {e}")

                # 3. Build LSH value index
                logger.info("JAI: Building LSH value index...")
                lsh = get_lsh_index()
                lsh.build(session)
                logger.info("JAI: LSH index ready")

                # 4. Pre-warm knowledge caches (synonyms, glossary, filter values, examples)
                logger.info("JAI: Pre-warming knowledge caches...")
                try:
                    from app.utils.jay.knowledge_cache import (
                        get_synonym_context,
                        get_business_glossary_context,
                        get_query_examples,
                    )
                    get_synonym_context()
                    get_business_glossary_context()
                    from app.utils.jay.knowledge_cache import _get_all_filter_values
                    _get_all_filter_values()
                    get_query_examples([])
                    logger.info("JAI: Knowledge caches pre-warmed")
                except Exception as e:
                    logger.warning(f"JAI: Knowledge cache warmup failed (non-fatal): {e}")

                # 5. Pre-warm RAG knowledge embeddings
                try:
                    from app.utils.jay.rag_retriever import warmup_knowledge_cache
                    rag_stats = warmup_knowledge_cache(session)
                    logger.info(f"JAI: RAG knowledge embeddings pre-warmed: {rag_stats}")
                except Exception as e:
                    logger.warning(f"JAI: RAG warmup failed (non-fatal): {e}")

                # 6. Pre-warm query cache embeddings
                try:
                    from app.utils.jay.query_cache import warmup_query_cache
                    qc_stats = warmup_query_cache(session)
                    logger.info(f"JAI: Query cache pre-warmed: {qc_stats}")
                except Exception as e:
                    logger.warning(f"JAI: Query cache warmup failed (non-fatal): {e}")

                # 7. Pre-warm entity alias cache (self-learning abbreviation resolver)
                try:
                    from app.utils.jay.alias_cache import warmup as warmup_alias_cache
                    alias_stats = warmup_alias_cache(session)
                    logger.info(f"JAI: Entity alias cache pre-warmed: {alias_stats}")
                except Exception as e:
                    logger.warning(f"JAI: Alias cache warmup failed (non-fatal): {e}")

                # 8. Pre-warm carrier lookup cache (in-memory for fast entity resolution)
                try:
                    from app.utils.jay.knowledge_cache import warmup_carrier_lookup
                    carrier_count = warmup_carrier_lookup(session)
                    logger.info(f"JAI: Carrier lookup cache pre-warmed: {carrier_count} carriers")
                except Exception as e:
                    logger.warning(f"JAI: Carrier lookup warmup failed (non-fatal): {e}")

                logger.info("JAI: Phase A complete — all caches warm, ready for queries")
            finally:
                session.close()

            # ── PHASE B: Slow background sync (~4-5 min) ─────────────
            # Uses a SEPARATE session so Phase A's connection is returned to pool.
            # Incrementally re-profile and re-embed any changed columns.
            phase_b_session = SessionLocal()
            try:
                logger.info("JAI: Syncing column embeddings (background)...")
                stats = sync_column_embeddings(phase_b_session, force=False)
                logger.info(f"JAI: Column embeddings synced: {stats}")

                # If any columns changed, refresh the matrix cache
                if stats.get("changed_columns", 0) > 0:
                    try:
                        from app.utils.jay.column_retriever import invalidate_column_embedding_cache
                        invalidate_column_embedding_cache()
                        warmup_column_cache(phase_b_session)
                        logger.info("JAI: Column matrix refreshed after sync")
                    except Exception as e:
                        logger.warning(f"JAI: Column matrix refresh failed (non-fatal): {e}")

                logger.info("JAI: Bootstrap complete")
            except Exception as e:
                logger.warning(f"JAI: Phase B sync failed (non-fatal): {e}")
            finally:
                phase_b_session.close()

        except Exception as e:
            logger.error(f"JAI: Bootstrap failed (non-fatal): {e}")

    # Run in background thread to not block startup
    thread = threading.Thread(target=_bootstrap, name="jai-bootstrap", daemon=True)
    thread.start()
    logger.info("JAI: Bootstrap started in background thread")


# Register routes with version prefix
app.include_router(assistant.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(user.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(entityManagement.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(pchCrm.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(pchCrmNew.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(navigation.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(power_bi.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(call_ai.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(agility_call_assessment.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(agents.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(affiliations.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(agent_contract.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(contract_filters.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(call_recording.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(commission.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(agent_calls.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(agent_emails.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(agent_notes.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(agent_attachments.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(get_apis_synapse.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(email.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(commissionDashboard.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(tickets.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(crm_tickets_users.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(audits.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(agentsReport.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(service_interruption.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(location.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(attachments.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(automationOps.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(project_status.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(pch_care_manegement.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(pch_member.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(pch_population.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(agility_text_bot.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(producer_support.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(superset_dashboard.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(kvp_extract_pdf.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(jay_routes.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(jay_config_routes.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(jay_admin_router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(dashboard.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(membercare_dashboard.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(membercare_agents_view.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(membercare_agents_dashboards.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(membercare_customers_view.router, prefix=f"{settings.API_V1_PREFIX}")
app.include_router(membercare_customer_dashboards.router, prefix=f"{settings.API_V1_PREFIX}")

# app.include_router(call_ai.router, prefix=f"{settings.API_V1_PREFIX}")

# @app.on_event("startup")
# def startup_event():
#     start_scheduler()
