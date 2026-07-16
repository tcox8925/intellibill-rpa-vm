# app/routes/call_ai.py
import json
import os, tempfile, logging
import traceback
import io
from fastapi import UploadFile, File, HTTPException, Request, APIRouter, Depends, Form, Query
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field, ValidationError
from typing import List, Optional, Dict, Any, Union, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, Date, asc, desc
from datetime import datetime, timezone, timedelta
import re
import json 
from azure.storage.blob import generate_blob_sas, BlobSasPermissions
from app.middleware.validator import get_current_user
from pydub import AudioSegment
from collections import OrderedDict
from uuid import UUID, uuid4
from app.utils.score_transformer import transform_and_merge_detailed_score
from azure.core.exceptions import ResourceNotFoundError

# use absolute imports from your package
from app.utils.callAI.transcript_score import (
    get_token,
    fast_transcribe,
    create_transcript_lines,
)
from app.utils.callAI.QA_score import score_transcript_lines
from app.db.session import get_db
from app.models.MemberCareAudioEvaluations import MembercareAgentAssessmentRecordings
from app.models.MembercareAssessmentRecordingNotes import (
    MembercareAssessmentRecordingNotes,
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
import app.utils.callAI.sud_file_creation as sud

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("call-qa-app")

router = APIRouter(tags=["USER ROUTES"])

def extract_awarded_total_score(payload: Union[str, bytes]) -> Optional[int]:
    """
    Extract awarded_total_score from a JSON string.
    1) Tries strict JSON parsing (recommended).
    2) Falls back to regex if JSON is malformed.
    Returns int if found, else None.
    """
    if payload is None:
        return None

    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="ignore")

    s = payload.strip()
    if not s:
        return None

    # 1) Preferred: parse as JSON
    try:
        obj: Any = json.loads(s)
        val = obj.get("awarded_total_score", None) if isinstance(obj, dict) else None
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return int(val)
    except json.JSONDecodeError:
        pass

    # 2) Fallback: regex extraction (handles broken JSON)
    m = re.search(r'"awarded_total_score"\s*:\s*(-?\d+(?:\.\d+)?)', s)
    if not m:
        return None

    try:
        return int(float(m.group(1)))
    except ValueError:
        return None

def get_call_status(total_score: Optional[int]) -> str:
    """
    Determine call status based on total_score.
    - Pass  : total_score >= 84
    - Fail  : total_score < 84 or invalid
    """
    try:
        if int(total_score)>= 84:
            return "Pass"
        else:
            return "Fail"
    except (TypeError, ValueError):
        return "Fail"


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

def normalize_to_utc_aware(dt: datetime) -> datetime:
    """
    Ensure dt is timezone-aware in UTC so comparisons don't crash.
    If dt is naive, assume UTC (matches your parser intent).
    If dt is aware, convert to UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def agent_for_recorded_at(dt: datetime) -> str:
    dt = normalize_to_utc_aware(dt)

    cutoff_2026 = datetime(2026, 1, 16, 0, 0, 0, tzinfo=timezone.utc)  #new line
    if dt >= cutoff_2026:  #new line
        return "Special_enrollment_2026"  #new line

    w2024_start = datetime(2024, 10, 31, 0, 0, 0, tzinfo=timezone.utc)
    w2024_end   = datetime(2025, 1, 16, 23, 59, 59, tzinfo=timezone.utc)

    w2025_start = datetime(2025, 10, 31, 0, 0, 0, tzinfo=timezone.utc)
    w2025_end   = datetime(2026, 1, 16, 23, 59, 59, tzinfo=timezone.utc)

    if w2024_start <= dt <= w2024_end:
        return "Open_enrollment_2024"

    if w2025_start <= dt <= w2025_end:
        # return "Open_enrollment_2025"
        return "Updated_OEP_2025"

    raise HTTPException(
        status_code=422,
        detail="recorded_at is outside valid OEP windows"
    )

def campaign_for_agent(agent_name: str) -> str:
    return {
        "Open_enrollment_2024": "OEP_2024",
        # "Open_enrollment_2025": "OEP_2025",
        "Updated_OEP_2025": "OEP_2025",
        "Special_enrollment_2026": "SEP_2026",  #new line
    }[agent_name]

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
    agent_used: str = Field(..., description="Which scoring agent was used")
    campaign: str = Field(..., description="Campaign applied to the call")
    sale_scorecard: str = Field(..., description="Sale scorecard applied to the call")


class CreateNoteRequest(BaseModel):
    note_text: str = Field(..., description="The note content")


class UpdateNoteRequest(BaseModel):
    note_text: str = Field(..., description="The updated note content")


class UpdateRecordingRequest(BaseModel):
    agent_login: Optional[str] = Field(None, description="The agent login to update")
    recorded_at: Optional[str] = Field(
        None,
        description="The recording date/time to update (ISO format or MM/DD/YYYY HH:MM:SS AM/PM)",
    )


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


@router.post("/transcribe", response_model=TranscribeResponse)
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


@router.post("/score", response_model=ScoreResponse)
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

# def year_to_agent_and_campaign(recorded_at_dt: datetime) -> tuple[str, str]:
#     """
#     Map recorded_at year to:
#       - Foundry agent name
#       - default campaign
#     """
#     if recorded_at_dt.year == 2025:
#         return "Open_enrollment_2025", "OEP_2025"
#     return "Final_Score", "OEP_2024"

def parse_year(recorded_at: str) -> int:
    """
    Supports:
      - ISO strings (with or without T / Z)
      - MM/DD/YYYY HH:MM:SS AM/PM
      - Fallback: any 4-digit year
    """
    s = (recorded_at or "").strip()
    if not s:
        raise HTTPException(status_code=422, detail="recorded_at is required")

    # Try ISO-ish
    try:
        s_clean = s.replace("Z", "").replace("z", "")
        if "T" not in s_clean and " " in s_clean:
            s_clean = s_clean.replace(" ", "T", 1)
        return datetime.fromisoformat(s_clean).year
    except Exception:
        pass

    # Try US format
    try:
        return datetime.strptime(s, "%m/%d/%Y %I:%M:%S %p").year
    except Exception:
        pass

    # Fallback regex
    m = re.search(r"(20\d{2})", s)
    if m:
        return int(m.group(1))

    raise HTTPException(
        status_code=422,
        detail=f"Could not parse recorded_at year: {recorded_at}",
    )


def agent_for_year(year: int) -> str:
    if year == 2026:  #new line
        return "Open_enrollment_2026"  #new line
    return "Updated_OEP_2025" if year == 2025 else "Open_enrollment_2024"
    # return "Open_enrollment_2025" if year == 2025 else "Open_enrollment_2024"


def campaign_for_agent(agent_name: str) -> str:
    if agent_name == "Special_enrollment_2026":  #new line
        return "SEP_2026"  #new line
    # if agent_name == "Open_enrollment_2025":
    #     return "OEP_2025"
    if agent_name == "Updated_OEP_2025":
        return "OEP_2025"
    if agent_name == "Open_enrollment_2024":
        return "OEP_2024"
    raise HTTPException(status_code=422, detail=f"Unknown agent: {agent_name}")


