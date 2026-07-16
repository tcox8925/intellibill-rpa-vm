"""
JAI Permission Utilities

Provides helpers for extracting user entity permissions,
used by Jay API routes to enforce entity-scoped visibility.
"""

from typing import Optional, List
from sqlalchemy.orm import Session
from app.models.Users import UserPermissions


def get_permitted_entity_ids(
    user_id: str,
    role: str,
    db: Session,
) -> Optional[List[str]]:
    """Return the list of entity_ids the user has permission to access.

    Returns:
        None: for admin users (meaning all entities are allowed, no filter needed)
        List[str]: for non-admin users, the list of permitted entity_id values.
                   Empty list means the user has no entity permissions.
    """
    if role == "admin":
        return None

    row = (
        db.query(UserPermissions)
        .filter(UserPermissions.user_id == user_id)
        .first()
    )

    if not row or not row.entity_permissions:
        return []

    user_entities = row.entity_permissions.get("userEntities", [])
    if not isinstance(user_entities, list):
        return []

    return [
        ent["id"]
        for ent in user_entities
        if isinstance(ent, dict) and "id" in ent
    ]
