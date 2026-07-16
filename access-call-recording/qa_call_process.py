import os
import subprocess
import requests
from io import BytesIO
from datetime import datetime
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from config import QA_API_URL, TOKEN

# ------------------------------
# Helpers
# ------------------------------
def extract_agent_name(filename: str) -> str:
    name_without_ext = os.path.splitext(filename)[0]
    agent_name = name_without_ext.split("_")[-1].strip()
    return agent_name.title()


def wav_bytes_to_mp3_bytes(wav_bytes: BytesIO) -> BytesIO:
    process = subprocess.Popen(
        [
            "ffmpeg",
            "-loglevel", "error",
            "-i", "pipe:0",
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-b:a", "64k",
            "-f", "mp3",
            "pipe:1",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    mp3_data, err = process.communicate(wav_bytes.read())

    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {err.decode()}")

    return BytesIO(mp3_data)


def make_session():
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["POST"]),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


session = make_session()

# ------------------------------
# Main processor
# ------------------------------
def prepare_audio(filename: str, audio_stream: BytesIO):
    """Convert WAV→MP3 if needed. Returns (upload_name, audio_stream).
    Must be called from the main thread (subprocess/fork unsafe in threads on macOS)."""
    if filename.lower().endswith(".wav"):
        audio_stream.seek(0)
        audio_stream = wav_bytes_to_mp3_bytes(audio_stream)
        base_name = os.path.splitext(filename)[0]
        upload_name = f"{base_name}_11_03.mp3"
    else:
        base_name = os.path.splitext(filename)[0]
        ext = os.path.splitext(filename)[1]
        upload_name = f"{base_name}_11_03{ext}"
    return upload_name, audio_stream


def upload_audio(original_filename: str, upload_name: str, audio_stream: BytesIO,
                 api_url=QA_API_URL, token=TOKEN):
    """Upload pre-converted audio to the QA API. Safe to call from threads."""
    agent_name = extract_agent_name(original_filename)

    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    payload = {
        "agent_login": agent_name,
        "phone_number": "1234567890",
        "recorded_at": "2026-11-03T10:30:00Z",
        "campaign": "OEP_2025",
    }

    files_payload = {
        "file": (upload_name, audio_stream, "audio/mpeg")
    }

    response = session.post(api_url, headers=headers, data=payload, files=files_payload, timeout=10000)
    response.raise_for_status()
    return response.json()


def process_audio_bytes(filename: str, audio_stream: BytesIO, api_url=QA_API_URL, token=TOKEN):
    upload_name, audio_stream = prepare_audio(filename, audio_stream)
    return upload_audio(filename, upload_name, audio_stream, api_url, token)

