from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.config import get_recording_blob_service_client, settings, download_recording, download_blob
from app.models import CallRecording
from app.schemas import CallRecoringBase, CallRecordingSearchSchema
from sqlalchemy.orm import Session
from app.db.session import get_db
from fastapi.responses import StreamingResponse
from azure.core.exceptions import ResourceNotFoundError
import httpx
import datetime

router = APIRouter(tags=["CALL RECORDING ROUTES"])

@router.get("/call-recording", response_model=list[CallRecoringBase])
def get_agent_contracts(
    db: Session = Depends(get_db)
):
    return db.query(CallRecording).order_by(CallRecording.created_on.desc()).all()

@router.get("/call-recording/file-records")
def stream_recording(
    user_folder: str,
):
    """
    Stream a recording file from Azure Blob Storage.
    """
    try:
        blobs = download_blob(user_folder)

        results = []

        for blob in blobs:
            blob_name = blob.name

            if not blob_name.endswith(".wav"):
                continue  
            parts = blob_name.split("/") 

            if len(parts) < 5:
                continue  

            campaign = parts[-3]   
            recording_date = parts[-2]  
            file_name = parts[-1]   

            results.append({
                "file_name": file_name,
                "campaign": campaign,
                "recording_date": recording_date,
                "path": blob_name  
            })

        return {"recordings": results}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing recordings: {str(e)}")
    
@router.get("/call-recording/play")
async def play_recording(path: str, download: bool = False):
    """
    Stream a .wav recording from blob storage so it can play in the browser.
    path = full blob path returned from the list API
    """
    # return download_recording_blob(path, download)
    try:
        file_like = download_recording(path)

        disposition_type = "attachment" if download else "inline"

        return StreamingResponse(
            file_like,
            media_type="audio/wav",
            headers={
                "Content-Disposition": f'{disposition_type}; filename="{path.split("/")[-1]}"'
            }
        )

    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Recording not found: {path}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
   
# @router.post("/call-recording/search")
# async def search_recordings(data: CallRecordingSearchSchema):
#     """
#     Calls Azure Function 'http_trigger_search_recordings'
#     with username + date.
#     """

#     # data.date is now a date object, so we can format it.
#     formatted_date = data.date.strftime("%-m_%-d_%Y")

#     # Build request body
#     request_body = {
#         "username": data.username,
#         "date": formatted_date  # format like "M_d_yyyy"
#     }

#     print(f"Request Body: {request_body}")

#     url = f"{settings.RECORDING_AZURE_FUNCTION_URL}/orchestrators/http_trigger_search_recordings"

#     try:
#         async with httpx.AsyncClient(timeout=60.0) as client:
#             response = await client.post(url, json=request_body)

#         if response.status_code == 200:
#             return response.json()  # or response.text if it's plain string
#         else:
#             raise HTTPException(
#                 status_code=response.status_code,
#                 detail=f"Error calling Azure Function: {response.text}"
#             )

#     except Exception as e:
        # raise HTTPException(status_code=500, detail=f"Request failed: {str(e)}")