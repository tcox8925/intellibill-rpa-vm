import math
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import cast, String

from app.db.session import get_db
from app.models.membercare.MembercareAgent import MembercareAgent
from app.schemas.Agent import NameValueModel
from app.schemas.membercare_agents import (
    MembercareAgentResponse,
    MembercareAgentCreateSchema,
    MembercareAgentUpdateSchema,
)

router = APIRouter(tags=["MEMBERCARE AGENTS VIEW"])

MA = MembercareAgent


@router.get("/membercare-agents")
def get_membercare_agents(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(MA)

    total = query.count()
    total_pages = math.ceil(total / page_size) if total else 0
    offset = (page - 1) * page_size

    rows = query.offset(offset).limit(page_size).all()
    data = [MembercareAgentResponse.model_validate(row) for row in rows]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "data": data,
    }


@router.get("/membercare-agents/list", response_model=list[NameValueModel])
def get_membercare_agents_list(
    search: str | None = Query(None, description="Search by name or email"),
    db: Session = Depends(get_db),
):
    query = db.query(MA)
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            (MA.name.ilike(pattern)) | (MA.email.ilike(pattern))
        )
    query = query.order_by(MA.name.asc()).limit(50)
    return [{"value": str(agent.id), "name": agent.name} for agent in query.all()]


@router.get("/membercare-agents/{agent_id}")
def get_membercare_agent(
    agent_id: str,
    db: Session = Depends(get_db),
):
    agent = db.query(MA).filter(cast(MA.id, String) == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    return MembercareAgentResponse.model_validate(agent)


@router.post("/membercare-agents", status_code=201)
def create_membercare_agent(
    payload: MembercareAgentCreateSchema,
    db: Session = Depends(get_db),
):
    try:
        agent_data = payload.model_dump()
        new_agent = MA(**agent_data)
        db.add(new_agent)
        db.commit()
        db.refresh(new_agent)

        return {"message": "Membercare agent created successfully", "id": str(new_agent.id)}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/membercare-agents/{agent_id}")
def update_membercare_agent(
    agent_id: str,
    payload: MembercareAgentUpdateSchema,
    db: Session = Depends(get_db),
):
    agent = db.query(MA).filter(cast(MA.id, String) == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    try:
        updates = payload.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(agent, key, value)

        db.commit()
        return {"message": "Membercare agent updated successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
