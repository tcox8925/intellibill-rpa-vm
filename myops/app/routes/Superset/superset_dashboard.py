import requests
from fastapi import APIRouter

# Note: making these constants strings as of now as we are not using these for now

SUPERSET_URL = "settings.APACHE_SUPERSET_URL"
USERNAME = "settings.APACHE_SUPERSET_USER"
PASSWORD = "settings.APACHE_SUPERSET_PASSWORD"

router = APIRouter(tags=["SUPERSET ROUTES"])

@router.get("/superset/guest-token/{dashboardId}")
def get_guest_token(dashboardId: str):
    session = requests.Session()  

    # 1️⃣ Login
    login_res = session.post(
        f"{SUPERSET_URL}/api/v1/security/login",
        json={
            "username": USERNAME,
            "password": PASSWORD,
            "provider": "db",
            "refresh": True
        }
    )
    login_res.raise_for_status()
    access_token = login_res.json()["access_token"]

    # 2️⃣ Get CSRF token (SAME SESSION)
    csrf_res = session.get(
        f"{SUPERSET_URL}/api/v1/security/csrf_token/",
        headers={
            "Authorization": f"Bearer {access_token}"
        }
    )
    csrf_res.raise_for_status()
    csrf_token = csrf_res.json()["result"]

    # 3️⃣ Create Guest Token (SAME SESSION)
    guest_res = session.post(
        f"{SUPERSET_URL}/api/v1/security/guest_token/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "X-CSRFToken": csrf_token,
            "Referer": SUPERSET_URL
        },
        json={
            "user": {
                "username": "embed_user"
            },
            "resources": [{
                "type": "dashboard",
                "id": dashboardId
            }],
            "rls": []
        }
    )

    guest_res.raise_for_status()
    return guest_res.json()

