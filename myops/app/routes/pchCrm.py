import asyncio
from fastapi import (
    APIRouter,
    Depends,
    File,
    Query,
    HTTPException,
    Request,
    status,
    Form,
    UploadFile,
    Body,
)
from sqlalchemy.orm import aliased, selectinload, joinedload, Session, with_loader_criteria
from app.db.session import get_db
from app.models import Entity
from app.models import Carrier, CarrierShort
from app.models import npi_registry
from app.models import Users, UserPermissions
from app.models import (
    AddressType,
    CommunicationMedium,
    Ops_Pch_Logs,
    pch_attachments,
    pch_carrier_contracting,
    Pch_Carrier_Credentials,
    pch_sales,
    Pch_Affiliations,
    Pch_Audit_History,
    Pch_Caqh_Disclosures,
    Pch_Caqh_Education,
    Pch_Caqh_Hospitals,
    Pch_Caqh_Identifiers,
    Pch_Caqh_Insurance,
    Pch_Caqh_Malpractice_Claims,
    Pch_Caqh_Practice,
    Pch_Caqh_Practice_Accessibility,
    Pch_Caqh_Practice_Associates,
    Pch_Caqh_Practice_Hours,
    Pch_Caqh_Practice_Languages,
    Pch_Caqh_Practice_Limitations,
    Pch_Caqh_Practice_Patient_Acceptance,
    Pch_Caqh_Practice_Services,
    Pch_Caqh_Provider_Associates,
    Pch_Caqh_Provider_Info,
    Pch_Caqh_References,
    Pch_Caqh_Specialties,
    Pch_Caqh_Work_History,
    Pch_Networks,
    Pch_Notes,
    Pch_Provider_Address,
    Pch_Provider_Communication,
    Pch_Provider_Education,
    Pch_Provider_Identifiers,
    Pch_Provider_Info,
    Pch_Provider_Location,
    Pch_Regulatory_Fail_Details,
    Pch_Regulatory_Validation,
    Pch_Sales_Service,
    Sub_Entity
)
from app.schemas import CarrierCombinedSchema
from app.schemas import NpiRegistryLookupSchema
from app.schemas import (
    PchAuditHistorySchema,
    PchAuditHistoryCreateSchema,
    PchCaqhPracticeDropdownItem,
    PchCaqhPracticeDetailResponse,
    PchCaqhProviderInfoSchema,
    PchCarrierContractingCreateUpdateSchema,
    PchCarrierContractingSchema,
    PchCarrierCredentialsSchema,
    PchCarrierCredentialsCreateSchema,
    PchCarrierCredentialsUpdateSchema,
    PchNetworksSchema,
    PchNetworksCreateUpdateSchema,
    PchNotesCreateUpdateSchema,
    PchNotesSchema,
    ProviderAddressCreateSchema,
    ProviderAddressUpdateSchema,
    PchProviderEmailCreateRequest,
    PchProviderEmailUpdateRequest,
    PchProviderPhoneTextCreateRequest,
    PchProviderPhoneTextUpdateRequest,
    PchProviderInfoCreateSchema,
    PchProviderInfoSchema,
    PchRegulatoryValidationWithFailuresSchema,
    PchSalesCreateUpdateSchema,
)
from app.middleware.validator import get_current_user
from sqlalchemy import or_, func, and_, literal_column, distinct, text, desc, asc, select, update, cast, String 
from typing import List, Optional, Literal
from uuid import uuid4 as uuid, UUID
from datetime import datetime, timezone
from app.utils.pagination import paginate
from app.utils.pchCrmNotifyService import (
    notify_external_service,
    notify_external_service_batch,
    notify_external_refresh_service,
    notify_external_service_async,
)
from app.core.config import settings
from app.utils.attachment_utility import upload_blob_to_path

router = APIRouter(tags=["PCH-CRM ROUTES"])

def build_entity_tree(entities, default_entity_id=None, default_sub_entity_id=None):
    return [
        {
            "id": str(e.entity_id),
            "title": e.entity_name,
            "uuid": str(e.id),
            "affiliations": e.entity_affiliation,
            "isDefault": str(e.id) == str(default_entity_id),
            "children": [
                {
                    "id": str(s.sub_entity_id),
                    "title": (
                        f"{(s.sub_entity_fname or '').strip()} "
                        f"{(s.sub_entity_lname or '').strip()}"
                    ).strip() or "",
                    "uuid": str(s.id),
                    "isDefault": str(s.sub_entity_id) == str(default_sub_entity_id),
                    "children": []
                }
                for s in e.sub_entities
            ],
        }
        for e in entities
    ]

@router.get("/pch-crm/entity-filters")
async def get_permitted_entities(
    user: dict = Depends(get_current_user), db: Session = Depends(get_db)
):
    if user.get("role") == "admin":
        entities = (
            db.query(Entity)
            .filter(Entity.entity_active_status == 'active')
            .options(selectinload(Entity.sub_entities)) 
            .all()
        )

        return build_entity_tree(entities)
    
    row = (
        db.query(UserPermissions)
        .filter(UserPermissions.user_id == user.get("user_id"))
        .first()
    )

    permissions = row.entity_permissions if row else {}
    default_entity_id = row.default_entity_id if row else None
    default_sub_entity_id = row.default_sub_entity_id if row else None
    if permissions is None:
       return []
    assigned_entities = permissions.get("userEntities", [])
    entity_ids = []
    sub_entity_ids = []

    for ent in assigned_entities:
        entity_ids.append(ent["id"])
        for sub in ent.get("children", []):
            sub_entity_ids.append(sub["id"])
            
    entities = (
        db.query(Entity)
        .filter(
            Entity.entity_id.in_(entity_ids),
            Entity.entity_active_status == "active"
        )
        .options(
            selectinload(Entity.sub_entities),
            with_loader_criteria(
                Sub_Entity,
                Sub_Entity.sub_entity_id.in_(sub_entity_ids),
                include_aliases=True
            ),
        )
        .all()
    )
    result_entities = build_entity_tree(entities, default_entity_id=default_entity_id, default_sub_entity_id=default_sub_entity_id)
    return result_entities


@router.get("/pch-crm/state-filter")
async def get_state_filters(entity_id: str, sub_entity_id: str, db: Session = Depends(get_db)):
    states = (
        db.query(Pch_Provider_Info.state)
        .where(
            and_(
                Pch_Provider_Info.group_id == sub_entity_id,
                Pch_Provider_Info.company_id == entity_id,
                Pch_Provider_Info.state.isnot(None)
            )
        )
        .group_by(Pch_Provider_Info.state)
        .all()
    )
    states_list = [{"id": row[0], "value": row[0]} for row in states if row[0]]

    return states_list


@router.get("/pch-crm/supporting-filters")
async def get_supporting_filters(
    entityId: str = Query(..., alias="entity_id"),
    subEntityId: str = Query(..., alias="sub_entity_id"),
    state: Optional[str] = Query(None, alias="state"),
    status: Optional[str] = Query(None, alias="status"),
    db: Session = Depends(get_db),
):

    try:
        # Normalize state
        state_filter = None if not state else state
        status_filter = None if not status or status.lower() == "all" else status

        # Base query filters
        base_filters = [
            Pch_Provider_Info.company_id == entityId,
            Pch_Provider_Info.group_id == subEntityId
        ]
        if state_filter:
            base_filters.append(Pch_Provider_Info.state == state_filter)
        if status_filter:
            base_filters.append(Pch_Provider_Info.status == status_filter)

        # Query unique owners
        # owners = (
        #     db.query(
        #         distinct(Pch_Provider_Info.job_owner_email),
        #         Pch_Provider_Info.job_owner_name
        #     )
        #     .filter(
        #         *base_filters,
        #         Pch_Provider_Info.job_owner_email.isnot(None),
        #         # Pch_Provider_Info.job_owner_name.isnot(None)
        #     )
        #     .all()
        # )

        touch = (
            db.query(
                distinct(Pch_Provider_Info.touch_user)
            )
            .filter(
                *base_filters,
                Pch_Provider_Info.touch_user.isnot(None),
            )
            .all()
        )

        # Query unique statuses
        statuses = (
            db.query(distinct(Pch_Provider_Info.status))
            .filter(
                *base_filters,
                Pch_Provider_Info.status.isnot(None)
            )
            .all()
        )

        # Query unique statuses
        zips = (
            db.query(distinct(Pch_Provider_Info.zip))
            .filter(
                *base_filters,
                Pch_Provider_Info.zip.isnot(None)
            )
            .all()
        )

        # Format results
        owners_list = [] # [{"id": email, "value": name} for email, name in owners]
        touch_list = [{"id": t, "value": t} for (t,) in touch]
        statuses_list = [{"id": s, "value": s} for (s,) in statuses]
        zips_list = [{"id": z, "value": z} for (z,) in zips]

        return {
            "owners": owners_list,
            "touch": touch_list,
            "status": statuses_list,
            "zips": zips_list,
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            # detail=f"Database error: {str(e)}"
            detail="Failed to fetch pch supporting filters"
        )


@router.get("/pch-crm/npi-search")
async def search_npi(
    npiTerm: str = Query(..., alias="npi_term"),
    entity_id: str = Query(None),
    sub_entity_id: str = Query(None),
    state: Optional[str] = Query(None, alias="state"),
    owner: List[str] = Query(None),
    status: List[str] = Query(None),
    limit: int = Query(50, ge=1, le=200, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_db),
):

    try:
        # Handle empty or whitespace npiTerm
        if not npiTerm or npiTerm.strip() == "":
            return []

        state = None if state == "" else state

        # Query with only required columns
        query = db.query(
            Pch_Provider_Info.npi,
            Pch_Provider_Info.first_name,
            Pch_Provider_Info.last_name,
        )

        # Handle numeric npiTerm (search only on npi)
        if npiTerm.isdigit():
            query = query.filter(Pch_Provider_Info.npi.ilike(f"{npiTerm}%"))
        else:
            # Split search term for name searches
            parts = [part for part in npiTerm.split(" ") if part]
            if len(parts) == 2:
                # Search for first_name and last_name with prefix matching
                first_part, second_part = parts
                query = query.filter(
                    or_(
                        # Normal: first_name = part1, last_name = part2
                        (
                            Pch_Provider_Info.first_name.ilike(f"{first_part}%")
                            & Pch_Provider_Info.last_name.ilike(f"{second_part}%")
                        ),
                        # Reverse: first_name = part2, last_name = part1
                        (
                            Pch_Provider_Info.first_name.ilike(f"{second_part}%")
                            & Pch_Provider_Info.last_name.ilike(f"{first_part}%")
                        ),
                        Pch_Provider_Info.npi.ilike(f"%{npiTerm}%"),
                    )
                )
            else:
                # Single term: search first_name or last_name or npi
                term = npiTerm
                query = query.filter(
                    or_(
                        Pch_Provider_Info.first_name.ilike(f"{term}%"),
                        Pch_Provider_Info.last_name.ilike(f"{term}%"),
                        Pch_Provider_Info.npi.ilike(f"%{term}%"),
                    )
                )

        if entity_id:
            query = query.filter(Pch_Provider_Info.company_id == entity_id)
        if sub_entity_id:
            query = query.filter(Pch_Provider_Info.group_id == sub_entity_id)
        if state:
            query = query.filter(Pch_Provider_Info.state == state)
        if owner:
            owner_conditions = []
            if "null" in owner:
                owner_conditions.append(Pch_Provider_Info.job_owner_email.is_(None))
            non_null_owners = [o for o in owner if o != "null"]
            if non_null_owners:
                owner_conditions.append(
                    Pch_Provider_Info.job_owner_email.in_(non_null_owners)
                )
            if owner_conditions:
                query = query.filter(or_(*owner_conditions))
        if status:
            query = query.filter(
                Pch_Provider_Info.status.in_(list(filter(None, status)))
            )

        # Order for consistency and limit to 50
        query = query.order_by(
            Pch_Provider_Info.last_name, Pch_Provider_Info.first_name
        ).limit(limit).offset(offset)

        npis = query.all()

        npis_list = [
            {
                "id": row[0],
                "value": f"{row[0]} - {row[2] or ''}, {row[1] or ''}".strip(),
            }
            for row in npis
            if row[0]
        ]

        return npis_list
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}",
        )


@router.get("/pch-crm/provider-search")
async def search_provider(
    search_term: str = Query(..., alias="search_term"),
    limit: int = Query(50, ge=1, le=200, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_db),
):
    try:
        if not search_term or search_term.strip() == "":
            return []

        search_term = search_term.strip()

        # Base query (only required columns)
        query = db.query(
            Pch_Provider_Info.npi,
            Pch_Provider_Info.first_name,
            Pch_Provider_Info.last_name,
        )

        if search_term.isdigit():
            query = query.filter(
                Pch_Provider_Info.npi.ilike(f"{search_term}%")
            )
        else:
            parts = [part for part in search_term.split(" ") if part]

            if len(parts) == 2:
                first_part, second_part = parts
                query = query.filter(
                    or_(
                        # first last
                        (
                            Pch_Provider_Info.first_name.ilike(f"{first_part}%")
                            & Pch_Provider_Info.last_name.ilike(f"{second_part}%")
                        ),
                        # last first
                        (
                            Pch_Provider_Info.first_name.ilike(f"{second_part}%")
                            & Pch_Provider_Info.last_name.ilike(f"{first_part}%")
                        ),
                        # fallback partial match
                        # Pch_Provider_Info.npi.ilike(f"%{search_term}%"),
                    )
                )
            else:
                term = search_term
                query = query.filter(
                    or_(
                        Pch_Provider_Info.first_name.ilike(f"{term}%"),
                        Pch_Provider_Info.last_name.ilike(f"{term}%"),
                        # Pch_Provider_Info.npi.ilike(f"%{term}%"),
                    )
                )

        query = query.order_by(
            Pch_Provider_Info.last_name,
            Pch_Provider_Info.first_name
        ).limit(limit).offset(offset)

        results = query.all()

        return [
            {
                "id": row[0],
                "value": f"{row[0]} - {row[2] or ''}, {row[1] or ''}".strip(),
            }
            for row in results
            if row[0]
        ]

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}",
        )


