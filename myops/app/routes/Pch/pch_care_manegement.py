from typing import List
from fastapi import (
    APIRouter,
    Depends,
    HTTPException
)
from sqlalchemy.orm import  Session
from app.core.helpers import age_in_range, build_dropdown, normalize_gender, normalize_to_list
from app.db.session import get_db
from app.models.pch_member.LupImmunizationScreening import LupImmunizationScreening
from app.models.pch_member.PchCareGapDetail import PchCareGapDetail
from app.models.pch_member.PchIcd10 import PchICD10Mapping
from app.models.pch_member.PchMedClaims import PchMedClaims
from app.models.pch_member.PchMemberMeasurement import PchMemberMeasurement
from app.models.pch_member.PchMemberRoster import PchMemberRoster
from app.models.pch_member.PchMemberVitals import PchMemberVitals
from app.models.pch_member.PchMemberImmunization import PchMemberImmunization
from app.models.pch_member.PchMemberScreening import PchMemberScreening
from sqlalchemy import Integer, literal, or_, func, case, and_, DATETIME, literal_column, distinct, text, desc, asc, select, cast, Date, String
from collections import defaultdict

from app.models.pch_member.PchPreventiveCare import PchMemberPreventiveCare
from app.schemas.Pch.pch_schema import PchCareGapDetailSchema, PchDiagnosisCreateSchema, PchDiagnosisSchema, PchDiagnosisDetailSchema,PchHealthCategorySchema, PchImmunizationSchema, PchMeasurementSchema, PchPreventiveCareSchema, PchRiskCategorySchema, PchScreeningSchema, PchVitalsSchema

router = APIRouter(tags=["PCH MEMBER CARE MANAGEMENT ROUTES"])

# *********** PCH MEASUREMENT ROUTES ***********
@router.get("/pch-care-management/pch-measurement/{member_id}", response_model=List[PchMeasurementSchema])
def get_pch_measurement(
    member_id: str,
    db: Session = Depends(get_db)
):
    pch_measurement = (
    db.query(PchMemberMeasurement)
      .filter(PchMemberMeasurement.member_id == member_id)
      .order_by(cast(PchMemberMeasurement.report_date, Date).desc())
      .limit(3)
      .all()
)
    return pch_measurement


@router.post("/pch-care-management/pch-measurement")
def create_pch_measurement(
    measurement: PchMeasurementSchema,
    db: Session = Depends(get_db)
):
    new_measurement = PchMemberMeasurement(
        member_id=measurement.member_id,
        report_date=measurement.report_date,
        bmi=measurement.bmi,
        height=measurement.height,
        weight=measurement.weight
    )
    db.add(new_measurement)
    db.commit()
    db.refresh(new_measurement)
    return new_measurement

@router.patch("/pch-care-management/pch-measurement/{pk_id}")
def update_pch_measurement(
    pk_id: str,
    measurement: PchMeasurementSchema,
    db: Session = Depends(get_db)
):
    existing_measurement = db.query(PchMemberMeasurement).filter(PchMemberMeasurement.pk_id == pk_id).first()
    if not existing_measurement:
        return {"error": "Measurement not found"}

    if measurement.report_date is not None:
        existing_measurement.report_date = measurement.report_date
    if measurement.bmi is not None:
        existing_measurement.bmi = measurement.bmi
    if measurement.height is not None:
        existing_measurement.height = measurement.height
    if measurement.weight is not None:
        existing_measurement.weight = measurement.weight

    db.commit()
    db.refresh(existing_measurement)
    return existing_measurement

@router.delete("/pch-care-management/pch-measurement/{pk_id}")
def delete_pch_measurement(
    pk_id: str,
    db: Session = Depends(get_db)
):
    measurement = (
        db.query(PchMemberMeasurement)
        .filter(PchMemberMeasurement.pk_id == pk_id)
        .first()
    )

    if not measurement:
        raise HTTPException(status_code=404, detail="Measurement not found")

    db.delete(measurement)
    db.commit()

    return "PCH Measurement Deleted Sucessfully."


# *********** PCH PREVENTIVE CARE ROUTES ***********

