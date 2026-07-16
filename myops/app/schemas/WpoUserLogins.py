
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

class UserLoginsSchema(BaseModel):
	id: Optional[int]
	user_id: int
	email: EmailStr
	login_date: datetime
	success: bool
	ip_address: Optional[str]

	class Config:
		from_attributes = True
# Pydantic models (request/response) for user-related operations