@router.get("/pch-crm/providers/by-npi")
async def get_providers_by_npi(
    npi: List[str] = Query(..., description="List of NPI numbers to search"),
    db: Session = Depends(get_db),
):
    clean_npi = [x for x in npi if x and x.isdigit()]
    if not clean_npi:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid NPI provided."
        )

    stmt = select(Pch_Provider_Info).where(Pch_Provider_Info.npi.in_(clean_npi))
    results = db.execute(stmt).scalars().all()

    if not results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No providers found for the given NPI(s)."
        )

    return results


@router.get("/pch-crm/providers")
async def get_providers(
    entity_id: str,
    sub_entity_id: str,
    state: str,
    npi: List[str] = Query(None),
    owner: List[str] = Query(None),
    status: List[str] = Query(None),
    zip: List[str] = Query(None),
    touch: List[str] = Query(None),
    page: int = 1,
    page_size: int = 50,
    sort_column: str = "company_id",
    sort_order: str = "asc",
    db: Session = Depends(get_db),
):
    # Build filters once
    filters = [
        Pch_Provider_Info.company_id == entity_id,
        Pch_Provider_Info.group_id == sub_entity_id
    ]

    # State filtering
    if state:
        filters.append(Pch_Provider_Info.state == state)

    # Filter NPIs efficiently
    if npi:
        clean_npi = [x for x in npi if x]
        if clean_npi:
            filters.append(Pch_Provider_Info.npi.in_(clean_npi))

    # Owner filtering
    if owner:
        conditions = []
        if "null" in owner:
            conditions.append(Pch_Provider_Info.job_owner_email.is_(None))
        clean_owners = [x for x in owner if x and x != "null"]
        if clean_owners:
            conditions.append(Pch_Provider_Info.job_owner_email.in_(clean_owners))
        if conditions:
            filters.append(or_(*conditions))

    # Zip code filtering
    if zip:
        clean_zip = [x for x in zip if x]
        if clean_zip:
            filters.append(Pch_Provider_Info.zip.in_(clean_zip))

    # Touch filtering
    if touch:
        clean_touch = [x for x in touch if x]
        if clean_touch:
            filters.append(Pch_Provider_Info.touch_user.in_(clean_touch))

    # Status filtering
    if status:
        clean_status = [x for x in status if x]
        if clean_status:
            # If "All" is present → do NOT apply any status filter
            if "All" in clean_status:
                pass

            # If "Compliance" is present → include both Compliance and Deactivated
            elif "Compliance" in clean_status:
                filters.append(
                    Pch_Provider_Info.status.in_(["Compliance", "Deactivated"])
                )

            # Otherwise → use given values as-is
            else:
                filters.append(
                    Pch_Provider_Info.status.in_(clean_status)
                )

    # Base query (no .query() to avoid loading unnecessary ORM state)
    stmt = select(Pch_Provider_Info).where(and_(*filters))

    # Sorting with safe fallback
    if sort_column and hasattr(Pch_Provider_Info, sort_column):
        sort_attr = getattr(Pch_Provider_Info, sort_column)
        stmt = stmt.order_by(desc(sort_attr) if sort_order.lower() == "desc" else asc(sort_attr))
    else:
        stmt = stmt.order_by(Pch_Provider_Info.company_id.asc())

    # Total count query (efficient COUNT)
    total_count = db.execute(
        select(func.count()).select_from(
            select(Pch_Provider_Info.pk_id).where(and_(*filters)).subquery()
        )
    ).scalar()

    # Pagination
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    results = db.execute(stmt).scalars().all()

    return {
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "items": results,
    }


@router.get("/pch-crm/provider-locations")
def get_provider_locations(
    provider_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    sortOrder: str = Query("desc", regex="^(asc|desc)$"),
    sortColumn: str = Query("updated_on"),
):
    """
    Get latest distinct location per (type, location_name) for a provider.
    """
    name_clean = func.nullif(func.trim(Pch_Provider_Location.location_name), '')

    query = (
        db.query(Pch_Provider_Location)
        .filter(Pch_Provider_Location.txn_id_provider == provider_id)
        .filter(name_clean.is_not(None))
        .distinct(Pch_Provider_Location.type, name_clean)
        .order_by(
            Pch_Provider_Location.type,
            name_clean,
            desc(Pch_Provider_Location.updated_on)
        )
    )

    return paginate(
        query,
        db,
        model=Pch_Provider_Location,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )


@router.get("/pch-crm/provider-location-history")
async def ger_provider_location_history(
    provider_id: str,
    location_type: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
):
    """
    Fetch all location records for a given provider_id and type,
    with pagination and sorting.
    """

    query = db.query(Pch_Provider_Location).filter(
        Pch_Provider_Location.txn_id_provider == provider_id,
        Pch_Provider_Location.type == location_type,
    )

    paginated = paginate(
        query,
        db,
        model=Pch_Provider_Location,
        page=page,
        page_size=page_size,
        sort_column="updated_on",
        sort_order="desc",
    )

    return paginated


@router.get("/pch-crm/provider-identifiers")
def get_provider_identifiers(
    provider_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    sortOrder: str = Query("desc", regex="^(asc|desc)$"),
    sortColumn: str = Query("updated_on"),
):
    """
    Get latest distinct identifier per (id_type, id_type_value) for a provider.
    """
    value_clean = func.nullif(func.trim(Pch_Provider_Identifiers.id_type_value), '')

    query = (
        db.query(Pch_Provider_Identifiers)
        .filter(Pch_Provider_Identifiers.txn_id_provider == provider_id)
        .filter(value_clean.is_not(None))
        .distinct(Pch_Provider_Identifiers.id_type, value_clean)
        .order_by(
            Pch_Provider_Identifiers.id_type,
            value_clean,
            desc(Pch_Provider_Identifiers.updated_on)
        )
    )

    return paginate(
        query,
        db,
        model=Pch_Provider_Identifiers,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )


@router.get("/pch-crm/provider-identifier-history")
async def ger_provider_identifier_history(
    provider_id: str,
    identifier_type: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
):
    """
    Fetch all location records for a given provider_id and type,
    with pagination and sorting.
    """

    query = db.query(Pch_Provider_Identifiers).filter(
        Pch_Provider_Identifiers.txn_id_provider == provider_id,
        Pch_Provider_Identifiers.id_type == identifier_type,
    )

    paginated = paginate(
        query,
        db,
        model=Pch_Provider_Identifiers,
        page=page,
        page_size=page_size,
        sort_column="updated_on",
        sort_order="desc",
    )

    return paginated


@router.get("/pch-crm/provider-education")
def get_provider_education(
    provider_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    sortOrder: str = Query("desc", regex="^(asc|desc)$"),
    sortColumn: str = Query("school_program_name"),
):
    """
    Get latest distinct education record per (school_program_name, type, specialty).
    """
    school_clean = func.nullif(func.trim(Pch_Provider_Education.school_program_name), '')
    specialty_clean = func.nullif(func.trim(Pch_Provider_Education.specialty), '')

    query = (
        db.query(Pch_Provider_Education)
        .filter(Pch_Provider_Education.txn_id_provider == provider_id)
        .filter(school_clean.is_not(None))
        .distinct(
            Pch_Provider_Education.type,
            school_clean,
            specialty_clean
        )
        .order_by(
            Pch_Provider_Education.type,
            school_clean,
            specialty_clean,
            desc(Pch_Provider_Education.updated_on)
        )
    )

    return paginate(
        query,
        db,
        model=Pch_Provider_Education,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )


@router.get("/pch-crm/provider-education-history")
async def ger_provider_education_history(
    provider_id: str,
    education_type: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
):
    """
    Fetch all location records for a given provider_id and type,
    with pagination and sorting.
    """

    query = db.query(Pch_Provider_Education).filter(
        Pch_Provider_Education.txn_id_provider == provider_id,
        Pch_Provider_Education.type == education_type,
    )

    paginated = paginate(
        query,
        db,
        model=Pch_Provider_Education,
        page=page,
        page_size=page_size,
        sort_column="updated_on",
        sort_order="desc",
    )

    return paginated


@router.get("/pch-crm/regulatory-validation")
def get_regulatory_validation(
    provider_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    sortOrder: str = Query("desc", regex="^(asc|desc)$"),
    sortColumn: str = Query("source"),
):
    """
    Get latest regulatory validation per base_audit_id.
    - Handles UUID ↔ VARCHAR in BOTH txn_id AND txn_id_provider
    - Uses regexp_replace → no slicing
    - :$$: delimiter preserved
    - DISTINCT ON → 1 scan
    - Pydantic-safe: fail_description as string
    """
    # === 1. Extract base_audit_id: remove _UUID_TIMESTAMP suffix ===
    base_audit_id = func.regexp_replace(
        Pch_Regulatory_Validation.audit_id,
        r'_[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}_[0-9]{14}$',
        '',
        'i'  # case-insensitive
    ).label("base_audit_id")

    # === 2. Cast BOTH txn_id and txn_id_provider to TEXT ===
    val_txn_id = cast(Pch_Regulatory_Validation.txn_id, String)
    fail_txn_id = cast(Pch_Regulatory_Fail_Details.txn_id_reg, String)

    val_provider_id = cast(Pch_Regulatory_Validation.txn_id_provider, String)
    fail_provider_id = cast(Pch_Regulatory_Fail_Details.txn_id_provider, String)

    # === 3. Query with FULLY SAFE JOIN + DISTINCT ON ===
    query = (
        db.query(
            Pch_Regulatory_Validation.txn_id,
            Pch_Regulatory_Validation.audit_id,
            Pch_Regulatory_Validation.status,
            Pch_Regulatory_Validation.source,
            Pch_Regulatory_Validation.date_time,
            Pch_Regulatory_Validation.txn_id_provider,
            base_audit_id,
            func.coalesce(
                func.string_agg(Pch_Regulatory_Fail_Details.description, literal_column("':$$:'")),
                literal_column("''")
            ).label("fail_description")
        )
        .outerjoin(
            Pch_Regulatory_Fail_Details,
            and_(
                val_txn_id == fail_txn_id,
                # val_provider_id == fail_provider_id,
            )
        )
        .filter(
            val_provider_id == provider_id,
            Pch_Regulatory_Validation.date_time.is_not(None)
        )
        .group_by(
            Pch_Regulatory_Validation.txn_id,
            Pch_Regulatory_Validation.audit_id,
            Pch_Regulatory_Validation.status,
            Pch_Regulatory_Validation.source,
            Pch_Regulatory_Validation.date_time,
            Pch_Regulatory_Validation.txn_id_provider,
            base_audit_id
        )
        .distinct(base_audit_id)
        .order_by(
            base_audit_id,
            desc(Pch_Regulatory_Validation.date_time),
            desc(Pch_Regulatory_Validation.txn_id)
        )
    )

    total_count = db.query(func.count(func.distinct(base_audit_id))).filter(
        val_provider_id == provider_id,
        Pch_Regulatory_Validation.date_time.is_not(None)
    ).scalar()

    results = query.offset((page - 1) * page_size).limit(page_size).all()

    items = [
        PchRegulatoryValidationWithFailuresSchema(
            txn_id=str(row.txn_id),
            audit_id=row.audit_id,
            status=row.status,
            source=row.source,
            date_time=row.date_time,
            txn_id_provider=str(row.txn_id_provider),
            fail_description=row.fail_description,  # ← Keep as string
        )
        for row in results
    ]

    return {
        "items": items,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": (total_count + page_size - 1) // page_size,
    }


@router.get("/pch-crm/regulatory-validation-history")
async def ger_regulatiory_validation_history(
    provider_id: str,
    validation_type: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
):
    """
    Fetch all location records for a given provider_id and type,
    with pagination and sorting.
    """

    query = db.query(Pch_Regulatory_Validation).filter(
        Pch_Regulatory_Validation.txn_id_provider == provider_id,
        Pch_Regulatory_Validation.source == validation_type,
    )

    paginated = paginate(
        query,
        db,
        model=Pch_Regulatory_Validation,
        page=page,
        page_size=page_size,
        sort_column="date_time",
        sort_order="desc",
    )

    return paginated


