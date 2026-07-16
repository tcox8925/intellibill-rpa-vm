import os, json, requests
from datetime import datetime
from azure.identity import ClientSecretCredential
from app.core.config import settings
import numpy as np
from datetime import timedelta

# ========= CONFIG =========

TENANT_ID = settings.AZURE_TENANT_ID
CLIENT_ID = settings.AZURE_CLIENT_ID
CLIENT_SECRET = settings.AZURE_CLIENT_SECRET
COG_ENDPOINT  = "https://callagentbot.cognitiveservices.azure.com/"

API_VERSION   = "2024-11-15"
LOCALES       = ["en-US"]
DIARIZATION   = {"enabled": True, "maxSpeakers": 3}
SCOPE         = "https://cognitiveservices.azure.com/.default"

AUDIO_FILE    = "test2.wav"

SPEAKER_MAP = {1: "Agent", 2: "Caller"}

def get_token() -> str:
    cred = ClientSecretCredential(TENANT_ID, CLIENT_ID, CLIENT_SECRET)
    return cred.get_token(SCOPE).token
# print(get_token())

def fast_transcribe(token: str, audio_path: str) -> dict:
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    url = f"{COG_ENDPOINT}/speechtotext/transcriptions:transcribe?api-version={API_VERSION}"
    definition = {"locales": LOCALES, "diarization": DIARIZATION}

    with open(audio_path, "rb") as f:
        files = {
            "audio": (os.path.basename(audio_path), f, "application/octet-stream"),
            "definition": (None, json.dumps(definition), "application/json"),
        }
        headers = {"Authorization": f"Bearer {token}"}
        r = requests.post(url, headers=headers, files=files, timeout=1200)

    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} {r.text[:500]}")
    return r.json()

# print(fast_transcribe(get_token(), AUDIO_FILE))
def format_time(ms: int) -> str:
    """Convert milliseconds to [HH:MM:SS] format (rounded down to seconds)."""
    td = timedelta(milliseconds=ms)
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02}:{minutes:02}:{seconds:02}"

def create_transcript_lines(resp_json: dict) -> list[str]:
    """
    Extract transcript lines with Agent/Caller labels and single start timestamps.
    Format: [HH:MM:SS] Role: text
    """
    phrases = resp_json.get("phrases") or []
    combined = resp_json.get("combinedPhrases") or []

    lines: list[str] = []

    if phrases:
        for p in phrases:
            txt = (p.get("text") or "").strip()
            if not txt:
                continue

            spk = p.get("speaker")
            role = SPEAKER_MAP.get(spk, f"Speaker-{spk}")

            start_ms = int(p.get("offsetMilliseconds") or 0)
            start_ts = format_time(start_ms)

            lines.append(f"[{start_ts}] {role}: {txt}")
    else:
        if combined:
            text = (combined[0].get("text") or "").strip()
            if text:
                lines.append(f"[00:00:00] {text}")

    return lines

# def create_transcript_lines(resp_json: dict) -> list[str]:
#     """Extract transcript lines with Agent/Caller labels (no timestamps)."""
#     phrases = resp_json.get("phrases") or []
#     combined = resp_json.get("combinedPhrases") or []

#     lines = []
#     if phrases:
#         for p in phrases:
#             txt = (p.get("text") or "").strip()
#             if not txt:
#                 continue
#             spk = p.get("speaker")
#             if spk == 1:
#                 role = "Agent"
#             elif spk == 2:
#                 role = "Caller"
#             else:
#                 role = f"Speaker-{spk}"
#             lines.append(f"{role}: {txt}")
#     else:
#         text = (combined[0]["text"] if combined else "").strip()
#         if text:
#             lines.append(text)

#     return lines

# print(create_transcript_lines(fast_transcribe(get_token(), AUDIO_FILE)))

def to_score_from_cos(cos):
    return (cos + 1.0) / 2.0  # [-1,1] -> [0,1]

def agg_retrieval_score(cos_sims, weights=None):
    cos_sims = np.asarray(cos_sims, dtype=float)
    if weights is None:
        weights = np.ones_like(cos_sims) / len(cos_sims)
    else:
        weights = np.asarray(weights, dtype=float)
        weights = weights / weights.sum()
    return float((to_score_from_cos(cos_sims) * weights).sum())