@router.get("/pch-care-management/pch-preventive-care/{member_id}", response_model=List[PchPreventiveCareSchema])
def get_pch_preventive_care(
    member_id: str,
    db: Session = Depends(get_db)
):
    query = (
        db.query(PchMemberPreventiveCare)
        .filter(PchMemberPreventiveCare.member_id == member_id)
        .order_by(
            cast(PchMemberPreventiveCare.onboarding_date, Date).desc()
        )
        .limit(3)
        .all()
    )
    return query

@router.post("/pch-care-management/pch-preventive-care")
def create_pch_preventive_care(
    preventive_care: PchPreventiveCareSchema,
    db: Session = Depends(get_db)
):
    new_preventive_care = PchMemberPreventiveCare(
        member_id=preventive_care.member_id,
        status=preventive_care.status,
        care_type=preventive_care.care_type,
        care_name=preventive_care.care_name,
        diagnosis=preventive_care.diagnosis,
        onboarding_date=preventive_care.onboarding_date
    )
    db.add(new_preventive_care)
    db.commit()
    db.refresh(new_preventive_care)
    return new_preventive_care
    
@router.patch("/pch-care-management/pch-preventive-care/{pk_id}")
def update_pch_preventive_care(
    pk_id: str,
    preventive_care: PchPreventiveCareSchema,
    db: Session = Depends(get_db)
):
    existing_preventive_care = db.query(PchMemberPreventiveCare).filter(PchMemberPreventiveCare.pk_id == pk_id).first()
    if not existing_preventive_care:
        return {"error": "Preventive Care not found"}

    if preventive_care.status is not None:
        existing_preventive_care.status = preventive_care.status
    if preventive_care.care_type is not None:
        existing_preventive_care.care_type = preventive_care.care_type
    if preventive_care.care_name is not None:
        existing_preventive_care.care_name = preventive_care.care_name
    if preventive_care.diagnosis is not None:
        existing_preventive_care.diagnosis = preventive_care.diagnosis
    if preventive_care.onboarding_date is not None:
        existing_preventive_care.onboarding_date = preventive_care.onboarding_date

    db.commit()
    db.refresh(existing_preventive_care)
    return existing_preventive_care

@router.delete("/pch-care-management/pch-preventive-care/{pk_id}")
def delete_pch_preventive_care(
    pk_id: str,
    db: Session = Depends(get_db)
):
    preventive_care = (
        db.query(PchMemberPreventiveCare)
        .filter(PchMemberPreventiveCare.pk_id == pk_id)
        .first()
    )

    if not preventive_care:
        raise HTTPException(status_code=404, detail="Preventive Care not found")

    db.delete(preventive_care)
    db.commit()

    return "PCH Preventive Care Deleted Sucessfully"

# *********** PCH HEALTH CATEGORY ROUTES ***********    

@router.get("/pch-care-management/pch-health-category/{member_id}",response_model=List[PchHealthCategorySchema])
def get_pch_health_category(
    member_id: str,
    db: Session = Depends(get_db)
):
    subquery = (
        db.query(
            PchMemberRoster.population_health_category.label("category"),
            func.max(cast(PchMemberRoster.report_date, Date)).label("latest_report_date"),
            func.max(PchMemberRoster.source).label("source"),
            func.count(PchMemberRoster.population_health_category).label("count")
        )
        .filter(PchMemberRoster.amisys_number == member_id)
        .group_by(PchMemberRoster.population_health_category)
        .subquery()
    )

    result = (
        db.query(
            subquery.c.category.label("population_health_category"),
            subquery.c.latest_report_date.label("report_date"),
            subquery.c.source.label("source"),
            subquery.c.count.label("count"),
        )
        .order_by(subquery.c.latest_report_date.desc())
        .limit(3)
        .all()
    )

    return result

# @router.post("/pch-care-management/pch-health-category")
# def create_pch_health_category(
#     health_category: PchHealthCategorySchema,
#     db: Session = Depends(get_db)
# ):
#     new_health_category = PchMemberRoster(
#         population_health_category=health_category.population_health_category,
#         report_date=health_category.report_date,
#         member_status=health_category.member_status
#     )
#     db.add(new_health_category)
#     db.commit()
#     db.refresh(new_health_category)
#     return new_health_category