@router.post("/assess-call-recording", response_model=ScoreResponse)
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
            db.query(MembercareAgentAssessmentRecordings)
            .filter(MembercareAgentAssessmentRecordings.file_name == file.filename)
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
        
        # Pick agent + default campaign by year
        # year = parse_year(recorded_at)
        # ---- agent + campaign selection ----
        dt = parse_recorded_at_timestamp(recorded_at)
        ai_agent_name = agent_for_recorded_at(dt)
        effective_campaign = campaign_for_agent(ai_agent_name)
        # agent_name, default_campaign = year_to_agent_and_campaign(recorded_at_dt)
        # campaign_final = (campaign or "").strip() or default_campaign

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
        BLOB_CONTAINER_NAME = "membercare-assessment-login"
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
        score = score_transcript_lines(lines, agent_name=ai_agent_name, poll_timeout_s=1800)
        json_score = parse_possible_markdown_json(score)
        # if not score:
        #     raise HTTPException(
        #         status_code=502, detail="Empty score returned from agent"
        #     )
        # try:
        #     json_score = parse_possible_markdown_json(score)
        # except Exception as e:
        #     json_score = {}
        
        # print(json_score)
        # total_score = int(json_score.get("total_score", 0))
        total_score = int(json_score.get("awarded_total_score", 0))
        sale_no_sale = json_score.get("sale or not sale", "")
        if sale_no_sale == "" or sale_no_sale.lower() == 'not sale':
            sale_no_sale = 'Not sale'
            sale = score_transcript_lines(lines, agent_name='Not_Sale_Scorecard', poll_timeout_s=1800)
        else:
            sale_no_sale = 'Sale'
            sale = 'Sale'

        # Extract metadata fields from JSON for dedicated columns
        detailscore_obj = json_score.get("detailscore", {})
        if isinstance(detailscore_obj, str):
            try:
                detailscore_obj = json.loads(detailscore_obj)
            except (json.JSONDecodeError, TypeError):
                detailscore_obj = {}
        extracted_confidence = json_score.get("confidence_score")
        extracted_max_total = detailscore_obj.get("max_total_score")
        extracted_agent_name = detailscore_obj.get("agent_name")
        extracted_caller_name = detailscore_obj.get("caller_name")

        # Store the results in the database
        new_recording = MembercareAgentAssessmentRecordings(
            agent_login=agent_login,
            recorded_at=recorded_at_dt,
            phone_number=phone_number,
            campaign=effective_campaign or "",
            # campaign=campaign or "",
            file_name=file.filename,
            # file_location="",
            file_location=blob_url,
            file_size=len(contents),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            detailed_score=score,
            total_score=total_score,
            call_status=(
                "pass" if total_score > 84 else "fail"
            ),  # Replace with actual logic to determine call status
            transcription=transcription_text,
            entity_id="932058900",
            sub_entity_id="932058900001",
            sales_scorecard=sale,
            max_total_score=int(extracted_max_total) if extracted_max_total is not None else None,
            confidence_score=float(extracted_confidence) if extracted_confidence is not None else None,
            agent_name=str(extracted_agent_name)[:200] if extracted_agent_name else None,
            caller_name=str(extracted_caller_name)[:200] if extracted_caller_name else None,
            sale_or_not_sale=sale_no_sale,
        )
        db.add(new_recording)
        db.commit()
        db.refresh(new_recording)

        return ScoreResponse(score=score, transcription=transcription_text,
                             agent_used=ai_agent_name, campaign=effective_campaign,
                             sale_scorecard=sale)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Assessment failed: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Assessment failed: {e}")


