from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.helpers import fetch_all_from_table, pagination_params
from app.db.session import get_synapse_db

router = APIRouter(prefix="/service", tags=["RAW TABLE ROUTES"])

@router.get("/pch_care_gap_detail")
def get_pch_care_gap_detail(
    db: Session = Depends(get_synapse_db),
    pagination: dict = Depends(pagination_params)
):
    return fetch_all_from_table("raw.pch_care_gap_detail", db, **pagination)

@router.get("/pch_measures")
def get_pch_measures(
    db: Session = Depends(get_synapse_db),
    pagination: dict = Depends(pagination_params)
):
    return fetch_all_from_table("raw.pch_measures", db, **pagination)

@router.get("/pcp_measure_summary")
def get_pcp_measure_summary(
    db: Session = Depends(get_synapse_db),
    pagination: dict = Depends(pagination_params)
):
    return fetch_all_from_table("raw.pcp_measure_summary", db, **pagination)

@router.get("/pch_med_claims")
def get_pch_med_claims(
    db: Session = Depends(get_synapse_db),
    pagination: dict = Depends(pagination_params)
):
    return fetch_all_from_table("raw.pch_med_claims", db, **pagination)

@router.get("/pch_fin_summary")
def get_pch_fin_summary(
    db: Session = Depends(get_synapse_db),
    pagination: dict = Depends(pagination_params)
):
    return fetch_all_from_table("raw.pch_fin_summary", db, **pagination)

@router.get("/pch_rx_claim")
def get_pch_rx_claim(
    db: Session = Depends(get_synapse_db),
    pagination: dict = Depends(pagination_params)
):
    return fetch_all_from_table("raw.pch_rx_claim", db, **pagination)

@router.get("/pch_oth_adj")
def get_pch_oth_adj(
    db: Session = Depends(get_synapse_db),
    pagination: dict = Depends(pagination_params)
):
    return fetch_all_from_table("raw.pch_oth_adj", db, **pagination)

@router.get("/pch_mem_elig")
def get_pch_mem_elig(
    db: Session = Depends(get_synapse_db),
    pagination: dict = Depends(pagination_params)
):
    return fetch_all_from_table("raw.pch_mem_elig", db, **pagination)

@router.get("/pch_cap")
def get_pch_cap(
    db: Session = Depends(get_synapse_db),
    pagination: dict = Depends(pagination_params)
):
    return fetch_all_from_table("raw.pch_cap", db, **pagination)

@router.get("/pch_member_roster")
def get_pch_member_roster(
    db: Session = Depends(get_synapse_db),
    pagination: dict = Depends(pagination_params)
):
    return fetch_all_from_table("raw.pch_member_roster", db, **pagination)
