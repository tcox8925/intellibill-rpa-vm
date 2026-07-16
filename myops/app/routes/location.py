
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.agentModels.lup_zip_database import LupZipDatabase
router = APIRouter(tags=["LOCATION ROUTES"])

@router.get("/location/{zip_code}")
def get_zip_codes(
    zip_code: str,
    db: Session = Depends(get_db)
):
    try:
        zip_codes = db.query(
            LupZipDatabase.city_name,
            LupZipDatabase.state_name,
            LupZipDatabase.state_code,
            LupZipDatabase.county_name,
            LupZipDatabase.cbsa_name,
            LupZipDatabase.fips_county,
            LupZipDatabase.fips_state,
            LupZipDatabase.zip_code
        ).filter(LupZipDatabase.zip_code == zip_code).all()

        result = [
            {
                "city_name": r.city_name,
                "state_name": r.state_name,
                "state_code": r.state_code,
                "county_name": r.county_name,
                "cbsa_name": r.cbsa_name,
                "fips_county": r.fips_county,
                "fips_state": r.fips_state,
                "zip_code": r.zip_code,
            }
            for r in zip_codes
        ]

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")
