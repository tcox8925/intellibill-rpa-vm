from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import Integer, String, case, cast, distinct, func, and_, or_
from app.core.helpers import custom_order_automationops
from app.models import OpsAutomationDashboard, ServiceInterruption
from app.db.session import get_db
from typing import List, Optional
from app.middleware.validator import get_current_user
from app.models.ServicePerformance.ops_automation_matrix import OpsAccProcessMatrix, OpsAcrProcessMatrix, OpsLoadMatrixAcu, OpsProcessMatrix, OpsRpaMatrix
from app.schemas import OpsAutomationUpdateSchema
from app.schemas.service_performance.automation_ops_schema import AutomationOpsUpdateSchema
from fastapi.security import HTTPBearer

router = APIRouter(tags=["AUTOMATION OPS ROUTES"])
security = HTTPBearer()


@router.get("/automation-ops/common_filters", dependencies=[Depends(security)])
async def get_automation_acc_filters(
    tab: str,
    db: Session = Depends(get_db)
):
    try:
        # Get distinct carriers
        carriers = db.query(distinct(OpsAutomationDashboard.carrier_id), OpsAutomationDashboard.carrier_name).order_by(
            OpsAutomationDashboard.carrier_name).all()
        carrier_list = [{"id": str(c[0]), "value": c[1]}
                        for c in carriers if c[0] and c[1] and c[1].strip()]
        # Get distinct process status
        if tab == "ACC":
            automated_values = db.query(distinct(OpsAutomationDashboard.acc_status)).order_by(
                OpsAutomationDashboard.acc_status).all()
            automated_list = [{"id": ps[0], "value": ps[0]}
                              for ps in automated_values if ps[0] is not None]
        elif tab == "ACR":
            automated_values = db.query(distinct(OpsAutomationDashboard.acr_status)).order_by(
                OpsAutomationDashboard.acr_status).all()
            automated_list = [{"id": ps[0], "value": ps[0]}
                              for ps in automated_values if ps[0] is not None]
        elif tab == "BOB":
            automated_values = db.query(distinct(OpsAutomationDashboard.bob_status)).order_by(
                OpsAutomationDashboard.bob_status).all()
            automated_list = [{"id": ps[0], "value": ps[0]}
                              for ps in automated_values if ps[0] is not None]
        elif tab == "ACU":
            automated_values = db.query(distinct(OpsAutomationDashboard.acu_status)).order_by(
                OpsAutomationDashboard.acu_status).all()
            automated_list = [{"id": ps[0], "value": ps[0]}
                              for ps in automated_values if ps[0] is not None]
        elif tab == "COM":
            automated_values = db.query(distinct(OpsAutomationDashboard.com_status)).order_by(
                OpsAutomationDashboard.com_status).all()
            automated_list = [{"id": ps[0], "value": ps[0]}
                              for ps in automated_values if ps[0] is not None]

        # Get distinct carrier statuses
        carrier_statuses = db.query(distinct(OpsAutomationDashboard.carrier_status)).order_by(
            OpsAutomationDashboard.carrier_status).all()
        carrier_status_list = [{"id": cs[0], "value": cs[0]}
                               for cs in carrier_statuses if cs[0] and cs[0].strip()]

        last_proc_dates = db.query(distinct(OpsAutomationDashboard.record_date)).order_by(
            OpsAutomationDashboard.record_date.desc()).all()
        # last_proc_date_list = [{"id": ld[0], "value": str(ld[0]) if ld[0] else None} for ld in last_proc_dates if ld[0]]

        today = date.today()
        # filter out today's date and null values, and convert to string for frontend
        last_proc_date_list = [
            {"id": ld[0], "value": str(ld[0]) if ld[0] else None}
            for ld in last_proc_dates
            if ld[0] and ld[0] != today
        ]

        return {
            "filters": {
                "carriers": carrier_list,
                "automated": automated_list,
                "carrier_statuses": carrier_status_list,
                "last_proc_dates": last_proc_date_list
            }
        }

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="failed to fetch data")

