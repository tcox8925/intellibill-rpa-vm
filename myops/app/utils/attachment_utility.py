import base64
from io import BytesIO
from typing import Optional, Union
from uuid import uuid4
from datetime import datetime, timezone
from fastapi.responses import StreamingResponse
from fastapi import HTTPException, UploadFile
from azure.core.exceptions import ResourceNotFoundError
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models import pch_attachments


def download_blob_stream_from_path(blob_path: str) -> StreamingResponse:
    """Download a blob and return a StreamingResponse.

    This helper accepts only the blob path (as stored in DB) and performs the
    blob client retrieval, download into memory, and StreamingResponse creation.

    Raises HTTPException(404) when blob or DB path is not found, or 500 on other errors.
    """
    if not blob_path:
        raise HTTPException(status_code=404, detail="Attachment path not provided")

    try:
        blob_client = settings.blob_service_client.get_blob_client(
            container=settings.ATTACHMENT_CONTAINER_NAME,
            blob=blob_path,
        )

        stream = BytesIO()
        download_stream = blob_client.download_blob()
        download_stream.readinto(stream)
        stream.seek(0)

        filename = blob_path.split("/")[-1]

        return StreamingResponse(
            stream,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Attachment blob not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching file: {str(e)}")


async def upload_blob_to_path(
    blob_path: str,
    file: Union[UploadFile, bytes],
    overwrite: bool = True,
) -> dict:
    """Upload an UploadFile or bytes to the provided blob_path and return metadata.

    This helper performs only the blob upload and does NOT touch the database.
    Returns a dict with `path`, `filename`, and `size`.
    """
    if not blob_path:
        raise HTTPException(status_code=400, detail="Blob path is required")

    # Read data
    if isinstance(file, UploadFile):
        try:
            data = await file.read()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to read upload file: {e}")
    elif isinstance(file, (bytes, bytearray)):
        data = bytes(file)
    else:
        raise HTTPException(status_code=400, detail="file must be UploadFile or bytes")

    # Upload
    try:
        blob_client = settings.blob_service_client.get_blob_client(
            container=settings.ATTACHMENT_CONTAINER_NAME,
            blob=blob_path,
        )
        blob_client.upload_blob(data, overwrite=overwrite)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Blob upload failed: {e}")

    return {"path": blob_path, "filename": blob_path.split("/")[-1], "size": len(data)}

def download_blob_as_base64(blob_path: str) -> str:
    if not blob_path:
        raise HTTPException(status_code=404, detail="Blob path not provided")

    try:
        blob_client = settings.blob_service_client.get_blob_client(
            container=settings.ATTACHMENT_CONTAINER_NAME,
            blob=blob_path,
        )

        image_bytes = blob_client.download_blob().readall()
        base64_image = base64.b64encode(image_bytes).decode("utf-8")

        return base64_image

    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Attachment blob not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching file: {str(e)}")
    
async def delete_blob_from_path(blob_path: str):
    """Delete a blob from Azure Blob Storage."""
    try:
        blob_client = settings.blob_service_client.get_blob_client(
            container=settings.ATTACHMENT_CONTAINER_NAME,
            blob=blob_path
        )
        blob_client.delete_blob()
    except ResourceNotFoundError:
        pass  # Blob already gone, no need to raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete blob: {str(e)}")