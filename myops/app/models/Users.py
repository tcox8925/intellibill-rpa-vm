import hashlib
import uuid
import jwt
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from sqlalchemy import Column, Boolean, Integer, text, String, ForeignKey, Identity
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, BIGINT, NVARCHAR, BIT, DATETIME2
from app.models.BaseClasses import Base
from app.core.config import settings
import secrets
from sqlalchemy.dialects.postgresql import UUID, JSONB

class Users(Base):
    __tablename__ = "users"
    __schema__ = "ops_sec"
    __table_args__ = {'schema': __schema__}

    id = Column(BIGINT, autoincrement=True, nullable=False, unique=True, server_default=text("0"))
    user_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4, nullable=False)
    email = Column(String(200), nullable=False)
    password = Column(String(400), nullable=False)
    f_name = Column(String(100), nullable=True)
    l_name = Column(String(100), nullable=True)
    date = Column(DATETIME2, nullable=True)
    role = Column(String(50), nullable=False)
    active = Column(Boolean, nullable=True)
    login = Column(String(100), nullable=True)
    contact = Column(String(100), nullable=True)
    agent_npn = Column(String(20), nullable=True, unique=True)
    agent_principal_npn = Column(String(20), nullable=True)
    reset_token_jti = Column(String, nullable=True, unique=True)
    call_center = Column(String(20), nullable=True)

    def __repr__(self):
        return (
            f"""<Users(
            id={self.id}, 
            user_id='{self.user_id}',
            email='{self.email}', 
            f_name='{self.f_name}', 
            l_name='{self.l_name}', 
            date='{self.date}',
            role='{self.role}',
            active={self.active}, 
            login='{self.login}',
            agent_npn='{self.agent_npn}',
            agent_principal_npn='{self.agent_principal_npn}',
            contact='{self.contact}',
            call_center='{self.call_center}'
            )>"""
        )

    @classmethod
    def hash_password(cls, password: str) -> str:
        """Hash a password for storing."""
        sha256 = hashlib.sha256()
        sha256.update(password.encode("utf-8"))
        return sha256.hexdigest()
    
    # @classmethod
    def generate_jwt_token(user: Any) -> str:
        """
        Generates a JWT token for the given user.
        Mirrors the .NET implementation.
        """
        expiry_minutes = settings.JWT_EXPIRY_MINUTES * 9
        expires = datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)

        claims: Dict[str, Any] = {
            "jti": str(uuid.uuid4()),
            "user_id": str(user.user_id),
            "id": str(user.id),
            "unique_name": f"{user.f_name} {user.l_name}",
            "role": user.role,
            "email": user.login,
            # "FirstName": user.f_name,
            # "LastName": user.l_name,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_ISSUER,
            "exp": expires,
            "reset_token_jti": user.reset_token_jti
        }

        token = jwt.encode(
            payload=claims,
            key=settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        return token
    
    def generate_onboarding_token(user: Any) -> str:
        """
        Generates a JWT token for the given user.
        Mirrors the .NET implementation.
        """
        expiry_minutes = settings.JWT_EXPIRY_MINUTES * 24
        expires = datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)

        claims: Dict[str, Any] = {
            "jti": str(uuid.uuid4()),
            "user_id": str(user.user_id),
            "id": str(user.id),
            "unique_name": f"{user.f_name} {user.l_name}",
            "role": user.role,
            "email": user.login,
            # "FirstName": user.f_name,
            # "LastName": user.l_name,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_ISSUER,
            "exp": expires,
            "reset_token_jti": user.reset_token_jti
        }

        token = jwt.encode(
            payload=claims,
            key=settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        return token

    @classmethod
    def create_client_service_token(cls) -> str:
        token = secrets.token_urlsafe(64)
        return token
    
class ClientAccessToken(Base):
    __tablename__ = "client_access_token"
    __table_args__ = {"schema": "wpo"}  # ensures table is created under wpo schema

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token = Column(String, nullable=False)
    
# USER PERMISSIONS MODEL
class UserPermissions(Base):
    __tablename__ = "user_permissions"
    __schema__ = "ops_sec"
    __table_args__ = {"schema": __schema__}
    
    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        ForeignKey(f"{__schema__}.users.user_id", ondelete="CASCADE"),
        nullable=False
    )

    permissions = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    default_entity_id = Column(UUID(as_uuid=True))
    default_sub_entity_id = Column(
        String(20),
        nullable=True,
    )
    entity_permissions = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    
    def __repr__(self):
        return (
            f"pk_id={self.pk_id}, "
            f"user_id={self.user_id}, "
            f"permissions={self.permissions}, "
            f"default_entity_id={self.default_entity_id}, "
            f"entity_permissions={self.entity_permissions}"
        )
    

class DashboardPermissions(Base):
    __tablename__ = "dashboard_permissions"
    __schema__ = "ops_sec"
    __table_args__ = {"schema": __schema__}

    id = Column(
        Integer,
        Identity(
            always=True,
            start=1,
            increment=1,
            minvalue=1,
            maxvalue=2147483647,
            cache=1,
            cycle=False
        ),
        primary_key=True,
        nullable=False
    )

    parent_id = Column(Integer, nullable=False)
    nav_abbr = Column(String(100), nullable=False)
    name = Column(String(200), nullable=False)
    active = Column(Boolean, nullable=True)
    order_num = Column(Integer, nullable=True)

    project = Column(
        String(10),
        nullable=False,
        server_default=text("'my_ops'")
    )
    
    def __repr__(self):
        return (
            f"id={self.id}, "
            f"parent_id={self.parent_id}, "
            f"nav_abbr='{self.nav_abbr}', "
            f"name='{self.name}', "
            f"active={self.active}, "
            f"order_num={self.order_num}, "
            f"project='{self.project}'"
        )
