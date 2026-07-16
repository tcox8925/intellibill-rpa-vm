import os

# Local synced SharePoint folder path on the VM
LOCAL_FOLDER = r"C:\Users\myopsadmin\Agility Insurance Services\834 Labs - Documents\Data Ops Production Files\834labs raw files\QA Member Care Recordings\11.03"

DB_HOST=os.getenv("MYOPS_DB_HOST", "")

DB_PORT=5432
DB_NAME=os.getenv("MYOPS_DB_NAME", "")
DB_USER=os.getenv("ACCESS_CALL_DB_USER", "")
DB_PASSWORD=os.getenv("ACCESS_CALL_DB_PASSWORD", "")

DB_URI = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# QA_API_URL = "https://834-appbe-dev001-dfeagea9fkera2gx.centralus-01.azurewebsites.net/api/v1/assess-call-recording"

QA_API_URL = "http://127.0.0.1:8000/api/v1/assess-call-recording"

TOKEN = os.getenv("ACCESS_CALL_JWT_TOKEN", "")