# For ACC
# Automated - acc_automated
# last_run_date
@router.get("/automation-ops/acc", dependencies=[Depends(security)])
async def get_automation_acc(
    entity: str,
    affiliation: str,
    carrier: Optional[str] = None,
    tab: Optional[str] = None,
    automated: Optional[str] = None,
    carrier_status: Optional[str] = None,
    processed_date: Optional[str] = None,
    view_type: Optional[str] = "dashboard",
    page: int = 1,
    page_size: int = 50,
    sort_column: Optional[str] = None,
    sort_order: Optional[str] = "asc",
    db: Session = Depends(get_db)
):
    try:
        # Main query joined with latest record and last run
        query = (
            db.query(
                OpsAutomationDashboard.id,
                OpsAutomationDashboard.record_date,
                OpsAutomationDashboard.carrier_id,
                OpsAutomationDashboard.carrier_name,
                OpsAutomationDashboard.carrier_status,
                OpsAutomationDashboard.acc_cadence,
                OpsAutomationDashboard.acc_automation_type,
                OpsAutomationDashboard.acc_status,
                OpsAutomationDashboard.acc_automated,
                OpsAutomationDashboard.acc_rpa,
                OpsAutomationDashboard.interruptions["ACC"].astext.label(
                    "interruptions"),
                OpsAutomationDashboard.notes["ACC"].astext.label("notes"),
                OpsAutomationDashboard.entity_id,
                OpsAutomationDashboard.sub_entity_id,
                OpsAutomationDashboard.last_run_date["acc"].astext.label("last_run_date"),
                ServiceInterruption.issue_status
            )
            .filter(
                OpsAutomationDashboard.entity_id == entity,
                OpsAutomationDashboard.sub_entity_id == affiliation
            )
            .outerjoin(
                ServiceInterruption,
                OpsAutomationDashboard.interruptions["ACC"].astext
                == cast(ServiceInterruption.interruption_id, String)
            )
        )

        if processed_date:
            query = query.filter(
                OpsAutomationDashboard.record_date == processed_date)

        # Apply optional filters
        if carrier:
            query = query.filter(OpsAutomationDashboard.carrier_id == carrier)

        if tab == "active":
            # query = query.filter(OpsAutomationDashboard.acc_automated.in_([1, 3]))
            query = query.filter(OpsAutomationDashboard.acc_automated == 1)
        elif tab == "inactive":
            query = query.filter(
                OpsAutomationDashboard.acc_automated.in_([0, 2, 3]))

        if automated:
            query = query.filter(
                OpsAutomationDashboard.acc_automated == automated)

        if carrier_status:
            query = query.filter(
                OpsAutomationDashboard.carrier_status == carrier_status)

        # Apply sorting once at the end
        if sort_column:
            if sort_column == "last_run_date":
                query = query.order_by(
                    OpsAutomationDashboard.last_run_date["acc"].astext.desc(
                    ) if sort_order == "desc" else OpsAutomationDashboard.last_run_date["acc"].astext.asc()
                )
            elif sort_column == "interruption":
                interruption = OpsAutomationDashboard.interruptions["ACC"].astext
                query = query.order_by(
                    interruption.desc() if sort_order == "desc" else interruption.asc()
                )
            else:
                sort_attr = getattr(OpsAutomationDashboard, sort_column, None)
                if sort_attr is not None:
                    query = query.order_by(
                        sort_attr.desc() if sort_order == "desc" else sort_attr.asc()
                    )
        else:
            query = query.order_by(
                custom_order_automationops(
                    OpsAutomationDashboard.acc_automated)
            )

        # Get total count before pagination
        total_count = query.count()

        # Apply pagination for dashboard view
        if view_type == "dashboard":
            query = query.offset((page - 1) * page_size).limit(page_size)

        rows = query.all()

        # Build response payload
        items = [
            {
                "id": r.id,
                "record_date": r.record_date,
                "carrier_id": str(r.carrier_id),
                "carrier_name": r.carrier_name,
                "carrier_status": r.carrier_status,
                "acc_cadence": r.acc_cadence,
                "acc_automated": r.acc_automated,
                "acc_automation_type": r.acc_automation_type,
                "interruptions": r.interruptions if r.interruptions else None,
                "issue_status": r.issue_status if r.issue_status else None,
                "notes": r.notes if r.notes else None,
                "last_run_date": r.last_run_date if r.last_run_date else None,
                "entity_id": str(r.entity_id) if r.entity_id else None,
                "sub_entity_id": str(r.sub_entity_id) if r.sub_entity_id else None
            }
            for r in rows
        ]

        return {
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "items": items
        }

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="failed to fetch data")

