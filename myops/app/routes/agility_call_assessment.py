# app/routes/call_ai.py
import json
import os, tempfile, logging
import traceback
from fastapi import UploadFile, File, HTTPException, Request, APIRouter, Depends, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, Date
from datetime import datetime, timezone, timedelta
import re
from azure.storage.blob import generate_blob_sas, BlobSasPermissions
from app.middleware.validator import get_current_user
from azure.core.exceptions import ResourceNotFoundError

from uuid import UUID, uuid4
from app.utils.score_transformer import transform_and_merge_detailed_score

# use absolute imports from your package
from app.utils.callAI.transcript_score import (
    get_token,
    fast_transcribe,
    create_transcript_lines,
)
from app.utils.callAI.QA_score import score_transcript_lines
from app.db.session import get_db
from app.models.AgilityAudioEvaluations import AgilityAgentAssessmentRecordings
from app.models.AgilityAssessmentRecordingNotes import (
    AgilityAssessmentRecordingNotes,
)
from app.core.config import (
    get_recording_blob_service_client,
    settings,
    upload_to_azure_blob,
)

# from app.utils.callAI.pdf_ocr import process_pdf_bytes, send_to_llm
from sqlalchemy.sql import text
from app.utils.callAI.pdf_ocr import extract_text_from_pdf, send_to_llm_agent
from app.models.centenesupplemental import CenteneSupplemental
from sqlalchemy import and_, or_

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("call-qa-app")

router = APIRouter(tags=["AGILITY ROUTES"])