@router.post("/pch-crm/regulatory-validation-check")
def regulatory_validation_check(
    txn_id_providers: List[str],
    db: Session = Depends(get_db),
):
    """
    Check regulatory validation status for a list of provider txn IDs.
    Returns JSON array with `id` and `value` (true if all latest validations passed per provider).
    """

    if not txn_id_providers:
        return []

    # Subquery: latest date_time per provider_id and source
    subq = (
        db.query(
            Pch_Regulatory_Validation.txn_id_provider.label("provider_id"),
            Pch_Regulatory_Validation.source.label("source"),
            func.max(Pch_Regulatory_Validation.date_time).label("latest_date"),
        )
        .filter(Pch_Regulatory_Validation.txn_id_provider.in_(txn_id_providers))
        .group_by(
            Pch_Regulatory_Validation.txn_id_provider, Pch_Regulatory_Validation.source
        )
        .subquery()
    )

    # Join to get the latest records per provider/source
    latest_records = (
        db.query(Pch_Regulatory_Validation)
        .join(
            subq,
            (Pch_Regulatory_Validation.txn_id_provider == subq.c.provider_id)
            & (Pch_Regulatory_Validation.source == subq.c.source)
            & (Pch_Regulatory_Validation.date_time == subq.c.latest_date),
        )
        .all()
    )

    FAIL_STATES = {"Fail", "Failed", "Reject", "Blocked", "Error", "Inactive"}
    # Group by provider_id and check if all latest statuses are "Pass"
    provider_status = {}
    for record in latest_records:
        provider = record.txn_id_provider
        if provider not in provider_status:
            provider_status[provider] = True  # assume all pass initially
        # if record.status == "Fail":
        if record.status in FAIL_STATES:
            provider_status[provider] = False

    # Format output
    output = [
        {"id": provider, "value": "true" if all_pass else "false"}
        for provider, all_pass in sorted(provider_status.items())
    ]

    return output


@router.get("/pch-crm/ops-logs/check-started")
async def check_ops_logs_started(
    txn_id_provider: str,
    db: Session = Depends(get_db),
):
    """
    Check if any ops_pch_logs record exists with given txn_id and status='STARTED'.
    Returns true if NO records exist, false if any record exists.
    """

    # Query to check if any record exists with txn_id and status='STARTED'
    exists = (
        db.query(Ops_Pch_Logs)
        .filter(
            Ops_Pch_Logs.txn_id == txn_id_provider, Ops_Pch_Logs.status == "STARTED"
        )
        .order_by(Ops_Pch_Logs.created_at.desc())
        .first()
        is not None
    )

    # Return true if NO records exist, false if any record exists
    return True if not exists else False


@router.get("/pch-crm/affiliation")
def get_affiliations(
    provider_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    sortOrder: str = Query("desc", regex="^(asc|desc)$"),
    sortColumn: str = Query("affiliate_name"),
):
    """
    Get latest distinct affiliation per affiliate_name.
    """
    name_clean = func.nullif(func.trim(Pch_Affiliations.affiliate_name), '')

    query = (
        db.query(Pch_Affiliations)
        .filter(Pch_Affiliations.txn_id_provider == provider_id)
        .filter(name_clean.is_not(None))
        .distinct(name_clean)
        .order_by(
            name_clean,
            desc(Pch_Affiliations.updated_on)
        )
    )

    return paginate(
        query,
        db,
        model=Pch_Affiliations,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )


@router.get("/pch-crm/affiliation-history")
async def ger_affiliation_history(
    provider_id: str,
    affiliate_name: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
):
    """
    Fetch all location records for a given provider_id and type,
    with pagination and sorting.
    """

    query = db.query(Pch_Affiliations).filter(
        Pch_Affiliations.txn_id_provider == provider_id,
        Pch_Affiliations.affiliate_name == affiliate_name,
    )

    paginated = paginate(
        query,
        db,
        model=Pch_Affiliations,
        page=page,
        page_size=page_size,
        sort_column="updated_on",
        sort_order="desc",
    )

    return paginated


@router.get("/pch-crm", response_model=PchProviderInfoSchema)
async def get_provider_details(
    txn_id: Optional[str],
    db: Session = Depends(get_db),
):
    query = (
        db.query(Pch_Provider_Info).filter(Pch_Provider_Info.txn_id == txn_id).first()
    )
    return query


@router.patch("/pch-crm", response_model=PchProviderInfoSchema)
async def update_provider_details(
    provider: PchProviderInfoSchema,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    existing_provider = (
        db.query(Pch_Provider_Info)
        .filter(Pch_Provider_Info.txn_id == provider.txn_id)
        .first()
    )
    if not existing_provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider not found"
        )
    
    # Step 1: Set touch_user to current user's email on the DB object
    if user.get("email"):
        existing_provider.touch_user = user["email"]

    update_data = provider.model_dump(exclude_none=True, exclude_unset=True,exclude={"touch_user"})
    for key, value in update_data.items():
        setattr(existing_provider, key, value)

    if provider.status in ["Prospect", "Active"]:
        asyncio.create_task(
            notify_external_service(
                provider.npi, provider.txn_id, provider.company_id, "ALL"
            )
        )

    # Step 4: Handle CAQH validation (if caqh_number is provided)
    if provider.caqh_number:
        # print(f"Validating CAQH ID: {provider.caqh_number} for NPI={existing_provider.npi}")

        response_data = await notify_external_service_async(
            npi=existing_provider.npi,
            txn_id=existing_provider.txn_id,
            company_id=existing_provider.company_id,
            module="CAQH",
            caqh_id=provider.caqh_number,
        )

        raw_status = response_data.get("status") or ""
        status_normalized = raw_status.strip().lower()

        # === Handle Known CAQH Errors with Simple String detail ===
        if status_normalized == "terminated":
            msg = response_data.get("message") or "Provider CAQH access terminated"
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=msg
            )

        if status_normalized == "invalid caqh id":
            msg = response_data.get("message") or "Invalid CAQH ID"
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=msg
            )

        if status_normalized == "caqh npi mismatch":
            caqh_npi = response_data.get("caqh_npi")
            caqh_id = response_data.get("caqh_id")
            msg = f"CAQH NPI mismatch: {existing_provider.npi} ≠ {caqh_npi} (CAQH ID: {caqh_id})"
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=msg
            )

        if status_normalized != "submitted":
            msg = response_data.get("message") or f"Unexpected CAQH status: {raw_status}"
            # raise HTTPException(
            #     status_code=status.HTTP_200_OK,
            #     detail=msg
            # )

        # If "submitted" → success, continue

    # Step 5: Commit and return
    db.commit()
    db.refresh(existing_provider)

    return existing_provider


@router.patch("/pch-crm/caqh")
async def update_provider_caqh_number(
    provider: PchProviderInfoSchema,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    existing_provider = (
        db.query(Pch_Provider_Info)
        .filter(Pch_Provider_Info.txn_id == str(provider.txn_id))
        .first()
    )
    if not existing_provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"data": None, "success": False, "message": "Provider not found"},
        )

    if user.get("email"):
        existing_provider.touch_user = user["email"]

    if not provider.caqh_number:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"data": None, "success": False, "message": "CAQH number is missing in request"},
        )

    response_data = await notify_external_service_async(
        npi=existing_provider.npi,
        txn_id=existing_provider.txn_id,
        company_id=existing_provider.company_id,
        module="CAQH",
        caqh_id=provider.caqh_number,
    )

    # print("CAQH Service Response:", response_data)

    raw_status = response_data.get("status") or ""
    status_normalized = raw_status.strip().lower()

    if status_normalized in ("submitted", "valid caqh and npi match — pull initiated"):
        existing_provider.caqh_number = provider.caqh_number
        db.commit()
        db.refresh(existing_provider)

        # print(f"Successfully updated CAQH number for NPI={existing_provider.npi}, txn_id={existing_provider.txn_id}")
        
        raise HTTPException(
            status_code=status.HTTP_200_OK,
            detail={
                "data": {"caqh_number": provider.caqh_number},
                "success": True,
                "message": "CAQH number updated successfully"
            },
        )

    error_message = response_data.get("message") or raw_status

    if status_normalized == "terminated":
        error_message = error_message or "Provider CAQH access terminated"
        # print(f"CAQH Terminated for NPI={existing_provider.npi}, CAQH ID={provider.caqh_number}")
        raise HTTPException(
            status_code=status.HTTP_200_OK,
            detail={"data": None, "success": False, "message": error_message},
        )

    if status_normalized == "invalid caqh id":
        error_message = error_message or "Invalid CAQH ID"
        # print(f"Invalid CAQH ID: {provider.caqh_number} for NPI={existing_provider.npi}")
        raise HTTPException(
            status_code=status.HTTP_200_OK,
            detail={"data": None, "success": False, "message": error_message},
        )

    if status_normalized == "caqh npi mismatch":
        our_npi = existing_provider.npi
        caqh_npi = response_data.get("caqh_npi")
        caqh_id = response_data.get("caqh_id")

        error_message = (
            f"CAQH NPI mismatch: Provider NPI {our_npi} does not match CAQH record NPI {caqh_npi} "
            f"for CAQH ID {caqh_id}"
        )
        # print(f"NPI MISMATCH → System: {our_npi}, CAQH: {caqh_npi}, CAQH ID: {caqh_id}")

        raise HTTPException(
            status_code=status.HTTP_200_OK,
            detail={
                "data": {
                    "npi_in_system": our_npi,
                    "npi_in_caqh": caqh_npi,
                    "caqh_id": caqh_id
                },
                "success": False,
                "message": error_message
            },
        )

    # print(f"Unexpected CAQH status: {raw_status}")
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "data": None,
            "success": False,
            "message": f"Unexpected CAQH status: {raw_status}"
        },
    )


@router.get("/pch-crm/carrier-contracts")
async def get_provider_carriers(
    txnIdProvider: str = Query(None),
    sortColumn: Optional[str] = Query("updated_on"),
    sortOrder: Optional[str] = Query("desc"),
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    if txnIdProvider:
        provider_exists = (
            db.query(Pch_Provider_Info)
            .filter(Pch_Provider_Info.txn_id == txnIdProvider)
            .first()
        )
        if not provider_exists:

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found"
            )

        # Authorization check
        if user.get("role") != "admin":
            permitted = (
                db.query(UserPermissions)
                .filter(
                    UserPermissions.user_id == user.get("user_id"),
                    UserPermissions.entity_permissions.contains(
                        provider_exists.company_id
                    ),
                )
                .first()
            )
            if not permitted:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to access this provider",
                )

    query = db.query(pch_carrier_contracting)

    # Apply filter only if txnIdProvider is provided
    if txnIdProvider:
        query = query.filter(pch_carrier_contracting.txn_id_provider == txnIdProvider)

    try:
        paginated = paginate(
            query,
            db,
            model=pch_carrier_contracting,
            page=page,
            page_size=page_size,
            sort_column=sortColumn,
            sort_order=sortOrder,
        )
        return paginated
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}",
        )


@router.post(
    "/pch-crm/carrier-contract", response_model=List[PchCarrierContractingSchema]
)
async def create_carrier(
    carriers: List[PchCarrierContractingCreateUpdateSchema],
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    created_carriers = []
    try:
        with db.begin():  # Start a transaction
            for carrier in carriers:
                # Validate txn_id_provider
                provider_exists = (
                    db.query(Pch_Provider_Info)
                    .filter(Pch_Provider_Info.txn_id == carrier.txn_id_provider)
                    .first()
                )
                if not provider_exists:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Provider not found for txn_id_provider: {carrier.txn_id_provider}",
                    )

                # Authorization check
                if user.get("role") != "admin":
                    permitted = (
                        db.query(UserPermissions)
                        .filter(
                            UserPermissions.user_id == user.get("user_id"),
                            UserPermissions.entity_permissions.contains(
                                provider_exists.company_id
                            ),
                        )
                        .first()
                    )
                    if not permitted:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"Not authorized to create carrier contracting for provider: {carrier.txn_id_provider}",
                        )

                # Create new carrier record
                new_carrier = pch_carrier_contracting(
                    txn_id=str(uuid()),
                    txn_id_provider=carrier.txn_id_provider,
                    status=carrier.status,
                    carrier_id=carrier.carrier_id,
                    carrier_name=carrier.carrier_name,
                    carrier_product=carrier.carrier_product,
                    carrier_status=carrier.carrier_status,
                    is_credentialing=carrier.is_credentialing,
                    created_on=datetime.now(timezone.utc),
                    updated_on=datetime.now(timezone.utc),
                )
                db.add(new_carrier)
                db.flush()
                db.refresh(new_carrier)
                created_carriers.append(new_carrier)

        return created_carriers
    except HTTPException as e:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}",
        )


