from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class QueryIntent:
    module: str
    metric: str
    dimensions: List[str] = field(default_factory=list)
    filters: Dict[str, Any] = field(default_factory=dict)
    state_dimension: Optional[str] = None
    order_by: Optional[str] = None
    order_direction: str = "ASC"
    limit: Optional[int] = None
    explicit_dimensions: bool = False

    # NEW: allows planner to mark auto-applied logic
    auto_time_applied: bool = False