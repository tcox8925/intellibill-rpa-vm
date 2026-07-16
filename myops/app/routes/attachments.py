import asyncio
from fastapi import (
    APIRouter,
    File,
    Form,
    UploadFile,
)
from app.utils.attachment_utility import download_blob_stream_from_path, upload_blob_to_path

router = APIRouter(tags=["ATTACHMENT ROUTES"])

@router.get("/attachments/download")
async def download_attachment(path: str):
    return download_blob_stream_from_path(path)

@router.post("/attachments/upload")
async def upload_attachment(
    file: UploadFile = File(...),
    txn_id_provider: str = Form(...),
    npi: str = Form(...),
):
    file_name = file.filename
    blob_path = f"Documents/{txn_id_provider}/{npi}/{file_name}"
    meta = await upload_blob_to_path(blob_path, file, overwrite=True)
    return {"success": True, "message": f"File '{file_name}' uploaded successfully.", "meta": meta}