@router.patch("/pch-care-management/pch-health-category/{pk_id}")
def update_pch_health_category(
    pk_id: str,
    health_category: PchHealthCategorySchema,
    db: Session = Depends(get_db)
):
    existing_health_category = db.query(PchMemberRoster).filter(PchMemberRoster.pk_id == pk_id).first()
    if not existing_health_category:
        return {"error": "Health Category not found"}

    if health_category.population_health_category is not None:
        existing_health_category.population_health_category = health_category.population_health_category
    if health_category.report_date is not None:
        existing_health_category.report_date = health_category.report_date
    if health_category.member_status is not None:
        existing_health_category.member_status = health_category.member_status

    db.commit()
    db.refresh(existing_health_category)
    return existing_health_category

@router.delete("/pch-care-management/pch-health-category/{pk_id}")
def delete_pch_health_category(
    pk_id: str,
    db: Session = Depends(get_db)
):
    health_category = (
        db.query(PchMemberRoster)
        .filter(PchMemberRoster.pk_id == pk_id)
        .first()
    )

    if not health_category:
        raise HTTPException(status_code=404, detail="Health Category not found")

    db.delete(health_category)
    db.commit()

    return "PCH Health Category Deleted Sucessfully."

# *********** PCH DIAGNOSIS ROUTES ***********

@router.get("/pch-care-management/pch-diagnosis/{member_id}", response_model=List[PchDiagnosisSchema])
def get_pch_diagnosis(
    member_id: str,
    db: Session = Depends(get_db)
):
    claims = (
        db.query(
            PchMedClaims.primary_diag_code,
            PchMedClaims.status,
            PchMedClaims.report_date,
            PchMedClaims.source 
        )
        .filter(PchMedClaims.member_amisys_nbr == member_id)
        .order_by(cast(PchMedClaims.report_date, Date).desc())
        .all()
    )

    if not claims:
        return []

    diag_map = defaultdict(lambda: {"count": 0, "records": []})

    # ---------- GROUP CLAIMS ----------
    for diag_code, status, report_date, source in claims:
        diag_map[diag_code]["count"] += 1
        diag_map[diag_code]["records"].append({
            "status": status,
            "date": report_date,
            "source": source      
        })

    sorted_diagnoses = sorted(
        diag_map.items(),
        key=lambda x: max(r["date"] for r in x[1]["records"]),
        reverse=True
    )[:3]

    results = []

    for diag_code, diag_info in sorted_diagnoses:

        icd_map = (
            db.query(
                PchICD10Mapping.short_description_valid_icd_10_fy2025,
                PchICD10Mapping.long_description_valid_icd_10_fy2025
            )
            .filter(PchICD10Mapping.code == diag_code)
            .first()
        )

        short_desc = icd_map.short_description_valid_icd_10_fy2025 if icd_map else None
        long_desc = icd_map.long_description_valid_icd_10_fy2025 if icd_map else None
        description = long_desc or short_desc or diag_code

        desc_lower = (description or "").lower()
        diagnosis_type = (
            "Chronic" if "chronic" in desc_lower else
            "Acute" if "acute" in desc_lower else
            "Unknown"
        )

        most_recent = max(
            diag_info["records"],
            key=lambda r: r["date"]
        )

        results.append(
            PchDiagnosisSchema(
                primary_diagnosis_code=diag_code, 
                diagnosis=description,
                type=diagnosis_type,
                count=diag_info["count"],
                date=most_recent["date"],
                source=most_recent["source"]
            )
        )

    return results

@router.get("/pch-care-management/pch-diagnosis-details/{member_id}")
def get_pch_diagnosis_details(
    member_id: str,
    diagnosis_code: str,
    db: Session = Depends(get_db)
):
    rows = (
        db.query(
            PchMedClaims.primary_diag_code,
            PchMedClaims.status,
            PchMedClaims.report_date,
            PchMedClaims.source
        )
        .filter(
            PchMedClaims.member_amisys_nbr == member_id,
            PchMedClaims.primary_diag_code == diagnosis_code
        )
        .distinct(cast(PchMedClaims.report_date, Date))
        .order_by(
            cast(PchMedClaims.report_date, Date).desc()
        )
        .all()
    )

    if not rows:
        return []

    icd_map = (
        db.query(
            PchICD10Mapping.short_description_valid_icd_10_fy2025,
            PchICD10Mapping.long_description_valid_icd_10_fy2025
        )
        .filter(PchICD10Mapping.code == diagnosis_code)
        .first()
    )

    short_desc = (
        icd_map.short_description_valid_icd_10_fy2025
        if icd_map else None
    )
    long_desc = (
        icd_map.long_description_valid_icd_10_fy2025
        if icd_map else None
    )
    description = long_desc or short_desc or diagnosis_code
    desc_lower = description.lower()
    diagnosis_type = (
        "Chronic" if "chronic" in desc_lower else
        "Acute" if "acute" in desc_lower else
        "Unknown"
    )
    return [
        {
            "primary_diag_code": r.primary_diag_code,
            "diagnosis": description,
            "type": diagnosis_type,
            "status": r.status,
            "report_date": r.report_date,
            "source": r.source,
        }
        for r in rows
    ]


