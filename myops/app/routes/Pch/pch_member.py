from fastapi import (
    APIRouter,
    Depends,
    Query,
    HTTPException,
    status,
)
from sqlalchemy.orm import  Session
from app.core.helpers import calculate_age, normalize_gender, parse_dob_any_format
from app.db.session import get_db
from app.models.pch_member.PchCareGapDetail import PchCareGapDetail
from app.models.pch_member.LupPayer import LupPayers
from app.models.pch_member.PchDischarges import PchDischarges
from app.models.pch_member.PchInpatientCensus import PchInpatientCensus
from app.models.pch_member.PchMedClaims import PchMedClaims
from app.models.pch_member.PchMemberRoster import PchMemberRoster
from app.models.pch_member.PchPatientCoverages import PchPatientCoverages
from app.models.pch_member.PchRXClaims import PchRXClaim
from sqlalchemy import or_, func, case, and_, DATETIME, literal_column, distinct, text, desc, asc, select, cast, Date, String, Numeric
from typing import List, Optional, Literal
from uuid import UUID, uuid4 as uuid
from app.schemas.Pch.pch_schema import CoverageResponseSchema, PatientCoveragesSchema, PchHospitalizationCreateUpdateSchema, PchInpatientCensusSchema, PchMedicalClaimCreateSchema, PchMedicalClaimHistorySchema, PchMedicalClaimUpdateSchema, PchMemberEditSchema, PchMemberRosterSchema, PchPrescriptionCreateSchema, PchPrescriptionSchema, PchRXClaimCreateSchema, PchRXClaimHistorySchema, PchRXClaimUpdateSchema, PchTopRxExpenseSchema
router = APIRouter(tags=["PCH CRM MEMBER ROUTES"])

from datetime import date, datetime
import re

def parse_numeric_dob(dob_str: str) -> date | None:
    """Parse numeric DOB in MMDDYY or MMDDYYYY format to date object"""
    if not dob_str.isdigit():
        return None
    try:
        if len(dob_str) == 6:  # MMDDYY
            month = dob_str[0:2]
            day = dob_str[2:4]
            year = dob_str[4:6]
            full_year = 1900 + int(year) if int(year) > 50 else 2000 + int(year)
            return date(full_year, int(month), int(day))

        if len(dob_str) == 8:  # MMDDYYYY
            month = dob_str[0:2]
            day = dob_str[2:4]
            year = dob_str[4:8]
            return date(int(year), int(month), int(day))

    except ValueError:
        return None

    return None