# For ACR
# Automated - acr_automated
# last_run_date
@router.get("/automation-ops/acr", dependencies=[Depends(security)])
async def get_automation_acr(
    entity: str,
    affiliation: str,
    carrier: Optional[str] = None,
    tab: Optional[str] = None,
    automated: Optional[str] = None,
    carrier_status: Optional[str] = None,
    processed_date: Optional[str] = None,
    view_type: Optional[str] = "dashboard",
    page: int = 1,
    page_size: int = 50,
    sort_column: Optional[str] = None,
    sort_order: Optional[str] = "asc",
    db: Session = Depends(get_db)
):
    try:
        # Main query joined with latest record and last run
        query = (
            db.query(
                OpsAutomationDashboard.id,
                OpsAutomationDashboard.record_date,
                OpsAutomationDashboard.carrier_id,
                OpsAutomationDashboard.carrier_name,
                OpsAutomationDashboard.carrier_status,
                OpsAutomationDashboard.acr_cadence,
                OpsAutomationDashboard.acr_automation_type,
                OpsAutomationDashboard.acr_status,
                OpsAutomationDashboard.acr_rpa,
                OpsAutomationDashboard.acr_automated,
                OpsAutomationDashboard.acr_process,
                OpsAutomationDashboard.interruptions["ACR"].astext.label(
                    "interruptions"),
                OpsAutomationDashboard.notes["ACR"].astext.label("notes"),
                OpsAutomationDashboard.entity_id,
                OpsAutomationDashboard.sub_entity_id,
                OpsAutomationDashboard.last_run_date["acr"].astext.label("last_run_date"),
                ServiceInterruption.issue_status
            )
            .filter(
                OpsAutomationDashboard.entity_id == entity,
                OpsAutomationDashboard.sub_entity_id == affiliation
            )
            .outerjoin(
                ServiceInterruption,
                OpsAutomationDashboard.interruptions["ACR"].astext
                == cast(ServiceInterruption.interruption_id, String)
            )
        )

        if processed_date:
            query = query.filter(
                OpsAutomationDashboard.record_date == processed_date)

        # Apply optional filters
        if carrier:
            query = query.filter(OpsAutomationDashboard.carrier_id == carrier)

        if tab == "active":
            # query = query.filter(OpsAutomationDashboard.acr_automated.in_([1]))
            query = query.filter(OpsAutomationDashboard.acr_automated == 1)
        elif tab == "inactive":
            query = query.filter(
                OpsAutomationDashboard.acr_automated.in_([0, 2, 3]))

        if automated:
            query = query.filter(
                OpsAutomationDashboard.acr_automated == automated)

        if carrier_status:
            query = query.filter(
                OpsAutomationDashboard.carrier_status == carrier_status)

        # Apply sorting once at the end
        if sort_column:
            if sort_column == "last_run_date":
                query = query.order_by(
                    OpsAutomationDashboard.last_run_date["acr"].astext.desc(
                    ) if sort_order == "desc" else OpsAutomationDashboard.last_run_date["acr"].astext.asc()
                )
            elif sort_column == "interruption":
                interruption = OpsAutomationDashboard.interruptions["ACR"].astext
                query = query.order_by(
                    interruption.desc() if sort_order == "desc" else interruption.asc()
                )
            else:
                sort_attr = getattr(OpsAutomationDashboard, sort_column, None)
                if sort_attr is not None:
                    query = query.order_by(
                        sort_attr.desc() if sort_order == "desc" else sort_attr.asc()
                    )
        else:
            query = query.order_by(
                custom_order_automationops(
                    OpsAutomationDashboard.acr_automated)
            )

        # Get total count before pagination
        total_count = query.count()

        # Apply pagination for dashboard view
        if view_type == "dashboard":
            query = query.offset((page - 1) * page_size).limit(page_size)

        rows = query.all()

        # Build response payload
        items = [
            {
                "id": r.id,
                "record_date": r.record_date,
                "carrier_id": str(r.carrier_id),
                "carrier_name": r.carrier_name,
                "carrier_status": r.carrier_status,
                "acr_cadence": r.acr_cadence,
                "acr_automated": r.acr_automated,
                "acr_automation_type": r.acr_automation_type,
                "interruptions": r.interruptions if r.interruptions else None,
                "issue_status": r.issue_status if r.issue_status else None,
                "notes": r.notes if r.notes else None,
                "last_run_date": r.last_run_date if r.last_run_date else None,
                "entity_id": str(r.entity_id) if r.entity_id else None,
                "sub_entity_id": str(r.sub_entity_id) if r.sub_entity_id else None
            }
            for r in rows
        ]

        return {
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "items": items
        }

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="failed to fetch data")

