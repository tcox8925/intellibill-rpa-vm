import base64
import logging
import json
from fastapi import APIRouter, Depends, HTTPException, Form, File, UploadFile
from sqlalchemy.orm import Session, aliased
from sqlalchemy import Numeric, distinct, func, case, cast, or_
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from uuid import uuid4, UUID
from datetime import datetime, timezone
from app.middleware.validator import get_current_user
from app.models.Emails.EmailModal import EmailContent, EmailService
from app.models.ServicePerformance.project_status_models import ProjectStatusNotes
from app.schemas.WpoUsers import WpoGetUsersSchema
from app.utils.attachment_utility import upload_blob_to_path
from app.db.session import get_db
from app.models import ProjectStatus, ProjectStatusEmailStore, ProjectSubTask, ProjectFeature, Entity, ProjectResource, Users, ProjectStatusAuditHistory
from app.schemas.project_status import (
    FeatureSubtaskUpdate,
    ProjectStatusNoteBase,
    ProjectStatusNoteCreate,
    ProjectStatusNoteUpdate,
    ProjectStatusUpdate,
    ProjectStatusResponse,
    ProjectFeatureCreate,
    ProjectFeatureUpdate,
    ProjectResourceUpdate,
    ProjectResourceResponse,
    ProjectStatusAuditHistoryCreate,
    ProjectStatusAuditHistoryResponse,
    FeatureSubtaskCreate
)
from fastapi.security import HTTPBearer

router = APIRouter(tags=["DATA INSIGHTS PROJECT STATUS"])
security = HTTPBearer()
logger = logging.getLogger(__name__)


def generate_project_id(db: Session) -> int:
    last_id = (
        db.query(func.max(cast(ProjectStatus.project_id, Numeric)))
        .scalar()
    )
    return int((last_id or 0) + 1)


