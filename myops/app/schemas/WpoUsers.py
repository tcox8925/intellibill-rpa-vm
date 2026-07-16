from pydantic import BaseModel, EmailStr, Field, AliasChoices, field_validator
from typing import Optional, List, Dict
from datetime import datetime
from app.schemas.WpoNavigation import WpoNavigationUpdateSchema
from app.schemas.UserEntityPermssion import UserEntityPermissionUpdateSchema
import uuid
import re


def validate_password_strength(password: str) -> str:
    """Reusable password validation function"""
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain at least one uppercase letter")
    if not re.search(r"[0-9]", password):
        raise ValueError("Password must contain at least one number")
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        raise ValueError("Password must contain at least one special character")
    return password

class WpoUsersSchema(BaseModel):
    id: Optional[int] = None  
    user_id: Optional[uuid.UUID] = None  
    email: Optional[EmailStr] = None 
    password: Optional[str] = None
    f_name: Optional[str] = None
    l_name: Optional[str] = None
    date: Optional[datetime] = None
    role: Optional[str] = None
    active: Optional[bool] = None
    company_id: Optional[int] = None
    user_guid: Optional[str] = None
    primary_company_tax_id: Optional[str] = None
    login: Optional[str] = None
    contact: Optional[str] = None
    class Config:
        from_attributes = True
        
class WpoGetUsersSchema(BaseModel):
    id: Optional[int] = None  
    user_id: Optional[uuid.UUID] = None  
    email: Optional[EmailStr] = None 
    login: Optional[str] = None
    firstName: Optional[str] = Field(validation_alias=AliasChoices('f_name'))
    lastName: Optional[str] = Field(validation_alias=AliasChoices('l_name'))
    date: Optional[datetime] = None
    role: Optional[str] = None
    active: Optional[bool] = None
    contact: Optional[str] = None
    token: Optional[str] = None
    default_entity_id: Optional[uuid.UUID] = None
    entity_name: Optional[str] = None
    entity_active_status: Optional[str] = None
    agent_npn: Optional[str] = None
    agent_principal_npn: Optional[str] = None
    call_center: Optional[str] = None
    class Config:
        from_attributes = True
        populate_by_name = True

class userLoginSchema(BaseModel):
    email: EmailStr
    password: str

    class Config:
        form_attributes = True


class WpoUsersUpdateSchema(BaseModel):
    id: Optional[int] = None
    user_id: Optional[uuid.UUID] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    f_name: Optional[str] = None
    l_name: Optional[str] = None
    date: Optional[datetime] = None
    role: Optional[str] = None
    active: Optional[bool] = None
    company_id: Optional[int] = None
    user_guid: Optional[str] = None
    primary_company_tax_id: Optional[str] = None
    login: Optional[str] = None
    contact: Optional[str] = None
    call_center: Optional[str] = None
    agent_npn: Optional[str] = None
    agent_principal_npn: Optional[str] = None

    class Config:
        from_attributes = True

 
class WpoUserRegisterAndUpdateschema(WpoUsersUpdateSchema):
	# permissions: Optional[str] = None
	permissions: Optional[Dict[str, List]] = None
	affiliation: Optional[UserEntityPermissionUpdateSchema] = None
	class Config:
		from_attributes = True

class WpoUserAbstractionSchema(BaseModel):
	id: Optional[int] = None
	email: EmailStr
	f_name: Optional[str]
	l_name: Optional[str]
	role: Optional[str] = None
	active: Optional[bool] = None
	company_id: Optional[int] = None
	user_guid: Optional[uuid.UUID] = None
	primary_company_tax_id: Optional[str] = None
	navigations: List[WpoNavigationUpdateSchema] = None
	affiliations: List[UserEntityPermissionUpdateSchema] = None
	login: Optional[str] = None
	contact: Optional[str] = None

	class Config:
		from_attributes = True
          
class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str
    
    @field_validator("new_password")
    def check_password(cls, value: str) -> str:
        return validate_password_strength(value)

    class Config:
        from_attributes = True
        

class ResetPasswordRequest(BaseModel):
    new_password: str

    @field_validator("new_password")
    def check_password(cls, value: str) -> str:
        return validate_password_strength(value)
    
    class Config:
        from_attributes = True