# For BOB
# Data Retrieval status ---- bob_proc_automated
# Automated -- bob_process
# active/inactive use bob_proc_automated
# last_run_date to be updated
@router.get("/automation-ops/bob", dependencies=[Depends(security)])
async def get_automation_bob(
    entity: str,
    affiliation: str,
    processed_date: Optional[str] = None,
    carrier: Optional[str] = None,
    tab: Optional[str] = None,
    automated: Optional[int] = None,
    carrier_status: Optional[str] = None,
    view_type: Optional[str] = "dashboard",
    page: int = 1,
    page_size: int = 50,
    sort_column: Optional[str] = None,
    sort_order: Optional[str] = "asc",
    db: Session = Depends(get_db)
):
    try:
        # Main query joined with latest record and last run
        query = (
            db.query(
                OpsAutomationDashboard.id,
                OpsAutomationDashboard.record_date,
                OpsAutomationDashboard.carrier_id,
                OpsAutomationDashboard.carrier_name,
                OpsAutomationDashboard.carrier_status,
                OpsAutomationDashboard.bob_cadence,
                OpsAutomationDashboard.bob_automation_type,
                OpsAutomationDashboard.bob_status,
                # OpsAutomationDashboard.bob_download,
                # OpsAutomationDashboard.bob_automated,
                OpsAutomationDashboard.bob_process,
                OpsAutomationDashboard.bob_proc_automated,
                OpsAutomationDashboard.interruptions["BOB"].astext.label(
                    "interruptions"),
                OpsAutomationDashboard.notes["BOB"].astext.label("notes"),
                OpsAutomationDashboard.entity_id,
                OpsAutomationDashboard.sub_entity_id,
                OpsAutomationDashboard.last_run_date["bob"].astext.label("last_run_date"),
                # last_run_subq.c.last_run_date,
                ServiceInterruption.issue_status
            )
            .filter(
                OpsAutomationDashboard.entity_id == entity,
                OpsAutomationDashboard.sub_entity_id == affiliation
            )
            .outerjoin(
                ServiceInterruption,
                OpsAutomationDashboard.interruptions["BOB"].astext
                == cast(ServiceInterruption.interruption_id, String)
            )
        )
        if processed_date:
            # When filtering by processed_date, apply the date filter directly
            query = query.filter(
                OpsAutomationDashboard.record_date == processed_date)

        if carrier:
            query = query.filter(OpsAutomationDashboard.carrier_id == carrier)

        if tab == "active":
            query = query.filter(
                OpsAutomationDashboard.bob_proc_automated == 1
            )
        elif tab == "inactive":
            query = query.filter(
                OpsAutomationDashboard.bob_proc_automated != 1
            )

        if automated is not None:
            query = query.filter(
                OpsAutomationDashboard.bob_proc_automated == automated)

        if carrier_status:
            query = query.filter(
                OpsAutomationDashboard.carrier_status == carrier_status)

        # Apply sorting once at the end
        if sort_column:
            if sort_column == "last_run_date":
                query = query.order_by(
                    OpsAutomationDashboard.last_run_date["bob"].astext.desc(
                    ) if sort_order == "desc" else OpsAutomationDashboard.last_run_date["bob"].astext.asc()
                )
            elif sort_column == "interruption":
                interruption = OpsAutomationDashboard.interruptions["BOB"].astext
                query = query.order_by(
                    interruption.desc() if sort_order == "desc" else interruption.asc()
                )
            else:
                sort_attr = getattr(OpsAutomationDashboard, sort_column, None)
                if sort_attr is not None:
                    query = query.order_by(
                        sort_attr.desc() if sort_order == "desc" else sort_attr.asc()
                    )
        else:
            query = query.order_by(
                custom_order_automationops(
                    OpsAutomationDashboard.bob_proc_automated)
            )

        # Get total count before pagination
        total_count = query.count()

        # Apply pagination for dashboard view
        if view_type == "dashboard":
            query = query.offset((page - 1) * page_size).limit(page_size)

        rows = query.all()

        # Build response payload
        items = [
            {
                "id": r.id,
                "record_date": r.record_date,
                "carrier_id": str(r.carrier_id),
                "carrier_name": r.carrier_name,
                "carrier_status": r.carrier_status,
                "bob_cadence": r.bob_cadence,
                "bob_proc_automated": r.bob_proc_automated,
                "bob_automation_type": r.bob_automation_type,
                # "bob_automated": r.bob_automated,
                # "bob_download": r.bob_download,
                "bob_process": r.bob_process,
                "interruptions": r.interruptions if r.interruptions else None,
                "issue_status": r.issue_status if r.issue_status else None,
                "notes": r.notes if r.notes else None,
                # "last_run_date": str(r.last_run_date) if r.last_run_date else None,
                "last_run_date": r.last_run_date if r.last_run_date else None,
                "entity_id": str(r.entity_id) if r.entity_id else None,
                "sub_entity_id": str(r.sub_entity_id) if r.sub_entity_id else None
            }
            for r in rows
        ]

        return {
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "items": items
        }

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="failed to fetch data")

