import os
import time
from io import BytesIO
from sqlalchemy import create_engine, text
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import (
    DB_NAME,
    DB_HOST,
    DB_PORT,
    DB_USER,
    DB_PASSWORD,
    LOCAL_FOLDER,
)
from qa_call_process import prepare_audio, upload_audio

DB_URI = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# ------------------------------
# Normalize filename
# ------------------------------
def normalize_base_name(filename: str) -> str:
    """
    _1_1_karina arroyo.mp3 -> _1_1_karina arroyo
    karina arroyo_11_02.mp3 -> karina arroyo
    """
    name = os.path.splitext(filename.strip().lower())[0]
    if name.endswith("_11_03"):
        name = name[:-6]
    return name


# ------------------------------
# DB existing base names
# ------------------------------
def get_existing_filenames(engine) -> set:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT file_name FROM wpo.membercare_agent_assessment_recordings"
            " WHERE file_name LIKE '%\\_11\\_03%' ESCAPE '\\'"
        )).fetchall()
        return {normalize_base_name(r[0]) for r in rows}


# ------------------------------
# Scan local folder for audio files
# ------------------------------
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}

def scan_local_files(folder: str) -> list:
    """Return list of dicts with name and full_path for each audio file in folder."""
    files = []
    for entry in os.scandir(folder):
        if entry.is_file():
            ext = os.path.splitext(entry.name)[1].lower()
            if ext in AUDIO_EXTENSIONS:
                files.append({"name": entry.name, "full_path": entry.path})
    return files


# ------------------------------
# Step 1 (main thread): read + convert
# ------------------------------
def read_and_convert(item):
    """Read local file and convert WAV→MP3 in the main thread."""
    name = item["name"]
    with open(item["full_path"], "rb") as f:
        audio = BytesIO(f.read())
    upload_name, audio = prepare_audio(name, audio)
    return {"original_name": name, "upload_name": upload_name, "audio": audio}


# ------------------------------
# Step 2 (thread): upload only
# ------------------------------
def upload_single_file(prepared):
    name = prepared["original_name"]
    try:
        upload_audio(name, prepared["upload_name"], prepared["audio"])
        print(f"Processed: {name}")
        return True
    except Exception as e:
        print(f"Failed: {name} | {e}")
        return False


# ------------------------------
# Run pipeline
# ------------------------------
def run_pipeline(batch_size=10):
    print("Starting Local Folder → QA pipeline (Continuous Mode)...\n")
    while True:
        _run_once()
        print("All files processed. Rechecking folder in 5 seconds...\n")
        time.sleep(5)


def _run_once(batch_size=10):
    engine = create_engine(DB_URI)
    existing_files = get_existing_filenames(engine)

    print(f"DB processed (base match): {len(existing_files)}")
    print(f"\nScanning local folder: {LOCAL_FOLDER}")

    all_local_files = scan_local_files(LOCAL_FOLDER)

    total_files = len(all_local_files)
    all_files = []
    skipped = 0

    for item in all_local_files:
        base_name = normalize_base_name(item["name"])
        if base_name not in existing_files:
            all_files.append(item)
        else:
            skipped += 1

    total_remaining = len(all_files)

    print("\n==============================")
    print(f"Total local files      : {total_files}")
    print(f"Already processed      : {skipped}")
    print(f"Remaining to process   : {total_remaining}")
    print("==============================\n")

    if not all_files:
        print("Nothing new to process.")
        return

    BATCH_SIZE = 3
    processed_count = 0
    total_batches = (len(all_files) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_num, batch_start in enumerate(range(0, len(all_files), BATCH_SIZE), 1):
        batch = all_files[batch_start:batch_start + BATCH_SIZE]

        print(f"Batch {batch_num}/{total_batches} — Reading & converting {len(batch)} files (main thread)...\n")

        prepared_files = []
        for item in batch:
            name = item["name"]
            try:
                prepared_files.append(read_and_convert(item))
                print(f"  Ready: {name}")
            except Exception as e:
                print(f"  Read/convert failed: {name} | {e}")

        print(f"\nBatch {batch_num}/{total_batches} — Uploading {len(prepared_files)} files (parallel)...\n")

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(upload_single_file, f): f for f in prepared_files}
            for future in as_completed(futures):
                if future.result():
                    processed_count += 1

        print(f"Batch {batch_num}/{total_batches} complete.\n")

    print("\n======== FINAL SUMMARY ========")
    print(f"Total local files      : {total_files}")
    print(f"Already processed      : {skipped}")
    print(f"Newly processed        : {processed_count}")
    print("================================")


if __name__ == "__main__":
    run_pipeline()
