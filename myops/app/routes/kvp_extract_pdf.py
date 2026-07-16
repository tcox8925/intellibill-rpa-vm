import logging
import os
import secrets
import tempfile
from typing import List
import json
import re
import ast

from fastapi import APIRouter, UploadFile, File, HTTPException, Header, Depends

from app.utils.callAI.kvp_extraction import extract_text_from_pdf, send_to_llm_agent

logger = logging.getLogger(__name__)

router = APIRouter(tags=["PDF KVP Extraction"])

SECRET_KEY = os.getenv("MYOPS_APP_SECRET_KEY", "")


def append_error(results, filename, message):
    results.append({
        "filename": filename,
        "final_output": "",
        "message": message
    })


def verify_token(
    x_auth_token: str = Header(None, alias="x-auth-token"),
):
    if not x_auth_token:
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")

    incoming = x_auth_token.strip()
    expected = SECRET_KEY.strip()

    if not secrets.compare_digest(incoming, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")

    return True


def parse_llm_json(text: str):
    """
    Handles:
    - JSON
    - fenced json blocks
    - extra text around json
    - keys without quotes (document_id: "x")
    """
    if text is None:
        raise ValueError("Empty LLM output")

    s = str(text).strip()

    # Remove markdown code fences
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)

    # Try strict JSON
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # Extract JSON-like object/array
    m = re.search(r"(\{.*\}|\[.*\])", s, flags=re.DOTALL)
    if m:
        s = m.group(1).strip()

    # Quote unquoted keys
    s_fixed = re.sub(r'(?m)(\{|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1 "\2":', s)

    try:
        return json.loads(s_fixed)
    except json.JSONDecodeError:
        pass

    # Python literal fallback
    try:
        obj = ast.literal_eval(s)
        if isinstance(obj, (dict, list)):
            return obj
    except Exception:
        pass

    raise ValueError("LLM output is not valid JSON (or could not be extracted).")


@router.post("/pdf-kvp-extraction")
async def extract_llm_output(
    files: List[UploadFile] = File(...),
    _: bool = Depends(verify_token),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    results = []

    for file in files:
        logger.info(f"Received file: {file.filename}")

        if file.content_type not in ("application/pdf", "application/octet-stream"):
            logger.warning(f"Skipped (not a PDF): {file.filename}")
            append_error(results, file.filename, "Skipped: Not a PDF.")
            continue

        pdf_bytes = await file.read()
        if not pdf_bytes:
            logger.warning(f"Empty file detected: {file.filename}")
            append_error(results, file.filename, "Empty file.")
            continue

        temp_path = None
        try:
            # Save PDF temporarily
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
                temp_pdf.write(pdf_bytes)
                temp_path = temp_pdf.name
            logger.info(f"Saved PDF temporarily: {temp_path}")

            # --- OCR ---
            ocr_text = extract_text_from_pdf(temp_path)
            logger.info(f"OCR complete for {file.filename} (chars: {len(ocr_text)})")

            if not ocr_text.strip():
                append_error(results, file.filename, "OCR returned no text.")
                continue

            # --- LLM ---
            raw_output = send_to_llm_agent(ocr_text)

            try:
                final_output = parse_llm_json(raw_output)
            except Exception as e:
                logger.error(
                    f"LLM returned non-JSON for {file.filename}: {str(e)} | preview={str(raw_output)[:500]}"
                )
                append_error(results, file.filename, f"LLM output not valid JSON: {str(e)}")
                continue

            results.append({
                "filename": file.filename,
                "ocr_text": ocr_text,
                "final_output": final_output
            })

        except Exception as e:
            logger.error(f"Processing failed for {file.filename}: {e}")
            append_error(results, file.filename, f"Processing failed: {str(e)}")

        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                    logger.info(f"Deleted temporary file: {temp_path}")
                except Exception as e:
                    logger.warning(f"Could not delete temp file {temp_path}: {e}")

    return results