# For ACU
# Data Retrieval status -- acu_proc_automated
# Automated - 	acu_process
# active/inactive use acu_proc_automated
# last_run_date to be updated
@router.get("/automation-ops/acu", dependencies=[Depends(security)])
async def get_automation_acu(
    entity: str,
    affiliation: str,
    processed_date: Optional[str] = None,
    carrier: Optional[str] = None,
    tab: Optional[str] = None,
    automated: Optional[int] = None,
    carrier_status: Optional[str] = None,
    view_type: Optional[str] = "dashboard",
    page: int = 1,
    page_size: int = 50,
    sort_column: Optional[str] = None,
    sort_order: Optional[str] = "asc",
    db: Session = Depends(get_db)
):
    try:
        # Main query joined with latest record and last run
        query = (
            db.query(
                OpsAutomationDashboard.id,
                OpsAutomationDashboard.record_date,
                OpsAutomationDashboard.carrier_id,
                OpsAutomationDashboard.carrier_name,
                OpsAutomationDashboard.carrier_status,
                OpsAutomationDashboard.acu_cadence,
                OpsAutomationDashboard.acu_automation_type,
                # OpsAutomationDashboard.acu_status,
                # OpsAutomationDashboard.acu_download,
                # OpsAutomationDashboard.acu_automated,
                OpsAutomationDashboard.acu_process,
                OpsAutomationDashboard.acu_proc_automated,
                OpsAutomationDashboard.interruptions["ACU"].astext.label(
                    "interruptions"),
                OpsAutomationDashboard.notes["ACU"].astext.label("notes"),
                OpsAutomationDashboard.entity_id,
                OpsAutomationDashboard.sub_entity_id,
                OpsAutomationDashboard.last_run_date["acu"].astext.label("last_run_date"),
                ServiceInterruption.issue_status
            )
            .filter(
                OpsAutomationDashboard.entity_id == entity,
                OpsAutomationDashboard.sub_entity_id == affiliation
            )
            .outerjoin(
                ServiceInterruption,
                OpsAutomationDashboard.interruptions["ACU"].astext
                == cast(ServiceInterruption.interruption_id, String)
            )
        )

        if processed_date:
            query = query.filter(
                OpsAutomationDashboard.record_date == processed_date)

        # Apply optional filters

        if carrier:
            query = query.filter(OpsAutomationDashboard.carrier_id == carrier)

        if tab == "active":
            # query = query.filter(OpsAutomationDashboard.acu_proc_automated.in_([1, 3]))
            query = query.filter(
                OpsAutomationDashboard.acu_proc_automated == 1
            )
        elif tab == "inactive":
            query = query.filter(
                OpsAutomationDashboard.acu_proc_automated != 1
            )
            
        if automated is not None:
            query = query.filter(
                OpsAutomationDashboard.acu_process == automated)

        if carrier_status:
            query = query.filter(
                OpsAutomationDashboard.carrier_status == carrier_status)

        # Apply sorting once at the end
        if sort_column:
            if sort_column == "last_run_date":           
                query = query.order_by(
                    OpsAutomationDashboard.last_run_date["acu"].astext.desc(
                    ) if sort_order == "desc" else OpsAutomationDashboard.last_run_date["acu"].astext.asc()
                )
            elif sort_column == "interruption":
                interruption = OpsAutomationDashboard.interruptions["ACU"].astext
                query = query.order_by(
                    interruption.desc() if sort_order == "desc" else interruption.asc()
                )
            else:
                sort_attr = getattr(OpsAutomationDashboard, sort_column, None)
                if sort_attr is not None:
                    query = query.order_by(
                        sort_attr.desc() if sort_order == "desc" else sort_attr.asc()
                    )
        else:
            query = query.order_by(
                custom_order_automationops(
                    OpsAutomationDashboard.acu_proc_automated)
            )

        # Get total count before pagination
        total_count = query.count()

        # Apply pagination for dashboard view
        if view_type == "dashboard":
            query = query.offset((page - 1) * page_size).limit(page_size)

        rows = query.all()

        # Build response payload
        items = [
            {
                "id": r.id,
                "record_date": r.record_date,
                "carrier_id": str(r.carrier_id),
                "carrier_name": r.carrier_name,
                "carrier_status": r.carrier_status,
                "acu_cadence": r.acu_cadence,
                "acu_proc_automated": r.acu_proc_automated,
                "acu_automation_type": r.acu_automation_type,
                # "acu_automated": r.acu_automated,
                "acu_process": r.acu_process,
                # "acu_download": r.acu_download,
                "interruptions": r.interruptions if r.interruptions else None,
                "issue_status": r.issue_status if r.issue_status else None,
                "notes": r.notes if r.notes else None,
                "last_run_date": r.last_run_date if r.last_run_date else None,
                "entity_id": str(r.entity_id) if r.entity_id else None,
                "sub_entity_id": str(r.sub_entity_id) if r.sub_entity_id else None
            }
            for r in rows
        ]

        return {
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "items": items
        }

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="failed to fetch data")

