from fastapi import HTTPException, Depends
from pydantic import BaseModel
from fastapi import APIRouter
import httpx
from app.core.config import settings
from app.middleware.validator import get_current_user

router = APIRouter(tags=["POWER BI ROUTES"])

# Response model
class PowerBIEmbedInfo(BaseModel):
    embedToken: str
    embedUrl: str
    reportId: str

# Utility function: Get Azure AD access token
async def get_access_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    # https://analysis.windows.net/powerbi/api
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "resource": "https://analysis.windows.net/powerbi/api"
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(token_url, data=data)
        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Failed to get access token: {resp.text}")

        return resp.json()["access_token"]

# API endpoint
@router.get("/powerbi/embed-info/{workspace_id}/{report_id}", response_model=PowerBIEmbedInfo)
async def get_powerbi_embed_info(workspace_id: str, report_id: str):
    
    try:
        # Fetch secrets from env vars (or replace with Key Vault integration)
        tenant_id = settings.POWER_BI_TENANT_ID
        client_id = settings.POWER_BI_CLIENT_ID
        client_secret = settings.POWER_BI_CLIENT_SECRET
        
        if not tenant_id or not client_id or not client_secret:
            raise HTTPException(status_code=403, detail="Missing Power BI credentials")

        # Get access token
        access_token = await get_access_token(tenant_id, client_id, client_secret)

        # Prepare Power BI REST API client
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        # Step 1: Get report details
        report_url = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/reports/{report_id}"

        async with httpx.AsyncClient() as client:
            report_resp = await client.get(report_url, headers=headers)
            
            if report_resp.status_code != 200:
                raise HTTPException(status_code=500, detail=f"Failed to fetch report: {report_resp.text}")
            report = report_resp.json()

        # Step 2: Generate embed token
        token_url = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/reports/{report_id}/GenerateToken"
        token_payload = {
            "accessLevel": "view"
        }

        async with httpx.AsyncClient() as client:
            token_resp = await client.post(token_url, headers=headers, json=token_payload)
            if token_resp.status_code != 200:
                raise HTTPException(status_code=500, detail=f"Failed to generate embed token: {token_resp.text}")
            embed_token = token_resp.json()

        # Step 3: Return embed info
        return PowerBIEmbedInfo(
            embedToken=embed_token["token"],
            embedUrl=report["embedUrl"],
            reportId=report["id"]
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
