from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy import and_, func, or_, String
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models import Users, DashboardPermissions, UserPermissions, UserLoginLogs, UserRoles, ClientAccessToken, Entity, Sub_Entity
from app.schemas import WpoUserRegisterAndUpdateschema, ChangePasswordRequest, WpoGetUsersSchema, userLoginSchema, ResetPasswordRequest
from typing import List, Optional
from datetime import datetime, timezone
import uuid
from app.utils.attachment_utility import download_blob_as_base64
from app.utils.util import get_client_ip
from app.middleware.validator import get_current_user
from app.models.Emails.EmailModal import EmailService
from app.utils.util import build_project_tree
from app.utils.azure_communication_manager import AzureCommunicationManager
import secrets


router = APIRouter(tags=["USER ROUTES"])

def normalize_datetime(value):
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)  # drop tzinfo
    return value

def normalize_uuid(value):
    if isinstance(value, uuid.UUID):
        return str(value)  # store as NVARCHAR in SQL
    return value


@router.post("/users/login", response_model=WpoGetUsersSchema)
async def login(loginData: userLoginSchema, request: Request, db: Session = Depends(get_db)):
    email = loginData.email
    password = loginData.password
    
    ip_address = get_client_ip(request)
    
    # Initialize reason and action for logging
    action = "fail"
    reason = ""

    # Get user by email
    user = db.query(Users).filter(Users.login == email).first()
    if not user:
        reason = "Invalid email or password"
        # Log failed attempt
        db.add(UserLoginLogs(
            email=email,
            ip_address=ip_address,
            login_time=datetime.utcnow(),
            action=action,
            reason="Invalid email"
        ))
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=reason)

    # Verify password
    hashed_input = Users.hash_password(password)
    if hashed_input != user.password:
        reason = "Invalid email or password"
        # Log failed attempt
        db.add(UserLoginLogs(
            email=email,
            ip_address=ip_address,
            login_time=datetime.utcnow(),
            action=action,
            reason="Invalid password"
        ))
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=reason)

    # Check if user is active
    if not user.active:
        reason = "User account is inactive"
        db.add(UserLoginLogs(
            email=email,
            ip_address=ip_address,
            login_time=datetime.utcnow(),
            action=action,
            reason=reason
        ))
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=reason)

    # Success: generate token
    token = Users.generate_jwt_token(user)
    user_dict = user.__dict__.copy()
    user_dict['token'] = token

    # Log successful login
    action = "success"
    reason = "Login successful"
    db.add(UserLoginLogs(
        email=email,
        ip_address=ip_address,
        login_time=datetime.utcnow(),
        action=action,
        reason=reason
    ))
    db.commit()

    return user_dict