@router.post("/project-status", dependencies=[Depends(security)])
async def create_project_status(
    project_name: str = Form(...),
    majorDeliverables: Optional[str] = Form(None),
    entity_id: str = Form(...),
    attachments: Optional[List[UploadFile]] = File(None),
    phase: Optional[str] = Form(None),
    lead: Optional[str] = Form(None),
    project_status: Optional[str] = Form(None),
    progress: Optional[float] = Form(None),
    priority: Optional[str] = Form(None),
    project_date: Optional[str] = Form(None),
    requested: Optional[str] = Form(None),
    project_type:Optional[str] = Form(None),
    buisness_entity: Optional[str] = Form(None),
    major_deliverables: Optional[str] = Form(None),
    resources: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    try:
        project_id = generate_project_id(db)
        
        project = ProjectStatus(
            project_id=project_id,
            project_name=project_name,
            phase=phase,
            lead=lead,
            entity_id=entity_id,
            project_status=project_status,
            progress=progress,
            priority=priority,
            major_deliverable=majorDeliverables,
            project_date=project_date,
            requested=requested,
            project_type=project_type,
            buisness_entity=buisness_entity,
            created_at=datetime.now(timezone.utc)
        )

        db.add(project)
        db.commit()
        db.refresh(project)


        pk_id = project.pk_id
        attachment_records = []
        total_size = 0

        try:

            if attachments:
                for file in attachments:
                    file_bytes = await file.read()
                    file_size = len(file_bytes)
                    total_size += file_size

                    blob_folder = f"project_status/{pk_id}"
                    blob_path = f"{blob_folder}/{file.filename}"

                    meta = await upload_blob_to_path(blob_path, file_bytes, overwrite=True)

                    # store DB metadata
                    attachment_records.append({
                        "id": str(uuid4()),
                        "file_name": file.filename,
                        "blob_path": blob_path,
                        "mime_type": file.content_type,
                        "size": len(file_bytes),
                        "uploaded_at": datetime.now(timezone.utc).isoformat()
                    })
        
        except:
            raise HTTPException(status_code=500, detail="Failed to upload attachments")

        # Save attachments JSON
        if attachment_records:
            project.attachments = attachment_records

        saved_deliverables = []
        if major_deliverables:
            try:
                deliverables_list = json.loads(major_deliverables) if isinstance(major_deliverables, str) else major_deliverables
                for index, deliverable in enumerate(deliverables_list, start=1):
                    priority_num = deliverable.get('priority_number')
                    if priority_num is None:
                        priority_num = index
                    
                    go_live_date = deliverable.get('go_live_date') or None
         
                    subtask = ProjectFeature(
                        project_pk_id=pk_id,
                        project_name=project.project_name,
                        category=deliverable.get('category'),
                        features=deliverable.get('features'),
                        go_live_date=go_live_date,
                        status=deliverable.get('status', ''),
                        priority_number=priority_num,
                        phase=deliverable.get('phase'),
                        progress=deliverable.get('progress', 'Not Started')
                    )
                    db.add(subtask)
                    saved_deliverables.append(deliverable)
            except Exception as e:
                db.rollback()
                raise HTTPException(status_code=500, detail="Failed to save major deliverables")
        saved_resources = []
        if resources:
            try:
                resources_list = json.loads(resources) if isinstance(resources, str) else resources
                for resource_data in resources_list:
                    user_id = resource_data.get('user_id')
                    role = resource_data.get('role')
                    
                   
                    
                    if user_id and role:
                        user = db.query(Users).filter(Users.user_id == user_id).first()
                        if user:
                            resource = ProjectResource(
                                project_id=pk_id,
                                user_id=user_id,
                                role=role
                            )
                            db.add(resource)
                            saved_resources.append(resource_data)
                        else:
                            raise HTTPException(status_code=404, detail=f"User with id {user_id} not found")
            except Exception as e:
                db.rollback()
                raise HTTPException(status_code=500, detail="Failed to save resources")
        if attachment_records or saved_deliverables or saved_resources:
            db.commit()
            db.refresh(project)
        
        return {
            "success": True,
            "project_id": pk_id,
            "status": project.project_status,
            "attachments": attachment_records,
            "major_deliverables": saved_deliverables,
            "resources": saved_resources
        }

    except:
        raise HTTPException(status_code=500, detail="Failed to create project status record")
@router.get("/project-status", dependencies=[Depends(security)])
def get_projects(
    project_type: str,
    page: int = 1,
    page_size: int = 50,
    project_status: Optional[str] = None,
    phase: Optional[str] = None,
    priority: Optional[str] = None,
    requested: Optional[str] = None,
    buisness_entity: Optional[str] = None,
    entity_id: Optional[str]=None,
    db: Session = Depends(get_db)
):
    entity_alias = aliased(Entity)
    business_entity_alias = aliased(Entity)
    
    query = db.query(
        ProjectStatus.pk_id,
        ProjectStatus.project_id,
        ProjectStatus.project_name,
        ProjectStatus.phase,
        ProjectStatus.lead,
        entity_alias.entity_id,
        entity_alias.entity_name,
        ProjectStatus.project_status,
        ProjectStatus.progress,
        ProjectStatus.priority,
        ProjectStatus.major_deliverable,
        ProjectStatus.project_date,
        ProjectStatus.requested,
        ProjectStatus.project_type,
        ProjectStatus.buisness_entity,
        business_entity_alias.entity_name.label("business_entity_name"),
        ProjectStatus.attachments,
        ProjectStatus.updated_at,
        ProjectStatus.project_cost
    ).outerjoin(
        entity_alias, entity_alias.entity_id == ProjectStatus.entity_id
    ).outerjoin(
        business_entity_alias, business_entity_alias.entity_id == ProjectStatus.buisness_entity
    ).filter(ProjectStatus.project_type == project_type)

    if entity_id:
        query = query.filter(
            ProjectStatus.entity_id == entity_id
        )
    if phase:
        query = query.filter(ProjectStatus.phase == phase)
    if priority:
        query = query.filter(ProjectStatus.priority == priority)
    if requested:
        query = query.filter(ProjectStatus.requested == requested)
    if buisness_entity:
        query = query.filter(ProjectStatus.buisness_entity == buisness_entity)
    if project_status == "Active":
        query = query.filter(
            (
                ~(
                    (ProjectStatus.phase == "Complete") &
                    (ProjectStatus.priority == "Complete") 
                )
            )
        )
    elif project_status == "Complete":
        query = query.filter(
            ProjectStatus.priority == "Complete",
            ProjectStatus.phase == "Complete"
        )
    elif project_status:
        query = query.filter(ProjectStatus.project_status == project_status)

    if project_type in ["project_development", "project_ad_hoc"]:
        priority_order = case(
            (ProjectStatus.priority == "High", 1),
            (ProjectStatus.priority == "Medium", 2),
            (ProjectStatus.priority == "Low", 3),
            (ProjectStatus.priority == "Complete", 4),
            else_=5
        )

        query = query.order_by(
            priority_order, 
            case(
                (entity_alias.entity_name.op('~')('^[0-9]'), 1),
                else_=0
            ),
            entity_alias.entity_name.asc(),
            ProjectStatus.project_name.asc()
        )

    # Manual pagination
    total = query.count()
    offset = (page - 1) * page_size
    items = query.offset(offset).limit(page_size).all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "pk_id": row.pk_id,
                "project_id": row.project_id,
                "project_name": row.project_name,
                "project_status": row.project_status,
                "phase": row.phase,
                "priority": row.priority,
                "entity_id": row.entity_id,
                "entity_name": row.entity_name,
                "lead": row.lead,
                "progress": row.progress,
                "major_deliverable": row.major_deliverable,
                "project_date": row.project_date,
                "requested": row.requested,
                "project_type": row.project_type,
                "buisness_entity": row.buisness_entity,
                "buisness_entity_name": row.business_entity_name,
                "attachments": row.attachments,
                "updated_at": row.updated_at,
                "project_cost": row.project_cost
            }
            for row in items
        ]
    }