@router.get("/pch-crm/sales-services")
async def get_sales_services(
    serviceStatus: Optional[str] = Query(None, alias="service_status"),
    serviceName: Optional[str] = Query(None, alias="service_name"),
    serviceType: Optional[str] = Query(None, alias="service_type"),
    searchTerm: Optional[str] = Query(None, alias="search_term"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=500, alias="page_size"),
    sortColumn: Optional[str] = Query("updated_on", alias="sort_column"),
    sortOrder: Optional[str] = Query("desc", alias="sort_order"),
    db: Session = Depends(get_db),
):
    try:
        serviceStatus = None if serviceStatus == "" else serviceStatus
        serviceName = None if serviceName == "" else serviceName
        serviceType = None if serviceType == "" else serviceType
        searchTerm = None if searchTerm == "" else searchTerm

        valid_sort_columns = [
            "service_status",
            "service_name",
            "service_type",
            "service_rate",
            "created_on",
            "updated_on",
        ]
        if sortColumn not in valid_sort_columns:
            sortColumn = "updated_on"

        sortOrder = sortOrder.lower()
        if sortOrder not in ["asc", "desc"]:
            sortOrder = "desc"

        query = db.query(Pch_Sales_Service).where(
            Pch_Sales_Service.service_status == "ACTIVE"
        )

        if serviceStatus:
            query = query.filter(
                Pch_Sales_Service.service_status.ilike(f"%{serviceStatus}%")
            )
        if serviceName:
            query = query.filter(
                Pch_Sales_Service.service_name.ilike(f"%{serviceName}%")
            )
        if serviceType:
            query = query.filter(
                Pch_Sales_Service.service_type.ilike(f"%{serviceType}%")
            )
        if searchTerm:
            query = query.filter(
                or_(
                    Pch_Sales_Service.service_description.contains(searchTerm),
                    Pch_Sales_Service.service_price_desc.contains(searchTerm),
                )
            )

        paginated = paginate(
            query,
            db,
            model=Pch_Sales_Service,
            page=page,
            page_size=pageSize,
            sort_column=sortColumn,
            sort_order=sortOrder,
        )

        return paginated
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}",
        )


@router.get("/pch-crm/sales")
async def get_sales(
    txnIdProvider: str = Query(None),
    sortColumn: Optional[str] = Query("updated_on"),
    sortOrder: Optional[str] = Query("desc"),
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    if txnIdProvider:
        provider_exists = (
            db.query(Pch_Provider_Info)
            .filter(Pch_Provider_Info.txn_id == txnIdProvider)
            .first()
        )
        if not provider_exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found"
            )

        # Authorization check
        if user.get("role") != "admin":
            permitted = (
                db.query(UserPermissions)
                .filter(
                    UserPermissions.user_id == user.get("user_id"),
                    UserPermissions.entity_permissions.contains(
                        provider_exists.company_id
                    ),
                )
                .first()
            )
            if not permitted:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to access this provider",
                )

    query = db.query(pch_sales)

    # Apply filter only if txnIdProvider is provided
    if txnIdProvider:
        query = query.filter(pch_sales.txn_id_provider == txnIdProvider)

    try:
        paginated = paginate(
            query,
            db,
            model=pch_sales,
            page=page,
            page_size=page_size,
            sort_column=sortColumn,
            sort_order=sortOrder,
        )
        return paginated
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}",
        )


@router.post("/pch-crm/sales")
async def create_sale(
    sales: List[PchSalesCreateUpdateSchema],
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    created_sales = []
    try:
        with db.begin():  # Start a transaction
            for sale in sales:
                provider_exists = (
                    db.query(Pch_Provider_Info)
                    .filter(Pch_Provider_Info.txn_id == sale.txn_id_provider)
                    .first()
                )
                if not provider_exists:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Provider not found for txn_id_provider: {sale.txn_id_provider}",
                    )

                if user.get("role") != "admin":
                    permitted = (
                        db.query(UserPermissions)
                        .filter(
                            UserPermissions.user_id == user.get("user_id"),
                            UserPermissions.entity_permissions.contains(
                                provider_exists.company_id
                            ),
                        )
                        .first()
                    )
                    if not permitted:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"Not authorized to create sales record for provider: {sale.txn_id_provider}",
                        )

                new_sale = pch_sales(
                    txn_id=str(uuid()),
                    txn_id_provider=sale.txn_id_provider,
                    pch_header_txn=sale.pch_header_txn,
                    service_status=sale.service_status,
                    service_name=sale.service_name,
                    service_type=sale.service_type,
                    service_description=sale.service_description,
                    service_rate_type=sale.service_rate_type,
                    service_rate=sale.service_rate,
                    service_price_desc=sale.service_price_desc,
                    created_on=datetime.now(timezone.utc),
                    updated_on=datetime.now(timezone.utc),
                )
                db.add(new_sale)
                db.flush()
                db.refresh(new_sale)
                created_sales.append(new_sale)

        return created_sales
    except HTTPException as e:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}",
        )


@router.delete("/pch-crm/sales")
async def delete_sale(
    txn_id: str = Query(..., alias="txn_id"),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    try:
        with db.begin():
            sale = db.query(pch_sales).filter(pch_sales.txn_id == txn_id).first()
            if not sale:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Sales record not found for txn_id: {txn_id}",
                )

            provider_exists = (
                db.query(Pch_Provider_Info)
                .filter(Pch_Provider_Info.txn_id == sale.txn_id_provider)
                .first()
            )
            if not provider_exists:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Provider not found for txn_id_provider: {sale.txn_id_provider}",
                )

            # Authorization check
            if user.get("role") != "admin":
                permitted = (
                    db.query(UserPermissions)
                    .filter(
                        UserPermissions.user_id == user.get("user_id"),
                        UserPermissions.entity_permissions.contains(
                            provider_exists.company_id
                        ),
                    )
                    .first()
                )
                if not permitted:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Not authorized to delete sales record for provider: {sale.txn_id_provider}",
                    )

            # Delete the record
            db.delete(sale)
            db.flush()

        return {"detail": "Sales record deleted successfully"}
    except HTTPException as e:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}",
        )


