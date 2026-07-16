from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_db
from app.core.helpers import parse_dob_any_format, calculate_age, normalize_gender
from app.utils.population_helpers import (
    _exec,
    calculate_age_group,
    normalize_gender_value,
    resolve_report_date,
    safe_float,
    safe_int,
    safe_divide,
)
from app.queries.population import (
    CARDS_SQL,
    CARD_DETAILS_SQL,
    CARD_DETAILS_COUNT_SQL,
    FILTERS_REPORT_DATES_SQL,
    FILTERS_GENDERS_SQL,
    MEMBERSHIP_BY_MONTH_SQL,
    FIN_SUMMARY_KPI_SQL,
    FIN_SUMMARY_TREND_SQL,
    TOP_RX_SQL,
    TOP_DIAGNOSES_SQL,
)
from app.schemas.Pch.pch_population_schema import (
    PopulationCardsResponse,
    PopulationCardSchema,
    PopulationCardDetailsResponse,
    PopulationCardDetailRow,
    PopulationFiltersResponse,
    FilterOption,
    PopulationDashboardDataResponse,
    MembershipByMonthPoint,
    KpiPmpmPoint,
    PmpmByMonthPoint,
    TopRxExpensePoint,
    TopDiagnosisPoint,
)

router = APIRouter(tags=["PCH POPULATION DASHBOARD"])

# ---------------------------------------------------------------------------
# Allowed sort columns for card details (whitelist to prevent SQL injection)
# ---------------------------------------------------------------------------
ALLOWED_SORT_COLUMNS = {
    "amisys_number", "first_name", "last_name", "member_dob",
    "gender", "line_of_business", "product",
    "population_health_category", "primary_risk_category", "risk_score",
}

# Card key alias mapping
CARD_KEY_ALIASES = {
    "totalmembers": "total_members",
    "membermonths": "member_months",
    "avgrisk": "avg_risk_score",
    "avgriskscore": "avg_risk_score",
    "healthy": "healthy",
    "chronicdisease": "chronic_disease_1",
    "chronicdisease1": "chronic_disease_1",
    "chronicdisease2": "chronic_disease_2plus",
    "chronic1": "chronic_disease_1",
    "chronic2": "chronic_disease_2plus",
    "chronic2plus": "chronic_disease_2plus",
}

# SQL WHERE fragment for each card key
CARD_KEY_FILTERS = {
    "total_members": "",
    "member_months": "",
    "avg_risk_score": "",
    "healthy": "AND LOWER(population_health_category) = 'healthy'",
    "chronic_disease_1": "AND LOWER(population_health_category) LIKE 'chronic%%' AND LOWER(population_health_category) LIKE '%%1 condition%%'",
    "chronic_disease_2plus": "AND LOWER(population_health_category) LIKE 'chronic%%' AND LOWER(population_health_category) LIKE '%%2%%condition%%'",
}


def _resolve_entity(entity_id: str | None, company_id: str | None) -> str | None:
    return entity_id or company_id


# ---------------------------------------------------------------------------
# Helper: build cards from aggregation row
# ---------------------------------------------------------------------------
def _build_cards(row: dict) -> list[PopulationCardSchema]:
    return [
        PopulationCardSchema(
            card_key="total_members",
            label="Total Members",
            value=safe_int(row.get("total_members")),
        ),
        PopulationCardSchema(
            card_key="member_months",
            label="Member Months",
            value=round(safe_float(row.get("member_months")), 2),
        ),
        PopulationCardSchema(
            card_key="avg_risk_score",
            label="Avg Risk Score",
            value=round(safe_float(row.get("avg_risk_score")), 2) if row.get("avg_risk_score") is not None else None,
        ),
        PopulationCardSchema(
            card_key="healthy",
            label="Healthy",
            value=safe_int(row.get("healthy_count")),
        ),
        PopulationCardSchema(
            card_key="chronic_disease_1",
            label="Chronic (1 Condition)",
            value=safe_int(row.get("chronic_1_count")),
        ),
        PopulationCardSchema(
            card_key="chronic_disease_2plus",
            label="Chronic (2+ Conditions)",
            value=safe_int(row.get("chronic_2plus_count")),
        ),
    ]


def _get_cards(db: Session, params: dict) -> list[PopulationCardSchema]:
    """Shared cards logic used by both /cards and /dashboard endpoints."""
    rows = _exec(db, CARDS_SQL, params)
    if not rows:
        return _build_cards({})
    return _build_cards(rows[0])