@router.post(
    "/pch-care-management/pch-diagnosis",
    status_code=201
)
def create_pch_diagnosis(
    payload: PchDiagnosisCreateSchema,
    db: Session = Depends(get_db)
):
    try:
        claim = PchMedClaims(
            member_amisys_nbr=payload.member_amisys_nbr,
            primary_diag_code=payload.primary_diag_code,
            status=payload.status,
            report_date=payload.report_date,
            source=payload.source
        )

        db.add(claim)
        db.commit()
        db.refresh(claim)

        return {
            "success": True,
            "pk_id": str(claim.pk_id),
            "member_amisys_nbr": claim.member_amisys_nbr,
            "primary_diag_code": claim.primary_diag_code
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/pch-care-management/pch-diagnosis/{pk_id}")
def update_pch_diagnosis(
    pk_id: str,
    diagnosis: PchDiagnosisSchema,
    db: Session = Depends(get_db)
):
    existing_diagnosis = db.query(PchMedClaims).filter(PchMedClaims.pk_id == pk_id).first()
    if not existing_diagnosis:
        return {"error": "Diagnosis not found"}

    db.commit()
    db.refresh(existing_diagnosis)
    return existing_diagnosis

@router.delete("/pch-care-management/pch-diagnosis/{pk_id}")
def delete_pch_diagnosis(
    pk_id: str,
    db: Session = Depends(get_db)
):
    diagnosis = (
        db.query(PchMedClaims)
        .filter(PchMedClaims.pk_id == pk_id)
        .first()
    )

    if not diagnosis:
        raise HTTPException(status_code=404, detail="Diagnosis not found")

    db.delete(diagnosis)
    db.commit()

    return "PCH Diagnosis Deleted Sucessfully."

#*********** PCH VITALS ROUTES ***********

@router.get("/pch-care-management/pch-vitals/{member_id}", response_model=List[PchVitalsSchema])
def get_pch_member_by_npi(
    member_id: str,
    db: Session = Depends(get_db)
):
    pch_vitals = (
        db.query(PchMemberVitals)
        .filter(PchMemberVitals.member_id == member_id)
        .order_by(
            cast(PchMemberVitals.report_date, Date).desc()
        )
        .limit(3)
        .all()
    )
    return pch_vitals

@router.post("/pch-care-management/pch-vitals")
def create_pch_vitals(
    vitals: PchVitalsSchema,
    db: Session = Depends(get_db)
):
    new_vitals = PchMemberVitals(
        member_id=vitals.member_id,
        report_date=vitals.report_date,
        systolic=vitals.systolic,
        diastolic=vitals.diastolic
    )
    db.add(new_vitals)
    db.commit()
    db.refresh(new_vitals)
    return new_vitals


@router.patch("/pch-care-management/pch-vitals/{pk_id}")
def update_pch_vitals(
    pk_id: str,
    vitals: PchVitalsSchema,
    db: Session = Depends(get_db)
):
    existing_vitals = db.query(PchMemberVitals).filter(PchMemberVitals.pk_id == pk_id).first()
    if not existing_vitals:
        return {"error": "Vitals not found"}

    if vitals.report_date is not None:
        existing_vitals.report_date = vitals.report_date
    if vitals.systolic is not None:
        existing_vitals.systolic = vitals.systolic
    if vitals.diastolic is not None:
        existing_vitals.diastolic = vitals.diastolic

    db.commit()
    db.refresh(existing_vitals)
    return existing_vitals

@router.delete("/pch-care-management/pch-vitals/{pk_id}")
def delete_pch_vitals(
    pk_id: str,
    db: Session = Depends(get_db)
):
    vitals = (
        db.query(PchMemberVitals)
        .filter(PchMemberVitals.pk_id == pk_id)
        .first()
    )

    if not vitals:
        raise HTTPException(status_code=404, detail="Vitals not found")

    db.delete(vitals)
    db.commit()

    return "PCH Vitals Deleted Sucessfully."

# *********** CARE GAP DETAIL ROUTES ***********

@router.get("/pch-care-management/pch-care-gaps/{member_id}", response_model=List[PchCareGapDetailSchema])
def get_pch_care_gaps(
    member_id: str,
    db: Session = Depends(get_db)
):
    pch_care_gaps = (
        db.query(PchCareGapDetail)
        .filter(PchCareGapDetail.mem_id == member_id)
        .order_by(
            cast(PchCareGapDetail.report_date, Date).desc()
        )
        .limit(3)
        .all()
    )
    return pch_care_gaps

@router.post("/pch-care-management/pch-care-gaps")
def create_pch_care_gap(
    care_gap: PchCareGapDetailSchema,
    db: Session = Depends(get_db)
):
    new_care_gap = PchCareGapDetail(
        mem_id=care_gap.mem_id,
        measure_status=care_gap.measure_status,
        gap_type=care_gap.gap_type,
        measure=care_gap.measure,
        service_strt=care_gap.service_strt,
        service_end=care_gap.service_end,
        source=care_gap.source
    )

    db.add(new_care_gap)
    db.commit()
    db.refresh(new_care_gap)

    return new_care_gap


@router.patch("/pch-care-management/pch-care-gaps/{pk_id}")
def update_pch_care_gaps(
    pk_id: str,
    risk_category: PchCareGapDetailSchema,
    db: Session = Depends(get_db)
):
    existing_pch_care_gap = db.query(PchCareGapDetail).filter(PchCareGapDetail.pk_id == pk_id).first()
    if not existing_pch_care_gap:
        return {"error": "Risk Category not found"}
    

    if risk_category.service_strt is not None:
        existing_pch_care_gap.service_strt = risk_category.service_strt
        
    if risk_category.service_end is not None:
        existing_pch_care_gap.service_end = risk_category.service_end
        
    if risk_category.measure_status is not None:
        existing_pch_care_gap.measure_status = risk_category.measure_status
        
    if risk_category.gap_type is not None:
        existing_pch_care_gap.gap_type = risk_category.gap_type
        
    if risk_category.measure is not None:
        existing_pch_care_gap.measure = risk_category.measure

    db.commit()
    db.refresh(existing_pch_care_gap)

@router.delete("/pch-care-management/pch-care-gaps/{pk_id}", status_code=204)
def delete_pch_care_gaps(
    pk_id: str,
    db: Session = Depends(get_db)
):
    care_gap = (
        db.query(PchCareGapDetail)
        .filter(PchCareGapDetail.pk_id == pk_id)
        .first()
    )

    if not care_gap:
        raise HTTPException(status_code=404, detail="Risk Category not found")

    db.delete(care_gap)
    db.commit()

    return "PCH Care Gaps Deleted Sucessfully."


# *********** RISK CATEGORY ROUTES ***********
@router.get(
    "/pch-care-management/pch-risk-category/{member_id}",
    response_model=List[PchRiskCategorySchema]
)
def get_pch_risk_category(
    member_id: str,
    db: Session = Depends(get_db)
):
    ranked_subquery = (
        db.query(
            PchMemberRoster.primary_risk_category.label("primary_risk_category"),
            PchMemberRoster.pk_id.label("pk_id"),
            cast(PchMemberRoster.report_date, Date).label("report_date"),
            PchMemberRoster.source.label("source"),

            func.count(PchMemberRoster.primary_risk_category)
            .over(partition_by=PchMemberRoster.primary_risk_category)
            .label("count"),

            func.row_number()
            .over(
                partition_by=PchMemberRoster.primary_risk_category,
                order_by=cast(PchMemberRoster.report_date, Date).desc()
            )
            .label("rn")
        )
        .filter(PchMemberRoster.amisys_number == member_id)
        .subquery()
    )

    result = (
        db.query(
            ranked_subquery.c.primary_risk_category,
            ranked_subquery.c.pk_id,         
            ranked_subquery.c.report_date,
            ranked_subquery.c.count,
            ranked_subquery.c.source,
        )
        .filter(ranked_subquery.c.rn == 1)
        .order_by(ranked_subquery.c.report_date.desc())
        .limit(3)
        .all()
    )

    return result

@router.post("/pch-care-management/pch-risk-category")
def create_pch_health_category(
    health_category: PchRiskCategorySchema,
    db: Session = Depends(get_db)
):
    new_health_category = PchMemberRoster(
        primary_risk_category=health_category.primary_risk_category,
        report_date=health_category.report_date,
        member_status=health_category.member_status,
        amisys_number = health_category.member_id
    )
    db.add(new_health_category)
    db.commit()
    db.refresh(new_health_category)
    return new_health_category

@router.patch("/pch-care-management/pch-risk-category/{pk_id}")
def update_pch_risk_category(
    pk_id: str,
    risk_category: PchRiskCategorySchema,
    db: Session = Depends(get_db)
):
    existing_risk_category = db.query(PchMemberRoster).filter(PchMemberRoster.pk_id == pk_id).first()
    if not existing_risk_category:
        return {"error": "Risk Category not found"}

    if risk_category.primary_risk_category is not None:
        existing_risk_category.primary_risk_category = risk_category.primary_risk_category
    if risk_category.report_date is not None:
        existing_risk_category.report_date = risk_category.report_date
    if risk_category.member_status is not None:
        existing_risk_category.member_status = risk_category.member_status

    db.commit()
    db.refresh(existing_risk_category)

@router.delete("/pch-care-management/pch-risk-category/{pk_id}")
def delete_pch_risk_category(
    pk_id: str,
    db: Session = Depends(get_db)
):
    risk_category = (
        db.query(PchMemberRoster)
        .filter(PchMemberRoster.pk_id == pk_id)
        .first()
    )

    if not risk_category:
        raise HTTPException(status_code=404, detail="Risk Category not found")

    db.delete(risk_category)
    db.commit()

    return "PCH Risk Category Deleted Sucessfully."


#*********** PCH IMMUNIZATION ROUTES ***********

@router.get(
    "/pch-care-management/pch-immunization/{member_id}",
    response_model=list[PchImmunizationSchema]
)
def get_pch_immunization_by_member(
    member_id: str,
    age: int | None = None,
    gender: str | None = None,
    db: Session = Depends(get_db)
):
    
    if gender in ["M", "Male","MALE"]:
        gender = "Male"
    elif gender in ["F", "Female","FEMALE"]:
        gender = "Female"
    
    member_has_data = (
        db.query(PchMemberImmunization.pk_id)
        .filter(PchMemberImmunization.member_id == member_id)
        .first()
        is not None
    )

    if member_has_data:
        records = (
            db.query(
                PchMemberImmunization.complete_date,
                PchMemberImmunization.status,
                PchMemberImmunization.fields,
                PchMemberImmunization.member_id,
                LupImmunizationScreening.type,
                LupImmunizationScreening.category,
                LupImmunizationScreening.procedure,
                LupImmunizationScreening.requirement,
                LupImmunizationScreening.condition,
                PchMemberImmunization.pk_id,
                PchMemberImmunization.immunization_id
            )
            .outerjoin(
                LupImmunizationScreening,
                PchMemberImmunization.immunization_id
                == LupImmunizationScreening.pk_id
            )
            .filter(PchMemberImmunization.member_id == member_id)
            .order_by(
                cast(PchMemberImmunization.complete_date, Date).desc()
            )
            .all()
        )
        return records

    age_condition = True
    if age is not None:
        min_age = cast(
            func.split_part(LupImmunizationScreening.age_range, '-', 1),
            Integer
        )
        max_age = cast(
            func.split_part(LupImmunizationScreening.age_range, '-', 2),
            Integer
        )
        plus_age = cast(
            func.replace(LupImmunizationScreening.age_range, '+', ''),
            Integer
        )

        age_condition = case(
            (
                LupImmunizationScreening.age_range.like('%-%'),
                (age >= min_age) & (age <= max_age)
            ),
            (
                LupImmunizationScreening.age_range.like('%+'),
                age >= plus_age
            ),
            else_=False
        )

    records = (
        db.query(
            literal(None).label("complete_date"),
            literal(None).label("status"),
            literal(None).label("fields"),
            literal(member_id).label("member_id"),
            LupImmunizationScreening.type,
            LupImmunizationScreening.category,
            LupImmunizationScreening.procedure,
            LupImmunizationScreening.requirement,
            LupImmunizationScreening.condition,
            literal(None).label("pk_id"),
            LupImmunizationScreening.pk_id.label("immunization_id")
        )
        .filter(
            LupImmunizationScreening.type.ilike("immunization"),
            age_condition,
            LupImmunizationScreening.gender.in_([gender, "ALL"])
            if gender else True
        )
        .all()
    )

    return records

@router.get(
    "/pch-care-management/pch-immunization/by-age/{age}"
)
def get_pch_immunization_by_member_age(
    age: int,
    gender: str,
    type: str, 
    db: Session = Depends(get_db)
):
    norm_gender = normalize_gender(gender)

    records = (
        db.query(LupImmunizationScreening)
        .filter(LupImmunizationScreening.type.ilike(type))
        .all()
    )
    categories = {}
    procedures = {}
    requirements = {}
    conditions = {}
    for rec in records:
        db_gender = normalize_gender(rec.gender)

        gender_match = (
            db_gender is None
            or db_gender == "OTHERS"
            or db_gender == norm_gender
        )

        age_match = age_in_range(age, rec.age_range)

        if not (gender_match and age_match):
            continue

        for v in normalize_to_list(rec.category):
            categories.setdefault(v, str(rec.pk_id))

        for v in normalize_to_list(rec.procedure):
            procedures.setdefault(v, str(rec.pk_id))

        for v in normalize_to_list(rec.requirement):
            requirements.setdefault(v, str(rec.pk_id))

        for v in normalize_to_list(rec.condition):
            conditions.setdefault(v, str(rec.pk_id))

    return {
        "category": [
            {"label": value, "value": pk_id}
            for value, pk_id in categories.items()
        ],
        "procedure": [
            {"label": value, "value": pk_id}
            for value, pk_id in procedures.items()
        ],
        "requirement": [
            {"label": value, "value": pk_id}
            for value, pk_id in requirements.items()
        ],
        "condition": [
            {"label": value, "value": pk_id}
            for value, pk_id in conditions.items()
        ]
    }

@router.post(
    "/pch-care-management/pch-immunization",
    response_model=PchImmunizationSchema
)
def create_pch_immunization(
    immunization: PchImmunizationSchema,
    db: Session = Depends(get_db)
):
    new_record = PchMemberImmunization(
        member_id=immunization.member_id,
        status=immunization.status,
        fields=immunization.fields,
        category=immunization.category,
        procedure=immunization.procedure,
        requirement=immunization.requirement,
        condition=immunization.condition,
        complete_date=immunization.complete_date,
        source=immunization.source,
        immunization_id=immunization.immunization_id
    )

    db.add(new_record)
    db.commit()
    db.refresh(new_record)

    return new_record

@router.patch(
    "/pch-care-management/pch-immunization/{pk_id}",
    # response_model=PchImmunizationSchema
)
def update_pch_immunization(
    pk_id: str,
    immunization: PchImmunizationSchema,
    db: Session = Depends(get_db)
):
    record = (
        db.query(PchMemberImmunization)
        .filter(PchMemberImmunization.pk_id == pk_id)
        .first()
    )

    if not record:
        raise HTTPException(status_code=404, detail="Immunization record not found")

    for field, value in immunization.model_dump(exclude_unset=True).items():
        setattr(record, field, value)

    db.commit()
    db.refresh(record)

    return record

@router.delete(
    "/pch-care-management/pch-immunization/{pk_id}"
)
def delete_pch_immunization(
    pk_id: str,
    db: Session = Depends(get_db)
):
    record = (
        db.query(PchMemberImmunization)
        .filter(PchMemberImmunization.pk_id == pk_id)
        .first()
    )

    if not record:
        raise HTTPException(status_code=404, detail="Immunization record not found")

    db.delete(record)
    db.commit()

    return {"message": "PCH Immunization deleted successfully"}



#*********** PCH SCREENING ROUTES ***********
@router.get(
    "/pch-care-management/pch-screening/{member_id}",
    response_model=list[PchScreeningSchema]
)
def get_pch_screening_by_member(
    member_id: str,
    age: int | None = None,
    gender: str | None = None,
    db: Session = Depends(get_db)
):

    if gender in ["M", "Male","MALE"]:
        gender = "Male"
    elif gender in ["F", "Female","FEMALE"]:
        gender = "Female"
    
    member_has_data = (
        db.query(PchMemberScreening.pk_id)
        .filter(PchMemberScreening.member_id == member_id)
        .first()
        is not None
    )

    if member_has_data:
        records = (
            db.query(
                PchMemberScreening.status,
                PchMemberScreening.complete_date,
                PchMemberScreening.fields,
                PchMemberScreening.member_id,
                PchMemberScreening.source,
                PchMemberScreening.pk_id,
                PchMemberScreening.immunization_id,
                LupImmunizationScreening.type,
                LupImmunizationScreening.category,
                LupImmunizationScreening.procedure,
                LupImmunizationScreening.requirement,
                LupImmunizationScreening.condition
            )
            .outerjoin(
                LupImmunizationScreening,
                (PchMemberScreening.immunization_id
                 == LupImmunizationScreening.pk_id)
                & (LupImmunizationScreening.type.ilike("screen"))
            )
            .filter(
                PchMemberScreening.member_id == member_id
            )
            .order_by(
                cast(PchMemberScreening.complete_date, Date).desc()
            )
            .all()
        )
        return records

    age_condition = True
    if age is not None:
        min_age = cast(
            func.split_part(LupImmunizationScreening.age_range, '-', 1),
            Integer
        )
        max_age = cast(
            func.split_part(LupImmunizationScreening.age_range, '-', 2),
            Integer
        )
        plus_age = cast(
            func.replace(LupImmunizationScreening.age_range, '+', ''),
            Integer
        )

        age_condition = case(
            (
                LupImmunizationScreening.age_range.like('%-%'),
                (age >= min_age) & (age <= max_age)
            ),
            (
                LupImmunizationScreening.age_range.like('%+'),
                age >= plus_age
            ),
            else_=False
        )

    records = (
        db.query(
            literal(None).label("status"),
            literal(None).label("complete_date"),
            literal(None).label("fields"),
            literal(member_id).label("member_id"),
            literal(None).label("source"),
            literal(None).label("pk_id"),
            LupImmunizationScreening.pk_id.label("immunization_id"),
            LupImmunizationScreening.type,
            LupImmunizationScreening.category,
            LupImmunizationScreening.procedure,
            LupImmunizationScreening.requirement,
            LupImmunizationScreening.condition
        )
        .filter(
            LupImmunizationScreening.type.ilike("screen"),
            age_condition,
            LupImmunizationScreening.gender.in_([gender, "ALL"])
            if gender else True
        )
        .all()
    )

    return records

@router.post(
    "/pch-care-management/pch-screening",
    response_model=PchScreeningSchema
)
def create_pch_screening(
    screening: PchScreeningSchema,
    db: Session = Depends(get_db)
):
    new_record = PchMemberScreening(
        member_id=screening.member_id,
        status=screening.status,
        fields=screening.fields,
        category=screening.category,
        procedure=screening.procedure,
        requirement=screening.requirement,
        condition=screening.condition,
        complete_date=screening.complete_date,
        source=screening.source,
        immunization_id=screening.immunization_id
    )

    db.add(new_record)
    db.commit()
    db.refresh(new_record)

    return new_record

@router.patch(
    "/pch-care-management/pch-screening/{pk_id}",
    response_model=PchScreeningSchema
)
def update_pch_screening(
    pk_id: str,
    screening: PchScreeningSchema,
    db: Session = Depends(get_db)
):
    record = (
        db.query(PchMemberScreening)
        .filter(PchMemberScreening.pk_id == pk_id)
        .first()
    )

    if not record:
        raise HTTPException(status_code=404, detail="Screening record not found")

    for field, value in screening.model_dump(exclude_unset=True).items():
        setattr(record, field, value)

    db.commit()
    db.refresh(record)

    return record

@router.delete(
    "/pch-care-management/pch-screening/{pk_id}"
)
def delete_pch_screening(
    pk_id: str,
    db: Session = Depends(get_db)
):
    record = (
        db.query(PchMemberScreening)
        .filter(PchMemberScreening.pk_id == pk_id)
        .first()
    )

    if not record:
        raise HTTPException(status_code=404, detail="Screening record not found")

    db.delete(record)
    db.commit()

    return {"message": "PCH Screening deleted successfully"}