@router.get("/pch-crm/networks")
async def get_crm_networks(
    provider: Optional[str] = Query(None),
    affiliation: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    sortColumn: Optional[str] = Query("created_at"),
    sortOrder: Optional[str] = Query("desc"),
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    query = db.query(Pch_Networks)

    if provider:
        query = query.filter(Pch_Networks.provider == provider)

    if affiliation:
        query = query.filter(Pch_Networks.affiliation == affiliation)

    if status:
        query = query.filter(Pch_Networks.status == status)

    paginated = paginate(
        query,
        db,
        model=Pch_Networks,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )

    items = []
    for row in paginated["items"]:
        item_dict = dict(row.__dict__)
        item_dict.pop("_sa_instance_state", None)

        sub_entity = (
            db.query(Sub_Entity)
            .filter(Sub_Entity.sub_entity_id == row.affiliation)
            .first()
        )

        if sub_entity:
            first = sub_entity.sub_entity_fname
            last = sub_entity.sub_entity_lname
            item_dict["affiliation_name"] = (first + " " + last).strip()
        else:
            item_dict["affiliation_name"]

        items.append(item_dict)

    paginated["items"] = items
    return paginated


@router.post("/pch-crm/networks")
async def create_network(
    network: PchNetworksCreateUpdateSchema,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    new_network = Pch_Networks(
        pk_id=str(uuid()),
        provider=network.provider,
        affiliation=network.affiliation,
        status=network.status or "Active",
    )

    try:
        db.add(new_network)
        db.flush()
        db.refresh(new_network)
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider and affiliation combination already exists",
        )

    return new_network

@router.patch("/pch-crm/networks")
async def update_network(
    network_update: PchNetworksSchema,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    current_network = (
        db.query(Pch_Networks)
        .filter(Pch_Networks.pk_id == network_update.pk_id)
        .first()
    )

    if not current_network:
        raise HTTPException(status_code=404, detail="Network not found")

    if network_update.status:
        current_network.status = network_update.status

    if network_update.provider:
        current_network.provider = network_update.provider

    if network_update.affiliation:
        current_network.affiliation = network_update.affiliation

    current_network.updated_at = datetime.now(timezone.utc)

    try:
        db.flush()
        db.refresh(current_network)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider and affiliation combination already exists",
        )

    return current_network


@router.get("/pch-crm/notes")
async def get_crm_notes(
    reference_id: Optional[str] = Query(None),
    sortColumn: Optional[str] = Query("date_time"),  # default sorting column
    sortOrder: Optional[str] = Query("desc"),  # default sort order
    module: Optional[str] = Query(None),
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    query = db.query(Pch_Notes, Users.f_name, Users.l_name).join(
        Users, Users.email == Pch_Notes.login
    ).filter(Pch_Notes.module == module)

    # Apply filter only if txnIdProvider is provided
    if reference_id:
        query = query.filter(Pch_Notes.reference_id == reference_id)

    paginated = paginate(
        query,
        db,
        model=Pch_Notes,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )

    items = []
    for row in paginated["items"]:
        attachment_obj, f_name, l_name = row
        item_dict = dict(attachment_obj.__dict__)  # convert SQLAlchemy object to dict
        item_dict["full_name"] = f_name + " " + l_name
        # Remove SQLAlchemy internal keys
        item_dict.pop("_sa_instance_state", None)
        items.append(item_dict)

    paginated["items"] = items

    return paginated


@router.post("/pch-crm/notes") #, response_model=PchNotesSchema
async def create_note(
    note: PchNotesCreateUpdateSchema,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    new_note = Pch_Notes(
        txn_id=str(uuid()),
        note_type=note.note_type,
        notes_context=note.notes_context,
        reference_id=note.reference_id,
        module=note.module,
        date_time=datetime.now(timezone.utc).strftime("%m/%d/%Y %I:%M:%S %p"),
        login=user["email"],
    )
    db.add(new_note)
    db.flush()
    db.refresh(new_note)
    return new_note


@router.patch("/pch-crm/notes") #, response_model=PchNotesSchema
async def update_note(
    note_update: PchNotesSchema,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    current_note = (
        db.query(Pch_Notes).filter(Pch_Notes.txn_id == note_update.txn_id).first()
    )
    if not current_note:
        raise HTTPException(status_code=404, detail="Note not found")

    # Authorization check
    if current_note.login != user["email"] and user.get("role") != "admin":
        raise HTTPException(
            status_code=403, detail="Not authorized to update this note"
        )

    current_note.date_time = datetime.now(timezone.utc).strftime("%m/%d/%Y %I:%M:%S %p")
    current_note.note_type = note_update.note_type
    current_note.notes_context = note_update.notes_context

    db.flush()
    db.refresh(current_note)
    return current_note


@router.delete("/pch-crm/notes")
async def delete_note(
    txn_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    note = db.query(Pch_Notes).filter(Pch_Notes.txn_id == txn_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    # Authorization check
    if note.login != user["email"] and user.get("role") != "admin":
        raise HTTPException(
            status_code=403, detail="Not authorized to delete this note"
        )

    db.delete(note)
    db.flush()
    return {"detail": "Note deleted successfully"}


@router.get("/pch-crm/attachements")
async def get_attachements(
    txnIdProvider: Optional[str] = Query(None),
    sortColumn: Optional[str] = Query("path"),  # default sorting column
    sortOrder: Optional[str] = Query("desc"),  # default sort order
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    query = db.query(pch_attachments, Users.f_name, Users.l_name).join(
        Users, Users.email == pch_attachments.login
    )

    # Apply filter only if txnIdProvider is provided
    if txnIdProvider:
        query = query.filter(pch_attachments.txn_id_provider == txnIdProvider)

    paginated = paginate(
        query,
        db,
        model=pch_attachments,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )

    items = []
    for row in paginated["items"]:
        attachment_obj, f_name, l_name = row
        item_dict = dict(attachment_obj.__dict__)  # convert SQLAlchemy object to dict
        item_dict["full_name"] = f_name + " " + l_name
        # Remove SQLAlchemy internal keys
        item_dict.pop("_sa_instance_state", None)
        items.append(item_dict)

    paginated["items"] = items

    return paginated


@router.post("/pch-crm/attachements")
async def upload_file(
    txn_id_provider: str = Form(...),
    description: Optional[str] = Form(None),
    npi: str = Form(...),
    file_name: str = Form(...),
    file: Optional[UploadFile] = File(None),
    file_type: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    print("FILE RECEIVED:", file, type(file))
    txn_id = str(uuid())

    # Directory-style path
    directory = f"Documents/{txn_id_provider}/{npi}/"

    # Final blob path (full blob "key")
    blob_path = f"{directory}{file_name}"

    data = await file.read()

    meta = await upload_blob_to_path(blob_path, data)

    # Save metadata in DB
    attachment = pch_attachments(
        txn_id=txn_id,
        path=blob_path,  # save full path
        description=description,
        date_time=datetime.now(timezone.utc).strftime("%m/%d/%Y %I:%M:%S %p"),
        login=user["email"],
        owner_name=user["unique_name"],
        txn_id_provider=txn_id_provider,
        file_type=file_type,
    )
    db.add(attachment)
    db.flush()

    return {"success": True, "message": f"File '{file_name}' uploaded successfully."}


@router.delete("/pch-crm/attachments")
async def delete_attachment(
    txn_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    # Find attachment in DB
    attachment = (
        db.query(pch_attachments).filter(pch_attachments.txn_id == txn_id).first()
    )

    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    # Authorization check
    if attachment.login != user["email"] and user.get("role") != "admin":
        raise HTTPException(
            status_code=403, detail="Not authorized to delete this attachment"
        )

    # Delete from Blob Storage
    try:
        blob_client = settings.blob_service_client.get_blob_client(
            container=settings.ATTACHMENT_CONTAINER_NAME,
            blob=attachment.path,  # path column should have the blob name
        )
        blob_client.delete_blob()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Blob deletion failed: {str(e)}")

    # Delete from DB
    db.delete(attachment)
    db.commit()  # commit instead of flush to persist change

    return {"detail": "Attachment deleted successfully"}

@router.get(
    "/pch-crm/audit-history",
    summary="List PCH audit history",
)
def list_pch_audit_history(
    txn_id: Optional[str] = Query(None),
    user_email: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    module: Optional[str] = Query(None),
    sortColumn: str = Query("created_at"),
    sortOrder: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(Pch_Audit_History)

    if txn_id:
        query = query.filter(Pch_Audit_History.txn_id == txn_id)

    if user_email:
        query = query.filter(Pch_Audit_History.user_email == user_email)

    if action:
        query = query.filter(Pch_Audit_History.action == action)

    if module:
        query = query.filter(Pch_Audit_History.module == module)

    return paginate(
        query,
        db,
        model=Pch_Audit_History,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )

@router.post(
    "/pch-crm/audit-history",
    response_model=PchAuditHistorySchema,
    summary="Create PCH audit history entry"
)
def create_pch_audit_history(
    request : Request,
    audit_data: PchAuditHistoryCreateSchema,
    db: Session = Depends(get_db),
):
    user = request.state.user
    audit_entry = Pch_Audit_History(
        # audit_id=uuid(),
        txn_id=audit_data.txn_id,
        user_email=user['email'],
        user_id=user['id'],
        action_message=audit_data.action_message,
        action=audit_data.action,
        tab=audit_data.tab,
        module=audit_data.module,
        sub_module=audit_data.sub_module,
        entity_id=audit_data.entity_id,
        sub_entity_id=audit_data.sub_entity_id,
    )

    db.add(audit_entry)
    db.commit()
    db.refresh(audit_entry)

    return audit_entry

@router.post(
    "/pch-crm/audit-history/bulk",
    response_model=List[PchAuditHistorySchema],
    summary="Bulk create PCH audit history entries",
)
def bulk_create_pch_audit_history(
    request: Request,
    audit_entries: List[PchAuditHistoryCreateSchema],
    db: Session = Depends(get_db),
):
    user = request.state.user
    user_email = user["email"]
    user_id = user["id"]

    db_objects = []

    for item in audit_entries:
        entry = Pch_Audit_History(
            txn_id=item.txn_id,
            user_email=user_email,
            user_id=user_id,
            action_message=item.action_message,
            action=item.action,
            tab=item.tab,
            module=item.module,
            sub_module=item.sub_module,
        )
        db_objects.append(entry)

    if db_objects:
        db.add_all(db_objects)
        db.commit()
        for obj in db_objects:
            db.refresh(obj)

    return db_objects

@router.get("/pch-crm/provider/summary")
async def get_provider_summary(
    txn_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Full provider summary with smart deduplication:
    - Regulatory: latest per base_audit_id
    - Locations: latest per (type, location_name)
    - Identifiers: latest per (id_type, id_type_value)
    - Education: latest per (school, type, specialty)
    - Affiliations: latest per affiliate_name
    """
    if not txn_id:
        return None

    # === 1. Provider Info ===
    provider_info = db.query(Pch_Provider_Info).filter(Pch_Provider_Info.txn_id == txn_id).first()
    if not provider_info:
        return None

    # === 2. base_audit_id: remove _UUID_TIMESTAMP ===
    base_audit_id = func.regexp_replace(
        Pch_Regulatory_Validation.audit_id,
        r'_[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}_[0-9]{14}$',
        '',
        'i'
    ).label("base_audit_id")

    # === 3. Cast UUID/VARCHAR fields ===
    val_txn_id = cast(Pch_Regulatory_Validation.txn_id, String)
    fail_txn_id = cast(Pch_Regulatory_Fail_Details.txn_id_reg, String)
    val_provider_id = cast(Pch_Regulatory_Validation.txn_id_provider, String)
    fail_provider_id = cast(Pch_Regulatory_Fail_Details.txn_id_provider, String)

    # === 4. DISTINCT ON: latest per base_audit_id ===
    regulatory_query = (
        db.query(
            Pch_Regulatory_Validation.txn_id,
            Pch_Regulatory_Validation.audit_id,
            Pch_Regulatory_Validation.status,
            Pch_Regulatory_Validation.source,
            Pch_Regulatory_Validation.date_time,
            Pch_Regulatory_Validation.txn_id_provider,
            base_audit_id,
            func.coalesce(
                func.string_agg(Pch_Regulatory_Fail_Details.description, literal_column("':$$:'")),
                literal_column("''")
            ).label("fail_description")
        )
        .outerjoin(
            Pch_Regulatory_Fail_Details,
            and_(
                val_txn_id == fail_txn_id,
                # val_provider_id == fail_provider_id,
            )
        )
        .filter(
            val_provider_id == txn_id,
            Pch_Regulatory_Validation.date_time.is_not(None)
        )
        .group_by(
            Pch_Regulatory_Validation.txn_id,
            Pch_Regulatory_Validation.audit_id,
            Pch_Regulatory_Validation.status,
            Pch_Regulatory_Validation.source,
            Pch_Regulatory_Validation.date_time,
            Pch_Regulatory_Validation.txn_id_provider,
            base_audit_id
        )
        .distinct(base_audit_id)
        .order_by(
            base_audit_id,
            desc(Pch_Regulatory_Validation.date_time),
            desc(Pch_Regulatory_Validation.txn_id)
        )
        .all()
    )

    # === 5. Convert to list of dicts ===
    regulatory_validations_list = [
        {
            "txn_id": str(row.txn_id),
            "audit_id": row.audit_id,
            "status": row.status,
            "source": row.source,
            "date_time": row.date_time,
            "txn_id_provider": str(row.txn_id_provider),
            "fail_description": row.fail_description or "",
        }
        for row in regulatory_query
    ]

    # === 3. Locations: latest per (type, location_name) ===
    loc_name_clean = func.nullif(func.trim(Pch_Provider_Location.location_name), '')
    locations_query = (
        db.query(Pch_Provider_Location)
        .filter(Pch_Provider_Location.txn_id_provider == txn_id)
        .filter(loc_name_clean.is_not(None))
        .distinct(Pch_Provider_Location.type, loc_name_clean)
        .order_by(Pch_Provider_Location.type, loc_name_clean, desc(Pch_Provider_Location.updated_on))
        .all()
    )

    # === 4. Identifiers: latest per (id_type, id_type_value) ===
    id_value_clean = func.nullif(func.trim(Pch_Provider_Identifiers.id_type_value), '')
    identifiers_query = (
        db.query(Pch_Provider_Identifiers)
        .filter(Pch_Provider_Identifiers.txn_id_provider == txn_id)
        .filter(id_value_clean.is_not(None))
        .distinct(Pch_Provider_Identifiers.id_type, id_value_clean)
        .order_by(Pch_Provider_Identifiers.id_type, id_value_clean, desc(Pch_Provider_Identifiers.updated_on))
        .all()
    )

    # === 5. Education: latest per (school_program_name, type, specialty) ===
    school_clean = func.nullif(func.trim(Pch_Provider_Education.school_program_name), '')
    specialty_clean = func.nullif(func.trim(Pch_Provider_Education.specialty), '')
    education_query = (
        db.query(Pch_Provider_Education)
        .filter(Pch_Provider_Education.txn_id_provider == txn_id)
        .filter(school_clean.is_not(None))
        .distinct(Pch_Provider_Education.type, school_clean, specialty_clean)
        .order_by(
            Pch_Provider_Education.type,
            school_clean,
            specialty_clean,
            desc(Pch_Provider_Education.updated_on)
        )
        .all()
    )

    # === 6. Affiliations: latest per affiliate_name ===
    aff_name_clean = func.nullif(func.trim(Pch_Affiliations.affiliate_name), '')
    affiliations_query = (
        db.query(Pch_Affiliations)
        .filter(Pch_Affiliations.txn_id_provider == txn_id)
        .filter(aff_name_clean.is_not(None))
        .distinct(aff_name_clean)
        .order_by(aff_name_clean, desc(Pch_Affiliations.updated_on))
        .all()
    )

    # carriers = db.query(Pch_Carriers).filter(Pch_Carriers.txn_id_provider == txn_id).all()

    # === 7. Return ===
    return {
        **provider_info.__dict__,
        "regulatory_validation": regulatory_validations_list,
        "locations": [loc.__dict__ for loc in locations_query],
        "identifiers": [id.__dict__ for id in identifiers_query],
        "education": [edu.__dict__ for edu in education_query],
        "affiliations": [aff.__dict__ for aff in affiliations_query],
        # "carriers": [cr.__dict__ for cr in carriers]
    }


@router.post("/pch-crm/provider")
async def create_provider(
    providers: PchProviderInfoCreateSchema | List[PchProviderInfoCreateSchema],
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    # Single insert
    if isinstance(providers, PchProviderInfoCreateSchema):
        existing_stmt = select(Pch_Provider_Info.txn_id).where(
            Pch_Provider_Info.npi == providers.npi
        )

        existing_provider = db.execute(existing_stmt).first()

        if existing_provider:
            return {
                "message": "Provider with this NPI already exists."
            }

        new_provider = Pch_Provider_Info(
            **providers.model_dump(exclude_unset=True),
            txn_id=str(uuid()),  # generate UUID
            status="Prospect"
        )

        if user.get("email"):
            new_provider.touch_user =user.get("email"),

        db.add(new_provider)
        db.commit()
        db.refresh(new_provider)

        # Fire & forget notification
        asyncio.create_task(
            notify_external_service(
                new_provider.npi,
                new_provider.txn_id,
                new_provider.company_id,
                "SINGLE",
                "",
            )
        )

        return new_provider

    # Bulk insert
    elif isinstance(providers, list):

        incoming_npis = [p.npi for p in providers if p.npi]

        if not incoming_npis:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No valid NPI provided."
            )

        existing_stmt = select(
            Pch_Provider_Info.npi,
            Pch_Provider_Info.txn_id,
            Pch_Provider_Info.company_id
        ).where(Pch_Provider_Info.npi.in_(incoming_npis))

        existing_records = db.execute(existing_stmt).all()
        existing_npis = set(record[0] for record in existing_records)
        existing_info = {record[0]: {"txn_id": record[1], "company_id": record[2], "status": record[3]} 
                        for record in existing_records}

        # Update existing records with status='Prospect'
        for npi, info in existing_info.items():
            if info["status"] != "Prospect":
                db.execute(
                    update(Pch_Provider_Info)
                    .where(Pch_Provider_Info.npi == npi)
                    .values(status="Prospect", touch_user=user.get("email"))
                )

        new_providers = [
            Pch_Provider_Info(
                **p.model_dump(exclude_unset=True),
                txn_id=str(uuid()),  # generate UUID for each
                touch_user=user.get("email"),
                status="Prospect"
            )
            for p in providers
            if p.npi not in existing_npis
        ]

        parent_txn_id = str(uuid())

        if new_providers:
            db.add_all(new_providers)

        db.commit()

        for p in new_providers:
            db.refresh(p)

        all_npi_info = []

        all_npi_info.extend([
            (p.txn_id, p.npi, p.company_id)
            for p in new_providers
        ])

        for npi in existing_npis:
            txn_id, company_id = existing_info[npi]["txn_id"], existing_info[npi]["company_id"]
            all_npi_info.append((txn_id, npi, company_id))

        # Fire & forget notification for batch
        asyncio.create_task(notify_external_service_batch(parent_txn_id, all_npi_info))

        if not new_providers:
            return {
                "message": "All provided NPIs already exist (status updated if needed).",
                "skipped_npis": list(existing_npis),
            }

        return {
            "inserted_count": len(new_providers),
            "skipped_count": len(existing_npis),
            "skipped_npis": list(existing_npis),
            "data": new_providers,
        }



@router.post("/pch-crm/provider/refresh")
async def refresh_data(
    npi: str, txn_id: str, company_id: str, module: str, caqh_number: str
):
    # Fire & forget (doesn’t block request)
    asyncio.create_task(
        notify_external_refresh_service(npi, txn_id, company_id, module)
    )

    if caqh_number:
        asyncio.create_task(
            notify_external_service(npi, txn_id, company_id, "CAQH", caqh_number)
        )

    return {"status": 1}


@router.get("/pch-crm/provider/npi_search")
async def get_npi_lookup_search(npi_term: str, db: Session = Depends(get_db)):
    query = db.query(
        npi_registry.npi,
        npi_registry.provider_first_name,
        npi_registry.provider_last_name,
    )

    # Search by NPI (partial match, case-insensitive)
    query = query.filter(
        or_(
            npi_registry.npi.ilike(f"%{npi_term}%"),
            npi_registry.provider_first_name.ilike(f"%{npi_term}%"),
            npi_registry.provider_last_name.ilike(f"%{npi_term}%"),
        )
    )

    npis = query.limit(100).all()

    npis_list = [
        {"id": row[0], "value": f"{row[0]} - {row[1]}, {row[2]}".strip()}
        for row in npis
        if row[0]
    ]

    return npis_list


@router.get("/pch-crm/npi-lookup", response_model=NpiRegistryLookupSchema)
async def get_npi_lookup(npi: str, db: Session = Depends(get_db)):
    query = db.query(
        npi_registry.npi,
        npi_registry.entity_type_code,
        npi_registry.provider_last_name,
        npi_registry.provider_first_name,
        npi_registry.provider_business_practice_location_address_city_name,
        npi_registry.provider_business_practice_location_address_state_name,
        npi_registry.provider_business_practice_location_address_postal_code,
    )

    query = query.filter(npi_registry.npi == npi)

    npi_info = query.first()

    return npi_info


@router.put("/pch-crm/assign-unassign-provider")
async def assign_unassign_provider(
    provider_ids: List[str] = Body(..., embed=True),
    flag: Optional[Literal["assign", "unassign"]] = Query("assign"),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    # providers = db.query(Pch_Provider_Info.npi, Pch_Provider_Info.job_owner_email, Pch_Provider_Info.job_owner_name).filter(Pch_Provider_Info.npi.in_(provider_ids)).all()

    if flag == "assign":
        update_values = {
            "job_owner_email": user.get("email"),
            "job_owner_name": user.get("unique_name"),
            "updated_on": datetime.now(timezone.utc),
        }
    else:  # unassign
        update_values = {
            "job_owner_email": None,
            "job_owner_name": None,
            "updated_on": datetime.now(timezone.utc),
        }

    # Perform the update
    update_stmt = (
        Pch_Provider_Info.__table__.update()
        .where(Pch_Provider_Info.npi.in_(provider_ids))
        .values(**update_values)
    )
    db.execute(update_stmt)
    db.commit()

    # Fetch the updated providers to return them
    updated_providers = (
        db.query(Pch_Provider_Info)
        .filter(Pch_Provider_Info.npi.in_(provider_ids))
        .all()
    )

    return updated_providers


@router.get("/pch-crm/carriers/short")
async def get_carriers_short(
    searchTerm: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    # Authorization check
    if user.get("role") != "admin":
        pass

    query = db.query(CarrierShort.id, CarrierShort.carrier_short_name.label("value"))

    if searchTerm:
        query = query.filter(
            or_(
                CarrierShort.id.ilike(f"%{searchTerm}%"),
                CarrierShort.vendor_name.ilike(f"%{searchTerm}%"),
                CarrierShort.carrier_short_name.ilike(f"%{searchTerm}%"),
            )
        )

    # Order by carrier_short_name and limit to 50
    query = query.order_by(CarrierShort.carrier_short_name.asc()).limit(50)

    try:
        results = query.all()
        return [{"id": r.id, "value": r.value} for r in results]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}",
        )


@router.get("/pch-crm/carrier/{id}", response_model=CarrierCombinedSchema)
async def get_carrier_by_id(
    id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    # Authorization check (allow admin or any authenticated user for simplicity, adjust as needed)
    if user.get("role") != "admin":
        pass

    # Build the query with a LEFT JOIN
    query = (
        db.query(Carrier, CarrierShort)
        .outerjoin(CarrierShort, Carrier.id == CarrierShort.id)
        .filter(Carrier.id == id)
    )

    try:
        result = query.first()
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Carrier not found"
            )

        carrier, carrier_short = result
        combined = {
            "id": carrier.id,
            "vendor_name": carrier.vendor_name,
            "market": carrier.market,
            "state_availability": carrier.state_availability,
            "modified_time": carrier.modified_time,
            "short_vendor_name": carrier_short.vendor_name if carrier_short else None,
            "carrier_short_name": (
                carrier_short.carrier_short_name if carrier_short else None
            ),
            "prefix": carrier_short.prefix if carrier_short else None,
            "acu_file_name_prefix": (
                carrier_short.acu_file_name_prefix if carrier_short else None
            ),
            "bob_file_name_prefix": (
                carrier_short.bob_file_name_prefix if carrier_short else None
            ),
            "writing_num_flag": (
                carrier_short.writing_num_flag if carrier_short else None
            ),
            "pch_agreement": carrier_short.pch_agreement if carrier_short else None,
            "delegated_cred": carrier_short.delegated_cred if carrier_short else None,
        }
        return combined
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}",
        )


@router.get("/pch-crm/carriers-combined")
async def get_carriers_combined(
    vendorName: Optional[str] = Query(None),
    market: Optional[str] = Query(None),
    searchTerm: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sortColumn: Optional[str] = Query("vendor_name"),
    sortOrder: Optional[str] = Query("asc"),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    # Authorization check
    if user.get("role") != "admin":
        # Add custom permission logic here if needed
        pass

    # Validate sortColumn
    valid_sort_columns = [
        "id",
        "vendor_name",
        "market",
        "state_availability",
        "modified_time",
    ]
    if sortColumn not in valid_sort_columns:
        sortColumn = "vendor_name"

    # Validate sortOrder
    sortOrder = sortOrder.lower()
    if sortOrder not in ["asc", "desc"]:
        sortOrder = "asc"

    # Build the query with a LEFT JOIN
    query = db.query(Carrier, CarrierShort).outerjoin(
        CarrierShort, Carrier.id == CarrierShort.id
    )

    query = query.filter(CarrierShort.pch_agreement == "True")

    # Apply filters
    if vendorName:
        query = query.filter(Carrier.vendor_name.ilike(f"%{vendorName}%"))
    if market:
        query = query.filter(Carrier.market.ilike(f"%{market}%"))
    if searchTerm:
        query = query.filter(
            or_(
                Carrier.id.ilike(f"%{searchTerm}%"),
                Carrier.vendor_name.ilike(f"%{searchTerm}%"),
                CarrierShort.carrier_short_name.ilike(f"%{searchTerm}%"),
            )
        )

    try:
        paginated = paginate(
            query,
            db,
            model=Carrier,  # Use Carrier as the primary model for sorting
            page=page,
            page_size=page_size,
            sort_column=sortColumn,
            sort_order=sortOrder,
        )
        # Transform results to match CarrierCombinedSchema
        paginated["items"] = [
            {
                "id": carrier.id,
                "vendor_name": carrier.vendor_name,
                "market": carrier.market,
                "state_availability": carrier.state_availability,
                "modified_time": carrier.modified_time,
                "short_vendor_name": (
                    carrier_short.vendor_name if carrier_short else None
                ),
                "carrier_short_name": (
                    carrier_short.carrier_short_name if carrier_short else None
                ),
                "prefix": carrier_short.prefix if carrier_short else None,
                "acu_file_name_prefix": (
                    carrier_short.acu_file_name_prefix if carrier_short else None
                ),
                "bob_file_name_prefix": (
                    carrier_short.bob_file_name_prefix if carrier_short else None
                ),
                "writing_num_flag": (
                    carrier_short.writing_num_flag if carrier_short else None
                ),
                "pch_agreement": carrier_short.pch_agreement if carrier_short else None,
                "delegated_cred": (
                    carrier_short.delegated_cred if carrier_short else None
                ),
            }
            for carrier, carrier_short in paginated["items"]
        ]
        return paginated
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}",
        )


@router.get("/pch-crm/provider-caqh-disclosures")
def get_caqh_disclosures(
    provider_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sortColumn: str = Query("updated_on"),
    sortOrder: str = Query("desc", regex="^(asc|desc)$"),
):
    """
    Get latest CAQH disclosure record per `disclosure_id` for a given provider.
    """
    query = (
        db.query(Pch_Caqh_Disclosures)
        .filter(Pch_Caqh_Disclosures.txn_id_provider == provider_id)
        .order_by(
            Pch_Caqh_Disclosures.disclosure_id,
            Pch_Caqh_Disclosures.updated_on.desc()
        )
        .distinct(Pch_Caqh_Disclosures.disclosure_id)
    )

    return paginate(
        query,
        db,
        model=Pch_Caqh_Disclosures,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )


@router.get("/pch-crm/provider-caqh-education")
def get_caqh_education(
    provider_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sortColumn: str = Query("updated_on"),
    sortOrder: str = Query("desc", regex="^(asc|desc)$"),
):
    # Normalize: '' → NULL for grouping
    specialty_clean = func.nullif(Pch_Caqh_Education.specialty, '')
    type_clean = func.nullif(Pch_Caqh_Education.type, '')

    # Base query: filter by provider
    base_query = db.query(Pch_Caqh_Education).filter(
        Pch_Caqh_Education.txn_id_provider == provider_id
    )

    # Use DISTINCT ON to get latest per (program_name, type, specialty_clean)
    query = (
        base_query
        .distinct(Pch_Caqh_Education.program_name, type_clean, specialty_clean)
        .order_by(
            Pch_Caqh_Education.program_name,
            type_clean,
            specialty_clean,
            Pch_Caqh_Education.updated_on.desc()
        )
    )

    return paginate(
        query,
        db,
        model=Pch_Caqh_Education,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )


@router.get("/pch-crm/provider-caqh-hospitals")
def get_caqh_hospitals(
    provider_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sortColumn: str = Query("updated_on"),
    sortOrder: str = Query("desc", regex="^(asc|desc)$"),
):
    """
    Get latest CAQH hospital record per `hospital_name` for a provider.
    - Handles NULL/empty names safely
    """
    # Normalize: '' → NULL for consistent grouping
    name_clean = func.nullif(func.trim(Pch_Caqh_Hospitals.hospital_name), '')

    query = (
        db.query(Pch_Caqh_Hospitals)
        .filter(Pch_Caqh_Hospitals.txn_id_provider == provider_id)
        .filter(name_clean.is_not(None))  # Skip empty/invalid names
        .distinct(name_clean)
        .order_by(
            name_clean,
            Pch_Caqh_Hospitals.updated_on.desc()
        )
    )

    return paginate(
        query,
        db,
        model=Pch_Caqh_Hospitals,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )


@router.get("/pch-crm/provider-caqh-identifiers")
def get_caqh_identifiers(
    provider_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    sortColumn: str = Query("updated_on"),
    sortOrder: str = Query("desc", regex="^(asc|desc)$"),
):
    # Normalize: '' → NULL for grouping
    state_clean = func.nullif(Pch_Caqh_Identifiers.state, '')

    # Base query: filter by provider
    base_query = db.query(Pch_Caqh_Identifiers).filter(
        Pch_Caqh_Identifiers.txn_id_provider == provider_id
    )

    # Use DISTINCT ON to get latest per (id_type, state_clean)
    query = (
        base_query
        .distinct(Pch_Caqh_Identifiers.id_type, state_clean)
        .order_by(
            Pch_Caqh_Identifiers.id_type,
            state_clean,
            Pch_Caqh_Identifiers.updated_on.desc()
        )
    )

    return paginate(
        query,
        db,
        model=Pch_Caqh_Identifiers,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )


@router.get("/pch-crm/provider-caqh-insurance")
def get_caqh_insurance(
    provider_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    sortColumn: str = Query("start_date"),
    sortOrder: str = Query("desc", regex="^(asc|desc)$"),
):
    """
    Get latest CAQH insurance per (carrier_name, start_date).
    - paginate() unchanged
    - ORM subquery + DISTINCT ON
    """
    # === 1. Subquery with DISTINCT ON ===
    subq = text("""
        SELECT DISTINCT ON (
            nullif(trim(carrier_name), ''),
            start_date
        )
            txn_id
        FROM wpo.pch_caqh_insurance
        WHERE txn_id_provider = :provider_id
          AND nullif(trim(carrier_name), '') IS NOT NULL
          AND start_date IS NOT NULL
        ORDER BY
            nullif(trim(carrier_name), ''),
            start_date,
            updated_on DESC
    """).bindparams(provider_id=provider_id).columns(Pch_Caqh_Insurance.txn_id)

    # === 2. Alias the subquery ===
    subq_alias = aliased(Pch_Caqh_Insurance, subq.subquery())

    # === 3. Main query: join on txn_id ===
    base_query = db.query(Pch_Caqh_Insurance).filter(
        Pch_Caqh_Insurance.txn_id.in_(db.query(subq_alias.txn_id))
    )

    # === 4. Use paginate() — NO CHANGE ===
    return paginate(
        query=base_query,
        db=db,
        model=Pch_Caqh_Insurance,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )


@router.get("/pch-crm/provider-caqh-malpractice-claims")
def get_caqh_malpractice_claims(
    provider_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sortColumn: str = Query("updated_on"),
    sortOrder: str = Query("desc", regex="^(asc|desc)$"),
):
    """
    Latest malpractice claim per (npi, disclosure_id, policy_number).
    - NULL and empty strings ('') are treated as the same value in grouping
    - Uses PostgreSQL DISTINCT ON → fastest deduplication
    """

    npi_clean = func.nullif(func.trim(Pch_Caqh_Malpractice_Claims.npi), '')
    disclosure_clean = func.nullif(func.trim(Pch_Caqh_Malpractice_Claims.disclosure_id), '')
    policy_clean = func.nullif(func.trim(Pch_Caqh_Malpractice_Claims.policy_number), '')

    query = (
        db.query(Pch_Caqh_Malpractice_Claims)
        .filter(Pch_Caqh_Malpractice_Claims.txn_id_provider == provider_id)
        .filter(npi_clean.is_not(None))
        .distinct(npi_clean, disclosure_clean, policy_clean)
        .order_by(
            npi_clean,
            disclosure_clean,
            policy_clean,
            Pch_Caqh_Malpractice_Claims.updated_on.desc()
        )
    )

    return paginate(
        query,
        db,
        model=Pch_Caqh_Malpractice_Claims,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )

@router.get(
    "/pch-crm/provider-caqh-practices/dropdown",
    response_model=List[PchCaqhPracticeDropdownItem],
    summary="Get distinct practice dropdown list",
    description="Returns {id: practice_uid, value: 'Name - UID'} filtered by provider_id or npi"
)
def get_caqh_practice_dropdown(
    provider_id: Optional[str] = Query(None, description="Filter by txn_id_provider"),
    npi: Optional[str] = Query(None, description="Filter by NPI"),
    db: Session = Depends(get_db),
):
    """
    Get distinct practice dropdown items.
    Requires either `provider_id` or `npi`.
    - Trims names in DB
    - Uses `DISTINCT ON (practice_uid)` for safety
    - Orders by cleaned name
    """
    if not provider_id and not npi:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'provider_id' or 'npi' is required."
        )

    # Normalize: trim name, nullif empty → NULL
    name_clean = func.nullif(func.trim(Pch_Caqh_Practice.practice_name), '')

    # Base query: select only needed columns
    query = db.query(
        Pch_Caqh_Practice.practice_uid,
        name_clean.label("practice_name")
    ).filter(
        Pch_Caqh_Practice.practice_uid.is_not(None),
        name_clean.is_not(None)
    )

    if npi:
        query = query.filter(Pch_Caqh_Practice.npi == npi)
    if provider_id:
        query = query.filter(Pch_Caqh_Practice.txn_id_provider == provider_id)

    # Use DISTINCT ON (practice_uid) to ensure one row per UID
    # Order by name for consistent dropdown
    results = (
        query.distinct(Pch_Caqh_Practice.practice_uid)
        .order_by(Pch_Caqh_Practice.practice_uid, name_clean)
        .all()
    )

    return [
        PchCaqhPracticeDropdownItem(
            id=row.practice_uid,
            value=f"{row.practice_name} ( {row.practice_uid} )"
        )
        for row in results
    ]


@router.get(
    "/pch-crm/provider-caqh-practices/{practice_uid}",
    response_model=PchCaqhPracticeDetailResponse,
    summary="Get full practice detail by practice_uid",
    description="Returns latest practice + latest distinct child records (except limitations: single latest)"
)
def get_caqh_practice_detail(
    practice_uid: str,
    db: Session = Depends(get_db),
):
    """
    Get complete practice profile by `practice_uid`.
    - Uses PostgreSQL `DISTINCT ON` → fastest
    - All child tables: latest per business key
    - `limitations`: single latest record
    """
    # === 1. Latest Practice (single record) ===
    practice = (
        db.query(Pch_Caqh_Practice)
        .filter(Pch_Caqh_Practice.practice_uid == practice_uid)
        .distinct(Pch_Caqh_Practice.practice_uid)
        .order_by(Pch_Caqh_Practice.practice_uid, Pch_Caqh_Practice.updated_on.desc())
        .first()
    )
    if not practice:
        raise HTTPException(status_code=404, detail="Practice not found")

    # === 2. Helper: DISTINCT ON with normalization ===
    def latest_distinct(model, *group_cols):
        # Normalize string columns: trim + '' → NULL
        normalized = [
            func.nullif(func.trim(col), '') if col.type.python_type == str else col
            for col in group_cols
        ]
        return (
            db.query(model)
            .filter(model.practice_uid == practice_uid)
            .filter(*[col.is_not(None) for col in normalized])
            .distinct(*normalized)
            .order_by(*normalized, model.updated_on.desc())
            .all()
        )

    # === 3. Child Tables ===
    accessibility = latest_distinct(
        Pch_Caqh_Practice_Accessibility,
        Pch_Caqh_Practice_Accessibility.accessibility,
        Pch_Caqh_Practice_Accessibility.accessibility_flag
    )

    associates = latest_distinct(
        Pch_Caqh_Practice_Associates,
        Pch_Caqh_Practice_Associates.first_name,
        Pch_Caqh_Practice_Associates.last_name,
        Pch_Caqh_Practice_Associates.middle_initial,
        Pch_Caqh_Practice_Associates.relationship
    )

    hours = latest_distinct(
        Pch_Caqh_Practice_Hours,
        Pch_Caqh_Practice_Hours.day,
        Pch_Caqh_Practice_Hours.start_time,
        Pch_Caqh_Practice_Hours.end_time,
        Pch_Caqh_Practice_Hours.hours_type
    )

    languages = latest_distinct(
        Pch_Caqh_Practice_Languages,
        Pch_Caqh_Practice_Languages.language,
        Pch_Caqh_Practice_Languages.type,
        Pch_Caqh_Practice_Languages.employee_type
    )

    patient_acceptance = latest_distinct(
        Pch_Caqh_Practice_Patient_Acceptance,
        Pch_Caqh_Practice_Patient_Acceptance.patient_type
    )

    services = latest_distinct(
        Pch_Caqh_Practice_Services,
        Pch_Caqh_Practice_Services.service_name,
        Pch_Caqh_Practice_Services.provided_flag
    )

    # === 4. Limitations: single latest only ===
    limitations = (
        db.query(Pch_Caqh_Practice_Limitations)
        .filter(Pch_Caqh_Practice_Limitations.practice_uid == practice_uid)
        .distinct(Pch_Caqh_Practice_Limitations.practice_uid)
        .order_by(Pch_Caqh_Practice_Limitations.practice_uid, Pch_Caqh_Practice_Limitations.updated_on.desc())
        .first()
    )
    limitations = [limitations] if limitations else []

    # === 5. Return ===
    return PchCaqhPracticeDetailResponse(
        practice=practice,
        practice_accessibility=accessibility,
        practice_associates=associates,
        practice_hours=hours,
        practice_languages=languages,
        practice_limitations=limitations,
        practice_patient_acceptance=patient_acceptance,
        practice_services=services,
    )


@router.get("/pch-crm/provider-caqh-provider-associates")
def get_caqh_provider_associates(
    provider_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sortColumn: str = Query("updated_on"),
    sortOrder: str = Query("desc", regex="^(asc|desc)$"),
):
    """
    Get latest CAQH provider associate per `last_name` (normalized).
    - Uses PostgreSQL `DISTINCT ON` → fastest
    - Normalizes: trim + '' → NULL
    """
    # Normalize last_name: trim + empty → NULL
    name_clean = func.nullif(func.trim(Pch_Caqh_Provider_Associates.last_name), '')

    query = (
        db.query(Pch_Caqh_Provider_Associates)
        .filter(Pch_Caqh_Provider_Associates.txn_id_provider == provider_id)
        .filter(name_clean.is_not(None))  # Skip invalid names
        .distinct(name_clean)
        .order_by(
            name_clean,
            Pch_Caqh_Provider_Associates.updated_on.desc()
        )
    )

    return paginate(
        query,
        db,
        model=Pch_Caqh_Provider_Associates,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )


@router.get(
    "/pch-crm/provider-caqh-provider-info",
    response_model=PchCaqhProviderInfoSchema,
    summary="Get latest CAQH provider info",
    description="Returns the single most recent CAQH provider info record"
)
def get_caqh_provider_info(
    provider_id: str = Query(..., description="Provider ID (txn_id_provider)"),
    db: Session = Depends(get_db),
):
    """
    Get latest CAQH provider info using PostgreSQL-optimized query.
    """
    record = (
        db.query(Pch_Caqh_Provider_Info)
        .filter(Pch_Caqh_Provider_Info.txn_id_provider == provider_id)
        .order_by(Pch_Caqh_Provider_Info.updated_on.desc())
        .limit(1)
        .one_or_none()
    )

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No CAQH provider info found for this provider"
        )

    return record


@router.get("/pch-crm/provider-caqh-references")
def get_caqh_references(
    provider_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sortColumn: str = Query("updated_on"),
    sortOrder: str = Query("desc", regex="^(asc|desc)$"),
):
    """
    Get latest CAQH reference per `last_name` (normalized).
    - Uses PostgreSQL `DISTINCT ON` → fastest
    - Normalizes: trim + '' → NULL
    """
    # Normalize last_name: trim + empty → NULL
    name_clean = func.nullif(func.trim(Pch_Caqh_References.last_name), '')

    query = (
        db.query(Pch_Caqh_References)
        .filter(Pch_Caqh_References.txn_id_provider == provider_id)
        .filter(name_clean.is_not(None))  # Skip invalid names
        .distinct(name_clean)
        .order_by(
            name_clean,
            Pch_Caqh_References.updated_on.desc()
        )
    )

    return paginate(
        query,
        db,
        model=Pch_Caqh_References,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )


@router.get("/pch-crm/provider-caqh-specialties")
def get_caqh_specialties(
    provider_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sortColumn: str = Query("updated_on"),
    sortOrder: str = Query("desc", regex="^(asc|desc)$"),
):
    """
    Get latest CAQH specialty per `specialty_name` (normalized).
    - Uses PostgreSQL `DISTINCT ON` → fastest
    - Normalizes: trim + '' → NULL
    """
    # Normalize specialty_name: trim + empty → NULL
    name_clean = func.nullif(func.trim(Pch_Caqh_Specialties.specialty_name), '')

    query = (
        db.query(Pch_Caqh_Specialties)
        .filter(Pch_Caqh_Specialties.txn_id_provider == provider_id)
        .filter(name_clean.is_not(None))  # Skip invalid names
        .distinct(name_clean)
        .order_by(
            name_clean,
            Pch_Caqh_Specialties.updated_on.desc()
        )
    )

    return paginate(
        query,
        db,
        model=Pch_Caqh_Specialties,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )


@router.get("/pch-crm/provider-caqh-work-history")
def get_caqh_work_history(
    provider_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sortColumn: str = Query("updated_on"),
    sortOrder: str = Query("desc", regex="^(asc|desc)$"),
):
    """
    Get latest CAQH work history per `employer_name` using PostgreSQL `DISTINCT ON`.
    - Treats NULL, '', '  ' as same group
    """
    name_clean = func.nullif(func.trim(Pch_Caqh_Work_History.employer_name), '')

    query = (
        db.query(Pch_Caqh_Work_History)
        .filter(Pch_Caqh_Work_History.txn_id_provider == provider_id)
        .filter(name_clean.is_not(None))
        .distinct(name_clean)
        .order_by(
            name_clean,
            Pch_Caqh_Work_History.updated_on.desc()
        )
    )

    return paginate(
        query,
        db,
        model=Pch_Caqh_Work_History,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )


@router.get("/providers/communication-types/email")
def get_provider_email_communication_types(db: Session = Depends(get_db)):
    email_types = db.query(
        CommunicationMedium.pk_id,
        CommunicationMedium.sub_type
    ).filter(
        func.lower(CommunicationMedium.type) == "email"
    ).all()

    return [
        {
            "label": email_type.sub_type,
            "value": str(email_type.pk_id)
        }
        for email_type in email_types
    ]

@router.get("/providers/communication-types/phone")
def get_provider_phone_communication_types(db: Session = Depends(get_db)):
    phone_types = db.query(
        CommunicationMedium.pk_id,
        CommunicationMedium.sub_type
    ).filter(
        func.lower(CommunicationMedium.type) == "phone"
    ).all()

    return [
        {
            "label": phone_type.sub_type,
            "value": str(phone_type.pk_id)
        }
        for phone_type in phone_types
    ]

@router.get("/providers/{provider_id}/emails")
def get_provider_emails(provider_id: str, db: Session = Depends(get_db)):
    provider = db.query(Pch_Provider_Info).filter(Pch_Provider_Info.txn_id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    email_communications = (
        db.query(
            Pch_Provider_Communication.pk_id,
            CommunicationMedium.pk_id.label("communication_id"),
            CommunicationMedium.type.label("comm_type"),
            CommunicationMedium.sub_type.label("comm_sub_type"),
            Pch_Provider_Communication.value,
            Pch_Provider_Communication.marketing_opt_in,
            Pch_Provider_Communication.primary
        )
        .join(
            CommunicationMedium,
            CommunicationMedium.pk_id == Pch_Provider_Communication.communication_id
        )
        .filter(
            Pch_Provider_Communication.provider_id == provider_id,
            func.lower(CommunicationMedium.type) == "email"
        )
        .all()
    )

    emails = [
        {
            "pk_id": str(email.pk_id),
            "communication_id": str(email.communication_id),
            "type": email.comm_sub_type,
            "email": email.value,
            "marketing_opt_in": email.marketing_opt_in,
            "primary": email.primary
        }
        for email in email_communications
    ]

    return emails

@router.post("/providers/{provider_id}/emails")
def create_provider_email(
    provider_id: str,
    email_data: PchProviderEmailCreateRequest,
    db: Session = Depends(get_db)
):
    provider = db.query(Pch_Provider_Info).filter(Pch_Provider_Info.txn_id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    comm_medium = db.query(CommunicationMedium).filter(
        func.lower(CommunicationMedium.sub_type) == func.lower(email_data.communication_type)
    ).first()

    if not comm_medium:
        raise HTTPException(status_code=400, detail=f"Invalid communication_type: {email_data.communication_type}")

    existing_email_count = db.query(Pch_Provider_Communication).join(
        CommunicationMedium,
        Pch_Provider_Communication.communication_id == CommunicationMedium.pk_id
    ).filter(
        Pch_Provider_Communication.provider_id == provider_id,
        func.lower(CommunicationMedium.type) == "email"
    ).count()

    if existing_email_count == 0:
        email_data.primary = True

    if email_data.primary:
        email_comm_ids = [
            row[0] for row in db.query(CommunicationMedium.pk_id).filter(
                func.lower(CommunicationMedium.type) == "email"
            ).all()
        ]

        db.query(Pch_Provider_Communication).filter(
            Pch_Provider_Communication.provider_id == provider_id,
            Pch_Provider_Communication.communication_id.in_(email_comm_ids)
        ).update({Pch_Provider_Communication.primary: False}, synchronize_session=False)

    new_email = Pch_Provider_Communication(
        provider_id=provider_id,
        communication_id=comm_medium.pk_id,
        value=email_data.value,
        marketing_opt_in=email_data.marketing_opt_in,
        primary=email_data.primary
    )

    db.add(new_email)
    db.commit()
    db.refresh(new_email)

    return {
        "message": "Email created successfully",
        "pk_id": str(new_email.pk_id)
    }

@router.patch("/providers/{provider_id}/emails")
def update_provider_emails(
    provider_id: str,
    email_data: PchProviderEmailUpdateRequest,
    db: Session = Depends(get_db)
):
    provider = db.query(Pch_Provider_Info).filter(Pch_Provider_Info.txn_id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    email_record = db.query(Pch_Provider_Communication).filter(
        Pch_Provider_Communication.pk_id == email_data.pk_id,
        Pch_Provider_Communication.provider_id == provider_id
    ).first()

    if not email_record:
        raise HTTPException(status_code=404, detail="Email record not found")

    if email_data.communication_type is not None:
        comm_medium = db.query(CommunicationMedium).filter(
            func.lower(CommunicationMedium.sub_type) == func.lower(email_data.communication_type)
        ).first()

        if not comm_medium:
            raise HTTPException(status_code=400, detail=f"Invalid communication_type: {email_data.communication_type}")

        email_record.communication_id = comm_medium.pk_id

    if email_data.value is not None:
        email_record.value = email_data.value

    if email_data.marketing_opt_in is not None:
        email_record.marketing_opt_in = email_data.marketing_opt_in

    if email_data.primary is not None:
        if email_data.primary:
            other_emails = db.query(Pch_Provider_Communication.pk_id).join(
                CommunicationMedium,
                Pch_Provider_Communication.communication_id == CommunicationMedium.pk_id
            ).filter(
                Pch_Provider_Communication.provider_id == provider_id,
                Pch_Provider_Communication.pk_id != email_data.pk_id,
                func.lower(CommunicationMedium.type) == "email"
            ).all()

            if other_emails:
                email_ids = [email[0] for email in other_emails]
                db.query(Pch_Provider_Communication).filter(
                    Pch_Provider_Communication.pk_id.in_(email_ids)
                ).update({"primary": False}, synchronize_session=False)

        email_record.primary = email_data.primary

    db.commit()

    return {
        "message": "Email communication updated successfully",
        "pk_id": str(email_data.pk_id)
    }

@router.delete("/providers/emails/{pk_id}")
def delete_provider_email(
    pk_id: str,
    db: Session = Depends(get_db)
):
    email_record = db.query(Pch_Provider_Communication).filter(
        Pch_Provider_Communication.pk_id == pk_id
    ).first()

    if not email_record:
        raise HTTPException(status_code=404, detail="Email record not found")

    provider_id = email_record.provider_id

    db.delete(email_record)
    db.commit()

    email_comm_ids = [
        row[0] for row in db.query(CommunicationMedium.pk_id).filter(
            func.lower(CommunicationMedium.type) == "email"
        ).all()
    ]

    remaining_emails = db.query(Pch_Provider_Communication).filter(
        Pch_Provider_Communication.provider_id == provider_id,
        Pch_Provider_Communication.communication_id.in_(email_comm_ids)
    ).all()

    if len(remaining_emails) == 1 and not remaining_emails[0].primary:
        remaining_emails[0].primary = True
        db.commit()

    return {
        "message": "Email communication deleted successfully",
        "pk_id": str(pk_id)
    }

@router.get("/providers/{provider_id}/phone-text")
def get_provider_phone_text(provider_id: str, db: Session = Depends(get_db)):
    provider = db.query(Pch_Provider_Info.txn_id).filter(
        Pch_Provider_Info.txn_id == provider_id
    ).first()

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    communications = (
        db.query(
            Pch_Provider_Communication.pk_id,
            CommunicationMedium.pk_id.label("communication_id"),
            CommunicationMedium.sub_type,
            Pch_Provider_Communication.value,
            Pch_Provider_Communication.extension,
            Pch_Provider_Communication.text_opt,
            Pch_Provider_Communication.dnd,
            Pch_Provider_Communication.ai_pre_recording,
            Pch_Provider_Communication.primary
        )
        .join(
            CommunicationMedium,
            Pch_Provider_Communication.communication_id == CommunicationMedium.pk_id
        )
        .filter(
            Pch_Provider_Communication.provider_id == str(provider.txn_id),
            func.lower(CommunicationMedium.type) == "phone"
        )
        .all()
    )

    return [
        {
            "pk_id": str(c.pk_id),
            "communication_id": str(c.communication_id),
            "type": c.sub_type,
            "phone": c.value,
            "extension": c.extension,
            "text_opt_in": c.text_opt,
            "do_not_call": c.dnd,
            "ai_pre_recording_opt_in": c.ai_pre_recording,
            "primary": c.primary
        }
        for c in communications
    ]

@router.post("/providers/{provider_id}/phone-text")
def create_provider_phone_text(
    provider_id: str,
    phone_data: PchProviderPhoneTextCreateRequest,
    db: Session = Depends(get_db)
):
    provider = db.query(Pch_Provider_Info).filter(
        Pch_Provider_Info.txn_id == provider_id
    ).first()

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    comm_medium = db.query(CommunicationMedium).filter(
        func.lower(CommunicationMedium.sub_type) == func.lower(phone_data.communication_type)
    ).first()

    if not comm_medium:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid communication_type:{phone_data.communication_type}"
        )

    existing_phone_count = (
        db.query(Pch_Provider_Communication)
        .join(
            CommunicationMedium,
            Pch_Provider_Communication.communication_id == CommunicationMedium.pk_id
        )
        .filter(
            Pch_Provider_Communication.provider_id == provider_id,
            func.lower(CommunicationMedium.type) == "phone"
        )
        .count()
    )

    if existing_phone_count == 0:
        phone_data.primary = True

    if phone_data.primary:
        phone_comm_ids = [
            row[0] for row in db.query(CommunicationMedium.pk_id).filter(
                func.lower(CommunicationMedium.type) == "phone"
            ).all()
        ]

        db.query(Pch_Provider_Communication).filter(
            Pch_Provider_Communication.provider_id == provider_id,
            Pch_Provider_Communication.communication_id.in_(phone_comm_ids)
        ).update({Pch_Provider_Communication.primary: False}, synchronize_session=False)

    new_phone = Pch_Provider_Communication(
        provider_id=provider_id,
        communication_id=comm_medium.pk_id,
        value=phone_data.phone,
        extension=phone_data.extension,
        text_opt=phone_data.text_opt_in,
        dnd=phone_data.do_not_call,
        ai_pre_recording=phone_data.ai_pre_recording_opt_in,
        primary=phone_data.primary
    )

    db.add(new_phone)
    db.commit()
    db.refresh(new_phone)

    return {
        "message": "Phone record created successfully",
        "pk_id": str(new_phone.pk_id)
    }

@router.patch("/providers/{provider_id}/phone-text")
def update_provider_phone_text(
    provider_id: str,
    phone_data: PchProviderPhoneTextUpdateRequest,
    db: Session = Depends(get_db)
):
    provider = db.query(Pch_Provider_Info).filter(
        Pch_Provider_Info.txn_id == provider_id
    ).first()

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    phone_record = (
        db.query(Pch_Provider_Communication)
        .filter(
            Pch_Provider_Communication.pk_id == str(phone_data.pk_id),
            Pch_Provider_Communication.provider_id == str(provider_id)
        )
        .first()
    )

    if not phone_record:
        raise HTTPException(status_code=404, detail="Phone record not found")

    if phone_data.communication_type is not None:
        comm_medium = db.query(CommunicationMedium).filter(
            func.lower(CommunicationMedium.sub_type) == func.lower(phone_data.communication_type)
        ).first()
        if not comm_medium:
            raise HTTPException(status_code=400, detail=f"Invalid communication_type: {phone_data.communication_type}")
        phone_record.communication_id = comm_medium.pk_id

    if phone_data.phone is not None:
        phone_record.value = phone_data.phone

    if phone_data.extension is not None:
        phone_record.extension = phone_data.extension

    if phone_data.text_opt_in is not None:
        phone_record.text_opt = phone_data.text_opt_in

    if phone_data.ai_pre_recording_opt_in is not None:
        phone_record.ai_pre_recording = phone_data.ai_pre_recording_opt_in

    if phone_data.do_not_call is not None:
        phone_record.dnd = phone_data.do_not_call

    if phone_data.primary is not None:
        if phone_data.primary:
            other_phones = (
                db.query(Pch_Provider_Communication.pk_id)
                .join(
                    CommunicationMedium,
                    Pch_Provider_Communication.communication_id == CommunicationMedium.pk_id
                )
                .filter(
                    Pch_Provider_Communication.provider_id == provider_id,
                    Pch_Provider_Communication.pk_id != phone_data.pk_id,
                    func.lower(CommunicationMedium.type) == "phone"
                )
                .all()
            )

            if other_phones:
                phone_ids = [phone[0] for phone in other_phones]
                db.query(Pch_Provider_Communication).filter(
                    Pch_Provider_Communication.pk_id.in_(phone_ids)
                ).update({Pch_Provider_Communication.primary: False}, synchronize_session=False)

        phone_record.primary = phone_data.primary

    db.commit()

    return {
        "message": "Phone record updated successfully",
        "pk_id": str(phone_data.pk_id)
    }

@router.delete("/providers/phone-text/{phone_id}")
def delete_provider_phone_text(
    phone_id: str,
    db: Session = Depends(get_db)
):
    phone_record = (
        db.query(Pch_Provider_Communication)
        .join(
            CommunicationMedium,
            Pch_Provider_Communication.communication_id == CommunicationMedium.pk_id
        )
        .filter(
            Pch_Provider_Communication.pk_id == str(phone_id),
            func.lower(CommunicationMedium.type) == "phone"
        )
        .first()
    )

    if not phone_record:
        raise HTTPException(status_code=404, detail="Phone record not found")

    provider_id = phone_record.provider_id

    db.delete(phone_record)
    db.commit()

    remaining_phones = (
        db.query(Pch_Provider_Communication)
        .join(
            CommunicationMedium,
            Pch_Provider_Communication.communication_id == CommunicationMedium.pk_id
        )
        .filter(
            Pch_Provider_Communication.provider_id == provider_id,
            func.lower(CommunicationMedium.type) == "phone"
        )
        .all()
    )

    if len(remaining_phones) == 1 and not remaining_phones[0].primary:
        remaining_phones[0].primary = True
        db.commit()

    return {
        "success": True,
        "message": "Phone record deleted successfully",
        "deleted_id": str(phone_id)
    }

@router.get("/providers/carrier-credentials/{txn_id_provider}")
def get_credentials(txn_id_provider: str, db: Session = Depends(get_db)):
    credentials = db.query(Pch_Carrier_Credentials).filter(
        Pch_Carrier_Credentials.txn_id_provider == txn_id_provider
    ).all()
    return credentials

@router.post("/providers/carrier-credentials")
def create_credential(
    payload: PchCarrierCredentialsCreateSchema,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    new_credential = Pch_Carrier_Credentials(
        **{**payload.dict(), "login": user["email"]}
    )

    db.add(new_credential)
    db.commit()
    db.refresh(new_credential)

    return {
        "message": "Carrier credential created successfully",
        "pk_id": str(new_credential.pk_id)
    }

@router.patch("/providers/carrier-credentials")
def update_credential(
    payload: PchCarrierCredentialsUpdateSchema,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    credential = db.query(Pch_Carrier_Credentials).filter(
        Pch_Carrier_Credentials.pk_id == payload.pk_id
    ).first()

    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")

    for field, value in payload.dict(exclude={"pk_id"}, exclude_unset=True).items():
        if value is not None:
            setattr(credential, field, value)

    credential.login = user["email"]

    db.commit()
    db.refresh(credential)

    return {
        "message": "Carrier credential updated successfully",
        "pk_id": str(credential.pk_id)
    }

@router.delete("/providers/carrier-credentials/{pk_id}")
def delete_credential(pk_id: str, db: Session = Depends(get_db)):
    credential = db.query(Pch_Carrier_Credentials).filter(
        Pch_Carrier_Credentials.pk_id == pk_id
    ).first()
    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")

    db.delete(credential)
    db.commit()
    return {"message": "Carrier credential deleted successfully", "pk_id": pk_id}