@router.get("/pch-member/pch-member-filter")
def get_pch_member_filter(
    entityId: str = Query(..., description="Company ID filter"),
    search: Optional[str] = Query(None, description="Search string"),
    limit: int = Query(50, ge=1, le=200, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_db)
):
    if not search or not search.strip():
        return []

    search_text_raw = search.strip()
    search_text = search_text_raw.lower()
    parts = search_text.split()
    dob_string: Optional[str] = None
    parsed_dob = parse_numeric_dob(search_text_raw)
    print("date of birth:", parsed_dob)
    if parsed_dob:
        dob_string = parsed_dob.strftime('%Y-%m-%d')

    # ---------- LATEST MEMBER ----------
    if dob_string:
        latest_member_subq = (
            db.query(
                PchMemberRoster.amisys_number,
                func.max(PchMemberRoster.load_date).label("latest_load")
            )
            .filter(PchMemberRoster.company_id == entityId)
            .filter(PchMemberRoster.member_dob == dob_string)
            .group_by(PchMemberRoster.amisys_number)
            .subquery()
        )

        member_subq = (
            db.query(PchMemberRoster)
            .join(
                latest_member_subq,
                and_(
                    PchMemberRoster.amisys_number == latest_member_subq.c.amisys_number,
                    PchMemberRoster.load_date == latest_member_subq.c.latest_load
                )
            )
            .subquery()
        )
    else:
        latest_member_subq = (
            db.query(
                PchMemberRoster.pcp_npi,
                func.max(PchMemberRoster.load_date).label("latest_load")
            )
            .group_by(PchMemberRoster.pcp_npi)
            .subquery()
        )

        member_subq = (
            db.query(PchMemberRoster)
            .join(
                latest_member_subq,
                and_(
                    PchMemberRoster.pcp_npi == latest_member_subq.c.pcp_npi,
                    PchMemberRoster.load_date == latest_member_subq.c.latest_load
                )
            )
            .filter(PchMemberRoster.company_id == entityId)
            .subquery()
        )

    # ---------- RX AGG ----------
    rx_subq = (
        db.query(
            PchRXClaim.member_amisys_nbr.label("rx_mem"),
            func.max(PchRXClaim.coverage_level).label("coverage_level"),
            func.max(PchRXClaim.pcp_prac_title_name).label("title"),
            func.max(PchRXClaim.pcp_prac_first_name).label("first"),
            func.max(PchRXClaim.pcp_prac_middle_name).label("middle"),
            func.max(PchRXClaim.pcp_prac_last_name).label("last")
        )
        .group_by(PchRXClaim.member_amisys_nbr)
        .subquery()
    )

    # ---------- CARE GAP ----------
    caregap_subq = (
        db.query(
            PchCareGapDetail.mem_id.label("cg_mem"),
            func.max(PchCareGapDetail.last_visit).label("last_visit")
        )
        .group_by(PchCareGapDetail.mem_id)
        .subquery()
    )

    # ---------- MAIN QUERY ----------
    query = (
        db.query(
            member_subq,
            rx_subq.c.coverage_level,
            rx_subq.c.title,
            rx_subq.c.first,
            rx_subq.c.middle,
            rx_subq.c.last,
            caregap_subq.c.last_visit
        )
        .outerjoin(rx_subq, member_subq.c.amisys_number == rx_subq.c.rx_mem)
        .outerjoin(caregap_subq, member_subq.c.amisys_number == caregap_subq.c.cg_mem)
    )

    # ---------- SEARCH ----------
    if dob_string:
        pass
    else:
        if len(parts) == 1:
            part = parts[0]
            query = query.filter(
                or_(
                    func.lower(member_subq.c.first_name).like(f"{part}%"),
                    func.lower(member_subq.c.last_name).like(f"{part}%")
                )
            )
        else:
            p1, p2 = parts[0], parts[1]
            query = query.filter(
                or_(
                    and_(
                        func.lower(member_subq.c.first_name).like(f"{p1}%"),
                        func.lower(member_subq.c.last_name).like(f"{p2}%")
                    ),
                    and_(
                        func.lower(member_subq.c.first_name).like(f"{p2}%"),
                        func.lower(member_subq.c.last_name).like(f"{p1}%")
                    )
                )
            )

    rows = (
        query
        .order_by(member_subq.c.first_name)
        .offset(offset)
        .limit(limit)
        .all()
    )

    result = []

    for row in rows:
        m = row._mapping  

        dob = parse_dob_any_format(m["member_dob"])
        age = calculate_age(dob)

        provider_name = " ".join(
            filter(None, [m["title"], m["first"], m["middle"], m["last"]])
        )

        data = dict(m)
        data["member_age"] = age
        data["gender"] = normalize_gender(m["gender"])
        data["provider_name"] = provider_name

        result.append(data)

    return result