@router.patch("/project-status/{project_id}", response_model=ProjectStatusResponse, dependencies=[Depends(security)])
def update_project(
    project_id: UUID,
    project_update: ProjectStatusUpdate,
    db: Session = Depends(get_db)
):
    project = db.query(ProjectStatus).filter(ProjectStatus.pk_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    update_data = project_update.dict(exclude_unset=True)

    for field, value in update_data.items():
        setattr(project, field, value)
    if update_data.get("phase") == "Complete":
        project.progress = 100.0
        project.priority = "Complete"
        deliverables = db.query(ProjectFeature).filter(
            ProjectFeature.project_pk_id == project_id
        ).all()  
        for deliverable in deliverables:
            deliverable.progress = "Complete"
    
    project.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(project)
    return project

@router.get("/project-status-counts", dependencies=[Depends(security)])
def get_project_counts(
    project_type: str,
    project_status: Optional[str] = None,
    phase: Optional[str] = None,
    priority: Optional[str] = None,
    requested: Optional[str] = None,
    entity_id: Optional[str]= None,
    db: Session = Depends(get_db)
):
    if project_type not in ["project_development", "project_ad_hoc", "project_inventory", "project_innovation", "project_planning"]:
        raise HTTPException(status_code=400, detail="Invalid project_type")
    
    base_query = db.query(func.count(ProjectStatus.pk_id)).filter(ProjectStatus.project_type == project_type)
    if entity_id:
        base_query = base_query.filter(
            ProjectStatus.entity_id == entity_id
        )
    if phase:
        base_query = base_query.filter(ProjectStatus.phase == phase)
    if priority:
        base_query = base_query.filter(ProjectStatus.priority == priority)
    if requested:
        base_query = base_query.filter(ProjectStatus.requested == requested)
    if project_status == "Active":
        base_query = base_query.filter(
            (
                ~(
                    (ProjectStatus.phase == "Complete") &
                    (ProjectStatus.priority == "Complete") 
                )
            )
        )
    elif project_status == "Complete":
        base_query = base_query.filter(
            ProjectStatus.priority == "Complete",
            ProjectStatus.phase == "Complete"
        )
    elif project_status:
        base_query = base_query.filter(ProjectStatus.project_status == project_status)
    
    counts = {
        "total_projects": base_query.scalar() or 0,
        "high_priority": base_query.filter(ProjectStatus.priority == 'High').scalar() or 0,
        "medium_priority": base_query.filter(ProjectStatus.priority.in_(['Medium', 'Med'])).scalar() or 0,
        "complete": base_query.filter(
            ProjectStatus.phase == 'Complete',
            ProjectStatus.priority == 'Complete'
        ).scalar() or 0
    }
    
    last_updated = db.query(
        func.max(
            func.greatest(
                func.coalesce(ProjectStatus.created_at, ProjectStatus.updated_at),
                func.coalesce(ProjectStatus.updated_at, ProjectStatus.created_at)
            )
        )
    ).filter(ProjectStatus.project_type == project_type).scalar()
    
    last_updated_formatted = last_updated.strftime("%m-%d-%Y") if last_updated else None
    
    # Get filter options
    base_filter = db.query(ProjectStatus).filter(ProjectStatus.project_type == project_type)
    if entity_id:
        base_filter = base_filter.filter(ProjectStatus.entity_id == entity_id)
    
    filters = {}
    
    if project_type in ["project_inventory", "project_innovation", "project_planning"]:
        requested_values = base_filter.with_entities(distinct(ProjectStatus.requested)).filter(ProjectStatus.requested.isnot(None)).all()
        filters["requested"] = [{"id": v[0], "value": v[0]} for v in requested_values if v[0]]
    else:
        for field_name, column in [("project_status", ProjectStatus.project_status), ("phase", ProjectStatus.phase), ("priority", ProjectStatus.priority), ("requested", ProjectStatus.requested)]:
            values = base_filter.with_entities(distinct(column)).filter(column.isnot(None)).all()
            filters[field_name] = [{"id": v[0], "value": v[0]} for v in values if v[0]]
    
    return {"counts": counts, "filters": filters, "last_updated": last_updated_formatted}

@router.get("/project-status/features/{project_id}", dependencies=[Depends(security)])
def get_all_features(
    project_id: UUID,
    sort_by: str = "priority_number",
    sort_order: str = "asc",
    db: Session = Depends(get_db)
):
    query = (
        db.query(ProjectFeature)
        .filter(ProjectFeature.project_pk_id == project_id)
    )
    
    valid_sort_fields = {
        "priority_number": ProjectFeature.priority_number,
        "project_name": ProjectFeature.project_name,
        "category": ProjectFeature.category,
        "features": ProjectFeature.features,
        "status": ProjectFeature.status,
        "go_live_date": ProjectFeature.go_live_date,
        "phase": ProjectFeature.phase,
        "progress":ProjectFeature.progress
    }
    
    sort_column = valid_sort_fields.get(sort_by, ProjectFeature.priority_number)
    
    if sort_order.lower() == "desc":
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())
        
    query = query.order_by(ProjectFeature.priority_number.asc())
    
    results = query.all()
    
    items = []
    for subtask in results:
        item_dict = {
            "pk_id": subtask.pk_id,
            "project_pk_id": subtask.project_pk_id,
            "category": subtask.category,
            "features": subtask.features,
            "go_live_date": subtask.go_live_date,
            "status": subtask.status,
            "priority_number": subtask.priority_number,
            "project_name": subtask.project_name,
            "phase": subtask.phase,
            "progress": subtask.progress,
            "push_count": subtask.push_count
        }
        items.append(item_dict)
    
    return {
        "sort_by": sort_by,
        "sort_order": sort_order,
        "items": items
    }

