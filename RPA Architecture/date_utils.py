from datetime import datetime
import calendar
from datetime import timedelta

def get_current_date_info():
    """
    Returns a dictionary containing commonly used date formats, including previous month details.
    """
    now = datetime.now()

    # Current month details
    current_month_year = now.strftime("%B %Y")  # e.g., "February 2025"
    current_year = now.strftime("%Y")  # e.g., "2025"
    current_month_number = now.strftime("%m")  # e.g., "02"
    current_month_short = now.strftime("%b")  # e.g., "Feb"
    today_date_mmddyyyy = now.strftime("%m%d%Y")  # e.g., "02062025"
    today_date_yyyy_mm_dd = now.strftime("%Y-%m-%d")  # e.g., "2025-02-06"
    first_of_month = now.replace(day=1).strftime("%m%Y")  # First of the current month
    current_month_year_short = f"{current_month_short} {current_year}"

    # Last day of the current month
    last_day = calendar.monthrange(now.year, now.month)[1]
    last_of_current_month = now.replace(day=last_day).strftime("%m/%d/%Y")  # e.g., "02/28/2025"

    # Three months prior calculation
    three_months_prior = now - timedelta(days=90)
    first_of_three_months_prior = datetime(three_months_prior.year, three_months_prior.month, 1).strftime("%m/%d/%Y")

    # Previous month calculation
    prev_month_number = now.month - 1 if now.month > 1 else 12
    prev_year = now.year if now.month > 1 else now.year - 1
    prev_month_short = calendar.month_abbr[prev_month_number]  # Short month format, e.g., "Jan"
    prev_month_year = f"{prev_month_short} {prev_year}"  # e.g., "Jan 2025"
    prev_month = (now.replace(day=1) - timedelta(days=1)).month
    prev_month_full = calendar.month_name[prev_month]
    first_of_prev_month = datetime(prev_year, prev_month_number, 1).strftime("%m%Y")  # e.g., "01012025"

    return {
        "current_month_year": current_month_year,
        "current_year": current_year,
        "current_month_number": current_month_number,
        "current_month_short": current_month_short,
        "today_date_mmddyyyy": today_date_mmddyyyy,
        "today_date_yyyy_mm_dd": today_date_yyyy_mm_dd,
        "first_of_month": first_of_month,
        "prev_month_year": prev_month_year,  # e.g., "Jan 2025"
        "first_of_prev_month": first_of_prev_month,  # e.g., "01012025"
        "last_of_current_month": last_of_current_month,
        "first_of_three_months_prior": first_of_three_months_prior,
        "prev_month_year" : prev_month_year,
        "prev_month_year_full": f"{prev_month_full} {prev_year}",
        "current_month_year_short" : current_month_year_short
    }

def get_current_month_year():
    """Returns the current month and year in 'MMM YYYY' format (e.g., 'Feb 2025')."""
    return datetime.now().strftime("%b %Y")

# Example Usage:
# date_info = get_current_date_info()
# print(date_info["prev_month_year"])  # "Jan 2025"
# print(date_info["first_of_prev_month"])  # "01012025"
