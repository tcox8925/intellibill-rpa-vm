"""
Jay Pipeline Models - Data classes for the 3-step LLM pipeline.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set, Union


@dataclass
class EntityMention:
    """An entity mentioned in the user's query (e.g., agent name, carrier name)."""
    entity_type: str  # "agent", "carrier", "provider", "member"
    raw_value: str    # The raw text from the user query


@dataclass
class IntentResult:
    """Output from Step 1: Intent Detection."""
    action: str                                      # data_query, navigate, filter, answer, etc.
    module: Optional[str] = None                     # Which data module to query
    user_summary: Optional[str] = None               # LLM's understanding of what user wants
    format_hint: str = "auto"                        # Suggested display format
    entity_mentions: List[EntityMention] = field(default_factory=list)
    comparison_periods: Optional[List[str]] = None   # ["last_year", "this_year"]
    domains: List[str] = field(default_factory=list)   # Selected table catalog domains
    confidence: float = 0.0
    route: Optional[str] = None                      # For navigate actions
    filters: Optional[Dict[str, Any]] = None         # For filter/navigate actions
    message: Optional[str] = None                    # For answer/navigate actions
    answer_type: Optional[str] = None                # greeting, general, meta
    assumption: Optional[str] = None                 # LLM's assumption about ambiguous queries


@dataclass
class ResolvedEntities:
    """Output from Step 2: Entity Resolution."""
    resolved: Dict[str, Union[str, List[str]]] = field(default_factory=dict)    # entity_type -> resolved DB value(s)
    unresolved: List[EntityMention] = field(default_factory=list)  # Entities that couldn't be resolved
    candidates: Optional[List[Dict[str, Any]]] = None         # Ambiguous match candidates


@dataclass
class SQLGenerationResult:
    """Output from Step 3: SQL Generation."""
    sql: str
    module: str
    db_type: str = "postgres"


@dataclass
class SafetyReviewResult:
    """Output from Step 4: Safety Review."""
    is_safe: bool
    issues: List[str] = field(default_factory=list)
    severity: str = "pass"  # "pass", "warn", "block"


@dataclass
class PipelineContext:
    """Accumulated context that flows through all pipeline steps.

    Each step reads from prior entries and appends its own.
    """
    entries: List[Dict[str, Any]] = field(default_factory=list)

    def add(self, step: str, key: str, value: Any):
        """Add a context entry from a pipeline step."""
        self.entries.append({"step": step, "key": key, "value": value})

    def get(self, key: str, default: Any = None) -> Any:
        """Get the most recent value for a key (any step)."""
        for entry in reversed(self.entries):
            if entry["key"] == key:
                return entry["value"]
        return default

    def get_by_step(self, step: str) -> List[Dict[str, Any]]:
        """Get all entries from a specific step."""
        return [e for e in self.entries if e["step"] == step]

    def summary(self) -> str:
        """Human-readable summary for LLM prompts."""
        if not self.entries:
            return ""
        lines = []
        for entry in self.entries:
            lines.append(f"[{entry['step']}] {entry['key']}: {entry['value']}")
        return "\n".join(lines)


@dataclass
class RetryAttempt:
    """Record of a single pipeline retry attempt."""
    attempt: int
    module: str
    sql: Optional[str] = None
    error_category: Optional[str] = None
    error_message: Optional[str] = None
    remediation: Optional[str] = None
    stage: str = "sql_generation"  # which stage failed: sql_generation, scope_injection, safety_review, execution


@dataclass
class RetryHistory:
    """Accumulated history of all retry attempts for context passing."""
    attempts: List[RetryAttempt] = field(default_factory=list)

    def add(self, attempt: RetryAttempt):
        self.attempts.append(attempt)

    @property
    def previous_sqls(self) -> List[str]:
        """All previously generated SQL strings."""
        return [a.sql for a in self.attempts if a.sql]

    def summary_for_llm(self) -> str:
        """Format retry history as structured context for LLM prompts."""
        if not self.attempts:
            return ""
        lines = [
            "RETRY HISTORY (all previous attempts that FAILED):",
            "=" * 50,
        ]
        for a in self.attempts:
            lines.append(f"\nAttempt {a.attempt} (module: {a.module}, failed at: {a.stage}):")
            if a.sql:
                lines.append(f"  SQL: {a.sql}")
            if a.error_category:
                lines.append(f"  Error type: {a.error_category}")
            if a.error_message:
                lines.append(f"  Error: {a.error_message[:200]}")
            if a.remediation:
                lines.append(f"  Fix guidance: {a.remediation}")
        lines.append("\nYou MUST avoid repeating any of the above SQL queries.")
        lines.append("Use the error types and fix guidance to generate a fundamentally different approach.")
        return "\n".join(lines)


@dataclass
class PipelineResult:
    """Final output from the complete pipeline."""
    success: bool
    raw_data: Optional[List[Dict[str, Any]]] = None
    sql: Optional[str] = None
    module: Optional[str] = None
    format_hint: str = "auto"
    error_message: Optional[str] = None
    attempts: int = 0
    safety_blocked: bool = False
    context: Optional[PipelineContext] = None
    needs_full_restart: bool = False  # Signal to caller to restart from intent detection
    retry_history: Optional[RetryHistory] = None
    assumption: Optional[str] = None  # LLM's assumption about ambiguous queries (passed through)


@dataclass
class ToolLoopResult:
    """Result from the multi-step tool conversation loop."""
    content: str = ""                                          # Final LLM response text
    tool_calls_made: List[Dict[str, Any]] = field(default_factory=list)  # Log of all tool calls
    iterations: int = 0                                        # Number of LLM round-trips
    timed_out: bool = False                                    # Whether timeout/max iterations was hit
    error: Optional[str] = None                                # Error message if loop failed


@dataclass
class SchemaContext:
    """Assembled schema context for v2 pipeline SQL generation.

    Built by schema_assembler.py from a technical spec + metadata queries.
    Contains everything the SQL-generation LLM needs to write correct SQL
    without requiring additional tool calls or schema lookups.
    """
    ddl_context: str = ""                                          # Merged DDL (domain + dynamic)
    join_context: str = ""                                         # Merged JOIN paths
    column_values: Dict[str, list] = field(default_factory=dict)   # {table.col: [values]}
    similar_queries: List[dict] = field(default_factory=list)      # past query+SQL pairs
    query_examples: str = ""                                       # domain-specific SQL examples
    expanded_tables: Set[str] = field(default_factory=set)         # all tables in context
    has_good_context: bool = False                                 # whether retrieval found useful schema
    retrieval_time_ms: float = 0.0


@dataclass
class DashboardSection:
    """A single section in a multi-section dashboard response."""
    title: str
    format: str  # "kpi", "bar_chart", "pie_chart", "line_chart", "table", "text"
    data: Dict[str, Any] = field(default_factory=dict)
    description: str = ""  # Brief description/insight for this section
    action: Optional[str] = None  # "navigate" | "filter" | None
    action_data: Optional[Dict[str, Any]] = None  # {route, filters}


@dataclass
class DashboardResponse:
    """A multi-section dashboard response with summary, sections, and navigation."""
    summary: str = ""  # Overall rich summary text (markdown)
    sections: List[DashboardSection] = field(default_factory=list)
    navigation_links: List[Dict[str, str]] = field(default_factory=list)  # [{label, route, description}]