def parse_possible_markdown_json(text: str):
    """
    Safely parse either raw JSON (`{}`) or Markdown-formatted JSON (```json {...}```).
    Returns a Python object (dict, list, etc.) or {} if parsing fails.
    """
    if not text:
        return {}

    # Remove markdown formatting if present
    cleaned = re.sub(r"^```json\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fallback to empty object if invalid JSON
        return {}

def parse_recorded_at_timestamp(timestamp_str: str) -> datetime:
    """
    Parse timestamp string in multiple formats:
    - ISO format: '2025-10-13T10:30:00Z' or '2025-10-13T10:30:00'
    - US format: '10/13/2025 10:30:00 AM' or '10/13/2025 10:30:00 PM'

    Returns a datetime object in UTC timezone.
    """
    # Try ISO format first
    try:
        return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except ValueError:
        pass

    # Try US format: MM/DD/YYYY HH:MM:SS AM/PM
    try:
        # Parse the US format and convert to UTC
        dt = datetime.strptime(timestamp_str, "%m/%d/%Y %I:%M:%S %p")
        # Assume local timezone and convert to UTC (you may want to adjust this based on your needs)
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    # Try US format without seconds: MM/DD/YYYY HH:MM AM/PM
    try:
        dt = datetime.strptime(timestamp_str, "%m/%d/%Y %I:%M %p")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    # If none of the formats work, raise an error
    raise ValueError(f"Unable to parse timestamp: {timestamp_str}")


class TranscribeResponse(BaseModel):
    lines: List[str] = Field(
        ..., description="Transcript lines like ['Agent: ...', 'Caller: ...']"
    )


class ScoreRequest(BaseModel):
    lines: List[str] = Field(
        ..., description="Transcript lines produced by /transcribe"
    )


class ScoreResponse(BaseModel):
    score: str = Field(..., description="The agent's score text")
    transcription: Optional[str] = Field(None, description="The call transcription")


class CreateNoteRequest(BaseModel):
    note_text: str = Field(..., description="The note content")


class UpdateNoteRequest(BaseModel):
    note_text: str = Field(..., description="The updated note content")


class NoteResponse(BaseModel):
    id: str = Field(..., description="Note ID")
    recording_id: str = Field(..., description="Recording ID")
    user_login: str = Field(..., description="User login who created the note")
    note_text: str = Field(..., description="The note content")
    created_at: datetime = Field(..., description="When the note was created")
    updated_at: datetime = Field(..., description="When the note was last updated")

    class Config:
        from_attributes = True


class DetailedScoreItem(BaseModel):
    """Individual score item in API format with flat data_id structure"""

    data_id: str = Field(..., description="Hierarchical identifier (e.g., '1', '1>1a')")
    label: str = Field(..., description="Human-readable label")
    awarded_score: Optional[int] = None
    max_score: Optional[int] = None
    percentage: Optional[str] = None
    required: Optional[bool] = None
    comments: Optional[str] = None
    editedScore: Optional[int] = Field(None, description="Modified score by reviewer")
    remarks: Optional[str] = Field(None, description="Additional remarks by reviewer")


class DetailedScoreRequest(BaseModel):
    recording_id: Optional[str] = Field(
        None, description="Recording ID (optional, can be in URL path)"
    )
    detailed_score: dict = Field(
        ..., description="Detailed score as a JSON object with 'data' array"
    )


class DetailedScoreResponse(BaseModel):
    recording_id: str = Field(..., description="Recording ID")
    detailed_score: dict = Field(..., description="Detailed score as a JSON object")


@router.post("/agility-transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    file: UploadFile = File(..., description="Audio file (wav/mp3/etc)")
):
    logger.info(f"Received /transcribe request: filename={file.filename}")
    try:
        suffix = os.path.splitext(file.filename or "")[1] or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            contents = await file.read()
            tmp.write(contents)
            tmp_path = tmp.name

        token = get_token()
        resp = fast_transcribe(token, tmp_path)
        lines = create_transcript_lines(resp)
        if not lines:
            raise HTTPException(status_code=422, detail="No transcript lines extracted")
        return TranscribeResponse(lines=lines)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")
    finally:
        try:
            if "tmp_path" in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


@router.post("/agility-score", response_model=ScoreResponse)
async def score_lines(payload: ScoreRequest, request: Request):
    try:
        score = score_transcript_lines(payload.lines)
        if not score:
            raise HTTPException(
                status_code=502, detail="Empty score returned from agent"
            )
        return ScoreResponse(score=score)
    except ValidationError as ve:
        raise HTTPException(status_code=422, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Scoring failed: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Scoring failed: {e}")


@router.post("/agility-assess-call-recording", response_model=ScoreResponse)
async def assess_call_recording(
    file: UploadFile = File(..., description="Audio file (wav/mp3/etc)"),
    agent_login: str = Form(..., description="Agent login identifier"),
    phone_number: str = Form(..., description="Phone number associated with the call"),
    recorded_at: str = Form(
        ...,
        description="Timestamp when the call was recorded (ISO format or MM/DD/YYYY HH:MM:SS AM/PM)",
    ),
    campaign: Optional[str] = Form(None, description="Campaign name (optional)"),
    db: Session = Depends(get_db),
):
    logger.info(
        f"Received /assess-call-recording request: filename={file.filename}, agent_login={agent_login}, phone_number={phone_number}, recorded_at={recorded_at}, campaign={campaign}"
    )
    tmp_path = None
    try:
        # Check if file with same name already exists in database
        existing_record = (
            db.query(AgilityAgentAssessmentRecordings)
            .filter(AgilityAgentAssessmentRecordings.file_name == file.filename)
            .first()
        )
        if existing_record:
            raise HTTPException(
                status_code=409,
                detail=f"File '{file.filename}' already exists in the database. Please use a different file name.",
            )

        # Parse the recorded_at timestamp
        try:
            recorded_at_dt = parse_recorded_at_timestamp(recorded_at)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Invalid recorded_at format. Please use ISO format (e.g., '2025-10-13T10:30:00Z') or US format (e.g., '10/13/2025 10:30:00 AM')",
            )

        suffix = os.path.splitext(file.filename or "")[1] or ".wav"
        filename_wo_ext = os.path.splitext(file.filename or "recording")[0]

        def sanitize_name(name: str) -> str:
            name = re.sub(r"[^a-zA-Z0-9\-_.]", "-", name)
            return name.strip(".-/").lower()

        agent_login_safe = sanitize_name(agent_login)
        filename_safe = sanitize_name(filename_wo_ext)

        # Create a timestamped filename before the extension
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        timestamped_filename = f"{filename_safe}_{timestamp}{suffix}"

        blob_name = f"{agent_login_safe.lower()}/{timestamped_filename}"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            contents = await file.read()
            tmp.write(contents)
            tmp_path = tmp.name

        # Upload to Azure Blob (container name fixed)
        BLOB_CONTAINER_NAME = "agility-assessment-login"
        blob_url = upload_to_azure_blob(tmp_path, blob_name, BLOB_CONTAINER_NAME)

        # Transcribe the audio
        token = get_token()
        resp = fast_transcribe(token, tmp_path)
        lines = create_transcript_lines(resp)
        if not lines:
            raise HTTPException(status_code=422, detail="No transcript lines extracted")

        # Convert transcript lines to a single string for storage
        transcription_text = "\n".join(lines)

        # Generate the score
        score = score_transcript_lines(lines)
        if not score:
            raise HTTPException(
                status_code=502, detail="Empty score returned from agent"
            )
        try:
            json_score = parse_possible_markdown_json(score)
        except Exception as e:
            json_score = {}

        # Store the results in the database
        new_recording = AgilityAgentAssessmentRecordings(
            agent_login=agent_login,
            recorded_at=recorded_at_dt,
            phone_number=phone_number,
            campaign=campaign or "",
            file_name=file.filename,
            file_location=blob_url,
            file_size=len(contents),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            detailed_score=score,
            total_score=json_score.get("total_score", 0),
            call_status="pass",  # Replace with actual logic to determine call status
            transcription=transcription_text,
        )
        db.add(new_recording)
        db.commit()
        db.refresh(new_recording)

        return ScoreResponse(score=score, transcription=transcription_text)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Assessment failed: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Assessment failed: {e}")


@router.get("/agility-assessment-recordings", response_model=List[dict])
async def get_grouped_agility_assessment_recordings(
    agent_login: Optional[str] = None,
    recorded_at: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Fetch grouped records from the AgilityAgentAssessmentRecordings table.
    Groups by agent_login and recorded_at date (without time).
    Returns agent_login, recorded_at_date, file_count, and average total_score.

    Optional filters:
    - agent_login: Filter by specific agent login
    - recorded_at: Filter by recorded date (ISO format date, e.g., '2025-10-13')
    """
    try:
        query = db.query(
            AgilityAgentAssessmentRecordings.agent_login,
            cast(AgilityAgentAssessmentRecordings.recorded_at, Date).label(
                "recorded_at_date"
            ),
            func.count(AgilityAgentAssessmentRecordings.id).label("file_count"),
            func.avg(AgilityAgentAssessmentRecordings.total_score).label(
                "average_total_score"
            ),
        )

        # Apply optional filters
        if agent_login:
            query = query.filter(
                AgilityAgentAssessmentRecordings.agent_login == agent_login
            )

        if recorded_at:
            try:
                # Parse the date string and filter by date (ignoring time)
                recorded_date = datetime.fromisoformat(
                    recorded_at.replace("Z", "+00:00")
                )
                # Filter for records on the same date
                query = query.filter(
                    cast(AgilityAgentAssessmentRecordings.recorded_at, Date)
                    == recorded_date.date()
                )
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid recorded_at format. Please use ISO format date (e.g., '2025-10-13' or '2025-10-13T10:30:00Z')",
                )

        # Group and execute query - group by date as well
        grouped_records = query.group_by(
            AgilityAgentAssessmentRecordings.agent_login,
            cast(AgilityAgentAssessmentRecordings.recorded_at, Date),
        ).all()

        result = []
        for record in grouped_records:

            result.append(
                {
                    "agent_login": record.agent_login,
                    "recorded_at_date": (
                        record.recorded_at_date.isoformat()
                        if record.recorded_at_date
                        else None
                    ),
                    "file_count": record.file_count,
                    "average_total_score": (
                        float(record.average_total_score)
                        if record.average_total_score is not None
                        else 0.0
                    ),
                }
            )

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching records: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to fetch records: {e}")


@router.get("/agility-assessment-recordings-v2", response_model=List[dict])
async def get_grouped_agility_assessment_recordings_v2(
    agent_login: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Fetch grouped records from the AgilityAgentAssessmentRecordings table.
    Groups by agent_login only (no date grouping).
    Returns agent_login and file_count.

    Optional filters:
    - agent_login: Filter by specific agent login
    """
    try:
        # Start building the query - group only by agent_login
        query = db.query(
            AgilityAgentAssessmentRecordings.agent_login,
            func.count(AgilityAgentAssessmentRecordings.id).label("file_count"),
        )

        # Apply optional filter
        if agent_login:
            query = query.filter(
                AgilityAgentAssessmentRecordings.agent_login == agent_login
            )

        # Group by agent_login only and execute query
        grouped_records = query.group_by(
            AgilityAgentAssessmentRecordings.agent_login,
        ).all()

        return [
            {
                "agent_login": record.agent_login,
                "file_count": record.file_count,
            }
            for record in grouped_records
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching records: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to fetch records: {e}")


@router.get("/agility-assessment-recordings/{agent_login}", response_model=List[dict])
async def get_agility_assessment_recordings_by_agent(
    agent_login: str, date: Optional[str] = None, db: Session = Depends(get_db)
):
    """
    Fetch detailed records for a specific agent from the AgilityAgentAssessmentRecordings table.
    Returns all detailed scores and information for the specified agent_login.

    Optional query parameters:
    - date: Filter by recorded date (ISO format date, e.g., '2025-10-13')
    """
    try:
        query = db.query(AgilityAgentAssessmentRecordings).filter(
            AgilityAgentAssessmentRecordings.agent_login == agent_login
        )

        # Apply date filter if provided
        if date:
            try:
                # Parse the date string and filter by date (ignoring time)
                recorded_date = datetime.fromisoformat(date.replace("Z", "+00:00"))
                # Filter for records on the same date
                query = query.filter(
                    cast(AgilityAgentAssessmentRecordings.recorded_at, Date)
                    == recorded_date.date()
                )
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid date format. Please use ISO format date (e.g., '2025-10-13' or '2025-10-13T10:30:00Z')",
                )

        records = query.all()

        if not records:
            date_filter = f" for date {date}" if date else ""
            raise HTTPException(
                status_code=404,
                detail=f"No records found for agent_login: {agent_login}{date_filter}",
            )

        # Fetch notes for each record
        result = []
        for record in records:
            notes = (
                db.query(AgilityAssessmentRecordingNotes)
                .filter(AgilityAssessmentRecordingNotes.recording_id == record.id)
                .order_by(AgilityAssessmentRecordingNotes.created_at.desc())
                .all()
            )

            result.append(
                {
                    "id": str(record.id),
                    "agent_login": record.agent_login,
                    "recorded_at": record.recorded_at,
                    "phone_number": record.phone_number,
                    "campaign": record.campaign,
                    "file_name": record.file_name,
                    "file_location": record.file_location,
                    "file_size": record.file_size,
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                    "detailed_score": record.detailed_score,
                    "total_score": record.total_score,
                    "call_status": record.call_status,
                    "transcription": record.transcription,
                    "total_edited_score": record.total_edited_score,
                    "edited_by": record.edited_by,
                    "edited_at": record.edited_at,
                    "notes": [
                        {
                            "id": str(note.id),
                            "user_login": note.user_login,
                            "note_text": note.note_text,
                            "created_at": note.created_at,
                            "updated_at": note.updated_at,
                        }
                        for note in notes
                    ],
                }
            )

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch records: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to fetch records: {e}")


@router.get("/agility-assessment-recordings/download/{recording_id}")
async def download_recording_endpoint(
    recording_id: str, db: Session = Depends(get_db), download: bool = False
):
    """
    Generate a SAS URL for downloading a specific recording audio file.
    Returns a download link with 1-hour expiration.
    """
    try:
        # Fetch the recording from database
        from uuid import UUID
        from app.core.config import download_recording
        from urllib.parse import urlparse

        try:
            recording_uuid = UUID(recording_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid recording ID format")

        record = (
            db.query(AgilityAgentAssessmentRecordings)
            .filter(AgilityAgentAssessmentRecordings.id == recording_uuid)
            .first()
        )

        if not record:
            raise HTTPException(
                status_code=404, detail=f"Recording not found with ID: {recording_id}"
            )

        # Extract blob path from file_location
        # If it's a full URL like "https://834analyticsdatalake.blob.core.windows.net/agility-assessment-login/..."
        # Extract just "agility-assessment-login/..."
        # If it's already just a path, use as-is
        file_location = record.file_location

        if file_location.startswith("http://") or file_location.startswith("https://"):
            # Parse the URL and extract the path (removing leading slash)
            parsed_url = urlparse(file_location)
            blob_path = parsed_url.path.lstrip("/")
        else:
            # Already a path, use as-is
            blob_path = file_location

        blob_container_name = blob_path.split("/")[0]
        file_path = "/".join(blob_path.split("/")[1:])

        # Download the blob
        file_like = download_recording(file_path, blob_container_name)

        # Extract filename from blob path
        filename = blob_path.split("/")[-1]

        disposition_type = "attachment" if download else "inline"

        return StreamingResponse(
            file_like,
            media_type="audio/wav",
            headers={
                "Content-Disposition": f'{disposition_type}; filename="{filename}"'
            },
        )

    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Recording not found: {blob_path}")
    except Exception as e:
        logger.error(f"Failed to download recording: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.post(
    "/agility-assessment-recordings/{recording_id}/notes",
    response_model=NoteResponse,
)
async def create_note(
    recording_id: str,
    payload: CreateNoteRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Create a new note for a specific recording.
    Returns the created note with all details.
    """
    try:
        from uuid import UUID

        try:
            recording_uuid = UUID(recording_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid recording ID format")

        # Check if recording exists
        record = (
            db.query(AgilityAgentAssessmentRecordings)
            .filter(AgilityAgentAssessmentRecordings.id == recording_uuid)
            .first()
        )

        if not record:
            raise HTTPException(
                status_code=404, detail=f"Recording not found with ID: {recording_id}"
            )

        # Create the note
        new_note = AgilityAssessmentRecordingNotes(
            recording_id=recording_uuid,
            user_login=current_user.get("email"),
            note_text=payload.note_text,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(new_note)
        db.commit()
        db.refresh(new_note)

        return NoteResponse(
            id=str(new_note.id),
            recording_id=str(new_note.recording_id),
            user_login=new_note.user_login,
            note_text=new_note.note_text,
            created_at=new_note.created_at,
            updated_at=new_note.updated_at,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create note: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to create note: {e}")


@router.put(
    "/agility-assessment-recordings/notes/{note_id}", response_model=NoteResponse
)
async def update_note(
    note_id: str, payload: UpdateNoteRequest, db: Session = Depends(get_db)
):
    """
    Update an existing note.
    Returns the updated note with all details.
    """
    try:
        from uuid import UUID

        try:
            note_uuid = UUID(note_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid note ID format")

        note = (
            db.query(AgilityAssessmentRecordingNotes)
            .filter(AgilityAssessmentRecordingNotes.id == note_uuid)
            .first()
        )

        if not note:
            raise HTTPException(
                status_code=404, detail=f"Note not found with ID: {note_id}"
            )

        # Update the note
        note.note_text = payload.note_text
        note.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(note)

        return NoteResponse(
            id=str(note.id),
            recording_id=str(note.recording_id),
            user_login=note.user_login,
            note_text=note.note_text,
            created_at=note.created_at,
            updated_at=note.updated_at,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update note: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to update note: {e}")


@router.delete("/agility-assessment-recordings/notes/{note_id}")
async def delete_note(note_id: str, db: Session = Depends(get_db)):
    """
    Delete a note for a specific recording.
    Returns a success message.
    """
    try:
        from uuid import UUID

        try:
            note_uuid = UUID(note_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid note ID format")

        note = (
            db.query(AgilityAssessmentRecordingNotes)
            .filter(AgilityAssessmentRecordingNotes.id == note_uuid)
            .first()
        )

        if not note:
            raise HTTPException(
                status_code=404, detail=f"Note not found with ID: {note_id}"
            )

        # Delete the note
        db.delete(note)
        db.commit()

        return {"message": "Note deleted successfully", "note_id": note_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete note: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to delete note: {e}")


@router.get(
    "/agility-assessment-recordings/{recording_id}/notes",
    response_model=List[NoteResponse],
)
async def get_recording_notes(recording_id: str, db: Session = Depends(get_db)):
    """
    Get all notes for a specific recording.
    Returns a list of notes sorted by created_at in descending order.
    """
    try:
        from uuid import UUID

        try:
            recording_uuid = UUID(recording_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid recording ID format")

        # Check if recording exists
        record = (
            db.query(AgilityAgentAssessmentRecordings)
            .filter(AgilityAgentAssessmentRecordings.id == recording_uuid)
            .first()
        )

        if not record:
            raise HTTPException(
                status_code=404, detail=f"Recording not found with ID: {recording_id}"
            )

        # Get all notes for the recording
        notes = (
            db.query(AgilityAssessmentRecordingNotes)
            .filter(AgilityAssessmentRecordingNotes.recording_id == recording_uuid)
            .order_by(AgilityAssessmentRecordingNotes.created_at.desc())
            .all()
        )

        return [
            NoteResponse(
                id=str(note.id),
                recording_id=str(note.recording_id),
                user_login=note.user_login,
                note_text=note.note_text,
                created_at=note.created_at,
                updated_at=note.updated_at,
            )
            for note in notes
        ]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch notes: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to fetch notes: {e}")


@router.post(
    "/agility-assessment-recordings/{recording_id}/detailed-score",
    response_model=DetailedScoreResponse,
)
async def store_detailed_score(
    recording_id: str,
    payload: DetailedScoreRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Store detailed score for a specific recording.

    Accepts detailed score in API format (flat array with data_id structure) and:
    1. Extracts editedScore and remarks from the API request
    2. Merges them into the existing DB format (hierarchical structure)
    3. Stores the updated detailed score in the database
    4. Returns the complete stored detailed score with edits merged in proper paths

    The request payload should contain:
    - recording_id: UUID of the recording
    - detailed_score: JSON object with a "data" array containing score items with:
        - data_id: hierarchical identifier (e.g., "1", "1>1a")
        - editedScore: (optional) modified score by reviewer
        - remarks: (optional) additional remarks by reviewer
        - other fields as per the API format

    Edits are merged directly into the hierarchical DB structure:
    - data_id "1" → db["1"]["editedScore"] and db["1"]["remarks"]
    - data_id "1>1a" → db["1"]["subcriteria"]["1a"]["editedScore"] and db["1"]["subcriteria"]["1a"]["remarks"]
    """
    try:

        try:
            recording_uuid = UUID(recording_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid recording ID format")

        # Check if recording exists
        record = (
            db.query(AgilityAgentAssessmentRecordings)
            .filter(AgilityAgentAssessmentRecordings.id == recording_uuid)
            .first()
        )

        if not record:
            raise HTTPException(
                status_code=404, detail=f"Recording not found with ID: {recording_id}"
            )

        # Extract the API format data (flat array with data_id)
        api_data = payload.detailed_score.get("data", [])

        if not isinstance(api_data, list):
            raise HTTPException(
                status_code=422,
                detail="Invalid format: 'data' field must be an array",
            )

        # Get current detailed score from database
        current_detailed_score = {}
        if record.detailed_score:
            try:
                current_detailed_score = json.loads(record.detailed_score)
            except json.JSONDecodeError as e:
                logger.warning(f"Could not parse existing detailed_score: {e}")
                current_detailed_score = {}

        # Transform and merge the new API data with existing DB format
        # Returns: (updated_score, total_edited_score, edited_at)
        try:
            updated_detailed_score, total_edited_score, edited_at = (
                transform_and_merge_detailed_score(current_detailed_score, api_data)
            )
        except ValueError as ve:
            # Handle validation error for incomplete editedScore
            raise HTTPException(
                status_code=422,
                detail=str(ve),
            )

        # Validate and serialize the updated score
        try:
            detailed_score_json = json.dumps(updated_detailed_score)
        except (TypeError, ValueError) as e:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid JSON format for detailed_score: {str(e)}",
            )

        # Store the updated detailed score
        record.detailed_score = detailed_score_json
        record.updated_at = datetime.now(timezone.utc)

        # Store total_edited_score and edited_at if edits were made
        if total_edited_score is not None:
            record.total_edited_score = total_edited_score
            record.edited_at = datetime.fromisoformat(edited_at) if edited_at else None
            record.edited_by = current_user.get("email")
            logger.info(
                f"Stored total_edited_score: {total_edited_score}, edited_at: {edited_at}"
            )

        # Add to session explicitly and commit
        db.merge(record)
        db.commit()

        # Refresh from database to confirm storage
        db.refresh(record)

        logger.info(f"Stored detailed score for recording {recording_id}")

        # Parse back the stored JSON for response
        stored_detailed_score = (
            json.loads(record.detailed_score) if record.detailed_score else {}
        )

        return DetailedScoreResponse(
            recording_id=recording_id,
            detailed_score=stored_detailed_score,
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to store detailed score: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500, detail=f"Failed to store detailed score: {e}"
        )


def parse_date(date_str: str) -> datetime:
    """
    Parse a date string in multiple formats.
    Supported formats:
    - 'YYYY-MM-DD' (ISO format)
    - 'MM/DD/YYYY' (US format)
    """
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Date '{date_str}' does not match supported formats.")

    files: List[UploadFile] = (File(...),)
    db: Session = (Depends(get_db),)

