from datetime import datetime
import json
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.params import File, Form
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models import Entity, Sub_Entity, Entity_Location, Entity_Representative
from app.schemas import (
    EntitySchema,
    EntityRepresentativePatchSchema,
    SubEntitySchema,
    EntityLocationSchema,
    EntityRepresentativeSchema,
    SubEntityPatchSchema,
    EntityLocationPatchSchema,
    EntitySubEntityLocSchema,
)
from typing import List, Optional
from app.core.config import settings
from app.schemas.SubEntity import (
    SubEntityLocationRepSchema,
    SubEntityPaginationResponse,
)
from app.utils.attachment_utility import upload_blob_to_path

router = APIRouter(tags=["ENTITY ROUTES"])


@router.get("/entity/all")
async def get_all_entities(
    entity_status: Optional[List[str]] = Query(None),
    entity_type: Optional[List[str]] = Query(None),
    entity_name: Optional[List[str]] = Query(None),
    entity_id: Optional[List[str]] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    def normalize(values):
        if not values:
            return None
        final = []
        for v in values:
            final.extend([p.strip() for p in v.split(",") if p.strip()])
        return final

    entity_status = normalize(entity_status)
    entity_type = normalize(entity_type)
    entity_name = normalize(entity_name)
    entity_id = normalize(entity_id)
    query = db.query(Entity)

    if entity_status:
        query = query.filter(Entity.entity_active_status.in_(entity_status))

    if entity_type:
        query = query.filter(Entity.entity_affiliation.in_(entity_type))

    if entity_name:
        query = query.filter(Entity.entity_name.in_(entity_name))

    if entity_id:
        query = query.filter(Entity.entity_id.in_(entity_id))

    total = query.count()

    rows = (
        query.order_by(Entity.entity_name)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    def extract_logo_name(entity_logo):
        if isinstance(entity_logo, list) and len(entity_logo) > 0:
            return entity_logo[0].get("file_name")
        return None

    data = []
    for row in rows:
        data.append(
            {
                "id": str(row.id),
                "entity_id": row.entity_id,
                "entity_name": row.entity_name,
                "entity_affiliation": row.entity_affiliation,
                "entity_active_status": row.entity_active_status,
                "entity_support_email": row.entity_support_email,
                "entity_logo_name": extract_logo_name(row.entity_logo),
                "entity_logo": row.entity_logo,
                "entity_address_1": row.entity_address_1,
                "entity_address_2": row.entity_address_2,
                "entity_zip_code": row.entity_zip_code,
                "entity_city": row.entity_city,
                "entity_state": row.entity_state,
            }
        )

    return {"total": total, "page": page, "page_size": page_size, "data": data}


@router.get("/entity/hierarchy", response_model=List[EntitySubEntityLocSchema])
async def get_entity_hierarchy(db: Session = Depends(get_db)):
    result = db.query(Entity).filter(Entity.entity_active_status == "active").all()
    return result


@router.post("/entity", response_model=EntitySchema)
async def create_entity(
    entity_info: str = Form(...),
    logo: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    try:
        entity_data = json.loads(entity_info)
        entity_schema = EntitySchema(**entity_data)

        existing_entity = (
            db.query(Entity).filter(Entity.entity_id == entity_schema.entity_id).first()
        )
        if existing_entity:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Entity already exists"
            )
        logo_json = None

        if logo:
            blob_path = f"entity/{entity_schema.entity_id}/{logo.filename}"
            file_data = await logo.read()

            await upload_blob_to_path(blob_path, file_data)

            logo_json = [
                {
                    "file_name": logo.filename,
                    "path": blob_path,
                    "uploaded_at": datetime.utcnow().isoformat(),
                }
            ]

        new_entity = Entity(
            **entity_schema.model_dump(by_alias=False, exclude={"entity_logo"}),
            entity_logo=logo_json,
        )

        db.add(new_entity)
        db.commit()
        db.refresh(new_entity)

        return new_entity

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to create entity: {str(e)}"
        )


@router.patch("/entity", response_model=EntitySchema)
async def update_entity(
    entity: str = Form(...),
    logo: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    try:
        entity_data = json.loads(entity)
        existing_entity = (
            db.query(Entity)
            .filter(Entity.entity_id == entity_data.get("entity_id"))
            .first()
        )
        if not existing_entity:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found"
            )
        if logo:
            blob_path = f"entity/{entity_data['entity_id']}/{logo.filename}"

            file_data = await logo.read()

            await upload_blob_to_path(
                blob_path=blob_path, file=file_data, overwrite=True
            )

            existing_entity.entity_logo = [
                {
                    "file_name": logo.filename,
                    "path": blob_path,
                    "uploaded_at": datetime.utcnow().isoformat(),
                }
            ]
        for key, value in entity_data.items():
            if (
                value is not None
                and hasattr(existing_entity, key)
                and key != "entity_logo"
            ):
                setattr(existing_entity, key, value)

        db.commit()
        db.refresh(existing_entity)

        return existing_entity

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Failed to update entity: {str(e)}"
        )