# ---------------------------------------------------------------------------
# GET /pch-population/dashboard/cards
# ---------------------------------------------------------------------------
@router.get("/pch-population/dashboard/cards", response_model=PopulationCardsResponse)
def get_population_cards(
    entity_id: Optional[str] = Query(None),
    company_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    carrier_id: Optional[str] = Query(None),
    report_date: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    age_group: Optional[str] = Query(None),
    line_of_business: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    try:
        eid = _resolve_entity(entity_id, company_id)
        cid = sub_entity_id or carrier_id
        rd = resolve_report_date(db, eid, report_date) if eid else report_date
        gender_norm = normalize_gender_value(gender).upper() if gender else None

        params = {
            "entity_id": eid,
            "carrier_id": cid,
            "report_date": rd,
            "line_of_business": line_of_business,
            "gender": gender_norm,
        }

        if not age_group:
            cards = _get_cards(db, params)
            return PopulationCardsResponse(cards=cards)

        # Age group filter requires Python-side computation
        detail_sql = """
        WITH latest AS (
            SELECT *,
                ROW_NUMBER() OVER (PARTITION BY amisys_number ORDER BY report_date DESC) AS rn
            FROM wpo.pch_member_roster
            WHERE (:entity_id IS NULL OR company_id = :entity_id)
              AND (:carrier_id IS NULL OR carrier_id = :carrier_id)
              AND (:report_date IS NULL OR report_date = :report_date)
              AND (:line_of_business IS NULL OR line_of_business = :line_of_business)
              AND (:gender IS NULL OR UPPER(gender) = :gender)
        )
        SELECT amisys_number, member_dob, member_months, risk_score,
               population_health_category
        FROM latest WHERE rn = 1
        """
        members = _exec(db, detail_sql, params)

        filtered = [m for m in members if calculate_age_group(m.get("member_dob")) == age_group]

        total_members = len(set(m["amisys_number"] for m in filtered if m.get("amisys_number")))
        member_months_sum = sum(safe_float(m.get("member_months")) for m in filtered)
        risk_scores = [safe_float(m.get("risk_score")) for m in filtered if m.get("risk_score")]
        avg_risk = round(sum(risk_scores) / len(risk_scores), 2) if risk_scores else None

        healthy = 0
        chronic_1 = 0
        chronic_2plus = 0
        for m in filtered:
            phc = (m.get("population_health_category") or "").lower()
            if phc == "healthy":
                healthy += 1
            elif "chronic" in phc and "1 condition" in phc:
                chronic_1 += 1
            elif "chronic" in phc and "2" in phc and "condition" in phc:
                chronic_2plus += 1

        cards = _build_cards({
            "total_members": total_members,
            "member_months": member_months_sum,
            "avg_risk_score": avg_risk,
            "healthy_count": healthy,
            "chronic_1_count": chronic_1,
            "chronic_2plus_count": chronic_2plus,
        })
        return PopulationCardsResponse(cards=cards)

    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# GET /pch-population/dashboard/cards/{card_key}/details
# ---------------------------------------------------------------------------
@router.get(
    "/pch-population/dashboard/cards/{card_key}/details",
    response_model=PopulationCardDetailsResponse,
)
def get_population_card_details(
    card_key: str,
    entity_id: Optional[str] = Query(None),
    company_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    carrier_id: Optional[str] = Query(None),
    report_date: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    age_group: Optional[str] = Query(None),
    line_of_business: Optional[str] = Query(None),
    product: Optional[str] = Query(None),
    population_health_category: Optional[str] = Query(None),
    primary_risk_category: Optional[str] = Query(None),
    sort_by: str = Query("last_name"),
    sort_order: str = Query("asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    try:
        eid = _resolve_entity(entity_id, company_id)
        cid = sub_entity_id or carrier_id
        rd = resolve_report_date(db, eid, report_date) if eid else report_date
        gender_norm = normalize_gender_value(gender).upper() if gender else None

        # Normalize card_key
        normalized_key = CARD_KEY_ALIASES.get(
            card_key.lower().replace("-", "").replace("_", ""), card_key.lower()
        )
        card_filter = CARD_KEY_FILTERS.get(normalized_key, "")

        # Validate sort_by against whitelist
        if sort_by not in ALLOWED_SORT_COLUMNS:
            sort_by = "last_name"
        if sort_order.lower() not in ("asc", "desc"):
            sort_order = "asc"

        params = {
            "entity_id": eid,
            "carrier_id": cid,
            "report_date": rd,
            "line_of_business": line_of_business,
            "gender": gender_norm,
            "product": product,
            "population_health_category": population_health_category,
            "primary_risk_category": primary_risk_category,
            "page_size": page_size,
            "offset": (page - 1) * page_size,
        }

        def _row_to_detail(r: dict) -> PopulationCardDetailRow:
            dob = r.get("member_dob")
            parsed = parse_dob_any_format(dob)
            age = calculate_age(parsed) if parsed else None
            return PopulationCardDetailRow(
                amisys_number=r.get("amisys_number"),
                first_name=r.get("first_name"),
                last_name=r.get("last_name"),
                member_dob=dob,
                age=age,
                gender=r.get("gender"),
                line_of_business=r.get("line_of_business"),
                product=r.get("product"),
                population_health_category=r.get("population_health_category"),
                primary_risk_category=r.get("primary_risk_category"),
                risk_score=r.get("risk_score"),
            )

        if not age_group:
            sql = CARD_DETAILS_SQL.format(
                card_filter=card_filter, sort_by=sort_by, sort_order=sort_order
            )
            count_sql = CARD_DETAILS_COUNT_SQL.format(card_filter=card_filter)

            rows = _exec(db, sql, params)
            count_rows = _exec(db, count_sql, params)
            total = count_rows[0]["total"] if count_rows else 0

            items = [_row_to_detail(r) for r in rows]
            return PopulationCardDetailsResponse(
                total=total, page=page, page_size=page_size, items=items
            )

        # Age group filter: fetch all matching, filter in Python, paginate manually
        full_sql = CARD_DETAILS_SQL.format(
            card_filter=card_filter, sort_by=sort_by, sort_order=sort_order
        ).replace("LIMIT :page_size OFFSET :offset", "")
        full_params = {k: v for k, v in params.items() if k not in ("page_size", "offset")}
        all_rows = _exec(db, full_sql, full_params)

        filtered = [
            _row_to_detail(r)
            for r in all_rows
            if calculate_age_group(r.get("member_dob")) == age_group
        ]

        total = len(filtered)
        start = (page - 1) * page_size
        items = filtered[start : start + page_size]

        return PopulationCardDetailsResponse(
            total=total, page=page, page_size=page_size, items=items
        )

    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# GET /pch-population/dashboard/filters
# ---------------------------------------------------------------------------
@router.get("/pch-population/dashboard/filters", response_model=PopulationFiltersResponse)
def get_population_filters(
    entity_id: str = Query(...),
    db: Session = Depends(get_db),
):
    try:
        # Report dates
        rd_rows = _exec(db, FILTERS_REPORT_DATES_SQL, {"entity_id": entity_id})
        report_dates = [
            FilterOption(label=str(r["report_date"]), value=str(r["report_date"]))
            for r in rd_rows
            if r.get("report_date")
        ]

        # Genders
        g_rows = _exec(db, FILTERS_GENDERS_SQL, {"entity_id": entity_id})
        gender_set = set()
        for r in g_rows:
            g = normalize_gender(r.get("gender"))
            if g:
                gender_set.add(g)
        genders = [FilterOption(label=g, value=g) for g in sorted(gender_set)]

        # Age groups (hardcoded buckets matching existing helpers)
        age_groups = [
            FilterOption(label="0-17", value="0-17"),
            FilterOption(label="18-39", value="18-39"),
            FilterOption(label="40-64", value="40-64"),
            FilterOption(label="65+", value="65+"),
        ]

        return PopulationFiltersResponse(
            report_dates=report_dates,
            genders=genders,
            age_groups=age_groups,
        )

    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# GET /pch-population/dashboard  (composite)
# ---------------------------------------------------------------------------
@router.get("/pch-population/dashboard", response_model=PopulationDashboardDataResponse)
def get_population_dashboard(
    entity_id: Optional[str] = Query(None),
    company_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    carrier_id: Optional[str] = Query(None),
    report_date: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    age_group: Optional[str] = Query(None),
    line_of_business: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    try:
        eid = _resolve_entity(entity_id, company_id)
        cid = sub_entity_id or carrier_id
        rd = resolve_report_date(db, eid, report_date) if eid else report_date
        gender_norm = normalize_gender_value(gender).upper() if gender else None

        base_params = {
            "entity_id": eid,
            "carrier_id": cid,
            "report_date": rd,
            "line_of_business": line_of_business,
            "gender": gender_norm,
        }

        # 1. Cards
        cards = _get_cards(db, base_params)

        # 2. Membership trend
        membership_rows = _exec(db, MEMBERSHIP_BY_MONTH_SQL, {
            "entity_id": eid,
            "carrier_id": cid,
            "line_of_business": line_of_business,
        })
        membership_trend = [
            MembershipByMonthPoint(
                month=str(r.get("month", "")),
                count=safe_int(r.get("count")),
            )
            for r in membership_rows
        ]

        fin_params = {
            "entity_id": eid,
            "carrier_id": cid,
            "report_date": rd,
        }

        # 3. KPI PMPM
        kpi_rows = _exec(db, FIN_SUMMARY_KPI_SQL, fin_params)
        kpi_pmpm: list[KpiPmpmPoint] = []
        if kpi_rows:
            r = kpi_rows[0]
            mm = safe_float(r.get("sum_member_months"))
            for cat, col in [
                ("Inpatient", "sum_inpatient"),
                ("Outpatient", "sum_outpatient"),
                ("Primary Care", "sum_primary_care"),
                ("Specialty", "sum_specialty"),
                ("Net Rx", "sum_net_rx"),
                ("Net Other Medical", "sum_net_other_medical"),
            ]:
                kpi_pmpm.append(KpiPmpmPoint(
                    category=cat,
                    pmpm=safe_divide(safe_float(r.get(col)), mm),
                ))

        # 4. PMPM trend
        trend_rows = _exec(db, FIN_SUMMARY_TREND_SQL, {
            "entity_id": eid,
            "carrier_id": cid,
        })
        pmpm_trend: list[PmpmByMonthPoint] = []
        for r in trend_rows:
            mm = safe_float(r.get("sum_member_months"))
            ip = safe_divide(safe_float(r.get("sum_inpatient")), mm)
            op = safe_divide(safe_float(r.get("sum_outpatient")), mm)
            pc = safe_divide(safe_float(r.get("sum_primary_care")), mm)
            sp = safe_divide(safe_float(r.get("sum_specialty")), mm)
            rx = safe_divide(safe_float(r.get("sum_net_rx")), mm)
            om = safe_divide(safe_float(r.get("sum_net_other_medical")), mm)
            vals = [v for v in [ip, op, pc, sp, rx, om] if v is not None]
            total = round(sum(vals), 2) if vals else None
            pmpm_trend.append(PmpmByMonthPoint(
                month=str(r.get("month", "")),
                inpatient=ip, outpatient=op, primary_care=pc,
                specialty=sp, net_rx=rx, net_other_medical=om,
                total=total,
            ))

        # 5. Top Rx
        rx_params = {"entity_id": eid, "carrier_id": cid, "report_date": rd}
        rx_rows = _exec(db, TOP_RX_SQL, rx_params)
        top_rx = [
            TopRxExpensePoint(
                drug_desc=r.get("drug_desc"),
                total_paid=safe_float(r.get("total_paid")),
                claim_count=safe_int(r.get("claim_count")),
            )
            for r in rx_rows
        ]

        # 6. Top Diagnoses
        diag_rows = _exec(db, TOP_DIAGNOSES_SQL, rx_params)
        top_diagnoses = [
            TopDiagnosisPoint(
                diag_code=r.get("diag_code"),
                description=r.get("description"),
                claim_count=safe_int(r.get("claim_count")),
                total_paid=safe_float(r.get("total_paid")),
                cms_hcc_v22=r.get("cms_hcc_v22"),
                cms_hcc_v24=r.get("cms_hcc_v24"),
                cms_hcc_v28=r.get("cms_hcc_v28"),
            )
            for r in diag_rows
        ]

        return PopulationDashboardDataResponse(
            cards=cards,
            membership_trend=membership_trend,
            kpi_pmpm=kpi_pmpm,
            pmpm_trend=pmpm_trend,
            top_rx=top_rx,
            top_diagnoses=top_diagnoses,
        )

    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database unavailable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