@router.get("/pch-member/pch-member/{member_id}", response_model=List[PchMemberRosterSchema])
def get_pch_member_by_npi(
    member_id: str,
    db: Session = Depends(get_db)
):
    member = (
        db.query(
            PchMemberRoster,
            PchRXClaim.coverage_level,
            PchRXClaim.pcp_npi,
            PchRXClaim.pcp_prac_title_name,
            PchRXClaim.pcp_prac_first_name,
            PchRXClaim.pcp_prac_middle_name,
            PchRXClaim.pcp_prac_last_name,
        )
        .outerjoin(
            PchRXClaim,
            PchMemberRoster.amisys_number == PchRXClaim.member_amisys_nbr
        )
        .filter(PchMemberRoster.amisys_number == member_id)
        .first()
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No PCH member found for this Member ID"
        )

    (
        member_data,
        coverage_level,
        pcp_npi,
        title,
        first,
        middle,
        last
    ) = member

    full_name = " ".join(
        filter(None, [
            member_data.first_name,
            member_data.middle_name,
            member_data.last_name
        ])
    )

    provider_name = " ".join(filter(None, [title, first, middle, last]))

    dob = parse_dob_any_format(member_data.member_dob)
    member_age = calculate_age(dob)

    average_risk_score = None
    if member_data.risk_score not in (None, "", "null", "None"):
        try:
            cleaned = str(member_data.risk_score).replace(",", ".").strip()
            average_risk_score = float(cleaned)
        except:
            average_risk_score = None

    return [
        PchMemberRosterSchema(
            amisys_number=member_data.amisys_number,
            full_name=full_name,
            first_name=member_data.first_name,
            middle_name=member_data.middle_name,
            last_name=member_data.last_name,
            member_dob=member_data.member_dob,
            member_age=member_age,
            product=member_data.product,
            population_health_category=member_data.population_health_category,
            primary_risk_category=member_data.primary_risk_category,
            member_city=member_data.member_city,
            member_state=member_data.member_state,
            member_zip=member_data.member_zip,
            member_status=member_data.member_status,
            report_date=member_data.report_date,
            gender=member_data.gender,
            member_address_line_1=member_data.member_address_line_1,
            member_address_line_2=member_data.member_address_line_2,
            member_phone_number=member_data.member_phone_number,
            email_address=member_data.email_address,
            pcp_npi=pcp_npi,
            provider_name=provider_name,   
            coverage_level=coverage_level,  
            risk_score=average_risk_score,
            pch_name="N/A",
            primary_diagnosis="N/A"
        )
    ]

@router.patch("/pch-member/pch-member/{member_id}")
def update_pch_member(
    member_id: str,
    payload: PchMemberEditSchema,
    db: Session = Depends(get_db)
):
    member = (
        db.query(PchMemberRoster)
        .filter(PchMemberRoster.amisys_number == member_id)
        .first()
    )

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found"
        )

    member_fields = [
        "first_name", "middle_name", "last_name",
        "member_dob", "gender", "member_phone_number",
        "member_address_line_1", "member_address_line_2",
        "member_city", "member_state", "member_zip",
        "email_address", "member_status",
        "product", "population_health_category",
        "primary_risk_category", "risk_score"
    ]

    for field in member_fields:
        value = getattr(payload, field)
        if value is not None:
            setattr(member, field, value)

    rx_claim = (
        db.query(PchRXClaim)
        .filter(PchRXClaim.member_amisys_nbr == member_id)
        .order_by(PchRXClaim.report_date.desc())
        .first()
    )

    if rx_claim:
        rx_fields = [
            "pcp_npi",
            "pcp_prac_title_name",
            "pcp_prac_first_name",
            "pcp_prac_middle_name",
            "pcp_prac_last_name",
            "coverage_level"
        ]

        for field in rx_fields:
            value = getattr(payload, field)
            if value is not None:
                setattr(rx_claim, field, value)

    db.commit()

    return {
        "message": "Member details updated successfully",
        "member_id": member_id
    }


# **** Prescription APIs ****
@router.get("/pch-member/pch-prescription/{member_id}", response_model=List[PchPrescriptionSchema])
def get_pch_member_by_npi(
    member_id: str,
    db: Session = Depends(get_db)
):
    prescriptions = (
        db.query(PchRXClaim)
        .filter(PchRXClaim.member_amisys_nbr == member_id)
        .all()
    )

    result = []
    def parse_date(date_str: str):
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except:
            return None

    for rx in prescriptions:

        fill_date = parse_date(rx.fill_date_dim_ck)
        prescription_date = parse_date(rx.prescription_date_dim_ck)

        if not fill_date:
            adherence = "Not Adherent"
        elif not prescription_date:
            adherence = "Unknown"
        else:
            gap = (fill_date - prescription_date).days

            if gap > 30:
                adherence = "Not Adherent"
            elif gap > 14:
                adherence = "At Risk"
            else:
                adherence = "Adherent"

        result.append(
            PchPrescriptionSchema(
                pk_id=rx.pk_id,
                member_amisys_nbr=rx.member_amisys_nbr,
                drug_type_code=rx.drug_type_code,
                drug_desc=rx.drug_desc,
                drug_type_desc=rx.drug_type_desc,
                refill_nbr=rx.refill_nbr,
                formulary_ind=rx.formulary_ind,
                fill_date_dim_ck=rx.fill_date_dim_ck,
                prescription_date_dim_ck=rx.prescription_date_dim_ck,
                adherence_status=adherence, 
                source = rx.source 
            )
        )

    return result