@router.post("/project-status/features", dependencies=[Depends(security)])
async def create_feature(
    feature: ProjectFeatureCreate,
    db: Session = Depends(get_db)
):
    subtask_data = feature.model_dump()
    
    project_pk_id = subtask_data.get('project_pk_id')
    project = db.query(ProjectStatus).filter(
        ProjectStatus.pk_id == project_pk_id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    max_priority = db.query(func.max(ProjectFeature.priority_number)).filter(
        ProjectFeature.project_pk_id == project_pk_id
    ).scalar()
    subtask_data['priority_number'] = (max_priority or 0) + 1
    
    subtask = ProjectFeature(**subtask_data)
    db.add(subtask)
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(subtask)
    return subtask

@router.get("/project-status/{project_id}/resources/users", dependencies=[Depends(security)])
def get_project_resources_with_user_details(
    project_id: UUID,
    db: Session = Depends(get_db)
):
    try:
        project = db.query(ProjectStatus.pk_id).filter(ProjectStatus.pk_id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        rows = (
            db.query(
                ProjectResource.pk_id.label("resource_id"),
                Users.user_id.label("user_id"),
                Users.login.label("username"),
                Users.f_name.label("f_name"),
                Users.l_name.label("l_name"),
                Users.email.label("email"),
                ProjectResource.role.label("role"),
            )
            .select_from(ProjectResource)
            .join(Users, Users.user_id == ProjectResource.user_id)
            .filter(ProjectResource.project_id == project_id)
            .all()
        )

        return [
            {
                "resource_id": r.resource_id,
                "user_id": r.user_id,
                "username": r.username,
                "f_name": r.f_name,
                "l_name": r.l_name,
                "email": r.email,
                "role": r.role,
            }
            for r in rows
        ]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch resources")
@router.post("/project-status/{project_id}/resources", response_model=ProjectResourceResponse, dependencies=[Depends(security)])
def add_project_resource(
    project_id: UUID,
    user_id: UUID = Form(...),
    role: str = Form(...),
    db: Session = Depends(get_db)
):
    project = db.query(ProjectStatus).filter(ProjectStatus.pk_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    user = db.query(Users).filter(Users.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        resource = ProjectResource(project_id=project_id, user_id=user_id, role=role)
        db.add(resource)
        project.updated_at = datetime.now(timezone.utc)
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="Failed to add resource due to constraint violation")
    db.refresh(resource)
    return resource
@router.delete("/project-status/resources/{resource_id}", dependencies=[Depends(security)])
def delete_project_resource(
    resource_id: UUID,
    db: Session = Depends(get_db)
):
    resource = db.query(ProjectResource).filter(ProjectResource.pk_id == resource_id).first()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    db.delete(resource)
    project = db.query(ProjectStatus).filter(ProjectStatus.pk_id == resource.project_id).first()
    if project:
        project.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {"detail": "Resource deleted successfully"}


@router.patch("/project-status/resources/{resource_id}", response_model=ProjectResourceResponse, dependencies=[Depends(security)])
def update_project_resource(
    resource_id: UUID,
    resource_update: ProjectResourceUpdate,
    db: Session = Depends(get_db)
):
    resource = db.query(ProjectResource).filter(ProjectResource.pk_id == resource_id).first()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    update_data = resource_update.model_dump(exclude_unset=True)

    if "user_id" in update_data and update_data["user_id"] is not None:
        user = db.query(Users).filter(Users.user_id == update_data["user_id"]).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

    for field, value in update_data.items():
        setattr(resource, field, value)

    parent_project = db.query(ProjectStatus).filter(ProjectStatus.pk_id == resource.project_id).first()
    if parent_project:
        parent_project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(resource)
    return resource

@router.patch("/project-status/features/{feature_id}", dependencies=[Depends(security)])
def update_feature(
    feature_id: UUID,
    feature_update: ProjectFeatureUpdate,
    db: Session = Depends(get_db)
):
    subtask = db.query(ProjectFeature).filter(ProjectFeature.pk_id == feature_id).first()
    if not subtask:
        raise HTTPException(status_code=404, detail="Feature not found")
    
    update_data = feature_update.model_dump(exclude_unset=True)
    old_priority = subtask.priority_number
    new_priority = update_data.get('priority_number')
    old_go_live_date = subtask.go_live_date
    new_go_live_date = update_data.get('go_live_date')
    
    if new_go_live_date is not None and old_go_live_date != new_go_live_date:
        if subtask.push_count is None:
            subtask.push_count = 1
        else:
            subtask.push_count += 1
    
    if new_priority is not None and old_priority != new_priority:
        project_pk_id = subtask.project_pk_id
        
        other_subtasks = db.query(ProjectFeature).filter(
            ProjectFeature.project_pk_id == project_pk_id,
            ProjectFeature.pk_id != feature_id
        ).all()
        
        for other in other_subtasks:
            if other.priority_number is not None and other.priority_number >= new_priority:
                other.priority_number += 1
    
    for field, value in update_data.items():
        setattr(subtask, field, value)
       
    parent_project = db.query(ProjectStatus).filter(ProjectStatus.pk_id == subtask.project_pk_id).first()
    if parent_project:
        parent_project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(subtask)
    return subtask

@router.delete("/project-status/features/{feature_id}", dependencies=[Depends(security)])
def delete_feature(
    feature_id: UUID,
    db: Session = Depends(get_db)
):
    feature = db.query(ProjectFeature).filter(ProjectFeature.pk_id == feature_id).first()
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")
    
    parent_project = db.query(ProjectStatus).filter(ProjectStatus.pk_id == feature.project_pk_id).first()
    db.delete(feature)
    if parent_project:
        parent_project.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"detail": "Feature deleted successfully"}

@router.post("/project-status/{project_id}/attachments", dependencies=[Depends(security)])
async def add_project_attachments(
    project_id: UUID,
    attachments: List[UploadFile] = File(...),
    amount: float = Form(0),
    db: Session = Depends(get_db)
):
    project = db.query(ProjectStatus).filter(ProjectStatus.pk_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    existing_attachments = project.attachments or []
    new_attachments = []
    
    for file in attachments:
        file_bytes = await file.read()
        blob_folder = f"project_status/{project_id}"
        blob_path = f"{blob_folder}/{file.filename}"
        
        try:
            await upload_blob_to_path(blob_path, file_bytes, overwrite=True)
            upload_success = True
        except Exception as e:
            # If blob upload fails, still save metadata without blob_path
            upload_success = False
            blob_path = f"upload_failed_{file.filename}"
        
        new_attachments.append({
            "id": str(uuid4()),
            "file_name": file.filename,
            "blob_path": blob_path,
            "mime_type": file.content_type,
            "size": len(file_bytes),
            "upload_success": upload_success,
            "amount": amount,
            "uploaded_at": datetime.now(timezone.utc).isoformat()
        })
    
    project.attachments = existing_attachments + new_attachments
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    
    return {"success": True, "added_attachments": len(new_attachments), "total_attachments": len(project.attachments)}

@router.delete("/project-status/{project_id}/attachments/{attachment_id}", dependencies=[Depends(security)])
def delete_project_attachment(
    project_id: UUID,
    attachment_id: str,
    db: Session = Depends(get_db)
):
    project = db.query(ProjectStatus).filter(ProjectStatus.pk_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    attachments = project.attachments or []
    updated_attachments = [att for att in attachments if att.get("id") != attachment_id]
    
    if len(updated_attachments) == len(attachments):
        raise HTTPException(status_code=404, detail="Attachment not found")
    
    project.attachments = updated_attachments
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    
    return {"detail": "Attachment deleted successfully"}


@router.delete("/project-status/{project_id}", dependencies=[Depends(security)])
def delete_project(
    project_id: UUID,
    db: Session = Depends(get_db)
):
    project = (
        db.query(ProjectStatus)
        .filter(ProjectStatus.pk_id == project_id)
        .first()
    )

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    db.query(ProjectStatusAuditHistory).filter(
        ProjectStatusAuditHistory.project_id == project_id
    ).delete(synchronize_session=False)
    db.query(ProjectFeature).filter(
        ProjectFeature.project_pk_id == project_id
    ).delete(synchronize_session=False)
    db.query(ProjectResource).filter(
        ProjectResource.project_id == project_id
    ).delete(synchronize_session=False)
    db.delete(project)
    db.commit()

    return {"detail": "Project deleted successfully"}

@router.post("/project-status/audit-history", response_model=ProjectStatusAuditHistoryResponse, dependencies=[Depends(security)])
def create_audit_history(
    audit_data: ProjectStatusAuditHistoryCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        audit_record = ProjectStatusAuditHistory(
            created_at=datetime.now(timezone.utc),
            user_id=current_user.get("user_id"),
            project_id=audit_data.project_id,
            action_message=audit_data.action_message,
            action=audit_data.action,
            tab=audit_data.tab,
            sub_entity_id=audit_data.sub_entity_id,
            entity_id=audit_data.entity_id
        )
        
        db.add(audit_record)
        db.commit()
        db.refresh(audit_record)
        return audit_record
    
    except Exception as e:
        db.rollback()
        # logging.error(f"Error creating audit history: {str(e)}")
        print(e)
        raise HTTPException(status_code=500, detail=f"Failed to create audit history")


@router.get("/project-status/audit-history/{project_id}", dependencies=[Depends(security)])
def get_audit_history_by_project(
    project_id: UUID,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db)
):
    try:
        project = db.query(ProjectStatus).filter(ProjectStatus.pk_id == project_id).first()
        if not project:
            
            raise HTTPException(status_code=404, detail="Project not found")
        
        query = (
            db.query(
                ProjectStatusAuditHistory.pk_id,
                ProjectStatusAuditHistory.created_at,
                Users.login.label("user_login"),
                ProjectStatusAuditHistory.project_id,
                ProjectStatusAuditHistory.action_message,
                ProjectStatusAuditHistory.action,
                ProjectStatusAuditHistory.tab,
                ProjectStatusAuditHistory.sub_entity_id,
                ProjectStatusAuditHistory.entity_id
            )
            .outerjoin(Users, Users.user_id == ProjectStatusAuditHistory.user_id)
            .filter(ProjectStatusAuditHistory.project_id == project_id)
            .order_by(ProjectStatusAuditHistory.created_at.desc())
        )
        
        total = query.count()
        offset = (page - 1) * page_size
        items = query.offset(offset).limit(page_size).all()
        
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "pk_id": record.pk_id,
                    "created_at": record.created_at,
                    "user_login": record.user_login,
                    "project_id": record.project_id,
                    "action_message": record.action_message,
                    "action": record.action,
                    "tab": record.tab,
                    "sub_entity_id": record.sub_entity_id,
                    "entity_id": record.entity_id
                }
                for record in items
            ]
        }
    
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Failed to retrieve audit history")
    

@router.get("/project-status/features/sub-tasks/{feature_id}", dependencies=[Depends(security)])
def get_all_features(
    feature_id: UUID,
    sort_by: str = "priority_number",
    sort_order: str = "asc",
    db: Session = Depends(get_db)
):
    query = (
        db.query(
            ProjectSubTask.pk_id,
            ProjectSubTask.feature_pk_id,
            ProjectSubTask.id,
            ProjectSubTask.title,
            ProjectSubTask.status,
            ProjectSubTask.priority_number,
            ProjectSubTask.assignee,
            ProjectSubTask.description,
            ProjectFeature.features,
            ProjectFeature.category,
        )
        .filter(ProjectSubTask.feature_pk_id == feature_id)
        .join(ProjectFeature, ProjectFeature.pk_id == ProjectSubTask.feature_pk_id)
    )
    
    valid_sort_fields = {
        "id": ProjectSubTask.id,
        "title": ProjectSubTask.title,
        "priority_number": ProjectSubTask.priority_number,
        "status": ProjectSubTask.status,
        "assignee": ProjectSubTask.assignee,
        "description": ProjectSubTask.description,
        "features": ProjectFeature.features,
        "category": ProjectFeature.category
        
    }
    
    sort_column = valid_sort_fields.get(sort_by, ProjectSubTask.priority_number)
    
    if sort_order.lower() == "desc":
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())
        
    query = query.order_by(ProjectSubTask.priority_number.asc())
    
    results = query.all()
    
    items = []
    for subtask in results:
        item_dict = {
            "pk_id": subtask.pk_id,
            "feature_pk_id": subtask.feature_pk_id,
            "id": subtask.id,
            "title": subtask.title,
            "status": subtask.status,
            "priority_number": subtask.priority_number,
            "assignee": subtask.assignee,
            "description": subtask.description
        }
        items.append(item_dict)
    
    return {
        "sort_by": sort_by,
        "sort_order": sort_order,
        "items": items
    }

