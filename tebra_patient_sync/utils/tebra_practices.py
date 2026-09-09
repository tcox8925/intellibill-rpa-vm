"""Active practices (EDI_Tebra groups) belonging to an active client -
used to feed tebra_api.py's per-practice demographics pull one practice
at a time.

Ported from the getGroupsAsPracticesForPullingFaceSheets tRPC procedure:

    const groups = await db
        .select({ id: group.id, groupName: group.grpName })
        .from(group)
        .innerJoin(client, eq(group.clientId, client.clientId))
        .where(eq(client.status, true));
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.client import Client
from models.group import Group


def get_active_groups_as_practices(session: Session) -> list[dict]:
    rows = session.execute(
        select(Group.id, Group.grp_name)
        .join(Client, Group.client_id == Client.client_id)
        .where(Client.status.is_(True))
    ).all()
    return [{"id": row.id, "practice_name": row.grp_name} for row in rows]