@router.delete("/entity")
async def delete_entity(entity_id: str, db: Session = Depends(get_db)):
    existing_entity = (
        db.query(Entity).filter(Entity.entity_id == str(entity_id)).first()
    )
    if not existing_entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found"
        )
    db.delete(existing_entity)
    db.flush()
    return {"detail": "Entity deleted successfully"}


@router.get("/entity/sub-entity", response_model=SubEntityPaginationResponse)
async def get_sub_entities(
    entity_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    total = db.query(Sub_Entity).filter(Sub_Entity.entity_id == entity_id).count()

    rows = (
        db.query(Sub_Entity)
        .filter(Sub_Entity.entity_id == entity_id)
        .order_by(Sub_Entity.sub_entity_fname)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    data = [SubEntityLocationRepSchema.model_validate(r) for r in rows]

    return {"total": total, "page": page, "page_size": page_size, "data": data}


@router.post("/entity/sub-entity", response_model=SubEntitySchema)
async def create_sub_entity(sub_entity: SubEntitySchema, db: Session = Depends(get_db)):
    existing_sub_entity = (
        db.query(Sub_Entity)
        .filter(Sub_Entity.sub_entity_id == sub_entity.sub_entity_id)
        .first()
    )
    if existing_sub_entity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Sub-Entity already exists"
        )
    new_sub_entity = Sub_Entity(**sub_entity.model_dump())
    db.add(new_sub_entity)
    db.flush()  # Send the INSERT to the DB
    db.refresh(new_sub_entity)  # Update the new_entity object with the ID from the DB
    return new_sub_entity


@router.patch("/entity/sub-entity", response_model=SubEntitySchema)
async def update_sub_entity(
    sub_entity: SubEntityPatchSchema, db: Session = Depends(get_db)
):
    existing_sub_entity = (
        db.query(Sub_Entity)
        .filter(Sub_Entity.sub_entity_id == sub_entity.sub_entity_id)
        .first()
    )
    if not existing_sub_entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Sub-Entity not found"
        )
    for key, value in sub_entity.model_dump(
        exclude_none=True, exclude_unset=True
    ).items():
        setattr(existing_sub_entity, key, value)
    db.flush()
    db.refresh(existing_sub_entity)
    return existing_sub_entity


@router.delete("/entity/sub-entity")
async def delete_sub_entity(sub_entity_id: str, db: Session = Depends(get_db)):
    existing_sub_entity = (
        db.query(Sub_Entity)
        .filter(Sub_Entity.sub_entity_id == str(sub_entity_id))
        .first()
    )
    if not existing_sub_entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Sub-Entity not found"
        )

    db.delete(existing_sub_entity)
    db.flush()
    return {"detail": "Sub-Entity deleted successfully"}


@router.post("/entity/location", response_model=EntityLocationSchema)
async def create_entity_location(
    location: EntityLocationSchema, db: Session = Depends(get_db)
):
    existing_location = (
        db.query(Entity_Location)
        .filter(
            Entity_Location.sub_entity_id == location.sub_entity_id,
            Entity_Location.location_id == location.location_id,
        )
        .first()
    )
    if existing_location:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Entity Location already exists",
        )
    new_location = Entity_Location(**location.model_dump())
    db.add(new_location)
    db.flush()  # Send the INSERT to the DB
    db.refresh(new_location)  # Update the new_entity object with the ID from the DB
    return new_location