@router.post("/project-status/features/sub-tasks", dependencies=[Depends(security)])
async def create_subtask(
    subtask: FeatureSubtaskCreate,
    db: Session = Depends(get_db)
):
    subtask_data = subtask.model_dump()
    
    feature_pk_id = subtask_data.get('feature_pk_id')
    feature = db.query(ProjectFeature).filter(
        ProjectFeature.pk_id == feature_pk_id
    ).first()
    
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")
    
    max_priority = db.query(func.max(ProjectSubTask.priority_number)).filter(
        ProjectSubTask.feature_pk_id == feature_pk_id
    ).scalar()
    subtask_data['priority_number'] = (max_priority or 0) + 1
    
    subtask = ProjectSubTask(**subtask_data)
    db.add(subtask)
    feature.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(subtask)
    return subtask

@router.patch("/project-status/features/sub-tasks/{subtask_id}", dependencies=[Depends(security)])
def update_subtask(
    subtask_id: UUID,
    subtask_update: FeatureSubtaskUpdate,
    db: Session = Depends(get_db)
):
    subtask = db.query(ProjectSubTask).filter(ProjectSubTask.pk_id == subtask_id).first()
    if not subtask:
        raise HTTPException(status_code=404, detail="Feature not found")

    update_data = subtask_update.model_dump(exclude_unset=True)
    old_priority = subtask.priority_number
    new_priority = update_data.get('priority_number')

    # ─── Handle priority reordering ───────────────────────────────
    if new_priority is not None and old_priority != new_priority:
        feature_pk_id = subtask.feature_pk_id

        other_subtasks = db.query(ProjectSubTask).filter(
            ProjectSubTask.feature_pk_id == feature_pk_id,
            ProjectSubTask.pk_id != subtask_id
        ).all()

        for other in other_subtasks:
            if other.priority_number is not None and other.priority_number >= new_priority:
                other.priority_number += 1

    # ─── Handle feature_pk_id change ──────────────────────────────
    new_feature_pk_id = update_data.get('feature_pk_id')
    if new_feature_pk_id is not None:
        if str(subtask.feature_pk_id) == str(new_feature_pk_id):
            # Same as existing — remove from update to avoid redundant write
            update_data.pop('feature_pk_id')
        else:
            # Different — update old parent feature's updated_at too
            old_parent = db.query(ProjectFeature).filter(
                ProjectFeature.pk_id == subtask.feature_pk_id
            ).first()
            if old_parent:
                old_parent.updated_at = datetime.now(timezone.utc)

    for field, value in update_data.items():
        setattr(subtask, field, value)

    # Update new parent feature's updated_at
    parent_feature = db.query(ProjectFeature).filter(
        ProjectFeature.pk_id == subtask.feature_pk_id
    ).first()
    if parent_feature:
        parent_feature.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(subtask)
    return subtask


