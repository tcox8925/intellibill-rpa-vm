from datetime import date, datetime
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import Query
from sqlalchemy import text
from sqlalchemy import case

def custom_order_automationops(col):
    return case(
        (col == 1, 1),
        (col == 0, 2),
        (col == 2, 3),
        (col == 3, 4),
        else_=5
    )


def fetch_all_from_table(table_name: str, db: Session, page: int = 1, page_size: int = 50):
    start = (page - 1) * page_size + 1
    end = page * page_size

    query = text(f"""
        SELECT *
        FROM (
            SELECT 
                ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS rn,
                *
            FROM {table_name}
        ) AS t
        WHERE t.rn BETWEEN :start AND :end
        ORDER BY t.rn
    """)

    count_query = text(f"SELECT COUNT(*) AS total FROM {table_name}")

    total = db.execute(count_query).scalar()
    rows = db.execute(query, {"start": start, "end": end}).mappings().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "data": rows
    }



def pagination_params(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500)
):
    return {"page": page, "page_size": page_size}

def normalize_list(value):
    if not value:
        return []
    if isinstance(value, list):
        if len(value) == 1 and "," in value[0]:
            return value[0].split(",")
        return value
    return [value]

def parse_dob_any_format(dob_value):
    """
    Accepts date or string and returns date object or None
    """
    if not dob_value:
        return None

    # If already a date object
    if isinstance(dob_value, date):
        return dob_value

    dob_str = str(dob_value).strip()

    for fmt in (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%m-%d-%Y",
        "%d-%m-%Y",
        "%m/%d/%y"  
    ):
        try:
            return datetime.strptime(dob_str, fmt).date()
        except ValueError:
            continue

    return None

def calculate_age(dob: date):
    if not dob:
        return None
    today = date.today()
    return today.year - dob.year - (
        (today.month, today.day) < (dob.month, dob.day)
    )


def normalize_gender(gender):
    if not gender:
        return "Others"

    gender = gender.strip().upper()

    if gender == "M" or gender == "MALE":
        return "Male"
    elif gender == "F" or gender == "FEMALE":
        return "Female"
    else:
        return "Others"

def age_in_range(age: int, age_range: str):
    """
    age_range examples:
    - '18-39'
    - '40+'
    - '65+'
    """
    if not age_range:
        return False

    age_range = age_range.strip()

    if "+" in age_range:
        min_age = int(age_range.replace("+", ""))
        return age >= min_age

    if "-" in age_range:
        min_age, max_age = age_range.split("-")
        return int(min_age) <= age <= int(max_age)

    return False

CENTRAL_TZ = ZoneInfo("America/Chicago")
def to_central_time(dt: datetime | None) -> datetime | None:
    if not dt:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))

    return dt.astimezone(CENTRAL_TZ)

def normalize_to_list(value: str | None):
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def build_dropdown(values: set[str]):
    return [{"label": v, "value": v} for v in sorted(values)]