@router.patch("/entity/location", response_model=EntityLocationSchema)
async def update_entity_location(
    location: EntityLocationPatchSchema, db: Session = Depends(get_db)
):
    existing_location = (
        db.query(Entity_Location)
        .filter(Entity_Location.location_id == location.location_id)
        .first()
    )
    if not existing_location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Entity Location not found"
        )
    for key, value in location.model_dump(
        exclude_none=True, exclude_unset=True
    ).items():
        setattr(existing_location, key, value)
    db.flush()
    db.refresh(existing_location)
    return existing_location


@router.delete("/entity/location")
async def delete_entity_location(
    sub_entity_id: str, location_id: str, db: Session = Depends(get_db)
):
    existing_location = (
        db.query(Entity_Location)
        .filter(
            Entity_Location.sub_entity_id == str(sub_entity_id),
            Entity_Location.location_id == str(location_id),
        )
        .first()
    )
    if not existing_location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Entity Location not found"
        )
    db.delete(existing_location)
    db.flush()
    return {"detail": "Entity Location deleted successfully"}


@router.post("/entity/representative", response_model=EntityRepresentativeSchema)
async def create_entity_representative(
    representative: EntityRepresentativeSchema, db: Session = Depends(get_db)
):
    existing_representative = (
        db.query(Entity_Representative)
        .filter(
            Entity_Representative.sub_entity_id == representative.sub_entity_id,
            Entity_Representative.rep_id == representative.rep_id,
        )
        .first()
    )
    if existing_representative:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Entity Representative already exists",
        )
    new_representative = Entity_Representative(**representative.model_dump())
    db.add(new_representative)
    db.flush()  # Send the INSERT to the DB
    db.refresh(
        new_representative
    )  # Update the new_entity object with the ID from the DB
    return new_representative


@router.patch("/entity/representative", response_model=EntityRepresentativeSchema)
async def update_entity_representative(
    representative: EntityRepresentativePatchSchema, db: Session = Depends(get_db)
):
    existing_representative = (
        db.query(Entity_Representative)
        .filter(Entity_Representative.rep_id == representative.rep_id)
        .first()
    )
    if not existing_representative:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entity Representative not found",
        )
    for key, value in representative.model_dump(
        exclude_none=True, exclude_unset=True
    ).items():
        setattr(existing_representative, key, value)
    db.flush()
    db.refresh(existing_representative)
    return existing_representative


@router.delete("/entity/representative")
async def delete_entity_representative(
    sub_entity_id: str, representative_id: str, db: Session = Depends(get_db)
):
    existing_representative = (
        db.query(Entity_Representative)
        .filter(
            Entity_Representative.sub_entity_id == sub_entity_id,
            Entity_Representative.rep_id == representative_id,
        )
        .first()
    )
    if not existing_representative:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entity Representative not found",
        )
    db.delete(existing_representative)
    db.flush()
    return {"detail": "Entity Representative deleted successfully"}


@router.get("/entity/entity-dropdowns")
async def get_entity_dropdowns(db: Session = Depends(get_db)):

    rows = (
        db.query(
            Entity.entity_name,
            Entity.entity_affiliation,
            Entity.entity_active_status,
            Entity.id,
            Entity.entity_id,
        )
        .order_by(Entity.entity_name)
        .all()
    )

    entity_type_set = set()
    entity_name_set = set()
    entity_id_set = set()
    entity_status_set = set()

    for r in rows:
        entity_type_set.add((r.entity_affiliation))
        entity_name_set.add((r.entity_name))
        entity_id_set.add((r.entity_id))
        entity_status_set.add((r.entity_active_status))

    entity_type_list = [{"label": value, "value": value} for value in entity_type_set]
    entity_name_list = [{"label": value, "value": value} for value in entity_name_set]
    entity_id_list = [{"label": value, "value": value} for value in entity_id_set]
    entity_status_list = [
        {"label": value, "value": value} for value in entity_status_set
    ]

    return {
        "data": {
            "entity_type": entity_type_list,
            "entity_name": entity_name_list,
            "entity_id": entity_id_list,
            "entity_status": entity_status_list,
        }
    }