@router.delete("/project-status/features/sub-tasks/{subtask_id}", dependencies=[Depends(security)])
def delete_subtask(
    subtask_id: UUID,
    db: Session = Depends(get_db)
):
    subtask = db.query(ProjectSubTask).filter(ProjectSubTask.pk_id == subtask_id).first()
    if not subtask:
        raise HTTPException(status_code=404, detail="Subtask not found")
    
    parent_feature = db.query(ProjectFeature).filter(ProjectFeature.pk_id == subtask.feature_pk_id).first()
    db.delete(subtask)
    if parent_feature:
        parent_feature.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"detail": "Subtask deleted successfully"}


@router.post("/project-status/notes", response_model=ProjectStatusNoteBase, dependencies=[Depends(security)])
def create_project_status_note(
    payload: ProjectStatusNoteCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    
    note = ProjectStatusNotes(
        module=payload.module,
        description=payload.description,
        user_id=current_user.get("user_id"),
        source_id=payload.source_id,
        time_stamp=datetime.now(timezone.utc)
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note

@router.get("/project-status/notes/{source_id}", response_model=list[ProjectStatusNoteBase], dependencies=[Depends(security)])
def get_notes_by_source(
    source_id: UUID,
    db: Session = Depends(get_db)
):    
    return db.query(ProjectStatusNotes).filter(
        ProjectStatusNotes.source_id == source_id
    ).all()

@router.patch("/project-status/notes/{pk_id}", response_model=ProjectStatusNoteBase, dependencies=[Depends(security)])
def update_project_status_note(
    pk_id: UUID,
    payload: ProjectStatusNoteUpdate,
    db: Session = Depends(get_db)
):
    note = db.query(ProjectStatusNotes).filter(ProjectStatusNotes.pk_id == pk_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(note, field, value)

    db.commit()
    db.refresh(note)
    return note

@router.delete("/project-status/notes/{pk_id}", dependencies=[Depends(security)])
def delete_project_status_note(
    pk_id: UUID,
    db: Session = Depends(get_db)
):
    note = db.query(ProjectStatusNotes).filter(ProjectStatusNotes.pk_id == pk_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    db.delete(note)
    db.commit()

    return {"detail": "Note deleted successfully"}

@router.get("/project-status/features/sub-tasks/assignee/{project_id}", response_model=List[WpoGetUsersSchema], dependencies=[Depends(security)])
def get_subtasks_assignees(
    project_id: UUID,
    db: Session = Depends(get_db)
):
    query = (
        db.query(
            Users
        ).join(ProjectResource, ProjectResource.user_id == Users.user_id)
        .filter(ProjectResource.project_id == project_id)
    )

    users = query.all()

    return users

@router.post("/project-status/features/sub-tasks/email", dependencies=[Depends(security)])
async def store_and_send_sub_task_email(
    sender: str = Form(...),
    recipients: List[str] = Form(...),
    cc: Optional[List[str]] = Form(None),
    bcc: Optional[List[str]] = Form(None),
    subject: Optional[str] = Form(None),
    body: str = Form(...),
    body_format: str = Form("plain"),
    email_type: str = Form(...),  # mail/draft/scheduled
    sent_datetime: Optional[str] = Form(None),
    schedule_datetime: Optional[str] = Form(None),
    attachments: Optional[List[UploadFile]] = File(None),
    ticket_id: UUID = Form(...),

    db: Session = Depends(get_db)
):
    try:
        def parse_date(dt):
            return datetime.fromisoformat(dt) if dt else None
        
        def normalize_emails(value):
            if not value:
                return []
            # if single comma-separated string
            if isinstance(value, str):
                value = value.split(",")
            # if already a list, flatten any comma-separated items within
            elif isinstance(value, list):
                cleaned = []
                for v in value:
                    cleaned.extend(v.split(","))
                value = cleaned
            return [v.strip() for v in value if v and v.strip()]
        
        recipients = normalize_emails(recipients)
        cc = normalize_emails(cc)
        bcc = normalize_emails(bcc)

        # ---- STEP 1: Create record first with status="sending" ----
        email = ProjectStatusEmailStore(
            sender=sender,
            recipients=recipients,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            body_format=body_format,
            email_type=email_type,
            sent_datetime=parse_date(sent_datetime),
            schedule_datetime=parse_date(schedule_datetime),
            status="sending",
            ticket_id=ticket_id
        )

        db.add(email)
        db.commit()
        db.refresh(email)

        MAX_ATTACHMENT_SIZE = 7 * 1024 * 1024  # 7 MB

        pk_id = email.pk_id
        attachment_records = []
        total_size = 0
        email_attachments = []  # <--- store file bytes for sending

        if attachments:
            for file in attachments:
                file_bytes = await file.read()
                file_size = len(file_bytes)
                total_size += file_size

                # Check size limit (total across all attachments)
                if total_size > MAX_ATTACHMENT_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Total attachment size exceeds 20MB limit"
                    )

                blob_folder = f"emails/{pk_id}"
                blob_path = f"{blob_folder}/{file.filename}"

                meta = await upload_blob_to_path(blob_path, file_bytes, overwrite=True)

                # store DB metadata
                attachment_records.append({
                    "id": str(uuid4()),
                    "file_name": file.filename,
                    "blob_path": blob_path,
                    "mime_type": file.content_type,
                    "size": len(file_bytes)
                })

                # store actual file bytes for sending email
                email_attachments.append({
                    "file_name": file.filename,
                    "content": base64.b64encode(file_bytes).decode(),
                    "content_type": file.content_type
                })

        # Save attachments JSON
        if attachment_records:
            email.attachments = attachment_records
            db.commit()
            db.refresh(email)

        if email_type == "draft":
            return {"success": True, "email_id": pk_id}

        if email_type == "scheduled" and schedule_datetime:
            email.status = "scheduled"
            db.commit()
            db.refresh(email)
            return {"success": True, "email_id": pk_id}

        # ---- STEP 3: SEND THE EMAIL ----
        email_service = EmailService()
        email_model = EmailContent(
            sender=sender,
            to=recipients,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            html=(body_format == "html"),
            attachments=email_attachments
        )

        send_result = await email_service.send_email(email_model)

        # ---- STEP 4: Update status ----
        if send_result.get("status") == "Succeeded":
            email.status = "sent"

            # Set actual sent_datetime
            email.sent_datetime = datetime.now(timezone.utc)

        else:
            email.status = "failed"

        db.commit()
        db.refresh(email)

        return {
            "success": True,
            "email_id": pk_id,
            "status": email.status,
            "attachments": attachment_records,
            "email_response": send_result
        }

    except Exception as e:
        logger.exception("Error storing/sending email")

        # mark DB record failed if exception happened AFTER creating it
        try:
            email.status = "failed"
            db.commit()
        except:
            pass

        raise HTTPException(status_code=500, detail=str(e))
    
@router.get("/project-status/features/sub-tasks/email/{ticket_id}", dependencies=[Depends(security)])
async def get_sub_task_emails(
    ticket_id: UUID,
    db: Session = Depends(get_db)
):
    try:
        emails = (
            db.query(ProjectStatusEmailStore)
            .filter(ProjectStatusEmailStore.ticket_id == ticket_id)
            .order_by(
                ProjectStatusEmailStore.sent_datetime.desc(),
                ProjectStatusEmailStore.schedule_datetime.desc(),
                ProjectStatusEmailStore.pk_id.desc(),
            )
            .all()
        )

        def deserialize_email_list(value):
            """Convert email list from various formats to proper list"""
            if not value:
                return []
            if isinstance(value, list):
                return value
            if isinstance(value, str):
                cleaned = value.strip("{}[]")
                if cleaned:
                    return [e.strip() for e in cleaned.split(",") if e.strip()]
            return []
        
        results = []
        for email in emails:
            results.append(
                {
                    "pk_id": str(email.pk_id),
                    "sender": email.sender,
                    "recipients": deserialize_email_list(email.recipients),
                    "cc": deserialize_email_list(email.cc),
                    "bcc": deserialize_email_list(email.bcc),
                    "subject": email.subject,
                    "body": email.body,
                    "body_format": email.body_format,
                    "email_type": email.email_type,
                    "sent_datetime": email.sent_datetime,
                    "schedule_datetime": email.schedule_datetime,
                    "attachments": email.attachments,
                    "status": email.status,
                    "ticket_id": str(email.ticket_id),
                }
            )

        return {"count": len(results), "emails": results}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to fetch ticket email history", exc_info=exc)
        raise HTTPException(status_code=500, detail="Failed to fetch ticket emails")
    