# For COM
# Data Retrieval status -- com_proc_automated
# Automated -- com_process
# active/inactive use com_proc_automated
# last_run_date to be updated
@router.get("/automation-ops/com", dependencies=[Depends(security)])
async def get_automation_com(
    entity: str,
    affiliation: str,
    carrier: Optional[str] = None,
    tab: Optional[str] = None,
    automated: Optional[str] = None,
    processed_date: Optional[str] = None,
    view_type: Optional[str] = "dashboard",
    page: int = 1,
    page_size: int = 50,
    sort_column: Optional[str] = None,
    sort_order: Optional[str] = "asc",
    db: Session = Depends(get_db)
):
    try:
        query = (
            db.query(
                OpsAutomationDashboard.id,
                OpsAutomationDashboard.record_date,
                OpsAutomationDashboard.carrier_id,
                OpsAutomationDashboard.carrier_name,
                OpsAutomationDashboard.carrier_status,
                OpsAutomationDashboard.com_cadence,
                OpsAutomationDashboard.com_automation_type,
                # OpsAutomationDashboard.com_status,
                # OpsAutomationDashboard.com_download,
                # OpsAutomationDashboard.com_automated,
                OpsAutomationDashboard.com_process,
                OpsAutomationDashboard.com_proc_automated,
                OpsAutomationDashboard.interruptions["COM"].astext.label(
                    "interruptions"),
                OpsAutomationDashboard.notes["COM"].astext.label("notes"),
                OpsAutomationDashboard.entity_id,
                OpsAutomationDashboard.sub_entity_id,
                OpsAutomationDashboard.last_run_date["com"].astext.label("last_run_date"),
                # last_run_subq.c.last_run_date,
                ServiceInterruption.issue_status
            )
            .filter(
                OpsAutomationDashboard.entity_id == entity,
                OpsAutomationDashboard.sub_entity_id == affiliation
            )
            .outerjoin(
                ServiceInterruption,
                OpsAutomationDashboard.interruptions["COM"].astext
                == cast(ServiceInterruption.interruption_id, String)
            )
        )

        if processed_date:
            query = query.filter(
                OpsAutomationDashboard.record_date == processed_date)

        if carrier:
            query = query.filter(OpsAutomationDashboard.carrier_id == carrier)

        if tab == "active":
            query = query.filter(
                OpsAutomationDashboard.com_proc_automated == 1
            )
        elif tab == "inactive":
            query = query.filter(
                or_(
                    OpsAutomationDashboard.com_proc_automated != 1
                )
            )

        if automated:
            query = query.filter(
                OpsAutomationDashboard.com_process == automated)

        if sort_column:
            if sort_column == "last_run_date":
                query = query.order_by(
                    OpsAutomationDashboard.last_run_date["com"].astext.desc(
                    ) if sort_order == "desc" else OpsAutomationDashboard.last_run_date["com"].astext.asc()
                )
            elif sort_column == "interruption":
                interruption = OpsAutomationDashboard.interruptions["COM"].astext
                query = query.order_by(
                    interruption.desc() if sort_order == "desc" else interruption.asc()
                )
            else:
                sort_attr = getattr(OpsAutomationDashboard, sort_column, None)
                if sort_attr is not None:
                    query = query.order_by(
                        sort_attr.desc() if sort_order == "desc" else sort_attr.asc()
                    )
        else:
            query = query.order_by(
                custom_order_automationops(
                    OpsAutomationDashboard.com_proc_automated)
            )

        total_count = query.count()

        if view_type == "dashboard":
            query = query.offset((page - 1) * page_size).limit(page_size)

        rows = query.all()

        items = [
            {
                "id": r.id,
                "record_date": r.record_date,
                "carrier_id": str(r.carrier_id),
                "carrier_name": r.carrier_name,
                "carrier_status": r.carrier_status,
                "com_cadence": r.com_cadence,
                "com_automation_type": r.com_automation_type,
                # "com_automated": r.com_automated,
                "com_process": r.com_process,
                # "com_download": r.com_download,
                "com_proc_automated": r.com_proc_automated,
                "interruptions": r.interruptions if r.interruptions else None,
                "issue_status": r.issue_status if r.issue_status else None,
                "notes": r.notes if r.notes else None,
                "last_run_date": r.last_run_date if r.last_run_date else None,
                "entity_id": str(r.entity_id) if r.entity_id else None,
                "sub_entity_id": str(r.sub_entity_id) if r.sub_entity_id else None,
            }
            for r in rows
        ]

        return {
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "items": items
        }

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="failed to fetch data")