@router.post(
    "/pch-member/pch-prescription"
)
def create_pch_prescription(
    payload: PchPrescriptionCreateSchema,
    db: Session = Depends(get_db)
):
    try:
        prescription = PchRXClaim(
            member_amisys_nbr=payload.member_amisys_nbr,

            drug_type_code=payload.drug_type_code,
            drug_desc=payload.drug_desc,
            drug_type_desc=payload.drug_type_desc,

            refill_nbr=payload.refill_nbr,
            formulary_ind=payload.formulary_ind,

            fill_date_dim_ck=payload.fill_date_dim_ck,
            prescription_date_dim_ck=payload.prescription_date_dim_ck,

            source=payload.source,
            status=payload.status
        )

        db.add(prescription)
        db.commit()
        db.refresh(prescription)

        return {
            "success": True,
            "pk_id": str(prescription.pk_id),
            "member_amisys_nbr": prescription.member_amisys_nbr
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/pch-member/pch-prescription/{pk_id}")
def update_pch_prescription(
    pk_id: str,
    prescription_data: PchPrescriptionSchema,
    db: Session = Depends(get_db)
):
    prescription = db.query(PchRXClaim).filter(PchRXClaim.pk_id == pk_id).first()

    if not prescription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prescription record not found"
        )

    for field, value in prescription_data.dict(exclude_unset=True).items():
        setattr(prescription, field, value)

    db.commit()
    db.refresh(prescription)

    return {"message": "Prescription record updated successfully"}

@router.delete("/pch-member/pch-prescription/{pk_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pch_prescription(
    pk_id: str,
    db: Session = Depends(get_db)
):
    prescription = (
        db.query(PchRXClaim)
        .filter(PchRXClaim.pk_id == pk_id)
        .first()
    )

    if not prescription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prescription record not found"
        )

    db.delete(prescription)
    db.commit()

    return "Prescription Data Deleted Sucessfully."


# **** Hospitalization APIs ****

@router.get("/pch-member/pch-hospitalization/{member_id}", response_model=List[PchInpatientCensusSchema])
def get_pch_member_by_npi(
    member_id: str,
    db: Session = Depends(get_db)
):
    query = (
        db.query(
            PchInpatientCensus.status,
            PchInpatientCensus.pcp_npi,
            PchInpatientCensus.pcp_npi_name,
            PchInpatientCensus.facility,
            PchInpatientCensus.service_type,
            PchInpatientCensus.diagnosis_code,
            PchInpatientCensus.diagnosis_code_description,
            PchInpatientCensus.admit_date,
            PchDischarges.discharge_date,
            PchDischarges.discharge_code,
            PchDischarges.discharge_code_description,
            PchInpatientCensus.source
        )
        .outerjoin(
            PchDischarges, PchInpatientCensus.amisys_nbr == PchDischarges.amisys_nbr
        )
        .filter(or_(PchInpatientCensus.amisys_nbr == member_id, PchDischarges.amisys_nbr == member_id))
    )

    return query.all()

