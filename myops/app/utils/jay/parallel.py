"""JAI Parallel Execution - Thread-safe session factory and parallel pipeline executor.

Provides ``ParallelPipelineExecutor`` which runs multiple intent-pipeline
workers concurrently using ``ThreadPoolExecutor``.  Each worker gets its own
DB session created via ``create_worker_session()`` (and optionally a Synapse
session) so there is no cross-thread session sharing.

Also provides ``run_parallel_insights()`` for executing multiple dashboard
insight queries in parallel, collecting all results.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Any, Dict

from sqlalchemy import text

from app.db.session import SessionLocal, SynapseSessionLocal
from app.utils.jay.scope_injector import inject_scope

logger = logging.getLogger(__name__)

MAX_PARALLEL_WORKERS = 3
WORKER_TIMEOUT_SECONDS = 45


def create_worker_session():
    """Create a new PostgreSQL DB session for a worker thread.

    Caller is responsible for closing the returned session.
    """
    return SessionLocal()


def create_worker_synapse_session():
    """Create a new Synapse DB session for a worker thread.

    Caller is responsible for closing the returned session.
    """
    return SynapseSessionLocal()


class WorkerResult:
    """Result from a parallel pipeline worker."""

    def __init__(
        self,
        worker_id: int,
        intent_confidence: float,
        result: Any = None,
        error: str = None,
        elapsed_ms: float = 0,
        cancelled: bool = False,
    ):
        self.worker_id = worker_id
        self.intent_confidence = intent_confidence
        self.result = result
        self.error = error
        self.elapsed_ms = elapsed_ms
        self.cancelled = cancelled

    @property
    def success(self) -> bool:
        return self.result is not None and self.error is None and not self.cancelled

    def __repr__(self) -> str:
        status = "OK" if self.success else ("CANCEL" if self.cancelled else "ERR")
        return (
            f"WorkerResult(id={self.worker_id}, status={status}, "
            f"confidence={self.intent_confidence:.2f}, elapsed={self.elapsed_ms:.0f}ms)"
        )


class ParallelPipelineExecutor:
    """Runs multiple pipeline workers in parallel, returns best successful result.

    Each worker function receives ``(cancel_event, worker_session)`` and should
    return a pipeline result or raise an exception.  The executor creates a
    fresh DB session per worker, catches exceptions, and sets a cancel event
    once the first worker succeeds so slower workers can bail out early.
    """

    def __init__(
        self,
        max_workers: int = MAX_PARALLEL_WORKERS,
        timeout: float = WORKER_TIMEOUT_SECONDS,
    ):
        self.max_workers = max_workers
        self.timeout = timeout
        self._cancel_event = threading.Event()
        self._results: List[WorkerResult] = []
        self._lock = threading.Lock()

    def execute(self, worker_fns: List[Callable]) -> List[WorkerResult]:
        """Execute worker functions in parallel.

        Each ``worker_fn`` must accept two positional arguments:

            ``(cancel_event: threading.Event, worker_session: Session)``

        and return a result or raise an exception.

        Returns all ``WorkerResult`` objects sorted by:
          1. Successful results first
          2. Then by ``intent_confidence`` descending
        """
        self._cancel_event.clear()
        self._results = []
        n = min(len(worker_fns), self.max_workers)

        if n == 0:
            return []

        # Confidence values are encoded as the index in the worker_fns list;
        # the caller is expected to pass them in descending confidence order.
        # We store the actual confidence in the WorkerResult later via the
        # worker_fn's return value.  For now we pass the index to identify them.

        def _run_worker(worker_id: int, fn: Callable) -> WorkerResult:
            session = None
            t0 = time.time()
            try:
                session = create_worker_session()

                if self._cancel_event.is_set():
                    return WorkerResult(
                        worker_id=worker_id,
                        intent_confidence=0.0,
                        cancelled=True,
                        elapsed_ms=(time.time() - t0) * 1000,
                    )

                result = fn(self._cancel_event, session)

                elapsed = (time.time() - t0) * 1000

                # Extract confidence from result if available
                confidence = 0.0
                if hasattr(result, "intent_confidence"):
                    confidence = result.intent_confidence
                elif isinstance(result, dict) and "intent_confidence" in result:
                    confidence = result["intent_confidence"]

                worker_result = WorkerResult(
                    worker_id=worker_id,
                    intent_confidence=confidence,
                    result=result,
                    elapsed_ms=elapsed,
                )

                # Signal other workers to cancel once we have a success
                self._cancel_event.set()
                logger.info(
                    f"[PARALLEL] Worker {worker_id} succeeded in {elapsed:.0f}ms "
                    f"(confidence={confidence:.2f}), cancelling others"
                )
                return worker_result

            except Exception as e:
                elapsed = (time.time() - t0) * 1000
                error_msg = str(e)[:200]
                logger.warning(
                    f"[PARALLEL] Worker {worker_id} failed in {elapsed:.0f}ms: {error_msg}"
                )
                return WorkerResult(
                    worker_id=worker_id,
                    intent_confidence=0.0,
                    error=error_msg,
                    elapsed_ms=elapsed,
                )
            finally:
                if session is not None:
                    try:
                        session.close()
                    except Exception:
                        pass

        # Submit all workers
        results: List[WorkerResult] = []
        with ThreadPoolExecutor(max_workers=n) as pool:
            future_to_id: Dict[Future, int] = {}
            for idx, fn in enumerate(worker_fns[:n]):
                future = pool.submit(_run_worker, idx, fn)
                future_to_id[future] = idx

            # Track which workers have reported back
            completed_worker_ids: set = set()

            # Collect results as they complete, respecting timeout.
            # as_completed() raises TimeoutError if not all futures finish
            # within the deadline — we must catch it to preserve partial results.
            try:
                for future in as_completed(future_to_id, timeout=self.timeout):
                    try:
                        wr = future.result(timeout=1)
                        results.append(wr)
                        completed_worker_ids.add(future_to_id[future])
                    except Exception as e:
                        wid = future_to_id[future]
                        completed_worker_ids.add(wid)
                        results.append(WorkerResult(
                            worker_id=wid,
                            intent_confidence=0.0,
                            error=f"Future error: {str(e)[:100]}",
                        ))
            except TimeoutError:
                # Some futures did not complete within the deadline.
                # Signal remaining workers to stop.
                self._cancel_event.set()

                timed_out_ids = [
                    wid for wid in future_to_id.values()
                    if wid not in completed_worker_ids
                ]
                logger.warning(
                    f"[PARALLEL] Timeout after {self.timeout}s — "
                    f"completed workers: {sorted(completed_worker_ids)}, "
                    f"timed-out workers: {timed_out_ids}"
                )

                # Record timed-out workers as cancelled results
                for wid in timed_out_ids:
                    results.append(WorkerResult(
                        worker_id=wid,
                        intent_confidence=0.0,
                        error=f"Worker timed out after {self.timeout}s",
                        elapsed_ms=self.timeout * 1000,
                        cancelled=True,
                    ))

        # Sort: successful first, then by confidence descending
        results.sort(key=lambda r: (r.success, r.intent_confidence), reverse=True)

        logger.info(
            f"[PARALLEL] {len(results)} workers finished: "
            f"{sum(1 for r in results if r.success)} succeeded, "
            f"{sum(1 for r in results if r.cancelled)} cancelled, "
            f"{sum(1 for r in results if r.error and not r.cancelled)} failed"
        )

        self._results = results
        return results


# ---------------------------------------------------------------------------
# Parallel Insight Execution (Dashboard)
# ---------------------------------------------------------------------------

INSIGHT_MAX_WORKERS = 4
INSIGHT_TIMEOUT_SECONDS = 30.0
_PG_STATEMENT_TIMEOUT_MS = 25_000  # per-query guard inside Postgres


@dataclass
class InsightResult:
    """Result from executing a single dashboard insight query."""

    title: str
    description: str
    format_hint: str
    raw_data: List[Dict[str, Any]]
    sql: str
    success: bool
    error: str = ""
    metric_columns: List[str] = field(default_factory=list)
    dimension_columns: List[str] = field(default_factory=list)


def _execute_insight_sql(
    sql: str,
    db_type: str,
) -> List[Dict[str, Any]]:
    """Execute a single insight SQL string in a fresh, thread-local session.

    Creates and closes its own session so callers never share sessions across
    threads.  For Postgres a per-statement timeout is set as an extra guard.
    """
    session = None
    try:
        if db_type == "synapse":
            session = SynapseSessionLocal()
        else:
            session = SessionLocal()
            session.execute(
                text(f"SET statement_timeout = '{_PG_STATEMENT_TIMEOUT_MS}'")
            )

        result = session.execute(text(sql))
        columns = list(result.keys())
        rows = result.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    finally:
        if session is not None:
            try:
                if db_type != "synapse":
                    session.execute(text("RESET statement_timeout"))
            except Exception:
                pass
            try:
                session.close()
            except Exception:
                pass


def run_parallel_insights(
    insight_specs: List[Dict[str, Any]],
    scope: Dict[str, str],
    scope_columns: Dict[str, str],
    db_type: str = "postgres",
    timeout_seconds: float = INSIGHT_TIMEOUT_SECONDS,
) -> List[InsightResult]:
    """Execute multiple insight SQL queries in parallel and collect ALL results.

    Args:
        insight_specs: List of insight spec dicts, each containing at minimum
            ``sql_template``, ``title``, ``description``, ``format_hint``.
            May also contain ``metric_columns`` and ``dimension_columns``.
        scope: Permission scope dict from JWT (e.g. ``{"entity_id": "42"}``).
        scope_columns: Module scope column mapping
            (e.g. ``{"entity_id": "company_id"}``).
        db_type: ``"postgres"`` or ``"synapse"``.
        timeout_seconds: Maximum wall-clock time to wait for all queries.

    Returns:
        A list of :class:`InsightResult` in the **same order** as
        *insight_specs*.  Failed or timed-out specs produce results with
        ``success=False`` and a descriptive ``error`` message.
    """
    if not insight_specs:
        return []

    n_specs = len(insight_specs)
    # Pre-allocate result slots so we can fill them by index
    results: List[Optional[InsightResult]] = [None] * n_specs

    def _run_one(index: int) -> InsightResult:
        spec = insight_specs[index]
        title = spec.get("title", f"Insight {index + 1}")
        description = spec.get("description", "")
        format_hint = spec.get("format_hint", "table")
        metric_cols = spec.get("metric_columns", [])
        dimension_cols = spec.get("dimension_columns", [])
        sql_template = spec.get("sql_template", "")

        if not sql_template:
            return InsightResult(
                title=title,
                description=description,
                format_hint=format_hint,
                raw_data=[],
                sql="",
                success=False,
                error="No sql_template provided in insight spec",
                metric_columns=metric_cols,
                dimension_columns=dimension_cols,
            )

        try:
            # Normalize scope placeholder variants from LLM output
            normalized_sql = sql_template
            normalized_sql = normalized_sql.replace("{{SCOPE_FILTER}}", "{SCOPE_FILTER}")
            # Fix empty braces {} that LLM sometimes generates
            import re as _re
            normalized_sql = _re.sub(
                r"WHERE\s+\{\}\s+AND",
                "WHERE {SCOPE_FILTER} AND",
                normalized_sql,
                flags=_re.IGNORECASE,
            )
            normalized_sql = _re.sub(
                r"WHERE\s+\{\}",
                "WHERE {SCOPE_FILTER}",
                normalized_sql,
                flags=_re.IGNORECASE,
            )

            # Inject entity scope filters
            scoped_sql = inject_scope(
                normalized_sql, scope, scope_columns, db_type=db_type
            )
            # Execute with a thread-local session
            rows = _execute_insight_sql(scoped_sql, db_type)

            return InsightResult(
                title=title,
                description=description,
                format_hint=format_hint,
                raw_data=rows,
                sql=scoped_sql,
                success=True,
                metric_columns=metric_cols,
                dimension_columns=dimension_cols,
            )
        except Exception as exc:
            error_msg = str(exc)[:300]
            logger.warning(
                "[INSIGHTS] Insight %d (%s) failed: %s", index, title, error_msg
            )
            return InsightResult(
                title=title,
                description=description,
                format_hint=format_hint,
                raw_data=[],
                sql=sql_template,
                success=False,
                error=error_msg,
                metric_columns=metric_cols,
                dimension_columns=dimension_cols,
            )

    t0 = time.time()
    workers = min(n_specs, INSIGHT_MAX_WORKERS)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx: Dict[Future, int] = {}
        for idx in range(n_specs):
            future = pool.submit(_run_one, idx)
            future_to_idx[future] = idx

        completed_indices: set = set()

        try:
            for future in as_completed(future_to_idx, timeout=timeout_seconds):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result(timeout=1)
                except Exception as exc:
                    spec = insight_specs[idx]
                    results[idx] = InsightResult(
                        title=spec.get("title", f"Insight {idx + 1}"),
                        description=spec.get("description", ""),
                        format_hint=spec.get("format_hint", "table"),
                        raw_data=[],
                        sql=spec.get("sql_template", ""),
                        success=False,
                        error=f"Future error: {str(exc)[:200]}",
                        metric_columns=spec.get("metric_columns", []),
                        dimension_columns=spec.get("dimension_columns", []),
                    )
                completed_indices.add(idx)
        except TimeoutError:
            elapsed = time.time() - t0
            timed_out = [i for i in range(n_specs) if i not in completed_indices]
            logger.warning(
                "[INSIGHTS] Timeout after %.1fs — completed: %s, timed-out: %s",
                elapsed,
                sorted(completed_indices),
                timed_out,
            )
            for idx in timed_out:
                spec = insight_specs[idx]
                results[idx] = InsightResult(
                    title=spec.get("title", f"Insight {idx + 1}"),
                    description=spec.get("description", ""),
                    format_hint=spec.get("format_hint", "table"),
                    raw_data=[],
                    sql=spec.get("sql_template", ""),
                    success=False,
                    error=f"Query timed out after {timeout_seconds}s",
                    metric_columns=spec.get("metric_columns", []),
                    dimension_columns=spec.get("dimension_columns", []),
                )

    elapsed_total = (time.time() - t0) * 1000
    succeeded = sum(1 for r in results if r is not None and r.success)
    failed = sum(1 for r in results if r is not None and not r.success)
    logger.info(
        "[INSIGHTS] %d insights executed in %.0fms — %d succeeded, %d failed",
        n_specs,
        elapsed_total,
        succeeded,
        failed,
    )

    # Safety: fill any None slots (shouldn't happen, but be defensive)
    for idx in range(n_specs):
        if results[idx] is None:
            spec = insight_specs[idx]
            results[idx] = InsightResult(
                title=spec.get("title", f"Insight {idx + 1}"),
                description=spec.get("description", ""),
                format_hint=spec.get("format_hint", "table"),
                raw_data=[],
                sql=spec.get("sql_template", ""),
                success=False,
                error="Unknown error — result slot was empty",
                metric_columns=spec.get("metric_columns", []),
                dimension_columns=spec.get("dimension_columns", []),
            )

    return results  # type: ignore[return-value]
