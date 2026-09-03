import os

from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()


# .env stores SSL_MODE as a boolean-ish flag ("true"/"false"), not a
# libpq sslmode value - translate it rather than pass it straight through.
SSL_MODE_MAP = {"true": "require", "false": "disable"}


def get_engine():
    url = (
        f"postgresql+psycopg://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    )
    ssl_flag = os.getenv("SSL_MODE", "").strip().lower()
    sslmode = SSL_MODE_MAP.get(ssl_flag, ssl_flag) or None
    connect_args = {"sslmode": sslmode} if sslmode else {}
    return create_engine(url, connect_args=connect_args)