# Patch endpoints for automation ops updates - separate endpoints for COM, ACU, and BOB to handle specific logic for each process type
@router.patch("/automation-ops/com", dependencies=[Depends(security)])
async def update_interruption_status(
    payload: AutomationOpsUpdateSchema,
    db: Session = Depends(get_db)
):
    payload.process_type = "COM"
    matrix_data = db.query(OpsRpaMatrix).filter(OpsRpaMatrix.process_name ==
                                                payload.process_type, OpsRpaMatrix.carrier_id == payload.carrier_id).first()
    ops_data = db.query(OpsAutomationDashboard).filter(OpsAutomationDashboard.id ==
                                                       payload.id, OpsAutomationDashboard.carrier_id == payload.carrier_id).first()

    if not matrix_data or not ops_data:
        raise HTTPException(status_code=404, detail="Record not found")

    if payload.cadence:
        matrix_data.cadence = payload.cadence
        ops_data.com_cadence = payload.cadence

    # TODO: validate the format of cadence_description before updating
    if payload.cadence_description:
        matrix_data.target_dates = payload.cadence_description
        ops_data.cadence_desc = {
            **(ops_data.cadence_desc or {}), payload.process_type: payload.cadence_description}

    if payload.automated is not None:
        matrix_data.automated = payload.automated
        ops_data.com_process = OpsAutomationDashboard.status_mapping(
            payload.automated)
        # ops_data.com_automated = 0 if payload.automated == 'Active' else 0

    if payload.automation_type:
        matrix_data.pickup_method = payload.automation_type
        ops_data.com_automation_type = payload.automation_type

    if payload.interruption:
        ops_data.interruptions = {
            **(ops_data.interruptions or {}), payload.process_type: payload.interruption}
    if payload.notes:
        ops_data.notes = {**(ops_data.notes or {}),
                          payload.process_type: payload.notes}
    db.commit()
    return {"message": "Record updated successfully"}


@router.patch("/automation-ops/acu", dependencies=[Depends(security)])
async def update_interruption_status(
    payload: AutomationOpsUpdateSchema,
    db: Session = Depends(get_db)
):
    payload.process_type = "ACU"
    matrix_data = db.query(OpsRpaMatrix).filter(OpsRpaMatrix.process_name ==
                                                payload.process_type, OpsRpaMatrix.carrier_id == payload.carrier_id).first()
    ops_data = db.query(OpsAutomationDashboard).filter(OpsAutomationDashboard.id ==
                                                       payload.id, OpsAutomationDashboard.carrier_id == payload.carrier_id).first()

    load_matrix_data = db.query(OpsLoadMatrixAcu).filter(OpsLoadMatrixAcu.carrier_id ==
                                                         payload.carrier_id, OpsLoadMatrixAcu.process_type == payload.process_type).first()

    if not load_matrix_data or not matrix_data or not ops_data:
        raise HTTPException(
            status_code=404, detail="Load matrix data or RPA matrix data or Ops dashboard data not found")

    if payload.cadence:
        matrix_data.cadence = payload.cadence
        ops_data.acu_cadence = payload.cadence

    if payload.automated is not None:
        load_matrix_data.automated = payload.automated
        ops_data.acu_process = OpsAutomationDashboard.status_mapping(
            payload.automated)
        # ops_data.acu_automated = 0 if payload.automated == 'Active' else 0

    # TODO: validate the format of cadence_description before updating
    if payload.cadence_description:
        matrix_data.target_dates = payload.cadence_description
        ops_data.cadence_desc = {
            **(ops_data.cadence_desc or {}), payload.process_type: payload.cadence_description}

    if payload.automation_type:
        matrix_data.pickup_method = payload.automation_type
        ops_data.acu_automation_type = payload.automation_type

    if payload.interruption:
        ops_data.interruptions = {
            **(ops_data.interruptions or {}), payload.process_type: payload.interruption}
    if payload.notes:
        ops_data.notes = {**(ops_data.notes or {}),
                          payload.process_type: payload.notes}
    db.commit()
    return {"message": "Record updated successfully"}


@router.patch("/automation-ops/bob", dependencies=[Depends(security)])
async def update_interruption_status(
    payload: AutomationOpsUpdateSchema,
    db: Session = Depends(get_db)
):
    payload.process_type = "BOB"
    matrix_data = db.query(OpsRpaMatrix).filter(OpsRpaMatrix.process_name ==
                                                payload.process_type, OpsRpaMatrix.carrier_id == payload.carrier_id).first()
    ops_data = db.query(OpsAutomationDashboard).filter(OpsAutomationDashboard.id ==
                                                       payload.id, OpsAutomationDashboard.carrier_id == payload.carrier_id).first()

    load_matrix_data = db.query(OpsProcessMatrix).filter(OpsProcessMatrix.carrier_id ==
                                                         payload.carrier_id, OpsProcessMatrix.process_type == payload.process_type).first()

    if not load_matrix_data or not matrix_data or not ops_data:
        raise HTTPException(
            status_code=404, detail="Load matrix data or RPA matrix data or Ops dashboard data not found")

    if payload.cadence:
        matrix_data.cadence = payload.cadence
        ops_data.bob_cadence = payload.cadence

    if payload.automated is not None:
        load_matrix_data.automated = payload.automated
        ops_data.bob_process = OpsAutomationDashboard.status_mapping(
            payload.automated)
        # ops_data.bob_automated = 0 if payload.automated == 'Active' else 0

    # TODO: validate the format of cadence_description before updating
    if payload.cadence_description:
        matrix_data.target_dates = payload.cadence_description
        ops_data.cadence_desc = {
            **(ops_data.cadence_desc or {}), payload.process_type: payload.cadence_description}

    if payload.automation_type:
        matrix_data.pickup_method = payload.automation_type
        ops_data.bob_automation_type = payload.automation_type

    if payload.interruption:
        ops_data.interruptions = {
            **(ops_data.interruptions or {}), payload.process_type: payload.interruption}
    if payload.notes:
        ops_data.notes = {**(ops_data.notes or {}),
                          payload.process_type: payload.notes}
    db.commit()
    return {"message": "Record updated successfully"}


