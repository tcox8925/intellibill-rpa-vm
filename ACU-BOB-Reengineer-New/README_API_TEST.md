# How to Test the ACU/BOB API

## 1. Turn on test mode

In `config.py` set `test_mode: True` and `notifications: False`

## 2. Setup

cd ACU-BOB-Reengineer-New
.\.venv\Scripts\activate
pip install -r requirements.txt
az login

## 3. Start the API

uvicorn api:app --host 0.0.0.0 --port 8020

Logs show in this window.

## 4. Run one file (new terminal)

ACU:
curl -X POST http://localhost:8020/run-file-processing -H "Content-Type: application/json" -d "{\"process_type\": \"ACU\", \"filename\": \"YOUR_FILE.csv\", \"report_date\": \"2026-06-30\"}"

BOB:
curl -X POST http://localhost:8020/run-file-processing -H "Content-Type: application/json" -d "{\"process_type\": \"BOB\", \"filename\": \"YOUR_FILE.csv\", \"report_date\": \"2026-06-30\"}"

Replace YOUR_FILE.csv and report_date with your file and month.

## 5. Check logs

Watch the uvicorn window for scan → process → complete.
