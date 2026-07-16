"""
JAI Suggestions - Context-aware prompt suggestions.

Provides default suggestions per module and merges with user's top favorites.
"""

from typing import List, Dict, Optional

MODULE_SUGGESTIONS: Dict[str, List[str]] = {
    "agility-crm": [
        "How many active agents do we have?",
        "Show agent count by carrier",
        "List agents with expired licenses",
    ],
    "pch-crm": [
        "How many providers are in Texas?",
        "Show providers by credentialing status",
        "Find provider by NPI",
    ],
    "commissions": [
        "Total commissions this month",
        "Commission breakdown by carrier",
        "Show top 10 agents by commission",
    ],
    "default": [
        "How many total agents?",
        "Show commission summary",
        "Navigate to agent management",
    ],
}


def get_default_suggestions(current_module: Optional[str] = None) -> List[str]:
    """Get default suggestions based on the current module context."""
    if current_module:
        for key, suggestions in MODULE_SUGGESTIONS.items():
            if key in current_module.lower():
                return suggestions
    return MODULE_SUGGESTIONS["default"]


def merge_suggestions(
    top_favorites: List[Dict],
    current_module: Optional[str] = None,
    max_favorites: int = 3,
    max_defaults: int = 3,
) -> Dict[str, List]:
    """Merge user's top favorites with module defaults.

    Returns:
        {
            "favorites": [{"prompt_text": ..., "use_count": ...}],
            "defaults": ["suggestion1", "suggestion2", ...]
        }
    """
    favorites = top_favorites[:max_favorites]
    defaults = get_default_suggestions(current_module)[:max_defaults]

    # Remove defaults that overlap with favorites
    fav_texts = {f.get("prompt_text", "").lower() for f in favorites}
    filtered_defaults = [d for d in defaults if d.lower() not in fav_texts]

    return {
        "favorites": favorites,
        "defaults": filtered_defaults,
    }
