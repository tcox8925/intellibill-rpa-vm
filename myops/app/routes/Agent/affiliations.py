from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models import Agents
from app.schemas import AgentAffiliationsResponse, AssociationItem
from app.schemas.affiliations import AgentAffilitationCreateRequest

router = APIRouter()


@router.get("/agent-affiliations", response_model=AgentAffiliationsResponse)
def get_agent_affiliations(
    agent_npn: str,
    db: Session = Depends(get_db)
):
    agent = db.query(Agents).filter(Agents.npn == agent_npn).first()
    
    if not agent:
        raise HTTPException(
            status_code=404,
            detail=f"Agent/Agency with NPN {agent_npn} not found"
        )
    
    associations = []
    
    if agent.type and agent.type.lower() == "firm":
        responsible_agents = (
            db.query(Agents)
            .filter(Agents.responsible_agency == agent.id)
            .all()
        )
        
        for resp_agent in responsible_agents:
            associations.append(AssociationItem(
                npn=resp_agent.npn,
                fullName=resp_agent.full_name,
                phone=resp_agent.phone,
                email=resp_agent.email,
                status=resp_agent.status,
                association_type="Principal Agent"
            ))
    
    else:
        if agent.responsible_agency:
            principal_agencies = (
                db.query(Agents)
                .filter(Agents.id == agent.responsible_agency)
                .all()
            )
            
            for principal_agency in principal_agencies:
                associations.append(AssociationItem(
                    npn=principal_agency.npn,
                    fullName=principal_agency.full_name,
                    phone=principal_agency.phone,
                    email=principal_agency.email,
                    status=principal_agency.status,
                    association_type="Affiliated Agency"
                ))
    
    return AgentAffiliationsResponse(
        agent_npn=agent_npn,
        agent_type=agent.type,
        associations=associations
    )


@router.post("/agent-affiliations")
def create_agent_affiliation(
    affiliation: AgentAffilitationCreateRequest,
    db: Session = Depends(get_db)
):
    agent = db.query(Agents).filter(Agents.npn == affiliation.npn).first()
    
    if not agent:
        raise HTTPException(
            status_code=404,
            detail=f"Agent with NPN {affiliation.npn} not found"
        )
    affiliated_agent = db.query(Agents).filter(Agents.npn == affiliation.affiliated_agent_npn).first()
    if not affiliated_agent:
        raise HTTPException(
            status_code=404,
            detail=f"Affiliated Agent with NPN {affiliation.affiliated_agent_npn} not found"
        )
        
    agent.responsible_agency = affiliated_agent.id
    db.commit()
    db.refresh(agent)
    return "Affiliation added successfully"

@router.delete("/agent-affiliations", response_model=None)
def delete_agent_affiliation(
    npn: str,
    affiliated_agent_npn: str,
    db: Session = Depends(get_db)
):
    # Validate both agents exist
    agent = db.query(Agents).filter(Agents.npn == npn).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent with NPN {npn} not found")

    affiliated_agent = db.query(Agents).filter(Agents.npn == affiliated_agent_npn).first()
    if not affiliated_agent:
        raise HTTPException(status_code=404, detail=f"Affiliated Agent with NPN {affiliated_agent_npn} not found")

    # Case 1: `npn` is a FIRM → remove the affiliated agent from this agency
    if agent.type and agent.type.lower() == "firm":
        if affiliated_agent.responsible_agency != agent.id:
            raise HTTPException(
                status_code=400,
                detail=f"Agent {affiliated_agent_npn} is not affiliated with agency {npn}"
            )
        affiliated_agent.responsible_agency = None

    # Case 2: `npn` is an AGENT → remove its link to the affiliated agency
    else:
        if agent.responsible_agency != affiliated_agent.id:
            raise HTTPException(
                status_code=400,
                detail=f"Agent {npn} is not affiliated with agency {affiliated_agent_npn}"
            )
        agent.responsible_agency = None

    db.commit()
    return {"detail": "Affiliation removed successfully"}
