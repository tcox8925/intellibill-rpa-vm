from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import json
from ringcentral import SDK
from app.core.config import settings

router = APIRouter(tags=["AGENT CALL ROUTES"])

SERVER_URL = settings.RING_CENTRAL_SERVER_URL
CLIENT_ID = settings.RING_CENTRAL_CLIENT_ID
CLIENT_SECRET = settings.RING_CENTRAL_CLIENT_SECRET
JWT = settings.RING_CENTRAL_JWT

RING_CENTRAL_API_URL = "/restapi/v1.0/account"
# Initialize SDK
# rcsdk = SDK(CLIENT_ID, CLIENT_SECRET, SERVER_URL)
# platform = rcsdk.platform()

# @router.on_event("startup")
# def login_ringcentral():
#     try:
#         print("Logging in to RingCentral with JWT...")
#         platform.login(jwt=JWT)
#         print("RingCentral Login successful!")

#     except Exception as e:
#         print("Login failed:", str(e))


@router.get("/ring-central-users")
def get_ring_central_users(email: Optional[str] = Query(None, description="Email to filter users")):
    params = {
        # "perPage": 10,   # limit results
        "view": "Simple"
    }
    rcsdk = SDK(CLIENT_ID, CLIENT_SECRET, SERVER_URL)
    platform = rcsdk.platform()

    platform.login(jwt=JWT)
    print("RingCentral Login successful!")
    if email:
        params["email"] = email
    try:
        response = platform.get(f"{RING_CENTRAL_API_URL}/~/extension", params)
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.get("/agent/call-logs")
def get_call_logs(
        email: str,
        phoneNumber: Optional[str] = Query(None, description="Phone number to filter call logs"),
        call_type: Optional[str] = Query(None, description="Type of call: Inbound or Outbound"),
    ):
    rcsdk = SDK(CLIENT_ID, CLIENT_SECRET, SERVER_URL)
    platform = rcsdk.platform()

    platform.login(jwt=JWT)
    print("RingCentral Login successful!")
    
    user = platform.get(f"{RING_CENTRAL_API_URL}/~/extension", {
        "email": email,
        "view": "Simple"
    })
    # Convert JsonObject → dict
    ring_central_user = user.json_dict() if hasattr(user, "json_dict") else json.loads(user.text())

    ring_central_user = ring_central_user.get('records', [])
    if not ring_central_user:
        raise HTTPException(status_code=404, detail="user_not_found_in_ring_central")
    
    ring_central_user = ring_central_user[0]

    id = ring_central_user.get('id')
    call_log_params = {
        # "perPage": 10,   # limit results
        "view": "Simple",
    }
    if call_type in ["Inbound", "Outbound"]:
        call_log_params["direction"] = call_type
    
    try:
        response = platform.get(f"{RING_CENTRAL_API_URL}/~/extension/{id}/call-log", call_log_params)
        call_logs = response.json_dict() if hasattr(response, "json_dict") else json.loads(response.text())
        
        records = call_logs.get('records', [])
        if phoneNumber:
            phoneNumber = phoneNumber.strip()
            if not phoneNumber.startswith("+"):
                phoneNumber = "+" + phoneNumber
            filtered_records = []
            for log in records:
                direction = log.get("direction")
                from_number = log.get("from", {}).get("phoneNumber")
                to_number = log.get("to", {}).get("phoneNumber")
                if direction == "Inbound":
                    if phoneNumber == from_number:
                        filtered_records.append(log)
                elif direction == "Outbound":
                    if phoneNumber == to_number:
                        filtered_records.append(log)

            records = filtered_records


        return records
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))