@router.get("/membercare-assessment-recordings-v2", response_model=dict)
async def get_grouped_membercare_assessment_recordings_v2(
    entity_id: str,
    sub_entity_id: str,
    agent_login: Optional[str] = None,
    db: Session = Depends(get_db),
    page_size: int = 100,
    page_number: int = 1,
    sort_by: str = "file_count",     # file_count | agent_login
    sort_order: str = "desc"
):
    """
    Fetch grouped records from the MembercareAgentAssessmentRecordings table.
    Groups by agent_login only (no date grouping).
    Returns agent_login and file_count with pagination.

    Optional filters:
    - agent_login: Filter by specific agent login
    - page_size: Number of records per page (default: 100)
    - page_number: Page number (1-indexed, default: 1)
    """
    try:
        # Start building the query - group only by agent_login
        query = db.query(
            MembercareAgentAssessmentRecordings.agent_login,
            func.count(MembercareAgentAssessmentRecordings.id).label("file_count"),
        )

        # Apply entity_id and sub_entity_id filters if provided
        # if entity_id and sub_entity_id:
        query = query.filter(
                MembercareAgentAssessmentRecordings.entity_id == entity_id,
                MembercareAgentAssessmentRecordings.sub_entity_id == sub_entity_id,
            )


        # Apply optional filter
        if agent_login:
            query = query.filter(
                MembercareAgentAssessmentRecordings.agent_login == agent_login
            )

        # Group by agent_login only
        query = query.group_by(
            MembercareAgentAssessmentRecordings.agent_login,
        )

        # Sorting logic
        if sort_by == "agent_login":
            order_column = MembercareAgentAssessmentRecordings.agent_login
        else:
            # default + safe fallback
            order_column = func.count(MembercareAgentAssessmentRecordings.id)

        if sort_order == "asc":
            query = query.order_by(order_column.asc())
        else:
            query = query.order_by(order_column.desc())


        # Get total count before pagination
        total_count = query.count()

        # Apply pagination
        offset = (page_number - 1) * page_size
        grouped_records = query.offset(offset).limit(page_size).all()

        # Calculate total pages
        total_pages = (total_count + page_size - 1) // page_size

        return {
            "data": [
                {
                    "agent_login": record.agent_login,
                    "file_count": record.file_count,
                }
                for record in grouped_records
            ],
            "pagination": {
                "page_number": page_number,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": total_pages,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching records: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to fetch records: {e}")


import json
from typing import Any, Dict
from collections import OrderedDict

import json
from typing import Any, Dict
from collections import OrderedDict


def calculate_adjusted_from_llm_total(raw: str):
    data = json.loads(raw)

    llm_total = data.get("overall", {}).get("total_awarded_score", 0)
    detailscore = data.get("detailscore", {})

    delta = 0

    for criterion in detailscore.values():
        for sub in criterion.get("subcriteria", {}).values():
            awarded = sub.get("awarded_points", 0)
            edited = sub.get("editedScore")

            if edited is not None:
                delta += (edited - awarded)

    # adjusted_total = llm_total + delta
    adjusted_total = min(llm_total + delta, 100)
    return llm_total, adjusted_total


def add_type_to_detailscore(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Adds `type` inside each detailscore criterion dict.
    Accepts either a dict or a JSON string (as stored in DB).
    For each item in data["detailscore"]:
        - if the criterion dict contains key "compliance" => type = "compliance"
        - else => type = "qa"
    - Update in original json/dict and return it
    """
    input_was_string = isinstance(data, str)

    # Accept JSON string from DB
    if input_was_string:
        raw = data
        if not raw or not raw.strip():
            # preserve original empty string
            return data
        try:
            data_obj = json.loads(raw)
        except Exception:
            # If parsing fails, return original input to avoid breaking callers
            return data
    elif isinstance(data, dict):
        data_obj = data
    else:
        # Unexpected type, return as-is to preserve format
        return data

    # Get detailscore safely; if missing or not a dict, there's nothing to update.
    details = data_obj.get("detailscore")
    if not isinstance(details, dict):
        return data_obj  # Nothing to do, return original as-is.

    # Loop over each criterion block inside detailscore (keys like "1", "2", ...)
    for crit_id, crit_obj in list(details.items()):
        # We only expect dicts here; if any entry is not a dict, skip it safely.
        if not isinstance(crit_obj, dict):
            continue

        # If 'type' already exists, skip (makes function idempotent / safe to run multiple times).
        if "type" in crit_obj:
            continue

        # Decide the type based on whether this criterion dict has the "compliance" key.
        type_value = "compliance" if "compliance" in crit_obj else "qa"

        new_obj: Dict[str, Any] = {}
        inserted_type = False

        for k, v in crit_obj.items():
            new_obj[k] = v
            if k == "criterion":
                new_obj["type"] = type_value
                inserted_type = True

        if not inserted_type:
            new_obj["type"] = type_value

        details[crit_id] = new_obj

    # Return the modified object in the same format as the input
    return json.dumps(data_obj) if input_was_string else data_obj
   
@router.get("/membercare-assessment-recordings/{agent_login}", response_model=dict)
async def get_membercare_assessment_recordings_by_agent(
    agent_login: str,
    date: Optional[str] = None,
    page_size: int = 100,
    page_number: int = 1,
    sort_by: Optional[str] = "created_at",  # new line
    sort_order: str = "desc",  # new line
    db: Session = Depends(get_db),
):
    """
    Fetch detailed records for a specific agent from the MembercareAgentAssessmentRecordings table.
    Returns all detailed scores and information for the specified agent_login with pagination.

    Optional query parameters:
    - date: Filter by recorded date (ISO format date, e.g., '2025-10-13')
    - page_size: Number of records per page (default: 100)
    - page_number: Page number (1-indexed, default: 1)
    """
    try:

        # Start with base query for the agent
        query = db.query(MembercareAgentAssessmentRecordings).filter(
            MembercareAgentAssessmentRecordings.agent_login == agent_login
        )

        # Apply date filter if provided
        if date:
            try:
                # Parse the date string and filter by date (ignoring time)
                recorded_date = datetime.fromisoformat(date.replace("Z", "+00:00"))
                # Filter for records on the same date
                query = query.filter(
                    cast(MembercareAgentAssessmentRecordings.recorded_at, Date)
                    == recorded_date.date()
                )
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid date format. Please use ISO format. Use ISO format (YYYY-MM-DD)",
                )
        
        # DB-level sorting fields  # new line
        DB_SORTABLE_FIELDS = {  # new line
            "file_name": MembercareAgentAssessmentRecordings.file_name,  # new line
            "recorded_at": MembercareAgentAssessmentRecordings.recorded_at,  
            "created_at": MembercareAgentAssessmentRecordings.created_at,# new line
        }  # new line

        if sort_by in DB_SORTABLE_FIELDS:  # new line
            sort_column = DB_SORTABLE_FIELDS[sort_by]  # new line
            query = query.order_by(  # new line
                asc(sort_column) if sort_order == "asc" else desc(sort_column)  # new line
            )  # new line

        # Get total count before pagination
        total_count = query.count()

        # Apply pagination
        # offset = (page_number - 1) * page_size
        # records = query.offset(offset).limit(page_size).all()

        # Apply DB pagination only for DB-sortable fields  # new line
        if sort_by in DB_SORTABLE_FIELDS:  # new line
            offset = (page_number - 1) * page_size
            records = query.offset(offset).limit(page_size).all()
        else:  # new line
            records = query.all()  # new line

        if not records and page_number == 1:
            date_filter = f" for date {date}" if date else ""
            raise HTTPException(
                status_code=404,
                detail=f"No records found for agent_login: {agent_login}{date_filter}",
            )

        # Fetch notes for each record
        result = []
        for record in records:
            notes = (
                db.query(MembercareAssessmentRecordingNotes)
                .filter(MembercareAssessmentRecordingNotes.recording_id == record.id)
                .order_by(MembercareAssessmentRecordingNotes.created_at.desc())
                .all()
            )
            detailscore = add_type_to_detailscore(record.detailed_score)
            detailscore_dict = (json.loads(detailscore) if isinstance(detailscore, str) else detailscore)
            total_score = extract_awarded_total_score(record.detailed_score)
            call_status = get_call_status(total_score)
            sale_or_not_sale = detailscore_dict.get("sale or not sale")
            edited_sale_score = detailscore_dict.get("edited_sale_or_not_sale", "")
            awarded, final = calculate_adjusted_from_llm_total(detailscore) 

            # detailscore = ('"'+add_type_to_detailscore(detailscore)+'"')
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
                    "detailed_score": detailscore,
                    # "detailed_score": record.detailscore,
                    # "total_score": record.total_score,
                    # "total_score": total_score,
                    "total_score": awarded,
                    "call_status": get_call_status(total_score),
                    # "call_status": record.call_status,
                    "transcription": record.transcription,
                    "sale_score": record.sales_scorecard,
                    "total_edited_score": record.total_edited_score,
                    "edited_by": record.edited_by,
                    "edited_at": record.edited_at,
                    "edited_compliance_score": record.edited_compliance_score,
                    "edited_sale_score": edited_sale_score,
                    "final_score": final,
                    "agent_name": record.agent_name or detailscore_dict.get("detailscore", {}).get("agent_name") if isinstance(detailscore_dict.get("detailscore"), dict) else record.agent_name,
                    "caller_name": record.caller_name or detailscore_dict.get("detailscore", {}).get("caller_name") if isinstance(detailscore_dict.get("detailscore"), dict) else record.caller_name,
                    "max_total_score": record.max_total_score,
                    "confidence_score": record.confidence_score or detailscore_dict.get("confidence_score"),
                    "sale_or_not_sale": record.sale_or_not_sale or sale_or_not_sale,
                    "edited_sale_reason": record.edited_sale_reason or detailscore_dict.get("edited_sale_reason"),
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

        # Python-level sorting for derived fields  # new line
        DERIVED_SORT_FIELDS = {  # new line
            "total_score": lambda x: x["total_score"] or 0,  # new line
            "call_status": lambda x: x["call_status"] or "",  # new line
            "sale_or_not_sale": lambda x: x.get("sale_or_not_sale") or "",  # new line
        }  # new line

        if sort_by in DERIVED_SORT_FIELDS:  # new line
            result.sort(  # new line
                key=DERIVED_SORT_FIELDS[sort_by],  # new line
                reverse=(sort_order == "desc"),  # new line
            )  # new line

            # Apply pagination after derived sorting  # new line
            start = (page_number - 1) * page_size  # new line
            end = start + page_size  # new line
            result = result[start:end]  # new line

        total_pages = (total_count + page_size - 1) // page_size


        # Calculate total pages
        total_pages = (total_count + page_size - 1) // page_size

        return {
            "data": result,
            "pagination": {
                "page_number": page_number,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": total_pages,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch records: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to fetch records: {e}")


@router.get("/membercare-assessment-recordings/download/{recording_id}")
async def download_recording_endpoint(
    recording_id: str,
    request: Request,
    db: Session = Depends(get_db),
    download: bool = False,
):
    """
    Stream audio file for browser playback with range request support.
    Supports both inline playback and download.
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
            db.query(MembercareAgentAssessmentRecordings)
            .filter(MembercareAgentAssessmentRecordings.id == recording_uuid)
            .first()
        )

        if not record:
            raise HTTPException(
                status_code=404, detail=f"Recording not found with ID: {recording_id}"
            )

        # Extract blob path from file_location
        file_location = record.file_location

        if file_location.startswith("http://") or file_location.startswith("https://"):
            parsed_url = urlparse(file_location)
            blob_path = parsed_url.path.lstrip("/")
        else:
            blob_path = file_location

        blob_container_name = blob_path.split("/")[0]
        file_path = "/".join(blob_path.split("/")[1:])

        # Download the blob
        file_like = download_recording(file_path, blob_container_name)

        # Extract filename and detect content type
        filename = blob_path.split("/")[-1]
        file_extension = os.path.splitext(filename)[1].lower()

        # Read entire content
        file_like.seek(0)
        full_content = file_like.read()

        # Try to convert audio to browser-compatible format if it's WAV
        # This handles telephony codecs like G.711 μ-law/A-law that browsers can't play
        if file_extension == ".wav":
            try:
                # Load audio using pydub (handles various codecs)
                audio = AudioSegment.from_file(io.BytesIO(full_content), format="wav")

                # Convert to PCM WAV (16-bit, which browsers support)
                # Export with standard settings that work in all browsers
                output_buffer = io.BytesIO()
                audio.export(
                    output_buffer,
                    format="wav",
                    parameters=["-acodec", "pcm_s16le"],  # 16-bit PCM
                )
                output_buffer.seek(0)
                full_content = output_buffer.read()

                logger.info(f"Converted audio to browser-compatible PCM WAV format")
            except ImportError:
                logger.warning("pydub not available, serving original audio file")
            except Exception as e:
                logger.warning(f"Audio conversion failed: {e}, serving original file")

        # Determine correct MIME type based on file extension
        content_type_map = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".ogg": "audio/ogg",
            ".webm": "audio/webm",
            ".flac": "audio/flac",
            ".aac": "audio/aac",
        }
        media_type = content_type_map.get(file_extension, "audio/wav")

        # Get file size
        file_size = len(full_content)

        # Handle range requests for browser audio playback
        range_header = request.headers.get("range")

        if range_header:
            # Parse range header (format: "bytes=start-end")
            range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if range_match:
                start = int(range_match.group(1))
                end = (
                    int(range_match.group(2)) if range_match.group(2) else file_size - 1
                )
                end = min(end, file_size - 1)

                # Extract the requested range from content
                content = full_content[start : end + 1]
                chunk_size = len(content)

                # Return partial content with 206 status
                headers = {
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(chunk_size),
                    "Content-Type": media_type,
                    "Content-Disposition": f'{"attachment" if download else "inline"}; filename="{filename}"',
                    "Cache-Control": "public, max-age=3600",
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                    "Access-Control-Allow-Headers": "Range, Authorization",
                    "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges",
                }

                from fastapi.responses import Response

                return Response(
                    content=content,
                    status_code=206,
                    headers=headers,
                    media_type=media_type,
                )

        # No range request - return full file with proper headers
        disposition_type = "attachment" if download else "inline"

        from fastapi.responses import Response

        return Response(
            content=full_content,
            status_code=200,
            media_type=media_type,
            headers={
                "Content-Disposition": f'{disposition_type}; filename="{filename}"',
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
                "Cache-Control": "public, max-age=3600",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "Range, Authorization",
                "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges",
            },
        )

    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Recording not found: {blob_path}")
    except Exception as e:
        logger.error(f"Failed to download recording: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.put("/membercare-assessment-recordings/{recording_id}")
async def update_recording(
    recording_id: str,
    payload: UpdateRecordingRequest,
    db: Session = Depends(get_db),
):
    """
    Update agent_login and/or recorded_at for a specific recording.
    """
    try:
        from uuid import UUID

        try:
            recording_uuid = UUID(recording_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid recording ID format")

        # Check if recording exists
        record = (
            db.query(MembercareAgentAssessmentRecordings)
            .filter(MembercareAgentAssessmentRecordings.id == recording_uuid)
            .first()
        )

        if not record:
            raise HTTPException(
                status_code=404, detail=f"Recording not found with ID: {recording_id}"
            )

        # Update fields if provided
        updated = False
        if payload.agent_login is not None:
            record.agent_login = payload.agent_login
            updated = True

        if payload.recorded_at is not None:
            try:
                # Parse the timestamp using the helper function
                parsed_timestamp = parse_recorded_at_timestamp(payload.recorded_at)
                record.recorded_at = parsed_timestamp
                updated = True
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid recorded_at format. Use ISO format (YYYY-MM-DDTHH:MM:SS) or US format (MM/DD/YYYY HH:MM:SS AM/PM): {str(e)}",
                )

        if not updated:
            raise HTTPException(
                status_code=400,
                detail="No fields to update. Provide agent_login and/or recorded_at.",
            )

        # Update the updated_at timestamp
        record.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(record)

        return {
            "message": "Recording updated successfully",
            "recording_id": str(record.id),
            "agent_login": record.agent_login,
            "recorded_at": record.recorded_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to update recording: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to update recording: {e}")


@router.post(
    "/membercare-assessment-recordings/{recording_id}/notes",
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
            db.query(MembercareAgentAssessmentRecordings)
            .filter(MembercareAgentAssessmentRecordings.id == recording_uuid)
            .first()
        )

        if not record:
            raise HTTPException(
                status_code=404, detail=f"Recording not found with ID: {recording_id}"
            )

        # Create the note
        new_note = MembercareAssessmentRecordingNotes(
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
    "/membercare-assessment-recordings/notes/{note_id}", response_model=NoteResponse
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
            db.query(MembercareAssessmentRecordingNotes)
            .filter(MembercareAssessmentRecordingNotes.id == note_uuid)
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


@router.delete("/membercare-assessment-recordings/notes/{note_id}")
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
            db.query(MembercareAssessmentRecordingNotes)
            .filter(MembercareAssessmentRecordingNotes.id == note_uuid)
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
    "/membercare-assessment-recordings/{recording_id}/notes",
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
            db.query(MembercareAgentAssessmentRecordings)
            .filter(MembercareAgentAssessmentRecordings.id == recording_uuid)
            .first()
        )

        if not record:
            raise HTTPException(
                status_code=404, detail=f"Recording not found with ID: {recording_id}"
            )

        # Get all notes for the recording
        notes = (
            db.query(MembercareAssessmentRecordingNotes)
            .filter(MembercareAssessmentRecordingNotes.recording_id == recording_uuid)
            .order_by(MembercareAssessmentRecordingNotes.created_at.desc())
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
    "/membercare-assessment-recordings/{recording_id}/detailed-score",
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
            db.query(MembercareAgentAssessmentRecordings)
            .filter(MembercareAgentAssessmentRecordings.id == recording_uuid)
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

        # Extract sale data from the payload (if provided)
        sale_data = payload.detailed_score.get("sale", {})

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

        # Calculate edited_compliance_score
        # Dynamically find all compliance criteria (those with "compliance" field)
        compliance_status = "Pass"
        detailscore = updated_detailed_score.get("detailscore", {})

        for crit_key, criterion in detailscore.items():
            if not crit_key.isdigit() or not isinstance(criterion, dict):
                continue
            # Only check criteria that have a "compliance" field
            if "compliance" not in criterion:
                continue

            # Use editedScore if present, otherwise fall back to compliance field
            if "editedScore" in criterion:
                status_value = criterion["editedScore"]
            else:
                status_value = criterion["compliance"]

            # If any criterion fails, set overall compliance to "Fail"
            if status_value == "Fail":
                compliance_status = "Fail"
                break

        # Store edited_compliance_score in the record
        record.edited_compliance_score = compliance_status
        logger.info(f"Calculated edited_compliance_score: {compliance_status}")

        # Store edited sale data in the detailed_score JSON and dedicated columns
        if sale_data and sale_data.get("sale_or_nosale"):
            raw_sale = sale_data.get("sale_or_nosale", "")
            normalized_sale = "Sale" if raw_sale.lower() == "sale" else "Not sale" if raw_sale.lower() in ("no sale", "not sale") else raw_sale
            edited_reason = sale_data.get("reason", "")
            updated_detailed_score["edited_sale_or_not_sale"] = normalized_sale
            updated_detailed_score["edited_sale_reason"] = edited_reason
            # Re-serialize after adding sale data
            record.detailed_score = json.dumps(updated_detailed_score)
            # Persist to dedicated columns
            record.edited_sale_or_not_sale = normalized_sale
            record.edited_sale_reason = edited_reason
            logger.info(f"Stored edited sale: {sale_data.get('sale_or_nosale')}")

        # print(detailed_score_json)
        # awarded, final = calculate_adjusted_from_llm_total(detailed_score_json)
        # updated_detailed_score["final_score"] = final
        # record.detailed_score = json.dumps(updated_detailed_score)

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

    # files: List[UploadFile] = (File(...),)
    # db: Session = (Depends(get_db),)


@router.post("/extract")
async def extract_pdfs(
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    results = []

    for file in files:
        if file.content_type not in ("application/pdf", "application/octet-stream"):
            results.append(
                {
                    "filename": file.filename or "unknown",
                    "ocr_text": "",
                    "agent_response": "",
                    "message": f"Skipped: not a PDF (content_type={file.content_type})",
                }
            )
            continue

        pdf_bytes = await file.read()
        if not pdf_bytes:
            results.append(
                {
                    "filename": file.filename or "unknown",
                    "ocr_text": "",
                    "agent_response": "",
                    "message": "Uploaded file is empty.",
                }
            )
            continue

        file_name = file.filename or "uploaded.pdf"

        # 1) OCR using the shared pipeline
        try:
            ocr_text = process_pdf_bytes(pdf_bytes)
        except Exception as e:
            results.append(
                {
                    "filename": file_name,
                    "ocr_text": "",
                    "agent_response": "",
                    "message": f"Error during OCR: {e}",
                }
            )
            continue

        if not ocr_text:
            results.append(
                {
                    "filename": file_name,
                    "ocr_text": "",
                    "agent_response": "",
                    "message": "No OCR text extracted from the PDF.",
                }
            )
            continue

        # 2) Call Azure Agent
        try:
            llm_input = f"""
Filename: {file_name}

ExtractedText:
{ocr_text}
""".strip()

            agent_response = send_to_llm(llm_input)
            logger.info(f"Agent response: {agent_response}")

            if not agent_response:
                raise HTTPException(status_code=500, detail="Agent response is empty.")

            # Parse the agent_response JSON string into a dictionary
            try:
                response_data = json.loads(agent_response)
            except json.JSONDecodeError as e:
                raise HTTPException(
                    status_code=500, detail=f"Invalid JSON in agent response: {e}"
                )

        except Exception as e:
            results.append(
                {
                    "filename": file_name,
                    "ocr_text": ocr_text,
                    "agent_response": "",
                    "message": f"Error calling Azure Agent: {e}",
                }
            )
            continue

        # 3) Insert into the database
        try:
            new_record = {
                "id": str(uuid4()),  # Use uuid4() to generate a unique ID
                "cnc_member_id": response_data.get("CNCMemberId", ""),
                "medicaid_nbr": response_data.get("MedicaidNbr", ""),
                "mbi": response_data.get("MBI", ""),
                "last_name": response_data.get("LastName", ""),
                "first_name": response_data.get("FirstName", ""),
                "dob": parse_date(
                    response_data.get("DOB", "1900-01-01")
                ),  # Use parse_date
                "dos": parse_date(
                    response_data.get("DOS", "1900-01-01")
                ),  # Use parse_date
                "icd_px1": response_data.get("ICDPx1", ""),
                "icd_px2": response_data.get("ICDPx2", ""),
                "icd_dx_pri": response_data.get("ICDDxPri", "")[
                    :50
                ],  # Truncate to 50 characters
                "icd_dx_sec1": response_data.get("ICDDxSec1", "")[
                    :50
                ],  # Truncate to 50 characters
                "icd_dx_sec2": response_data.get("ICDDxSec2", "")[
                    :50
                ],  # Truncate to 50 characters
                "icd_dx_sec3": response_data.get("ICDDxSec3", "")[
                    :50
                ],  # Truncate to 50 characters
                "icd_dx_sec4": response_data.get("ICDDxSec4", "")[
                    :50
                ],  # Truncate to 50 characters
                "icd_version": response_data.get("ICDVersion", ""),
                "cpt_px": response_data.get("CPTPx", ""),
                "hcpc_ps_px": response_data.get("HCPCPSPx", ""),
                "hcfa_pos": response_data.get("HCFAPOS", ""),
                "cvx": response_data.get("CVX", ""),
                "bp_systolic": response_data.get("BPSystolic", "")[:50],
                "bp_diastolic": response_data.get("BPDiastolic", "")[:50],
                "bmi_value": response_data.get("BmiValue", "")[:50],
                "provider_npi": response_data.get("ProviderNPI", "")[:50],
                "provider_tin": response_data.get("ProviderTIN", "")[:50],
                "taxonomy_code": response_data.get("TaxonomyCode", "")[:50],
                "cms_specialty_code": response_data.get("CmsSpecialtyCode", ""),
                "pdf_name": file_name,
                "entity_id": "932058900",
                "sub_entity_id": "932058900001",
            }

            query = text(
                """
            INSERT INTO wpo.centene_supplemental (
                id, cnc_member_id, medicaid_nbr, mbi, last_name, first_name, dob, dos,
                icd_px1, icd_px2, icd_dx_pri, icd_dx_sec1, icd_dx_sec2, icd_dx_sec3, icd_dx_sec4,
                icd_version, cpt_px, hcpc_ps_px, hcfa_pos, cvx,
                bp_systolic, bp_diastolic, bmi_value, provider_npi, provider_tin, taxonomy_code,
                cms_specialty_code, pdf_name, entity_id, sub_entity_id
            ) VALUES (
                :id, :cnc_member_id, :medicaid_nbr, :mbi, :last_name, :first_name, :dob, :dos,
                :icd_px1, :icd_px2, :icd_dx_pri, :icd_dx_sec1, :icd_dx_sec2, :icd_dx_sec3, :icd_dx_sec4,
                :icd_version, :cpt_px, :hcpc_ps_px, :hcfa_pos, :cvx,
                :bp_systolic, :bp_diastolic, :bmi_value, :provider_npi, :provider_tin, :taxonomy_code,
                :cms_specialty_code, :pdf_name, :entity_id, :sub_entity_id
            )
            """
            )

            db.execute(query, new_record)
            db.commit()

            results.append(
                {
                    "filename": file_name,
                    "ocr_text": ocr_text,
                    "agent_response": agent_response,
                    "message": "Success, data inserted into the database.",
                }
            )

        except Exception as e:
            db.rollback()
            results.append(
                {
                    "filename": file_name,
                    "ocr_text": ocr_text,
                    "agent_response": agent_response,
                    "message": f"Error inserting data into the database: {e}",
                }
            )

    return results


@router.post("/medical-details")
async def extract_llm_output(
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """
    Upload PDF(s), perform OCR, send to LLM agent, and return final output.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    results = []
    processed_files = set()  # To track processed filenames and avoid duplicates

    for file in files:
        logger.info(f"Received file: {file.filename}")

        # Skip duplicate files
        if file.filename in processed_files:
            logger.warning(f"Duplicate file detected: {file.filename}. Skipping.")
            continue

        # Validate PDF file type
        if file.content_type not in ("application/pdf", "application/octet-stream"):
            logger.warning(f"Skipped (not a PDF): {file.filename}")
            continue

        # Read uploaded file bytes
        pdf_bytes = await file.read()
        if not pdf_bytes:
            logger.warning(f"Empty file detected: {file.filename}")
            continue

        # Save PDF temporarily
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
                temp_pdf.write(pdf_bytes)
                temp_path = temp_pdf.name
        except Exception as e:
            logger.error(f"File save error for {file.filename}: {e}")
            continue

        # --- Step 1: OCR ---
        try:
            ocr_text = extract_text_from_pdf(temp_path)
            logger.info(f"OCR complete for {file.filename} (chars: {len(ocr_text)})")
        except Exception as e:
            logger.error(f"OCR failed for {file.filename}: {e}")
            continue

        if not ocr_text.strip():
            logger.warning(f"OCR returned empty output for {file.filename}")
            continue

        # --- Step 2: Send text to LLM agent ---
        try:
            final_output = send_to_llm_agent(ocr_text)
            logger.info(f"LLM completed for {file.filename}")

            # Parse the final_output JSON string into a Python dictionary
            final_output_dict = json.loads(final_output)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse final_output for {file.filename}: {e}")
            raise HTTPException(
                status_code=500, detail="Invalid JSON format in final_output"
            )
        except Exception as e:
            logger.error(f"LLM error for {file.filename}: {e}")
            continue

        # Successfully processed file
        logger.info(f"Successfully processed {file.filename}")
        processed_files.add(file.filename)  # Mark file as processed

        # --- Step 3: Insert into the database ---

        try:
            raw_name = f"{final_output_dict.get('FirstName', '')} {final_output_dict.get('MiddleName', '')} {final_output_dict.get('LastName', '')}"
            # --- Step 3A: Check if PDF already exists in DB ---
            existing = (
                db.query(CenteneSupplemental)
                .filter(CenteneSupplemental.pdf_name == file.filename)
                .first()
            )

            if existing:
                results.append(
                    {
                        "filename": file.filename,
                        "message": f"{file.filename} already exists",
                    }
                )
                continue

            # Use the CenteneSupplemental model to create a new record
            new_record = CenteneSupplemental(
                id=str(uuid4()),  # Generate a unique ID
                cnc_member_id=final_output_dict.get("CNCMemberId", "")[
                    :50
                ],  # Truncate to 50 chars
                medicaid_nbr=final_output_dict.get("MedicaidNbr", "")[
                    :50
                ],  # Truncate to 50 chars
                mbi=final_output_dict.get("MBI", "")[:50],  # Truncate to 50 chars
                last_name=final_output_dict.get("LastName", "")[
                    :50
                ],  # Truncate to 50 chars
                first_name=final_output_dict.get("FirstName", "")[
                    :50
                ],  # Truncate to 50 chars
                middle_name=final_output_dict.get("MiddleName", "")[
                    :50
                ],  # Truncate to 50 chars
                dob=final_output_dict.get("DOB", "")[:50],  # Truncate to 50 chars
                dos=final_output_dict.get("DOS", "")[:50],  # Truncate to 50 chars
                dos_thru=final_output_dict.get("DOSThru", "")[
                    :50
                ],  # Truncate to 50 chars
                claim_number=final_output_dict.get("ClaimNumber", "")[
                    :80
                ],  # Truncate to 80 chars
                cnc_plan_code=final_output_dict.get("CNCPlanCode", "")[
                    :50
                ],  # Truncate to 50 chars
                hichn=final_output_dict.get("HICHN", "")[:50],  # Truncate to 50 chars
                event_diagnosis=final_output_dict.get("EventDiagnosis", "")[
                    :200
                ],  # Truncate to 200 chars
                icd_px1=final_output_dict.get("ICDPx1", "")[
                    :50
                ],  # Truncate to 50 chars
                icd_px2=final_output_dict.get("ICDPx2", "")[
                    :50
                ],  # Truncate to 50 chars
                icd_dx_pri=final_output_dict.get("ICDDxPri", "")[
                    :50
                ],  # Truncate to 50 chars
                icd_dx_sec1=final_output_dict.get("ICDDxSec1", "")[
                    :50
                ],  # Truncate to 50 chars
                icd_dx_sec2=final_output_dict.get("ICDDxSec2", "")[
                    :50
                ],  # Truncate to 50 chars
                icd_dx_sec3=final_output_dict.get("ICDDxSec3", "")[
                    :50
                ],  # Truncate to 50 chars
                icd_dx_sec4=final_output_dict.get("ICDDxSec4", "")[
                    :50
                ],  # Truncate to 50 chars
                icd_dx_sec5=final_output_dict.get("ICDDxSec5", "")[
                    :50
                ],  # Truncate to 50 chars
                icd_dx_sec6=final_output_dict.get("ICDDxSec6", "")[
                    :50
                ],  # Truncate to 50 chars
                icd_dx_sec7=final_output_dict.get("ICDDxSec7", "")[
                    :50
                ],  # Truncate to 50 chars
                icd_dx_sec8=final_output_dict.get("ICDDxSec8", "")[
                    :50
                ],  # Truncate to 50 chars
                icd_dx_sec9=final_output_dict.get("ICDDxSec9", "")[
                    :50
                ],  # Truncate to 50 chars
                icd_dx_sec10=final_output_dict.get("ICDDxSec10", "")[
                    :50
                ],  # Truncate to 50 chars
                cpt_px=final_output_dict.get("CPTPx", "")[:50],  # Truncate to 50 chars
                cpt_mod=final_output_dict.get("CPTMod", "")[
                    :50
                ],  # Truncate to 50 chars
                hcpc_ps_px=final_output_dict.get("HCPCPSPx", "")[
                    :50
                ],  # Truncate to 50 chars
                hcpc_ps_mod=final_output_dict.get("HCPCPSMod", "")[
                    :50
                ],  # Truncate to 50 chars
                ub_revenue_code=final_output_dict.get("UBRevenueCode", "")[
                    :50
                ],  # Truncate to 50 chars
                tob=final_output_dict.get("TOB", "")[:50],  # Truncate to 50 chars
                loinc=final_output_dict.get("LOINC", "")[:50],  # Truncate to 50 chars
                loinc_answer=final_output_dict.get("LOINCAnswer", "")[
                    :15
                ],  # Truncate to 15 chars
                snomed=final_output_dict.get("SNOMED", "")[:50],  # Truncate to 50 chars
                result=final_output_dict.get("Result", "")[:50],  # Truncate to 50 chars
                pos_neg_result=final_output_dict.get("PosNegResult", "")[
                    :50
                ],  # Truncate to 50 chars
                claim_alt_id1=final_output_dict.get("ClaimAltID1", "")[
                    :255
                ],  # Truncate to 255 chars
                claim_alt_id2=final_output_dict.get("ClaimAltID2", "")[
                    :255
                ],  # Truncate to 255 chars
                provider_name=final_output_dict.get("ProviderName", "")[
                    :179
                ],  # Truncate to 179 chars
                provider_address1=final_output_dict.get("ProviderAddress1", "")[
                    :50
                ],  # Truncate to 50 chars
                provider_address2=final_output_dict.get("ProviderAddress2", "")[
                    :50
                ],  # Truncate to 50 chars
                provider_city=final_output_dict.get("ProviderCity", "")[
                    :50
                ],  # Truncate to 50 chars
                provider_state=final_output_dict.get("ProviderState", "")[
                    :50
                ],  # Truncate to 50 chars
                provider_zipcode=final_output_dict.get("ProviderZipcode", "")[
                    :50
                ],  # Truncate to 50 chars
                provider_phone=final_output_dict.get("ProviderPhone", "")[
                    :50
                ],  # Truncate to 50 chars
                intake_type=final_output_dict.get("IntakeType", "")[
                    :50
                ],  # Truncate to 50 chars
                pdf_name=file.filename[:255],  # Truncate to 255 chars
                ocr_text=ocr_text,
                full_name=" ".join(
                    raw_name.split()
                ).strip(),  # Normalize whitespace in full name
                subjective=final_output_dict.get("Subjective", ""),
                chief_complaint=final_output_dict.get("ChiefComplaint", ""),
                hpi=final_output_dict.get("HPI", ""),
                ros=final_output_dict.get("ROS", ""),
                medical_history=final_output_dict.get("MedicalHistory", ""),
                social_history=final_output_dict.get("SocialHistory", ""),
                family_history=final_output_dict.get("FamilyHistory", ""),
                medications=final_output_dict.get("Medications", ""),
                allergies=final_output_dict.get("Allergies", ""),
                vitals=final_output_dict.get("Vitals", ""),
                physical_examination=final_output_dict.get("PhysicalExamination", ""),
                assessment=final_output_dict.get("Assessment", ""),
                plan=final_output_dict.get("Plan", ""),
                treatment=final_output_dict.get("Treatment", ""),
                recommendations=final_output_dict.get("Recommendations", ""),
                preventive_medicine=final_output_dict.get("PreventiveMedicine", ""),
                procedure_codes=final_output_dict.get("ProcedureCodes", ""),
                notes=final_output_dict.get("Notes", ""),
                reason_of_visit=final_output_dict.get("reason of visit", ""),
                height=final_output_dict.get("Height", ""),
                weight=final_output_dict.get("Weight", ""),
                bmi=final_output_dict.get("BMI", ""),
                temperature=final_output_dict.get("Temperature", ""),
                heart_rate=final_output_dict.get("HeartRate", ""),
                result_category=final_output_dict.get("Result Category", ""),
                bp_systolic=final_output_dict.get("BPSystolic", ""),
                bp_diastolic=final_output_dict.get("BPDiastolic", ""),
                entity_id="932058900",
                sub_entity_id="932058900001",
            )
            db.add(new_record)
            db.commit()

            results.append(
                {
                    "filename": file.filename,
                    "ocr_text": ocr_text,
                    "final_output": final_output_dict,  # Use the parsed dictionary here
                    "message": "Success, data inserted into the database.",
                }
            )

        except Exception as e:
            db.rollback()
            results.append(
                {
                    "filename": file.filename,
                    "ocr_text": ocr_text,
                    "final_output": final_output_dict,  # Use the parsed dictionary here
                    "message": f"Error inserting data into the database: {e}",
                }
            )

    return results


@router.get("/medical-details/search", response_model=List[dict])
async def search_medical_details(
    full_name: str,
    dob: str,
    db: Session = Depends(get_db),
):
    """
    Search for medical details using strict full_name matching
    with whitespace normalization.
    """
    try:
        dob_clean = dob.strip()

        # Normalize incoming name: collapse multi-spaces -> one space
        name_clean = " ".join(full_name.strip().split()).lower()

        # Ensure name has at least first + last
        if len(name_clean.split()) < 2:
            raise HTTPException(
                status_code=400, detail="full_name must contain at least two words"
            )

        # Normalize DB full_name for matching:
        #  - Trim spaces
        #  - Replace multiple spaces with a single space
        #  - Lowercase
        db_normalized = func.lower(
            func.regexp_replace(func.trim(CenteneSupplemental.full_name), r"\s+", " ")
        )

        query = db.query(CenteneSupplemental).filter(
            CenteneSupplemental.dob == dob_clean
        )

        # Strict normalized match
        records = query.filter(db_normalized == name_clean).all()

        if not records:
            raise HTTPException(
                status_code=404, detail="No records found for given name and dob."
            )

        return [
            {
                "id": r.id,
                "cnc_member_id": r.cnc_member_id,
                "medicaid_nbr": r.medicaid_nbr,
                "mbi": r.mbi,
                "last_name": r.last_name,
                "first_name": r.first_name,
                "middle_name": r.middle_name,
                "full_name": r.full_name,
                "dob": r.dob,
                "dos": r.dos,
                "dos_thru": r.dos_thru,
                "claim_number": r.claim_number,
                "cnc_plan_code": r.cnc_plan_code,
                "hichn": r.hichn,
                "event_diagnosis": r.event_diagnosis,
                "icd_px1": r.icd_px1,
                "icd_px2": r.icd_px2,
                "icd_dx_pri": r.icd_dx_pri,
                "icd_dx_sec1": r.icd_dx_sec1,
                "icd_dx_sec2": r.icd_dx_sec2,
                "icd_dx_sec3": r.icd_dx_sec3,
                "icd_dx_sec4": r.icd_dx_sec4,
                "icd_dx_sec5": r.icd_dx_sec5,
                "icd_px": r.cpt_px,
                "cpt_mod": r.cpt_mod,
                "hcpc_ps_px": r.hcpc_ps_px,
                "hcpc_ps_mod": r.hcpc_ps_mod,
                "ub_revenue_code": r.ub_revenue_code,
                "tob": r.tob,
                "loinc": r.loinc,
                "loinc_answer": r.loinc_answer,
                "snomed": r.snomed,
                "result": r.result,
                "pos_neg_result": r.pos_neg_result,
                "claim_alt_id1": r.claim_alt_id1,
                "claim_alt_id2": r.claim_alt_id2,
                "provider_name": r.provider_name,
                "provider_address1": r.provider_address1,
                "provider_address2": r.provider_address2,
                "provider_city": r.provider_city,
                "provider_state": r.provider_state,
                "provider_zipcode": r.provider_zipcode,
                "provider_phone": r.provider_phone,
                "intake_type": r.intake_type,
                "pdf_name": r.pdf_name,
                "ocr_text": r.ocr_text,
                "full_name": r.full_name,
                "subjective": r.subjective,
                "subjective": r.subjective,
                "chief_complaint": r.chief_complaint,
                "hpi": r.hpi,
                "ros": r.ros,
                "medical_history": r.medical_history,
                "social_history": r.social_history,
                "family_history": r.family_history,
                "medications": r.medications,
                "allergies": r.allergies,
                "vitals": r.vitals,
                "physical_examination": r.physical_examination,
                "assessment": r.assessment,
                "plan": r.plan,
                "treatment": r.treatment,
                "recommendations": r.recommendations,
                "preventive_medicine": r.preventive_medicine,
                "procedure_codes": r.procedure_codes,
                "notes": r.notes,
                "reason_of_visit": r.reason_of_visit,
                "height": r.height,
                "weight": r.weight,
                "bmi": r.bmi,
                "temperature": r.temperature,
                "heart_rate": r.heart_rate,
                "result_category": r.result_category,
                "bp_systolic": r.bp_systolic,
                "bp_diastolic": r.bp_diastolic,
            }
            for r in records
        ]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to search medical details: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to search medical details: {e}"
        )


@router.get("/medical-details", response_model=List[dict])
async def get_medical_details_name_count(db: Session = Depends(get_db)):
    """
    Return count of records grouped by full_name + dob.
    """

    try:
        results = (
            db.query(
                CenteneSupplemental.full_name,
                CenteneSupplemental.dob,
                func.count().label("record_count"),
            )
            .group_by(
                CenteneSupplemental.full_name,
                CenteneSupplemental.dob,
            )
            .all()
        )

        response = [
            {"name": row.full_name, "dob": row.dob, "count": row.record_count}
            for row in results
        ]

        return response

    except Exception as e:
        logger.error(f"Failed to fetch grouped name count: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch grouped name count: {e}"
        )

def extract_mmddyyyy(fname: str) -> Optional[str]:
    m = re.search(r"_(\d{8})\.(txt|TXT)$", fname)
    return m.group(1) if m else None


def latest_blob_date(container_client) -> Optional[str]:
    blobs = list(container_client.list_blobs(name_starts_with=f"{sud.BASE_BLOB_PATH}/"))
    if not blobs:
        return None

    newest = max(blobs, key=lambda b: b.last_modified)
    fname = newest.name.split("/")[-1]
    return extract_mmddyyyy(fname)


def download_blobs_for_date(container_client, mmddyyyy: str) -> List[Dict[str, str]]:
    blobs = list(container_client.list_blobs(name_starts_with=f"{sud.BASE_BLOB_PATH}/"))
    if not blobs:
        return []

    targets = []
    for b in blobs:
        fname = b.name.split("/")[-1]
        if extract_mmddyyyy(fname) == mmddyyyy:
            targets.append(b)

    targets.sort(key=lambda b: b.name.lower())

    out = []
    for b in targets:
        blob_client = container_client.get_blob_client(b.name)
        data = blob_client.download_blob().readall()
        out.append({
            "filename": b.name.split("/")[-1],
            "text": data.decode("utf-8", errors="replace")
        })

    return out

def pipe_text_to_json(pipe_text: str) -> List[Dict[str, Any]]:
    if not pipe_text or not pipe_text.strip():
        return []

    # Normalize newlines, remove trailing whitespace
    text = pipe_text.replace("\r\n", "\n").strip()
    lines = [ln for ln in text.split("\n") if ln.strip()]

    if len(lines) < 2:
        return []

    headers = lines[0].split("|")
    # result: Dict[str, Dict[str, Any]] = {}
    result: List[Dict[str, Any]] = []
    for idx, line in enumerate(lines[1:], start=1):
        values = line.split("|")

        # normalize column count
        if len(values) < len(headers):
            values += [""] * (len(headers) - len(values))
        elif len(values) > len(headers):
            values = values[:len(headers) - 1] + ["|".join(values[len(headers) - 1:])]

        record = dict(zip(headers, values))
        result.append(record)


    return result

@router.get("/download-medical-details")
def export_and_download_latest(page: int = Query(1, ge=1),page_size: int = Query(50, ge=1, le=500)):
    """
    1) Run export using sud_file_creation.main()
    2) Download latest Service + Lab TXT files from Blob
    3) Return JSON: [{filename, text}, ...]
    """

    # Run export (NO subprocess)
    try:
        result = sud.main()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")

    # Connect to Blob using same logic as script
    blob_service = sud.get_blob_service()
    container = blob_service.get_container_client(sud.BLOB_CONTAINER)

    mmddyyyy = latest_blob_date(container)

    # If no new rows and no blob exists
    if not mmddyyyy:
        if isinstance(result, dict) and result.get("status") == "no_new_rows":
            return []
        raise HTTPException(status_code=404, detail="No TXT files found in blob.")

    files = download_blobs_for_date(container, mmddyyyy)
    response: List[Dict[str, Any]] = []

    start = (page - 1) * page_size
    end = start + page_size

    for f in files:
        filename = f.get("filename", "")
        text = f.get("text", "")

        try:
            all_rows = pipe_text_to_json(text)  # List[Dict[str, Any]]
        except Exception:
            logger.exception(f"Pipe->JSON parsing failed for {filename}")
            all_rows = []

        total_rows = len(all_rows)


        paged_rows = all_rows[start:end] if total_rows else []

        response.append({
            "filename": filename,
            "total_rows": total_rows,
            "page": page,
            "page_size": page_size,
            "data": paged_rows,
            "text": text
        })

    return response

    # return files

def list_and_download_files(
    container_client,
    base_prefix: str,
    mmddyyyy: Optional[str] = None,
    prefix_filter: Optional[str] = None,
    include_subdirs: bool = True,
    limit: int = 50,
) -> List[Dict[str, str]]:

    # Collect candidates
    candidates = []
    for b in iter_target_blobs(container_client, base_prefix, include_subdirs=include_subdirs):
        fname = b.name.split("/")[-1]

        # Only TXT files (optional but usually expected)
        if not fname.lower().endswith(".txt"):
            continue

        if mmddyyyy and extract_mmddyyyy(fname) != mmddyyyy:
            continue

        if prefix_filter and not fname.lower().startswith(prefix_filter.lower()):
            continue

        candidates.append(b)

    # Sort newest first by last_modified, then name
    candidates.sort(key=lambda x: (x.last_modified, x.name.lower()), reverse=True)

    # Apply limit
    candidates = candidates[: max(0, limit)]

    out = []
    for b in candidates:
        text = download_blob_text(container_client, b.name)
        out.append({
            "filename": b.name.split("/")[-1],
            "blob_path": b.name,  # helpful for debugging
            "last_modified": b.last_modified.isoformat() if b.last_modified else None,
            "text": text
        })

    return out

def iter_target_blobs(container_client, base_prefix: str, include_subdirs: bool = True):
    """
    Yields blobs under base_prefix. If include_subdirs=False, only yields blobs directly
    under base_prefix (no deeper folders).
    """
    for b in container_client.list_blobs(name_starts_with=base_prefix):
        if not include_subdirs:
            # only allow "base_prefix/<file>" (one level)
            # remove trailing slash safety
            bp = base_prefix.rstrip("/")
            rest = b.name[len(bp):].lstrip("/")  # portion after base prefix
            if "/" in rest:
                continue
        yield b

def download_blob_text(container_client, blob_name: str) -> str:
    blob_client = container_client.get_blob_client(blob_name)
    data = blob_client.download_blob().readall()
    return data.decode("utf-8", errors="replace")

def list_and_download_files(
    container_client,
    base_prefix: str,
    mmddyyyy: Optional[str] = None,
    prefix_filter: Optional[str] = None,
    include_subdirs: bool = True,
    limit: int = 50,
) -> List[Dict[str, str]]:

    # Collect candidates
    candidates = []
    for b in iter_target_blobs(container_client, base_prefix, include_subdirs=include_subdirs):
        fname = b.name.split("/")[-1]

        # Only TXT files (optional but usually expected)
        if not fname.lower().endswith(".txt"):
            continue

        if mmddyyyy and extract_mmddyyyy(fname) != mmddyyyy:
            continue

        if prefix_filter and not fname.lower().startswith(prefix_filter.lower()):
            continue

        candidates.append(b)

    # Sort newest first by last_modified, then name
    candidates.sort(key=lambda x: (x.last_modified, x.name.lower()), reverse=True)

    # Apply limit
    candidates = candidates[: max(0, limit)]

    out = []
    for b in candidates:
        text = download_blob_text(container_client, b.name)
        out.append({
            "filename": b.name.split("/")[-1],
            "blob_path": b.name,  # helpful for debugging
            "last_modified": b.last_modified.isoformat() if b.last_modified else None,
            "text": text
        })

    return out


@router.get("/files")
def fetch_all_files_from_blob(
    mmddyyyy: Optional[str] = Query(default=None, description="Filter by _MMDDYYYY.txt, e.g. 01092026"),
    prefix: Optional[str] = Query(default=None, description="Filename startswith filter, e.g. Service or Lab"),
    include_subdirs: bool = Query(default=True, description="Include month folders/subdirectories"),
    limit: int = Query(default=50, ge=1, le=2000, description="Max files to return (protects from huge responses)"),
    run_export: bool = Query(default=False, description="If true, run sud.main() before fetching files"),
):

    if run_export:
        try:
            sud.main()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Export failed: {e}")

    blob_service = sud.get_blob_service()
    container = blob_service.get_container_client(sud.BLOB_CONTAINER)

    base_prefix = f"{sud.BASE_BLOB_PATH}/"

    files = list_and_download_files(
        container_client=container,
        base_prefix=base_prefix,
        mmddyyyy=mmddyyyy,
        prefix_filter=prefix,
        include_subdirs=include_subdirs,
        limit=limit
    )

    return files