@router.post("/pch-member/pch-hospitalization")
def create_or_update_hospitalization(
    payload: PchHospitalizationCreateUpdateSchema,
    db: Session = Depends(get_db)
):
    try:
        # ---------- Inpatient ----------
        inpatient = db.query(PchInpatientCensus).filter(
            PchInpatientCensus.amisys_nbr == payload.amisys_nbr
        ).first()

        if not inpatient:
            inpatient = PchInpatientCensus(amisys_nbr=payload.amisys_nbr)
            db.add(inpatient)

        if payload.inpatient:
            for field, value in payload.inpatient.dict(exclude_unset=True).items():
                setattr(inpatient, field, value)

        # ---------- Discharge ----------
        if payload.discharge:
            discharge = db.query(PchDischarges).filter(
                PchDischarges.amisys_nbr == payload.amisys_nbr
            ).first()

            if not discharge:
                discharge = PchDischarges(amisys_nbr=payload.amisys_nbr)
                db.add(discharge)

            for field, value in payload.discharge.dict(exclude_unset=True).items():
                setattr(discharge, field, value)

        db.commit()

        return {
            "success": True,
            "message": "Hospitalization record created/updated",
            "amisys_nbr": payload.amisys_nbr
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/pch-member/pch-hospitalization/{amisys_nbr}")
def patch_hospitalization(
    amisys_nbr: str,
    payload: PchHospitalizationCreateUpdateSchema,
    db: Session = Depends(get_db)
):
    try:
        inpatient = db.query(PchInpatientCensus).filter(
            PchInpatientCensus.amisys_nbr == amisys_nbr
        ).first()

        discharge = db.query(PchDischarges).filter(
            PchDischarges.amisys_nbr == amisys_nbr
        ).first()

        if payload.inpatient:
            if not inpatient:
                raise HTTPException(404, "Inpatient record not found")
            for field, value in payload.inpatient.dict(exclude_unset=True).items():
                setattr(inpatient, field, value)

        if payload.discharge:
            if not discharge:
                raise HTTPException(404, "Discharge record not found")
            for field, value in payload.discharge.dict(exclude_unset=True).items():
                setattr(discharge, field, value)

        db.commit()

        return {
            "success": True,
            "message": "Hospitalization record updated",
            "amisys_nbr": amisys_nbr
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/pch-member/pch-hospitalization/{amisys_nbr}")
def delete_hospitalization(
    amisys_nbr: str,
    db: Session = Depends(get_db)
):
    try:
        inpatient = db.query(PchInpatientCensus).filter(
            PchInpatientCensus.amisys_nbr == amisys_nbr
        ).first()

        discharge = db.query(PchDischarges).filter(
            PchDischarges.amisys_nbr == amisys_nbr
        ).first()

        if not inpatient and not discharge:
            raise HTTPException(404, "No hospitalization record found")

        if inpatient:
            db.delete(inpatient)

        if discharge:
            db.delete(discharge)

        db.commit()

        return {
            "success": True,
            "message": "Hospitalization records deleted",
            "amisys_nbr": amisys_nbr
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# **** Medical Claim History APIs ****
@router.get("/pch-member/pch-medical-claim-history/{member_id}", response_model=List[PchMedicalClaimHistorySchema])
def get_pch_member_by_npi(
    member_id: str,
    db: Session = Depends(get_db)
):

    claims = (
        db.query(
            PchMedClaims.status,
            PchMedClaims.pcp_npi,
            PchMedClaims.pcp_prac_first_name,
            PchMedClaims.pcp_prac_last_name,
            PchMedClaims.pcp_prac_middle_name,
            PchMedClaims.pcp_prac_title_name,
            PchMedClaims.service_end_date_dim_ck,
            PchMedClaims.service_start_date_dim_ck,
            PchMedClaims.claim_paid_date_dim_ck,
            PchMedClaims.place_of_serv_code,
            PchMedClaims.place_of_serv_desc,
            PchMedClaims.coverage_level,
            PchMedClaims.product_line,
            PchMedClaims.mcc,
            PchMedClaims.proc_code,
            PchMedClaims.rev_code,
            PchMedClaims.primary_diag_code,
            PchMedClaims.pay_to_inst_name,
            PchMedClaims.admission_date_dim_ck,
            PchMedClaims.dischg_date_dim_ck,
            PchMedClaims.source
        )
        .filter(PchMedClaims.member_amisys_nbr == member_id)
        .all()
    )

    result = []

    for c in claims:

        # Combine name → full_name
        full_name = " ".join(
            filter(None, [
                c.pcp_prac_title_name,
                c.pcp_prac_first_name,
                c.pcp_prac_middle_name,
                c.pcp_prac_last_name
            ])
        )

        result.append(
            PchMedicalClaimHistorySchema(
                status=c.status,
                pcp_npi=c.pcp_npi,
                full_name=full_name,
                service_start_date_dim_ck=c.service_start_date_dim_ck,
                service_end_date_dim_ck=c.service_end_date_dim_ck,
                claim_paid_date_dim_ck=c.claim_paid_date_dim_ck,
                place_of_serv_code=c.place_of_serv_code,
                place_of_serv_desc=c.place_of_serv_desc,
                coverage_level=c.coverage_level,
                product_line=c.product_line,
                mcc=c.mcc,
                proc_code=c.proc_code,
                rev_code=c.rev_code,
                primary_diag_code=c.primary_diag_code,
                pay_to_inst_name=c.pay_to_inst_name,
                admission_date_dim_ck=c.admission_date_dim_ck,
                dischg_date_dim_ck=c.dischg_date_dim_ck,
                source = c.source
            )
        )

    return result

@router.post("/pch-member/pch-medical-claim-history", status_code=201)
def create_pch_medical_claim(
    payload: PchMedicalClaimCreateSchema,
    db: Session = Depends(get_db)
):
    try:
        claim = PchMedClaims(
            member_amisys_nbr=payload.member_amisys_nbr,
            status=payload.status,

            pcp_npi=payload.pcp_npi,
            pcp_prac_title_name=payload.pcp_prac_title_name,
            pcp_prac_first_name=payload.pcp_prac_first_name,
            pcp_prac_middle_name=payload.pcp_prac_middle_name,
            pcp_prac_last_name=payload.pcp_prac_last_name,

            service_start_date_dim_ck=payload.service_start_date_dim_ck,
            service_end_date_dim_ck=payload.service_end_date_dim_ck,
            claim_paid_date_dim_ck=payload.claim_paid_date_dim_ck,

            place_of_serv_code=payload.place_of_serv_code,
            place_of_serv_desc=payload.place_of_serv_desc,

            coverage_level=payload.coverage_level,
            product_line=payload.product_line,
            mcc=payload.mcc,

            proc_code=payload.proc_code,
            rev_code=payload.rev_code,
            primary_diag_code=payload.primary_diag_code,

            pay_to_inst_name=payload.pay_to_inst_name,
            admission_date_dim_ck=payload.admission_date_dim_ck,
            dischg_date_dim_ck=payload.dischg_date_dim_ck,

            source=payload.source
        )

        db.add(claim)
        db.commit()
        db.refresh(claim)

        return {
            "success": True,
            "pk_id": str(claim.pk_id),
            "member_amisys_nbr": claim.member_amisys_nbr
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/pch-member/pch-medical-claim-history/{pk_id}")
def update_pch_medical_claim(
    pk_id: str,
    payload: PchMedicalClaimUpdateSchema,
    db: Session = Depends(get_db)
):
    try:
        claim = db.query(PchMedClaims).filter(
            PchMedClaims.pk_id == pk_id
        ).first()

        if not claim:
            raise HTTPException(status_code=404, detail="Medical claim not found")

        for field, value in payload.dict(exclude_unset=True).items():
            setattr(claim, field, value)

        db.commit()
        db.refresh(claim)

        return {
            "success": True,
            "message": "Medical claim updated successfully",
            "pk_id": str(claim.pk_id)
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/pch-member/pch-medical-claim-history/{pk_id}")
def delete_pch_medical_claim(
    pk_id: str,
    db: Session = Depends(get_db)
):
    try:
        claim = db.query(PchMedClaims).filter(
            PchMedClaims.pk_id == pk_id
        ).first()

        if not claim:
            raise HTTPException(status_code=404, detail="Medical claim not found")

        db.delete(claim)
        db.commit()

        return {
            "success": True,
            "message": "Medical claim deleted successfully",
            "pk_id": pk_id
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# **** RX Claim History APIs ****
@router.get("/pch-member/pch-rx-claim-history/{member_id}", response_model=List[PchRXClaimHistorySchema])
def get_pch_member_by_npi(
    member_id: str,
    db: Session = Depends(get_db)
):
    claims = (
        db.query(
            PchRXClaim.status,
            PchRXClaim.member_amisys_nbr,
            PchRXClaim.prescribing_npi,
            PchRXClaim.prescribing_npi_name,
            PchRXClaim.pcp_npi,
            PchRXClaim.pcp_prac_first_name,
            PchRXClaim.pcp_prac_middle_name,
            PchRXClaim.pcp_prac_last_name,
            PchRXClaim.pcp_inst_name,
            PchRXClaim.drug_type_code,
            PchRXClaim.drug_desc,
            PchRXClaim.drug_type_desc,
            PchRXClaim.refill_nbr,
            PchRXClaim.formulary_ind,
            PchRXClaim.fill_date_dim_ck,
            PchRXClaim.prescription_date_dim_ck,
            PchRXClaim.coverage_level,
            PchRXClaim.product_line,
            PchRXClaim.mcc,
            PchRXClaim.source
        )
        .filter(PchRXClaim.member_amisys_nbr == member_id)
        .all()
    )

    result = []

    for c in claims:

        pcp_full_name = " ".join(
            filter(None, [
                c.pcp_prac_first_name,
                c.pcp_prac_middle_name,
                c.pcp_prac_last_name
            ])
        )

        result.append(
            PchRXClaimHistorySchema(
                status=c.status,
                member_amisys_nbr=c.member_amisys_nbr,
                prescribing_npi=c.prescribing_npi,
                prescribing_npi_name=c.prescribing_npi_name,

                pcp_npi=c.pcp_npi,
                pcp_name=pcp_full_name,
                pcp_inst_name=c.pcp_inst_name,

                drug_type_code=c.drug_type_code,
                drug_desc=c.drug_desc,
                drug_type_desc=c.drug_type_desc,
                refill_nbr=c.refill_nbr,
                formulary_ind=c.formulary_ind,

                fill_date_dim_ck=c.fill_date_dim_ck,
                prescription_date_dim_ck=c.prescription_date_dim_ck,

                coverage_level=c.coverage_level,
                product_line=c.product_line,
                mcc=c.mcc,
                source = c.source
            )
        )

    return result

@router.post(
    "/pch-member/pch-rx-claim-history"
)
def create_pch_rx_claim(
    payload: PchRXClaimCreateSchema,
    db: Session = Depends(get_db)
):
    try:
        new_claim = PchRXClaim(
            status=payload.status,
            member_amisys_nbr=payload.member_amisys_nbr,

            prescribing_npi=payload.prescribing_npi,
            prescribing_npi_name=payload.prescribing_npi_name,

            pcp_npi=payload.pcp_npi,
            pcp_prac_first_name=payload.pcp_prac_first_name,
            pcp_prac_middle_name=payload.pcp_prac_middle_name,
            pcp_prac_last_name=payload.pcp_prac_last_name,
            pcp_inst_name=payload.pcp_inst_name,

            drug_type_code=payload.drug_type_code,
            drug_desc=payload.drug_desc,
            drug_type_desc=payload.drug_type_desc,

            refill_nbr=payload.refill_nbr,
            formulary_ind=payload.formulary_ind,

            fill_date_dim_ck=payload.fill_date_dim_ck,
            prescription_date_dim_ck=payload.prescription_date_dim_ck,

            coverage_level=payload.coverage_level,
            product_line=payload.product_line,
            mcc=payload.mcc,

            source=payload.source
        )

        db.add(new_claim)
        db.commit()
        db.refresh(new_claim)

        return {
            "success": True,
            "pk_id": str(new_claim.pk_id),
            "member_amisys_nbr": new_claim.member_amisys_nbr
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/pch-member/pch-rx-claim-history/{pk_id}")
def update_pch_rx_claim(
    pk_id: str,
    payload: PchRXClaimUpdateSchema,
    db: Session = Depends(get_db)
):
    try:
        claim = db.query(PchRXClaim).filter(
            PchRXClaim.pk_id == pk_id
        ).first()

        if not claim:
            raise HTTPException(status_code=404, detail="RX claim not found")

        for field, value in payload.dict(exclude_unset=True).items():
            setattr(claim, field, value)

        db.commit()
        db.refresh(claim)

        return {
            "success": True,
            "message": "RX claim updated successfully",
            "pk_id": str(claim.pk_id)
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/pch-member/pch-rx-claim-history/{pk_id}")
def delete_pch_rx_claim(
    pk_id: str,
    db: Session = Depends(get_db)
):
    try:
        claim = db.query(PchRXClaim).filter(
            PchRXClaim.pk_id == pk_id
        ).first()

        if not claim:
            raise HTTPException(status_code=404, detail="RX claim not found")

        db.delete(claim)
        db.commit()

        return {
            "success": True,
            "message": "RX claim deleted successfully",
            "pk_id": pk_id
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# **** Top RX Expense API ****
@router.get("/pch-member/pch-top-rx-expense/{member_id}", response_model=List[PchTopRxExpenseSchema])
def get_pch_top_rx_expense(
    member_id: str,
    db: Session = Depends(get_db)
):
    results = (
        db.query(
            PchRXClaim.drug_desc.label("name"),
            func.sum(cast(PchRXClaim.paid_amt, Numeric)).label("value")
        )
        .filter(PchRXClaim.member_amisys_nbr == member_id)
        .group_by(PchRXClaim.drug_desc)
        .order_by(desc(func.sum(cast(PchRXClaim.paid_amt, Numeric))))
        .all()
    )

    return results

# ****** COVERAGES *****************
@router.get(
    "/pch-member/coverage/{pat_id}",
    response_model=List[CoverageResponseSchema]
)
def get_member_coverages(
    pat_id: str,
    db: Session = Depends(get_db)
):
    coverages = (
        db.query(
            PchPatientCoverages.pat_id.label("pat_id"),
            PchPatientCoverages.cov_type.label("cov_type"),
            PchPatientCoverages.cov_status.label("status"),
            PchPatientCoverages.cov_car_type.label("carrier_type"),
            PchPatientCoverages.cov_car_nam.label("cov_car_name"),
            PchPatientCoverages.effective_start_date.label("effective_start_date"),
            PchPatientCoverages.effective_end_date.label("effective_end_date"),
            PchPatientCoverages.cov_rel.label("relationship"),
            PchPatientCoverages.pk_id.label('pk_id'),
            PchPatientCoverages.source.label('source')
        )
        .filter(PchPatientCoverages.member_id == pat_id)
        .order_by(
            PchPatientCoverages.cov_type.desc(),
            PchPatientCoverages.cov_start_date.desc()
        )
        .all()
    )

    if not coverages:
        return []

    return coverages

@router.post("/patient-coverages")
def add_patient_coverage(
    payload: PatientCoveragesSchema,
    db: Session = Depends(get_db)
):
    if payload.pat_id is None or payload.member_id is None:
        raise HTTPException(400, "patient id and member id are required fields")
    
    coverage = PchPatientCoverages(
        **payload.dict(exclude_unset=True),
        cov_status=payload.cov_status or "Active"
    )

    db.add(coverage)
    db.commit()
    db.refresh(coverage)

    return {
        "success": True,
        "pk_id": coverage.pk_id,
        "message": "Coverage added successfully"
    }


@router.patch("/patient-coverages/{pk_id}")
def update_patient_coverage(
    pk_id: UUID,
    payload: PatientCoveragesSchema,
    db: Session = Depends(get_db)
):
    coverage = db.query(PchPatientCoverages).filter(
        PchPatientCoverages.pk_id == pk_id
    ).first()

    if not coverage:
        raise HTTPException(404, "Coverage not found")

    for field, value in payload.dict(exclude_unset=True).items():
        setattr(coverage, field, value)

    db.commit()
    db.refresh(coverage)

    return {
        "success": True,
        "pk_id": coverage.pk_id,
        "message": "Coverage updated successfully"
    }


@router.delete("/patient-coverages/{pk_id}")
def delete_patient_coverage(
    pk_id: UUID,
    db: Session = Depends(get_db)
):
    coverage = db.query(PchPatientCoverages).filter(
        PchPatientCoverages.pk_id == pk_id
    ).first()

    if not coverage:
        raise HTTPException(404, "Coverage not found")

    db.delete(coverage)
    db.commit()

    return {
        "success": True,
        "message": "Coverage deleted successfully"
    }

@router.get("/payer/search")
def search_payers(
    payer_id: str,
    db: Session = Depends(get_db)
):
    search_term = f"%{payer_id}%"

    results = (
        db.query(
            distinct(LupPayers.payer_name).label("payer_name"),
            LupPayers.payer_type,
            LupPayers.payer_id
        )
        .filter(
            or_(
                LupPayers.payer_id.ilike(search_term),
                LupPayers.payer_name.ilike(search_term)
            )
        )
        .limit(20)
        .all()
    )

    return [
        {
            "payer_id": r.payer_id,
            "payer_name": r.payer_name,
            "payer_type": r.payer_type
        }
        for r in results
    ]
