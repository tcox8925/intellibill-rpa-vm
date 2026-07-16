import pyodbc
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, session
from app.core.config import settings
from datetime import datetime, timedelta, timezone
from azure.identity import DefaultAzureCredential
import psycopg2

DATABASE_USER = settings.PG_USER
DATABASE_HOST = settings.PG_HOST  
DATABASE_PORT = settings.PG_PORT
DATABASE_NAME = settings.PG_DATABASE
DATABASE_PASSWORD = settings.PG_PASSWORD

# --- Token Cache ---
_cached_token = None
_token_expiration = datetime.now(timezone.utc)


def get_db_token():
    """Fetch and cache AAD access token for Azure PostgreSQL."""
    global _cached_token, _token_expiration
    # Refresh token if expired or not set
    if not _cached_token or datetime.now(timezone.utc) >= _token_expiration:
        credential = DefaultAzureCredential()
        token_obj = credential.get_token("https://ossrdbms-aad.database.windows.net/.default")
        _cached_token = token_obj.token
        # Token valid for 1h — refresh 5 minutes early
        _token_expiration = datetime.now(timezone.utc) + timedelta(minutes=55)

    return _cached_token

    
def get_synapse_conn():
    """Create token-authenticated connection for Synapse"""
    # token_bytes = get_token().encode("utf-16-le")
    return pyodbc.connect(settings.SYNAPSE_CONNECTION_STRING)

# SQLAlchemy engine for Synapse
synapse_engine = create_engine(
    "mssql+pyodbc://",
    creator=get_synapse_conn,
    fast_executemany=True,
    isolation_level="AUTOCOMMIT"
)

SynapseSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=synapse_engine)

def get_synapse_db():
    db = SynapseSessionLocal()
    try:
        yield db
    finally:
        db.close()


DATABASE_URL = (
    f"postgresql+psycopg2://{DATABASE_USER}:{DATABASE_PASSWORD}@"
    f"{DATABASE_HOST}:{DATABASE_PORT}/{DATABASE_NAME}"
)

def get_connection():
    """Create PostgreSQL connection with fresh token."""
    token = get_db_token()
    conn = psycopg2.connect(
        host=DATABASE_HOST,
        dbname=DATABASE_NAME,
        user=f"{DATABASE_USER}",  # or your user@tenant.com
        password=token,
        port=DATABASE_PORT,
        sslmode="require"
    )
    return conn

# --- SQLAlchemy Engine ---
engine = create_engine(
    DATABASE_URL,
    # "postgresql+psycopg2://",
    # creator=get_connection,       # dynamically generates fresh token each time
    pool_size=20,
    max_overflow=40,
    pool_timeout=30,
    pool_recycle=1800,            # recycle connections every 30 min
    pool_pre_ping=True,
    isolation_level="AUTOCOMMIT"
)

# Create a configured "Session" class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db: session = SessionLocal()
    try:
        yield db
    finally:
        db.close()

