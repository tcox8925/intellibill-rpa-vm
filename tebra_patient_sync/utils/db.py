import os

from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()


# .env stores SSL_MODE as a boolean-ish flag ("true"/"false"), not a
# libpq sslmode value - translate it rather than pass it straight through.
SSL_MODE_MAP = {"true": "require", "false": "disable"}


def get_engine(application_name: str | None = None):
    """application_name labels every connection this engine opens. The
    patient_header/patient_coverages audit trigger records it as
    patient_audit_history.change_source, so a run's changes can be told apart
    from everyone else's (see utils/patient_audit_counts.py)."""
    url = (
        f"postgresql+psycopg://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    )
    ssl_flag = os.getenv("SSL_MODE", "").strip().lower()
    sslmode = SSL_MODE_MAP.get(ssl_flag, ssl_flag) or None
    connect_args = {"sslmode": sslmode} if sslmode else {}
    if application_name:
        connect_args["application_name"] = application_name
    return create_engine(url, connect_args=connect_args)