@router.patch("/automation-ops/acr", dependencies=[Depends(security)])
async def update_interruption_status(
    payload: AutomationOpsUpdateSchema,
    db: Session = Depends(get_db)
):
    payload.process_type = "ACR"
    matrix_data = db.query(OpsAcrProcessMatrix).filter(
        OpsAcrProcessMatrix.carrier_id == payload.carrier_id).first()
    ops_data = db.query(OpsAutomationDashboard).filter(OpsAutomationDashboard.id ==
                                                       payload.id, OpsAutomationDashboard.carrier_id == payload.carrier_id).first()

    if not matrix_data or not ops_data:
        raise HTTPException(
            status_code=404, detail="ACR matrix data or Ops dashboard data not found")

    if payload.cadence:
        matrix_data.schedule = payload.cadence
        ops_data.acr_cadence = payload.cadence

    if payload.automated is not None:
        matrix_data.automated = payload.automated
        ops_data.acr_automated = OpsAutomationDashboard.status_mapping(
            payload.automated)
        # ops_data.acr_automated = 0 if payload.automated == 'Active' else 0

    # TODO: validate the format of cadence_description before updating
    if payload.cadence_description:
        matrix_data.schedule_desc = payload.cadence_description
        ops_data.cadence_desc = {
            **(ops_data.cadence_desc or {}), payload.process_type: payload.cadence_description}

    if payload.automation_type:
        matrix_data.automation_type = payload.automation_type
        ops_data.acr_automation_type = payload.automation_type

    if payload.interruption:
        ops_data.interruptions = {
            **(ops_data.interruptions or {}), payload.process_type: payload.interruption}
    if payload.notes:
        ops_data.notes = {**(ops_data.notes or {}),
                          payload.process_type: payload.notes}

    db.commit()
    return {"message": "Record updated successfully"}


@router.patch("/automation-ops/acc", dependencies=[Depends(security)])
async def update_interruption_status(
    payload: AutomationOpsUpdateSchema,
    db: Session = Depends(get_db)
):
    payload.process_type = "ACC"
    matrix_data = db.query(OpsAccProcessMatrix).filter(
        OpsAccProcessMatrix.carrier_id == payload.carrier_id).first()
    ops_data = db.query(OpsAutomationDashboard).filter(OpsAutomationDashboard.id ==
                                                       payload.id, OpsAutomationDashboard.carrier_id == payload.carrier_id).first()

    if not matrix_data or not ops_data:
        print("matrix_data", matrix_data)
        print("ops_data", ops_data)
        raise HTTPException(
            status_code=404, detail="ACC matrix data or Ops dashboard data not found")

    if payload.cadence:
        matrix_data.run_cadence = payload.cadence
        ops_data.acc_cadence = payload.cadence

    if payload.automated is not None:
        matrix_data.automated = payload.automated
        ops_data.acc_automated = OpsAutomationDashboard.status_mapping(
            payload.automated)
        # ops_data.acc_automated = 0 if payload.automated == 'Active' else 0

    # TODO: validate the format of cadence_description before updating
    if payload.cadence_description:
        matrix_data.cadence_desc = payload.cadence_description
        ops_data.cadence_desc = {
            **(ops_data.cadence_desc or {}), payload.process_type: payload.cadence_description}

    if payload.automation_type:
        matrix_data.automation_type = payload.automation_type
        ops_data.acc_automation_type = payload.automation_type

    if payload.interruption:
        ops_data.interruptions = {
            **(ops_data.interruptions or {}), payload.process_type: payload.interruption}
    if payload.notes:
        ops_data.notes = {**(ops_data.notes or {}),
                          payload.process_type: payload.notes}

    db.commit()
    return {"message": "Record updated successfully"}