@router.post("/users/change-password")
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("user_id")
    # Fetch user from DB
    user: Users = db.query(Users).filter(Users.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Verify old password
    if user.password != Users.hash_password(body.old_password):
        raise HTTPException(status_code=400, detail="Old password is incorrect")

    # Update with new password
    new_hashed_password = Users.hash_password(body.new_password)
    if new_hashed_password == user.password:
        raise HTTPException(status_code=400, detail="New password cannot be the same as the old password")
    
    user.password = new_hashed_password
    db.add(user)
    db.commit()

    return {"message": "Password updated successfully"}

@router.get("/users/forgot-password")
async def forgot_password(
    email: str,
    db: Session = Depends(get_db)
):
    # Fetch user from DB
    user: Users = db.query(Users).filter(Users.login == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    email_service = EmailService()
    token = Users.generate_jwt_token(user)
    
    try:
        await email_service.send_password_reset_email(token=token, recipient_email=email)

    except Exception as e:
        print(f"Error sending email: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to send email" )

    return {"message": "Password reset link has been sent to your email"}

@router.post("/users/reset-password")
def reset_password(
    user_password: ResetPasswordRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    # Fetch user from DB
    user: Users = db.query(Users).filter(Users.email == current_user.get("email")).first() # here email is login
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    new_password = user_password.new_password
    old_password = user.password

    hashed_new_password = Users.hash_password(new_password)

    if hashed_new_password == old_password:
        raise HTTPException(status_code=400, detail="New password cannot be the same as the old password")
    
    try:
        user.password = hashed_new_password
        db.add(user)
        db.commit()
        return {"message": "Password reset successfully"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reset password: {str(e)}")

# generate token for external user
@router.get("/users/generate-token")
async def generate_token_for_external_user(client_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to create client tokens")  
    
    client = db.query(ClientAccessToken).filter(ClientAccessToken.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    token = Users.create_client_service_token()
    client.token = token
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


@router.post("/users/create-client")
async def create_new_client_with_token(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to create client tokens")   
    token = Users.create_client_service_token()

    new_client = ClientAccessToken(
        token=token
    )

    db.add(new_client)
    db.commit()
    db.refresh(new_client)

    return {
        "client_id": str(new_client.id),
        "token": new_client.token
    }

@router.get("/users/search")
async def search_users(
    email: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    firstName: Optional[str] = Query(None),
    lastName: Optional[str] = Query(None),
    page: int = Query(1, ge=1, description="Page number (starting from 1)"),
    page_size: int = Query(50, ge=1, le=1000, description="Records per page"),
    db: Session = Depends(get_db)
):
    """
    Search users by optional filters:
    - email
    - role
    - firstName
    - lastName
    """
    # Base query with join
    query = (
        db.query(Users, 
                UserRoles, 
                Entity
                )
        .join(UserRoles, Users.role == UserRoles.role_code, isouter=True)
        .join(UserPermissions, Users.user_id == UserPermissions.user_id, isouter=True)
        .join(Entity, UserPermissions.default_entity_id == Entity.id, isouter=True)
    )

    if email and email.strip():
        query = query.filter(Users.login.ilike(f"%{email.strip()}%"))
    if role and role.strip():
        query = query.filter(Users.role == role.strip())
    if firstName and firstName.strip():
        query = query.filter(Users.f_name.ilike(f"%{firstName.strip()}%"))
    if lastName and lastName.strip():
        query = query.filter(Users.l_name.ilike(f"%{lastName.strip()}%"))

    total_count = query.count()
    results = (
        query
        .order_by(Users.date.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
        .all()
    )        

    # Convert results into schema format
    users_list = [        
        WpoGetUsersSchema(
            id=user.id,
            user_id=user.user_id,
            email=user.email,
            firstName=user.f_name,
            lastName=user.l_name,
            date=user.date,
            role=role.role_name if role else user.role,
            active=user.active,
            login=user.login,
            contact=user.contact,
            call_center=user.call_center,
            default_entity_id=entity.id if entity else None,
            entity_name=entity.entity_name if entity else None,
            entity_active_status=entity.entity_active_status if entity else None,
        )
        for user, role, entity in results
    ]

    return {
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "records": users_list,
    }

@router.get("/users/search-by-email", response_model=List[WpoGetUsersSchema])
async def search_users_by_email(
    email: str = Query(..., description="Partial or full email to search"),
    db: Session = Depends(get_db)
):
    email = email.strip()
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is required")
    query = (
        db.query(Users)
        .filter(Users.login.ilike(f"%{email}%"))
        .filter(Users.active == True)
        .order_by(Users.login)
        
    )

    results = query.all()

    users_list = [
        WpoGetUsersSchema(
            id=user.id,
            user_id=user.user_id,
            email=user.email,
            firstName=user.f_name,
            lastName=user.l_name,
            date=user.date,
            active=user.active,
            login=user.login,
            contact=user.contact,
            call_center=user.call_center,
        )
        for user in results
    ]

    return users_list

# CREATE USER API
@router.post("/users", response_model=WpoGetUsersSchema)
async def register_or_update_user(
    user_data: WpoUserRegisterAndUpdateschema,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    existing_user = db.query(Users).filter(Users.login == user_data.login).first()
    if existing_user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")

    user_data.email = user_data.email.strip().replace(" ", "").lower()
    user_data.login = user_data.login.strip().replace(" ", "").lower()
    
    if user_data.agent_npn:
        agent_npn_exists = db.query(Users).filter(Users.agent_npn == user_data.agent_npn).first()
        if agent_npn_exists:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Agent NPN already exists")

    try:
        new_user = Users(
            user_id=uuid.uuid4(),
            email=user_data.email,
            password=Users.hash_password(user_data.password),
            f_name=user_data.f_name,
            l_name=user_data.l_name,
            date=datetime.now(timezone.utc),
            role=user_data.role,
            active=user_data.active,
            login=user_data.login,
            contact=user_data.contact,
            agent_npn=user_data.agent_npn,
            agent_principal_npn=user_data.agent_principal_npn,
            call_center=user_data.call_center,
            reset_token_jti=secrets.token_hex(16)
        )
        db.add(new_user)
        db.flush()
        
        # Add email sender to Azure Communication Services
        # try:
        #     comm_manager = AzureCommunicationManager()
        #     sender_result = comm_manager.create_email_sender_from_login(
        #         login_email=user_data.login,
        #         first_name=user_data.f_name or "",
        #         last_name=user_data.l_name or ""
        #     )
            
        #     if sender_result.get("success"):
        #         print(f"Successfully added email sender for {user_data.login}")
        #         print(f"Domain: {sender_result.get('domain')}")
        #         print(f"Sender info: {sender_result.get('sender_info')}")
        #     else:
        #         print(f"Warning: Failed to add email sender for {user_data.login}")
        #         print(f"Error: {sender_result.get('error')}")
        #         # Note: We don't raise an exception here, just log the warning
        #         # User creation continues even if sender addition fails
        # except Exception as e:
        #     print(f"Warning: Exception while adding email sender: {str(e)}")
        #     # Continue with user creation even if ACS sender addition fails
        
        if user_data.role != "admin":
            # if user_data.affiliation or user_data.permissions:
            affiliation = user_data.affiliation if user_data.affiliation else {}
            user_permissions = user_data.permissions if user_data.permissions else {}

            new_affiliation = UserPermissions(
                user_id=new_user.user_id,
                default_entity_id=affiliation.default_entity_id if affiliation else None,
                entity_permissions= affiliation.permission_json if affiliation else {},
                permissions = user_permissions
            )
            db.add(new_affiliation)

        email_service = EmailService()
        token = Users.generate_onboarding_token(new_user)
        
        # Todo: this file download should be optimized / cached
        myadmin_logo = download_blob_as_base64("entity_logos/email_logos/Frame 1377.png")
        mail_logo = download_blob_as_base64("entity_logos/email_logos/Mail.png")
        message_logo = download_blob_as_base64("entity_logos/email_logos/Message.png")
        phone_logo = download_blob_as_base64("entity_logos/email_logos/Phone.png")
        
        try:
            await email_service.send_onboarding_email(
                token=token,
                recipient_email=new_user.email,
                sender_email=current_user.get("email"),
                agent_name=f"{new_user.f_name} {new_user.l_name}",
                role=new_user.role,
                temporary_password=user_data.password,
                myadmin_logo=myadmin_logo,
                mail_logo=mail_logo,
                message_logo=message_logo,
                phone_logo=phone_logo
            )

        except Exception as e:
            print(f"Error sending email: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to send email" )
        
        db.commit()
        db.refresh(new_user)

    except Exception as e:
        print(e)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create user")
    
    return new_user

@router.patch("/users")
async def register_or_update_user(
    user_data: WpoUserRegisterAndUpdateschema,
    # current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Check if updating an existing user
    # if current_user.get("role") != "admin":
    #     raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to edit user details")
    existing_user = db.query(Users).filter(Users.login == user_data.login).first()
    if not existing_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User does not exist.")
    
    if user_data.agent_npn and existing_user.agent_npn != user_data.agent_npn:
        agent_npn_exists = db.query(Users).filter(Users.agent_npn == user_data.agent_npn).first()
        if agent_npn_exists:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Agent NPN already exists")

    existing_user.f_name = user_data.f_name or existing_user.f_name
    existing_user.l_name = user_data.l_name or existing_user.l_name
    existing_user.role = user_data.role or existing_user.role
    existing_user.active = user_data.active if user_data.active is not None else existing_user.active
    # existing_user.login = user_data.login or existing_user.login
    existing_user.email = user_data.email or existing_user.email
    existing_user.contact = user_data.contact or existing_user.contact
    existing_user.agent_npn = user_data.agent_npn or existing_user.agent_npn
    existing_user.agent_principal_npn = user_data.agent_principal_npn or existing_user.agent_principal_npn
    existing_user.call_center = user_data.call_center or existing_user.call_center

    # Update navigation restrictions (edit instead of wipe & re-add)
    if user_data.permissions or user_data.affiliation:
        # Fetch all existing nav restrictions for this user
        existing_user_permission = db.query(UserPermissions).filter(UserPermissions.user_id == existing_user.user_id).first()
        if existing_user_permission:
            # Update only changed fields
            existing_user_permission.permissions = user_data.permissions or existing_user_permission.permissions
            existing_user_permission.default_entity_id = user_data.affiliation.default_entity_id if user_data.affiliation and user_data.affiliation.default_entity_id is not None else existing_user_permission.default_entity_id
            existing_user_permission.default_sub_entity_id = user_data.affiliation.default_sub_entity_id if user_data.affiliation and user_data.affiliation.default_sub_entity_id is not None else existing_user_permission.default_sub_entity_id
            existing_user_permission.entity_permissions = user_data.affiliation.permission_json if user_data.affiliation else existing_user_permission.entity_permissions
            db.add(existing_user_permission)    
        else:
            db.add(
                UserPermissions(
                    user_id=existing_user.user_id,
                    permissions=user_data.permissions if user_data.permissions else {},
                    default_entity_id=user_data.affiliation.default_entity_id if user_data.affiliation else None,
                    default_sub_entity_id=user_data.affiliation.default_sub_entity_id if user_data.affiliation else None,
                    entity_permissions=user_data.affiliation.permission_json if user_data.affiliation else {}
                )
            )

    db.commit()
    db.refresh(existing_user)
    return existing_user

@router.get("/users/{user_id}")
async def get_user(
    user_id: int,
    # current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    user = db.query(Users).filter(Users.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    # TODO: confirm and enable auth check
    # if current_user.get("role") != "admin" and current_user.get("id") != user.id:
    #     raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view user details")

    user_data = {
        "id": user.id,
        "email": user.email,
        "user_uuid": user.user_id,
        "f_name": user.f_name,
        "l_name": user.l_name,
        "date": str(user.date) if user.date else None,
        "role": user.role,
        "active": user.active,
        "login": user.login,
        "contact": user.contact,
        "agent_npn": user.agent_npn,
        "agent_principal_npn": user.agent_principal_npn,
        "call_center": user.call_center,
    }
    user_data["affiliation"] = None
    user_data["permissions"] = None
    
    if user.role == "admin":
        return user_data

    # 3. Fetch all active navigations
    all_navigations = (
        db.query(DashboardPermissions)
        .filter(DashboardPermissions.active == True)
        .all()
    )
    user_permissions = db.query(UserPermissions).filter(UserPermissions.user_id == user.user_id).first()
    user_assigned_permissions = []
    affiliation = None
    if user_permissions:
        user_assigned_permissions = user_permissions.permissions
        affiliation = user_permissions.entity_permissions
        
    permissions_result = build_project_tree(all_navigations, user_assigned_permissions)

    # Example: fetch affiliations (adapt as needed)
    default_entity_name = None
    default_sub_entity_name = None
    if user_permissions and user_permissions.default_entity_id:
        default_entity = db.query(Entity).filter(Entity.id == user_permissions.default_entity_id).first()
        if default_entity:
            default_entity_name = default_entity.entity_name
        if user_permissions and user_permissions.default_sub_entity_id:
            default_sub_entity = db.query(Sub_Entity).filter(
                and_(
                    Sub_Entity.entity_id == default_entity.entity_id,
                    Sub_Entity.sub_entity_id == user_permissions.default_sub_entity_id,
                )
            ).first()
            if default_sub_entity:
                fname = default_sub_entity.sub_entity_fname or ""
                lname = default_sub_entity.sub_entity_lname or ""
                default_sub_entity_name = f"{fname} {lname}".strip()
    
    # 6. Return full schema
    user_data.update({
        "permissions": permissions_result,
        "affiliation": 
            {
                "user_id": user_permissions.user_id,
                "default_entity_id": user_permissions.default_entity_id,
                "default_entity_name": default_entity_name,
                "default_sub_entity_id": user_permissions.default_sub_entity_id,
                "default_sub_entity_name": default_sub_entity_name,
                "permission_id": user_permissions.pk_id,
                "permissions_json": affiliation
            } if affiliation else None
    })
    return user_data
    
@router.delete("/users/{user_id}")
async def delete_user(user_id: int, db: Session = Depends(get_db)):
    # TODO: only admin can delete users - add auth check
    # Check if user exists
    existing_user = db.query(Users).filter(Users.id == user_id).first()
    if not existing_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    # Fetch single permission row
    user_permission = (
        db.query(UserPermissions)
        .filter(UserPermissions.user_id == existing_user.user_id)
        .first()
    )
    # Delete permission row if exists
    if user_permission:
        db.delete(user_permission)
        
    # Delete user
    db.delete(existing_user)
    db.commit()

    return {"detail": "User deleted successfully"}

@router.get("/user-login-logs/filters")
def get_user_login_logs_filters(
    agent_id: str = Query(..., description="Filter by agent email/ID"),
    db: Session = Depends(get_db)
):
    query = db.query(UserLoginLogs).filter(UserLoginLogs.email.ilike(agent_id))
    reasons = [
        {"id": r[0], "value": r[0]} 
        for r in query.with_entities(UserLoginLogs.reason)
        .filter(UserLoginLogs.reason.isnot(None))
        .distinct()
        .order_by(UserLoginLogs.reason.asc())
        .all()
    ]
    dates = sorted(
        {row[0][:10] for row in query.with_entities(func.cast(UserLoginLogs.login_time, String))
        .filter(UserLoginLogs.login_time.isnot(None))
        .distinct()
        .all() if row[0]},
        reverse=True
    )
    
    return {
        "reasons": reasons,
        "dates": [{"id": d, "value": d} for d in dates]
    }
# User Login logs api
@router.get("/user-login-logs")
def get_user_login_logs(
    email: Optional[str] = None,
    reason: Optional[List[str]] = Query(None, description="Filter by reasons (multi-select)"),
    login_date: Optional[List[str]] = Query(None, description="Filter by dates (multi-select, format: YYYY-MM-DD)"),
    sort_column: Optional[str] = None,
    sort_order: Optional[str] = "desc",
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
    db: Session = Depends(get_db)
):
    query = db.query(UserLoginLogs)

    if email:
        query = query.filter(UserLoginLogs.email.ilike(email))   
    if reason:
        query = query.filter(UserLoginLogs.reason.in_([r.strip() for r in reason if r and r.strip()]))  
    if login_date:
        date_filters = [d.strip() for d in login_date if d and d.strip()]
        if date_filters:
            query = query.filter(or_(*[func.cast(UserLoginLogs.login_time, String).like(f"{d}%") for d in date_filters]))
    
    total = query.count()
    sort_attr = getattr(UserLoginLogs, sort_column, None) if sort_column else UserLoginLogs.login_time
    query = query.order_by(sort_attr.desc() if sort_order == "desc" else sort_attr.asc())

    offset = (page - 1) * page_size
    logs = query.offset(offset).limit(page_size).all()
    
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": logs
    }

# User Login logs api
@router.get("/user-role")
def get_user_role(
    db: Session = Depends(get_db)
):
    query = db.query(UserRoles)

    roles = query.order_by(UserRoles.role_code).all()
    
    return roles
